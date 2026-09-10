from __future__ import annotations

import json
from pathlib import Path

from benchmark import m51b_runner, m56r_runner, m58_runner
from benchmark.m56_runner import load_rows
from benchmark.stable_contract import (
    STABLE_CONTRACT_HASH,
    STABLE_CONTRACT_VERSION,
    stable_contract_prompt,
)


def test_candidate_c_is_the_stable_default_contract() -> None:
    assert m51b_runner.DEFAULT_CONTRACT_VERSION == STABLE_CONTRACT_VERSION
    assert m51b_runner.PROMPT_HASH == STABLE_CONTRACT_HASH
    ids, rows = load_rows()
    assert {row["prompt_sha256"] for row in m51b_runner._requests(ids, rows)} == {
        STABLE_CONTRACT_HASH
    }


def test_default_uses_the_same_provider_request_as_explicit_candidate_c() -> None:
    ids, rows = load_rows()
    case_id = ids[0]
    default = m51b_runner._provider_request(1, case_id, rows[case_id][0])
    explicit = m51b_runner._provider_request(
        1, case_id, rows[case_id][0], prompt=stable_contract_prompt()
    )
    assert m56r_runner.provider_payload(default) == m56r_runner.provider_payload(explicit)
    assert m56r_runner.request_fingerprint(default) == m56r_runner.request_fingerprint(explicit)


def test_m58_promotion_fingerprint_equivalence_is_complete() -> None:
    artifact = json.loads(
        (Path(m58_runner.AUDIT) / "m58_production_fingerprint_equivalence.json").read_text()
    )
    assert artifact["status"] == "PASS"
    assert artifact["equal_count"] == artifact["total"] == 90
    assert all(row["equal"] for row in artifact["cases"])


def test_m58_scope_has_no_live_calls_and_stable_partition() -> None:
    scope = json.loads((Path(m58_runner.AUDIT) / "m58_scope.json").read_text())
    summary = json.loads((Path(m58_runner.AUDIT) / "m58_summary.json").read_text())
    assert scope["provider_model_calls_allowed"] == 0
    assert summary["provider_model_calls"] == 0
    assert summary["official_stable_expansion"] == {
        "governed": "83/90",
        "answerable_tsa": "59/62",
    }
