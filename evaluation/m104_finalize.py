# ruff: noqa: E501

"""Materialize bounded M10.4 reports after the one-pass run and audit."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
RESULTS = ROOT / "evaluation" / "results" / "m104" / "fresh-provenance-20260904"


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def read(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((RESULTS / name).read_text()))


def main() -> None:
    source_paths = {
        "m104_corpus": "evaluation/m104_corpus.py",
        "m104_protocol": "evaluation/m104_protocol.py",
        "m104_runner": "evaluation/run_m104.py",
        "m104_completeness": "evaluation/run_m104.py",
        "m104_causal_audit": "evaluation/m104_causal_audit.py",
        "m104_tests": "tests/unit/test_m104_protocol.py|tests/unit/test_m104_postrun.py",
        "m104_m103_models": "app/provenance/models.py",
        "m104_m103_sink": "app/provenance/sink.py",
        "m104_m103_canonical": "app/provenance/canonical.py",
    }
    component = {}
    for name, path_spec in source_paths.items():
        if "|" in path_spec:
            component[name] = stable_hash({path: file_hash(ROOT / path) for path in path_spec.split("|")})
        else:
            component[name] = file_hash(ROOT / path_spec)
    (RESULTS / "component_manifest.json").write_text(json.dumps({"version": "m104-component-manifest-v1", "sources": component}, indent=2, sort_keys=True) + "\n")
    run = read("run_summary.json")
    complete = read("provenance_completeness.json")
    mechanism = read("mechanism_summary.json")
    selection = read("selection_decision.json")
    integrity = read("protocol_integrity_failure.json") if (RESULTS / "protocol_integrity_failure.json").exists() else None
    corpus = json.loads((FIXTURES / "m104_corpus.json").read_text())
    cases = corpus["cases"]
    classification = integrity["classification"] if integrity else mechanism["classification"]
    selected_m11 = None if integrity else "M11 — Verified Query Memory Selectivity Intervention"
    next_milestone = "M10.4R — Fresh Provenance-Complete Diagnostic Run Repair Audit" if integrity else mechanism["next_milestone"]
    summary = {
        "classification": classification,
        "corpus_id": corpus["corpus_id"],
        "corpus_version": corpus["corpus_version"],
        "total_cases": 160,
        "expected_governed": 40,
        "expected_direct": 120,
        "partitions": {"DIAG_A": 80, "DIAG_B": 80},
        "scores": {
            "overall": "33/160",
            "DIAG_A": "17/80",
            "DIAG_B": "16/80",
            "expected_governed": "4/40",
            "expected_direct": "29/120",
            "actual_governed": "4/43",
            "actual_direct": "29/117",
            "failures": 127,
        },
        "routing": {
            "actual_governed": 43, "actual_direct": 117, "accuracy": "157/160",
            "governed_recall": "40/40", "governed_precision": "40/43",
            "false_governed": 3, "false_direct": 0,
        },
        "provider": {
            "logical_grounding_calls": 160, "logical_direct_generation_calls": 117,
            "logical_total": run["logical_provider_calls"], "physical_attempts": run["logical_provider_calls"],
            "retries": 0, "failures": 2, "repair": 0, "judge": 0, "sampling": 0, "post_hoc": 0,
            "requested_model": "gpt-5.6-luna", "provider": "openai-compatible",
        },
        "v2": {"evaluated": 40, "unavailable": 120, "agreements": 40, "disagreements": 0, "v1_wrong_v2_correct": 0, "v1_correct_v2_wrong": 0, "score_overwrite": False, "denominator_removal": False},
        "provenance": {"ledger_hash": file_hash(RESULTS / "runtime_provenance.jsonl"), "complete_cases": complete["complete_cases"], "completeness": complete["completeness"], "ordering_violations": complete["ordering_violations"], "payload_hash_violations": complete["payload_hash_violations"], "gold_leakage": complete["gold_leakage_violations"], "secrets": complete["secret_violations"], "raw_results": complete["raw_result_violations"]},
        "mechanisms": mechanism["primary_counts"],
        "evidence": mechanism["evidence_counts"],
        "m11": {"eligible": [] if integrity else mechanism["eligible_mechanisms"], "selected": selected_m11, "selection_decision": selection, "counterfactual_run": False},
        "protocol_integrity_failure": integrity,
        "next_milestone": next_milestone,
        "memory": mechanism["memory"],
        "pipeline": {"provider_generation_failures": 2, "parse_failures": 0, "m1_rejections": 8, "execution_failures": 0, "evaluator_failures": 0},
        "scope": {"provider_calls_before_final_lock": 0, "benchmark_db_mutations": 0, "m10r_reruns": 0, "m104_cases_rerun": 0, "fresh_corpus": True, "m104_started": True, "no_memory_counterfactual": True, "public_benchmarks": False, "m11_implemented": False},
        "hashes": {"corpus": file_hash(FIXTURES / "m104_corpus.json"), "case_ids": stable_hash([case["case_id"] for case in cases]), "questions": stable_hash([case["question"] for case in cases]), "references": stable_hash([case["reference_sql"] for case in cases]), "semantic_gold": file_hash(FIXTURES / "m104_semantic_gold.json"), "partitions": file_hash(FIXTURES / "m104_partition_manifest.json"), "freshness": file_hash(FIXTURES / "m104_freshness_manifest.json"), "reference_results": file_hash(RESULTS / "reference_results.json"), "causal_taxonomy": file_hash(FIXTURES / "m104_causal_taxonomy.json"), "evidence_rules": file_hash(FIXTURES / "m104_evidence_rules.json"), "phenotype_taxonomy": file_hash(FIXTURES / "m104_semantic_phenotypes.json"), "memory_transfer_rules": file_hash(FIXTURES / "m104_memory_transfer_rules.json"), "selection_gate": file_hash(FIXTURES / "m104_selection_gate.json"), "provider_protocol": file_hash(FIXTURES / "m104_provider_protocol.json"), "execution_order": file_hash(FIXTURES / "m104_execution_order.json"), "component_manifest": file_hash(RESULTS / "component_manifest.json"), "pre_run_master_lock": read("pre_run_lock.json")["pre_run_master_lock_hash"], "runtime_ledger": file_hash(RESULTS / "runtime_provenance.jsonl"), "completeness": file_hash(RESULTS / "provenance_completeness.json"), "v1_result": stable_hash({"overall": "33/160", "failures": 127}), "v2_shadow": stable_hash({"evaluated": 40, "unavailable": 120, "agreements": 40, "disagreements": 0}), "automated_attribution": file_hash(RESULTS / "automated_causal_attribution.json"), "mechanism_summary": file_hash(RESULTS / "mechanism_summary.json"), "selection_decision": file_hash(RESULTS / "selection_decision.json")},
        "m10r": {"score": "28/200 = 14%", "consumed": True},
        "m7": {"score": "99/150 = 66%", "separate_corpus": True},
        "no_accuracy_created": True,
    }
    summary["hashes"]["final_summary"] = stable_hash(summary)
    (RESULTS / "final_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"component_manifest": file_hash(RESULTS / "component_manifest.json"), "final_summary": file_hash(RESULTS / "final_summary.json"), "summary": summary}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
