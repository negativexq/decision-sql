from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine, delete, text

from app.generation.decision_contract import DecisionDetail, MissingRelationship
from app.metadata.connectors import ConnectionConfig
from app.metadata.relationships import (
    RelationshipDeclaration,
    RelationshipRejectedError,
    SqlRelationshipStore,
)
from app.metadata.secrets import SecretBox
from app.metadata.service import ConnectionService
from app.metadata.store import SqlConnectionStore, SqlRunStore
from app.metadata.tables import audit_runs, connections
from app.metadata.values import (
    SqlValueSharingStore,
    ValueSharingRejectedError,
    ValueSharingUpdate,
)
from app.observability.run_trace import RunTraceCollector
from app.operator.models import RunRecord, RuntimeOutcome
from app.sql.authority import ExecutionAuthority
from app.sql.models import CandidateSource, QueryPlan, SqlCandidate, SqlExecutionError
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


def test_audit_log_keeps_the_explanation_of_a_non_answer(engine: Engine) -> None:
    store = SqlRunStore(engine)
    trace = RunTraceCollector("run_it_detail").finish("NOT_ENTERED")
    detail = DecisionDetail(
        explanation="orders and customers have no authorized join.",
        missing_relationships=(MissingRelationship(from_table="orders", to_table="customers"),),
    )
    store.save(
        RunRecord(
            run_id="run_it_detail",
            trace_id=trace.trace_id,
            question="Revenue by customer",
            proposal_source="LIVE_MODEL",
            model_decision="BLOCKED_AUTHORITY",
            model_reason_code="MISSING_AUTHORIZED_RELATIONSHIP",
            model_detail=detail,
            runtime_outcome=RuntimeOutcome.NOT_ENTERED,
            created_at=trace.started_at.isoformat(),
            trace=trace,
        )
    )

    restored = store.get("run_it_detail")
    assert restored is not None
    assert restored.model_detail == detail
    assert restored.sql_changes == []


def test_executed_sql_and_its_changes_survive_the_audit_store(engine: Engine) -> None:
    store = SqlRunStore(engine)
    trace = RunTraceCollector("run_it_executed").finish("EXECUTED")
    store.save(
        RunRecord(
            run_id="run_it_executed",
            trace_id=trace.trace_id,
            question="Revenue by category",
            proposal_source="LIVE_MODEL",
            model_decision="ANSWER",
            proposed_sql="SELECT 1",
            executed_sql="SELECT 2",
            sql_changes=["Grain safety totals the child rows first."],
            runtime_outcome=RuntimeOutcome.EXECUTED,
            created_at=trace.started_at.isoformat(),
            trace=trace,
        )
    )

    restored = store.get("run_it_executed")
    assert restored is not None
    assert (restored.proposed_sql, restored.executed_sql) == ("SELECT 1", "SELECT 2")
    assert restored.sql_changes == ["Grain safety totals the child rows first."]


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


def test_declared_relationship_is_checked_against_the_live_source(engine: Engine) -> None:
    box = SecretBox(Fernet.generate_key().decode())
    service = ConnectionService(
        SqlConnectionStore(engine), box, engine, relationships=SqlRelationshipStore(engine)
    )
    summary, result = service.create(
        ConnectionConfig(
            kind="postgresql",
            name="it-relationships",
            host="localhost",
            port=5432,
            database="decision_sql",
            username="decision_reader",
            password="decision_reader_password",
            ssl_mode="disable",
        ),
        "ayse@example.com",
    )
    assert result.ok, result.message

    def declare(from_table: str, from_column: str, to_table: str, to_column: str) -> object:
        return service.declare_relationship(
            summary.id,
            RelationshipDeclaration(
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
            ),
            "ayse@example.com",
        )

    with pytest.raises(RelationshipRejectedError, match="already exists"):
        declare("orders", "customer_id", "customers", "id")
    # Payments already reach orders through orders.id; another orders column would be a
    # second path to the same row.
    with pytest.raises(RelationshipRejectedError, match="already points to orders"):
        declare("payments", "order_id", "orders", "customer_id")
    # Many items share an order, so order_id cannot be the "one" side.
    with pytest.raises(RelationshipRejectedError, match="repeated values"):
        declare("products", "id", "order_items", "order_id")

    item = declare("refunds", "id", "orders", "id")
    listed = service.relationships(summary.id)
    assert listed is not None
    assert [entry.id for entry in listed.declared] == [item.id]  # type: ignore[attr-defined]
    source = service.question_source(summary.id)
    assert source is not None
    refunds = source.catalog.get_table("refunds")
    assert refunds is not None
    assert any(
        relationship.referenced_table == "orders" and relationship.column == "id"
        for relationship in refunds.relationships
    )

    assert service.remove_relationship(summary.id, item.id)  # type: ignore[attr-defined]
    assert service.delete(summary.id)


def test_stored_values_reach_the_model_only_after_an_admin_shares_them(engine: Engine) -> None:
    box = SecretBox(Fernet.generate_key().decode())
    sharing = SqlValueSharingStore(engine)
    service = ConnectionService(SqlConnectionStore(engine), box, engine, value_sharing=sharing)
    summary, result = service.create(
        ConnectionConfig(
            kind="postgresql",
            name="it-values",
            host="localhost",
            port=5432,
            database="decision_sql",
            username="decision_reader",
            password="decision_reader_password",
            ssl_mode="disable",
        ),
        "ayse@example.com",
    )
    assert result.ok, result.message

    def shared_columns() -> set[str]:
        source = service.question_source(summary.id)
        assert source is not None
        return {
            f"{table.name}.{column.name}"
            for table in source.catalog.tables
            for column in table.columns
            if column.known_values
        }

    listed = service.values(summary.id)
    assert listed is not None and listed.available and not listed.enabled
    assert listed.columns and not any(column.shared for column in listed.columns)
    assert shared_columns() == set()

    # Unshared values still check the query's literals on the server.
    source = service.question_source(summary.id)
    assert source is not None
    safety = SqlSafetyService(source.engine, catalog=source.catalog, reader_role=source.reader_role)

    def plan(sql: str) -> QueryPlan:
        result = safety.plan(
            SqlCandidate(
                sql=sql,
                source=CandidateSource.LLM,
                correlation_id="it-values",
                execution_authority=ExecutionAuthority.from_table_names(["orders"]),
            )
        )
        assert isinstance(result, QueryPlan), result
        return result

    fixed = plan("SELECT COUNT(*) FROM orders o WHERE o.status = 'Completed'")
    assert "'completed'" in fixed.normalized_sql
    assert fixed.sql_changes and not fixed.unmatched_values
    missing = plan("SELECT COUNT(*) FROM orders WHERE status IN ('completed', 'shipped')")
    assert [(item.column, item.literal, item.shared) for item in missing.unmatched_values] == [
        ("status", "shipped", False)
    ]

    first = f"{listed.columns[0].table}.{listed.columns[0].column}"
    with pytest.raises(ValueSharingRejectedError, match="not in the connection"):
        service.set_values(
            summary.id, ValueSharingUpdate(enabled=True, excluded=["nope.nothing"]), "admin"
        )
    updated = service.set_values(
        summary.id, ValueSharingUpdate(enabled=True, excluded=[first]), "admin"
    )
    assert updated is not None and updated.enabled and updated.updated_by == "admin"
    names = {f"{column.table}.{column.column}" for column in listed.columns}
    assert shared_columns() == names - {first}

    assert service.delete(summary.id)
    assert sharing.get(summary.id).enabled is False
