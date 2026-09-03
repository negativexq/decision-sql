"""Run the M2.9 current-schema versus role-aware-schema ablation."""

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
from app.catalog.models import SchemaCatalog, SchemaContext
from app.catalog.role_metadata import (
    ROLE_AWARE_METADATA,
    serialize_role_aware_schema_context,
    validate_role_metadata,
)
from app.config import get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.provider import ModelIOCapture, OpenAICompatibleProvider
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.m27_forensics import (
    context_visibility,
    sha256_json,
    sha256_text,
    sql_signature,
    structural_diff,
)
from evaluation.m28_projection import projection_diagnostics
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase
from evaluation.runner import load_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.9 role-aware schema ablation")
    parser.add_argument(
        "--dataset", type=Path, default=Path("evaluation/datasets/m2_baseline.json")
    )
    parser.add_argument("--results-root", type=Path, default=Path("evaluation/results/m29/dev"))
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--report", type=Path, default=Path("docs/m29-role-aware-schema-ablation.md")
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("M2.9 requires DECISION_SQL_LLM_API_KEY; no provider calls were made.")

    cases = load_baseline(args.dataset)
    dataset_sha = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    if args.stage == "holdout":
        expected_sha = (
            "27eed0939171273fdf79132c3f4a0a4eb0a1f4413b3f53201856f60b1d760bb3"
        )
        if dataset_sha != expected_sha:
            raise SystemExit("M2.9 holdout SHA-256 mismatch; evaluation stopped.")
    catalog = build_default_catalog(Base.metadata)
    resolver = SchemaContextResolver(
        catalog,
        top_k=settings.schema_top_k,
        max_tables=settings.max_context_tables,
        max_columns_per_table=settings.max_columns_per_table,
        relationship_depth=settings.relationship_depth,
    )
    audit_context = resolver.resolve("schema audit", mode=SchemaContextMode.FULL_COMPACT)
    baseline_schema = serialize_schema_context(audit_context)
    role_schema = serialize_role_aware_schema_context(audit_context)
    schema_audit, ambiguity = _schema_audit(catalog, audit_context, baseline_schema, role_schema)
    if not schema_audit["valid"]:
        raise SystemExit("M2.9 schema audit failed; no provider calls were made.")

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.results_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "schema_audit.json", schema_audit)
    _write_json(output_dir / "schema_ambiguity_inventory.json", ambiguity)

    def build_service(role_aware: bool) -> tuple[TextToSqlService, OpenAICompatibleProvider]:
        arm_settings = settings.model_copy(
            update={
                "llm_model": "gpt-5.6-luna",
                "llm_temperature": None,
                "llm_reasoning_effort": "none",
                "eval_capture_model_io": True,
            }
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
                schema_serializer=(
                    serialize_role_aware_schema_context if role_aware else serialize_schema_context
                ),
            ),
            provider,
        )

    baseline_service, baseline_provider = build_service(False)
    role_service, role_provider = build_service(True)
    baseline = asyncio.run(evaluate("BASELINE_SCHEMA", cases, baseline_service, baseline_provider))
    role_aware = asyncio.run(evaluate("ROLE_AWARE_SCHEMA", cases, role_service, role_provider))
    _assert_paired_inputs(baseline, role_aware)

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
            "role_aware": "ROLE_AWARE_FULL_COMPACT + ONE_SHOT",
            "result_shape_enabled": False,
            "query_intent_enabled": False,
            "retrieval_enabled": False,
            "repair_enabled": False,
            "candidate_generation_enabled": False,
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
        "schema_sizes": {
            "baseline_characters": len(baseline_schema),
            "role_aware_characters": len(role_schema),
            "baseline_estimated_tokens_char_div_4": (len(baseline_schema) + 3) // 4,
            "role_aware_estimated_tokens_char_div_4": (len(role_schema) + 3) // 4,
            "character_delta": len(role_schema) - len(baseline_schema),
            "character_ratio": len(role_schema) / len(baseline_schema),
        },
        "schema_audit": schema_audit,
        "provider_calls_attempted": baseline["provider_calls_attempted"]
        + role_aware["provider_calls_attempted"],
        "provider_calls_succeeded": baseline["provider_calls_succeeded"]
        + role_aware["provider_calls_succeeded"],
        "provider_calls_failed": baseline["provider_calls_failed"]
        + role_aware["provider_calls_failed"],
        "no_adaptive_tuning": True,
    }
    _write_json(output_dir / "metadata.json", metadata)
    _write_json(output_dir / "baseline.json", baseline)
    _write_json(output_dir / "role_aware.json", role_aware)
    comparison = _comparison(baseline, role_aware)
    _write_json(output_dir / "comparison.json", comparison)
    _write_json(output_dir / "role_analysis.json", _role_analysis(baseline, role_aware))
    _write_json(output_dir / "failure_analysis.json", _failure_analysis(baseline, role_aware))
    _write_model_io(output_dir / "model_io.jsonl", baseline, role_aware)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        _render_report(metadata, baseline, role_aware, comparison), encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(output_dir), "report": str(args.report)}))


async def evaluate(
    arm: str,
    cases: list[BaselineCase],
    service: TextToSqlService,
    provider: OpenAICompatibleProvider,
) -> dict[str, Any]:
    rows = [await _evaluate_case(case, service, provider) for case in cases]
    return _summarize(arm, rows)


async def _evaluate_case(
    case: BaselineCase, service: TextToSqlService, provider: OpenAICompatibleProvider
) -> dict[str, Any]:
    result = await service.run(
        TextToSqlRequest(question=case.question, correlation_id=case.id, execute=True)
    )
    capture = provider.consume_model_io()
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
    primary, subtype = _root_cause(result, case, diff, visibility, projection, equivalent)
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
        "object_selection_subtype": subtype,
        "gold_signature": gold_signature,
        "generated_signature": generated_signature,
        "structural_diff": diff,
        "context_visibility": visibility,
        "projection_diagnostics": projection,
        "model_io": _capture_record(capture, case, result.context),
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
        "generation_latency_ms": proposal.latency_ms if proposal else None,
    }


def _root_cause(
    result: Any,
    case: BaselineCase,
    diff: dict[str, Any],
    visibility: dict[str, Any] | None,
    projection: dict[str, Any],
    equivalent: bool | None,
) -> tuple[str | None, str | None]:
    if result.failure_stage == FailureStage.SQL_GENERATION_ERROR:
        return "PROVIDER_FAILURE", None
    if result.failure_stage == FailureStage.POLICY_REJECTION:
        return "POLICY_REJECTION", None
    if equivalent is True:
        return None, None
    if visibility and not all(
        visibility.get(key, True)
        for key in ("gold_tables_visible", "gold_columns_visible", "gold_relationships_visible")
    ):
        return "STRUCTURAL_CONTEXT_INSUFFICIENT", None
    structural_non_projection = any(
        key.startswith(prefix)
        for key in diff
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
    )
    if projection.get("status") == "PROJECTION_EXTRA" and not structural_non_projection:
        return "OBJECT_SELECTION_ERROR", "PROJECTION_OVERSELECTION"
    if any(key.startswith("TABLE_") or key.startswith("COLUMN_") for key in diff):
        return "OBJECT_SELECTION_ERROR", _object_subtype(case, diff)
    if any(key.startswith("FILTER_") for key in diff):
        return "FILTER_CONSTRUCTION_ERROR", None
    if any(key.startswith("AGGREGATION_") or key.startswith("GROUP_BY_") for key in diff):
        return "AGGREGATION_GRAIN_ERROR", None
    if any(key.startswith("JOIN_") for key in diff):
        return "JOIN_COMPOSITION_ERROR", None
    if any(key.startswith("WINDOW_") for key in diff):
        return "WINDOW_COMPOSITION_ERROR", None
    if any(key.startswith("ORDER_") or key.startswith("LIMIT_") for key in diff):
        return "ORDER_TOPK_ERROR", None
    return "SQL_COMPOSITION_ERROR", None


def _object_subtype(case: BaselineCase, diff: dict[str, Any]) -> str:
    if case.category == "date_filtering":
        return "WRONG_DATE_COLUMN"
    if case.category == "simple_filters":
        return "WRONG_FILTER_COLUMN"
    if case.category in {"aggregation", "simple_aggregation", "ratios", "top_k"}:
        return "WRONG_MEASURE_COLUMN"
    return "WRONG_DIMENSION_COLUMN"


def _summarize(arm: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    roots = Counter(row["primary_root_cause"] for row in rows if row["primary_root_cause"])
    subtypes = Counter(
        row["object_selection_subtype"] for row in rows if row["object_selection_subtype"]
    )
    projections = Counter(row["projection_diagnostics"].get("status") for row in rows)
    return {
        "arm": arm,
        "total_questions": total,
        "result_equivalence_count": sum(row["result_equivalent"] is True for row in rows),
        "result_equivalence_rate": sum(row["result_equivalent"] is True for row in rows) / total,
        "parse_success_count": sum(row["parse_success"] for row in rows),
        "plan_acceptance_count": sum(row["plan_accepted"] for row in rows),
        "execution_success_count": sum(row["execution_success"] for row in rows),
        "policy_rejection_count": sum(row["failure_stage"] == "POLICY_REJECTION" for row in rows),
        "query_cost_rejection_count": sum(
            row["failure_stage"] == "QUERY_COST_REJECTION" for row in rows
        ),
        "provider_failure_count": sum(
            row["failure_stage"] == "SQL_GENERATION_ERROR" for row in rows
        ),
        "provider_calls_attempted": sum(row["provider_calls_attempted"] for row in rows),
        "provider_calls_succeeded": sum(row["provider_calls_succeeded"] for row in rows),
        "provider_calls_failed": sum(row["provider_calls_failed"] for row in rows),
        "total_input_tokens": _sum_optional([row["input_tokens"] for row in rows]),
        "total_output_tokens": _sum_optional([row["output_tokens"] for row in rows]),
        "average_input_tokens": _average([row["input_tokens"] for row in rows]),
        "average_output_tokens": _average([row["output_tokens"] for row in rows]),
        "average_latency_ms": _average([row["generation_latency_ms"] for row in rows]),
        "p50_latency_ms": _percentile([row["generation_latency_ms"] for row in rows], 0.5),
        "p95_latency_ms": _percentile([row["generation_latency_ms"] for row in rows], 0.95),
        "failure_taxonomy": dict(roots),
        "object_selection_subtypes": dict(subtypes),
        "projection_status_counts": dict(projections),
        "projection_metrics": _projection_metrics(rows),
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


def _projection_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    total = len(rows)
    return {
        "exact_projection_arity_rate": sum(
            row["projection_diagnostics"].get("arity_exact", False) for row in rows
        )
        / total,
        "extra_projection_rate": sum(
            row["projection_diagnostics"].get("status") == "PROJECTION_EXTRA" for row in rows
        )
        / total,
        "missing_projection_rate": sum(
            row["projection_diagnostics"].get("status") == "PROJECTION_MISSING" for row in rows
        )
        / total,
        "exact_physical_projection_match_rate": sum(
            row["projection_diagnostics"].get("physical_projection_exact", False) for row in rows
        )
        / total,
    }


def _comparison(baseline: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
    pairwise: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    same_root = 0
    different_root = 0
    for left, right in zip(baseline["cases"], role["cases"], strict=True):
        left_correct = left["result_equivalent"] is True
        right_correct = right["result_equivalent"] is True
        pairwise[
            "BOTH_CORRECT"
            if left_correct and right_correct
            else "BASELINE_ONLY_CORRECT"
            if left_correct
            else "ROLE_AWARE_ONLY_CORRECT"
            if right_correct
            else "BOTH_INCORRECT"
        ] += 1
        transitions[
            f"{left['primary_root_cause'] or 'CORRECT'} -> "
            f"{right['primary_root_cause'] or 'CORRECT'}"
        ] += 1
        if not left_correct and not right_correct:
            if left["primary_root_cause"] == right["primary_root_cause"]:
                same_root += 1
            else:
                different_root += 1
    return {
        "baseline": _arm_metrics(baseline),
        "role_aware": _arm_metrics(role),
        "delta_role_aware_minus_baseline": {
            "result_equivalence_count": role["result_equivalence_count"]
            - baseline["result_equivalence_count"],
            "result_equivalence_rate": role["result_equivalence_rate"]
            - baseline["result_equivalence_rate"],
            "average_latency_ms": _difference(
                role["average_latency_ms"], baseline["average_latency_ms"]
            ),
            "average_input_tokens": _difference(
                role["average_input_tokens"], baseline["average_input_tokens"]
            ),
            "average_output_tokens": _difference(
                role["average_output_tokens"], baseline["average_output_tokens"]
            ),
        },
        "pairwise_outcomes": dict(sorted(pairwise.items())),
        "both_incorrect_same_primary_root_cause": same_root,
        "both_incorrect_different_primary_root_cause": different_root,
        "role_aware_transitions": dict(sorted(transitions.items())),
        "category_delta": {
            category: {
                "baseline": baseline["category_breakdown"].get(category),
                "role_aware": role["category_breakdown"].get(category),
                "delta_rate": _difference(
                    role["category_breakdown"].get(category, {}).get("result_equivalence_rate"),
                    baseline["category_breakdown"].get(category, {}).get("result_equivalence_rate"),
                ),
            }
            for category in sorted(
                set(baseline["category_breakdown"]) | set(role["category_breakdown"])
            )
        },
    }


def _role_analysis(baseline: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
    return {
        "role_sensitive_transitions": _comparison(baseline, role)["role_aware_transitions"],
        "baseline_object_selection": baseline["object_selection_subtypes"],
        "role_aware_object_selection": role["object_selection_subtypes"],
        "baseline_projection": baseline["projection_metrics"],
        "role_aware_projection": role["projection_metrics"],
        "simple_filters": {
            arm: [
                {
                    "id": row["id"],
                    "result_equivalent": row["result_equivalent"],
                    "generated_sql": row["generated_sql"],
                    "projection": row["projection_diagnostics"],
                    "structural_diff": row["structural_diff"],
                    "policy_outcome": row["failure_stage"],
                }
                for row in report["cases"]
                if row["category"] == "simple_filters"
            ]
            for arm, report in (("baseline", baseline), ("role_aware", role))
        },
    }


def _failure_analysis(baseline: dict[str, Any], role: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline": baseline["failure_taxonomy"],
        "role_aware": role["failure_taxonomy"],
        "baseline_object_selection_subtypes": baseline["object_selection_subtypes"],
        "role_aware_object_selection_subtypes": role["object_selection_subtypes"],
        "both_incorrect_same_root": _comparison(baseline, role)[
            "both_incorrect_same_primary_root_cause"
        ],
        "both_incorrect_different_root": _comparison(baseline, role)[
            "both_incorrect_different_primary_root_cause"
        ],
    }


def _schema_audit(
    catalog: SchemaCatalog,
    context: SchemaContext,
    baseline_schema: str,
    role_schema: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_role_metadata(catalog)
    queryable_tables = {table.name for table in catalog.tables if table.queryable}
    context_tables = {table.name for table in context.tables}
    queryable_columns = {
        f"{table.name}.{column.name}"
        for table in catalog.tables
        if table.queryable
        for column in table.columns
        if column.queryable
    }
    context_columns = {
        f"{table.name}.{column.name}" for table in context.tables for column in table.columns
    }
    relationships = {
        f"{item.source_table}.{item.source_column}->{item.target_table}.{item.target_column}"
        for item in context.relationships
    }
    role_keys = set(ROLE_AWARE_METADATA.columns)
    forbidden_tokens = (
        "external_key",
        "pg_catalog",
        "pg_shadow",
        "net_revenue",
        "refund_rate",
        "select ",
    )
    valid = (
        context_tables == queryable_tables
        and queryable_columns == context_columns
        and role_keys.issuperset(queryable_columns)
        and not any(token in role_schema.lower() for token in forbidden_tokens)
    )
    role_columns = sum(f"- {key} " in role_schema for key in sorted(queryable_columns))
    return (
        {
            "valid": valid,
            "queryable_table_count": len(queryable_tables),
            "queryable_column_count": len(queryable_columns),
            "context_relationship_count": len(relationships),
            "baseline_tables_preserved": context_tables == queryable_tables,
            "baseline_columns_preserved": queryable_columns == context_columns,
            "role_table_coverage": len(ROLE_AWARE_METADATA.tables) / len(queryable_tables),
            "role_column_coverage": role_columns / len(queryable_columns),
            "role_schema_contains_no_values_or_formulas": not any(
                token in role_schema.lower() for token in forbidden_tokens
            ),
            "baseline_schema_sha256": sha256_text(baseline_schema),
            "role_schema_sha256": sha256_text(role_schema),
        },
        _ambiguity_inventory(catalog),
    )


def _ambiguity_inventory(catalog: SchemaCatalog) -> dict[str, Any]:
    queryable = [
        (table.name, column)
        for table in catalog.tables
        if table.queryable
        for column in table.columns
        if column.queryable
    ]
    by_name: dict[str, list[str]] = defaultdict(list)
    by_type: dict[str, list[str]] = defaultdict(list)
    for table_name, column in queryable:
        key = f"{table_name}.{column.name}"
        by_name[column.name.lower()].append(key)
        by_type[column.type.lower()].append(key)
    return {
        "same_name_groups": {
            key: sorted(value) for key, value in by_name.items() if len(value) > 1
        },
        "same_type_groups": {
            key: sorted(value) for key, value in by_type.items() if len(value) > 1
        },
        "role_groups": {
            role.value: sorted(
                key for key, item in ROLE_AWARE_METADATA.columns.items() if item.role is role
            )
            for role in set(item.role for item in ROLE_AWARE_METADATA.columns.values())
        },
    }


def _assert_paired_inputs(baseline: dict[str, Any], role: dict[str, Any]) -> None:
    for left, right in zip(baseline["cases"], role["cases"], strict=True):
        left_io = left.get("model_io")
        right_io = right.get("model_io")
        if not left_io or not right_io:
            raise RuntimeError(f"Missing model I/O capture for {left['id']}")
        if left_io["question_sha256"] != right_io["question_sha256"]:
            raise RuntimeError(f"Question hash mismatch for {left['id']}")
        if left_io["prompt_template_sha256"] != right_io["prompt_template_sha256"]:
            raise RuntimeError(f"Prompt template mismatch for {left['id']}")


def _capture_record(
    capture: ModelIOCapture | None, case: BaselineCase, context: Any
) -> dict[str, Any] | None:
    if capture is None:
        return None
    payload = capture.model_dump(mode="json")
    system_prompt = capture.messages[0]["content"]
    template = system_prompt.replace(capture.serialized_schema_context, "{SCHEMA_CONTEXT}")
    payload.update(
        {
            "question_id": case.id,
            "category": case.category,
            "question_sha256": sha256_text(case.question),
            "prompt_template_sha256": sha256_text(template),
            "schema_context_sha256": sha256_text(capture.serialized_schema_context),
            "input_sha256": sha256_json(capture.messages),
            "context_metadata": context.context_metadata.model_dump(mode="json")
            if context
            else None,
        }
    )
    return payload


def _arm_metrics(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "arm",
        "total_questions",
        "result_equivalence_count",
        "result_equivalence_rate",
        "parse_success_count",
        "plan_acceptance_count",
        "execution_success_count",
        "policy_rejection_count",
        "query_cost_rejection_count",
        "provider_failure_count",
        "provider_calls_attempted",
        "provider_calls_succeeded",
        "provider_calls_failed",
        "total_input_tokens",
        "total_output_tokens",
        "average_input_tokens",
        "average_output_tokens",
        "average_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "failure_taxonomy",
        "object_selection_subtypes",
        "projection_metrics",
    )
    return {key: report[key] for key in keys}


def _write_model_io(path: Path, baseline: dict[str, Any], role: dict[str, Any]) -> None:
    records = []
    for arm, report in (("BASELINE_SCHEMA", baseline), ("ROLE_AWARE_SCHEMA", role)):
        for row in report["cases"]:
            if row["model_io"] is not None:
                record = dict(row["model_io"])
                record["experiment_arm"] = arm
                records.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")


def _render_report(
    metadata: dict[str, Any],
    baseline: dict[str, Any],
    role: dict[str, Any],
    comparison: dict[str, Any],
) -> str:
    total = metadata["question_count"]
    lines = [
        "# M2.9 Role-Aware Schema Representation Ablation",
        "",
        (
            "Development-only screening on the consumed M2 dataset."
            if metadata["stage"] == "dev"
            else "Frozen M2.9 holdout evaluation."
        ),
        "",
        f"- Dataset SHA-256: `{metadata['dataset_sha256']}`",
        "- Model: `gpt-5.6-luna`; reasoning `none`; temperature omitted/provider default",
        "- Both arms use one SQL-generation call, FULL_COMPACT, and the unchanged "
        "M1 boundary.",
        "- The only intended intervention is deterministic role/grain/meaning "
        "schema serialization.",
        "",
        "| Metric | Baseline | Role-aware | Delta |",
        "|---|---:|---:|---:|",
        (
            f"| Result equivalence | {baseline['result_equivalence_count']}/{total} "
            f"({baseline['result_equivalence_rate']:.2%}) | "
            f"{role['result_equivalence_count']}/{total} "
            f"({role['result_equivalence_rate']:.2%}) | "
            f"{comparison['delta_role_aware_minus_baseline']['result_equivalence_count']} "
            f"({comparison['delta_role_aware_minus_baseline']['result_equivalence_rate']:.2%}) |"
        ),
        (
            f"| Plan acceptance | {baseline['plan_acceptance_count']}/{total} | "
            f"{role['plan_acceptance_count']}/{total} | — |"
        ),
        (
            f"| Execution success | {baseline['execution_success_count']}/{total} | "
            f"{role['execution_success_count']}/{total} | — |"
        ),
        f"| Input tokens | {baseline['total_input_tokens']} | {role['total_input_tokens']} | — |",
        (
            f"| Output tokens | {baseline['total_output_tokens']} | "
            f"{role['total_output_tokens']} | — |"
        ),
        (
            f"| Average latency | {baseline['average_latency_ms']:.1f} ms | "
            f"{role['average_latency_ms']:.1f} ms | "
            f"{comparison['delta_role_aware_minus_baseline']['average_latency_ms']:.1f} ms |"
        ),
        "",
        f"Pairwise outcomes: `{json.dumps(comparison['pairwise_outcomes'], sort_keys=True)}`",
        "",
        (
            f"Schema characters: baseline={metadata['schema_sizes']['baseline_characters']}, "
            f"role-aware={metadata['schema_sizes']['role_aware_characters']}; "
            f"ratio={metadata['schema_sizes']['character_ratio']:.2f}."
        ),
        "",
        "No versioned pricing configuration exists, so API cost was not computed.",
        "No adaptive tuning was performed between paired arms.",
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
