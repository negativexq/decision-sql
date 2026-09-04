"""Provider- and DB-free checks for the completed M10.4S audit."""

from __future__ import annotations

from evaluation.m104s_causal_audit import MECHANISMS, audit


def test_m104s_audit_has_one_primary_mechanism_per_failure() -> None:
    result = audit()
    assert result["total_cases"] == 160
    assert result["v1_correct"] == 27
    assert result["failures"] == 133
    assert sum(result["primary_counts"].values()) == result["failures"]
    assert set(result["primary_counts"]) == set(MECHANISMS)


def test_m104s_frozen_gate_selects_only_measured_mechanism() -> None:
    result = audit()
    assert result["classification"] == "M104S_HIGH_LEVERAGE_MECHANISM_IDENTIFIED"
    assert result["eligible_mechanisms"] == ["MEMORY_RETRIEVAL_SELECTIVITY_FAILURE"]
    assert result["selected_mechanism"] == "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE"
    assert result["v2_shadow"]["score_overwrite"] is False
    assert result["v2_shadow"]["denominator_removal"] is False


def test_m104s_audit_preserves_observational_memory_boundary() -> None:
    result = audit()
    memory = result["memory"]
    assert memory["counterfactual_run"] is False
    assert memory["top_k"] == 3
    assert memory["threshold"] is None
    assert result["no_llm_judge"] is True
