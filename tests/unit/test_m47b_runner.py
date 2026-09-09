from __future__ import annotations

import json
from pathlib import Path

from benchmark import m47b_runner

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "benchmark" / "experiments" / "results" / "m47b"


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_m47b_frozen_generation_has_one_response_per_case() -> None:
    raw = _lines(RESULT / "raw_responses.jsonl")
    parsed = _lines(RESULT / "parsed_submissions.jsonl")
    assert len(raw) == len(parsed) == 90
    assert len({item["case_id"] for item in raw}) == 90
    assert all(item["schema_validation"] == "PASS" for item in parsed)


def test_m47b_model_contract_is_legacy_m43_generation() -> None:
    manifest = json.loads((ROOT / "benchmark" / "manifests" / "m47b_contract.json").read_text())
    assert manifest["truth_hash"] == m47b_runner.TRUTH_HASH
    assert manifest["prompt_hash"] == m47b_runner.EXPECTED_PROMPT_HASH
    assert manifest["model_context_changed"] is False
    assert manifest["provider_calls"] == 0


def test_m47b_raw_and_normalized_decisions_are_identical() -> None:
    raw = {
        item["case_id"]: item for item in json.loads((RESULT / "raw_case_results.json").read_text())
    }
    normalized = {
        item["case_id"]: item
        for item in json.loads((RESULT / "normalized_case_results.json").read_text())
    }
    assert (
        set(raw)
        == set(normalized)
        == {item["case_id"] for item in _lines(RESULT / "parsed_submissions.jsonl")}
    )
    assert all(
        raw[case_id]["model_decision"] == normalized[case_id]["model_decision"] for case_id in raw
    )


def test_m47b_only_changed_sql_was_a_fanout_candidate() -> None:
    ledger = json.loads((RESULT / "normalization_ledger.json").read_text())
    paired = json.loads((RESULT / "paired_case_analysis.json").read_text())
    sql_by_case = {item["case_id"]: item.get("normalized_sql") or "" for item in paired}
    changed = [item for item in ledger if item["changed"]]
    assert all(item["raw_diagnostic"] == "PARENT_MEASURE_FANOUT" for item in changed)
    assert all(item["normalized_diagnostic"] != "PARENT_MEASURE_FANOUT" for item in changed)
    assert all("SUM(DISTINCT" not in sql_by_case[item["case_id"]].upper() for item in changed)
