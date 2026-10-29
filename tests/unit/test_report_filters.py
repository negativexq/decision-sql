from __future__ import annotations

from datetime import date

import pytest
import sqlglot

from app.catalog.models import ColumnMetadata, SchemaCatalog, TableMetadata
from app.reports.filters import (
    FilterChoice,
    FilterKind,
    ReportFilter,
    apply_filters,
    check_filters,
    filters_for,
)

STATUS = ReportFilter(id="status", label="Status", table="orders", column="status", kind="values")
ORDERED = ReportFilter(
    id="ordered", label="Ordered", table="orders", column="ordered_at", kind=FilterKind.DATE_RANGE
)
REGION = ReportFilter(id="region", label="Region", table="regions", column="name", kind="values")


def _same(left: str, right: str) -> None:
    assert (
        sqlglot.transpile(left, read="postgres")[0] == sqlglot.transpile(right, read="postgres")[0]
    )


def test_nothing_picked_leaves_the_query_alone() -> None:
    sql = "SELECT status, COUNT(*) FROM orders GROUP BY status"
    result = apply_filters(sql, [STATUS], {"status": FilterChoice()})
    assert result.sql == sql and result.applied == () and result.skipped == ()


def test_values_and_dates_narrow_each_select_that_reads_the_table() -> None:
    result = apply_filters(
        "SELECT o.status, SUM(o.total) FROM orders AS o WHERE o.total > 0 GROUP BY o.status",
        [STATUS, ORDERED],
        {
            "status": FilterChoice(values=["completed", "it's"]),
            "ordered": FilterChoice(start=date(2025, 1, 1), end=date(2025, 1, 31)),
        },
    )
    _same(
        result.sql,
        "SELECT o.status, SUM(o.total) FROM orders AS o WHERE (o.total > 0 "
        "AND o.status IN ('completed', 'it''s')) "
        "AND (o.ordered_at >= CAST('2025-01-01' AS DATE) "
        "AND o.ordered_at < CAST('2025-02-01' AS DATE)) "
        "GROUP BY o.status",
    )
    assert result.applied == ("status", "ordered")


def test_a_filter_reaches_ctes_and_subqueries_but_not_tables_the_query_does_not_read() -> None:
    sql = (
        "WITH paid AS (SELECT customer_id, total FROM orders) "
        "SELECT c.name, (SELECT MAX(total) FROM orders) AS top "
        "FROM customers c JOIN paid p ON p.customer_id = c.id"
    )
    result = apply_filters(
        sql,
        [STATUS, REGION],
        {"status": FilterChoice(values=["completed"]), "region": FilterChoice(values=["EU"])},
    )
    assert result.applied == ("status",) and result.skipped == ("region",)
    assert result.sql.count("status IN ('completed')") == 2
    assert filters_for(sql, [STATUS, REGION]) == ["status"]


def test_a_left_joined_table_is_narrowed_in_its_join_so_unmatched_rows_stay() -> None:
    result = apply_filters(
        "SELECT c.name, COUNT(o.id) FROM customers c "
        "LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.name",
        [STATUS],
        {"status": FilterChoice(values=["completed"])},
    )
    _same(
        result.sql,
        "SELECT c.name, COUNT(o.id) FROM customers AS c LEFT JOIN orders AS o "
        "ON o.customer_id = c.id AND o.status IN ('completed') GROUP BY c.name",
    )
    right = apply_filters(
        "SELECT o.id FROM orders o RIGHT JOIN customers c ON o.customer_id = c.id",
        [STATUS],
        {"status": FilterChoice(values=["completed"])},
    )
    assert right.applied == () and right.skipped == ("status",)


def test_filters_must_name_a_readable_column_of_the_right_kind() -> None:
    catalog = SchemaCatalog(
        tables=(
            TableMetadata(
                name="orders",
                description="",
                columns=(
                    ColumnMetadata(name="status", type="text", description=""),
                    ColumnMetadata(
                        name="ordered_at", type="timestamp with time zone", description=""
                    ),
                    ColumnMetadata(name="secret", type="text", description="", queryable=False),
                ),
            ),
        )
    )
    check_filters([STATUS, ORDERED], catalog)
    for bad in (
        STATUS.model_copy(update={"column": "secret"}),
        STATUS.model_copy(update={"table": "nowhere"}),
        ORDERED.model_copy(update={"column": "status"}),
    ):
        with pytest.raises(ValueError):
            check_filters([bad], catalog)
    with pytest.raises(ValueError, match="share an id"):
        check_filters([STATUS, STATUS], catalog)
    with pytest.raises(ValueError):
        FilterChoice(start=date(2025, 2, 1), end=date(2025, 1, 1))
