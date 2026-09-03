"""Run the M2.6 inference-reasoning ablation without changing generation architecture."""

import argparse
import asyncio
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.catalog.default import build_default_catalog
from app.config import get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.provider import OpenAICompatibleProvider
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import GenerationMode
from app.text_to_sql.service import TextToSqlService
from evaluation.m25_classification import classify_sql_failure
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase
from evaluation.runner import load_baseline

EXPECTED_HOLDOUT_SHA = "c96473d64100370df6cfb86ac6afa53edba5d4af82772413b856bfe1579ba354"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.6 reasoning-effort ablation")
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--results-root", type=Path, default=Path("evaluation/results/m26"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--report", type=Path, default=Path("docs/m26-baseline.md"))
    args = parser.parse_args()
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit(
            "M2.6 evaluation requires DECISION_SQL_LLM_API_KEY; no provider calls were made."
        )
    dataset = args.dataset or (
        Path("evaluation/datasets/m2_baseline.json")
        if args.stage == "dev"
        else Path("evaluation/datasets/m26_holdout.json")
    )
    if args.stage == "holdout":
        _verify_holdout(dataset)
    cases = load_baseline(dataset)
    catalog = build_default_catalog(Base.metadata)

    def build_service(reasoning_effort: str) -> TextToSqlService:
        arm_settings = settings.model_copy(
            update={
                "llm_model": "gpt-5.6-luna",
                "llm_temperature": None,
                "llm_reasoning_effort": reasoning_effort,
            }
        )
        resolver = SchemaContextResolver(
            catalog,
            top_k=arm_settings.schema_top_k,
            max_tables=arm_settings.max_context_tables,
            max_columns_per_table=arm_settings.max_columns_per_table,
            relationship_depth=arm_settings.relationship_depth,
        )
        safety = SqlSafetyService(
            build_reader_engine(arm_settings), settings=arm_settings, catalog=catalog
        )
        return TextToSqlService(
            resolver,
            OpenAICompatibleProvider(arm_settings),
            safety,
            context_mode=SchemaContextMode.FULL_COMPACT,
            generation_mode=GenerationMode.ONE_SHOT,
        )

    reports = {
        effort: asyncio.run(evaluate(effort, cases, build_service(effort)))
        for effort in ("none", "low")
    }
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dataset_sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
    metadata = {
        "run_id": run_id,
        "stage": args.stage,
        "evaluation_timestamp": datetime.now(UTC).isoformat(),
        "provider": "openai-compatible",
        "model": "gpt-5.6-luna",
        "endpoint_family": "chat_completions",
        "generation_settings": {
            "sampling_temperature_requested": None,
            "sampling_temperature_mode": "provider_default",
            "response_format": "json_object",
            "dialect": "postgres",
            "reasoning_efforts": ["none", "low"],
        },
        "architecture": {
            "schema_context_mode": "FULL_COMPACT",
            "generation_mode": "ONE_SHOT",
            "query_intent_enabled": False,
            "retrieval_enabled": False,
            "repair_enabled": False,
        },
        "dataset_path": str(dataset),
        "dataset_sha256": dataset_sha,
        "question_count": len(cases),
        "code_commit_sha": _git_sha(),
        "evaluator_version": "m2-result-equivalence-v2-ordinal-columns",
        "provider_calls_attempted": sum(
            report["provider_calls_attempted"] for report in reports.values()
        ),
        "provider_calls_succeeded": sum(
            report["provider_calls_succeeded"] for report in reports.values()
        ),
        "provider_calls_failed": sum(
            report["provider_calls_failed"] for report in reports.values()
        ),
        "no_adaptive_tuning": True,
    }
    output_dir = args.results_root / args.stage / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "metadata.json", metadata)
    for effort, report in reports.items():
        _write_json(output_dir / f"{effort}.json", report)
    comparison = _comparison(reports)
    _write_json(output_dir / "comparison.json", comparison)
    _write_json(output_dir / "failure_analysis.json", _failure_analysis(reports, metadata))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_render_report(metadata, reports, comparison), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "report": str(args.report)}))


def _verify_holdout(dataset: Path) -> None:
    actual = hashlib.sha256(dataset.read_bytes()).hexdigest()
    if actual != EXPECTED_HOLDOUT_SHA:
        raise SystemExit("M2.6 holdout SHA-256 mismatch; evaluation stopped.")


async def evaluate(
    effort: str, cases: list[BaselineCase], service: TextToSqlService
) -> dict[str, Any]:
    rows = [await _evaluate_case(case, service) for case in cases]
    return _summarize(effort, rows)


async def _evaluate_case(case: BaselineCase, service: TextToSqlService) -> dict[str, Any]:
    result = await service.run(
        TextToSqlRequest(question=case.question, correlation_id=case.id, execute=True)
    )
    gold = service.safety_service.plan(
        SqlCandidate(sql=case.gold_sql, correlation_id=f"gold-{case.id}")
    )
    gold_execution = (
        service.safety_service.execute(gold) if isinstance(gold, QueryPlan) else None
    )
    comparison = None
    if isinstance(result.execution, QueryExecution) and isinstance(gold_execution, QueryExecution):
        comparison = assess_query_results(
            result.execution,
            gold_execution,
            order_sensitive=case.order_sensitive,
            actual_sql=result.proposal.sql if result.proposal else None,
            expected_sql=case.gold_sql,
        )
    equivalent = comparison.equivalent if comparison else None
    proposal = result.proposal
    failure_class = (
        classify_sql_failure(case, proposal.sql) if equivalent is False and proposal else None
    )
    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "generated_sql": proposal.sql if proposal else None,
        "status": result.status.value,
        "failure_stage": result.failure_stage.value if result.failure_stage else None,
        "provider_error": (
            result.provider_error.model_dump(mode="json") if result.provider_error else None
        ),
        "result_equivalent": equivalent,
        "equivalence_diagnostic": (
            comparison.diagnostic.value if comparison and comparison.diagnostic else None
        ),
        "failure_class": failure_class,
        "parse_success": result.candidate is not None
        and result.failure_stage is not FailureStage.SQL_PARSE_ERROR,
        "plan_accepted": isinstance(result.plan, QueryPlan),
        "execution_success": isinstance(result.execution, QueryExecution),
        "execution_row_count": result.execution.row_count if result.execution else None,
        "provider_calls_attempted": result.provider_calls_attempted,
        "provider_calls_succeeded": result.provider_calls_succeeded,
        "provider_calls_failed": result.provider_calls_failed,
        "input_tokens": proposal.prompt_tokens if proposal else None,
        "output_tokens": proposal.completion_tokens if proposal else None,
        "reasoning_tokens": proposal.reasoning_tokens if proposal else None,
        "cached_prompt_tokens": proposal.cached_prompt_tokens if proposal else None,
        "generation_latency_ms": proposal.latency_ms if proposal else None,
    }


def _summarize(effort: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    return {
        "reasoning_effort": effort,
        "total_questions": total,
        "result_equivalence_count": sum(row["result_equivalent"] is True for row in rows),
        "result_equivalence_rate": sum(row["result_equivalent"] is True for row in rows) / total,
        "parse_success_count": sum(row["parse_success"] for row in rows),
        "plan_acceptance_count": sum(row["plan_accepted"] for row in rows),
        "execution_success_count": sum(row["execution_success"] for row in rows),
        "policy_rejection_count": sum(
            row["failure_stage"] == FailureStage.POLICY_REJECTION.value for row in rows
        ),
        "query_cost_rejection_count": sum(
            row["failure_stage"] == FailureStage.QUERY_COST_REJECTION.value for row in rows
        ),
        "execution_failure_count": sum(
            row["failure_stage"] == FailureStage.EXECUTION_ERROR.value for row in rows
        ),
        "generation_failure_count": sum(
            row["failure_stage"] == FailureStage.SQL_GENERATION_ERROR.value for row in rows
        ),
        "provider_calls_attempted": sum(row["provider_calls_attempted"] for row in rows),
        "provider_calls_succeeded": sum(row["provider_calls_succeeded"] for row in rows),
        "provider_calls_failed": sum(row["provider_calls_failed"] for row in rows),
        "total_input_tokens": _sum_optional(row["input_tokens"] for row in rows),
        "total_output_tokens": _sum_optional(row["output_tokens"] for row in rows),
        "total_reasoning_tokens": _sum_optional(row["reasoning_tokens"] for row in rows),
        "total_cached_prompt_tokens": _sum_optional(
            row["cached_prompt_tokens"] for row in rows
        ),
        "average_input_tokens": _average(row["input_tokens"] for row in rows),
        "average_output_tokens": _average(row["output_tokens"] for row in rows),
        "average_latency_ms": _average(row["generation_latency_ms"] for row in rows),
        "p50_latency_ms": _percentile(
            (row["generation_latency_ms"] for row in rows), 0.5
        ),
        "p95_latency_ms": _percentile(
            (row["generation_latency_ms"] for row in rows), 0.95
        ),
        "failure_taxonomy": dict(
            Counter(row["failure_class"] for row in rows if row["failure_class"])
        ),
        "category_breakdown": {
            category: {
                "total": len(items),
                "result_equivalence_count": sum(
                    item["result_equivalent"] is True for item in items
                ),
                "result_equivalence_rate": sum(
                    item["result_equivalent"] is True for item in items
                )
                / len(items),
            }
            for category, items in sorted(categories.items())
        },
        "cases": rows,
    }


def _comparison(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    none = reports["none"]
    low = reports["low"]
    pairwise: Counter[str] = Counter()
    for none_case, low_case in zip(none["cases"], low["cases"], strict=True):
        none_correct = none_case["result_equivalent"] is True
        low_correct = low_case["result_equivalent"] is True
        pairwise[
            "BOTH_CORRECT"
            if none_correct and low_correct
            else "NONE_ONLY_CORRECT"
            if none_correct
            else "LOW_ONLY_CORRECT"
            if low_correct
            else "BOTH_INCORRECT"
        ] += 1
    return {
        "none": _comparison_metrics(none),
        "low": _comparison_metrics(low),
        "delta_low_minus_none": {
            "result_equivalence_count": low["result_equivalence_count"]
            - none["result_equivalence_count"],
            "result_equivalence_rate": _difference(
                low["result_equivalence_rate"], none["result_equivalence_rate"]
            ),
            "average_latency_ms": _difference(
                low["average_latency_ms"], none["average_latency_ms"]
            ),
            "average_input_tokens": _difference(
                low["average_input_tokens"], none["average_input_tokens"]
            ),
            "average_output_tokens": _difference(
                low["average_output_tokens"], none["average_output_tokens"]
            ),
        },
        "pairwise_outcomes": dict(sorted(pairwise.items())),
        "category_delta": _category_delta(none, low),
    }


def _comparison_metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report[key]
        for key in (
            "reasoning_effort",
            "total_questions",
            "result_equivalence_count",
            "result_equivalence_rate",
            "parse_success_count",
            "plan_acceptance_count",
            "execution_success_count",
            "policy_rejection_count",
            "query_cost_rejection_count",
            "execution_failure_count",
            "generation_failure_count",
            "provider_calls_attempted",
            "provider_calls_succeeded",
            "provider_calls_failed",
            "total_input_tokens",
            "total_output_tokens",
            "total_reasoning_tokens",
            "total_cached_prompt_tokens",
            "average_input_tokens",
            "average_output_tokens",
            "average_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "failure_taxonomy",
        )
    }


def _category_delta(none: dict[str, Any], low: dict[str, Any]) -> dict[str, Any]:
    return {
        category: {
            "none": none["category_breakdown"].get(category),
            "low": low["category_breakdown"].get(category),
            "delta_rate": low["category_breakdown"].get(category, {}).get(
                "result_equivalence_rate", 0
            )
            - none["category_breakdown"].get(category, {}).get(
                "result_equivalence_rate", 0
            ),
        }
        for category in sorted(
            set(none["category_breakdown"]) | set(low["category_breakdown"])
        )
    }


def _failure_analysis(
    reports: dict[str, dict[str, Any]], metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "dataset_sha256": metadata["dataset_sha256"],
        "code_commit_sha": metadata["code_commit_sha"],
        "failure_taxonomy": {
            effort: report["failure_taxonomy"] for effort, report in reports.items()
        },
        "provider_failure_count": {
            effort: report["provider_calls_failed"] for effort, report in reports.items()
        },
        "pairwise_outcomes": _comparison(reports)["pairwise_outcomes"],
    }


def _render_report(
    metadata: dict[str, Any], reports: dict[str, dict[str, Any]], comparison: dict[str, Any]
) -> str:
    none = reports["none"]
    low = reports["low"]
    delta = comparison["delta_low_minus_none"]
    lines = [
        "# M2.6 Reasoning Capacity Ablation",
        "",
        f"- Stage: `{metadata['stage']}`",
        f"- Dataset SHA-256: `{metadata['dataset_sha256']}`",
        f"- Model: `{metadata['model']}`",
        "- Architecture: `FULL_COMPACT + ONE_SHOT` for both arms",
        "- QueryIntent/retrieval: disabled",
        "",
        "| Metric | NONE | LOW | Delta |",
        "|---|---:|---:|---:|",
        (
            f"| Result equivalence | {none['result_equivalence_count']}/"
            f"{none['total_questions']} ({none['result_equivalence_rate']:.2%}) | "
            f"{low['result_equivalence_count']}/{low['total_questions']} "
            f"({low['result_equivalence_rate']:.2%}) | "
            f"{delta['result_equivalence_count']} "
            f"({delta['result_equivalence_rate']:.2%}) |"
        ),
        (
            f"| Provider calls attempted | {none['provider_calls_attempted']} | "
            f"{low['provider_calls_attempted']} | "
            f"{low['provider_calls_attempted'] - none['provider_calls_attempted']} |"
        ),
        (
            f"| Input tokens | {none['total_input_tokens']} | "
            f"{low['total_input_tokens']} | "
            f"{_difference(low['total_input_tokens'], none['total_input_tokens'])} |"
        ),
        (
            f"| Output tokens | {none['total_output_tokens']} | "
            f"{low['total_output_tokens']} | "
            f"{_difference(low['total_output_tokens'], none['total_output_tokens'])} |"
        ),
        (
            f"| Average latency (ms) | {none['average_latency_ms']} | "
            f"{low['average_latency_ms']} | {delta['average_latency_ms']} |"
        ),
        (
            f"| P95 latency (ms) | {none['p95_latency_ms']} | "
            f"{low['p95_latency_ms']} | "
            f"{_difference(low['p95_latency_ms'], none['p95_latency_ms'])} |"
        ),
        "",
        f"Pairwise outcomes: `{json.dumps(comparison['pairwise_outcomes'], sort_keys=True)}`",
        "",
        "No pricing configuration is available; API cost was not computed.",
        "The development dataset is diagnostic only and must not be treated as final evidence.",
    ]
    return "\n".join(lines) + "\n"


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value or None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sum_optional(values: Any) -> int | None:
    numbers = [value for value in values if isinstance(value, int)]
    return sum(numbers) if numbers else None


def _average(values: Any) -> float | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


def _percentile(values: Any, quantile: float) -> float | None:
    numbers = sorted(value for value in values if isinstance(value, (int, float)))
    if not numbers:
        return None
    position = (len(numbers) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(numbers) - 1)
    fraction = position - lower
    return numbers[lower] + (numbers[upper] - numbers[lower]) * fraction


def _difference(left: Any, right: Any) -> float | int | None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left - right
    return None


if __name__ == "__main__":
    main()
