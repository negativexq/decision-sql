import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_admin_engine, build_reader_engine
from app.semantics.measures import measure_catalog_from_schema
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
from tests.integration.conftest import postgres_unavailable


@pytest.fixture(scope="module")
def service() -> SqlSafetyService:
    engine = build_reader_engine(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        postgres_unavailable(
            "PostgreSQL is not available; run Docker Compose for integration tests"
        )
    return SqlSafetyService(engine)


def run_candidate(
    service: SqlSafetyService, sql: str
) -> QueryExecution | SqlPlanFailure | SqlExecutionError:
    planned = service.plan(SqlCandidate(sql=sql))
    if isinstance(planned, QueryPlan):
        return service.execute(planned)
    return planned


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


@pytest.mark.parametrize(
    "sql",
    (
        "SELECT CURRENT_DATE",
        "SELECT CURRENT_TIME",
        "SELECT CURRENT_TIMESTAMP",
        "SELECT LOCALTIME",
        "SELECT LOCALTIMESTAMP",
        "SELECT NOW()",
        "SELECT TRANSACTION_TIMESTAMP()",
        "SELECT STATEMENT_TIMESTAMP()",
    ),
)
def test_stable_temporal_functions_execute_read_only(service: SqlSafetyService, sql: str) -> None:
    result = run_candidate(service, sql)
    assert isinstance(result, QueryExecution)
    assert result.executed_at_utc is not None
    assert result.executed_at_utc.tzinfo is not None
    assert result.session_timezone


def test_stable_temporal_functions_are_consistent_within_one_statement(
    service: SqlSafetyService,
) -> None:
    result = run_candidate(
        service,
        "SELECT CURRENT_TIMESTAMP AS current_ts, NOW() AS now_ts, "
        "TRANSACTION_TIMESTAMP() AS transaction_ts, "
        "STATEMENT_TIMESTAMP() AS statement_ts",
    )
    assert isinstance(result, QueryExecution)
    assert len(result.rows) == 1
    values = list(result.rows[0].values())
    assert values[0] == values[1] == values[2]
    assert values[3] >= values[0]


def test_ambiguous_unqualified_column_fails_closed_like_postgres(
    service: SqlSafetyService,
) -> None:
    ambiguous = "SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id LIMIT 1"
    qualified = "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id LIMIT 1"

    planned = service.plan(SqlCandidate(sql=ambiguous))
    assert isinstance(planned, SqlPlanFailure)
    assert planned.rejection is not None
    assert planned.rejection.code is PolicyCode.UNKNOWN_COLUMN
    assert "Ambiguous unqualified column" in planned.rejection.message

    qualified_result = run_candidate(service, qualified)
    assert isinstance(qualified_result, QueryExecution)

    with service.reader_engine.connect() as connection:
        with pytest.raises(SQLAlchemyError):
            connection.exec_driver_sql(ambiguous)


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


def test_grain_safety_stops_parent_measure_fanout_before_execution(
    service: SqlSafetyService,
) -> None:
    guarded = SqlSafetyService(
        service.reader_engine,
        catalog=service.catalog,
        measure_catalog=measure_catalog_from_schema(service.catalog),
        grain_normalization_enabled=True,
    )
    fanout = guarded.plan(
        SqlCandidate(
            sql="SELECT p.category, SUM(o.total_amount) FROM orders o "
            "JOIN order_items oi ON oi.order_id = o.id "
            "JOIN products p ON p.id = oi.product_id GROUP BY p.category"
        )
    )
    assert isinstance(fanout, SqlPlanFailure)
    safe = guarded.plan(
        SqlCandidate(sql="SELECT status, SUM(total_amount) FROM orders GROUP BY status")
    )
    assert isinstance(safe, QueryPlan)
    assert not isinstance(guarded.execute(safe), SqlExecutionError)


def test_the_top_row_of_a_descending_order_is_a_value_not_a_missing_one(
    service: SqlSafetyService,
) -> None:
    # Half the amounts are NULL; PostgreSQL alone would return one of those first.
    planned = service.plan(
        SqlCandidate(
            sql="SELECT id, CASE WHEN id % 2 = 0 THEN total_amount END AS amount "
            "FROM orders ORDER BY amount DESC LIMIT 1"
        )
    )
    assert isinstance(planned, QueryPlan)
    assert "NULLS LAST" in planned.normalized_sql and planned.sql_changes
    result = service.execute(planned)
    assert isinstance(result, QueryExecution)
    assert result.rows[0]["amount"] is not None


def test_the_value_probe_finds_a_stored_spelling_in_the_database(
    service: SqlSafetyService,
) -> None:
    probed = service.probe_values(
        "SELECT COUNT(*) FROM products p "
        "WHERE p.name = 'office item 03' AND p.category IN ('Office', 'stationery')"
    )

    assert probed is not None
    assert "'Office Item 03'" in probed.expression.sql(dialect="postgres")
    assert [(item.column, item.literal) for item in probed.unmatched] == [
        ("category", "stationery")
    ]
    # A query on no probed column, or one that does not parse, has nothing to look up.
    assert service.probe_values("SELECT COUNT(*) FROM products WHERE id = 1") is None
    assert service.probe_values("SELECT FROM WHERE") is None


def test_a_failed_cast_is_named_by_its_class_without_the_database_text(
    service: SqlSafetyService,
) -> None:
    planned = service.plan(SqlCandidate(sql="SELECT name::numeric AS n FROM products LIMIT 5"))
    assert isinstance(planned, QueryPlan)
    result = service.execute(planned)

    assert isinstance(result, SqlExecutionError)
    assert result.error_class == "INVALID_TEXT_CONVERSION"
    assert "numeric" not in result.error


def test_a_probe_runs_through_the_gates_without_touching_the_trace(
    service: SqlSafetyService,
) -> None:
    recorded: list[str] = []
    service.stage_recorder = lambda name, *args, **kwargs: recorded.append(name)  # type: ignore[assignment]
    try:
        plan, execution = service.explore(
            SqlCandidate(sql="SELECT category, COUNT(*) AS n FROM products GROUP BY category")
        )
        assert isinstance(plan, QueryPlan) and isinstance(execution, QueryExecution)
        assert execution.rows

        rejected, nothing = service.explore(SqlCandidate(sql="DELETE FROM products"))
        assert isinstance(rejected, SqlPlanFailure) and nothing is None
        assert recorded == []
    finally:
        service.stage_recorder = None
