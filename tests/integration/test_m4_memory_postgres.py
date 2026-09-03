import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.sql.service import SqlSafetyService
from evaluation.m4_benchmark import build_benchmark, build_memory_corpus
from evaluation.run_m4 import validate_database_inputs


@pytest.fixture(scope="module")
def m4_service() -> SqlSafetyService:
    settings = get_settings()
    engine = build_reader_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not available; run Docker Compose for M4 integration tests")
    return SqlSafetyService(engine, settings=settings)


def test_m4_memory_and_gold_are_m1_compatible_and_executable(
    m4_service: SqlSafetyService,
) -> None:
    result = validate_database_inputs(build_memory_corpus(), build_benchmark(), m4_service)

    assert result["memory_parse"] == "50/50"
    assert result["memory_execute"] == "50/50"
    assert result["benchmark_parse"] == "80/80"
    assert result["benchmark_execute"] == "80/80"
    assert result["m1_compatibility"] == "130/130"
    assert result["passed"] is True
