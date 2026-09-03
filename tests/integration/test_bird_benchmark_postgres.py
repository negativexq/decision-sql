from __future__ import annotations

import os

import pytest

from evaluation.external.bird.benchmark import BirdBenchmarkExecutor

pytestmark = pytest.mark.skipif(
    os.getenv("BIRD_BENCHMARK_INTEGRATION") != "1",
    reason="Set BIRD_BENCHMARK_INTEGRATION=1 for the isolated BIRD PostgreSQL test",
)


def test_bird_reader_executes_select_and_rejects_writes():
    executor = BirdBenchmarkExecutor()
    result = executor.execute("SELECT 1 AS smoke")
    assert result.columns == ("smoke",)
    assert result.rows == ((1,),)
    with pytest.raises(ValueError):
        executor.execute("UPDATE public.author SET name = name")
