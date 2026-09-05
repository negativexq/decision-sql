from __future__ import annotations

import inspect
import json
from pathlib import Path

from evaluation.m110r_memory_compatibility import classify_entry
from evaluation.m111p1_semantic_adaptability import (
    AdaptabilityState,
    SemanticRelationState,
    assess_pair,
    assessment_dict,
    semantic_profile,
)

FIXTURE_DIR = Path("evaluation/fixtures")


def _case(fixture_id: str) -> dict[str, str]:
    cases = json.loads(
        (FIXTURE_DIR / "m111p1_validation_fixtures.json").read_text()
    )["cases"]
    return next(case for case in cases if case["fixture_id"] == fixture_id)


def test_validation_labels_are_frozen_and_all_pass() -> None:
    cases = json.loads(
        (FIXTURE_DIR / "m111p1_validation_fixtures.json").read_text()
    )["cases"]
    labels = {
        label["fixture_id"]: label
        for label in json.loads(
            (FIXTURE_DIR / "m111p1_validation_expected.json").read_text()
        )["labels"]
    }
    assert len(cases) == len(labels) == 40
    for case in cases:
        result = assessment_dict(assess_pair(case["target_sql"], case["memory_sql"]))
        label = labels[case["fixture_id"]]
        assert result["semantic_relation"] == label["expected_semantic_relation"]
        assert result["adaptability"] == label["expected_adaptability"]


def test_mandatory_five_counterexamples() -> None:
    expected = {
        "v01": ("PROVEN_MATERIAL_CONFLICT", "PROVEN_ADAPTABLE"),
        "v02": ("PROVEN_EQUIVALENT", "PROVEN_ADAPTABLE"),
        "v03": ("PROVEN_MATERIAL_CONFLICT", "PROVEN_NON_ADAPTABLE"),
        "v04": ("PROVEN_MATERIAL_CONFLICT", "PROVEN_ADAPTABLE"),
        "v05": ("PROVEN_MATERIAL_CONFLICT", "PROVEN_NON_ADAPTABLE"),
    }
    for fixture_id, states in expected.items():
        case = _case(fixture_id)
        result = assessment_dict(assess_pair(case["target_sql"], case["memory_sql"]))
        assert (result["semantic_relation"], result["adaptability"]) == states


def test_safe_normalizations_and_material_profiles() -> None:
    for fixture_id in ("v06", "v07", "v08", "v09", "v10", "v27", "v29", "v33"):
        case = _case(fixture_id)
        result = assess_pair(case["target_sql"], case["memory_sql"])
        assert result.semantic_relation is SemanticRelationState.PROVEN_EQUIVALENT
        assert result.adaptability is AdaptabilityState.PROVEN_ADAPTABLE

    join_case = _case("v03")
    join_profile, join_reason = semantic_profile(join_case["target_sql"])
    memory_join_profile, memory_join_reason = semantic_profile(join_case["memory_sql"])
    assert join_reason is None and memory_join_reason is None
    assert join_profile is not None and memory_join_profile is not None
    assert join_profile.joins[0].startswith("INNER|")
    assert memory_join_profile.joins[0].startswith("LEFT|")
    having_profile, having_reason = semantic_profile(_case("v04")["target_sql"])
    assert having_reason is None
    assert having_profile is not None and having_profile.having
    order_profile, order_reason = semantic_profile(_case("v05")["target_sql"])
    assert order_reason is None
    assert order_profile is not None
    assert order_profile.ordering[0] != order_profile.ordering[1]


def test_parameter_slots_are_explicit_and_bounded() -> None:
    cases = (
        ("v01", "WHERE_NUMERIC_LITERAL"),
        ("v04", "HAVING_NUMERIC_LITERAL"),
        ("v12", "LIMIT_INTEGER"),
    )
    for fixture_id, family in cases:
        case = _case(fixture_id)
        result = assess_pair(case["target_sql"], case["memory_sql"])
        assert result.adaptability is AdaptabilityState.PROVEN_ADAPTABLE
        assert result.parameter_slots
        assert all(slot["kind"] == family for slot in result.parameter_slots)


def test_unknown_fails_closed() -> None:
    for fixture_id in ("v37", "v38", "v39", "v40"):
        case = _case(fixture_id)
        result = assess_pair(case["target_sql"], case["memory_sql"])
        assert result.semantic_relation is SemanticRelationState.UNKNOWN
        assert result.adaptability is AdaptabilityState.UNKNOWN
        assert result.unknown_reason


def test_profiles_and_assessments_are_deterministic() -> None:
    case = _case("v01")
    first_profile = semantic_profile(case["target_sql"])
    second_profile = semantic_profile(case["target_sql"])
    first_assessment = assessment_dict(assess_pair(case["target_sql"], case["memory_sql"]))
    second_assessment = assessment_dict(assess_pair(case["target_sql"], case["memory_sql"]))
    assert first_profile == second_profile
    assert first_assessment == second_assessment


def test_contract_has_no_outcome_or_runtime_inputs() -> None:
    assert set(inspect.signature(assess_pair).parameters) == {"target_sql", "memory_sql"}
    source = Path("evaluation/m111p1_semantic_adaptability.py").read_text()
    assert "from app" not in source
    assert "import app" not in source
    assert "retrieval_score" not in source
    assert "from app" not in source


def test_legacy_comparison_is_observed_without_mutating_the_legacy_contract() -> None:
    expected = {
        "v01": "PROVEN_INCOMPATIBLE",
        "v02": "PROVEN_INCOMPATIBLE",
        "v03": "PROVEN_COMPATIBLE",
        "v04": "PROVEN_COMPATIBLE",
        "v05": "PROVEN_COMPATIBLE",
    }
    for fixture_id, state in expected.items():
        case = _case(fixture_id)
        observed = classify_entry(case["target_sql"], case["memory_sql"])
        assert observed.state.value == state
