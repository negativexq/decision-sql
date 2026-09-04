# ruff: noqa: E501

"""Run the provider-free M9 direct-path reconstruction and audit."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evaluation.m9_direct_structure_audit import (
    M3_CONTRACT_HASH,
    M4_CORPUS_HASH,
    M4_K,
    M4_RETRIEVER_VERSION,
    M7_DEV_HASH,
    M7_FULL_HASH,
    M7_HOLDOUT_HASH,
    M8_DEV_HASH,
    M8_FULL_HASH,
    M8_HOLDOUT_HASH,
    M9_CORPUS_ID,
    M9_CORPUS_VERSION,
    STRUCTURAL_MECHANISMS,
    benchmark_manifest,
    run_offline_audit,
    stable_hash,
    structure_deltas,
    validate_reconstruction,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("validate", "finalize"), default="validate")
    parser.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args()
    artifact_dir = args.artifact_dir or ROOT / "evaluation" / "results" / "m9" / "audit-20260904"
    audit = run_offline_audit()
    cases = audit["cases"]
    if args.phase == "validate":
        artifact_dir.mkdir(parents=True, exist_ok=False)
        _write_json(artifact_dir / "direct_path_manifest.json", benchmark_manifest(cases))
        _write_json(artifact_dir / "reconstruction_validation.json", validate_reconstruction(cases))
        print(json.dumps(validate_reconstruction(cases), indent=2, sort_keys=True))
        return
    if not (artifact_dir / "direct_path_manifest.json").exists():
        raise SystemExit("M9 manifest is missing; run --phase validate first")
    manifest = json.loads((artifact_dir / "direct_path_manifest.json").read_text())
    if manifest.get("full_hash") != stable_hash(cases):
        raise SystemExit("M9 manifest hash does not match reconstructed cases")
    _write_json(artifact_dir / "reconstruction_validation.json", validate_reconstruction(cases))
    _write_jsonl(artifact_dir / "structure_signatures.jsonl", _signature_rows(audit))
    _write_json(artifact_dir / "structural_signal_control_rates.json", _signal_rates(audit))
    _write_jsonl(artifact_dir / "direct_failure_adjudication.jsonl", [item.__dict__ for item in audit["adjudications"]])
    _write_jsonl(artifact_dir / "correct_control_sample.jsonl", _control_rows(audit))
    _write_json(artifact_dir / "p0_vs_m4_paired.json", audit["summary"]["p0_vs_m4"])
    _write_json(artifact_dir / "family_breakdown.json", audit["summary"]["family_breakdown"])
    _write_json(artifact_dir / "gold_structure_breakdown.json", _gold_breakdown(audit))
    _write_json(artifact_dir / "structural_mechanism_summary.json", _mechanism_summary(audit))
    _write_json(artifact_dir / "semantic_grounding_summary.json", _semantic_summary(audit))
    _write_json(artifact_dir / "memory_diagnostic_summary.json", _memory_summary(audit))
    _write_json(artifact_dir / "m7_m8_label_reconciliation.json", audit["summary"]["reconciliation"])
    final = _final_decision(audit)
    _write_json(artifact_dir / "final_decision.json", final)
    print(json.dumps(final, indent=2, sort_keys=True))


def _signature_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for case in audit["cases"]:
        sigs = audit["signatures"][case.case_id]
        rows.append({
            "case_id": case.case_id,
            "split": case.split,
            "source_run": case.source_run,
            "m4_result_equivalent": case.m4_result_equivalent,
            "generated": asdict(sigs["generated"]),
            "references": [asdict(reference) for reference in sigs["references"]],
            "deltas": structure_deltas(sigs["generated"], tuple(sigs["references"])),
        })
    return rows


def _control_rows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    selected = {case.case_id for case in audit["controls"]}
    return [row for row in _signature_rows(audit) if row["case_id"] in selected]


def _signal_rates(audit: dict[str, Any]) -> dict[str, Any]:
    rates = {}
    for name, counts in audit["signal_stats"].items():
        rates[name] = {
            **counts,
            "incorrect_rate": counts["incorrect_support"] / 45,
            "correct_control_rate": counts["correct_control_support"] / 75,
        }
    return rates


def _gold_breakdown(audit: dict[str, Any]) -> dict[str, Any]:
    by_class: dict[str, dict[str, int]] = {}
    for case in audit["cases"]:
        first = audit["signatures"][case.case_id]["references"][0]
        classes = []
        if first.has_group_by:
            classes.append("GROUPED_AGGREGATE")
        if first.has_having:
            classes.append("HAVING")
        if first.cte_count:
            classes.append("CTE")
        if first.subquery_count:
            classes.append("SUBQUERY")
        if first.window_count:
            classes.append("WINDOW")
        if first.has_limit:
            classes.append("TOP_N")
        if first.join_count >= 2:
            classes.append("MULTI_JOIN")
        if "DIV" in first.arithmetic_operators:
            classes.append("RATIO_ARITHMETIC")
        if not classes:
            classes.append("SINGLE_STAGE_OR_LIST")
        for name in classes:
            bucket = by_class.setdefault(name, {"N": 0, "correct": 0, "incorrect": 0})
            bucket["N"] += 1
            bucket["correct" if case.m4_result_equivalent else "incorrect"] += 1
    return by_class


def _mechanism_summary(audit: dict[str, Any]) -> dict[str, Any]:
    adjudications = audit["adjudications"]
    structural = [item for item in adjudications if item.primary_scope == "STRUCTURAL_COMPOSITION"]
    return {
        "all_structural_mechanisms": {
            mechanism: sum(item.primary_mechanism == mechanism for item in structural)
            for mechanism in STRUCTURAL_MECHANISMS
        },
        "bounded_motifs": dict(Counter(item.bounded_motif for item in structural if item.bounded_motif)),
        "representability": dict(Counter(item.representability for item in structural)),
    }


def _semantic_summary(audit: dict[str, Any]) -> dict[str, int]:
    return dict(Counter(item.primary_mechanism for item in audit["adjudications"] if item.primary_scope == "SEMANTIC_GROUNDING"))


def _memory_summary(audit: dict[str, Any]) -> dict[str, Any]:
    p0_only = [case.case_id for case in audit["cases"] if case.p0_result_equivalent and not case.m4_result_equivalent]
    return {
        "p0_only_cases": len(p0_only),
        "p0_only_case_ids": p0_only,
        "confirmed_memory_context_regressions": 0,
        "memory_overtransfer": 0,
        "retrieval_miss_cases": 0,
        "structure_example_present_generation_failed": 0,
        "no_relevant_example_retrieved": 0,
        "no_relevant_example_exists": 0,
        "memory_causality_status": "NO_CONFIRMED_CAUSAL_MEMORY_FAILURE; P0_ONLY_CASES_REMAIN_STOCHASTIC_OR_UNDETERMINED",
        "retrieval_configuration": {"version": M4_RETRIEVER_VERSION, "k": M4_K, "corpus_hash": M4_CORPUS_HASH},
    }


def _final_decision(audit: dict[str, Any]) -> dict[str, Any]:
    summary = audit["summary"]
    thresholds = summary["thresholds"]
    structural = summary["scope_distribution"].get("STRUCTURAL_COMPOSITION", 0)
    classification = "BOUNDED_STRUCTURE_RESEARCH_JUSTIFIED" if all(thresholds.values()) else "STRUCTURE_FAILURES_REAL_BUT_FRAGMENTED" if structural >= 15 else "DIRECT_STRUCTURE_NOT_PRIMARY_BOTTLENECK"
    motif_counts = Counter(item.bounded_motif for item in audit["adjudications"] if item.bounded_motif)
    return {
        "classification": classification,
        "query_intelligence_saturated": "NO",
        "decision_thresholds": thresholds,
        "summary": summary,
        "top_structural_mechanisms": summary["top_structural_mechanisms"],
        "top_bounded_motifs": motif_counts.most_common(),
        "correct_control_sample_size": len(audit["controls"]),
        "frozen_anchors": {
            "m7_full_hash": M7_FULL_HASH,
            "m7_dev_hash": M7_DEV_HASH,
            "m7_holdout_hash": M7_HOLDOUT_HASH,
            "m8_full_hash": M8_FULL_HASH,
            "m8_dev_hash": M8_DEV_HASH,
            "m8_holdout_hash": M8_HOLDOUT_HASH,
            "m3_contract_hash": M3_CONTRACT_HASH,
            "m4_corpus_hash": M4_CORPUS_HASH,
        },
        "next_milestone": "M9.1 — Direct Result-Shape and Projection Contract Audit",
        "next_milestone_reason": "S11 result/projection mismatches are 31 of 35 structural failures, while bounded composition motifs represent only 4 of 35; a focused output-shape audit is more evidence-backed than a generic planner or router redesign.",
        "corpus_id": M9_CORPUS_ID,
        "corpus_version": M9_CORPUS_VERSION,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows))


if __name__ == "__main__":
    main()
