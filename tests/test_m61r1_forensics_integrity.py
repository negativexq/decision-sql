from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "benchmark" / "audits" / "m61r1"
M61R = ROOT / "benchmark" / "audits" / "m61r"
EXPECTED_CANDIDATE_C = "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587"


def _json(name: str) -> Any:
    return json.loads((AUDIT / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_m61r1_freezes_m61r_and_candidate_contract() -> None:
    scope = _json("m61r1_scope.json")
    assert isinstance(scope, dict)
    assert scope["provider_calls"] == 0
    assert scope["candidate_c_hash"] == EXPECTED_CANDIDATE_C
    assert scope["benchmark_semantics_changed"] is False
    assert scope["adapter_changed"] is False
    assert scope["runtime_changed"] is False

    historical_hashes = scope["historical_m61r_artifact_hashes"]
    assert historical_hashes
    for relative, expected in historical_hashes.items():
        assert _sha256(M61R / relative) == expected


def test_m61r1_accounts_for_every_observation_and_failure_once() -> None:
    rows = [
        json.loads(line)
        for line in (M61R / "m61r_case_results.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 220
    assert len({(row["question_id"], row["iteration"]) for row in rows}) == 220
    assert {row["decision"] for row in rows} == {
        "ANSWER",
        "BLOCKED_AUTHORITY",
        "NEEDS_CLARIFICATION",
    }

    authority = _json("m61r1_authority_decision_validity.json")
    clarification = _json("m61r1_clarification_forensics.json")
    answer_failures = _json("m61r1_answer_failure_forensics.json")
    assert len(authority["observations"]) == 118
    assert len(clarification["observations"]) == 13
    assert len(answer_failures["observations"]) == 7
    assert (
        len(authority["observations"])
        + len(clarification["observations"])
        + len(answer_failures["observations"])
        == 138
    )
    assert (
        len({(item["question_id"], item["iteration"]) for item in authority["observations"]}) == 118
    )
    assert (
        len({(item["question_id"], item["iteration"]) for item in clarification["observations"]})
        == 13
    )
    assert (
        len({(item["question_id"], item["iteration"]) for item in answer_failures["observations"]})
        == 7
    )


def test_m61r1_root_cause_conservation_and_graph_audit() -> None:
    summary = _json("m61r1_summary.json")
    accounting = _json("m61r1_root_cause_accounting.json")
    graph = _json("m61r1_graph_inventory.json")
    relationship_loss = _json("m61r1_adapter_relationship_loss_audit.json")
    assert summary["observations"] == 220
    assert summary["failures"] == 138
    assert sum(summary["decisions"].values()) == 220
    assert accounting["conservation_check"] is True
    assert sum(accounting["root_cause_counts"].values()) == 138
    assert accounting["partition"] == {
        "blocked_authority": 118,
        "needs_clarification": 13,
        "incorrect_answer": 7,
    }
    assert graph["ddl_declared_graph"]["scalar_relationship_count"] == 28
    assert graph["provider_visible_authority_graph"]["relationship_count"] == 18
    assert relationship_loss["independent_parser_matches"] is True
    assert relationship_loss["resolvable_relationships_missing_from_provider"] == []


def test_m61r1_required_forensic_outputs_are_complete() -> None:
    required = {
        "m61r1_scope.json",
        "m61r1_graph_inventory.json",
        "m61r1_authority_graph_connectivity.json",
        "m61r1_gold_dependency_graph.json",
        "m61r1_gold_path_authority_matrix.json",
        "m61r1_business_semantic_support.json",
        "m61r1_authority_decision_validity.json",
        "m61r1_clarification_forensics.json",
        "m61r1_answer_failure_forensics.json",
        "m61r1_question_failure_profile.json",
        "m61r1_unresolved_target_impact.json",
        "m61r1_schema_data_version_audit.json",
        "m61r1_adapter_relationship_loss_audit.json",
        "m61r1_semantic_layer_information_gap.json",
        "m61r1_pydough_structural_comparison.json",
        "m61r1_root_cause_accounting.json",
        "m61r1_question_applicability.json",
        "m61r1_comparability_verdict.json",
        "m61r1_next_step_recommendation.json",
        "m61r1_summary.json",
        "m61r1_report.md",
        "m61r1_manifest.json",
    }
    manifest = _json("m61r1_manifest.json")
    assert set(manifest["artifact_names"]) == required
    assert manifest["provider_calls"] == 0
    assert manifest["all_220_observations_accounted"] is True
    assert manifest["all_138_failures_accounted"] is True


def test_m61r1_gold_and_authority_extractions_are_deterministic() -> None:
    gold = _json("m61r1_gold_dependency_graph.json")
    matrix = _json("m61r1_gold_path_authority_matrix.json")
    profiles = _json("m61r1_question_failure_profile.json")
    assert len(gold) == len(matrix) == len(profiles) == 11
    assert sum(item["gold_join_count"] for item in gold) == 23
    assert sum(len(item["gold_edges"]) for item in matrix) == 23
    assert sum(item["answer_count"] for item in profiles) == 89
    assert sum(item["blocked_authority_count"] for item in profiles) == 118
    assert sum(item["clarification_count"] for item in profiles) == 13
    assert sum(item["wrong_answer_count"] for item in profiles) == 7
