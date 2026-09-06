from __future__ import annotations

import pytest

from app.models.domain import ExecutionMode, TextToSqlRequest
from evaluation.m14_governed_runtime_canary import (
    _fault_service,
    _quantiles,
    run_fault_soak,
)


def test_quantiles_are_bounded_and_deterministic() -> None:
    assert _quantiles([1.0, 2.0, 3.0, 4.0]) == {
        "p50": 2.0,
        "p95": 4.0,
        "p99": 4.0,
        "max": 4.0,
    }


@pytest.mark.asyncio
async def test_fault_soak_is_request_isolated() -> None:
    result = await run_fault_soak(total=32, concurrency=8)
    assert result["correct"] == 32
    assert result["route_loops"] == 0
    assert result["cross_request_leaks"] == 0
    assert result["direct_planner_calls"] == 0


@pytest.mark.asyncio
async def test_direct_mode_never_calls_governed_provider() -> None:
    service, provider, _ = _fault_service("direct")
    result = await service.run(
        TextToSqlRequest(question="direct", execution_mode=ExecutionMode.DIRECT)
    )
    assert result.status.value == "SUCCEEDED"
    assert provider.grounding_calls == 0
    assert provider.sql_calls == 1


@pytest.mark.asyncio
async def test_post_m1_faults_fail_closed() -> None:
    for kind, state in (
        ("m1_rejection", "GOVERNED_POLICY_INVARIANT_FAILURE"),
        ("execution_failure", "GOVERNED_EXECUTION_FAILURE"),
    ):
        service, provider, _ = _fault_service(kind)
        result = await service.run(
            TextToSqlRequest(question=kind, execution_mode=ExecutionMode.GOVERNED_METRIC)
        )
        assert result.diagnostics["route_state"] == state
        assert provider.sql_calls == 0
