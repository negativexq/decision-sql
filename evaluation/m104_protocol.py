# ruff: noqa: E501

"""Frozen, provider-blind M10.4 protocol artifacts and pre-run validation."""

from __future__ import annotations

import json
import random
from hashlib import sha256
from pathlib import Path
from typing import Any

from evaluation.m104_corpus import FIXTURES, build_cases, stable_hash

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "960c1cd37b487e47f9323d81fcbd1f2b538a21b8"
PROTOCOL_HASH = "m104-protocol-v1"
SEED = int(sha256(CHECKPOINT.encode()).hexdigest()[:16], 16)

CAUSAL_MECHANISMS = (
    "EVALUATOR_ARTIFACT", "EXECUTION_FAILURE", "TRANSPORT_FAILURE", "M1_REJECTION",
    "ROUTING_OVERREACH", "ROUTING_FALSE_DIRECT", "GOVERNED_GROUNDING_FAILURE",
    "GOVERNED_COMPILER_FAILURE", "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE",
    "MEMORY_OVERTRANSFER_EVIDENCE", "DOWNSTREAM_GENERATION_FAILURE", "UNRESOLVED",
)
EVIDENCE_GRADES = (
    "E1_DIRECT_DECISION_EVIDENCE", "E2_DETERMINISTIC_CORROBORATION",
    "E3_TRACE_ASSOCIATION", "E4_MANUAL_DIAGNOSTIC", "E5_UNRESOLVED",
)
PHENOTYPES = (
    "SCHEMA_SOURCE", "JOIN_GRAIN", "AGGREGATION_GROUPING", "FORMULA_RATIO",
    "FILTER_COLUMN", "FILTER_OPERATOR", "FILTER_LITERAL", "FILTER_BOOLEAN_LOGIC",
    "TEMPORAL", "WINDOW", "PROJECTION_RESULT_SHAPE", "ORDER_TOPN", "DISTINCT_SET",
    "COMPOUND_SEMANTIC", "OTHER", "UNRESOLVED",
)


def _write(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def build_protocol_artifacts() -> dict[str, str]:
    cases = build_cases()
    order = [case["case_id"] for case in cases]
    random.Random(SEED).shuffle(order)
    artifacts = {
        "m104_causal_taxonomy.json": {
            "version": "m104-causal-taxonomy-v1", "primary_mechanisms": CAUSAL_MECHANISMS,
            "priority": ["EVALUATOR_ARTIFACT", "EXECUTION_FAILURE", "TRANSPORT_FAILURE", "M1_REJECTION", "ROUTING_OVERREACH", "ROUTING_FALSE_DIRECT", "GOVERNED_GROUNDING_FAILURE", "GOVERNED_COMPILER_FAILURE", "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE", "MEMORY_OVERTRANSFER_EVIDENCE", "DOWNSTREAM_GENERATION_FAILURE", "UNRESOLVED"],
        },
        "m104_evidence_rules.json": {
            "version": "m104-evidence-rules-v1", "grades": EVIDENCE_GRADES,
            "m11_counts": "E1+E2 only", "manual_review": "unresolved and borderline cases after automated artifact freeze",
        },
        "m104_semantic_phenotypes.json": {"version": "m104-phenotypes-v1", "phenotypes": PHENOTYPES},
        "m104_memory_transfer_rules.json": {
            "version": "m104-memory-transfer-v1", "strengths": ["MT0_NO_TRANSFER_EVIDENCE", "MT1_CONTEXT_ASSOCIATION_ONLY", "MT2_STRUCTURAL_MOTIF_MATCH", "MT3_MATERIAL_WRONG_MOTIF_TRANSFER"],
            "primary_overtransfer_requires": ["rendered_context", "selected_id", "memory_motif", "gold_incompatibility", "candidate_motif", "gold_absence", "result_consistency", "no_earlier_primary"],
            "counterfactual_claim": False,
        },
        "m104_selection_gate.json": {
            "version": "m104-selection-gate-v1", "minimum_e1_e2": 12, "minimum_share": 0.20,
            "minimum_each_partition": 5, "requires_both_partitions": True,
            "requires_coherent_bounded_boundary": True,
        },
        "m104_provider_protocol.json": {
            "version": "m104-provider-protocol-v1", "provider": "openai-compatible",
            "requested_model": "gpt-5.6-luna", "temperature": None, "top_p": None,
            "reasoning_effort": None, "max_logical_calls": 320, "judge_calls": 0,
            "repair_calls": 0, "sampling_calls": 0, "post_hoc_calls": 0,
        },
        "m104_execution_order.json": {
            "version": "m104-execution-order-v1", "seed": SEED, "case_ids": order,
            "order_hash": stable_hash(order), "interleave_recipe": "deterministic shuffle across both partitions",
        },
    }
    return {name: _write(name, value) for name, value in artifacts.items()}


def validate_protocol() -> dict[str, Any]:
    cases = build_cases()
    families = {family: sum(case["primary_diagnostic_family"] == family for case in cases) for family in {
        *[case["primary_diagnostic_family"] for case in cases if case["expected_route"] == "DIRECT"],
    }}
    partitions = {
        name: [case for case in cases if case["partition"] == name]
        for name in ("DIAG_A", "DIAG_B")
    }
    metrics = {
        metric: sum(case.get("governed_metric") == metric for case in cases)
        for metric in {case.get("governed_metric") for case in cases if case.get("governed_metric")}
    }
    assert len(cases) == 160
    assert sum(case["expected_route"] == "GOVERNED" for case in cases) == 40
    assert sum(case["expected_route"] == "DIRECT" for case in cases) == 120
    assert all(value == 10 for value in families.values())
    assert all(len(partition) == 80 for partition in partitions.values())
    assert all(sum(case["expected_route"] == "GOVERNED" for case in partition) == 20 for partition in partitions.values())
    assert all(sum(case["expected_route"] == "DIRECT" for case in partition) == 60 for partition in partitions.values())
    assert all(value == 4 for value in metrics.values())
    return {"status": "PASS", "cases": len(cases), "families": families, "metrics": metrics, "partitions": {key: len(value) for key, value in partitions.items()}, "provider_calls_before_final_lock": 0, "architecture_cases_consumed_before_final_lock": 0}


if __name__ == "__main__":
    print(json.dumps({"validation": validate_protocol(), "hashes": build_protocol_artifacts()}, indent=2, sort_keys=True))
