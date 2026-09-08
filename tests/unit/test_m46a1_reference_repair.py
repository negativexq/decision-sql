from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_m46a1_adjudication_is_independent_and_resolved() -> None:
    report = _load("benchmark/audits/m46a1/m46a1_reference_adjudication.json")
    verdicts = {
        (row["case_id"], row["reference_id"]): row["classification"] for row in report["cases"]
    }
    assert verdicts[("subscription_04", "A")] == "REFERENCE_DEFECT"
    assert verdicts[("subscription_04", "B")] == "REFERENCE_CORRECT"
    assert verdicts[("subscription_10", "A")] == "REFERENCE_DEFECT"
    assert verdicts[("subscription_10", "B")] == "REFERENCE_CORRECT"
    assert report["review_required"] == 0
    assert report["post_repair"]["reference_replay_agreement"] is True
    assert report["post_repair"]["reference_grain_warnings"] == 0


def test_m46a1_repaired_reference_and_mutation_gates_are_clean() -> None:
    replay = _load("benchmark/audits/m46a1/m46a1_reference_grain_audit.json")
    assert replay["summary"]["analyzed"] == 120
    assert replay["summary"]["parseable"] == 120
    assert replay["summary"]["validator_parent_measure_fanout"] == 0
    assert replay["summary"]["review_required"] == 0

    coverage = _load("benchmark/audits/m46a1/m46a1_fanout_fixture_coverage.json")
    assert coverage["fanout_sensitive_answerable_cases"] == 3
    assert coverage["cases_with_discriminator"] == 3
    assert coverage["uncovered"] == []

    mutants = _load("benchmark/audits/m46a1/m46a1_mutation_audit.json")
    assert mutants["mutants"] == 190
    assert mutants["valid"] == 190
    assert mutants["killed"] == 190
    assert mutants["surviving"] == 0
    assert mutants["invalid"] == 0


def test_m46a1_preserves_historical_evidence_and_freezes_new_version() -> None:
    summary = _load("benchmark/reports/m46a1_benchmark_repair_summary.json")
    version = _load("benchmark/version.json")
    assert summary["provider_calls"] == 0
    assert summary["model_calls"] == 0
    assert summary["historical_preservation"]["historical_unchanged"] is True
    assert summary["benchmark"]["version"] == "0.2.2-dev"
    assert version["version"] == "0.2.2-dev"
    assert version["content_hash"] == summary["benchmark"]["content_hash"]
    assert summary["benchmark"]["content_hash"] != (
        "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
    )

    preservation = _load("benchmark/reports/m46a1_historical_preservation.json")
    for relative, expected in preservation["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_m46a1_fixture_contracts_are_explicit_and_case_neutral_in_runtime() -> None:
    for case_id in ("subscription_04", "subscription_10"):
        truth = _load(f"benchmark/ground_truth/m38_dev/{case_id}.json")
        fixture = next(
            item
            for item in truth["counterfactual_fixtures"]
            if item["fixture_id"].endswith("multi_refund")
        )
        contract = fixture["adversarial_contract"]
        assert contract["target_failure_family"] == "PARENT_MEASURE_FANOUT"
        assert len(contract["affected_measures"]) == 2
        assert "PUBLIC" in contract["provenance"][0]
    production = (ROOT / "app" / "semantics" / "grain.py").read_text(encoding="utf-8")
    for case_id in ("subscription_04", "subscription_10", "warehouse_08"):
        assert case_id not in production
