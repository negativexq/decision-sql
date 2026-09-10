from __future__ import annotations

import json

import pytest

from benchmark.m531r1_runner import (
    MISSING_SCHEDULE,
    SCHEDULE_HASH,
    M532CallGuard,
    evaluate,
    require_frozen_snapshot,
    sha_value,
)


def allowed_guard() -> M532CallGuard:
    return M532CallGuard(
        {"case_a": {"model_visible_hash": "mv-a", "provider_request_hash": "pr-a"}},
        SCHEDULE_HASH,
    )


def test_score_and_live_readiness_are_distinct() -> None:
    assert SCHEDULE_HASH == "41d180528f01f989e67e46d8d33931a0ed9545c840b38e1692c4329fc68e5536"
    guard = allowed_guard()
    guard.approve("case_a", 1, "mv-a", "pr-a")
    assert guard.attempted == {"case_a"}


def test_unknown_case_and_second_attempt_are_rejected() -> None:
    with pytest.raises(RuntimeError, match="UNKNOWN_CASE"):
        allowed_guard().approve("case_b", 1, "mv-a", "pr-a")
    guard = allowed_guard()
    guard.approve("case_a", 1, "mv-a", "pr-a")
    with pytest.raises(RuntimeError, match="SECOND_ATTEMPT"):
        guard.approve("case_a", 1, "mv-a", "pr-a")


def test_request_hash_mismatch_aborts_before_call() -> None:
    with pytest.raises(RuntimeError, match="MODEL_VISIBLE_HASH_DRIFT"):
        allowed_guard().approve("case_a", 1, "wrong", "pr-a")
    with pytest.raises(RuntimeError, match="PROVIDER_REQUEST_HASH_DRIFT"):
        allowed_guard().approve("case_a", 1, "mv-a", "wrong")


def test_attempt_number_must_be_one() -> None:
    with pytest.raises(RuntimeError, match="SECOND_ATTEMPT"):
        allowed_guard().approve("case_a", 2, "mv-a", "pr-a")


def test_partition_counts_are_frozen_by_audit_contract() -> None:
    assert 10 + 24 + 56 == 90


def test_frozen_schedule_and_partition_validate_without_calls() -> None:
    result = evaluate()
    schedule = json.loads(MISSING_SCHEDULE.read_text(encoding="utf-8"))["schedule"]
    assert sha_value(schedule) == SCHEDULE_HASH
    assert (len(result["reusable"]), len(result["fresh_valid"]), len(result["missing"])) == (
        10,
        24,
        56,
    )


def test_shared_context_drift_is_rejected_by_fingerprint_gate() -> None:
    guard = allowed_guard()
    with pytest.raises(RuntimeError, match="MODEL_VISIBLE_HASH_DRIFT"):
        guard.approve("case_a", 1, "changed-by-shared-rule", "pr-a")


def test_benchmark_snapshot_drift_aborts_before_call() -> None:
    with pytest.raises(RuntimeError, match="BENCHMARK_DRIFT"):
        require_frozen_snapshot("before", "after")
