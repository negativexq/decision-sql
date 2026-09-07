"""Evaluation-only construction of the unchanged M1 service for LiveSQLBench DBs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL

from app.catalog.models import (
    ColumnMetadata,
    RelationshipMetadata,
    SchemaCatalog,
    TableMetadata,
)
from app.config import Settings
from app.sql.service import SqlSafetyService
from evaluation.external.livesqlbench.schema import LiveSqlBenchDatabase, PostgresConnectionConfig


def catalog_for_m1(database: LiveSqlBenchDatabase) -> SchemaCatalog:
    tables: list[TableMetadata] = []
    for table in database.tables:
        columns = tuple(
            ColumnMetadata(
                name=column.name,
                type=column.data_type,
                description=f"{column.name} from {table.name}.",
                primary_key=column.primary_key,
            )
            for column in table.columns
        )
        relationships = tuple(
            RelationshipMetadata(
                column=column.name,
                referenced_table=column.foreign_key_target[1],
                referenced_column=column.foreign_key_target[2],
            )
            for column in table.columns
            if column.foreign_key_target is not None
        )
        tables.append(
            TableMetadata(
                name=table.name,
                description=f"{table.name} table.",
                columns=columns,
                relationships=relationships,
            )
        )
    return SchemaCatalog(tables=tuple(tables))


def engine_for_database(database: str, config: PostgresConnectionConfig) -> Engine:
    url = URL.create(
        "postgresql+psycopg",
        username=config.user,
        password=config.password,
        host=config.host,
        port=config.port,
        database=f"{database}{config.database_suffix}",
    )
    return create_engine(url, pool_size=1, max_overflow=0, pool_pre_ping=True)


def safety_for_database(
    database: LiveSqlBenchDatabase,
    config: PostgresConnectionConfig,
    *,
    max_plan_rows: int = 100_000,
    max_plan_cost: float = 100_000.0,
    max_result_rows: int = 1_000,
    statement_timeout_ms: int = 5_000,
) -> tuple[Engine, SqlSafetyService, dict[str, Any]]:
    engine = engine_for_database(database.name, config)
    settings = Settings(
        _env_file=None,
        max_plan_rows=max_plan_rows,
        max_plan_cost=max_plan_cost,
        max_result_rows=max_result_rows,
        statement_timeout_ms=statement_timeout_ms,
        reader_role=config.user,
    )
    return (
        engine,
        SqlSafetyService(engine, settings=settings, catalog=catalog_for_m1(database)),
        {
            "max_plan_rows": max_plan_rows,
            "max_plan_cost": max_plan_cost,
            "max_result_rows": max_result_rows,
            "statement_timeout_ms": statement_timeout_ms,
            "reader_role": config.user,
        },
    )
