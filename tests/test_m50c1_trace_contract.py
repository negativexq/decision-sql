"""Provider-free contract tests for the M50C.1 two-layer trace."""

import json
from pathlib import Path

from benchmark.m50c1_trace import _make_trace, _stage


def test_trace_hash_excludes_ephemeral_plan_identity() -> None:
    first = _make_trace(
        run_id="test",
        request_hash="a" * 64,
        response_identity={"case_id": "case", "plan_id": "one"},
        response_hash="b" * 64,
        stages=[_stage("INPUT_CONTEXT", "PASS")],
        terminal="INPUT_CONTEXT",
        disposition="PASS",
        first_failure=None,
    )
    second = _make_trace(
        run_id="test",
        request_hash="a" * 64,
        response_identity={"case_id": "case", "plan_id": "two"},
        response_hash="b" * 64,
        stages=[_stage("INPUT_CONTEXT", "PASS")],
        terminal="INPUT_CONTEXT",
        disposition="PASS",
        first_failure=None,
    )
    assert first["trace_hash"] == second["trace_hash"]


def test_phase_a_contract_separates_runtime_and_evaluator_layers() -> None:
    contract = json.loads(Path("benchmark/audits/m50c1/m50c1_trace_contract.json").read_text())
    assert contract["layer_a_reference_blind"] is True
    assert contract["layer_b_evaluator_only"] is True
    assert contract["provider_calls"] == 0


def test_non_answer_stages_are_explicitly_skippable() -> None:
    skipped = _stage("SQL_PARSE", "SKIPPED", skipped_reason="NON_ANSWER_SUBMISSION", reached=False)
    assert skipped.status == "SKIPPED"
    assert skipped.reached is False
