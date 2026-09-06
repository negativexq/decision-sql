import json
from collections import Counter
from pathlib import Path

from evaluation.m22_2_projection_semantics_forensics import (
    _projection_deltas,
    projection_facts,
    validate_artifact,
)

ARTIFACT = Path("evaluation/fixtures/m22_2_projection_semantics_forensics.json")


def _artifact() -> dict:
    artifact = json.loads(ARTIFACT.read_text())
    validate_artifact(artifact)
    return artifact


def test_m222_projection_population_and_causal_counts_reconcile() -> None:
    artifact = _artifact()
    assert artifact["population"] == {
        "confirmed_projection_causal": 17,
        "m20_2_native_projection": 3,
        "m22_1_projection_causal": 15,
        "overlap": 0,
        "projection_not_single_causal": 1,
        "unique_candidates": 18,
    }
    assert sum(artifact["primary_subtypes"].values()) == 18
    assert len(artifact["cases"]) == 18
    assert artifact["provider_calls"] == 0
    assert artifact["fresh_generation"] == 0
    assert artifact["production_changes"] == 0


def test_projection_aliases_are_not_semantic_differences() -> None:
    left = projection_facts("SELECT t.a AS x FROM t")
    right = projection_facts("SELECT t.a AS y FROM t")

    assert left["normalized_expressions"] == right["normalized_expressions"]
    assert _projection_deltas(left, right) == []


def test_projection_order_remains_visible_to_positional_evaluator_analysis() -> None:
    left = projection_facts("SELECT a, b FROM t")
    right = projection_facts("SELECT b, a FROM t")

    assert left["normalized_expressions"] != right["normalized_expressions"]
    assert "EXPRESSION" in _projection_deltas(left, right)


def test_projection_subtype_totals_are_stable() -> None:
    artifact = _artifact()
    counts = Counter(case["primary_subtype"] for case in artifact["cases"])

    assert counts == Counter(artifact["primary_subtypes"])


def test_native_projection_population_keeps_filter_reclassification_visible() -> None:
    artifact = _artifact()
    advanced = next(case for case in artifact["cases"] if case["case_id"] == "advanced:44")

    assert advanced["projection_causal"] is False
    assert advanced["reclassified_cause"] == "FILTER_CAUSAL"
    assert any(
        item["dimension"] == "FILTER" and item["result_match"]
        for item in advanced["counterfactuals"]
    )
