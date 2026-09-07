"""Evaluation-only helpers for future LiveSQLBench result ledgers."""

from __future__ import annotations

from typing import Any

from evaluation.external.livesqlbench.evaluator import summarize_result
from evaluation.external.livesqlbench.loader import LiveSqlBenchCase


def failure_ledger_record(
    case: LiveSqlBenchCase,
    *,
    generated_sql: str | None,
    generated_result: Any = None,
    reference_result: Any = None,
    m1_status: str | None = None,
    execution_status: str | None = None,
    correctness: bool | None = None,
) -> dict[str, Any]:
    """Serialize diagnostic fields without making an automatic root-cause claim."""
    return {
        "case_id": case.instance_id,
        "database": case.selected_database,
        "question": case.query,
        "external_knowledge": list(case.external_knowledge),
        "generated_sql": generated_sql,
        "reference_sql_offline": list(case.sol_sql) if case.sol_sql else None,
        "generated_result_summary": summarize_result(generated_result),
        "reference_result_summary": summarize_result(reference_result),
        "m1_status": m1_status,
        "execution_status": execution_status,
        "correctness": correctness,
    }
