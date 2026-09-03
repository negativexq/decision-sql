import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.semantics.catalog import build_m3_catalog
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.requests import MetricRequest
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from evaluation.m3_benchmark import build_targets
from evaluation.metrics import compare_query_results


@pytest.fixture(scope="module")
def m3_service() -> SqlSafetyService:
    engine = build_reader_engine(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not available; run Docker Compose for M3 integration tests")
    return SqlSafetyService(engine)


def test_all_m3_reference_targets_reach_compiler_ceiling(m3_service: SqlSafetyService) -> None:
    compiler = MetricCompiler(build_m3_catalog())
    for target in build_targets():
        reference_plan = m3_service.plan(SqlCandidate(sql=target.reference_sql))
        compiled = compiler.compile_metric(
            MetricRequest(metric_name=target.metric_name, dimensions=target.dimensions)
        )
        compiled_plan = (
            m3_service.plan(compiled)
            if not isinstance(compiled, MetricCompilationFailure)
            else compiled
        )
        assert isinstance(reference_plan, QueryPlan), target.target_id
        assert isinstance(compiled_plan, QueryPlan), target.target_id
        reference_result = m3_service.execute(reference_plan)
        compiled_result = m3_service.execute(compiled_plan)
        assert isinstance(reference_result, QueryExecution), target.target_id
        assert isinstance(compiled_result, QueryExecution), target.target_id
        assert compare_query_results(compiled_result, reference_result), target.target_id
