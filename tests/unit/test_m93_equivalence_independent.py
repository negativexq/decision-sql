"""Offline independent-validation tests for M9.3."""

from evaluation.m92_equivalence_regression import run as run_m92
from evaluation.m93_equivalence_independent import (
    CANDIDATE_VERSION,
    CONTRACT_VERSION,
    build_cases,
    decision,
    evaluate,
    fixture_hash,
    manifest,
    source_hash,
    validate,
)


def test_independent_corpus_is_frozen_160_cases_with_balanced_splits() -> None:
    cases = build_cases()
    result = validate(cases)
    assert result["passed"] is True
    assert result["case_count"] == 160
    assert result["split_counts"] == {"VALIDATION_A": 80, "VALIDATION_B": 80}
    assert result["m91_overlap"] == 0
    assert result["m92_overlap"] == 0
    assert result["category_counts"] == {
        "CONTRACT_EQUIVALENT_POSITIVES": 40,
        "STRICT_EQUIVALENT_CONTROLS": 30,
        "PROJECTION_NEGATIVES": 30,
        "GRAIN_CARDINALITY_NEGATIVES": 20,
        "VALUE_SEMANTIC_NEGATIVES": 20,
        "ORDER_DUPLICATE_SHAPE_NEGATIVES": 10,
        "INVALID_OR_AMBIGUOUS": 10,
    }
    positives = [case for case in cases if case.category == "CONTRACT_EQUIVALENT_POSITIVES"]
    assert sum(len(case.contract.optional_slots) == 2 for case in positives) == 5
    assert sum(case.contract.scalar_or_tabular.value == "SCALAR" for case in positives) == 5
    assert manifest(cases)["version"] == "m93-equivalence-independent-v2"


def test_frozen_candidate_passes_every_independent_category() -> None:
    result = evaluate(build_cases())
    assert result["summary"]["passes"] == 160
    assert result["summary"]["false_accepts"] == 0
    assert result["summary"]["false_rejects"] == 0
    assert (
        decision(result["summary"], source_hash(), source_hash())["classification"]
        == "RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED"
    )


def test_manifest_and_source_hashes_are_deterministic() -> None:
    cases = build_cases()
    assert fixture_hash() == fixture_hash()
    assert manifest(cases) == manifest(cases)
    assert source_hash() == source_hash()
    assert CONTRACT_VERSION == "result-equivalence-contract-v1"
    assert CANDIDATE_VERSION == "m92-result-equivalence-candidate-v1"


def test_m92_regression_remains_accepted_and_unchanged() -> None:
    before = run_m92()
    after = run_m92()
    assert before["decision"]["classification"] == "RESULT_EQUIVALENCE_CONTRACT_ACCEPTED"
    assert before["evaluation"]["summary"] == after["evaluation"]["summary"]
    assert before["decision"] == after["decision"]
