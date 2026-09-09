from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmark.m46b_contract import EXPECTED_PROMPT_HASH, TRUTH_HASH, TRUTH_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_m46b_truth_and_parent_prompt_are_frozen() -> None:
    version = json.loads((ROOT / "benchmark/version.json").read_text())
    contract = json.loads((ROOT / "benchmark/manifests/m46b_contract.json").read_text())
    assert version["version"] == TRUTH_VERSION
    assert version["content_hash"] == TRUTH_HASH
    assert contract["evaluation_truth_version"] == TRUTH_VERSION
    assert contract["evaluation_truth_hash"] == TRUTH_HASH
    assert contract["prompt_hash"] == EXPECTED_PROMPT_HASH


def test_m46b_has_90_isolated_pairs_and_interleaved_schedule() -> None:
    paired = json.loads((ROOT / "benchmark/manifests/m46b_paired_request_audit.json").read_text())
    schedule = json.loads((ROOT / "benchmark/manifests/m46b_execution_schedule.json").read_text())
    assert paired["case_count"] == 90
    assert len(paired["pairs"]) == 90
    assert paired["all_isolated"] is True
    assert all(pair["only_structured_block_differs"] for pair in paired["pairs"])
    assert len(schedule["schedule"]) == 180
    for index in range(1, 91):
        arms = [item["arm"] for item in schedule["schedule"] if item["case_index"] == index]
        assert arms == (["CONTROL", "TREATMENT"] if index % 2 else ["TREATMENT", "CONTROL"])


def test_m46b_treatment_context_has_no_evaluator_fields() -> None:
    provenance = json.loads(
        (ROOT / "benchmark/audits/m46b/m46b_structured_context_provenance.json").read_text()
    )
    leakage = json.loads((ROOT / "benchmark/audits/m46b/m46b_leakage_audit.json").read_text())
    assert provenance["public_provenance_only"] is True
    assert provenance["facts_excluded_for_insufficient_provenance"] == 0
    assert leakage["count"] == 0
    assert leakage["passed"] is True


def test_m46b_historical_hashes_still_match() -> None:
    preservation = json.loads(
        (ROOT / "benchmark/reports/m46b_historical_preservation.json").read_text()
    )
    assert preservation["provider_calls"] == 0
    assert preservation["model_calls"] == 0
    for relative, expected in preservation["files"].items():
        assert _sha(ROOT / relative) == expected, relative


def test_m46b_configs_are_single_call_and_identical_model_policy() -> None:
    control = json.loads(
        (ROOT / "benchmark/experiments/m46b_control_repaired_context.json").read_text()
    )
    treatment = json.loads(
        (ROOT / "benchmark/experiments/m46b_treatment_structured_grain.json").read_text()
    )
    for config in (control, treatment):
        assert config["model"] == "gpt-5.6-luna"
        assert config["reasoning"] == "none"
        assert config["temperature"] == 0.0
        assert config["timeout_seconds"] == 90
        assert config["calls_per_case"] == 1
        assert config["transport_retries"] == 0
        assert config["semantic_retries"] == 0
        assert config["repair"] is False
        assert config["selector"] is False
        assert config["judge"] is False
        assert config["reflection"] is False
    assert control["context_variant"] == "legacy_repaired_context"
    assert treatment["context_variant"] == "structured_grain_context"
