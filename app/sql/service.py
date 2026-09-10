from time import perf_counter
from uuid import UUID, uuid4

from opentelemetry import trace
from sqlalchemy import Engine
from sqlglot import exp

from app.catalog.default import build_default_catalog
from app.catalog.models import SchemaCatalog
from app.config import Settings, get_settings
from app.db.models import Base
from app.execution.cost import QueryCostGate
from app.execution.reader import ReaderRoleError, ReadOnlyExecutor
from app.models.domain import FailureStage
from app.observability.run_trace import TraceStageSink, TraceStageStatus
from app.observability.tracing import get_tracer
from app.provenance.canonical import text_hash
from app.provenance.models import (
    ProvenanceEventType,
    ProvenanceSink,
    ProvenanceStage,
    recorder_for_identity,
)
from app.provenance.sink import NoOpProvenanceSink
from app.semantics.grain import MeasureCatalog
from app.semantics.grain_runtime import (
    GrainRuntimeDecision,
    GrainRuntimeStatus,
    RuntimeGrainSafetyCoordinator,
)
from app.sql.authority import (
    AuthorityCode,
    AuthorityRejection,
    ExecutionAuthority,
    validate_authority,
)
from app.sql.models import (
    CandidateSource,
    ExplainEstimate,
    PolicyCode,
    PolicyRejection,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.sql.parser import SQLParseFailure, SQLParser
from app.sql.policy import SQLPolicy


class _AbortedPlanning(Exception):
    def __init__(self, result: SqlPlanFailure) -> None:
        self.result = result


class _AbortedExecution(Exception):
    def __init__(self, result: SqlExecutionError) -> None:
        self.result = result


class SqlSafetyService:
    """Deterministic M1 planner and executor for untrusted SQL candidates."""

    def __init__(
        self,
        reader_engine: Engine,
        settings: Settings | None = None,
        catalog: SchemaCatalog | None = None,
        tracer: trace.Tracer | None = None,
        stage_recorder: TraceStageSink | None = None,
        provenance_sink: ProvenanceSink | None = None,
        measure_catalog: MeasureCatalog | None = None,
        grain_normalization_enabled: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.reader_engine = reader_engine
        self.parser = SQLParser()
        self.catalog = catalog or build_default_catalog(Base.metadata)
        authority_tables = [table.name for table in self.catalog.tables if table.queryable]
        if measure_catalog is not None:
            authority_tables.extend(entity.physical_table for entity in measure_catalog.entities)
        self.default_execution_authority = ExecutionAuthority.from_table_names(authority_tables)
        self.policy = SQLPolicy(self.catalog)
        self.cost_gate = QueryCostGate()
        self.executor = ReadOnlyExecutor(
            reader_engine,
            statement_timeout_ms=self.settings.statement_timeout_ms,
            max_rows=self.settings.max_result_rows,
            reader_role=self.settings.reader_role,
        )
        self.tracer = tracer or get_tracer()
        self.stage_recorder = stage_recorder
        self.provenance_sink = provenance_sink or NoOpProvenanceSink()
        if grain_normalization_enabled and measure_catalog is None:
            raise ValueError("Grain normalization requires an explicit MeasureCatalog.")
        self.grain_normalization_enabled = grain_normalization_enabled
        self.grain_coordinator = (
            RuntimeGrainSafetyCoordinator(measure_catalog)
            if grain_normalization_enabled and measure_catalog is not None
            else None
        )
        self._accepted_plans: dict[UUID, QueryPlan] = {}

    def plan(self, candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure:
        result = self._plan(candidate)
        self._record_plan(candidate, result)
        return result

    def _plan(self, candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure:
        """Parse, authorize, cost-check, and return an executable QueryPlan."""
        parse_started = perf_counter()
        with self.tracer.start_as_current_span("decision_sql.validate") as span:
            span.set_attribute("decision_sql.statement_length", len(candidate.sql))
            try:
                parsed = self.parser.parse(candidate.sql)
            except SQLParseFailure as error:
                self._record_stage(
                    "sql_parse",
                    TraceStageStatus.FAILED,
                    reason=str(error),
                    duration_ms=(perf_counter() - parse_started) * 1000,
                )
                span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.SQL_PARSE_ERROR)
                return SqlPlanFailure(
                    status=SqlSafetyStatus.SQL_PARSE_ERROR,
                    failure_stage=FailureStage.SQL_PARSE_ERROR,
                    error=str(error),
                )
            span.set_attribute("decision_sql.statement_type", type(parsed.expression).__name__)
            self._record_stage(
                "sql_parse",
                TraceStageStatus.PASS,
                metadata={"statement_type": type(parsed.expression).__name__},
                duration_ms=(perf_counter() - parse_started) * 1000,
            )

        policy_started = perf_counter()
        with self.tracer.start_as_current_span("decision_sql.policy") as span:
            rejection = self.policy.validate(parsed)
            if rejection:
                self._record_stage(
                    "global_policy",
                    TraceStageStatus.REJECTED,
                    reason=rejection.code.value,
                    metadata={"object": rejection.object} if rejection.object else None,
                    duration_ms=(perf_counter() - policy_started) * 1000,
                )
                span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.POLICY_REJECTION)
                span.set_attribute("decision_sql.rejection_code", rejection.code)
                return self._policy_failure(rejection)
            span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.ALLOWED)
            span.set_attribute(
                "decision_sql.referenced_table_count",
                len(self._referenced_tables(parsed.expression)),
            )
            self._record_stage(
                "global_policy",
                TraceStageStatus.PASS,
                metadata={
                    "referenced_table_count": len(self._referenced_tables(parsed.expression))
                },
                duration_ms=(perf_counter() - policy_started) * 1000,
            )

        if candidate.execution_authority is None and candidate.source in {
            CandidateSource.LLM,
            CandidateSource.FUTURE_LLM,
        }:
            self._record_stage(
                "execution_authority",
                TraceStageStatus.REJECTED,
                reason=AuthorityCode.MISSING_REQUEST_AUTHORITY.value,
            )
            return self._authority_failure(
                AuthorityRejection(
                    code=AuthorityCode.MISSING_REQUEST_AUTHORITY,
                    message="Model-sourced SQL requires a request execution authority envelope.",
                )
            )

        authority = candidate.execution_authority
        if authority is None and candidate.source is not CandidateSource.INTERNAL:
            authority = self.default_execution_authority
        if authority is not None:
            authority_rejection = validate_authority(parsed.expression, authority)
            if authority_rejection:
                self._record_stage(
                    "execution_authority",
                    TraceStageStatus.REJECTED,
                    reason=authority_rejection.code.value,
                    metadata={"relations": authority_rejection.unauthorized_relations},
                )
                return self._authority_failure(authority_rejection)
            self._record_stage(
                "execution_authority",
                TraceStageStatus.PASS,
                metadata={"allowed_relation_count": len(authority.allowed_relations)},
            )
        else:
            self._record_stage(
                "execution_authority",
                TraceStageStatus.SKIPPED,
                reason="No authority envelope",
            )

        parsed_for_plan = parsed
        if self.grain_normalization_enabled:
            assert self.grain_coordinator is not None
            with self.tracer.start_as_current_span("decision_sql.semantic_grain") as span:
                decision = self.grain_coordinator.inspect(candidate.sql)
                self._record_grain_decision(span, decision)
            if decision.status is GrainRuntimeStatus.REJECTED:
                self._record_stage(
                    "grain_safety",
                    TraceStageStatus.REJECTED,
                    reason=decision.runtime_reason.value,
                )
                return self._semantic_failure(decision)
            self._record_stage(
                "grain_safety",
                TraceStageStatus.PASS,
                reason=decision.runtime_reason.value,
            )
            if decision.status is GrainRuntimeStatus.NORMALIZED:
                try:
                    parsed_for_plan = self.parser.parse(decision.selected_sql)
                except SQLParseFailure:
                    return SqlPlanFailure(
                        status=SqlSafetyStatus.SEMANTIC_REJECTION,
                        failure_stage=FailureStage.SEMANTIC_SAFETY_REJECTION,
                        error="Normalized SQL failed post-normalization parsing.",
                        semantic_reason="POST_NORMALIZATION_PARSE_REJECTED",
                    )
                post_rejection = self.policy.validate(parsed_for_plan)
                if post_rejection:
                    return SqlPlanFailure(
                        status=SqlSafetyStatus.SEMANTIC_REJECTION,
                        failure_stage=FailureStage.SEMANTIC_SAFETY_REJECTION,
                        error="Normalized SQL failed post-normalization policy validation.",
                        rejection=post_rejection,
                        semantic_reason="POST_NORMALIZATION_POLICY_REJECTED",
                    )
                if authority is not None:
                    post_authority_rejection = validate_authority(
                        parsed_for_plan.expression, authority
                    )
                    if post_authority_rejection:
                        return self._authority_failure(post_authority_rejection)
                if decision.output_diagnostic.code.value not in {"PASS", "NOT_APPLICABLE"}:
                    return SqlPlanFailure(
                        status=SqlSafetyStatus.SEMANTIC_REJECTION,
                        failure_stage=FailureStage.SEMANTIC_SAFETY_REJECTION,
                        error="Normalized SQL failed post-normalization grain validation.",
                        semantic_reason="POST_NORMALIZATION_GRAIN_REJECTED",
                    )

        else:
            self._record_stage(
                "grain_safety",
                TraceStageStatus.SKIPPED,
                reason="Normalization disabled",
            )

        connection_started = perf_counter()
        connection_opened = False
        try:
            with self.reader_engine.connect() as connection:
                connection_opened = True
                self._record_stage(
                    "planning_connection",
                    TraceStageStatus.PASS,
                    duration_ms=(perf_counter() - connection_started) * 1000,
                )
                with connection.begin():
                    self.executor.configure_transaction(connection)
                    normalized_sql = self.parser.normalize(parsed_for_plan)
                    with self.tracer.start_as_current_span("decision_sql.explain") as span:
                        try:
                            estimate = self.cost_gate.explain(connection, normalized_sql)
                        except Exception as error:
                            self._record_stage(
                                "explain",
                                TraceStageStatus.FAILED,
                                reason="EXPLAIN_ERROR",
                            )
                            span.set_attribute("decision_sql.policy_outcome", "EXPLAIN_ERROR")
                            raise _AbortedPlanning(
                                SqlPlanFailure(
                                    status=SqlSafetyStatus.EXECUTION_ERROR,
                                    failure_stage=FailureStage.EXECUTION_ERROR,
                                    error=(
                                        "Candidate SQL could not be explained by the "
                                        "restricted reader."
                                    ),
                                )
                            ) from error
                        self._record_estimate(span, estimate)
                        self._record_stage(
                            "explain",
                            TraceStageStatus.PASS,
                            metadata={
                                "estimated_cost": estimate.total_cost,
                                "estimated_rows": estimate.plan_rows,
                            },
                        )
                        if self.cost_gate.exceeds(
                            estimate, self.settings.max_plan_rows, self.settings.max_plan_cost
                        ):
                            self._record_stage(
                                "cost_gate",
                                TraceStageStatus.REJECTED,
                                reason=PolicyCode.QUERY_TOO_EXPENSIVE.value,
                            )
                            span.set_attribute(
                                "decision_sql.policy_outcome", SqlSafetyStatus.QUERY_COST_REJECTION
                            )
                            return SqlPlanFailure(
                                status=SqlSafetyStatus.QUERY_COST_REJECTION,
                                failure_stage=FailureStage.QUERY_COST_REJECTION,
                                rejection=PolicyRejection(
                                    code=PolicyCode.QUERY_TOO_EXPENSIVE,
                                    message="Query plan exceeds the configured cost policy.",
                                ),
                                estimate=estimate,
                            )

                    self._record_stage("cost_gate", TraceStageStatus.PASS)

                    plan = QueryPlan(
                        plan_id=uuid4(),
                        correlation_id=candidate.correlation_id,
                        candidate_source=candidate.source,
                        normalized_sql=normalized_sql,
                        statement_type=type(parsed_for_plan.expression).__name__,
                        referenced_tables=self._referenced_tables(parsed_for_plan.expression),
                        referenced_columns=self._referenced_columns(parsed_for_plan.expression),
                        referenced_functions=self._referenced_functions(parsed_for_plan.expression),
                        estimate=estimate,
                    )
                    self._accepted_plans[plan.plan_id] = plan
                    return plan
        except _AbortedPlanning as aborted:
            return aborted.result
        except ReaderRoleError:
            if not connection_opened:
                self._record_stage(
                    "planning_connection",
                    TraceStageStatus.FAILED,
                    reason="DATABASE_CONNECTION_ERROR",
                    duration_ms=(perf_counter() - connection_started) * 1000,
                )
            return SqlPlanFailure(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate planning requires the configured reader role.",
            )
        except Exception:
            if not connection_opened:
                self._record_stage(
                    "planning_connection",
                    TraceStageStatus.FAILED,
                    reason="DATABASE_CONNECTION_ERROR",
                    duration_ms=(perf_counter() - connection_started) * 1000,
                )
            return SqlPlanFailure(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate SQL could not be planned by the restricted reader.",
            )

    def execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError:
        result = self._execute(plan)
        self._record_execution(plan, result)
        return result

    def _execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError:
        """Execute only a QueryPlan issued by this service's successful plan call."""
        if not isinstance(plan, QueryPlan):
            return SqlExecutionError(error="Execution requires an accepted QueryPlan.")
        if self._accepted_plans.get(plan.plan_id) is not plan:
            return SqlExecutionError(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                error="Execution requires a QueryPlan issued by this service's plan method.",
            )

        started = perf_counter()
        connection_opened = False
        try:
            with self.reader_engine.connect() as connection:
                connection_opened = True
                self._record_stage("execution_connection", TraceStageStatus.PASS)
                with connection.begin():
                    self.executor.configure_transaction(connection)
                    with self.tracer.start_as_current_span("decision_sql.execute") as span:
                        result = self.executor._execute_on_connection(connection, plan)
                        if isinstance(result, SqlExecutionError):
                            raise _AbortedExecution(result)
                        execution = result.model_copy(
                            update={"latency_ms": (perf_counter() - started) * 1000}
                        )
                        span.set_attribute("decision_sql.row_count", execution.row_count)
                        span.set_attribute("decision_sql.truncated", execution.truncated)
                        span.set_attribute("decision_sql.latency_ms", execution.latency_ms)
                        self._record_stage(
                            "execution",
                            TraceStageStatus.PASS,
                            metadata={"row_count": execution.row_count},
                            duration_ms=execution.latency_ms,
                        )
                        return execution
        except _AbortedExecution as aborted:
            self._record_stage("execution", TraceStageStatus.FAILED, reason="EXECUTION_ERROR")
            return aborted.result
        except ReaderRoleError:
            if not connection_opened:
                self._record_stage(
                    "execution_connection",
                    TraceStageStatus.FAILED,
                    reason="DATABASE_CONNECTION_ERROR",
                )
            return SqlExecutionError(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                error="Candidate SQL execution requires the configured reader role.",
            )
        except Exception:
            if not connection_opened:
                self._record_stage(
                    "execution_connection",
                    TraceStageStatus.FAILED,
                    reason="DATABASE_CONNECTION_ERROR",
                )
            self._record_stage(
                "execution",
                TraceStageStatus.FAILED,
                reason="EXECUTION_ERROR",
            )
            return SqlExecutionError(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                error="Candidate SQL could not be executed by the restricted reader.",
            )

    def _record_plan(self, candidate: SqlCandidate, result: QueryPlan | SqlPlanFailure) -> None:
        rejection = (
            result.rejection.code.value
            if isinstance(result, SqlPlanFailure) and result.rejection
            else result.semantic_reason
            if isinstance(result, SqlPlanFailure) and result.semantic_reason
            else result.authority_rejection.code
            if isinstance(result, SqlPlanFailure) and result.authority_rejection
            else result.status.value
            if isinstance(result, SqlPlanFailure)
            else None
        )
        outcome = "ALLOWED" if isinstance(result, QueryPlan) else result.status.value
        recorder = recorder_for_identity(
            self.provenance_sink,
            candidate.correlation_id or f"candidate:{text_hash(candidate.sql)}",
        )
        recorder.emit(
            ProvenanceStage.M1_PLAN,
            ProvenanceEventType.M1_PLAN_COMPLETED,
            {
                "candidate_sql_hash": text_hash(candidate.sql),
                "m1_outcome": outcome,
                "m1_rejection_code": rejection,
            },
        )

    def _record_execution(
        self, plan: QueryPlan, result: QueryExecution | SqlExecutionError
    ) -> None:
        recorder = recorder_for_identity(
            self.provenance_sink,
            plan.correlation_id or f"plan:{plan.plan_id}",
        )
        recorder.emit(
            ProvenanceStage.EXECUTION,
            ProvenanceEventType.EXECUTION_COMPLETED,
            {"execution_outcome": "SUCCEEDED" if isinstance(result, QueryExecution) else "FAILED"},
        )

    @staticmethod
    def _policy_failure(rejection: PolicyRejection) -> SqlPlanFailure:
        return SqlPlanFailure(
            status=SqlSafetyStatus.POLICY_REJECTION,
            failure_stage=FailureStage.POLICY_REJECTION,
            rejection=rejection,
        )

    @staticmethod
    def _authority_failure(rejection: AuthorityRejection) -> SqlPlanFailure:
        return SqlPlanFailure(
            status=SqlSafetyStatus.AUTHORITY_REJECTION,
            failure_stage=FailureStage.AUTHORITY_REJECTION,
            error=rejection.message,
            authority_rejection=rejection,
        )

    @staticmethod
    def _semantic_failure(decision: GrainRuntimeDecision) -> SqlPlanFailure:
        return SqlPlanFailure(
            status=SqlSafetyStatus.SEMANTIC_REJECTION,
            failure_stage=FailureStage.SEMANTIC_SAFETY_REJECTION,
            error="Candidate SQL failed the semantic grain-safety boundary.",
            semantic_reason=decision.runtime_reason.value,
        )

    @staticmethod
    def _record_grain_decision(span: trace.Span, decision: GrainRuntimeDecision) -> None:
        span.set_attribute("decision_sql.grain_diagnostic", decision.input_diagnostic.code.value)
        span.set_attribute(
            "decision_sql.grain_output_diagnostic", decision.output_diagnostic.code.value
        )
        span.set_attribute("decision_sql.grain_normalization_status", decision.status.value)
        span.set_attribute("decision_sql.grain_normalization_reason", decision.runtime_reason.value)
        span.set_attribute("decision_sql.grain_input_sql_hash", decision.input_sql_hash)
        span.set_attribute("decision_sql.grain_selected_sql_hash", decision.selected_sql_hash)
        span.set_attribute(
            "decision_sql.semantic_rejected",
            decision.status is GrainRuntimeStatus.REJECTED,
        )

    @staticmethod
    def _referenced_tables(expression: exp.Expression) -> tuple[str, ...]:
        cte_names = {
            cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name
        }
        return tuple(
            sorted(
                {
                    table.name.lower()
                    for table in expression.find_all(exp.Table)
                    if table.name.lower() not in cte_names
                }
            )
        )

    @staticmethod
    def _referenced_columns(expression: exp.Expression) -> tuple[str, ...]:
        return tuple(
            sorted({column.sql(dialect="postgres") for column in expression.find_all(exp.Column)})
        )

    def _referenced_functions(self, expression: exp.Expression) -> tuple[str, ...]:
        return tuple(
            sorted(
                {self.policy.function_name(function) for function in expression.find_all(exp.Func)}
            )
        )

    @staticmethod
    def _record_estimate(span: trace.Span, estimate: ExplainEstimate) -> None:
        span.set_attribute("decision_sql.estimated_rows", estimate.plan_rows)
        span.set_attribute("decision_sql.estimated_cost", estimate.total_cost)
        span.set_attribute("decision_sql.top_level_node_type", estimate.top_level_node_type)

    def _record_stage(
        self,
        name: str,
        status: TraceStageStatus,
        *,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if self.stage_recorder is not None:
            self.stage_recorder.record_stage(
                name,
                status,
                reason=reason,
                metadata=metadata,
                duration_ms=duration_ms,
            )
