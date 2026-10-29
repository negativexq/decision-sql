from sqlalchemy import create_engine

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.sql.functions import model_visible_functions
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

    # Stable temporal context is safe; volatile time-dependent values remain
    # a separate deny boundary.
    for query in queries[:-1]:
        assert sql_policy.validate(parsed(query)) is None
    assert sql_policy.validate(parsed(queries[-1])) is None


def test_postgres_jsonb_text_extraction_is_read_only_and_reviewed() -> None:
    sql_policy = policy()
    query = (
        "SELECT JSONB_BUILD_OBJECT('profile', JSONB_BUILD_OBJECT('display_name', 'x')) "
        "#>> '{profile,display_name}'"
    )

    assert sql_policy.validate(parsed(query)) is None


def test_function_policy_remains_deny_first() -> None:
    sql_policy = policy()
    rejected = {
        "SELECT PG_SLEEP(1)": "PG_SLEEP",
        "SELECT SET_CONFIG('x', 'y', false)": "SET_CONFIG",
        "SELECT RANDOM()": "RANDOM",
        "SELECT CLOCK_TIMESTAMP()": "CLOCK_TIMESTAMP",
        "SELECT UNKNOWN_EXTENSION_FUNCTION(1)": "UNKNOWN_EXTENSION_FUNCTION",
    }

    for query, name in rejected.items():
        rejection = sql_policy.validate(parsed(query))
        assert rejection is not None
        assert rejection.code is PolicyCode.FORBIDDEN_FUNCTION
        assert rejection.object == name


def test_pure_text_and_numeric_functions_are_allowed() -> None:
    sql_policy = policy()
    queries = (
        "SELECT LOWER(name), UPPER(name), INITCAP(name), LENGTH(name) FROM products",
        "SELECT SUBSTRING(name FROM 1 FOR 3), SUBSTR(name, 1, 3), LEFT(name, 2), RIGHT(name, 2) "
        "FROM products",
        "SELECT BTRIM(name), LTRIM(name), REPLACE(name, 'a', 'b'), POSITION('a' IN name), "
        "STRPOS(name, 'a') FROM products",
        "SELECT CONCAT(name, '!'), SPLIT_PART(name, ' ', 1), LPAD(name, 10, ' ') FROM products",
        "SELECT FLOOR(unit_price), CEIL(unit_price), TRUNC(unit_price), SIGN(unit_price) "
        "FROM products",
        "SELECT JSONB_OBJECT_AGG(name, unit_price) FROM products",
    )

    for query in queries:
        assert sql_policy.validate(parsed(query)) is None, query

    for query in (
        "SELECT REGEXP_REPLACE(name, 'a+', 'b') FROM products",
        "SELECT MD5(name) FROM products",
    ):
        rejection = sql_policy.validate(parsed(query))
        assert rejection is not None
        assert rejection.code is PolicyCode.FORBIDDEN_FUNCTION


def test_every_function_shown_to_the_model_passes_the_policy() -> None:
    """The model sees ``model_visible_functions``; none of them may be rejected at runtime."""
    special = {
        "CURRENT_DATE": "CURRENT_DATE",
        "CURRENT_TIME": "CURRENT_TIME",
        "CURRENT_TIMESTAMP": "CURRENT_TIMESTAMP",
        "LOCALTIME": "LOCALTIME",
        "LOCALTIMESTAMP": "LOCALTIMESTAMP",
        "ARRAY": "ARRAY[1, 2]",
        "CAST": "CAST(unit_price AS INTEGER)",
        "EXISTS": "EXISTS(SELECT 1 FROM products)",
        "EXTRACT": "EXTRACT(YEAR FROM CURRENT_DATE)",
        "POSITION": "POSITION('a' IN name)",
        "MODE": "MODE() WITHIN GROUP (ORDER BY name)",
        "PERCENTILE_CONT": "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY unit_price)",
        "PERCENTILE_DISC": "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY unit_price)",
    }
    sql_policy = policy()
    for name in model_visible_functions():
        call = special.get(name)
        candidates = (
            [call] if call else [f"{name}(name)", f"{name}(name, 1)", f"{name}(name, 1, 2)"]
        )
        for candidate in candidates:
            try:
                statement = parsed(f"SELECT {candidate} FROM products")
            except Exception:  # noqa: BLE001 - try the next arity
                continue
            break
        else:
            raise AssertionError(f"no parsable call for {name}")
        rejection = sql_policy.validate(statement)
        assert rejection is None or rejection.code is not PolicyCode.FORBIDDEN_FUNCTION, name


def test_temporal_policy_distinguishes_stable_context_from_volatile_values() -> None:
    sql_policy = policy()
    stable = (
        "SELECT CURRENT_DATE",
        "SELECT CURRENT_TIME",
        "SELECT CURRENT_TIMESTAMP",
        "SELECT LOCALTIME",
        "SELECT LOCALTIMESTAMP",
        "SELECT NOW()",
        "SELECT TRANSACTION_TIMESTAMP()",
        "SELECT STATEMENT_TIMESTAMP()",
    )
    for query in stable:
        assert sql_policy.validate(parsed(query)) is None

    for query in ("SELECT RANDOM()", "SELECT CLOCK_TIMESTAMP()"):
        rejection = sql_policy.validate(parsed(query))
        assert rejection is not None
        assert rejection.code is PolicyCode.FORBIDDEN_FUNCTION


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


def test_scope_validation_rejects_ambiguous_unqualified_columns() -> None:
    sql_policy = policy()
    valid = (
        "SELECT total_amount FROM customers c JOIN orders o ON o.customer_id = c.id",
        "SELECT c.id, o.id FROM customers c JOIN orders o ON o.customer_id = c.id",
        "SELECT derived_col FROM (SELECT name AS derived_col FROM products) q",
        "SELECT p.id FROM products p WHERE EXISTS "
        "(SELECT 1 FROM order_items oi WHERE oi.product_id = p.id)",
        "SELECT name AS product_name FROM products ORDER BY product_name",
        "SELECT ROW_TO_JSON(p) FROM products p LIMIT 1",
    )
    invalid = (
        "SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id",
        "SELECT metric FROM (SELECT unit_price AS metric FROM products) a "
        "JOIN (SELECT total_amount AS metric FROM orders) b ON 1 = 1",
    )

    for query in valid:
        assert sql_policy.validate(parsed(query)) is None
    for query in invalid:
        rejection = sql_policy.validate(parsed(query))
        assert rejection is not None
        assert rejection.code is PolicyCode.UNKNOWN_COLUMN
        assert rejection.message.startswith("Ambiguous unqualified column")


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


def test_values_alias_columns_resolve() -> None:
    sql_policy = policy()
    queries = (
        """SELECT pair.label, pair.amount FROM orders o
           CROSS JOIN LATERAL (VALUES ('subtotal', o.subtotal), ('total', o.total_amount))
           AS pair(label, amount)""",
        "SELECT v.code FROM (VALUES ('a'), ('b')) AS v(code)",
    )

    assert all(sql_policy.validate(parsed(query)) is None for query in queries)
    unknown = sql_policy.validate(
        parsed(
            """SELECT pair.missing FROM orders o
               CROSS JOIN LATERAL (VALUES (o.subtotal)) AS pair(amount)"""
        )
    )
    assert unknown is not None and unknown.code is PolicyCode.UNKNOWN_COLUMN


def test_joins_must_follow_an_authorized_relationship() -> None:
    sql_policy = policy()
    allowed = (
        "SELECT c.name, o.total_amount FROM orders o JOIN customers c ON o.customer_id = c.id",
        "SELECT c.name FROM customers c JOIN orders o ON c.id = o.customer_id::int",
        "SELECT o.id FROM orders o, customers c WHERE c.id = o.customer_id AND c.name = 'x'",
        "SELECT c.name FROM customers c WHERE EXISTS "
        "(SELECT 1 FROM orders o WHERE o.customer_id = c.id)",
        # A table compared with itself, and CTE columns, are not traced.
        "SELECT a.id FROM orders a JOIN orders b ON a.customer_id = b.customer_id",
        "WITH t AS (SELECT id AS key FROM customers) "
        "SELECT o.id FROM orders o JOIN t ON t.key = o.id",
    )
    for query in allowed:
        assert sql_policy.validate(parsed(query)) is None, query

    rejected = {
        "SELECT c.name FROM orders o JOIN customers c ON o.id = c.id": "linked only by",
        "SELECT c.name FROM orders o, customers c WHERE o.id = c.id": "linked only by",
        "SELECT c.name FROM customers c WHERE EXISTS "
        "(SELECT 1 FROM orders o WHERE o.id = c.id)": "linked only by",
        "SELECT p.name FROM products p JOIN customers c ON c.id = p.id": "no authorized",
    }
    for query, wording in rejected.items():
        rejection = sql_policy.validate(parsed(query))
        assert rejection is not None, query
        assert rejection.code is PolicyCode.UNAUTHORIZED_JOIN
        assert wording in rejection.message
    message = sql_policy.validate(
        parsed("SELECT c.name FROM orders o JOIN customers c ON o.id = c.id")
    ).message  # type: ignore[union-attr]
    assert "orders.customer_id = customers.id" in message
