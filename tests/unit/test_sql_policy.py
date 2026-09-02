from sqlalchemy import create_engine

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.sql.models import PolicyCode, SqlCandidate, SqlSafetyStatus
from app.sql.parser import SQLParser
from app.sql.policy import SQLPolicy
from app.sql.service import SqlSafetyService


def policy() -> SQLPolicy:
    return SQLPolicy(build_default_catalog(Base.metadata))


def parsed(sql: str):
    return SQLParser().parse(sql)


def test_safe_analytical_shapes_are_allowed() -> None:
    sql_policy = policy()
    queries = (
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

    assert all(sql_policy.validate(parsed(query)) is None for query in queries)


def test_mutation_and_multiple_statements_are_rejected_before_execution() -> None:
    sql_policy = policy()
    assert (
        sql_policy.validate(parsed("DELETE FROM orders")).code
        is PolicyCode.NON_READ_ONLY_STATEMENT
    )
    assert (
        sql_policy.validate(parsed("SELECT * INTO hacked FROM products")).code
        is PolicyCode.NON_READ_ONLY_STATEMENT
    )
    assert (
        sql_policy.validate(parsed("SELECT * FROM products FOR UPDATE")).code
        is PolicyCode.NON_READ_ONLY_STATEMENT
    )
    assert (
        SqlSafetyService(create_engine("sqlite://"))
        .plan(SqlCandidate(sql="SELECT * FROM products; DROP TABLE products"))
        .status
        is SqlSafetyStatus.SQL_PARSE_ERROR
    )


def test_catalog_table_and_column_policy_is_deterministic() -> None:
    sql_policy = policy()
    cases = {
        "SELECT * FROM pg_shadow": PolicyCode.FORBIDDEN_CATALOG,
        "SELECT * FROM pg_roles": PolicyCode.FORBIDDEN_CATALOG,
        "SELECT * FROM pg_catalog.pg_tables": PolicyCode.FORBIDDEN_CATALOG,
        "SELECT * FROM random_unknown_table": PolicyCode.UNKNOWN_TABLE,
        "SELECT external_key FROM customers": PolicyCode.FORBIDDEN_COLUMN,
        "SELECT * FROM customers": PolicyCode.FORBIDDEN_COLUMN,
        "SELECT pg_read_file('/etc/passwd')": PolicyCode.FORBIDDEN_FUNCTION,
    }

    for sql, expected_code in cases.items():
        rejection = sql_policy.validate(parsed(sql))
        assert rejection is not None
        assert rejection.code is expected_code


def test_policy_rejection_does_not_reach_explain() -> None:
    service = SqlSafetyService(create_engine("sqlite://"))
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("EXPLAIN must not run for rejected SQL")

    service.cost_gate.explain = fail_if_called
    result = service.plan(SqlCandidate(sql="SELECT * FROM pg_roles"))

    assert result.status is SqlSafetyStatus.POLICY_REJECTION
    assert called is False
