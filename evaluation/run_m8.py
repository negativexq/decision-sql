# ruff: noqa: E501

"""Run the bounded M8 false-governed routing audit.

``validate`` is provider-free and freezes the exact 20-case slice.  The only
provider-enabled phase is ``counterfactual``: it sends those cases through the
existing M4 direct path once each.  No router, M3 grounding, P0, or full M7
benchmark is rerun.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import Settings, VerifiedMemoryMode, get_settings
from app.db.session import build_reader_engine
from app.generation.provider import OpenAICompatibleProvider
from app.memory.runtime import FROZEN_MEMORY_CORPUS_HASH, VerifiedMemoryRuntime
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m7_benchmark import build_benchmark
from evaluation.m8_routing_audit import (
    M4_K,
    M4_RETRIEVER_VERSION,
    M8_CORPUS_ID,
    M8_CORPUS_VERSION,
    RoutingAuditCase,
    audit_hash,
    build_audit_cases,
    manifest,
    split_hash,
    validate_audit_cases,
)
from evaluation.metrics import compare_query_results

ROOT = Path(__file__).resolve().parents[1]
M7_REFERENCE_CASES = {case.case_id: case for case in build_benchmark()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("validate", "counterfactual", "finalize"), default="validate")
    parser.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args()
    cases = build_audit_cases()
    validation = validate_audit_cases(cases)
    if not validation["passed"]:
        raise SystemExit(f"M8 audit slice invalid: {json.dumps(validation, sort_keys=True)}")
    artifact_dir = args.artifact_dir or ROOT / "evaluation" / "results" / "m8" / "audit-20260904"
    if args.phase == "validate":
        artifact_dir.mkdir(parents=True, exist_ok=False)
        audit_manifest = manifest(cases)
        _write_json(artifact_dir / "benchmark_manifest.json", audit_manifest)
        _write_json(artifact_dir / "benchmark_validation.json", validation)
        print(json.dumps({"manifest": audit_manifest, "validation": validation}, indent=2, sort_keys=True))
        return
    if not (artifact_dir / "benchmark_manifest.json").exists():
        raise SystemExit("M8 manifest is missing; run --phase validate first")
    persisted_manifest = json.loads((artifact_dir / "benchmark_manifest.json").read_text())
    if persisted_manifest.get("full_hash") != audit_hash(cases):
        raise SystemExit("M8 manifest hash does not match frozen audit cases")
    if args.phase == "counterfactual":
        output = asyncio.run(run_counterfactual(cases, artifact_dir))
        _write_json(artifact_dir / "counterfactual_summary.json", output["summary"])
        _write_json(artifact_dir / "counterfactual_analysis.json", output["analysis"])
        _write_jsonl(artifact_dir / "counterfactual_results.jsonl", output["rows"])
        print(json.dumps(output["summary"], indent=2, sort_keys=True))
        return
    if not (artifact_dir / "counterfactual_results.jsonl").exists():
        raise SystemExit("Counterfactual results are missing; run --phase counterfactual first")
    rows = [json.loads(line) for line in (artifact_dir / "counterfactual_results.jsonl").read_text().splitlines() if line]
    final = build_final_decision(cases, rows)
    _write_json(artifact_dir / "final_decision.json", final)
    print(json.dumps(final, indent=2, sort_keys=True))


async def run_counterfactual(cases: tuple[RoutingAuditCase, ...], artifact_dir: Path) -> dict[str, Any]:
    settings = _m8_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    references = _reference_results(safety, cases)
    resolver = SchemaContextResolver(safety.catalog)
    direct_service = TextToSqlService(
        resolver,
        OpenAICompatibleProvider(settings),
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
    )
    memory_settings = settings.model_copy(update={"verified_query_memory_mode": VerifiedMemoryMode.ON})
    memory = VerifiedMemoryRuntime(
        direct_service,
        build_memory_corpus(),
        settings=memory_settings,
        expected_corpus_hash=FROZEN_MEMORY_CORPUS_HASH,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        started = perf_counter()
        try:
            result = await memory.run(
                TextToSqlRequest(question=_question(case), correlation_id=case.case_id, execute=True)
            )
            row = _result_row(case, result, references[case.case_id], (perf_counter() - started) * 1000)
        except Exception as error:
            row = {
                "case_id": case.case_id,
                "family": case.family,
                "provider_protocol_success": False,
                "provider_calls_attempted": 1,
                "provider_calls_succeeded": 0,
                "provider_calls_failed": 1,
                "status": "UNHANDLED_EXCEPTION",
                "error_type": type(error).__name__,
                "error": "Counterfactual direct path raised an evaluation error.",
                "counterfactual_correct": False,
                "counterfactual_failure_cause": "PROVIDER_PROTOCOL_ERROR",
                "total_latency_ms": (perf_counter() - started) * 1000,
            }
        rows.append(row)
    summary = summarize_counterfactual(cases, rows)
    analysis = {
        "audit_hash": audit_hash(cases),
        "dev_hash": split_hash(cases, "dev"),
        "holdout_hash": split_hash(cases, "holdout"),
        "retriever_version": M4_RETRIEVER_VERSION,
        "k": M4_K,
        "provider_model": settings.llm_model,
        "reasoning_effort": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "provider_calls": sum(int(row.get("provider_calls_attempted", 0)) for row in rows),
        "rows": rows,
    }
    return {"rows": rows, "summary": summary, "analysis": analysis}


def _m8_settings() -> Settings:
    credential = os.getenv("DECISION_SQL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not credential:
        raise SystemExit("No provider credential: set DECISION_SQL_LLM_API_KEY or OPENAI_API_KEY")
    return get_settings().model_copy(
        update={
            "llm_api_key": credential,
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": None,
            "llm_temperature": None,
        }
    )


def _question(case: RoutingAuditCase) -> str:
    return M7_REFERENCE_CASES[case.case_id].question


def _reference_results(
    safety: SqlSafetyService, cases: tuple[RoutingAuditCase, ...]
) -> dict[str, QueryExecution]:
    references: dict[str, QueryExecution] = {}
    for case in cases:
        source = M7_REFERENCE_CASES[case.case_id]
        planned = safety.plan(SqlCandidate(sql=source.reference_sql, source=CandidateSource.INTERNAL))
        if not isinstance(planned, QueryPlan):
            raise RuntimeError(f"Frozen M7 reference failed M1 for {case.case_id}")
        execution = safety.execute(planned)
        if not isinstance(execution, QueryExecution):
            raise RuntimeError(f"Frozen M7 reference failed execution for {case.case_id}")
        references[case.case_id] = execution
    return references


def _result_row(
    case: RoutingAuditCase, result: TextToSqlResult, reference: QueryExecution, total_latency: float
) -> dict[str, Any]:
    execution = result.execution
    correct = isinstance(execution, QueryExecution) and compare_query_results(execution, reference)
    provenance = result.verified_memory_provenance
    failure_stage = result.failure_stage.value if result.failure_stage else None
    m1_outcome = "ACCEPTED" if result.plan is not None else failure_stage
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "family": case.family,
        "provider_protocol_success": result.proposal is not None,
        "provider_calls_attempted": result.provider_calls_attempted,
        "provider_calls_succeeded": result.provider_calls_succeeded,
        "provider_calls_failed": result.provider_calls_failed,
        "status": result.status.value,
        "failure_stage": failure_stage,
        "generated_sql": result.candidate.sql if result.candidate else None,
        "generated_sql_parse_success": result.candidate is not None and failure_stage != FailureStage.SQL_PARSE_ERROR.value,
        "m1_outcome": m1_outcome,
        "m1_accepted": result.plan is not None,
        "m1_policy_rejection": failure_stage == FailureStage.POLICY_REJECTION.value,
        "m1_cost_rejection": failure_stage == FailureStage.QUERY_COST_REJECTION.value,
        "execution_success": execution is not None,
        "counterfactual_correct": correct,
        "counterfactual_failure_cause": _failure_cause(case, result, correct),
        "memory_ids": list(provenance.retrieved_example_ids) if provenance else [],
        "memory_hit": bool(provenance and provenance.retrieved_example_ids),
        "memory_retrieval_latency_ms": provenance.retrieval_latency_ms if provenance else None,
        "generation_latency_ms": result.generation_latency_ms,
        "prompt_tokens": result.proposal.prompt_tokens if result.proposal else None,
        "completion_tokens": result.proposal.completion_tokens if result.proposal else None,
        "total_latency_ms": total_latency,
    }
    return row


def _failure_cause(case: RoutingAuditCase, result: TextToSqlResult, correct: bool) -> str | None:
    if correct:
        return None
    if result.status is TextToSqlStatus.SQL_GENERATION_ERROR:
        return "PROVIDER_PROTOCOL_ERROR"
    if result.status is TextToSqlStatus.PLAN_REJECTED:
        if result.failure_stage is FailureStage.QUERY_COST_REJECTION:
            return "M1_COST_REJECTION"
        return "M1_POLICY_REJECTION"
    if result.status is TextToSqlStatus.EXECUTION_ERROR:
        return "EXECUTION_ERROR"
    if "WINDOW" in case.required_operations or "RUNNING_AGGREGATE" in case.required_operations:
        return "WINDOW_COMPOSITION_ERROR"
    if "TOP_N" in case.required_operations or "ORDER" in case.required_operations:
        return "ORDER_LIMIT_ERROR"
    if "CUSTOM_FORMULA" in case.required_operations or "RATIO" in case.required_operations:
        return "DERIVED_METRIC_FORMULA_ERROR"
    if "EXPLICIT_FILTER" in case.required_operations or "HAVING" in case.required_operations:
        return "FILTER_ERROR"
    return "QUERY_STRUCTURE_ERROR"


def summarize_counterfactual(cases: tuple[RoutingAuditCase, ...], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {row["case_id"]: row for row in rows}
    if set(by_id) != {case.case_id for case in cases}:
        raise RuntimeError("Counterfactual results do not cover the exact M8 slice")
    provider_success = sum(bool(row.get("provider_protocol_success")) for row in rows)
    correct = sum(bool(row.get("counterfactual_correct")) for row in rows)
    recovered = sum(not case.current_p1_correct and bool(by_id[case.case_id].get("counterfactual_correct")) for case in cases)
    not_recovered = sum(not case.current_p1_correct and not bool(by_id[case.case_id].get("counterfactual_correct")) for case in cases)
    direct_also_correct = sum(case.current_p1_correct and bool(by_id[case.case_id].get("counterfactual_correct")) for case in cases)
    regressions = sum(case.current_p1_correct and not bool(by_id[case.case_id].get("counterfactual_correct")) for case in cases)
    return {
        "corpus_id": M8_CORPUS_ID,
        "corpus_version": M8_CORPUS_VERSION,
        "audit_cases": len(cases),
        "provider_calls_attempted": sum(int(row.get("provider_calls_attempted", 0)) for row in rows),
        "provider_protocol_success": provider_success,
        "provider_protocol_failures": len(rows) - provider_success,
        "counterfactual_correct": correct,
        "counterfactual_accuracy": correct / len(rows),
        "harmful_cases": 13,
        "harmless_cases": 7,
        "routing_recovered": recovered,
        "routing_not_recovered": not_recovered,
        "harmful_recoverability": recovered / 13,
        "direct_also_correct": direct_also_correct,
        "direct_regressions": regressions,
        "harmless_regression_rate": regressions / 7,
        "net_gain_cases": recovered - regressions,
        "observed_counterfactual_total_correct": 99 + recovered - regressions,
        "observed_counterfactual_total_accuracy": (99 + recovered - regressions) / 150,
        "memory_hits": sum(bool(row.get("memory_hit")) for row in rows),
        "memory_no_hits": sum(not bool(row.get("memory_hit")) for row in rows),
        "generated_sql_parse_success": sum(bool(row.get("generated_sql_parse_success")) for row in rows),
        "m1_accepted": sum(bool(row.get("m1_accepted")) for row in rows),
        "m1_policy_rejections": sum(bool(row.get("m1_policy_rejection")) for row in rows),
        "m1_cost_rejections": sum(bool(row.get("m1_cost_rejection")) for row in rows),
        "execution_success": sum(bool(row.get("execution_success")) for row in rows),
        "failure_causes": dict(Counter(row.get("counterfactual_failure_cause") for row in rows if row.get("counterfactual_failure_cause"))),
        "latency_ms": _latency_summary(rows, "total_latency_ms"),
        "memory_retrieval_latency_ms": _latency_summary(rows, "memory_retrieval_latency_ms"),
        "provider_generation_latency_ms": _latency_summary(rows, "generation_latency_ms"),
        "token_input_total": sum(row.get("prompt_tokens") or 0 for row in rows),
        "token_output_total": sum(row.get("completion_tokens") or 0 for row in rows),
    }


def build_final_decision(cases: tuple[RoutingAuditCase, ...], rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_counterfactual(cases, rows)
    mechanism = Counter(case.primary_routing_mechanism for case in cases)
    harmful_mechanism = Counter(case.primary_routing_mechanism for case in cases if not case.current_p1_correct)
    recovered_ids = {case.case_id for case in cases if not case.current_p1_correct and next(row for row in rows if row["case_id"] == case.case_id).get("counterfactual_correct")}
    narrow_candidates = {
        name: {
            "harmful": sum(1 for case in cases if not case.current_p1_correct and case.primary_routing_mechanism == name),
            "recovered": sum(1 for case in cases if case.case_id in recovered_ids and case.primary_routing_mechanism == name),
            "harmless_regressions": sum(1 for case in cases if case.current_p1_correct and case.primary_routing_mechanism == name and not next(row for row in rows if row["case_id"] == case.case_id).get("counterfactual_correct")),
        }
        for name in sorted(mechanism)
    }
    broad = summary["routing_recovered"] >= 8 and summary["direct_regressions"] <= 2 and sum(value for name, value in harmful_mechanism.items() if name != "R10_OTHER") >= 8 and summary["net_gain_cases"] >= 6
    narrow = any(value["harmful"] >= 4 and value["recovered"] >= 3 and value["harmless_regressions"] <= 1 and name != "R10_OTHER" for name, value in narrow_candidates.items())
    classification = "ROUTING_REDESIGN_JUSTIFIED" if broad else "ROUTING_NARROW_FIX_JUSTIFIED" if narrow else "ROUTING_NOT_PRIMARY_RECOVERABLE_BOTTLENECK"
    return {
        "classification": classification,
        "summary": summary,
        "mechanism_distribution": dict(mechanism),
        "harmful_mechanism_distribution": dict(harmful_mechanism),
        "narrow_candidate_metrics": narrow_candidates,
        "broad_threshold_passed": broad,
        "narrow_threshold_passed": narrow,
        "partial_coverage_hypothesis_supported": sum(case.coverage == "PARTIAL" and not case.current_p1_correct for case in cases) >= 8,
        "capability_based_applicability_supported": broad or narrow,
        "cases": [
            {**case.model_dump(mode="json"), **next(row for row in rows if row["case_id"] == case.case_id)}
            for case in cases
        ],
    }


def _latency_summary(rows: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    values = sorted(float(row[key]) for row in rows if row.get(key) is not None)
    if not values:
        return {"avg": None, "p50": None, "p95": None}
    return {"avg": sum(values) / len(values), "p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95)}


def _percentile(values: list[float], fraction: float) -> float:
    index = min(len(values) - 1, int(round((len(values) - 1) * fraction)))
    return values[index]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))


if __name__ == "__main__":
    main()
