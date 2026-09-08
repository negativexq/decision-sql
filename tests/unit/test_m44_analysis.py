from __future__ import annotations

import json
from pathlib import Path

from benchmark.m44_analysis import FANOUT_CASES, JSON_TARGETS


def test_m44_run_artifacts_are_complete() -> None:
    root = Path("benchmark/experiments/results/m44")
    rows = json.loads((root / "m44_case_results.json").read_text())
    ledger = json.loads((root / "m44_request_ledger.json").read_text())
    assert len(rows) == 90
    assert len({row["case_id"] for row in rows}) == 90
    assert len(ledger["requests"]) == 90
    assert len((root / "m44_raw_responses.jsonl").read_text().splitlines()) == 90
    assert json.loads((root / "m44_manifest.json").read_text())["provider_calls_attempted"] == 90


def test_m44_fanout_and_json_analysis_are_frozen() -> None:
    paired = json.loads(
        Path("benchmark/experiments/results/m44/m44_paired_analysis.json").read_text()
    )
    assert set(row["case_id"] for row in paired["fanout_cases"]) == set(FANOUT_CASES)
    assert paired["warehouse_08"]["fixed"] is False
    assert paired["json_target_fixes_preserved"] == len(JSON_TARGETS)
