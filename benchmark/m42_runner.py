"""M42 authorized-relationship traversal/composition ablation.

This module reuses the frozen M41 execution path while changing only the
governance prompt and writing all contract/run artifacts under M42 names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from benchmark import m41_runner as m41
from benchmark.model_contract import context_hash, governance_instructions, sha256_text

ROOT = Path(__file__).resolve().parent
M41_CONTRACT = ROOT / "manifests" / "m41_contract.json"
M41_LEDGER = ROOT / "manifests" / "m41_request_ledger.json"
M42_CONFIG = ROOT / "experiments" / "m42_authority_composition_clarification.json"
M42_CONTRACT = ROOT / "manifests" / "m42_contract.json"
M42_LEDGER = ROOT / "manifests" / "m42_request_ledger.json"


def _configure() -> None:
    m41.CONFIG_PATH = M42_CONFIG
    m41.CONTRACT_PATH = M42_CONTRACT
    m41.LEDGER_PATH = M42_LEDGER
    m41.RESULT_ROOT = ROOT / "experiments" / "results" / "m42"
    m41.ARTIFACT_PREFIX = "m42"
    m41.OPERATION_NAME = "m42_authority_composition_clarification"
    m41.SCHEMA_NAME = "decision_sql_m42_submission"
    m41.DISPLAY_NAME = "M42"


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def paired_request_audit() -> dict[str, Any]:
    _configure()
    old_contract = _load(M41_CONTRACT)
    old_ledger = _load(M41_LEDGER)
    config = _load(M42_CONFIG)
    rows = m41._rows(list(old_ledger["case_order"]))
    requests = m41._requests(list(old_ledger["case_order"]), rows)
    entries: list[dict[str, Any]] = []
    for old, new in zip(old_ledger["requests"], requests, strict=True):
        entries.append(
            {
                "case_id": old["case_id"],
                "m41_request_sha256": old["full_request_sha256"],
                "m42_request_sha256": new["request_sha256"],
                "question_identical": old["question_sha256"] == sha256_text(new["question"]),
                "context_identical": old["context_sha256"] == context_hash(new["database_id"]),
                "case_id_identical": old["case_id"] == new["case_id"],
                "provider_schema_identical": old["submission_schema_sha256"]
                == old_contract["hashes"]["submission_schema_hash"],
                "prompt_changed": old["instruction_sha256"]
                != sha256_text(governance_instructions()),
                "full_request_changed": old["full_request_sha256"] != new["request_sha256"],
            }
        )
    result = {
        "experiment_id": config["experiment_id"],
        "m41_prompt_hash": old_contract["hashes"]["governance_prompt_hash"],
        "m42_prompt_hash": sha256_text(governance_instructions()),
        "requests": entries,
        "counts": {
            "questions_identical": sum(e["question_identical"] for e in entries),
            "contexts_identical": sum(e["context_identical"] for e in entries),
            "case_ids_identical": sum(e["case_id_identical"] for e in entries),
            "provider_schema_identical": sum(e["provider_schema_identical"] for e in entries),
            "prompts_changed": sum(e["prompt_changed"] for e in entries),
            "full_requests_changed": sum(e["full_request_changed"] for e in entries),
        },
        "provider_calls": 0,
    }
    return result


def freeze_m42_contract() -> dict[str, Any]:
    _configure()
    result = m41.freeze_m41_contract()
    contract = result["contract"]
    old_contract = _load(M41_CONTRACT)
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
            raise RuntimeError(f"M42_CONTRACT_CONTAMINATION:{key}")
    if hashes["governance_prompt_hash"] == old_hashes["governance_prompt_hash"]:
        raise RuntimeError("M42_CONTRACT_CONTAMINATION:prompt_unchanged")
    paired = paired_request_audit()
    if paired["counts"] != {
        "questions_identical": 90,
        "contexts_identical": 90,
        "case_ids_identical": 90,
        "provider_schema_identical": 90,
        "prompts_changed": 90,
        "full_requests_changed": 90,
    }:
        raise RuntimeError("M42_CONTRACT_CONTAMINATION:paired_requests")
    contract["parent_contract"] = "benchmark/manifests/m41_contract.json"
    contract["old_m41_prompt_hash"] = old_hashes["governance_prompt_hash"]
    contract["new_m42_prompt_hash"] = hashes["governance_prompt_hash"]
    contract["paired_request_audit"] = "benchmark/manifests/m42_paired_request_audit.json"
    m41._dump(M42_CONTRACT, contract)
    m41._dump(ROOT / "manifests" / "m42_paired_request_audit.json", paired)
    return {"contract": contract, "ledger": result["ledger"], "paired": paired}


def run_m42() -> dict[str, Any]:
    _configure()
    return m41.run_m41()


if __name__ == "__main__":
    raise SystemExit(json.dumps(freeze_m42_contract(), indent=2, sort_keys=True))
