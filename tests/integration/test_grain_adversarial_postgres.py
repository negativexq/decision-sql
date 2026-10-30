"""Adversarial grain and join SQL, checked against what PostgreSQL returns.

Each case pairs a candidate with a reference query that answers the same
question correctly. A rejected case must be rejected by the gates, and where
the demo data can show it, running it anyway must give a different answer than
the reference. An allowed case must pass the gates and return the reference's
rows. Every order in the demo data has two items, so a parent measure that
fans out over them doubles.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.semantics.measures import measure_catalog_from_schema
from app.sql.models import (
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.sql.service import SqlSafetyService
from tests.integration.conftest import postgres_unavailable


@dataclass(frozen=True)
class Case:
    name: str
    sql: str
    reference: str
    # False when the demo data happens to give the right answer anyway, so only
    # the verdict can be checked.
    wrong_on_demo: bool = True
    # The grain check refuses fan-outs; the join policy refuses unrelated joins.
    refused_by: SqlSafetyStatus = SqlSafetyStatus.SEMANTIC_REJECTION


STATUS_TOTALS = "SELECT status, SUM(total_amount) FROM orders GROUP BY status ORDER BY 1"

REJECTED = (
    Case(
        "parent measure summed over its child rows",
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "parent measure summed per parent key over its child rows",
        "SELECT o.id, SUM(o.total_amount) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.id ORDER BY 1",
        "SELECT id, total_amount FROM orders ORDER BY 1",
    ),
    Case(
        "per-order sum over child rows, summed again outside a CTE",
        "WITH t AS (SELECT o.id, o.status, SUM(o.total_amount) AS amount FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.id, o.status) "
        "SELECT status, SUM(amount) FROM t GROUP BY status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "join made inside a CTE, parent measure summed outside it",
        "WITH j AS (SELECT o.status, o.total_amount FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id) "
        "SELECT status, SUM(total_amount) FROM j GROUP BY status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "raw child rows joined through a CTE",
        "WITH items AS (SELECT order_id, quantity FROM order_items) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN items i ON i.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "raw child rows joined through a derived table",
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN (SELECT order_id FROM order_items) i ON i.order_id = o.id "
        "GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "child rows kept distinct on more than the join key",
        "WITH items AS (SELECT DISTINCT order_id, product_id FROM order_items) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN items i ON i.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "child rows grouped on more than the join key",
        "WITH items AS (SELECT order_id, product_id, SUM(quantity) AS q FROM order_items "
        "GROUP BY order_id, product_id) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN items i ON i.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "child rows grouped on more than the join key, passed through another CTE",
        "WITH items AS (SELECT order_id, product_id FROM order_items "
        "GROUP BY order_id, product_id), "
        "wrapped AS (SELECT order_id, product_id FROM items) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN wrapped w ON w.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "child rows kept distinct on more than the join key, the extra key dropped outside",
        "WITH items AS (SELECT DISTINCT order_id, product_id FROM order_items), "
        "wrapped AS (SELECT order_id FROM items) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN wrapped w ON w.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "child rows grouped on more than the join key, in nested derived tables",
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN (SELECT order_id FROM (SELECT order_id, product_id FROM order_items "
        "GROUP BY order_id, product_id) x) w ON w.order_id = o.id "
        "GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "raw child rows beside an aggregate in a scalar subquery",
        "WITH items AS (SELECT oi.order_id, (SELECT COUNT(*) FROM order_items) AS n "
        "FROM order_items oi) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN items i ON i.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "raw child rows beside an aggregate in a scalar subquery, in a derived table",
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN (SELECT oi.order_id, (SELECT MAX(quantity) FROM order_items) AS m "
        "FROM order_items oi) i ON i.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "parent measure summed across a grandchild's parent",
        "SELECT p.category, SUM(o.total_amount) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id "
        "JOIN products p ON p.id = oi.product_id GROUP BY p.category ORDER BY 1",
        # Each order counted once per category it has items in.
        "SELECT c.category, SUM(o.total_amount) FROM (SELECT DISTINCT p.category, oi.order_id "
        "FROM order_items oi JOIN products p ON p.id = oi.product_id) c "
        "JOIN orders o ON o.id = c.order_id GROUP BY c.category ORDER BY 1",
        # No demo order has two items of one category, so nothing doubles here.
        wrong_on_demo=False,
    ),
    Case(
        "SUM(DISTINCT) of a parent measure over its child rows",
        "SELECT o.status, SUM(DISTINCT o.total_amount) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
        # Every order total is distinct in the demo data, so DISTINCT happens to
        # undo the fan-out; two equal totals would be merged.
        wrong_on_demo=False,
    ),
    Case(
        "order joined to a customer by id instead of customer_id",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.id GROUP BY c.region_id ORDER BY 1",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.customer_id GROUP BY c.region_id ORDER BY 1",
        refused_by=SqlSafetyStatus.POLICY_REJECTION,
    ),
    Case(
        "order joined to a customer by id through a CTE",
        "WITH c AS (SELECT id, region_id FROM customers) "
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN c ON c.id = o.id GROUP BY c.region_id ORDER BY 1",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.customer_id GROUP BY c.region_id ORDER BY 1",
        refused_by=SqlSafetyStatus.POLICY_REJECTION,
    ),
    Case(
        "order and customer joined on a shared sales rep",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.sales_rep_id = o.sales_rep_id GROUP BY c.region_id ORDER BY 1",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.customer_id GROUP BY c.region_id ORDER BY 1",
        refused_by=SqlSafetyStatus.POLICY_REJECTION,
    ),
    Case(
        "orders cross joined to regions",
        "SELECT r.id, SUM(o.total_amount) FROM orders o CROSS JOIN regions r "
        "GROUP BY r.id ORDER BY 1",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.customer_id GROUP BY c.region_id ORDER BY 1",
        refused_by=SqlSafetyStatus.POLICY_REJECTION,
    ),
    Case(
        "orders joined to customers by a range",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id <= o.customer_id GROUP BY c.region_id ORDER BY 1",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.customer_id GROUP BY c.region_id ORDER BY 1",
        refused_by=SqlSafetyStatus.POLICY_REJECTION,
    ),
)

ALLOWED = (
    Case(
        "child rows preaggregated per order",
        "WITH i AS (SELECT order_id, SUM(quantity) AS q FROM order_items GROUP BY order_id) "
        "SELECT o.status, SUM(o.total_amount), SUM(i.q) FROM orders o "
        "JOIN i ON i.order_id = o.id GROUP BY o.status ORDER BY 1",
        "SELECT o.status, SUM(o.total_amount), SUM((SELECT SUM(quantity) FROM order_items "
        "WHERE order_id = o.id)) FROM orders o GROUP BY o.status ORDER BY 1",
    ),
    Case(
        "child rows kept distinct on the join key",
        "WITH i AS (SELECT DISTINCT order_id FROM order_items) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN i ON i.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "child rows grouped on the join key, renamed and filtered in another CTE",
        "WITH items AS (SELECT order_id, SUM(quantity) AS q FROM order_items "
        "GROUP BY order_id), "
        "wrapped AS (SELECT order_id AS oid, q FROM items WHERE q > 3) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN wrapped w ON w.oid = o.id GROUP BY o.status ORDER BY 1",
        "SELECT status, SUM(total_amount) FROM orders WHERE id IN "
        "(SELECT order_id FROM order_items GROUP BY order_id HAVING SUM(quantity) > 3) "
        "GROUP BY status ORDER BY 1",
    ),
    Case(
        "child rows kept distinct on the join key, passed through with a star",
        "WITH items AS (SELECT DISTINCT order_id FROM order_items), "
        "wrapped AS (SELECT * FROM items) "
        "SELECT o.status, SUM(o.total_amount) FROM orders o "
        "JOIN wrapped w ON w.order_id = o.id GROUP BY o.status ORDER BY 1",
        STATUS_TOTALS,
    ),
    Case(
        "child rows as an EXISTS filter",
        "SELECT o.status, SUM(o.total_amount) FROM orders o WHERE EXISTS "
        "(SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.quantity > 1) "
        "GROUP BY o.status ORDER BY 1",
        "SELECT status, SUM(total_amount) FROM orders WHERE id IN "
        "(SELECT order_id FROM order_items WHERE quantity > 1) GROUP BY status ORDER BY 1",
    ),
    Case(
        "child measure summed over the join",
        "SELECT o.status, SUM(oi.quantity * oi.unit_price) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.status ORDER BY 1",
        "SELECT o.status, SUM((SELECT SUM(quantity * unit_price) FROM order_items "
        "WHERE order_id = o.id)) FROM orders o GROUP BY o.status ORDER BY 1",
    ),
    Case(
        "parent measure averaged per parent key over its child rows",
        "SELECT o.id, AVG(o.total_amount) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.id ORDER BY 1",
        "SELECT id, total_amount FROM orders ORDER BY 1",
    ),
    Case(
        "parent measure maximum over its child rows",
        "SELECT o.status, MAX(o.total_amount) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY o.status ORDER BY 1",
        "SELECT status, MAX(total_amount) FROM orders GROUP BY status ORDER BY 1",
    ),
    Case(
        "child measure summed under its parent",
        "SELECT c.region_id, SUM(o.total_amount) FROM customers c "
        "JOIN orders o ON o.customer_id = c.id GROUP BY c.region_id ORDER BY 1",
        "SELECT c.region_id, (SELECT SUM(total_amount) FROM orders o JOIN customers c2 "
        "ON c2.id = o.customer_id WHERE c2.region_id = c.region_id) FROM customers c "
        "GROUP BY c.region_id ORDER BY 1",
    ),
    Case(
        "order totals per customer through a CTE",
        "WITH t AS (SELECT customer_id, SUM(total_amount) AS spend FROM orders "
        "GROUP BY customer_id) "
        "SELECT c.region_id, SUM(t.spend) FROM customers c "
        "JOIN t ON t.customer_id = c.id GROUP BY c.region_id ORDER BY 1",
        "SELECT c.region_id, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.customer_id GROUP BY c.region_id ORDER BY 1",
    ),
)


@pytest.fixture(scope="module")
def guarded() -> SqlSafetyService:
    engine = build_reader_engine(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        postgres_unavailable(
            "PostgreSQL is not available; run Docker Compose for integration tests"
        )
    plain = SqlSafetyService(engine)
    return SqlSafetyService(
        engine,
        catalog=plain.catalog,
        measure_catalog=measure_catalog_from_schema(plain.catalog),
        grain_normalization_enabled=True,
    )


def rows_of(service: SqlSafetyService, sql: str) -> list[tuple[object, ...]]:
    with service.reader_engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql))]


@pytest.mark.parametrize("case", REJECTED, ids=lambda case: case.name)
def test_wrong_sql_is_refused(guarded: SqlSafetyService, case: Case) -> None:
    if case.wrong_on_demo:
        assert rows_of(guarded, case.sql) != rows_of(guarded, case.reference)

    refusal = guarded.plan(SqlCandidate(sql=case.sql))

    assert isinstance(refusal, SqlPlanFailure)
    assert refusal.status is case.refused_by


def test_two_unnamed_aggregates_keep_both_values(guarded: SqlSafetyService) -> None:
    plan = guarded.plan(
        SqlCandidate(
            sql="SELECT status, SUM(total_amount), SUM(subtotal) FROM orders GROUP BY status"
        )
    )
    assert isinstance(plan, QueryPlan)
    result = guarded.execute(plan)

    assert isinstance(result, QueryExecution)
    assert result.columns == ["status", "sum", "sum_2"]
    assert all(len(row) == 3 for row in result.rows)


@pytest.mark.parametrize("case", ALLOWED, ids=lambda case: case.name)
def test_right_sql_returns_the_reference_answer(guarded: SqlSafetyService, case: Case) -> None:
    plan = guarded.plan(SqlCandidate(sql=case.sql))
    assert isinstance(plan, QueryPlan), plan

    result = guarded.execute(plan)

    assert isinstance(result, QueryExecution), result
    assert [tuple(row.values()) for row in result.rows] == rows_of(guarded, case.reference)
