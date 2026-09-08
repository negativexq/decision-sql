"""Extract a human-review package from the persisted M32 fresh DIRECT run.

This is intentionally a read-only forensic tool.  It never constructs a
provider, calls an LLM, changes prompts, or regenerates SQL.  Gold SQL is
written only below the ignored protected results directory; the public fixture
contains stable hashes and protected references.
"""

# The forensic renderer intentionally keeps some long diagnostics readable;
# the machine-readable artifact contains the structured form.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from evaluation.analysis.sql_ast_diff import compare_summaries, summarize_sql
from evaluation.external.livesqlbench.protected import LiveSqlBenchEvaluationCase
from evaluation.run_m32 import _load_cases

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MANIFEST = ROOT / "evaluation/fixtures/m32_direct_fresh_manifest.json"
SOURCE_RESULT = ROOT / "evaluation/fixtures/m32_direct_fresh_result.json"
RAW_ROOT = ROOT / "evaluation/external/livesqlbench/protected/results/m32/direct_fresh"
PROTECTED_OUT = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m33_fresh_direct_failure_review"
)
PUBLIC_JSON = ROOT / "evaluation/fixtures/m33_fresh_direct_failure_review.json"
PUBLIC_DOC = ROOT / "docs/m33_fresh_direct_failure_review.md"
PROTECTED_JSON = PROTECTED_OUT / "m33_fresh_direct_failure_review.json"
PROTECTED_DOC = PROTECTED_OUT / "m33_fresh_direct_failure_review.md"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _json_value(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _table_names(sql: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read="postgres")
    return sorted({table.name.lower() for table in tree.find_all(exp.Table)})


def _root_from(sql: str) -> str | None:
    tree = sqlglot.parse_one(sql, read="postgres")
    from_node = tree.find(exp.From)
    if from_node is None or not from_node.expressions:
        return None
    return str(from_node.expressions[0].sql(dialect="postgres", pretty=False))


def _shape(summary: dict[str, Any]) -> str:
    if summary.get("set_operations"):
        return "set op"
    if summary.get("ctes"):
        return "CTE"
    if summary.get("subqueries"):
        return "nested scalar" if summary.get("subqueries") == 1 else "multi-stage"
    if summary.get("window_functions"):
        return "window"
    if summary.get("aggregations") and summary.get("group_by"):
        return "grouped aggregate"
    if summary.get("aggregations"):
        return "aggregate"
    return "simple"


def _grain_observation(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("aggregations") and summary.get("group_by"):
        grain = {"kind": "grouped", "dimensions": summary["group_by"]}
    elif summary.get("aggregations"):
        grain = {"kind": "scalar", "dimensions": []}
    elif summary.get("window_functions"):
        grain = {"kind": "row_level_with_window", "dimensions": []}
    else:
        grain = {"kind": "row_level", "dimensions": []}
    if any(kind == "LEFT" for kind in summary.get("join_types", [])):
        population = "preserve-anchor-observed-from-left-join"
    elif summary.get("joins"):
        population = "matched-only-observed-from-join"
    else:
        population = "base-from-observed"
    return {"population_observation": population, "grain_observation": grain}


def _context_tables(context: str) -> dict[str, str]:
    lines = context.splitlines()
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = re.match(r"\[Table\] ([^\s]+)", line)
        if match:
            current = match.group(1).lower()
            blocks[current] = [line]
        elif current is not None and line.startswith("[Columns]"):
            blocks[current].append(line)
        elif current is not None and line.startswith("-"):
            blocks[current].append(line)
        elif line.startswith("RELATIONSHIPS"):
            current = None
    return {name: "\n".join(block) for name, block in blocks.items()}


def _context_entity_blocks(context: str) -> dict[str, str]:
    lines = context.splitlines()
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = re.match(r"ENTITY entity:[^:]+: ([^.]*)\.", line)
        if match:
            current = match.group(1).strip().lower()
            blocks[current] = [line]
        elif current is not None and line.startswith("  ATTRIBUTE "):
            blocks[current].append(line)
        elif line.startswith("RELATIONSHIPS"):
            current = None
    return {name: "\n".join(block) for name, block in blocks.items()}


def _context_relationships(context: str, relevant: set[str]) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for line in context.splitlines():
        if not line.startswith("  RELATIONSHIP "):
            continue
        match = re.match(r"  RELATIONSHIP (.+): entity:([^ ]+) -> entity:([^ ]+)", line)
        if match is None:
            continue
        relation_id, left, right = match.groups()
        if left not in relevant or right not in relevant:
            continue
        relationships.append(
            {
                "relationship_id": relation_id,
                "left_entity": left,
                "right_entity": right,
                "raw_server_context": line.strip(),
                "source": "server-owned semantic context / RelationshipGraph",
            }
        )
    return relationships


def _relevant_context(context: str, generated: str, gold: str) -> dict[str, Any]:
    tables = sorted(set(_table_names(generated)) | set(_table_names(gold)))
    blocks = _context_tables(context)
    entity_blocks = _context_entity_blocks(context)
    physical_tables = sorted(table for table in tables if table in blocks)
    relationships = _context_relationships(context, set(physical_tables))
    value_lines = [
        line
        for line in context.splitlines()
        if any(token in line.lower() for token in ("value evidence", "value:", "literal", "sample"))
    ]
    return {
        "query_referenced_tables": tables,
        "relevant_tables": physical_tables,
        "relevant_table_context_exact": {table: blocks[table] for table in physical_tables},
        "relevant_entity_mapping_context_exact": {
            table: entity_blocks.get(table)
            for table in physical_tables
            if entity_blocks.get(table)
        },
        "server_relationships": relationships,
        "value_context_lines": value_lines,
        "full_serialized_schema_context": context,
        "full_context_sha256": _sha256_text(context),
    }


def _comments(sql: str) -> list[str]:
    return [
        line.strip(" /\t")
        for line in sql.splitlines()
        if line.lstrip().startswith("--")
        or line.lstrip().startswith("/*")
        or line.lstrip().startswith("*")
    ]


def _expression_diff(generated: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_select_expressions": generated.get("select_expressions", []),
        "gold_select_expressions": gold.get("select_expressions", []),
        "generated_arithmetic": generated.get("arithmetic", []),
        "gold_arithmetic": gold.get("arithmetic", []),
        "generated_case": generated.get("case", []),
        "gold_case": gold.get("case", []),
        "generated_division": generated.get("division", []),
        "gold_division": gold.get("division", []),
    }


def _filter_diff(generated: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_where": generated.get("where", []),
        "gold_where": gold.get("where", []),
        "generated_having": generated.get("having", []),
        "gold_having": gold.get("having", []),
        "generated_literals": generated.get("literal_values", []),
        "gold_literals": gold.get("literal_values", []),
    }


def _join_table_pairs(sql: str) -> list[dict[str, Any]]:
    tree = sqlglot.parse_one(sql, read="postgres")
    aliases = {
        (table.alias_or_name or table.name).lower(): table.name.lower()
        for table in tree.find_all(exp.Table)
    }
    pairs: list[dict[str, Any]] = []
    for join in tree.find_all(exp.Join):
        right = join.this.name.lower() if isinstance(join.this, exp.Table) else str(join.this)
        on_expression = join.args.get("on")
        referenced_columns = on_expression.find_all(exp.Column) if on_expression else []
        referenced = {
            aliases.get(column.table.lower(), column.table.lower())
            for column in referenced_columns
            if column.table
        }
        for left in sorted(referenced - {right}):
            pairs.append(
                {
                    "left_table": left,
                    "right_table": right,
                    "condition": on_expression.sql(dialect="postgres", pretty=False)
                    if on_expression
                    else None,
                }
            )
    return pairs


def _authorized_pairs(relationships: list[dict[str, Any]]) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for relationship in relationships:
        relation_id = relationship["relationship_id"].removeprefix("relationship:")
        endpoints = relation_id.split("->", 1)
        if len(endpoints) != 2:
            continue
        left = endpoints[0].split(".", 1)[0]
        right = endpoints[1].split(".", 1)[0]
        pairs.add(frozenset((left, right)))
    return pairs


def _join_diff(
    generated_sql: str,
    gold_sql: str,
    generated: dict[str, Any],
    gold: dict[str, Any],
    relationships: list[dict[str, Any]],
    physical_tables: set[str],
) -> dict[str, Any]:
    gold_pairs = _join_table_pairs(gold_sql)
    authorized = _authorized_pairs(relationships)
    gold_only = [
        pair
        for pair in gold_pairs
        if pair["left_table"] in physical_tables and pair["right_table"] in physical_tables
        if frozenset((pair["left_table"], pair["right_table"])) not in authorized
    ]
    return {
        "generated_tables": generated.get("tables", []),
        "gold_tables": gold.get("tables", []),
        "generated_join_count": generated.get("joins", 0),
        "gold_join_count": gold.get("joins", 0),
        "generated_join_types": generated.get("join_types", []),
        "gold_join_types": gold.get("join_types", []),
        "generated_join_conditions": generated.get("join_conditions", []),
        "gold_join_conditions": gold.get("join_conditions", []),
        "generated_join_table_pairs": _join_table_pairs(generated_sql),
        "gold_join_table_pairs": gold_pairs,
        "server_authorized_relationships": relationships,
        "gold_only_not_server_authorized": gold_only,
    }


def _temporal_diff(generated: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    def temporal_expressions(summary: dict[str, Any]) -> list[str]:
        return sorted(
            expression
            for expression in summary.get("select_expressions", [])
            + summary.get("order_by", [])
            + summary.get("where", [])
            if any(
                token in expression
                for token in ("date", "time", "timestamp", "::date", "::timestamp", "->")
            )
        )

    return {
        "generated_date_time_functions": generated.get("date_time_functions", []),
        "gold_date_time_functions": gold.get("date_time_functions", []),
        "generated_temporal_expressions": temporal_expressions(generated),
        "gold_temporal_expressions": temporal_expressions(gold),
    }


def _ordering_limit_diff(generated: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_order_by": generated.get("order_by", []),
        "gold_order_by": gold.get("order_by", []),
        "generated_limit": generated.get("limit"),
        "gold_limit": gold.get("limit"),
    }


def _query_shape_diff(generated: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_shape": _shape(generated),
        "gold_shape": _shape(gold),
        "generated_features": {
            key: generated.get(key)
            for key in ("ctes", "subqueries", "window_functions", "set_operations", "aggregations")
        },
        "gold_features": {
            key: gold.get(key)
            for key in ("ctes", "subqueries", "window_functions", "set_operations", "aggregations")
        },
    }


def _execution_record(
    case: LiveSqlBenchEvaluationCase,
    generated_sql: str,
    safety: Any,
    frozen_row: dict[str, Any],
) -> tuple[dict[str, Any], QueryExecution | None, QueryExecution | None]:
    generated_plan = safety.plan(SqlCandidate(sql=generated_sql))
    reference_plan = safety.plan(SqlCandidate(sql=case.sol_sql[0]))
    result: dict[str, Any] = {
        "frozen_official": frozen_row.get("official"),
        "frozen_primary_failure": frozen_row.get("primary_failure"),
        "generated_m1_status": "ACCEPTED" if isinstance(generated_plan, QueryPlan) else "REJECTED",
        "generated_m1_parse_status": "ACCEPTED" if isinstance(generated_plan, QueryPlan) else None,
        "generated_m1_policy_status": generated_plan.policy_decision.value
        if isinstance(generated_plan, QueryPlan)
        else None,
        "generated_explain_status": "ACCEPTED"
        if isinstance(generated_plan, QueryPlan)
        else "NOT_REACHED",
        "generated_m1_failure_code": None,
        "generated_error": None,
        "gold_reference_plan_status": "ACCEPTED"
        if isinstance(reference_plan, QueryPlan)
        else "LIMITATION",
        "gold_reference_error": None,
    }
    generated_execution: QueryExecution | None = None
    reference_execution: QueryExecution | None = None
    if isinstance(generated_plan, SqlPlanFailure):
        result["generated_m1_failure_code"] = (
            generated_plan.rejection.code.value
            if generated_plan.rejection is not None
            else generated_plan.status.value
        )
        result["generated_error"] = generated_plan.error
    elif isinstance(generated_plan, QueryPlan):
        execution = safety.execute(generated_plan)
        if isinstance(execution, QueryExecution):
            generated_execution = execution
            result["generated_execution_status"] = "SUCCESS"
        else:
            result["generated_execution_status"] = "ERROR"
            result["generated_error"] = getattr(execution, "error", str(execution))
    if isinstance(reference_plan, SqlPlanFailure):
        result["gold_reference_error"] = reference_plan.error
    elif isinstance(reference_plan, QueryPlan):
        execution = safety.execute(reference_plan)
        if isinstance(execution, QueryExecution):
            reference_execution = execution
            result["gold_execution_status"] = "SUCCESS"
        else:
            result["gold_execution_status"] = "ERROR"
            result["gold_reference_error"] = getattr(execution, "error", str(execution))
    result["generated_row_count"] = generated_execution.row_count if generated_execution else None
    result["gold_row_count"] = reference_execution.row_count if reference_execution else None
    result["generated_result_sample"] = generated_execution.rows[:10] if generated_execution else []
    result["gold_result_sample"] = reference_execution.rows[:10] if reference_execution else []
    result["generated_columns"] = generated_execution.columns if generated_execution else []
    result["gold_columns"] = reference_execution.columns if reference_execution else []
    if generated_execution and reference_execution:

        def row_key(row: dict[str, Any], columns: list[str]) -> str:
            return json.dumps(
                [_json_value(row.get(column)) for column in columns], sort_keys=True, default=str
            )

        generated_keys = Counter(
            row_key(row, generated_execution.columns) for row in generated_execution.rows
        )
        gold_keys = Counter(
            row_key(row, reference_execution.columns) for row in reference_execution.rows
        )
        result["same_columns"] = generated_execution.columns == reference_execution.columns
        result["same_row_set"] = generated_keys == gold_keys
        result["empty_vs_non_empty"] = (
            generated_execution.row_count == 0,
            reference_execution.row_count == 0,
        )
    else:
        result["same_columns"] = None
        result["same_row_set"] = None
        result["empty_vs_non_empty"] = None
    return result, generated_execution, reference_execution


def _case_record(
    case: LiveSqlBenchEvaluationCase,
    frozen_row: dict[str, Any],
    capture: dict[str, Any],
    context: str,
    safety: Any,
) -> dict[str, Any]:
    generated_sql = str(capture.get("parsed_sql") or "")
    gold_sql = case.sol_sql[0]
    generated_summary = summarize_sql(generated_sql)
    gold_summary = summarize_sql(gold_sql)
    exact_provider_context = str(capture.get("serialized_schema_context") or context)
    relevant = _relevant_context(exact_provider_context, generated_sql, gold_sql)
    execution, _, _ = _execution_record(case, generated_sql, safety, frozen_row)
    generated_grain = _grain_observation(generated_summary)
    gold_grain = _grain_observation(gold_summary)
    messages = capture.get("messages", [])
    system_message = next(
        (message.get("content", "") for message in messages if message.get("role") == "system"), ""
    )
    user_message = next(
        (message.get("content", "") for message in messages if message.get("role") == "user"), ""
    )
    return {
        "case_id": case.instance_id,
        "database_id": case.database,
        "question": case.runtime.question,
        "generated_sql": generated_sql,
        "raw_generated_sql": generated_sql,
        "gold_sql": gold_sql,
        "generated_sql_sha256": _sha256_text(generated_sql),
        "gold_sql_sha256": _sha256_text(gold_sql),
        "provider_response_hash": capture.get("raw_assistant_content_sha256"),
        "execution": execution,
        "generated_result_sample": execution["generated_result_sample"],
        "gold_result_sample": execution["gold_result_sample"],
        "provider_context": {
            "system_prompt_hash": _sha256_text(system_message),
            "user_prompt_hash": _sha256_text(user_message),
            "user_prompt_exact": user_message,
            "serialized_schema_context_bytes": len(exact_provider_context.encode("utf-8")),
            "serialized_schema_context_sha256": _sha256_text(exact_provider_context),
            "input_tokens": (capture.get("usage") or {}).get("prompt_tokens"),
            "output_tokens": (capture.get("usage") or {}).get("completion_tokens"),
            "model": capture.get("request_config", {}).get("model"),
            "reasoning": capture.get("request_config", {}).get("reasoning_effort"),
            "temperature": capture.get("request_config", {}).get("temperature"),
            "timeout_seconds": capture.get("request_config", {}).get("timeout_seconds"),
            "full_messages_exact": messages,
            "serialized_schema_context_exact": capture.get("serialized_schema_context"),
        },
        "server_relationships": relevant["server_relationships"],
        "server_semantic_facts": [
            {"kind": "relevant_schema_context", "table": table, "text": text}
            for table, text in relevant["relevant_table_context_exact"].items()
            if text is not None
        ]
        + [
            {"kind": "semantic_mapping_context", "entity": table, "text": text}
            for table, text in relevant["relevant_entity_mapping_context_exact"].items()
        ]
        + ([{"kind": "relationship_context", **item} for item in relevant["server_relationships"]]),
        "gold_only_facts": _comments(gold_sql),
        "provider_context_relevant": {
            "tables_exposed": relevant["relevant_tables"],
            "relevant_table_context_exact": relevant["relevant_table_context_exact"],
            "relevant_entity_mapping_context_exact": relevant[
                "relevant_entity_mapping_context_exact"
            ],
            "value_context_lines": relevant["value_context_lines"],
            "full_context_sha256": relevant["full_context_sha256"],
        },
        "ast_diff": {
            "generated": generated_summary,
            "gold": gold_summary,
            "difference": compare_summaries(gold_summary, generated_summary),
        },
        "formula_diff": _expression_diff(generated_summary, gold_summary),
        "filter_diff": _filter_diff(generated_summary, gold_summary),
        "join_diff": _join_diff(
            generated_sql,
            gold_sql,
            generated_summary,
            gold_summary,
            relevant["server_relationships"],
            set(relevant["relevant_table_context_exact"]),
        ),
        "population_grain_diff": {
            "generated_base_from": _root_from(generated_sql),
            "gold_base_from": _root_from(gold_sql),
            "generated": generated_grain,
            "gold": gold_grain,
        },
        "temporal_diff": _temporal_diff(generated_summary, gold_summary),
        "ordering_limit_diff": _ordering_limit_diff(generated_summary, gold_summary),
        "query_shape_diff": _query_shape_diff(generated_summary, gold_summary),
    }


def _md_sql(sql: str) -> str:
    return f"```sql\n{sql.rstrip()}\n```"


def _md_list(values: Any) -> str:
    if not values:
        return "(none)"
    return "\n".join(
        f"- `{value}`"
        if isinstance(value, str)
        else f"- `{json.dumps(value, sort_keys=True, default=str)}`"
        for value in values
    )


def _human_case(record: dict[str, Any]) -> str:
    execution = record["execution"]
    ast = record["ast_diff"]
    structural = ast["difference"].get("differences", {})
    return "\n".join(
        [
            f"# {record['case_id']}",
            "",
            f"Database: `{record['database_id']}`",
            f"Question: {record['question']}",
            "",
            "## Generated SQL",
            "",
            _md_sql(record["raw_generated_sql"]),
            "",
            "## Gold SQL",
            "",
            _md_sql(record["gold_sql"]),
            "",
            "## Execution",
            "",
            f"Generated: `{execution.get('generated_execution_status')}`; M1 `{execution.get('generated_m1_status')}`; policy `{execution.get('generated_m1_policy_status')}`; EXPLAIN `{execution.get('generated_explain_status')}`; rows `{execution.get('generated_row_count')}`.",
            f"Gold/reference: `{execution.get('gold_execution_status')}`; rows `{execution.get('gold_row_count')}`.",
            f"Official: `{execution.get('frozen_official')}`; primary failure `{execution.get('frozen_primary_failure')}`.",
            f"Error: `{execution.get('generated_error')}`"
            if execution.get("generated_error")
            else "Error: (none)",
            "",
            "## Relevant server context",
            "",
            f"Tables: `{', '.join(record['provider_context_relevant']['tables_exposed'])}`",
            "",
            "Server-owned schema facts:",
            _md_list(record["server_semantic_facts"]),
            "",
            "Server-known relationships:",
            _md_list(record["server_relationships"]),
            "",
            f"Exact full context is preserved in the protected machine artifact; context SHA-256: `{record['provider_context_relevant']['full_context_sha256']}`.",
            "",
            "## Structural differences",
            "",
            f"Projection/formulas: generated `{len(ast['generated'].get('select_expressions', []))}` expressions; gold `{len(ast['gold'].get('select_expressions', []))}`.",
            f"Joins: generated `{ast['generated'].get('join_conditions', [])}`; gold `{ast['gold'].get('join_conditions', [])}`.",
            f"Filters: generated `{record['filter_diff']['generated_where']}` / `{record['filter_diff']['generated_having']}`; gold `{record['filter_diff']['gold_where']}` / `{record['filter_diff']['gold_having']}`.",
            f"Aggregation: generated `{ast['generated'].get('aggregations', [])}`; gold `{ast['gold'].get('aggregations', [])}`.",
            f"Calculation: generated `{record['formula_diff']['generated_arithmetic']}`; gold `{record['formula_diff']['gold_arithmetic']}`.",
            f"Grouping/grain: generated `{record['population_grain_diff']['generated']}`; gold `{record['population_grain_diff']['gold']}`.",
            f"Temporal: generated `{record['temporal_diff']['generated_date_time_functions']}`; gold `{record['temporal_diff']['gold_date_time_functions']}`.",
            f"Ordering/limit: generated `{record['ordering_limit_diff']['generated_order_by']}`, `{record['ordering_limit_diff']['generated_limit']}`; gold `{record['ordering_limit_diff']['gold_order_by']}`, `{record['ordering_limit_diff']['gold_limit']}`.",
            f"Query shape: generated `{record['query_shape_diff']['generated_shape']}`; gold `{record['query_shape_diff']['gold_shape']}`.",
            f"AST field differences: `{json.dumps(structural, sort_keys=True, default=str)}`",
            "",
            "## Result sample",
            "",
            "Generated:",
            "",
            "```json",
            json.dumps(record["generated_result_sample"], indent=2, default=str),
            "```",
            "",
            "Expected:",
            "",
            "```json",
            json.dumps(record["gold_result_sample"], indent=2, default=str),
            "```",
            "",
            f"Same columns: `{execution.get('same_columns')}`; same row set: `{execution.get('same_row_set')}`; empty/non-empty: `{execution.get('empty_vs_non_empty')}`.",
            "",
            f"Provider response hash: `{record['provider_response_hash']}`.",
            "",
        ]
    )


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": record["case_id"],
        "database_id": record["database_id"],
        "question": record["question"],
        "generated_sql": record["generated_sql"],
        "gold_sql": None,
        "gold_sql_protected_ref": str(PROTECTED_JSON),
        "gold_sql_sha256": record["gold_sql_sha256"],
        "provider_response_hash": record["provider_response_hash"],
        "execution": record["execution"],
        "ast_diff": record["ast_diff"]["difference"],
        "formula_diff": record["formula_diff"],
        "filter_diff": record["filter_diff"],
        "join_diff": record["join_diff"],
        "population_grain_diff": record["population_grain_diff"],
        "temporal_diff": record["temporal_diff"],
        "ordering_limit_diff": record["ordering_limit_diff"],
        "query_shape_diff": record["query_shape_diff"],
        "protected_full_record_ref": str(PROTECTED_JSON),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-failures", action="store_true")
    args = parser.parse_args()
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    source_result = json.loads(SOURCE_RESULT.read_text(encoding="utf-8"))
    cases, contexts, _mappings, states, _oracle = _load_cases()
    case_by_id = {case.instance_id: case for case in cases}
    frozen_rows = {row["case_id"]: row for row in source_result["rows"]}
    failed_ids = [
        case_id
        for case_id in source_manifest["context_hashes"]
        if frozen_rows[case_id].get("official") != "CORRECT"
    ]
    if len(cases) != 18 or len(failed_ids) != 10 or source_result.get("official_correct") != 8:
        raise RuntimeError("fresh DIRECT source run does not match the expected 18/8/10 evidence")

    records: list[dict[str, Any]] = []
    for case_id in failed_ids:
        capture_path = RAW_ROOT / case_id / "direct.json"
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        case = case_by_id[case_id]
        record = _case_record(
            case, frozen_rows[case_id], capture, contexts[case_id], states[case.database][2]
        )
        records.append(record)

    source_run = {
        "experiment_id": "M32-DIRECT-FRESH",
        "manifest_path": str(SOURCE_MANIFEST),
        "result_path": str(SOURCE_RESULT),
        "manifest_sha256": _sha256_text(SOURCE_MANIFEST.read_text(encoding="utf-8")),
        "result_sha256": _sha256_text(SOURCE_RESULT.read_text(encoding="utf-8")),
        "model": source_manifest["model"],
        "provider": source_manifest["provider"],
        "reasoning": source_manifest["reasoning"],
        "temperature": source_manifest["temperature"],
        "timeout_seconds": source_manifest["timeout_seconds"],
        "generation_prompt_hash": source_manifest["generation_prompt_hash"],
        "case_order_hash": source_manifest["case_order_hash"],
        "context_hashes": source_manifest["context_hashes"],
        "db_image_digest": source_manifest["db_image_digest"],
        "cases": 18,
        "official_correct": 8,
        "non_correct": 10,
        "new_provider_calls": 0,
        "raw_artifact_root": str(RAW_ROOT),
    }
    protected_result = {
        "source_run": source_run,
        "summary": {
            "correct": 8,
            "non_correct": 10,
            "failed_case_ids": failed_ids,
            "gold_sql_policy": "protected-local-only",
            "deterministic_local_execution": True,
        },
        "failures": records,
    }
    public_result = {
        "source_run": {key: value for key, value in source_run.items() if key != "context_hashes"},
        "summary": {
            "correct": 8,
            "non_correct": 10,
            "failed_case_ids": failed_ids,
            "protected_full_artifact": str(PROTECTED_JSON),
            "gold_sql_in_public_artifact": False,
        },
        "failures": [_public_record(record) for record in records],
    }
    PROTECTED_OUT.mkdir(parents=True, exist_ok=True)
    PROTECTED_JSON.write_text(
        json.dumps(protected_result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    PUBLIC_JSON.write_text(
        json.dumps(public_result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    protected_doc = (
        "# M33 Fresh DIRECT Failure Review\n\nFresh DIRECT: **8/18 correct; 10/18 non-correct**.\n\n"
        + "\n".join(_human_case(record) for record in records)
    )
    PROTECTED_DOC.write_text(protected_doc, encoding="utf-8")
    public_doc = (
        "# M33 Fresh DIRECT Failure Review\n\n"
        "Fresh DIRECT: **8/18 correct; 10/18 non-correct**.\n\n"
        "The complete case-by-case review, including protected gold SQL, is in:\n\n"
        f"`{PROTECTED_DOC}`\n\n"
        f"Machine-readable public index: `{PUBLIC_JSON}`\n\n"
        "The protected artifact contains exact generated SQL, gold SQL, frozen provider context, execution samples, and deterministic SQLGlot diffs for all ten failures."
    )
    PUBLIC_DOC.write_text(public_doc + "\n", encoding="utf-8")

    if args.print_failures:
        for record in records:
            print(
                f"\nCASE {record['case_id']} ({record['database_id']})\nQUESTION: {record['question']}\n"
            )
            print("GENERATED:")
            print(record["raw_generated_sql"])
            print("\nGOLD:")
            print(record["gold_sql"])
            print("\nKEY STRUCTURAL DIFFERENCES:")
            print(
                json.dumps(record["ast_diff"]["difference"], indent=2, sort_keys=True, default=str)
            )
    print(
        json.dumps(
            {
                "source": "M32-DIRECT-FRESH",
                "correct": 8,
                "non_correct": 10,
                "new_provider_calls": 0,
                "failed_case_ids": failed_ids,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
