from app.catalog.models import ColumnMetadata, SchemaCatalog, TableMetadata
from app.sql.parser import SQLParser
from app.sql.precision import SinglePrecisionSums


def _widen(sql: str):
    catalog = SchemaCatalog(
        tables=(
            TableMetadata(
                name="yearmonth",
                description="Monthly consumption.",
                columns=(
                    ColumnMetadata(name="customerid", type="bigint", description="Key."),
                    ColumnMetadata(name="consumption", type="real", description="Amount."),
                ),
            ),
        )
    )
    checked = SinglePrecisionSums(catalog).widen(SQLParser().parse(sql).expression)
    return checked.changed, checked.expression.sql(dialect="postgres")


def test_a_sum_of_a_real_column_adds_in_double_precision() -> None:
    changed, sql = _widen("SELECT y.customerid, SUM(y.consumption) FROM yearmonth y GROUP BY 1")
    assert changed and "SUM(CAST(y.consumption AS DOUBLE PRECISION))" in sql

    changed, sql = _widen(
        "WITH t AS (SELECT SUM(consumption * 2) AS s FROM yearmonth) SELECT s FROM t"
    )
    assert changed and "SUM(CAST(consumption AS DOUBLE PRECISION) * 2)" in sql


def test_other_aggregates_and_cast_columns_are_left_as_written() -> None:
    assert _widen("SELECT AVG(consumption) FROM yearmonth")[0] is False
    assert _widen("SELECT SUM(consumption::numeric) FROM yearmonth")[0] is False
    assert _widen("SELECT SUM(customerid) FROM yearmonth")[0] is False
