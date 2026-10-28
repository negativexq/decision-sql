from __future__ import annotations

from time import perf_counter

import psycopg

from app.catalog.models import (
    ColumnMetadata,
    RelationshipMetadata,
    SchemaCatalog,
    TableMetadata,
)
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

    def catalog(self, config: ConnectionConfig, password: str | None) -> SchemaCatalog:
        """Read tables, columns, keys and comments; the catalog is the question's authority."""
        schema = config.schema_name or "public"
        with self._connect(config, password) as connection:
            columns = connection.execute(
                "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), "
                "col_description(c.oid, a.attnum), obj_description(c.oid, 'pg_class') "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm') "
                "AND NOT c.relispartition AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY c.relname, a.attnum",
                (schema,),
            ).fetchall()
            primary_keys = set(
                connection.execute(
                    "SELECT c.relname, a.attname FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indisprimary AND n.nspname = %s",
                    (schema,),
                ).fetchall()
            )
            foreign_keys = connection.execute(
                "SELECT src.relname, sa.attname, dst.relname, da.attname "
                "FROM pg_constraint con "
                "CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(src_col, dst_col) "
                "JOIN pg_class src ON src.oid = con.conrelid "
                "JOIN pg_attribute sa ON sa.attrelid = con.conrelid AND sa.attnum = k.src_col "
                "JOIN pg_class dst ON dst.oid = con.confrelid "
                "JOIN pg_attribute da ON da.attrelid = con.confrelid AND da.attnum = k.dst_col "
                "JOIN pg_namespace n ON n.oid = con.connamespace "
                "JOIN pg_namespace dn ON dn.oid = dst.relnamespace "
                "WHERE con.contype = 'f' AND n.nspname = %s AND dn.nspname = %s "
                "ORDER BY src.relname, con.conname, k.src_col",
                (schema, schema),
            ).fetchall()
        return build_catalog(columns, primary_keys, foreign_keys)


def build_catalog(
    columns: list[tuple[str, str, str, str | None, str | None]],
    primary_keys: set[tuple[str, str]],
    foreign_keys: list[tuple[str, str, str, str]],
) -> SchemaCatalog:
    """Assemble a catalog from catalog rows; database comments become descriptions."""
    tables: dict[str, list[ColumnMetadata]] = {}
    table_comments: dict[str, str | None] = {}
    for table, column, data_type, comment, table_comment in columns:
        table_comments[table] = table_comment
        tables.setdefault(table, []).append(
            ColumnMetadata(
                name=column,
                type=data_type,
                description=comment or f"{column} from {table}.",
                primary_key=(table, column) in primary_keys,
            )
        )
    relationships: dict[str, list[RelationshipMetadata]] = {}
    for table, column, referenced_table, referenced_column in foreign_keys:
        relationships.setdefault(table, []).append(
            RelationshipMetadata(
                column=column,
                referenced_table=referenced_table,
                referenced_column=referenced_column,
            )
        )
    return SchemaCatalog(
        tables=tuple(
            TableMetadata(
                name=table,
                description=table_comments.get(table) or f"{table} table.",
                columns=tuple(table_columns),
                relationships=tuple(relationships.get(table, ())),
            )
            for table, table_columns in tables.items()
        )
    )


CONNECTOR = PostgresConnector()
