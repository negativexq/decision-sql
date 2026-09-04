from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.catalog.default import build_default_catalog
from app.config import GovernedMetricsMode, Settings, VerifiedMemoryMode
from app.db.models import Base
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.provider import (
    GovernedMetricGroundingProposal,
    OpenAICompatibleProvider,
)
from app.memory.models import (
    VerifiedQueryExample,
    build_verified_query_example,
    memory_corpus_hash,
)
from app.memory.runtime import VerifiedMemoryRuntime
from app.models.domain import TextToSqlRequest
from app.provenance.canonical import semantic_hash
from app.provenance.models import (
    ProvenanceEvent,
    ProvenanceEventType,
    ProvenanceStage,
    recorder_for,
)
from app.provenance.sink import DiagnosticJsonlProvenanceSink, NoOpProvenanceSink
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.semantics.routing import GovernedMetricRouteService
from app.sql.models import (
    ExplainEstimate,
    PolicyCode,
    PolicyRejection,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.m103_behavior_equivalence import compare_snapshots
from evaluation.m103_provenance_validation import (
    ENVELOPE_FIELDS,
    PROFILE_FIELDS,
    validate_jsonl,
    validate_provenance,
)


class _MemoryDirect:
    context_resolver = SimpleNamespace(
        resolve=lambda question, mode: SimpleNamespace(tables=(), selected_columns=())
    )

    def __init__(self, sink: object) -> None:
        self.sink = sink

    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        return TextToSqlResult(
            status=TextToSqlStatus.PLANNED, correlation_id=request.correlation_id
        )

    async def run_with_context_addition(
        self, request: TextToSqlRequest, context_addition: str
    ) -> TextToSqlResult:
        assert "VERIFIED EXAMPLES" in context_addition
        recorder_for(self.sink, request).emit(  # type: ignore[arg-type]
            ProvenanceStage.GENERATION_CONTEXT,
            ProvenanceEventType.MEMORY_CONTEXT_RENDERED,
            {
                "schema_context_hash": semantic_hash("schema"),
                "generation_context_manifest_hash": semantic_hash(context_addition),
                "rendered_memory_context_hash": semantic_hash(context_addition),
            },
        )
        return TextToSqlResult(
            status=TextToSqlStatus.PLANNED, correlation_id=request.correlation_id
        )


def _memory_example(example_id: str) -> VerifiedQueryExample:
    return build_verified_query_example(
        example_id=example_id,
        question="show products",
        sql="SELECT products.id FROM products",
        tags=("control",),
    )


@pytest.mark.asyncio
async def test_memory_capture_preserves_rank_score_selection_and_context_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.jsonl"
    sink = DiagnosticJsonlProvenanceSink(path)
    examples = (_memory_example("vq:one:v1"), _memory_example("vq:two:v1"))
    request = TextToSqlRequest(question="show products", correlation_id="case-1")
    off_runtime = VerifiedMemoryRuntime(
        _MemoryDirect(NoOpProvenanceSink()),  # type: ignore[arg-type]
        examples,
        settings=Settings(_env_file=None, verified_query_memory_mode=VerifiedMemoryMode.ON),
        provenance_sink=NoOpProvenanceSink(),
        expected_corpus_hash=memory_corpus_hash(examples),
    )
    runtime = VerifiedMemoryRuntime(
        _MemoryDirect(sink),  # type: ignore[arg-type]
        examples,
        settings=Settings(_env_file=None, verified_query_memory_mode=VerifiedMemoryMode.ON),
        provenance_sink=sink,
        expected_corpus_hash=memory_corpus_hash(examples),
    )

    off = await off_runtime.run(request)
    result = await runtime.run(request)
    sink.close()
    report = validate_jsonl(path, "MEMORY_DIAGNOSTIC")

    comparison = compare_snapshots(
        {"status": off.status, "candidate": off.candidate, "plan": off.plan},
        {"status": result.status, "candidate": result.candidate, "plan": result.plan},
        ("status", "candidate", "plan"),
    )
    assert comparison.equivalent
    assert result.status is TextToSqlStatus.PLANNED
    assert report.complete is True
    events = [json.loads(line) for line in path.read_text().splitlines()]
    retrieval = next(event for event in events if event["stage"] == "MEMORY_RETRIEVAL_RESULT")
    selection = next(event for event in events if event["stage"] == "MEMORY_SELECTION")
    assert [item["rank"] for item in retrieval["payload"]["ranked_results"]] == [1, 2]
    assert [item["memory_entry_id"] for item in retrieval["payload"]["ranked_results"]] == [
        "vq:one:v1", "vq:two:v1"
    ]
    assert selection["payload"]["selected_memory_ids"] == [
        item["memory_entry_id"] for item in retrieval["payload"]["ranked_results"]
    ]
    assert selection["payload"]["rendered_memory_context_hash"]


class _GroundingProvider:
    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        return GovernedMetricGroundingProposal(
            grounding=GovernedMetricGroundingDTO(
                applicable=True, metric_name="completed_revenue", dimensions=("region",)
            ),
            provider="fixture",
            model="fixture-model",
        )


class _PlanOnlySafety:
    def plan(self, candidate: SqlCandidate) -> QueryPlan:
        return QueryPlan(
            plan_id=UUID("11111111-1111-4111-8111-111111111111"),
            correlation_id=candidate.correlation_id,
            candidate_source=candidate.source,
            normalized_sql=candidate.sql,
            statement_type="Select",
            estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Seq Scan"),
        )

    def execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError:
        raise AssertionError(f"unexpected execution in fixture: {plan.plan_id}")


class _DirectFallback:
    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        return TextToSqlResult(
            status=TextToSqlStatus.PLANNED, correlation_id=request.correlation_id
        )


@pytest.mark.asyncio
async def test_governed_capture_preserves_dto_and_compiler_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "governed.jsonl"
    sink = DiagnosticJsonlProvenanceSink(path)
    off_route = GovernedMetricRouteService(
        _DirectFallback(),
        _GroundingProvider(),
        _PlanOnlySafety(),
        mode=GovernedMetricsMode.ON,
        provenance_sink=NoOpProvenanceSink(),
    )
    route = GovernedMetricRouteService(
        _DirectFallback(),
        _GroundingProvider(),
        _PlanOnlySafety(),
        mode=GovernedMetricsMode.ON,
        provenance_sink=sink,
    )

    request = TextToSqlRequest(
        question="completed revenue by region", correlation_id="case-2", execute=False
    )
    off = await off_route.run(request)
    decision = await route.run(request)
    sink.close()

    comparison = compare_snapshots(
        {
            "path": off.path,
            "status": off.status,
            "grounding": off.grounding,
            "sql": off.governed_candidate.sql if off.governed_candidate else None,
        },
        {
            "path": decision.path,
            "status": decision.status,
            "grounding": decision.grounding,
            "sql": decision.governed_candidate.sql if decision.governed_candidate else None,
        },
        ("path", "status", "grounding", "sql"),
    )
    assert comparison.equivalent
    assert decision.grounding is not None
    assert validate_jsonl(path, "GOVERNED_DIAGNOSTIC").complete is True
    payloads = [json.loads(line)["payload"] for line in path.read_text().splitlines()]
    assert any(payload.get("selected_dimension_ids") == ["region"] for payload in payloads)
    assert any("compiler_input_hash" in payload for payload in payloads)
    assert all("reference_sql" not in payload for payload in payloads)


async def _fake_post(body: dict[str, object]) -> dict[str, object]:
    return {
        "id": "response-1",
        "model": body["model"],
        "choices": [{"message": {"content": '{"sql":"SELECT 1"}'}}],
    }


def _direct_service(provider: OpenAICompatibleProvider, sink: object) -> TextToSqlService:
    class Safety:
        def plan(self, candidate: SqlCandidate) -> QueryPlan:
            return QueryPlan(
                plan_id=UUID("22222222-2222-4222-8222-222222222222"),
                correlation_id=candidate.correlation_id,
                candidate_source=candidate.source,
                normalized_sql=candidate.sql,
                statement_type="Select",
                estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
            )

    return TextToSqlService(
        SchemaContextResolver(build_default_catalog(Base.metadata)),
        provider,
        Safety(),  # type: ignore[arg-type]
        context_mode=SchemaContextMode.FULL_COMPACT,
        provenance_sink=sink,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_direct_provider_capture_and_sink_off_on_equivalence(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key", llm_model="fixture-model")
    off_provider = OpenAICompatibleProvider(settings)
    on_sink = DiagnosticJsonlProvenanceSink(tmp_path / "direct.jsonl")
    on_provider = OpenAICompatibleProvider(settings, provenance_sink=on_sink)
    off_provider._post = _fake_post  # type: ignore[method-assign]
    on_provider._post = _fake_post  # type: ignore[method-assign]
    request = TextToSqlRequest(question="show one value", correlation_id="case-3", execute=False)

    off = await _direct_service(off_provider, NoOpProvenanceSink()).run(request)
    on = await _direct_service(on_provider, on_sink).run(request)
    on_sink.close()
    report = validate_jsonl(on_sink.path, "DIRECT_DIAGNOSTIC")

    comparison = compare_snapshots(
        {
            "status": off.status,
            "sql": off.candidate.sql if off.candidate else None,
            "normalized_sql": off.plan.normalized_sql if off.plan else None,
        },
        {
            "status": on.status,
            "sql": on.candidate.sql if on.candidate else None,
            "normalized_sql": on.plan.normalized_sql if on.plan else None,
        },
        ("status", "sql", "normalized_sql"),
    )
    assert comparison.equivalent
    assert report.complete is True
    assert len(on_sink.path.read_text().splitlines()) == 4


def test_sink_failures_are_fail_open_and_payload_hashes_are_stable(tmp_path: Path) -> None:
    class FailingSink:
        enabled = True

        def record(self, event: ProvenanceEvent) -> None:
            raise OSError("synthetic sink failure")

    recorder = recorder_for(FailingSink(), TextToSqlRequest(question="x", correlation_id="case"))
    recorder.emit(
        ProvenanceStage.ROUTER,
        ProvenanceEventType.ROUTE_DECIDED,
        {"route_output": "DIRECT_SQL"},
    )
    event_a = ProvenanceEvent.create(
        run_id="r", case_id="c", stage=ProvenanceStage.ROUTER,
        event_type=ProvenanceEventType.ROUTE_DECIDED, payload={"route_output": "DIRECT_SQL"}
    )
    event_b = ProvenanceEvent.create(
        run_id="r", case_id="c", stage=ProvenanceStage.ROUTER,
        event_type=ProvenanceEventType.ROUTE_DECIDED, payload={"route_output": "DIRECT_SQL"}
    )
    assert event_a.payload_hash == event_b.payload_hash == semantic_hash(event_a.payload)
    invalid = validate_provenance([event_a.model_copy(update={"sequence": 1})], "FULL_DIAGNOSTIC")
    assert invalid.complete is False


def test_provenance_sink_rejects_gold_and_secret_fields(tmp_path: Path) -> None:
    sink = DiagnosticJsonlProvenanceSink(tmp_path / "blocked.jsonl")
    sink.record(
        ProvenanceEvent.create(
            run_id="r", case_id="c", stage=ProvenanceStage.ROUTER,
            event_type=ProvenanceEventType.ROUTE_DECIDED,
            payload={"authorization": "Bearer secret"},
        )
    )
    sink.record(
        ProvenanceEvent.create(
            run_id="r", case_id="c", stage=ProvenanceStage.ROUTER,
            event_type=ProvenanceEventType.ROUTE_DECIDED,
            payload={"reference_sql": "SELECT secret"},
        )
    )
    assert sink.error_count == 2
    assert not sink.path.exists()

    sink.record(
        ProvenanceEvent.create(
            run_id="r", case_id="c", stage=ProvenanceStage.EXECUTION,
            event_type=ProvenanceEventType.EXECUTION_COMPLETED,
            payload={"rows": [{"secret": "not persisted"}]},
        )
    )
    assert sink.error_count == 3


def test_jsonl_sink_is_append_only_and_preserves_event_order(tmp_path: Path) -> None:
    sink = DiagnosticJsonlProvenanceSink(tmp_path / "append.jsonl")
    for event_type, stage in (
        (ProvenanceEventType.MEMORY_RETRIEVAL_COMPLETED, ProvenanceStage.MEMORY_RETRIEVAL_RESULT),
        (ProvenanceEventType.MEMORY_SELECTION_COMPLETED, ProvenanceStage.MEMORY_SELECTION),
    ):
        sink.record(
            ProvenanceEvent.create(
                run_id="run", case_id="case", stage=stage,
                event_type=event_type, payload={"event": event_type.value},
            )
        )
    sink.close()
    lines = [json.loads(line) for line in sink.path.read_text().splitlines()]
    assert [line["sequence"] for line in lines] == [1, 2]
    assert [line["stage"] for line in lines] == [
        "MEMORY_RETRIEVAL_RESULT", "MEMORY_SELECTION"
    ]


def test_m1_capture_observes_typed_plan_and_execution_boundaries(tmp_path: Path) -> None:
    sink = DiagnosticJsonlProvenanceSink(tmp_path / "m1.jsonl")
    service = object.__new__(SqlSafetyService)
    service.provenance_sink = sink
    candidate = SqlCandidate(sql="SELECT 1", correlation_id="case-m1")
    service._record_plan(
        candidate,
        SqlPlanFailure(
            status=SqlSafetyStatus.POLICY_REJECTION,
            rejection=PolicyRejection(
                code=PolicyCode.FORBIDDEN_TABLE, message="blocked"
            ),
        ),
    )
    plan = QueryPlan(
        plan_id=UUID("33333333-3333-4333-8333-333333333333"),
        correlation_id="case-m1",
        normalized_sql="SELECT 1",
        statement_type="Select",
        estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
    )
    service._record_execution(
        plan,
        QueryExecution(
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            columns=["?column?"],
            row_count=1,
            latency_ms=1,
        ),
    )
    sink.close()
    events = [json.loads(line) for line in sink.path.read_text().splitlines()]
    assert events[0]["payload"]["m1_rejection_code"] == "FORBIDDEN_TABLE"
    assert events[1]["payload"]["execution_outcome"] == "SUCCEEDED"


def test_unwritable_diagnostic_sink_fails_open(tmp_path: Path) -> None:
    target = tmp_path / "existing-directory"
    target.mkdir()
    sink = DiagnosticJsonlProvenanceSink(target)
    sink.record(
        ProvenanceEvent.create(
            run_id="r",
            case_id="c",
            stage=ProvenanceStage.ROUTER,
            event_type=ProvenanceEventType.ROUTE_DECIDED,
            payload={"route_output": "DIRECT_SQL"},
        )
    )
    assert sink.error_count == 1
    assert sink.last_error


def test_full_profile_fixture_is_complete_and_wrong_order_is_rejected() -> None:
    stages = (
        ProvenanceStage.MEMORY_RETRIEVAL_RESULT,
        ProvenanceStage.MEMORY_SELECTION,
        ProvenanceStage.GENERATION_CONTEXT,
        ProvenanceStage.GOVERNED_GROUNDING_REQUEST,
        ProvenanceStage.PROVIDER_REQUEST,
        ProvenanceStage.PROVIDER_RESPONSE,
        ProvenanceStage.GOVERNED_GROUNDING_RESULT,
        ProvenanceStage.GOVERNED_COMPILER_INPUT,
        ProvenanceStage.GOVERNED_COMPILER_OUTPUT,
        ProvenanceStage.CANDIDATE_EXTRACTION,
        ProvenanceStage.M1_PLAN,
        ProvenanceStage.EXECUTION,
        ProvenanceStage.ROUTER,
    )
    payload_fields = set(PROFILE_FIELDS["FULL_DIAGNOSTIC"]) - ENVELOPE_FIELDS
    events = [
        ProvenanceEvent.create(
            run_id="run",
            case_id="case",
            stage=stage,
            event_type=ProvenanceEventType.ROUTE_DECIDED,
            payload={field: None for field in payload_fields} if index == 0 else {},
        ).model_copy(update={"sequence": index + 1})
        for index, stage in enumerate(stages)
    ]
    complete = validate_provenance(events, "FULL_DIAGNOSTIC")
    assert complete.complete is True

    wrong_order = [events[5], events[4]]
    invalid = validate_provenance(wrong_order, "DIRECT_DIAGNOSTIC")
    assert invalid.complete is False
    assert invalid.order_valid is False
