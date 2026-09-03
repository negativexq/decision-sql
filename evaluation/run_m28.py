"""Run the M2.8 baseline versus narrow result-shape contract experiment."""

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
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.m27_forensics import (
    context_visibility,
    sha256_json,
    sha256_text,
    sql_signature,
    structural_diff,
)
from evaluation.m28_projection import (
    projection_diagnostics,
    projection_only_failure,
    validate_shape_against_gold,
)
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase
from evaluation.runner import load_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.8 result-shape ablation")
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument(
        "--dataset", type=Path, default=Path("evaluation/datasets/m2_baseline.json")
    )
    parser.add_argument("--results-root", type=Path, default=Path("evaluation/results/m28"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--report", type=Path, default=Path("docs/m28-result-shape-ablation.md"))
    args = parser.parse_args()
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit(
            "M2.8 evaluation requires DECISION_SQL_LLM_API_KEY; no provider calls were made."
        )
    if args.stage == "holdout":
        raise SystemExit("M2.8 holdout evaluation is gated on a positive development screen.")

    cases = load_baseline(args.dataset)
    catalog = build_default_catalog(Base.metadata)

    def build_service(contract: bool) -> tuple[TextToSqlService, OpenAICompatibleProvider]:
        arm_settings = settings.model_copy(
            update={
                "llm_model": "gpt-5.6-luna",
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
        return (
            TextToSqlService(
                resolver,
                provider,
                safety,
                context_mode=SchemaContextMode.FULL_COMPACT,
            ),
            provider,
        )

    baseline_service, baseline_provider = build_service(False)
    contract_service, contract_provider = build_service(True)
    baseline = asyncio.run(evaluate("BASELINE", cases, baseline_service, baseline_provider, False))
    contract = asyncio.run(
        evaluate("RESULT_SHAPE_CONTRACT", cases, contract_service, contract_provider, True)
    )
    _assert_same_context(baseline, contract)

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dataset_sha = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    metadata = {
        "run_id": run_id,
        "stage": args.stage,
        "evaluation_timestamp": datetime.now(UTC).isoformat(),
        "provider": "openai-compatible",
        "model": "gpt-5.6-luna",
        "endpoint_family": "chat_completions",
        "generation_settings": {
            "reasoning_effort": "none",
            "sampling_temperature_requested": None,
            "sampling_temperature_mode": "provider_default",
            "response_format": "json_object",
            "dialect": "postgres",
        },
        "architecture": {
            "baseline": "FULL_COMPACT + ONE_SHOT",
            "contract": "ResultShapeProposal + FULL_COMPACT + ONE_SHOT",
            "query_intent_enabled": False,
            "retrieval_enabled": False,
            "repair_enabled": False,
            "semantic_layer_enabled": False,
        },
        "dataset_path": str(args.dataset),
        "dataset_sha256": dataset_sha,
        "question_count": len(cases),
        "code_commit_sha": _git_sha(),
        "evaluator_version": "m2-result-equivalence-v2-ordinal-columns",
        "model_io_capture": {
            "enabled": True,
            "production_telemetry": False,
            "hidden_reasoning_requested": False,
        },
        "provider_calls_attempted": baseline["provider_calls_attempted"]
        + contract["provider_calls_attempted"],
        "provider_calls_succeeded": baseline["provider_calls_succeeded"]
        + contract["provider_calls_succeeded"],
        "provider_calls_failed": baseline["provider_calls_failed"]
        + contract["provider_calls_failed"],
        "no_adaptive_tuning": True,
        "offline_projection_only_failure_ceiling_from_m27": _existing_projection_ceiling(),
    }
    output_dir = args.results_root / args.stage / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "metadata.json", metadata)
    _write_json(output_dir / "baseline.json", baseline)
    _write_json(output_dir / "result_shape.json", contract)
    comparison = _comparison(baseline, contract)
    _write_json(output_dir / "comparison.json", comparison)
    _write_json(output_dir / "projection_analysis.json", _projection_analysis(baseline, contract))
    _write_json(
        output_dir / "failure_analysis.json", _failure_analysis(baseline, contract, metadata)
    )
    _write_model_io(output_dir / "model_io.jsonl", baseline, contract)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        _render_report(metadata, baseline, contract, comparison), encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(output_dir), "report": str(args.report)}))


async def evaluate(
    arm: str,
    cases: list[BaselineCase],
    service: TextToSqlService,
    provider: OpenAICompatibleProvider,
    contract: bool,
) -> dict[str, Any]:
    rows = [await _evaluate_case(case, service, provider, contract) for case in cases]
    return _summarize(arm, rows)


async def _evaluate_case(
    case: BaselineCase,
    service: TextToSqlService,
    provider: OpenAICompatibleProvider,
    contract: bool,
) -> dict[str, Any]:
    request = TextToSqlRequest(question=case.question, correlation_id=case.id, execute=True)
    result: TextToSqlResult = (
        await service.run_result_shape_contract(request) if contract else await service.run(request)
    )
    captures = provider.consume_model_io_history()
    sql_capture = next((item for item in reversed(captures) if item.operation == "sql"), None)
    shape_capture = next((item for item in captures if item.operation == "result_shape"), None)
    proposal = result.proposal
    gold = service.safety_service.plan(
        SqlCandidate(sql=case.gold_sql, correlation_id=f"gold-{case.id}")
    )
    gold_execution = service.safety_service.execute(gold) if isinstance(gold, QueryPlan) else None
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
    gold_signature = sql_signature(case.gold_sql)
    generated_signature = None
    diff: dict[str, Any] = {}
    if proposal:
        try:
            generated_signature = sql_signature(proposal.sql)
            diff = structural_diff(gold_signature, generated_signature)
        except Exception:
            pass
    visibility = context_visibility(gold_signature, result.context) if result.context else None
    projection = projection_diagnostics(case.gold_sql, proposal.sql if proposal else None)
    shape_quality = validate_shape_against_gold(result.result_shape_proposal, case.gold_sql)
    primary = _primary_cause(result, case, diff, visibility, projection, equivalent)
    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "gold_sql": case.gold_sql,
        "generated_sql": proposal.sql if proposal else None,
        "status": result.status.value,
        "failure_stage": result.failure_stage.value if result.failure_stage else None,
        "provider_error": result.provider_error.model_dump(mode="json")
        if result.provider_error
        else None,
        "result_equivalent": equivalent,
        "equivalence_diagnostic": comparison.diagnostic.value
        if comparison and comparison.diagnostic
        else None,
        "primary_root_cause": primary,
        "gold_signature": gold_signature,
        "generated_signature": generated_signature,
        "structural_diff": diff,
        "context_visibility": visibility,
        "projection_diagnostics": projection,
        "projection_only_failure": projection_only_failure(
            case.gold_sql, proposal.sql if proposal else None, equivalent
        ),
        "result_shape": result.result_shape_proposal.model_dump(mode="json")
        if result.result_shape_proposal
        else None,
        "result_shape_validation": result.result_shape_validation.model_dump(mode="json")
        if result.result_shape_validation
        else None,
        "result_shape_quality": shape_quality,
        "model_io": [_capture_record(item, case, result.context) for item in captures],
        "sql_model_io": _capture_record(sql_capture, case, result.context),
        "shape_model_io": _capture_record(shape_capture, case, result.context),
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
        "shape_input_tokens": result.result_shape_proposal.prompt_tokens
        if result.result_shape_proposal
        else None,
        "shape_output_tokens": result.result_shape_proposal.completion_tokens
        if result.result_shape_proposal
        else None,
        "generation_latency_ms": proposal.latency_ms if proposal else None,
        "shape_latency_ms": result.result_shape_proposal.latency_ms
        if result.result_shape_proposal
        else None,
    }


def _primary_cause(
    result: TextToSqlResult,
    case: BaselineCase,
    diff: dict[str, Any],
    visibility: dict[str, Any] | None,
    projection: dict[str, Any],
    equivalent: bool | None,
) -> str | None:
    if result.status in {
        TextToSqlStatus.SQL_GENERATION_ERROR,
        TextToSqlStatus.RESULT_SHAPE_GENERATION_ERROR,
    }:
        return "PROVIDER_FAILURE"
    if result.failure_stage is FailureStage.POLICY_REJECTION:
        return "POLICY_REJECTION"
    if equivalent is True:
        return None
    if visibility and not all(
        visibility.get(key, True)
        for key in ("gold_tables_visible", "gold_columns_visible", "gold_relationships_visible")
    ):
        return "STRUCTURAL_CONTEXT_INSUFFICIENT"
    if projection.get("status") == "PROJECTION_EXTRA" and not any(
        key.startswith(prefix)
        for prefix in (
            "TABLE_",
            "JOIN_",
            "FILTER_",
            "AGGREGATION_",
            "GROUP_BY_",
            "ORDER_",
            "LIMIT_",
            "WINDOW_",
        )
        for key in diff
    ):
        return "PROJECTION_OVERSELECTION"
    if any(key.startswith("TABLE_") or key.startswith("COLUMN_") for key in diff):
        return "REQUIRED_OBJECT_WRONG"
    if case.category in {"simple_filters", "date_filtering"} and any(
        key.startswith("FILTER_") for key in diff
    ):
        return "FILTER_CONSTRUCTION_ERROR"
    if case.category in {"aggregation", "simple_aggregation", "group_by", "ratios"} and any(
        key.startswith("AGGREGATION_") or key.startswith("GROUP_BY_") for key in diff
    ):
        return "AGGREGATION_GRAIN_ERROR"
    if case.category in {"joins", "multi_table_joins"} and any(
        key.startswith("JOIN_") for key in diff
    ):
        return "JOIN_COMPOSITION_ERROR"
    if case.category == "window_functions" and any(key.startswith("WINDOW_") for key in diff):
        return "WINDOW_COMPOSITION_ERROR"
    if any(key.startswith("ORDER_") or key.startswith("LIMIT_") for key in diff):
        return "ORDER_TOPK_ERROR"
    return "SQL_COMPOSITION_ERROR"


def _summarize(arm: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    return {
        "arm": arm,
        "total_questions": total,
        "result_equivalence_count": sum(row["result_equivalent"] is True for row in rows),
        "result_equivalence_rate": sum(row["result_equivalent"] is True for row in rows) / total,
        "parse_success_count": sum(row["parse_success"] for row in rows),
        "shape_proposal_success_count": sum(row["result_shape"] is not None for row in rows),
        "shape_validation_pass_count": sum(
            bool((row["result_shape_validation"] or {}).get("accepted")) for row in rows
        ),
        "plan_acceptance_count": sum(row["plan_accepted"] for row in rows),
        "execution_success_count": sum(row["execution_success"] for row in rows),
        "policy_rejection_count": sum(row["failure_stage"] == "POLICY_REJECTION" for row in rows),
        "query_cost_rejection_count": sum(
            row["failure_stage"] == "QUERY_COST_REJECTION" for row in rows
        ),
        "generation_failure_count": sum(
            row["failure_stage"] in {"SQL_GENERATION_ERROR", "RESULT_VALIDATION_ERROR"}
            for row in rows
        ),
        "provider_calls_attempted": sum(row["provider_calls_attempted"] for row in rows),
        "provider_calls_succeeded": sum(row["provider_calls_succeeded"] for row in rows),
        "provider_calls_failed": sum(row["provider_calls_failed"] for row in rows),
        "total_input_tokens": _sum_optional([row["input_tokens"] for row in rows]),
        "total_output_tokens": _sum_optional([row["output_tokens"] for row in rows]),
        "total_shape_input_tokens": _sum_optional([row["shape_input_tokens"] for row in rows]),
        "total_shape_output_tokens": _sum_optional([row["shape_output_tokens"] for row in rows]),
        "average_input_tokens": _average([row["input_tokens"] for row in rows]),
        "average_output_tokens": _average([row["output_tokens"] for row in rows]),
        "average_shape_input_tokens": _average([row["shape_input_tokens"] for row in rows]),
        "average_shape_output_tokens": _average([row["shape_output_tokens"] for row in rows]),
        "average_latency_ms": _average([row["generation_latency_ms"] for row in rows]),
        "average_end_to_end_latency_ms": _average([_end_to_end_latency(row) for row in rows]),
        "shape_average_latency_ms": _average([row["shape_latency_ms"] for row in rows]),
        "p50_latency_ms": _percentile([row["generation_latency_ms"] for row in rows], 0.5),
        "p95_latency_ms": _percentile([row["generation_latency_ms"] for row in rows], 0.95),
        "p50_end_to_end_latency_ms": _percentile([_end_to_end_latency(row) for row in rows], 0.5),
        "p95_end_to_end_latency_ms": _percentile([_end_to_end_latency(row) for row in rows], 0.95),
        "failure_taxonomy": dict(
            Counter(row["primary_root_cause"] for row in rows if row["primary_root_cause"])
        ),
        "projection_status_counts": dict(
            Counter(row["projection_diagnostics"].get("status") for row in rows)
        ),
        "projection_only_failure_count": sum(row["projection_only_failure"] for row in rows),
        "projection_metrics": _projection_metrics(rows),
        "result_shape_quality": _shape_quality_metrics(rows),
        "gold_visibility": _visibility(rows),
        "category_breakdown": {
            category: {
                "total": len(items),
                "result_equivalence_count": sum(
                    item["result_equivalent"] is True for item in items
                ),
                "result_equivalence_rate": sum(item["result_equivalent"] is True for item in items)
                / len(items),
            }
            for category, items in sorted(categories.items())
        },
        "cases": rows,
    }


def _projection_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    statuses = Counter(row["projection_diagnostics"].get("status") for row in rows)
    return {
        "exact_projection_arity_rate": sum(
            row["projection_diagnostics"].get("arity_exact", False) for row in rows
        )
        / total,
        "extra_projection_rate": statuses["PROJECTION_EXTRA"] / total,
        "missing_projection_rate": statuses["PROJECTION_MISSING"] / total,
        "exact_physical_projection_match_rate": sum(
            row["projection_diagnostics"].get("physical_projection_exact", False) for row in rows
        )
        / total,
    }


def _shape_quality_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shaped = [row["result_shape_quality"] for row in rows if row["result_shape_quality"]]
    if not shaped:
        return {}
    return {
        "output_arity_accuracy": _average([item["output_arity_exact"] for item in shaped]),
        "required_output_recall": _average([item["required_output_recall"] for item in shaped]),
        "extra_output_precision": _average(
            [item["output_arity_exact"] and item["extra_output_count"] == 0 for item in shaped]
        ),
    }


def _comparison(baseline: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    pairwise: Counter[str] = Counter()
    baseline_cases = {row["id"]: row for row in baseline["cases"]}
    contract_cases = {row["id"]: row for row in contract["cases"]}
    for case_id in baseline_cases:
        left = baseline_cases[case_id]["result_equivalent"] is True
        right = contract_cases[case_id]["result_equivalent"] is True
        pairwise[
            "BOTH_CORRECT"
            if left and right
            else "BASELINE_ONLY_CORRECT"
            if left
            else "CONTRACT_ONLY_CORRECT"
            if right
            else "BOTH_INCORRECT"
        ] += 1
    return {
        "baseline": _arm_metrics(baseline),
        "result_shape_contract": _arm_metrics(contract),
        "delta_contract_minus_baseline": {
            "result_equivalence_count": contract["result_equivalence_count"]
            - baseline["result_equivalence_count"],
            "result_equivalence_rate": contract["result_equivalence_rate"]
            - baseline["result_equivalence_rate"],
            "average_end_to_end_latency_ms": _difference(
                contract["average_end_to_end_latency_ms"], baseline["average_end_to_end_latency_ms"]
            ),
        },
        "pairwise_outcomes": dict(sorted(pairwise.items())),
        "category_delta": {
            category: {
                "baseline": baseline["category_breakdown"].get(category),
                "contract": contract["category_breakdown"].get(category),
                "delta_rate": _difference(
                    contract["category_breakdown"].get(category, {}).get("result_equivalence_rate"),
                    baseline["category_breakdown"].get(category, {}).get("result_equivalence_rate"),
                ),
            }
            for category in sorted(
                set(baseline["category_breakdown"]) | set(contract["category_breakdown"])
            )
        },
    }


def _arm_metrics(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "arm",
        "total_questions",
        "result_equivalence_count",
        "result_equivalence_rate",
        "parse_success_count",
        "shape_proposal_success_count",
        "shape_validation_pass_count",
        "plan_acceptance_count",
        "execution_success_count",
        "policy_rejection_count",
        "query_cost_rejection_count",
        "generation_failure_count",
        "provider_calls_attempted",
        "provider_calls_succeeded",
        "provider_calls_failed",
        "total_input_tokens",
        "total_output_tokens",
        "total_shape_input_tokens",
        "total_shape_output_tokens",
        "average_input_tokens",
        "average_output_tokens",
        "average_shape_input_tokens",
        "average_shape_output_tokens",
        "average_latency_ms",
        "average_end_to_end_latency_ms",
        "shape_average_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "p50_end_to_end_latency_ms",
        "p95_end_to_end_latency_ms",
        "failure_taxonomy",
        "projection_metrics",
        "result_shape_quality",
        "projection_only_failure_count",
    )
    return {key: report[key] for key in keys}


def _projection_analysis(baseline: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "development_only": True,
        "baseline": baseline["projection_metrics"],
        "result_shape_contract": contract["projection_metrics"],
        "projection_only_failure_ceiling": baseline["projection_only_failure_count"],
        "existing_m27_projection_only_failure_ceiling": _existing_projection_ceiling(),
        "simple_filters": {
            arm: [
                {
                    "id": row["id"],
                    "result_equivalent": row["result_equivalent"],
                    "projection": row["projection_diagnostics"],
                    "shape_validation": row["result_shape_validation"],
                    "policy_outcome": row["failure_stage"],
                }
                for row in report["cases"]
                if row["category"] == "simple_filters"
            ]
            for arm, report in (("baseline", baseline), ("result_shape_contract", contract))
        },
    }


def _existing_projection_ceiling() -> int | None:
    path = Path("evaluation/results/m27/dev/20260903T023000Z/recomputed-v2/luna.json")
    if not path.exists():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    return sum(
        projection_only_failure(
            row["gold_sql"], row.get("generated_sql"), row.get("result_equivalent")
        )
        for row in report.get("cases", [])
    )


def _failure_analysis(
    baseline: dict[str, Any], contract: dict[str, Any], metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "dataset_sha256": metadata["dataset_sha256"],
        "evaluator_version": metadata["evaluator_version"],
        "root_cause_counts": {
            "baseline": baseline["failure_taxonomy"],
            "result_shape_contract": contract["failure_taxonomy"],
        },
        "projection_status_counts": {
            "baseline": baseline["projection_status_counts"],
            "result_shape_contract": contract["projection_status_counts"],
        },
        "projection_only_failure_ceiling": baseline["projection_only_failure_count"],
    }


def _capture_record(
    capture: ModelIOCapture | None, case: BaselineCase, context: Any
) -> dict[str, Any] | None:
    if capture is None:
        return None
    payload = capture.model_dump(mode="json")
    payload.update(
        {
            "question_id": case.id,
            "category": case.category,
            "question_sha256": sha256_text(case.question),
            "system_prompt_sha256": sha256_text(capture.messages[0]["content"]),
            "schema_context_sha256": sha256_text(capture.serialized_schema_context),
            "input_sha256": sha256_json(capture.messages),
            "context_metadata": context.context_metadata.model_dump(mode="json")
            if context
            else None,
        }
    )
    return payload


def _assert_same_context(baseline: dict[str, Any], contract: dict[str, Any]) -> None:
    for left, right in zip(baseline["cases"], contract["cases"], strict=True):
        left_io = left.get("sql_model_io")
        right_io = right.get("sql_model_io")
        if left_io and right_io:
            if left_io["question_sha256"] != right_io["question_sha256"]:
                raise RuntimeError(f"Question mismatch for {left['id']}")
            if left_io["schema_context_sha256"] != right_io["schema_context_sha256"]:
                raise RuntimeError(f"FULL_COMPACT context mismatch for {left['id']}")


def _visibility(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    visible = [row["context_visibility"] for row in rows if row["context_visibility"]]
    if not visible:
        return {"tables": None, "columns": None, "relationships": None}
    return {
        "tables": sum(item["gold_tables_visible"] for item in visible) / len(visible),
        "columns": sum(item["gold_columns_visible"] for item in visible) / len(visible),
        "relationships": sum(item["gold_relationships_visible"] for item in visible) / len(visible),
    }


def _end_to_end_latency(row: dict[str, Any]) -> float | None:
    values = [row["generation_latency_ms"], row["shape_latency_ms"]]
    numbers = [value for value in values if isinstance(value, (int, float))]
    return sum(numbers) if numbers else None


def _write_model_io(path: Path, baseline: dict[str, Any], contract: dict[str, Any]) -> None:
    records: list[str] = []
    for arm, report in (("BASELINE", baseline), ("RESULT_SHAPE_CONTRACT", contract)):
        for row in report["cases"]:
            for capture in row["model_io"]:
                if capture is not None:
                    capture["experiment_arm"] = arm
                    records.append(json.dumps(capture, sort_keys=True))
    path.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")


def _render_report(
    metadata: dict[str, Any],
    baseline: dict[str, Any],
    contract: dict[str, Any],
    comparison: dict[str, Any],
) -> str:
    result_line = (
        f"| Result equivalence | {baseline['result_equivalence_count']}/"
        f"{baseline['total_questions']} ({baseline['result_equivalence_rate']:.2%}) | "
        f"{contract['result_equivalence_count']}/{contract['total_questions']} "
        f"({contract['result_equivalence_rate']:.2%}) | "
        f"{comparison['delta_contract_minus_baseline']['result_equivalence_count']} "
        f"({comparison['delta_contract_minus_baseline']['result_equivalence_rate']:.2%}) |"
    )
    latency_line = (
        "| Avg end-to-end generation latency | "
        f"{baseline['average_end_to_end_latency_ms']} ms | "
        f"{contract['average_end_to_end_latency_ms']} ms | "
        f"{comparison['delta_contract_minus_baseline']['average_end_to_end_latency_ms']} ms |"
    )
    calls_line = (
        f"| Provider calls | {baseline['provider_calls_attempted']} | "
        f"{contract['provider_calls_attempted']} | — |"
    )
    projection_line = (
        f"| Extra projection rate | "
        f"{baseline['projection_metrics']['extra_projection_rate']:.2%} | "
        f"{contract['projection_metrics']['extra_projection_rate']:.2%} | — |"
    )
    lines = [
        "# M2.8 Result-Shape Contract Ablation",
        "",
        "This is a development/diagnostic run on the consumed M2 dataset; "
        "it is not unbiased holdout evidence.",
        "",
        f"- Dataset SHA-256: `{metadata['dataset_sha256']}`",
        "- Model: `gpt-5.6-luna`; reasoning `none`; temperature omitted/provider default",
        "- Both arms use `FULL_COMPACT` and the same M1 safety boundary.",
        "- ResultShape is narrow and untrusted: outputs, shape, explicit limit, "
        "and explicit order direction only.",
        "",
        "| Metric | Baseline | ResultShape contract | Delta |",
        "|---|---:|---:|---:|",
        result_line,
        calls_line,
        f"| Input tokens | {baseline['total_input_tokens']} | "
        f"{contract['total_input_tokens']} + shape {contract['total_shape_input_tokens']} | — |",
        f"| Output tokens | {baseline['total_output_tokens']} | "
        f"{contract['total_output_tokens']} + shape {contract['total_shape_output_tokens']} | — |",
        projection_line,
        latency_line,
        "",
        f"Pairwise outcomes: `{json.dumps(comparison['pairwise_outcomes'], sort_keys=True)}`",
        "",
        "No M2.8 holdout was created or consumed by this development run.",
    ]
    return "\n".join(lines) + "\n"


def _sum_optional(values: list[Any]) -> int | None:
    numbers = [value for value in values if isinstance(value, int)]
    return sum(numbers) if numbers else None


def _average(values: list[Any]) -> float | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


def _percentile(values: list[Any], quantile: float) -> float | None:
    numbers = sorted(value for value in values if isinstance(value, (int, float)))
    if not numbers:
        return None
    position = (len(numbers) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(numbers) - 1)
    return numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)


def _difference(left: Any, right: Any) -> float | int | None:
    return (
        left - right if isinstance(left, (int, float)) and isinstance(right, (int, float)) else None
    )


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return result.stdout.strip() or None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
