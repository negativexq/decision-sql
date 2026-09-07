"""Provider-free PostgreSQL metadata and deterministic LiveSQLBench context."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass(frozen=True)
class PostgresConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 55434
    user: str = "root"
    password: str = "123123"
    database_suffix: str = "_template"
    timeout_seconds: int = 10

    @classmethod
    def from_environment(cls) -> PostgresConnectionConfig:
        return cls(
            host=os.getenv("LIVESQLBENCH_DB_HOST", cls.host),
            port=int(os.getenv("LIVESQLBENCH_DB_PORT", str(cls.port))),
            user=os.getenv("LIVESQLBENCH_DB_USER", cls.user),
            password=os.getenv("LIVESQLBENCH_DB_PASSWORD", cls.password),
            database_suffix=os.getenv("LIVESQLBENCH_DB_SUFFIX", cls.database_suffix),
            timeout_seconds=int(
                os.getenv("LIVESQLBENCH_DB_TIMEOUT_SECONDS", str(cls.timeout_seconds))
            ),
        )

    def public_identity(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "database_suffix": self.database_suffix,
            "timeout_seconds": self.timeout_seconds,
        }

    def connect(self, database: str) -> psycopg.Connection[Any]:
        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=f"{database}{self.database_suffix}",
            connect_timeout=self.timeout_seconds,
        )


@dataclass(frozen=True)
class LiveSqlBenchColumn:
    schema: str
    table: str
    name: str
    data_type: str
    nullable: bool
    ordinal: int
    primary_key: bool
    foreign_key_target: tuple[str, str, str] | None


@dataclass(frozen=True)
class LiveSqlBenchTable:
    schema: str
    name: str
    columns: tuple[LiveSqlBenchColumn, ...]


@dataclass(frozen=True)
class LiveSqlBenchDatabase:
    name: str
    tables: tuple[LiveSqlBenchTable, ...]
    primary_key_count: int
    foreign_key_count: int
    server_version: str

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def column_count(self) -> int:
        return sum(len(table.columns) for table in self.tables)

    @property
    def relationship_count(self) -> int:
        return sum(
            column.foreign_key_target is not None
            for table in self.tables
            for column in table.columns
        )

    def stable_identity(self) -> str:
        payload = "\n".join(
            f"{table.schema}.{table.name}:{column.name}:{column.data_type}:{column.nullable}:"
            f"{column.primary_key}:{column.foreign_key_target}"
            for table in self.tables
            for column in table.columns
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def introspect_database(database: str, config: PostgresConnectionConfig) -> LiveSqlBenchDatabase:
    with config.connect(database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_setting('server_version')")
            version_row = cursor.fetchone()
            if version_row is None:
                raise RuntimeError("PostgreSQL did not return server_version")
            server_version = str(version_row[0])
            cursor.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """
            )
            table_rows = [(str(schema), str(table)) for schema, table in cursor.fetchall()]
            cursor.execute(
                """
                SELECT table_schema, table_name, column_name, data_type,
                       is_nullable, ordinal_position
                FROM information_schema.columns
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name, ordinal_position
                """
            )
            column_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT kcu.table_schema, kcu.table_name, kcu.column_name,
                       ccu.table_schema, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                 AND tc.table_name = kcu.table_name
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                ORDER BY kcu.table_schema, kcu.table_name, kcu.column_name
                """
            )
            foreign_keys = {
                (str(schema), str(table), str(column)): (
                    str(target_schema),
                    str(target_table),
                    str(target_column),
                )
                for (
                    schema,
                    table,
                    column,
                    target_schema,
                    target_table,
                    target_column,
                ) in cursor.fetchall()
            }
            cursor.execute(
                """
                SELECT kcu.table_schema, kcu.table_name, kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                 AND tc.table_name = kcu.table_name
                WHERE tc.constraint_type = 'PRIMARY KEY'
                """
            )
            primary_keys = {
                (str(schema), str(table), str(column))
                for schema, table, column in cursor.fetchall()
            }

    by_table: dict[tuple[str, str], list[LiveSqlBenchColumn]] = {key: [] for key in table_rows}
    for schema, table, name, data_type, nullable, ordinal in column_rows:
        key = (str(schema), str(table))
        if key not in by_table:
            continue
        by_table[key].append(
            LiveSqlBenchColumn(
                schema=str(schema),
                table=str(table),
                name=str(name),
                data_type=str(data_type),
                nullable=str(nullable).upper() == "YES",
                ordinal=int(ordinal),
                primary_key=key + (str(name),) in primary_keys,
                foreign_key_target=foreign_keys.get(key + (str(name),)),
            )
        )
    tables = tuple(
        LiveSqlBenchTable(schema, table, tuple(columns))
        for schema, table in table_rows
        for columns in [by_table[(schema, table)]]
    )
    return LiveSqlBenchDatabase(
        name=database,
        tables=tables,
        primary_key_count=len(primary_keys),
        foreign_key_count=len(foreign_keys),
        server_version=server_version,
    )


def verify_read_only_probe(database: str, config: PostgresConnectionConfig) -> bool:
    """Verify the official DB accepts a bounded read-only query."""
    with config.connect(database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)


def render_schema_context(database: LiveSqlBenchDatabase) -> str:
    """Render complete bounded Base-Lite metadata in normalized database order."""
    lines = ["POSTGRESQL SCHEMA CONTEXT", f"DATABASE: {database.name}"]
    for table in database.tables:
        lines.append(f"TABLE {table.schema}.{table.name}")
        for column in table.columns:
            key = " PRIMARY KEY" if column.primary_key else ""
            nullable = " NULL" if column.nullable else " NOT NULL"
            lines.append(f"  COLUMN {column.name} TYPE {column.data_type}{nullable}{key}")
            if column.foreign_key_target is not None:
                target_schema, target_table, target_column = column.foreign_key_target
                lines.append(
                    f"  FOREIGN KEY {table.schema}.{table.name}.{column.name} "
                    f"REFERENCES {target_schema}.{target_table}.{target_column}"
                )
    return "\n".join(lines) + "\n"
