"""Offline M2.7 failure-mechanism audit.

This module deliberately does not execute SQL or call a provider.  It consumes
persisted M2.7 development rows and applies a more conservative root-cause
hierarchy than the first-pass structural classifier.
"""

# Long lines in this module are bounded report prose, not application logic.
# They are kept readable at the generated-report call sites.
# ruff: noqa: E501

import json
import re
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from evaluation.m27_forensics import sql_signature, structural_diff


class RootCause(StrEnum):
    OBJECT_SELECTION_ERROR = "OBJECT_SELECTION_ERROR"
    FILTER_CONSTRUCTION_ERROR = "FILTER_CONSTRUCTION_ERROR"
    AGGREGATION_GRAIN_ERROR = "AGGREGATION_GRAIN_ERROR"
    JOIN_COMPOSITION_ERROR = "JOIN_COMPOSITION_ERROR"
    ORDER_TOPK_ERROR = "ORDER_TOPK_ERROR"
    WINDOW_COMPOSITION_ERROR = "WINDOW_COMPOSITION_ERROR"
    SQL_COMPOSITION_ERROR = "SQL_COMPOSITION_ERROR"
    STRUCTURAL_CONTEXT_INSUFFICIENT = "STRUCTURAL_CONTEXT_INSUFFICIENT"
    BUSINESS_SEMANTIC_INFORMATION_MISSING = "BUSINESS_SEMANTIC_INFORMATION_MISSING"
    EVALUATOR_OR_FIXTURE_ARTIFACT = "EVALUATOR_OR_FIXTURE_ARTIFACT"
    POLICY_REJECTION = "POLICY_REJECTION"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    OTHER = "OTHER"


AUDIT_IDS = {
    "simple_filters": (
        "m2-001",
        "m2-002",
        "m2-003",
        "m2-004",
        "m2-005",
        "m2-006",
        "m2-007",
        "m2-008",
    ),
    "joins": ("m2-017", "m2-019", "m2-021"),
    "multi_table_joins": ("m2-024", "m2-027", "m2-028"),
    "top_k": ("m2-039", "m2-040", "m2-041", "m2-042"),
    "ratios": ("m2-043", "m2-044"),
    "window_functions": ("m2-046", "m2-047", "m2-048"),
    "aggregation_grouping": ("m2-015", "m2-035"),
    "date_filtering": ("m2-030",),
}

PRIMARY_CAUSES = tuple(RootCause)


def load_persisted_models(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load only the persisted model reports; no database or provider access."""
    return {
        model: json.loads((run_dir / f"{model}.json").read_text()) for model in ("luna", "terra")
    }


def analyze_row(row: dict[str, Any]) -> dict[str, Any]:
    """Attach conservative root-cause and structural mechanism evidence."""
    generated_sql = row.get("generated_sql")
    gold_sql = row["gold_sql"]
    gold_tree = parse_one(gold_sql, dialect="postgres")
    generated_tree = None
    parse_error = None
    if generated_sql:
        try:
            generated_tree = parse_one(generated_sql, dialect="postgres")
        except Exception as error:  # pragma: no cover - persisted bad SQL path
            parse_error = type(error).__name__

    gold_facts = _query_facts(gold_tree)
    generated_facts = _query_facts(generated_tree) if generated_tree is not None else None
    visibility = row.get("context_visibility") or {}
    equivalent = row.get("result_equivalent")
    primary = _primary_cause(
        row,
        gold_facts,
        generated_facts,
        visibility,
        parse_error,
    )
    high_confidence, unmatched = _column_alignment(gold_facts, generated_facts)
    model_io = row.get("model_io") or {}
    serialized_context = model_io.get("serialized_schema_context")
    return {
        "id": row["id"],
        "model": model_io.get("request_config", {}).get("model", row.get("model")),
        "category": row["category"],
        "question": row["question"],
        "model_visible_schema_excerpt": _schema_excerpt(
            serialized_context, gold_facts["tables"] | (generated_facts or {}).get("tables", set())
        ),
        "gold_sql": gold_sql,
        "generated_sql": generated_sql,
        "gold_result": {
            "available": False,
            "reason": "M2.7 persisted artifacts contain execution diagnostics, not result rows.",
        },
        "generated_result": {
            "available": False,
            "row_count": row.get("execution_row_count"),
            "reason": "M2.7 persisted artifacts contain execution diagnostics, not result rows.",
        },
        "gold_signature": row.get("gold_signature") or sql_signature(gold_sql),
        "generated_signature": (
            row.get("generated_signature")
            or (sql_signature(generated_sql) if generated_sql else None)
        ),
        "gold_structural_parts": _public_facts(gold_facts),
        "generated_structural_parts": (
            _public_facts(generated_facts) if generated_facts is not None else None
        ),
        "structural_diff": (
            row.get("structural_diff")
            or (
                structural_diff(sql_signature(gold_sql), sql_signature(generated_sql))
                if generated_sql
                else {}
            )
        ),
        "result_equivalent": equivalent,
        "equivalence_diagnostic": row.get("equivalence_diagnostic"),
        "failure_stage": row.get("failure_stage"),
        "parse_success": row.get("parse_success"),
        "plan_accepted": row.get("plan_accepted"),
        "execution_success": row.get("execution_success"),
        "primary_root_cause": primary,
        "secondary_tags": _secondary_tags(row, gold_facts, generated_facts, primary),
        "root_cause_basis": _root_cause_basis(primary, gold_facts, generated_facts),
        "column_analysis": {
            "high_confidence_substitutions": high_confidence,
            "unmatched_missing_expected_columns": unmatched["missing"],
            "unmatched_extra_generated_columns": unmatched["extra"],
        },
        "context_visibility": visibility,
        "input_sha256": model_io.get("input_sha256"),
        "schema_context_sha256": model_io.get("schema_context_sha256"),
    }


def build_audit(models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build all-case classifications and the mandated stratified audit set."""
    analyses: dict[str, list[dict[str, Any]]] = {}
    for model, report in models.items():
        analyses[model] = [analyze_row(row) for row in report["cases"]]

    by_model_id = {model: {row["id"]: row for row in rows} for model, rows in analyses.items()}
    audit_ids = [case_id for ids in AUDIT_IDS.values() for case_id in ids]
    audit_cases = [
        by_model_id[model][case_id] for case_id in audit_ids for model in ("luna", "terra")
    ]
    return {
        "source_run": "evaluation/results/m27/dev/20260903T023000Z/recomputed-v2",
        "source_dataset": "evaluation/datasets/m2_baseline.json",
        "provider_calls": 0,
        "holdout_used": False,
        "classifier_audit": _classifier_audit(models),
        "root_cause_counts": {model: _primary_counts(rows) for model, rows in analyses.items()},
        "artifact_warning_counts": {
            model: sum(
                row["primary_root_cause"] == RootCause.EVALUATOR_OR_FIXTURE_ARTIFACT.value
                for row in rows
            )
            for model, rows in analyses.items()
        },
        "primary_cause_denominators": {
            model: sum(
                row["result_equivalent"] is not True and row["primary_root_cause"] is not None
                for row in rows
            )
            for model, rows in analyses.items()
        },
        "category_root_cause_matrix": _category_matrix(analyses),
        "model_pairwise": _pairwise(analyses),
        "column_confusion": _column_report(analyses),
        "context_audit": _context_audit(analyses),
        "simple_filters_audit": _category_rows(analyses, "simple_filters"),
        "top_k_failure_matrix": _mechanism_matrix(analyses, "top_k"),
        "window_failure_matrix": _mechanism_matrix(analyses, "window_functions"),
        "join_audit": _join_audit(analyses),
        "audit_case_count": len(audit_cases),
        "audit_question_count": len(audit_ids),
        "audit_case_ids": audit_ids,
        "audit_cases": audit_cases,
    }


def _primary_cause(
    row: dict[str, Any],
    gold: dict[str, Any],
    generated: dict[str, Any] | None,
    visibility: dict[str, Any],
    parse_error: str | None,
) -> str | None:
    stage = row.get("failure_stage")
    if not row.get("generated_sql") or stage == "SQL_GENERATION_ERROR":
        return RootCause.PROVIDER_FAILURE.value
    if stage == "POLICY_REJECTION":
        return RootCause.POLICY_REJECTION.value
    if row.get("result_equivalent") is True:
        return (
            RootCause.EVALUATOR_OR_FIXTURE_ARTIFACT.value
            if row.get("equivalence_diagnostic")
            or row.get("forensic_cause") == "EVALUATOR_OR_FIXTURE_WARNING"
            else None
        )
    if generated is None or parse_error:
        return RootCause.SQL_COMPOSITION_ERROR.value
    if not (
        visibility.get("gold_tables_visible", True)
        and visibility.get("gold_columns_visible", True)
        and visibility.get("gold_relationships_visible", True)
    ):
        return RootCause.STRUCTURAL_CONTEXT_INSUFFICIENT.value

    category = row["category"]
    if _business_semantic_difference(row, gold, generated):
        return RootCause.BUSINESS_SEMANTIC_INFORMATION_MISSING.value

    if _substantive_filter_difference(gold, generated):
        if _filter_column_difference(gold, generated):
            return RootCause.OBJECT_SELECTION_ERROR.value
        return RootCause.FILTER_CONSTRUCTION_ERROR.value

    if category == "top_k":
        if _substantive_aggregation_difference(gold, generated):
            return RootCause.AGGREGATION_GRAIN_ERROR.value
        if _order_or_limit_difference(gold, generated):
            return RootCause.ORDER_TOPK_ERROR.value

    if category == "window_functions":
        if _projection_difference(gold, generated):
            return RootCause.OBJECT_SELECTION_ERROR.value
        if _window_difference(gold, generated):
            return RootCause.WINDOW_COMPOSITION_ERROR.value

    if _table_difference(gold, generated) and _projection_difference(gold, generated):
        return RootCause.OBJECT_SELECTION_ERROR.value
    if _substantive_aggregation_difference(gold, generated):
        return RootCause.AGGREGATION_GRAIN_ERROR.value
    if _join_difference(gold, generated) and not _projection_difference(gold, generated):
        return RootCause.JOIN_COMPOSITION_ERROR.value
    if _projection_difference(gold, generated):
        return RootCause.OBJECT_SELECTION_ERROR.value
    if _window_difference(gold, generated):
        return RootCause.WINDOW_COMPOSITION_ERROR.value
    if _order_or_limit_difference(gold, generated):
        return RootCause.ORDER_TOPK_ERROR.value
    return RootCause.SQL_COMPOSITION_ERROR.value


def _query_facts(tree: exp.Expression | None) -> dict[str, Any]:
    if tree is None:
        return {
            "tables": set(),
            "columns": set(),
            "filters": (),
            "filter_columns": (),
            "join_types": (),
            "aggregations": (),
            "group_by": (),
            "order_by": (),
            "limit": None,
            "windows": (),
            "window_functions": (),
            "window_partitions": (),
            "window_orders": (),
            "projections": (),
        }
    signature = sql_signature(tree.sql(dialect="postgres"))
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    projections = tuple(
        _normalize_expression(item.this if isinstance(item, exp.Alias) else item)
        for item in (select.expressions if select is not None else [])
    )
    return {
        **signature,
        "filters_normalized": _where_parts(tree),
        "filter_columns": _where_columns(tree),
        "join_types": _join_type_parts(tree),
        "aggregations_normalized": _aggregate_parts(tree),
        "group_by_normalized": _group_parts(tree),
        "order_by_normalized": _order_parts(tree),
        "windows_normalized": _window_parts(tree),
        "projections": projections,
        "tables": set(signature["tables"]),
        "columns": set(signature["columns"]),
    }


def _public_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        key: sorted(value)
        if isinstance(value, set)
        else list(value)
        if isinstance(value, tuple)
        else value
        for key, value in facts.items()
        if key
        not in {
            "filters_normalized",
            "filter_columns",
            "join_types",
            "aggregations_normalized",
            "group_by_normalized",
            "order_by_normalized",
            "windows_normalized",
            "window_functions",
            "window_partitions",
            "window_orders",
            "projections",
            "tables",
            "columns",
        }
    } | {
        "tables": sorted(facts["tables"]),
        "columns": sorted(facts["columns"]),
        "filters": list(facts["filters_normalized"]),
        "aggregations": list(facts["aggregations_normalized"]),
        "group_by": list(facts["group_by_normalized"]),
        "order_by": list(facts["order_by_normalized"]),
        "limit": facts["limit"],
        "windows": list(facts["windows_normalized"]),
        "join_types": list(facts["join_types"]),
        "window_functions": [item[0] for item in facts["windows_normalized"]],
        "window_partitions": [item[1] for item in facts["windows_normalized"]],
        "window_orders": [item[2] for item in facts["windows_normalized"]],
        "projections": list(facts["projections"]),
    }


def _normalize_expression(value: exp.Expression) -> str:
    copy = value.copy()
    for column in copy.find_all(exp.Column):
        column.set("catalog", None)
        column.set("db", None)
        column.set("table", None)
    for cast in list(copy.find_all(exp.Cast)):
        if isinstance(cast.this, exp.Literal):
            cast.replace(cast.this.copy())
    return copy.sql(dialect="postgres").lower()


def _where_parts(tree: exp.Expression) -> tuple[str, ...]:
    return tuple(sorted(_normalize_expression(item.this) for item in tree.find_all(exp.Where)))


def _where_columns(tree: exp.Expression) -> tuple[str, ...]:
    return tuple(
        sorted(
            _normalize_expression(column)
            for where in tree.find_all(exp.Where)
            for column in where.find_all(exp.Column)
        )
    )


def _join_type_parts(tree: exp.Expression) -> tuple[str, ...]:
    parts = []
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        edge = (
            "<->".join(sorted(_normalize_expression(column) for column in on.find_all(exp.Column)))
            if on is not None
            else ""
        )
        parts.append(f"{edge}|{(join.side or 'INNER').upper()}")
    return tuple(sorted(parts))


def _aggregate_parts(tree: exp.Expression) -> tuple[str, ...]:
    return tuple(sorted(_normalize_expression(item) for item in tree.find_all(exp.AggFunc)))


def _group_parts(tree: exp.Expression) -> tuple[str, ...]:
    group = tree.find(exp.Group)
    return tuple(sorted(_normalize_expression(item) for item in group.expressions)) if group else ()


def _order_parts(tree: exp.Expression) -> tuple[str, ...]:
    return tuple(_normalize_expression(item) for item in tree.find_all(exp.Ordered))


def _window_parts(tree: exp.Expression) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    parts = []
    for window in tree.find_all(exp.Window):
        function = _normalize_expression(window.this) if window.this is not None else ""
        partitions = tuple(
            _normalize_expression(item) for item in window.args.get("partition_by") or []
        )
        order = window.args.get("order")
        orders = tuple(_normalize_expression(item) for item in order.expressions) if order else ()
        parts.append((function, partitions, orders))
    return tuple(sorted(parts))


def _projection_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return bool(gold["projections"] != generated["projections"])


def _substantive_filter_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return bool(gold["filters_normalized"] != generated["filters_normalized"])


def _filter_column_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return (
        bool(gold["filter_columns"])
        and bool(generated["filter_columns"])
        and bool(set(gold["filter_columns"]) != set(generated["filter_columns"]))
    )


def _substantive_aggregation_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return bool(gold["aggregations_normalized"] != generated["aggregations_normalized"])


def _table_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return bool(gold["tables"] != generated["tables"])


def _join_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return bool(
        gold["joins"] != generated["joins"]
        or gold["join_types"] != generated["join_types"]
    )


def _window_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return bool(gold["windows_normalized"] != generated["windows_normalized"])


def _order_or_limit_difference(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    return bool(
        gold["limit"] != generated["limit"]
        or gold["order_by_normalized"] != generated["order_by_normalized"]
    )


def _business_semantic_difference(
    row: dict[str, Any], gold: dict[str, Any], generated: dict[str, Any]
) -> bool:
    question = row["question"].lower()
    if "revenue" not in question:
        return False
    generated_sql = (row.get("generated_sql") or "").lower()
    if "discount_amount" in generated_sql and "discount_amount" not in " ".join(gold["columns"]):
        return True
    if "status" in generated_sql and "status" not in " ".join(gold["columns"]):
        return True
    return "discount_amount" in generated_sql and _substantive_aggregation_difference(
        gold, generated
    )


def _secondary_tags(
    row: dict[str, Any],
    gold: dict[str, Any],
    generated: dict[str, Any] | None,
    primary: str | None,
) -> list[str]:
    if generated is None:
        return []
    tags: list[str] = []
    if _projection_difference(gold, generated):
        tags.append("PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE")
    if _substantive_filter_difference(gold, generated):
        tags.append("FILTER_DIFFERENCE")
    if _substantive_aggregation_difference(gold, generated):
        tags.append("AGGREGATION_DIFFERENCE")
    if gold["group_by_normalized"] != generated["group_by_normalized"]:
        tags.append("GROUPING_DIFFERENCE")
    if _join_difference(gold, generated):
        tags.append(
            "JOIN_EDGE_DIFFERENCE"
            if gold["joins"] != generated["joins"]
            else "JOIN_TYPE_DIFFERENCE"
        )
    if _window_difference(gold, generated):
        tags.append("WINDOW_DIFFERENCE")
    if gold["order_by_normalized"] != generated["order_by_normalized"]:
        tags.append("ORDER_DIFFERENCE")
    if gold["limit"] != generated["limit"]:
        tags.append("LIMIT_DIFFERENCE")
    if primary == RootCause.POLICY_REJECTION.value:
        tags.extend(_policy_function_tags(row.get("generated_sql") or ""))
    return sorted(set(tags))


def _policy_function_tags(sql: str) -> list[str]:
    try:
        tree = parse_one(sql, dialect="postgres")
    except Exception:
        return []
    safe = {
        "AVG",
        "COALESCE",
        "COUNT",
        "DATE_TRUNC",
        "EXTRACT",
        "MAX",
        "MIN",
        "RANK",
        "ROW_NUMBER",
        "LAG",
        "SUM",
        "CAST",
    }
    names = {
        (function.name or type(function).__name__).upper()
        for function in tree.find_all(exp.Func)
        if not isinstance(function, (exp.Case, exp.If, exp.Connector))
    }
    return [f"FORBIDDEN_FUNCTION:{name}" for name in sorted(names - safe)]


def _root_cause_basis(
    primary: str | None, gold: dict[str, Any], generated: dict[str, Any] | None
) -> str:
    if primary == RootCause.POLICY_REJECTION.value:
        return "M1 rejected the generated SQL before execution."
    if generated is None:
        return "No parseable generated SQL was persisted."
    if primary == RootCause.OBJECT_SELECTION_ERROR.value:
        return "Visible schema objects were available, but projection/table selection differed materially."
    if primary == RootCause.FILTER_CONSTRUCTION_ERROR.value:
        return "The selected filter field was retained but the predicate changed materially."
    if primary == RootCause.AGGREGATION_GRAIN_ERROR.value:
        return (
            "The selected relational objects were present, but aggregate or grouping grain changed."
        )
    if primary == RootCause.ORDER_TOPK_ERROR.value:
        return "The core measure/entity was substantially aligned, but ORDER BY/LIMIT changed."
    if primary == RootCause.WINDOW_COMPOSITION_ERROR.value:
        return "The window operation, partition, or window ordering changed."
    if primary == RootCause.BUSINESS_SEMANTIC_INFORMATION_MISSING.value:
        return "Structural schema does not define the business inclusion/formula choice."
    if primary == RootCause.EVALUATOR_OR_FIXTURE_ARTIFACT.value:
        return "Execution equivalence succeeded; structural divergence is retained as a warning."
    return "Conservative fallback after higher-precedence mechanisms were excluded."


def _column_alignment(
    gold: dict[str, Any], generated: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    if generated is None:
        return [], {"missing": sorted(gold["columns"]), "extra": []}
    high: list[dict[str, Any]] = []
    gold_projection = gold["projections"]
    generated_projection = generated["projections"]
    for index, (expected, actual) in enumerate(
        zip(gold_projection, generated_projection, strict=False)
    ):
        if expected == actual:
            continue
        expected_columns = _simple_columns(expected)
        actual_columns = _simple_columns(actual)
        expected_shape = _expression_shape(expected)
        actual_shape = _expression_shape(actual)
        if (
            len(expected_columns) == len(actual_columns) == 1
            and expected_shape == actual_shape
            and expected_shape != "COLUMN"
        ):
            high.append(
                {
                    "ordinal": index,
                    "generated_column": actual_columns[0],
                    "expected_column": expected_columns[0],
                    "evidence": "same ordinal and non-trivial expression shape with one referenced column each",
                }
            )
    missing = sorted(gold["columns"] - generated["columns"])
    extra = sorted(generated["columns"] - gold["columns"])
    return high, {"missing": missing, "extra": extra}


def _simple_columns(expression: str) -> list[str]:
    try:
        tree = parse_one(expression, dialect="postgres")
    except Exception:
        return []
    return sorted(
        _normalize_expression(column) for column in tree.find_all(exp.Column) if column.name != "*"
    )


def _expression_shape(expression: str) -> str:
    try:
        tree = parse_one(expression, dialect="postgres")
    except Exception:
        return expression
    if isinstance(tree, exp.Column):
        return "COLUMN"
    for column in list(tree.find_all(exp.Column)):
        column.replace(exp.Var(this="COLUMN"))
    return tree.sql(dialect="postgres").lower()


def _schema_excerpt(serialized: str | None, tables: set[str]) -> str | None:
    if not serialized:
        return None
    lines = serialized.splitlines()
    sections: list[list[str]] = []
    current_section: list[str] = []
    current_table = ""
    for line in lines:
        match = re.match(r"\[Table\]\s+([A-Za-z0-9_]+)", line)
        if match:
            if current_section and current_table in tables:
                sections.append(current_section)
            current_section = [line]
            current_table = match.group(1).lower()
        elif current_section:
            current_section.append(line)
    if current_section and current_table in tables:
        sections.append(current_section)
    selected = [line for section in sections for line in section]
    relationship_lines = [line for line in lines if line.startswith("- ") and " -> " in line]
    if relationship_lines:
        selected.extend(["", "[Relationships]"] + relationship_lines)
    return "\n".join(selected)


def _primary_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        row["primary_root_cause"]
        for row in rows
        if row["result_equivalent"] is not True and row["primary_root_cause"]
    )
    return {cause.value: counts.get(cause.value, 0) for cause in PRIMARY_CAUSES}


def _category_matrix(
    analyses: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, dict[str, int]]]:
    output: dict[str, dict[str, dict[str, int]]] = {}
    for model, rows in analyses.items():
        output[model] = {}
        for row in rows:
            category = row["category"]
            output[model].setdefault(category, {})
            if row["result_equivalent"] is True:
                continue
            cause = row["primary_root_cause"]
            if cause:
                output[model][category][cause] = output[model][category].get(cause, 0) + 1

    return output


def _category_rows(
    analyses: dict[str, list[dict[str, Any]]], category: str
) -> dict[str, list[dict[str, Any]]]:
    return {
        model: [row for row in rows if row["category"] == category]
        for model, rows in analyses.items()
    }


def _mechanism_matrix(
    analyses: dict[str, list[dict[str, Any]]], category: str
) -> dict[str, list[dict[str, Any]]]:
    matrix: dict[str, list[dict[str, Any]]] = {}
    for model, rows in analyses.items():
        records = []
        for row in rows:
            if row["category"] != category:
                continue
            gold = row["gold_structural_parts"]
            generated = row["generated_structural_parts"] or {}
            records.append(
                {
                    "id": row["id"],
                    "result_equivalent": row["result_equivalent"],
                    "measure_or_aggregation_correct": gold["aggregations"]
                    == generated.get("aggregations", []),
                    "grouping_entity_correct": gold["group_by"] == generated.get("group_by", [])
                    or category == "top_k",
                    "aggregation_correct": gold["aggregations"]
                    == generated.get("aggregations", []),
                    "ordering_correct": gold["order_by"] == generated.get("order_by", []),
                    "direction_correct": _direction_tuple(gold["order_by"])
                    == _direction_tuple(generated.get("order_by", [])),
                    "limit_correct": gold["limit"] == generated.get("limit"),
                    "window_recognized": bool(gold["windows"]) == bool(generated.get("windows")),
                    "window_function_correct": gold.get("window_functions", [])
                    == generated.get("window_functions", []),
                    "partition_correct": gold.get("window_partitions", [])
                    == generated.get("window_partitions", []),
                    "window_order_correct": gold.get("window_orders", [])
                    == generated.get("window_orders", []),
                    "projection_correct": gold["projections"] == generated.get("projections", []),
                    "primary_root_cause": row["primary_root_cause"],
                }
            )
        matrix[model] = records
    return matrix


def _direction_tuple(values: list[str]) -> tuple[str, ...]:
    return tuple("DESC" if " desc" in value.lower() else "ASC" for value in values)


def _join_audit(analyses: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rows = {
        model: [
            {
                "id": row["id"],
                "category": row["category"],
                "result_equivalent": row["result_equivalent"],
                "primary_root_cause": row["primary_root_cause"],
                "join_difference": "JOIN_EDGE_DIFFERENCE" in row["secondary_tags"],
                "join_type_difference": "JOIN_TYPE_DIFFERENCE" in row["secondary_tags"],
                "gold_joins": row["gold_structural_parts"]["joins"],
                "generated_joins": (row["generated_structural_parts"] or {}).get("joins", []),
                "gold_join_types": row["gold_structural_parts"].get("join_types", []),
                "generated_join_types": (row["generated_structural_parts"] or {}).get(
                    "join_types", []
                ),
                "secondary_tags": row["secondary_tags"],
            }
            for row in rows
            if row["category"] in {"joins", "multi_table_joins"}
        ]
        for model, rows in analyses.items()
    }
    return {
        "rows": rows,
        "primary_join_composition_error": {
            model: sum(
                row["primary_root_cause"] == RootCause.JOIN_COMPOSITION_ERROR.value for row in items
            )
            for model, items in rows.items()
        },
        "rows_with_join_edge_difference": {
            model: sum(
                row["join_difference"] and row["result_equivalent"] is not True
                for row in items
            )
            for model, items in rows.items()
        },
        "rows_with_join_type_difference": {
            model: sum(
                row["join_type_difference"] and row["result_equivalent"] is not True
                for row in items
            )
            for model, items in rows.items()
        },
    }


def _column_report(analyses: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    high: dict[str, Counter[str]] = {"luna": Counter(), "terra": Counter()}
    unmatched: dict[str, Counter[str]] = {"luna": Counter(), "terra": Counter()}
    for model, rows in analyses.items():
        for row in rows:
            if row["result_equivalent"] is not False:
                continue
            for item in row["column_analysis"]["high_confidence_substitutions"]:
                high[model][f"{item['generated_column']} -> {item['expected_column']}"] += 1
            for value in row["column_analysis"]["unmatched_missing_expected_columns"]:
                unmatched[model][f"MISSING_EXPECTED_COLUMN:{value}"] += 1
            for value in row["column_analysis"]["unmatched_extra_generated_columns"]:
                unmatched[model][f"EXTRA_GENERATED_COLUMN:{value}"] += 1
    return {
        "high_confidence_substitutions": {model: dict(counter) for model, counter in high.items()},
        "unmatched_missing_or_extra": {
            model: dict(counter) for model, counter in unmatched.items()
        },
    }


def _context_audit(analyses: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for model, rows in analyses.items():
        incorrect = [row for row in rows if row["result_equivalent"] is not True]
        output[model] = {
            "incorrect_or_rejected": len(incorrect),
            "gold_tables_visible": sum(
                row["context_visibility"].get("gold_tables_visible", False) for row in incorrect
            ),
            "gold_columns_visible": sum(
                row["context_visibility"].get("gold_columns_visible", False) for row in incorrect
            ),
            "gold_relationships_visible": sum(
                row["context_visibility"].get("gold_relationships_visible", False)
                for row in incorrect
            ),
            "missing_table_cases": [
                row["id"] for row in incorrect if row["context_visibility"].get("missing_tables")
            ],
            "missing_column_cases": [
                row["id"] for row in incorrect if row["context_visibility"].get("missing_columns")
            ],
            "missing_relationship_cases": [
                row["id"]
                for row in incorrect
                if row["context_visibility"].get("missing_relationships")
            ],
        }
    return output


def _pairwise(analyses: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    luna = {row["id"]: row for row in analyses["luna"]}
    terra = {row["id"]: row for row in analyses["terra"]}
    counts: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    for case_id in luna:
        left = luna[case_id]["result_equivalent"] is True
        right = terra[case_id]["result_equivalent"] is True
        outcome = (
            "BOTH_CORRECT"
            if left and right
            else "LUNA_ONLY_CORRECT"
            if left
            else "TERRA_ONLY_CORRECT"
            if right
            else "BOTH_INCORRECT"
        )
        counts[outcome] += 1
        if outcome != "BOTH_CORRECT":
            details.append(
                {
                    "id": case_id,
                    "outcome": outcome,
                    "luna_root_cause": luna[case_id]["primary_root_cause"],
                    "terra_root_cause": terra[case_id]["primary_root_cause"],
                    "luna_structural_diff": luna[case_id]["structural_diff"],
                    "terra_structural_diff": terra[case_id]["structural_diff"],
                }
            )
    both_wrong = [item for item in details if item["outcome"] == "BOTH_INCORRECT"]
    same = sum(item["luna_root_cause"] == item["terra_root_cause"] for item in both_wrong)
    return {
        "counts": dict(sorted(counts.items())),
        "both_incorrect_same_root_cause": same,
        "both_incorrect_different_root_cause": len(both_wrong) - same,
        "details": details,
    }


def _classifier_audit(models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for model, report in models.items():
        rows = report["cases"]
        old = Counter(row.get("forensic_cause") for row in rows if row.get("forensic_cause"))
        output[model] = {
            "trust_assessment": "NOT_TRUSTWORTHY_AS_PRIMARY_CAUSE",
            "over_triggered_rules": [
                "ORDER_LIMIT_REASONING_ERROR triggered by any raw ORDER_BY diff, including projection/qualifier cases.",
                "COLUMN_SUBSTITUTED paired arbitrary set differences rather than aligned expressions.",
                "WINDOW_REASONING_ERROR treated qualifier-only window text differences as semantic differences.",
            ],
            "original_cause_counts": dict(old),
        }
    return output
