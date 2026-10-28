from __future__ import annotations

from time import perf_counter
from typing import Any

import pymssql

from app.metadata.connectors import (
    ConnectionConfig,
    ConnectionTestResult,
    IntrospectedTable,
    connected_message,
    failure,
    group_columns,
    missing_schema,
)


class SqlServerConnector:
    def _connect(self, config: ConnectionConfig, password: str | None) -> Any:
        return pymssql.connect(
            server=config.host,
            port=str(config.port or 1433),
            user=config.username,
            password=password or "",
            database=config.database,
            login_timeout=5,
            timeout=15,
            appname="decision-sql",
        )

    def test(self, config: ConnectionConfig, password: str | None) -> ConnectionTestResult:
        schema = config.schema_name or "dbo"
        started = perf_counter()
        try:
            connection = self._connect(config, password)
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128))")
                (version,) = cursor.fetchone()
                cursor.execute(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                    (schema,),
                )
                if not cursor.fetchone()[0]:
                    return missing_schema(schema)
                cursor.execute(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'",
                    (schema,),
                )
                (table_count,) = cursor.fetchone()
                cursor.execute(
                    "SELECT COUNT(*) FROM fn_my_permissions(NULL, 'DATABASE') "
                    "WHERE permission_name IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER')"
                )
                (writable,) = cursor.fetchone()
            finally:
                connection.close()
        except pymssql.Error as error:
            return failure(_mssql_message(error))
        return ConnectionTestResult(
            ok=True,
            message=connected_message(int(table_count), f"schema {schema}", bool(writable)),
            server_version=str(version),
            latency_ms=(perf_counter() - started) * 1000,
            table_count=int(table_count),
            can_write=bool(writable),
        )

    def introspect(self, config: ConnectionConfig, password: str | None) -> list[IntrospectedTable]:
        schema = config.schema_name or "dbo"
        connection = self._connect(config, password)
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, "
                "CASE WHEN IS_NULLABLE = 'YES' THEN 1 ELSE 0 END "
                "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (schema,),
            )
            columns = cursor.fetchall()
            cursor.execute(
                "SELECT t.name, SUM(p.rows) FROM sys.tables t "
                "JOIN sys.schemas s ON s.schema_id = t.schema_id "
                "JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
                "WHERE s.name = %s GROUP BY t.name",
                (schema,),
            )
            estimates = {str(name): rows for name, rows in cursor.fetchall()}
        finally:
            connection.close()
        return group_columns(columns, estimates)


def _mssql_message(error: Exception) -> str:
    # pymssql wraps server messages as (code, bytes); decode the message part.
    if error.args and isinstance(error.args[0], tuple) and len(error.args[0]) > 1:
        detail = error.args[0][1]
        return detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
    return str(error)


CONNECTOR = SqlServerConnector()
