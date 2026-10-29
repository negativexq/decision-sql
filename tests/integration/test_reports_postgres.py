from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.decision.models import ProposalSource
from app.operator.models import RuntimeOutcome
from app.operator.service import OperatorApplication, build_operator_application
from app.reports.filters import FilterChoice, ReportFilter, apply_filters, check_filters
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


@pytest.mark.asyncio
async def test_a_filtered_saved_query_passes_the_gates_and_its_values_are_checked() -> None:
    settings = get_settings()
    engine = build_reader_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        postgres_unavailable("PostgreSQL is not available; run Docker Compose")
    governed = build_operator_application(engine, settings)
    # Saved connections read each column's stored values from statistics; give the
    # governed catalog the same for orders.status.
    catalog = governed.catalog.model_copy(
        update={
            "tables": tuple(
                table.model_copy(
                    update={
                        "columns": tuple(
                            column.model_copy(
                                update={"value_domain": ("cancelled", "completed", "pending")}
                            )
                            if column.name == "status"
                            else column
                            for column in table.columns
                        )
                    }
                )
                if table.name == "orders"
                else table
                for table in governed.catalog.tables
            )
        }
    )
    operator = OperatorApplication(engine, settings, catalog)
    status = ReportFilter(id="s", label="Status", table="orders", column="status", kind="values")
    ordered = ReportFilter(
        id="d", label="Ordered", table="orders", column="ordered_at", kind="date_range"
    )
    check_filters([status, ordered], operator.catalog)

    filtered = apply_filters(
        "SELECT status, COUNT(*) AS orders FROM orders GROUP BY status",
        [status, ordered],
        {"s": FilterChoice(values=["Completed"]), "d": FilterChoice(start=date(2000, 1, 1))},
    )
    record = await operator.run("Orders by status", saved_sql=filtered.sql)
    assert record.runtime_outcome is RuntimeOutcome.EXECUTED, record.runtime_reason
    # The value check gives the literal the stored spelling.
    assert [row["status"] for row in record.rows] == ["completed"]
    assert record.executed_sql is not None and "'completed'" in record.executed_sql
