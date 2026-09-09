import json

from app.generation.provider_schema import validate_provider_strict_schema
from app.semantics.semantic_blocker_shadow import SemanticSubmissionShadow, ShadowDecision
from benchmark.m50c4s_wire import corrected_provider_schema, provider_response_format_hash
from benchmark.m50c5_runner import (
    _canonical_trace_value,
    _counter_records,
    _parse_treatment,
    _schedule,
)


def test_exact_frozen_provider_wire_is_reused() -> None:
    assert (
        provider_response_format_hash()
        == "c9ea99c4ac05e1a4f1c756d313bdfc74ca6e30578e7c9cda129de33f1fe617e8"
    )
    assert validate_provider_strict_schema(corrected_provider_schema()) == ()


def test_treatment_parser_requires_nullable_cross_field_shape() -> None:
    answer = json.dumps(
        {
            "case_id": "x",
            "decision": "ANSWER",
            "sql": "SELECT 1",
            "reason_code": None,
            "blocking_claim": None,
        }
    )
    parsed, status, detail = _parse_treatment(answer, "x")
    assert status == "PASS"
    assert detail is None
    assert parsed is not None
    invalid = json.dumps(
        {
            "case_id": "x",
            "decision": "NEEDS_CLARIFICATION",
            "sql": None,
            "reason_code": None,
            "blocking_claim": None,
        }
    )
    _, invalid_status, _ = _parse_treatment(invalid, "x")
    assert invalid_status == "CROSS_FIELD_FAILURE"


def test_schedule_is_adjacent_and_balanced() -> None:
    slots = _schedule(["a", "b", "c"])
    assert len(slots) == 6
    assert slots[0]["case_id"] == slots[1]["case_id"]
    assert slots[0]["arm"] != slots[1]["arm"]


def test_answer_shadow_shape_remains_minimal() -> None:
    submission = SemanticSubmissionShadow(decision=ShadowDecision.ANSWER, sql="SELECT 1")
    assert submission.blocking_claim is None


def test_bad_treatment_json_is_not_coerced() -> None:
    parsed, status, detail = _parse_treatment("not json", "x")
    assert parsed is None
    assert status == "WIRE_PARSE_FAILURE"
    assert detail


def test_mixed_optional_category_serialization_is_null_safe() -> None:
    expected = [
        {"key": None, "count": 6},
        {"key": "RELATIONSHIP", "count": 10},
        {"key": "SCHEMA_OBJECT", "count": 5},
    ]
    assert _counter_records([None] * 6 + ["RELATIONSHIP"] * 10 + ["SCHEMA_OBJECT"] * 5) == expected
    assert _counter_records([None] * 6 + ["SCHEMA_OBJECT"] * 5 + ["RELATIONSHIP"] * 10) == expected


def test_trace_identity_excludes_ephemeral_runtime_fields() -> None:
    value = {
        "plan_id": "uuid-a",
        "executed_at_utc": "2026-01-01T00:00:00Z",
        "plan_ms": 1.0,
        "estimate": {"plan_rows": 10, "total_cost": 20.0, "top_level_node_type": "Seq Scan"},
        "status": "ALLOWED",
    }
    assert _canonical_trace_value(value) == {"status": "ALLOWED"}
