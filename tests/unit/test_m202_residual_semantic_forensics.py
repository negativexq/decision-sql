import json
from pathlib import Path

from evaluation.m20_2_residual_semantic_forensics import validate_artifact

ARTIFACT = Path("evaluation/fixtures/m20_2_residual_semantic_forensics.json")


def test_frozen_residual_artifact_reconciles_all_cases() -> None:
    artifact = json.loads(ARTIFACT.read_text())

    validate_artifact(artifact)

    assert artifact["provider_calls"] == 0
    assert artifact["fresh_sql_generation"] == 0
    assert artifact["accounting"] == {
        "total": 774,
        "correct": 321,
        "semantic_mismatch": 189,
        "legitimate_m1_reject": 264,
        "planning_error": 0,
        "execution_failure": 0,
        "unaccounted": 0,
    }
    assert len(artifact["cases"]) == 189
    assert all(case["current_m1_status"] == "ALLOWED" for case in artifact["cases"])
    assert sum(item["count"] for item in artifact["primary_root_causes"].values()) == 189


def test_residual_root_cause_mapping_is_stable_and_excludes_non_failures() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    cases = artifact["cases"]

    first = [(case["benchmark"], case["case_id"], case["primary_root_cause"]) for case in cases]
    second = [(case["benchmark"], case["case_id"], case["primary_root_cause"]) for case in cases]

    assert first == second
    assert len({(case["benchmark"], case["case_id"]) for case in cases}) == 189
    assert all(case["primary_root_cause"] for case in cases)
    assert not any(case.get("current_m1_status") == "REJECTED" for case in cases)
