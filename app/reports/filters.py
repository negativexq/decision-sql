"""Report filters: one choice narrows every card that reads the filtered table.

A filter names a catalog column, not SQL.  When a reader picks values, the server
adds the predicate to each SELECT of a card's pinned SQL that reads the column's
table directly, and the result goes through every gate like any saved query: a
literal is checked against the column's stored values there, and the audit log
records the SQL that ran.  Nothing the reader sends becomes SQL text except as a
quoted literal built by the parser.

A card whose SQL does not read the table is not filtered, and says so; the filter
does not follow relationships to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp

from app.catalog.models import SchemaCatalog
from app.sql.parser import SQLParseFailure, SQLParser
from app.sql.values import cte_names, direct_tables

MAX_FILTERS = 8
MAX_FILTER_VALUES = 100
_DATE_TYPES = ("date", "time")


class FilterKind(StrEnum):
    # The rows whose column holds one of the chosen values.
    VALUES = "values"
    # The rows whose date or timestamp falls on or between two days.
    DATE_RANGE = "date_range"


class ReportFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=80)
    table: str = Field(min_length=1, max_length=200)
    column: str = Field(min_length=1, max_length=200)
    kind: FilterKind


class FilterChoice(BaseModel):
    """What one reader picked for one filter; nothing picked means no filter."""

    model_config = ConfigDict(extra="forbid")

    values: list[str] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> FilterChoice:
        if self.start and self.end and self.start > self.end:
            raise ValueError("The range starts after it ends.")
        if any(len(value) > 200 for value in self.values):
            raise ValueError("A filter value is longer than 200 characters.")
        return self

    @property
    def empty(self) -> bool:
        return not self.values and self.start is None and self.end is None


@dataclass(frozen=True)
class FilteredSql:
    sql: str
    # Filters with a choice that narrowed the query.
    applied: tuple[str, ...]
    # Filters with a choice that the query could not take: it does not read the table.
    skipped: tuple[str, ...]


def check_filters(filters: list[ReportFilter], catalog: SchemaCatalog) -> None:
    """Refuse a filter on a column the catalog does not offer, or of the wrong kind."""
    if len({item.id for item in filters}) != len(filters):
        raise ValueError("Two filters share an id.")
    for item in filters:
        table = catalog.get_table(item.table)
        column = table.get_column(item.column) if table is not None and table.queryable else None
        if column is None or not column.queryable:
            raise ValueError(f"{item.table}.{item.column} is not a column this report can read.")
        if item.kind is FilterKind.DATE_RANGE and not any(
            word in column.type.lower() for word in _DATE_TYPES
        ):
            raise ValueError(f"{item.table}.{item.column} holds {column.type}, not dates.")


def _predicate(item: ReportFilter, choice: FilterChoice, alias: str) -> exp.Expression | None:
    column = exp.column(item.column, table=alias)
    if item.kind is FilterKind.VALUES:
        if not choice.values:
            return None
        return exp.In(this=column, expressions=[exp.Literal.string(v) for v in choice.values])
    parts: list[exp.Expression] = []
    if choice.start is not None:
        parts.append(exp.GTE(this=column, expression=_day(choice.start)))
    if choice.end is not None:
        # The end day is included, whether the column holds dates or timestamps.
        parts.append(exp.LT(this=column, expression=_day(choice.end + timedelta(days=1))))
    return exp.and_(*parts) if parts else None


def _day(value: date) -> exp.Expression:
    return exp.Cast(this=exp.Literal.string(value.isoformat()), to=exp.DataType.build("date"))


def _attach(select: exp.Select, alias: str, predicate: exp.Expression) -> bool:
    """Add the predicate where it narrows rows without changing the join's meaning."""
    joins = select.args.get("joins") or []
    sides = {str(join.args.get("side") or "").upper() for join in joins}
    if sides & {"RIGHT", "FULL"}:
        return False
    for join in joins:
        if (
            join.this.alias_or_name.lower() == alias
            and str(join.args.get("side")).upper() == "LEFT"
        ):
            # In WHERE it would drop the unmatched rows a LEFT JOIN keeps.
            join.set(
                "on", exp.and_(join.args["on"], predicate) if join.args.get("on") else predicate
            )
            return True
    select.where(predicate, append=True, copy=False)
    return True


def apply_filters(
    sql: str, filters: list[ReportFilter], choices: dict[str, FilterChoice]
) -> FilteredSql:
    active = [
        (item, choices[item.id])
        for item in filters
        if item.id in choices and not choices[item.id].empty
    ]
    if not active:
        return FilteredSql(sql=sql, applied=(), skipped=())
    try:
        expression = SQLParser().parse(sql).expression
    except SQLParseFailure:
        return FilteredSql(sql=sql, applied=(), skipped=tuple(item.id for item, _ in active))
    ctes = cte_names(expression)
    applied: list[str] = []
    skipped: list[str] = []
    for item, choice in active:
        narrowed = False
        for select in list(expression.find_all(exp.Select)):
            for alias, table in direct_tables(select, ctes).items():
                predicate = _predicate(item, choice, alias)
                if table == item.table.lower() and predicate is not None:
                    narrowed = _attach(select, alias, predicate) or narrowed
        (applied if narrowed else skipped).append(item.id)
    if not applied:
        return FilteredSql(sql=sql, applied=(), skipped=tuple(skipped))
    return FilteredSql(
        sql=expression.sql(dialect="postgres"), applied=tuple(applied), skipped=tuple(skipped)
    )


def filters_for(sql: str, filters: list[ReportFilter]) -> list[str]:
    """The filters that would narrow this query, whether or not anything is picked."""
    probe = {item.id: FilterChoice(values=["x"], start=date(2000, 1, 1)) for item in filters}
    return list(apply_filters(sql, filters, probe).applied)
