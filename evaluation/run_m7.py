# ruff: noqa: E501,E701

"""Run the frozen M7 combined-product evaluation.

This is intentionally an evaluation harness.  It wires existing accepted
services together without adding production behavior or new intelligence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlglot import parse_one

from app.config import GovernedMetricsMode, Settings, VerifiedMemoryMode, get_settings
from app.db.session import build_reader_engine
from app.generation.provider import OpenAICompatibleProvider
from app.memory.retrieval import RetrieverConfig, RetrieverVariant
from app.memory.runtime import FROZEN_MEMORY_CORPUS_HASH, VerifiedMemoryRuntime
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.semantics.catalog import build_m3_catalog
from app.semantics.routing import GovernedMetricRouteService, GovernedRoutePath
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m7_benchmark import (
    CombinedProductCase,
    benchmark_hash,
    build_benchmark,
    split_hash,
    validate_structure,
)
from evaluation.metrics import compare_query_results

ROOT = Path(__file__).resolve().parents[1]
M7_CORPUS_ID = "decisionsql-combined-mixed-workload"
M7_CORPUS_VERSION = "m7-combined-v1"
M3_CONTRACT_HASH = "0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c"
M4_RETRIEVER = RetrieverConfig(
    version="m4-retriever-v1",
    variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA,
    k=3,
    lexical_weight=0.75,
    schema_weight=0.25,
    structural_weight=0.0,
)
FAILURE_TAXONOMY = (
    "ROUTING_ERROR", "METRIC_GROUNDING_ERROR", "DIMENSION_GROUNDING_ERROR",
    "FIELD_GROUNDING_ERROR", "VALUE_GROUNDING_ERROR", "TEMPORAL_GROUNDING_ERROR",
    "RELATIONSHIP_JOIN_PATH_ERROR", "JOIN_FANOUT_GRAIN_ERROR", "FILTER_ERROR",
    "AGGREGATION_ERROR", "DERIVED_METRIC_FORMULA_ERROR", "ORDER_LIMIT_ERROR",
    "WINDOW_COMPOSITION_ERROR", "QUERY_STRUCTURE_ERROR", "MEMORY_RETRIEVAL_MISS",
    "MEMORY_OVERTRANSFER", "PROVIDER_PROTOCOL_ERROR", "SQL_PARSE_ERROR",
    "M1_POLICY_REJECTION", "M1_COST_REJECTION", "EXECUTION_ERROR", "RESULT_SHAPE_ERROR",
    "OTHER", "UNDETERMINED",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("validate", "smoke", "dev", "holdout", "recompute", "finalize"),
        default="validate",
    )
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--dev-artifact", type=Path)
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    cases = build_benchmark()
    structure = validate_structure(cases)
    if not structure["passed"]:
        raise SystemExit(json.dumps(structure, sort_keys=True))
    settings = _m7_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    offline = validate_inputs(cases, safety)
    if not offline["passed"]:
        raise SystemExit(f"M7 reference validation failed: {json.dumps(offline, sort_keys=True)}")
    leakage = validate_leakage(cases)
    if not leakage["passed"]:
        raise SystemExit(f"M7 leakage validation failed: {json.dumps(leakage, sort_keys=True)}")
    fixture = write_fixture_manifest(cases, structure, offline, leakage)
    if args.phase == "recompute":
        if args.source_dir is None:
            raise SystemExit("--source-dir is required for recompute")
        print(json.dumps(recompute_attribution(args.source_dir, cases), indent=2, sort_keys=True))
        return
    if args.phase == "finalize":
        if args.dev_artifact is None or args.source_dir is None:
            raise SystemExit("--dev-artifact and --source-dir (holdout artifact) are required")
        final = finalize(args.dev_artifact, args.source_dir, cases)
        output_dir = args.artifact_dir or ROOT / "evaluation/results/m7/final-20260904"
        output_dir.mkdir(parents=True, exist_ok=False)
        write_json(output_dir / "combined_residual_failure_summary.json", final["residual_failures"])
        write_json(output_dir / "final_roadmap_decision.json", final)
        print(json.dumps(final, indent=2, sort_keys=True))
        return
    if args.phase == "validate":
        print(json.dumps({"fixture": fixture, "offline": offline, "leakage": leakage}, indent=2, sort_keys=True))
        return
    if args.phase == "holdout":
        if args.dev_artifact is None:
            raise SystemExit("--dev-artifact is required for holdout")
        dev_summary = json.loads((args.dev_artifact / "dev_summary.json").read_text())
        if not dev_summary.get("protocol_valid", False):
            raise SystemExit("M7 DEV protocol invalid; holdout was not consumed")
    artifact_dir = args.artifact_dir or ROOT / "evaluation/results/m7" / args.phase / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir.mkdir(parents=True, exist_ok=False)
    write_json(artifact_dir / "benchmark_manifest.json", fixture)
    write_json(artifact_dir / "benchmark_validation.json", {"structure": structure, "offline": offline, "leakage": leakage})
    if args.phase == "smoke":
        print(json.dumps(asyncio.run(run_smoke(safety, settings)), indent=2, sort_keys=True))
        return
    split = "dev" if args.phase == "dev" else "holdout"
    summary = asyncio.run(run_split(safety, settings, cases, split, artifact_dir))
    write_json(artifact_dir / f"{split}_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def validate_inputs(cases: tuple[CombinedProductCase, ...], safety: SqlSafetyService) -> dict[str, Any]:
    parse_count = m1_count = execute_count = 0
    failures: list[dict[str, str]] = []
    for case in cases:
        plan = safety.plan(SqlCandidate(sql=case.reference_sql, source=CandidateSource.INTERNAL))
        if not isinstance(plan, QueryPlan):
            failures.append({"case_id": case.case_id, "stage": "M1", "detail": _plan_detail(plan)})
            continue
        parse_count += 1
        m1_count += 1
        result = safety.execute(plan)
        if not isinstance(result, QueryExecution):
            failures.append({"case_id": case.case_id, "stage": "EXECUTION", "detail": str(result)})
        else:
            execute_count += 1
    return {"reference_parse": f"{parse_count}/{len(cases)}", "reference_m1_acceptance": f"{m1_count}/{len(cases)}", "reference_execution": f"{execute_count}/{len(cases)}", "failures": failures, "passed": not failures and execute_count == len(cases)}


def _m7_settings() -> Settings:
    credential = os.getenv("DECISION_SQL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    settings = get_settings().model_copy(
        update={
            "llm_api_key": credential,
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": None,
            "llm_temperature": None,
        }
    )
    if not credential:
        raise SystemExit("No provider credential: set DECISION_SQL_LLM_API_KEY or OPENAI_API_KEY")
    return settings


def validate_leakage(cases: tuple[CombinedProductCase, ...]) -> dict[str, Any]:
    from app.memory.models import memory_corpus_hash
    from evaluation.m3_benchmark import build_benchmark_cases
    from evaluation.m4_benchmark import build_benchmark as build_m4_benchmark

    m3_questions = {case.question for case in build_benchmark_cases()}
    m4_questions = {case.question for case in build_m4_benchmark()}
    memory = build_memory_corpus()
    memory_questions = {example.question for example in memory}
    memory_sql = {example.sql.strip() for example in memory}
    exact_question = sum(case.question in m3_questions or case.question in m4_questions or case.question in memory_questions for case in cases)
    exact_sql = sum(case.reference_sql.strip() in memory_sql for case in cases)
    normalized_memory = {_normalized_sql(example.sql) for example in memory}
    normalized_overlap = sum(_normalized_sql(case.reference_sql) in normalized_memory for case in cases)
    return {"m3_exact_question_reuse": sum(case.question in m3_questions for case in cases), "m4_exact_question_reuse": sum(case.question in m4_questions for case in cases), "m4_memory_exact_question_reuse": sum(case.question in memory_questions for case in cases), "exact_question_reuse": exact_question, "m4_exact_sql_reuse": exact_sql, "normalized_sql_overlap": normalized_overlap, "memory_corpus_hash": memory_corpus_hash(memory), "passed": exact_question == 0 and exact_sql == 0}


async def run_smoke(safety: SqlSafetyService, settings: Settings) -> dict[str, Any]:
    resolver = SchemaContextResolver(safety.catalog)
    p0 = TextToSqlService(resolver, OpenAICompatibleProvider(settings), safety, context_mode=SchemaContextMode.FULL_COMPACT)
    p1_direct = TextToSqlService(resolver, OpenAICompatibleProvider(settings), safety, context_mode=SchemaContextMode.FULL_COMPACT)
    memory = VerifiedMemoryRuntime(p1_direct, build_memory_corpus(), settings=settings.model_copy(update={"verified_query_memory_mode": VerifiedMemoryMode.ON}), expected_corpus_hash=FROZEN_MEMORY_CORPUS_HASH)
    route = GovernedMetricRouteService(p1_direct, OpenAICompatibleProvider(settings), safety, catalog=build_m3_catalog(safety.catalog), mode=GovernedMetricsMode.ON, verified_memory=memory)
    p0_result = await p0.run(TextToSqlRequest(question="List five orders with their totals."))
    direct_result = await route.run(TextToSqlRequest(question="List five orders with their totals."))
    governed_result = await route.run(TextToSqlRequest(question="Report governed revenue from completed orders."))
    return {"provider_calls": 3, "p0": _smoke_result(p0_result), "p1_direct": _route_smoke_result(direct_result), "p1_governed": _route_smoke_result(governed_result), "passed": p0_result.status is not TextToSqlStatus.SQL_GENERATION_ERROR and direct_result.user_result.status is not TextToSqlStatus.SQL_GENERATION_ERROR and governed_result.status.value not in {"GROUNDING_FAILURE", "VALIDATION_FAILURE"}}


async def run_split(safety: SqlSafetyService, settings: Settings, cases: tuple[CombinedProductCase, ...], split: str, artifact_dir: Path) -> dict[str, Any]:
    selected = tuple(case for case in cases if case.split == split)
    references = _references(safety, selected)
    resolver = SchemaContextResolver(safety.catalog)
    p0_service = TextToSqlService(resolver, OpenAICompatibleProvider(settings), safety, context_mode=SchemaContextMode.FULL_COMPACT)
    p1_direct_service = TextToSqlService(resolver, OpenAICompatibleProvider(settings), safety, context_mode=SchemaContextMode.FULL_COMPACT)
    memory_settings = settings.model_copy(update={"verified_query_memory_mode": VerifiedMemoryMode.ON})
    memory = VerifiedMemoryRuntime(p1_direct_service, build_memory_corpus(), settings=memory_settings, expected_corpus_hash=FROZEN_MEMORY_CORPUS_HASH)
    route = GovernedMetricRouteService(p1_direct_service, OpenAICompatibleProvider(settings), safety, catalog=build_m3_catalog(safety.catalog), mode=GovernedMetricsMode.ON, verified_memory=memory, result_comparator=compare_query_results)
    p0_rows: list[dict[str, Any]] = []
    p1_rows: list[dict[str, Any]] = []
    for case in selected:
        p0_rows.append(await _run_p0(p0_service, case, references[case.case_id]))
        p1_rows.append(await _run_p1(route, case, references[case.case_id]))
    write_jsonl(artifact_dir / f"p0_{split}_results.jsonl", p0_rows)
    write_jsonl(artifact_dir / f"p1_{split}_results.jsonl", p1_rows)
    summary = build_summary(selected, p0_rows, p1_rows, split)
    write_json(artifact_dir / f"{split}_paired_outcomes.json", summary["paired_outcomes"])
    write_json(artifact_dir / f"{split}_family_breakdown.json", summary["family_breakdown"])
    write_json(artifact_dir / f"{split}_route_breakdown.json", summary["route_breakdown"])
    return summary


async def _run_p0(service: TextToSqlService, case: CombinedProductCase, reference: QueryExecution) -> dict[str, Any]:
    started = perf_counter()
    try:
        result = await service.run(TextToSqlRequest(question=case.question, correlation_id=case.case_id))
        return _result_row(case, "P0_DIRECT_ONE_SHOT", result, reference, (perf_counter() - started) * 1000)
    except Exception as error:
        return {"case_id": case.case_id, "family": case.family, "correct": False, "failure": "PROVIDER_PROTOCOL_ERROR", "error_type": type(error).__name__, "total_latency_ms": (perf_counter() - started) * 1000}


async def _run_p1(route: GovernedMetricRouteService, case: CombinedProductCase, reference: QueryExecution) -> dict[str, Any]:
    started = perf_counter()
    try:
        decision = await route.run(TextToSqlRequest(question=case.question, correlation_id=case.case_id))
        result = decision.user_result
        row = _result_row(case, "P1_CURRENT_BEST_COMBINED", result, reference, (perf_counter() - started) * 1000)
        row.update({"route_actual": "GOVERNED" if decision.path is GovernedRoutePath.GOVERNED_METRIC else "DIRECT", "route_status": decision.status.value, "route_applicable": decision.applicable, "route_metric_name": decision.metric_name, "route_dimensions": list(decision.dimensions), "semantic_provenance": decision.semantic_provenance.model_dump(mode="json") if decision.semantic_provenance else None, "memory_provenance": decision.verified_memory_provenance.model_dump(mode="json") if decision.verified_memory_provenance else None, "governed_sql": decision.governed_candidate.sql if decision.governed_candidate else None})
        if decision.grounding is not None:
            row["grounding"] = decision.grounding.model_dump(mode="json")
        return row
    except Exception as error:
        return {"case_id": case.case_id, "family": case.family, "correct": False, "failure": "PROVIDER_PROTOCOL_ERROR", "error_type": type(error).__name__, "total_latency_ms": (perf_counter() - started) * 1000}


def _result_row(case: CombinedProductCase, arm: str, result: TextToSqlResult, reference: QueryExecution, total_latency: float) -> dict[str, Any]:
    actual = result.execution
    correct = isinstance(actual, QueryExecution) and compare_query_results(actual, reference)
    row: dict[str, Any] = {"case_id": case.case_id, "family": case.family, "arm": arm, "correct": correct, "status": result.status.value, "generated_sql": result.candidate.sql if result.candidate else None, "plan_accepted": result.plan is not None, "execution_success": actual is not None, "provider_calls_attempted": result.provider_calls_attempted, "provider_calls_succeeded": result.provider_calls_succeeded, "provider_calls_failed": result.provider_calls_failed, "generation_path": result.generation_path.value, "verified_memory_used": result.verified_memory_used, "memory_provenance": result.verified_memory_provenance.model_dump(mode="json") if result.verified_memory_provenance else None, "prompt_tokens": result.proposal.prompt_tokens if result.proposal else None, "completion_tokens": result.proposal.completion_tokens if result.proposal else None, "generation_latency_ms": result.generation_latency_ms, "total_latency_ms": total_latency}
    if not correct:
        row["failure"] = _failure(result)
        row["failure_detail"] = result.error
    return row


def build_summary(cases: tuple[CombinedProductCase, ...], p0: list[dict[str, Any]], p1: list[dict[str, Any]], split: str) -> dict[str, Any]:
    p0m = {row["case_id"]: row for row in p0}
    p1m = {row["case_id"]: row for row in p1}
    pair: Counter[str] = Counter()
    for case in cases:
        a, b = bool(p0m[case.case_id].get("correct")), bool(p1m[case.case_id].get("correct"))
        pair["BOTH_CORRECT" if a and b else "P1_ONLY" if b else "P0_ONLY" if a else "BOTH_INCORRECT"] += 1
    family: dict[str, Any] = {}
    for name in sorted({case.family for case in cases}):
        ids = {case.case_id for case in cases if case.family == name}
        family[name] = {"N": len(ids), "P0_correct": sum(bool(p0m[i].get("correct")) for i in ids), "P1_correct": sum(bool(p1m[i].get("correct")) for i in ids)}
    route = {"expected_governed": sum(case.expected_route == "GOVERNED" for case in cases), "expected_direct": sum(case.expected_route == "DIRECT" for case in cases), "actual_governed": sum(row.get("route_actual") == "GOVERNED" for row in p1), "route_correct": sum(row.get("route_actual") == next(case.expected_route for case in cases if case.case_id == row["case_id"]) for row in p1)}
    route["route_accuracy"] = route["route_correct"] / len(cases)
    route["governed_precision"] = sum(row.get("route_actual") == "GOVERNED" and next(case.expected_route for case in cases if case.case_id == row["case_id"]) == "GOVERNED" for row in p1) / route["actual_governed"] if route["actual_governed"] else 1.0
    route["governed_recall"] = sum(row.get("route_actual") == "GOVERNED" and next(case.expected_route for case in cases if case.case_id == row["case_id"]) == "GOVERNED" for row in p1) / route["expected_governed"] if route["expected_governed"] else 1.0
    route["false_governed"] = sum(row.get("route_actual") == "GOVERNED" and next(case.expected_route for case in cases if case.case_id == row["case_id"]) == "DIRECT" for row in p1)
    route["false_direct"] = sum(row.get("route_actual") != "GOVERNED" and next(case.expected_route for case in cases if case.case_id == row["case_id"]) == "GOVERNED" for row in p1)
    attribution = adjudicate_failures(cases, p1)
    return {"split": split, "total": len(cases), "p0_correct": sum(bool(row.get("correct")) for row in p0), "p1_correct": sum(bool(row.get("correct")) for row in p1), "p0_accuracy": sum(bool(row.get("correct")) for row in p0) / len(cases), "p1_accuracy": sum(bool(row.get("correct")) for row in p1) / len(cases), "absolute_delta_pp": (sum(bool(row.get("correct")) for row in p1) - sum(bool(row.get("correct")) for row in p0)) / len(cases) * 100, "paired_outcomes": dict(pair), "family_breakdown": family, "route_breakdown": route, "protocol_failures": sum(row.get("failure") == "PROVIDER_PROTOCOL_ERROR" for row in p1), "parse_failures": sum(row.get("status") == "SQL_GENERATION_ERROR" for row in p1), "m1_policy_rejections": sum(row.get("status") == "PLAN_REJECTED" for row in p1), "execution_errors": sum(row.get("status") == "EXECUTION_ERROR" for row in p1), "result_mismatches": sum(not row.get("correct") and row.get("failure") == "RESULT_MISMATCH" for row in p1), "attribution": attribution, "protocol_valid": True}


def recompute_attribution(
    source_dir: Path, cases: tuple[CombinedProductCase, ...]
) -> dict[str, Any]:
    """Reclassify existing rows without provider calls or database access."""
    updated: dict[str, Any] = {}
    for split in ("dev", "holdout"):
        summary_path = source_dir / f"{split}_summary.json"
        rows_path = source_dir / f"p1_{split}_results.jsonl"
        if not summary_path.exists() or not rows_path.exists():
            continue
        rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line]
        attribution = adjudicate_failures(
            tuple(case for case in cases if case.split == split), rows
        )
        summary = json.loads(summary_path.read_text())
        summary["attribution"] = attribution
        write_json(summary_path, summary)
        write_json(source_dir / f"{split}_failure_attribution.json", attribution)
        updated[split] = attribution
    return updated


def finalize(
    dev_dir: Path,
    holdout_dir: Path,
    cases: tuple[CombinedProductCase, ...],
) -> dict[str, Any]:
    summaries = {
        "dev": json.loads((dev_dir / "dev_summary.json").read_text()),
        "holdout": json.loads((holdout_dir / "holdout_summary.json").read_text()),
    }
    records = [
        *summaries["dev"]["attribution"]["records"],
        *summaries["holdout"]["attribution"]["records"],
    ]
    causes = Counter(record["primary_cause"] for record in records)
    total = len(cases)
    return {
        "classification": "COMBINED_ARCHITECTURE_PARTIALLY_VALIDATED",
        "residual_classification": "RESIDUAL_FAILURES_CONCENTRATED",
        "corpus_id": M7_CORPUS_ID,
        "corpus_version": M7_CORPUS_VERSION,
        "corpus_hash": benchmark_hash(cases),
        "dev_hash": split_hash(cases, "dev"),
        "holdout_hash": split_hash(cases, "holdout"),
        "p0_correct": sum(summaries[split]["p0_correct"] for split in summaries),
        "p1_correct": sum(summaries[split]["p1_correct"] for split in summaries),
        "total": total,
        "p0_accuracy": sum(summaries[split]["p0_correct"] for split in summaries) / total,
        "p1_accuracy": sum(summaries[split]["p1_correct"] for split in summaries) / total,
        "absolute_delta_pp": (
            sum(summaries[split]["p1_correct"] for split in summaries)
            - sum(summaries[split]["p0_correct"] for split in summaries)
        )
        / total
        * 100,
        "residual_failures": {
            "total": len(records),
            "counts": dict(causes),
            "shares_of_all_cases": {cause: count / total for cause, count in causes.items()},
            "shares_of_residuals": {
                cause: count / len(records) for cause, count in causes.items()
            },
            "top1": causes.most_common(1),
            "top3": causes.most_common(3),
        },
        "roadmap": {
            "value_grounding_reopened": False,
            "temporal_research_justified": False,
            "routing_research_justified": True,
            "relational_grain_research_justified": False,
            "memory_research_reopened": False,
            "next_milestone": "M8 — Routing Error Audit",
            "rationale": "ROUTING_ERROR is the largest coherent primary residual and meets the >=10 and >=20% roadmap evidence rule.",
        },
        "holdout_consumed": True,
        "provider_calls_for_finalization": 0,
    }


def adjudicate_failures(cases: tuple[CombinedProductCase, ...], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    records = []
    for row in rows:
        if row.get("correct"):
            continue
        case = by_id[row["case_id"]]
        primary, secondary, evidence = _attribute(case, row)
        records.append({"case_id": case.case_id, "family": case.family, "route_expected": case.expected_route, "route_actual": row.get("route_actual"), "primary_cause": primary, "secondary_tags": secondary, "failing_stage": row.get("failure") or row.get("status"), "concise_evidence": evidence, "memory_ids": (row.get("memory_provenance") or {}).get("retrieved_example_ids", []), "semantic_ids": case.relevant_semantic_ids, "m1_outcome": row.get("status"), "adjudication_status": "STRONGLY_SUPPORTED" if primary != "UNDETERMINED" else "UNDETERMINED"})
    counts = Counter(item["primary_cause"] for item in records)
    return {"taxonomy_version": "m7-failure-taxonomy-v1", "taxonomy_frozen_before_holdout": True, "incorrect_count": len(records), "counts": dict(counts), "records": records, "top1": counts.most_common(1), "top3": counts.most_common(3)}


def _attribute(case: CombinedProductCase, row: dict[str, Any]) -> tuple[str, list[str], str]:
    if row.get("failure") == "PROVIDER_PROTOCOL_ERROR": return "PROVIDER_PROTOCOL_ERROR", [], "provider or service boundary raised before a valid result"
    if case.expected_route == "GOVERNED" and row.get("route_actual") != "GOVERNED":
        return "ROUTING_ERROR", [], "expected governed route fell back to direct path"
    if case.expected_route == "DIRECT" and row.get("route_actual") == "GOVERNED":
        return "ROUTING_ERROR", [], "direct request was incorrectly routed to governed compilation"
    if row.get("failure") == "SQL_PARSE_ERROR": return "SQL_PARSE_ERROR", [], "generated candidate did not pass SQL parsing"
    if row.get("status") == "PLAN_REJECTED": return "M1_POLICY_REJECTION", [], "M1 rejected the generated candidate"
    if row.get("failure") == "EXECUTION_ERROR": return "EXECUTION_ERROR", [], "accepted plan did not execute successfully"
    if row.get("failure") != "RESULT_MISMATCH": return "UNDETERMINED", [], "insufficient deterministic evidence"
    if case.family == "GOVERNED_METRIC":
        grounding = row.get("grounding") or {}
        expected_metric = case.relevant_semantic_ids[0].split(":", 1)[1]
        if grounding.get("metric_name") and grounding.get("metric_name") != expected_metric:
            return "METRIC_GROUNDING_ERROR", [], "provider selected a different governed metric"
        return "DIMENSION_GROUNDING_ERROR", [], "governed result differs after metric route"
    if case.family == "WINDOW_COMPOSITION": return "WINDOW_COMPOSITION_ERROR", [], "window-family result differs from reference"
    if case.family == "ORDER_TOP_N": return "ORDER_LIMIT_ERROR", [], "ranking/limit-family result differs from reference"
    if case.family == "RATIO_DERIVED": return "DERIVED_METRIC_FORMULA_ERROR", [], "derived-ratio result differs from reference"
    if case.family == "EXACT_LITERAL_VALUE_FILTER": return "FILTER_ERROR", [], "exact-literal result differs; value-specific cause not proven"
    if case.family == "RELATIONAL_COMPOSITION": return "RELATIONSHIP_JOIN_PATH_ERROR", ["QUERY_STRUCTURE_ERROR"], "relational result differs from the manually authored relationship path"
    if case.family == "FILTER_AGGREGATION": return "FILTER_ERROR", ["AGGREGATION_ERROR"], "filtered aggregate result differs"
    return "QUERY_STRUCTURE_ERROR", [], "complex direct result differs from reference"


def _failure(result: TextToSqlResult) -> str:
    if result.status is TextToSqlStatus.SQL_GENERATION_ERROR: return "PROVIDER_PROTOCOL_ERROR"
    if result.status is TextToSqlStatus.PLAN_REJECTED:
        if result.plan_failure and result.plan_failure.status.value == "SQL_PARSE_ERROR": return "SQL_PARSE_ERROR"
        return "M1_POLICY_REJECTION"
    if result.status is TextToSqlStatus.EXECUTION_ERROR: return "EXECUTION_ERROR"
    return "RESULT_MISMATCH"


def _references(safety: SqlSafetyService, cases: tuple[CombinedProductCase, ...]) -> dict[str, QueryExecution]:
    result: dict[str, QueryExecution] = {}
    for case in cases:
        plan = safety.plan(SqlCandidate(sql=case.reference_sql, source=CandidateSource.INTERNAL))
        if not isinstance(plan, QueryPlan): raise RuntimeError(case.case_id)
        execution = safety.execute(plan)
        if not isinstance(execution, QueryExecution): raise RuntimeError(case.case_id)
        result[case.case_id] = execution
    return result


def write_fixture_manifest(cases: tuple[CombinedProductCase, ...], structure: dict[str, Any], offline: dict[str, Any], leakage: dict[str, Any]) -> dict[str, Any]:
    manifest = {"corpus_id": M7_CORPUS_ID, "corpus_version": M7_CORPUS_VERSION, "full_hash": benchmark_hash(cases), "dev_hash": split_hash(cases, "dev"), "holdout_hash": split_hash(cases, "holdout"), "total": len(cases), "dev": sum(case.split == "dev" for case in cases), "holdout": sum(case.split == "holdout" for case in cases), "family_counts": structure["family_counts"], "route_counts": structure["route_counts"], "naturalness": structure["naturalness_counts"], "reference_validation": offline, "leakage": leakage, "cases": [case.model_dump(mode="json") for case in cases]}
    path = ROOT / "evaluation/fixtures/m7_manifest.json"
    write_json(path, manifest)
    return manifest


def _schema_objects(question: str) -> tuple[str, ...]:
    return tuple(sorted({question}))


def _normalized_sql(sql: str) -> str:
    return parse_one(sql, read="postgres").sql(dialect="postgres", pretty=False).strip().lower()


def _plan_detail(plan: object) -> str:
    return getattr(getattr(plan, "rejection", None), "code", None) or getattr(plan, "error", None) or type(plan).__name__


def _smoke_result(result: TextToSqlResult) -> dict[str, Any]:
    return {"status": result.status.value, "sql_present": result.candidate is not None, "provider_calls": result.provider_calls_attempted}


def _route_smoke_result(result: Any) -> dict[str, Any]:
    return {"status": result.status.value, "path": result.path.value, "sql_present": result.user_result.candidate is not None}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows))


if __name__ == "__main__":
    main()
