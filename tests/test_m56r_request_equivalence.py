from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import m51b_runner, m56r_runner
from benchmark.m56_runner import candidate_prompts


def _case(case_id: str = "synthetic_case") -> dict[str, str]:
    return {
        "database_id": "commerce_ops",
        "question": "Return the requested governed result.",
    }


def test_control_uses_the_canonical_production_request_boundary() -> None:
    request = _case()
    production = m51b_runner._requests(["synthetic_case"], {"synthetic_case": (request, {})})
    control = m51b_runner._provider_request(
        1, "synthetic_case", request, prompt=candidate_prompts()["CANDIDATE_C"]
    )

    assert m56r_runner.provider_payload(production[0]) == m56r_runner.provider_payload(control)
    assert m56r_runner.request_fingerprint(production[0]) == m56r_runner.request_fingerprint(
        control
    )


def test_fingerprint_covers_full_provider_visible_payload() -> None:
    control = m51b_runner._provider_request(
        1, "synthetic_case", _case(), prompt=candidate_prompts()["CANDIDATE_C"]
    )
    payload = m56r_runner.provider_payload(control)
    changed = json.loads(json.dumps(payload))
    changed["response_format"]["json_schema"]["strict"] = False

    assert m56r_runner.structural_diff(payload, changed)
    assert m56r_runner.digest(payload) != m56r_runner.digest(changed)


def test_candidate_delta_is_limited_to_contract_message() -> None:
    case = _case()
    control = m56r_runner.provider_payload(
        m51b_runner._provider_request(
            1, "synthetic_case", case, prompt=candidate_prompts()["CONTROL"]
        )
    )
    candidate = m56r_runner.provider_payload(
        m51b_runner._provider_request(
            1, "synthetic_case", case, prompt=candidate_prompts()["CANDIDATE_A"]
        )
    )
    differences = m56r_runner.structural_diff(control, candidate)

    assert differences
    assert {item["path"] for item in differences} == {"messages[0].content"}
    for key in ("model", "messages", "response_format", "temperature"):
        if key != "messages":
            assert control[key] == candidate[key]
    assert control["messages"][1] == candidate["messages"][1]


def test_guard_rejects_request_drift_before_provider_invocation(tmp_path: Path) -> None:
    request = m51b_runner._provider_request(1, "synthetic_case", _case())
    schedule_row = {
        "arm": "CONTROL",
        "case_id": "synthetic_case",
        "provider_request_fingerprint": "not-the-request-fingerprint",
    }
    guard = m56r_runner.Guard([schedule_row], tmp_path / "responses.jsonl")
    provider_calls = 0

    with pytest.raises(RuntimeError, match="M56R_REQUEST_DRIFT_BEFORE_CALL"):
        guard.admit(schedule_row, request)

    assert provider_calls == 0


def test_guard_rejects_unknown_and_second_attempt(tmp_path: Path) -> None:
    case = _case()
    request = m51b_runner._provider_request(1, "synthetic_case", case)
    row = {
        "arm": "CONTROL",
        "case_id": "synthetic_case",
        "provider_request_fingerprint": m56r_runner.request_fingerprint(request),
    }
    guard = m56r_runner.Guard([row], tmp_path / "responses.jsonl")

    with pytest.raises(RuntimeError, match="M56R_CALL_GUARD"):
        guard.admit({**row, "case_id": "unknown"}, request)

    guard.admit(row, request)
    with pytest.raises(RuntimeError, match="M56R_CALL_GUARD"):
        guard.admit(row, request)


def test_invalid_m56_corpora_are_explicitly_ineligible() -> None:
    exclusion = m56r_runner.load_json(m56r_runner.AUDIT / "m56r_invalid_m56_corpus_exclusion.json")

    assert exclusion["invalid_reason"] == "PRELIVE_REQUEST_BUILDER_DRIFT"
    assert exclusion["canonical_evidence_eligible"] is False
    assert exclusion["response_bytes_mutated"] is False
