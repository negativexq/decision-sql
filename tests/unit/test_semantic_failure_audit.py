from evaluation.analysis.semantic_failure_audit import (
    _classify_general,
    _classify_ratio,
    _classify_temporal,
)
from evaluation.analysis.sql_ast_diff import compare_summaries, summarize_sql


def test_ast_summary_detects_ratio_and_join_structure() -> None:
    summary = summarize_sql(
        "SELECT SUM(a.amount)::numeric / COUNT(DISTINCT c.id) FROM a JOIN c ON c.id = a.cid"
    )

    assert summary["parse_success"] is True
    assert summary["joins"] == 1
    assert summary["division"]
    assert summary["aggregations"]
    assert summary["distinct"] is True


def test_ast_diff_ignores_alias_only_projection_names() -> None:
    gold = summarize_sql("SELECT SUM(amount) AS total FROM orders")
    generated = summarize_sql("SELECT SUM(amount) AS other_name FROM orders")

    diff = compare_summaries(gold, generated)

    assert diff["differences"] == {}


def test_ratio_classifier_labels_missing_distinct() -> None:
    gold = summarize_sql("SELECT COUNT(DISTINCT customer_id)::numeric / COUNT(*) FROM orders")
    generated = summarize_sql("SELECT COUNT(customer_id)::numeric / COUNT(*) FROM orders")

    primary, tags, _ = _classify_ratio(gold, generated, "What is the customer ratio?", {})

    assert primary == "MISSING_DISTINCT"
    assert "DISTINCT" in tags


def test_ratio_classifier_labels_numerator_difference() -> None:
    gold = summarize_sql("SELECT SUM(revenue)::numeric / SUM(cost) FROM facts")
    generated = summarize_sql("SELECT SUM(profit)::numeric / SUM(cost) FROM facts")

    primary, _, _ = _classify_ratio(gold, generated, "What is the ratio?", {})

    assert primary == "WRONG_NUMERATOR"


def test_ratio_classifier_labels_denominator_difference() -> None:
    gold = summarize_sql("SELECT SUM(revenue)::numeric / SUM(cost) FROM facts")
    generated = summarize_sql("SELECT SUM(revenue)::numeric / SUM(expense) FROM facts")

    primary, _, _ = _classify_ratio(gold, generated, "What is the ratio?", {})

    assert primary == "WRONG_DENOMINATOR"


def test_temporal_classifier_labels_wrong_time_column() -> None:
    gold = summarize_sql("SELECT COUNT(*) FROM orders WHERE ordered_at >= DATE '2024-01-01'")
    generated = summarize_sql("SELECT COUNT(*) FROM orders WHERE created_at >= DATE '2024-01-01'")

    primary, _, _ = _classify_temporal(gold, generated, "How many orders in 2024?")

    assert primary == "WRONG_TIME_COLUMN"


def test_general_classifier_labels_table_grounding() -> None:
    gold = summarize_sql("SELECT COUNT(*) FROM orders")
    generated = summarize_sql("SELECT COUNT(*) FROM customers")

    primary, _, _ = _classify_general(gold, generated)

    assert primary == "TABLE_GROUNDING"
