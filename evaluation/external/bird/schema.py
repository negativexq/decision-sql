"""Deterministic BIRD PostgreSQL schema prompt serialization."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import psycopg

from evaluation.external.bird.benchmark import postgres_connection_kwargs

_POSTGRES_RESERVED = {
    "all",
    "and",
    "as",
    "between",
    "by",
    "case",
    "create",
    "from",
    "group",
    "order",
    "select",
    "table",
    "where",
}

BIRD_TABLES: dict[str, tuple[str, ...]] = {
    "debit_card_specializing": (
        "customers",
        "gasstations",
        "products",
        "transactions_1k",
        "yearmonth",
    ),
    "student_club": (
        "major",
        "member",
        "attendance",
        "budget",
        "event",
        "expense",
        "income",
        "zip_code",
    ),
    "thrombosis_prediction": ("Patient", "Examination", "Laboratory"),
    "european_football_2": (
        "League",
        "Match",
        "Player",
        "Player_Attributes",
        "Team",
        "Team_Attributes",
    ),
    "formula_1": (
        "circuits",
        "seasons",
        "races",
        "constructors",
        "constructorResults",
        "constructorStandings",
        "drivers",
        "driverStandings",
        "lapTimes",
        "pitStops",
        "qualifying",
        "status",
        "results",
    ),
    "superhero": (
        "alignment",
        "attribute",
        "colour",
        "gender",
        "publisher",
        "race",
        "superpower",
        "superhero",
        "hero_attribute",
        "hero_power",
    ),
    "codebase_community": (
        "posts",
        "users",
        "badges",
        "comments",
        "postHistory",
        "postLinks",
        "tags",
        "votes",
    ),
    "card_games": ("cards", "foreign_data", "legalities", "rulings", "set_translations", "sets"),
    "toxicology": ("molecule", "atom", "bond", "connected"),
    "california_schools": ("satscores", "frpm", "schools"),
    "financial": ("district", "account", "client", "disp", "card", "loan", "order", "trans"),
}


def prompt_identifier(value: str) -> str:
    if re.fullmatch(r"[a-z_][a-z0-9_]*", value) and value.lower() not in _POSTGRES_RESERVED:
        return value
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def schema_context(db_id: str) -> tuple[str, dict[str, Any]]:
    if db_id not in BIRD_TABLES:
        raise ValueError(f"Unknown BIRD logical database: {db_id}")
    allowed = {table.lower() for table in BIRD_TABLES[db_id]}
    with psycopg.connect(dbname="bird", **postgres_connection_kwargs()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_schema, table_name, column_name, data_type, udt_name,
                       is_nullable, ordinal_position
                FROM information_schema.columns
                WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY table_schema, table_name, ordinal_position
                """
            )
            rows = cursor.fetchall()
    tables: dict[str, list[dict[str, Any]]] = {}
    for schema, table, column, data_type, udt_name, nullable, position in rows:
        if table.lower() not in allowed:
            continue
        key = f"{schema}.{table}"
        tables.setdefault(key, []).append(
            {
                "name": column,
                "type": data_type if data_type != "USER-DEFINED" else udt_name,
                "nullable": nullable == "YES",
                "position": int(position),
            }
        )
    lines: list[str] = []
    for table in sorted(tables):
        schema, name = table.split(".", 1)
        lines.append(f"CREATE TABLE {prompt_identifier(schema)}.{prompt_identifier(name)} (")
        columns = tables[table]
        for index, column in enumerate(columns):
            nullable = "NULL" if column["nullable"] else "NOT NULL"
            comma = "," if index + 1 < len(columns) else ""
            lines.append(
                f"  {prompt_identifier(column['name'])} {column['type']} {nullable}{comma}"
            )
        lines.append(");")
    text = "\n".join(lines)
    metadata = {
        "db_id": db_id,
        "schema_context_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "schema_context_chars": len(text),
        "table_count": len(tables),
        "column_count": sum(len(columns) for columns in tables.values()),
        "foreign_keys_exposed": False,
        "sample_rows_exposed": False,
        "tables": sorted(tables),
    }
    return text, metadata
