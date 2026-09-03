"""Offline Defog dataset, schema, executor, and canonical-evaluator adapter."""

from __future__ import annotations

import csv
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]
import psycopg
import sqlglot
from sqlglot import exp

REQUIRED_CLASSIC = {"question", "query", "db_name", "query_category", "instructions"}
REQUIRED_ADVANCED = {
    "question",
    "query",
    "db_name",
    "query_category",
    "instructions",
    "full_instructions",
}
DEFAULT_DBS = (
    "academic",
    "advising",
    "atis",
    "broker",
    "car_dealership",
    "derm_treatment",
    "ewallet",
    "geography",
    "restaurants",
    "scholar",
    "yelp",
)


@dataclass(frozen=True)
class BenchmarkQuestion:
    dataset: str
    index: int
    question: str
    gold_sql: str
    db_name: str
    query_category: str
    instructions: str
    full_instructions: str = ""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(list(self.rows), columns=list(self.columns))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_questions(path: Path, dataset: str) -> list[BenchmarkQuestion]:
    required = REQUIRED_CLASSIC if dataset == "classic" else REQUIRED_ADVANCED
    result: list[BenchmarkQuestion] = []
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        actual = set(reader.fieldnames or ())
        missing = required - actual
        if missing:
            raise ValueError(f"{path.name} missing columns: {sorted(missing)}")
        for index, row in enumerate(reader):
            result.append(
                BenchmarkQuestion(
                    dataset=dataset,
                    index=index,
                    question=row["question"],
                    gold_sql=row["query"],
                    db_name=row["db_name"],
                    query_category=row["query_category"],
                    instructions=row.get("instructions") or "",
                    full_instructions=row.get("full_instructions") or "",
                )
            )
    return result


def format_reference_instructions(value: str) -> str:
    """Match pinned Defog prepare_questions_df instruction formatting."""
    value = value.replace(". ", ".\n")
    return f"\nFollow the instructions below to generate the query:\n{value}\n" if value else ""


def postgres_connection_kwargs() -> dict[str, Any]:
    return {
        "host": os.getenv("DEFOG_DB_HOST", "host.docker.internal"),
        "port": int(os.getenv("DEFOG_DB_PORT", "55432")),
        "user": os.getenv("DEFOG_DB_USER", "defog_reader"),
        "password": os.getenv("DEFOG_DB_PASSWORD", "defog_reader_password"),
    }


class DefogBenchmarkExecutor:
    """Evaluation-only SELECT executor for the isolated Defog PostgreSQL cluster."""

    def __init__(self, timeout_ms: int = 10_000) -> None:
        self.timeout_ms = timeout_ms

    @staticmethod
    def _validate_select(query: str) -> None:
        statements = sqlglot.parse(query, read="postgres")
        if len(statements) != 1:
            raise ValueError("benchmark query must contain exactly one statement")
        tree = statements[0]
        if not isinstance(tree, exp.Select):
            raise ValueError("benchmark query must be SELECT or SELECT-CTE")
        forbidden = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Create,
            exp.Drop,
            exp.Command,
            exp.Copy,
            exp.Transaction,
            exp.Merge,
            exp.Alter,
            exp.Lock,
        )
        if any(tree.find(node) is not None for node in forbidden):
            raise ValueError("benchmark query contains a forbidden SQL operation")
        forbidden_functions = {"pg_advisory_lock", "pg_advisory_xact_lock"}
        if any(
            isinstance(node, exp.Anonymous) and node.name.lower() in forbidden_functions
            for node in tree.walk()
        ):
            raise ValueError("benchmark query contains a forbidden lock function")

    def execute(self, db_name: str, query: str) -> QueryResult:
        self._validate_select(query)
        kwargs = postgres_connection_kwargs()
        started = time.perf_counter()
        with psycopg.connect(dbname=db_name, **kwargs) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(f"SET LOCAL statement_timeout = {self.timeout_ms}")
                cursor.execute(query)
                columns = tuple(description.name for description in cursor.description or ())
                rows = tuple(tuple(row) for row in cursor.fetchall())
        del started
        return QueryResult(columns=columns, rows=rows)


def canonical_gold_variants(sql: str) -> list[str]:
    """Use the pinned Defog parser for braces and multiple accepted queries."""
    import sys

    root = os.environ["DEFOG_SQL_EVAL_ROOT"]
    if root not in sys.path:
        sys.path.insert(0, root)
    from eval.eval import get_all_minimal_queries  # type: ignore[import-not-found]

    return cast(list[str], get_all_minimal_queries(sql))


def canonical_compare(
    gold: QueryResult,
    generated: QueryResult,
    question: BenchmarkQuestion,
    gold_sql: str,
    generated_sql: str,
) -> tuple[bool, bool]:
    """Delegate result semantics to the pinned Defog evaluator implementation."""
    import sys

    root = os.environ["DEFOG_SQL_EVAL_ROOT"]
    if root not in sys.path:
        sys.path.insert(0, root)
    from eval.eval import compare_df, subset_df

    gold_df = gold.dataframe()
    generated_df = generated.dataframe()
    exact = compare_df(
        gold_df,
        generated_df,
        question.query_category,
        question.question,
        gold_sql,
        generated_sql,
    )
    subset = subset_df(
        gold_df,
        generated_df,
        question.query_category,
        question.question,
        gold_sql,
        generated_sql,
    )
    return bool(exact), bool(subset)


def schema_context(executor: DefogBenchmarkExecutor, db_name: str) -> tuple[str, dict[str, Any]]:
    """Serialize only deterministic PostgreSQL catalog metadata, never data values."""
    kwargs = postgres_connection_kwargs()
    with psycopg.connect(dbname=db_name, **kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.udt_name,
                       c.ordinal_position,
                       CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN true ELSE false END
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage k
                  ON k.table_schema = c.table_schema AND k.table_name = c.table_name
                 AND k.column_name = c.column_name
                LEFT JOIN information_schema.table_constraints tc
                  ON tc.constraint_schema = k.constraint_schema
                 AND tc.constraint_name = k.constraint_name
                WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY c.table_schema, c.table_name, c.ordinal_position
                """
            )
            columns = cursor.fetchall()
            cursor.execute(
                """
                SELECT source_ns.nspname, source_table.relname, source_column.attname,
                       target_ns.nspname, target_table.relname, target_column.attname
                FROM pg_constraint fk
                JOIN pg_class source_table ON source_table.oid = fk.conrelid
                JOIN pg_namespace source_ns ON source_ns.oid = source_table.relnamespace
                JOIN pg_class target_table ON target_table.oid = fk.confrelid
                JOIN pg_namespace target_ns ON target_ns.oid = target_table.relnamespace
                CROSS JOIN LATERAL unnest(fk.conkey) WITH ORDINALITY source_key(attnum, position)
                CROSS JOIN LATERAL unnest(fk.confkey) WITH ORDINALITY target_key(attnum, position)
                JOIN pg_attribute source_column
                  ON source_column.attrelid = source_table.oid
                 AND source_column.attnum = source_key.attnum
                JOIN pg_attribute target_column
                  ON target_column.attrelid = target_table.oid
                 AND target_column.attnum = target_key.attnum
                 AND target_key.position = source_key.position
                WHERE fk.contype = 'f'
                  AND source_ns.nspname NOT IN ('information_schema', 'pg_catalog')
                ORDER BY source_ns.nspname, source_table.relname, source_column.attname,
                         target_ns.nspname, target_table.relname, target_column.attname
                """
            )
            relationships = cursor.fetchall()
    tables: dict[str, list[dict[str, Any]]] = {}
    for schema, table, column, data_type, udt_name, position, primary_key in columns:
        table_key = f"{schema}.{table}"
        tables.setdefault(table_key, []).append(
            {
                "name": column,
                "type": data_type or udt_name,
                "position": position,
                "primary_key": bool(primary_key),
            }
        )
    lines: list[str] = []
    for table in sorted(tables):
        lines.append(f"CREATE TABLE {table} (")
        values = tables[table]
        for index, column in enumerate(values):
            suffix = " PRIMARY KEY" if column["primary_key"] else ""
            comma = "," if index + 1 < len(values) else ""
            lines.append(f"  {column['name']} {column['type']}{suffix}{comma}")
        lines.append(");")
    if relationships:
        lines.append("-- Foreign-key relationships:")
        lines.extend(
            f"-- {schema}.{table}.{column} references {ref_schema}.{ref_table}.{ref_column}"
            for schema, table, column, ref_schema, ref_table, ref_column in relationships
        )
    text = "\n".join(lines)
    metadata = {
        "db_name": db_name,
        "schema_context_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "schema_context_chars": len(text),
        "table_count": len(tables),
        "column_count": len(columns),
        "relationship_count": len(relationships),
        "tables": sorted(tables),
    }
    del executor
    return text, metadata


def ast_features(sql: str) -> dict[str, bool]:
    """Deterministic gold/generated SQL feature tags; never included in prompts."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return {"PARSE_ERROR": True}
    windows = list(tree.find_all(exp.Window))
    functions = {type(node.this).__name__.upper() for node in windows if node.this is not None}
    return {
        "HAS_WINDOW": bool(windows),
        "WINDOW_OVER": bool(windows),
        "LAG": "LAG" in functions,
        "LEAD": "LEAD" in functions,
        "ROW_NUMBER": "ROWNUMBER" in functions,
        "RANK": "RANK" in functions,
        "DENSE_RANK": "DENSERANK" in functions,
        "WINDOW_AGGREGATE": any(isinstance(node.this, exp.AggFunc) for node in windows),
        "EXPLICIT_WINDOW_FRAME": any(node.args.get("spec") is not None for node in windows),
        "PARTITION_BY": any(node.args.get("partition_by") for node in windows),
    }


def structural_profile(sql: str) -> dict[str, int | bool]:
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return {"parse_error": 1}
    joins = len(list(tree.find_all(exp.Join)))
    features = ast_features(sql)
    return {
        "tables": len(list(tree.find_all(exp.Table))),
        "joins": joins,
        "cte": int(tree.args.get("with") is not None or tree.args.get("with_") is not None),
        "window": int(features.get("HAS_WINDOW", False)),
        "subquery": len(list(tree.find_all(exp.Subquery))),
        "filter": int(tree.args.get("where") is not None),
        "aggregation": int(any(isinstance(node, exp.AggFunc) for node in tree.walk())),
        "group_by": int(tree.args.get("group") is not None),
        "order_by": int(tree.args.get("order") is not None),
        "limit": int(tree.args.get("limit") is not None),
        "arithmetic": int(
            any(isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)) for node in tree.walk())
        ),
    }


def dataset_profile(questions: list[BenchmarkQuestion]) -> dict[str, Any]:
    category: dict[str, int] = {}
    features: dict[str, int] = {}
    structural: dict[str, int] = {}
    dbs = sorted({q.db_name for q in questions})
    for q in questions:
        category[q.query_category] = category.get(q.query_category, 0) + 1
        variants = canonical_gold_variants(q.gold_sql)
        variant_features = [ast_features(variant) for variant in variants]
        variant_profiles = [structural_profile(variant) for variant in variants]
        for key in {key for profile in variant_features for key in profile}:
            features[key] = features.get(key, 0) + int(
                any(profile.get(key, False) for profile in variant_features)
            )
        for key in {key for profile in variant_profiles for key in profile}:
            values = [int(profile.get(key, 0)) for profile in variant_profiles]
            structural[key] = structural.get(key, 0) + max(values, default=0)
    return {
        "questions": len(questions),
        "databases": dbs,
        "database_count": len(dbs),
        "category_counts": category,
        "gold_feature_question_counts": features,
        "gold_structural_feature_sums": structural,
    }
