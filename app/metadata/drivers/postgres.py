from __future__ import annotations

from time import perf_counter

import psycopg

from app.metadata.connectors import (
    ConnectionConfig,
    ConnectionTestResult,
    IntrospectedTable,
    connected_message,
    failure,
    group_columns,
    missing_schema,
)


class PostgresConnector:
    """Opens short-lived, read-only sessions; never runs model SQL."""

    def _connect(self, config: ConnectionConfig, password: str | None) -> psycopg.Connection:
        return psycopg.connect(
            host=config.host,
            port=config.port or 5432,
            dbname=config.database,
            user=config.username,
            password=password,
            sslmode=config.ssl_mode,
            connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
            autocommit=True,
        )

    def test(self, config: ConnectionConfig, password: str | None) -> ConnectionTestResult:
        schema = config.schema_name or "public"
        started = perf_counter()
        try:
            with self._connect(config, password) as connection:
                version = connection.execute("SHOW server_version").fetchone()
                schema_found = connection.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = %s)",
                    (schema,),
                ).fetchone()
                if not schema_found or not schema_found[0]:
                    return missing_schema(schema)
                tables = connection.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                    (schema,),
                ).fetchone()
                writable = connection.execute(
                    "SELECT has_schema_privilege(current_user, %s, 'CREATE') OR EXISTS ("
                    " SELECT 1 FROM information_schema.table_privileges"
                    " WHERE grantee = current_user AND table_schema = %s"
                    " AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE'))",
                    (schema, schema),
                ).fetchone()
        except psycopg.Error as error:
            return failure(error)
        table_count = int(tables[0]) if tables else 0
        can_write = bool(writable[0]) if writable else None
        return ConnectionTestResult(
            ok=True,
            message=connected_message(table_count, f"schema {schema}", can_write),
            server_version=str(version[0]) if version else None,
            latency_ms=(perf_counter() - started) * 1000,
            table_count=table_count,
            can_write=can_write,
        )

    def introspect(self, config: ConnectionConfig, password: str | None) -> list[IntrospectedTable]:
        schema = config.schema_name or "public"
        with self._connect(config, password) as connection:
            columns = connection.execute(
                "SELECT table_name, column_name, data_type, is_nullable = 'YES' "
                "FROM information_schema.columns WHERE table_schema = %s "
                "ORDER BY table_name, ordinal_position",
                (schema,),
            ).fetchall()
            estimates: dict[str, int] = dict(
                connection.execute(
                    "SELECT c.relname, c.reltuples::bigint FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relkind = 'r'",
                    (schema,),
                ).fetchall()
            )
        return group_columns(columns, estimates)


CONNECTOR = PostgresConnector()
