"""Total single-precision columns in double precision.

PostgreSQL's ``SUM`` over a ``real`` (float4) column adds in single precision
and returns ``real``, which keeps about six significant digits: a yearly total
of 14009.34 comes back as 14009.3, and a difference of two such totals is wrong
in its second decimal. ``AVG`` already accumulates in double precision. Where a
``SUM`` reads a ``real`` column, the column is cast to ``double precision``
inside the sum, so the total keeps the precision the stored values have.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

from app.catalog.models import SchemaCatalog
from app.sql.values import _resolve, cte_names, direct_tables

_SINGLE = {"real", "float4"}

CHANGE = (
    "A SUM over a single-precision (real) column adds in double precision, "
    "so the total keeps its decimals."
)


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
        changed = False
        for total in list(expression.find_all(exp.Sum)):
            for column in list(total.find_all(exp.Column)):
                if isinstance(column.parent, exp.Cast):
                    continue  # the author chose the type
                select = column.find_ancestor(exp.Select)
                if select is None:
                    continue
                target = _resolve(column, direct_tables(select, ctes), self._columns)
                if target not in self._single:
                    continue
                column.replace(exp.cast(column.copy(), exp.DataType.build("double precision")))
                changed = True
        return PrecisionCheck(expression, changed)
