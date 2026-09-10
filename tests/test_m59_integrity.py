from __future__ import annotations

import json
from pathlib import Path

from benchmark import m51b_runner, m59_runner
from benchmark.m56_runner import load_rows
from benchmark.stable_contract import STABLE_CONTRACT_HASH


def test_m59_control_is_exact_candidate_c() -> None:
    assert m59_runner.sha256_text(m59_runner.candidate_prompts()["CONTROL_C"]) == STABLE_CONTRACT_HASH


def test_m59_prepare_artifacts_have_zero_call_gate_and_structural_diffs() -> None:
    prelive = json.loads((Path(m59_runner.AUDIT) / "m59_prelive_provenance.json").read_text())
    structural = json.loads((Path(m59_runner.AUDIT) / "m59_structural_request_diff.json").read_text())
    assert prelive["equivalence"] == "PASS"
    assert prelive["provider_calls_before_gate"] == 0
    assert prelive["case_count"] == 90
    assert all(row["equal"] for row in prelive["cases"])
    assert structural["status"] == "PASS"
    assert all(row["contract_only_delta"] for row in structural["differences"])


def test_m59_default_production_requests_remain_candidate_c() -> None:
    ids, rows = load_rows()
    assert {row["prompt_sha256"] for row in m51b_runner._requests(ids, rows)} == {STABLE_CONTRACT_HASH}


def test_m59_selection_has_three_arms_and_no_case_specific_prompt_text() -> None:
    prompts = m59_runner.candidate_prompts()
    assert set(prompts) == {"CONTROL_C", "CANDIDATE_D", "CANDIDATE_E"}
    for prompt in prompts.values():
        assert "telecom_10" not in prompt
        assert "workforce_03" not in prompt
        assert "procurement_05" not in prompt
