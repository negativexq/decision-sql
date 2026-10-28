"""Stored spellings of categorical text columns, read from planner statistics.

A question about ``'premium'`` stations fails silently when the column stores
``'Premium'``; a filter on ``'2012/01'`` misses dates stored as ``'2012-01-15'``.
The model can only spell values the way the data does if it sees them.  These
hints come from ``pg_stats`` (or a bounded sample of a small table nobody has
analyzed), never from rows a question returned, and leave out columns whose
names suggest personal data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.catalog.models import SchemaCatalog
from app.metadata.relationships import type_family

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

    def distinct_count(self) -> float:
        if self.n_distinct >= 0:
            return self.n_distinct
        return -self.n_distinct * self.rows


@dataclass(frozen=True)
class KnownValues:
    values: tuple[str, ...]
    complete: bool


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
        )
    return ColumnStats(
        table=table,
        column=column,
        null_frac=nulls / total if total else 0.0,
        most_common_vals=[value for value, _ in present],
        most_common_freqs=[count / total for _, count in present] if total else [],
        histogram_bounds=None,
        n_distinct=len(present),
    )


def apply_known_values(
    catalog: SchemaCatalog, hints: dict[tuple[str, str], KnownValues]
) -> SchemaCatalog:
    if not hints:
        return catalog
    return SchemaCatalog(
        tables=tuple(
            table.model_copy(
                update={
                    "columns": tuple(
                        column.model_copy(
                            update={
                                "known_values": hint.values,
                                "known_values_complete": hint.complete,
                            }
                        )
                        if (hint := hints.get((table.name, column.name))) is not None
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
