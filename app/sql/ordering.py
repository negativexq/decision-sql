"""Put rows with no value last when a descending order decides which rows come back.

PostgreSQL sorts NULL above every value, so ``ORDER BY rating DESC LIMIT 1``
returns a row with no rating before the highest one, and a ranking
(``RANK() OVER (ORDER BY rating DESC)``) gives rank 1 to a missing value. A
question about the greatest, latest, or top N never means those rows, and other
engines a query is often written for sort NULL lowest. Where a descending order
picks rows (a LIMIT, OFFSET, FETCH, or DISTINCT ON, or a ranking window), the
order gets ``NULLS LAST``. An order whose author wrote ``NULLS FIRST`` anywhere
is left as written, and a COUNT, which is never NULL, is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.tokenizer_core import TokenType

# Window functions whose result depends on which row sorts first.
_RANKING = (
    exp.RowNumber,
    exp.Rank,
    exp.DenseRank,
    exp.PercentRank,
    exp.CumeDist,
    exp.Ntile,
    exp.FirstValue,
    exp.NthValue,
)

CHANGE = (
    "Rows with no value sort last in a descending order that picks rows (NULLS LAST): "
    "PostgreSQL would otherwise put them first."
)


@dataclass(frozen=True)
class OrderingCheck:
    expression: exp.Expression
    changed: bool


def _names_nulls_first(sql: str) -> bool:
    try:
        tokens = sqlglot.tokenize(sql, read="postgres")
    except Exception:  # an unreadable text has nothing to honour
        return False
    return any(
        first.text.upper() == "NULLS" and second.token_type is TokenType.FIRST
        for first, second in zip(tokens, tokens[1:], strict=False)
    )


def _picks_rows(ordered: exp.Ordered) -> bool:
    order = ordered.parent
    if not isinstance(order, exp.Order):
        return False
    owner = order.parent
    if isinstance(owner, exp.Window):
        return isinstance(owner.this, _RANKING)
    if isinstance(owner, exp.Select | exp.Union | exp.Intersect | exp.Except):
        if any(owner.args.get(key) for key in ("limit", "offset", "fetch")):
            return True
        distinct = owner.args.get("distinct")
        return isinstance(distinct, exp.Distinct) and distinct.args.get("on") is not None
    return False


def nulls_last(expression: exp.Expression, written_sql: str) -> OrderingCheck:
    """``expression`` with NULLS LAST on each descending order that picks rows.

    ``written_sql`` is the text as its author wrote it: once parsed, an explicit
    ``DESC NULLS FIRST`` looks the same as a bare ``DESC``.
    """
    if _names_nulls_first(written_sql):
        return OrderingCheck(expression, False)
    tree = expression.copy()
    changed = False
    for ordered in tree.find_all(exp.Ordered):
        if not ordered.args.get("desc") or not ordered.args.get("nulls_first"):
            continue
        if isinstance(ordered.this, exp.Count) or not _picks_rows(ordered):
            continue
        ordered.set("nulls_first", False)
        changed = True
    return OrderingCheck(tree if changed else expression, changed)
