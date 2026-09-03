"""Offline, gold-derived BIRD SQL feature extraction."""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def _parse(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return None


def ast_features(sql: str) -> dict[str, bool]:
    tree = _parse(sql)
    if tree is None:
        return {"PARSE_ERROR": True}
    windows = list(tree.find_all(exp.Window))
    names = {type(node.this).__name__.replace("_", "").upper() for node in windows if node.this}
    return {
        "HAS_WINDOW": bool(windows),
        "WINDOW_OVER": bool(windows),
        "LAG": "LAG" in names,
        "LEAD": "LEAD" in names,
        "ROW_NUMBER": "ROWNUMBER" in names,
        "RANK": "RANK" in names,
        "DENSE_RANK": "DENSERANK" in names,
        "WINDOW_AGGREGATE": any(isinstance(node.this, exp.AggFunc) for node in windows),
        "EXPLICIT_FRAME": any(node.args.get("spec") is not None for node in windows),
        "PARTITION_BY": any(node.args.get("partition_by") for node in windows),
    }


def structural_profile(sql: str) -> dict[str, int | bool]:
    tree = _parse(sql)
    if tree is None:
        return {"parse_error": 1}
    features = ast_features(sql)
    joins = len(list(tree.find_all(exp.Join)))
    return {
        "tables": len(list(tree.find_all(exp.Table))),
        "joins": joins,
        "multi_join": int(joins > 1),
        "group_by": int(tree.args.get("group") is not None),
        "having": int(tree.args.get("having") is not None),
        "order_by": int(tree.args.get("order") is not None),
        "limit": int(tree.args.get("limit") is not None),
        "subquery": len(list(tree.find_all(exp.Subquery))),
        "cte": int(tree.args.get("with") is not None or tree.args.get("with_") is not None),
        "set_operation": int(any(isinstance(node, exp.SetOperation) for node in tree.walk())),
        "case": int(any(isinstance(node, exp.Case) for node in tree.walk())),
        "arithmetic": int(
            any(isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)) for node in tree.walk())
        ),
        "division": int(any(isinstance(node, exp.Div) for node in tree.walk())),
        "window": int(features.get("HAS_WINDOW", False)),
        "lag": int(features.get("LAG", False)),
        "lead": int(features.get("LEAD", False)),
        "row_number": int(features.get("ROW_NUMBER", False)),
        "rank": int(features.get("RANK", False)),
        "dense_rank": int(features.get("DENSE_RANK", False)),
        "explicit_frame": int(features.get("EXPLICIT_FRAME", False)),
        "window_aggregate": int(features.get("WINDOW_AGGREGATE", False)),
        "date_time": int(
            any(
                isinstance(
                    node, (exp.DateAdd, exp.DateDiff, exp.Extract, exp.Interval, exp.CurrentDate)
                )
                for node in tree.walk()
            )
        ),
    }


def complexity_tags(profile: dict[str, int | bool]) -> list[str]:
    tags: list[str] = []
    if (
        not profile.get("joins")
        and not profile.get("group_by")
        and not profile.get("window")
        and not profile.get("subquery")
    ):
        tags.append("SIMPLE_SELECT")
    if profile.get("joins"):
        tags.append("MULTI_JOIN" if profile.get("multi_join") else "JOIN")
    if profile.get("group_by"):
        tags.append("AGGREGATION")
    if profile.get("subquery") or profile.get("cte"):
        tags.append("NESTED_QUERY")
    if profile.get("window"):
        tags.append("WINDOW")
    if profile.get("arithmetic"):
        tags.append("COMPOSITIONAL_ANALYTIC")
    return tags
