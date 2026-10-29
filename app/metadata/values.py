"""Stored spellings of categorical text columns, read from planner statistics.

A question about ``'premium'`` stations fails silently when the column stores
``'Premium'``; a filter on ``'2012/01'`` misses dates stored as ``'2012-01-15'``.
The model can only spell values the way the data does if it sees them.  These
hints come from ``pg_stats`` (or a bounded sample of a small table nobody has
analyzed), never from rows a question returned, and leave out columns whose
names suggest personal data.

Values are real data and the model may run at an outside provider, so they are
shared only when an administrator turns sharing on for the connection, and never
for the columns they exclude.  Without that, the model sees only the shape of
code and date columns (``9999-99-99``), which carries no stored value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.catalog.models import SchemaCatalog
from app.metadata.relationships import type_family
from app.metadata.tables import value_sharing

MAX_VALUES = 30
MAX_VALUE_LENGTH = 60
MAX_FORMAT_EXAMPLES = 3
# Most-common values covering this share of rows (nulls included) are the whole domain.
COMPLETE_COVERAGE = 0.99

_PERSONAL = re.compile(
    r"(e_?mail|phone|mobile|address|street|zip|postal|ssn|social_?security|passport|"
    r"password|passwd|secret|token|iban|card_?num|account_?num|first_?name|last_?name|"
    r"full_?name|surname|birth|dob)",
    re.IGNORECASE,
)
# Codes and dates stored as text: digits with separators, no words.
_FORMAT_LIKE = re.compile(r"^[0-9][0-9:/\-. T+]*$")


@dataclass(frozen=True)
class ColumnStats:
    """One ``pg_stats`` row, or the same shape built from a sample."""

    table: str
    column: str
    null_frac: float
    most_common_vals: Sequence[str] | None
    most_common_freqs: Sequence[float] | None
    histogram_bounds: Sequence[str] | None
    # pg_stats convention: a positive count, or a negative share of rows when it grows
    # with the table.  A mostly-null column can look covered while holding many values.
    n_distinct: float = 0.0
    # The table's row estimate, which turns a negative n_distinct into a count.
    rows: float = 0.0
    # Exact share of empty or blank strings, when the column was counted in full.
    blank_frac: float | None = None
    # The counted non-NULL values and their row counts, when the column was counted.
    sample: tuple[tuple[str, int], ...] | None = None

    def distinct_count(self) -> float:
        if self.n_distinct >= 0:
            return self.n_distinct
        return -self.n_distinct * self.rows


@dataclass(frozen=True)
class KnownValues:
    values: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class ColumnProfile:
    """Shares of rows with no value, and of values joining several items.

    Shares and a separator character, never a stored value.
    """

    null_share: float
    blank_share: float
    separator: str = ""
    separated_share: float = 0.0


# Shares below one percent are left out; they rarely change an answer and cost tokens.
MIN_SHARE = 0.01
# A separator in fewer values than this is punctuation, not a list.
MIN_SEPARATED = 0.1
_SEPARATORS = (",", ";", "|")


def _value_share(stats: ColumnStats, test: Callable[[str], bool]) -> float:
    """Estimated share of non-NULL rows whose value passes the test.

    Exact for a counted column; otherwise the most common values by their
    frequencies, and the histogram bounds standing for the remaining rows.
    """
    if stats.sample is not None:
        total = sum(count for _, count in stats.sample)
        return sum(count for value, count in stats.sample if test(value)) / total if total else 0.0
    present = 1.0 - stats.null_frac
    if present <= 0:
        return 0.0
    common = list(zip(stats.most_common_vals or (), stats.most_common_freqs or (), strict=False))
    share = sum(frequency for value, frequency in common if test(value))
    rest = max(present - sum(frequency for _, frequency in common), 0.0)
    bounds = stats.histogram_bounds or ()
    if bounds:
        share += rest * sum(1 for value in bounds if test(value)) / len(bounds)
    return min(share / present, 1.0)


def _separated(stats: ColumnStats) -> tuple[str, float]:
    """The separator most short values of a text column join items with, and its share."""
    values = [*(stats.most_common_vals or ()), *(stats.histogram_bounds or ())]
    if stats.sample is not None:
        values = [value for value, _ in stats.sample]
    lengths = sorted(len(value) for value in values)
    if not lengths or lengths[len(lengths) // 2] > MAX_VALUE_LENGTH:
        return "", 0.0  # free text uses commas as punctuation

    def holds(mark: str) -> Callable[[str], bool]:
        return lambda value: mark in value

    found = max(
        ((mark, _value_share(stats, holds(mark))) for mark in _SEPARATORS),
        key=lambda item: item[1],
    )
    return found if found[1] >= MIN_SEPARATED else ("", 0.0)


def column_profile(stats: ColumnStats, *, text: bool = False) -> ColumnProfile | None:
    """How much of a column is NULL or an empty string, when that is at least 1%, and
    for a text column, which separator joins several items in at least 10% of values."""
    blank = stats.blank_frac
    if blank is None:
        blank = sum(
            frequency
            for value, frequency in zip(
                stats.most_common_vals or (), stats.most_common_freqs or (), strict=False
            )
            if not value.strip()
        )
    null_share = round(stats.null_frac, 2) if stats.null_frac >= MIN_SHARE else 0.0
    blank_share = round(blank, 2) if blank >= MIN_SHARE else 0.0
    separator, separated = _separated(stats) if text else ("", 0.0)
    if not null_share and not blank_share and not separator:
        return None
    return ColumnProfile(null_share, blank_share, separator, round(separated, 2))


def apply_profiles(
    catalog: SchemaCatalog, profiles: dict[tuple[str, str], ColumnProfile]
) -> SchemaCatalog:
    """Attach NULL and blank shares to the catalog's columns."""
    if not profiles:
        return catalog
    return SchemaCatalog(
        tables=tuple(
            table.model_copy(
                update={
                    "columns": tuple(
                        column.model_copy(
                            update={
                                "null_share": profiles[table.name, column.name].null_share,
                                "blank_share": profiles[table.name, column.name].blank_share,
                                "separator": profiles[table.name, column.name].separator,
                                "separated_share": (
                                    profiles[table.name, column.name].separated_share
                                ),
                            }
                        )
                        if (table.name, column.name) in profiles
                        else column
                        for column in table.columns
                    )
                }
            )
            for table in catalog.tables
        )
    )


def eligible_columns(catalog: SchemaCatalog) -> set[tuple[str, str]]:
    """Text columns whose names do not suggest personal data.

    Keys are left out: their values are identifiers, which say nothing about spelling.
    """
    keys = {
        pair
        for table in catalog.tables
        for relationship in table.relationships
        for pair in (
            (table.name, relationship.column),
            (relationship.referenced_table, relationship.referenced_column),
        )
    }
    return {
        (table.name, column.name)
        for table in catalog.tables
        for column in table.columns
        if column.queryable
        and not column.primary_key
        and (table.name, column.name) not in keys
        and type_family(column.type) == "text"
        and not _PERSONAL.search(column.name)
    }


def known_values(stats: ColumnStats) -> KnownValues | None:
    """A complete small domain, or a few format examples of a code/date column."""
    common = list(stats.most_common_vals or ())
    if common and all(len(value) <= MAX_VALUE_LENGTH for value in common):
        coverage = sum(stats.most_common_freqs or ()) + stats.null_frac
        if (
            len(common) <= MAX_VALUES
            and coverage >= COMPLETE_COVERAGE
            and 0 < round(stats.distinct_count()) <= len(common)
        ):
            return KnownValues(tuple(sorted(common)), complete=True)
    examples = [*common, *(stats.histogram_bounds or ())]
    if examples and all(_FORMAT_LIKE.match(value) for value in examples):
        spread = _spread(sorted(set(examples)), MAX_FORMAT_EXAMPLES)
        return KnownValues(tuple(spread), complete=False)
    return None


def sample_stats(table: str, column: str, counts: Iterable[tuple[str | None, int]]) -> ColumnStats:
    """Stats-shaped summary of a fully read small column: value counts, nulls included."""
    pairs = list(counts)
    total = sum(count for _, count in pairs)
    present = [(value, count) for value, count in pairs if value is not None]
    nulls = total - sum(count for _, count in present)
    present.sort(key=lambda item: -item[1])
    blank = sum(count for value, count in present if not value.strip()) / total if total else 0.0
    if len(present) > MAX_VALUES:
        # Too many to be a domain; keep them only as format evidence.
        return ColumnStats(
            table,
            column,
            nulls / total if total else 0.0,
            None,
            None,
            [value for value, _ in present],
            n_distinct=len(present),
            blank_frac=blank,
            sample=tuple(present),
        )
    return ColumnStats(
        table=table,
        column=column,
        null_frac=nulls / total if total else 0.0,
        most_common_vals=[value for value, _ in present],
        most_common_freqs=[count / total for _, count in present] if total else [],
        histogram_bounds=None,
        n_distinct=len(present),
        blank_frac=blank,
        sample=tuple(present),
    )


def value_patterns(values: Iterable[str]) -> tuple[str, ...]:
    """The shapes of code or date values, with every digit shown as 9."""
    items = list(values)
    if not items or not all(_FORMAT_LIKE.match(value) for value in items):
        return ()
    return tuple(sorted({re.sub(r"[0-9]", "9", value) for value in items})[:MAX_FORMAT_EXAMPLES])


class ValueSharing(BaseModel):
    """An administrator's choice to share stored values of one connection with the model."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    # "table.column" names whose values are never shared.
    excluded: tuple[str, ...] = ()
    updated_by: str | None = None
    updated_at: datetime | None = None

    def shares(self, table: str, column: str) -> bool:
        return self.enabled and f"{table}.{column}" not in self.excluded


class ValueSharingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    excluded: list[str] = Field(default_factory=list, max_length=2000)


class ValueColumn(BaseModel):
    """What the model would see for one column, so an administrator can decide."""

    table: str
    column: str
    values: tuple[str, ...]
    complete: bool
    patterns: tuple[str, ...]
    shared: bool


class ConnectionValues(BaseModel):
    # False when the server turned value hints off for every connection.
    available: bool
    enabled: bool
    excluded: tuple[str, ...]
    updated_by: str | None = None
    updated_at: datetime | None = None
    columns: list[ValueColumn]


class ValueSharingRejectedError(ValueError):
    pass


class ValueSharingStore(Protocol):
    def get(self, connection_id: str) -> ValueSharing: ...

    def set(
        self, connection_id: str, enabled: bool, excluded: tuple[str, ...], actor: str
    ) -> ValueSharing: ...

    def delete(self, connection_id: str) -> None: ...


class InMemoryValueSharingStore:
    def __init__(self) -> None:
        self._items: dict[str, ValueSharing] = {}

    def get(self, connection_id: str) -> ValueSharing:
        return self._items.get(connection_id, ValueSharing())

    def set(
        self, connection_id: str, enabled: bool, excluded: tuple[str, ...], actor: str
    ) -> ValueSharing:
        item = ValueSharing(
            enabled=enabled, excluded=excluded, updated_by=actor, updated_at=datetime.now(UTC)
        )
        self._items[connection_id] = item
        return item

    def delete(self, connection_id: str) -> None:
        self._items.pop(connection_id, None)


class SqlValueSharingStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, connection_id: str) -> ValueSharing:
        query = select(value_sharing).where(value_sharing.c.connection_id == connection_id)
        with self.engine.connect() as connection:
            row = connection.execute(query).mappings().first()
        if row is None:
            return ValueSharing()
        return ValueSharing(
            enabled=row["enabled"],
            excluded=tuple(row["excluded"] or ()),
            updated_by=row["updated_by"],
            updated_at=row["updated_at"],
        )

    def set(
        self, connection_id: str, enabled: bool, excluded: tuple[str, ...], actor: str
    ) -> ValueSharing:
        values = {
            "enabled": enabled,
            "excluded": list(excluded),
            "updated_by": actor,
            "updated_at": datetime.now(UTC),
        }
        statement = insert(value_sharing).values(connection_id=connection_id, **values)
        with self.engine.begin() as connection:
            connection.execute(
                statement.on_conflict_do_update(index_elements=["connection_id"], set_=values)
            )
        return self.get(connection_id)

    def delete(self, connection_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(value_sharing).where(value_sharing.c.connection_id == connection_id)
            )


def apply_known_values(
    catalog: SchemaCatalog,
    hints: dict[tuple[str, str], KnownValues],
    sharing: ValueSharing | None = None,
) -> SchemaCatalog:
    """Attach shared values, or only the value shapes, to the catalog's columns.

    Complete value sets are always attached as ``value_domain`` for the runtime
    literal check, which never leaves the server.
    """
    sharing = sharing or ValueSharing()
    updates: dict[tuple[str, str], dict[str, object]] = {}
    for (table, column), hint in hints.items():
        update: dict[str, object] = {"value_patterns": value_patterns(hint.values)}
        if hint.complete:
            update["value_domain"] = hint.values
        if sharing.shares(table, column):
            update.update(known_values=hint.values, known_values_complete=hint.complete)
        if update["value_patterns"] or len(update) > 1:
            updates[(table, column)] = update
    if not updates:
        return catalog
    return SchemaCatalog(
        tables=tuple(
            table.model_copy(
                update={
                    "columns": tuple(
                        column.model_copy(update=change)
                        if (change := updates.get((table.name, column.name))) is not None
                        else column
                        for column in table.columns
                    )
                }
            )
            for table in catalog.tables
        )
    )


def _spread(values: list[str], count: int) -> list[str]:
    """First, middle, and last of a sorted list: the range and the shape."""
    if len(values) <= count:
        return values
    step = (len(values) - 1) / (count - 1)
    return [values[round(index * step)] for index in range(count)]
