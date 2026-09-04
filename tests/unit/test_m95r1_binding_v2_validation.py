from __future__ import annotations

from collections import Counter

from evaluation.m95r1_binding_v2_validation import (
    build_adversarial_cases,
    build_independent_cases,
    run,
)


def test_new_independent_recipe_has_exact_frozen_composition() -> None:
    cases = build_independent_cases()
    assert len(cases) == 120
    assert len({case.case_id for case in cases}) == 120
    assert Counter(case.category for case in cases) == Counter(
        {
            "SEMANTICALLY_CORRECT_POSITIVE": 60,
            "SEMANTICALLY_WRONG_EXECUTABLE": 40,
            "FAIL_CLOSED_AMBIGUOUS_OR_UNSUPPORTED": 20,
        }
    )
    assert Counter(case.family for case in cases)["GOVERNED_PROVENANCE"] == 20
    assert (
        sum(
            case.family
            in {
                "UNIQUE_DIRECT_COLUMN_QUALIFIED_MULTI_TABLE",
                "CTE_OR_DERIVED_PASSTHROUGH",
                "AGGREGATE_OR_DISTINCT",
                "ARITHMETIC_OR_RATIO",
                "CASE_OR_SAFE_CAST",
                "WINDOW",
                "SCALAR_OR_BOUNDED_NESTED_EXPRESSION",
            }
            for case in cases
        )
        == 40
    )


def test_new_adversarial_recipe_has_exact_frozen_composition() -> None:
    cases = build_adversarial_cases()
    assert len(cases) == 60
    assert len({case.case_id for case in cases}) == 60
    assert Counter(case.family for case in cases) == Counter(
        {
            "SCOPE_AND_RELATION_INSTANCE": 12,
            "ALIAS_SPOOF_AND_COLLISION": 10,
            "NORMALIZATION_AND_FINGERPRINT_COLLISION": 10,
            "AGGREGATE_AND_DISTINCT": 8,
            "ARITHMETIC_AND_RATIO": 8,
            "WINDOW": 8,
            "MULTI_SLOT_OR_UNSUPPORTED": 4,
        }
    )


def test_independent_validation_is_one_shot_safe_and_nonleaky() -> None:
    result = run()
    assert result["classification"] == "FAIL_CLOSED_BINDER_V2_INDEPENDENTLY_VALIDATED"
    assert result["binder_v2_unchanged"] is True
    assert result["independent"]["all_expected"] is True
    assert result["adversarial"]["all_expected"] is True
    assert result["positive_count"] == 60
    assert result["positive_evaluable"] == 60
    assert result["failclosed_count"] == 20
    assert result["independent"]["v2_calls"] == 60
    assert result["independent"]["nonleaky_api"] is True
    assert result["provider_calls"] == 0
    assert result["m10_cases_consumed"] == 0
