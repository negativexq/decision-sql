from __future__ import annotations

from time import perf_counter
from typing import Any

import pymysql

from app.metadata.connectors import (
    ConnectionConfig,
    ConnectionTestResult,
    IntrospectedTable,
    connected_message,
    failure,
    group_columns,
)

_SSL: dict[str, dict[str, Any] | None] = {
    "disable": None,
    "require": {"check_hostname": False, "verify_mode": "none"},
    "verify-full": {"check_hostname": True, "verify_mode": "required"},
    "prefer": {"check_hostname": False, "verify_mode": "none"},
}

# A user can write if any global, schema or table grant allows it.
_CAN_WRITE = """
SELECT EXISTS (
  SELECT 1 FROM information_schema.user_privileges
  WHERE grantee = %(grantee)s AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
) OR EXISTS (
  SELECT 1 FROM information_schema.schema_privileges
  WHERE grantee = %(grantee)s AND table_schema = %(schema)s
    AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
) OR EXISTS (
  SELECT 1 FROM information_schema.table_privileges
  WHERE grantee = %(grantee)s AND table_schema = %(schema)s
    AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
)
"""


class MySqlConnector:
    def _connect(self, config: ConnectionConfig, password: str | None) -> Any:
        connection = pymysql.connect(
            host=config.host,
            port=config.port or 3306,
            user=config.username,
            password=password or "",
            database=config.database,
            connect_timeout=5,
            read_timeout=15,
            ssl=_SSL.get(config.ssl_mode),
            ssl_disabled=config.ssl_mode == "disable",
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        return connection

    def test(self, config: ConnectionConfig, password: str | None) -> ConnectionTestResult:
        started = perf_counter()
        try:
            connection = self._connect(config, password)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT VERSION(), CURRENT_USER()")
                    version, current_user = cursor.fetchone()
                    user, _, host = str(current_user).partition("@")
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                        (config.database,),
                    )
                    (table_count,) = cursor.fetchone()
                    cursor.execute(
                        _CAN_WRITE, {"grantee": f"'{user}'@'{host}'", "schema": config.database}
                    )
                    (writable,) = cursor.fetchone()
            finally:
                connection.close()
        except pymysql.MySQLError as error:
            return failure(_mysql_message(error))
        return ConnectionTestResult(
            ok=True,
            message=connected_message(
                int(table_count), f"database {config.database}", bool(writable)
            ),
            server_version=str(version),
            latency_ms=(perf_counter() - started) * 1000,
            table_count=int(table_count),
            can_write=bool(writable),
        )

    def introspect(self, config: ConnectionConfig, password: str | None) -> list[IntrospectedTable]:
        connection = self._connect(config, password)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name, column_name, column_type, is_nullable = 'YES' "
                    "FROM information_schema.columns WHERE table_schema = %s "
                    "ORDER BY table_name, ordinal_position",
                    (config.database,),
                )
                columns = cursor.fetchall()
                cursor.execute(
                    "SELECT table_name, table_rows FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                    (config.database,),
                )
                estimates = {str(name): rows for name, rows in cursor.fetchall()}
        finally:
            connection.close()
        return group_columns(columns, estimates)


def _mysql_message(error: Exception) -> str:
    # PyMySQL errors are (code, message) tuples.
    return str(error.args[1]) if len(error.args) > 1 else str(error)


CONNECTOR = MySqlConnector()
