from decimal import Decimal

import pytest

from app.sql.results import keeps_ties, percent_out_of_range


def test_only_percentage_columns_outside_0_to_100_are_named() -> None:
    columns = ["name", "share_percent", "pct_change", "ratio", "Percentage"]
    rows: list[dict[str, object]] = [
        {"name": "a", "share_percent": Decimal("94.04"), "pct_change": -3, "ratio": 2.5},
        {"name": "b", "share_percent": 988.424, "pct_change": None, "Percentage": True},
    ]
    assert percent_out_of_range(columns, rows) == ("share_percent", "pct_change")
    assert percent_out_of_range(["percent"], [{"percent": 100}, {"percent": 0}]) == ()


@pytest.mark.parametrize(
    ("sql", "tied"),
    (
        ("SELECT name FROM heroes WHERE speed = (SELECT MAX(speed) FROM heroes)", True),
        ("SELECT name FROM heroes WHERE (SELECT MIN(speed) AS s FROM heroes) = speed", True),
        (
            "SELECT name FROM (SELECT name, DENSE_RANK() OVER (ORDER BY speed DESC) AS r "
            "FROM heroes) ranked WHERE r = 1",
            True,
        ),
        # A per-group extreme is one answer per group, not a tie.
        (
            "SELECT name FROM heroes h1 WHERE speed = "
            "(SELECT MAX(h2.speed) FROM heroes h2 WHERE h2.team = h1.team)",
            False,
        ),
        (
            "SELECT name FROM (SELECT name, RANK() OVER (PARTITION BY team ORDER BY speed DESC) "
            "AS r FROM heroes) ranked WHERE r = 1",
            False,
        ),
        ("SELECT name FROM heroes ORDER BY speed DESC LIMIT 1", False),
        ("SELECT name FROM heroes WHERE speed = (SELECT AVG(speed) FROM heroes)", False),
        ("SELECT (", False),
    ),
)
def test_keeps_ties(sql: str, tied: bool) -> None:
    assert keeps_ties(sql) is tied
