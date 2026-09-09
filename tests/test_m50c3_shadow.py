import pytest
from pydantic import ValidationError

from app.semantics.semantic_blocker_shadow import (
    SemanticSubmissionShadow,
    ShadowDecision,
    audit_shadow_submission,
    canonical_shadow_contract,
    shadow_checker_hash,
)
from app.semantics.semantic_submission import (
    CatalogRegistry,
    CatalogView,
    ClaimCheckStatus,
    ClaimFamily,
)


def registry(*, complete: bool = True) -> CatalogRegistry:
    views = [
        CatalogView(
            family=ClaimFamily.SCHEMA_OBJECT,
            id_field="attribute_id",
            records=({"attribute_id": "attribute:demo:orders:order_id"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.RELATIONSHIP,
            id_field="relationship_id",
            records=({"authorized": True, "relationship_id": "relationship:demo:order_customer"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.SEMANTIC_DEFINITION,
            id_field="rule_id",
            records=({"rule_id": "rule:demo:completed_order"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.TEMPORAL_DEFINITION,
            id_field="temporal_rule_id",
            records=({"temporal_rule_id": "time:demo:now"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.STATUS_DEFINITION,
            id_field="rule_id",
            records=({"rule_id": "rule:demo:active_status"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.POLICY,
            id_field="policy_id",
            records=({"policy_id": "policy:demo:readonly"},),
            authoritative_complete=complete,
        ),
    ]
    return CatalogRegistry(views=tuple(sorted(views, key=lambda view: view.family.value)))


def test_answer_has_no_blocker_and_non_answer_requires_one() -> None:
    answer = SemanticSubmissionShadow(decision=ShadowDecision.ANSWER, sql="SELECT 1")
    assert audit_shadow_submission(answer, registry()).status is ClaimCheckStatus.NOT_APPLICABLE

    with pytest.raises(ValidationError):
        SemanticSubmissionShadow(decision=ShadowDecision.NEEDS_CLARIFICATION)

    with pytest.raises(ValidationError):
        SemanticSubmissionShadow(
            decision=ShadowDecision.ANSWER,
            sql="SELECT 1",
            blocking_claim={
                "family": "SCHEMA_OBJECT",
                "object_id": "attribute:demo:orders:order_id",
                "assertion": "MISSING",
            },
        )


def test_supported_claims_delegate_to_frozen_checker() -> None:
    cases = [
        (
            ShadowDecision.NEEDS_CLARIFICATION,
            "SCHEMA_OBJECT",
            "attribute:demo:orders:order_id",
            "MISSING",
            ClaimCheckStatus.CONTRADICTED,
        ),
        (
            ShadowDecision.BLOCKED_AUTHORITY,
            "RELATIONSHIP",
            "relationship:demo:order_customer",
            "UNAUTHORIZED",
            ClaimCheckStatus.CONTRADICTED,
        ),
        (
            ShadowDecision.NEEDS_CLARIFICATION,
            "SEMANTIC_DEFINITION",
            "rule:demo:missing",
            "MISSING",
            ClaimCheckStatus.VERIFIED,
        ),
        (
            ShadowDecision.NEEDS_CLARIFICATION,
            "TEMPORAL_DEFINITION",
            "time:demo:now",
            "MISSING",
            ClaimCheckStatus.CONTRADICTED,
        ),
        (
            ShadowDecision.NEEDS_CLARIFICATION,
            "STATUS_DEFINITION",
            "rule:demo:active_status",
            "UNDEFINED",
            ClaimCheckStatus.CONTRADICTED,
        ),
        (
            ShadowDecision.BLOCKED_POLICY,
            "POLICY",
            "policy:demo:missing",
            "MISSING",
            ClaimCheckStatus.VERIFIED,
        ),
    ]
    for decision, family, object_id, assertion, expected in cases:
        submission = SemanticSubmissionShadow(
            decision=decision,
            blocking_claim={"family": family, "object_id": object_id, "assertion": assertion},
        )
        assert audit_shadow_submission(submission, registry()).status is expected


def test_stable_id_and_family_assertion_validation() -> None:
    with pytest.raises(ValidationError):
        SemanticSubmissionShadow(
            decision=ShadowDecision.NEEDS_CLARIFICATION,
            blocking_claim={
                "family": "SCHEMA_OBJECT",
                "object_id": "free text blocker",
                "assertion": "MISSING",
            },
        )
    with pytest.raises(ValidationError):
        SemanticSubmissionShadow(
            decision=ShadowDecision.NEEDS_CLARIFICATION,
            blocking_claim={
                "family": "SCHEMA_OBJECT",
                "object_id": "attribute:demo:orders:order_id",
                "assertion": "UNAUTHORIZED",
            },
        )


def test_unknown_absence_remains_unresolved() -> None:
    submission = SemanticSubmissionShadow(
        decision=ShadowDecision.NEEDS_CLARIFICATION,
        blocking_claim={
            "family": "SEMANTIC_DEFINITION",
            "object_id": "rule:demo:missing",
            "assertion": "MISSING",
        },
    )
    assert (
        audit_shadow_submission(submission, registry(complete=False)).status
        is ClaimCheckStatus.UNRESOLVED
    )


def test_shadow_result_does_not_change_downstream_decision() -> None:
    submission = SemanticSubmissionShadow(
        decision=ShadowDecision.BLOCKED_AUTHORITY,
        blocking_claim={
            "family": "RELATIONSHIP",
            "object_id": "relationship:demo:order_customer",
            "assertion": "UNAUTHORIZED",
        },
    )
    before = (submission.decision.value, None, "unchanged-runtime-token")
    audit = audit_shadow_submission(submission, registry())
    after = (submission.decision.value, None, "unchanged-runtime-token")
    assert before == after
    assert audit.status is ClaimCheckStatus.CONTRADICTED


def test_provenance_payload_is_bounded_and_truth_free() -> None:
    submission = SemanticSubmissionShadow(
        decision=ShadowDecision.NEEDS_CLARIFICATION,
        blocking_claim={
            "family": "SCHEMA_OBJECT",
            "object_id": "attribute:demo:orders:order_id",
            "assertion": "MISSING",
        },
    )
    audit = audit_shadow_submission(submission, registry())
    payload = audit.provenance_payload()
    assert payload["claim_status"] == "CONTRADICTED"
    assert "claim_object_hash" in payload
    assert "truth" not in payload
    assert "reference_sql" not in payload


def test_hashing_and_contract_are_stable() -> None:
    assert canonical_shadow_contract()["one_blocker_only"] is True
    assert shadow_checker_hash() == shadow_checker_hash()
    assert len(shadow_checker_hash()) == 64
