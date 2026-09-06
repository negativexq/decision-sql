from pathlib import Path
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from app.config import GovernedMetricsMode, Settings
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.provider import GovernedMetricGroundingProposal, LLMProviderError
from app.models.domain import FailureStage, TextToSqlRequest
from app.semantics.catalog import build_m3_catalog
from app.semantics.compiler import MetricCompilationFailure
from app.semantics.routing import (
    GovernedFallbackReason,
    GovernedMetricRouteService,
    GovernedRoutePath,
    GovernedRouteStatus,
    ShadowComparison,
)
from app.sql.models import (
    ExplainEstimate,
    QueryExecution,
    QueryPlan,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus
from evaluation.run_m34 import load_cases


class FakeDirectService:
    def __init__(self) -> None:
        self.calls = 0
        self.result = TextToSqlResult(status=TextToSqlStatus.SUCCEEDED)

    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        del request
        self.calls += 1
        return self.result


class FakeGroundingProvider:
    def __init__(self, grounding: GovernedMetricGroundingDTO) -> None:
        self.grounding = grounding
        self.calls = 0

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        self.calls += 1
        return GovernedMetricGroundingProposal(
            grounding=self.grounding,
            provider="fake",
            model="fake-grounding",
            prompt_tokens=11,
            completion_tokens=4,
            latency_ms=1.5,
        )


class FakeSafetyService:
    def __init__(self, *, reject: bool = False, execute_failure: bool = False) -> None:
        self.plan_calls = 0
        self.execute_calls = 0
        self.reject = reject
        self.execute_failure = execute_failure
        self.plan_result = QueryPlan(
            plan_id=uuid4(),
            normalized_sql="SELECT 1",
            statement_type="Select",
            estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
        )

    def plan(self, candidate: object) -> QueryPlan | SqlPlanFailure:
        del candidate
        self.plan_calls += 1
        if self.reject:
            return SqlPlanFailure(
                status=SqlSafetyStatus.POLICY_REJECTION,
                failure_stage=FailureStage.POLICY_REJECTION,
                error="rejected in test",
            )
        return self.plan_result

    def execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError:
        del plan
        self.execute_calls += 1
        if self.execute_failure:
            return SqlExecutionError(error="execution failed in test")
        return QueryExecution(
            plan_id=self.plan_result.plan_id,
            columns=["metric_value"],
            rows=[{"metric_value": 1}],
            row_count=1,
            latency_ms=1,
        )


def build_route(
    mode: GovernedMetricsMode,
    grounding: GovernedMetricGroundingDTO,
    *,
    shadow_execute: bool = False,
    direct: FakeDirectService | None = None,
    safety: FakeSafetyService | None = None,
    tracer: object | None = None,
) -> tuple[GovernedMetricRouteService, FakeDirectService, FakeGroundingProvider, FakeSafetyService]:
    direct = direct or FakeDirectService()
    provider = FakeGroundingProvider(grounding)
    safety = safety or FakeSafetyService()
    route = GovernedMetricRouteService(
        direct,
        provider,
        safety,
        catalog=build_m3_catalog(),
        mode=mode,
        shadow_execute=shadow_execute,
        tracer=tracer,  # type: ignore[arg-type]
        result_comparator=lambda actual, expected: actual.rows == expected.rows,
    )
    return route, direct, provider, safety


@pytest.mark.asyncio
async def test_off_mode_preserves_direct_path_and_makes_no_grounding_call() -> None:
    route, direct, provider, safety = build_route(
        GovernedMetricsMode.OFF,
        GovernedMetricGroundingDTO(metric_name="completed_revenue"),
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.path is GovernedRoutePath.DIRECT_SQL
    assert decision.status is GovernedRouteStatus.SUCCESS
    assert decision.fallback_reason is GovernedFallbackReason.FEATURE_OFF
    assert direct.calls == 1
    assert provider.calls == 0
    assert safety.plan_calls == 0


@pytest.mark.asyncio
async def test_shadow_without_execution_keeps_direct_result_authoritative() -> None:
    route, direct, provider, safety = build_route(
        GovernedMetricsMode.SHADOW,
        GovernedMetricGroundingDTO(metric_name="completed_revenue"),
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.path is GovernedRoutePath.DIRECT_SQL_WITH_GOVERNED_SHADOW
    assert decision.status is GovernedRouteStatus.READY
    assert decision.fallback_reason is GovernedFallbackReason.SHADOW_ONLY
    assert decision.user_result is direct.result
    assert direct.calls == 1
    assert provider.calls == 1
    assert safety.plan_calls == 1
    assert safety.execute_calls == 0


@pytest.mark.asyncio
async def test_shadow_execution_is_diagnostic_only() -> None:
    direct = FakeDirectService()
    direct.result = TextToSqlResult(
        status=TextToSqlStatus.SUCCEEDED,
        execution=QueryExecution(
            plan_id=uuid4(),
            columns=["metric_value"],
            rows=[{"metric_value": 9}],
            row_count=1,
            latency_ms=1,
        ),
    )
    route, _, provider, safety = build_route(
        GovernedMetricsMode.SHADOW,
        GovernedMetricGroundingDTO(metric_name="completed_revenue"),
        shadow_execute=True,
        direct=direct,
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.user_result is direct.result
    assert decision.shadow_comparison is ShadowComparison.DIFFER
    assert provider.calls == 1
    assert safety.execute_calls == 1


@pytest.mark.asyncio
async def test_on_valid_governed_request_does_not_call_direct_provider() -> None:
    route, direct, provider, safety = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(metric_name="completed_revenue", dimensions=("customer",)),
    )

    decision = await route.run(TextToSqlRequest(question="show revenue by customer"))

    assert decision.path is GovernedRoutePath.GOVERNED_METRIC
    assert decision.status is GovernedRouteStatus.SUCCESS
    assert decision.metric_name == "completed_revenue"
    assert decision.dimensions == ("customer",)
    assert decision.semantic_provenance is not None
    assert decision.semantic_provenance.catalog_id == "decisionsql-demo-semantic-catalog"
    assert decision.semantic_provenance.catalog_version == 1
    assert decision.semantic_provenance.metric_stable_id == "metric:completed_revenue"
    assert decision.semantic_provenance.requested_dimensions == ("dimension:customer",)
    assert len(decision.semantic_provenance.semantic_contract_hash) == 64
    assert direct.calls == 0
    assert provider.calls == 1
    assert safety.plan_calls == 1
    assert safety.execute_calls == 1


@pytest.mark.asyncio
async def test_on_not_applicable_falls_back_to_direct_once() -> None:
    route, direct, provider, safety = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(applicable=False),
    )

    decision = await route.run(TextToSqlRequest(question="list products"))

    assert decision.path is GovernedRoutePath.DIRECT_SQL
    assert decision.status is GovernedRouteStatus.NOT_APPLICABLE
    assert decision.fallback_reason is GovernedFallbackReason.NOT_APPLICABLE
    assert direct.calls == 1
    assert provider.calls == 1
    assert safety.plan_calls == 0


@pytest.mark.asyncio
async def test_invalid_metric_is_not_fuzzy_repaired() -> None:
    route, direct, provider, safety = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(metric_name="nearest_revenue_metric"),
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.status is GovernedRouteStatus.VALIDATION_FAILURE
    assert decision.fallback_reason is GovernedFallbackReason.UNKNOWN_METRIC
    assert direct.calls == 1
    assert provider.calls == 1
    assert safety.plan_calls == 0


@pytest.mark.asyncio
async def test_provider_failure_falls_back_with_bounded_status() -> None:
    class UnavailableProvider(FakeGroundingProvider):
        async def propose_metric_grounding(
            self, question: str, glossary: str
        ) -> GovernedMetricGroundingProposal:
            del question, glossary
            self.calls += 1
            raise LLMProviderError("provider unavailable")

    direct = FakeDirectService()
    provider = UnavailableProvider(GovernedMetricGroundingDTO(metric_name="completed_revenue"))
    route = GovernedMetricRouteService(
        direct,
        provider,
        FakeSafetyService(),
        catalog=build_m3_catalog(),
        mode=GovernedMetricsMode.ON,
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.status is GovernedRouteStatus.GROUNDING_FAILURE
    assert decision.fallback_reason is GovernedFallbackReason.PROVIDER_FAILURE
    assert direct.calls == 1


@pytest.mark.asyncio
async def test_invalid_dimension_is_rejected_without_repair() -> None:
    route, direct, provider, safety = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(
            metric_name="completed_revenue", dimensions=("unknown_dimension",)
        ),
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.status is GovernedRouteStatus.VALIDATION_FAILURE
    assert decision.fallback_reason is GovernedFallbackReason.INVALID_DIMENSION
    assert direct.calls == 1
    assert provider.calls == 1
    assert safety.plan_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_code", "expected_reason"),
    (
        ("AMBIGUOUS_RELATIONSHIP_PATH", GovernedFallbackReason.AMBIGUOUS_RELATIONSHIP_PATH),
        ("FANOUT_UNSAFE_DIMENSION_PATH", GovernedFallbackReason.FANOUT_UNSAFE_PATH),
    ),
)
async def test_compiler_path_failures_are_bounded_and_fall_back(
    failure_code: str,
    expected_reason: GovernedFallbackReason,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route, direct, provider, safety = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(metric_name="completed_revenue"),
    )
    monkeypatch.setattr(
        route.compiler,
        "compile_metric",
        lambda request: MetricCompilationFailure(failure_code, "controlled test failure"),
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.status is GovernedRouteStatus.COMPILATION_FAILURE
    assert decision.fallback_reason is expected_reason
    assert direct.calls == 1
    assert provider.calls == 1
    assert safety.plan_calls == 0


@pytest.mark.asyncio
async def test_governed_execution_failure_does_not_bypass_to_execution() -> None:
    safety = FakeSafetyService(execute_failure=True)
    route, direct, provider, _ = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(metric_name="completed_revenue"),
        safety=safety,
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.status is GovernedRouteStatus.EXECUTION_FAILURE
    assert decision.fallback_reason is GovernedFallbackReason.EXECUTION_FAILURE
    assert direct.calls == 1
    assert provider.calls == 1
    assert safety.execute_calls == 1


@pytest.mark.asyncio
async def test_m1_rejection_never_executes_compiled_candidate() -> None:
    safety = FakeSafetyService(reject=True)
    route, direct, provider, _ = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(metric_name="completed_revenue"),
        safety=safety,
    )

    decision = await route.run(TextToSqlRequest(question="show revenue"))

    assert decision.status is GovernedRouteStatus.M1_REJECTION
    assert decision.fallback_reason is GovernedFallbackReason.M1_REJECTED
    assert direct.calls == 1
    assert provider.calls == 1
    assert safety.execute_calls == 0


def test_route_spans_contain_only_bounded_attributes() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("decision-sql-routing-test")
    route, _, _, _ = build_route(
        GovernedMetricsMode.ON,
        GovernedMetricGroundingDTO(metric_name="completed_revenue", dimensions=("customer",)),
        tracer=tracer,
    )

    import asyncio

    asyncio.run(route.run(TextToSqlRequest(question="private question")))

    spans = exporter.get_finished_spans()
    names = [span.name for span in spans]
    assert "decision_sql.route" in names
    assert "decision_sql.semantic.grounding" in names
    assert "decision_sql.semantic.compile" in names
    route_span = next(span for span in spans if span.name == "decision_sql.route")
    attributes = route_span.attributes or {}
    assert attributes["decision_sql.route.path"] == "GOVERNED_METRIC"
    assert attributes["decision_sql.semantic.metric_name"] == "completed_revenue"
    assert attributes["decision_sql.semantic.catalog_id"] == "decisionsql-demo-semantic-catalog"
    assert attributes["decision_sql.semantic.catalog_version"] == 1
    assert len(attributes["decision_sql.semantic.contract_hash_prefix"]) == 16
    assert attributes["decision_sql.semantic.metric_id"] == "metric:completed_revenue"
    assert attributes["decision_sql.semantic.metric_version"] == 1
    serialized = str(attributes)
    assert "private question" not in serialized
    assert "SELECT" not in serialized


def test_applicability_contract_is_strict() -> None:
    assert GovernedMetricGroundingDTO(applicable=False).metric_name is None
    with pytest.raises(ValidationError):
        GovernedMetricGroundingDTO(applicable=True)
    with pytest.raises(ValidationError):
        GovernedMetricGroundingDTO(applicable=False, metric_name="completed_revenue")


def test_feature_mode_defaults_off_and_rejects_invalid_values() -> None:
    settings = Settings(_env_file=None)
    assert settings.governed_metrics_mode is GovernedMetricsMode.OFF
    assert settings.governed_metrics_shadow_execute is False
    assert (
        Settings(_env_file=None, governed_metrics_mode="shadow").governed_metrics_mode
        is GovernedMetricsMode.SHADOW
    )
    assert (
        Settings(_env_file=None, governed_metrics_mode="on").governed_metrics_mode
        is GovernedMetricsMode.ON
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, governed_metrics_mode="maybe")


def test_routing_fixture_is_frozen_and_balanced() -> None:
    cases = load_cases(Path("evaluation/fixtures/m34_routing.json"))
    assert len(cases) == 40
    assert sum(case.expected_path == "GOVERNED_METRIC" for case in cases) == 20
    assert sum(case.expected_path == "DIRECT_SQL" for case in cases) == 20
    assert all(case.metric_name is not None for case in cases[:20])
    assert all(case.metric_name is None and not case.dimensions for case in cases[20:])
