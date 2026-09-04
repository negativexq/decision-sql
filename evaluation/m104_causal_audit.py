# ruff: noqa: E501

"""Deterministic post-run M10.4 attribution; never calls a provider or database."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from evaluation.m104_corpus import _signature

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
RESULT_ROOT = ROOT / "evaluation" / "results" / "m104" / "fresh-provenance-20260904"

MECHANISMS = (
    "EVALUATOR_ARTIFACT", "EXECUTION_FAILURE", "TRANSPORT_FAILURE", "M1_REJECTION",
    "ROUTING_OVERREACH", "ROUTING_FALSE_DIRECT", "GOVERNED_GROUNDING_FAILURE",
    "GOVERNED_COMPILER_FAILURE", "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE",
    "MEMORY_OVERTRANSFER_EVIDENCE", "DOWNSTREAM_GENERATION_FAILURE", "UNRESOLVED",
)
FAMILY_PHENOTYPE = {
    "SCHEMA_SOURCE_GROUNDING": "SCHEMA_SOURCE", "JOIN_GRAIN": "JOIN_GRAIN",
    "AGGREGATION_GROUPING": "AGGREGATION_GROUPING", "FORMULA_RATIO": "FORMULA_RATIO",
    "FILTER_COLUMN": "FILTER_COLUMN", "FILTER_OPERATOR_BOOLEAN": "FILTER_BOOLEAN_LOGIC",
    "FILTER_LITERAL": "FILTER_LITERAL", "TEMPORAL": "TEMPORAL", "WINDOW": "WINDOW",
    "PROJECTION_RESULT_SHAPE": "PROJECTION_RESULT_SHAPE", "ORDER_TOPN": "ORDER_TOPN",
    "DISTINCT_SET": "DISTINCT_SET", "GOVERNED_SEMANTIC": "COMPOUND_SEMANTIC",
}


def _load_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _same_motif(left: dict[str, Any], right: dict[str, Any], reference: dict[str, Any]) -> bool:
    keys = ("table_set", "join_graph", "projection_count", "has_aggregate", "has_grouping", "has_filter", "has_window", "has_order", "has_limit", "has_distinct")
    return any(left[key] == right[key] and left[key] != reference[key] for key in keys)


def _compatible(candidate: dict[str, Any], reference: dict[str, Any]) -> bool:
    return candidate == reference


def _event_by_stage(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {event["stage"]: event for event in events}


def audit() -> dict[str, Any]:
    cases = {case["case_id"]: case for case in json.loads((FIXTURES / "m104_corpus.json").read_text())["cases"]}
    rows = {row["case_id"]: row for row in _load_lines(RESULT_ROOT / "case_ledger.jsonl")}
    provenance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _load_lines(RESULT_ROOT / "runtime_provenance.jsonl"):
        provenance[event["case_id"]].append(event)
    from evaluation.m4_benchmark import build_memory_corpus

    memory = {example.example_id: _signature(example.sql) for example in build_memory_corpus()}
    outputs: list[dict[str, Any]] = []
    memory_strength: Counter[str] = Counter()
    compatible_memory_failures = 0
    selected_compatible = selected_incompatible = correct_memory = incorrect_memory = 0
    actual_governed = sum(row.get("actual_path") == "GOVERNED_METRIC" for row in rows.values())
    for case_id, row in sorted(rows.items()):
        case = cases[case_id]
        case_strength = "MT0_NO_TRANSFER_EVIDENCE"
        if row.get("v1_correct"):
            mechanism = None
            grade = None
            reason = "authoritative V1 correct control"
        elif row.get("primary_status") == "TRANSPORT_FAILURE":
            mechanism, grade, reason = "TRANSPORT_FAILURE", "E1_DIRECT_DECISION_EVIDENCE", "provider transport failed after frozen retry behavior"
        elif row.get("primary_status") == "M1_REJECTED":
            mechanism, grade, reason = "M1_REJECTION", "E1_DIRECT_DECISION_EVIDENCE", "M1 rejected the generated candidate"
        elif row.get("actual_path") == "GOVERNED_METRIC" and case["expected_route"] == "DIRECT":
            mechanism, grade, reason = "ROUTING_OVERREACH", "E1_DIRECT_DECISION_EVIDENCE", "direct gold case took governed route"
        elif row.get("actual_path") != "GOVERNED_METRIC" and case["expected_route"] == "GOVERNED":
            mechanism, grade, reason = "ROUTING_FALSE_DIRECT", "E1_DIRECT_DECISION_EVIDENCE", "governed gold case did not take governed route"
        else:
            events = _event_by_stage(provenance.get(case_id, []))
            reference_sig = case["semantic_signature"]
            candidate_sql = row.get("candidate_sql")
            candidate_sig = _signature(candidate_sql) if candidate_sql else None
            if row.get("actual_path") == "GOVERNED_METRIC":
                grounding = (events.get("GOVERNED_GROUNDING_RESULT") or {}).get("payload", {}).get("grounding_output") or {}
                if grounding.get("metric_name") != case.get("governed_metric") or tuple(grounding.get("dimensions", ())) != tuple(case.get("dimensions", ())):
                    mechanism, grade, reason = "GOVERNED_GROUNDING_FAILURE", "E1_DIRECT_DECISION_EVIDENCE", "grounding DTO differs from the applicable frozen governed semantics"
                elif candidate_sig is not None and not _compatible(candidate_sig, reference_sig):
                    mechanism, grade, reason = "GOVERNED_COMPILER_FAILURE", "E2_DETERMINISTIC_CORROBORATION", "grounding is compatible but compiled candidate structure differs from frozen governed reference"
                else:
                    mechanism, grade, reason = "UNRESOLVED", "E5_UNRESOLVED", "authoritative result mismatch without a deterministic grounding/compiler structure mismatch"
            elif candidate_sig is None:
                mechanism, grade, reason = "UNRESOLVED", "E5_UNRESOLVED", "candidate SQL identity unavailable for structural attribution"
            else:
                selected = (events.get("MEMORY_SELECTION") or {}).get("payload", {}).get("selected_memory_ids", [])
                if selected:
                    selected_sigs = [memory[item] for item in selected if item in memory]
                    incompatible = any(not _compatible(item, reference_sig) for item in selected_sigs)
                    if incompatible:
                        selected_incompatible += 1
                    else:
                        selected_compatible += 1
                    if incompatible and any(_same_motif(candidate_sig, item, reference_sig) for item in selected_sigs):
                        mechanism, grade, reason = "MEMORY_OVERTRANSFER_EVIDENCE", "E3_TRACE_ASSOCIATION", "candidate carries a material motif present in rendered selected memory and absent from gold"
                        case_strength = "MT3_MATERIAL_WRONG_MOTIF_TRANSFER"
                        memory_strength["MT3_MATERIAL_WRONG_MOTIF_TRANSFER"] += 1
                    elif incompatible:
                        mechanism, grade, reason = "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE", "E2_DETERMINISTIC_CORROBORATION", "selected memory is structurally incompatible with frozen gold semantics"
                        case_strength = "MT2_STRUCTURAL_MOTIF_MATCH"
                        memory_strength["MT2_STRUCTURAL_MOTIF_MATCH"] += 1
                    elif not _compatible(candidate_sig, reference_sig):
                        mechanism, grade, reason = "DOWNSTREAM_GENERATION_FAILURE", "E2_DETERMINISTIC_CORROBORATION", "selected memory is compatible but generated SQL diverges structurally from gold"
                        compatible_memory_failures += 1
                        case_strength = "MT0_NO_TRANSFER_EVIDENCE"
                        memory_strength["MT0_NO_TRANSFER_EVIDENCE"] += 1
                    else:
                        mechanism, grade, reason = "UNRESOLVED", "E5_UNRESOLVED", "result mismatch is not explained by bounded structural evidence"
                        case_strength = "MT1_CONTEXT_ASSOCIATION_ONLY"
                        memory_strength["MT1_CONTEXT_ASSOCIATION_ONLY"] += 1
                elif not _compatible(candidate_sig, reference_sig):
                    mechanism, grade, reason = "DOWNSTREAM_GENERATION_FAILURE", "E2_DETERMINISTIC_CORROBORATION", "no selected memory context and generated SQL diverges structurally from gold"
                else:
                    mechanism, grade, reason = "UNRESOLVED", "E5_UNRESOLVED", "result mismatch is not explained by bounded structural evidence"
        if row.get("memory_used"):
            if row.get("v1_correct"):
                correct_memory += 1
            else:
                incorrect_memory += 1
        if mechanism is not None:
            outputs.append({"case_id": case_id, "partition": case["partition"], "expected_route": case["expected_route"], "actual_route": row.get("actual_path"), "primary_causal_mechanism": mechanism, "evidence_grade": grade, "secondary_phenotypes": [FAMILY_PHENOTYPE[case["primary_diagnostic_family"]]], "relevant_event_stages": [event["stage"] for event in provenance.get(case_id, [])], "memory_transfer_strength": case_strength, "evidence_reason": reason})
    failures = [item for item in outputs if item["primary_causal_mechanism"]]
    primary = Counter(item["primary_causal_mechanism"] for item in failures)
    grades = Counter(item["evidence_grade"] for item in failures)
    by_partition = {part: Counter(item["primary_causal_mechanism"] for item in failures if item["partition"] == part) for part in ("DIAG_A", "DIAG_B")}
    qualifying = {mechanism: {
        "e1": sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] == "E1_DIRECT_DECISION_EVIDENCE" for item in failures),
        "e2": sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] == "E2_DETERMINISTIC_CORROBORATION" for item in failures),
        "diag_a": sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] in {"E1_DIRECT_DECISION_EVIDENCE", "E2_DETERMINISTIC_CORROBORATION"} and item["partition"] == "DIAG_A" for item in failures),
        "diag_b": sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] in {"E1_DIRECT_DECISION_EVIDENCE", "E2_DETERMINISTIC_CORROBORATION"} and item["partition"] == "DIAG_B" for item in failures),
    } for mechanism in MECHANISMS}
    for value in qualifying.values():
        value["e1_e2"] = value["e1"] + value["e2"]
        value["share_of_failures"] = value["e1_e2"] / len(failures) if failures else 0
        value["eligible"] = value["e1_e2"] >= 12 and value["share_of_failures"] >= 0.20 and value["diag_a"] >= 5 and value["diag_b"] >= 5
    eligible = [key for key, value in qualifying.items() if value["eligible"]]
    if not eligible:
        classification = "M104_FRAGMENTED_CAUSAL_RESIDUALS"
        next_milestone = "M10.5 — High-Value Residual Slice Audit"
        selected = None
    elif len(eligible) == 1:
        classification = "M104_HIGH_LEVERAGE_MECHANISM_IDENTIFIED"
        selected = eligible[0]
        next_milestone = f"M11 mapped to {selected}"
    else:
        ranked = sorted(eligible, key=lambda key: (qualifying[key]["e1_e2"], qualifying[key]["e1"], qualifying[key]["diag_a"] + qualifying[key]["diag_b"]), reverse=True)
        top, second = qualifying[ranked[0]]["e1_e2"], qualifying[ranked[1]]["e1_e2"]
        if second and (top - second) / top <= 0.10:
            classification, selected, next_milestone = "M104_MULTIPLE_HIGH_LEVERAGE_MECHANISMS_IDENTIFIED", None, "M10.5 — High-Leverage Mechanism Disambiguation Audit"
        else:
            classification, selected, next_milestone = "M104_MULTIPLE_HIGH_LEVERAGE_MECHANISMS_IDENTIFIED", ranked[0], f"M11 mapped to {ranked[0]}"
    artifact = {
        "classification": classification, "failures": len(failures), "primary_counts": {key: primary.get(key, 0) for key in MECHANISMS}, "evidence_counts": {key: grades.get(key, 0) for key in ("E1_DIRECT_DECISION_EVIDENCE", "E2_DETERMINISTIC_CORROBORATION", "E3_TRACE_ASSOCIATION", "E4_MANUAL_DIAGNOSTIC", "E5_UNRESOLVED")}, "partition_counts": {key: dict(value) for key, value in by_partition.items()}, "qualifying": qualifying, "eligible_mechanisms": eligible, "selected_mechanism": selected, "next_milestone": next_milestone, "memory": {"retrieval_attempts": sum(bool(row.get("memory_used")) for row in rows.values()), "memory_use_cases": sum(bool(row.get("memory_used")) for row in rows.values()), "correct_memory_use_cases": correct_memory, "incorrect_memory_use_cases": incorrect_memory, "selected_compatible": selected_compatible, "selected_incompatible": selected_incompatible, "compatible_memory_downstream_failures": compatible_memory_failures, "transfer_strength_counts": dict(memory_strength), "counterfactual_run": False}, "actual_governed": actual_governed, "actual_direct": len(rows) - actual_governed, "no_gold_runtime": True, "manual_review": 0, "rule_version": "m104-causal-audit-v1",
    }
    (RESULT_ROOT / "automated_causal_attribution.json").write_text(json.dumps({"records": outputs}, indent=2, sort_keys=True) + "\n")
    artifact["attribution_hash"] = sha256((RESULT_ROOT / "automated_causal_attribution.json").read_bytes()).hexdigest()
    (RESULT_ROOT / "mechanism_summary.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    (RESULT_ROOT / "selection_decision.json").write_text(json.dumps({"classification": classification, "selected_mechanism": selected, "next_milestone": next_milestone, "gate": qualifying}, indent=2, sort_keys=True) + "\n")
    return artifact


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2, sort_keys=True))
