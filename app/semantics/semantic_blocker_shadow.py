"""Minimal stable-ID blocker claims for a future NON-ANSWER submission.

This is a shadow candidate only.  It validates a compact wire object, delegates
catalog consistency to the frozen M50C.2 checker, and returns an audit result.
It never decides whether a request is answerable and never changes a decision.
"""

from __future__ import annotations

import re
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.provenance.canonical import semantic_hash
from app.semantics.semantic_submission import (
    CLAIM_CONTRACT_VERSION,
    CatalogRegistry,
    ClaimAssertion,
    ClaimCheckStatus,
    ClaimFamily,
    SemanticClaim,
    SemanticSubmissionClaims,
    canonical_claim_contract,
    check_claims,
)

SHADOW_CONTRACT_VERSION = "semantic-submission-shadow-1"
SHADOW_CHECKER_VERSION = "semantic-blocker-checker-1"
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]*:[^\s]+$")


class ShadowDecision(StrEnum):
    ANSWER = "ANSWER"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED_AUTHORITY = "BLOCKED_AUTHORITY"
    BLOCKED_POLICY = "BLOCKED_POLICY"


class BlockerAssertion(StrEnum):
    MISSING = "MISSING"
    UNAUTHORIZED = "UNAUTHORIZED"
    UNDEFINED = "UNDEFINED"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


_COMPATIBLE_ASSERTIONS: dict[ClaimFamily, frozenset[BlockerAssertion]] = {
    ClaimFamily.SCHEMA_OBJECT: frozenset({BlockerAssertion.MISSING}),
    ClaimFamily.RELATIONSHIP: frozenset({BlockerAssertion.MISSING, BlockerAssertion.UNAUTHORIZED}),
    ClaimFamily.SEMANTIC_DEFINITION: frozenset(
        {BlockerAssertion.MISSING, BlockerAssertion.UNDEFINED}
    ),
    ClaimFamily.TEMPORAL_DEFINITION: frozenset(
        {BlockerAssertion.MISSING, BlockerAssertion.UNDEFINED}
    ),
    ClaimFamily.STATUS_DEFINITION: frozenset(
        {BlockerAssertion.MISSING, BlockerAssertion.UNDEFINED}
    ),
    # The frozen M50C.2 checker supports policy presence/absence, but not a
    # separate policy applicability/disallowed-state assertion.
    ClaimFamily.POLICY: frozenset({BlockerAssertion.MISSING}),
}


class BlockingClaim(_Frozen):
    family: ClaimFamily
    object_id: str = Field(min_length=1)
    assertion: BlockerAssertion

    @model_validator(mode="after")
    def validate_stable_id_and_pairing(self) -> BlockingClaim:
        if not _STABLE_ID.fullmatch(self.object_id):
            raise ValueError("blocking claim object_id must be a stable colon-delimited ID")
        if self.family not in _COMPATIBLE_ASSERTIONS:
            raise ValueError("claim family is not supported by the blocker shadow contract")
        if self.assertion not in _COMPATIBLE_ASSERTIONS[self.family]:
            raise ValueError("claim family/assertion combination is incompatible")
        return self


class SemanticSubmissionShadow(_Frozen):
    """Candidate wire shape; not the current provider or benchmark schema."""

    contract_version: str = SHADOW_CONTRACT_VERSION
    decision: ShadowDecision
    sql: str | None = None
    blocking_claim: BlockingClaim | None = None

    @model_validator(mode="after")
    def validate_decision_shape(self) -> SemanticSubmissionShadow:
        if self.decision is ShadowDecision.ANSWER:
            if self.blocking_claim is not None:
                raise ValueError("ANSWER submissions cannot carry a blocking claim")
            return self
        if self.blocking_claim is None:
            raise ValueError("NON-ANSWER submissions require one blocking claim")
        if self.sql is not None:
            raise ValueError("NON-ANSWER submissions cannot carry candidate SQL")
        return self


class SemanticBlockerShadowAudit(_Frozen):
    contract_version: str = SHADOW_CONTRACT_VERSION
    checker_version: str = SHADOW_CHECKER_VERSION
    checker_hash: str
    decision: ShadowDecision
    claim_family: ClaimFamily | None = None
    claim_assertion: BlockerAssertion | None = None
    claim_object_hash: str | None = None
    status: ClaimCheckStatus
    reason_code: str
    authority_source: str | None = None

    @property
    def semantic_hash(self) -> str:
        return semantic_hash(self.model_dump(mode="json"))

    def provenance_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "checker_version": self.checker_version,
            "checker_hash": self.checker_hash,
            "decision": self.decision.value,
            "claim_family": self.claim_family.value if self.claim_family else None,
            "claim_assertion": self.claim_assertion.value if self.claim_assertion else None,
            "claim_object_hash": self.claim_object_hash,
            "claim_status": self.status.value,
            "reason_code": self.reason_code,
            "authority_source": self.authority_source,
        }


def canonical_shadow_contract() -> dict[str, Any]:
    return {
        "contract_version": SHADOW_CONTRACT_VERSION,
        "parent_contract_version": CLAIM_CONTRACT_VERSION,
        "decisions": [decision.value for decision in ShadowDecision],
        "blocker_fields": ["family", "object_id", "assertion"],
        "supported_families": {
            family.value: sorted(assertion.value for assertion in assertions)
            for family, assertions in sorted(
                _COMPATIBLE_ASSERTIONS.items(), key=lambda item: item[0].value
            )
        },
        "one_blocker_only": True,
        "answer_requires_blocker": False,
        "non_answer_requires_blocker": True,
        "free_text_claims": False,
        "stable_id_required": True,
        "negative_capabilities": [
            "is_answerable",
            "correct_decision",
            "should_answer",
            "should_clarify",
            "should_block",
            "required_facts",
            "required_facts_complete",
            "context_complete",
            "unique_interpretation",
            "semantic_scope_unique",
        ],
    }


def shadow_checker_hash() -> str:
    return semantic_hash(
        {
            "checker_version": SHADOW_CHECKER_VERSION,
            "contract": canonical_shadow_contract(),
            "parent_checker_contract": canonical_claim_contract(),
        }
    )


def audit_shadow_submission(
    submission: SemanticSubmissionShadow, registry: CatalogRegistry
) -> SemanticBlockerShadowAudit:
    """Audit one explicit blocker while leaving the submitted decision untouched."""
    checker_hash = shadow_checker_hash()
    claim = submission.blocking_claim
    if claim is None:
        return SemanticBlockerShadowAudit(
            checker_hash=checker_hash,
            decision=submission.decision,
            status=ClaimCheckStatus.NOT_APPLICABLE,
            reason_code="NO_BLOCKING_CLAIM_ON_ANSWER",
        )
    assertion = {
        BlockerAssertion.MISSING: ClaimAssertion.MISSING,
        BlockerAssertion.UNAUTHORIZED: ClaimAssertion.UNAUTHORIZED,
        BlockerAssertion.UNDEFINED: ClaimAssertion.UNDEFINED,
    }[claim.assertion]
    delegated = SemanticSubmissionClaims(
        decision=submission.decision.value,
        sql=None,
        claims=(
            SemanticClaim(
                family=claim.family,
                object_id=claim.object_id,
                assertion=assertion,
            ),
        ),
    )
    result = check_claims(delegated, registry)
    check = result.checks[0]
    authority_source = f"catalog:{claim.family.value}"
    return SemanticBlockerShadowAudit(
        checker_hash=checker_hash,
        decision=submission.decision,
        claim_family=claim.family,
        claim_assertion=claim.assertion,
        claim_object_hash=sha256(claim.object_id.encode()).hexdigest(),
        status=check.status,
        reason_code=check.reason_code,
        authority_source=authority_source,
    )
