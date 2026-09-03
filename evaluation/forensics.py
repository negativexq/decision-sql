"""Read-only forensic analysis for the persisted M2 baseline.

This module deliberately does not modify the evaluator or baseline artifacts. It
replays only the persisted generated SQL and dataset gold SQL through the M1
reader boundary and writes a separate diagnostic audit.
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from app.catalog.default import build_default_catalog
from app.catalog.models import SchemaCatalog, SchemaContext
from app.config import get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlExecutionError
from app.sql.service import SqlSafetyService
from evaluation.metrics import compare_query_results
from evaluation.models import BaselineCase, BaselineReport
from evaluation.runner import load_baseline

RUN_DIR = Path("evaluation/results/m2/m2-real-20260903")
DATASET = Path("evaluation/datasets/m2_baseline.json")
AUDIT_PATH = RUN_DIR / "failure_analysis.json"
REPORT_PATH = Path("docs/m2-failure-analysis.md")


# These are diagnosis labels, not evaluator behavior. The evaluator remains
# unchanged; labels are assigned after inspecting SQL structure and replayed
# results. Cases with only output-label differences are detected separately.
MANUAL_CLASSES: dict[str, dict[str, str]] = {
    "full": {
        "m2-001": "WRONG_COLUMN",
        "m2-003": "WRONG_TABLE",
        "m2-005": "WRONG_TABLE",
        "m2-006": "WRONG_COLUMN",
        "m2-007": "WRONG_TABLE",
        "m2-008": "WRONG_COLUMN",
        "m2-017": "WRONG_AGGREGATION",
        "m2-018": "WRONG_COLUMN",
        "m2-019": "WRONG_COLUMN",
        "m2-020": "WRONG_COLUMN",
        "m2-023": "WRONG_COLUMN",
        "m2-024": "WRONG_COLUMN",
        "m2-025": "WRONG_COLUMN",
        "m2-026": "WRONG_COLUMN",
        "m2-027": "WRONG_COLUMN",
        "m2-028": "WRONG_GROUPING",
        "m2-031": "WRONG_DATE_BOUNDARY",
        "m2-033": "WRONG_COLUMN",
        "m2-035": "WRONG_JOIN",
        "m2-039": "WRONG_LIMIT",
        "m2-040": "WRONG_LIMIT",
        "m2-041": "WRONG_COLUMN",
        "m2-042": "WRONG_LIMIT",
        "m2-044": "WRONG_RATIO_DENOMINATOR",
        "m2-045": "WRONG_RATIO_SCALING",
        "m2-046": "WRONG_WINDOW_LOGIC",
        "m2-047": "WRONG_WINDOW_LOGIC",
        "m2-048": "WRONG_WINDOW_LOGIC",
    },
    "retrieved": {
        "m2-001": "WRONG_COLUMN",
        "m2-003": "WRONG_COLUMN",
        "m2-004": "WRONG_COLUMN",
        "m2-005": "WRONG_COLUMN",
        "m2-006": "WRONG_COLUMN",
        "m2-007": "WRONG_COLUMN",
        "m2-008": "WRONG_COLUMN",
        "m2-015": "WRONG_FILTER",
        "m2-017": "WRONG_AGGREGATION",
        "m2-018": "WRONG_COLUMN",
        "m2-019": "WRONG_COLUMN",
        "m2-020": "WRONG_COLUMN",
        "m2-021": "WRONG_COLUMN",
        "m2-023": "WRONG_COLUMN",
        "m2-024": "WRONG_COLUMN",
        "m2-025": "WRONG_COLUMN",
        "m2-026": "WRONG_COLUMN",
        "m2-027": "SCHEMA_CONTEXT_MISSING",
        "m2-028": "WRONG_GROUPING",
        "m2-030": "WRONG_COLUMN",
        "m2-031": "WRONG_DATE_BOUNDARY",
        "m2-033": "WRONG_COLUMN",
        "m2-035": "WRONG_JOIN",
        "m2-039": "WRONG_LIMIT",
        "m2-040": "WRONG_COLUMN",
        "m2-041": "WRONG_COLUMN",
        "m2-042": "WRONG_LIMIT",
        "m2-043": "WRONG_RATIO_SCALING",
        "m2-044": "WRONG_RATIO_DENOMINATOR",
        "m2-045": "WRONG_COLUMN",
        "m2-046": "WRONG_ORDERING",
        "m2-047": "WRONG_COLUMN",
        "m2-048": "WRONG_WINDOW_LOGIC",
    },
}

ALIAS_ONLY_FALSE_NEGATIVES: dict[str, set[str]] = {
    "full": {
        "m2-009",
        "m2-010",
        "m2-012",
        "m2-013",
        "m2-014",
        "m2-015",
        "m2-022",
        "m2-029",
        "m2-030",
        "m2-034",
        "m2-038",
        "m2-043",
    },
    "retrieved": {
        "m2-009",
        "m2-010",
        "m2-012",
        "m2-013",
        "m2-014",
        "m2-022",
        "m2-029",
        "m2-032",
        "m2-038",
    },
}

FIXTURE_EQUIVALENT_SEMANTIC_WARNINGS: dict[str, dict[str, str]] = {
    "retrieved": {
        "m2-011": "WRONG_AGGREGATION",
        "m2-034": "WRONG_AGGREGATION",
        "m2-036": "WRONG_AGGREGATION",
    }
}

M3_HIGH_IDS = {
    "m2-039",
    "m2-040",
    "m2-043",
    "m2-044",
    "m2-045",
}
M3_MEDIUM_CLASSES = {"SCHEMA_CONTEXT_MISSING", "WRONG_AGGREGATION", "WRONG_GROUPING"}
REPRESENTATIVE_IDS = {
    "full": ("m2-009", "m2-017", "m2-024", "m2-031", "m2-039", "m2-044", "m2-046"),
    "retrieved": (
        "m2-001",
        "m2-015",
        "m2-019",
        "m2-028",
        "m2-035",
        "m2-043",
        "m2-048",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze persisted M2 failures")
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--audit", type=Path, default=AUDIT_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    settings = get_settings()
    catalog = build_default_catalog(Base.metadata)
    cases = {case.id: case for case in load_baseline(args.dataset)}
    reports = {
        "full": BaselineReport.model_validate(
            json.loads((args.run_dir / "full_schema.json").read_text())
        ),
        "retrieved": BaselineReport.model_validate(
            json.loads((args.run_dir / "retrieved_schema.json").read_text())
        ),
    }
    resolver = SchemaContextResolver(
        catalog,
        top_k=settings.schema_top_k,
        max_tables=settings.max_context_tables,
        max_columns_per_table=settings.max_columns_per_table,
        relationship_depth=settings.relationship_depth,
    )
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings, catalog=catalog)

    analyzed: dict[str, list[dict[str, Any]]] = {}
    for mode in ("full", "retrieved"):
        persisted = {item.id: item for item in reports[mode].cases}
        mode_rows: list[dict[str, Any]] = []
        for case_id in sorted(cases):
            case = cases[case_id]
            saved = persisted[case_id]
            generated = _replay(safety, saved.generated_sql, f"{mode}-{case_id}-generated")
            gold = _replay(safety, case.gold_sql, f"{mode}-{case_id}-gold")
            current_equivalence = (
                compare_query_results(generated, gold)
                if isinstance(generated, QueryExecution) and isinstance(gold, QueryExecution)
                else False
            )
            context = resolver.resolve(case.question, mode=SchemaContextMode(mode))
            class_name = _classify(mode, case, saved, generated, gold, current_equivalence)
            if current_equivalence:
                class_name = None
            semantic_warning = _semantic_warning(mode, case.id, current_equivalence)
            mode_rows.append(
                {
                    "mode": mode,
                    "id": case.id,
                    "category": case.category,
                    "question": case.question,
                    "gold_sql": case.gold_sql,
                    "generated_sql": saved.generated_sql,
                    "persisted_equivalence": saved.result_equivalent,
                    "current_equivalence_recomputed": current_equivalence,
                    "equivalence_consistent_with_persisted": (
                        saved.result_equivalent == current_equivalence
                    ),
                    "primary_failure_class": class_name,
                    "semantic_warning": semantic_warning,
                    "manual_assessment": _manual_assessment(
                        class_name, semantic_warning, current_equivalence
                    ),
                    "m3_relevance": _m3_relevance(case.id, class_name),
                    "failure_stage": saved.failure_stage,
                    "generated": _execution_payload(generated),
                    "gold": _execution_payload(gold),
                    "context_adequacy": _context_adequacy(context, case, catalog),
                }
            )
        analyzed[mode] = mode_rows

    payload = _build_audit_payload(args.run_dir, args.dataset, reports, analyzed)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_render_report(payload))
    print(json.dumps({"audit": str(args.audit), "report": str(args.report)}))


def _replay(
    safety: SqlSafetyService, sql: str | None, correlation_id: str
) -> QueryExecution | SqlExecutionError:
    if not sql:
        return SqlExecutionError(error="Persisted generated SQL is missing.")
    planned = safety.plan(SqlCandidate(sql=sql, correlation_id=correlation_id))
    if not isinstance(planned, QueryPlan):
        return SqlExecutionError(error="Persisted SQL could not be replanned for analysis.")
    return safety.execute(planned)


def _execution_payload(value: QueryExecution | SqlExecutionError) -> dict[str, Any]:
    if isinstance(value, QueryExecution):
        return {"status": "success", **value.model_dump(mode="json")}
    return {"status": "error", **value.model_dump(mode="json")}


def _classify(
    mode: str,
    case: BaselineCase,
    saved: Any,
    generated: QueryExecution | SqlExecutionError,
    gold: QueryExecution | SqlExecutionError,
    current_equivalence: bool,
) -> str | None:
    if current_equivalence:
        return None
    if isinstance(generated, QueryExecution) and isinstance(gold, QueryExecution):
        if case.id in ALIAS_ONLY_FALSE_NEGATIVES.get(mode, set()) and _positional_equivalence(
            generated, gold, order_sensitive=case.order_sensitive
        ):
            return "GOLD_OR_EVAL_ISSUE"
    return MANUAL_CLASSES[mode].get(case.id, "OTHER")


def _semantic_warning(mode: str, case_id: str, current_equivalence: bool) -> str | None:
    if current_equivalence and case_id in FIXTURE_EQUIVALENT_SEMANTIC_WARNINGS.get(mode, {}):
        return "POSSIBLE_FALSE_POSITIVE_FIXTURE_EQUIVALENCE"
    if not current_equivalence and case_id in ALIAS_ONLY_FALSE_NEGATIVES.get(mode, set()):
        return "LIKELY_FALSE_NEGATIVE_ALIAS_ONLY"
    return None


def _manual_assessment(
    class_name: str | None, semantic_warning: str | None, current_equivalence: bool
) -> str:
    if semantic_warning == "LIKELY_FALSE_NEGATIVE_ALIAS_ONLY":
        return (
            "Current verdict is likely a false negative: replayed values and row shape "
            "match, and only output aliases differ."
        )
    if semantic_warning == "POSSIBLE_FALSE_POSITIVE_FIXTURE_EQUIVALENCE":
        return (
            "Current verdict is true on this fixture, but the generated SQL uses a "
            "different semantic expression; result equality may be data-specific."
        )
    if not current_equivalence:
        return f"Current verdict is substantively correct; primary cause is {class_name}."
    return "Current verdict is consistent with the inspected SQL and result."


def _positional_equivalence(
    actual: QueryExecution, expected: QueryExecution, *, order_sensitive: bool
) -> bool:
    if actual.truncated != expected.truncated or len(actual.columns) != len(expected.columns):
        return False
    actual_rows = [_row_by_position(actual, row) for row in actual.rows]
    expected_rows = [_row_by_position(expected, row) for row in expected.rows]
    if not order_sensitive:
        actual_rows.sort(key=repr)
        expected_rows.sort(key=repr)
    return actual_rows == expected_rows


def _row_by_position(execution: QueryExecution, row: dict[str, object]) -> tuple[object, ...]:
    return tuple(_json_value(row.get(column)) for column in execution.columns)


def _json_value(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def _context_adequacy(
    context: SchemaContext, case: BaselineCase, catalog: SchemaCatalog
) -> dict[str, Any]:
    gold_ast = parse_one(case.gold_sql, dialect="postgres")
    expected_tables = set(case.expected_tables)
    selected_tables = {table.name for table in context.tables}
    aliases = {
        table.alias_or_name.lower(): table.name.lower()
        for table in gold_ast.find_all(exp.Table)
        if table.name.lower() in expected_tables
    }
    output_aliases = {
        alias.alias_or_name.lower()
        for alias in gold_ast.find_all(exp.Alias)
        if alias.alias_or_name
    }
    required_columns: set[str] = set()
    for column in gold_ast.find_all(exp.Column):
        table_name = aliases.get((column.table or "").lower())
        if table_name:
            required_columns.add(f"{table_name}.{column.name.lower()}")
        elif column.name.lower() not in output_aliases and column.name != "*":
            required_columns.add(column.name.lower())
    selected_columns = {
        f"{table.name}.{column.name}" for table in context.tables for column in table.columns
    }
    qualified_columns_present = all(
        item in selected_columns for item in required_columns if "." in item
    )
    unqualified_required = {item for item in required_columns if "." not in item}
    unqualified_present = all(
        any(item == column.name for table in context.tables for column in table.columns)
        for item in unqualified_required
    )
    relationships_required = [
        (
            table.name,
            relationship.column,
            relationship.referenced_table,
            relationship.referenced_column,
        )
        for table in catalog.tables
        for relationship in table.relationships
        if table.name in expected_tables and relationship.referenced_table in expected_tables
    ]
    selected_relationships = {
        (
            relationship.source_table,
            relationship.source_column,
            relationship.target_table,
            relationship.target_column,
        )
        for relationship in context.relationships
    }
    relationships_present = all(
        relationship in selected_relationships for relationship in relationships_required
    )
    return {
        "required_gold_tables": sorted(expected_tables),
        "selected_tables": sorted(selected_tables),
        "required_tables_present": expected_tables.issubset(selected_tables),
        "required_columns_present_where_determinable": (
            qualified_columns_present and unqualified_present
        ),
        "required_relationships_present": relationships_present,
        "required_relationship_count": len(relationships_required),
    }


def _m3_relevance(case_id: str, class_name: str | None) -> str | None:
    if class_name is None:
        return None
    if class_name == "GOLD_OR_EVAL_ISSUE":
        return "LOW"
    if case_id in M3_HIGH_IDS:
        return "HIGH"
    if class_name in M3_MEDIUM_CLASSES:
        return "MEDIUM"
    return "LOW"


def _build_audit_payload(
    run_dir: Path,
    dataset: Path,
    reports: dict[str, BaselineReport],
    analyzed: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    all_rows = [row for rows in analyzed.values() for row in rows]
    mismatches = [row for row in all_rows if not row["equivalence_consistent_with_persisted"]]
    failure_counts = {
        mode: dict(
            Counter(row["primary_failure_class"] for row in rows if row["primary_failure_class"])
        )
        for mode, rows in analyzed.items()
    }
    category_matrix: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for mode, rows in analyzed.items():
        for row in rows:
            if row["primary_failure_class"]:
                category_matrix[row["category"]][mode][row["primary_failure_class"]] += 1
    comparison = _comparison(analyzed)
    comparison_details = _comparison_details(analyzed)
    context_rates = _context_rates(analyzed["retrieved"])
    m3_counts = dict(Counter(row["m3_relevance"] for row in all_rows if row["m3_relevance"]))
    return {
        "audit_metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "run_dir": str(run_dir),
            "dataset": str(dataset),
            "no_llm_calls": True,
            "evaluator_modified": False,
        },
        "evaluator_sanity": {
            "persisted_case_count": {mode: report.total_cases for mode, report in reports.items()},
            "replayed_case_count": {mode: len(rows) for mode, rows in analyzed.items()},
            "persisted_vs_recomputed_mismatches": len(mismatches),
            "persisted_vs_recomputed_mismatch_ids": [row["id"] for row in mismatches],
            "current_evaluator_false_negative_candidates": [
                {"mode": row["mode"], "id": row["id"]}
                for row in all_rows
                if row.get("primary_failure_class") == "GOLD_OR_EVAL_ISSUE"
            ],
            "interpretation": (
                "The current evaluator requires identical result column labels. "
                "Alias-only differences are flagged separately as possible false negatives; "
                "the evaluator was not changed."
            ),
        },
        "summary": {
            "failure_counts_by_mode": failure_counts,
            "category_failure_matrix": category_matrix,
            "full_vs_retrieved": comparison,
            "full_vs_retrieved_details": comparison_details,
            "fixture_equivalent_semantic_warnings": [
                {"mode": row["mode"], "id": row["id"], "category": row["category"]}
                for row in all_rows
                if row["semantic_warning"] == "POSSIBLE_FALSE_POSITIVE_FIXTURE_EQUIVALENCE"
            ],
            "retrieved_context_adequacy": context_rates,
            "m3_relevance_counts": m3_counts,
            "join_grain_failure_count": sum(
                1
                for row in all_rows
                if row["primary_failure_class"]
                in {
                    "WRONG_TABLE",
                    "WRONG_JOIN",
                    "WRONG_AGGREGATION",
                    "WRONG_GROUPING",
                    "JOIN_CARDINALITY_ERROR",
                    "WRONG_GRAIN",
                }
            ),
        },
        "representative_cases": {
            mode: [
                next(row for row in analyzed[mode] if row["id"] == case_id)
                for case_id in REPRESENTATIVE_IDS[mode]
            ]
            for mode in REPRESENTATIVE_IDS
        },
        "cases": analyzed,
    }


def _context_rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [row for row in rows if row["primary_failure_class"]]
    total = len(failures)
    return {
        "retrieved_failure_count": total,
        "required_tables_present_count": sum(
            row["context_adequacy"]["required_tables_present"] for row in failures
        ),
        "required_tables_missing_count": sum(
            not row["context_adequacy"]["required_tables_present"] for row in failures
        ),
        "required_columns_present_count": sum(
            row["context_adequacy"]["required_columns_present_where_determinable"]
            for row in failures
        ),
        "required_columns_missing_count": sum(
            not row["context_adequacy"]["required_columns_present_where_determinable"]
            for row in failures
        ),
        "required_relationships_present_count": sum(
            row["context_adequacy"]["required_relationships_present"] for row in failures
        ),
        "required_relationships_missing_count": sum(
            not row["context_adequacy"]["required_relationships_present"] for row in failures
        ),
        "rates_denominator": total,
    }


def _comparison(analyzed: dict[str, list[dict[str, Any]]]) -> list[dict[str, str | int]]:
    counts: Counter[str] = Counter()
    for item in _comparison_details(analyzed):
        counts[item["classification"]] += 1
    return [{"classification": key, "count": value} for key, value in sorted(counts.items())]


def _comparison_details(analyzed: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    full = {row["id"]: row for row in analyzed["full"]}
    retrieved = {row["id"]: row for row in analyzed["retrieved"]}
    details: list[dict[str, Any]] = []
    for case_id in sorted(full):
        full_row = full[case_id]
        retrieved_row = retrieved[case_id]
        full_correct = full_row["current_equivalence_recomputed"]
        retrieved_correct = retrieved_row["current_equivalence_recomputed"]
        if full_correct and retrieved_correct:
            label = "BOTH_CORRECT"
        elif full_correct:
            label = "FULL_ONLY_CORRECT"
        elif retrieved_correct:
            label = "RETRIEVED_ONLY_CORRECT"
        elif full_row["primary_failure_class"] == retrieved_row["primary_failure_class"]:
            label = "BOTH_INCORRECT_SAME_REASON"
        else:
            label = "BOTH_INCORRECT_DIFFERENT_REASON"
        details.append(
            {
                "id": case_id,
                "category": full_row["category"],
                "classification": label,
                "full_primary_failure_class": full_row["primary_failure_class"],
                "retrieved_primary_failure_class": retrieved_row["primary_failure_class"],
                "full_semantic_warning": full_row["semantic_warning"],
                "retrieved_semantic_warning": retrieved_row["semantic_warning"],
                "full_context_adequacy": full_row["context_adequacy"],
                "retrieved_context_adequacy": retrieved_row["context_adequacy"],
            }
        )
    return details


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    sanity = payload["evaluator_sanity"]
    lines = [
        "# M2 Failure Analysis",
        "",
        (
            "This is a read-only forensic analysis of the persisted M2 run. No SQL was "
            "regenerated, no LLM was called, and the evaluator, prompt, retrieval, model, "
            "policy, and gold SQL were not changed."
        ),
        "",
        "## Evaluator sanity check",
        "",
        (
            f"Replayed cases: full={sanity['replayed_case_count']['full']}, "
            f"retrieved={sanity['replayed_case_count']['retrieved']}. "
            f"Persisted/recomputed verdict mismatches: "
            f"{sanity['persisted_vs_recomputed_mismatches']}."
        ),
        "",
        (
            "The existing evaluator compares result column-name sets exactly. The audit "
            "separately flags cases where values and row shape match positionally but "
            "aliases differ; these are possible evaluator false negatives and are not "
            "silently corrected."
        ),
        "",
        (
            f"Likely alias-only false-negative candidates: "
            f"{len(sanity['current_evaluator_false_negative_candidates'])}. "
            "For example, m2-009 returns the same count value under different output "
            "aliases (total_customers versus customer_count)."
        ),
        (
            f"Fixture-equivalence semantic warnings: "
            f"{len(summary['fixture_equivalent_semantic_warnings'])}. "
            "These are current evaluator positives whose SQL expressions differ in a "
            "way that may only coincide on this seeded dataset."
        ),
        "",
        "## Failure taxonomy",
        "",
        "| Primary class | Full | Retrieved |",
        "|---|---:|---:|",
    ]
    classes = sorted(
        set(summary["failure_counts_by_mode"]["full"])
        | set(summary["failure_counts_by_mode"]["retrieved"])
    )
    for class_name in classes:
        lines.append(
            f"| {class_name} | "
            f"{summary['failure_counts_by_mode']['full'].get(class_name, 0)} | "
            f"{summary['failure_counts_by_mode']['retrieved'].get(class_name, 0)} |"
        )
    lines.extend(
        [
            "",
            "## Full vs retrieved",
            "",
            "| Classification | Count |",
            "|---|---:|",
        ]
    )
    for item in summary["full_vs_retrieved"]:
        lines.append(f"| {item['classification']} | {item['count']} |")
    pair_details = summary["full_vs_retrieved_details"]
    full_only = [
        item["id"]
        for item in pair_details
        if item["classification"] == "FULL_ONLY_CORRECT"
    ]
    retrieved_only = [
        item["id"]
        for item in pair_details
        if item["classification"] == "RETRIEVED_ONLY_CORRECT"
    ]
    lines.extend(
        [
            "",
            (
                "Full-only correct cases: "
                f"{', '.join(full_only) or 'none'}. Retrieved-only correct cases: "
                f"{', '.join(retrieved_only) or 'none'}."
            ),
            (
                "The retrieved context did not omit required gold tables in the full-only "
                "cases. m2-032 is a full-only label caused by the retrieved variant's "
                "alias-only false negative, not a demonstrated context-quality win. The "
                "retrieved-only case, m2-034, is also flagged as a possible fixture-"
                "equivalence false positive because its generated aggregation uses a "
                "different semantic expression."
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Category × failure type",
            "",
            "| Category | Mode | Failure type | Count |",
            "|---|---|---|---:|",
        ]
    )
    for category in sorted(summary["category_failure_matrix"]):
        for mode in ("full", "retrieved"):
            for class_name, count in sorted(
                summary["category_failure_matrix"][category].get(mode, {}).items()
            ):
                lines.append(f"| {category} | {mode} | {class_name} | {count} |")
    context = summary["retrieved_context_adequacy"]
    lines.extend(
        [
            "",
            "## Retrieved context adequacy",
            "",
            (
                f"Among {context['retrieved_failure_count']} retrieved-context failures: "
                f"required tables were present in {context['required_tables_present_count']} "
                f"and missing in {context['required_tables_missing_count']}; required "
                f"columns were present in {context['required_columns_present_count']} and "
                f"missing in {context['required_columns_missing_count']} where determinable; "
                f"required relationships were present in "
                f"{context['required_relationships_present_count']} and missing in "
                f"{context['required_relationships_missing_count']}."
            ),
            "",
            (
                "Join/grain-related primary failures across both generated variants: "
                f"{summary['join_grain_failure_count']}."
            ),
            "",
            "## Join and grain findings",
            "",
            (
                "The nine primary join/grain failures comprise WRONG_TABLE=3, "
                "WRONG_JOIN=2, WRONG_AGGREGATION=2, and WRONG_GROUPING=2. The reviewed "
                "errors include unnecessary or incorrect join paths, line-item versus "
                "order-level aggregation, and grouping at the wrong grain. No separate "
                "JOIN_CARDINALITY_ERROR or WRONG_GRAIN label was needed; those effects "
                "were assigned to the primary structural cause."
            ),
            "",
            "## Date findings",
            "",
            (
                "The date failures are concentrated in boundary and column selection. "
                "Both modes fail m2-031 with a strict boundary where the gold query is "
                "inclusive; retrieved m2-030 also selects a discount-related expression "
                "instead of the requested order revenue. No reviewed failure required a "
                "current-date assumption, quarter interpretation, or DATE_TRUNC diagnosis."
            ),
            "",
            "## Top-k and ordering findings",
            "",
            (
                "WRONG_LIMIT appears five times across both modes, mainly as an omitted "
                "or misplaced top-k limit. The remaining top-k/window ordering cases "
                "include wrong output columns and an outer ordering/window-shape error; "
                "no clear wrong sort-direction case was identified."
            ),
            "",
            "## Ratio findings",
            "",
            (
                "Ratio failures include two WRONG_RATIO_SCALING cases and two "
                "WRONG_RATIO_DENOMINATOR cases. The denominator cases use an average of "
                "per-row ratios or a line-item population instead of the gold population "
                "ratio. One happens to equal the gold value on this fixture, which is why "
                "result execution alone cannot establish metric correctness. Explicit "
                "business metric contracts would likely help these cases; other arithmetic "
                "and SQL-shape failures remain ordinary generation problems."
            ),
            "",
            "## M3 relevance",
            "",
            (
                f"HIGH={summary['m3_relevance_counts'].get('HIGH', 0)}, "
                f"MEDIUM={summary['m3_relevance_counts'].get('MEDIUM', 0)}, "
                f"LOW={summary['m3_relevance_counts'].get('LOW', 0)} across incorrect "
                "generated variants. HIGH is reserved for explicit metric/ratio or "
                "business-definition dependence; ordinary SQL selection, filtering, "
                "ordering, joins, and windows are LOW."
            ),
            "",
            "## Representative failed cases",
            "",
            (
                "The JSON audit contains complete generated/gold SQL and replayed result "
                "rows for these cases. The table below summarizes the manual/structural "
                "assessment."
            ),
            "",
            "| Mode | ID | Category | Current verdict | Primary assessment |",
            "|---|---|---|---|---|",
        ]
    )
    for mode, rows in payload["representative_cases"].items():
        for row in rows:
            lines.append(
                f"| {mode} | {row['id']} | {row['category']} | "
                f"{row['current_equivalence_recomputed']} | "
                f"{row['primary_failure_class']} |"
            )
    lines.extend(
        [
            "",
            "## Conclusions",
            "",
            (
                "The main observed problem is not parsing, authorization, or execution. "
                "It is result semantics: over-selection of columns, unnecessary joins, "
                "incorrect aggregation/grain, omitted LIMIT/order/window logic, and ratio "
                "definitions. The current evaluator also has alias-sensitive false-negative "
                "candidates, so the 16.67%/12.50% baseline should not be interpreted as a "
                "perfect semantic score until that evaluator issue is resolved in a "
                "separately authorized change."
            ),
            "",
            (
                "The evaluator sanity result is therefore mixed: replay is deterministic "
                "and agrees with persisted verdicts, but the current label metric is "
                "alias-sensitive and the finite fixture permits a small number of "
                "structurally different queries to pass. This is a diagnosis blocker for "
                "interpreting the headline score, not a reason to change the evaluator in "
                "this read-only task."
            ),
            "",
            (
                "M3 remains relevant for the high-value metric/ratio subset, but it will "
                "not by itself solve the dominant ordinary SQL-generation failures. No M3 "
                "implementation was started."
            ),
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
