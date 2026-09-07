from types import SimpleNamespace

from evaluation.m24_m1_analytical_coverage import _function_inventory


def test_function_inventory_is_aggregate_only_and_deterministic() -> None:
    cases = (
        SimpleNamespace(sol_sql=("SELECT ABS(1), SUM(1) FROM products",)),
        SimpleNamespace(sol_sql=("SELECT PG_SLEEP(1)",)),
    )

    first = _function_inventory(cases)
    second = _function_inventory(cases)

    assert first == second
    assert first["total_function_occurrences"] == 3
    assert first["distinct_functions"] == 3
    assert first["reviewed_safe_distinct"] == 2
    assert first["unknown_fail_closed_distinct"] == 0


def test_function_inventory_does_not_store_case_or_sql_content() -> None:
    case = SimpleNamespace(sol_sql=("SELECT ABS(1) FROM products",))

    result = _function_inventory((case,))

    assert "case_id" not in result
    assert "SELECT ABS(1) FROM products" not in str(result)
