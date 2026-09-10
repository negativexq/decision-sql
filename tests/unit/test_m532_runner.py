from __future__ import annotations

import pytest

from benchmark.m531r1_runner import M532CallGuard, require_frozen_snapshot
from benchmark.m532_runner import SCHEDULE_HASH, schedule, sha_value


def test_frozen_m532_schedule_has_56_unique_entries() -> None:
    rows, by_id = schedule()
    assert len(rows) == 56
    assert len(by_id) == 56
    assert sha_value(rows) == SCHEDULE_HASH


def test_m532_guard_rejects_unscheduled_and_second_attempts() -> None:
    guard = M532CallGuard(
        {"scheduled": {"model_visible_hash": "visible", "provider_request_hash": "provider"}},
        SCHEDULE_HASH,
    )
    with pytest.raises(RuntimeError, match="UNKNOWN_CASE"):
        guard.approve("not-scheduled", 1, "visible", "provider")
    guard.approve("scheduled", 1, "visible", "provider")
    with pytest.raises(RuntimeError, match="SECOND_ATTEMPT"):
        guard.approve("scheduled", 1, "visible", "provider")


def test_m532_guard_rejects_request_and_benchmark_drift_before_call() -> None:
    guard = M532CallGuard(
        {"scheduled": {"model_visible_hash": "visible", "provider_request_hash": "provider"}},
        SCHEDULE_HASH,
    )
    with pytest.raises(RuntimeError, match="MODEL_VISIBLE_HASH_DRIFT"):
        guard.approve("scheduled", 1, "changed", "provider")
    with pytest.raises(RuntimeError, match="BENCHMARK_DRIFT"):
        require_frozen_snapshot("frozen", "changed")


def test_m532_guard_rejects_second_attempt_number() -> None:
    guard = M532CallGuard(
        {"scheduled": {"model_visible_hash": "visible", "provider_request_hash": "provider"}},
        SCHEDULE_HASH,
    )
    with pytest.raises(RuntimeError, match="SECOND_ATTEMPT"):
        guard.approve("scheduled", 2, "visible", "provider")
