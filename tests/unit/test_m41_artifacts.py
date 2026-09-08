import base64
import hashlib
import json
from pathlib import Path

ROOT = Path("benchmark/experiments/results/m41")


def _json(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_m41_artifacts_have_one_record_per_case() -> None:
    ledger = _json("m41_request_ledger.json")
    results = _json("m41_case_results.json")
    raw = [json.loads(line) for line in (ROOT / "m41_raw_responses.jsonl").read_text().splitlines()]
    parsed = [
        json.loads(line)
        for line in (ROOT / "m41_parsed_submissions.jsonl").read_text().splitlines()
    ]

    assert ledger["provider_calls"] == 0
    assert len(ledger["requests"]) == 90
    assert len(results) == len(raw) == len(parsed) == 90
    assert [row["case_id"] for row in results] == ledger["case_order"]
    assert len({row["case_id"] for row in results}) == 90
    assert all(row["official_category"] for row in results)


def test_m41_raw_response_hashes_match_embedded_wire_bytes() -> None:
    raw = [json.loads(line) for line in (ROOT / "m41_raw_responses.jsonl").read_text().splitlines()]
    assert all(
        hashlib.sha256(base64.b64decode(row["raw_response_bytes_base64"])).hexdigest()
        == row["response_sha256"]
        for row in raw
    )


def test_m41_summary_matches_case_results() -> None:
    summary = _json("m41_summary.json")
    results = _json("m41_case_results.json")
    assert summary["provider_calls_attempted"] == 90
    assert summary["provider_responses_received"] == 90
    assert summary["all_cases_deterministically_classified"] is True
    assert sum(row["official_correct"] for row in results) == 72


def test_m39_historical_artifacts_match_m40_preservation_hashes() -> None:
    report = json.loads(
        Path("benchmark/reports/m40_historical_preservation.json").read_text(encoding="utf-8")
    )
    for relative_path, expected_hash in report["files"].items():
        actual_hash = hashlib.sha256(Path(relative_path).read_bytes()).hexdigest()
        assert actual_hash == expected_hash
