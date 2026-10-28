from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, text

from app.catalog.default import build_default_catalog
from app.catalog.models import ColumnMetadata, SchemaCatalog, SchemaContext, TableMetadata
from app.config import Settings
from app.db.models import Base
from app.decision.models import ProposalSource
from app.decision.service import DecisionSqlApplication
from app.generation.decision_contract import (
    PRODUCTION_DECISION_CASE_ID,
    DecisionType,
    ProductionDecision,
)
from app.generation.provider import LLMProvider, OpenAICompatibleProvider
from app.metadata.service import GOVERNED_CONNECTION_ID, QuestionSource
from app.metadata.store import InMemoryRunStore, RunStore
from app.observability.run_trace import RunTraceCollector, TraceStageStatus
from app.operator.models import (
    DemoPreset,
    GovernedSchemaResponse,
    RunRecord,
    RunSummary,
    RuntimeOutcome,
    SchemaColumn,
    SchemaEntity,
    SchemaRelationship,
    SourceDatabase,
)
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.semantics.measures import measure_catalog_from_schema
from app.sql.service import SqlSafetyService


class _ContextOnlyResolver(SchemaContextResolver):
    """Keep the safety replay authority envelope narrower than the catalog."""

    def __init__(self, base: SchemaContextResolver, question: str) -> None:
        super().__init__(
            base.catalog,
            top_k=base.top_k,
            max_tables=base.max_tables,
            max_columns_per_table=base.max_columns_per_table,
            relationship_depth=base.relationship_depth,
            full_context_max_tables=base.full_context_max_tables,
        )
        self._question = question

    def resolve(
        self, question: str, mode: SchemaContextMode = SchemaContextMode.RETRIEVED_BOUNDED
    ) -> SchemaContext:
        return super().resolve(self._question, mode)


class OperatorApplication:
    """Operator facade backed by the canonical production application service."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        catalog: SchemaCatalog,
        run_store: RunStore | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.catalog = catalog
        self.resolver = self._resolver(catalog)
        self.run_store: RunStore = run_store or InMemoryRunStore()

    def _resolver(self, catalog: SchemaCatalog) -> SchemaContextResolver:
        return SchemaContextResolver(
            catalog,
            top_k=self.settings.schema_top_k,
            max_tables=self.settings.max_context_tables,
            max_columns_per_table=self.settings.max_columns_per_table,
            relationship_depth=self.settings.relationship_depth,
            full_context_max_tables=self.settings.full_context_max_tables,
        )

    @property
    def presets(self) -> tuple[DemoPreset, ...]:
        return (
            DemoPreset(
                id="successful_query",
                label="Successful query",
                description="A live bounded revenue question that reaches PostgreSQL.",
                category="SUCCESSFUL_QUERY",
                question="Show total order revenue by status.",
                mode=ProposalSource.LIVE_MODEL,
            ),
            DemoPreset(
                id="needs_clarification",
                label="Ambiguous live request",
                description=(
                    "A schema-resolvable request for testing model clarification semantics."
                ),
                category="AMBIGUOUS_LIVE_REQUEST",
                question="What is the order value?",
                mode=ProposalSource.LIVE_MODEL,
            ),
            DemoPreset(
                id="unauthorized_query",
                label="Unauthorized relation",
                description="A frozen proposal replay stopped by the current authority gate.",
                category="UNAUTHORIZED_QUERY",
                question="Show subscriber IDs from the partner directory.",
                mode=ProposalSource.SAFETY_REPLAY,
            ),
            DemoPreset(
                id="policy_blocked",
                label="Policy blocked",
                description="A frozen write proposal replay stopped by read-only policy.",
                category="POLICY_BLOCKED",
                question="Remove cancelled orders.",
                mode=ProposalSource.RUNTIME_POLICY_REPLAY,
            ),
        )

    async def run(
        self,
        question: str,
        preset_id: str | None = None,
        actor: str = "anonymous",
        source: QuestionSource | None = None,
    ) -> RunRecord:
        run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        collector = RunTraceCollector(run_id)
        collector.event("request.received", "Natural-language request received", "request")
        collector.record_stage("request", TraceStageStatus.PASS)
        service, effective_question, proposal_source, replay = self._service_for(
            preset_id, question, source
        )
        service.safety_service.stage_recorder = collector
        result = await service.run(
            effective_question,
            collector,
            proposal_source=proposal_source,
            replay_decision=replay,
        )
        record = RunRecord(
            run_id=run_id,
            trace_id=result.trace.trace_id,
            question=effective_question,
            preset_id=preset_id,
            proposal_source=result.proposal_source,
            replay_notice=(
                "No provider call was made for this scenario. The frozen proposal was "
                "evaluated by the current deterministic runtime."
                if result.proposal_source is not ProposalSource.LIVE_MODEL
                else None
            ),
            model_decision=result.model_decision,
            model_reason_code=result.model_reason_code,
            runtime_outcome=RuntimeOutcome(result.runtime_outcome),
            runtime_reason=result.runtime_reason,
            provider=result.provider,
            model=result.model,
            model_context=result.model_context,
            model_context_hash=result.model_context_hash,
            proposed_sql=result.proposed_sql,
            rows=result.rows,
            columns=result.columns,
            row_count=result.row_count,
            truncated=result.truncated,
            duration_ms=result.trace.duration_ms,
            created_at=result.trace.started_at.isoformat(),
            trace=result.trace,
            actor=actor,
            connection_id=source.connection_id if source is not None else GOVERNED_CONNECTION_ID,
        )
        await asyncio.to_thread(self.run_store.save, record)
        return record

    def list_runs(
        self, *, actor: str | None = None, outcome: str | None = None, limit: int = 200
    ) -> list[RunSummary]:
        return self.run_store.list(actor=actor, outcome=outcome, limit=limit)

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.run_store.get(run_id)

    def schema(self) -> GovernedSchemaResponse:
        database, row_estimates = self._source_summary()
        return GovernedSchemaResponse(
            database=database,
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
                    row_estimate=row_estimates.get(table.name),
                )
                for table in self.catalog.tables
                if table.queryable
            ],
        )

    def _source_summary(self) -> tuple[SourceDatabase, dict[str, int]]:
        """Read connection identity and planner row estimates; degrade to disconnected."""
        name = self.engine.url.database or "unknown"
        try:
            with self.engine.connect() as connection:
                version: object = connection.execute(text("SHOW server_version")).scalar_one()
                estimates = connection.execute(
                    text(
                        "SELECT c.relname, c.reltuples::bigint FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                    )
                ).all()
        except Exception:
            return SourceDatabase(name=name, connected=False), {}
        return (
            SourceDatabase(name=name, server_version=str(version), connected=True),
            {str(row[0]): int(row[1]) for row in estimates if int(row[1]) >= 0},
        )

    def _service_for(
        self, preset_id: str | None, question: str, source: QuestionSource | None = None
    ) -> tuple[DecisionSqlApplication, str, ProposalSource, ProductionDecision | None]:
        preset = next((item for item in self.presets if item.id == preset_id), None)
        effective_question = preset.question if preset is not None else question
        mode = preset.mode if preset is not None else ProposalSource.LIVE_MODEL
        provider: LLMProvider = OpenAICompatibleProvider(self.settings)
        if source is not None:
            # A connected database: its live catalog is both the model context and the
            # authority envelope; SQL runs read-only as the connection's own user.
            resolver = self._resolver(source.catalog)
            safety = SqlSafetyService(
                source.engine,
                settings=self.settings,
                catalog=source.catalog,
                reader_role=source.reader_role,
                measure_catalog=measure_catalog_from_schema(source.catalog),
                grain_normalization_enabled=True,
            )
            return (
                DecisionSqlApplication(resolver, provider, safety, self.settings),
                question,
                ProposalSource.LIVE_MODEL,
                None,
            )
        resolver = self.resolver
        catalog = self.catalog
        replay: ProductionDecision | None = None
        if preset_id == "unauthorized_query":
            replay = ProductionDecision(
                case_id=PRODUCTION_DECISION_CASE_ID,
                decision=DecisionType.ANSWER,
                sql="SELECT subscriber_id FROM external_directory",
            )
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
        elif preset_id == "policy_blocked":
            replay = ProductionDecision(
                case_id=PRODUCTION_DECISION_CASE_ID,
                decision=DecisionType.ANSWER,
                sql="DELETE FROM orders",
            )
        safety = SqlSafetyService(
            self.engine,
            settings=self.settings,
            catalog=catalog,
            stage_recorder=None,
            measure_catalog=measure_catalog_from_schema(catalog),
            grain_normalization_enabled=True,
        )
        return (
            DecisionSqlApplication(resolver, provider, safety, self.settings),
            effective_question,
            mode,
            replay,
        )


def build_operator_application(
    engine: Engine, settings: Settings, run_store: RunStore | None = None
) -> OperatorApplication:
    return OperatorApplication(engine, settings, build_default_catalog(Base.metadata), run_store)
