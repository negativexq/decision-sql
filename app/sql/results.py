"""Checks on what a query returned, run after it executes.

Two findings the runtime can name without judging the question:

- A column the model named as a percentage (an alias containing ``percent`` or
  ``pct``, as the contract asks) holds a value below 0 or above 100.
- The query keeps every row equal to an overall maximum or minimum, or ranked
  first, and more than one row came back: those rows tie, and the user should
  hear so rather than read the list as several answers.
"""

from __future__ import annotations

from decimal import Decimal

import sqlglot
from sqlglot import exp

_PERCENT_MARKS = ("percent", "pct")


def percent_out_of_range(columns: list[str], rows: list[dict[str, object]]) -> tuple[str, ...]:
    """The percentage columns that hold a number outside 0 to 100."""
    marked = [name for name in columns if any(mark in name.casefold() for mark in _PERCENT_MARKS)]
    return tuple(
        name
        for name in marked
        if any(
            isinstance(value, int | float | Decimal)
            and not isinstance(value, bool)
            and not 0 <= value <= 100
            for value in (row.get(name) for row in rows)
        )
    )


def keeps_ties(sql: str) -> bool:
    """Whether the query keeps all rows sharing one overall extreme value.

    That is a comparison with an uncorrelated ``(SELECT MAX(..))`` or
    ``(SELECT MIN(..))``, or a filter on an unpartitioned ``RANK()`` or
    ``DENSE_RANK()`` equal to 1.  A per-group extreme is not a tie.
    """
    try:
        expression = sqlglot.parse_one(sql, read="postgres")
    except Exception:  # an unreadable text names no tie
        return False
    if expression is None:
        return False
    sides = (side for eq in expression.find_all(exp.EQ) for side in eq.args.values())
    if any(_is_overall_extreme(side) for side in sides):
        return True
    return _filters_first_rank(expression)


def _is_overall_extreme(side: object) -> bool:
    if not isinstance(side, exp.Subquery):
        return False
    inner = side.this
    if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
        return False
    projected = inner.expressions[0].unalias()
    if not isinstance(projected, exp.Max | exp.Min) or inner.args.get("group"):
        return False
    return not _correlated(inner)


def _correlated(select: exp.Select) -> bool:
    local = {table.alias_or_name for table in select.find_all(exp.Table)}
    return any(column.table and column.table not in local for column in select.find_all(exp.Column))


def _filters_first_rank(expression: exp.Expression) -> bool:
    ranked: set[str] = set()
    for window in expression.find_all(exp.Window):
        first_rank = isinstance(window.this, exp.Rank | exp.DenseRank)
        if first_rank and not window.args.get("partition_by"):
            alias = window.parent
            if isinstance(alias, exp.Alias):
                ranked.add(alias.alias)
    if not ranked:
        return False
    for comparison in (*expression.find_all(exp.EQ), *expression.find_all(exp.LTE)):
        left, right = comparison.this, comparison.expression
        if (
            isinstance(left, exp.Column)
            and left.name in ranked
            and isinstance(right, exp.Literal)
            and right.this == "1"
        ):
            return True
    return False
