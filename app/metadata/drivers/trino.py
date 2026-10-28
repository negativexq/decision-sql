from __future__ import annotations

from time import perf_counter
from typing import Any

import trino
from trino.auth import BasicAuthentication

from app.metadata.connectors import (
    ConnectionConfig,
    ConnectionTestResult,
    IntrospectedTable,
    connected_message,
    failure,
    group_columns,
    missing_schema,
)


class TrinoConnector:
    """Trino has no portable privilege view, so write access is reported as unknown."""

    def _connect(self, config: ConnectionConfig, password: str | None) -> Any:
        scheme = config.option("http_scheme", "https")
        if password and scheme != "https":
            raise ValueError("Trino only accepts a password over HTTPS.")
        return trino.dbapi.connect(
            host=config.host,
            port=config.port or 8080,
            user=config.username,
            catalog=config.database,
            schema=config.schema_name or "default",
            http_scheme=scheme,
            auth=BasicAuthentication(config.username, password) if password else None,
            request_timeout=15,
            source="decision-sql",
        )

    def test(self, config: ConnectionConfig, password: str | None) -> ConnectionTestResult:
        schema = config.schema_name or "default"
        started = perf_counter()
        try:
            connection = self._connect(config, password)
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT node_version FROM system.runtime.nodes LIMIT 1")
                version = cursor.fetchone()
                cursor.execute(
                    "SELECT count(*) FROM information_schema.schemata WHERE schema_name = ?",
                    (schema,),
                )
                if not cursor.fetchone()[0]:
                    return missing_schema(schema)
                cursor.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = ? AND table_type = 'BASE TABLE'",
                    (schema,),
                )
                (table_count,) = cursor.fetchone()
            finally:
                connection.close()
        except Exception as error:  # trino surfaces HTTP and query errors with many types
            return failure(error)
        return ConnectionTestResult(
            ok=True,
            message=connected_message(int(table_count), f"{config.database}.{schema}", None),
            server_version=str(version[0]) if version else None,
            latency_ms=(perf_counter() - started) * 1000,
            table_count=int(table_count),
            can_write=None,
        )

    def introspect(self, config: ConnectionConfig, password: str | None) -> list[IntrospectedTable]:
        schema = config.schema_name or "default"
        connection = self._connect(config, password)
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT table_name, column_name, data_type, is_nullable = 'YES' "
                "FROM information_schema.columns WHERE table_schema = ? "
                "ORDER BY table_name, ordinal_position",
                (schema,),
            )
            columns = cursor.fetchall()
        finally:
            connection.close()
        return group_columns(columns, {})


CONNECTOR = TrinoConnector()
