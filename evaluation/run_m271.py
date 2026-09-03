"""Create the offline M2.7.1 failure-mechanism audit."""

# This module contains generated Markdown templates with intentionally long lines.
# ruff: noqa: E501

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.m27_failure_mechanism import (
    PRIMARY_CAUSES,
    build_audit,
    load_persisted_models,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline M2.7.1 audit")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("evaluation/results/m27/dev/20260903T023000Z/recomputed-v2"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/m27/failure_mechanism_audit.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/m27-failure-mechanism-audit.md"),
    )
    args = parser.parse_args()
    models = load_persisted_models(args.source)
    audit = build_audit(models)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(_render_report(audit), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "report": str(args.report), "provider_calls": 0}))


def _render_report(audit: dict[str, Any]) -> str:
    lines = [
        "# M2.7.1 Failure Mechanism Audit",
        "",
        "Offline forensic analysis of the persisted M2.7 development run.",
        "No provider, database, SQL regeneration, or holdout access occurred.",
        "",
        f"- Source: `{audit['source_run']}`",
        f"- Development dataset: `{audit['source_dataset']}`",
        f"- Audited questions: {audit['audit_question_count']} ({audit['audit_case_count']} model cases)",
        "- Provider calls: 0",
        "- Result rows: not present in the persisted M2.7 artifacts; row counts are reported where available.",
        "",
        "## Classifier audit",
        "",
        "The first-pass classifier is not trustworthy as a primary root-cause classifier.",
        "It treated any raw ORDER BY difference as an order/limit failure, paired arbitrary missing/extra columns, and treated qualifier-only window differences as semantic window errors.",
        "The corrected audit normalizes aliases and qualifiers, checks output projection shape separately, and applies root-cause precedence.",
        "",
        "## Corrected primary root causes",
        "",
        "Counts include policy-rejected cases and exclude result-equivalent fixture warnings. Each model denominator is 33 non-equivalent or rejected cases.",
        "",
        "| Root cause | Luna | Terra |",
        "|---|---:|---:|",
    ]
    for cause in PRIMARY_CAUSES:
        lines.append(
            f"| {cause.value} | {audit['root_cause_counts']['luna'][cause.value]} | {audit['root_cause_counts']['terra'][cause.value]} |"
        )
    lines.extend(
        [
            "",
            f"Fixture/evaluator warnings retained separately: Luna {audit['artifact_warning_counts']['luna']}, Terra {audit['artifact_warning_counts']['terra']}.",
            "",
            "## Why simple filters are 0/8",
            "",
            "The zero is not caused by a result-equivalence alias bug. The corrected evaluator compares equal-arity ordinal columns; the generated queries frequently project the whole/expanded table instead of the requested projection.",
            "",
            "- Luna: 6 projection/object-selection failures and 2 M1 policy rejections caused by unsupported `LOWER()`.",
            "- Terra: 7 projection/object-selection failures and 1 M1 policy rejection caused by unsupported `LOWER()`.",
            "- The filter predicates themselves are usually structurally correct after qualifier and date-cast normalization.",
            "- The benchmark’s explicit projection contract makes extra columns a genuine result mismatch, not a harmless alias difference.",
            "",
            "## Top-k failure matrix",
            "",
        ]
    )
    _append_matrix(lines, audit["top_k_failure_matrix"], "top_k")
    lines.extend(["", "## Window failure matrix", ""])
    _append_matrix(lines, audit["window_failure_matrix"], "window_functions")
    lines.extend(
        [
            "",
            "## Join analysis",
            "",
            f"Primary JOIN_COMPOSITION_ERROR: Luna {audit['join_audit']['primary_join_composition_error']['luna']}, Terra {audit['join_audit']['primary_join_composition_error']['terra']}.",
            f"Rows with a normalized join-edge difference: Luna {audit['join_audit']['rows_with_join_edge_difference']['luna']}, Terra {audit['join_audit']['rows_with_join_edge_difference']['terra']}.",
            f"Rows with a join-type difference (for example INNER vs LEFT): Luna {audit['join_audit']['rows_with_join_type_difference']['luna']}, Terra {audit['join_audit']['rows_with_join_type_difference']['terra']}.",
            "Join differences are secondary in the audited failures when projection, aggregation, or top-k errors occur earlier. The detailed join rows are persisted in the JSON artifact.",
            "",
            "## Context adequacy",
            "",
            "| Model | Non-equivalent/rejected | Tables visible | Columns visible | Relationships visible |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for model in ("luna", "terra"):
        item = audit["context_audit"][model]
        lines.append(
            f"| {model} | {item['incorrect_or_rejected']} | {item['gold_tables_visible']} | {item['gold_columns_visible']} | {item['gold_relationships_visible']} |"
        )
    lines.extend(
        [
            "",
            "No structural-context-insufficient case was found. The gold table, column, and relationship visibility rate remains 100%.",
            "",
            "## Column confusion",
            "",
            "High-confidence substitutions require ordinal projection alignment with one referenced column on each side. Arbitrary set differences are retained as unmatched columns.",
            "",
            "### High-confidence substitutions",
            "",
        ]
    )
    for model in ("luna", "terra"):
        lines.append(
            f"- {model}: `{json.dumps(audit['column_confusion']['high_confidence_substitutions'][model], sort_keys=True)}`"
        )
    lines.extend(["", "### Unmatched missing/extra columns", ""])
    for model in ("luna", "terra"):
        lines.append(
            f"- {model}: `{json.dumps(audit['column_confusion']['unmatched_missing_or_extra'][model], sort_keys=True)}`"
        )
    pairwise = audit["model_pairwise"]
    lines.extend(
        [
            "",
            "## Model-capacity comparison",
            "",
            f"Pairwise outcomes: `{json.dumps(pairwise['counts'], sort_keys=True)}`.",
            f"Among BOTH_INCORRECT cases, same root cause: {pairwise['both_incorrect_same_root_cause']}; different root cause: {pairwise['both_incorrect_different_root_cause']}.",
            "",
            "The Luna-only correct case is `m2-015` (Luna produced the unfiltered payment total; Terra added a status predicate). The Terra-only correct case is `m2-030` (Terra used the February date range without Luna’s extra cancellation predicate).",
            "",
            "## Recommended M2.8 experiment",
            "",
            "Recommend **SQL_SHAPE_DECOMPOSITION** as the single next experiment.",
            "",
            "The required schema was visible, model capacity was neutral, and the dominant remaining mechanisms are projection selection, filter/aggregation composition, ordering/LIMIT, and query shape. A narrow decomposition experiment is therefore better justified than richer schema context, another model ablation, candidate voting, or a semantic contract. No decomposition is implemented here.",
            "",
            "## Detailed stratified audit",
            "",
            "The following cases cover all eight simple-filter questions, all four top-k questions, all three window questions, six join/multi-table cases, two ratio cases, aggregation/grouping, and both model-exclusive correct cases. The machine-readable artifact contains the complete case-by-case fields, including model-visible schema excerpts and structural parts.",
        ]
    )
    for case_id in audit["audit_case_ids"]:
        lines.extend(_render_case(audit, case_id))
    return "\n".join(lines) + "\n"


def _append_matrix(
    lines: list[str], matrix: dict[str, list[dict[str, Any]]], category: str
) -> None:
    if category == "top_k":
        lines.extend(
            [
                "| Model | Case | Measure/agg | Grouping entity | Order | Direction | Limit | Projection | Root |",
                "|---|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for model in ("luna", "terra"):
            for item in matrix[model]:
                lines.append(
                    f"| {model} | {item['id']} | {item['measure_or_aggregation_correct']} | {item['grouping_entity_correct']} | {item['ordering_correct']} | {item['direction_correct']} | {item['limit_correct']} | {item['projection_correct']} | {item['primary_root_cause']} |"
                )
    else:
        lines.extend(
            [
                "| Model | Case | Window recognized | Function | Partition | Window order | Projection | Root |",
                "|---|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for model in ("luna", "terra"):
            for item in matrix[model]:
                lines.append(
                    f"| {model} | {item['id']} | {item['window_recognized']} | {item['window_function_correct']} | {item['partition_correct']} | {item['window_order_correct']} | {item['projection_correct']} | {item['primary_root_cause']} |"
                )


def _render_case(audit: dict[str, Any], case_id: str) -> list[str]:
    rows = {
        model: next(
            row
            for row in audit["audit_cases"]
            if row["id"] == case_id and row["model"].endswith(model)
        )
        for model in ("luna", "terra")
    }
    left = rows["luna"]
    right = rows["terra"]
    lines = [
        "",
        f"### {case_id} — {left['category']}",
        "",
        f"Question: {left['question']}",
        "",
        "Model-visible schema excerpt:",
        "",
        "```text",
        left["model_visible_schema_excerpt"] or "unavailable",
        "```",
        "",
        f"Gold SQL: `{left['gold_sql']}`",
        f"Gold result: unavailable in source artifacts; generated row counts: Luna `{left['generated_result']['row_count']}`, Terra `{right['generated_result']['row_count']}`.",
        "",
        "| Field | Gold | Luna | Terra |",
        "|---|---|---|---|",
        f"| Tables | `{_compact(left['gold_structural_parts']['tables'])}` | `{_compact(left['generated_structural_parts']['tables'] if left['generated_structural_parts'] else [])}` | `{_compact(right['generated_structural_parts']['tables'] if right['generated_structural_parts'] else [])}` |",
        f"| Columns | `{_compact(left['gold_structural_parts']['columns'])}` | `{_compact(left['generated_structural_parts']['columns'] if left['generated_structural_parts'] else [])}` | `{_compact(right['generated_structural_parts']['columns'] if right['generated_structural_parts'] else [])}` |",
        f"| Joins | `{_compact(left['gold_structural_parts']['joins'])}` | `{_compact(left['generated_structural_parts']['joins'] if left['generated_structural_parts'] else [])}` | `{_compact(right['generated_structural_parts']['joins'] if right['generated_structural_parts'] else [])}` |",
        f"| Filters | `{_compact(left['gold_structural_parts']['filters'])}` | `{_compact(left['generated_structural_parts']['filters'] if left['generated_structural_parts'] else [])}` | `{_compact(right['generated_structural_parts']['filters'] if right['generated_structural_parts'] else [])}` |",
        f"| Aggregation/grouping | `{_compact((left['gold_structural_parts']['aggregations'], left['gold_structural_parts']['group_by']))}` | `{_compact((left['generated_structural_parts']['aggregations'], left['generated_structural_parts']['group_by']) if left['generated_structural_parts'] else [])}` | `{_compact((right['generated_structural_parts']['aggregations'], right['generated_structural_parts']['group_by']) if right['generated_structural_parts'] else [])}` |",
        f"| Order/limit | `{_compact((left['gold_structural_parts']['order_by'], left['gold_structural_parts']['limit']))}` | `{_compact((left['generated_structural_parts']['order_by'], left['generated_structural_parts']['limit']) if left['generated_structural_parts'] else [])}` | `{_compact((right['generated_structural_parts']['order_by'], right['generated_structural_parts']['limit']) if right['generated_structural_parts'] else [])}` |",
        f"| Window | `{_compact(left['gold_structural_parts']['windows'])}` | `{_compact(left['generated_structural_parts']['windows'] if left['generated_structural_parts'] else [])}` | `{_compact(right['generated_structural_parts']['windows'] if right['generated_structural_parts'] else [])}` |",
        "",
        f"Luna SQL: `{left['generated_sql'] or 'unavailable'}`",
        f"Terra SQL: `{right['generated_sql'] or 'unavailable'}`",
        f"Result-equivalence: Luna `{left['result_equivalent']}`, Terra `{right['result_equivalent']}`",
        f"Primary root cause: Luna `{left['primary_root_cause']}`, Terra `{right['primary_root_cause']}`",
        f"Secondary tags: Luna `{left['secondary_tags']}`, Terra `{right['secondary_tags']}`",
    ]
    return lines


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    main()
