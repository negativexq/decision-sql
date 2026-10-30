from datetime import UTC, datetime

from sqlalchemy import Connection, Engine

from app.sql.models import QueryExecution, QueryPlan, SqlExecutionError


class ReaderRoleError(RuntimeError):
    pass


# SQLSTATE codes of failures the query's author can fix, by the fixed name the model
# hears.  Every other failure, and every database message, stays unnamed.
_ERROR_CLASSES = {
    "22P02": "INVALID_TEXT_CONVERSION",
    "22003": "NUMERIC_OUT_OF_RANGE",
    "22012": "DIVISION_BY_ZERO",
    "22007": "INVALID_DATETIME_FORMAT",
    "22008": "DATETIME_OUT_OF_RANGE",
    "21000": "SUBQUERY_RETURNED_SEVERAL_ROWS",
    "42804": "TYPE_MISMATCH",
    "42883": "NO_SUCH_FUNCTION_FOR_TYPES",
    "57014": "STATEMENT_TIMEOUT",
}


def unique_columns(names: list[str]) -> list[str]:
    """Result column names made distinct, so no value is lost when rows become dicts.

    PostgreSQL names every unaliased SUM ``sum``; the second becomes ``sum_2``.
    """
    taken = set(names)
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        candidate, suffix = name, 2
        while candidate in seen or (candidate != name and candidate in taken):
            candidate = f"{name}_{suffix}"
            suffix += 1
        seen.add(candidate)
        unique.append(candidate)
    return unique


def error_class(error: BaseException) -> str | None:
    """The fixed name of a fixable database failure, from its SQLSTATE only."""
    code = getattr(getattr(error, "orig", error), "sqlstate", None)
    return _ERROR_CLASSES.get(code) if isinstance(code, str) else None


class ReadOnlyExecutor:
    def __init__(
        self, engine: Engine, statement_timeout_ms: int, max_rows: int, reader_role: str
    ) -> None:
        self.engine = engine
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
        self.reader_role = reader_role

    def _execute_on_connection(
        self, connection: Connection, plan: QueryPlan
    ) -> QueryExecution | SqlExecutionError:
        """Execute after SqlSafetyService has established plan acceptance and EXPLAIN."""
        if not isinstance(plan, QueryPlan):
            return SqlExecutionError(error="Restricted execution requires an accepted QueryPlan.")
        try:
            # psycopg treats percent signs as DB-API placeholder syntax at
            # this driver boundary.  Escape only the transport copy; the
            # accepted QueryPlan and its normalized SQL remain unchanged.
            transport_sql = plan.normalized_sql.replace("%", "%%")
            executed_at_utc = datetime.now(UTC)
            session_timezone = str(connection.exec_driver_sql("SHOW TIME ZONE").scalar_one())
            result = connection.exec_driver_sql(transport_sql)
            rows = result.fetchmany(self.max_rows + 1)
            truncated = len(rows) > self.max_rows
            bounded_rows = rows[: self.max_rows]
            columns = unique_columns(list(result.keys()))
            return QueryExecution(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                columns=columns,
                rows=[dict(zip(columns, row, strict=True)) for row in bounded_rows],
                row_count=len(bounded_rows),
                truncated=truncated,
                latency_ms=0.0,
                executed_at_utc=executed_at_utc,
                session_timezone=session_timezone,
            )
        except Exception as error:
            return SqlExecutionError(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                error="Candidate SQL could not be executed by the restricted reader.",
                error_class=error_class(error),
            )

    def configure_transaction(self, connection: Connection) -> None:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {self.statement_timeout_ms}")
        current_user: object = connection.exec_driver_sql("SELECT current_user").scalar_one()
        if current_user != self.reader_role:
            raise ReaderRoleError("Candidate SQL execution requires the configured reader role.")
