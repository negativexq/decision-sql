"""Deterministic PostgreSQL AST summaries and pairwise diagnostics."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import sqlglot
from sqlglot import exp


def parse_sql(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read="postgres")
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, TypeError):
        return None


def _sql(node: exp.Expression | None) -> str | None:
    if node is None:
        return None
    return node.sql(dialect="postgres", pretty=False).strip()


def _unaliased(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Alias):
        node = node.this
    return node


def _column_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Column):
        return node.name.lower()
    return _sql(node) or ""


def _table_name(node: exp.Table) -> str:
    """Normalize schema qualification while preserving the physical table name."""
    return node.name.lower()


def _aggregate_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.AggFunc):
        return node.key.upper()
    return None


def _predicate_sql(node: exp.Expression | None) -> list[str]:
    if node is None:
        return []
    predicate_types = (
        exp.And,
        exp.Or,
        exp.EQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.NEQ,
        exp.Between,
        exp.In,
        exp.Like,
    )
    return sorted(
        {
            (_sql(condition) or "").lower()
            for condition in node.walk()
            if isinstance(condition, predicate_types)
            if _sql(condition)
        }
    )


def _literal_values(tree: exp.Expression) -> list[str]:
    values: list[str] = []
    for node in tree.find_all(exp.Literal):
        values.append(node.sql(dialect="postgres", pretty=False).lower())
    return sorted(values)


def _date_functions(tree: exp.Expression) -> list[str]:
    names: list[str] = []
    temporal_types = (
        exp.DateAdd,
        exp.DateDiff,
        exp.Extract,
        exp.Interval,
        exp.CurrentDate,
        exp.CurrentTimestamp,
    )
    for node in tree.walk():
        if isinstance(node, temporal_types):
            if isinstance(node, (exp.CurrentDate, exp.CurrentTimestamp)):
                names.append("CURRENT_REFERENCE")
            else:
                names.append(node.key.upper())
        elif isinstance(node, exp.Anonymous) and node.name.lower() in {
            "date_trunc",
            "date_part",
            "to_char",
            "make_date",
            "make_interval",
        }:
            names.append(node.name.upper())
    return sorted(names)


def _window_functions(tree: exp.Expression) -> list[str]:
    result: list[str] = []
    for node in tree.find_all(exp.Window):
        if node.this is not None:
            result.append(node.this.key.upper())
    return sorted(result)


def _expression_signature(
    node: exp.Expression | None,
    table_aliases: dict[str, str] | None = None,
) -> Any:
    """Create a conservative semantic-ish signature for diagnostic comparison."""
    if node is None:
        return None
    if isinstance(node, exp.Alias):
        return _expression_signature(node.this, table_aliases)
    if isinstance(node, exp.Column):
        qualifier = node.table.lower() if node.table else ""
        if table_aliases is not None:
            qualifier = table_aliases.get(qualifier, qualifier)
        return {
            "column": node.name.lower(),
            **({"table": qualifier} if table_aliases is not None and qualifier else {}),
        }
    if isinstance(node, exp.Table):
        return {"table": node.name.lower()}
    if isinstance(node, exp.Literal):
        value = str(node.this).lower()
        if node.is_number:
            try:
                value = str(float(value)).rstrip("0").rstrip(".")
            except ValueError:
                pass
        return {"literal": value}
    if isinstance(node, exp.Nullif):
        return _expression_signature(node.this, table_aliases)
    if isinstance(node, exp.Cast):
        return _expression_signature(node.this, table_aliases)
    if isinstance(node, exp.Paren):
        return _expression_signature(node.this, table_aliases)
    if isinstance(node, exp.Star):
        return {"star": True}
    args: dict[str, Any] = {}
    for key, value in node.args.items():
        if key in {"alias", "comments"}:
            continue
        if isinstance(value, list):
            args[key] = [_expression_signature(item, table_aliases) for item in value]
        elif isinstance(value, exp.Expression):
            args[key] = _expression_signature(value, table_aliases)
        elif value is not None:
            args[key] = str(value).lower()
    if isinstance(node, exp.EQ):
        operands = [args.get("this"), args.get("expression")]
        args["this"], args["expression"] = sorted(
            operands, key=lambda item: json.dumps(item, sort_keys=True)
        )
    return {"expression": node.key.lower(), "args": args}


def _ratio_components(tree: exp.Expression) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for node in tree.find_all(exp.Div):
        components.append(
            {
                "numerator": _expression_signature(node.this),
                "denominator": _expression_signature(node.expression),
            }
        )
    return components


def _join_condition_signatures(
    tree: exp.Expression, table_aliases: dict[str, str]
) -> list[str]:
    return sorted(
        json.dumps(_expression_signature(join.args.get("on"), table_aliases), sort_keys=True)
        for join in tree.find_all(exp.Join)
        if join.args.get("on") is not None
    )


def summarize_sql(sql: str) -> dict[str, Any]:
    """Return a stable, diagnostic-only structural representation."""
    tree = parse_sql(sql)
    if tree is None:
        return {"parse_success": False, "sql": sql.strip()}

    select_nodes = list(tree.find_all(exp.Select))
    root_select = (
        tree if isinstance(tree, exp.Select) else select_nodes[0] if select_nodes else tree
    )
    projections = [
        (_sql(_unaliased(expression)) or "").lower()
        for expression in getattr(root_select, "expressions", [])
    ]
    group = root_select.args.get("group") if isinstance(root_select, exp.Select) else None
    order = root_select.args.get("order") if isinstance(root_select, exp.Select) else None
    where = root_select.args.get("where") if isinstance(root_select, exp.Select) else None
    having = root_select.args.get("having") if isinstance(root_select, exp.Select) else None
    joins = list(tree.find_all(exp.Join))
    join_conditions = sorted(
        (_sql(join.args.get("on")) or "").lower()
        for join in joins
        if join.args.get("on") is not None
    )
    table_aliases = {
        (node.alias_or_name or node.name).lower(): node.name.lower()
        for node in tree.find_all(exp.Table)
    }
    normalized_join_conditions = _join_condition_signatures(tree, table_aliases)
    columns_set: set[str] = set()
    for node in tree.find_all(exp.Column):
        qualifier = table_aliases.get(node.table.lower(), node.table.lower()) if node.table else ""
        columns_set.add(f"{qualifier + '.' if qualifier else ''}{node.name.lower()}")
    columns = sorted(columns_set)
    aggregates = sorted(name for node in tree.walk() if (name := _aggregate_name(node)) is not None)
    arithmetic = sorted(
        (_sql(node) or "").lower()
        for node in tree.walk()
        if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div))
    )
    divisions = sorted((_sql(node) or "").lower() for node in tree.find_all(exp.Div) if _sql(node))
    cases = sorted((_sql(node) or "").lower() for node in tree.find_all(exp.Case) if _sql(node))
    return {
        "parse_success": True,
        "tables": sorted({_table_name(node) for node in tree.find_all(exp.Table)}),
        "joins": len(joins),
        "join_types": sorted(str(join.args.get("kind") or "INNER").upper() for join in joins),
        "join_conditions": join_conditions,
        "join_conditions_normalized": normalized_join_conditions,
        "select_count": len(projections),
        "select_expressions": projections,
        "select_expression_signatures": [
            json.dumps(_expression_signature(_unaliased(expression)), sort_keys=True)
            for expression in getattr(root_select, "expressions", [])
        ],
        "columns": columns,
        "aggregations": aggregates,
        "distinct": bool(list(tree.find_all(exp.Distinct))),
        "group_by": sorted(
            (_sql(item) or "").lower() for item in (group.expressions if group else [])
        ),
        "having": _predicate_sql(having),
        "where": _predicate_sql(where),
        "order_by": sorted(
            (_sql(item) or "").lower() for item in (order.expressions if order else [])
        ),
        "limit": _sql(root_select.args.get("limit"))
        if isinstance(root_select, exp.Select)
        else None,
        "subqueries": len(list(tree.find_all(exp.Subquery))),
        "ctes": len(list(tree.find_all(exp.CTE))),
        "set_operations": len(list(tree.find_all(exp.SetOperation))),
        "case": cases,
        "arithmetic": arithmetic,
        "division": divisions,
        "ratio_components": _ratio_components(tree),
        "date_time_functions": _date_functions(tree),
        "window_functions": _window_functions(tree),
        "literal_values": _literal_values(tree),
        "sql_normalized": tree.sql(dialect="postgres", pretty=False).strip().lower(),
    }


def _set_difference(left: list[str], right: list[str]) -> dict[str, list[str]]:
    return {"missing": sorted(set(left) - set(right)), "extra": sorted(set(right) - set(left))}


def compare_summaries(gold: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    """Compare structure without rewriting or repairing either query."""
    if not gold.get("parse_success") or not generated.get("parse_success"):
        return {"parse_comparable": False}
    list_fields = (
        "tables",
        "join_conditions",
        "select_expressions",
        "columns",
        "aggregations",
        "group_by",
        "where",
        "having",
        "order_by",
        "case",
        "arithmetic",
        "division",
        "date_time_functions",
        "window_functions",
    )
    differences: dict[str, Any] = {}
    for field in list_fields:
        difference = _set_difference(gold.get(field, []), generated.get(field, []))
        if difference["missing"] or difference["extra"]:
            differences[field] = difference
    for field in ("joins", "select_count", "subqueries", "ctes", "set_operations", "distinct"):
        if gold.get(field) != generated.get(field):
            differences[field] = {"gold": gold.get(field), "generated": generated.get(field)}
    if gold.get("limit") != generated.get("limit"):
        differences["limit"] = {"gold": gold.get("limit"), "generated": generated.get("limit")}
    return {"parse_comparable": True, "differences": differences}


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def count_feature(values: list[dict[str, Any]], field: str) -> Counter[str]:
    return Counter(value for summary in values for value in summary.get(field, []))
