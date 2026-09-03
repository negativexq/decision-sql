import hashlib
import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.catalog.default import build_default_catalog
from app.config import GovernedMetricsMode, Settings, VerifiedMemoryMode
from app.db.models import Base
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.provider import GovernedMetricGroundingProposal, StaticLLMProvider
from app.memory.models import MemoryCorpusError
from app.memory.provenance import (
    ShadowResultComparison,
    VerifiedMemoryOutcome,
)
from app.memory.retrieval import RetrievedExample
from app.memory.runtime import VerifiedMemoryRuntime
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextResolver
from app.semantics.routing import GovernedMetricRouteService, GovernedRoutePath
from app.sql.models import ExplainEstimate, QueryExecution, QueryPlan
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import GenerationPath, TextToSqlResult, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.m4_benchmark import build_memory_corpus

M41_INTEGRATION_HASH = "31d1d3612ba8377ace6097ccb60dac79cf136a4c2d3af58596ef710d33b97461"


class FakeDirectService:
    def __init__(self, *, memory_result: TextToSqlResult | None = None) -> None:
        self.context_resolver = SchemaContextResolver(build_default_catalog(Base.metadata))
        self.direct_calls = 0
        self.memory_calls = 0
        self.memory_requests: list[TextToSqlRequest] = []
        self.result = TextToSqlResult(
            status=TextToSqlStatus.SUCCEEDED,
            execution=QueryExecution(
                plan_id="11111111-1111-4111-8111-111111111111",
                columns=["value"],
                rows=[{"value": 1}],
                row_count=1,
                latency_ms=1,
            ),
        )
        self.memory_result = memory_result if memory_result is not None else self.result

    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        del request
        self.direct_calls += 1
        return self.result

    async def run_with_context_addition(
        self, request: TextToSqlRequest, context_addition: str
    ) -> TextToSqlResult:
        assert "VERIFIED EXAMPLES" in context_addition
        self.memory_calls += 1
        self.memory_requests.append(request)
        return self.memory_result


def settings(mode: VerifiedMemoryMode, **overrides: object) -> Settings:
    values = {
        "verified_query_memory_mode": mode,
        "verified_query_memory_shadow_sample_rate": 1.0,
        "verified_query_memory_shadow_execute": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_runtime(
    mode: VerifiedMemoryMode, direct: FakeDirectService | None = None, **overrides: object
) -> tuple[VerifiedMemoryRuntime, FakeDirectService]:
    if direct is None:
        direct = FakeDirectService(memory_result=overrides.pop("memory_result", None))
    return (
        VerifiedMemoryRuntime(
            direct,
            build_memory_corpus(),
            settings=settings(mode, **overrides),
        ),
        direct,
    )


@pytest.mark.asyncio
async def test_off_is_lazy_and_preserves_direct_behavior() -> None:
    direct = FakeDirectService()
    runtime = VerifiedMemoryRuntime(direct, (), settings=settings(VerifiedMemoryMode.OFF))

    result = await runtime.run(TextToSqlRequest(question="list products"))

    assert result is direct.result
    assert result.generation_path is GenerationPath.DIRECT_SQL
    assert direct.direct_calls == 1
    assert direct.memory_calls == 0


@pytest.mark.asyncio
async def test_on_uses_one_memory_generation_and_records_provenance() -> None:
    runtime, direct = make_runtime(VerifiedMemoryMode.ON)

    result = await runtime.run(TextToSqlRequest(question="show order totals by status"))

    assert result.generation_path is GenerationPath.DIRECT_SQL_WITH_VERIFIED_MEMORY
    assert result.verified_memory_used is True
    assert result.verified_memory_provenance is not None
    assert result.verified_memory_provenance.corpus_id == (
        "decisionsql-demo-verified-query-memory"
    )
    assert result.verified_memory_provenance.k == 3
    assert result.verified_memory_provenance.outcome is VerifiedMemoryOutcome.SUCCESS
    assert len(result.verified_memory_provenance.retrieved_example_ids) == 3
    assert direct.direct_calls == 0
    assert direct.memory_calls == 1


@pytest.mark.asyncio
async def test_shadow_sampled_calls_memory_but_returns_baseline() -> None:
    runtime, direct = make_runtime(VerifiedMemoryMode.SHADOW)
    baseline = direct.result

    result, provenance = await runtime.run_shadow(
        TextToSqlRequest(question="show order totals", execute=True), baseline
    )

    assert result.execution == baseline.execution
    assert result.generation_path is GenerationPath.DIRECT_SQL
    assert result.verified_memory_used is False
    assert provenance.outcome is VerifiedMemoryOutcome.SUCCESS
    assert direct.direct_calls == 0
    assert direct.memory_calls == 1
    assert direct.memory_requests[0].execute is False


@pytest.mark.asyncio
async def test_shadow_unsampled_makes_no_memory_generation_call() -> None:
    runtime, direct = make_runtime(
        VerifiedMemoryMode.SHADOW,
        verified_query_memory_shadow_sample_rate=0.0,
    )

    result, provenance = await runtime.run_shadow(
        TextToSqlRequest(question="show order totals", correlation_id="request-1"),
        direct.result,
    )

    assert result is not direct.result
    assert provenance.outcome is VerifiedMemoryOutcome.NOT_SAMPLED
    assert direct.memory_calls == 0


@pytest.mark.asyncio
async def test_shadow_no_hit_keeps_existing_baseline_without_duplicate_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, direct = make_runtime(VerifiedMemoryMode.SHADOW)

    def empty_retrieve(*args: object, **kwargs: object) -> tuple[RetrievedExample, ...]:
        del args, kwargs
        return ()

    assert runtime.retriever is not None
    monkeypatch.setattr(runtime.retriever, "retrieve", empty_retrieve)
    _, provenance = await runtime.run_shadow(
        TextToSqlRequest(question="unsupported lookup"), direct.result
    )

    assert provenance.outcome is VerifiedMemoryOutcome.NO_HIT
    assert direct.direct_calls == 0
    assert direct.memory_calls == 0


def test_shadow_sampling_is_stable_for_same_request_identity() -> None:
    runtime, _ = make_runtime(
        VerifiedMemoryMode.SHADOW,
        verified_query_memory_shadow_sample_rate=0.5,
    )
    request = TextToSqlRequest(question="show orders", correlation_id="stable-request")

    assert runtime._is_sampled(request) is runtime._is_sampled(request)


@pytest.mark.asyncio
async def test_no_hit_falls_back_without_second_direct_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, direct = make_runtime(VerifiedMemoryMode.ON)

    def empty_retrieve(*args: object, **kwargs: object) -> tuple[RetrievedExample, ...]:
        del args, kwargs
        return ()

    assert runtime.retriever is not None
    monkeypatch.setattr(runtime.retriever, "retrieve", empty_retrieve)
    result = await runtime.run(TextToSqlRequest(question="unsupported lookup"))

    assert result is not direct.result
    assert result.verified_memory_provenance is not None
    assert result.verified_memory_provenance.outcome is VerifiedMemoryOutcome.NO_HIT
    assert direct.direct_calls == 1
    assert direct.memory_calls == 0


@pytest.mark.asyncio
async def test_retrieval_error_falls_back_to_direct() -> None:
    runtime, direct = make_runtime(VerifiedMemoryMode.ON)

    class BrokenRetriever:
        def retrieve(self, *args: object, **kwargs: object) -> tuple[RetrievedExample, ...]:
            del args, kwargs
            raise RuntimeError("retrieval failed")

    runtime.retriever = BrokenRetriever()  # type: ignore[assignment]
    result = await runtime.run(TextToSqlRequest(question="unsupported lookup"))

    assert result.verified_memory_provenance is not None
    assert result.verified_memory_provenance.outcome is VerifiedMemoryOutcome.RETRIEVAL_FAILURE
    assert direct.direct_calls == 1
    assert direct.memory_calls == 0


@pytest.mark.asyncio
async def test_shadow_result_comparison_is_bounded() -> None:
    memory_result = TextToSqlResult(
        status=TextToSqlStatus.SUCCEEDED,
        execution=QueryExecution(
            plan_id="11111111-1111-4111-8111-111111111111",
            columns=["value"],
            rows=[{"value": 2}],
            row_count=1,
            latency_ms=1,
        ),
    )
    runtime, direct = make_runtime(VerifiedMemoryMode.SHADOW, memory_result=memory_result)

    _, provenance = await runtime.run_shadow(
        TextToSqlRequest(question="show order totals"), direct.result
    )

    assert provenance.shadow_result_comparison is ShadowResultComparison.RESULTS_DIFFER


@pytest.mark.asyncio
async def test_memory_generated_sql_still_passes_through_m1() -> None:
    catalog = build_default_catalog(Base.metadata)
    direct = TextToSqlService(
        SchemaContextResolver(catalog),
        StaticLLMProvider("DELETE FROM products"),
        SqlSafetyService(
            create_engine("sqlite://"), catalog=catalog, settings=Settings(_env_file=None)
        ),
    )
    runtime = VerifiedMemoryRuntime(
        direct,
        build_memory_corpus(),
        settings=settings(VerifiedMemoryMode.ON),
    )

    result = await runtime.run(TextToSqlRequest(question="show products"))

    assert result.status is TextToSqlStatus.PLAN_REJECTED
    assert result.verified_memory_used is False
    assert result.verified_memory_provenance is not None
    assert result.verified_memory_provenance.outcome is VerifiedMemoryOutcome.M1_REJECTION


def test_non_off_modes_validate_frozen_corpus_hash() -> None:
    with pytest.raises(MemoryCorpusError, match="hash"):
        VerifiedMemoryRuntime(
            FakeDirectService(),
            build_memory_corpus(),
            settings=settings(VerifiedMemoryMode.ON),
            expected_corpus_hash="0" * 64,
        )


def test_memory_configuration_validates_mode_and_sampling_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).verified_query_memory_mode is VerifiedMemoryMode.OFF
    assert Settings(_env_file=None).verified_query_memory_shadow_sample_rate == 0.0
    assert Settings(_env_file=None).verified_query_memory_shadow_execute is False
    assert (
        Settings(_env_file=None, verified_query_memory_mode="on").verified_query_memory_mode
        is VerifiedMemoryMode.ON
    )
    monkeypatch.setenv("DECISION_SQL_VERIFIED_QUERY_MEMORY_MODE", "shadow")
    monkeypatch.setenv("DECISION_SQL_VERIFIED_QUERY_MEMORY_SHADOW_SAMPLE_RATE", "0.5")
    assert Settings(_env_file=None).verified_query_memory_mode is VerifiedMemoryMode.SHADOW
    assert Settings(_env_file=None).verified_query_memory_shadow_sample_rate == 0.5
    with pytest.raises(ValidationError):
        Settings(_env_file=None, verified_query_memory_shadow_sample_rate=1.1)


def test_m41_integration_corpus_is_frozen_and_balanced() -> None:
    path = Path(__file__).parents[2] / "evaluation" / "fixtures" / "m41_integration.json"
    payload = json.loads(path.read_text())
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    assert hashlib.sha256(canonical.encode()).hexdigest() == M41_INTEGRATION_HASH
    assert payload["corpus_version"] == 1
    assert len(payload["cases"]) == 40
    assert len({case["id"] for case in payload["cases"]}) == 40
    assert sum(case["expected_path"] == "GOVERNED_METRIC" for case in payload["cases"]) == 10
    assert sum(case["expected_path"] == "DIRECT_SQL" for case in payload["cases"]) == 30
    assert all(case["memory_expected"] is False for case in payload["cases"][-10:])


@pytest.mark.asyncio
async def test_governed_route_does_not_invoke_memory() -> None:
    runtime, direct = make_runtime(VerifiedMemoryMode.ON)

    class GovernedProvider:
        async def propose_metric_grounding(self, question: str, glossary: str) -> object:
            del question, glossary
            return GovernedMetricGroundingProposal(
                grounding=GovernedMetricGroundingDTO(metric_name="completed_revenue"),
                provider="fake",
                model="fake",
            )

    class Safety:
        def plan(self, candidate: object) -> QueryPlan:
            del candidate
            return QueryPlan(
                plan_id="11111111-1111-4111-8111-111111111111",
                normalized_sql="SELECT 1",
                statement_type="Select",
                estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
            )

        def execute(self, plan: QueryPlan) -> QueryExecution:
            del plan
            return direct.result.execution  # type: ignore[return-value]

    route = GovernedMetricRouteService(
        direct,
        GovernedProvider(),  # type: ignore[arg-type]
        Safety(),
        mode=GovernedMetricsMode.ON,
        verified_memory=runtime,
    )
    decision = await route.run(TextToSqlRequest(question="show completed revenue"))

    assert decision.path is GovernedRoutePath.GOVERNED_METRIC
    assert decision.verified_memory_provenance is None
    assert direct.memory_calls == 0
    assert direct.direct_calls == 0


@pytest.mark.asyncio
async def test_on_not_applicable_routes_to_memory_once_without_baseline_duplicate() -> None:
    runtime, direct = make_runtime(VerifiedMemoryMode.ON)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("decision-sql-m41-route-test")
    runtime.tracer = tracer

    class NotApplicableProvider:
        async def propose_metric_grounding(self, question: str, glossary: str) -> object:
            del question, glossary
            return GovernedMetricGroundingProposal(
                grounding=GovernedMetricGroundingDTO(applicable=False),
                provider="fake",
                model="fake",
            )

    route = GovernedMetricRouteService(
        direct,
        NotApplicableProvider(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        mode=GovernedMetricsMode.ON,
        verified_memory=runtime,
        tracer=tracer,
    )
    decision = await route.run(TextToSqlRequest(question="show order totals"))

    assert decision.path is GovernedRoutePath.DIRECT_SQL
    assert decision.user_result.verified_memory_used is True
    assert direct.direct_calls == 0
    assert direct.memory_calls == 1
    route_span = next(
        span for span in exporter.get_finished_spans() if span.name == "decision_sql.route"
    )
    attributes = route_span.attributes or {}
    assert attributes["decision_sql.verified_memory.enabled"] is True
    assert attributes["decision_sql.verified_memory.mode"] == "on"
    assert attributes["decision_sql.verified_memory.corpus_version"] == 1
    assert len(attributes["decision_sql.verified_memory.corpus_hash_prefix"]) == 16
    assert attributes["decision_sql.verified_memory.retriever_version"] == "m4-retriever-v1"
    assert attributes["decision_sql.verified_memory.k"] == 3
    assert "show order totals" not in str(attributes)


@pytest.mark.asyncio
async def test_memory_observability_is_bounded() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("decision-sql-m41-test")
    runtime, direct = make_runtime(VerifiedMemoryMode.ON)
    runtime.tracer = tracer

    await runtime.run(
        TextToSqlRequest(question="private customer question", correlation_id="request-1")
    )

    spans = exporter.get_finished_spans()
    names = [span.name for span in spans]
    assert "decision_sql.memory.retrieve" in names
    assert "decision_sql.memory.prompt" in names
    attributes = next(
        span.attributes or {} for span in spans if span.name == "decision_sql.memory.retrieve"
    )
    serialized = str(attributes)
    assert "private customer question" not in serialized
    assert "SELECT" not in serialized
    assert "authorization" not in serialized.lower()
    assert direct.memory_calls == 1
