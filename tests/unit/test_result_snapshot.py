import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.sql.models import QueryExecution
from evaluation.metrics import compare_query_results
from evaluation.result_snapshot import (
    InvalidSnapshotContract,
    SnapshotCellType,
    SnapshotClassification,
    classify_snapshot_payload,
    restore_query_execution,
    serialize_query_execution,
    snapshot_query_execution,
)

PLAN_ID = UUID("00000000-0000-0000-0000-000000000001")


def make_execution(
    rows: list[dict[str, object]], columns: list[str] | None = None
) -> QueryExecution:
    resolved_columns = columns or list(rows[0]) if rows else columns or []
    return QueryExecution(
        plan_id=PLAN_ID,
        correlation_id="snapshot-test",
        columns=resolved_columns,
        rows=rows,
        row_count=len(rows),
        truncated=False,
        latency_ms=12.5,
    )


def round_trip(execution: QueryExecution) -> QueryExecution:
    encoded = snapshot_query_execution(execution)
    decoded = json.loads(json.dumps(encoded, ensure_ascii=False, sort_keys=True))
    return restore_query_execution(decoded)


@pytest.mark.parametrize(
    ("value", "cell_type"),
    [
        (None, SnapshotCellType.NULL),
        (True, SnapshotCellType.BOOLEAN),
        (False, SnapshotCellType.BOOLEAN),
        (0, SnapshotCellType.INTEGER),
        (42, SnapshotCellType.INTEGER),
        (-3, SnapshotCellType.INTEGER),
        (1.25, SnapshotCellType.FLOAT),
        (Decimal("123.45"), SnapshotCellType.DECIMAL),
        ("normal text", SnapshotCellType.STRING),
        (date(2026, 9, 5), SnapshotCellType.DATE),
        (datetime(2026, 9, 5, 13, 14, 15, tzinfo=UTC), SnapshotCellType.DATETIME),
    ],
)
def test_supported_cell_types_round_trip(value: object, cell_type: SnapshotCellType) -> None:
    original = make_execution([{"value": value}], ["value"])
    restored = round_trip(original)

    assert restored.rows == original.rows
    assert type(restored.rows[0]["value"]) is type(value)
    assert snapshot_query_execution(original)["execution"]["rows"][0]["value"]["type"] == cell_type
    assert compare_query_results(original, restored) is True


@pytest.mark.parametrize(
    "value",
    [
        Decimal("123.45"),
        Decimal("0"),
        Decimal("0.00"),
        Decimal("-1.2500"),
        Decimal("12345678901234567890.12345678901234567890"),
    ],
)
def test_decimal_preserves_exact_decimal_type_and_text(value: Decimal) -> None:
    original = make_execution([{"value": value}], ["value"])
    encoded = snapshot_query_execution(original)
    restored = round_trip(original)

    assert encoded["execution"]["rows"][0]["value"] == {"type": "DECIMAL", "value": str(value)}
    assert type(restored.rows[0]["value"]) is Decimal
    assert restored.rows[0]["value"] == value


@pytest.mark.parametrize("value", ["123.45", "00123", "0", "-1.2500", "true", "2026-09-05", "null"])
def test_numeric_looking_and_literal_looking_strings_remain_strings(value: str) -> None:
    restored = round_trip(make_execution([{"value": value}], ["value"]))

    assert type(restored.rows[0]["value"]) is str
    assert restored.rows[0]["value"] == value


def test_mixed_rows_preserve_types_order_duplicates_and_metadata() -> None:
    row = {
        "money": Decimal("0.00"),
        "external_code": "00123",
        "count": 42,
        "active": True,
        "created_date": date(2026, 9, 5),
        "created_at": datetime(2026, 9, 5, 8, 9, 10),
        "optional": None,
    }
    original = QueryExecution(
        plan_id=PLAN_ID,
        correlation_id="mixed",
        columns=list(row),
        rows=[row, row.copy(), {**row, "count": -3}],
        row_count=3,
        truncated=True,
        latency_ms=0.0,
    )
    restored = round_trip(original)

    assert restored.columns == original.columns
    assert restored.rows == original.rows
    assert restored.rows[0] == restored.rows[1]
    assert [type(item["money"]) for item in restored.rows] == [Decimal, Decimal, Decimal]
    assert type(restored.rows[0]["external_code"]) is str
    assert type(restored.rows[0]["count"]) is int
    assert type(restored.rows[0]["active"]) is bool
    assert type(restored.rows[0]["created_date"]) is date
    assert type(restored.rows[0]["created_at"]) is datetime
    assert restored.row_count == 3
    assert restored.truncated is True
    assert compare_query_results(original, restored) is True


def test_deterministic_serialization() -> None:
    execution = make_execution([{"b": Decimal("0.00"), "a": "00123"}], ["b", "a"])

    assert serialize_query_execution(execution) == serialize_query_execution(execution)


def test_legacy_payload_is_classified_and_never_heuristically_decoded() -> None:
    original = make_execution([{"value": Decimal("123.45")}], ["value"])
    legacy_payload = original.model_dump(mode="json")
    json_round_trip = json.loads(json.dumps(legacy_payload))

    assert (
        classify_snapshot_payload(json_round_trip)
        is SnapshotClassification.LEGACY_UNTYPED_RESULT_SNAPSHOT
    )
    with pytest.raises(InvalidSnapshotContract, match="LEGACY_UNTYPED_RESULT_SNAPSHOT"):
        restore_query_execution(json_round_trip)


def test_old_path_decimal_self_comparison_reproduces_defect() -> None:
    original = make_execution([{"value": Decimal("123.45")}], ["value"])
    legacy = json.loads(json.dumps(original.model_dump(mode="json")))
    restored = QueryExecution.model_validate(legacy)

    assert type(restored.rows[0]["value"]) is str
    assert compare_query_results(original, restored) is False


def test_evaluation_reference_call_sites_use_typed_snapshot_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "evaluation/m104s_protocol.py",
        "evaluation/run_m104s.py",
        "evaluation/run_m10r.py",
        "evaluation/run_m104.py",
        "evaluation/run_m10.py",
    ):
        source = (root / relative).read_text()
        assert "result_snapshot" in source
