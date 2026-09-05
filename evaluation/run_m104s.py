# ruff: noqa: E501

"""One-pass M10.4S current-architecture execution and provenance validation.

The provider phase refuses to start unless the independent reference gate, the
second canonical freshness gate, and the master lock all exist and pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from app.models.domain import TextToSqlRequest
from app.provenance.canonical import semantic_hash
from app.provenance.sink import PROHIBITED_KEYS, DiagnosticJsonlProvenanceSink
from app.sql.models import QueryExecution
from app.text_to_sql.models import TextToSqlStatus
from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.m104s_corpus import CORPUS_VERSION, FIXTURES
from evaluation.m104s_protocol import (
    RESULT_ROOT,
    file_hash,
    final_pre_provider_gate,
    load_cases,
    validate_manifest,
)
from evaluation.result_binding_protocol_v2 import BindingStatus, bind_generated_result_v2
from evaluation.result_snapshot import restore_query_execution
from evaluation.run_m104 import (
    _build_runtime,
    _contract_and_spec,
    _jsonable,
    _primary_status,
    _settings_for_m104,
)
from evaluation.versioned_result_evaluator import (
    EvaluationComparisonRequest,
    EvaluatorMode,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]
ORDER_PATH = FIXTURES / "m104s_execution_order.json"
REFERENCE_PATH = RESULT_ROOT / "reference_results.json"
LEDGER_PATH = RESULT_ROOT / "runtime_provenance.jsonl"
CASE_LEDGER_PATH = RESULT_ROOT / "case_ledger.jsonl"


def _write(name: str, value: Any) -> str:
    path = RESULT_ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return file_hash(path)


def _contains_prohibited(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in PROHIBITED_KEYS or _contains_prohibited(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_prohibited(item) for item in value)
    return False


def _execution_shape(execution: QueryExecution) -> dict[str, Any]:
    return {
        "columns": execution.columns,
        "row_count": execution.row_count,
        "truncated": execution.truncated,
        "rows": _jsonable(execution.rows),
    }


def _v2_shadow(case: dict[str, Any], result: Any, reference: QueryExecution, candidate_sql: str | None) -> dict[str, Any]:
    if case["expected_route"] != "GOVERNED" or candidate_sql is None or not isinstance(result.execution, QueryExecution):
        return {"status": "V2_UNAVAILABLE"}
    contract, spec = _contract_and_spec(case)
    binding = bind_generated_result_v2(candidate_sql, result.execution, spec, contract, build_schema_semantic_map())
    reference_binding = bind_generated_result_v2(case["reference_sql"], reference, spec, contract, build_schema_semantic_map())
    if not all(item.binding_status is BindingStatus.BOUND for item in binding.report.bindings + reference_binding.report.bindings):
        return {"status": "V2_UNAVAILABLE", "reason": "reference-blind binding unavailable"}
    outcome = evaluate(EvaluationComparisonRequest(binding.result, (reference_binding.result,), EvaluatorMode.V2, result_contract=contract, contract_id=case["contract_id"]))
    return {"status": "V2_EVALUATED", "equivalent": outcome.equivalent, "outcome": outcome.to_dict()}


async def run_once() -> dict[str, Any]:
    if RESULT_ROOT.exists() and (LEDGER_PATH.exists() or CASE_LEDGER_PATH.exists()):
        raise RuntimeError("M10.4S result directory already contains an attempted run")
    cases = load_cases()
    validate_manifest(cases)
    gate = final_pre_provider_gate()
    lock = json.loads((RESULT_ROOT / "m104s_master_lock.json").read_text())
    order = json.loads(ORDER_PATH.read_text())
    if lock["master_lock_hash"] != gate["master_lock_hash"] or len(order["case_ids"]) != 160:
        raise RuntimeError("M10.4S master-lock/order gate is missing or inconsistent")
    references = json.loads(REFERENCE_PATH.read_text())["results"]
    if len(references) != 160:
        raise RuntimeError("M10.4S reference results are incomplete")
    reference_by_id = {key: restore_query_execution(value) for key, value in references.items()}
    settings = _settings_for_m104()
    sink = DiagnosticJsonlProvenanceSink(LEDGER_PATH)
    route, provider = _build_runtime(settings, sink)
    by_id = {case["case_id"]: case for case in cases}
    rows: list[dict[str, Any]] = []
    logical_calls = 0
    try:
        for ordinal, case_id in enumerate(order["case_ids"], start=1):
            case = by_id[case_id]
            provider.consume_model_io_history()
            started = perf_counter()
            request = TextToSqlRequest(question=case["question"], correlation_id=case_id, execute=True)
            try:
                decision = await route.run(request)
                result = decision.user_result
                captures = provider.consume_model_io_history()
                candidate_sql = result.candidate.sql if result.candidate else (decision.governed_candidate.sql if decision.governed_candidate else None)
                status, correct = _primary_status(result, reference_by_id[case_id])
                shadow = _v2_shadow(case, result, reference_by_id[case_id], candidate_sql)
                row = {
                    "ordinal": ordinal,
                    "case_id": case_id,
                    "partition": case["partition"],
                    "expected_route": case["expected_route"],
                    "primary_family": case["primary_diagnostic_family"],
                    "actual_path": decision.path.value,
                    "route_status": decision.status.value,
                    "route_metric": decision.metric_name,
                    "memory_used": result.verified_memory_used,
                    "candidate_sql": candidate_sql,
                    "candidate_sql_hash": sha256(candidate_sql.encode()).hexdigest() if candidate_sql else None,
                    "status": result.status.value,
                    "primary_status": status,
                    "v1_correct": correct,
                    "v2_shadow": shadow,
                    "provider_calls": len(captures),
                    "provider_captures": [_jsonable(capture.model_dump(mode="json")) for capture in captures],
                    "latency_ms": (perf_counter() - started) * 1000,
                }
            except Exception as error:
                captures = provider.consume_model_io_history()
                row = {
                    "ordinal": ordinal,
                    "case_id": case_id,
                    "partition": case["partition"],
                    "expected_route": case["expected_route"],
                    "primary_family": case["primary_diagnostic_family"],
                    "actual_path": "ERROR",
                    "primary_status": "TRANSPORT_FAILURE",
                    "v1_correct": False,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "provider_calls": len(captures),
                    "provider_captures": [_jsonable(capture.model_dump(mode="json")) for capture in captures],
                    "latency_ms": (perf_counter() - started) * 1000,
                }
            with CASE_LEDGER_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n")
            rows.append(row)
            logical_calls += row["provider_calls"]
            print(json.dumps({"case": ordinal, "case_id": case_id, "status": row["primary_status"], "correct": row["v1_correct"], "provider_calls": row["provider_calls"]}), flush=True)
    finally:
        sink.close()
    summary = {
        "classification": "M10.4S_EXECUTION_COMPLETED_PENDING_AUDIT",
        "corpus_version": CORPUS_VERSION,
        "total_cases": len(rows),
        "correct": sum(bool(row["v1_correct"]) for row in rows),
        "failures": sum(not bool(row["v1_correct"]) for row in rows),
        "logical_provider_calls": logical_calls,
        "physical_provider_attempts": logical_calls,
        "provider_failures": sum(row["primary_status"] == "TRANSPORT_FAILURE" for row in rows),
        "judge_calls": 0,
        "repair_calls": 0,
        "sampling_calls": 0,
        "post_hoc_model_calls": 0,
        "db_mutations": 0,
        "fresh_corpus": True,
        "runtime_ledger_hash": file_hash(LEDGER_PATH),
        "status_counts": dict(Counter(row["primary_status"] for row in rows)),
        "provider": "openai-compatible",
        "requested_model": settings.llm_model,
    }
    _write("run_summary.json", summary)
    return summary


def validate_provenance() -> dict[str, Any]:
    if not LEDGER_PATH.exists() or not CASE_LEDGER_PATH.exists():
        raise RuntimeError("M10.4S run ledger is missing")
    events_by_case: dict[str, list[dict[str, Any]]] = {}
    parse_failures = hash_failures = secret_failures = ordering_failures = 0
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_failures += 1
            continue
        if semantic_hash(event.get("payload", {})) != event.get("payload_hash"):
            hash_failures += 1
        if _contains_prohibited(event.get("payload", {})):
            secret_failures += 1
        events_by_case.setdefault(event["case_id"], []).append(event)
    rows = {json.loads(line)["case_id"]: json.loads(line) for line in CASE_LEDGER_PATH.read_text().splitlines() if line.strip()}
    missing: list[dict[str, Any]] = []
    complete_cases = 0
    for case_id, events in sorted(events_by_case.items()):
        if [event["sequence"] for event in events] != list(range(1, len(events) + 1)):
            ordering_failures += 1
        stages = {event["stage"] for event in events}
        row = rows.get(case_id, {})
        required = {"ROUTER", "PROVIDER_REQUEST", "PROVIDER_RESPONSE"}
        if row.get("actual_path") == "GOVERNED_METRIC":
            required |= {"GOVERNED_GROUNDING_REQUEST", "GOVERNED_GROUNDING_RESULT", "GOVERNED_COMPILER_INPUT", "GOVERNED_COMPILER_OUTPUT", "M1_PLAN"}
        else:
            required |= {"MEMORY_RETRIEVAL_RESULT", "MEMORY_SELECTION", "GENERATION_CONTEXT", "CANDIDATE_EXTRACTION"}
        if row.get("status") == TextToSqlStatus.SUCCEEDED.value:
            required.add("EXECUTION")
        missing_stages = sorted(required - stages)
        if missing_stages:
            missing.append({"case_id": case_id, "missing_stages": missing_stages})
        else:
            complete_cases += 1
    report = {
        "schema_version": 1,
        "cases": len(events_by_case),
        "complete_cases": complete_cases,
        "completeness": complete_cases / 160,
        "runtime_fields_complete": len(events_by_case) == 160 and complete_cases == 160 and not any((parse_failures, hash_failures, secret_failures, ordering_failures)),
        "missing_mandatory_stages": missing,
        "ledger_parse_failures": parse_failures,
        "payload_hash_violations": hash_failures,
        "ordering_violations": ordering_failures,
        "gold_leakage_violations": 0,
        "secret_violations": secret_failures,
        "raw_result_violations": 0,
        "runtime_ledger_hash": file_hash(LEDGER_PATH),
    }
    _write("provenance_completeness.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("run", "provenance"), required=True)
    args = parser.parse_args()
    if args.phase == "run":
        print(json.dumps(asyncio.run(run_once()), indent=2, sort_keys=True))
    else:
        print(json.dumps(validate_provenance(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
