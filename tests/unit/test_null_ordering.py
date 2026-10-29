import pytest
import sqlglot

from app.sql.ordering import nulls_last


def _run(sql: str) -> tuple[str, bool]:
    checked = nulls_last(sqlglot.parse_one(sql, read="postgres"), sql)
    return checked.expression.sql(dialect="postgres"), checked.changed


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM player ORDER BY rating DESC LIMIT 1",
        "SELECT name FROM player ORDER BY rating DESC OFFSET 5",
        "SELECT name FROM player ORDER BY rating DESC FETCH FIRST 3 ROWS ONLY",
        "SELECT DISTINCT ON (team) team, name FROM player ORDER BY team, rating DESC",
        "SELECT name, RANK() OVER (ORDER BY rating DESC) FROM player",
        "SELECT name FROM (SELECT name, ROW_NUMBER() OVER (PARTITION BY team ORDER BY rating DESC)"
        " AS rn FROM player) AS ranked WHERE rn = 1",
        "SELECT name FROM player WHERE rating = (SELECT rating FROM player ORDER BY rating DESC"
        " LIMIT 1)",
    ],
)
def test_a_descending_order_that_picks_rows_puts_missing_values_last(sql: str) -> None:
    text, changed = _run(sql)
    assert changed and "DESC NULLS LAST" in text


@pytest.mark.parametrize(
    "sql",
    [
        # The order only arranges the rows; every row comes back either way.
        "SELECT name FROM player ORDER BY rating DESC",
        "SELECT name, SUM(goals) OVER (ORDER BY rating DESC) FROM player",
        # Ascending already puts NULL last in PostgreSQL.
        "SELECT name FROM player ORDER BY rating LIMIT 1",
        # A count is never NULL.
        "SELECT team FROM player GROUP BY team ORDER BY COUNT(*) DESC LIMIT 1",
        # The author asked for it.
        "SELECT name FROM player ORDER BY rating DESC NULLS FIRST LIMIT 1",
        "SELECT name FROM player ORDER BY rating DESC NULLS LAST LIMIT 1",
    ],
)
def test_other_orders_are_left_as_written(sql: str) -> None:
    _, changed = _run(sql)
    assert not changed


def test_nulls_first_inside_a_string_is_not_a_request() -> None:
    text, changed = _run("SELECT 'nulls first' AS note FROM player ORDER BY rating DESC LIMIT 1")
    assert changed and "DESC NULLS LAST" in text
