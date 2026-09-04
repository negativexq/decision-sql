"""Unit tests for the explicit M9.4 evaluator boundary."""

from dataclasses import replace

import pytest

from evaluation.m92_equivalence_regression import build_cases as build_m92_cases
from evaluation.m93_equivalence_independent import build_cases as build_m93_cases
from evaluation.metrics import assess_query_results
from evaluation.result_equivalence_contract import Outcome, compare_contract, validate_contract
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    EvaluationComparisonRequest,
    EvaluationConfigurationError,
    EvaluationErrorCode,
    EvaluatorMode,
    EvaluatorVersion,
    as_query_execution,
    comparator_source_hash,
    contract_instance_hash,
    evaluate,
    verify_frozen_comparator_source,
)


def request(case, mode: EvaluatorMode = EvaluatorMode.V1):
    return EvaluationComparisonRequest(
        case.generated,
        case.references,
        mode,
        case.contract,
        f"{case.case_id}-contract",
        case.contract.row_order_policy.value == "ORDER_REQUIRED",
    )


def test_default_and_explicit_v1_are_identical() -> None:
    for case in build_m93_cases()[:12]:
        default = evaluate(
            EvaluationComparisonRequest(
                case.generated,
                case.references,
                result_contract=case.contract,
                contract_id=f"{case.case_id}-contract",
                order_sensitive=case.contract.row_order_policy.value == "ORDER_REQUIRED",
            )
        )
        explicit = evaluate(request(case, EvaluatorMode.V1))
        assert default.to_dict() == explicit.to_dict()
        assert default.authoritative.evaluator is EvaluatorVersion.V1


def test_v2_matches_frozen_m92_and_m93_comparators() -> None:
    for case in (*build_m92_cases(), *build_m93_cases()):
        direct = compare_contract(case.generated, case.references, case.contract)
        if validate_contract(case.contract):
            with pytest.raises(EvaluationConfigurationError) as error:
                evaluate(request(case, EvaluatorMode.V2))
            assert error.value.code is EvaluationErrorCode.EVALUATOR_CONTRACT_INVALID
            continue
        integrated = evaluate(request(case, EvaluatorMode.V2)).authoritative
        assert integrated.equivalent == (
            direct.outcome in (Outcome.STRICT_EQUIVALENT, Outcome.CONTRACT_EQUIVALENT)
        )
        assert integrated.outcome == direct.outcome.value
        assert integrated.reason == direct.reason.value
        assert integrated.reference_variant_index == direct.reference_variant


def test_v1_adapter_has_full_parity_on_one_hundred_cases() -> None:
    cases = tuple(case for case in build_m93_cases() if len(case.references) == 1)[:100]
    assert len(cases) == 100
    for case in cases:
        direct = assess_query_results(
            as_query_execution(case.generated),
            as_query_execution(case.references[0]),
            order_sensitive=case.contract.row_order_policy.value == "ORDER_REQUIRED",
        )
        assert evaluate(request(case)).authoritative.equivalent == direct.equivalent


def test_v2_requires_contract_and_never_falls_back() -> None:
    case = build_m93_cases()[0]
    with pytest.raises(EvaluationConfigurationError) as missing:
        evaluate(EvaluationComparisonRequest(case.generated, case.references, EvaluatorMode.V2))
    assert missing.value.code is EvaluationErrorCode.EVALUATOR_CONTRACT_REQUIRED

    invalid = replace(case.contract, row_identity_slots=("unknown",))
    with pytest.raises(EvaluationConfigurationError) as rejected:
        evaluate(replace(request(case, EvaluatorMode.V2), result_contract=invalid))
    assert rejected.value.code is EvaluationErrorCode.EVALUATOR_CONTRACT_INVALID


def test_dual_shadow_keeps_v1_authoritative_and_preserves_disagreement() -> None:
    case = build_m93_cases()[0]
    v1 = evaluate(request(case, EvaluatorMode.V1))
    v2 = evaluate(request(case, EvaluatorMode.V2))
    dual = evaluate(request(case, EvaluatorMode.DUAL_SHADOW))
    assert dual.shadow is not None
    assert dual.authoritative.equivalent is v1.authoritative.equivalent
    assert dual.shadow.equivalent is v2.authoritative.equivalent
    assert dual.authoritative.equivalent is False
    assert dual.shadow.equivalent is True
    assert dual.dual_shadow_status.value == "V2_ONLY_EQUIVALENT"


def test_invalid_dual_request_fails_closed() -> None:
    case = build_m93_cases()[0]
    with pytest.raises(EvaluationConfigurationError) as error:
        evaluate(
            EvaluationComparisonRequest(
                case.generated, case.references, EvaluatorMode.DUAL_SHADOW
            )
        )
    assert error.value.code is EvaluationErrorCode.EVALUATOR_CONTRACT_REQUIRED

    invalid = replace(case.contract, row_identity_slots=("unknown",))
    with pytest.raises(EvaluationConfigurationError) as invalid_error:
        evaluate(replace(request(case, EvaluatorMode.DUAL_SHADOW), result_contract=invalid))
    assert invalid_error.value.code is EvaluationErrorCode.EVALUATOR_CONTRACT_INVALID


def test_invalid_mode_fails_closed() -> None:
    case = build_m93_cases()[0]
    with pytest.raises(EvaluationConfigurationError) as error:
        evaluate(
            EvaluationComparisonRequest(
                case.generated,
                case.references,
                evaluator_mode="UNKNOWN",  # type: ignore[arg-type]
            )
        )
    assert error.value.code is EvaluationErrorCode.EVALUATOR_MODE_INVALID


def test_contract_hash_and_frozen_source_hash_are_deterministic() -> None:
    contract = build_m93_cases()[0].contract
    reordered = replace(contract, slots=tuple(reversed(contract.slots)))
    assert contract_instance_hash(contract) == contract_instance_hash(reordered)
    assert comparator_source_hash() == FROZEN_V2_COMPARATOR_SOURCE_HASH
    assert verify_frozen_comparator_source() == FROZEN_V2_COMPARATOR_SOURCE_HASH


def test_semantic_provenance_hash_is_deterministic() -> None:
    case = build_m93_cases()[0]
    first = evaluate(request(case, EvaluatorMode.V2)).authoritative.provenance
    second = evaluate(request(case, EvaluatorMode.V2)).authoritative.provenance
    assert first.semantic_hash() == second.semantic_hash()


def test_provenance_is_bounded_and_explicit() -> None:
    case = build_m93_cases()[0]
    v1 = evaluate(request(case, EvaluatorMode.V1)).to_dict()
    v2 = evaluate(request(case, EvaluatorMode.V2)).to_dict()
    assert v1["authoritative"]["provenance"]["evaluator_id"] == "decision-result-evaluator-v1"
    assert v1["authoritative"]["provenance"]["contract_instance_hash"] is None
    assert v2["authoritative"]["provenance"]["evaluator_id"] == "decision-result-evaluator-v2"
    assert v2["authoritative"]["provenance"]["contract_instance_hash"]
    assert (
        v2["authoritative"]["provenance"]["comparator_source_hash"]
        == FROZEN_V2_COMPARATOR_SOURCE_HASH
    )
    serialized = str(v2)
    assert "sql" not in serialized.lower()
    assert "rows" not in serialized.lower()
    assert "password" not in serialized.lower()
