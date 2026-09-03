"""M3 compiler ceiling and controlled governed-metric evaluation runner."""

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.db.session import build_reader_engine
from app.generation.governed_metric_grounding import grounding_to_request
from app.generation.provider import (
    GovernedMetricGroundingProposal,
    LLMProviderError,
    OpenAICompatibleProvider,
    SqlProposal,
)
from app.models.domain import QueryRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.requests import MetricRequest
from app.sql.models import (
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)
from app.sql.service import SqlSafetyService
from evaluation.m3_benchmark import (
    M3BenchmarkCase,
    M3Target,
    benchmark_hash,
    build_benchmark_cases,
    build_targets,
)
from evaluation.metrics import compare_query_results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=("ceiling", "freeze", "dev", "holdout"), default="ceiling"
    )
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--dev-artifact", type=Path)
    args = parser.parse_args()
    if args.phase == "freeze":
        freeze_fixtures()
        return
    settings = _m3_settings() if args.phase in {"dev", "holdout"} else get_settings()
    engine = build_reader_engine(settings)
    service = SqlSafetyService(engine, settings=settings)
    targets = build_targets(build_m3_catalog(service.catalog))
    cases = build_benchmark_cases(targets)
    ceiling = validate_ceiling(service, targets)
    if not ceiling["passed"]:
        raise SystemExit(
            "M3 provider-free compiler ceiling failed; provider calls were not attempted"
        )
    if args.phase == "ceiling":
        print(json.dumps(ceiling, indent=2))
        return
    if args.phase == "holdout":
        if args.dev_artifact is None:
            raise SystemExit("--dev-artifact is required for the holdout phase")
        dev_summary = json.loads((args.dev_artifact / "dev_summary.json").read_text())
        if not _dev_gate_from_summary(dev_summary):
            raise SystemExit("M3 DEV gate did not pass; holdout was not consumed")
    artifact_dir = args.artifact_dir or _new_artifact_dir(args.phase)
    artifact_dir.mkdir(parents=True, exist_ok=False)
    write_json(artifact_dir / "metadata.json", _metadata(settings, targets, cases, ceiling))
    write_json(artifact_dir / "compiler_ceiling.json", ceiling)
    if args.phase == "dev":
        asyncio.run(run_split(service, settings, artifact_dir, targets, cases, "dev"))
    else:
        asyncio.run(run_split(service, settings, artifact_dir, targets, cases, "holdout"))


def freeze_fixtures() -> None:
    targets = build_targets()
    cases = build_benchmark_cases(targets)
    fixture_dir = Path("evaluation/fixtures")
    fixture_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        fixture_dir / "m3_targets.json", [target.model_dump(mode="json") for target in targets]
    )
    write_json(fixture_dir / "m3_cases.json", [case.model_dump(mode="json") for case in cases])
    print(
        json.dumps(
            {
                "benchmark_hash": benchmark_hash(targets, cases),
                "targets": len(targets),
                "cases": len(cases),
            },
            indent=2,
        )
    )


def validate_ceiling(service: SqlSafetyService, targets: tuple[M3Target, ...]) -> dict[str, Any]:
    catalog = build_m3_catalog(service.catalog)
    compiler = MetricCompiler(catalog)
    rows: list[dict[str, Any]] = []
    correct = 0
    reference_parse = 0
    reference_execute = 0
    compiled_m1_accept = 0
    for target in targets:
        reference_plan = service.plan(SqlCandidate(sql=target.reference_sql))
        reference_result: QueryExecution | None = None
        if isinstance(reference_plan, QueryPlan):
            reference_parse += 1
            reference_result_candidate = service.execute(reference_plan)
            if isinstance(reference_result_candidate, QueryExecution):
                reference_execute += 1
                reference_result = reference_result_candidate
        compiled = compiler.compile_metric(
            MetricRequest(metric_name=target.metric_name, dimensions=target.dimensions)
        )
        compiled_plan: QueryPlan | None = None
        if not isinstance(compiled, MetricCompilationFailure):
            compiled_plan_candidate = service.plan(compiled)
            if isinstance(compiled_plan_candidate, QueryPlan):
                compiled_m1_accept += 1
                compiled_plan = compiled_plan_candidate
        compiled_result = service.execute(compiled_plan) if compiled_plan else None
        equivalent = (
            reference_result is not None
            and isinstance(compiled_result, QueryExecution)
            and compare_query_results(compiled_result, reference_result)
        )
        correct += int(equivalent)
        rows.append(
            {
                "target_id": target.target_id,
                "reference_sql_hash": sha256(target.reference_sql.encode()).hexdigest(),
                "compiled_sql": compiled.sql
                if not isinstance(compiled, MetricCompilationFailure)
                else None,
                "reference_parse": isinstance(reference_plan, QueryPlan),
                "reference_execute": reference_result is not None,
                "compiled_m1_accept": compiled_plan is not None,
                "equivalent": equivalent,
            }
        )
    result = {
        "target_count": len(targets),
        "reference_parse": f"{reference_parse}/{len(targets)}",
        "reference_execute": f"{reference_execute}/{len(targets)}",
        "compiler_m1_acceptance": f"{compiled_m1_accept}/{len(targets)}",
        "correct": f"{correct}/{len(targets)}",
        "accuracy": correct / len(targets),
        "passed": correct == len(targets)
        and reference_parse == len(targets)
        and reference_execute == len(targets),
        "targets": rows,
    }
    return result


async def run_split(
    service: SqlSafetyService,
    settings: Settings,
    artifact_dir: Path,
    targets: tuple[M3Target, ...],
    cases: tuple[M3BenchmarkCase, ...],
    split: str,
) -> None:
    catalog = build_m3_catalog(service.catalog)
    glossary = public_metric_glossary(catalog)
    glossary_hash = sha256(glossary.encode()).hexdigest()
    resolver = SchemaContextResolver(catalog=service.catalog)
    references = _reference_results(service, targets)
    direct_provider = OpenAICompatibleProvider(settings)
    governed_provider = OpenAICompatibleProvider(settings)
    direct_rows: list[dict[str, Any]] = []
    governed_rows: list[dict[str, Any]] = []
    model_io: list[dict[str, Any]] = []
    for case in cases:
        if case.split != split:
            continue
        target = next(target for target in targets if target.target_id == case.target_id)
        reference = references[target.target_id]
        context = resolver.resolve(case.question, SchemaContextMode.FULL_COMPACT)
        schema = serialize_schema_context(context)
        direct = await _run_direct(service, direct_provider, case, reference, glossary, schema)
        direct_rows.append(direct[0])
        model_io.append(
            {"arm": "DIRECT", "case_id": case.case_id, "glossary_hash": glossary_hash, **direct[1]}
        )
        governed = await _run_governed(
            service, governed_provider, case, reference, glossary, catalog
        )
        governed_rows.append(governed[0])
        model_io.append(
            {
                "arm": "GOVERNED",
                "case_id": case.case_id,
                "glossary_hash": glossary_hash,
                **governed[1],
            }
        )
    write_jsonl(artifact_dir / f"{split}_direct_results.jsonl", direct_rows)
    write_jsonl(artifact_dir / f"{split}_governed_results.jsonl", governed_rows)
    write_jsonl(artifact_dir / "model_io.jsonl", model_io)
    summary = _build_summary(direct_rows, governed_rows, cases, split)
    write_json(artifact_dir / f"{split}_summary.json", summary)
    print(json.dumps(summary, indent=2))


async def _run_direct(
    service: SqlSafetyService,
    provider: OpenAICompatibleProvider,
    case: M3BenchmarkCase,
    reference: QueryExecution,
    glossary: str,
    schema: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = datetime.now(UTC)
    try:
        proposal: SqlProposal = await provider.propose_sql(
            QueryRequest(question=case.question),
            None,
            f"PUBLIC GLOSSARY:\n{glossary}\n\nSCHEMA:\n{schema}",
        )
        generated_sql = _clean_sql(proposal.sql)
        candidate = service.plan(SqlCandidate(sql=generated_sql))
        execution = service.execute(candidate) if isinstance(candidate, QueryPlan) else candidate
        correct = isinstance(execution, QueryExecution) and compare_query_results(
            execution, reference
        )
        row = _row_base(
            case,
            generated_sql,
            correct,
            proposal.prompt_tokens,
            proposal.completion_tokens,
            proposal.latency_ms,
        )
        row.update(_outcome_fields(candidate, execution))
        return row, {
            "question": case.question,
            "generated_sql": generated_sql,
            "tokens": _usage(proposal),
            "latency_ms": proposal.latency_ms,
            "request_id": None,
        }
    except LLMProviderError as error:
        row = _row_base(
            case, None, False, None, None, (datetime.now(UTC) - started).total_seconds() * 1000
        )
        row["failure"] = "PROVIDER_FAILURE"
        row["provider_error"] = error.detail.model_dump(mode="json") if error.detail else None
        return row, {
            "question": case.question,
            "generated_sql": None,
            "tokens": {},
            "latency_ms": row["latency_ms"],
            "request_id": None,
        }


async def _run_governed(
    service: SqlSafetyService,
    provider: OpenAICompatibleProvider,
    case: M3BenchmarkCase,
    reference: QueryExecution,
    glossary: str,
    catalog: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = datetime.now(UTC)
    try:
        proposal: GovernedMetricGroundingProposal = await provider.propose_metric_grounding(
            case.question, glossary
        )
        grounding = proposal.grounding
        row = _row_base(
            case,
            None,
            False,
            proposal.prompt_tokens,
            proposal.completion_tokens,
            proposal.latency_ms,
        )
        row["grounding"] = grounding.model_dump(mode="json")
        try:
            request = grounding_to_request(grounding, catalog)
            row["metric_name_exact"] = request.metric_name == case.metric_name
            row["dimensions_exact"] = request.dimensions == case.dimensions
            compiled = MetricCompiler(catalog).compile_metric(request)
            if isinstance(compiled, MetricCompilationFailure):
                row["failure"] = compiled.code
            else:
                row["adapter_success"] = True
                plan = service.plan(compiled)
                row["compiler_success"] = True
                execution = service.execute(plan) if isinstance(plan, QueryPlan) else plan
                row.update(_outcome_fields(plan, execution))
                row["correct"] = isinstance(execution, QueryExecution) and compare_query_results(
                    execution, reference
                )
        except (ValueError, KeyError) as error:
            row["failure"] = "ADAPTER_FAILED"
            row["error"] = str(error)
        return row, {
            "question": case.question,
            "grounding": grounding.model_dump(mode="json"),
            "tokens": _usage(proposal),
            "latency_ms": proposal.latency_ms,
            "request_id": None,
        }
    except LLMProviderError as error:
        row = _row_base(
            case, None, False, None, None, (datetime.now(UTC) - started).total_seconds() * 1000
        )
        row["failure"] = "PROVIDER_FAILURE"
        row["provider_error"] = error.detail.model_dump(mode="json") if error.detail else None
        return row, {
            "question": case.question,
            "grounding": None,
            "tokens": {},
            "latency_ms": row["latency_ms"],
            "request_id": None,
        }


def _reference_results(
    service: SqlSafetyService, targets: tuple[M3Target, ...]
) -> dict[str, QueryExecution]:
    result: dict[str, QueryExecution] = {}
    for target in targets:
        plan = service.plan(SqlCandidate(sql=target.reference_sql))
        if not isinstance(plan, QueryPlan):
            raise RuntimeError(f"reference plan failed: {target.target_id}")
        execution = service.execute(plan)
        if not isinstance(execution, QueryExecution):
            raise RuntimeError(f"reference execution failed: {target.target_id}")
        result[target.target_id] = execution
    return result


def _row_base(
    case: M3BenchmarkCase,
    sql: str | None,
    correct: bool,
    prompt: int | None,
    completion: int | None,
    latency: float | None,
) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "target_id": case.target_id,
        "metric_name": case.metric_name,
        "dimensions": list(case.dimensions),
        "generated_sql": sql,
        "correct": correct,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "latency_ms": latency,
    }


def _outcome_fields(plan_or_failure: Any, execution: Any) -> dict[str, Any]:
    return {
        "plan_accepted": isinstance(plan_or_failure, QueryPlan),
        "execution_success": isinstance(execution, QueryExecution),
        "execution_error": isinstance(execution, SqlExecutionError),
        "plan_failure": isinstance(plan_or_failure, SqlPlanFailure),
    }


def _build_summary(
    direct: list[dict[str, Any]],
    governed: list[dict[str, Any]],
    cases: tuple[M3BenchmarkCase, ...],
    split: str,
) -> dict[str, Any]:
    def score(rows: list[dict[str, Any]]) -> int:
        return sum(bool(row.get("correct")) for row in rows)

    direct_map = {row["case_id"]: row for row in direct}
    governed_map = {row["case_id"]: row for row in governed}
    pair = {"BOTH_CORRECT": 0, "DIRECT_ONLY": 0, "GOVERNED_ONLY": 0, "BOTH_INCORRECT": 0}
    for case in cases:
        if case.split != split:
            continue
        d = bool(direct_map[case.case_id].get("correct"))
        g = bool(governed_map[case.case_id].get("correct"))
        pair[
            "BOTH_CORRECT"
            if d and g
            else "DIRECT_ONLY"
            if d
            else "GOVERNED_ONLY"
            if g
            else "BOTH_INCORRECT"
        ] += 1
    governed_transport = sum(
        "grounding" in row and row.get("failure") != "PROVIDER_FAILURE" for row in governed
    )
    metric_exact = sum(bool(row.get("metric_name_exact")) for row in governed)
    dimension_exact = sum(bool(row.get("dimensions_exact")) for row in governed)
    correct_grounding = [
        row for row in governed if row.get("metric_name_exact") and row.get("dimensions_exact")
    ]
    correct_grounding_results = sum(bool(row.get("correct")) for row in correct_grounding)
    governed_only = pair["GOVERNED_ONLY"]
    direct_only = pair["DIRECT_ONLY"]
    direct_accuracy = score(direct) / len(direct)
    governed_accuracy = score(governed) / len(governed)
    return {
        "split": split,
        "total": len(direct),
        "direct_correct": score(direct),
        "governed_correct": score(governed),
        "direct_accuracy": direct_accuracy,
        "governed_accuracy": governed_accuracy,
        "absolute_delta_pp": (governed_accuracy - direct_accuracy) * 100,
        "pairwise": pair,
        "direct_provider_failures": sum(row.get("failure") == "PROVIDER_FAILURE" for row in direct),
        "governed_provider_failures": sum(
            row.get("failure") == "PROVIDER_FAILURE" for row in governed
        ),
        "governed_transport": f"{governed_transport}/{len(governed)}",
        "metric_name_exact": f"{metric_exact}/{len(governed)}",
        "dimensions_exact": f"{dimension_exact}/{len(governed)}",
        "correct_grounding_to_correct_result": (
            f"{correct_grounding_results}/{len(correct_grounding)}"
        ),
        "dev_gate_passed": (
            split != "dev"
            or (
                governed_transport / len(governed) >= 0.95
                and governed_accuracy - direct_accuracy >= 0.10
                and governed_only > direct_only
                and correct_grounding_results == len(correct_grounding)
            )
        ),
    }


def _m3_settings() -> Settings:
    credential = os.getenv("DECISION_SQL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    settings = get_settings().model_copy(update={"llm_api_key": credential})
    if not credential:
        raise SystemExit("No provider credential: set DECISION_SQL_LLM_API_KEY or OPENAI_API_KEY")
    return settings.model_copy(
        update={"llm_model": "gpt-5.6-luna", "llm_temperature": None, "llm_reasoning_effort": None}
    )


def _dev_gate_from_summary(summary: dict[str, Any]) -> bool:
    if "dev_gate_passed" in summary:
        return bool(summary["dev_gate_passed"])
    total = int(summary["total"])
    governed_transport = summary.get("governed_transport", f"{total}/{total}")
    transport_count = int(str(governed_transport).split("/", 1)[0])
    return (
        transport_count / total >= 0.95
        and float(summary["governed_accuracy"]) - float(summary["direct_accuracy"]) >= 0.10
        and int(summary["pairwise"]["GOVERNED_ONLY"]) > int(summary["pairwise"]["DIRECT_ONLY"])
    )


def _metadata(
    settings: Settings,
    targets: tuple[M3Target, ...],
    cases: tuple[M3BenchmarkCase, ...],
    ceiling: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run": "M3",
        "created_at": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "reasoning": "none",
        "temperature": "omitted",
        "provider_calls_per_dev_case": {"DIRECT": 1, "GOVERNED": 1},
        "benchmark_hash": benchmark_hash(targets, cases),
        "target_count": len(targets),
        "dev_count": sum(case.split == "dev" for case in cases),
        "holdout_count": sum(case.split == "holdout" for case in cases),
        "compiler_ceiling_passed": ceiling["passed"],
        "credential_present": bool(settings.llm_api_key),
    }


def _new_artifact_dir(phase: str) -> Path:
    return Path("evaluation/results/m3") / phase / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _clean_sql(sql: str) -> str:
    cleaned = sql.strip()
    match = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", cleaned, re.IGNORECASE | re.DOTALL)
    return (match.group(1) if match else cleaned).strip()


def _usage(proposal: SqlProposal | GovernedMetricGroundingProposal) -> dict[str, int | None]:
    return {
        "prompt_tokens": proposal.prompt_tokens,
        "completion_tokens": proposal.completion_tokens,
        "reasoning_tokens": proposal.reasoning_tokens,
        "cached_prompt_tokens": proposal.cached_prompt_tokens,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows))


if __name__ == "__main__":
    main()
