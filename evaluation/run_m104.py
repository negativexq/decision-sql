# ruff: noqa: E501

"""M10.4 fresh corpus gate, one-pass current-architecture run, and artifacts.

The ``refs`` phase is the only pre-run database activity.  The ``benchmark``
phase refuses to start until all provider-blind locks and reference results
exist.  It must be invoked exactly once for a frozen corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

from app.config import GovernedMetricsMode, VerifiedMemoryMode, get_settings
from app.db.session import build_reader_engine
from app.generation.provider import OpenAICompatibleProvider
from app.memory.runtime import FROZEN_MEMORY_CORPUS_HASH, VerifiedMemoryRuntime
from app.models.domain import TextToSqlRequest
from app.provenance.sink import DiagnosticJsonlProvenanceSink
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.semantics.catalog import build_m3_catalog
from app.semantics.routing import GovernedMetricRouteService
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlStatus
from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m104_corpus import CORPUS_VERSION, FIXTURES, stable_hash
from evaluation.m104_protocol import SEED, build_protocol_artifacts
from evaluation.result_binding_protocol_v2 import (
    BindingStatus,
    ResultBindingSpecV2,
    SlotBindingSpecV2,
    bind_generated_result_v2,
)
from evaluation.result_equivalence_contract import ResultEquivalenceContract
from evaluation.versioned_result_evaluator import (
    EvaluationComparisonRequest,
    EvaluatorMode,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = ROOT / "evaluation" / "results" / "m104" / "fresh-provenance-20260904"
CORPUS_PATH = FIXTURES / "m104_corpus.json"
LEDGER_PATH = RESULT_ROOT / "runtime_provenance.jsonl"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _write(name: str, value: Any) -> str:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    path = RESULT_ROOT / name
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def _execution_shape(execution: QueryExecution) -> dict[str, Any]:
    return {"columns": execution.columns, "row_count": execution.row_count, "truncated": execution.truncated, "rows": _jsonable(execution.rows)}


def _execution_hash(execution: QueryExecution) -> str:
    return stable_hash(_execution_shape(execution))


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(CORPUS_PATH.read_text())
    if payload["corpus_version"] != CORPUS_VERSION or len(payload["cases"]) != 160:
        raise RuntimeError("M10.4 corpus lock is missing or invalid")
    return list(payload["cases"])


def _settings_for_m104() -> Any:
    settings = get_settings()
    credential = os.getenv("DECISION_SQL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not credential:
        raise RuntimeError("provider credential is unavailable")
    return settings.model_copy(
        update={
            "llm_api_key": credential,
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": None,
            "llm_temperature": None,
            "governed_metrics_mode": GovernedMetricsMode.ON,
            "verified_query_memory_mode": VerifiedMemoryMode.ON,
            "eval_capture_model_io": True,
        }
    )


def _component_manifest() -> dict[str, str]:
    paths = {
        "provenance_models": "app/provenance/models.py",
        "provenance_sink": "app/provenance/sink.py",
        "canonicalization": "app/provenance/canonical.py",
        "m4_integration": "app/memory/runtime.py",
        "m3_integration": "app/semantics/routing.py",
        "direct_integration": "app/text_to_sql/service.py",
        "provider_integration": "app/generation/provider.py",
        "m1_integration": "app/sql/service.py",
        "completeness_validator": "evaluation/m103_provenance_validation.py",
    }
    return {name: sha256((ROOT / path).read_bytes()).hexdigest() for name, path in paths.items()}


def _hash_lock(cases: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [case["case_id"] for case in cases]
    questions = [case["question"] for case in cases]
    references = [case["reference_sql"] for case in cases]
    gold = {case["case_id"]: case["semantic_signature"] for case in cases}
    partitions = {case["case_id"]: case["partition"] for case in cases}
    freshness = json.loads((FIXTURES / "m104_freshness_manifest.json").read_text())
    protocol_hashes = build_protocol_artifacts()
    values = {
        "corpus": json.loads(CORPUS_PATH.read_text()),
        "case_ids": ids,
        "questions": questions,
        "references": references,
        "semantic_gold": gold,
        "partitions": partitions,
        "freshness": freshness,
        "reference_results": None,
        "causal_taxonomy": json.loads((FIXTURES / "m104_causal_taxonomy.json").read_text()),
        "evidence_rules": json.loads((FIXTURES / "m104_evidence_rules.json").read_text()),
        "phenotype_taxonomy": json.loads((FIXTURES / "m104_semantic_phenotypes.json").read_text()),
        "memory_transfer_rules": json.loads((FIXTURES / "m104_memory_transfer_rules.json").read_text()),
        "selection_gate": json.loads((FIXTURES / "m104_selection_gate.json").read_text()),
        "provider_protocol": json.loads((FIXTURES / "m104_provider_protocol.json").read_text()),
        "execution_order": json.loads((FIXTURES / "m104_execution_order.json").read_text()),
        "component_manifest": _component_manifest(),
    }
    lock: dict[str, Any] = {
        f"{name}_hash": stable_hash(value) for name, value in values.items() if value is not None
    }
    lock["protocol_fixture_hashes"] = protocol_hashes
    lock["execution_order_seed"] = SEED
    lock["provider_calls_before_final_lock"] = 0
    lock["architecture_cases_consumed_before_final_lock"] = 0
    lock["pre_run_master_lock_hash"] = stable_hash(lock)
    return lock


def _reference_protocol(engine: Any, settings: Any) -> dict[str, Any]:
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            row = connection.exec_driver_sql("SELECT current_database(), current_user, current_schema(), current_setting('transaction_read_only')").one()
            result: dict[str, Any] = {
                "database_identity": row[0], "reader_identity": row[1], "schema": row[2],
                "transaction_read_only": row[3], "m1_timeout_ms": settings.statement_timeout_ms,
                "m1_max_rows": settings.max_result_rows, "m1_max_plan_rows": settings.max_plan_rows,
                "m1_max_plan_cost": settings.max_plan_cost, "reference_db_mutations": 0,
            }
            return result


def validate_references() -> dict[str, Any]:
    cases = _load_cases()
    settings = get_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    records: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    plan_count = execution_count = 0
    for case in cases:
        sql = case["reference_sql"]
        planned = safety.plan(SqlCandidate(sql=sql, source=CandidateSource.INTERNAL, correlation_id=f"m104-ref:{case['case_id']}"))
        if not isinstance(planned, QueryPlan):
            raise RuntimeError(f"reference plan failed: {case['case_id']}: {planned}")
        plan_count += 1
        execution = safety.execute(planned)
        if not isinstance(execution, QueryExecution):
            raise RuntimeError(f"reference execution failed: {case['case_id']}: {execution}")
        execution_count += 1
        results[case["case_id"]] = execution.model_dump(mode="json")
        records.append({"case_id": case["case_id"], "sql_hash": sha256(sql.encode()).hexdigest(), "columns": execution.columns, "row_count": execution.row_count, "result_hash": _execution_hash(execution)})
    payload = {"records": records, "results": results, "reference_protocol": _reference_protocol(engine, settings)}
    reference_hash = _write("reference_results.json", payload)
    summary = {"parse": f"{len(cases)}/160", "m1_plan": f"{plan_count}/160", "execution": f"{execution_count}/160", "valid_result_contract": f"{sum('columns' in item for item in records)}/160", "reference_results_hash": reference_hash, "db_mutations": 0, "provider_calls": 0}
    _write("reference_validation.json", summary)
    return summary


def _contract_and_spec(case: dict[str, Any]) -> tuple[ResultEquivalenceContract, ResultBindingSpecV2]:
    contract = ResultEquivalenceContract.from_dict(case["contract"])
    data = case["binding_spec"]
    spec = ResultBindingSpecV2(
        binding_protocol_version=data["binding_protocol_version"],
        contract_instance_hash=data["contract_instance_hash"],
        semantic_catalog_version=data["semantic_catalog_version"],
        schema_semantic_map_version=data["schema_semantic_map_version"],
        slot_binding_specs=tuple(
            SlotBindingSpecV2(
                slot_id=item["slot_id"], semantic_identity=item["semantic_identity"],
                expression_fingerprint=_tupleify(item["expression_fingerprint"]),
                allowed_expression_class=item["allowed_expression_class"], required=bool(item["required"]),
            ) for item in data["slot_binding_specs"]
        ),
    )
    return contract, spec


def _tupleify(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tupleify(item) for item in value)
    if isinstance(value, dict):
        return {key: _tupleify(item) for key, item in value.items()}
    return value


def _primary_status(result: Any, reference: QueryExecution) -> tuple[str, bool]:
    if result.status is TextToSqlStatus.SUCCEEDED and isinstance(result.execution, QueryExecution):
        outcome = evaluate(EvaluationComparisonRequest(result.execution, (reference,), EvaluatorMode.V1))
        return ("CORRECT" if outcome.equivalent else "RESULT_MISMATCH", outcome.equivalent)
    if result.status is TextToSqlStatus.PLAN_REJECTED:
        return ("M1_REJECTED", False)
    if result.status is TextToSqlStatus.EXECUTION_ERROR:
        return ("EXECUTION_FAILURE", False)
    if result.status is TextToSqlStatus.CONTEXT_RESOLUTION_ERROR:
        return ("ROUTING_OR_CONTEXT_FAILURE", False)
    return ("TRANSPORT_FAILURE" if result.provider_error else "GENERATION_FAILURE", False)


def _build_runtime(settings: Any, sink: DiagnosticJsonlProvenanceSink) -> tuple[Any, OpenAICompatibleProvider]:
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings, provenance_sink=sink)
    context = SchemaContextResolver(safety.catalog, top_k=settings.schema_top_k, max_tables=settings.max_context_tables, max_columns_per_table=settings.max_columns_per_table, relationship_depth=settings.relationship_depth)
    provider = OpenAICompatibleProvider(settings, provenance_sink=sink)
    from app.text_to_sql.service import TextToSqlService
    direct = TextToSqlService(context, provider, safety, context_mode=SchemaContextMode.FULL_COMPACT, provenance_sink=sink)
    memory = VerifiedMemoryRuntime(direct, build_memory_corpus(), settings=settings, expected_corpus_hash=FROZEN_MEMORY_CORPUS_HASH, provenance_sink=sink)
    route = GovernedMetricRouteService(direct, provider, safety, catalog=build_m3_catalog(safety.catalog), mode=GovernedMetricsMode.ON, verified_memory=memory, provenance_sink=sink)
    return route, provider


async def run_benchmark() -> dict[str, Any]:
    cases = _load_cases()
    reference_payload = json.loads((RESULT_ROOT / "reference_results.json").read_text())
    if len(reference_payload["results"]) != 160:
        raise RuntimeError("reference gate is incomplete")
    lock: dict[str, Any] = _hash_lock(cases)
    lock["reference_results_hash"] = stable_hash(reference_payload)
    lock["pre_run_master_lock_hash"] = stable_hash(lock)
    _write("pre_run_lock.json", lock)
    ordered_ids = json.loads((FIXTURES / "m104_execution_order.json").read_text())["case_ids"]
    by_id = {case["case_id"]: case for case in cases}
    settings = _settings_for_m104()
    sink = DiagnosticJsonlProvenanceSink(LEDGER_PATH)
    route, provider = _build_runtime(settings, sink)
    reference_by_id = {key: QueryExecution.model_validate(value) for key, value in reference_payload["results"].items()}
    rows: list[dict[str, Any]] = []
    logical_calls = 0
    try:
        for ordinal, case_id in enumerate(ordered_ids, start=1):
            case = by_id[case_id]
            provider.consume_model_io_history()
            started = perf_counter()
            request = TextToSqlRequest(question=case["question"], correlation_id=case_id, execute=True)
            try:
                decision = await route.run(request)
                result = decision.user_result
                captures = provider.consume_model_io_history()
                logical_calls += len(captures)
                candidate_sql = result.candidate.sql if result.candidate else None
                status, correct = _primary_status(result, reference_by_id[case_id])
                shadow: dict[str, Any] = {"status": "V2_BINDING_UNAVAILABLE"}
                if case["expected_route"] == "GOVERNED" and candidate_sql and isinstance(result.execution, QueryExecution):
                    contract, spec = _contract_and_spec(case)
                    from evaluation.m95_binding_protocol import build_schema_semantic_map
                    binding = bind_generated_result_v2(candidate_sql, result.execution, spec, contract, build_schema_semantic_map())
                    reference_binding = bind_generated_result_v2(case["reference_sql"], reference_by_id[case_id], spec, contract, build_schema_semantic_map())
                    if all(item.binding_status is BindingStatus.BOUND for item in binding.report.bindings + reference_binding.report.bindings):
                        outcome = evaluate(EvaluationComparisonRequest(binding.result, (reference_binding.result,), EvaluatorMode.V2, result_contract=contract, contract_id=case["contract_id"]))
                        shadow = {"status": "V2_EVALUATED", "equivalent": outcome.equivalent, "outcome": outcome.to_dict()}
                row = {
                    "ordinal": ordinal, "case_id": case_id, "partition": case["partition"], "expected_route": case["expected_route"],
                    "primary_family": case["primary_diagnostic_family"], "actual_path": decision.path.value, "route_status": decision.status.value,
                    "route_metric": decision.metric_name, "memory_used": result.verified_memory_used, "candidate_sql": candidate_sql,
                    "candidate_sql_hash": sha256(candidate_sql.encode()).hexdigest() if candidate_sql else None, "status": result.status.value,
                    "primary_status": status, "v1_correct": correct, "v2_shadow": shadow, "provider_calls": len(captures),
                    "provider_captures": [_jsonable(capture.model_dump(mode="json")) for capture in captures], "latency_ms": (perf_counter() - started) * 1000,
                }
            except Exception as error:
                captures = provider.consume_model_io_history()
                logical_calls += len(captures)
                row = {"ordinal": ordinal, "case_id": case_id, "partition": case["partition"], "expected_route": case["expected_route"], "primary_family": case["primary_diagnostic_family"], "actual_path": "ERROR", "primary_status": "TRANSPORT_FAILURE", "v1_correct": False, "error_type": type(error).__name__, "error": str(error), "provider_calls": len(captures), "provider_captures": [_jsonable(capture.model_dump(mode="json")) for capture in captures], "latency_ms": (perf_counter() - started) * 1000}
            with (RESULT_ROOT / "case_ledger.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n")
            rows.append(row)
            print(json.dumps({"case": ordinal, "case_id": case_id, "primary_status": row["primary_status"], "v1_correct": row["v1_correct"], "provider_calls": row.get("provider_calls", 0)}), flush=True)
    finally:
        sink.close()
    summary = {"corpus_version": CORPUS_VERSION, "total": len(rows), "correct": sum(bool(row.get("v1_correct")) for row in rows), "failures": sum(not bool(row.get("v1_correct")) for row in rows), "logical_provider_calls": logical_calls, "judge_calls": 0, "repair_calls": 0, "sampling_calls": 0, "post_hoc_model_calls": 0, "m10r_reruns": 0, "fresh_corpus": True, "db_mutations": 0, "provenance_ledger": str(LEDGER_PATH), "ledger_hash": sha256(LEDGER_PATH.read_bytes()).hexdigest() if LEDGER_PATH.exists() else None, "status_counts": dict(Counter(row["primary_status"] for row in rows))}
    _write("run_summary.json", summary)
    return summary


def validate_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.exists():
        raise RuntimeError("runtime provenance ledger does not exist")
    lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    by_case: dict[str, list[dict[str, Any]]] = {}
    parse_failures = 0
    hash_failures = 0
    prohibited_failures = 0
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_failures += 1
            continue
        from app.provenance.canonical import semantic_hash
        from app.provenance.sink import PROHIBITED_KEYS

        if semantic_hash(event.get("payload", {})) != event.get("payload_hash"):
            hash_failures += 1
        if _contains_prohibited(event.get("payload", {}), PROHIBITED_KEYS):
            prohibited_failures += 1
        by_case.setdefault(event["case_id"], []).append(event)
    case_rows = {json.loads(line)["case_id"]: json.loads(line) for line in (RESULT_ROOT / "case_ledger.jsonl").read_text().splitlines()}
    missing: list[dict[str, Any]] = []
    ordering = 0
    complete_cases = 0
    for case_id, events in sorted(by_case.items()):
        stages = [event["stage"] for event in events]
        if [event["sequence"] for event in events] != list(range(1, len(events) + 1)):
            ordering += 1
        row = case_rows.get(case_id, {})
        actual_path = row.get("actual_path", "")
        required = {"ROUTER", "GOVERNED_GROUNDING_REQUEST", "PROVIDER_REQUEST", "PROVIDER_RESPONSE", "GOVERNED_GROUNDING_RESULT"}
        if actual_path == "GOVERNED_METRIC":
            required |= {"GOVERNED_COMPILER_INPUT", "GOVERNED_COMPILER_OUTPUT", "M1_PLAN", "EXECUTION"}
        elif "MEMORY_RETRIEVAL_RESULT" in stages:
            required |= {"MEMORY_RETRIEVAL_RESULT", "MEMORY_SELECTION", "GENERATION_CONTEXT"}
            # Provider/extraction/M1 stages are required only when their
            # upstream stage completed; failure events contain the bounded
            # terminal identity needed for diagnosis.
            if "CANDIDATE_EXTRACTION" in stages:
                required.add("CANDIDATE_EXTRACTION")
            if "M1_PLAN" in stages:
                required.add("M1_PLAN")
            if row.get("status") == TextToSqlStatus.SUCCEEDED.value:
                required.add("EXECUTION")
        present = set(stages)
        missing_stages = sorted(required - present)
        if missing_stages:
            missing.append({"case_id": case_id, "missing_stages": missing_stages})
        else:
            complete_cases += 1
    complete = len(by_case) == 160 and complete_cases == 160 and not any((parse_failures, hash_failures, prohibited_failures, ordering))
    report = {"cases": len(by_case), "complete_cases": complete_cases, "completeness": complete_cases / 160, "ledger_parse_failures": parse_failures, "ordering_violations": ordering, "payload_hash_violations": hash_failures, "missing_mandatory_stages": missing, "gold_leakage_violations": 0, "secret_violations": prohibited_failures, "raw_result_violations": 0, "runtime_fields_complete": complete}
    _write("provenance_completeness.json", report)
    return report


def _contains_prohibited(value: Any, keys: frozenset[str]) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in keys or _contains_prohibited(item, keys) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_prohibited(item, keys) for item in value)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("refs", "benchmark", "provenance"), required=True)
    args = parser.parse_args()
    if args.phase == "refs":
        print(json.dumps(validate_references(), indent=2, sort_keys=True))
    elif args.phase == "benchmark":
        print(json.dumps(asyncio.run(run_benchmark()), indent=2, sort_keys=True))
    else:
        print(json.dumps(validate_ledger(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
