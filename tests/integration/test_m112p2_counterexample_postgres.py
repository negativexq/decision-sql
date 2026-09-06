import os

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_engine
from app.sql.models import SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from evaluation.m112p2_counterexample_diagnostic import DiagnosticState
from evaluation.run_m112p2 import build_harness, load_pairs


def test_postgres_d2_witness_and_read_only_admission() -> None:
    url = os.getenv("M112P2_DATABASE_URL")
    if not url:
        pytest.skip("M112P2_DATABASE_URL is not configured")
    try:
        engine = build_engine(url)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except SQLAlchemyError:
        pytest.skip("isolated M11.2P2 PostgreSQL database is unavailable")
    harness, _ = build_harness()
    literal = next(pair for pair in load_pairs() if pair.pair_id == "NE01_LITERAL_CASE")
    result = harness.run_pair(literal)
    assert result.state is DiagnosticState.NON_EQUIVALENCE_WITNESSED
    service = SqlSafetyService(
        engine,
        settings=get_settings().model_copy(update={"database_url": url}),
    )
    rejected = service.plan(SqlCandidate(sql="DELETE FROM products"))
    assert isinstance(rejected, SqlPlanFailure)
    assert rejected.status.value == "POLICY_REJECTION"
