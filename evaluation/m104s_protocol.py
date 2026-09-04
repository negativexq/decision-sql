# ruff: noqa: E501

"""M10.4S provider-blind protocol, freshness, and reference gates."""

from __future__ import annotations

import json
import random
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.db.session import build_reader_engine
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from evaluation.m104s_corpus import (
    CORPUS_VERSION,
    FIXTURES,
    STARTING_CHECKPOINT,
    freshness_report,
    stable_hash,
)

ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = ROOT / "evaluation" / "results" / "m104s" / "fresh-provenance-20260905"
CORPUS_PATH = FIXTURES / "m104s_corpus.json"
GOLD_PATH = FIXTURES / "m104s_semantic_gold.json"
CONSUMED_MANIFEST_HASH = "17b5fc23ffdbcf1c2e45191c708032fae9b7690632197f4c0aa8b9d9d5de7ebc"
FRESHNESS_CONTRACT_HASH = "f4ae704d4380e41bdc1bbfefb487216d0e03b6ea53a7834a796b8e08a012617e"
CANONICALIZER_HASH = "3917b5f4a4a55ec897e8e799d1228ca4223ceae57aa836e54de3e17c22118e62"
CAUSAL_HASHES = {
    "causal_taxonomy": "8478cb00b95ae06126968afea1b0e23cca43918d7510c793380781ee7a807e1d",
    "evidence_rules": "d0339b054ee72b1074f976a083f6b52ffdd24ca2b3a79a140aa148fb9d8a2ce1",
    "phenotype": "57c925a976eb38c1d1a8e3123a83b7a3d28d22fba78d1751e9105ed1a66bbc97",
    "memory_rules": "200463c4b7c72745592ac6e6445b1f758b29937e49ed8e2317c6f701c5d36256",
    "selection_gate": "f5c06957b2ec72aba470fff42785843ad7b472f0cc36249ad3a80b8f0a963304",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_cases() -> list[dict[str, Any]]:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    if payload.get("corpus_version") != CORPUS_VERSION or len(payload.get("cases", [])) != 160:
        raise RuntimeError("M10.4S corpus fixture is not the frozen 160-case artifact")
    return list(payload["cases"])


def validate_manifest(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = cases or load_cases()
    ids = [case["case_id"] for case in rows]
    questions = [case["question"] for case in rows]
    governed = [case for case in rows if case["expected_route"] == "GOVERNED"]
    direct = [case for case in rows if case["expected_route"] == "DIRECT"]
    metrics = {case["governed_metric"]: 0 for case in governed}
    families = {case["primary_diagnostic_family"]: 0 for case in direct}
    partitions = {"DIAG_A": 0, "DIAG_B": 0}
    for case in governed:
        metrics[case["governed_metric"]] += 1
    for case in direct:
        families[case["primary_diagnostic_family"]] += 1
    for case in rows:
        partitions[case["partition"]] += 1
    result = {
        "corpus_version": CORPUS_VERSION,
        "cases": len(rows),
        "expected_governed": len(governed),
        "expected_direct": len(direct),
        "unique_case_ids": len(set(ids)),
        "unique_questions": len(set(questions)),
        "governed_metric_counts": metrics,
        "direct_family_counts": families,
        "partition_counts": partitions,
        "all_case_ids_new_prefix": all(item.startswith("m104s-") for item in ids),
        "contains_old_m104_ids": any(item.startswith("m104-") for item in ids),
    }
    if not (
        result["cases"] == 160
        and result["expected_governed"] == 40
        and result["expected_direct"] == 120
        and result["unique_case_ids"] == 160
        and result["unique_questions"] == 160
        and all(value == 4 for value in metrics.values())
        and all(value == 10 for value in families.values())
        and partitions == {"DIAG_A": 80, "DIAG_B": 80}
        and result["all_case_ids_new_prefix"]
        and not result["contains_old_m104_ids"]
    ):
        raise RuntimeError(f"M10.4S quota validation failed: {result}")
    return result


def write_first_freshness() -> dict[str, Any]:
    cases = load_cases()
    validate_manifest(cases)
    report = freshness_report(tuple(cases))
    if report["status"] != "PASS":
        raise RuntimeError(f"M10.4S first freshness gate failed: {report}")
    report["gate"] = "FIRST_PRE_REFERENCE_VALIDATION"
    write_json(FIXTURES / "m104s_first_freshness.json", report)
    return report


def write_final_freshness() -> dict[str, Any]:
    cases = load_cases()
    report = freshness_report(tuple(cases))
    if report["status"] != "PASS":
        raise RuntimeError(f"M10.4S final freshness gate failed: {report}")
    report["gate"] = "SECOND_IMMEDIATE_BEFORE_MASTER_LOCK"
    write_json(FIXTURES / "m104s_final_freshness.json", report)
    return report


def write_execution_protocol() -> dict[str, str]:
    cases = load_cases()
    corpus_hash = file_hash(CORPUS_PATH)
    seed = int(sha256(f"{STARTING_CHECKPOINT}|{corpus_hash}".encode()).hexdigest()[:16], 16)
    ids = [case["case_id"] for case in cases]
    random.Random(seed).shuffle(ids)
    provider = {
        "version": "m104s-provider-protocol-v1",
        "provider": "openai-compatible",
        "requested_model": "gpt-5.6-luna",
        "temperature": None,
        "top_p": None,
        "reasoning_effort": None,
        "max_tokens": "provider_current_default",
        "retry_policy": "existing_current_runtime_only",
        "max_logical_calls": 320,
        "repair_calls": 0,
        "judge_calls": 0,
        "sampling_calls": 0,
        "post_hoc_calls": 0,
    }
    order = {
        "version": "m104s-execution-order-v1",
        "seed": seed,
        "starting_checkpoint": STARTING_CHECKPOINT,
        "corpus_hash": corpus_hash,
        "case_ids": ids,
        "order_hash": stable_hash(ids),
        "interleave_recipe": "deterministic shuffle of both frozen partitions",
    }
    result = {
        "provider_protocol": write_json(FIXTURES / "m104s_provider_protocol.json", provider),
        "execution_order": write_json(FIXTURES / "m104s_execution_order.json", order),
    }
    return result


def _execution_shape(execution: QueryExecution) -> dict[str, Any]:
    return {
        "columns": execution.columns,
        "row_count": execution.row_count,
        "truncated": execution.truncated,
        "rows": _jsonable(execution.rows),
    }


def validate_references() -> dict[str, Any]:
    cases = load_cases()
    settings = get_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    records: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    plans = executions = 0
    for case in cases:
        sql = case["reference_sql"]
        planned = safety.plan(SqlCandidate(sql=sql, source=CandidateSource.INTERNAL, correlation_id=f"m104s-ref:{case['case_id']}"))
        if not isinstance(planned, QueryPlan):
            raise RuntimeError(f"M10.4S reference plan failed: {case['case_id']}")
        plans += 1
        execution = safety.execute(planned)
        if not isinstance(execution, QueryExecution):
            raise RuntimeError(f"M10.4S reference execution failed: {case['case_id']}")
        executions += 1
        results[case["case_id"]] = execution.model_dump(mode="json")
        records.append({
            "case_id": case["case_id"],
            "reference_sql_hash": case["reference_sql_hash"],
            "canonical_reference_fingerprint": case["canonical_reference_fingerprint"],
            "columns": execution.columns,
            "row_count": execution.row_count,
            "result_hash": stable_hash(_execution_shape(execution)),
        })
    payload = {"records": records, "results": results, "db_mutations": 0, "validation_role": "reference_only"}
    result_hash = write_json(RESULT_ROOT / "reference_results.json", payload)
    report = {
        "parse": f"{len(cases)}/160",
        "m1_plan": f"{plans}/160",
        "execution": f"{executions}/160",
        "valid_result_contract": f"{sum('columns' in item for item in records)}/160",
        "reference_results_hash": result_hash,
        "reference_db_mutations": 0,
        "provider_calls": 0,
        "architecture_cases_consumed": 0,
    }
    write_json(RESULT_ROOT / "reference_validation.json", report)
    return report


def component_manifest() -> dict[str, str]:
    paths = {
        "provenance_models": "app/provenance/models.py",
        "provenance_sink": "app/provenance/sink.py",
        "provenance_canonicalization": "app/provenance/canonical.py",
        "m4_integration": "app/memory/runtime.py",
        "m3_integration": "app/semantics/routing.py",
        "direct_integration": "app/text_to_sql/service.py",
        "provider_integration": "app/generation/provider.py",
        "m1_integration": "app/sql/service.py",
        "freshness_canonicalizer": "evaluation/reference_freshness.py",
        "m104s_corpus": "evaluation/m104s_corpus.py",
        "m104s_protocol": "evaluation/m104s_protocol.py",
        "m104s_runner": "evaluation/run_m104s.py",
    }
    return {name: file_hash(ROOT / path) for name, path in paths.items()}


def create_master_lock() -> dict[str, Any]:
    cases = load_cases()
    first = json.loads((FIXTURES / "m104s_first_freshness.json").read_text())
    final = json.loads((FIXTURES / "m104s_final_freshness.json").read_text())
    reference = json.loads((RESULT_ROOT / "reference_results.json").read_text())
    order = json.loads((FIXTURES / "m104s_execution_order.json").read_text())
    provider = json.loads((FIXTURES / "m104s_provider_protocol.json").read_text())
    values = {
        "corpus": json.loads(CORPUS_PATH.read_text()),
        "case_ids": [case["case_id"] for case in cases],
        "questions": [case["question"] for case in cases],
        "references": [case["reference_sql"] for case in cases],
        "canonical_fingerprints": [case["canonical_reference_fingerprint"] for case in cases],
        "semantic_gold": json.loads(GOLD_PATH.read_text()),
        "partitions": json.loads((FIXTURES / "m104s_partition_manifest.json").read_text()),
        "first_freshness": first,
        "final_freshness": final,
        "consumed_evidence_manifest_hash": CONSUMED_MANIFEST_HASH,
        "freshness_contract_hash": FRESHNESS_CONTRACT_HASH,
        "canonicalizer_hash": CANONICALIZER_HASH,
        **CAUSAL_HASHES,
        "provider_protocol": provider,
        "execution_order": order,
        "component_manifest": component_manifest(),
        "reference_results": reference,
    }
    lock = {
        "version": "m104s-master-lock-v1",
        "starting_checkpoint": STARTING_CHECKPOINT,
        "corpus_version": CORPUS_VERSION,
        "contract_id": "decision-sql-residual-provenance-v1",
        "freshness_contract_id": "decision-sql-reference-freshness-v1",
        "event_schema_version": 1,
        "hashes": {name: stable_hash(value) for name, value in values.items()},
        "execution_order_seed": order["seed"],
        "provider_calls_before_lock": 0,
        "architecture_cases_consumed_before_lock": 0,
    }
    lock["master_lock_hash"] = stable_hash(lock)
    write_json(RESULT_ROOT / "m104s_master_lock.json", lock)
    write_json(FIXTURES / "m104s_master_lock.json", lock)
    return lock


def final_pre_provider_gate() -> dict[str, Any]:
    first = json.loads((FIXTURES / "m104s_first_freshness.json").read_text())
    final = json.loads((FIXTURES / "m104s_final_freshness.json").read_text())
    reference = json.loads((RESULT_ROOT / "reference_validation.json").read_text())
    lock = json.loads((RESULT_ROOT / "m104s_master_lock.json").read_text())
    gate = {
        "final_freshness_status": final["status"],
        "canonical_reference_overlap": final["canonical_reference_overlap"],
        "exact_question_overlap": final["exact_historical_question_overlap"],
        "normalized_question_overlap": final["normalized_historical_question_overlap"],
        "invalid_m104_canonical_overlap": final["invalid_m104_canonical_reference_overlap"],
        "reference_validation": reference["execution"],
        "provider_calls_so_far": 0,
        "architecture_cases_consumed": 0,
        "master_lock_hash": lock["master_lock_hash"],
        "passed": final["status"] == "PASS" and final["canonical_reference_overlap"] == 0 and reference["execution"] == "160/160",
    }
    write_json(RESULT_ROOT / "provider_call_one_gate.json", gate)
    if not gate["passed"] or first["status"] != "PASS":
        raise RuntimeError(f"M10.4S provider gate failed: {gate}")
    return gate


if __name__ == "__main__":
    write_first_freshness()
    write_execution_protocol()
    print(json.dumps(validate_manifest(), indent=2, sort_keys=True))
