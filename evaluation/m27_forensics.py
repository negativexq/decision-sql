"""Offline structural signatures and diagnostics for the M2.7 model comparison."""

import hashlib
import json
from collections import Counter
from typing import Any

from sqlglot import exp, parse_one

from app.catalog.models import SchemaContext
from app.sql.models import QueryPlan

SIGNATURE_FIELDS = (
    "tables",
    "columns",
    "joins",
    "filters",
    "aggregations",
    "group_by",
    "order_by",
    "limit",
    "windows",
    "window_functions",
    "window_partitions",
    "window_orders",
)


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sql_signature(sql: str) -> dict[str, Any]:
    expression = parse_one(sql, dialect="postgres")
    aliases = {
        table.alias_or_name.lower(): table.name.lower()
        for table in expression.find_all(exp.Table)
    }
    cte_names = {
        cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name
    }
    tables = {
        table.name.lower()
        for table in expression.find_all(exp.Table)
        if table.name.lower() not in cte_names
    }
    output_aliases = {
        alias.alias_or_name.lower()
        for alias in expression.find_all(exp.Alias)
        if alias.alias_or_name
    }
    columns = {
        _column_key(column, aliases, tables)
        for column in expression.find_all(exp.Column)
        if column.name != "*"
        and not (not column.table and column.name.lower() in output_aliases)
    }
    windows = tuple(
        sorted(
            window.sql(dialect="postgres")
            for window in expression.find_all(exp.Window)
        )
    )
    window_functions = tuple(
        sorted(
            window.this.sql(dialect="postgres")
            for window in expression.find_all(exp.Window)
            if window.this is not None
        )
    )
    window_partitions = tuple(
        sorted(
            _window_partition(window)
            for window in expression.find_all(exp.Window)
            if _window_partition(window)
        )
    )
    window_orders = tuple(
        sorted(
            _window_order(window)
            for window in expression.find_all(exp.Window)
            if _window_order(window)
        )
    )
    return {
        "tables": tuple(sorted(tables)),
        "columns": tuple(sorted(columns)),
        "joins": tuple(sorted(_join_key(join, aliases) for join in expression.find_all(exp.Join))),
        "filters": tuple(sorted(_where_values(expression))),
        "aggregations": tuple(
            sorted(item.sql(dialect="postgres") for item in expression.find_all(exp.AggFunc))
        ),
        "group_by": tuple(sorted(_group_by_values(expression))),
        "order_by": tuple(
            item.sql(dialect="postgres") for item in expression.find_all(exp.Ordered)
        ),
        "limit": _limit_value(expression),
        "windows": windows,
        "window_functions": window_functions,
        "window_partitions": window_partitions,
        "window_orders": window_orders,
    }


def plan_signature(plan: QueryPlan) -> dict[str, Any]:
    return sql_signature(plan.normalized_sql)


def structural_diff(gold: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    _set_diff(diff, "TABLE", gold["tables"], generated["tables"])
    _set_diff(diff, "COLUMN", gold["columns"], generated["columns"])
    _set_diff(diff, "JOIN", gold["joins"], generated["joins"])
    _set_diff(diff, "FILTER", gold["filters"], generated["filters"])
    _set_diff(diff, "AGGREGATION", gold["aggregations"], generated["aggregations"])
    _set_diff(diff, "GROUP_BY", gold["group_by"], generated["group_by"])
    if gold["order_by"] != generated["order_by"]:
        diff["ORDER_BY_MISSING"] = _missing(gold["order_by"], generated["order_by"])
        diff["ORDER_BY_EXTRA"] = _missing(generated["order_by"], gold["order_by"])
        gold_directions = _order_directions(gold["order_by"])
        generated_directions = _order_directions(generated["order_by"])
        if gold_directions != generated_directions:
            diff["ORDER_DIRECTION_DIFFERENT"] = {
                "gold": gold_directions,
                "generated": generated_directions,
            }
    if gold["limit"] != generated["limit"]:
        diff["LIMIT_MISSING"] = gold["limit"] if generated["limit"] is None else None
        diff["LIMIT_DIFFERENT"] = {
            "gold": gold["limit"],
            "generated": generated["limit"],
        }
    _set_diff(diff, "WINDOW", gold["windows"], generated["windows"])
    if gold["window_functions"] != generated["window_functions"]:
        diff["WINDOW_FUNCTION_DIFFERENT"] = {
            "gold": gold["window_functions"],
            "generated": generated["window_functions"],
        }
    if gold["window_partitions"] != generated["window_partitions"]:
        diff["WINDOW_PARTITION_DIFFERENT"] = {
            "gold": gold["window_partitions"],
            "generated": generated["window_partitions"],
        }
    if gold["window_orders"] != generated["window_orders"]:
        diff["WINDOW_ORDER_DIFFERENT"] = {
            "gold": gold["window_orders"],
            "generated": generated["window_orders"],
        }
    if "COLUMN_MISSING" in diff and "COLUMN_EXTRA" in diff:
        diff["COLUMN_SUBSTITUTED"] = True
    if "JOIN_MISSING" in diff and "JOIN_EXTRA" in diff:
        diff["JOIN_PATH_DIFFERENT"] = True
    return diff


def context_visibility(signature: dict[str, Any], context: SchemaContext) -> dict[str, Any]:
    visible_tables = {table.name.lower() for table in context.tables}
    visible_columns = {
        f"{table.name.lower()}.{column.name.lower()}"
        for table in context.tables
        for column in table.columns
    }
    visible_relationships = {
        _relationship_key(
            f"{item.source_table.lower()}.{item.source_column.lower()}",
            f"{item.target_table.lower()}.{item.target_column.lower()}",
        )
        for item in context.relationships
    }
    required_joins = set(signature["joins"])
    missing_columns = sorted(
        column
        for column in signature["columns"]
        if not _column_visible(column, visible_columns)
    )
    return {
        "gold_tables_visible": set(signature["tables"]).issubset(visible_tables),
        "gold_columns_visible": not missing_columns,
        "gold_relationships_visible": required_joins.issubset(visible_relationships),
        "missing_tables": sorted(set(signature["tables"]) - visible_tables),
        "missing_columns": missing_columns,
        "missing_relationships": sorted(required_joins - visible_relationships),
    }


def classify_forensic_cause(
    *,
    generated_sql: str | None,
    failure_stage: str | None,
    gold_signature: dict[str, Any],
    generated_signature: dict[str, Any] | None,
    diff: dict[str, Any],
    visibility: dict[str, Any] | None,
    equivalent: bool | None,
) -> str | None:
    if generated_sql is None:
        return "PROVIDER_FAILURE"
    if failure_stage == "POLICY_REJECTION":
        return "POLICY_REJECTION"
    if generated_signature is None:
        return "SQL_COMPOSITION_ERROR"
    if equivalent is True and diff:
        return "EVALUATOR_OR_FIXTURE_WARNING"
    if visibility and (
        not visibility["gold_tables_visible"]
        or not visibility["gold_columns_visible"]
        or not visibility["gold_relationships_visible"]
    ):
        return "INPUT_SCHEMA_INSUFFICIENT"
    if any(key.startswith("TABLE_") for key in diff):
        return "SCHEMA_SELECTION_ERROR"
    if any(key.startswith("JOIN_") for key in diff):
        return "JOIN_REASONING_ERROR"
    if any(key.startswith("WINDOW_") for key in diff):
        return "WINDOW_REASONING_ERROR"
    if any(key.startswith("ORDER_") or key.startswith("LIMIT_") for key in diff):
        return "ORDER_LIMIT_REASONING_ERROR"
    if any(key.startswith("GROUP_BY") or key.startswith("AGGREGATION") for key in diff):
        return "AGGREGATION_GRAIN_ERROR"
    if equivalent is False:
        return "SQL_COMPOSITION_ERROR"
    return None


def column_confusions(
    gold_signature: dict[str, Any],
    generated_signature: dict[str, Any],
    context: SchemaContext,
) -> list[dict[str, Any]]:
    expected = sorted(set(gold_signature["columns"]) - set(generated_signature["columns"]))
    actual = sorted(set(generated_signature["columns"]) - set(gold_signature["columns"]))
    records: list[dict[str, Any]] = []
    for generated_column, gold_column in zip(actual, expected, strict=False):
        generated_metadata = _column_metadata(generated_column, context)
        gold_metadata = _column_metadata(gold_column, context)
        records.append(
            {
                "generated_column": generated_column,
                "expected_column": gold_column,
                "generated_table": generated_column.split(".", 1)[0]
                if "." in generated_column
                else None,
                "expected_table": (
                    gold_column.split(".", 1)[0] if "." in gold_column else None
                ),
                "both_columns_visible": (
                    generated_metadata is not None and gold_metadata is not None
                ),
                "generated_metadata": generated_metadata,
                "expected_metadata": gold_metadata,
            }
        )
    return records


def aggregate_column_confusions(records: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(
            f"{item['generated_column']} -> {item['expected_column']}"
            for item in records
        )
    )


def _set_diff(
    output: dict[str, Any], label: str, gold: tuple[str, ...], generated: tuple[str, ...]
) -> None:
    missing = _missing(gold, generated)
    extra = _missing(generated, gold)
    if missing:
        output[f"{label}_MISSING"] = missing
    if extra:
        output[f"{label}_EXTRA"] = extra


def _missing(left: tuple[str, ...], right: tuple[str, ...]) -> list[str]:
    return sorted(set(left) - set(right))


def _column_key(
    column: exp.Column, aliases: dict[str, str], tables: set[str] | None = None
) -> str:
    table = aliases.get(column.table.lower(), column.table.lower()) if column.table else ""
    if table:
        return f"{table}.{column.name.lower()}"
    candidates = (
        sorted(f"{table_name}.{column.name.lower()}" for table_name in tables or ())
        if tables
        else []
    )
    return candidates[0] if len(candidates) == 1 else column.name.lower()


def _column_visible(value: str, visible_columns: set[str]) -> bool:
    if "." in value:
        return value in visible_columns
    return any(column.endswith(f".{value}") for column in visible_columns)


def _join_key(join: exp.Join, aliases: dict[str, str]) -> str:
    on = join.args.get("on")
    if on is None:
        return ""
    columns = sorted(
        _column_key(column, aliases) for column in on.find_all(exp.Column)
    )
    return _relationship_key(*columns) if columns else on.sql(dialect="postgres")


def _relationship_key(left: str, right: str) -> str:
    return "<->".join(sorted((left, right)))


def _where_values(expression: exp.Expression) -> list[str]:
    return [
        where.this.sql(dialect="postgres")
        for where in expression.find_all(exp.Where)
        if where.this is not None
    ]


def _group_by_values(expression: exp.Expression) -> list[str]:
    group = expression.find(exp.Group)
    if group is None:
        return []
    return [item.sql(dialect="postgres") for item in group.expressions]


def _limit_value(expression: exp.Expression) -> int | str | None:
    limit = expression.find(exp.Limit)
    if limit is None or limit.expression is None:
        return None
    value = limit.expression
    if isinstance(value, exp.Literal) and value.is_number:
        return int(value.this)
    return str(value.sql(dialect="postgres"))


def _window_partition(window: exp.Window) -> str:
    values = window.args.get("partition_by") or []
    return ", ".join(item.sql(dialect="postgres") for item in values)


def _window_order(window: exp.Window) -> str:
    order = window.args.get("order")
    return order.sql(dialect="postgres") if order is not None else ""


def _order_directions(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple("DESC" if " DESC" in value.upper() else "ASC" for value in values)


def _column_metadata(value: str, context: SchemaContext) -> dict[str, Any] | None:
    if "." not in value:
        return None
    table_name, column_name = value.split(".", 1)
    for table in context.tables:
        if table.name.lower() != table_name.lower():
            continue
        for column in table.columns:
            if column.name.lower() == column_name.lower():
                return {
                    "type": column.type,
                    "description": column.description,
                    "foreign_key_table": column.foreign_key_table,
                    "foreign_key_column": column.foreign_key_column,
                }
    return None
