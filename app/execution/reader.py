from sqlalchemy import Connection, Engine

from app.sql.models import QueryExecution, QueryPlan, SqlExecutionError


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

    def _execute_on_connection(
        self, connection: Connection, plan: QueryPlan
    ) -> QueryExecution | SqlExecutionError:
        """Execute after SqlSafetyService has established plan acceptance and EXPLAIN."""
        if not isinstance(plan, QueryPlan):
            return SqlExecutionError(
                error="Restricted execution requires an accepted QueryPlan."
            )
        try:
            # psycopg treats percent signs as DB-API placeholder syntax at
            # this driver boundary.  Escape only the transport copy; the
            # accepted QueryPlan and its normalized SQL remain unchanged.
            transport_sql = plan.normalized_sql.replace("%", "%%")
            result = connection.exec_driver_sql(transport_sql)
            rows = result.fetchmany(self.max_rows + 1)
            truncated = len(rows) > self.max_rows
            bounded_rows = rows[: self.max_rows]
            return QueryExecution(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                columns=list(result.keys()),
                rows=[dict(row._mapping) for row in bounded_rows],
                row_count=len(bounded_rows),
                truncated=truncated,
                latency_ms=0.0,
            )
        except Exception:
            return SqlExecutionError(
                plan_id=plan.plan_id,
                correlation_id=plan.correlation_id,
                error="Candidate SQL could not be executed by the restricted reader.",
            )

    def configure_transaction(self, connection: Connection) -> None:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {self.statement_timeout_ms}")
        current_user = connection.exec_driver_sql("SELECT current_user").scalar_one()
        if current_user != self.reader_role:
            raise ReaderRoleError("Candidate SQL execution requires the configured reader role.")
