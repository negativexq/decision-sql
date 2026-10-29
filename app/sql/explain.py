"""Describe a query in plain parts, and change its literal conditions.

Both read only the SQL: what it reads, what it computes, which rows it keeps,
and how it groups, orders, and cuts them.  Nothing here asks the model, so the
description says what the query does rather than what the model meant.

A condition is a comparison between a column and literals, such as
``o.status = 'shipped'`` or ``o.created_at >= DATE '2024-01-01'``.  Conditions
are numbered in the order the parser walks the query, so the same SQL always
numbers them the same way and an edit names a condition by that number.  An
edit replaces the literals or drops the condition; the result is still
untrusted SQL that every gate checks again.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp

from app.sql.parser import SQLParseFailure, SQLParser
from app.sql.values import cte_names, direct_tables

MAX_CONDITIONS = 12
MAX_VALUES = 50

ValueKind = Literal["text", "number", "date"]

_OPERATORS: dict[type[exp.Expression], str] = {
    exp.EQ: "=",
    exp.NEQ: "≠",
    exp.GT: ">",
    exp.GTE: "≥",
    exp.LT: "<",
    exp.LTE: "≤",
    exp.Like: "like",
    exp.ILike: "like",
    exp.In: "in",
    exp.Between: "between",
}
_FLIPPED: dict[type[exp.Expression], str] = {exp.GT: "<", exp.GTE: "≤", exp.LT: ">", exp.LTE: "≥"}
_AGGREGATES: dict[type[exp.Expression], str] = {
    exp.Count: "count",
    exp.Sum: "sum",
    exp.Avg: "average",
    exp.Min: "min",
    exp.Max: "max",
}
_DATE_TYPES = {exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPTZ}
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


class Condition(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    # table.column when the column's table is known, else the column as written.
    column: str
    operator: str
    values: list[str]
    kind: ValueKind
    # False when dropping it would change more than this condition, as under OR.
    removable: bool


class Measure(BaseModel):
    model_config = ConfigDict(frozen=True)

    function: str
    # The column, or null for COUNT(*).
    column: str | None = None
    distinct: bool = False


class Ordering(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    descending: bool


class SqlExplanation(BaseModel):
    model_config = ConfigDict(frozen=True)

    tables: list[str] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    order: list[Ordering] = Field(default_factory=list)
    limit: int | None = None
    distinct: bool = False


class ConditionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, lt=MAX_CONDITIONS)
    values: list[str] = Field(default_factory=list, max_length=MAX_VALUES)
    remove: bool = False

    @model_validator(mode="after")
    def _one_change(self) -> ConditionEdit:
        if self.remove == bool(self.values):
            raise ValueError("An edit either gives new values or removes the condition.")
        if any(len(value) > 200 for value in self.values):
            raise ValueError("A value is longer than 200 characters.")
        return self


class ConditionEditError(ValueError):
    pass


def explain(sql: str) -> SqlExplanation | None:
    try:
        expression = SQLParser().parse(sql).expression
    except SQLParseFailure:
        return None
    outer = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if outer is None:
        return None
    ctes = cte_names(expression)
    selects = list(expression.find_all(exp.Select))
    tables = sorted(
        {
            table.name.lower()
            for table in expression.find_all(exp.Table)
            if table.name.lower() not in ctes
        }
    )
    # The outer SELECT says what the answer shows; a query that computes inside a
    # CTE or subquery shows what it computed there.
    measured = next((select for select in selects if _measures(select)), outer)
    grouped = next((select for select in selects if select.args.get("group")), None)
    order = outer.args.get("order")
    limit = outer.args.get("limit")
    limit_value = limit.expression if isinstance(limit, exp.Limit) else None
    return SqlExplanation(
        tables=tables,
        measures=_measures(measured)[:4],
        conditions=[condition for condition, _ in _conditions(expression)],
        groups=[_name(item) for item in grouped.args["group"].expressions] if grouped else [],
        order=[
            Ordering(column=_name(item.this), descending=bool(item.args.get("desc")))
            for item in (order.expressions if order is not None else [])
        ][:3],
        limit=int(limit_value.this)
        if isinstance(limit_value, exp.Literal) and limit_value.is_int
        else None,
        distinct=bool(outer.args.get("distinct")),
    )


def edit_conditions(sql: str, edits: list[ConditionEdit]) -> str:
    """Return the SQL with the edited conditions' literals replaced or dropped."""
    try:
        expression = SQLParser().parse(sql).expression
    except SQLParseFailure as error:
        raise ConditionEditError("The query could not be read.") from error
    found = {condition.index: (condition, node) for condition, node in _conditions(expression)}
    if len({edit.index for edit in edits}) != len(edits):
        raise ConditionEditError("Two edits name the same condition.")
    for edit in edits:
        if edit.index not in found:
            raise ConditionEditError(f"The query has no condition {edit.index}.")
        condition, node = found[edit.index]
        if edit.remove:
            if not condition.removable:
                raise ConditionEditError(
                    f"{condition.column} is tied to other conditions and cannot be removed alone."
                )
            node.replace(exp.true())
            continue
        _replace_values(node, condition, edit.values)
    return expression.sql(dialect="postgres")


def _conditions(expression: exp.Expression) -> list[tuple[Condition, exp.Expression]]:
    ctes = cte_names(expression)
    found: list[tuple[Condition, exp.Expression]] = []
    for select in expression.find_all(exp.Select):
        aliases = direct_tables(select, ctes)
        clauses = [select.args.get("where")]
        clauses.extend(join.args.get("on") for join in select.args.get("joins") or ())
        for clause in clauses:
            if clause is None:
                continue
            for node in clause.walk():
                if len(found) >= MAX_CONDITIONS:
                    return found
                if node.find_ancestor(exp.Select) is not select:
                    continue
                condition = _condition(node, len(found), aliases)
                if condition is not None:
                    found.append((condition, node))
    return found


def _condition(node: exp.Expression, index: int, aliases: dict[str, str]) -> Condition | None:
    kind = type(node)
    if kind not in _OPERATORS:
        return None
    column: exp.Expression | None = node.this
    literals: list[exp.Expression]
    operator = _OPERATORS[kind]
    if isinstance(node, exp.In):
        if node.args.get("query") or not node.expressions:
            return None
        literals = list(node.expressions)
    elif isinstance(node, exp.Between):
        literals = [node.args["low"], node.args["high"]]
    else:
        literals = [node.expression]
        if not isinstance(column, exp.Column) and isinstance(node.expression, exp.Column):
            # 2024 <= year reads as year >= 2024.
            column, literals = node.expression, [node.this]
            operator = _FLIPPED.get(kind, operator)
    if not isinstance(column, exp.Column) or len(literals) > MAX_VALUES:
        return None
    values: list[str] = []
    kinds: set[ValueKind] = set()
    for literal in literals:
        read = _literal(literal)
        if read is None:
            return None
        values.append(read[0])
        kinds.add(read[1])
    if len(kinds) != 1:
        return None
    table = aliases.get(column.table.lower()) if column.table else None
    if table is None and not column.table and len(set(aliases.values())) == 1:
        table = next(iter(aliases.values()))
    return Condition(
        index=index,
        column=f"{table}.{column.name}" if table else column.sql(dialect="postgres"),
        operator=f"not {operator}" if isinstance(node.parent, exp.Not) else operator,
        values=values,
        kind=kinds.pop(),
        removable=_conjunct(node),
    )


def _literal(node: exp.Expression) -> tuple[str, ValueKind] | None:
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal) and not node.this.is_string:
        return f"-{node.this.this}", "number"
    if isinstance(node, exp.Literal):
        if not node.is_string:
            return str(node.this), "number"
        return str(node.this), "date" if _ISO_DAY.match(str(node.this)) else "text"
    if (
        isinstance(node, exp.Cast)
        and isinstance(node.this, exp.Literal)
        and node.this.is_string
        and node.to.this in _DATE_TYPES
    ):
        return str(node.this.this), "date"
    return None


def _conjunct(node: exp.Expression) -> bool:
    """True when the node is ANDed directly into a WHERE or JOIN ON."""
    if isinstance(node.parent, exp.Not):
        node = node.parent
    parent = node.parent
    while isinstance(parent, exp.And | exp.Paren):
        node, parent = parent, parent.parent
    return isinstance(parent, exp.Where) or (isinstance(parent, exp.Join) and node.arg_key == "on")


def _replace_values(node: exp.Expression, condition: Condition, values: list[str]) -> None:
    if isinstance(node, exp.In):
        shape = node.expressions[0]
        node.set("expressions", [_build(value, condition.kind, shape) for value in values])
        return
    if isinstance(node, exp.Between):
        if len(values) != 2:
            raise ConditionEditError(f"{condition.column} needs two values, a low and a high.")
        node.set("low", _build(values[0], condition.kind, node.args["low"]))
        node.set("high", _build(values[1], condition.kind, node.args["high"]))
        return
    if len(values) != 1:
        raise ConditionEditError(f"{condition.column} takes one value.")
    target = "expression" if isinstance(node.this, exp.Column) else "this"
    node.set(target, _build(values[0], condition.kind, node.args[target]))


def _build(value: str, kind: ValueKind, shape: exp.Expression) -> exp.Expression:
    """A literal of the kind and form the replaced one had: a quoted date stays quoted."""
    value = value.strip()
    if kind == "number":
        if not _NUMBER.match(value):
            raise ConditionEditError(f"{value!r} is not a number.")
        return exp.Literal.number(value)
    if kind == "date":
        try:
            date.fromisoformat(value[:10])
        except ValueError as error:
            raise ConditionEditError(f"{value!r} is not a date like 2024-01-31.") from error
        if isinstance(shape, exp.Cast):
            return exp.Cast(this=exp.Literal.string(value), to=shape.to.copy())
    return exp.Literal.string(value)


def _measures(select: exp.Select) -> list[Measure]:
    found: list[Measure] = []
    for projection in select.expressions:
        for node in projection.find_all(*_AGGREGATES):
            if node.find_ancestor(exp.Select) is not select:
                continue
            argument = node.this
            distinct = isinstance(argument, exp.Distinct)
            if distinct:
                argument = argument.expressions[0] if argument.expressions else None
            column = None if argument is None or isinstance(argument, exp.Star) else _name(argument)
            measure = Measure(function=_AGGREGATES[type(node)], column=column, distinct=distinct)
            if measure not in found:
                found.append(measure)
    return found


def _name(node: exp.Expression) -> str:
    if isinstance(node, exp.Column):
        return node.name
    if isinstance(node, exp.Alias):
        return node.alias
    return node.sql(dialect="postgres")[:60]
