from __future__ import annotations

from time import perf_counter
from typing import Any

import snowflake.connector
from snowflake.connector.errors import Error as SnowflakeError

from app.metadata.connectors import (
    ConnectionConfig,
    ConnectionTestResult,
    IntrospectedTable,
    connected_message,
    failure,
    group_columns,
    missing_schema,
)


class SnowflakeConnector:
    """Write access depends on role grants; it is reported as unknown."""

    def _connect(self, config: ConnectionConfig, password: str | None) -> Any:
        return snowflake.connector.connect(
            account=config.host,
            user=config.username,
            password=password,
            database=config.database,
            schema=config.schema_name or "PUBLIC",
            warehouse=config.option("warehouse") or None,
            role=config.option("role") or None,
            login_timeout=15,
            network_timeout=30,
            application="decision-sql",
        )

    def test(self, config: ConnectionConfig, password: str | None) -> ConnectionTestResult:
        schema = (config.schema_name or "PUBLIC").upper()
        started = perf_counter()
        try:
            connection = self._connect(config, password)
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT CURRENT_VERSION()")
                (version,) = cursor.fetchone()
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s",
                    (schema,),
                )
                if not cursor.fetchone()[0]:
                    return missing_schema(schema)
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                    (schema,),
                )
                (table_count,) = cursor.fetchone()
            finally:
                connection.close()
        except SnowflakeError as error:
            return failure(error)
        return ConnectionTestResult(
            ok=True,
            message=connected_message(int(table_count), f"{config.database}.{schema}", None),
            server_version=str(version),
            latency_ms=(perf_counter() - started) * 1000,
            table_count=int(table_count),
        )

    def introspect(self, config: ConnectionConfig, password: str | None) -> list[IntrospectedTable]:
        schema = (config.schema_name or "PUBLIC").upper()
        connection = self._connect(config, password)
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT table_name, column_name, data_type, is_nullable = 'YES' "
                "FROM information_schema.columns WHERE table_schema = %s "
                "ORDER BY table_name, ordinal_position",
                (schema,),
            )
            columns = cursor.fetchall()
            cursor.execute(
                "SELECT table_name, row_count FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                (schema,),
            )
            estimates = {str(name): rows for name, rows in cursor.fetchall()}
        finally:
            connection.close()
        return group_columns(columns, estimates)


CONNECTOR = SnowflakeConnector()
