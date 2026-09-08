from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmark.model_contract import context_contains_fact

ROOT = Path(__file__).resolve().parents[2]


def _json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_m40_quality_gate_is_clean_and_provider_free() -> None:
    report = _json("benchmark/reports/m40_integrity_repair_summary.json")
    manifest = _json("benchmark/manifests/m40_repaired_benchmark_manifest.json")
    assert report["benchmark_version"] == "0.2.1-dev"
    assert report["passed"] is True
    assert manifest["provider_calls"] == 0
    assert report["context_coverage"]["complete"] == 60
    assert report["references"]["fixture_comparisons"] == 182
    assert report["mutants"]["killed"] == report["mutants"]["authored"] == 188
    assert report["mutants"]["invalid"] == report["mutants"]["survived"] == 0
    assert report["leakage"]["leakage_cases"] == 0


def test_m40_fact_parser_is_typed_and_fails_closed() -> None:
    assert context_contains_fact(
        "risk_operations", "attributes:risk_operations:risk_assessments:assessment_id"
    )
    assert context_contains_fact(
        "risk_operations", "relationships:risk_operations:assessment_customer"
    )
    assert context_contains_fact(
        "warehouse_logistics",
        "attributes:warehouse_logistics:delivery_events:payload #>> '{temperature_c}'",
    )
    assert not context_contains_fact(
        "risk_operations", "attributes:risk_operations:risk_assessments:"
    )
    assert not context_contains_fact("risk_operations", "unknown:risk_operations:assessment_id")
    assert not context_contains_fact(
        "risk_operations", "attributes:other_db:risk_assessments:assessment_id"
    )
    assert not context_contains_fact(
        "risk_operations", "relationships:risk_operations:device_customer_trap"
    )


def test_m40_preserves_every_m39_artifact() -> None:
    preservation = _json("benchmark/reports/m40_historical_preservation.json")
    for relative, expected in preservation["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_m40_has_no_unresolved_case_reviews() -> None:
    audit = _json("benchmark/audits/m40/m40_case_integrity_audit.json")
    assert audit["case_count"] == 90
    assert audit["status_counts"] == {"CLEAN": 30, "REPAIRED": 60, "REVIEW_REQUIRED": 0}
    assert audit["passed"] is True
