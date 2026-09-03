from __future__ import annotations

import os

import pytest

from evaluation.external.defog.benchmark import DefogBenchmarkExecutor

pytestmark = pytest.mark.skipif(
    os.getenv("DEFOG_BENCHMARK_INTEGRATION") != "1",
    reason="Set DEFOG_BENCHMARK_INTEGRATION=1 for the isolated Defog PostgreSQL test",
)


def test_defog_executor_uses_read_only_postgres_and_rejects_writes():
    executor = DefogBenchmarkExecutor()

    result = executor.execute("academic", "SELECT 1 AS smoke")

    assert result.columns == ("smoke",)
    assert result.rows == ((1,),)
    with pytest.raises(ValueError):
        executor.execute("academic", "UPDATE author SET name = name")
