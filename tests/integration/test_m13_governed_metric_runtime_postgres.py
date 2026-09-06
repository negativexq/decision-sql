import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import build_reader_engine
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.provider import GovernedMetricGroundingProposal
from app.models.domain import ExecutionMode, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import GenerationPath, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService


class StaticMetricProvider:
    def __init__(self, dimensions: tuple[str, ...]) -> None:
        self.dimensions = dimensions

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        return GovernedMetricGroundingProposal(
            grounding=GovernedMetricGroundingDTO(
                metric_name="completed_revenue", dimensions=self.dimensions
            ),
            provider="static",
            model="gpt-5.6-terra",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions", ((), ("customer",), ("customer", "order_currency")))
async def test_governed_runtime_compiles_and_executes_m3_shapes(
    dimensions: tuple[str, ...],
) -> None:
    settings = Settings(_env_file=None, governed_metric_runtime_enabled=True)
    engine = build_reader_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not available")
    safety = SqlSafetyService(engine, settings=settings)
    service = TextToSqlService(
        SchemaContextResolver(safety.catalog),
        StaticMetricProvider(dimensions),  # type: ignore[arg-type]
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        settings=settings,
    )
    result = await service.run(
        TextToSqlRequest(
            question="show completed revenue",
            execution_mode=ExecutionMode.GOVERNED_METRIC,
        )
    )
    assert result.status is TextToSqlStatus.SUCCEEDED
    assert result.generation_path is GenerationPath.GOVERNED_METRIC
    assert result.plan is not None
    assert result.execution is not None
    assert result.execution.row_count >= 1
