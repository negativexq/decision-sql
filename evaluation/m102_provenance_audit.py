"""Offline audit and contract validator for M10.2.

This module deliberately does not import application runtime services.  It
validates the frozen, behavior-neutral provenance design and its DB/provider-
free synthetic fixtures.  Runtime capture is a later milestone.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
OUT = ROOT / "evaluation" / "results" / "m102" / "provenance-audit-20260904"

FROZEN_HASHES = {
    "m10r_corpus": "4718051d31ffe00695f26413cc0595f8ab9b7e51a546b551b5d907d17caed0c5",
    "m10r_final_summary": "3ba4d1c4ab9ee1859576c3672338ddd653b40dd034d8fd4db95d868ea27513a1",
    "m10r_reference_results": "8730aacf8d80d719d492f1fa7d66a34915cc1795721ad0b4e9697514bfc122f9",
    "m10r_provider_protocol": "443d3ab59635bc8c3823bfced6c13a6963df60638e9f24586675604349f3c2e2",
    "m10r_master_protocol": "1212542536762b0688e465dac3700d07e82e26e252064818996449f5ecb89b18",
    "m10r_ledger": "0eb4d4d640f3759f24da40ad0e242950eedc1a39fb6e039968e9e9c94e346f49",
    "m10r_v1": "e327a6bd3b9272549d5e5cea7fa4edcc836a6841f7627c87b27d96a78c67640d",
    "m10r_v2": "536a1d77883d754e4161d4a0a0bb648013333b7f4f37a85d33f6eafb1dfb9497",
    "m10r_residuals": "38d914526c0debf68da291c9a1a7070c5b4ad3d906ea8bf16f25831b30900e04",
    "m10r_final": "3ba4d1c4ab9ee1859576c3672338ddd653b40dd034d8fd4db95d868ea27513a1",
    "m101_taxonomy": "e6e47e41d94638c90b77f10216d7450a43db440bb2fa0bb91f91430cd8ca19d0",
    "m101_evidence": "7c25341a3cc878989b1ee66d4d4dbf3d5589cfd8ac08584289fbdea83da227b5",
    "m101_protocol": "b833439f0c0c515aa77797fa93e6a402740d8d7f85e5f069414f22f09d459c18",
    "m101_automated": "548a543d3a1f66e19f5403074634736b87dafa4e639fca979cd1b074bb718789",
    "m101_final": "007d1c0442330694fd393cb24f61fa7ca94ed41bf0275652ce3180e537505632",
    "binder_v1": "aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb",
    "binder_v2": "ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27ecda3b78fb8e",
    "comparator": "677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97",
}

PROFILE_FIELDS = {
    "MEMORY_DIAGNOSTIC": [
        "retrieval_attempted", "retriever_version", "retriever_config_hash",
        "memory_corpus_hash", "query_input_hash", "top_k_requested", "ranked_results",
        "selected_memory_ids", "memory_use_mode", "rendered_memory_context_hash",
    ],
    "GOVERNED_DIAGNOSTIC": [
        "grounding_request_hash", "grounding_output", "selected_metric_id",
        "selected_dimension_ids", "compiler_input_hash", "compiler_version_hash",
        "compiled_sql_hash", "provider_request_hash", "provider_response_id",
        "raw_provider_output", "candidate_extraction_status", "candidate_sql_hash",
    ],
    "DIRECT_DIAGNOSTIC": [
        "schema_context_hash", "generation_context_manifest_hash",
        "rendered_memory_context_hash", "provider_request_hash", "provider_response_id",
        "raw_provider_output", "candidate_extraction_status", "candidate_sql_hash",
    ],
}

PROHIBITED_NAMES = {
    "api_key", "authorization_header", "database_password",
    "credentialed_connection_string", "provider_headers", "environment_secrets",
    "unbounded_result_rows",
}


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _contains_prohibited(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in PROHIBITED_NAMES:
                return str(key)
            found = _contains_prohibited(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _contains_prohibited(item)
            if found:
                return found
    return None


def validate_contract() -> dict[str, Any]:
    contract = load("m102_provenance_contract.json")
    catalog = load("m102_provenance_field_catalog.json")
    matrix = load("m102_hypothesis_field_matrix.json")
    seams = load("m102_capture_seam_matrix.json")
    strategies = load("m102_observability_strategy_matrix.json")
    plan = load("m102_behavior_equivalence_test_plan.json")
    evidence = load("m102_evidence_manifest.json")
    fixtures = load("m102_provenance_contract_fixtures.json")

    if contract["contract_id"] != "decision-sql-residual-provenance-v1":
        raise ValueError("unexpected provenance contract ID")
    for profile, fields in PROFILE_FIELDS.items():
        declared = set(contract["profiles"][profile])
        missing = set(fields) - declared
        if missing:
            raise ValueError(f"profile {profile} omits {sorted(missing)}")
    field_names = {field["name"] for field in catalog["fields"]}
    for profile, fields in contract["profiles"].items():
        if profile == "FULL_DIAGNOSTIC":
            continue
        unknown = set(fields) - field_names
        if unknown:
            raise ValueError(f"profile {profile} has unknown fields {sorted(unknown)}")
    matrix_fields = {
        field
        for hypothesis in matrix["hypotheses"]
        for field in hypothesis["required_runtime_fields"]
    }
    unknown_matrix = matrix_fields - field_names
    if unknown_matrix:
        raise ValueError(f"hypothesis matrix has unknown fields {sorted(unknown_matrix)}")
    seam_stages = {item["stage"] for item in seams["seams"]}
    required_seams = {
        "MEMORY_RETRIEVAL_RESULT",
        "MEMORY_SELECTION",
        "GOVERNED_GROUNDING_RESULT",
        "GENERATION_CONTEXT",
    }
    if not required_seams <= seam_stages:
        raise ValueError("required capture seams are missing")
    if strategies["selected_option"] != "O3_TYPED_PROVENANCE_COLLECTOR_INTERFACE":
        raise ValueError("future strategy is not singularly selected")
    if len(plan["tests"]) < 5:
        raise ValueError("behavior-equivalence plan is incomplete")
    prohibited = _contains_prohibited(contract) or _contains_prohibited(catalog)
    if prohibited:
        raise ValueError(f"prohibited field appears in contract: {prohibited}")
    if _contains_prohibited(fixtures["secret_payload"]) != "authorization_header":
        raise ValueError("secret rejection fixture is not exercising the required boundary")
    if len(evidence["historical_inputs"]) < 10:
        raise ValueError("historical evidence manifest is incomplete")
    return {
        "contract_id": contract["contract_id"],
        "field_count": len(catalog["fields"]),
        "profile_counts": {name: len(fields) for name, fields in contract["profiles"].items()},
        "hypothesis_count": len(matrix["hypotheses"]),
        "capture_seam_count": len(seams["seams"]),
        "strategy_count": len(strategies["ratings"]),
        "behavior_test_count": len(plan["tests"]),
        "prohibited_field_count": len(catalog["prohibited"]),
        "historical_inputs": len(evidence["historical_inputs"]),
        "m10r_score_unchanged": True,
        "runtime_activity": {
            "provider_calls": 0, "database_calls": 0, "retrieval_calls": 0,
            "generation_calls": 0, "grounding_calls": 0, "embedding_calls": 0,
            "reranker_calls": 0,
        },
    }


def verify_frozen_hashes() -> dict[str, str]:
    result_root = ROOT / "evaluation" / "results" / "m10r" / "clean-rebaseline-20260904"
    master_protocol = json.loads((result_root / "master_protocol.json").read_text())
    pre_db_lock = json.loads(
        (FIXTURES / "m10r_pre_db_freeze_lock_v2.json").read_text()
    )
    m101_final = json.loads(
        (ROOT / "evaluation/results/m101/residual-audit-20260904/final_decision.json").read_text()
    )
    actual = {
        "m10r_corpus": pre_db_lock["hashes"]["corpus_content_hash"],
        "m10r_final_summary": sha256((result_root / "final_summary.json").read_bytes()).hexdigest(),
        "m10r_reference_results": master_protocol["reference_results_hash"],
        "m10r_provider_protocol": sha256(
            (result_root / "provider_protocol.json").read_bytes()
        ).hexdigest(),
        "m10r_master_protocol": master_protocol["master_protocol_hash"],
        "m10r_ledger": sha256((result_root / "case_ledger.jsonl").read_bytes()).hexdigest(),
        "m10r_v1": sha256((result_root / "primary_results.json").read_bytes()).hexdigest(),
        "m10r_v2": sha256((result_root / "v2_shadow_results.json").read_bytes()).hexdigest(),
        "m10r_residuals": sha256((result_root / "residual_results.json").read_bytes()).hexdigest(),
        "m101_automated": m101_final["automated_audit_hash"],
        "m101_final": m101_final["final_summary_hash"],
        "binder_v1": master_protocol["binder_v1_hash"],
        "binder_v2": master_protocol["binder_v2_hash"],
        "comparator": master_protocol["comparator_hash"],
    }
    for name, value in actual.items():
        if value != FROZEN_HASHES[name]:
            raise ValueError(f"frozen evidence mismatch: {name}")
    return actual


def build_summary() -> dict[str, Any]:
    contract = load("m102_provenance_contract.json")
    catalog = load("m102_provenance_field_catalog.json")
    matrix = load("m102_hypothesis_field_matrix.json")
    seams = load("m102_capture_seam_matrix.json")
    strategies = load("m102_observability_strategy_matrix.json")
    plan = load("m102_behavior_equivalence_test_plan.json")
    evidence = load("m102_evidence_manifest.json")
    validation = validate_contract()
    return {
        "classification": "M102_PROVENANCE_CAPTURE_CONTRACT_READY",
        "starting_checkpoint": "c638dbaab1ea1f59c7178f7ee20eb24623fa0d78",
        "m10r_primary": "28/200",
        "m10r_consumed": 200,
        "m101_classification": "M101_AUDIT_BLOCKED",
        "m101_failures": 172,
        "m101_e1_e2": 125,
        "m101_dominance_threshold": 43,
        "m101_dominant_mechanism": None,
        "m10r_artifacts_modified": False,
        "historical_hashes": verify_frozen_hashes(),
        "contract_hashes": {
            "field_catalog": stable_hash(catalog),
            "hypothesis_matrix": stable_hash(matrix),
            "capture_seam_matrix": stable_hash(seams),
            "strategy_matrix": stable_hash(strategies),
            "behavior_equivalence_plan": stable_hash(plan),
            "provenance_contract": stable_hash(contract),
            "evidence_manifest": stable_hash(evidence),
        },
        "selected_architecture": strategies["selected_option"],
        "canonical_storage": contract["retention"]["canonical"],
        "blocker_closure": {
            "memory_selectivity": True,
            "memory_overtransfer": True,
            "governed_slot": True,
            "governed_compiler": True,
            "downstream_generation": True,
            "historical_values_reconstructed": False,
        },
        "validation": validation,
        "next_milestone": "M10.3 — Non-Semantic Residual Provenance Instrumentation",
    }


def main() -> None:
    summary = build_summary()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "final_decision.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
