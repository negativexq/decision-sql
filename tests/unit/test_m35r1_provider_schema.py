from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.m35_runner import _parse_submission
from benchmark.models import Submission, validate_submission_invariants

ROOT = Path(__file__).parents[2]
SCHEMA_PATH = ROOT / "benchmark/schemas/model_submission.schema.json"


def _schema_keywords(value: object) -> list[str]:
    if isinstance(value, dict):
        return [
            key
            for key, child in value.items()
            for _ in [0]
            if key in {"allOf", "oneOf", "if", "then", "else", "dependentSchemas"}
        ] + [keyword for child in value.values() for keyword in _schema_keywords(child)]
    if isinstance(value, list):
        return [keyword for child in value for keyword in _schema_keywords(child)]
    return []


def test_provider_schema_is_flat_and_strict_subset_compatible() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"case_id", "decision", "sql", "reason_code"}
    assert not _schema_keywords(schema)


@pytest.mark.parametrize(
    "value",
    [
        {
            "case_id": "commerce_01",
            "decision": "ANSWER",
            "sql": "SELECT 1",
            "reason_code": None,
        },
        {
            "case_id": "support_08",
            "decision": "BLOCKED_AUTHORITY",
            "sql": None,
            "reason_code": "MISSING_AUTHORIZED_RELATIONSHIP",
        },
    ],
)
def test_provider_shaped_valid_submissions_pass_local_invariants(value: dict[str, object]) -> None:
    parsed = Submission.from_dict_unchecked(value)
    assert validate_submission_invariants(value["case_id"], parsed) == ()


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (
            {"case_id": "commerce_01", "decision": "ANSWER", "sql": None, "reason_code": None},
            "ANSWER_SQL_REQUIRED",
        ),
        (
            {
                "case_id": "commerce_01",
                "decision": "ANSWER",
                "sql": "SELECT 1",
                "reason_code": "NO_REASON",
            },
            "ANSWER_REASON_MUST_BE_NULL",
        ),
        (
            {
                "case_id": "commerce_01",
                "decision": "BLOCKED_AUTHORITY",
                "sql": "SELECT 1",
                "reason_code": "MISSING_AUTHORIZED_RELATIONSHIP",
            },
            "NON_ANSWER_SQL_MUST_BE_NULL",
        ),
        (
            {
                "case_id": "commerce_01",
                "decision": "BLOCKED_AUTHORITY",
                "sql": None,
                "reason_code": "READ_ONLY_POLICY",
            },
            "AUTHORITY_REASON_MISMATCH",
        ),
        (
            {
                "case_id": "commerce_01",
                "decision": "NEEDS_CLARIFICATION",
                "sql": None,
                "reason_code": "AMBIGUOUS_SEMANTICS",
            },
            None,
        ),
        (
            {
                "case_id": "commerce_01",
                "decision": "BLOCKED_POLICY",
                "sql": None,
                "reason_code": "READ_ONLY_POLICY",
            },
            None,
        ),
    ],
)
def test_local_invariant_codes(value: dict[str, object], error: str | None) -> None:
    parsed = Submission.from_dict_unchecked(value)
    errors = validate_submission_invariants("commerce_01", parsed)
    if error is None:
        assert errors == ()
    else:
        assert errors[0] == error


def test_wrong_case_id_is_local_invalid_submission() -> None:
    content = json.dumps(
        {
            "case_id": "fleet_01",
            "decision": "BLOCKED_AUTHORITY",
            "sql": None,
            "reason_code": "MISSING_AUTHORIZED_RELATIONSHIP",
        }
    )
    _submission, status, detail, _value = _parse_submission(content, "commerce_01")
    assert status == "INVALID_SUBMISSION"
    assert detail == "CASE_ID_MISMATCH"
