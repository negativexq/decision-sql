from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "benchmark" / "audits" / "m61r2"
M61R = ROOT / "benchmark" / "audits" / "m61r"
EXPECTED_C = "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587"
EXPECTED_B = "dbbd918219870fb19e9efd4ea93555546967598663a0f9ebe7b9616b69b76e6d"


def _json(name: str) -> Any:
    return json.loads((AUDIT / name).read_text(encoding="utf-8"))


def _jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (AUDIT / name).read_text().splitlines() if line.strip()]


def test_m61r2_contract_and_prelive_gates_are_frozen() -> None:
    contracts = _json("m61r2_external_contracts.json")
    assert contracts["production_contract_hash"] == EXPECTED_C
    assert contracts["production_default_unchanged"] is True
    assert contracts["contracts"]["EXTERNAL_B"]["hash"] == EXPECTED_B
    contract_text = " ".join(item["text"] for item in contracts["contracts"].values()).lower()
    for term in ("telecom", "workforce", "procurement", "marketplace", "healthcare", "iq_"):
        assert term not in contract_text

    provenance = _json("m61r2_prelive_provenance.json")
    assert provenance["status"] == "PASS"
    assert provenance["request_count"] == 22
    assert provenance["provider_calls_before_gate"] == 0
    assert all(item["gold_leakage"] == [] for item in provenance["requests"])
    assert all(item["unexpected_differences"] == [] for item in provenance["requests"])

    evaluator = _json("m61r2_evaluator_integrity.json")
    assert evaluator["status"] == "PASS"
    assert evaluator["gold_execution"] == 11
    assert evaluator["gold_reflexivity"] == 11
    assert evaluator["gold_repeat_determinism"] == 11
    assert evaluator["candidate_path_canary"] == 11


def test_m61r2_selection_and_full_corpus_accounting() -> None:
    selection = _json("m61r2_selection_results.json")
    assert selection["calls"] == 22
    assert selection["winner"] == "EXTERNAL_B"
    assert selection["arms"]["CONTROL_EXTERNAL_A"]["dbt_correct"] == 4
    assert selection["arms"]["EXTERNAL_B"]["dbt_correct"] == 5
    assert len(_jsonl("m61r2_selection_requests.jsonl")) == 22
    assert len(_jsonl("m61r2_selection_responses.jsonl")) == 22

    rows = _jsonl("m61r2_case_results.jsonl")
    assert len(rows) == 220
    assert len({(row["question_id"], row["iteration"]) for row in rows}) == 220
    assert all(
        sum(row["question_id"] == question_id for row in rows) == 20
        for question_id in {row["question_id"] for row in rows}
    )
    assert all(row["provider_success"] for row in rows)
    assert sum(row["raw_proposal_dbt_pass"] for row in rows) == 93
    assert sum(row["decision"] == "ANSWER" for row in rows) == 121
    assert sum(row["decision"] == "BLOCKED_AUTHORITY" for row in rows) == 81
    assert sum(row["decision"] == "NEEDS_CLARIFICATION" for row in rows) == 18

    summary = _json("m61r2_summary.json")
    assert summary["provider_calls"] == 242
    assert summary["selection_calls"] == 22
    assert summary["full_run_calls"] == 220
    assert summary["retries"] == 0
    assert summary["failed_case_reruns"] == 0
    assert summary["dbt_comparable_correct"] == 93


def test_m61r2_failure_and_production_diagnostic_conservation() -> None:
    failure = _json("m61r2_failure_decomposition.json")
    assert failure["total_failures"] == 127
    assert sum(failure["counts"].values()) == 127
    assert failure["counts"] == {
        "BUSINESS_SEMANTICS": 4,
        "FALSE_AUTHORITY_BLOCK": 81,
        "JOIN_PATH": 24,
        "MODEL_ABSTENTION": 18,
    }

    policy = _json("m61r2_production_policy_diagnostic.json")
    assert sum(policy.values()) == 220
    assert policy == {
        "raw_correct_production_accepted": 93,
        "raw_correct_production_rejected": 0,
        "raw_wrong_production_accepted": 28,
        "raw_wrong_production_rejected": 99,
    }


def test_m61r2_required_artifacts_and_historical_reference_are_present() -> None:
    required = {
        "m61r2_scope.json",
        "m61r2_external_contracts.json",
        "m61r2_contract_diff.json",
        "m61r2_information_boundary.json",
        "m61r2_evaluator_integrity.json",
        "m61r2_prelive_provenance.json",
        "m61r2_experiment_plan.json",
        "m61r2_selection_requests.jsonl",
        "m61r2_selection_responses.jsonl",
        "m61r2_selection_results.json",
        "m61r2_selected_contract.json",
        "m61r2_requests.jsonl",
        "m61r2_responses.jsonl",
        "m61r2_case_results.jsonl",
        "m61r2_question_accuracy.json",
        "m61r2_decision_distribution.json",
        "m61r2_failure_decomposition.json",
        "m61r2_historical_category_comparison.json",
        "m61r2_production_policy_diagnostic.json",
        "m61r2_output_stability.json",
        "m61r2_cost_latency.json",
        "m61r2_summary.json",
        "m61r2_report.md",
        "m61r2_manifest.json",
    }
    manifest = _json("m61r2_manifest.json")
    assert set(manifest["artifact_names"]) == required
    assert manifest["candidate_c_hash"] == EXPECTED_C
    assert manifest["production_default_unchanged"] is True
    assert manifest["provider_calls_before_gate"] == 0
    assert manifest["total_provider_calls"] == 242
    assert (
        json.loads((M61R / "m61r_manifest.json").read_text())["verdict"]
        == "M61R_DBT_ACME_LOCAL_REPRODUCTION_COMPLETE"
    )
