from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.routes import get_operator_application
from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.generation.provider import StaticLLMProvider
from app.main import app
from app.models.domain import TextToSqlRequest
from app.observability.run_trace import RunTraceCollector, TraceStageStatus
from app.operator.service import OperatorApplication
from app.retrieval.context import SchemaContextResolver
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService


@pytest.fixture
def operator_application() -> OperatorApplication:
    settings = Settings(_env_file=None, reader_role="reader")
    engine = create_engine("sqlite://")
    return OperatorApplication(engine, settings, build_default_catalog(Base.metadata))


@pytest.mark.asyncio
async def test_unauthorized_preset_uses_real_runtime_authority_gate(
    operator_application: OperatorApplication,
) -> None:
    record = await operator_application.run("ignored", "unauthorized_query")

    assert record.model_decision == "ANSWER"
    assert record.runtime_outcome == "AUTHORITY_REJECTED"
    assert record.runtime_reason == "UNAUTHORIZED_RELATION"
    assert record.proposal_source == "SAFETY_REPLAY"
    assert record.proposed_sql == "SELECT subscriber_id FROM external_directory"
    assert any(
        stage.name == "generation" and stage.status is TraceStageStatus.SKIPPED
        for stage in record.trace.stages
    )
    assert any(
        stage.name == "proposal_replay" and stage.status is TraceStageStatus.PASS
        for stage in record.trace.stages
    )
    assert any(
        stage.name == "execution_authority" and stage.status is TraceStageStatus.REJECTED
        for stage in record.trace.stages
    )
    assert all(
        stage.status is TraceStageStatus.SKIPPED
        for stage in record.trace.stages
        if stage.name in {"database_connection", "explain", "cost_gate", "execution"}
    )
    event_types = [event.event_type for event in record.trace.events]
    assert (
        event_types.index("stage.execution_authority.rejected")
        < event_types.index("stage.database_connection.skipped")
        < event_types.index("stage.response.pass")
    )


@pytest.mark.asyncio
async def test_live_clarification_preset_is_not_a_static_replay(
    operator_application: OperatorApplication,
) -> None:
    preset = next(item for item in operator_application.presets if item.id == "needs_clarification")
    assert preset.mode == "LIVE_MODEL"


def test_trace_recorder_preserves_stage_detail_and_safe_metadata() -> None:
    collector = RunTraceCollector("run_test")
    collector.record_stage(
        "execution_authority",
        TraceStageStatus.REJECTED,
        reason="UNAUTHORIZED_RELATION",
        metadata={"relation": "external_directory", "rows": [{"secret": "x"}]},
    )
    trace = collector.finish("AUTHORITY_REJECTED")

    stage = trace.stages[0]
    assert stage.reason == "UNAUTHORIZED_RELATION"
    assert stage.metadata == {"relation": "external_directory"}
    assert trace.events[0].event_type == "stage.execution_authority.rejected"


def test_playground_rejects_raw_sql_payload(operator_application: OperatorApplication) -> None:
    app.dependency_overrides[get_operator_application] = lambda: operator_application
    try:
        client = TestClient(app)
        response = client.post(
            "/api/playground/query",
            json={"question": "show orders", "sql": "SELECT 1"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_schema_and_run_routes_are_available(operator_application: OperatorApplication) -> None:
    app.dependency_overrides[get_operator_application] = lambda: operator_application
    try:
        client = TestClient(app)
        schema = client.get("/api/schema")
        presets = client.get("/api/playground/presets")
        runs = client.get("/api/runs")
        assert schema.status_code == 200
        assert schema.json()["title"] == "Model-visible governed context"
        assert presets.status_code == 200
        assert len(presets.json()) >= 3
        assert runs.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_product_service_records_sql_gate_stages_without_changing_result() -> None:
    catalog = build_default_catalog(Base.metadata)
    collector = RunTraceCollector("run_stages")
    safety = SqlSafetyService(create_engine("sqlite://"), catalog=catalog, stage_recorder=collector)
    service = TextToSqlService(
        SchemaContextResolver(catalog),
        StaticLLMProvider("SELECT id FROM products LIMIT 1"),
        safety,
        stage_recorder=collector,
    )

    result = await service.run(TextToSqlRequest(question="list products", execute=False))

    # SQLite has no PostgreSQL JSON EXPLAIN implementation; the test still
    # verifies that the real service reached the deterministic planning gates
    # before reporting that backend limitation.
    assert result.status.value == "PLAN_REJECTED"
    trace = collector.finish("PLANNED")
    statuses = {stage.name: stage.status for stage in trace.stages}
    assert statuses["sql_parse"] is TraceStageStatus.PASS
    assert statuses["global_policy"] is TraceStageStatus.PASS
    assert statuses["execution_authority"] is TraceStageStatus.PASS
    assert statuses["grain_safety"] is TraceStageStatus.SKIPPED
