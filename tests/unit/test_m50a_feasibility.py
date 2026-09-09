from __future__ import annotations

import json

from benchmark import m50a_feasibility as m50a


def test_blind_derivation_has_90_cases_and_replays_identically() -> None:
    first = m50a._blind_matrix(m50a._model_cases())
    second = m50a._blind_matrix(m50a._model_cases())

    assert len(first) == 90
    assert first == second
    assert (
        m50a._hash(first)
        == json.loads((m50a.AUDIT / "m50a_blind_feature_matrix_hash.json").read_text())["hash"]
    )


def test_blind_matrix_has_no_evaluator_owned_fields() -> None:
    matrix = (m50a.AUDIT / "m50a_blind_feature_matrix.json").read_text()
    forbidden_keys = (
        '"truth_behavior"',
        '"reference_implementation_a"',
        '"reference_implementation_b"',
        '"counterfactual_fixtures"',
        '"result_comparison_contract"',
        '"m49_primary_mechanism"',
        '"m50_transition"',
    )

    assert all(key not in matrix for key in forbidden_keys)


def test_phase_a_artifacts_record_explicit_unknown_and_unresolved_states() -> None:
    matrix = json.loads((m50a.AUDIT / "m50a_blind_feature_matrix.json").read_text())
    for row in matrix:
        request_features = row["features"]["request_scoped_features"]
        assert request_features["fact_availability"]["value"] == "UNKNOWN"
        assert request_features["uniqueness_of_interpretation"]["value"] == "UNRESOLVED"
        assert request_features["composite_answerability"]["value"] == "UNRESOLVED"


def test_no_m50a_provider_or_model_calls_are_recorded() -> None:
    integrity = json.loads((m50a.AUDIT / "m50a_final_integrity.json").read_text())
    assert integrity["provider_calls"] == 0
    assert integrity["model_calls"] == 0
    assert integrity["runtime_changes"] == 0
    assert integrity["prompt_changes"] == 0
