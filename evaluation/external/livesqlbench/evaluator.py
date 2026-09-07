"""Offline evaluator adapter matching LiveSQLBench Query Soft-EX result semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass(frozen=True)
class LiveSqlBenchResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def _normalize(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def normalize_rows(result: LiveSqlBenchResult) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(_normalize(value) for value in row) for row in result.rows)


def soft_ex_match(
    predicted: LiveSqlBenchResult, expected: LiveSqlBenchResult, *, ordered: bool
) -> bool:
    """Port the official Query default's normalized ordered/set comparison."""
    pred = normalize_rows(predicted)
    truth = normalize_rows(expected)
    if not pred or not truth:
        return False
    return pred == truth if ordered else set(pred) == set(truth)


def summarize_result(result: LiveSqlBenchResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "columns": list(result.columns),
        "row_count": len(result.rows),
        "first_row": list(result.rows[0]) if result.rows else None,
    }


def evaluate_reference_available(case: Any) -> bool:
    """Public Base-Lite rows intentionally omit GT/test cases; fail closed when absent."""
    return bool(getattr(case, "sol_sql", ())) and bool(getattr(case, "test_cases", ()))
