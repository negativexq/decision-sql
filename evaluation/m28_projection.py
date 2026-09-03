"""Offline projection diagnostics for the M2.8 result-shape experiment."""

from typing import Any

from sqlglot import exp, parse_one

from app.generation.result_shape import ResultOutputKind, ResultShapeProposal
from evaluation.m27_forensics import sql_signature


def projection_facts(sql: str) -> dict[str, Any]:
    tree = parse_one(sql, dialect="postgres")
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return {"expressions": (), "physical_columns": (), "arity": None, "has_star": False}
    expressions = tuple(
        _normalize(item.this if isinstance(item, exp.Alias) else item)
        for item in select.expressions
    )
    physical = tuple(
        _normalize(item.this if isinstance(item, exp.Alias) else item)
        for item in select.expressions
        if isinstance(item.this if isinstance(item, exp.Alias) else item, exp.Column)
    )
    return {
        "expressions": expressions,
        "physical_columns": physical,
        "arity": len(expressions),
        "has_star": any(item == "*" or item.endswith(".*") for item in expressions),
    }


def projection_diagnostics(gold_sql: str, generated_sql: str | None) -> dict[str, Any]:
    if not generated_sql:
        return {"status": "PROJECTION_UNCERTAIN", "reason": "no generated SQL"}
    try:
        gold = projection_facts(gold_sql)
        generated = projection_facts(generated_sql)
    except Exception:
        return {"status": "PROJECTION_UNCERTAIN", "reason": "projection parse failed"}
    missing = sorted(set(gold["expressions"]) - set(generated["expressions"]))
    extra = sorted(set(generated["expressions"]) - set(gold["expressions"]))
    if generated["arity"] is None or gold["arity"] is None:
        status = "PROJECTION_UNCERTAIN"
    elif missing:
        status = "PROJECTION_MISSING"
    elif extra:
        status = "PROJECTION_EXTRA"
    elif gold["expressions"] == generated["expressions"]:
        status = "PROJECTION_EXACT"
    else:
        status = "PROJECTION_WRONG"
    return {
        "status": status,
        "gold_arity": gold["arity"],
        "generated_arity": generated["arity"],
        "arity_exact": gold["arity"] == generated["arity"],
        "missing_expressions": missing,
        "extra_expressions": extra,
        "gold_physical_columns": list(gold["physical_columns"]),
        "generated_physical_columns": list(generated["physical_columns"]),
        "physical_projection_exact": gold["physical_columns"] == generated["physical_columns"],
        "extra_projection_count": len(extra),
        "missing_projection_count": len(missing),
    }


def validate_shape_against_gold(
    proposal: ResultShapeProposal | None, gold_sql: str
) -> dict[str, Any] | None:
    if proposal is None:
        return None
    gold = projection_facts(gold_sql)
    outputs = proposal.outputs
    physical_hints = tuple(
        output.source_hint.lower()
        for output in outputs
        if output.kind is ResultOutputKind.PHYSICAL_COLUMN and output.source_hint
    )
    gold_physical = tuple(item.lower() for item in gold["physical_columns"])
    return {
        "output_arity_exact": len(outputs) == gold["arity"],
        "required_output_recall": (
            sum(hint in physical_hints for hint in gold_physical) / len(gold_physical)
            if gold_physical
            else 1.0
        ),
        "extra_output_count": max(0, len(outputs) - gold["arity"]),
        "shape": proposal.shape.value,
        "explicit_limit": proposal.explicit_limit,
        "explicit_order_direction": proposal.explicit_order_direction,
    }


def projection_only_failure(
    gold_sql: str, generated_sql: str | None, equivalent: bool | None
) -> bool:
    """Conservative ceiling estimate; never rewrites or executes SQL."""
    if equivalent is not False or not generated_sql:
        return False
    projection = projection_diagnostics(gold_sql, generated_sql)
    if projection.get("status") != "PROJECTION_EXTRA":
        return False
    gold_signature = sql_signature(gold_sql)
    generated_signature = sql_signature(generated_sql)
    structural_fields = (
        "tables",
        "joins",
        "filters",
        "aggregations",
        "group_by",
        "order_by",
        "limit",
        "windows",
    )
    return all(gold_signature[field] == generated_signature[field] for field in structural_fields)


def _normalize(expression: exp.Expression) -> str:
    copy = expression.copy()
    for alias in list(copy.find_all(exp.Alias)):
        alias.replace(alias.this.copy())
    return copy.sql(dialect="postgres").lower()
