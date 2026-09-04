"""Provider-free candidate result-equivalence contract for M9.2.

This module is deliberately evaluation-only.  It does not replace
``evaluation.metrics`` and it never changes SQL or repairs result tables.
Semantic bindings on :class:`BoundResult` are frozen evaluation-oracle
annotations, not an automatic runtime column resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

CONTRACT_VERSION = "result-equivalence-contract-v1"
CANDIDATE_VERSION = "m92-result-equivalence-candidate-v1"


class SlotKind(StrEnum):
    DIMENSION = "DIMENSION"
    MEASURE = "MEASURE"
    ENTITY_ID = "ENTITY_ID"
    DISPLAY_LABEL = "DISPLAY_LABEL"
    AUXILIARY = "AUXILIARY"


class ScalarOrTabular(StrEnum):
    SCALAR = "SCALAR"
    TABULAR = "TABULAR"


class RowOrderPolicy(StrEnum):
    ORDER_INSENSITIVE = "ORDER_INSENSITIVE"
    ORDER_REQUIRED = "ORDER_REQUIRED"


class DuplicatePolicy(StrEnum):
    PRESERVE = "PRESERVE"


class ExtraOutputPolicy(StrEnum):
    FORBID_EXTRA_OUTPUT = "FORBID_EXTRA_OUTPUT"
    ALLOW_DECLARED_OPTIONAL_ONLY = "ALLOW_DECLARED_OPTIONAL_ONLY"


class Outcome(StrEnum):
    STRICT_EQUIVALENT = "STRICT_EQUIVALENT"
    CONTRACT_EQUIVALENT = "CONTRACT_EQUIVALENT"
    NOT_EQUIVALENT = "NOT_EQUIVALENT"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    UNDETERMINED = "UNDETERMINED"


class ReasonCode(StrEnum):
    EXACT_OR_EXISTING_EQUIVALENCE = "EXACT_OR_EXISTING_EQUIVALENCE"
    DECLARED_OPTIONAL_EXTRA_IGNORED = "DECLARED_OPTIONAL_EXTRA_IGNORED"
    MISSING_REQUIRED_SLOT = "MISSING_REQUIRED_SLOT"
    UNDECLARED_EXTRA_OUTPUT = "UNDECLARED_EXTRA_OUTPUT"
    WRONG_REQUIRED_VALUE = "WRONG_REQUIRED_VALUE"
    WRONG_ROW_IDENTITY = "WRONG_ROW_IDENTITY"
    ROW_CARDINALITY_MISMATCH = "ROW_CARDINALITY_MISMATCH"
    DUPLICATE_MISMATCH = "DUPLICATE_MISMATCH"
    ORDER_MISMATCH = "ORDER_MISMATCH"
    SCALAR_TABULAR_MISMATCH = "SCALAR_TABULAR_MISMATCH"
    GRAIN_CHANGE_REQUIRED_TO_MATCH = "GRAIN_CHANGE_REQUIRED_TO_MATCH"
    AMBIGUOUS_SLOT_BINDING = "AMBIGUOUS_SLOT_BINDING"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    NO_REFERENCE_VARIANT_MATCH = "NO_REFERENCE_VARIANT_MATCH"
    OTHER = "OTHER"
    UNDETERMINED = "UNDETERMINED"


@dataclass(frozen=True)
class ResultSlot:
    slot_id: str
    kind: SlotKind
    required: bool
    role: str
    semantic_identity: str
    display_only: bool = False


@dataclass(frozen=True)
class ResultEquivalenceContract:
    contract_version: str
    slots: tuple[ResultSlot, ...]
    row_identity_slots: tuple[str, ...]
    required_measure_slots: tuple[str, ...]
    scalar_or_tabular: ScalarOrTabular
    row_order_policy: RowOrderPolicy
    duplicate_policy: DuplicatePolicy
    extra_output_policy: ExtraOutputPolicy
    null_policy_reference: str = "evaluation.metrics canonical NULL semantics"
    numeric_tolerance_reference: str = "evaluation.metrics rel_tol=abs_tol=1e-6"
    notes: str = ""

    @property
    def required_slots(self) -> tuple[ResultSlot, ...]:
        return tuple(slot for slot in self.slots if slot.required)

    @property
    def optional_slots(self) -> tuple[ResultSlot, ...]:
        return tuple(slot for slot in self.slots if not slot.required)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResultEquivalenceContract:
        slots = tuple(
            ResultSlot(
                slot_id=str(item["slot_id"]),
                kind=SlotKind(item["kind"]),
                required=bool(item["required"]),
                role=str(item["role"]),
                semantic_identity=str(item["semantic_identity"]),
                display_only=bool(item.get("display_only", False)),
            )
            for item in value["slots"]
        )
        return cls(
            contract_version=str(value["contract_version"]),
            slots=slots,
            row_identity_slots=tuple(value.get("row_identity_slots", ())),
            required_measure_slots=tuple(value.get("required_measure_slots", ())),
            scalar_or_tabular=ScalarOrTabular(value["scalar_or_tabular"]),
            row_order_policy=RowOrderPolicy(value["row_order_policy"]),
            duplicate_policy=DuplicatePolicy(value["duplicate_policy"]),
            extra_output_policy=ExtraOutputPolicy(value["extra_output_policy"]),
            null_policy_reference=str(value.get("null_policy_reference", "")),
            numeric_tolerance_reference=str(value.get("numeric_tolerance_reference", "")),
            notes=str(value.get("notes", "")),
        )


@dataclass(frozen=True)
class BoundResult:
    """A bounded result table with frozen semantic slot bindings."""

    columns: tuple[str, ...]
    bindings: tuple[str | None, ...]
    rows: tuple[tuple[Any, ...], ...]
    scalar_or_tabular: ScalarOrTabular


@dataclass(frozen=True)
class Comparison:
    outcome: Outcome
    reason: ReasonCode
    reference_variant: int | None = None
    detail: str = ""


def validate_contract(contract: ResultEquivalenceContract) -> tuple[str, ...]:
    errors: list[str] = []
    if contract.contract_version != CONTRACT_VERSION:
        errors.append("unsupported contract version")
    ids = [slot.slot_id for slot in contract.slots]
    if len(ids) != len(set(ids)):
        errors.append("duplicate semantic slot IDs")
    known = set(ids)
    required = {slot.slot_id for slot in contract.required_slots}
    optional = {slot.slot_id for slot in contract.optional_slots}
    if required & optional:
        errors.append("slot is both required and optional")
    if not set(contract.row_identity_slots) <= required:
        errors.append("row identity references unknown or optional slot")
    if not set(contract.required_measure_slots) <= required:
        errors.append("required measure references unknown or optional slot")
    if any(not slot.semantic_identity for slot in contract.slots):
        errors.append("semantic identity is required")
    if contract.duplicate_policy is not DuplicatePolicy.PRESERVE:
        errors.append("only duplicate-preserving comparison is supported")
    if (
        contract.row_order_policy is RowOrderPolicy.ORDER_REQUIRED
        and not contract.row_identity_slots
    ):
        errors.append("ordered results require row identity slots")
    if contract.extra_output_policy is ExtraOutputPolicy.FORBID_EXTRA_OUTPUT and optional:
        errors.append("optional slots conflict with forbid-extra policy")
    if not known:
        errors.append("contract must declare at least one slot")
    return tuple(errors)


def _canonical(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def _value_equal(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    left, right = _canonical(left), _canonical(right)
    if isinstance(left, float) and isinstance(right, (int, float)):
        return abs(left - float(right)) <= tolerance + tolerance * abs(float(right))
    if isinstance(right, float) and isinstance(left, (int, float)):
        return abs(float(left) - right) <= tolerance + tolerance * abs(right)
    return bool(left == right)


def _rows_equal(
    actual: tuple[tuple[Any, ...], ...],
    expected: tuple[tuple[Any, ...], ...],
    order_policy: RowOrderPolicy,
) -> tuple[bool, ReasonCode]:
    if len(actual) != len(expected):
        return False, ReasonCode.ROW_CARDINALITY_MISMATCH
    if order_policy is RowOrderPolicy.ORDER_INSENSITIVE:
        actual = tuple(sorted(actual, key=repr))
        expected = tuple(sorted(expected, key=repr))
    elif actual != expected:
        return False, ReasonCode.ORDER_MISMATCH
    for actual_row, expected_row in zip(actual, expected, strict=True):
        if len(actual_row) != len(expected_row):
            return False, ReasonCode.ROW_CARDINALITY_MISMATCH
        for actual_value, expected_value in zip(actual_row, expected_row, strict=True):
            if not _value_equal(actual_value, expected_value):
                return False, ReasonCode.WRONG_REQUIRED_VALUE
    return True, ReasonCode.EXACT_OR_EXISTING_EQUIVALENCE


def _table_bindings(
    result: BoundResult, contract: ResultEquivalenceContract
) -> tuple[dict[str, int], ReasonCode | None]:
    if len(result.columns) != len(result.bindings):
        return {}, ReasonCode.AMBIGUOUS_SLOT_BINDING
    if any(binding is None for binding in result.bindings):
        return {}, ReasonCode.AMBIGUOUS_SLOT_BINDING
    indices: dict[str, int] = {}
    for index, binding in enumerate(result.bindings):
        assert binding is not None
        if binding in indices:
            return {}, ReasonCode.AMBIGUOUS_SLOT_BINDING
        indices[binding] = index
    return indices, None


def _project(
    result: BoundResult, bindings: dict[str, int], slots: tuple[str, ...]
) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row[bindings[slot]] for slot in slots) for row in result.rows)


def _variant_comparison(
    generated: BoundResult,
    reference: BoundResult,
    contract: ResultEquivalenceContract,
) -> Comparison:
    generated_bindings, generated_error = _table_bindings(generated, contract)
    reference_bindings, reference_error = _table_bindings(reference, contract)
    if generated_error or reference_error:
        return Comparison(
            Outcome.CONTRACT_INVALID,
            generated_error or reference_error or ReasonCode.CONTRACT_INVALID,
        )
    required = tuple(slot.slot_id for slot in contract.required_slots)
    optional = {slot.slot_id for slot in contract.optional_slots}
    missing_generated = set(required) - set(generated_bindings)
    missing_reference = set(required) - set(reference_bindings)
    if missing_generated or missing_reference:
        return Comparison(Outcome.NOT_EQUIVALENT, ReasonCode.MISSING_REQUIRED_SLOT)
    extras = set(generated_bindings) - set(required)
    if extras and contract.extra_output_policy is ExtraOutputPolicy.FORBID_EXTRA_OUTPUT:
        return Comparison(Outcome.NOT_EQUIVALENT, ReasonCode.UNDECLARED_EXTRA_OUTPUT)
    if not extras <= optional:
        return Comparison(Outcome.NOT_EQUIVALENT, ReasonCode.UNDECLARED_EXTRA_OUTPUT)
    if (
        generated.scalar_or_tabular is not contract.scalar_or_tabular
        or reference.scalar_or_tabular is not contract.scalar_or_tabular
    ):
        return Comparison(Outcome.NOT_EQUIVALENT, ReasonCode.SCALAR_TABULAR_MISMATCH)
    if contract.scalar_or_tabular is ScalarOrTabular.SCALAR and (
        len(generated.rows) != 1 or len(reference.rows) != 1
    ):
        return Comparison(Outcome.NOT_EQUIVALENT, ReasonCode.SCALAR_TABULAR_MISMATCH)
    actual_rows = _project(generated, generated_bindings, required)
    expected_rows = _project(reference, reference_bindings, required)
    equal, reason = _rows_equal(actual_rows, expected_rows, contract.row_order_policy)
    if not equal:
        return Comparison(Outcome.NOT_EQUIVALENT, reason)
    if extras:
        return Comparison(Outcome.CONTRACT_EQUIVALENT, ReasonCode.DECLARED_OPTIONAL_EXTRA_IGNORED)
    return Comparison(Outcome.STRICT_EQUIVALENT, ReasonCode.EXACT_OR_EXISTING_EQUIVALENCE)


def compare_contract(
    generated: BoundResult,
    references: tuple[BoundResult, ...],
    contract: ResultEquivalenceContract,
) -> Comparison:
    """Compare against any valid reference variant without repair or search."""
    errors = validate_contract(contract)
    if errors:
        return Comparison(
            Outcome.CONTRACT_INVALID, ReasonCode.CONTRACT_INVALID, detail="; ".join(errors)
        )
    if not references:
        return Comparison(Outcome.UNDETERMINED, ReasonCode.NO_REFERENCE_VARIANT_MATCH)
    first_failure: Comparison | None = None
    for index, reference in enumerate(references):
        result = _variant_comparison(generated, reference, contract)
        if result.outcome in (Outcome.STRICT_EQUIVALENT, Outcome.CONTRACT_EQUIVALENT):
            return Comparison(result.outcome, result.reason, index, result.detail)
        if result.outcome is Outcome.CONTRACT_INVALID:
            return result
        first_failure = first_failure or result
    assert first_failure is not None
    return Comparison(
        Outcome.NOT_EQUIVALENT, first_failure.reason, detail=ReasonCode.NO_REFERENCE_VARIANT_MATCH
    )


def strict_compare(
    generated: BoundResult, reference: BoundResult, order_policy: RowOrderPolicy
) -> bool:
    """Small V1-shaped comparator for regression diagnostics only."""
    if len(generated.columns) != len(reference.columns):
        return False
    if generated.scalar_or_tabular is not reference.scalar_or_tabular:
        return False
    return _rows_equal(generated.rows, reference.rows, order_policy)[0]
