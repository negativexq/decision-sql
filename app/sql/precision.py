"""Keep the precision of single-precision columns in totals and numeric casts.

PostgreSQL's ``SUM`` over a ``real`` (float4) column adds in single precision
and returns ``real``, which keeps about six significant digits: a yearly total
of 14009.34 comes back as 14009.3, and a difference of two such totals is wrong
in its second decimal. ``AVG`` already accumulates in double precision. Where a
``SUM`` reads a ``real`` column, the column is cast to ``double precision``
inside the sum.

Casting a ``real`` column to ``numeric`` also rounds each value to six
significant digits: ``12685.04::real::numeric`` is ``12685``. Such a cast goes
through ``double precision`` first, which keeps every digit the stored value has.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

from app.catalog.models import SchemaCatalog
from app.sql.values import _resolve, cte_names, direct_tables

_SINGLE = {"real", "float4"}

CHANGE = (
    "A single-precision (real) column is read as double precision in a SUM or a "
    "cast to numeric, so the result keeps its decimals."
)
_NUMERIC = {exp.DataType.Type.DECIMAL}


@dataclass(frozen=True)
class PrecisionCheck:
    expression: exp.Expression
    changed: bool


class SinglePrecisionSums:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self._columns: dict[str, set[str]] = {}
        self._single: set[tuple[str, str]] = set()
        for table in catalog.tables:
            self._columns[table.name.lower()] = {column.name.lower() for column in table.columns}
            for column in table.columns:
                if column.type.strip().lower() in _SINGLE:
                    self._single.add((table.name.lower(), column.name.lower()))

    def widen(self, expression: exp.Expression) -> PrecisionCheck:
        if not self._single:
            return PrecisionCheck(expression, False)
        expression = expression.copy()
        ctes = cte_names(expression)

        def single(column: exp.Column) -> bool:
            select = column.find_ancestor(exp.Select)
            if select is None:
                return False
            return _resolve(column, direct_tables(select, ctes), self._columns) in self._single

        def widened(column: exp.Column) -> exp.Expression:
            return exp.cast(column.copy(), exp.DataType.build("double precision"))

        changed = False
        for cast in list(expression.find_all(exp.Cast)):
            # real -> numeric rounds to six digits; real -> double -> numeric does not.
            if isinstance(cast.this, exp.Column) and cast.to.this in _NUMERIC and single(cast.this):
                cast.this.replace(widened(cast.this))
                changed = True
        for total in list(expression.find_all(exp.Sum)):
            for column in list(total.find_all(exp.Column)):
                if isinstance(column.parent, exp.Cast) or not single(column):
                    continue  # a cast column is read as the author chose, or widened above
                column.replace(widened(column))
                changed = True
        return PrecisionCheck(expression, changed)
