from pathlib import Path

from benchmark.m49_forensics import (
    SYSTEMATICITY_THRESHOLD,
    _base_cf_sets,
    _load,
    reconstruct_population,
)


def test_m49_reconstructs_exact_frozen_failure_population() -> None:
    population = reconstruct_population()

    assert population["count"] == 12
    assert population["duplicate_case_ids"] == 0
    assert {item["top_level_family"] for item in population["records"]} == {
        "DECISIONING_ABSTENTION",
        "GOVERNANCE_UNDER_ABSTENTION",
        "SQL_SEMANTIC_RESULT",
    }
    assert (
        sum(item["top_level_family"] == "DECISIONING_ABSTENTION" for item in population["records"])
        == 7
    )
    assert (
        sum(
            item["top_level_family"] == "GOVERNANCE_UNDER_ABSTENTION"
            for item in population["records"]
        )
        == 3
    )
    assert (
        sum(item["top_level_family"] == "SQL_SEMANTIC_RESULT" for item in population["records"])
        == 2
    )


def test_m49_verifies_base_and_counterfactual_case_sets() -> None:
    sets = _base_cf_sets()

    assert sets["base_correct_count"] == 51
    assert sets["full_counterfactual_correct_count"] == 51
    assert sets["set_equal"] is True
    assert sets["base_only_false_positives"] == []
    assert sets["counterfactual_only_transitions"] == []


def test_m49_protocol_has_frozen_systematicity_threshold() -> None:
    protocol = _load(Path("benchmark/audits/m49/m49_forensic_protocol.json"))

    assert protocol["provider_calls"] == 0
    assert protocol["model_calls"] == 0
    assert protocol["systematicity_threshold"] == SYSTEMATICITY_THRESHOLD
