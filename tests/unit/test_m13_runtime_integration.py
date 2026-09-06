import pytest

from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.provider import GovernedMetricGroundingProposal, SqlProposal
from app.models.domain import ExecutionMode, FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextResolver
from app.sql.models import (
    ExplainEstimate,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.text_to_sql.models import GenerationPath, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService


class RecordingProvider:
    def __init__(self, grounding: GovernedMetricGroundingDTO) -> None:
        self.grounding = grounding
        self.grounding_calls = 0
        self.sql_calls = 0

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        self.grounding_calls += 1
        return GovernedMetricGroundingProposal(
            grounding=self.grounding, provider="test", model="gpt-5.6-terra"
        )

    async def propose_sql(self, *args: object, **kwargs: object) -> SqlProposal:
        del args, kwargs
        self.sql_calls += 1
        return SqlProposal(
            sql="SELECT 1 AS metric_value", provider="test", model="gpt-5.6-terra"
        )


class RecordingSafety:
    def __init__(self, *, reject: bool = False, execute_failure: bool = False) -> None:
        self.plan_calls = 0
        self.execute_calls = 0
        self.reject = reject
        self.execute_failure = execute_failure
        self.catalog = None
        self.plan_result = QueryPlan(
            plan_id="11111111-1111-4111-8111-111111111111",
            normalized_sql="SELECT 1 AS metric_value",
            statement_type="Select",
            estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
        )

    def plan(self, candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure:
        del candidate
        self.plan_calls += 1
        if self.reject:
            return SqlPlanFailure(
                status=SqlSafetyStatus.POLICY_REJECTION,
                failure_stage=FailureStage.POLICY_REJECTION,
                error="controlled policy rejection",
            )
        return self.plan_result

    def execute(self, plan: QueryPlan) -> QueryExecution | object:
        self.execute_calls += 1
        if self.execute_failure:
            from app.sql.models import SqlExecutionError

            return SqlExecutionError(error="controlled execution failure")
        return QueryExecution(
            plan_id=plan.plan_id,
            columns=["metric_value"],
            rows=[{"metric_value": 1}],
            row_count=1,
            latency_ms=1,
        )


def make_service(
    provider: RecordingProvider,
    safety: RecordingSafety,
    *,
    enabled: bool,
) -> TextToSqlService:
    catalog = build_default_catalog(Base.metadata)
    return TextToSqlService(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        safety,  # type: ignore[arg-type]
        settings=Settings(_env_file=None, governed_metric_runtime_enabled=enabled),
    )


@pytest.mark.asyncio
async def test_direct_default_never_calls_semantic_planner() -> None:
    provider = RecordingProvider(GovernedMetricGroundingDTO(metric_name="completed_revenue"))
    safety = RecordingSafety()
    result = await make_service(provider, safety, enabled=True).run(
        TextToSqlRequest(question="show revenue")
    )
    assert result.status is TextToSqlStatus.SUCCEEDED
    assert result.generation_path is GenerationPath.DIRECT_SQL
    assert provider.grounding_calls == 0
    assert provider.sql_calls == 1
    assert result.diagnostics["route_state"] == "DIRECT_REQUESTED"


@pytest.mark.asyncio
async def test_governed_feature_off_uses_direct_once() -> None:
    provider = RecordingProvider(GovernedMetricGroundingDTO(metric_name="completed_revenue"))
    safety = RecordingSafety()
    result = await make_service(provider, safety, enabled=False).run(
        TextToSqlRequest(question="show revenue", execution_mode=ExecutionMode.GOVERNED_METRIC)
    )
    assert result.status is TextToSqlStatus.SUCCEEDED
    assert provider.grounding_calls == 0
    assert provider.sql_calls == 1
    assert result.diagnostics["route_state"] == "GOVERNED_FALLBACK_FEATURE_DISABLED"


@pytest.mark.asyncio
async def test_governed_success_compiles_without_direct_generation() -> None:
    provider = RecordingProvider(
        GovernedMetricGroundingDTO(metric_name="completed_revenue", dimensions=("customer",))
    )
    safety = RecordingSafety()
    result = await make_service(provider, safety, enabled=True).run(
        TextToSqlRequest(
            question="show revenue by customer", execution_mode=ExecutionMode.GOVERNED_METRIC
        )
    )
    assert result.status is TextToSqlStatus.SUCCEEDED
    assert result.generation_path is GenerationPath.GOVERNED_METRIC
    assert provider.grounding_calls == 1
    assert provider.sql_calls == 0
    assert safety.plan_calls == 1
    assert safety.execute_calls == 1
    assert result.diagnostics["route_state"] == "GOVERNED_SUCCESS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grounding",
    (
        GovernedMetricGroundingDTO(applicable=False),
        GovernedMetricGroundingDTO(metric_name="not_a_metric"),
        GovernedMetricGroundingDTO(metric_name="completed_revenue", dimensions=("bad",)),
    ),
)
async def test_pre_m1_invalid_governed_plan_falls_back_once(
    grounding: GovernedMetricGroundingDTO,
) -> None:
    provider = RecordingProvider(grounding)
    safety = RecordingSafety()
    result = await make_service(provider, safety, enabled=True).run(
        TextToSqlRequest(question="show revenue", execution_mode=ExecutionMode.GOVERNED_METRIC)
    )
    assert result.status is TextToSqlStatus.SUCCEEDED
    assert provider.grounding_calls == 1
    assert provider.sql_calls == 1
    assert result.generation_path is GenerationPath.DIRECT_SQL


@pytest.mark.asyncio
async def test_m1_rejection_is_invariant_failure_without_direct_fallback() -> None:
    provider = RecordingProvider(GovernedMetricGroundingDTO(metric_name="completed_revenue"))
    safety = RecordingSafety(reject=True)
    result = await make_service(provider, safety, enabled=True).run(
        TextToSqlRequest(question="show revenue", execution_mode=ExecutionMode.GOVERNED_METRIC)
    )
    assert result.status is TextToSqlStatus.PLAN_REJECTED
    assert result.generation_path is GenerationPath.GOVERNED_METRIC
    assert provider.grounding_calls == 1
    assert provider.sql_calls == 0
    assert safety.execute_calls == 0
    assert result.diagnostics["route_state"] == "GOVERNED_POLICY_INVARIANT_FAILURE"


@pytest.mark.asyncio
async def test_post_m1_execution_failure_does_not_fallback_or_loop() -> None:
    provider = RecordingProvider(GovernedMetricGroundingDTO(metric_name="completed_revenue"))
    safety = RecordingSafety(execute_failure=True)
    result = await make_service(provider, safety, enabled=True).run(
        TextToSqlRequest(question="show revenue", execution_mode=ExecutionMode.GOVERNED_METRIC)
    )
    assert result.status is TextToSqlStatus.EXECUTION_ERROR
    assert result.generation_path is GenerationPath.GOVERNED_METRIC
    assert provider.grounding_calls == 1
    assert provider.sql_calls == 0
    assert safety.execute_calls == 1
    assert result.diagnostics["route_state"] == "GOVERNED_EXECUTION_FAILURE"


def test_runtime_flag_defaults_off_and_mode_is_strict() -> None:
    settings = Settings(_env_file=None)
    assert settings.governed_metric_runtime_enabled is False
    assert TextToSqlRequest(question="x").execution_mode is ExecutionMode.DIRECT
    with pytest.raises(ValueError):
        TextToSqlRequest(question="x", execution_mode="AUTO")
