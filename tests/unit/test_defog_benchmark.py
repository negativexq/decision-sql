from __future__ import annotations

import pytest

from evaluation.external.defog.benchmark import (
    DefogBenchmarkExecutor,
    ast_features,
    format_reference_instructions,
    load_questions,
)


def test_load_questions_preserves_rows_and_requires_columns(tmp_path):
    path = tmp_path / "questions.csv"
    path.write_text(
        "question,query,db_name,query_category,instructions\n"
        "What is one?,SELECT 1,db,group_by,\n"
    )

    questions = load_questions(path, "classic")

    assert questions[0].index == 0
    assert questions[0].question == "What is one?"
    assert questions[0].db_name == "db"


def test_load_questions_rejects_missing_columns(tmp_path):
    path = tmp_path / "questions.csv"
    path.write_text("question,query\nWhat is one?,SELECT 1\n")

    with pytest.raises(ValueError, match="missing columns"):
        load_questions(path, "classic")


def test_reference_instruction_format_matches_pinned_runner():
    assert format_reference_instructions("Use A. Use B.") == (
        "\nFollow the instructions below to generate the query:\nUse A.\nUse B.\n"
    )
    assert format_reference_instructions("") == ""


def test_external_executor_accepts_select_and_select_cte_without_network():
    for query in ("SELECT 1", "WITH one AS (SELECT 1) SELECT * FROM one"):
        DefogBenchmarkExecutor._validate_select(query)


@pytest.mark.parametrize(
    "query",
    (
        "INSERT INTO one VALUES (1)",
        "UPDATE one SET value = 1",
        "DELETE FROM one",
        "CREATE TABLE one (value int)",
        "DROP TABLE one",
        "SELECT 1; SELECT 2",
        "SELECT pg_advisory_lock(1)",
    ),
)
def test_external_executor_rejects_non_select_or_unsafe_sql(query):
    with pytest.raises(ValueError):
        DefogBenchmarkExecutor._validate_select(query)


def test_ast_features_extract_window_functions_and_frame():
    features = ast_features(
        "SELECT LAG(amount) OVER (PARTITION BY customer_id "
        "ORDER BY ordered_at ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) FROM orders"
    )

    assert features["HAS_WINDOW"]
    assert features["LAG"]
    assert features["PARTITION_BY"]
    assert features["EXPLICIT_WINDOW_FRAME"]


def test_ast_features_extract_ranking_variants():
    for function, key in (
        ("ROW_NUMBER", "ROW_NUMBER"),
        ("RANK", "RANK"),
        ("DENSE_RANK", "DENSE_RANK"),
    ):
        assert ast_features(f"SELECT {function}() OVER (ORDER BY amount) FROM orders")[key]


def test_ast_features_distinguishes_non_window_query():
    features = ast_features("SELECT customer_id, SUM(amount) FROM orders GROUP BY customer_id")

    assert not features["HAS_WINDOW"]
    assert not features["WINDOW_AGGREGATE"]
