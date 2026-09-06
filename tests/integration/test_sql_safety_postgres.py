import os

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_admin_engine, build_reader_engine
from app.generation.provider import StaticLLMProvider
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextResolver
from app.sql.models import (
    PolicyCode,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.models import BaselineCase
from evaluation.runner import evaluate_baseline


@pytest.fixture(scope="module")
def service() -> SqlSafetyService:
    engine = build_reader_engine(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not available; run Docker Compose for integration tests")
    return SqlSafetyService(engine)


def run_candidate(
    service: SqlSafetyService, sql: str
) -> QueryExecution | SqlPlanFailure | SqlExecutionError:
    planned = service.plan(SqlCandidate(sql=sql))
    if isinstance(planned, QueryPlan):
        return service.execute(planned)
    return planned


def build_text_to_sql_service(service: SqlSafetyService, sql: str) -> TextToSqlService:
    return TextToSqlService(
        SchemaContextResolver(
            service.catalog,
            top_k=service.settings.schema_top_k,
            max_tables=service.settings.max_context_tables,
            max_columns_per_table=service.settings.max_columns_per_table,
            relationship_depth=service.settings.relationship_depth,
        ),
        StaticLLMProvider(sql),
        service,
    )


def test_safe_select_join_cte_and_window_queries_execute(service: SqlSafetyService) -> None:
    safe_queries = (
        "SELECT * FROM products LIMIT 10",
        """SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue
           FROM order_items oi JOIN products p ON p.id = oi.product_id
           GROUP BY p.name ORDER BY revenue DESC LIMIT 5""",
        """WITH customer_totals AS (
             SELECT customer_id, SUM(total_amount) AS spend
             FROM orders GROUP BY customer_id
           ) SELECT * FROM customer_totals ORDER BY spend DESC LIMIT 10""",
        """SELECT p.category, ROW_NUMBER() OVER (ORDER BY p.category) AS category_rank
           FROM products p LIMIT 4""",
    )

    plans = [service.plan(SqlCandidate(sql=query)) for query in safe_queries]
    assert all(isinstance(plan, QueryPlan) for plan in plans)
    results = [service.execute(plan) for plan in plans if isinstance(plan, QueryPlan)]

    assert all(result.status is SqlSafetyStatus.ALLOWED for result in results)
    assert results[0].row_count == 10
    assert results[2].row_count == 10
    assert results[3].row_count == 4


def test_m212_compiled_window_ir_remains_behind_m1(service: SqlSafetyService) -> None:
    ir = WindowQueryIR(
        source_relation="orders",
        pattern="MOVING_AGGREGATE",
        physical_outputs=("orders.customer_id", "orders.id", "orders.total_amount"),
        computations=(
            {
                "pattern": "MOVING_AGGREGATE",
                "aggregate": "AVG",
                "target": "orders.total_amount",
                "partition_by": ("orders.customer_id",),
                "order_by": ({"column": "orders.ordered_at", "direction": "ASC"},),
                "frame": {
                    "mode": "ROWS",
                    "start": {"kind": "N_PRECEDING", "value": 2},
                    "end": {"kind": "CURRENT_ROW"},
                },
                "alias": "moving_average",
            },
        ),
    )
    sql = WindowSqlCompiler(service.catalog).compile(ir)
    planned = service.plan(SqlCandidate(sql=sql, source="window_compiler"))
    assert isinstance(planned, QueryPlan)
    result = service.execute(planned)
    assert isinstance(result, QueryExecution)
    assert result.row_count > 0
    assert planned.candidate_source.value == "window_compiler"


@pytest.mark.parametrize(
    ("sql", "code"),
    (
        ("DELETE FROM orders", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("UPDATE customers SET name = 'x'", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("INSERT INTO regions (id, name) VALUES (999, 'x')", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("SELECT * INTO hacked FROM products", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("SELECT * FROM products FOR UPDATE", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("DROP TABLE products", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("ALTER TABLE products ADD COLUMN hacked text", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("CREATE TABLE hacked (id integer)", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("TRUNCATE products", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("COPY customers TO '/tmp/customers.csv'", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("CALL do_not_call()", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("DO $$ BEGIN NULL; END $$", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("GRANT SELECT ON orders TO public", PolicyCode.NON_READ_ONLY_STATEMENT),
        ("REVOKE SELECT ON orders FROM public", PolicyCode.NON_READ_ONLY_STATEMENT),
        (
            "MERGE INTO orders AS target USING orders AS source "
            "ON target.id = source.id WHEN MATCHED THEN UPDATE "
            "SET total_amount = source.total_amount",
            PolicyCode.NON_READ_ONLY_STATEMENT,
        ),
        ("SELECT * FROM products; DROP TABLE products", None),
        ("SELECT * FROM pg_shadow", PolicyCode.FORBIDDEN_CATALOG),
        ("SELECT * FROM pg_roles", PolicyCode.FORBIDDEN_CATALOG),
        ("SELECT * FROM pg_catalog.pg_tables", PolicyCode.FORBIDDEN_CATALOG),
        ("SELECT * FROM random_unknown_table", PolicyCode.UNKNOWN_TABLE),
        ("SELECT external_key FROM customers", PolicyCode.FORBIDDEN_COLUMN),
        ("SELECT pg_read_file('/etc/passwd')", PolicyCode.FORBIDDEN_FUNCTION),
    ),
)
def test_adversarial_sql_is_rejected_without_execution(
    service: SqlSafetyService, sql: str, code: PolicyCode | None
) -> None:
    result = service.plan(SqlCandidate(sql=sql))

    if code is None:
        assert result.status is SqlSafetyStatus.SQL_PARSE_ERROR
    else:
        assert result.status is SqlSafetyStatus.POLICY_REJECTION
        assert result.rejection is not None
        assert result.rejection.code is code


def test_reader_is_read_only_and_cannot_mutate(service: SqlSafetyService) -> None:
    before = run_candidate(service, "SELECT count(*) AS total FROM regions")

    with service.reader_engine.connect() as connection:
        with connection.begin():
            service.executor.configure_transaction(connection)
            with pytest.raises(SQLAlchemyError):
                connection.exec_driver_sql(
                    "INSERT INTO regions (id, name) VALUES (999, 'must-fail')"
                )

    after = run_candidate(service, "SELECT count(*) AS total FROM regions")
    assert isinstance(before, QueryExecution)
    assert isinstance(after, QueryExecution)
    assert before.rows == after.rows


def test_executor_does_not_expose_raw_candidate_execution(service: SqlSafetyService) -> None:
    assert not hasattr(service.executor, "execute")


def test_execution_requires_a_plan_issued_by_the_same_service(service: SqlSafetyService) -> None:
    planned = service.plan(SqlCandidate(sql="SELECT id FROM products LIMIT 1"))
    assert isinstance(planned, QueryPlan)

    copied = QueryPlan.model_validate(planned.model_dump())
    result = service.execute(copied)

    assert isinstance(result, SqlExecutionError)
    assert result.error == "Execution requires a QueryPlan issued by this service's plan method."


def test_plan_contains_evidence_but_no_result_rows(service: SqlSafetyService) -> None:
    planned = service.plan(
        SqlCandidate(
            sql="SELECT category, COUNT(*) AS products FROM products GROUP BY category",
            correlation_id="plan-only-1",
        )
    )

    assert isinstance(planned, QueryPlan)
    assert planned.correlation_id == "plan-only-1"
    assert planned.candidate_source.value == "internal"
    assert planned.referenced_tables == ("products",)
    assert planned.referenced_functions == ("COUNT",)
    assert planned.estimate.plan_rows > 0
    assert not hasattr(planned, "rows")


def test_result_rows_are_bounded(service: SqlSafetyService) -> None:
    bounded = SqlSafetyService(
        service.reader_engine,
        settings=service.settings.model_copy(update={"max_result_rows": 5}),
    )

    result = run_candidate(bounded, "SELECT id FROM products ORDER BY id")

    assert result.status is SqlSafetyStatus.ALLOWED
    assert result.row_count == 5
    assert result.truncated is True


def test_cost_gate_rejects_before_execution(service: SqlSafetyService) -> None:
    gated = SqlSafetyService(
        service.reader_engine,
        settings=service.settings.model_copy(update={"max_plan_cost": 0.0}),
    )

    result = gated.plan(SqlCandidate(sql="SELECT * FROM products"))

    assert result.status is SqlSafetyStatus.QUERY_COST_REJECTION
    assert result.rejection is not None
    assert result.rejection.code is PolicyCode.QUERY_TOO_EXPENSIVE
    assert result.estimate is not None


def test_cost_gate_explains_multiple_percent_literals(service: SqlSafetyService) -> None:
    planned = service.plan(
        SqlCandidate(
            sql="""SELECT name
           FROM products
           WHERE name LIKE '%abc%'
              OR name LIKE 'foo%'
              OR name LIKE '%bar'"""
        )
    )

    assert isinstance(planned, QueryPlan)
    assert planned.statement_type == "Select"


def test_reader_executes_multiple_percent_literals_without_mutating_plan(
    service: SqlSafetyService,
) -> None:
    sql = """SELECT name
              FROM products
              WHERE name LIKE '%abc%'
                 OR name LIKE 'foo%'
                 OR name LIKE '%bar'"""
    planned = service.plan(SqlCandidate(sql=sql))
    assert isinstance(planned, QueryPlan)
    logical_sql = planned.normalized_sql

    result = service.execute(planned)

    assert isinstance(result, QueryExecution)
    assert "%abc%" in logical_sql
    assert "%%abc%%" not in logical_sql


def test_cost_gate_without_percent_and_policy_rejection_remain_unchanged(
    service: SqlSafetyService,
) -> None:
    ordinary = service.plan(SqlCandidate(sql="SELECT id FROM products LIMIT 1"))
    assert isinstance(ordinary, QueryPlan)

    rejected = service.plan(SqlCandidate(sql="SELECT pg_read_file('/etc/passwd')"))
    assert isinstance(rejected, SqlPlanFailure)
    assert rejected.status is SqlSafetyStatus.POLICY_REJECTION
    assert rejected.rejection is not None
    assert rejected.rejection.code is PolicyCode.FORBIDDEN_FUNCTION


def test_statement_timeout_is_enforced(service: SqlSafetyService) -> None:
    timed = SqlSafetyService(
        service.reader_engine,
        settings=service.settings.model_copy(
            update={
                "statement_timeout_ms": 25,
                "max_plan_cost": 2_000_000_000.0,
                "max_plan_rows": 10_000_000_000,
            }
        ),
    )

    result = run_candidate(
        timed,
        """SELECT COUNT(*)
           FROM orders a
           CROSS JOIN order_items b
           CROSS JOIN orders c
           CROSS JOIN order_items d""",
    )

    assert result.status is SqlSafetyStatus.EXECUTION_ERROR
    assert result.failure_stage.value == "EXECUTION_ERROR"


def test_admin_connection_cannot_reach_candidate_execution(service: SqlSafetyService) -> None:
    if not os.getenv("ADMIN_DATABASE_URL"):
        pytest.skip("Set ADMIN_DATABASE_URL explicitly for the admin-separation integration test")
    admin_service = SqlSafetyService(build_admin_engine(get_settings()))

    result = admin_service.plan(SqlCandidate(sql="SELECT * FROM products LIMIT 1"))

    assert result.status is SqlSafetyStatus.EXECUTION_ERROR
    assert result.error == "Candidate planning requires the configured reader role."


def test_successful_pipeline_emits_bounded_stage_spans(service: SqlSafetyService) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    traced = SqlSafetyService(service.reader_engine, tracer=provider.get_tracer("integration"))

    planned = traced.plan(SqlCandidate(sql="SELECT id FROM products LIMIT 1"))
    assert isinstance(planned, QueryPlan)
    result = traced.execute(planned)

    assert result.status is SqlSafetyStatus.ALLOWED
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == [
        "decision_sql.validate",
        "decision_sql.policy",
        "decision_sql.explain",
        "decision_sql.execute",
    ]
    assert all(
        key not in {"decision_sql.sql", "decision_sql.rows", "decision_sql.result_rows"}
        for span in spans
        for key in span.attributes
    )


@pytest.mark.asyncio
async def test_m2_static_provider_uses_m1_for_safe_sql(service: SqlSafetyService) -> None:
    coordinator = build_text_to_sql_service(service, "SELECT id, name FROM products LIMIT 2")

    result = await coordinator.run(
        TextToSqlRequest(question="list products", correlation_id="m2-1")
    )

    assert result.status is TextToSqlStatus.SUCCEEDED
    assert result.candidate is not None
    assert result.candidate.source.value == "llm"
    assert result.plan is not None
    assert result.execution is not None
    assert result.execution.row_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    ("DELETE FROM orders", "SELECT * FROM pg_shadow", "SELECT external_key FROM customers"),
)
async def test_m2_malicious_provider_output_stops_at_m1(
    service: SqlSafetyService, sql: str
) -> None:
    coordinator = build_text_to_sql_service(service, sql)

    result = await coordinator.run(TextToSqlRequest(question="show sales"))

    assert result.status is TextToSqlStatus.PLAN_REJECTED
    assert result.failure_stage is FailureStage.POLICY_REJECTION
    assert result.plan is None
    assert result.execution is None


@pytest.mark.asyncio
async def test_m2_baseline_compares_execution_results_not_sql_text(
    service: SqlSafetyService,
) -> None:
    sql = "SELECT id, name FROM products LIMIT 2"
    coordinator = build_text_to_sql_service(service, sql)
    cases = [
        BaselineCase(
            id="m2-eval-1",
            question="list products",
            gold_sql=sql,
            category="simple_filters",
            expected_tables=("products",),
        )
    ]

    report = await evaluate_baseline(cases, coordinator)

    assert report.total_cases == 1
    assert report.plan_acceptance_rate == 1.0
    assert report.execution_success_rate == 1.0
    assert report.result_equivalence_rate == 1.0
