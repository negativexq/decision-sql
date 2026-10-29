import datetime as dt
from decimal import Decimal

from app.catalog.models import ColumnMetadata, SchemaCatalog, Sensitivity, TableMetadata
from app.decision.exploration import (
    Exploration,
    NoValueAccess,
    ProbeReport,
    report_rows,
    rows_may_be_shared,
)
from app.generation.provider import _production_decision_messages
from app.metadata.values import ValueSharing


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="users",
                description="Users.",
                columns=(
                    ColumnMetadata(name="id", type="integer", description="Key."),
                    ColumnMetadata(name="location", type="text", description="Where."),
                    ColumnMetadata(
                        name="salary",
                        type="numeric",
                        description="Pay.",
                        sensitivity=Sensitivity.CONFIDENTIAL,
                    ),
                ),
            ),
        )
    )


def test_rows_are_shared_only_when_every_column_read_may_be_shared() -> None:
    on = ValueSharing(enabled=True)
    probe = "SELECT location, COUNT(*) FROM users GROUP BY location"
    assert rows_may_be_shared(probe, _catalog(), on)
    assert not rows_may_be_shared(probe, _catalog(), NoValueAccess())
    assert not rows_may_be_shared(
        probe, _catalog(), ValueSharing(enabled=True, excluded=("users.location",))
    )
    assert not rows_may_be_shared("SELECT location FROM users WHERE salary > 1", _catalog(), on)
    assert not rows_may_be_shared("SELECT * FROM users", _catalog(), on)
    assert not rows_may_be_shared("SELECT (", _catalog(), on)


def test_a_report_without_sharing_keeps_counts_and_drops_values() -> None:
    rows: list[dict[str, object]] = [
        {"location": "York, England", "n": 3},
        {"location": "York", "n": 3},
        {"location": None, "n": 1},
    ]
    hidden = report_rows("SELECT ...", ["location", "n"], rows, truncated=False, shared=False)
    assert hidden.rows is None and hidden.row_count == 3
    assert [(c.name, c.distinct, c.nulls) for c in hidden.columns] == [
        ("location", 2, 1),
        ("n", 2, 0),
    ]

    long_text = "x" * 200
    shown = report_rows(
        "SELECT ...",
        ["v", "d", "m"],
        [{"v": long_text, "d": dt.date(2010, 1, 2), "m": Decimal("1.50")}],
        truncated=True,
        shared=True,
    )
    assert shown.more_rows and shown.rows is not None
    [(value, day, money)] = shown.rows
    assert value is not None and len(value) == 80 and value.endswith("…")
    assert (day, money) == ("2010-01-02", "1.50")


def test_the_request_says_whether_exploration_is_open_and_carries_results() -> None:
    report = ProbeReport(sql="SELECT 1", status="ROWS", row_count=0)
    [_, user] = _production_decision_messages(
        "q", "{}", exploration=Exploration(open=False, results=(report,))
    )
    assert "Exploration results:" in user["content"] and '"row_count": 0' in user["content"]
    assert user["content"].endswith("Exploration is closed: return a final decision.")
    [_, first] = _production_decision_messages("q", "{}", exploration=Exploration(open=True))
    assert "Exploration results" not in first["content"]
    assert first["content"].endswith("Exploration is open.")
    [_, none] = _production_decision_messages("q", "{}")
    assert "Exploration" not in none["content"]
