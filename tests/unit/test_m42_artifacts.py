import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
RESULTS = ROOT / "benchmark/experiments/results/m42"


def _json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_m42_artifact_counts_and_case_identity() -> None:
    ledger = _json("benchmark/experiments/results/m42/m42_request_ledger.json")
    rows = _json("benchmark/experiments/results/m42/m42_case_results.json")
    raw = (RESULTS / "m42_raw_responses.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = (RESULTS / "m42_parsed_submissions.jsonl").read_text(encoding="utf-8").splitlines()
    expected = ledger["case_order"]
    assert len(expected) == len(set(expected)) == 90
    assert [entry["case_id"] for entry in ledger["requests"]] == expected
    assert [row["case_id"] for row in rows] == expected
    assert len(raw) == len(parsed) == len(rows) == 90


def test_m42_request_hashes_match_ledger() -> None:
    ledger = _json("benchmark/experiments/results/m42/m42_request_ledger.json")
    for entry in ledger["requests"]:
        assert (
            hashlib.sha256(entry["request_text"].encode()).hexdigest()
            == entry["full_request_sha256"]
        )


def test_m42_historical_artifacts_remain_byte_identical() -> None:
    preservation = _json("benchmark/reports/m42_historical_preservation.json")
    for relative, expected in preservation["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_m42_summary_recomputes_primary_counts() -> None:
    summary = _json("benchmark/experiments/results/m42/m42_summary.json")
    rows = _json("benchmark/experiments/results/m42/m42_case_results.json")
    assert len(rows) == 90
    assert sum(row["official_correct"] for row in rows) == 70
    answerable = [row for row in rows if row["gold_behavior"] == "ANSWERABLE"]
    assert sum(row["official_correct"] for row in answerable) == 46
    assert summary["scores"]["governed_task_success"]["correct"] == 70
    assert summary["scores"]["answerable_accuracy"]["correct"] == 46
