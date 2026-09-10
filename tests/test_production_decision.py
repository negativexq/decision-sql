from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.decision.service import DecisionSqlApplication
from app.generation.decision_contract import (
    PRODUCTION_DECISION_CASE_ID,
    DecisionReasonCode,
    DecisionType,
    ProductionDecision,
    production_decision_contract_hash,
    production_decision_schema,
)
from app.generation.provider import OpenAICompatibleProvider, ProductionDecisionProposal
from app.observability.run_trace import RunTraceCollector, TraceStageStatus
from app.retrieval.context import SchemaContextResolver
from app.sql.service import SqlSafetyService
from benchmark.model_contract import submission_schema


class FakeDecisionProvider:
    def __init__(self, decision: ProductionDecision) -> None:
        self.decision = decision
        self.calls = 0
        self.last_schema_context: str | None = None

    async def propose_decision(
        self, question: str, schema_context: str
    ) -> ProductionDecisionProposal:
        del question
        self.calls += 1
        self.last_schema_context = schema_context
        return ProductionDecisionProposal(
            decision=self.decision,
            provider="fake",
            model="fake-model",
            latency_ms=1.0,
        )


def test_candidate_c_prompt_hash_is_preserved() -> None:
    assert production_decision_contract_hash() == (
        "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587"
    )


def test_production_decision_invariants_and_extra_fields() -> None:
    assert (
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID, decision="ANSWER", sql="SELECT 1"
        ).decision
        is DecisionType.ANSWER
    )
    assert (
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID,
            decision="BLOCKED_AUTHORITY",
            reason_code="MISSING_AUTHORIZED_RELATIONSHIP",
        ).sql
        is None
    )
    with pytest.raises(ValidationError):
        ProductionDecision(decision="ANSWER")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID,
            decision="NEEDS_CLARIFICATION",
            sql="SELECT 1",
        )
    with pytest.raises(ValidationError):
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID,
            decision="ANSWER",
            sql="SELECT 1",
            unexpected="x",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID,
            decision="ANSWER",
            sql="SELECT 1",
            reason_code=DecisionReasonCode.NO_REASON,
        )
    schema = production_decision_schema()
    assert schema == submission_schema()
    assert set(schema["properties"]) == {"case_id", "decision", "sql", "reason_code"}
    assert schema["required"] == ["case_id", "decision", "sql", "reason_code"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["decision"]["enum"]) == {
        "ANSWER",
        "BLOCKED_AUTHORITY",
        "NEEDS_CLARIFICATION",
        "BLOCKED_POLICY",
    }
    assert "NO_REASON" in schema["properties"]["reason_code"]["enum"]


@pytest.mark.asyncio
async def test_openai_provider_admits_decision_and_sql_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key", reader_role="reader")
    provider = OpenAICompatibleProvider(settings)
    calls = 0

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        assert body["response_format"]
        return {
            "id": "resp_test",
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"case_id":"operator","decision":"ANSWER",'
                            '"sql":"SELECT 1","reason_code":null}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    result = await provider.propose_decision("List products", "[Table] products")

    assert calls == 1
    assert result.decision.decision is DecisionType.ANSWER
    assert result.decision.sql == "SELECT 1"


@pytest.mark.asyncio
async def test_non_answer_never_enters_sql_runtime_and_provider_is_called_once() -> None:
    settings = Settings(_env_file=None, reader_role="reader")
    catalog = build_default_catalog(Base.metadata)
    provider = FakeDecisionProvider(
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID,
            decision="NEEDS_CLARIFICATION",
            reason_code="AMBIGUOUS_SEMANTICS",
        )
    )

    class SpySafety:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, candidate: object) -> object:
            del candidate
            self.calls += 1
            raise AssertionError("non-ANSWER entered SQL runtime")

    safety = SpySafety()
    collector = RunTraceCollector("run_non_answer")
    collector.record_stage("request", TraceStageStatus.PASS)
    result = await DecisionSqlApplication(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        safety,  # type: ignore[arg-type]
        settings,
    ).run("Which products?", collector)

    assert provider.calls == 1
    assert provider.last_schema_context is not None
    assert '"authorized_relationships"' in provider.last_schema_context
    assert '"policy"' in provider.last_schema_context
    assert safety.calls == 0
    assert result.model_decision is DecisionType.NEEDS_CLARIFICATION
    assert result.runtime_outcome == "NOT_ENTERED"
    statuses = {stage.name: stage.status for stage in result.trace.stages}
    assert statuses["sql_parse"] is TraceStageStatus.SKIPPED
    assert statuses["execution"] is TraceStageStatus.SKIPPED


@pytest.mark.asyncio
async def test_answer_uses_same_typed_response_and_separates_runtime_rejection() -> None:
    settings = Settings(_env_file=None, reader_role="reader")
    catalog = build_default_catalog(Base.metadata)
    provider = FakeDecisionProvider(
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID,
            decision="ANSWER",
            sql="SELECT id FROM external_directory",
        )
    )
    safety = SqlSafetyService(create_engine("sqlite://"), settings=settings, catalog=catalog)
    collector = RunTraceCollector("run_runtime_reject")
    collector.record_stage("request", TraceStageStatus.PASS)
    result = await DecisionSqlApplication(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        safety,
        settings,
    ).run("List product IDs", collector)

    assert provider.calls == 1
    assert result.model_decision is DecisionType.ANSWER
    assert result.proposed_sql == "SELECT id FROM external_directory"
    assert result.runtime_outcome == "POLICY_REJECTED"
    assert result.trace.stages


@pytest.mark.asyncio
async def test_case_id_mismatch_fails_admission_before_sql_runtime() -> None:
    settings = Settings(_env_file=None, reader_role="reader")
    catalog = build_default_catalog(Base.metadata)
    provider = FakeDecisionProvider(
        ProductionDecision(case_id="other", decision="ANSWER", sql="SELECT 1")
    )

    class SpySafety:
        def plan(self, candidate: object) -> object:
            del candidate
            raise AssertionError("case-id mismatch entered SQL runtime")

    collector = RunTraceCollector("run_case_id_mismatch")
    collector.record_stage("request", TraceStageStatus.PASS)
    result = await DecisionSqlApplication(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        SpySafety(),  # type: ignore[arg-type]
        settings,
    ).run("List products", collector)

    assert result.model_decision is None
    assert result.runtime_reason == "CASE_ID_MISMATCH"
    assert {stage.name: stage.status for stage in result.trace.stages}[
        "decision_admission"
    ] is TraceStageStatus.FAILED
    assert {stage.name: stage.status for stage in result.trace.stages}[
        "sql_parse"
    ] is TraceStageStatus.SKIPPED
