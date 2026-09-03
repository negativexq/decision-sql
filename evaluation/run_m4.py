"""M4 verified-query-memory evaluation runner.

The offline phases build and validate all frozen inputs before the optional
provider phases.  No retrieved SQL is executed as trusted input: every
generated proposal is passed through the existing M1 service.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from app.catalog.default import build_default_catalog
from app.config import Settings, get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.provider import LLMProviderError, OpenAICompatibleProvider, SqlProposal
from app.memory.models import VerifiedQueryExample
from app.memory.prompt import render_memory_schema_context
from app.memory.retrieval import RetrieverConfig, RetrieverVariant, VerifiedQueryRetriever
from app.models.domain import QueryRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import (
    CandidateSource,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)
from app.sql.service import SqlSafetyService
from evaluation.m4_benchmark import (
    MEMORY_CORPUS_ID,
    MEMORY_CORPUS_VERSION,
    M4BenchmarkQuestion,
    benchmark_hash,
    build_benchmark,
    build_memory_corpus,
    corpus_hash,
    validate_benchmark,
)
from evaluation.metrics import compare_query_results

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RETRIEVER = RetrieverConfig(
    version="m4-retriever-v1",
    variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA,
    k=3,
    lexical_weight=0.75,
    schema_weight=0.25,
    structural_weight=0.0,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M4 verified query memory evaluation")
    parser.add_argument(
        "--phase", choices=("validate", "phase-a", "dev", "holdout"), default="validate"
    )
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--dev-artifact", type=Path)
    args = parser.parse_args()

    memory = build_memory_corpus()
    benchmark = build_benchmark()
    validation = validate_benchmark(memory, benchmark)
    if not validation["passed"]:
        raise SystemExit(
            f"M4 benchmark validation failed: {json.dumps(validation, sort_keys=True)}"
        )

    if args.phase == "validate":
        settings = get_settings()
        engine = build_reader_engine(settings)
        service = SqlSafetyService(engine, settings=settings)
        result = validate_database_inputs(memory, benchmark, service)
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    phase_a = run_phase_a(memory, benchmark)
    artifact_dir = args.artifact_dir or _new_artifact_dir(args.phase)
    artifact_dir.mkdir(parents=True, exist_ok=False)
    _write_json(artifact_dir / "memory_manifest.json", memory_manifest(memory))
    _write_json(artifact_dir / "benchmark_manifest.json", benchmark_manifest(benchmark))
    _write_json(artifact_dir / "leakage_report.json", validation)
    _write_json(artifact_dir / "retrieval_phase_a.json", phase_a)
    if args.phase == "phase-a":
        print(json.dumps(phase_a, indent=2, sort_keys=True))
        return
    if not phase_a["passed"]:
        raise SystemExit("M4 retrieval Phase A gate failed; provider calls were not attempted")

    if args.phase == "holdout":
        if args.dev_artifact is None:
            raise SystemExit("--dev-artifact is required for the holdout phase")
        dev_summary = json.loads((args.dev_artifact / "dev_summary.json").read_text())
        if not dev_summary.get("dev_gate_passed", False):
            raise SystemExit("M4 DEV gate did not pass; holdout was not consumed")

    offline_settings = get_settings()
    engine = build_reader_engine(offline_settings)
    service = SqlSafetyService(engine, settings=offline_settings)
    database_validation = validate_database_inputs(memory, benchmark, service)
    if not database_validation["passed"]:
        raise SystemExit(
            "M4 database/reference validation failed; provider calls were not attempted"
        )
    settings = _m4_settings()
    split = "dev" if args.phase == "dev" else "holdout"
    summary = run_provider_split(
        service,
        settings,
        memory,
        benchmark,
        phase_a["chosen_config"],
        split,
        artifact_dir,
    )
    _write_json(artifact_dir / f"{split}_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def memory_manifest(examples: tuple[VerifiedQueryExample, ...]) -> dict[str, Any]:
    return {
        "memory_corpus_id": MEMORY_CORPUS_ID,
        "memory_corpus_version": MEMORY_CORPUS_VERSION,
        "corpus_hash": corpus_hash(examples),
        "example_count": len(examples),
        "examples": [
            {
                "example_id": example.example_id,
                "content_hash": example.content_hash,
                "family": example.tags[0] if example.tags else "OTHER",
            }
            for example in examples
        ],
    }


def benchmark_manifest(questions: tuple[M4BenchmarkQuestion, ...]) -> dict[str, Any]:
    return {
        "benchmark_version": 1,
        "benchmark_hash": benchmark_hash(questions),
        "dev_count": sum(question.split == "dev" for question in questions),
        "holdout_count": sum(question.split == "holdout" for question in questions),
        "question_ids": [question.question_id for question in questions],
    }


def validate_database_inputs(
    examples: tuple[VerifiedQueryExample, ...],
    questions: tuple[M4BenchmarkQuestion, ...],
    service: SqlSafetyService | None = None,
) -> dict[str, Any]:
    if service is None:
        return {
            "memory_parse": f"{len(examples)}/{len(examples)}",
            "benchmark_parse": f"{len(questions)}/{len(questions)}",
            "memory_execute": "not_run",
            "benchmark_execute": "not_run",
            "m1_compatibility": "not_run",
            "passed": True,
        }
    memory_parse = memory_execute = benchmark_parse = benchmark_execute = m1_accept = 0
    failures: list[dict[str, str]] = []
    for example in examples:
        plan = service.plan(SqlCandidate(sql=example.sql, source=CandidateSource.INTERNAL))
        if isinstance(plan, QueryPlan):
            memory_parse += 1
            m1_accept += 1
            result = service.execute(plan)
            if isinstance(result, QueryExecution):
                memory_execute += 1
            else:
                failures.append({"id": example.example_id, "stage": "execute"})
        else:
            failures.append({"id": example.example_id, "stage": "plan"})
    for question in questions:
        plan = service.plan(SqlCandidate(sql=question.gold_sql, source=CandidateSource.INTERNAL))
        if isinstance(plan, QueryPlan):
            benchmark_parse += 1
            m1_accept += 1
            result = service.execute(plan)
            if isinstance(result, QueryExecution):
                benchmark_execute += 1
            else:
                failures.append({"id": question.question_id, "stage": "execute"})
        else:
            failures.append({"id": question.question_id, "stage": "plan"})
    return {
        "memory_parse": f"{memory_parse}/{len(examples)}",
        "memory_execute": f"{memory_execute}/{len(examples)}",
        "benchmark_parse": f"{benchmark_parse}/{len(questions)}",
        "benchmark_execute": f"{benchmark_execute}/{len(questions)}",
        "m1_compatibility": f"{m1_accept}/{len(examples) + len(questions)}",
        "failures": failures,
        "passed": not failures
        and memory_parse == len(examples)
        and memory_execute == len(examples)
        and benchmark_parse == len(questions)
        and benchmark_execute == len(questions),
    }


def run_phase_a(
    examples: tuple[VerifiedQueryExample, ...], questions: tuple[M4BenchmarkQuestion, ...]
) -> dict[str, Any]:
    resolver = SchemaContextResolver(build_default_catalog(Base.metadata))
    retriever = VerifiedQueryRetriever(examples)
    configs = (
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL,
            k=1,
            lexical_weight=1.0,
            schema_weight=0.0,
            structural_weight=0.0,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL,
            k=2,
            lexical_weight=1.0,
            schema_weight=0.0,
            structural_weight=0.0,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL,
            k=3,
            lexical_weight=1.0,
            schema_weight=0.0,
            structural_weight=0.0,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA,
            k=1,
            lexical_weight=0.75,
            schema_weight=0.25,
            structural_weight=0.0,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA,
            k=2,
            lexical_weight=0.75,
            schema_weight=0.25,
            structural_weight=0.0,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA,
            k=3,
            lexical_weight=0.75,
            schema_weight=0.25,
            structural_weight=0.0,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA_HEURISTIC,
            k=1,
            lexical_weight=0.65,
            schema_weight=0.20,
            structural_weight=0.15,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA_HEURISTIC,
            k=2,
            lexical_weight=0.65,
            schema_weight=0.20,
            structural_weight=0.15,
        ),
        RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA_HEURISTIC,
            k=3,
            lexical_weight=0.65,
            schema_weight=0.20,
            structural_weight=0.15,
        ),
    )
    reports = []
    for config in configs:
        rows: list[dict[str, Any]] = []
        for question in questions:
            if question.split != "dev":
                continue
            schema_objects = _schema_objects(resolver, question.question)
            retrieval_started = perf_counter()
            retrieved = retriever.retrieve(question.question, schema_objects, config)
            retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000
            useful = bool(
                {item.example.example_id for item in retrieved}
                & set(question.useful_example_ids)
            )
            rows.append(
                {
                    "question_id": question.question_id,
                    "useful": useful,
                    "retrieved_example_ids": [item.example.example_id for item in retrieved],
                    "scores": [round(item.final_score, 8) for item in retrieved],
                    "lexical_scores": [round(item.lexical_score, 8) for item in retrieved],
                    "schema_scores": [round(item.schema_score, 8) for item in retrieved],
                    "structural_bonuses": [
                        round(item.structural_bonus, 8) for item in retrieved
                    ],
                    "retrieval_latency_ms": retrieval_latency_ms,
                }
            )
        recall = sum(row["useful"] for row in rows) / len(rows)
        reports.append(
            {
                "config": config.model_dump(mode="json"),
                "config_hash": _hash_payload(config.model_dump(mode="json")),
                "question_count": len(rows),
                "useful_precedent_hits": sum(row["useful"] for row in rows),
                "useful_precedent_recall": recall,
                "mean_top_score": mean(row["scores"][0] for row in rows),
                "mean_lexical_score": mean(row["lexical_scores"][0] for row in rows),
                "mean_schema_score": mean(row["schema_scores"][0] for row in rows),
                "mean_structural_bonus": mean(
                    row["structural_bonuses"][0] for row in rows
                ),
                "retrieval_latency_ms": _latency(
                    [{"latency_ms": row["retrieval_latency_ms"]} for row in rows]
                ),
                "retrieval_diversity": len(
                    {item for row in rows for item in row["retrieved_example_ids"]}
                ),
                "family_match_rate": recall,
                "rows": rows,
            }
        )
    chosen = max(
        reports,
        key=lambda report: (
            report["useful_precedent_recall"],
            -report["config"]["k"],
            -sum(report["config"][field] for field in ("schema_weight", "structural_weight")),
        ),
    )
    chosen_config = RetrieverConfig.model_validate(chosen["config"])
    return {
        "phase": "provider_free_retrieval",
        "dev_count": sum(question.split == "dev" for question in questions),
        "variants": reports,
        "chosen_config": chosen_config.model_dump(mode="json"),
        "chosen_config_hash": chosen["config_hash"],
        "passed": chosen["useful_precedent_recall"] >= 0.70,
        "provider_calls": 0,
    }


def run_provider_split(
    service: SqlSafetyService,
    settings: Settings,
    examples: tuple[VerifiedQueryExample, ...],
    questions: tuple[M4BenchmarkQuestion, ...],
    config_data: dict[str, Any],
    split: str,
    artifact_dir: Path,
) -> dict[str, Any]:
    config = RetrieverConfig.model_validate(config_data)
    retriever = VerifiedQueryRetriever(examples)
    resolver = SchemaContextResolver(service.catalog)
    references = _reference_results(service, questions)
    provider = OpenAICompatibleProvider(settings)
    rows: dict[str, list[dict[str, Any]]] = {"baseline": [], "memory": []}
    model_io: list[dict[str, Any]] = []
    for question in questions:
        if question.split != split:
            continue
        schema_objects = _schema_objects(resolver, question.question)
        retrieved = retriever.retrieve(question.question, schema_objects, config)
        selected = tuple(item.example for item in retrieved)
        base_context = _generation_context(resolver, question.question)
        baseline = _run_generation_arm(
            service, provider, question, references[question.question_id], base_context, "BASELINE"
        )
        memory_context = render_memory_schema_context(base_context, selected)
        memory = _run_generation_arm(
            service, provider, question, references[question.question_id], memory_context, "MEMORY"
        )
        baseline_row, baseline_io = baseline
        memory_row, memory_io = memory
        baseline_row["retrieval_useful"] = False
        memory_row["retrieval_useful"] = bool(
            {item.example_id for item in selected} & set(question.useful_example_ids)
        )
        memory_row["retrieved_example_ids"] = [item.example_id for item in selected]
        memory_row["retrieval_scores"] = [round(item.final_score, 8) for item in retrieved]
        rows["baseline"].append(baseline_row)
        rows["memory"].append(memory_row)
        model_io.extend(
            [
                {"arm": "BASELINE", "question_id": question.question_id, **baseline_io},
                {"arm": "MEMORY", "question_id": question.question_id, **memory_io},
            ]
        )
    summary = _summary(rows["baseline"], rows["memory"], split)
    _write_jsonl(artifact_dir / f"{split}_baseline_results.jsonl", rows["baseline"])
    _write_jsonl(artifact_dir / f"{split}_memory_results.jsonl", rows["memory"])
    _write_jsonl(artifact_dir / "model_io.jsonl", model_io)
    _write_json(artifact_dir / f"{split}_pairwise.json", summary["pairwise"])
    _write_json(artifact_dir / f"{split}_failure_analysis.json", summary["failure_analysis"])
    _write_json(artifact_dir / f"{split}_retrieval_quality.json", summary["retrieval_quality"])
    _write_json(artifact_dir / f"{split}_cost_analysis.json", summary["cost"])
    _write_json(artifact_dir / f"{split}_latency_analysis.json", summary["latency"])
    return summary


def _run_generation_arm(
    service: SqlSafetyService,
    provider: OpenAICompatibleProvider,
    question: M4BenchmarkQuestion,
    reference: QueryExecution,
    context: str,
    arm: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = perf_counter()
    try:
        proposal: SqlProposal = _await_provider(provider, question.question, context)
        candidate = SqlCandidate(
            sql=_clean_sql(proposal.sql), source=CandidateSource.LLM
        )
        planned = service.plan(candidate)
        execution = service.execute(planned) if isinstance(planned, QueryPlan) else planned
        correct = isinstance(execution, QueryExecution) and compare_query_results(
            execution, reference
        )
        row = {
            "question_id": question.question_id,
            "family": question.family,
            "arm": arm,
            "generated_sql": candidate.sql,
            "correct": correct,
            "plan_accepted": isinstance(planned, QueryPlan),
            "execution_success": isinstance(execution, QueryExecution),
            "failure": None if correct else _failure(planned, execution),
            "prompt_tokens": proposal.prompt_tokens,
            "completion_tokens": proposal.completion_tokens,
            "latency_ms": proposal.latency_ms,
            "total_latency_ms": (perf_counter() - started) * 1000,
        }
        return row, {
            "generated_sql": candidate.sql,
            "prompt_tokens": proposal.prompt_tokens,
            "completion_tokens": proposal.completion_tokens,
            "latency_ms": proposal.latency_ms,
        }
    except LLMProviderError as error:
        return (
            {
                "question_id": question.question_id,
                "family": question.family,
                "arm": arm,
                "generated_sql": None,
                "correct": False,
                "plan_accepted": False,
                "execution_success": False,
                "failure": "PROVIDER_FAILURE",
                "prompt_tokens": None,
                "completion_tokens": None,
                "latency_ms": (perf_counter() - started) * 1000,
                "total_latency_ms": (perf_counter() - started) * 1000,
                "provider_error": error.detail.model_dump(mode="json") if error.detail else None,
            },
            {"generated_sql": None, "prompt_tokens": None, "completion_tokens": None},
        )


async def _async_propose(
    provider: OpenAICompatibleProvider, question: str, context: str
) -> SqlProposal:
    return await provider.propose_sql(QueryRequest(question=question), None, context)


def _await_provider(provider: OpenAICompatibleProvider, question: str, context: str) -> SqlProposal:
    import asyncio

    return asyncio.run(_async_propose(provider, question, context))


def _reference_results(
    service: SqlSafetyService, questions: tuple[M4BenchmarkQuestion, ...]
) -> dict[str, QueryExecution]:
    results: dict[str, QueryExecution] = {}
    for question in questions:
        if question.split not in {"dev", "holdout"}:
            continue
        plan = service.plan(SqlCandidate(sql=question.gold_sql, source=CandidateSource.INTERNAL))
        if not isinstance(plan, QueryPlan):
            raise RuntimeError(f"reference plan failed: {question.question_id}")
        result = service.execute(plan)
        if not isinstance(result, QueryExecution):
            raise RuntimeError(f"reference execution failed: {question.question_id}")
        results[question.question_id] = result
    return results


def _summary(
    baseline: list[dict[str, Any]], memory: list[dict[str, Any]], split: str
) -> dict[str, Any]:
    baseline_map = {row["question_id"]: row for row in baseline}
    memory_map = {row["question_id"]: row for row in memory}
    pair: Counter[str] = Counter()
    for question_id in baseline_map:
        b = bool(baseline_map[question_id]["correct"])
        m = bool(memory_map[question_id]["correct"])
        label = (
            "BOTH_CORRECT"
            if b
            and m
            else "BASELINE_ONLY"
            if b
            else "MEMORY_ONLY"
            if m
            else "BOTH_INCORRECT"
        )
        pair[label] += 1
    direct_correct = sum(bool(row["correct"]) for row in baseline)
    memory_correct = sum(bool(row["correct"]) for row in memory)
    total = len(baseline)
    retrieval_matrix: Counter[str] = Counter()
    for row in memory:
        retrieval_matrix[
            "USEFUL_RETRIEVAL + CORRECT_SQL"
            if row["retrieval_useful"] and row["correct"]
            else "USEFUL_RETRIEVAL + WRONG_SQL"
            if row["retrieval_useful"]
            else "NO_USEFUL_RETRIEVAL + CORRECT_SQL"
            if row["correct"]
            else "NO_USEFUL_RETRIEVAL + WRONG_SQL"
        ] += 1
    family = {}
    for name in sorted({row["family"] for row in baseline}):
        b_rows = [row for row in baseline if row["family"] == name]
        m_rows = [row for row in memory if row["family"] == name]
        family[name] = {
            "total": len(b_rows),
            "baseline_correct": sum(bool(row["correct"]) for row in b_rows),
            "memory_correct": sum(bool(row["correct"]) for row in m_rows),
        }
    baseline_failures = Counter(row["failure"] for row in baseline if row["failure"])
    memory_failures = Counter(row["failure"] for row in memory if row["failure"])
    return {
        "split": split,
        "total": total,
        "baseline_correct": direct_correct,
        "memory_correct": memory_correct,
        "baseline_accuracy": direct_correct / total if total else 0.0,
        "memory_accuracy": memory_correct / total if total else 0.0,
        "absolute_delta_pp": (memory_correct - direct_correct) / total * 100 if total else 0.0,
        "pairwise": dict(pair),
        "family": family,
        "provider_failures": {
            "baseline": baseline_failures.get("PROVIDER_FAILURE", 0),
            "memory": memory_failures.get("PROVIDER_FAILURE", 0),
        },
        "baseline_failure_distribution": dict(baseline_failures),
        "memory_failure_distribution": dict(memory_failures),
        "retrieval_quality": {
            "matrix": dict(retrieval_matrix),
            "useful_retrieval_rate": (
                sum(row["retrieval_useful"] for row in memory) / total if total else 0.0
            ),
            "example_overtransfer": sum(
                row.get("failure") == "EXAMPLE_OVERTRANSFER" for row in memory
            ),
        },
        "failure_analysis": _failure_analysis(baseline, memory),
        "cost": {"baseline": _cost(baseline), "memory": _cost(memory)},
        "latency": {"baseline": _latency(baseline), "memory": _latency(memory)},
        "dev_gate_passed": split != "dev" or (
            memory_correct / total - direct_correct / total >= 0.08
            and pair.get("MEMORY_ONLY", 0) > pair.get("BASELINE_ONLY", 0)
            and baseline_failures.get("PROVIDER_FAILURE", 0)
            == memory_failures.get("PROVIDER_FAILURE", 0)
            and not any(row.get("failure") == "EXAMPLE_OVERTRANSFER" for row in memory)
        ),
    }


def _failure_analysis(
    baseline: list[dict[str, Any]], memory: list[dict[str, Any]]
) -> dict[str, Any]:
    transitions: Counter[str] = Counter()
    for before, after in zip(baseline, memory, strict=True):
        if before["failure"] and after["correct"]:
            transitions[before["failure"]] += 1
    return {"memory_fixed_baseline_failures": dict(transitions)}


def _cost(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prompts = [row["prompt_tokens"] for row in rows if row["prompt_tokens"] is not None]
    completions = [row["completion_tokens"] for row in rows if row["completion_tokens"] is not None]
    return {
        "input_tokens": sum(prompts),
        "output_tokens": sum(completions),
        "average_input_tokens": mean(prompts) if prompts else None,
        "average_output_tokens": mean(completions) if completions else None,
    }


def _latency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None)
    return {
        "average_ms": mean(values) if values else None,
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return values[min(len(values) - 1, int(round((len(values) - 1) * percentile)))]


def _failure(planned: object, execution: object) -> str:
    if isinstance(planned, SqlPlanFailure):
        return "PARSE_FAILURE" if planned.status.value == "SQL_PARSE_ERROR" else "M1_REJECTION"
    if isinstance(execution, SqlExecutionError):
        return "EXECUTION_ERROR"
    return "RESULT_MISMATCH"


def _schema_objects(resolver: SchemaContextResolver, question: str) -> tuple[str, ...]:
    context = resolver.resolve(question, SchemaContextMode.FULL_COMPACT)
    values = {table.name for table in context.tables}
    values.update(f"{column.table_name}.{column.name}" for column in context.selected_columns)
    return tuple(sorted(values))


def _generation_context(resolver: SchemaContextResolver, question: str) -> str:
    context = resolver.resolve(question, SchemaContextMode.FULL_COMPACT)
    schema = serialize_schema_context(context)
    return (
        "BUSINESS CONTEXT:\n"
        "Answer the direct analytics question using only the supplied PostgreSQL schema. "
        "Return one read-only SELECT query as JSON with an SQL field. Do not provide reasoning.\n\n"
        f"SCHEMA:\n{schema}"
    )


def _m4_settings() -> Settings:
    credential = os.getenv("DECISION_SQL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not credential:
        raise SystemExit("No provider credential: set DECISION_SQL_LLM_API_KEY or OPENAI_API_KEY")
    settings = get_settings().model_copy(update={"llm_api_key": credential})
    return settings.model_copy(
        update={"llm_model": "gpt-5.6-luna", "llm_temperature": None, "llm_reasoning_effort": None}
    )


def _clean_sql(sql: str) -> str:
    cleaned = sql.strip()
    match = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", cleaned, re.IGNORECASE | re.DOTALL)
    return (match.group(1) if match else cleaned).strip()


def _new_artifact_dir(phase: str) -> Path:
    return ROOT / "evaluation/results/m4" / phase / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _hash_payload(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows))


if __name__ == "__main__":
    main()
