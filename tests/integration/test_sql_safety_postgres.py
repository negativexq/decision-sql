import os

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_admin_engine, build_reader_engine
from app.sql.models import PolicyCode, SqlSafetyStatus
from app.sql.service import SqlSafetyService


@pytest.fixture(scope="module")
def service() -> SqlSafetyService:
    engine = build_reader_engine(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not available; run Docker Compose for integration tests")
    return SqlSafetyService(engine)


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

    results = [service.execute(query) for query in safe_queries]

    assert all(result.status is SqlSafetyStatus.ALLOWED for result in results)
    assert results[0].row_count == 10
    assert results[1].estimate is not None
    assert results[2].row_count == 10
    assert results[3].row_count == 4


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
    result = service.execute(sql)

    if code is None:
        assert result.status is SqlSafetyStatus.SQL_PARSE_ERROR
    else:
        assert result.status is SqlSafetyStatus.POLICY_REJECTION
        assert result.rejection is not None
        assert result.rejection.code is code


def test_reader_is_read_only_and_cannot_mutate(service: SqlSafetyService) -> None:
    before = service.execute("SELECT count(*) AS total FROM regions")

    result = service.executor.execute("INSERT INTO regions (id, name) VALUES (999, 'must-fail')")

    after = service.execute("SELECT count(*) AS total FROM regions")
    assert result.status is SqlSafetyStatus.EXECUTION_ERROR
    assert before.rows == after.rows


def test_result_rows_are_bounded(service: SqlSafetyService) -> None:
    bounded = SqlSafetyService(
        service.reader_engine,
        settings=service.settings.model_copy(update={"max_result_rows": 5}),
    )

    result = bounded.execute("SELECT id FROM products ORDER BY id")

    assert result.status is SqlSafetyStatus.ALLOWED
    assert result.row_count == 5
    assert result.truncated is True


def test_cost_gate_rejects_before_execution(service: SqlSafetyService) -> None:
    gated = SqlSafetyService(
        service.reader_engine,
        settings=service.settings.model_copy(update={"max_plan_cost": 0.0}),
    )

    result = gated.execute("SELECT * FROM products")

    assert result.status is SqlSafetyStatus.QUERY_COST_REJECTION
    assert result.rejection is not None
    assert result.rejection.code is PolicyCode.QUERY_TOO_EXPENSIVE
    assert result.estimate is not None


def test_statement_timeout_is_enforced(service: SqlSafetyService) -> None:
    timed = SqlSafetyService(
        service.reader_engine,
        settings=service.settings.model_copy(
            update={
                "statement_timeout_ms": 25,
                "max_plan_cost": 1_000_000_000.0,
                "max_plan_rows": 10_000_000_000,
            }
        ),
    )

    result = timed.execute(
        """SELECT COUNT(*)
           FROM orders a
           CROSS JOIN order_items b
           CROSS JOIN orders c
           CROSS JOIN order_items d"""
    )

    assert result.status is SqlSafetyStatus.EXECUTION_ERROR
    assert result.failure_stage.value == "EXECUTION_ERROR"


def test_admin_connection_cannot_reach_candidate_execution(service: SqlSafetyService) -> None:
    if not os.getenv("ADMIN_DATABASE_URL"):
        pytest.skip("Set ADMIN_DATABASE_URL explicitly for the admin-separation integration test")
    admin_service = SqlSafetyService(build_admin_engine(get_settings()))

    result = admin_service.execute("SELECT * FROM products LIMIT 1")

    assert result.status is SqlSafetyStatus.EXECUTION_ERROR
    assert result.error == "Candidate SQL execution requires the configured reader role."


def test_successful_pipeline_emits_bounded_stage_spans(service: SqlSafetyService) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    traced = SqlSafetyService(service.reader_engine, tracer=provider.get_tracer("integration"))

    result = traced.execute("SELECT id FROM products LIMIT 1")

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
