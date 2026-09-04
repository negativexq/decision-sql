"""Offline M9.4 integration and provenance validation runner."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from evaluation.m92_equivalence_regression import build_cases as build_m92_cases
from evaluation.m92_equivalence_regression import run as run_m92
from evaluation.m93_equivalence_independent import build_cases as build_m93_cases
from evaluation.m93_equivalence_independent import decision as m93_decision
from evaluation.m93_equivalence_independent import evaluate as evaluate_m93
from evaluation.metrics import assess_query_results
from evaluation.result_equivalence_contract import Outcome, compare_contract, validate_contract
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    PROVENANCE_SCHEMA_VERSION,
    EvaluationComparisonRequest,
    EvaluationConfigurationError,
    EvaluationErrorCode,
    EvaluatorMode,
    as_query_execution,
    comparator_source_hash,
    evaluate,
    verify_frozen_comparator_source,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "results" / "m94" / "audit-20260904"

M7_HASH = "f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043"
M8_HASH = "e42d02cd04e31725eb38cf9da235787a410c28b3bd3b2397b35323754a1b319c"
M9_HASH = "6bd0e22b105607a0af8d17ff8774bba381ba61b4e43cdd88be2f10acc6e36d12"
M91_HASH = "7f838d8af24c62d89d37367c41efe1b404fbcb1c2f75ffa01419bd33162e74ac"
M92_FIXTURE_HASH = "b29bf443f4c883f31f0c14b0b5a0bfd5d2bf0acbc9ea0ff6c1810ac79b4360c7"
M92_FULL_HASH = "e19f8af090fb80521689649f577ba5f3db1ca98f2b81becf9d297531c2d3f276"
M93_HASH = "523f91f94d6c8f46db00d9cd6ec0368a0eff078f4af384fd1610cd285be483a2"
M3_HASH = "0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c"
M4_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"


def _core(result: Any) -> tuple[Any, ...]:
    return (
        result.equivalent,
        result.outcome,
        result.reason,
        result.reference_variant_index,
    )


def _request(case: Any, mode: EvaluatorMode | None = None) -> EvaluationComparisonRequest:
    order_sensitive = case.contract.row_order_policy.value == "ORDER_REQUIRED"
    kwargs: dict[str, Any] = {
        "generated_result": case.generated,
        "reference_result_variants": case.references,
        "result_contract": case.contract,
        "contract_id": f"{case.case_id}-contract",
        "order_sensitive": order_sensitive,
    }
    if mode is not None:
        kwargs["evaluator_mode"] = mode
    return EvaluationComparisonRequest(**kwargs)


def _write(name: str, value: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _v2_parity(cases: tuple[Any, ...]) -> dict[str, Any]:
    failures: list[str] = []
    for case in cases:
        direct = compare_contract(case.generated, case.references, case.contract)
        try:
            integrated = evaluate(_request(case, EvaluatorMode.V2)).authoritative
        except EvaluationConfigurationError as error:
            if (
                validate_contract(case.contract)
                and direct.outcome is Outcome.CONTRACT_INVALID
                and error.code is EvaluationErrorCode.EVALUATOR_CONTRACT_INVALID
            ):
                continue
            failures.append(case.case_id)
            continue
        actual = _core(integrated)
        expected = (
            direct.outcome in (Outcome.STRICT_EQUIVALENT, Outcome.CONTRACT_EQUIVALENT),
            direct.outcome.value,
            direct.reason.value,
            direct.reference_variant,
        )
        if actual != expected:
            failures.append(case.case_id)
    return {"cases": len(cases), "parity": len(failures) == 0, "failures": failures}


def _v1_parity(cases: tuple[Any, ...]) -> dict[str, Any]:
    selected = tuple(case for case in cases if len(case.references) == 1)[:100]
    failures: list[str] = []
    for case in selected:
        actual = as_query_execution(case.generated)
        expected = as_query_execution(case.references[0])
        direct = assess_query_results(
            actual,
            expected,
            order_sensitive=case.contract.row_order_policy.value == "ORDER_REQUIRED",
        )
        integrated = evaluate(_request(case, EvaluatorMode.V1)).authoritative
        if direct.equivalent != integrated.equivalent:
            failures.append(case.case_id)
    return {"cases": len(selected), "parity": len(failures) == 0, "failures": failures}


def _default_parity(cases: tuple[Any, ...]) -> dict[str, Any]:
    selected = cases[:12]
    failures: list[str] = []
    for case in selected:
        default_request = _request(case)
        explicit_request = _request(case, EvaluatorMode.V1)
        default = evaluate(default_request).authoritative
        explicit = evaluate(explicit_request).authoritative
        if _core(default) != _core(explicit):
            failures.append(case.case_id)
    return {"cases": len(selected), "parity": len(failures) == 0, "failures": failures}


def _dual_validation(cases: tuple[Any, ...]) -> dict[str, Any]:
    valid_cases = tuple(case for case in cases if not validate_contract(case.contract))
    disagreements = 0
    authoritative_failures: list[str] = []
    shadow_failures: list[str] = []
    for case in valid_cases:
        v1 = evaluate(_request(case, EvaluatorMode.V1))
        v2 = evaluate(_request(case, EvaluatorMode.V2))
        dual = evaluate(_request(case, EvaluatorMode.DUAL_SHADOW))
        assert dual.shadow is not None
        if _core(dual.authoritative) != _core(v1.authoritative):
            authoritative_failures.append(case.case_id)
        if _core(dual.shadow) != _core(v2.authoritative):
            shadow_failures.append(case.case_id)
        if dual.authoritative.equivalent != dual.shadow.equivalent:
            disagreements += 1
    return {
        "cases": len(valid_cases),
        "invalid_cases_excluded": len(cases) - len(valid_cases),
        "disagreements": disagreements,
        "authoritative_parity_failures": authoritative_failures,
        "shadow_parity_failures": shadow_failures,
        "parity": not authoritative_failures and not shadow_failures,
    }


def _fail_closed_checks(case: Any) -> dict[str, Any]:
    missing: str | None = None
    invalid: str | None = None
    invalid_dual: str | None = None
    try:
        evaluate(
            EvaluationComparisonRequest(
                case.generated, case.references, evaluator_mode=EvaluatorMode.V2
            )
        )
    except EvaluationConfigurationError as error:
        missing = error.code.value
    invalid_contract = replace(case.contract, row_identity_slots=("unknown",))
    try:
        evaluate(replace(_request(case, EvaluatorMode.V2), result_contract=invalid_contract))
    except EvaluationConfigurationError as error:
        invalid = error.code.value
    try:
        evaluate(
            replace(
                _request(case, EvaluatorMode.DUAL_SHADOW),
                result_contract=invalid_contract,
            )
        )
    except EvaluationConfigurationError as error:
        invalid_dual = error.code.value
    return {
        "missing_contract": missing,
        "invalid_contract": invalid,
        "invalid_dual_contract": invalid_dual,
        "missing_contract_pass": missing == EvaluationErrorCode.EVALUATOR_CONTRACT_REQUIRED,
        "invalid_contract_pass": invalid == EvaluationErrorCode.EVALUATOR_CONTRACT_INVALID,
        "invalid_dual_contract_pass": (
            invalid_dual == EvaluationErrorCode.EVALUATOR_CONTRACT_INVALID
        ),
    }


def main() -> None:
    source_before = verify_frozen_comparator_source()
    m92_cases = build_m92_cases()
    m93_cases = build_m93_cases()
    m92_direct = run_m92()
    m93_direct = evaluate_m93(m93_cases)
    m93_direct_decision = m93_decision(
        m93_direct["summary"], source_before, source_before
    )
    m92_parity = _v2_parity(m92_cases)
    m93_parity = _v2_parity(m93_cases)
    v1_parity = _v1_parity(m93_cases)
    default_parity = _default_parity(m93_cases)
    dual = _dual_validation(m93_cases)
    fail_closed = _fail_closed_checks(m93_cases[0])
    disagreement_case = m93_cases[0]
    dual_disagreement = evaluate(_request(disagreement_case, EvaluatorMode.DUAL_SHADOW))
    source_after = comparator_source_hash()
    gates = {
        "source_hash_unchanged": source_before == source_after == FROZEN_V2_COMPARATOR_SOURCE_HASH,
        "m92_direct_accepted": m92_direct["decision"]["classification"]
        == "RESULT_EQUIVALENCE_CONTRACT_ACCEPTED",
        "m92_v2_parity": m92_parity["parity"] and m92_parity["cases"] == 143,
        "m93_direct_accepted": m93_direct_decision["classification"]
        == "RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED",
        "m93_v2_parity": m93_parity["parity"] and m93_parity["cases"] == 160,
        "v1_parity": v1_parity["parity"] and v1_parity["cases"] == 100,
        "default_v1_parity": default_parity["parity"],
        "missing_contract_fail_closed": fail_closed["missing_contract_pass"],
        "invalid_contract_fail_closed": fail_closed["invalid_contract_pass"],
        "invalid_dual_contract_fail_closed": fail_closed["invalid_dual_contract_pass"],
        "dual_authority_parity": dual["parity"],
        "no_best_of": not dual_disagreement.equivalent,
    }
    classification = (
        "VERSIONED_EVALUATOR_V2_INTEGRATION_ACCEPTED"
        if all(gates.values())
        else "VERSIONED_EVALUATOR_V2_INTEGRATION_REJECTED"
    )
    provenance_examples = [
        evaluate(_request(m93_cases[1], EvaluatorMode.V1)).to_dict(),
        evaluate(_request(m93_cases[0], EvaluatorMode.V2)).to_dict(),
        dual_disagreement.to_dict(),
    ]
    _write(
        "metadata.json",
        {
            "milestone": "M9.4",
            "run_id": "audit-20260904",
            "provider_calls": 0,
            "sql_generation_calls": 0,
            "db_calls": 0,
            "db_mutations": 0,
            "m7_hash": M7_HASH,
            "m8_hash": M8_HASH,
            "m9_hash": M9_HASH,
            "m91_hash": M91_HASH,
            "m92_fixture_hash": M92_FIXTURE_HASH,
            "m92_full_hash": M92_FULL_HASH,
            "m93_hash": M93_HASH,
            "m3_contract_hash": M3_HASH,
            "m4_corpus_hash": M4_HASH,
            "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
            "frozen_v2_comparator_source_hash": source_before,
        },
    )
    _write(
        "frozen_hash_validation.json",
        {
            "source_before": source_before,
            "source_after": source_after,
            "passed": gates["source_hash_unchanged"],
        },
    )
    _write("v1_parity.json", v1_parity)
    _write("m92_v2_integration_parity.json", m92_parity)
    _write("m93_v2_integration_parity.json", m93_parity)
    _write("default_path_parity.json", default_parity)
    _write("dual_shadow_validation.json", dual)
    _write("provenance_examples.json", provenance_examples)
    provenance_requests = (
        _request(m93_cases[1], EvaluatorMode.V1),
        _request(m93_cases[0], EvaluatorMode.V2),
        _request(m93_cases[0], EvaluatorMode.DUAL_SHADOW),
    )
    first_hashes = [
        evaluate(item).authoritative.provenance.semantic_hash()
        for item in provenance_requests
    ]
    second_hashes = [
        evaluate(item).authoritative.provenance.semantic_hash()
        for item in provenance_requests
    ]
    _write(
        "provenance_determinism.json",
        {"hashes": first_hashes, "passed": first_hashes == second_hashes},
    )
    _write(
        "legacy_compatibility.json",
        {"rewritten": False, "read_policy": "historical artifacts unchanged"},
    )
    _write(
        "final_decision.json",
        {
            "classification": classification,
            "gates": gates,
            "fail_closed": fail_closed,
            "next_milestone": (
                "M10 — Clean Residual Rebaseline"
                if classification == "VERSIONED_EVALUATOR_V2_INTEGRATION_ACCEPTED"
                else None
            ),
        },
    )
    print(
        json.dumps(
            {"classification": classification, "gates": gates, "output": str(OUT)},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
