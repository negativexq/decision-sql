from collections.abc import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.semantics.query_plan_v1 import QueryPlanV1, QueryPlanV1Catalog, QueryPlanV1Compiler
from app.sql.models import QueryExecution, QueryPlan
from app.sql.service import SqlSafetyService


@pytest.fixture
def safety() -> Generator[SqlSafetyService, None, None]:
    settings = get_settings()
    engine = build_reader_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("PostgreSQL is not available")
    service = SqlSafetyService(engine, settings=settings)
    yield service
    engine.dispose()


@pytest.mark.parametrize(
    "plan",
    (
        QueryPlanV1(
            applicable=True,
            source="orders",
            projection=("orders.id", "orders.status"),
        ),
        QueryPlanV1(
            applicable=True,
            source="orders",
            joins=(
                {"relationship_id": "orders.customer_id", "join_type": "INNER"},
                {"relationship_id": "customers.region_id", "join_type": "LEFT"},
            ),
            projection=("orders.id", "customers.name", "regions.name"),
            filters=(
                {
                    "column_id": "orders.status",
                    "operator": "EQ",
                    "value": {"kind": "string", "value": "completed"},
                },
            ),
        ),
    ),
)
def test_query_plan_v1_compiles_passes_m1_and_executes(
    safety: SqlSafetyService, plan: QueryPlanV1
) -> None:
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    candidate = QueryPlanV1Compiler(catalog).compile(plan)
    planned = safety.plan(candidate)
    assert isinstance(planned, QueryPlan)
    execution = safety.execute(planned)
    assert isinstance(execution, QueryExecution)
    assert execution.truncated is False
    assert len(execution.columns) == len(plan.projection)
