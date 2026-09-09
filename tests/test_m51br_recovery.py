import json

from benchmark.analysis_serialization import canonicalize_analysis_value, dumps_analysis
from benchmark.m50c5_runner import _canonical_trace_value


def test_mixed_optional_keys_are_null_preserving_and_deterministic() -> None:
    value = {None: 6, "RELATIONSHIP": 10, "SCHEMA_OBJECT": 5}
    expected = {
        "__typed_mapping__": [
            {"key": None, "value": 6},
            {"key": "RELATIONSHIP", "value": 10},
            {"key": "SCHEMA_OBJECT", "value": 5},
        ]
    }
    assert canonicalize_analysis_value(value) == expected
    assert dumps_analysis(value) == dumps_analysis(dict(reversed(list(value.items()))))
    assert json.loads(dumps_analysis(value)) == expected


def test_actual_decision_matrix_shape_keeps_invalid_bucket_distinct() -> None:
    matrix = {
        "ANSWERABLE": {
            "ANSWER": 53,
            "NEEDS_CLARIFICATION": 19,
            "BLOCKED_AUTHORITY": 12,
            "BLOCKED_POLICY": 6,
            None: 0,
        }
    }
    encoded = json.loads(dumps_analysis(matrix))
    rows = encoded["ANSWERABLE"]["__typed_mapping__"]
    assert [row["key"] for row in rows] == [
        None,
        "ANSWER",
        "BLOCKED_AUTHORITY",
        "BLOCKED_POLICY",
        "NEEDS_CLARIFICATION",
    ]


def test_serializer_does_not_drop_null_rows() -> None:
    encoded = json.loads(dumps_analysis({None: 2, "ANSWER": 1}))
    assert any(row["key"] is None and row["value"] == 2 for row in encoded["__typed_mapping__"])


def test_runtime_trace_canonicalization_removes_ephemeral_fields_only() -> None:
    value = {
        "plan_id": "uuid",
        "executed_at_utc": "now",
        "plan_ms": 1.0,
        "execute_ms": 2.0,
        "latency_ms": 3.0,
        "result_contract_outcome": True,
        "status": "ALLOWED",
    }
    assert _canonical_trace_value(value) == {
        "result_contract_outcome": True,
        "status": "ALLOWED",
    }
