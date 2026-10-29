from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.decision.exploration import Exploration
from app.decision.service import DecisionSqlApplication
from app.generation.decision_contract import (
    PRODUCTION_DECISION_CASE_ID,
    DecisionReasonCode,
    DecisionType,
    ProductionDecision,
    production_decision_contract_hash,
    production_decision_schema,
)
from app.generation.provider import (
    EarlierAnswer,
    OpenAICompatibleProvider,
    ProductionDecisionProposal,
    RuntimeRejectionFeedback,
)
from app.observability.run_trace import RunTraceCollector, TraceStageStatus
from app.retrieval.context import SchemaContextResolver
from app.sql.service import SqlSafetyService


class FakeDecisionProvider:
    def __init__(self, decision: ProductionDecision, *repairs: ProductionDecision) -> None:
        self.decision = decision
        self.repairs = list(repairs)
        self.calls = 0
        self.last_schema_context: str | None = None
        self.feedback: list[RuntimeRejectionFeedback] = []
        self.earlier: list[EarlierAnswer | None] = []
        self.explorations: list[Exploration | None] = []

    async def propose_decision(
        self,
        question: str,
        schema_context: str,
        repair: RuntimeRejectionFeedback | None = None,
        earlier: EarlierAnswer | None = None,
        exploration: Exploration | None = None,
    ) -> ProductionDecisionProposal:
        del question
        self.calls += 1
        self.earlier.append(earlier)
        self.explorations.append(exploration)
        self.last_schema_context = schema_context
        if repair is not None:
            self.feedback.append(repair)
        # The first call answers the question; every later one takes the next scripted decision.
        return ProductionDecisionProposal(
            decision=self.decision if self.calls == 1 else self.repairs.pop(0),
            provider="fake",
            model="fake-model",
            latency_ms=1.0,
        )


def test_decision_prompt_hash_is_pinned() -> None:
    assert production_decision_contract_hash() == (
        "60f7a4941430dc07fb2a23787d4fb781d272ea7b13d95d0fe5850eb488750089"
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
    fields = {
        "case_id",
        "decision",
        "sql",
        "reason_code",
        "explanation",
        "missing_relationships",
        "clarification_question",
        "interpretations",
        "probes",
    }
    assert set(schema["properties"]) == fields
    assert set(schema["required"]) == fields
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["decision"]["enum"]) == {
        "ANSWER",
        "BLOCKED_AUTHORITY",
        "NEEDS_CLARIFICATION",
        "BLOCKED_POLICY",
        "EXPLORE",
    }
    assert "NO_REASON" in schema["properties"]["reason_code"]["enum"]


def test_non_answer_detail_keeps_only_fields_of_its_decision() -> None:
    clarification = ProductionDecision(
        case_id=PRODUCTION_DECISION_CASE_ID,
        decision="NEEDS_CLARIFICATION",
        reason_code="AMBIGUOUS_SEMANTICS",
        explanation="  Total could mean   gross or net.  ",
        clarification_question="Gross or net revenue?",
        interpretations=["Gross revenue", "", "Net revenue", "a", "b", "c"],
        missing_relationships=[{"from_table": "orders", "to_table": "customers"}],
    )
    assert clarification.detail is not None
    assert clarification.detail.explanation == "Total could mean gross or net."
    assert clarification.detail.interpretations == ("Gross revenue", "Net revenue", "a", "b")
    assert clarification.detail.missing_relationships == ()

    blocked = ProductionDecision(
        case_id=PRODUCTION_DECISION_CASE_ID,
        decision="BLOCKED_AUTHORITY",
        reason_code="MISSING_AUTHORIZED_RELATIONSHIP",
        explanation="orders and customers have no authorized join.",
        missing_relationships=[{"from_table": "orders", "to_table": "customers"}],
        clarification_question="ignored",
    )
    assert blocked.detail is not None
    assert [(m.from_table, m.to_table) for m in blocked.detail.missing_relationships] == [
        ("orders", "customers")
    ]
    assert blocked.detail.clarification_question is None

    answer = ProductionDecision(
        case_id=PRODUCTION_DECISION_CASE_ID,
        decision="ANSWER",
        sql="SELECT 1",
        explanation="not shown",
    )
    assert answer.detail is None


def test_decision_prompt_has_no_benchmark_framing() -> None:
    from app.generation.decision_contract import production_decision_prompt

    prompt = production_decision_prompt().lower()
    assert "benchmark" not in prompt
    assert "duration formula" not in prompt


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
            explanation="Which products is unclear.",
            clarification_question="Active products or all products?",
            interpretations=["Active products", "All products"],
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
    assert result.model_detail is not None
    assert result.model_detail.clarification_question == "Active products or all products?"
    assert result.model_detail.interpretations == ("Active products", "All products")
    assert result.runtime_outcome == "NOT_ENTERED"
    statuses = {stage.name: stage.status for stage in result.trace.stages}
    assert statuses["sql_parse"] is TraceStageStatus.SKIPPED
    assert statuses["execution"] is TraceStageStatus.SKIPPED


@pytest.mark.asyncio
async def test_answer_uses_same_typed_response_and_separates_runtime_rejection() -> None:
    settings = Settings(_env_file=None, reader_role="reader", repair_enabled=False)
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


def _answer(sql: str) -> ProductionDecision:
    return ProductionDecision(case_id=PRODUCTION_DECISION_CASE_ID, decision="ANSWER", sql=sql)


async def _run_with(
    provider: FakeDecisionProvider,
    question: str = "List product names",
    earlier: EarlierAnswer | None = None,
):
    settings = Settings(_env_file=None, reader_role="reader")
    catalog = build_default_catalog(Base.metadata)
    safety = SqlSafetyService(create_engine("sqlite://"), settings=settings, catalog=catalog)
    collector = RunTraceCollector("run_repair")
    collector.record_stage("request", TraceStageStatus.PASS)
    # As in the operator service: planning stages land in the same trace.
    safety.stage_recorder = collector
    return await DecisionSqlApplication(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        safety,
        settings,
        earlier,
    ).run(question, collector)


@pytest.mark.asyncio
async def test_fixable_rejection_gets_exactly_one_repair_round() -> None:
    provider = FakeDecisionProvider(
        _answer("SELECT name FROM external_directory"),
        _answer("SELECT REGEXP_REPLACE(name, 'a', 'b') FROM products"),
    )
    result = await _run_with(provider)

    assert provider.calls == 2
    [feedback] = provider.feedback
    assert feedback.stage == "global_policy"
    assert feedback.code == "UNKNOWN_TABLE"
    assert feedback.previous.sql == "SELECT name FROM external_directory"
    assert result.repaired_from_sql == "SELECT name FROM external_directory"
    assert result.proposed_sql == "SELECT REGEXP_REPLACE(name, 'a', 'b') FROM products"
    # The repaired SQL is rejected too, and there is no second repair.
    assert result.runtime_reason == "FORBIDDEN_FUNCTION"
    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["repair"].status is TraceStageStatus.PASS
    assert stages["global_policy"].reason == "FORBIDDEN_FUNCTION"


@pytest.mark.asyncio
async def test_repair_may_turn_into_a_clarification() -> None:
    provider = FakeDecisionProvider(
        _answer("SELECT name FROM external_directory"),
        ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID,
            decision="NEEDS_CLARIFICATION",
            reason_code="AMBIGUOUS_SEMANTICS",
            clarification_question="Products or partners?",
            interpretations=["Products", "Partners"],
        ),
    )
    result = await _run_with(provider)

    assert result.model_decision is DecisionType.NEEDS_CLARIFICATION
    assert result.runtime_outcome == "NOT_ENTERED"
    assert result.repaired_from_sql == "SELECT name FROM external_directory"
    assert result.model_detail is not None
    assert result.model_detail.interpretations == ("Products", "Partners")
    stages = {stage.name: stage.status for stage in result.trace.stages}
    assert stages["repair"] is TraceStageStatus.PASS
    assert stages["global_policy"] is TraceStageStatus.SKIPPED


@pytest.mark.asyncio
async def test_final_rejections_are_never_repaired() -> None:
    for sql in ("DELETE FROM products", "SELECT usename FROM pg_catalog.pg_user"):
        provider = FakeDecisionProvider(_answer(sql))
        result = await _run_with(provider)

        assert provider.calls == 1, sql
        assert result.repaired_from_sql is None
        stages = {stage.name: stage for stage in result.trace.stages}
        assert stages["repair"].status is TraceStageStatus.SKIPPED
        assert stages["repair"].reason == "FINAL_REJECTION"


@pytest.mark.asyncio
async def test_failed_repair_keeps_the_original_rejection() -> None:
    provider = FakeDecisionProvider(_answer("SELECT name FROM external_directory"))
    result = await _run_with(provider)  # no repair decision queued: the provider call fails

    assert provider.calls == 2
    assert result.proposed_sql == "SELECT name FROM external_directory"
    assert result.repaired_from_sql is None
    assert result.runtime_reason == "UNKNOWN_TABLE"
    assert {stage.name: stage.status for stage in result.trace.stages}[
        "repair"
    ] is TraceStageStatus.FAILED


def test_repair_conversation_carries_the_rejection_without_rows() -> None:
    from app.generation.provider import _production_decision_messages

    feedback = RuntimeRejectionFeedback(
        previous=_answer("SELECT 1"), stage="sql_parse", code="SQL_PARSE_ERROR", message="bad"
    )
    messages = _production_decision_messages("Q", "{}", feedback)

    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert '"sql":"SELECT 1"' in messages[2]["content"]
    assert '"code": "SQL_PARSE_ERROR"' in messages[3]["content"]


def test_a_follow_up_sends_the_earlier_question_and_proposed_sql() -> None:
    from app.generation.provider import _production_decision_messages

    earlier = EarlierAnswer(question="Revenue by month", sql="SELECT 1")
    [_, user] = _production_decision_messages("And by region?", "{}", None, earlier)
    question, rest = user["content"].split("Earlier answer:", 1)
    assert "And by region?" in question
    assert '"question": "Revenue by month"' in rest and '"sql": "SELECT 1"' in rest
    assert rest.index('"sql"') < rest.index("Governed context:")
    [_, plain] = _production_decision_messages("Q", "{}")
    assert "Earlier answer" not in plain["content"]


@pytest.mark.asyncio
async def test_every_model_call_of_a_follow_up_carries_the_earlier_answer() -> None:
    provider = FakeDecisionProvider(
        _answer("SELECT name FROM external_directory"), _answer("SELECT name FROM products")
    )
    earlier = EarlierAnswer(question="List products", sql="SELECT name FROM products")
    result = await _run_with(provider, "Only the first ten", earlier)
    assert provider.calls == 2 and provider.earlier == [earlier, earlier]
    assert result.proposed_sql == "SELECT name FROM products"
