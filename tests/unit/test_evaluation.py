from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.sql.models import QueryExecution
from evaluation.metrics import ResultDiagnostic, assess_query_results, compare_query_results
from evaluation.runner import load_baseline


def execution(columns: list[str], rows: list[dict[str, object]]) -> QueryExecution:
    return QueryExecution(
        plan_id=uuid4(),
        columns=columns,
        rows=rows,
        row_count=len(rows),
        latency_ms=1.0,
    )


def test_result_equivalence_matches_columns_by_ordinal_position() -> None:
    actual = execution(
        ["value", "name"],
        [{"value": Decimal("2.0000001"), "name": "B"}, {"value": 1, "name": "A"}],
    )
    expected = execution(
        ["total", "label"],
        [{"total": 1.0, "label": "A"}, {"total": 2, "label": "B"}],
    )

    assert compare_query_results(actual, expected)


def test_alias_only_difference_is_equivalent() -> None:
    actual = execution(["count"], [{"count": 40}])
    expected = execution(["order_count"], [{"order_count": 40}])

    comparison = assess_query_results(
        actual,
        expected,
        actual_sql="SELECT COUNT(*) AS count FROM orders",
        expected_sql="SELECT COUNT(*) AS order_count FROM orders",
    )

    assert comparison.equivalent
    assert comparison.diagnostic is None


def test_multiple_alias_forms_are_ignored_but_ordinal_values_are_not() -> None:
    alias_pairs = [
        (["customer_id", "rank"], ["customer_id", "__decision_rank_0"]),
        (["computed_value"], ["computed_value_renamed"]),
        (["__decision_window_0"], ["latest_value"]),
        (["rank"], ["ranked_value"]),
        (["value"], ["value_alias"]),
    ]
    for actual_columns, expected_columns in alias_pairs:
        actual = execution(
            actual_columns,
            [dict(zip(actual_columns, (1,) * len(actual_columns), strict=True))],
        )
        expected = execution(
            expected_columns,
            [dict(zip(expected_columns, (1,) * len(expected_columns), strict=True))],
        )
        assert compare_query_results(actual, expected)

    actual = execution(["id", "customer_id"], [{"id": 1, "customer_id": 7}])
    expected = execution(["customer_id", "id"], [{"customer_id": 7, "id": 1}])
    assert not compare_query_results(actual, expected)


def test_different_arity_is_not_equivalent() -> None:
    actual = execution(["count", "total"], [{"count": 2, "total": 10}])
    expected = execution(["count"], [{"count": 2}])

    assert not compare_query_results(actual, expected)


def test_duplicate_row_multiplicity_is_preserved() -> None:
    actual = execution(["value"], [{"value": 1}, {"value": 1}])
    expected = execution(["value"], [{"value": 1}])

    assert not compare_query_results(actual, expected)


def test_numeric_tolerance_is_bounded() -> None:
    actual = execution(["value"], [{"value": Decimal("1.0000005")}])
    expected = execution(["value"], [{"value": 1}])

    assert compare_query_results(actual, expected, numeric_tolerance=1e-6)
    assert not compare_query_results(actual, expected, numeric_tolerance=1e-8)


def test_null_values_are_normalized() -> None:
    actual = execution(["value"], [{"value": None}])
    expected = execution(["different_alias"], [{"different_alias": None}])

    assert compare_query_results(actual, expected)


def test_result_equivalence_preserves_order_when_query_semantics_require_it() -> None:
    actual = execution(["position"], [{"position": 1}, {"position": 2}])
    expected = execution(["position"], [{"position": 2}, {"position": 1}])

    assert not compare_query_results(actual, expected, order_sensitive=True)


def test_result_equivalence_ignores_row_order_when_not_required() -> None:
    actual = execution(["position"], [{"position": 1}, {"position": 2}])
    expected = execution(["position"], [{"position": 2}, {"position": 1}])

    assert compare_query_results(actual, expected)


def test_structural_divergence_is_diagnostic_only() -> None:
    actual = execution(["revenue"], [{"revenue": 10}])
    expected = execution(["revenue"], [{"revenue": 10}])

    comparison = assess_query_results(
        actual,
        expected,
        actual_sql="SELECT SUM(amount) AS revenue FROM order_items",
        expected_sql="SELECT SUM(total_amount) AS revenue FROM orders",
    )

    assert comparison.equivalent
    assert comparison.diagnostic is ResultDiagnostic.STRUCTURAL_DIVERGENCE


def test_m2_baseline_is_reproducible_and_covers_expected_categories() -> None:
    cases = load_baseline(Path("evaluation/datasets/m2_baseline.json"))
    categories = {case.category for case in cases}

    assert len(cases) == 48
    assert categories == {
        "simple_filters",
        "simple_aggregation",
        "joins",
        "multi_table_joins",
        "date_filtering",
        "group_by",
        "top_k",
        "ratios",
        "window_functions",
    }
