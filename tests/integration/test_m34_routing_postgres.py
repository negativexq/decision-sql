import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import GovernedMetricsMode, get_settings
from app.db.session import build_reader_engine
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.provider import GovernedMetricGroundingProposal
from app.models.domain import TextToSqlRequest
from app.semantics.catalog import build_m3_catalog
from app.semantics.routing import GovernedMetricRouteService, GovernedRoutePath
from app.sql.service import SqlSafetyService


class NeverDirectService:
    async def run(self, request: TextToSqlRequest):
        del request
        raise AssertionError("ON governed success must not call direct SQL")


class StaticMetricProvider:
    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        return GovernedMetricGroundingProposal(
            grounding=GovernedMetricGroundingDTO(metric_name="completed_revenue"),
            provider="static",
            model="static",
        )


@pytest.fixture(scope="module")
def safety_service() -> SqlSafetyService:
    settings = get_settings()
    engine = build_reader_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not available; run Docker Compose for integration tests")
    return SqlSafetyService(engine, settings=settings)


@pytest.mark.asyncio
async def test_on_governed_metric_compiles_and_executes_through_m1(
    safety_service: SqlSafetyService,
) -> None:
    route = GovernedMetricRouteService(
        NeverDirectService(),
        StaticMetricProvider(),
        safety_service,
        catalog=build_m3_catalog(safety_service.catalog),
        mode=GovernedMetricsMode.ON,
    )

    result = await route.run(TextToSqlRequest(question="show completed revenue"))

    assert result.path is GovernedRoutePath.GOVERNED_METRIC
    assert result.user_result.execution is not None
    assert result.user_result.execution.row_count >= 1
    assert result.governed_plan is not None
    assert result.governed_plan.candidate_source.value == "semantic_metric_compiler"
    assert result.semantic_provenance is not None
    assert result.semantic_provenance.metric_stable_id == "metric:completed_revenue"
    assert result.semantic_provenance.catalog_version == 1
