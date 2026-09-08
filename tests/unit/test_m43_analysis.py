from __future__ import annotations

import json
from pathlib import Path

from benchmark.m43_analysis import JSON_CASES, TARGET_CASES, _load, _numeric_coercion


def test_m43_json_case_inventory_and_targets() -> None:
    inventory = json.loads(Path("benchmark/audits/m43_json_semantics_inventory.json").read_text())
    assert inventory["provider_calls"] == 0
    assert set(inventory["answerable_json_cases"]) == set(JSON_CASES)
    assert set(TARGET_CASES) == {"fleet_06", "warehouse_09"}


def test_numeric_coercion_static_classifier() -> None:
    assert _numeric_coercion("SELECT payload #>> '{x}' FROM t") == "MISSING_NUMERIC_COERCION"
    assert (
        _numeric_coercion("SELECT (payload #>> '{x}')::numeric FROM t")
        == "CORRECT_NUMERIC_COERCION"
    )
    assert (
        _numeric_coercion("SELECT CAST(payload ->> 'x' AS integer) FROM t")
        == "CORRECT_NUMERIC_COERCION"
    )
    assert _numeric_coercion("SELECT payload ->> 'channel' FROM t") == "MISSING_NUMERIC_COERCION"
    assert _numeric_coercion("SELECT 1") == "NO_JSON_EXTRACTION"


def test_m43_artifact_consistency_after_run() -> None:
    root = Path("benchmark/experiments/results/m43")
    rows = _load(root / "m43_case_results.json")
    ledger = _load(root / "m43_request_ledger.json")
    parsed = (root / "m43_parsed_submissions.jsonl").read_text().splitlines()
    raw = (root / "m43_raw_responses.jsonl").read_text().splitlines()
    case_ids = [row["case_id"] for row in rows]
    assert len(rows) == 90
    assert len(set(case_ids)) == 90
    assert len(ledger["requests"]) == 90
    assert len(parsed) == 90
    assert len(raw) == 90
    assert _load(root / "m43_manifest.json")["provider_calls_attempted"] == 90


def test_m43_paired_analysis_records_two_target_fixes() -> None:
    analysis = _load(Path("benchmark/experiments/results/m43/m43_paired_analysis.json"))
    assert analysis["target_cases"] == sorted(TARGET_CASES)
    assert analysis["json_regressions"] == []
