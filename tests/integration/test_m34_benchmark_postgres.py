"""PostgreSQL execution and fixture-isolation checks for M34."""

# ruff: noqa: E501

import pytest
from psycopg import OperationalError

from benchmark.authoring import SCHEMA_NAMES, connection_kwargs_from_env, seed_database
from benchmark.safety import SqlAdmissionError, execute_query, validate_read_only_select


def test_m34_read_only_admission_rejects_writes() -> None:
    with pytest.raises(SqlAdmissionError):
        validate_read_only_select("DELETE FROM customers")


def test_m34_fixture_transaction_rolls_back() -> None:
    try:
        seed_database("commerce_ops", connection_kwargs_from_env())
        before = execute_query(
            connection_kwargs_from_env(),
            SCHEMA_NAMES["commerce_ops"],
            "SELECT COUNT(*) FROM customers",
        )[1][0][0]
        execute_query(
            connection_kwargs_from_env(),
            SCHEMA_NAMES["commerce_ops"],
            "SELECT COUNT(*) FROM customers",
            patch_sql=[
                "INSERT INTO customers VALUES (999999, 'Rollback fixture', 'North', DATE '2026-06-30', TRUE, 'ROLLBACK')"
            ],
        )
        after = execute_query(
            connection_kwargs_from_env(),
            SCHEMA_NAMES["commerce_ops"],
            "SELECT COUNT(*) FROM customers",
        )[1][0][0]
    except OperationalError:
        pytest.skip("PostgreSQL is not available")
    assert before == after
