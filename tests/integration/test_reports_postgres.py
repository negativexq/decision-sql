import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.decision.models import ProposalSource
from app.operator.models import RuntimeOutcome
from app.operator.service import build_operator_application
from tests.integration.conftest import postgres_unavailable


@pytest.mark.asyncio
async def test_a_saved_query_runs_again_through_every_check_without_the_model() -> None:
    settings = get_settings()
    engine = build_reader_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        postgres_unavailable("PostgreSQL is not available; run Docker Compose")
    operator = build_operator_application(engine, settings)

    record = await operator.run(
        "Orders by status",
        saved_sql="SELECT status, COUNT(*) AS orders FROM orders GROUP BY status",
    )
    assert record.proposal_source is ProposalSource.SAVED_QUERY
    assert record.runtime_outcome is RuntimeOutcome.EXECUTED and record.row_count > 0
    assert record.provider == "replay"
    # Tables the question alone would not select are still part of the saved query's context.
    joined = await operator.run(
        "Totals",
        saved_sql="SELECT c.name, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON o.customer_id = c.id GROUP BY c.name",
    )
    assert joined.runtime_outcome is RuntimeOutcome.EXECUTED
    # A saved query gets no exemption: the checks still reject a bad join.
    rejected = await operator.run(
        "Totals",
        saved_sql="SELECT c.name FROM orders o JOIN customers c ON o.id = c.id",
    )
    assert rejected.runtime_outcome is not RuntimeOutcome.EXECUTED
    assert rejected.runtime_reason == "UNAUTHORIZED_JOIN"
