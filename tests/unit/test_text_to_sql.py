import pytest
from sqlalchemy import create_engine

from app.catalog.default import build_default_catalog
from app.config import get_settings
from app.db.models import Base
from app.generation.intent import IntentProposal, QueryIntent
from app.generation.provider import (
    LLMProviderError,
    ProviderErrorDetail,
    SqlProposal,
    StaticLLMProvider,
)
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.models import ExplainEstimate, QueryPlan, SqlCandidate, SqlSafetyStatus
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import GenerationMode, GenerationStrategy, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService


def make_service(sql: str) -> TextToSqlService:
    catalog = build_default_catalog(Base.metadata)
    resolver = SchemaContextResolver(catalog)
    safety = SqlSafetyService(create_engine("sqlite://"), catalog=catalog, settings=get_settings())
    return TextToSqlService(resolver, StaticLLMProvider(sql), safety)


class RecordingGroundedProvider:
    def __init__(self, sql: str = "SELECT id FROM products LIMIT 1") -> None:
        self.sql = sql
        self.intent_calls = 0
        self.sql_calls = 0
        self.received_intent: QueryIntent | None = None
        self.intent_schema_context: str | None = None
        self.sql_schema_context: str | None = None

    async def propose_intent(self, question: str, schema_context: str) -> IntentProposal:
        del question
        self.intent_calls += 1
        self.intent_schema_context = schema_context
        return IntentProposal(
            intent=QueryIntent(
                selected_tables=("products",),
                selected_columns=("products.id",),
                limit=1,
            ),
            provider="fake",
            model="fake-grounded",
        )

    async def propose_sql(
        self,
        request: object,
        user_context: object,
        schema_context: str,
        query_intent: QueryIntent | None = None,
    ) -> SqlProposal:
        del request, user_context
        self.sql_calls += 1
        self.sql_schema_context = schema_context
        self.received_intent = query_intent
        return SqlProposal(sql=self.sql, provider="fake", model="fake-sql")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    (
        "DELETE FROM orders",
        "SELECT * FROM pg_shadow",
        "SELECT external_key FROM customers",
    ),
)
async def test_malicious_provider_output_is_rejected_by_m1(sql: str) -> None:
    result = await make_service(sql).run(TextToSqlRequest(question="show sales"))

    assert result.status is TextToSqlStatus.PLAN_REJECTED
    assert result.failure_stage is FailureStage.POLICY_REJECTION
    assert result.plan is None
    assert result.execution is None
    assert result.candidate is not None
    assert result.candidate.source.value == "llm"
    assert result.plan_failure is not None
    assert result.plan_failure.status is SqlSafetyStatus.POLICY_REJECTION
    assert result.provider_calls_attempted == 1
    assert result.provider_calls_succeeded == 1
    assert result.provider_calls_failed == 0


@pytest.mark.asyncio
async def test_coordinator_does_not_execute_when_m1_rejects() -> None:
    service = make_service("DELETE FROM orders")
    executed = False

    def fail_if_called(*args: object, **kwargs: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("M1 execution must not run after plan rejection")

    service.safety_service.execute = fail_if_called  # type: ignore[method-assign]
    result = await service.run(TextToSqlRequest(question="show sales"))

    assert result.status is TextToSqlStatus.PLAN_REJECTED
    assert executed is False


@pytest.mark.asyncio
async def test_coordinator_supports_plan_only_without_execution() -> None:
    catalog = build_default_catalog(Base.metadata)
    resolver = SchemaContextResolver(catalog)
    planned = QueryPlan(
        plan_id="11111111-1111-4111-8111-111111111111",
        normalized_sql="SELECT id FROM products LIMIT 1",
        statement_type="Select",
        referenced_tables=("products",),
        estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Limit"),
    )

    class PlanOnlySafety:
        def plan(self, candidate: SqlCandidate) -> QueryPlan:
            return planned

        def execute(self, plan: QueryPlan) -> object:
            raise AssertionError("execute must not run for a plan-only request")

    service = TextToSqlService(
        resolver,
        StaticLLMProvider("SELECT id FROM products LIMIT 1"),
        PlanOnlySafety(),  # type: ignore[arg-type]
    )
    result = await service.run(
        TextToSqlRequest(question="list products", execute=False)
    )

    assert result.status is TextToSqlStatus.PLANNED
    assert result.plan is not None


@pytest.mark.asyncio
async def test_grounded_strategy_calls_intent_then_sql_with_intent() -> None:
    catalog = build_default_catalog(Base.metadata)
    resolver = SchemaContextResolver(catalog)
    planned = QueryPlan(
        plan_id="11111111-1111-4111-8111-111111111111",
        normalized_sql="SELECT id FROM products LIMIT 1",
        statement_type="Select",
        referenced_tables=("products",),
        estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Limit"),
    )
    provider = RecordingGroundedProvider()

    class PlanOnlySafety:
        def plan(self, candidate: SqlCandidate) -> QueryPlan:
            assert candidate.source.value == "llm"
            return planned

        def execute(self, plan: QueryPlan) -> object:
            raise AssertionError("execute must not run for a plan-only request")

    service = TextToSqlService(
        resolver,
        provider,  # type: ignore[arg-type]
        PlanOnlySafety(),  # type: ignore[arg-type]
        strategy=GenerationStrategy.M25_GROUNDED,
    )
    result = await service.run(
        TextToSqlRequest(question="list one product", execute=False)
    )

    assert result.status is TextToSqlStatus.PLANNED
    assert provider.intent_calls == 1
    assert provider.sql_calls == 1
    assert provider.received_intent is not None
    assert result.intent is not None
    assert result.grounding_diagnostics is not None
    assert result.provider_calls_attempted == 2
    assert result.provider_calls_succeeded == 2
    assert result.provider_calls_failed == 0


@pytest.mark.asyncio
async def test_one_shot_strategy_makes_no_intent_call() -> None:
    provider = RecordingGroundedProvider()
    catalog = build_default_catalog(Base.metadata)
    planned = QueryPlan(
        plan_id="11111111-1111-4111-8111-111111111111",
        normalized_sql="SELECT id FROM products LIMIT 1",
        statement_type="Select",
        referenced_tables=("products",),
        estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Limit"),
    )

    class PlanOnlySafety:
        def plan(self, candidate: SqlCandidate) -> QueryPlan:
            return planned

        def execute(self, plan: QueryPlan) -> object:
            raise AssertionError("execute must not run for a plan-only request")

    service = TextToSqlService(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        PlanOnlySafety(),  # type: ignore[arg-type]
        context_mode=SchemaContextMode.FULL_COMPACT,
        generation_mode=GenerationMode.ONE_SHOT,
    )

    result = await service.run(TextToSqlRequest(question="list products", execute=False))

    assert result.status is TextToSqlStatus.PLANNED
    assert provider.intent_calls == 0
    assert provider.sql_calls == 1
    assert provider.sql_schema_context is not None
    assert provider.intent_schema_context is None


@pytest.mark.asyncio
async def test_paired_generation_modes_receive_identical_full_context() -> None:
    catalog = build_default_catalog(Base.metadata)
    safety = SqlSafetyService(
        create_engine("sqlite://"), catalog=catalog, settings=get_settings()
    )
    one_shot_provider = RecordingGroundedProvider()
    grounded_provider = RecordingGroundedProvider()
    one_shot = TextToSqlService(
        SchemaContextResolver(catalog),
        one_shot_provider,  # type: ignore[arg-type]
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        generation_mode=GenerationMode.ONE_SHOT,
    )
    grounded = TextToSqlService(
        SchemaContextResolver(catalog),
        grounded_provider,  # type: ignore[arg-type]
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        generation_mode=GenerationMode.GROUNDED,
    )

    await one_shot.run(TextToSqlRequest(question="list products", execute=False))
    await grounded.run(TextToSqlRequest(question="list products", execute=False))

    assert one_shot_provider.sql_schema_context == grounded_provider.sql_schema_context
    assert grounded_provider.intent_schema_context == one_shot_provider.sql_schema_context


@pytest.mark.asyncio
async def test_invalid_intent_visibility_stops_before_sql_generation() -> None:
    class InvalidIntentProvider(RecordingGroundedProvider):
        async def propose_intent(self, question: str, schema_context: str) -> IntentProposal:
            del question, schema_context
            self.intent_calls += 1
            return IntentProposal(
                intent=QueryIntent(selected_tables=("pg_shadow",)),
                provider="fake",
                model="fake-grounded",
            )

    provider = InvalidIntentProvider()
    catalog = build_default_catalog(Base.metadata)
    service = TextToSqlService(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        SqlSafetyService(create_engine("sqlite://"), catalog=catalog, settings=get_settings()),
        strategy=GenerationStrategy.M25_GROUNDED,
    )

    result = await service.run(TextToSqlRequest(question="list products"))

    assert result.failure_stage is FailureStage.SCHEMA_LINKING_ERROR
    assert provider.intent_calls == 1
    assert provider.sql_calls == 0
    assert result.provider_calls_attempted == 1
    assert result.provider_calls_succeeded == 1
    assert result.provider_calls_failed == 0


@pytest.mark.asyncio
async def test_grounded_malicious_sql_still_reaches_m1_only() -> None:
    provider = RecordingGroundedProvider("DELETE FROM orders")
    catalog = build_default_catalog(Base.metadata)
    service = TextToSqlService(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        SqlSafetyService(create_engine("sqlite://"), catalog=catalog, settings=get_settings()),
        strategy=GenerationStrategy.M25_GROUNDED,
    )

    result = await service.run(TextToSqlRequest(question="list products"))

    assert result.status is TextToSqlStatus.PLAN_REJECTED
    assert result.failure_stage is FailureStage.POLICY_REJECTION
    assert result.execution is None


@pytest.mark.asyncio
async def test_grounding_provider_failure_does_not_call_sql_generation() -> None:
    class FailingIntentProvider(RecordingGroundedProvider):
        async def propose_intent(self, question: str, schema_context: str) -> IntentProposal:
            del question, schema_context
            self.intent_calls += 1
            raise RuntimeError("grounding unavailable")

    provider = FailingIntentProvider()
    catalog = build_default_catalog(Base.metadata)
    service = TextToSqlService(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        SqlSafetyService(create_engine("sqlite://"), catalog=catalog, settings=get_settings()),
        strategy=GenerationStrategy.M25_GROUNDED,
    )

    result = await service.run(TextToSqlRequest(question="list products"))

    assert result.failure_stage is FailureStage.QUERY_INTENT_GENERATION_ERROR
    assert provider.intent_calls == 1
    assert provider.sql_calls == 0
    assert result.provider_calls_attempted == 1
    assert result.provider_calls_succeeded == 0
    assert result.provider_calls_failed == 1


@pytest.mark.asyncio
async def test_sql_generation_failure_has_no_retry() -> None:
    class FailingSqlProvider(RecordingGroundedProvider):
        async def propose_sql(
            self,
            request: object,
            user_context: object,
            schema_context: str,
            query_intent: QueryIntent | None = None,
        ) -> SqlProposal:
            del request, user_context, schema_context, query_intent
            self.sql_calls += 1
            raise RuntimeError("generation unavailable")

    provider = FailingSqlProvider()
    catalog = build_default_catalog(Base.metadata)
    service = TextToSqlService(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        SqlSafetyService(create_engine("sqlite://"), catalog=catalog, settings=get_settings()),
        strategy=GenerationStrategy.M25_GROUNDED,
    )

    result = await service.run(TextToSqlRequest(question="list products"))

    assert result.failure_stage is FailureStage.SQL_GENERATION_ERROR
    assert provider.intent_calls == 1
    assert provider.sql_calls == 1
    assert result.provider_calls_attempted == 2
    assert result.provider_calls_succeeded == 1
    assert result.provider_calls_failed == 1


@pytest.mark.asyncio
async def test_provider_error_detail_is_attached_to_typed_generation_failure() -> None:
    detail = ProviderErrorDetail(
        status_code=403,
        error_type="permission_error",
        error_code="model_not_allowed",
        message="The configured model is not available.",
        model="gpt-5.6-luna",
    )

    class FailingProvider(StaticLLMProvider):
        async def propose_sql(
            self,
            request: object,
            user_context: object,
            schema_context: str,
            query_intent: QueryIntent | None = None,
        ) -> SqlProposal:
            del request, user_context, schema_context, query_intent
            raise LLMProviderError("provider request failed", detail)

    catalog = build_default_catalog(Base.metadata)
    service = TextToSqlService(
        SchemaContextResolver(catalog),
        FailingProvider("SELECT 1"),  # type: ignore[arg-type]
        SqlSafetyService(create_engine("sqlite://"), catalog=catalog, settings=get_settings()),
    )

    result = await service.run(TextToSqlRequest(question="list products"))

    assert result.failure_stage is FailureStage.SQL_GENERATION_ERROR
    assert result.provider_error == detail
    assert result.provider_calls_attempted == 1
    assert result.provider_calls_succeeded == 0
    assert result.provider_calls_failed == 1
