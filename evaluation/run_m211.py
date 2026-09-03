"""Run the M2.11 narrow top-k/ratio/window decomposition ablation."""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from app.catalog.default import build_default_catalog
from app.config import get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.hard_query_plans import (
    OperationPlan,
    validate_operation_plan_visibility,
)
from app.generation.provider import LLMProviderError, ModelIOCapture, OpenAICompatibleProvider
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlResult
from app.text_to_sql.service import TextToSqlService
from evaluation.m27_failure_mechanism import analyze_row
from evaluation.m27_forensics import sha256_json, sha256_text, sql_signature, structural_diff
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase
from evaluation.runner import load_baseline

M2_DATASET = Path("evaluation/datasets/m2_baseline.json")
M2_DATASET_SHA = "5cf5a80366debff4efd6e33e5ea6ee1f668aa870f770d2982a1f0396d014cf87"
HOLDOUT = Path("evaluation/datasets/m211_holdout.json")
HOLDOUT_MANIFEST = Path("evaluation/datasets/m211_holdout_manifest.json")
OPERATIONS = ("top_k", "ratios", "window_functions")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.11 decomposition ablation")
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--dataset", type=Path, default=M2_DATASET)
    parser.add_argument("--categories", default=",".join(OPERATIONS))
    parser.add_argument("--results-root", type=Path, default=Path("evaluation/results/m211"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--report", type=Path, default=Path("docs/m211-narrow-decomposition-ablation.md")
    )
    args = parser.parse_args()
    categories = tuple(item.strip() for item in args.categories.split(",") if item.strip())
    if any(category not in OPERATIONS for category in categories):
        raise SystemExit(f"M2.11 categories must be one of {OPERATIONS}")
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("M2.11 requires DECISION_SQL_LLM_API_KEY; no provider calls were made.")
    if args.stage == "dev":
        _verify_dev_source(args.dataset, categories)
        cases = _load_dev_slice(args.dataset, categories)
    else:
        _verify_holdout(args.dataset)
        cases = [case for case in load_baseline(args.dataset) if case.category in categories]
    if not cases:
        raise SystemExit("M2.11 selection produced no cases")

    catalog = build_default_catalog(Base.metadata)
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

    def build_service(provider: OpenAICompatibleProvider) -> TextToSqlService:
        return TextToSqlService(
            resolver,
            provider,
            SqlSafetyService(
                build_reader_engine(arm_settings), settings=arm_settings, catalog=catalog
            ),
            context_mode=SchemaContextMode.FULL_COMPACT,
        )

    baseline_provider = OpenAICompatibleProvider(arm_settings)
    decomposed_provider = OpenAICompatibleProvider(arm_settings)
    baseline_service = build_service(baseline_provider)
    decomposed_service = build_service(decomposed_provider)
    baseline, decomposed = asyncio.run(
        evaluate_pair(
            cases,
            resolver,
            baseline_service,
            baseline_provider,
            decomposed_service,
            decomposed_provider,
        )
    )
    _assert_paired_inputs(baseline, decomposed)

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.results_root / args.stage / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    source_sha = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    metadata = _metadata(
        args, categories, source_sha, cases, baseline, decomposed, run_id, arm_settings
    )
    _write(output_dir / "metadata.json", metadata)
    _write(output_dir / "baseline.json", baseline)
    _write(output_dir / "decomposed.json", decomposed)
    _write(output_dir / "comparison.json", _comparison(baseline, decomposed))
    _write(output_dir / "plan_quality.json", _plan_quality(decomposed["cases"]))
    _write(output_dir / "failure_analysis.json", _failure_analysis(baseline, decomposed))
    _write(output_dir / "policy_shadow_analysis.json", _policy_shadow(decomposed["cases"]))
    _write(output_dir / "category_analysis.json", _category_analysis(baseline, decomposed))
    _write_jsonl(output_dir / "model_io.jsonl", _model_io_rows(baseline, decomposed))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_render_report(metadata, baseline, decomposed), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "report": str(args.report)}))


async def evaluate_pair(
    cases: list[BaselineCase],
    resolver: SchemaContextResolver,
    baseline_service: TextToSqlService,
    baseline_provider: OpenAICompatibleProvider,
    decomposed_service: TextToSqlService,
    decomposed_provider: OpenAICompatibleProvider,
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_rows: list[dict[str, Any]] = []
    decomposed_rows: list[dict[str, Any]] = []
    for case in cases:
        baseline_result = await baseline_service.run(
            TextToSqlRequest(question=case.question, correlation_id=f"m211-base-{case.id}")
        )
        baseline_capture = baseline_provider.consume_model_io()
        plan_capture: ModelIOCapture | None = None
        operation_plan: OperationPlan | None = None
        plan_proposal: Any = None
        plan_provider_error: Exception | None = None
        plan_schema = serialize_schema_context(
            resolver.resolve(case.question, mode=SchemaContextMode.FULL_COMPACT)
        )
        try:
            if case.category == "top_k":
                plan_proposal = await decomposed_provider.propose_top_k_plan(
                    case.question, plan_schema
                )
            elif case.category == "ratios":
                plan_proposal = await decomposed_provider.propose_ratio_plan(
                    case.question, plan_schema
                )
            else:
                plan_proposal = await decomposed_provider.propose_window_plan(
                    case.question, plan_schema
                )
            plan_capture = decomposed_provider.consume_model_io()
            operation_plan = plan_proposal.plan
        except Exception as error:
            plan_provider_error = error
            plan_capture = decomposed_provider.consume_model_io()
        decomposed_result: TextToSqlResult | None = None
        plan_error: str | None = None
        try:
            context = resolver.resolve(case.question, mode=SchemaContextMode.FULL_COMPACT)
            visible = {
                f"{table.name.lower()}.{column.name.lower()}"
                for table in context.tables
                for column in table.columns
            }
            if operation_plan is not None:
                validate_operation_plan_visibility(operation_plan, visible)
        except ValueError as error:
            plan_error = str(error)
        if plan_error is None and plan_provider_error is None and operation_plan is not None:
            decomposed_result = await decomposed_service.run_with_operation_plan(
                TextToSqlRequest(question=case.question, correlation_id=f"m211-decomp-{case.id}"),
                operation_plan,
            )
        sql_capture = decomposed_provider.consume_model_io()
        gold_execution = _gold_execution(baseline_service, case)
        baseline_rows.append(
            _row(case, baseline_result, baseline_capture, None, None, gold_execution)
        )
        decomposed_rows.append(
            _row(
                case,
                decomposed_result,
                sql_capture,
                plan_proposal,
                plan_capture,
                gold_execution,
                plan_error,
                plan_provider_error,
            )
        )
    return _summary("BASELINE", baseline_rows), _summary("DECOMPOSED", decomposed_rows)


def _row(
    case: BaselineCase,
    result: TextToSqlResult | None,
    sql_capture: ModelIOCapture | None,
    plan_proposal: Any,
    plan_capture: ModelIOCapture | None,
    gold_execution: QueryExecution,
    plan_error: str | None = None,
    plan_provider_error: Exception | None = None,
) -> dict[str, Any]:
    comparison = _compare_result(case, result, gold_execution)
    proposal = result.proposal if result else None
    gold_signature = sql_signature(case.gold_sql)
    generated_signature = None
    diff: dict[str, Any] = {}
    if proposal:
        try:
            generated_signature = sql_signature(proposal.sql)
            diff = structural_diff(gold_signature, generated_signature)
        except Exception:
            generated_signature = None
    row: dict[str, Any] = {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "gold_sql": case.gold_sql,
        "generated_sql": proposal.sql if proposal else None,
        "result_equivalent": comparison["equivalent"],
        "equivalence_diagnostic": comparison["diagnostic"],
        "status": result.status.value if result else "PLAN_FAILED",
        "failure_stage": result.failure_stage.value
        if result and result.failure_stage
        else (
            None
            if result is not None
            else ("PLAN_INVALID" if plan_error else "PLAN_PROVIDER_FAILURE")
        ),
        "policy_rejection_code": (
            result.plan_failure.rejection.code.value
            if result and result.plan_failure and result.plan_failure.rejection
            else None
        ),
        "policy_rejection_object": (
            result.plan_failure.rejection.object
            if result and result.plan_failure and result.plan_failure.rejection
            else None
        ),
        "provider_error": result.provider_error.model_dump(mode="json")
        if result and result.provider_error
        else None,
        "plan_provider_error": (
            plan_provider_error.detail.model_dump(mode="json")
            if isinstance(plan_provider_error, LLMProviderError) and plan_provider_error.detail
            else (type(plan_provider_error).__name__ if plan_provider_error else None)
        ),
        "parse_success": bool(
            result
            and result.candidate is not None
            and result.failure_stage is not FailureStage.SQL_PARSE_ERROR
        ),
        "plan_accepted": bool(result and isinstance(result.plan, QueryPlan)),
        "execution_success": bool(result and isinstance(result.execution, QueryExecution)),
        "execution_row_count": result.execution.row_count if result and result.execution else None,
        "plan_calls_attempted": 1 if plan_proposal or plan_provider_error else 0,
        "plan_calls_succeeded": 1 if plan_proposal else 0,
        "plan_calls_failed": 1 if plan_provider_error else 0,
        "sql_calls_attempted": result.provider_calls_attempted if result else 0,
        "sql_calls_succeeded": int(result is not None and result.proposal is not None),
        "sql_calls_failed": int(
            result is not None
            and result.provider_calls_attempted > 0
            and result.proposal is None
        ),
        "provider_calls_attempted": (1 if plan_proposal or plan_provider_error else 0)
        + (result.provider_calls_attempted if result else 0),
        "provider_calls_succeeded": (1 if plan_proposal else 0)
        + (result.provider_calls_succeeded if result else 0),
        "provider_calls_failed": (1 if plan_provider_error else 0)
        + (result.provider_calls_failed if result else 0),
        "plan_input_tokens": plan_proposal.prompt_tokens if plan_proposal else None,
        "plan_output_tokens": plan_proposal.completion_tokens if plan_proposal else None,
        "plan_latency_ms": plan_proposal.latency_ms if plan_proposal else None,
        "input_tokens": proposal.prompt_tokens if proposal else None,
        "output_tokens": proposal.completion_tokens if proposal else None,
        "latency_ms": proposal.latency_ms if proposal else None,
        "gold_signature": gold_signature,
        "generated_signature": generated_signature,
        "structural_diff": diff,
        "operation_plan": plan_proposal.plan.model_dump(mode="json") if plan_proposal else None,
        "operation_plan_type": type(plan_proposal.plan).__name__ if plan_proposal else None,
        "plan_provider_metadata": _plan_metadata(plan_proposal),
        "plan_model_io": _capture(plan_capture),
        "sql_model_io": _capture(sql_capture),
        "plan_error": plan_error,
    }
    analysis = analyze_row({**row, "id": case.id})
    row["primary_root_cause"] = analysis["primary_root_cause"]
    row["secondary_tags"] = analysis["secondary_tags"]
    return row


def _compare_result(
    case: BaselineCase, result: TextToSqlResult | None, gold_execution: QueryExecution
) -> dict[str, Any]:
    # Gold execution is performed by the same M1 service used for the generated
    # query.  The gold plan is intentionally never passed to a provider.
    if result is None or not isinstance(result.execution, QueryExecution):
        return {"equivalent": None, "diagnostic": None}
    comparison = assess_query_results(
        result.execution,
        gold_execution,
        order_sensitive=case.order_sensitive,
        actual_sql=result.proposal.sql if result.proposal else None,
        expected_sql=case.gold_sql,
    )
    return {
        "equivalent": comparison.equivalent,
        "diagnostic": comparison.diagnostic.value if comparison.diagnostic else None,
    }


def _gold_execution(service: TextToSqlService, case: BaselineCase) -> QueryExecution:
    gold = service.safety_service.plan(
        SqlCandidate(sql=case.gold_sql, correlation_id=f"m211-gold-{case.id}")
    )
    if not isinstance(gold, QueryPlan):
        raise RuntimeError(f"Gold query failed M1 plan during evaluation: {case.id}")
    execution = service.safety_service.execute(gold)
    if not isinstance(execution, QueryExecution):
        raise RuntimeError(f"Gold query failed execution during evaluation: {case.id}")
    return execution


def _summary(arm: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    correct = sum(row["result_equivalent"] is True for row in rows)
    return {
        "arm": arm,
        "total_questions": total,
        "result_equivalence_count": correct,
        "result_equivalence_rate": _rate(correct, total),
        "parse_success_count": sum(row["parse_success"] for row in rows),
        "plan_acceptance_count": sum(row["plan_accepted"] for row in rows),
        "execution_success_count": sum(row["execution_success"] for row in rows),
        "provider_failure_count": sum(row["provider_calls_failed"] for row in rows),
        "policy_rejection_count": sum(row["failure_stage"] == "POLICY_REJECTION" for row in rows),
        "query_cost_rejection_count": sum(
            row["failure_stage"] == "QUERY_COST_REJECTION" for row in rows
        ),
        "generation_failure_count": sum(
            row["failure_stage"]
            in {
                "SQL_GENERATION_ERROR",
                "QUERY_INTENT_GENERATION_ERROR",
                "PLAN_INVALID",
                "PLAN_PROVIDER_FAILURE",
            }
            for row in rows
        ),
        "provider_calls_attempted": sum(row["provider_calls_attempted"] for row in rows),
        "provider_calls_succeeded": sum(row["provider_calls_succeeded"] for row in rows),
        "provider_calls_failed": sum(row["provider_calls_failed"] for row in rows),
        "total_input_tokens": _sum(row["input_tokens"] for row in rows),
        "total_output_tokens": _sum(row["output_tokens"] for row in rows),
        "plan_calls_attempted": sum(row.get("plan_calls_attempted", 0) for row in rows),
        "plan_calls_succeeded": sum(row.get("plan_calls_succeeded", 0) for row in rows),
        "plan_calls_failed": sum(row.get("plan_calls_failed", 0) for row in rows),
        "sql_calls_attempted": sum(row.get("sql_calls_attempted", 0) for row in rows),
        "sql_calls_succeeded": sum(row.get("sql_calls_succeeded", 0) for row in rows),
        "sql_calls_failed": sum(row.get("sql_calls_failed", 0) for row in rows),
        "plan_input_tokens": _sum(row.get("plan_input_tokens") for row in rows),
        "plan_output_tokens": _sum(row.get("plan_output_tokens") for row in rows),
        "plan_average_latency_ms": _average(row.get("plan_latency_ms") for row in rows),
        "end_to_end_latency_ms": _average(
            (row.get("plan_latency_ms") or 0) + (row.get("latency_ms") or 0)
            for row in rows
            if row.get("plan_latency_ms") is not None and row.get("latency_ms") is not None
        ),
        "average_input_tokens": _average(row["input_tokens"] for row in rows),
        "average_output_tokens": _average(row["output_tokens"] for row in rows),
        "average_latency_ms": _average(row["latency_ms"] for row in rows),
        "p50_latency_ms": _percentile((row["latency_ms"] for row in rows), 0.50),
        "p95_latency_ms": _percentile((row["latency_ms"] for row in rows), 0.95),
        "failure_taxonomy": dict(
            Counter(row["primary_root_cause"] for row in rows if row["primary_root_cause"])
        ),
        "category_breakdown": {
            category: {
                "total": len(items),
                "correct": sum(item["result_equivalent"] is True for item in items),
                "rate": _rate(sum(item["result_equivalent"] is True for item in items), len(items)),
            }
            for category, items in sorted(categories.items())
        },
        "cases": rows,
    }


def _comparison(baseline: dict[str, Any], decomposed: dict[str, Any]) -> dict[str, Any]:
    pairwise: Counter[str] = Counter()
    for left, right in zip(baseline["cases"], decomposed["cases"], strict=True):
        left_correct = left["result_equivalent"] is True
        right_correct = right["result_equivalent"] is True
        pairwise[
            "BOTH_CORRECT"
            if left_correct and right_correct
            else "BASELINE_ONLY_CORRECT"
            if left_correct
            else "DECOMPOSED_ONLY_CORRECT"
            if right_correct
            else "BOTH_INCORRECT"
        ] += 1
    return {
        "baseline": _comparison_metrics(baseline),
        "decomposed": _comparison_metrics(decomposed),
        "delta_decomposed_minus_baseline": {
            "correct_count": decomposed["result_equivalence_count"]
            - baseline["result_equivalence_count"],
            "rate": decomposed["result_equivalence_rate"] - baseline["result_equivalence_rate"],
            "provider_calls": decomposed["provider_calls_attempted"]
            - baseline["provider_calls_attempted"],
            "input_tokens": decomposed["total_input_tokens"] - baseline["total_input_tokens"],
            "output_tokens": decomposed["total_output_tokens"] - baseline["total_output_tokens"],
            "latency_ms": _difference(
                decomposed["average_latency_ms"], baseline["average_latency_ms"]
            ),
        },
        "pairwise_outcomes": dict(sorted(pairwise.items())),
    }


def _comparison_metrics(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "arm",
        "total_questions",
        "result_equivalence_count",
        "result_equivalence_rate",
        "parse_success_count",
        "plan_acceptance_count",
        "execution_success_count",
        "provider_failure_count",
        "policy_rejection_count",
        "query_cost_rejection_count",
        "generation_failure_count",
        "provider_calls_attempted",
        "provider_calls_succeeded",
        "provider_calls_failed",
        "plan_calls_attempted",
        "plan_calls_succeeded",
        "plan_calls_failed",
        "sql_calls_attempted",
        "sql_calls_succeeded",
        "sql_calls_failed",
        "total_input_tokens",
        "total_output_tokens",
        "plan_input_tokens",
        "plan_output_tokens",
        "plan_average_latency_ms",
        "end_to_end_latency_ms",
        "average_input_tokens",
        "average_output_tokens",
        "average_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
    )
    return {key: report[key] for key in keys}


def _category_analysis(baseline: dict[str, Any], decomposed: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for category in OPERATIONS:
        left = [row for row in baseline["cases"] if row["category"] == category]
        right = [row for row in decomposed["cases"] if row["category"] == category]
        output[category] = {
            "baseline_correct": sum(row["result_equivalent"] is True for row in left),
            "decomposed_correct": sum(row["result_equivalent"] is True for row in right),
            "total": len(left),
        }
    return output


def _plan_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    per_case = {row["id"]: _plan_case_quality(row) for row in rows}
    fields: dict[str, list[bool]] = defaultdict(list)
    for quality in per_case.values():
        for field, value in quality.get("components", {}).items():
            fields[field].append(value)
    return {
        "per_case": per_case,
        "component_accuracy": {
            field: _rate(sum(values), len(values)) for field, values in sorted(fields.items())
        },
        "plan_attribution_counts": dict(
            Counter(quality["attribution"] for quality in per_case.values())
        ),
    }


def _plan_case_quality(row: dict[str, Any]) -> dict[str, Any]:
    plan = row.get("operation_plan")
    if not plan:
        return {"valid": False, "components": {}, "attribution": "PLAN_INVALID"}
    gold = row["gold_signature"]
    if row["category"] == "top_k":
        measure = plan.get("measure", {})
        components = {
            "entity": _refs_match(plan.get("entity_outputs", ()), gold["group_by"]),
            "measure": _refs_subset(measure.get("components", ()), gold["columns"]),
            "aggregation": _aggregate_name_match(measure, row["gold_sql"]),
            "grouping": _refs_match(plan.get("group_by", ()), gold["group_by"]),
            "direction": _direction_match(plan.get("order_direction"), gold["order_by"]),
            "limit": plan.get("limit") == gold["limit"],
        }
    elif row["category"] == "ratios":
        components = _ratio_quality(plan, row["gold_sql"])
    else:
        components = {
            "function": _window_function_match(plan.get("window_function"), row["gold_sql"]),
            "partition": _contains_refs(plan.get("partition_by", ()), gold["window_partitions"]),
            "order": _contains_refs(plan.get("order_by", ()), gold["window_orders"]),
            "direction": _direction_match(plan.get("order_direction"), gold["window_orders"]),
            "outputs": _refs_match(plan.get("requested_outputs", ()), gold["columns"]),
        }
    correct = all(components.values())
    if correct and row["failure_stage"] == "POLICY_REJECTION":
        attribution = "POLICY_BLOCKED_AFTER_CORRECT_PLAN"
    elif correct and row["result_equivalent"] is True:
        attribution = "PLAN_CORRECT_SQL_CORRECT"
    elif correct:
        attribution = "PLAN_CORRECT_SQL_WRONG"
    elif row["result_equivalent"] is True:
        attribution = "PLAN_WRONG_SQL_CORRECT"
    else:
        attribution = "PLAN_WRONG_SQL_WRONG"
    return {
        "valid": True,
        "components": components,
        "plan_correct": correct,
        "attribution": attribution,
    }


def _ratio_quality(plan: dict[str, Any], sql: str) -> dict[str, bool]:
    tree = parse_one(sql, dialect="postgres")
    division = next(tree.find_all(exp.Div), None)
    if division is None:
        return {
            "numerator": False,
            "denominator": False,
            "aggregation": False,
            "grain": bool(plan.get("grain")),
            "scale": plan.get("scale") is None or isinstance(plan.get("scale"), (int, float)),
        }
    sides = (
        _expression_facts(division.left),
        _expression_facts(division.right),
    )
    numerator = plan.get("numerator", {})
    denominator = plan.get("denominator", {})
    return {
        "numerator": _component_matches(numerator, sides[0]),
        "denominator": _component_matches(denominator, sides[1]),
        "aggregation": bool(numerator.get("aggregation")) and bool(denominator.get("aggregation")),
        "grain": bool(plan.get("grain")),
        "scale": plan.get("scale") is None or isinstance(plan.get("scale"), (int, float)),
    }


def _expression_facts(expression: exp.Expression) -> dict[str, set[str]]:
    return {
        "columns": {
            _normalize_ref(f"{column.table}.{column.name}")
            for column in expression.find_all(exp.Column)
        },
        "aggregations": {type(item).__name__.upper() for item in expression.find_all(exp.AggFunc)},
    }


def _component_matches(component: dict[str, Any], facts: dict[str, set[str]]) -> bool:
    columns = {_normalize_ref(str(value)) for value in component.get("source_columns", ())}
    aggregation = str(component.get("aggregation", "")).upper()
    return (
        bool(component.get("semantic_label"))
        and bool(aggregation)
        and (not columns or columns.issubset(facts["columns"]))
        and (aggregation in facts["aggregations"] or not facts["aggregations"])
    )


def _plan_projection_matches(references: Any, sql: str) -> bool:
    expected = {_normalize_ref(str(value)) for value in references if "." in str(value)}
    if not expected:
        return False
    tree = parse_one(sql, dialect="postgres")
    projected = {
        _normalize_ref(f"{item.table}.{item.name}")
        for item in tree.expressions[0].find_all(exp.Column)
    }
    return bool(expected & projected)


def _refs_match(actual: Any, expected: Any) -> bool:
    actual_names = {str(value).split(".")[-1].lower() for value in actual}
    expected_names = {str(value).split(".")[-1].lower() for value in expected}
    return bool(actual_names) and actual_names == expected_names


def _refs_subset(actual: Any, expected: Any) -> bool:
    actual_names = {str(value).split(".")[-1].lower() for value in actual}
    expected_names = {str(value).split(".")[-1].lower() for value in expected}
    return bool(actual_names) and actual_names.issubset(expected_names)


def _aggregate_components_match(measure: dict[str, Any], sql: str) -> bool:
    expected = {
        _normalize_ref(str(value)) for value in measure.get("components", ()) if "." in str(value)
    }
    if not expected:
        return False
    tree = parse_one(sql, dialect="postgres")
    actual = {
        _normalize_ref(f"{column.table}.{column.name}") for column in tree.find_all(exp.Column)
    }
    return expected.issubset(actual)


def _aggregate_name_match(measure: dict[str, Any], sql: str) -> bool:
    wanted = str(measure.get("aggregation", "")).upper()
    return wanted in {
        type(item).__name__.upper()
        for item in parse_one(sql, dialect="postgres").find_all(exp.AggFunc)
    }


def _same_refs(actual: Any, expected: Any) -> bool:
    return bool(actual) and {_normalize_ref(str(value)) for value in actual} == {
        _normalize_ref(str(value)) for value in expected
    }


def _contains_refs(actual: Any, expected: Any) -> bool:
    actual_values = {_normalize_ref(str(value)) for value in actual}
    expected_values = {_normalize_ref(str(value)) for value in expected}
    return bool(actual_values) and any(
        actual_value.endswith(expected_value.split(".")[-1])
        or expected_value.endswith(actual_value.split(".")[-1])
        for actual_value in actual_values
        for expected_value in expected_values
    )


def _normalize_ref(value: str) -> str:
    return value.lower().replace('"', "").split(" AS ")[0].strip()


def _direction_match(direction: Any, order_values: Any) -> bool:
    if not order_values or direction not in {"ASC", "DESC"}:
        return False
    return all(
        ("DESC" if " DESC" in str(value).upper() else "ASC") == direction for value in order_values
    )


def _window_function_match(function: Any, sql: str) -> bool:
    wanted = _canonical_window_function(function)
    actual = {
        _canonical_window_function(type(item.this).__name__)
        for item in parse_one(sql, dialect="postgres").find_all(exp.Window)
        if item.this is not None
    }
    return wanted in actual


def _canonical_window_function(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _failure_analysis(baseline: dict[str, Any], decomposed: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline": dict(
            Counter(
                row["primary_root_cause"] for row in baseline["cases"] if row["primary_root_cause"]
            )
        ),
        "decomposed": dict(
            Counter(
                row["primary_root_cause"]
                for row in decomposed["cases"]
                if row["primary_root_cause"]
            )
        ),
        "decomposed_plan_attribution": dict(
            Counter(_plan_case_quality(row)["attribution"] for row in decomposed["cases"])
        ),
    }


def _policy_shadow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    records = [
        {
            "id": row["id"],
            "policy_blocked_nullif": row.get("policy_rejection_object") == "NULLIF",
            "policy_rejection_object": row.get("policy_rejection_object"),
            "plan": row.get("operation_plan"),
            "structural_diff": row.get("structural_diff", {}),
        }
        for row in rows
        if row["failure_stage"] == "POLICY_REJECTION"
    ]
    return {
        "records": records,
        "nullif_rejection_count": sum(item["policy_blocked_nullif"] for item in records),
        "execution_performed_for_rejected_sql": False,
    }


def _model_io_rows(baseline: dict[str, Any], decomposed: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in (baseline, decomposed):
        for case in report["cases"]:
            if case.get("plan_model_io"):
                rows.append(case["plan_model_io"])
            if case.get("sql_model_io"):
                rows.append(case["sql_model_io"])
    return rows


def _capture(capture: ModelIOCapture | None) -> dict[str, Any] | None:
    if capture is None:
        return None
    payload = capture.model_dump(mode="json")
    prompt = capture.messages[0]["content"]
    base_prompt = prompt.split("\n\nNARROW OPERATION PLAN", 1)[0]
    payload.update(
        {
            "question_sha256": sha256_text(capture.question),
            "schema_context_sha256": sha256_text(capture.serialized_schema_context),
            "prompt_template_sha256": sha256_text(
                base_prompt.replace(capture.serialized_schema_context, "{SCHEMA_CONTEXT}")
            ),
            "input_sha256": sha256_json(capture.messages),
        }
    )
    return payload


def _assert_paired_inputs(baseline: dict[str, Any], decomposed: dict[str, Any]) -> None:
    for left, right in zip(baseline["cases"], decomposed["cases"], strict=True):
        left_io = left.get("sql_model_io") or {}
        right_io = right.get("sql_model_io") or {}
        # A plan validation/provider failure can legitimately prevent the
        # decomposed SQL call.  There is no paired SQL input to compare in
        # that case; the case-level failure is persisted in the report.
        if not left_io or not right_io:
            continue
        for key in ("question_sha256", "schema_context_sha256", "prompt_template_sha256"):
            if left_io.get(key) != right_io.get(key):
                raise RuntimeError(f"M2.11 paired input mismatch for {left['id']}: {key}")


def _plan_metadata(proposal: Any) -> dict[str, Any] | None:
    if proposal is None:
        return None
    return {
        "provider": proposal.provider,
        "model": proposal.model,
        "prompt_tokens": proposal.prompt_tokens,
        "completion_tokens": proposal.completion_tokens,
        "reasoning_tokens": proposal.reasoning_tokens,
        "cached_prompt_tokens": proposal.cached_prompt_tokens,
        "latency_ms": proposal.latency_ms,
    }


def _load_dev_slice(dataset: Path, categories: tuple[str, ...]) -> list[BaselineCase]:
    ids = {f"m2-{index:03d}" for index in range(39, 49)}
    return [
        case for case in load_baseline(dataset) if case.category in categories and case.id in ids
    ]


def _verify_dev_source(dataset: Path, categories: tuple[str, ...]) -> None:
    del categories
    if dataset != M2_DATASET:
        raise SystemExit("M2.11 development must use the existing M2 development dataset.")
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != M2_DATASET_SHA:
        raise SystemExit("M2 development dataset SHA-256 mismatch; evaluation stopped.")


def _verify_holdout(dataset: Path) -> None:
    manifest = json.loads(HOLDOUT_MANIFEST.read_text())
    actual = hashlib.sha256(dataset.read_bytes()).hexdigest()
    if actual != manifest["dataset_sha256"]:
        raise SystemExit("M2.11 holdout SHA-256 mismatch; evaluation stopped.")


def _metadata(
    args: argparse.Namespace,
    categories: tuple[str, ...],
    source_sha: str,
    cases: list[BaselineCase],
    baseline: dict[str, Any],
    decomposed: dict[str, Any],
    run_id: str,
    settings: Any,
) -> dict[str, Any]:
    return {
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
            "schema_context": "FULL_COMPACT",
            "generation_mode": "ONE_SHOT",
        },
        "categories": list(categories),
        "dataset_path": str(args.dataset),
        "dataset_sha256": source_sha,
        "question_count": len(cases),
        "code_commit_sha": _git_sha(),
        "evaluator_version": "m2-result-equivalence-v2-ordinal-columns",
        "architecture": {
            "baseline": "FULL_COMPACT + ONE_SHOT",
            "decomposed": "narrow operation plan + FULL_COMPACT + ONE_SHOT",
            "query_intent": False,
            "result_shape": False,
            "role_aware_schema": False,
            "retrieval": False,
            "candidate_sampling": False,
            "repair": False,
        },
        "provider_calls_attempted": (
            baseline["provider_calls_attempted"] + decomposed["provider_calls_attempted"]
        ),
        "provider_calls_succeeded": (
            baseline["provider_calls_succeeded"] + decomposed["provider_calls_succeeded"]
        ),
        "provider_calls_failed": (
            baseline["provider_calls_failed"] + decomposed["provider_calls_failed"]
        ),
        "settings_timeout_seconds": settings.llm_timeout_seconds,
        "no_adaptive_tuning": True,
    }


def _rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def _sum(values: Any) -> int:
    return sum(value for value in values if isinstance(value, int))


def _average(values: Any) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else None


def _percentile(values: Any, quantile: float) -> float | None:
    numbers = sorted(float(value) for value in values if isinstance(value, (int, float)))
    if not numbers:
        return None
    position = (len(numbers) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(numbers) - 1)
    return numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)


def _difference(left: Any, right: Any) -> float | None:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return float(left) - float(right)


def _format_latency(value: Any) -> str:
    return f"{value:.1f} ms" if isinstance(value, (int, float)) else "n/a"


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _render_report(
    metadata: dict[str, Any], baseline: dict[str, Any], decomposed: dict[str, Any]
) -> str:
    comparison = _comparison(baseline, decomposed)
    baseline_row = (
        f"| Baseline | {baseline['result_equivalence_count']}/{baseline['total_questions']} | "
        f"{baseline['provider_calls_attempted']} | {baseline['total_input_tokens']} | "
        f"{baseline['total_output_tokens']} | {_format_latency(baseline['average_latency_ms'])} |"
    )
    decomposed_row = (
        f"| Decomposed | {decomposed['result_equivalence_count']}/"
        f"{decomposed['total_questions']} | {decomposed['provider_calls_attempted']} | "
        f"{decomposed['total_input_tokens']} | {decomposed['total_output_tokens']} | "
        f"{_format_latency(decomposed['average_latency_ms'])} |"
    )
    delta = comparison["delta_decomposed_minus_baseline"]
    return "\n".join(
        [
            "# M2.11 — Narrow SQL-Shape Decomposition Ablation",
            "",
            (
                "Development/diagnostic run over the consumed M2 hard slice. "
                "No holdout result is claimed here."
            ),
            "",
            f"- Run: {metadata['run_id']}",
            f"- Dataset SHA: {metadata['dataset_sha256']}",
            f"- Questions: {metadata['question_count']} ({', '.join(metadata['categories'])})",
            "- Model: gpt-5.6-luna; reasoning none; temperature omitted/provider default",
            "",
            "| Arm | Result equivalence | Calls | Input tokens | Output tokens | Avg latency |",
            "|---|---:|---:|---:|---:|---:|",
            baseline_row,
            decomposed_row,
            "",
            (
                f"Delta: {delta['correct_count']} correct questions; "
                f"provider-call delta {delta['provider_calls']}."
            ),
            "",
            (
                "The decomposed arm uses one category-specific plan call plus one SQL call. "
                "Plans are untrusted and never authorize SQL; every generated query "
                "remains behind M1."
            ),
            "",
        ]
    )


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return result.stdout.strip() or None


if __name__ == "__main__":
    main()
