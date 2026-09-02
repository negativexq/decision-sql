from time import perf_counter

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
    SqlExecutionResult,
    SqlSafetyStatus,
)
from app.sql.parser import SQLParseFailure, SQLParser
from app.sql.policy import SQLPolicy


class _AbortedExecution(Exception):
    def __init__(self, result: SqlExecutionResult) -> None:
        self.result = result


class SqlSafetyService:
    """The only M1 entry point for candidate SQL: parse, policy, explain, execute."""

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

    def execute(self, candidate_sql: str) -> SqlExecutionResult:
        started = perf_counter()
        with self.tracer.start_as_current_span("decision_sql.validate") as span:
            span.set_attribute("decision_sql.statement_length", len(candidate_sql))
            try:
                parsed = self.parser.parse(candidate_sql)
            except SQLParseFailure as error:
                span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.SQL_PARSE_ERROR)
                return SqlExecutionResult(
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
                return self._policy_result(rejection)
            span.set_attribute("decision_sql.policy_outcome", SqlSafetyStatus.ALLOWED)
            span.set_attribute(
                "decision_sql.referenced_table_count",
                len({table.name for table in parsed.expression.find_all(exp.Table)}),
            )

        try:
            with self.reader_engine.connect() as connection:
                with connection.begin():
                    self.executor.configure_transaction(connection)
                    with self.tracer.start_as_current_span("decision_sql.explain") as span:
                        try:
                            estimate = self.cost_gate.explain(connection, parsed.sql)
                        except Exception as error:
                            span.set_attribute("decision_sql.policy_outcome", "EXPLAIN_ERROR")
                            raise _AbortedExecution(
                                SqlExecutionResult(
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
                            return SqlExecutionResult(
                                status=SqlSafetyStatus.QUERY_COST_REJECTION,
                                failure_stage=FailureStage.QUERY_COST_REJECTION,
                                rejection=PolicyRejection(
                                    code=PolicyCode.QUERY_TOO_EXPENSIVE,
                                    message="Query plan exceeds the configured cost policy.",
                                ),
                                estimate=estimate,
                            )

                    with self.tracer.start_as_current_span("decision_sql.execute") as span:
                        result = self.executor.execute_on_connection(connection, parsed.sql)
                        if result.status is SqlSafetyStatus.EXECUTION_ERROR:
                            raise _AbortedExecution(result)
                        span.set_attribute("decision_sql.row_count", result.row_count)
                        span.set_attribute("decision_sql.truncated", result.truncated)
                        result.estimate = estimate
                        span.set_attribute(
                            "decision_sql.latency_ms", (perf_counter() - started) * 1000
                        )
                        return result
        except _AbortedExecution as aborted:
            return aborted.result
        except ReaderRoleError:
            return SqlExecutionResult(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate SQL execution requires the configured reader role.",
            )
        except Exception:
            return SqlExecutionResult(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate SQL could not be executed by the restricted reader.",
            )

    @staticmethod
    def _policy_result(rejection: PolicyRejection) -> SqlExecutionResult:
        return SqlExecutionResult(
            status=SqlSafetyStatus.POLICY_REJECTION,
            failure_stage=FailureStage.POLICY_REJECTION,
            rejection=rejection,
        )

    @staticmethod
    def _record_estimate(span: trace.Span, estimate: ExplainEstimate) -> None:
        span.set_attribute("decision_sql.estimated_rows", estimate.plan_rows)
        span.set_attribute("decision_sql.estimated_cost", estimate.total_cost)
        span.set_attribute("decision_sql.top_level_node_type", estimate.top_level_node_type)
