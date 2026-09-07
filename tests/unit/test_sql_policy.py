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


def test_predicates_and_case_expressions_are_not_treated_as_functions() -> None:
    sql_policy = policy()

    assert (
        sql_policy.validate(
            parsed(
                "SELECT SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) "
                "FROM orders WHERE status = 'completed' AND ordered_at >= '2025-01-01'"
            )
        )
        is None
    )


def test_reviewed_analytical_function_families_are_allowed() -> None:
    sql_policy = policy()
    queries = (
        "SELECT ABS(unit_price), ROUND(unit_price, 2), POWER(unit_price, 2) FROM products",
        "SELECT STRING_AGG(name, ', '), ARRAY_AGG(name), JSON_AGG(name) FROM products",
        "SELECT JSON_BUILD_OBJECT('name', name), ARRAY_TO_STRING(ARRAY[name], ',') FROM products",
        "SELECT NULLIF(unit_price, 0), GREATEST(unit_price, 0), "
        "LEAST(unit_price, 100) FROM products",
        "SELECT UNNEST(ARRAY[1, 2])",
        "SELECT EXISTS(SELECT 1 FROM products), TO_CHAR(CURRENT_DATE, 'YYYY')",
    )

    # CURRENT_DATE is intentionally a determinism boundary, so the last
    # query is checked separately below with a literal date.
    for query in queries[:-1]:
        assert sql_policy.validate(parsed(query)) is None
    assert sql_policy.validate(parsed("SELECT TO_CHAR(DATE '2025-01-01', 'YYYY')")) is None
    rejection = sql_policy.validate(parsed(queries[-1]))
    assert rejection is not None
    assert rejection.code is PolicyCode.FORBIDDEN_FUNCTION
    assert rejection.object == "CURRENT_DATE"


def test_function_policy_remains_deny_first() -> None:
    sql_policy = policy()
    rejected = {
        "SELECT PG_SLEEP(1)": "PG_SLEEP",
        "SELECT SET_CONFIG('x', 'y', false)": "SET_CONFIG",
        "SELECT RANDOM()": "RANDOM",
        "SELECT UNKNOWN_EXTENSION_FUNCTION(1)": "UNKNOWN_EXTENSION_FUNCTION",
    }

    for query, name in rejected.items():
        rejection = sql_policy.validate(parsed(query))
        assert rejection is not None
        assert rejection.code is PolicyCode.FORBIDDEN_FUNCTION
        assert rejection.object == name


def test_scope_validation_supports_nested_relations_and_correlated_columns() -> None:
    sql_policy = policy()
    valid = (
        "WITH named AS (SELECT name AS product_name FROM products) "
        "SELECT named.product_name FROM named",
        "SELECT p.product_name FROM (SELECT name AS product_name FROM products) AS p",
        "WITH first_level AS (SELECT name AS product_name FROM products), "
        "second_level AS (SELECT product_name FROM first_level) "
        "SELECT second_level.product_name FROM second_level",
        "SELECT p.id FROM products AS p WHERE EXISTS "
        "(SELECT 1 FROM order_items AS oi WHERE oi.product_id = p.id)",
        "WITH named(product_name) AS (SELECT name FROM products) "
        "SELECT named.product_name FROM named",
        "SELECT name AS product_name FROM products ORDER BY product_name",
    )
    for query in valid:
        assert sql_policy.validate(parsed(query)) is None


def test_scope_validation_rejects_invalid_derived_and_physical_columns() -> None:
    sql_policy = policy()
    invalid = (
        "SELECT p.missing FROM (SELECT name AS product_name FROM products) AS p",
        "SELECT products.missing FROM products",
        "WITH named(product_name) AS (SELECT name FROM products) SELECT named.name FROM named",
        "SELECT name AS product_name FROM products WHERE product_name = 'x'",
    )
    for query in invalid:
        rejection = sql_policy.validate(parsed(query))
        assert rejection is not None
        assert rejection.code is PolicyCode.UNKNOWN_COLUMN


def test_mutation_and_multiple_statements_are_rejected_before_execution() -> None:
    sql_policy = policy()
    assert (
        sql_policy.validate(parsed("DELETE FROM orders")).code is PolicyCode.NON_READ_ONLY_STATEMENT
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
