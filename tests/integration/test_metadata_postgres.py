from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine, delete, text

from app.metadata.connectors import ConnectionConfig
from app.metadata.secrets import SecretBox
from app.metadata.service import ConnectionService
from app.metadata.store import SqlConnectionStore, SqlRunStore
from app.metadata.tables import audit_runs, connections
from app.observability.run_trace import RunTraceCollector
from app.operator.models import RunRecord, RuntimeOutcome
from app.sql.authority import ExecutionAuthority
from app.sql.models import CandidateSource, SqlCandidate, SqlExecutionError
from app.sql.service import SqlSafetyService
from tests.integration.conftest import postgres_unavailable

ADMIN_URL = os.getenv(
    "ADMIN_DATABASE_URL",
    "postgresql+psycopg://decision_admin:decision_admin_password@localhost:5432/decision_sql",
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(ADMIN_URL)
    try:
        with engine.connect() as connection:
            present = connection.execute(
                text("SELECT to_regclass('decision_meta.audit_runs') IS NOT NULL")
            ).scalar_one()
    except Exception:
        postgres_unavailable(
            "PostgreSQL is not available; run Docker Compose for integration tests"
        )
    if not present:
        postgres_unavailable("Run `alembic upgrade head` to create the decision_meta schema")
    yield engine
    with engine.begin() as connection:
        connection.execute(delete(audit_runs).where(audit_runs.c.run_id.like("run_it_%")))
        connection.execute(delete(connections).where(connections.c.name.like("it-%")))


def test_audit_log_persists_runs_without_result_rows(engine: Engine) -> None:
    store = SqlRunStore(engine)
    trace = RunTraceCollector("run_it_1").finish("EXECUTED")
    store.save(
        RunRecord(
            run_id="run_it_1",
            trace_id=trace.trace_id,
            question="Revenue by status",
            proposal_source="LIVE_MODEL",
            model_decision="ANSWER",
            runtime_outcome=RuntimeOutcome.EXECUTED,
            proposed_sql="SELECT 1",
            rows=[{"secret": "customer data"}],
            columns=["secret"],
            row_count=1,
            created_at=trace.started_at.isoformat(),
            trace=trace,
            actor="ayse@example.com",
        )
    )

    restored = SqlRunStore(engine).get("run_it_1")
    assert restored is not None
    assert restored.actor == "ayse@example.com"
    assert restored.proposed_sql == "SELECT 1"
    assert restored.rows == []
    assert restored.results_retained is False
    assert [run.run_id for run in store.list(actor="ayse@example.com") if run.run_id == "run_it_1"]


def test_connection_round_trip_against_the_local_database(engine: Engine) -> None:
    box = SecretBox(Fernet.generate_key().decode())
    service = ConnectionService(SqlConnectionStore(engine), box, engine)
    config = ConnectionConfig(
        kind="postgresql",
        name="it-local",
        host="localhost",
        port=5432,
        database="decision_sql",
        username="decision_reader",
        password="decision_reader_password",
        ssl_mode="disable",
    )
    summary, result = service.create(config, "ayse@example.com")
    assert result.ok, result.message
    assert result.can_write is False
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT password_encrypted FROM decision_meta.connections WHERE id = :id"),
            {"id": summary.id},
        ).scalar_one()
    assert b"decision_reader_password" not in bytes(stored)
    tables = service.introspect(summary.id)
    assert tables is not None and "orders" in {table.name for table in tables}

    source = service.question_source(summary.id)
    assert source is not None and source.reader_role == "decision_reader"
    orders = source.catalog.get_table("orders")
    assert orders is not None
    assert orders.get_column("id").primary_key  # type: ignore[union-attr]
    assert any(
        relationship.referenced_table == "customers" for relationship in orders.relationships
    )
    safety = SqlSafetyService(source.engine, catalog=source.catalog, reader_role=source.reader_role)
    plan = safety.plan(
        SqlCandidate(
            sql="SELECT status, COUNT(*) AS n FROM orders GROUP BY status",
            source=CandidateSource.LLM,
            correlation_id="it-connection",
            execution_authority=ExecutionAuthority.from_table_names(["orders"]),
        )
    )
    executed = safety.execute(plan)  # type: ignore[arg-type]
    assert not isinstance(executed, SqlExecutionError)
    assert executed.row_count > 0
    assert service.delete(summary.id)
