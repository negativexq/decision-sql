from __future__ import annotations

import json

from benchmark import m531_runner as runner


def test_m531_post_m53_truth_and_response_hashes_are_frozen() -> None:
    case_ids, rows = runner.load_rows()
    expansion, full = runner.post_truth_hashes(case_ids, rows)

    assert expansion == runner.POST_EXPANSION_TRUTH
    assert full == runner.POST_FULL_TRUTH
    assert runner.sha_path(runner.RESPONSE_PATH) == runner.RESPONSE_HASH


def test_m531_reuse_partition_is_exact() -> None:
    case_ids, _rows = runner.load_rows()
    reusable, invalidated = runner.m53_partition(case_ids)

    assert len(reusable) == 66
    assert len(invalidated) == 24
    assert set(reusable).isdisjoint(invalidated)
    assert set(reusable) | set(invalidated) == set(case_ids)
    assert set(invalidated) == runner.EXPECTED_INVALIDATED


def test_m531_schedule_is_manifest_order_and_excludes_reusable_cases() -> None:
    case_ids, rows = runner.load_rows()
    _reusable, invalidated = runner.m53_partition(case_ids)
    requests = {row["case_id"]: row for row in runner.m51b._requests(case_ids, rows)}

    schedule = [case_id for case_id in case_ids if case_id in set(invalidated)]

    assert len(schedule) == 24
    assert set(schedule) == set(invalidated)
    assert all(case_id in requests for case_id in schedule)
    assert not any(case_id in schedule for case_id in runner.m53_partition(case_ids)[0])


def test_m531_invalidated_ids_match_frozen_m53_ledger() -> None:
    ledger = json.loads((runner.M53_AUDIT / "m53_expansion_defect_ledger.json").read_text())
    changed = {row["case_id"] for row in ledger["cases"] if row["model_visible_changed"]}
    assert changed == runner.EXPECTED_INVALIDATED


def test_m531_provider_budget_is_one_attempt_per_fresh_case() -> None:
    assert len(runner.EXPECTED_INVALIDATED) == 24
    assert runner.TIMEOUT_SECONDS == 90
    assert runner.MODEL == "gpt-5.6-luna"
    assert runner.REASONING == "none"
    assert runner.TEMPERATURE == 0.0
