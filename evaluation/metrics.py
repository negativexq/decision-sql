import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlglot import exp, parse_one

from app.sql.models import QueryExecution


class ResultDiagnostic(StrEnum):
    """Non-scoring diagnostics attached to an otherwise equivalent result."""

    STRUCTURAL_DIVERGENCE = "STRUCTURAL_DIVERGENCE"


@dataclass(frozen=True)
class ResultComparison:
    """Result-equivalence outcome plus bounded diagnostic metadata."""

    equivalent: bool
    diagnostic: ResultDiagnostic | None = None


def compare_query_results(
    actual: QueryExecution,
    expected: QueryExecution,
    *,
    order_sensitive: bool = False,
    numeric_tolerance: float = 1e-6,
) -> bool:
    """Compare result values by ordinal output position.

    Output aliases are deliberately excluded from the correctness contract. The
    caller may use :func:`assess_query_results` when a structural diagnostic is
    also needed.
    """
    return assess_query_results(
        actual,
        expected,
        order_sensitive=order_sensitive,
        numeric_tolerance=numeric_tolerance,
    ).equivalent


def assess_query_results(
    actual: QueryExecution,
    expected: QueryExecution,
    *,
    order_sensitive: bool = False,
    numeric_tolerance: float = 1e-6,
    actual_sql: str | None = None,
    expected_sql: str | None = None,
) -> ResultComparison:
    """Compare results and optionally report structural SQL divergence.

    Result values are matched by ordinal position after equal arity is verified.
    For unordered benchmark items, rows are compared as sorted multisets, so
    duplicate multiplicity is preserved. Structural divergence never changes
    the primary equivalence result.
    """
    if actual.truncated != expected.truncated:
        return ResultComparison(False)
    if len(actual.columns) != len(expected.columns):
        return ResultComparison(False)
    actual_rows = [_canonical_row(actual, row) for row in actual.rows]
    expected_rows = [_canonical_row(expected, row) for row in expected.rows]
    if not order_sensitive:
        actual_rows.sort(key=repr)
        expected_rows.sort(key=repr)
    if len(actual_rows) != len(expected_rows):
        return ResultComparison(False)
    equivalent = all(
        _values_equal(actual_row, expected_row, numeric_tolerance)
        for actual_row, expected_row in zip(actual_rows, expected_rows, strict=True)
    )
    if not equivalent:
        return ResultComparison(False)
    diagnostic = None
    if (
        actual_sql
        and expected_sql
        and _structural_signature(actual_sql) != _structural_signature(expected_sql)
    ):
        diagnostic = ResultDiagnostic.STRUCTURAL_DIVERGENCE
    return ResultComparison(True, diagnostic)


def _canonical_row(execution: QueryExecution, row: dict[str, object]) -> tuple[object, ...]:
    return tuple(_canonical_value(row.get(column)) for column in execution.columns)


def _canonical_value(value: object) -> object:
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _values_equal(actual: Any, expected: Any, tolerance: float) -> bool:
    if isinstance(actual, float) and isinstance(expected, (int, float)):
        return math.isclose(actual, float(expected), rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(expected, float) and isinstance(actual, (int, float)):
        return math.isclose(float(actual), expected, rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(actual, tuple) and isinstance(expected, tuple):
        return len(actual) == len(expected) and all(
            _values_equal(left, right, tolerance)
            for left, right in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _structural_signature(sql: str) -> tuple[tuple[str, ...], bool]:
    """Return bounded material structure, excluding harmless naming differences."""
    tree = parse_one(sql, dialect="postgres")
    cte_names = {
        cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
    }
    tables = tuple(
        sorted(
            {
                table.name.lower()
                for table in tree.find_all(exp.Table)
                if table.name.lower() not in cte_names
            }
        )
    )
    aggregate_kinds = {type(function).__name__.lower() for function in tree.find_all(exp.AggFunc)}
    average_sum_difference = ("avg" in aggregate_kinds and "sum" in aggregate_kinds) or (
        "avg" in aggregate_kinds and "sum" not in aggregate_kinds
    )
    return tables, average_sum_difference
