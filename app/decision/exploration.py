"""Read-only probes the model runs before it decides, and what it is told about them.

A model that cannot see the data guesses or asks: whether a value is stored, how a
column is formatted, whether the tie it would ask about occurs at all. A probe lets
it look first. Every probe goes through the same gates as an answer (parse, policy,
authority, grain, cost) under the run's authority, runs as the reader role, and is
recorded in the run's trace by its SQL and outcome only.

What comes back follows the connection's value sharing. Rows are returned only when
the source shares stored values and every column the probe reads may be shared;
otherwise the model hears the row count and, per column, how many distinct and NULL
values came back. A rejected probe reports the runtime's own reason, a failed one
its error class; database text never goes back.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal, Protocol

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from app.catalog.models import SchemaCatalog, Sensitivity

PROBE_ROWS = 20
_VALUE_LENGTH = 80


class ColumnSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    distinct: int
    nulls: int


class ProbeReport(BaseModel):
    """One probe's outcome as the model sees it."""

    model_config = ConfigDict(frozen=True)

    sql: str
    status: Literal["ROWS", "REJECTED", "FAILED"]
    reason: str | None = None
    message: str | None = None
    row_count: int | None = None
    more_rows: bool = False
    columns: tuple[ColumnSummary, ...] = ()
    rows: tuple[tuple[str | None, ...], ...] | None = None


class Exploration(BaseModel):
    """What the model has learned so far, and whether it may look again.

    ``returned_without_looking`` names a question or refusal the model returned
    before it ran any probe; the request then asks it to check the data first.
    """

    model_config = ConfigDict(frozen=True)

    open: bool
    results: tuple[ProbeReport, ...] = ()
    returned_without_looking: Literal["NEEDS_CLARIFICATION", "BLOCKED_AUTHORITY"] | None = None


class ValueAccess(Protocol):
    def shares(self, table: str, column: str) -> bool: ...


class NoValueAccess:
    """A source whose stored values are never shared: probes report counts only."""

    def shares(self, table: str, column: str) -> bool:
        del table, column
        return False


def rows_may_be_shared(sql: str, catalog: SchemaCatalog, access: ValueAccess) -> bool:
    """Whether every column the probe reads may show its stored values."""
    try:
        expression = sqlglot.parse_one(sql, read="postgres")
    except Exception:  # an unreadable probe never shares
        return False
    if expression is None:
        return False
    ctes = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    tables = [
        table
        for name in {t.name.lower() for t in expression.find_all(exp.Table)} - ctes
        if (table := catalog.get_table(name)) is not None
    ]
    if not tables:
        return False

    def shareable(table_name: str, column_name: str) -> bool:
        table = catalog.get_table(table_name)
        column = table.get_column(column_name) if table is not None else None
        if table is None or column is None:
            return True  # not a stored column of this table: an alias or another table's
        return column.sensitivity is Sensitivity.PUBLIC and access.shares(table.name, column.name)

    # COUNT(*) reads no column's values; a star anywhere else may show every column.
    if any(not isinstance(star.parent, exp.Count) for star in expression.find_all(exp.Star)):
        if not all(shareable(t.name, c.name) for t in tables for c in t.columns):
            return False
    for column in expression.find_all(exp.Column):
        if not all(shareable(table.name, column.name) for table in tables):
            return False
    return True


def report_rows(
    sql: str,
    columns: list[str],
    rows: list[dict[str, object]],
    *,
    truncated: bool,
    shared: bool,
) -> ProbeReport:
    summaries = tuple(
        ColumnSummary(
            name=name,
            distinct=len({_text(row.get(name)) for row in rows if row.get(name) is not None}),
            nulls=sum(1 for row in rows if row.get(name) is None),
        )
        for name in columns
    )
    shown = (
        tuple(tuple(_text(row.get(name)) for name in columns) for row in rows[:PROBE_ROWS])
        if shared
        else None
    )
    return ProbeReport(
        sql=sql,
        status="ROWS",
        row_count=len(rows),
        more_rows=truncated,
        columns=summaries,
        rows=shown,
    )


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime | dt.date | dt.time):
        text = value.isoformat()
    elif isinstance(value, Decimal):
        text = format(value, "f")
    else:
        text = str(value)
    return text if len(text) <= _VALUE_LENGTH else text[: _VALUE_LENGTH - 1] + "…"
