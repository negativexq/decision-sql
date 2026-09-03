"""Run the M2.5 one-shot versus schema-grounded holdout experiment."""

import argparse
import asyncio
import hashlib
import json
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
from app.text_to_sql.models import GenerationMode, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.m25_classification import classify_sql_failure
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase
from evaluation.runner import load_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.5 holdout experiment")
    parser.add_argument(
        "--experiment",
        choices=("primary", "full"),
        default="primary",
        help="primary=full-context pair; full=all four factorial cells",
    )
    parser.add_argument(
        "--strategy",
        choices=("one_shot", "grounded", "ablation"),
        default=None,
        help="Deprecated M2 compatibility mode; evaluates retrieved context only.",
    )
    parser.add_argument(
        "--dataset", type=Path, default=Path("evaluation/datasets/m25_holdout.json")
    )
    parser.add_argument("--results-root", type=Path, default=Path("evaluation/results/m25"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--report", type=Path, default=Path("docs/m25-baseline.md"))
    args = parser.parse_args()
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit(
            "M2.5 empirical evaluation requires DECISION_SQL_LLM_API_KEY; "
            "no provider calls were made."
        )
    cases = load_baseline(args.dataset)
    catalog = build_default_catalog(Base.metadata)

    def build_service(
        context_mode: SchemaContextMode, generation_mode: GenerationMode
    ) -> TextToSqlService:
        resolver = SchemaContextResolver(
            catalog,
            top_k=settings.schema_top_k,
            max_tables=settings.max_context_tables,
            max_columns_per_table=settings.max_columns_per_table,
            relationship_depth=settings.relationship_depth,
        )
        safety = SqlSafetyService(build_reader_engine(settings), settings=settings, catalog=catalog)
        return TextToSqlService(
            resolver,
            OpenAICompatibleProvider(settings),
            safety,
            context_mode=context_mode,
            generation_mode=generation_mode,
        )

    cells = _experiment_cells(args.experiment, args.strategy)
    reports = {
        cell_id: asyncio.run(evaluate(cell_id, cases, build_service(mode, generation)))
        for cell_id, mode, generation in cells
    }
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    metadata = {
        "run_id": run_id,
        "evaluation_timestamp": datetime.now(UTC).isoformat(),
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "generation_settings": {
            "temperature": 0,
            "response_format": "json_object",
            "dialect": "postgres",
        },
        "experiment": args.experiment if args.strategy is None else "legacy_strategy",
        "cells": [cell_id for cell_id, _, _ in cells],
        "dataset_path": str(args.dataset),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "question_count": len(cases),
        "provider_calls_attempted": sum(
            report["provider_calls_attempted"] for report in reports.values()
        ),
        "provider_calls_succeeded": sum(
            report["provider_calls_succeeded"] for report in reports.values()
        ),
        "provider_calls_failed": sum(
            report["provider_calls_failed"] for report in reports.values()
        ),
        "evaluator_version": "m2-result-equivalence-v2-ordinal-columns",
        "m2_5_generation_strategies": [generation.value for _, _, generation in cells],
        "schema_context_modes": [mode.name for _, mode, _ in cells],
        "no_repair_loop": True,
        "no_semantic_layer": True,
        "no_value_profiling": True,
    }
    output_dir = args.results_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "metadata.json", metadata)
    for cell_id, report in reports.items():
        _write_json(output_dir / f"{cell_id}.json", report)
    comparison = _comparison(reports)
    _write_json(output_dir / "comparison.json", comparison)
    failure_analysis = _failure_analysis(reports, metadata)
    _write_json(output_dir / "failure_analysis.json", failure_analysis)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_render_report(metadata, reports, comparison), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "report": str(args.report)}))


def _experiment_cells(
    experiment: str, legacy_strategy: str | None
) -> tuple[tuple[str, SchemaContextMode, GenerationMode], ...]:
    if legacy_strategy is not None:
        generations = {
            "one_shot": (GenerationMode.ONE_SHOT,),
            "grounded": (GenerationMode.GROUNDED,),
            "ablation": (GenerationMode.ONE_SHOT, GenerationMode.GROUNDED),
        }[legacy_strategy]
        return tuple(
            (
                f"retrieved_{generation.value.lower()}",
                SchemaContextMode.RETRIEVED_BOUNDED,
                generation,
            )
            for generation in generations
        )
    modes: tuple[SchemaContextMode, ...] = (SchemaContextMode.FULL_COMPACT,)
    if experiment == "full":
        modes = (SchemaContextMode.FULL_COMPACT, SchemaContextMode.RETRIEVED_BOUNDED)
    return tuple(
        (
            f"{mode.name.lower()}_{generation.value.lower()}",
            mode,
            generation,
        )
        for mode in modes
        for generation in (GenerationMode.ONE_SHOT, GenerationMode.GROUNDED)
    )


async def evaluate(
    cell_id: str, cases: list[BaselineCase], service: TextToSqlService
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        rows.append(await _evaluate_case(case, service))
    return _summarize(cell_id, service, rows)


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
    failure_class = (
        classify_sql_failure(case, result.proposal.sql if result.proposal else None)
        if equivalent is False
        else None
    )
    context = result.context
    diagnostics = result.grounding_diagnostics or {}
    intent = result.intent
    proposal = result.proposal
    sql_prompt_tokens = proposal.prompt_tokens if proposal else None
    sql_completion_tokens = proposal.completion_tokens if proposal else None
    requested_tables_present = (
        intent is not None
        and context is not None
        and set(intent.selected_tables).issubset({table.name for table in context.tables})
    )
    requested_columns_present = (
        intent is not None
        and context is not None
        and _intent_columns_visible(intent.selected_columns, context)
    )
    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "generated_sql": proposal.sql if proposal else None,
        "status": result.status.value,
        "failure_stage": result.failure_stage.value if result.failure_stage else None,
        "result_equivalent": equivalent,
        "equivalence_diagnostic": (
            comparison.diagnostic.value if comparison and comparison.diagnostic else None
        ),
        "failure_class": failure_class,
        "intent": intent.model_dump(mode="json") if intent else None,
        "grounding_diagnostics": diagnostics,
        "requested_tables_present": requested_tables_present if intent else None,
        "requested_columns_present": requested_columns_present if intent else None,
        "parse_success": result.candidate is not None
        and result.failure_stage is not FailureStage.SQL_PARSE_ERROR,
        "plan_accepted": isinstance(result.plan, QueryPlan),
        "execution_success": isinstance(result.execution, QueryExecution),
        "execution_row_count": result.execution.row_count if result.execution else None,
        "provider_calls_attempted": result.provider_calls_attempted,
        "provider_calls_succeeded": result.provider_calls_succeeded,
        "provider_calls_failed": result.provider_calls_failed,
        "prompt_tokens": (result.intent_prompt_tokens or 0) + (sql_prompt_tokens or 0)
        if result.intent_proposal or proposal
        else None,
        "completion_tokens": (
            (result.intent_completion_tokens or 0) + (sql_completion_tokens or 0)
            if result.intent_proposal or proposal
            else None
        ),
        "generation_latency_ms": (
            (result.intent_latency_ms or 0) + (result.generation_latency_ms or 0)
            if result.intent_proposal or proposal
            else None
        ),
        "intent_prompt_tokens": result.intent_prompt_tokens,
        "intent_completion_tokens": result.intent_completion_tokens,
        "intent_latency_ms": result.intent_latency_ms,
        "sql_prompt_tokens": sql_prompt_tokens,
        "sql_completion_tokens": sql_completion_tokens,
        "sql_latency_ms": proposal.latency_ms if proposal else None,
        "context_table_count": context.context_metadata.selected_table_count if context else None,
        "context_column_count": context.context_metadata.selected_column_count if context else None,
    }


def _intent_columns_visible(columns: tuple[str, ...], context: Any) -> bool:
    visible = {
        f"{table.name.lower()}.{column.name.lower()}"
        for table in context.tables
        for column in table.columns
    }
    return all(column.lower() in visible for column in columns)


def _summarize(
    cell_id: str, service: TextToSqlService, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    total = len(rows)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    return {
        "cell_id": cell_id,
        "context_mode": service.context_mode.name,
        "generation_mode": service.generation_mode.value,
        "total_questions": total,
        "result_equivalence_count": sum(row["result_equivalent"] is True for row in rows),
        "result_equivalence_rate": sum(row["result_equivalent"] is True for row in rows) / total,
        "parse_success_count": sum(row["parse_success"] for row in rows),
        "plan_acceptance_count": sum(row["plan_accepted"] for row in rows),
        "execution_success_count": sum(row["execution_success"] for row in rows),
        "generation_failure_count": sum(
            row["status"] == TextToSqlStatus.SQL_GENERATION_ERROR.value for row in rows
        ),
        "policy_rejection_count": sum(
            row["status"] == TextToSqlStatus.PLAN_REJECTED.value for row in rows
        ),
        "query_cost_rejection_count": sum(
            row["failure_stage"] == FailureStage.QUERY_COST_REJECTION.value for row in rows
        ),
        "provider_calls_attempted": sum(row["provider_calls_attempted"] for row in rows),
        "provider_calls_succeeded": sum(row["provider_calls_succeeded"] for row in rows),
        "provider_calls_failed": sum(row["provider_calls_failed"] for row in rows),
        "total_prompt_tokens": _sum_optional(row["prompt_tokens"] for row in rows),
        "total_completion_tokens": _sum_optional(row["completion_tokens"] for row in rows),
        "average_prompt_tokens": _average(row["prompt_tokens"] for row in rows),
        "average_completion_tokens": _average(row["completion_tokens"] for row in rows),
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
        "grounding_metrics": _grounding_metrics(rows),
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


def _grounding_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    intent_rows = [row for row in rows if row["intent"] is not None]
    if not intent_rows:
        return {"intent_success_count": 0}
    diagnostics = [
        row["grounding_diagnostics"]
        for row in intent_rows
        if isinstance(row["grounding_diagnostics"], dict)
    ]
    if not diagnostics:
        return {"intent_success_count": len(intent_rows), "diagnostic_count": 0}
    return {
        "intent_success_count": len(intent_rows),
        "diagnostic_count": len(diagnostics),
        "requested_tables_present_count": sum(
            row["requested_tables_present"] is True for row in intent_rows
        ),
        "requested_columns_present_count": sum(
            row["requested_columns_present"] is True for row in intent_rows
        ),
        "sql_intent_table_agreement_count": sum(
            not diagnostic.get("intent_tables_not_used")
            and not diagnostic.get("sql_tables_not_in_intent")
            for diagnostic in diagnostics
        ),
        "sql_intent_column_agreement_count": sum(
            not diagnostic.get("intent_columns_not_used")
            and not diagnostic.get("sql_columns_not_in_intent")
            for diagnostic in diagnostics
        ),
        "limit_agreement_count": sum(
            diagnostic.get("limit_agreement") is not False for diagnostic in diagnostics
        ),
        "join_agreement_count": sum(
            diagnostic.get("join_agreement") is not False for diagnostic in diagnostics
        ),
        "ordering_agreement_count": sum(
            diagnostic.get("ordering_agreement") is not False for diagnostic in diagnostics
        ),
        "aggregation_agreement_count": sum(
            diagnostic.get("aggregation_agreement") is not False for diagnostic in diagnostics
        ),
        "grouping_agreement_count": sum(
            diagnostic.get("grouping_agreement") is not False for diagnostic in diagnostics
        ),
        "window_agreement_count": sum(
            diagnostic.get("window_agreement") is not False for diagnostic in diagnostics
        ),
    }


def _comparison(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    comparison: dict[str, Any] = {
        "cells": {cell: _comparison_metrics(report) for cell, report in reports.items()}
    }
    pairs = {
        "grounding_effect_full": ("full_compact_one_shot", "full_compact_grounded"),
        "grounding_effect_retrieved": (
            "retrieved_bounded_one_shot",
            "retrieved_bounded_grounded",
        ),
        "retrieval_effect_one_shot": (
            "full_compact_one_shot",
            "retrieved_bounded_one_shot",
        ),
        "retrieval_effect_grounded": (
            "full_compact_grounded",
            "retrieved_bounded_grounded",
        ),
    }
    for name, (baseline_key, variant_key) in pairs.items():
        if baseline_key in reports and variant_key in reports:
            comparison[name] = _pair_comparison(
                reports[baseline_key], reports[variant_key]
            )
    return comparison


def _pair_comparison(
    baseline: dict[str, Any], variant: dict[str, Any]
) -> dict[str, Any]:
    metric_keys = (
        "result_equivalence_count",
        "result_equivalence_rate",
        "parse_success_count",
        "plan_acceptance_count",
        "execution_success_count",
        "average_context_tables",
        "average_context_columns",
        "total_prompt_tokens",
        "total_completion_tokens",
        "average_prompt_tokens",
        "average_completion_tokens",
        "average_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "provider_calls_attempted",
        "provider_calls_succeeded",
        "provider_calls_failed",
    )
    delta: dict[str, Any] = {}
    for key in metric_keys:
        before = baseline.get(key)
        after = variant.get(key)
        delta[key] = after - before if before is not None and after is not None else None
    return {
        "baseline": _comparison_metrics(baseline),
        "variant": _comparison_metrics(variant),
        "delta_variant_minus_baseline": delta,
        "category_delta": _category_delta(baseline, variant),
    }


def _comparison_metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report[key]
        for key in (
            "cell_id",
            "context_mode",
            "generation_mode",
            "total_questions",
            "result_equivalence_count",
            "result_equivalence_rate",
            "parse_success_count",
            "plan_acceptance_count",
            "execution_success_count",
            "generation_failure_count",
            "policy_rejection_count",
            "query_cost_rejection_count",
        "provider_calls_attempted",
        "provider_calls_succeeded",
        "provider_calls_failed",
            "total_prompt_tokens",
            "total_completion_tokens",
            "average_prompt_tokens",
            "average_completion_tokens",
            "average_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "failure_taxonomy",
            "grounding_metrics",
        )
    }


def _category_delta(one_shot: dict[str, Any], grounded: dict[str, Any]) -> dict[str, Any]:
    categories = set(one_shot["category_breakdown"]) | set(grounded["category_breakdown"])
    return {
        category: {
            "one_shot": one_shot["category_breakdown"].get(category),
            "grounded": grounded["category_breakdown"].get(category),
            "delta_rate": grounded["category_breakdown"].get(category, {}).get(
                "result_equivalence_rate", 0
            )
            - one_shot["category_breakdown"].get(category, {}).get(
                "result_equivalence_rate", 0
            ),
        }
        for category in sorted(categories)
    }


def _failure_analysis(
    reports: dict[str, dict[str, Any]], metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "source_run_id": metadata["run_id"],
        "dataset_sha256": metadata["dataset_sha256"],
        "evaluator_version": metadata["evaluator_version"],
        "failure_taxonomy": {
            strategy: report["failure_taxonomy"] for strategy, report in reports.items()
        },
        "roadmap_classification": {
            strategy: _roadmap_counts(report["cases"])
            for strategy, report in reports.items()
        },
    }


def _roadmap_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row["failure_class"]:
            if row["failure_class"] in {"WRONG_RATIO_DENOMINATOR", "WRONG_RATIO_SCALING"}:
                counts["B_SEMANTIC_BUSINESS_CONTRACT"] += 1
            else:
                counts["A_BASIC_GENERATION_SCHEMA_REASONING"] += 1
    return dict(sorted(counts.items()))


def _render_report(
    metadata: dict[str, Any], reports: dict[str, dict[str, Any]], comparison: dict[str, Any]
) -> str:
    lines = [
        "# M2.5 Holdout Baseline",
        "",
        "This report is generated from the frozen M2.5 holdout. No repair loop, "
        "semantic layer, value profiling, or golden-example retrieval is used.",
        "",
        f"- Run ID: `{metadata['run_id']}`",
        f"- Dataset SHA-256: `{metadata['dataset_sha256']}`",
        f"- Model: `{metadata['model']}`",
        f"- Experiment: `{metadata['experiment']}`",
        f"- Cells: `{', '.join(metadata['cells'])}`",
        "",
        "## Strategy comparison",
        "",
        "| Metric | M2_ONE_SHOT | M25_GROUNDED | Delta |",
        "|---|---:|---:|---:|",
    ]
    primary = comparison.get("grounding_effect_full")
    if primary:
        one = primary["baseline"]
        grounded = primary["variant"]
        for key, label in (
            ("result_equivalence_count", "Result equivalence count"),
            ("result_equivalence_rate", "Result equivalence rate"),
            ("parse_success_count", "Parse success"),
            ("plan_acceptance_count", "Plan acceptance"),
            ("execution_success_count", "Execution success"),
            ("average_prompt_tokens", "Average input tokens"),
            ("average_completion_tokens", "Average output tokens"),
            ("average_latency_ms", "Average latency (ms)"),
            ("p50_latency_ms", "P50 latency (ms)"),
            ("p95_latency_ms", "P95 latency (ms)"),
            ("provider_calls_attempted", "Provider calls attempted"),
        ):
            before = one[key]
            after = grounded[key]
            delta = after - before if before is not None and after is not None else "n/a"
            lines.append(f"| {label} | {before} | {after} | {delta} |")
    lines.extend(
        [
            "",
            "## Category result equivalence",
            "",
            "| Category | One-shot | Grounded | Delta |",
            "|---|---:|---:|---:|",
        ]
    )
    if primary and "category_delta" in primary:
        for category, values in primary["category_delta"].items():
            lines.append(
                f"| {category} | {values['one_shot']['result_equivalence_rate']:.4f} | "
                f"{values['grounded']['result_equivalence_rate']:.4f} | "
                f"{values['delta_rate']:.4f} |"
            )
    for strategy, report in reports.items():
        lines.extend(
            [
                "",
                f"## {strategy} diagnostics",
                "",
                f"Failure taxonomy: `{json.dumps(report['failure_taxonomy'], sort_keys=True)}`",
                f"Grounding metrics: `{json.dumps(report['grounding_metrics'], sort_keys=True)}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "This is a 54-question frozen ordinary-SQL holdout. It is not the future "
            "M8 benchmark. No causal claim is made beyond this controlled ablation, "
            "and no generation setting was tuned against the holdout.",
            "",
        ]
    )
    return "\n".join(lines)


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


if __name__ == "__main__":
    main()
