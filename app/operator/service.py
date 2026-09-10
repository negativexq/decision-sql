from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime

from sqlalchemy import Engine

from app.catalog.models import ColumnMetadata, SchemaCatalog, SchemaContext, TableMetadata
from app.config import Settings
from app.db.models import Base
from app.generation.provider import LLMProvider, OpenAICompatibleProvider, StaticLLMProvider
from app.models.domain import FailureStage, TextToSqlRequest
from app.observability.run_trace import (
    TRACE_STAGE_ORDER,
    RunTraceCollector,
    TraceStageStatus,
)
from app.operator.models import (
    DecisionValue,
    DemoPreset,
    GovernedSchemaResponse,
    RunRecord,
    RunSummary,
    RuntimeOutcome,
    SchemaColumn,
    SchemaEntity,
    SchemaRelationship,
)
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, SchemaResolutionError
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService


class _ContextOnlyResolver(SchemaContextResolver):
    """Keep the demo authority envelope narrower than the known policy catalog."""

    def __init__(self, base: SchemaContextResolver, question: str) -> None:
        super().__init__(
            base.catalog,
            top_k=base.top_k,
            max_tables=base.max_tables,
            max_columns_per_table=base.max_columns_per_table,
            relationship_depth=base.relationship_depth,
        )
        self._question = question

    def resolve(
        self, question: str, mode: SchemaContextMode = SchemaContextMode.RETRIEVED_BOUNDED
    ) -> SchemaContext:
        return super().resolve(self._question, mode)


class _NoMatchResolver(SchemaContextResolver):
    def resolve(
        self, question: str, mode: SchemaContextMode = SchemaContextMode.RETRIEVED_BOUNDED
    ) -> SchemaContext:
        del question, mode
        raise SchemaResolutionError("No queryable schema matched the question")


class OperatorApplication:
    """Application facade for the UI; it delegates query work to product services."""

    def __init__(self, engine: Engine, settings: Settings, catalog: SchemaCatalog) -> None:
        self.engine = engine
        self.settings = settings
        self.catalog = catalog
        self.resolver = SchemaContextResolver(
            catalog,
            top_k=settings.schema_top_k,
            max_tables=settings.max_context_tables,
            max_columns_per_table=settings.max_columns_per_table,
            relationship_depth=settings.relationship_depth,
        )
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._max_runs = 100

    @property
    def presets(self) -> tuple[DemoPreset, ...]:
        return (
            DemoPreset(
                id="successful_query",
                label="Successful query",
                description="A bounded revenue summary that reaches PostgreSQL.",
                category="SUCCESSFUL_QUERY",
                question="Show total order revenue by status.",
            ),
            DemoPreset(
                id="needs_clarification",
                label="Needs clarification",
                description="A request with no resolvable governed subject.",
                category="NEEDS_CLARIFICATION",
                question="Tell me something interesting about the universe.",
            ),
            DemoPreset(
                id="unauthorized_query",
                label="Unauthorized relation",
                description="The model proposes a relation outside this request authority.",
                category="UNAUTHORIZED_QUERY",
                question="Show subscriber IDs from the partner directory.",
            ),
            DemoPreset(
                id="policy_blocked",
                label="Policy blocked",
                description="A write proposal is stopped by the read-only SQL policy.",
                category="POLICY_BLOCKED",
                question="Remove cancelled orders.",
            ),
        )

    async def run(self, question: str, preset_id: str | None = None) -> RunRecord:
        run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{len(self._runs) + 1:04d}"
        collector = RunTraceCollector(run_id)
        collector.event("request.received", "Natural-language request received", "request")
        collector.record_stage("request", TraceStageStatus.PASS)
        service, effective_question = self._service_for(preset_id, question, collector)
        request = TextToSqlRequest(
            question=effective_question,
            correlation_id=run_id,
            execute=True,
        )
        try:
            result = await service.run(request)
        except Exception:
            collector.record_stage(
                "response",
                TraceStageStatus.FAILED,
                reason="UNEXPECTED_APPLICATION_ERROR",
            )
            result = TextToSqlResult(
                status=TextToSqlStatus.EXECUTION_ERROR,
                correlation_id=run_id,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="The request could not be completed.",
            )
        record = self._record_from_result(run_id, preset_id, effective_question, result, collector)
        self._runs[run_id] = record
        self._runs.move_to_end(run_id)
        while len(self._runs) > self._max_runs:
            self._runs.popitem(last=False)
        return record

    def list_runs(self) -> list[RunSummary]:
        return [
            RunSummary(
                run_id=run.run_id,
                trace_id=run.trace_id,
                question=run.question,
                preset_id=run.preset_id,
                decision=run.decision,
                runtime_outcome=run.runtime_outcome,
                reason_code=run.reason_code,
                row_count=run.row_count,
                duration_ms=run.duration_ms,
                created_at=run.created_at,
            )
            for run in reversed(tuple(self._runs.values()))
        ]

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def schema(self) -> GovernedSchemaResponse:
        return GovernedSchemaResponse(
            entities=[
                SchemaEntity(
                    name=table.name,
                    description=table.description,
                    columns=[
                        SchemaColumn(
                            name=column.name,
                            type=column.type,
                            description=column.description,
                            queryable=column.queryable,
                            primary_key=column.primary_key,
                        )
                        for column in table.columns
                    ],
                    relationships=[
                        SchemaRelationship(
                            source_column=relationship.column,
                            target_table=relationship.referenced_table,
                            target_column=relationship.referenced_column,
                        )
                        for relationship in table.relationships
                    ],
                )
                for table in self.catalog.tables
                if table.queryable
            ]
        )

    def _service_for(
        self, preset_id: str | None, question: str, collector: RunTraceCollector
    ) -> tuple[TextToSqlService, str]:
        preset = next((item for item in self.presets if item.id == preset_id), None)
        effective_question = preset.question if preset is not None else question
        provider: LLMProvider = OpenAICompatibleProvider(self.settings)
        resolver = self.resolver
        catalog = self.catalog
        if preset_id == "successful_query":
            provider = StaticLLMProvider(
                "SELECT status, SUM(total_amount) AS total_revenue "
                "FROM orders GROUP BY status ORDER BY status"
            )
        elif preset_id == "policy_blocked":
            provider = StaticLLMProvider("DELETE FROM orders")
        elif preset_id == "needs_clarification":
            provider = StaticLLMProvider("SELECT 1")
            resolver = _NoMatchResolver(self.catalog)
        elif preset_id == "unauthorized_query":
            provider = StaticLLMProvider("SELECT subscriber_id FROM external_directory")
            external = TableMetadata(
                name="external_directory",
                description="Partner directory outside this request authority.",
                columns=(
                    ColumnMetadata(
                        name="subscriber_id",
                        type="integer",
                        description="Partner subscriber identifier.",
                    ),
                ),
                queryable=True,
            )
            catalog = catalog.model_copy(update={"tables": (*catalog.tables, external)})
            resolver = _ContextOnlyResolver(self.resolver, "show customers")
        safety = SqlSafetyService(
            self.engine,
            settings=self.settings,
            catalog=catalog,
            stage_recorder=collector,
        )
        return (
            TextToSqlService(
                resolver,
                provider,
                safety,
                settings=self.settings,
                stage_recorder=collector,
            ),
            effective_question,
        )

    @staticmethod
    def _record_from_result(
        run_id: str,
        preset_id: str | None,
        question: str,
        result: TextToSqlResult,
        collector: RunTraceCollector,
    ) -> RunRecord:
        if result.status is TextToSqlStatus.CONTEXT_RESOLUTION_ERROR:
            decision = DecisionValue.NEEDS_CLARIFICATION
            outcome = RuntimeOutcome.NOT_ENTERED
            reason = result.error or "NO_QUERYABLE_SCHEMA_MATCH"
        elif result.status is TextToSqlStatus.SQL_GENERATION_ERROR:
            decision = DecisionValue.GENERATION_FAILED
            outcome = RuntimeOutcome.GENERATION_ERROR
            reason = result.failure_stage.value if result.failure_stage else "SQL_GENERATION_ERROR"
        else:
            decision = DecisionValue.ANSWER
            failure = result.plan_failure
            if result.status is TextToSqlStatus.SUCCEEDED:
                outcome = RuntimeOutcome.EXECUTED
                reason = None
            elif failure is not None and failure.authority_rejection is not None:
                outcome = RuntimeOutcome.AUTHORITY_REJECTED
                reason = failure.authority_rejection.code.value
            elif failure is not None and failure.rejection is not None:
                decision = DecisionValue.BLOCKED_POLICY
                outcome = RuntimeOutcome.POLICY_REJECTED
                reason = failure.rejection.code.value
            elif failure is not None:
                outcome = RuntimeOutcome.RUNTIME_REJECTED
                reason = (
                    failure.failure_stage.value if failure.failure_stage else failure.status.value
                )
            else:
                outcome = RuntimeOutcome.EXECUTION_ERROR
                reason = result.failure_stage.value if result.failure_stage else "EXECUTION_ERROR"
        execution = result.execution
        collector.skip_unrecorded(
            TRACE_STAGE_ORDER[1:-1],
            "Not entered after an earlier stage outcome",
        )
        collector.record_stage(
            "response",
            (
                TraceStageStatus.PASS
                if outcome is not RuntimeOutcome.GENERATION_ERROR
                else TraceStageStatus.FAILED
            ),
            reason=reason,
        )
        trace = collector.finish(outcome.value)
        sql = result.proposal.sql if result.proposal is not None else None
        return RunRecord(
            run_id=run_id,
            trace_id=trace.trace_id,
            question=question,
            preset_id=preset_id,
            decision=decision,
            runtime_outcome=outcome,
            reason_code=reason,
            sql=sql,
            rows=execution.rows if execution is not None else [],
            columns=execution.columns if execution is not None else [],
            row_count=execution.row_count if execution is not None else 0,
            truncated=execution.truncated if execution is not None else False,
            duration_ms=trace.duration_ms,
            created_at=trace.started_at.isoformat(),
            trace=trace,
        )


def build_operator_application(engine: Engine, settings: Settings) -> OperatorApplication:
    return OperatorApplication(engine, settings, _default_catalog())


def _default_catalog() -> SchemaCatalog:
    from app.catalog.default import build_default_catalog

    return build_default_catalog(Base.metadata)
