# ruff: noqa: E501

"""M10R reference gate and one-pass benchmark runner.

The ``refs`` phase is the hard gate: it validates every frozen v2 reference
through the unchanged M1/PostgreSQL path.  The ``benchmark`` phase is allowed
only after that gate has produced 200 reference results.
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
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.semantics.catalog import build_m3_catalog
from app.semantics.routing import GovernedMetricRouteService, GovernedRoutePath
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlStatus
from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m10_corpus import stable_hash
from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.result_binding_protocol_v2 import (
    BindingStatus,
    ResultBindingSpecV2,
    SlotBindingSpecV2,
    bind_generated_result_v2,
)
from evaluation.result_equivalence_contract import ResultEquivalenceContract
from evaluation.result_snapshot import restore_query_execution, snapshot_query_execution
from evaluation.versioned_result_evaluator import (
    EvaluationComparisonRequest,
    EvaluatorMode,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
RESULT_ROOT = ROOT / "evaluation" / "results" / "m10r" / "clean-rebaseline-20260904"
STARTING_HEAD = "46e4e059eac8d7051e1ee7e23821ad4858b96ea9"
V1_HASH = "aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb"
V2_HASH = "ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27ecda3b78fb8e"
COMPARATOR_HASH = "677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


def _write(name: str, value: Any) -> str:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    path = RESULT_ROOT / name
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def _execution_shape(execution: QueryExecution) -> dict[str, Any]:
    return {
        "columns": execution.columns,
        "projection_count": len(execution.columns),
        "row_count": execution.row_count,
        "truncated": execution.truncated,
        "rows": _jsonable(execution.rows),
    }


def _execution_hash(execution: QueryExecution) -> str:
    return stable_hash(_execution_shape(execution))


def _observe_db_protocol(engine: Any, settings: Any) -> dict[str, Any]:
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            row = connection.exec_driver_sql("SELECT version(), current_database(), current_user, current_schema(), current_setting('transaction_read_only')").one()
            return {
                "postgres_version": row[0],
                "database_identity": row[1],
                "reader_identity": row[2],
                "schema": row[3],
                "transaction_read_only": row[4],
                "migration_head_expected": "0001_commerce_schema",
                "m1_timeout_ms": settings.statement_timeout_ms,
                "m1_max_rows": settings.max_result_rows,
                "m1_max_plan_rows": settings.max_plan_rows,
                "m1_max_plan_cost": settings.max_plan_cost,
                "setup_db_mutations": 0,
                "benchmark_db_mutations": 0,
            }


def validate_references() -> dict[str, Any]:
    cases = _load("m10r_cases_v2.json")["cases"]
    if len(cases) != 200:
        raise RuntimeError("M10R corpus is not exactly 200 cases")
    settings = get_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    records: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    parse_count = accepted_count = execution_count = 0
    for case in cases:
        sql = case["reference_sql_variants"][0]
        plan_or_failure = safety.plan(SqlCandidate(sql=sql, source=CandidateSource.INTERNAL, correlation_id=case["case_id"]))
        if isinstance(plan_or_failure, QueryPlan):
            parse_count += 1
            accepted_count += 1
            execution = safety.execute(plan_or_failure)
            if isinstance(execution, QueryExecution):
                execution_count += 1
                results[case["case_id"]] = execution
                records.append({"case_id": case["case_id"], "variant_index": 0, "sql_hash": sha256(sql.encode()).hexdigest(), "column_count": len(execution.columns), "row_count": execution.row_count, "result_hash": _execution_hash(execution), "execution_provenance": "current_m1_reader_read_only"})
                continue
            detail = getattr(execution, "error", None) or type(execution).__name__
        else:
            detail = getattr(getattr(plan_or_failure, "rejection", None), "code", None) or getattr(plan_or_failure, "error", None) or type(plan_or_failure).__name__
        records.append({"case_id": case["case_id"], "variant_index": 0, "sql_hash": sha256(sql.encode()).hexdigest(), "failure": str(detail)})
        _write("reference_validation.json", {"parse": f"{parse_count}/200", "m1_acceptance": f"{accepted_count}/200", "execution": f"{execution_count}/200", "failed_case": case["case_id"], "records": records})
        raise RuntimeError(f"M10R frozen reference failed M1/execution: {case['case_id']} {detail}")
    protocol = _observe_db_protocol(engine, settings)
    reference_results = {key: snapshot_query_execution(value) for key, value in results.items()}
    reference_results_hash = stable_hash(records)
    _write("reference_results.json", {"records": records, "results": reference_results})
    summary = {"parse": f"{parse_count}/200", "m1_acceptance": f"{accepted_count}/200", "execution": f"{execution_count}/200", "reference_result_count": len(results), "reference_results_hash": reference_results_hash, "db_protocol": protocol, "v1_hash": V1_HASH, "v2_hash": V2_HASH, "comparator_hash": COMPARATOR_HASH, "provider_calls_before_reference_gate": 0, "architecture_cases_consumed_before_reference_gate": 0}
    _write("reference_validation.json", summary)
    return summary


def _tupleify(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tupleify(item) for item in value)
    if isinstance(value, dict):
        return {key: _tupleify(item) for key, item in value.items()}
    return value


def _contract_and_spec(case: dict[str, Any]) -> tuple[ResultEquivalenceContract, ResultBindingSpecV2]:
    contract = ResultEquivalenceContract.from_dict(case["contract"])
    data = case["binding_spec"]
    spec = ResultBindingSpecV2(
        binding_protocol_version=data["binding_protocol_version"],
        contract_instance_hash=data["contract_instance_hash"],
        semantic_catalog_version=data["semantic_catalog_version"],
        schema_semantic_map_version=data["schema_semantic_map_version"],
        slot_binding_specs=tuple(SlotBindingSpecV2(slot_id=item["slot_id"], semantic_identity=item["semantic_identity"], expression_fingerprint=_tupleify(item["expression_fingerprint"]), allowed_expression_class=item["allowed_expression_class"], required=bool(item["required"])) for item in data["slot_binding_specs"]),
    )
    return contract, spec


def _settings_for_m10r() -> Any:
    settings = get_settings()
    credential = os.getenv("DECISION_SQL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not credential:
        raise RuntimeError("provider credential is unavailable")
    return settings.model_copy(update={"llm_api_key": credential, "llm_model": "gpt-5.6-luna", "llm_reasoning_effort": None, "llm_temperature": None, "governed_metrics_mode": GovernedMetricsMode.ON, "verified_query_memory_mode": VerifiedMemoryMode.ON, "eval_capture_model_io": True})


def _build_runtime(settings: Any) -> tuple[Any, OpenAICompatibleProvider]:
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    context = SchemaContextResolver(safety.catalog, top_k=settings.schema_top_k, max_tables=settings.max_context_tables, max_columns_per_table=settings.max_columns_per_table, relationship_depth=settings.relationship_depth)
    provider = OpenAICompatibleProvider(settings)
    from app.text_to_sql.service import TextToSqlService

    direct = TextToSqlService(context, provider, safety, context_mode=SchemaContextMode.FULL_COMPACT)
    memory = VerifiedMemoryRuntime(direct, build_memory_corpus(), settings=settings, expected_corpus_hash=FROZEN_MEMORY_CORPUS_HASH)
    route = GovernedMetricRouteService(direct, provider, safety, catalog=build_m3_catalog(safety.catalog), mode=GovernedMetricsMode.ON, verified_memory=memory)
    return route, provider


def _primary(result: Any, reference: QueryExecution) -> tuple[str, bool]:
    if result is None or result.status in (TextToSqlStatus.SQL_GENERATION_ERROR,):
        return "GENERATION_FAILURE", False
    if result.status is TextToSqlStatus.SUCCEEDED and isinstance(result.execution, QueryExecution):
        outcome = evaluate(EvaluationComparisonRequest(result.execution, (reference,), EvaluatorMode.V1))
        return ("CORRECT" if outcome.equivalent else "RESULT_MISMATCH", outcome.equivalent)
    if result.status is TextToSqlStatus.PLAN_REJECTED:
        return ("GENERATION_FAILURE" if result.plan_failure and result.plan_failure.status.value == "SQL_PARSE_ERROR" else "M1_REJECTED", False)
    if result.status is TextToSqlStatus.EXECUTION_ERROR:
        return "EXECUTION_FAILURE", False
    return "GENERATION_FAILURE", False


async def run_benchmark() -> dict[str, Any]:
    cases = _load("m10r_cases_v2.json")["cases"]
    manifest = _load("m10r_execution_manifest_v2.json")
    references = json.loads((RESULT_ROOT / "reference_results.json").read_text())["results"]
    reference_by_id = {key: restore_query_execution(value) for key, value in references.items()}
    settings = _settings_for_m10r()
    route, provider = _build_runtime(settings)
    provider_calls = 0
    rows: list[dict[str, Any]] = []
    by_id = {case["case_id"]: case for case in cases}
    for ordinal, case_id in enumerate(manifest["ordered_case_ids"], start=1):
        case = by_id[case_id]
        started = perf_counter()
        provider.consume_model_io_history()
        try:
            decision = await route.run(TextToSqlRequest(question=case["question"], correlation_id=case_id, execute=True))
            result = decision.user_result
            reference = reference_by_id[case_id]
            status, correct = _primary(result, reference)
            captures = provider.consume_model_io_history()
            candidate_sql = result.candidate.sql if result.candidate else (decision.governed_candidate.sql if decision.governed_candidate else None)
            shadow: dict[str, Any] = {"status": "V2_BINDING_UNAVAILABLE"}
            if candidate_sql and isinstance(result.execution, QueryExecution):
                contract, spec = _contract_and_spec(case)
                bound = bind_generated_result_v2(candidate_sql, result.execution, spec, contract, build_schema_semantic_map())
                if all(item.binding_status is BindingStatus.BOUND for item in bound.report.bindings):
                    reference_bound = bind_generated_result_v2(case["reference_sql_variants"][0], reference, spec, contract, build_schema_semantic_map())
                    if all(item.binding_status is BindingStatus.BOUND for item in reference_bound.report.bindings):
                        outcome = evaluate(EvaluationComparisonRequest(bound.result, (reference_bound.result,), EvaluatorMode.V2, result_contract=contract, contract_id=case["contract_id"]))
                        shadow = {"status": "V2_EVALUATED", "equivalent": outcome.equivalent, "outcome": outcome.to_dict()}
                    else:
                        shadow = {"status": "V2_BINDING_UNAVAILABLE", "reason": "reference binding unavailable"}
                else:
                    shadow = {"status": "V2_BINDING_UNAVAILABLE", "binding_statuses": [item.binding_status.value for item in bound.report.bindings]}
            calls = len(captures) or result.provider_calls_attempted
            provider_calls += calls
            row = {"ordinal": ordinal, "case_id": case_id, "family": case["family"], "split": case["split"], "expected_route": case["expected_route"], "actual_path": decision.path.value, "route_status": decision.status.value, "route_metric": decision.metric_name, "memory_used": result.verified_memory_used, "candidate_sql": candidate_sql, "candidate_sql_hash": sha256(candidate_sql.encode()).hexdigest() if candidate_sql else None, "status": result.status.value, "primary_status": status, "v1_correct": correct, "v2_shadow": shadow, "provider_calls": calls, "provider_captures": [_jsonable(capture.model_dump(mode="json")) for capture in captures], "latency_ms": (perf_counter() - started) * 1000}
        except Exception as error:
            captures = provider.consume_model_io_history()
            calls = len(captures)
            provider_calls += calls
            row = {"ordinal": ordinal, "case_id": case_id, "family": case["family"], "split": case["split"], "expected_route": case["expected_route"], "actual_path": "ERROR", "primary_status": "PROVIDER_FAILURE", "v1_correct": False, "error_type": type(error).__name__, "error": str(error), "provider_calls": calls, "provider_captures": [_jsonable(capture.model_dump(mode="json")) for capture in captures], "latency_ms": (perf_counter() - started) * 1000}
        with (RESULT_ROOT / "case_ledger.jsonl").open("a") as ledger:
            ledger.write(json.dumps(_jsonable(row), ensure_ascii=False, sort_keys=True) + "\n")
        rows.append(row)
        print(json.dumps({"case": ordinal, "case_id": case_id, "primary_status": row["primary_status"], "v1_correct": row["v1_correct"], "provider_calls": row.get("provider_calls", 0)}), flush=True)
    summary = summarize(cases, rows, provider_calls)
    _write("final_summary.json", summary)
    return summary


def summarize(cases: list[dict[str, Any]], rows: list[dict[str, Any]], provider_calls: int) -> dict[str, Any]:
    primary_correct = sum(bool(row.get("v1_correct")) for row in rows)
    counts = Counter(row["primary_status"] for row in rows)
    family: dict[str, dict[str, int]] = {}
    for name in sorted({case["family"] for case in cases}):
        subset = [row for row in rows if row["family"] == name]
        family[name] = {"total": len(subset), "correct": sum(bool(row.get("v1_correct")) for row in subset)}
    shadow_status = Counter((row.get("v2_shadow") or {}).get("status", "V2_BINDING_UNAVAILABLE") for row in rows)
    matrix: Counter[str] = Counter()
    for row in rows:
        shadow = row.get("v2_shadow") or {}
        if shadow.get("status") == "V2_EVALUATED":
            matrix["V1_CORRECT_V2_CORRECT" if row["v1_correct"] and shadow["equivalent"] else "V1_INCORRECT_V2_CORRECT" if shadow["equivalent"] else "V1_CORRECT_V2_INCORRECT" if row["v1_correct"] else "V1_INCORRECT_V2_INCORRECT"] += 1
    actual_governed = sum(row.get("actual_path") == GovernedRoutePath.GOVERNED_METRIC.value for row in rows)
    expected_governed = sum(case["expected_route"] == "GOVERNED" for case in cases)
    route_accuracy = sum((row.get("actual_path") == GovernedRoutePath.GOVERNED_METRIC.value) == (row.get("expected_route") == "GOVERNED") for row in rows)
    return {"classification": "M10R_COMPLETED_PENDING_RESIDUAL_CLASSIFICATION", "corpus_id": "decisionsql-m10-clean-rebaseline", "corpus_version": "m10-clean-rebaseline-v2", "total": len(rows), "primary_correct": primary_correct, "primary_rate": primary_correct / len(rows) if rows else 0, "primary_failures": len(rows) - primary_correct, "status_counts": dict(counts), "family": family, "dev_correct": sum(row.get("split") == "dev" and row.get("v1_correct") for row in rows), "holdout_correct": sum(row.get("split") == "holdout" and row.get("v1_correct") for row in rows), "expected_governed": expected_governed, "expected_direct": len(cases) - expected_governed, "actual_governed": actual_governed, "actual_direct_or_fallback": len(rows) - actual_governed, "route_accuracy": route_accuracy / len(rows) if rows else 0, "provider_calls": provider_calls, "v2_status_counts": dict(shadow_status), "v1_v2_outcome_matrix": dict(matrix), "v2_evaluated": sum(matrix.values()), "db_mutations": 0, "repair_calls": 0, "judge_calls": 0, "sampling_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("refs", "benchmark"), required=True)
    args = parser.parse_args()
    if args.phase == "refs":
        print(json.dumps(validate_references(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        payload = asyncio.run(run_benchmark())
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
