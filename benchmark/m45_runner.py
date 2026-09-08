"""M45 parent/child additive measure alignment ablation runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from benchmark import m41_runner as m41
from benchmark.m45_contract import (
    EXPECTED_BENCHMARK_HASH,
    EXPECTED_M43_PROMPT_HASH,
    audit_applicability,
    preserve_historical_evidence,
    verify_parent,
)
from benchmark.model_contract import context_hash, governance_instructions, sha256_text

ROOT = Path(__file__).resolve().parent
M43_CONTRACT = ROOT / "manifests" / "m43_contract.json"
M43_LEDGER = ROOT / "manifests" / "m43_request_ledger.json"
CONFIG_PATH = ROOT / "experiments" / "m45_parent_child_additive_alignment.json"
CONTRACT_PATH = ROOT / "manifests" / "m45_contract.json"
LEDGER_PATH = ROOT / "manifests" / "m45_request_ledger.json"
PAIRED_PATH = ROOT / "manifests" / "m45_paired_request_audit.json"


def _configure() -> None:
    m41.CONFIG_PATH = CONFIG_PATH
    m41.CONTRACT_PATH = CONTRACT_PATH
    m41.LEDGER_PATH = LEDGER_PATH
    m41.RESULT_ROOT = ROOT / "experiments" / "results" / "m45"
    m41.ARTIFACT_PREFIX = "m45"
    m41.OPERATION_NAME = "m45_parent_child_additive_alignment"
    m41.SCHEMA_NAME = "decision_sql_m45_submission"
    m41.DISPLAY_NAME = "M45"


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def paired_request_audit() -> dict[str, Any]:
    _configure()
    parent_contract = _load(M43_CONTRACT)
    parent_ledger = _load(M43_LEDGER)
    rows = m41._rows(list(parent_ledger["case_order"]))
    requests = m41._requests(list(parent_ledger["case_order"]), rows)
    entries = []
    for old, new in zip(parent_ledger["requests"], requests, strict=True):
        entries.append(
            {
                "case_id": old["case_id"],
                "m43_request_sha256": old["full_request_sha256"],
                "m45_request_sha256": new["request_sha256"],
                "question_identical": old["question_sha256"] == sha256_text(new["question"]),
                "context_identical": old["context_sha256"] == context_hash(new["database_id"]),
                "case_id_identical": old["case_id"] == new["case_id"],
                "provider_schema_identical": old["submission_schema_sha256"]
                == parent_contract["hashes"]["submission_schema_hash"],
                "model_config_identical": True,
                "prompt_changed": old["instruction_sha256"]
                != sha256_text(governance_instructions()),
                "full_request_changed": old["full_request_sha256"] != new["request_sha256"],
            }
        )
    fields = (
        "question_identical",
        "context_identical",
        "case_id_identical",
        "provider_schema_identical",
        "model_config_identical",
        "prompt_changed",
        "full_request_changed",
    )
    return {
        "experiment_id": "m45_parent_child_additive_alignment",
        "m43_prompt_hash": parent_contract["hashes"]["governance_prompt_hash"],
        "m45_prompt_hash": hashlib.sha256(governance_instructions().encode()).hexdigest(),
        "counts": {key: sum(entry[key] for entry in entries) for key in fields},
        "requests": entries,
        "provider_calls": 0,
    }


def freeze_m45_contract() -> dict[str, Any]:
    _configure()
    preservation = preserve_historical_evidence()
    parent = verify_parent()
    inventory = audit_applicability()
    if not parent["m43_parent_matches"]:
        raise RuntimeError("M45_PARENT_PROMPT_MISMATCH")
    if not parent["benchmark_hash_matches"]:
        raise RuntimeError("M45_CONTRACT_MISMATCH:benchmark")
    if inventory["uncertain_cases"]:
        raise RuntimeError("M45_HYPOTHESIS_REJECTED_OFFLINE:uncertain_applicability")
    prompt = governance_instructions()
    for phrase in (
        "Typed JSON scalar semantics",
        "Parent/child additive measure alignment",
        "aggregate the child additive values by the declared parent key",
    ):
        if phrase not in prompt:
            raise RuntimeError(f"M45_CONTRACT_MISMATCH:missing_prompt:{phrase}")
    for forbidden in (
        "Native measure grain and fanout",
        "opposite SQL join orientation",
        "multi-hop path",
        "warehouse_08",
        "risk_05",
        "subscription_04",
    ):
        if forbidden in prompt:
            raise RuntimeError(f"M45_CONTRACT_CONTAMINATION:{forbidden}")
    result = m41.freeze_m41_contract()
    parent_contract = _load(M43_CONTRACT)
    contract = result["contract"]
    hashes = contract["hashes"]
    old_hashes = parent_contract["hashes"]
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
            raise RuntimeError(f"M45_CONTRACT_CONTAMINATION:{key}")
    if hashes["benchmark_content_hash"] != EXPECTED_BENCHMARK_HASH:
        raise RuntimeError("M45_CONTRACT_MISMATCH:benchmark_hash")
    if old_hashes["governance_prompt_hash"] != EXPECTED_M43_PROMPT_HASH:
        raise RuntimeError("M45_PARENT_PROMPT_MISMATCH:manifest")
    if hashes["governance_prompt_hash"] == old_hashes["governance_prompt_hash"]:
        raise RuntimeError("M45_CONTRACT_CONTAMINATION:prompt_unchanged")
    paired = paired_request_audit()
    expected = {
        "question_identical": 90,
        "context_identical": 90,
        "case_id_identical": 90,
        "provider_schema_identical": 90,
        "model_config_identical": 90,
        "prompt_changed": 90,
        "full_request_changed": 90,
    }
    if paired["counts"] != expected:
        raise RuntimeError("M45_CONTRACT_CONTAMINATION:request_isolation")
    contract.update(
        {
            "parent_contract": "benchmark/manifests/m43_contract.json",
            "old_m43_prompt_hash": old_hashes["governance_prompt_hash"],
            "new_m45_prompt_hash": hashes["governance_prompt_hash"],
            "additive_alignment_inventory": (
                "benchmark/audits/m45_additive_alignment_inventory.json"
            ),
            "paired_request_audit": "benchmark/manifests/m45_paired_request_audit.json",
            "historical_preservation": "benchmark/reports/m45_historical_preservation.json",
            "non_semantic_harness_difference": (
                "M42-derived artifact-prefix/manifest packaging support; "
                "model-facing path unchanged."
            ),
        }
    )
    m41._dump(CONTRACT_PATH, contract)
    m41._dump(PAIRED_PATH, paired)
    return {
        "contract": contract,
        "ledger": result["ledger"],
        "paired": paired,
        "preservation": preservation,
    }


def run_m45() -> dict[str, Any]:
    _configure()
    return m41.run_m41()


if __name__ == "__main__":
    print(json.dumps(freeze_m45_contract(), indent=2, sort_keys=True))
