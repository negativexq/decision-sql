from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.routes import get_operator_application
from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.decision.models import ProposalSource
from app.main import app
from app.observability.run_trace import RunTraceCollector, TraceStageStatus
from app.operator.models import RunRecord
from app.operator.service import OperatorApplication, _earlier_answer


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
        if stage.name
        in {"planning_connection", "explain", "cost_gate", "execution_connection", "execution"}
    )
    event_types = [event.event_type for event in record.trace.events]
    assert (
        event_types.index("stage.execution_authority.rejected")
        < event_types.index("stage.planning_connection.skipped")
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
        assert schema.json()["title"] == "Governed Catalog"
        assert presets.status_code == 200
        assert len(presets.json()) >= 3
        assert runs.status_code == 200
    finally:
        app.dependency_overrides.clear()


def _stored(run_id: str, **update: object) -> RunRecord:
    collector = RunTraceCollector(run_id)
    return RunRecord(
        run_id=run_id,
        trace_id=collector.trace_id,
        question="Revenue by status in 2024",
        proposal_source=ProposalSource.LIVE_MODEL,
        runtime_outcome="EXECUTED",
        created_at="2026-10-29T00:00:00+00:00",
        trace=collector.finish("EXECUTED"),
        proposed_sql=(
            "SELECT status, SUM(total_amount) FROM orders "
            "WHERE ordered_at >= DATE '2024-01-01' GROUP BY status"
        ),
    ).model_copy(update=update)


def test_the_stream_sends_each_stage_then_the_run(
    operator_application: OperatorApplication,
) -> None:
    app.dependency_overrides[get_operator_application] = lambda: operator_application
    try:
        response = TestClient(app).post(
            "/api/playground/query/stream",
            json={"question": "ignored", "preset_id": "policy_blocked"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    stages = [event for event in events if event["type"] == "stage"]
    assert stages and {"name", "label", "status", "reason"} <= stages[0].keys()
    assert any(e["name"] == "global_policy" and e["status"] == "REJECTED" for e in stages)
    assert events[-1]["type"] == "run"
    assert events[-1]["run"]["runtime_outcome"] == "POLICY_REJECTED"
    # Stages carry no SQL or rows; the record at the end is the one the audit log keeps.
    assert all("sql" not in event for event in stages)


def test_an_edit_reruns_the_stored_query_without_the_model(
    operator_application: OperatorApplication,
) -> None:
    operator_application.run_store.save(_stored("run_a"))
    operator_application.run_store.save(
        _stored("run_saved", proposal_source=ProposalSource.SAVED_QUERY)
    )
    app.dependency_overrides[get_operator_application] = lambda: operator_application
    try:
        client = TestClient(app)
        edited = client.post(
            "/api/runs/run_a/edit", json={"edits": [{"index": 0, "values": ["2023-01-01"]}]}
        )
        refused = client.post(
            "/api/runs/run_saved/edit", json={"edits": [{"index": 0, "values": ["2023-01-01"]}]}
        )
        bad = client.post("/api/runs/run_a/edit", json={"edits": [{"index": 0, "values": ["x"]}]})
        # The client names a condition and values; it cannot send SQL.
        raw = client.post("/api/runs/run_a/edit", json={"edits": [], "sql": "SELECT 1"})
        thread = client.get(f"/api/runs/{edited.json()['run_id']}/thread")
    finally:
        app.dependency_overrides.clear()

    assert edited.status_code == 200
    body = edited.json()
    assert body["proposal_source"] == "EDITED_QUERY" and body["edited_from"] == "run_a"
    assert "CAST('2023-01-01' AS DATE)" in body["proposed_sql"]
    assert body["question"] == "Revenue by status in 2024"
    assert body["explanation"]["conditions"][0]["values"] == ["2023-01-01"]
    assert any(
        stage["name"] == "generation" and stage["status"] == "SKIPPED"
        for stage in body["trace"]["stages"]
    )
    assert refused.status_code == 409
    assert bad.status_code == 422 and "not a date" in bad.json()["detail"]
    assert raw.status_code == 422
    assert [record["run_id"] for record in thread.json()] == ["run_a", body["run_id"]]


def test_a_follow_up_on_an_edited_query_says_it_was_edited() -> None:
    edited = _stored("run_b", proposal_source=ProposalSource.EDITED_QUERY, edited_from="run_a")
    earlier = _earlier_answer(edited)

    assert earlier is not None and earlier.sql == edited.proposed_sql
    assert earlier.note is not None and "by hand" in earlier.note
    plain = _earlier_answer(_stored("run_a"))
    assert plain is not None and plain.note is None
    assert "note" not in plain.model_dump(exclude_none=True)


def test_a_follow_up_must_ask_the_earlier_questions_source(
    operator_application: OperatorApplication,
) -> None:
    operator_application.run_store.save(_stored("run_a"))
    app.dependency_overrides[get_operator_application] = lambda: operator_application
    try:
        client = TestClient(app)
        missing = client.post(
            "/api/playground/query", json={"question": "and by month?", "follow_up_of": "nope"}
        )
        elsewhere = client.post(
            "/api/playground/query",
            json={"question": "and by month?", "follow_up_of": "run_a", "connection_id": "c1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 404
    assert elsewhere.status_code == 400
