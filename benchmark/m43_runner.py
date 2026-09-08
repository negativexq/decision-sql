"""M43 typed JSON numeric scalar semantics ablation runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from benchmark import m41_runner as m41
from benchmark.m43_contract import (
    EXPECTED_BENCHMARK_HASH,
    EXPECTED_M41_PROMPT_HASH,
    audit_json_semantics,
    verify_parent,
)
from benchmark.model_contract import context_hash, governance_instructions, sha256_text

ROOT = Path(__file__).resolve().parent
M41_CONTRACT = ROOT / "manifests" / "m41_contract.json"
M41_LEDGER = ROOT / "manifests" / "m41_request_ledger.json"
CONFIG_PATH = ROOT / "experiments" / "m43_typed_json_numeric_semantics.json"
CONTRACT_PATH = ROOT / "manifests" / "m43_contract.json"
LEDGER_PATH = ROOT / "manifests" / "m43_request_ledger.json"
PAIRED_PATH = ROOT / "manifests" / "m43_request_isolation.json"


def _configure() -> None:
    m41.CONFIG_PATH = CONFIG_PATH
    m41.CONTRACT_PATH = CONTRACT_PATH
    m41.LEDGER_PATH = LEDGER_PATH
    m41.RESULT_ROOT = ROOT / "experiments" / "results" / "m43"
    m41.ARTIFACT_PREFIX = "m43"
    m41.OPERATION_NAME = "m43_typed_json_numeric_semantics"
    m41.SCHEMA_NAME = "decision_sql_m43_submission"
    m41.DISPLAY_NAME = "M43"


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def paired_request_audit() -> dict[str, Any]:
    _configure()
    old_contract = _load(M41_CONTRACT)
    old_ledger = _load(M41_LEDGER)
    rows = m41._rows(list(old_ledger["case_order"]))
    requests = m41._requests(list(old_ledger["case_order"]), rows)
    entries = []
    for old, new in zip(old_ledger["requests"], requests, strict=True):
        entries.append(
            {
                "case_id": old["case_id"],
                "m41_request_sha256": old["full_request_sha256"],
                "m43_request_sha256": new["request_sha256"],
                "question_identical": old["question_sha256"] == sha256_text(new["question"]),
                "context_identical": old["context_sha256"] == context_hash(new["database_id"]),
                "case_id_identical": old["case_id"] == new["case_id"],
                "provider_schema_identical": old["submission_schema_sha256"]
                == old_contract["hashes"]["submission_schema_hash"],
                "model_config_identical": True,
                "prompt_changed": old["instruction_sha256"]
                != sha256_text(governance_instructions()),
                "full_request_changed": old["full_request_sha256"] != new["request_sha256"],
            }
        )
    counts = {
        key: sum(entry[key] for entry in entries)
        for key in (
            "question_identical",
            "context_identical",
            "case_id_identical",
            "provider_schema_identical",
            "model_config_identical",
            "prompt_changed",
            "full_request_changed",
        )
    }
    return {
        "experiment_id": "m43_typed_json_numeric_semantics",
        "m41_prompt_hash": old_contract["hashes"]["governance_prompt_hash"],
        "m43_prompt_hash": sha256_text(governance_instructions()),
        "counts": counts,
        "requests": entries,
        "provider_calls": 0,
    }


def freeze_m43_contract() -> dict[str, Any]:
    _configure()
    parent = verify_parent()
    inventory = audit_json_semantics()
    if not parent["parent_prompt_matches"]:
        raise RuntimeError("M43_PARENT_PROMPT_MISMATCH")
    if not parent["benchmark_hash_matches"]:
        raise RuntimeError("M43_CONTRACT_MISMATCH:benchmark")
    if inventory["hypothesis_status"] != "SUPPORTED_OFFLINE":
        raise RuntimeError("M43_HYPOTHESIS_REJECTED_OFFLINE")
    prompt = governance_instructions()
    if any(
        term in prompt
        for term in (
            "Authorized relationship semantics",
            "opposite SQL join orientation",
            "multi-hop path",
        )
    ):
        raise RuntimeError("M43_CONTRACT_CONTAMINATION:m42_authority_text")
    for term in (
        "Typed JSON scalar semantics",
        "text-returning JSON extraction",
        "numeric comparison",
        "Do not infer a JSON path",
    ):
        if term not in prompt:
            raise RuntimeError(f"M43_CONTRACT_MISMATCH:missing_prompt:{term}")
    result = m41.freeze_m41_contract()
    old_contract = _load(M41_CONTRACT)
    contract = result["contract"]
    hashes = contract["hashes"]
    old_hashes = old_contract["hashes"]
    equal_keys = (
        "benchmark_content_hash",
        "submission_schema_hash",
        "serializer_hash",
        "evaluator_hash",
        "validator_hash",
        "case_order_hash",
        "provider_adapter_hash",
        "provider_config_hash",
    )
    for key in equal_keys:
        if hashes[key] != old_hashes[key]:
            raise RuntimeError(f"M43_CONTRACT_CONTAMINATION:{key}")
    if hashes["benchmark_content_hash"] != EXPECTED_BENCHMARK_HASH:
        raise RuntimeError("M43_CONTRACT_MISMATCH:benchmark_hash")
    if old_hashes["governance_prompt_hash"] != EXPECTED_M41_PROMPT_HASH:
        raise RuntimeError("M43_PARENT_PROMPT_MISMATCH:manifest")
    if hashes["governance_prompt_hash"] == old_hashes["governance_prompt_hash"]:
        raise RuntimeError("M43_CONTRACT_CONTAMINATION:prompt_unchanged")
    paired = paired_request_audit()
    expected_counts = {
        "question_identical": 90,
        "context_identical": 90,
        "case_id_identical": 90,
        "provider_schema_identical": 90,
        "model_config_identical": 90,
        "prompt_changed": 90,
        "full_request_changed": 90,
    }
    if paired["counts"] != expected_counts:
        raise RuntimeError("M43_CONTRACT_CONTAMINATION:request_isolation")
    contract.update(
        {
            "parent_contract": "benchmark/manifests/m41_contract.json",
            "old_m41_prompt_hash": old_hashes["governance_prompt_hash"],
            "new_m43_prompt_hash": hashes["governance_prompt_hash"],
            "json_semantics_inventory": "benchmark/audits/m43_json_semantics_inventory.json",
            "request_isolation": "benchmark/manifests/m43_request_isolation.json",
            "non_semantic_harness_difference": (
                "M42-derived artifact-prefix/manifest packaging support; model-facing "
                "path unchanged."
            ),
        }
    )
    m41._dump(CONTRACT_PATH, contract)
    m41._dump(PAIRED_PATH, paired)
    return {"contract": contract, "ledger": result["ledger"], "paired": paired}


def run_m43() -> dict[str, Any]:
    _configure()
    return m41.run_m41()


if __name__ == "__main__":
    raise SystemExit(json.dumps(freeze_m43_contract(), indent=2, sort_keys=True))
