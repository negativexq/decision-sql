import pytest

from app.memory.models import build_verified_query_example
from app.verification import DeterministicSemanticVerifier


@pytest.fixture
def verifier() -> DeterministicSemanticVerifier:
    return DeterministicSemanticVerifier()


def codes(verifier: DeterministicSemanticVerifier, question: str, sql: str) -> set[str]:
    return {signal.code.value for signal in verifier.verify(question, sql).signals}


def test_top_n_requires_order_and_limit(verifier: DeterministicSemanticVerifier) -> None:
    result = verifier.verify(
        "Show the top 5 products by revenue.",
        """
        SELECT p.name, SUM(o.total_amount)
        FROM products AS p
        JOIN order_items AS oi ON oi.product_id = p.id
        JOIN orders AS o ON o.id = oi.order_id
        GROUP BY p.name
        """,
    )

    assert {
        "TOP_N_STRUCTURE_MISSING",
        "TOP_N_WITHOUT_ORDER",
        "TOP_N_LIMIT_MISSING",
    } <= {signal.code.value for signal in result.signals}


def test_grouping_and_threshold_contradictions_are_detected(
    verifier: DeterministicSemanticVerifier,
) -> None:
    assert "REQUESTED_GROUPING_MISSING" in codes(
        verifier,
        "Show revenue by region.",
        "SELECT SUM(total_amount) FROM orders",
    )
    assert "AGGREGATE_THRESHOLD_FILTER_MISSING" in codes(
        verifier,
        "Find customers with more than 3 orders.",
        """
        SELECT c.id, COUNT(o.id)
        FROM customers AS c
        JOIN orders AS o ON o.customer_id = c.id
        GROUP BY c.id
        """,
    )


def test_count_and_aggregate_fanout_signals_are_distinct(
    verifier: DeterministicSemanticVerifier,
) -> None:
    count_codes = codes(
        verifier,
        "How many unique customers are in each region?",
        """
        SELECT r.name, COUNT(c.id)
        FROM regions AS r
        JOIN customers AS c ON c.region_id = r.id
        JOIN orders AS o ON o.customer_id = c.id
        GROUP BY r.name
        """,
    )
    assert "POSSIBLE_ENTITY_COUNT_FANOUT" in count_codes

    safe_count_codes = codes(
        verifier,
        "How many unique customers are in each region?",
        """
        SELECT r.name, COUNT(DISTINCT c.id)
        FROM regions AS r
        JOIN customers AS c ON c.region_id = r.id
        JOIN orders AS o ON o.customer_id = c.id
        GROUP BY r.name
        """,
    )
    assert "POSSIBLE_ENTITY_COUNT_FANOUT" not in safe_count_codes

    aggregate_codes = codes(
        verifier,
        "Show total order value by product.",
        """
        SELECT p.name, SUM(o.total_amount)
        FROM products AS p
        JOIN order_items AS oi ON oi.product_id = p.id
        JOIN orders AS o ON o.id = oi.order_id
        GROUP BY p.name
        """,
    )
    assert "POSSIBLE_AGGREGATE_FANOUT" in aggregate_codes


def test_projection_filter_and_cartesian_signals(verifier: DeterministicSemanticVerifier) -> None:
    assert "REQUESTED_DIMENSION_NOT_PROJECTED" in codes(
        verifier,
        "Show revenue by region.",
        "SELECT SUM(o.total_amount) FROM orders AS o GROUP BY o.status",
    )
    assert "EXPLICIT_FILTER_MISSING" in codes(
        verifier,
        "Show orders with status completed.",
        "SELECT id, order_number FROM orders",
    )
    assert "UNEXPECTED_CARTESIAN_JOIN" in codes(
        verifier,
        "Show customers and products.",
        "SELECT c.name, p.name FROM customers AS c CROSS JOIN products AS p",
    )


def test_unrequested_limit_and_memory_overtransfer_are_diagnostic(
    verifier: DeterministicSemanticVerifier,
) -> None:
    assert "UNREQUESTED_LIMIT" in codes(
        verifier,
        "List orders.",
        "SELECT id FROM orders LIMIT 5",
    )
    example = build_verified_query_example(
        example_id="vq:order_listing:v1",
        question="List five orders.",
        sql="SELECT id FROM orders LIMIT 5",
        tags=("ORDER_LIMIT",),
    )
    assert "POSSIBLE_EXAMPLE_OVERTRANSFER" in {
        signal.code.value
        for signal in verifier.verify(
            "List orders.",
            "SELECT id FROM orders LIMIT 5",
            memory_examples=(example,),
            generation_path="DIRECT_SQL_WITH_VERIFIED_MEMORY",
        ).signals
    }


def test_equivalent_valid_sql_is_not_flagged_and_reports_are_deterministic(
    verifier: DeterministicSemanticVerifier,
) -> None:
    question = "What is the average order total for each order status?"
    sql = "SELECT status, AVG(total_amount) FROM orders GROUP BY status"
    first = verifier.verify(question, sql)
    second = verifier.verify(question, sql)

    assert first.recommendation.value == "CONTINUE"
    assert first == second
    assert first.ruleset_hash == second.ruleset_hash


def test_gold_is_not_an_accepted_runtime_input(verifier: DeterministicSemanticVerifier) -> None:
    with pytest.raises(TypeError):
        verifier.verify(
            "List orders.",
            "SELECT id FROM orders",
            gold_sql="SELECT id FROM orders",  # type: ignore[call-arg]
        )
