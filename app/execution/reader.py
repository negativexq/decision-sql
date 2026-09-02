from sqlalchemy import Connection, Engine

from app.models.domain import FailureStage
from app.sql.models import SqlExecutionResult, SqlSafetyStatus


class ReaderRoleError(RuntimeError):
    pass


class ReadOnlyExecutor:
    def __init__(
        self, engine: Engine, statement_timeout_ms: int, max_rows: int, reader_role: str
    ) -> None:
        self.engine = engine
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
        self.reader_role = reader_role

    def execute(self, sql: str) -> SqlExecutionResult:
        try:
            with self.engine.connect() as connection:
                with connection.begin():
                    self.configure_transaction(connection)
                    result = connection.exec_driver_sql(sql)
                    rows = result.fetchmany(self.max_rows + 1)
                    truncated = len(rows) > self.max_rows
                    bounded_rows = rows[: self.max_rows]
                    columns = list(result.keys())
                    return SqlExecutionResult(
                        status=SqlSafetyStatus.ALLOWED,
                        columns=columns,
                        rows=[dict(row._mapping) for row in bounded_rows],
                        row_count=len(bounded_rows),
                        truncated=truncated,
                    )
        except Exception:
            return SqlExecutionResult(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate SQL could not be executed by the restricted reader.",
            )

    def execute_on_connection(self, connection: Connection, sql: str) -> SqlExecutionResult:
        """Execute inside a caller-owned read-only transaction after EXPLAIN acceptance."""
        try:
            result = connection.exec_driver_sql(sql)
            rows = result.fetchmany(self.max_rows + 1)
            truncated = len(rows) > self.max_rows
            bounded_rows = rows[: self.max_rows]
            return SqlExecutionResult(
                status=SqlSafetyStatus.ALLOWED,
                columns=list(result.keys()),
                rows=[dict(row._mapping) for row in bounded_rows],
                row_count=len(bounded_rows),
                truncated=truncated,
            )
        except Exception:
            return SqlExecutionResult(
                status=SqlSafetyStatus.EXECUTION_ERROR,
                failure_stage=FailureStage.EXECUTION_ERROR,
                error="Candidate SQL could not be executed by the restricted reader.",
            )

    def configure_transaction(self, connection: Connection) -> None:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {self.statement_timeout_ms}")
        current_user = connection.exec_driver_sql("SELECT current_user").scalar_one()
        if current_user != self.reader_role:
            raise ReaderRoleError("Candidate SQL execution requires the configured reader role.")
