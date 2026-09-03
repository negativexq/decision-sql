"""Pinned BIRD Mini-Dev loading, safe execution, and EX comparison."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import psycopg
import sqlglot
from sqlglot import exp

REQUIRED_FIELDS = {"db_id", "question", "evidence", "SQL", "difficulty"}


@dataclass(frozen=True)
class BirdQuestion:
    index: int
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: str


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(list(self.rows), columns=list(self.columns))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_questions(path: Path) -> list[BirdQuestion]:
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text())
        if not isinstance(rows, list):
            raise ValueError("BIRD dataset must be a JSON array")
    else:
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
    result: list[BirdQuestion] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"BIRD row {index} is not an object")
        missing = REQUIRED_FIELDS - set(row)
        if missing:
            raise ValueError(f"BIRD row {index} missing fields: {sorted(missing)}")
        result.append(
            BirdQuestion(
                index=index,
                question_id=int(row["question_id"]),
                db_id=str(row["db_id"]),
                question=str(row["question"]),
                evidence=str(row.get("evidence") or ""),
                gold_sql=str(row["SQL"]),
                difficulty=str(row.get("difficulty") or "unknown").lower(),
            )
        )
    return result


def postgres_connection_kwargs() -> dict[str, Any]:
    return {
        "host": os.getenv("BIRD_DB_HOST", "host.docker.internal"),
        "port": int(os.getenv("BIRD_DB_PORT", "55433")),
        "user": os.getenv("BIRD_DB_USER", "bird_reader"),
        "password": os.getenv("BIRD_DB_PASSWORD", "bird_reader_password"),
    }


class BirdBenchmarkExecutor:
    """Evaluation-only executor; deliberately separate from production M1."""

    def __init__(self, timeout_ms: int = 10_000, max_result_rows: int = 200_000) -> None:
        self.timeout_ms = timeout_ms
        self.max_result_rows = max_result_rows

    @staticmethod
    def _validate_select(query: str) -> None:
        try:
            statements = sqlglot.parse(query, read="postgres")
        except sqlglot.errors.ParseError as error:
            raise ValueError(f"invalid PostgreSQL SQL: {error}") from error
        if len(statements) != 1:
            raise ValueError("BIRD query must contain exactly one statement")
        tree = statements[0]
        if not isinstance(tree, (exp.Select, exp.SetOperation)):
            raise ValueError("BIRD query must be SELECT, SELECT-CTE, or SELECT set operation")
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
            raise ValueError("BIRD query contains a forbidden SQL operation")
        forbidden_functions = {"pg_advisory_lock", "pg_advisory_xact_lock"}
        if any(
            isinstance(node, exp.Anonymous) and node.name.lower() in forbidden_functions
            for node in tree.walk()
        ):
            raise ValueError("BIRD query contains a forbidden lock function")

    def execute(self, query: str) -> QueryResult:
        self._validate_select(query)
        started = time.perf_counter()
        with psycopg.connect(dbname="bird", **postgres_connection_kwargs()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(f"SET LOCAL statement_timeout = {self.timeout_ms}")
                cursor.execute(query)
                columns = tuple(description.name for description in cursor.description or ())
                rows = tuple(cursor.fetchmany(self.max_result_rows + 1))
                if len(rows) > self.max_result_rows:
                    raise ValueError("BIRD result exceeds the evaluation row bound")
        del started
        return QueryResult(columns=columns, rows=tuple(tuple(row) for row in rows))


def bird_execution_match(generated: QueryResult, gold: QueryResult) -> bool:
    """BIRD Mini-Dev EX: compare result-row sets, as in pinned evaluation_ex.py."""
    return {_hashable(row) for row in generated.rows} == {_hashable(row) for row in gold.rows}


def _hashable(value: Any) -> Any:
    """Preserve BIRD row-set equality for PostgreSQL array/JSON result values."""
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_hashable(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _hashable(item)) for key, item in value.items()))
    if isinstance(value, set):
        return frozenset(_hashable(item) for item in value)
    return value


def soft_f1(generated: QueryResult, gold: QueryResult) -> float:
    """Faithful local port of the pinned Mini-Dev beta Soft F1 calculation."""
    predicted = _unique_rows(generated.rows)
    truth = _unique_rows(gold.rows)
    if not predicted and not truth:
        return 1.0
    if not truth:
        return 0.0
    match: list[float] = []
    predicted_only: list[float] = []
    truth_only: list[float] = []
    width = max(len(gold.rows[0]) if gold.rows else 0, 1)
    for index, truth_row in enumerate(truth):
        if index >= len(predicted):
            match.append(0.0)
            predicted_only.append(0.0)
            truth_only.append(1.0)
            continue
        predicted_row = predicted[index]
        matches = sum(value in truth_row for value in predicted_row)
        missing_pred = sum(value not in truth_row for value in predicted_row)
        missing_truth = sum(value not in predicted_row for value in truth_row)
        match.append(matches / width)
        predicted_only.append(missing_pred / width)
        truth_only.append(missing_truth / width)
    for _ in range(max(len(predicted) - len(truth), 0)):
        match.append(0.0)
        predicted_only.append(1.0)
        truth_only.append(0.0)
    tp = sum(match)
    fp = sum(predicted_only)
    fn = sum(truth_only)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _unique_rows(rows: tuple[tuple[Any, ...], ...]) -> list[tuple[Any, ...]]:
    unique: list[tuple[Any, ...]] = []
    for row in rows:
        if row not in unique:
            unique.append(row)
    return unique
