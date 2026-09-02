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
from app.observability.tracing import get_tracer
from app.sql.models import (
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
    ) -> None:
        self.settings = settings or get_settings()
        self.reader_engine = reader_engine
        self.parser = SQLParser()
        self.catalog = catalog or build_default_catalog(Base.metadata)
        self.policy = SQLPolicy(self.catalog)
        self.cost_gate = QueryCostGate()
        self.executor = ReadOnlyExecutor(
            reader_engine,
            statement_timeout_ms=self.settings.statement_timeout_ms,
            max_rows=self.settings.max_result_rows,
            reader_role=self.settings.reader_role,
        )
        self.tracer = tracer or get_tracer()
        self._accepted_plans: dict[UUID, QueryPlan] = {}

    def plan(self, candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure:
        """Parse, authorize, cost-check, and return an executable QueryPlan."""
        with self.tracer.start_as_current_span("decision_sql.validate") as span:
            span.set_attribute("decision_sql.statement_length", len(candidate.sql))
            try:
                parsed = self.parser.parse(candidate.sql)
            except SQLParseFailure as error:
                span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.SQL_PARSE_ERROR)
                return SqlPlanFailure(
                    status=SqlSafetyStatus.SQL_PARSE_ERROR,
                    failure_stage=FailureStage.SQL_PARSE_ERROR,
                    error=str(error),
                )
            span.set_attribute("decision_sql.statement_type", type(parsed.expression).__name__)

        with self.tracer.start_as_current_span("decision_sql.policy") as span:
            rejection = self.policy.validate(parsed)
            if rejection:
                span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.POLICY_REJECTION)
                span.set_attribute("decision_sql.rejection_code", rejection.code)
                return self._policy_failure(rejection)
            span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.ALLOWED)
            span.set_attribute(
                "decision_sql.referenced_table_count",
                len(self._referenced_tables(parsed.expression)),
            )

        try:
            with self.reader_engine.connect() as connection:
                with connection.begin():
                    self.executor.configure_transaction(connection)
                    normalized_sql = parsed.expression.sql(dialect="postgres")
                    with self.tracer.start_as_current_span("decision_sql.explain") as span:
                        try:
                            estimate = self.cost_gate.explain(connection, normalized_sql)
                        except Exception as error:
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
                        if self.cost_gate.exceeds(
                            estimate, self.settings.max_plan_rows, self.settings.max_plan_cost
                        ):
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

                    plan = QueryPlan(
                        plan_id=uuid4(),
                        correlation_id=candidate.correlation_id,
                        candidate_source=candidate.source,
                        normalized_sql=normalized_sql,
                        statement_type=type(parsed.expression).__name__,
                        referenced_tables=self._referenced_tables(parsed.expression),
                        referenced_columns=self._referenced_columns(parsed.expression),
                        referenced_functions=self._referenced_functions(parsed.expression),
                        estimate=estimate,
                    )
                    self._accepted_plans[plan.plan_id] = plan
                    return plan
        except _AbortedPlanning as aborted:
            return aborted.result
        except ReaderRoleError:
            return SqlPlanFailure(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate planning requires the configured reader role.",
            )
        except Exception:
            return SqlPlanFailure(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate SQL could not be planned by the restricted reader.",
            )

    def execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError:
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
        try:
            with self.reader_engine.connect() as connection:
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
                        return execution
        except _AbortedExecution as aborted:
            return aborted.result
        except ReaderRoleError:
            return SqlExecutionError(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                error="Candidate SQL execution requires the configured reader role.",
            )
        except Exception:
            return SqlExecutionError(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                error="Candidate SQL could not be executed by the restricted reader.",
            )

    @staticmethod
    def _policy_failure(rejection: PolicyRejection) -> SqlPlanFailure:
        return SqlPlanFailure(
            status=SqlSafetyStatus.POLICY_REJECTION,
            failure_stage=FailureStage.POLICY_REJECTION,
            rejection=rejection,
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
                {
                    self.policy.function_name(function)
                    for function in expression.find_all(exp.Func)
                }
            )
        )

    @staticmethod
    def _record_estimate(span: trace.Span, estimate: ExplainEstimate) -> None:
        span.set_attribute("decision_sql.estimated_rows", estimate.plan_rows)
        span.set_attribute("decision_sql.estimated_cost", estimate.total_cost)
        span.set_attribute("decision_sql.top_level_node_type", estimate.top_level_node_type)
