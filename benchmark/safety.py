from __future__ import annotations

import re
from typing import Any

import psycopg
import sqlglot
from psycopg.rows import tuple_row
from sqlglot import exp


class SqlAdmissionError(ValueError):
    pass


def validate_read_only_select(sql: str) -> None:
    if not sql or not sql.strip():
        raise SqlAdmissionError("EMPTY_SQL")
    if len(sqlglot.parse(sql, read="postgres")) != 1:
        raise SqlAdmissionError("MULTIPLE_STATEMENTS")
    try:
        statement = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:  # pragma: no cover - parser error is surfaced as a stable code
        raise SqlAdmissionError("SQL_PARSE_ERROR") from exc
    if not isinstance(statement, exp.Query):
        raise SqlAdmissionError("NOT_READ_ONLY_SELECT")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command)
    if any(isinstance(node, forbidden) for node in statement.walk()):
        raise SqlAdmissionError("WRITE_OR_DDL")
    if re.search(r"\b(for\s+update|for\s+share)\b", sql, flags=re.IGNORECASE):
        raise SqlAdmissionError("LOCKING_READ")


def execute_query(
    connection_kwargs: dict[str, Any],
    search_path: str,
    sql: str,
    *,
    patch_sql: list[str] | None = None,
    timeout_ms: int = 3000,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    validate_read_only_select(sql)
    with psycopg.connect(**connection_kwargs, row_factory=tuple_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN")
            cursor.execute("SET LOCAL default_transaction_read_only = on")
            cursor.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            cursor.execute("SET LOCAL timezone = 'UTC'")
            cursor.execute("SET LOCAL datestyle = 'ISO, YMD'")
            cursor.execute("SELECT set_config('search_path', %s, true)", (search_path,))
            for patch in patch_sql or []:
                cursor.execute(patch)
            cursor.execute(sql)
            rows = cursor.fetchall()
            description = cursor.description or ()
            columns = [str(item.name) for item in description]
        connection.rollback()
    return columns, rows
