"""Reference-blind semantic submission claims and deterministic checks.

This module is intentionally smaller than a query-planning language.  It
represents declarative, stable-ID claims that a future submission could carry
next to SQL, and checks those claims against caller-supplied authoritative
catalog views.  It does not inspect questions, truth, references, fixtures,
or model correctness, and it never computes answerability.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, model_validator

CLAIM_CONTRACT_VERSION = "semantic-submission-claims-1"


class ClaimFamily(StrEnum):
    SCHEMA_OBJECT = "SCHEMA_OBJECT"
    RELATIONSHIP = "RELATIONSHIP"
    SEMANTIC_DEFINITION = "SEMANTIC_DEFINITION"
    TEMPORAL_DEFINITION = "TEMPORAL_DEFINITION"
    STATUS_DEFINITION = "STATUS_DEFINITION"
    POLICY = "POLICY"
    POPULATION_SEMANTICS = "POPULATION_SEMANTICS"


class ClaimAssertion(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    AUTHORIZED = "AUTHORIZED"
    UNAUTHORIZED = "UNAUTHORIZED"
    DEFINED = "DEFINED"
    UNDEFINED = "UNDEFINED"
    USED = "USED"
    NOT_USED = "NOT_USED"


class ClaimCheckStatus(StrEnum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_CLAIM = "INVALID_CLAIM"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SemanticClaim(_Frozen):
    family: ClaimFamily
    object_id: str = Field(min_length=1)
    assertion: ClaimAssertion


class SemanticSubmissionClaims(_Frozen):
    """A compact future sidecar; it carries no answerability oracle."""

    decision: str = Field(min_length=1)
    sql: str | None = None
    claims: tuple[SemanticClaim, ...] = ()

    @model_validator(mode="after")
    def validate_claims(self) -> SemanticSubmissionClaims:
        keys = [(claim.family, claim.object_id, claim.assertion) for claim in self.claims]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate semantic claims are not allowed")
        return self


class CatalogView(_Frozen):
    family: ClaimFamily
    id_field: str = Field(min_length=1)
    records: tuple[Mapping[str, Any], ...] = ()
    authoritative_complete: bool

    @model_validator(mode="after")
    def validate_records(self) -> CatalogView:
        ids = [str(record.get(self.id_field, "")) for record in self.records]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"catalog has missing or duplicate stable IDs: {self.family}")
        if ids != sorted(ids):
            raise ValueError(f"catalog is not canonically ordered: {self.family}")
        return self

    def by_id(self) -> dict[str, Mapping[str, Any]]:
        return {str(record[self.id_field]): record for record in self.records}


class CatalogRegistry(_Frozen):
    views: tuple[CatalogView, ...]

    @model_validator(mode="after")
    def validate_views(self) -> CatalogRegistry:
        families = [view.family for view in self.views]
        if len(families) != len(set(families)):
            raise ValueError("duplicate catalog family")
        if tuple(families) != tuple(sorted(families, key=lambda item: item.value)):
            raise ValueError("catalog views must be canonically ordered")
        return self

    def view(self, family: ClaimFamily) -> CatalogView | None:
        return next((view for view in self.views if view.family is family), None)

    def record(self, family: ClaimFamily, object_id: str) -> Mapping[str, Any] | None:
        view = self.view(family)
        return None if view is None else view.by_id().get(object_id)


class ClaimCheck(_Frozen):
    family: ClaimFamily
    object_id: str
    assertion: ClaimAssertion
    status: ClaimCheckStatus
    reason_code: str
    deterministic: bool


class SemanticClaimAudit(_Frozen):
    checks: tuple[ClaimCheck, ...]
    semantic_hash: str

    @property
    def contradictions(self) -> tuple[ClaimCheck, ...]:
        return tuple(
            check for check in self.checks if check.status is ClaimCheckStatus.CONTRADICTED
        )

    @property
    def unresolved(self) -> tuple[ClaimCheck, ...]:
        return tuple(check for check in self.checks if check.status is ClaimCheckStatus.UNRESOLVED)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        values = [_canonical(item) for item in value]
        return sorted(
            values,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, StrEnum):
        return value.value
    return value


def semantic_hash(value: Any) -> str:
    return sha256(
        json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def canonical_claim_contract() -> dict[str, Any]:
    return {
        "contract_version": CLAIM_CONTRACT_VERSION,
        "fields": {
            "decision": "existing governed decision enum/string",
            "sql": "optional SQL text for ANSWER submissions",
            "claims": "sparse stable-ID declarative claims",
        },
        "claim_families": [family.value for family in ClaimFamily],
        "assertions": [assertion.value for assertion in ClaimAssertion],
        "statuses": [status.value for status in ClaimCheckStatus],
        "forbidden_capabilities": [
            "is_answerable",
            "should_answer",
            "should_clarify",
            "should_block",
            "context_complete",
            "required_facts_complete",
            "semantic_scope_unique",
            "unique_interpretation",
            "correct_decision",
        ],
        "unknown_policy": "absence is unresolved when catalog completeness is not authoritative",
    }


_PRESENCE_ASSERTIONS = {
    ClaimAssertion.PRESENT,
    ClaimAssertion.MISSING,
    ClaimAssertion.DEFINED,
    ClaimAssertion.UNDEFINED,
}


def _check_presence(claim: SemanticClaim, view: CatalogView) -> ClaimCheck:
    exists = claim.object_id in view.by_id()
    assertion = claim.assertion
    if assertion in {ClaimAssertion.PRESENT, ClaimAssertion.DEFINED}:
        status = ClaimCheckStatus.VERIFIED if exists else ClaimCheckStatus.CONTRADICTED
        reason = "CLAIMED_OBJECT_PRESENT" if exists else "CLAIMED_PRESENT_OBJECT_MISSING"
    elif assertion in {ClaimAssertion.MISSING, ClaimAssertion.UNDEFINED}:
        if exists:
            status = ClaimCheckStatus.CONTRADICTED
            reason = "CLAIMED_MISSING_OBJECT_PRESENT"
        elif view.authoritative_complete:
            status = ClaimCheckStatus.VERIFIED
            reason = "CLAIMED_OBJECT_ABSENT_FROM_COMPLETE_CATALOG"
        else:
            status = ClaimCheckStatus.UNRESOLVED
            reason = "ABSENCE_REQUIRES_COMPLETE_CATALOG"
    else:
        return ClaimCheck(
            family=claim.family,
            object_id=claim.object_id,
            assertion=assertion,
            status=ClaimCheckStatus.NOT_APPLICABLE,
            reason_code="ASSERTION_NOT_APPLICABLE_TO_CATALOG_FAMILY",
            deterministic=True,
        )
    return ClaimCheck(
        family=claim.family,
        object_id=claim.object_id,
        assertion=assertion,
        status=status,
        reason_code=reason,
        deterministic=True,
    )


def _check_relationship(claim: SemanticClaim, view: CatalogView) -> ClaimCheck:
    record = view.by_id().get(claim.object_id)
    if claim.assertion in _PRESENCE_ASSERTIONS:
        return _check_presence(claim, view)
    if claim.assertion not in {ClaimAssertion.AUTHORIZED, ClaimAssertion.UNAUTHORIZED}:
        return ClaimCheck(
            family=claim.family,
            object_id=claim.object_id,
            assertion=claim.assertion,
            status=ClaimCheckStatus.NOT_APPLICABLE,
            reason_code="ASSERTION_NOT_APPLICABLE_TO_RELATIONSHIP_CATALOG",
            deterministic=True,
        )
    if record is None:
        if view.authoritative_complete:
            return ClaimCheck(
                family=claim.family,
                object_id=claim.object_id,
                assertion=claim.assertion,
                status=ClaimCheckStatus.CONTRADICTED,
                reason_code="CLAIMED_RELATIONSHIP_OBJECT_MISSING",
                deterministic=True,
            )
        return ClaimCheck(
            family=claim.family,
            object_id=claim.object_id,
            assertion=claim.assertion,
            status=ClaimCheckStatus.UNRESOLVED,
            reason_code="RELATIONSHIP_ABSENCE_REQUIRES_COMPLETE_CATALOG",
            deterministic=False,
        )
    authorized = record.get("authorized")
    expected = claim.assertion is ClaimAssertion.AUTHORIZED
    if not isinstance(authorized, bool):
        status = ClaimCheckStatus.UNRESOLVED
        reason = "RELATIONSHIP_AUTHORIZATION_UNKNOWN"
        deterministic = False
    elif authorized is expected:
        status = ClaimCheckStatus.VERIFIED
        reason = "CLAIMED_RELATIONSHIP_AUTHORIZATION_MATCHES"
        deterministic = True
    else:
        status = ClaimCheckStatus.CONTRADICTED
        reason = (
            "CLAIMED_AUTHORIZED_RELATIONSHIP_UNAUTHORIZED"
            if expected
            else "CLAIMED_UNAUTHORIZED_RELATIONSHIP_AUTHORIZED"
        )
        deterministic = True
    return ClaimCheck(
        family=claim.family,
        object_id=claim.object_id,
        assertion=claim.assertion,
        status=status,
        reason_code=reason,
        deterministic=deterministic,
    )


def _table_column_pairs(sql: str) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    """Extract explicit equality join pairs using only SQL structure."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return set()
    aliases: dict[str, str] = {}
    for table in tree.find_all(sqlglot.exp.Table):
        physical = table.name
        aliases[table.alias_or_name] = physical
        aliases[physical] = physical
    pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for join in tree.find_all(sqlglot.exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for equality in on.find_all(sqlglot.exp.EQ):
            columns = list(equality.find_all(sqlglot.exp.Column))
            if len(columns) != 2:
                continue
            refs: list[tuple[str, str]] = []
            for column in columns:
                table_name = aliases.get(column.table)
                if table_name is None:
                    refs = []
                    break
                refs.append((table_name, column.name))
            if len(refs) == 2:
                first, second = refs
                pairs.add((first, second) if first <= second else (second, first))
    return pairs


def _relationship_pairs(
    registry: CatalogRegistry,
) -> dict[str, tuple[tuple[str, str], tuple[str, str]]]:
    entities = registry.view(ClaimFamily.SCHEMA_OBJECT)
    relationships = registry.view(ClaimFamily.RELATIONSHIP)
    if entities is None or relationships is None:
        return {}
    entity_by_id = entities.by_id()
    attributes = {
        str(record.get("attribute_id")): record
        for record in entities.records
        if record.get("attribute_id")
    }
    # Schema views may contain both entity and attribute records; use the
    # stable IDs directly and fall back to the record's physical entity data.
    for record in entities.records:
        if record.get("physical_column_or_path"):
            attributes[str(record.get("attribute_id"))] = record
    result: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {}
    for relation in relationships.records:
        left_attr = attributes.get(str(relation.get("left_attribute")))
        right_attr = attributes.get(str(relation.get("right_attribute")))
        if left_attr is None or right_attr is None:
            continue
        left_entity = entity_by_id.get(str(left_attr.get("entity_id")))
        right_entity = entity_by_id.get(str(right_attr.get("entity_id")))
        if left_entity is None or right_entity is None:
            continue
        left = (
            str(left_entity.get("physical_table")),
            str(left_attr["physical_column_or_path"]),
        )
        right = (
            str(right_entity.get("physical_table")),
            str(right_attr["physical_column_or_path"]),
        )
        result[str(relation[relationships.id_field])] = (
            (left, right) if left <= right else (right, left)
        )
    return result


def _check_sql_relationship(
    claim: SemanticClaim, registry: CatalogRegistry, sql: str | None
) -> ClaimCheck:
    if sql is None:
        return ClaimCheck(
            family=claim.family,
            object_id=claim.object_id,
            assertion=claim.assertion,
            status=ClaimCheckStatus.UNRESOLVED,
            reason_code="SQL_REQUIRED_FOR_RELATIONSHIP_USAGE_CHECK",
            deterministic=False,
        )
    try:
        sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return ClaimCheck(
            family=claim.family,
            object_id=claim.object_id,
            assertion=claim.assertion,
            status=ClaimCheckStatus.UNRESOLVED,
            reason_code="SQL_PARSE_REQUIRED_FOR_RELATIONSHIP_USAGE_CHECK",
            deterministic=False,
        )
    pairs = _table_column_pairs(sql)
    expected = _relationship_pairs(registry).get(claim.object_id)
    if expected is None:
        return ClaimCheck(
            family=claim.family,
            object_id=claim.object_id,
            assertion=claim.assertion,
            status=ClaimCheckStatus.UNRESOLVED,
            reason_code="RELATIONSHIP_PHYSICAL_MAPPING_UNAVAILABLE",
            deterministic=False,
        )
    used = expected in pairs
    should_be_used = claim.assertion is ClaimAssertion.USED
    status = ClaimCheckStatus.VERIFIED if used is should_be_used else ClaimCheckStatus.CONTRADICTED
    return ClaimCheck(
        family=claim.family,
        object_id=claim.object_id,
        assertion=claim.assertion,
        status=status,
        reason_code=(
            "CLAIM_SQL_RELATIONSHIP_MATCH"
            if status is ClaimCheckStatus.VERIFIED
            else "CLAIM_SQL_RELATIONSHIP_MISMATCH"
        ),
        deterministic=True,
    )


def check_claims(
    submission: SemanticSubmissionClaims,
    registry: CatalogRegistry,
) -> SemanticClaimAudit:
    """Check explicit claims only; never infer the decision's correctness."""
    checks: list[ClaimCheck] = []
    for claim in submission.claims:
        if claim.family is ClaimFamily.POPULATION_SEMANTICS:
            checks.append(
                ClaimCheck(
                    family=claim.family,
                    object_id=claim.object_id,
                    assertion=claim.assertion,
                    status=ClaimCheckStatus.UNRESOLVED,
                    reason_code="POPULATION_SEMANTICS_NOT_DETERMINISTICALLY_CATALOGED",
                    deterministic=False,
                )
            )
            continue
        view = registry.view(claim.family)
        if view is None:
            checks.append(
                ClaimCheck(
                    family=claim.family,
                    object_id=claim.object_id,
                    assertion=claim.assertion,
                    status=ClaimCheckStatus.UNRESOLVED,
                    reason_code="CLAIM_CATALOG_UNAVAILABLE",
                    deterministic=False,
                )
            )
            continue
        if claim.family is ClaimFamily.RELATIONSHIP and claim.assertion in {
            ClaimAssertion.USED,
            ClaimAssertion.NOT_USED,
        }:
            checks.append(_check_sql_relationship(claim, registry, submission.sql))
        elif claim.family is ClaimFamily.RELATIONSHIP:
            checks.append(_check_relationship(claim, view))
        elif claim.assertion in _PRESENCE_ASSERTIONS:
            checks.append(_check_presence(claim, view))
        else:
            checks.append(
                ClaimCheck(
                    family=claim.family,
                    object_id=claim.object_id,
                    assertion=claim.assertion,
                    status=ClaimCheckStatus.NOT_APPLICABLE,
                    reason_code="ASSERTION_NOT_APPLICABLE_TO_CATALOG_FAMILY",
                    deterministic=True,
                )
            )
    payload = {
        "contract_version": CLAIM_CONTRACT_VERSION,
        "decision": submission.decision,
        "sql": submission.sql,
        "checks": [check.model_dump(mode="json") for check in checks],
    }
    return SemanticClaimAudit(checks=tuple(checks), semantic_hash=semantic_hash(payload))
