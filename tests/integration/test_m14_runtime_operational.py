from __future__ import annotations

import pytest

from app.config import Settings
from app.db.session import build_reader_engine
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.provider import GovernedMetricGroundingProposal
from app.models.domain import ExecutionMode, TextToSqlRequest
from app.retrieval.context import SchemaContextResolver
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService


class StaticMetricProvider:
    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        return GovernedMetricGroundingProposal(
            grounding=GovernedMetricGroundingDTO(metric_name="completed_revenue"),
            provider="test",
            model="gpt-5.6-terra",
        )


@pytest.mark.asyncio
async def test_production_governed_runtime_executes_scalar_metric() -> None:
    settings = Settings(_env_file=None, governed_metric_runtime_enabled=True)
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings)
    service = TextToSqlService(
        SchemaContextResolver(safety.catalog), StaticMetricProvider(), safety, settings=settings
    )
    result = await service.run(
        TextToSqlRequest(
            question="report completed revenue",
            correlation_id="m14-integration-scalar",
            execution_mode=ExecutionMode.GOVERNED_METRIC,
        )
    )
    assert result.status.value == "SUCCEEDED"
    assert result.diagnostics["route_state"] == "GOVERNED_SUCCESS"
    assert result.execution is not None
