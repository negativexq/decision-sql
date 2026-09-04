"""M9.2 corpus and gate tests; no provider or database dependency."""

from evaluation.m92_equivalence_regression import (
    build_cases,
    evaluate,
    final_decision,
    fixture_hash,
    stable_hash,
    validate_corpus,
)


def test_frozen_population_membership_and_counts() -> None:
    cases = build_cases()
    validation = validate_corpus(cases)
    assert validation["passed"] is True
    assert validation["group_counts"] == {
        "POSITIVE_EQUIVALENT_ARTIFACTS": 10,
        "NEGATIVE_PROJECTION_VIOLATIONS": 19,
        "NEGATIVE_SEMANTIC_ERRORS": 2,
        "CORRECT_CONTROLS": 75,
        "NON_S11_NEGATIVE_CONTROLS": 7,
    }
    assert len(cases) == 143
    assert len({case.case_id for case in cases}) == 143


def test_contract_candidate_satisfies_all_frozen_gates() -> None:
    result = evaluate()
    summary = result["summary"]
    assert summary["artifacts"] == {"N": 10, "recovered": 10}
    assert summary["projection_negatives"] == {"N": 19, "false_accepts": 0}
    assert summary["semantic_negatives"] == {"N": 2, "false_accepts": 0}
    assert summary["correct_controls"] == {"N": 75, "accepted": 75}
    assert summary["non_s11_negatives"] == {"N": 7, "false_accepts": 0}
    assert summary["synthetic"] == {"N": 30, "accepted_as_expected": 30, "failures": 0}
    assert final_decision(summary)["classification"] == "RESULT_EQUIVALENCE_CONTRACT_ACCEPTED"


def test_fixture_and_evaluation_are_deterministic() -> None:
    assert fixture_hash() == fixture_hash()
    first = evaluate()
    second = evaluate()
    assert stable_hash(first) == stable_hash(second)
