from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "benchmark" / "audits" / "m61r"


def _json(name: str) -> dict:
    return json.loads((AUDIT / name).read_text(encoding="utf-8"))


def test_m61r_completed_corpus_has_exact_scope_and_no_retries() -> None:
    manifest = _json("m61r_manifest.json")
    rows = [
        json.loads(line) for line in (AUDIT / "m61r_case_results.jsonl").read_text().splitlines()
    ]
    assert manifest["verdict"] == "M61R_DBT_ACME_LOCAL_REPRODUCTION_COMPLETE"
    assert len(rows) == 220
    assert len({row["question_id"] for row in rows}) == 11
    assert all(row["iteration"] in range(1, 21) for row in rows)
    assert manifest["provider_calls_before_gate"] == 0
    assert manifest["retries"] == 0
    assert manifest["failed_case_reruns"] == 0


def test_m61r_pre_live_gates_and_contract_are_frozen() -> None:
    evaluator = _json("m61r_evaluator_integrity.json")
    provenance = _json("m61r_prelive_provenance.json")
    assert evaluator["status"] == "PASS"
    assert evaluator["gold_execution"] == 11
    assert evaluator["gold_reflexivity"] == 11
    assert evaluator["gold_repeat_determinism"] == 11
    assert evaluator["candidate_path_canary"] == 11
    assert provenance["status"] == "PASS"
    assert provenance["provider_calls_before_gate"] == 0
    assert provenance["candidate_c_hash"] == (
        "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587"
    )
    assert all(not row["gold_leakage"] for row in provenance["requests"])


def test_m61r_source_rows_and_database_rebuild_are_deterministic() -> None:
    load = _json("m61r_data_load_manifest.json")
    database = _json("m61r_database_fingerprint.json")
    assert load["all_source_rows_loaded"] is True
    assert database["equal"] is True
    assert database["build_1"] == database["build_2"]


def test_m61r_internal_and_external_metrics_remain_separate() -> None:
    comparison = _json("m61r_internal_external_comparison.json")
    assert comparison["internal_stable"] == {"governed": "83/90", "answerable_tsa": "59/62"}
    assert comparison["external_local_first_party"]["end_to_end"] == "82/220"


def test_m61r_runtime_value_analysis_preserves_execution_boundary_counts() -> None:
    analysis = _json("m61r_runtime_value_analysis.json")
    assert analysis["non_answer_observations"] == 131
    assert analysis["raw_correct_runtime_accepted"] == 82
    assert analysis["raw_wrong_runtime_accepted"] == 7
    assert analysis["raw_correct_runtime_rejected"] == 0
    assert analysis["raw_wrong_runtime_rejected"] == 0
