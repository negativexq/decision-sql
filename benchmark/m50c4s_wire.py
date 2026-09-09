"""Provider-compatible representation for the frozen M50C.3 blocker contract."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from app.generation.provider_schema import validate_provider_strict_schema
from app.semantics.semantic_blocker_shadow import (
    BlockingClaim,
    SemanticSubmissionShadow,
    ShadowDecision,
)

ROOT = Path(__file__).resolve().parent
OLD_SCHEMA_HASH = "0141baad9202e960ea01b5ab1f232e591457e06073749b9464aa847b141fcec8"
PROVIDER_WIRE_VERSION = "semantic-submission-provider-wire-1"
PROVIDER_SCHEMA_NAME = "decision_sql_m50c4s_canary"


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def historical_rejected_schema() -> dict[str, Any]:
    path = ROOT / "audits" / "m50c4" / "m50c4_contract_freeze.json"
    value = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    schema = cast(dict[str, Any], value["treatment_schema"])
    if _canonical_hash(schema) != OLD_SCHEMA_HASH:
        raise RuntimeError("M50C4_HISTORICAL_SCHEMA_HASH_MISMATCH")
    return copy.deepcopy(schema)


def corrected_provider_schema() -> dict[str, Any]:
    schema = historical_rejected_schema()
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "blocking_claim" not in properties:
        raise RuntimeError("M50C4_BLOCKING_CLAIM_PROPERTY_MISSING")
    required = schema.get("required")
    if not isinstance(required, list) or "blocking_claim" in required:
        raise RuntimeError("M50C4_HISTORICAL_DEFECT_NOT_PRESENT")
    schema["required"] = [*required, "blocking_claim"]
    return schema


def corrected_provider_schema_hash() -> str:
    return _canonical_hash(corrected_provider_schema())


def provider_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": PROVIDER_SCHEMA_NAME,
            "strict": True,
            "schema": corrected_provider_schema(),
        },
    }


def provider_response_format_hash() -> str:
    return _canonical_hash(provider_response_format())


class ProviderWireSubmission(BaseModel):
    """Provider wire shape; cross-field meaning is checked separately."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    decision: ShadowDecision
    sql: str | None
    reason_code: str | None
    blocking_claim: BlockingClaim | None


def provider_wire_from_semantic(
    submission: SemanticSubmissionShadow,
    *,
    case_id: str = "synthetic:canary",
    reason_code: str | None = None,
) -> ProviderWireSubmission:
    return ProviderWireSubmission(
        case_id=case_id,
        decision=submission.decision,
        sql=submission.sql,
        reason_code=reason_code,
        blocking_claim=submission.blocking_claim,
    )


def cross_field_errors(wire: ProviderWireSubmission) -> tuple[str, ...]:
    errors: list[str] = []
    if wire.decision is ShadowDecision.ANSWER:
        if not isinstance(wire.sql, str) or not wire.sql.strip():
            errors.append("ANSWER_SQL_REQUIRED")
        if wire.blocking_claim is not None:
            errors.append("ANSWER_BLOCKING_CLAIM_MUST_BE_NULL")
    else:
        if wire.sql is not None:
            errors.append("NON_ANSWER_SQL_MUST_BE_NULL")
        if wire.blocking_claim is None:
            errors.append("NON_ANSWER_BLOCKING_CLAIM_REQUIRED")
    return tuple(errors)


def normalize_provider_wire(wire: ProviderWireSubmission) -> SemanticSubmissionShadow:
    errors = cross_field_errors(wire)
    if errors:
        raise ValueError(";".join(errors))
    return SemanticSubmissionShadow(
        decision=wire.decision,
        sql=wire.sql,
        blocking_claim=wire.blocking_claim,
    )


def corrected_schema_violations() -> tuple[dict[str, str], ...]:
    return tuple(
        {"code": item.code, "path": item.path, "detail": item.detail}
        for item in validate_provider_strict_schema(corrected_provider_schema())
    )


def historical_schema_violations() -> tuple[dict[str, str], ...]:
    return tuple(
        {"code": item.code, "path": item.path, "detail": item.detail}
        for item in validate_provider_strict_schema(historical_rejected_schema())
    )
