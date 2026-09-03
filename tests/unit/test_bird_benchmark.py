from __future__ import annotations

import json

import pytest

from evaluation.external.bird.benchmark import (
    BirdBenchmarkExecutor,
    QueryResult,
    bird_execution_match,
    load_questions,
)
from evaluation.external.bird.features import ast_features
from evaluation.external.bird.run_m214 import control_questions


def test_bird_loader_preserves_canonical_fields(tmp_path):
    path = tmp_path / "bird.json"
    path.write_text(
        json.dumps(
            [{
                "question_id": 1,
                "db_id": "financial",
                "question": "What is one?",
                "evidence": "",
                "SQL": "SELECT 1",
                "difficulty": "simple",
            }]
        )
    )
    row = load_questions(path)[0]
    assert row.question_id == 1
    assert row.db_id == "financial"
    assert row.gold_sql == "SELECT 1"


@pytest.mark.parametrize(
    "query",
    (
        "SELECT 1",
        "WITH one AS (SELECT 1) SELECT * FROM one",
        "SELECT 1 UNION SELECT 2",
    ),
)
def test_bird_executor_accepts_select_cte(query):
    BirdBenchmarkExecutor._validate_select(query)


@pytest.mark.parametrize(
    "query",
    (
        "INSERT INTO one VALUES (1)",
        "UPDATE one SET value = 1",
        "DELETE FROM one",
        "CREATE TABLE one (value int)",
        "SELECT 1; SELECT 2",
        "CREATE TABLE one (value int)",
        "SELECT pg_advisory_lock(1)",
    ),
)
def test_bird_executor_rejects_writes_and_multiple_statements(query):
    with pytest.raises(ValueError):
        BirdBenchmarkExecutor._validate_select(query)


def test_bird_features_cover_window_and_arithmetic():
    features = ast_features(
        "SELECT LAG(amount) OVER (PARTITION BY customer_id ORDER BY ordered_at "
        "ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), amount / 2 FROM orders"
    )
    assert features["HAS_WINDOW"]
    assert features["LAG"]
    assert features["EXPLICIT_FRAME"]


def test_bird_ex_handles_postgresql_array_values():
    gold = QueryResult(columns=("values",), rows=(([1, 2],),))
    generated = QueryResult(columns=("other_alias",), rows=(([1, 2],),))
    assert bird_execution_match(generated, gold)


def test_bird_control_slice_rejects_impossible_count():
    with pytest.raises(ValueError, match="available questions"):
        control_questions([], 20)
