import json
from hashlib import sha256
from pathlib import Path

from evaluation.m22_3_existing_result_shape_capability_audit import (
    M20_2,
    M22_2,
    _synthetic_boundaries,
    run_audit,
    validate_artifact,
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_m22_2_target_population_is_exactly_six_extra_and_two_missing() -> None:
    artifact = run_audit()

    assert artifact["target_population"]["selected_count"] == 8
    assert artifact["target_population"]["selected_subtypes"] == {
        "EXTRA_PROJECTED_COLUMN": 6,
        "MISSING_PROJECTED_COLUMN": 2,
    }
    assert len(artifact["target_population"]["case_ids"]) == 8
    assert len(set(artifact["target_population"]["case_ids"])) == 8


def test_existing_validator_detects_wrong_and_accepts_corrected_projection() -> None:
    artifact = run_audit()

    assert artifact["validator_detection"]["correctly_detected"] == 8
    assert artifact["validator_detection"]["wrong_status_counts"] == {
        "PROJECTION_EXTRA": 6,
        "PROJECTION_MISSING": 2,
    }
    assert artifact["corrected_sql_control"]["state_counts"] == {
        "frozen_wrong_sql": {"accepted": 0, "rejected": 8},
        "corrected_projection_sql": {"accepted": 8, "rejected": 0},
    }


def test_bounded_correct_controls_have_no_validator_false_rejects() -> None:
    artifact = run_audit()

    assert artifact["false_positive_control"]["cases_tested"] == 12
    assert artifact["false_positive_control"]["accepted"] == 12
    assert artifact["false_positive_control"]["false_rejects"] == 0


def test_alias_order_and_derived_boundaries_are_deterministic() -> None:
    first = _synthetic_boundaries()
    second = _synthetic_boundaries()

    assert first == second
    assert first["alias"]["validation"]["accepted"] is True
    assert first["output_order"]["validation"]["accepted"] is False
    assert first["output_order"]["validation"]["projection_status"] == "PROJECTION_WRONG"
    assert all(value["accepted"] is True for value in first["derived_aggregate_distinct"].values())


def test_audit_is_provider_free_and_historical_artifacts_are_unchanged() -> None:
    m20_2_before = _digest(M20_2)
    m22_2_before = _digest(M22_2)

    artifact = run_audit()
    validate_artifact(artifact)

    assert artifact["provider_calls"] == 0
    assert artifact["fresh_generation"] == 0
    assert artifact["production_changes"] == 0
    assert _digest(M20_2) == m20_2_before
    assert _digest(M22_2) == m22_2_before
    assert json.loads(M22_2.read_text())["classification"] == (
        "M22_2_PROJECTION_SEMANTICS_FORENSICS_COMPLETED"
    )
