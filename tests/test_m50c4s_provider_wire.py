import json

import pytest
from pydantic import ValidationError

from app.generation.provider_schema import validate_provider_strict_schema
from app.semantics.semantic_blocker_shadow import SemanticSubmissionShadow, ShadowDecision
from benchmark.m50c4s_wire import (
    corrected_provider_schema,
    cross_field_errors,
    historical_rejected_schema,
    normalize_provider_wire,
    provider_response_format,
    provider_wire_from_semantic,
)


def test_historical_schema_reproduces_m50c4_defect() -> None:
    violations = validate_provider_strict_schema(historical_rejected_schema())
    assert any(
        item.code == "PROPERTY_NOT_REQUIRED" and item.path == "$.blocking_claim"
        for item in violations
    )


def test_corrected_schema_is_recursive_strict_schema() -> None:
    assert validate_provider_strict_schema(corrected_provider_schema()) == ()
    schema = corrected_provider_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["sql"]["type"] == ["string", "null"]
    assert schema["properties"]["blocking_claim"]["type"] == ["object", "null"]


@pytest.mark.parametrize(
    "submission",
    [
        SemanticSubmissionShadow(decision=ShadowDecision.ANSWER, sql="SELECT 1"),
        SemanticSubmissionShadow(
            decision=ShadowDecision.NEEDS_CLARIFICATION,
            blocking_claim={
                "family": "SCHEMA_OBJECT",
                "object_id": "attribute:synthetic:missing",
                "assertion": "MISSING",
            },
        ),
        SemanticSubmissionShadow(
            decision=ShadowDecision.BLOCKED_AUTHORITY,
            blocking_claim={
                "family": "RELATIONSHIP",
                "object_id": "relationship:synthetic:unauthorized",
                "assertion": "UNAUTHORIZED",
            },
        ),
        SemanticSubmissionShadow(
            decision=ShadowDecision.BLOCKED_POLICY,
            blocking_claim={
                "family": "POLICY",
                "object_id": "policy:synthetic:readonly",
                "assertion": "MISSING",
            },
        ),
    ],
)
def test_semantic_provider_round_trip(submission: SemanticSubmissionShadow) -> None:
    wire = provider_wire_from_semantic(submission)
    assert cross_field_errors(wire) == ()
    assert normalize_provider_wire(wire) == submission


def test_cross_field_matrix() -> None:
    answer = provider_wire_from_semantic(
        SemanticSubmissionShadow(decision=ShadowDecision.ANSWER, sql="SELECT 1")
    )
    assert cross_field_errors(answer) == ()
    assert cross_field_errors(answer.model_copy(update={"sql": None})) == ("ANSWER_SQL_REQUIRED",)
    assert cross_field_errors(
        answer.model_copy(
            update={
                "blocking_claim": {
                    "family": "SCHEMA_OBJECT",
                    "object_id": "attribute:synthetic:x",
                    "assertion": "MISSING",
                }
            }
        )
    ) == ("ANSWER_BLOCKING_CLAIM_MUST_BE_NULL",)
    non_answer = answer.model_copy(
        update={
            "decision": ShadowDecision.NEEDS_CLARIFICATION,
            "sql": None,
            "blocking_claim": {
                "family": "SCHEMA_OBJECT",
                "object_id": "attribute:synthetic:x",
                "assertion": "MISSING",
            },
        }
    )
    assert cross_field_errors(non_answer) == ()
    assert cross_field_errors(non_answer.model_copy(update={"blocking_claim": None})) == (
        "NON_ANSWER_BLOCKING_CLAIM_REQUIRED",
    )
    assert cross_field_errors(non_answer.model_copy(update={"sql": "SELECT 1"})) == (
        "NON_ANSWER_SQL_MUST_BE_NULL",
    )


def test_nullability_and_provider_request_are_serializable() -> None:
    value = provider_wire_from_semantic(
        SemanticSubmissionShadow(decision=ShadowDecision.ANSWER, sql="SELECT 1")
    )
    encoded = json.dumps(value.model_dump(mode="json"), sort_keys=True)
    assert '"blocking_claim": null' in encoded
    assert provider_response_format()["json_schema"]["strict"] is True


def test_invalid_wire_shape_is_rejected() -> None:
    with pytest.raises(ValidationError):
        provider_wire_from_semantic(
            SemanticSubmissionShadow(decision=ShadowDecision.ANSWER, sql="SELECT 1")
        ).model_validate({"decision": "ANSWER", "sql": "SELECT 1"})
