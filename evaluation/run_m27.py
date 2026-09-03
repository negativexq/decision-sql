"""Run the M2.7 Luna-versus-Terra model-capacity experiment."""

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
from app.generation.provider import ModelIOCapture, OpenAICompatibleProvider
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import GenerationMode
from app.text_to_sql.service import TextToSqlService
from evaluation.m25_classification import classify_sql_failure
from evaluation.m27_forensics import (
    aggregate_column_confusions,
    classify_forensic_cause,
    column_confusions,
    context_visibility,
    sha256_json,
    sha256_text,
    sql_signature,
    structural_diff,
)
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase
from evaluation.runner import load_baseline

EXPECTED_HOLDOUT_SHA = "a540298e2776842c6b345f9984b21941c4d0f964447518cbaa12f98f34b8074c"
MODELS = ("gpt-5.6-luna", "gpt-5.6-terra")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.7 model-capacity experiment")
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--results-root", type=Path, default=Path("evaluation/results/m27"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--report", type=Path, default=Path("docs/m27-model-forensics.md"))
    args = parser.parse_args()
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("M2.7 requires DECISION_SQL_LLM_API_KEY; no provider calls were made.")
    dataset = args.dataset or (
        Path("evaluation/datasets/m2_baseline.json")
        if args.stage == "dev"
        else Path("evaluation/datasets/m27_holdout.json")
    )
    if args.stage == "holdout":
        _verify_holdout(dataset)
    cases = load_baseline(dataset)
    catalog = build_default_catalog(Base.metadata)

    def build_service(model: str) -> tuple[TextToSqlService, OpenAICompatibleProvider]:
        arm_settings = settings.model_copy(
            update={
                "llm_model": model,
                "llm_temperature": None,
                "llm_reasoning_effort": "none",
                "eval_capture_model_io": True,
            }
        )
        resolver = SchemaContextResolver(
            catalog,
            top_k=arm_settings.schema_top_k,
            max_tables=arm_settings.max_context_tables,
            max_columns_per_table=arm_settings.max_columns_per_table,
            relationship_depth=arm_settings.relationship_depth,
        )
        provider = OpenAICompatibleProvider(arm_settings)
        safety = SqlSafetyService(
            build_reader_engine(arm_settings), settings=arm_settings, catalog=catalog
        )
        service = TextToSqlService(
            resolver,
            provider,
            safety,
            context_mode=SchemaContextMode.FULL_COMPACT,
            generation_mode=GenerationMode.ONE_SHOT,
        )
        return service, provider

    reports: dict[str, dict[str, Any]] = {}
    for model in MODELS:
        service, provider = build_service(model)
        reports[model] = asyncio.run(evaluate(model, cases, service, provider))
    _assert_paired_inputs(reports)

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dataset_sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
    metadata = {
        "run_id": run_id,
        "stage": args.stage,
        "evaluation_timestamp": datetime.now(UTC).isoformat(),
        "provider": "openai-compatible",
        "models": list(MODELS),
        "endpoint_family": "chat_completions",
        "generation_settings": {
            "reasoning_effort": "none",
            "sampling_temperature_requested": None,
            "sampling_temperature_mode": "provider_default",
            "response_format": "json_object",
            "dialect": "postgres",
        },
        "architecture": {
            "schema_context_mode": "FULL_COMPACT",
            "generation_mode": "ONE_SHOT",
            "query_intent_enabled": False,
            "retrieval_enabled": False,
            "repair_enabled": False,
        },
        "model_io_capture": {
            "enabled": True,
            "production_telemetry": False,
            "hidden_reasoning_requested": False,
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
    for model, report in reports.items():
        filename = "luna.json" if model.endswith("luna") else "terra.json"
        _write_json(output_dir / filename, report)
        _write_model_io(output_dir / f"{filename.removesuffix('.json')}_model_io.jsonl", report)
    comparison = _comparison(reports)
    _write_json(output_dir / "comparison.json", comparison)
    _write_json(output_dir / "failure_analysis.json", _failure_analysis(reports, metadata))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_render_report(metadata, reports, comparison), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "report": str(args.report)}))


async def evaluate(
    model: str,
    cases: list[BaselineCase],
    service: TextToSqlService,
    provider: OpenAICompatibleProvider,
) -> dict[str, Any]:
    rows = [await _evaluate_case(case, service, provider) for case in cases]
    return _summarize(model, rows)


async def _evaluate_case(
    case: BaselineCase,
    service: TextToSqlService,
    provider: OpenAICompatibleProvider,
) -> dict[str, Any]:
    result = await service.run(
        TextToSqlRequest(question=case.question, correlation_id=case.id, execute=True)
    )
    capture = provider.consume_model_io()
    gold_signature = sql_signature(case.gold_sql)
    generated_signature: dict[str, Any] | None = None
    diff: dict[str, Any] = {}
    visibility = context_visibility(gold_signature, result.context) if result.context else None
    proposal = result.proposal
    if proposal is not None:
        try:
            generated_signature = sql_signature(proposal.sql)
            diff = structural_diff(gold_signature, generated_signature)
        except Exception:
            generated_signature = None

    gold_execution = None
    gold = service.safety_service.plan(
        SqlCandidate(sql=case.gold_sql, correlation_id=f"gold-{case.id}")
    )
    if isinstance(gold, QueryPlan):
        gold_execution = service.safety_service.execute(gold)
    comparison = None
    if isinstance(result.execution, QueryExecution) and isinstance(gold_execution, QueryExecution):
        comparison = assess_query_results(
            result.execution,
            gold_execution,
            order_sensitive=case.order_sensitive,
            actual_sql=proposal.sql if proposal else None,
            expected_sql=case.gold_sql,
        )
    equivalent = comparison.equivalent if comparison else None
    failure_class = (
        classify_sql_failure(case, proposal.sql)
        if equivalent is False and proposal
        else None
    )
    forensic_cause = classify_forensic_cause(
        generated_sql=proposal.sql if proposal else None,
        failure_stage=result.failure_stage.value if result.failure_stage else None,
        gold_signature=gold_signature,
        generated_signature=generated_signature,
        diff=diff,
        visibility=visibility,
        equivalent=equivalent,
    )
    model_io = _capture_record(capture, case, result.context)
    return {
                "id": case.id,
                "category": case.category,
                "question": case.question,
                "gold_sql": case.gold_sql,
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
        "forensic_cause": forensic_cause,
        "gold_signature": gold_signature,
        "generated_signature": generated_signature,
        "structural_diff": diff,
        "context_visibility": visibility,
        "column_confusions": (
            column_confusions(gold_signature, generated_signature, result.context)
            if generated_signature is not None and result.context
            else []
        ),
        "model_io": model_io,
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


def _capture_record(
    capture: ModelIOCapture | None,
    case: BaselineCase,
    context: Any,
) -> dict[str, Any] | None:
    if capture is None:
        return None
    payload = capture.model_dump(mode="json")
    payload.update(
        {
            "question_id": case.id,
            "category": case.category,
            "system_prompt_sha256": sha256_text(capture.messages[0]["content"]),
            "schema_context_sha256": sha256_text(capture.serialized_schema_context),
            "input_sha256": sha256_json(capture.messages),
            "context_metadata": (
                context.context_metadata.model_dump(mode="json") if context else None
            ),
        }
    )
    return payload


def _summarize(model: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    visibility_rows = [row["context_visibility"] for row in rows if row["context_visibility"]]
    diff_counts: Counter[str] = Counter()
    confusion_rows: list[dict[str, Any]] = []
    for row in rows:
        diff_counts.update(row["structural_diff"].keys())
        confusion_rows.extend(row["column_confusions"])
    return {
        "model": model,
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
        "p50_latency_ms": _percentile((row["generation_latency_ms"] for row in rows), 0.5),
        "p95_latency_ms": _percentile((row["generation_latency_ms"] for row in rows), 0.95),
        "failure_taxonomy": dict(
            Counter(row["failure_class"] for row in rows if row["failure_class"])
        ),
        "forensic_cause_taxonomy": dict(
            Counter(row["forensic_cause"] for row in rows if row["forensic_cause"])
        ),
        "structural_diff_counts": dict(diff_counts),
        "column_confusions": aggregate_column_confusions(confusion_rows),
        "model_io_capture_count": sum(row["model_io"] is not None for row in rows),
        "gold_table_visibility_rate": _visibility_rate(visibility_rows, "gold_tables_visible"),
        "gold_column_visibility_rate": _visibility_rate(
            visibility_rows, "gold_columns_visible"
        ),
        "gold_relationship_visibility_rate": _visibility_rate(
            visibility_rows, "gold_relationships_visible"
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
    luna = reports[MODELS[0]]
    terra = reports[MODELS[1]]
    pairwise: Counter[str] = Counter()
    for luna_case, terra_case in zip(luna["cases"], terra["cases"], strict=True):
        luna_correct = luna_case["result_equivalent"] is True
        terra_correct = terra_case["result_equivalent"] is True
        pairwise[
            "BOTH_CORRECT"
            if luna_correct and terra_correct
            else "LUNA_ONLY_CORRECT"
            if luna_correct
            else "TERRA_ONLY_CORRECT"
            if terra_correct
            else "BOTH_INCORRECT"
        ] += 1
    return {
        "luna": _comparison_metrics(luna),
        "terra": _comparison_metrics(terra),
        "delta_terra_minus_luna": {
            "result_equivalence_count": terra["result_equivalence_count"]
            - luna["result_equivalence_count"],
            "result_equivalence_rate": terra["result_equivalence_rate"]
            - luna["result_equivalence_rate"],
            "average_latency_ms": _difference(
                terra["average_latency_ms"], luna["average_latency_ms"]
            ),
            "average_input_tokens": _difference(
                terra["average_input_tokens"], luna["average_input_tokens"]
            ),
            "average_output_tokens": _difference(
                terra["average_output_tokens"], luna["average_output_tokens"]
            ),
        },
        "pairwise_outcomes": dict(sorted(pairwise.items())),
        "category_delta": _category_delta(luna, terra),
    }


def _comparison_metrics(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "model",
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
        "forensic_cause_taxonomy",
        "structural_diff_counts",
        "column_confusions",
        "gold_table_visibility_rate",
        "gold_column_visibility_rate",
        "gold_relationship_visibility_rate",
    )
    return {key: report[key] for key in keys}


def _category_delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    categories = set(left["category_breakdown"]) | set(right["category_breakdown"])
    return {
        category: {
            "luna": left["category_breakdown"].get(category),
            "terra": right["category_breakdown"].get(category),
            "delta_rate": _difference(
                right["category_breakdown"].get(category, {}).get("result_equivalence_rate"),
                left["category_breakdown"].get(category, {}).get("result_equivalence_rate"),
            ),
        }
        for category in sorted(categories)
    }


def _failure_analysis(
    reports: dict[str, dict[str, Any]], metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "dataset_sha256": metadata["dataset_sha256"],
        "code_commit_sha": metadata["code_commit_sha"],
        "failure_taxonomy": {
            model: report["failure_taxonomy"] for model, report in reports.items()
        },
        "forensic_cause_taxonomy": {
            model: report["forensic_cause_taxonomy"] for model, report in reports.items()
        },
        "structural_diff_counts": {
            model: report["structural_diff_counts"] for model, report in reports.items()
        },
        "column_confusions": {
            model: report["column_confusions"] for model, report in reports.items()
        },
        "visibility": {
            model: {
                "tables": report["gold_table_visibility_rate"],
                "columns": report["gold_column_visibility_rate"],
                "relationships": report["gold_relationship_visibility_rate"],
            }
            for model, report in reports.items()
        },
        "pairwise_outcomes": _comparison(reports)["pairwise_outcomes"],
    }


def _assert_paired_inputs(reports: dict[str, dict[str, Any]]) -> None:
    luna_cases = reports[MODELS[0]]["cases"]
    terra_cases = reports[MODELS[1]]["cases"]
    for luna_case, terra_case in zip(luna_cases, terra_cases, strict=True):
        luna_io = luna_case["model_io"]
        terra_io = terra_case["model_io"]
        if not luna_io or not terra_io:
            raise RuntimeError(f"Missing model I/O capture for paired case {luna_case['id']}")
        if luna_io["input_sha256"] != terra_io["input_sha256"]:
            raise RuntimeError(f"Model-visible input hash mismatch for {luna_case['id']}")
        if luna_io["schema_context_sha256"] != terra_io["schema_context_sha256"]:
            raise RuntimeError(f"Schema context hash mismatch for {luna_case['id']}")


def _verify_holdout(dataset: Path) -> None:
    actual = hashlib.sha256(dataset.read_bytes()).hexdigest()
    if actual != EXPECTED_HOLDOUT_SHA:
        raise SystemExit("M2.7 holdout SHA-256 mismatch; evaluation stopped.")


def _write_model_io(path: Path, report: dict[str, Any]) -> None:
    records = []
    for case in report["cases"]:
        if case["model_io"] is not None:
            records.append(json.dumps(case["model_io"], sort_keys=True))
    path.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")


def _render_report(
    metadata: dict[str, Any], reports: dict[str, dict[str, Any]], comparison: dict[str, Any]
) -> str:
    luna = reports[MODELS[0]]
    terra = reports[MODELS[1]]
    delta = comparison["delta_terra_minus_luna"]
    lines = [
        "# M2.7 Model Capacity and I/O Forensics",
        "",
        f"- Stage: `{metadata['stage']}`",
        f"- Dataset SHA-256: `{metadata['dataset_sha256']}`",
        "- Models: `gpt-5.6-luna` vs `gpt-5.6-terra`",
        "- Architecture: `FULL_COMPACT + ONE_SHOT` for both arms",
        "- Reasoning: `none`; temperature omitted/provider default",
        "- QueryIntent/retrieval/repair: disabled",
        "",
        "| Metric | Luna | Terra | Delta |",
        "|---|---:|---:|---:|",
        (
            f"| Result equivalence | {luna['result_equivalence_count']}/"
            f"{luna['total_questions']} ({luna['result_equivalence_rate']:.2%}) | "
            f"{terra['result_equivalence_count']}/{terra['total_questions']} "
            f"({terra['result_equivalence_rate']:.2%}) | "
            f"{delta['result_equivalence_count']} "
            f"({delta['result_equivalence_rate']:.2%}) |"
        ),
        (
            f"| Input tokens | {luna['total_input_tokens']} | "
            f"{terra['total_input_tokens']} | "
            f"{delta['average_input_tokens']} avg/question |"
        ),
        (
            f"| Output tokens | {luna['total_output_tokens']} | "
            f"{terra['total_output_tokens']} | "
            f"{delta['average_output_tokens']} avg/question |"
        ),
        (
            f"| Reasoning tokens | {luna['total_reasoning_tokens']} | "
            f"{terra['total_reasoning_tokens']} | — |"
        ),
        (
            f"| Average latency | {luna['average_latency_ms']} ms | "
            f"{terra['average_latency_ms']} ms | {delta['average_latency_ms']} ms |"
        ),
        (
            f"| P95 latency | {luna['p95_latency_ms']} ms | "
            f"{terra['p95_latency_ms']} ms | "
            f"{_difference(terra['p95_latency_ms'], luna['p95_latency_ms'])} ms |"
        ),
        "",
        f"Pairwise outcomes: `{json.dumps(comparison['pairwise_outcomes'], sort_keys=True)}`",
        "",
        (
            "Model I/O capture is evaluation-only and excludes credentials, headers, "
            "result rows, and hidden reasoning."
        ),
        "No pricing configuration is available; API cost was not computed.",
        "",
        "## Stratified model-I/O cases",
        "",
        (
            "The following cases are selected offline after both arms completed. "
            "No hidden reasoning is included."
        ),
    ]
    for luna_case, terra_case in _select_forensic_cases(reports):
        lines.extend(
            (
                "",
                f"### {luna_case['id']} — {luna_case['category']}",
                "",
                f"Question: {luna_case['question']}",
                "",
                "Model-visible schema excerpt:",
                "",
                "```text",
                _schema_excerpt(luna_case),
                "```",
                "",
                f"Gold SQL: `{luna_case['gold_sql']}`",
                f"Luna SQL: `{luna_case['generated_sql'] or 'unavailable'}`",
                f"Terra SQL: `{terra_case['generated_sql'] or 'unavailable'}`",
                "",
                (
                    "Luna structural diff: "
                    f"`{json.dumps(luna_case['structural_diff'], sort_keys=True)}`"
                ),
                (
                    "Terra structural diff: "
                    f"`{json.dumps(terra_case['structural_diff'], sort_keys=True)}`"
                ),
                (
                    f"Outcome: Luna={luna_case['result_equivalent']}, "
                    f"Terra={terra_case['result_equivalent']}"
                ),
                (
                    f"Forensic diagnosis: Luna={luna_case['forensic_cause']}, "
                    f"Terra={terra_case['forensic_cause']}"
                ),
            )
        )
    return "\n".join(lines) + "\n"


def _select_forensic_cases(
    reports: dict[str, dict[str, Any]], limit: int = 12
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    luna_by_id = {case["id"]: case for case in reports[MODELS[0]]["cases"]}
    terra_by_id = {case["id"]: case for case in reports[MODELS[1]]["cases"]}
    pairs = [
        (luna_case, terra_by_id[luna_case["id"]])
        for luna_case in reports[MODELS[0]]["cases"]
    ]
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    selected_ids: set[str] = set()
    for category in sorted({case["category"] for case in luna_by_id.values()}):
        category_pairs = [pair for pair in pairs if pair[0]["category"] == category]
        category_pairs.sort(key=_forensic_priority, reverse=True)
        selected.append(category_pairs[0])
        selected_ids.add(category_pairs[0][0]["id"])
    remaining = [pair for pair in pairs if pair[0]["id"] not in selected_ids]
    remaining.sort(key=_forensic_priority, reverse=True)
    selected.extend(remaining[: max(0, limit - len(selected))])
    return selected[:limit]


def _forensic_priority(pair: tuple[dict[str, Any], dict[str, Any]]) -> tuple[int, int]:
    luna_case, terra_case = pair
    disagreement = int(
        luna_case["result_equivalent"] is not terra_case["result_equivalent"]
    )
    incorrect = int(
        luna_case["result_equivalent"] is not True
        or terra_case["result_equivalent"] is not True
    )
    return disagreement, incorrect


def _schema_excerpt(case: dict[str, Any], max_chars: int = 2400) -> str:
    model_io = case.get("model_io") or {}
    schema = model_io.get("serialized_schema_context")
    if not isinstance(schema, str):
        return "unavailable"
    tables = set(case["gold_signature"]["tables"])
    blocks = schema.split("\n\n")
    relevant = [
        block
        for block in blocks
        if any(block.startswith(f"[Table] {table}") for table in tables)
        or block.startswith("[Relationships]")
    ]
    excerpt = "\n\n".join(relevant)
    return excerpt[:max_chars]


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


def _visibility_rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(bool(row[key]) for row in rows) / len(rows)


if __name__ == "__main__":
    main()
