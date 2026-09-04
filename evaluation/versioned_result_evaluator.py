"""Explicit V1/V2 result evaluation with bounded provenance.

This module is evaluation-only.  V1 delegates to the existing evaluator in
``evaluation.metrics`` and V2 delegates to the frozen M9.2 comparator.  The
wrapper selects an authoritative evaluator; it never combines outcomes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum, StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from app.sql.models import QueryExecution
from evaluation.metrics import assess_query_results
from evaluation.result_equivalence_contract import (
    CANDIDATE_VERSION,
    BoundResult,
    Comparison,
    Outcome,
    ResultEquivalenceContract,
    compare_contract,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]
V2_COMPARATOR_SOURCE = ROOT / "evaluation" / "result_equivalence_contract.py"
FROZEN_V2_COMPARATOR_SOURCE_HASH = (
    "677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97"
)
PROVENANCE_SCHEMA_VERSION = "evaluation-provenance-v1"
V1_SEMANTICS_VERSION = "legacy-result-equivalence-v1"
V2_BINDING_VERSION = "evaluation-semantic-bindings-v1"
V2_COMPARATOR_ID = "decision-result-comparator-v2"


class EvaluatorVersion(StrEnum):
    V1 = "decision-result-evaluator-v1"
    V2 = "decision-result-evaluator-v2"


class EvaluatorMode(StrEnum):
    V1 = "V1"
    V2 = "V2"
    DUAL_SHADOW = "DUAL_SHADOW"


class EvaluationErrorCode(StrEnum):
    EVALUATOR_CONTRACT_REQUIRED = "EVALUATOR_CONTRACT_REQUIRED"
    EVALUATOR_CONTRACT_INVALID = "EVALUATOR_CONTRACT_INVALID"
    EVALUATOR_MODE_INVALID = "EVALUATOR_MODE_INVALID"
    EVALUATOR_RESULT_TYPE_MISMATCH = "EVALUATOR_RESULT_TYPE_MISMATCH"
    EVALUATOR_REFERENCE_TYPE_MISMATCH = "EVALUATOR_REFERENCE_TYPE_MISMATCH"


class EvaluationConfigurationError(ValueError):
    """Typed fail-closed error for an invalid versioned evaluation request."""

    def __init__(self, code: EvaluationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class DualShadowStatus(StrEnum):
    AGREE_EQUIVALENT = "AGREE_EQUIVALENT"
    AGREE_NOT_EQUIVALENT = "AGREE_NOT_EQUIVALENT"
    V1_ONLY_EQUIVALENT = "V1_ONLY_EQUIVALENT"
    V2_ONLY_EQUIVALENT = "V2_ONLY_EQUIVALENT"
    SHADOW_FAILURE = "SHADOW_FAILURE"


@dataclass(frozen=True)
class EvaluatorProvenance:
    provenance_schema_version: str
    evaluator_id: str
    evaluator_version: str
    evaluator_mode: str
    authoritative: bool
    contract_id: str | None
    contract_version: str | None
    contract_instance_hash: str | None
    comparator_id: str | None
    comparator_version: str | None
    comparator_source_hash: str | None
    semantic_binding_version: str | None
    result_semantics_version: str
    reference_variant_count: int
    matched_reference_variant_index: int | None
    required_slot_count: int
    ignored_optional_slot_count: int

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _canonical(self))

    def semantic_hash(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True)
class EvaluationComparisonRequest:
    generated_result: QueryExecution | BoundResult
    reference_result_variants: tuple[QueryExecution | BoundResult, ...]
    evaluator_mode: EvaluatorMode = EvaluatorMode.V1
    result_contract: ResultEquivalenceContract | None = None
    contract_id: str | None = None
    order_sensitive: bool = False


@dataclass(frozen=True)
class EvaluatorOutcome:
    evaluator: EvaluatorVersion
    equivalent: bool
    outcome: str
    reason: str
    reference_variant_index: int | None
    provenance: EvaluatorProvenance
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _canonical(self))


@dataclass(frozen=True)
class VersionedEvaluationResult:
    evaluator_mode: EvaluatorMode
    authoritative: EvaluatorOutcome
    shadow: EvaluatorOutcome | None = None
    dual_shadow_status: DualShadowStatus | None = None

    @property
    def equivalent(self) -> bool:
        return self.authoritative.equivalent

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _canonical(self))


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def contract_instance_hash(contract: ResultEquivalenceContract) -> str:
    """Hash only the semantic contract instance, excluding run metadata."""
    payload = {
        "contract_version": contract.contract_version,
        "slots": sorted(
            (_canonical(slot) for slot in contract.slots), key=lambda item: item["slot_id"]
        ),
        "row_identity_slots": sorted(contract.row_identity_slots),
        "required_measure_slots": sorted(contract.required_measure_slots),
        "scalar_or_tabular": contract.scalar_or_tabular,
        "row_order_policy": contract.row_order_policy,
        "duplicate_policy": contract.duplicate_policy,
        "extra_output_policy": contract.extra_output_policy,
        "null_policy_reference": contract.null_policy_reference,
        "numeric_tolerance_reference": contract.numeric_tolerance_reference,
        "notes": contract.notes,
    }
    return canonical_hash(payload)


def comparator_source_hash() -> str:
    return sha256(V2_COMPARATOR_SOURCE.read_bytes()).hexdigest()


def verify_frozen_comparator_source() -> str:
    actual = comparator_source_hash()
    if actual != FROZEN_V2_COMPARATOR_SOURCE_HASH:
        raise RuntimeError(
            "Frozen M9.2 comparator source changed: "
            f"expected {FROZEN_V2_COMPARATOR_SOURCE_HASH}, got {actual}"
        )
    return actual


def as_query_execution(result: QueryExecution | BoundResult) -> QueryExecution:
    if isinstance(result, QueryExecution):
        return result
    if len(result.columns) != len(result.bindings):
        raise EvaluationConfigurationError(
            EvaluationErrorCode.EVALUATOR_RESULT_TYPE_MISMATCH,
            "BoundResult columns and bindings must have equal width for V1",
        )
    rows = [
        {column: row[index] for index, column in enumerate(result.columns)}
        for row in result.rows
    ]
    return QueryExecution(
        plan_id=uuid4(),
        columns=list(result.columns),
        rows=rows,
        row_count=len(rows),
        latency_ms=0.0,
    )


def _normalize_mode(value: EvaluatorMode | str) -> EvaluatorMode:
    if isinstance(value, EvaluatorMode):
        return value
    try:
        return EvaluatorMode(value)
    except ValueError as error:
        raise EvaluationConfigurationError(
            EvaluationErrorCode.EVALUATOR_MODE_INVALID,
            f"Unknown evaluator mode: {value}",
        ) from error


def _v2_contract(request: EvaluationComparisonRequest) -> ResultEquivalenceContract:
    contract = request.result_contract
    if contract is None:
        raise EvaluationConfigurationError(
            EvaluationErrorCode.EVALUATOR_CONTRACT_REQUIRED,
            "V2 evaluation requires an explicit result-equivalence contract",
        )
    errors = validate_contract(contract)
    if errors:
        raise EvaluationConfigurationError(
            EvaluationErrorCode.EVALUATOR_CONTRACT_INVALID,
            "; ".join(errors),
        )
    if not isinstance(request.generated_result, BoundResult) or any(
        not isinstance(reference, BoundResult) for reference in request.reference_result_variants
    ):
        raise EvaluationConfigurationError(
            EvaluationErrorCode.EVALUATOR_RESULT_TYPE_MISMATCH,
            "V2 evaluation requires BoundResult semantic bindings",
        )
    return contract


def _v1(request: EvaluationComparisonRequest, authoritative: bool) -> EvaluatorOutcome:
    generated = as_query_execution(request.generated_result)
    references = tuple(
        as_query_execution(reference) for reference in request.reference_result_variants
    )
    matched: int | None = None
    selected = None
    for index, reference in enumerate(references):
        comparison = assess_query_results(
            generated,
            reference,
            order_sensitive=request.order_sensitive,
        )
        if comparison.equivalent:
            matched, selected = index, comparison
            break
        selected = selected or comparison
    equivalent = bool(selected and selected.equivalent)
    return EvaluatorOutcome(
        evaluator=EvaluatorVersion.V1,
        equivalent=equivalent,
        outcome="EQUIVALENT" if equivalent else "NOT_EQUIVALENT",
        reason="EXISTING_EQUIVALENCE" if equivalent else "EXISTING_NOT_EQUIVALENT",
        reference_variant_index=matched,
        diagnostic=selected.diagnostic.value if selected and selected.diagnostic else None,
        provenance=EvaluatorProvenance(
            PROVENANCE_SCHEMA_VERSION,
            EvaluatorVersion.V1.value,
            EvaluatorVersion.V1.value,
            request.evaluator_mode.value,
            authoritative,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            V1_SEMANTICS_VERSION,
            len(references),
            matched,
            0,
            0,
        ),
    )


def _v2(
    request: EvaluationComparisonRequest,
    contract: ResultEquivalenceContract,
    authoritative: bool,
) -> EvaluatorOutcome:
    generated = request.generated_result
    references = request.reference_result_variants
    assert isinstance(generated, BoundResult)
    assert all(isinstance(reference, BoundResult) for reference in references)
    bound_references = cast(tuple[BoundResult, ...], references)
    comparison: Comparison = compare_contract(generated, bound_references, contract)
    required = {slot.slot_id for slot in contract.required_slots}
    extras = set(generated.bindings) - required
    ignored = len(extras) if comparison.outcome is Outcome.CONTRACT_EQUIVALENT else 0
    return EvaluatorOutcome(
        evaluator=EvaluatorVersion.V2,
        equivalent=comparison.outcome
        in (Outcome.STRICT_EQUIVALENT, Outcome.CONTRACT_EQUIVALENT),
        outcome=comparison.outcome.value,
        reason=comparison.reason.value,
        reference_variant_index=comparison.reference_variant,
        provenance=EvaluatorProvenance(
            PROVENANCE_SCHEMA_VERSION,
            EvaluatorVersion.V2.value,
            EvaluatorVersion.V2.value,
            request.evaluator_mode.value,
            authoritative,
            request.contract_id,
            contract.contract_version,
            contract_instance_hash(contract),
            V2_COMPARATOR_ID,
            CANDIDATE_VERSION,
            verify_frozen_comparator_source(),
            V2_BINDING_VERSION,
            contract.contract_version,
            len(references),
            comparison.reference_variant,
            len(contract.required_slots),
            ignored,
        ),
    )


def evaluate(request: EvaluationComparisonRequest) -> VersionedEvaluationResult:
    """Evaluate using the explicitly selected authority, defaulting to V1."""
    mode = _normalize_mode(request.evaluator_mode)
    normalized = replace(request, evaluator_mode=mode)
    if mode is EvaluatorMode.V1:
        return VersionedEvaluationResult(mode, _v1(normalized, True))
    contract = _v2_contract(normalized)
    if mode is EvaluatorMode.V2:
        return VersionedEvaluationResult(mode, _v2(normalized, contract, True))
    v1 = _v1(normalized, True)
    v2 = _v2(normalized, contract, False)
    if v1.equivalent and v2.equivalent:
        status = DualShadowStatus.AGREE_EQUIVALENT
    elif not v1.equivalent and not v2.equivalent:
        status = DualShadowStatus.AGREE_NOT_EQUIVALENT
    elif v1.equivalent:
        status = DualShadowStatus.V1_ONLY_EQUIVALENT
    else:
        status = DualShadowStatus.V2_ONLY_EQUIVALENT
    return VersionedEvaluationResult(mode, v1, v2, status)
