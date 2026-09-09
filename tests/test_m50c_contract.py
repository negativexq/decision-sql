"""Provider-free structural checks for the M50C frozen contract."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "benchmark" / "m50c_runner.py").read_text(encoding="utf-8")
AUDIT = ROOT / "benchmark" / "audits" / "m50c"


def test_m50c_phase_a_has_frozen_zero_call_contract() -> None:
    contract = json.loads((AUDIT / "m50c_contract.json").read_text(encoding="utf-8"))
    schedule = json.loads((AUDIT / "m50c_call_schedule.json").read_text(encoding="utf-8"))
    assert contract["provider_budget"] == 180
    assert contract["retries"] == contract["repair"] == contract["judge"] == 0
    assert schedule["paired_cases"] == 90
    assert schedule["arm_counts"] == {"CONTROL": 90, "TREATMENT": 90}
    assert schedule["first_arm_counts"]["CONTROL"] + schedule["first_arm_counts"]["TREATMENT"] == 90


def test_m50c_treatment_is_representation_only() -> None:
    novelty = json.loads((AUDIT / "m50c_novel_fact_audit.json").read_text(encoding="utf-8"))
    diff = json.loads((AUDIT / "m50c_request_diff_audit.json").read_text(encoding="utf-8"))
    assert novelty["new_evaluator_facts"] == 0
    assert novelty["new_server_owned_facts"] == 0
    assert novelty["all_primitive_values_redundant"] is True
    assert diff["truth_leakage"] == 0
    assert diff["all_base_request_equal"] is True
    assert diff["all_diff_only_block"] is True
    assert "should_answer" not in RUNNER
    assert "is_answerable" not in RUNNER


def test_m50c_uses_frozen_negative_capabilities() -> None:
    renderer = json.loads((AUDIT / "m50c_renderer_contract.json").read_text(encoding="utf-8"))
    assert renderer["behavioral_guidance_added"] is False
    assert renderer["truth_oracle_access"] is False
    assert set(renderer["families"]) == {
        "SCHEMA",
        "AUTHORIZED_RELATIONSHIP",
        "SEMANTIC_DEFINITION",
        "TEMPORAL_DEFINITION",
        "POLICY",
    }
