from decimal import Decimal
from uuid import uuid4

import pytest

from app.sql.models import QueryExecution, SqlSafetyStatus
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticExecutionSnapshot,
    DiagnosticFixture,
    DiagnosticFixtureBank,
    DiagnosticState,
    _snapshot,
    compare_snapshots,
    stable_hash,
    text_hash,
    validate_fixture_bank,
)


def snapshot(
    rows: tuple[tuple[tuple[str, dict[str, object]], ...], ...],
    columns: tuple[str, ...] = ("value",),
) -> DiagnosticExecutionSnapshot:
    return DiagnosticExecutionSnapshot(
        query_hash="q",
        fixture_id="fixture",
        columns=columns,
        typed_rows=rows,
        row_count=len(rows),
        truncated=False,
        result_hash=stable_hash(rows),
        latency_ms=0.0,
    )


def test_typed_values_preserve_null_decimal_and_case() -> None:
    execution = QueryExecution(
        plan_id=uuid4(),
        status=SqlSafetyStatus.ALLOWED,
        columns=["value"],
        rows=[{"value": None}, {"value": Decimal("1.20")}, {"value": "Completed"}],
        row_count=3,
        latency_ms=0.0,
    )
    fixture = DiagnosticFixture(
        fixture_id="fixture",
        fixture_version="v1",
        schema_version="schema",
        seed="seed",
        content_hash="unused",
    )
    result = _snapshot(execution, "query", fixture)
    assert result.typed_rows[0][0][1]["type"] == "NULL"
    assert result.typed_rows[1][0][1]["type"] == "DECIMAL"
    assert result.typed_rows[1][0][1]["value"] == "1.20"
    assert result.typed_rows[2][0][1]["value"] == "Completed"


def test_unordered_snapshot_hash_is_stable_across_database_row_order() -> None:
    fixture = DiagnosticFixture(
        fixture_id="fixture",
        fixture_version="v1",
        schema_version="schema",
        seed="seed",
        content_hash="unused",
    )
    first = QueryExecution(
        plan_id=uuid4(),
        status=SqlSafetyStatus.ALLOWED,
        columns=["value"],
        rows=[{"value": 1}, {"value": 2}],
        row_count=2,
        latency_ms=0.0,
    )
    second = first.model_copy(update={"rows": [{"value": 2}, {"value": 1}]})
    assert _snapshot(first, "query", fixture).result_hash == _snapshot(
        second, "query", fixture
    ).result_hash


def test_value_bag_preserves_multiplicity_and_ignores_aliases() -> None:
    left = snapshot(
        ((("a", {"type": "INTEGER", "value": 1}),),) * 2
    )
    right = snapshot(
        ((("renamed", {"type": "INTEGER", "value": 1}),),) * 2,
        ("renamed",),
    )
    assert compare_snapshots(left, right, ComparisonMode.VALUE_BAG, order_entitled=False)
    one_row = snapshot(((("value", {"type": "INTEGER", "value": 1}),),))
    assert not compare_snapshots(left, one_row, ComparisonMode.VALUE_BAG, order_entitled=False)


def test_ordered_comparison_requires_entitlement_and_preserves_order() -> None:
    first = snapshot(
        (
            (("value", {"type": "INTEGER", "value": 1}),),
            (("value", {"type": "INTEGER", "value": 2}),),
        )
    )
    reversed_rows = snapshot(tuple(reversed(first.typed_rows)))
    assert not compare_snapshots(
        first, reversed_rows, ComparisonMode.VALUE_ORDERED, order_entitled=True
    )
    assert compare_snapshots(
        first, reversed_rows, ComparisonMode.VALUE_ORDERED, order_entitled=False
    )


def test_truncated_result_is_not_comparable() -> None:
    left = snapshot(((("value", {"type": "INTEGER", "value": 1}),),))
    right = left.model_copy(update={"truncated": True})
    with pytest.raises(ValueError, match="truncated"):
        compare_snapshots(left, right, ComparisonMode.VALUE_BAG, order_entitled=False)


def test_fixture_bank_hash_and_state_names_are_closed() -> None:
    fixture_body = {
        "fixture_id": "fixture",
        "fixture_version": "v1",
        "schema_version": "schema",
        "seed": "seed",
        "scenario_tags": (),
        "valid": True,
        "validation_note": None,
    }
    fixture = DiagnosticFixture(
        **fixture_body,
        content_hash=stable_hash(fixture_body),
    )
    bank_data = {
        "bank_version": "bank-v1",
        "schema_version": "schema",
        "fixtures": [fixture.model_dump()],
    }
    bank_hash_data = {
        **bank_data,
        "fixtures": [fixture.model_dump(exclude={"content_hash"})],
    }
    bank = DiagnosticFixtureBank(**bank_data, content_hash=stable_hash(bank_hash_data))
    validate_fixture_bank(bank)
    assert len(DiagnosticState) == 5
    assert text_hash("Completed") != text_hash("completed")
