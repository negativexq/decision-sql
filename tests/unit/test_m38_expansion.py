"""Offline consistency gates for the M38 development benchmark freeze."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark.m38_authoring import new_cases
from benchmark.model_contract import frozen_benchmark_content_hash

ROOT = Path(__file__).resolve().parents[2]


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_m38_has_frozen_size_distribution_and_split() -> None:
    manifest = _json("benchmark/manifests/m38_benchmark_manifest.json")
    split = _json("benchmark/splits/m38_dev.json")
    assert manifest["benchmark_version"] == "0.2.0-dev"
    assert manifest["database_count"] == 6
    assert manifest["case_count"] == 90
    assert manifest["task_distribution"] == {
        "ANSWERABLE": 60,
        "AUTHORITY_BLOCKED": 15,
        "AMBIGUOUS": 9,
        "POLICY_BLOCKED": 6,
    }
    assert len(split["case_ids"]) == 90
    assert split["database_level_isolation"] is True
    assert split["confirmation_databases"] == ["warehouse_logistics", "risk_operations"]


def test_m38_offline_quality_artifacts_are_clean() -> None:
    audit = _json("benchmark/audits/m38_case_audit.json")
    leakage = _json("benchmark/audits/m38_leakage_audit.json")
    manifest = _json("benchmark/manifests/m38_benchmark_manifest.json")
    assert audit["provider_calls"] == 0
    assert audit["status_counts"] == {"CLEAN": 90}
    assert audit["passed"] is True
    assert leakage["leakage_count"] == 0
    assert leakage["passed"] is True
    assert manifest["provider_calls"] == 0
    assert manifest["model_baseline"] == "NOT_RUN"
    assert manifest["benchmark_content_hash"] == frozen_benchmark_content_hash()


def test_m38_new_cases_have_required_answerable_contracts_and_mutation_gate() -> None:
    rows = new_cases()
    answerable = [truth for case, truth in rows if case["task_type"] == "ANSWERABLE"]
    assert len(rows) == 60
    assert len(answerable) == 40
    assert all(len(truth["counterfactual_fixtures"]) >= 2 for truth in answerable)
    assert all(len(truth["semantic_mutants"]) >= 3 for truth in answerable)
    assert all(
        truth["semantic_target"]["projection_contract"]["source"] == "QUESTION_EXPLICIT"
        for truth in answerable
    )
    mutation = _json("benchmark/reports/m38_expansion_summary.json")
    assert mutation["mutants"] == 188
    assert mutation["killed"] == 188
    assert mutation["invalid"] == 0
    assert mutation["survived"] == 0


def test_m38_ambiguity_and_cross_domain_audits_are_clean() -> None:
    audit = _json("benchmark/audits/m38_cross_domain_duplication.json")
    assert audit["passed"] is True
    assert audit["findings"] == [{"duplicate_count": 0, "status": "CLEAN"}]
    ambiguity_cases = [case for case, _truth in new_cases() if case["task_type"] == "AMBIGUOUS"]
    assert len(ambiguity_cases) == 6
