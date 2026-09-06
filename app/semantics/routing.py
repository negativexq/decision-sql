"""Feature-gated orchestration for the accepted governed metric path."""

from collections.abc import Callable
from enum import StrEnum
from time import perf_counter
from typing import Protocol

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field

from app.config import GovernedMetricsMode, Settings, VerifiedMemoryMode
from app.generation.governed_metric_grounding import (
    GovernedMetricGroundingDTO,
    grounding_to_request,
)
from app.generation.provider import (
    GovernedMetricGroundingProposal,
    LLMProviderError,
)
from app.memory.provenance import VerifiedMemoryProvenance
from app.models.domain import TextToSqlRequest
from app.observability.tracing import get_tracer
from app.provenance.canonical import semantic_hash, text_hash
from app.provenance.models import (
    ProvenanceEventType,
    ProvenanceSink,
    ProvenanceStage,
    recorder_for,
)
from app.provenance.sink import NoOpProvenanceSink
from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.contract import (
    SemanticContractError,
    SemanticExecutionProvenance,
    build_semantic_contract,
    metric_provenance,
)
from app.semantics.models import MetricCatalog
from app.sql.models import (
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)
from app.text_to_sql.models import GenerationPath, TextToSqlResult, TextToSqlStatus


class MetricGroundingProvider(Protocol):
    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        """Return one untrusted governed metric grounding."""


class DirectQueryRunner(Protocol):
    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        """Run the existing direct SQL path."""


class GovernedRoutePath(StrEnum):
    DIRECT_SQL = "DIRECT_SQL"
    GOVERNED_METRIC = "GOVERNED_METRIC"
    DIRECT_SQL_WITH_GOVERNED_SHADOW = "DIRECT_SQL_WITH_GOVERNED_SHADOW"


class GovernedRouteStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    READY = "READY"
    GROUNDING_FAILURE = "GROUNDING_FAILURE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    COMPILATION_FAILURE = "COMPILATION_FAILURE"
    M1_REJECTION = "M1_REJECTION"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    SUCCESS = "SUCCESS"


class GovernedFallbackReason(StrEnum):
    FEATURE_OFF = "FEATURE_OFF"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID_GROUNDING = "INVALID_GROUNDING"
    UNKNOWN_METRIC = "UNKNOWN_METRIC"
    INVALID_DIMENSION = "INVALID_DIMENSION"
    AMBIGUOUS_RELATIONSHIP_PATH = "AMBIGUOUS_RELATIONSHIP_PATH"
    FANOUT_UNSAFE_PATH = "FANOUT_UNSAFE_PATH"
    COMPILER_FAILURE = "COMPILER_FAILURE"
    M1_REJECTED = "M1_REJECTED"
    SHADOW_ONLY = "SHADOW_ONLY"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


class GovernedRuntimeRouteState(StrEnum):
    """Bounded M13 route states recorded on the normal result envelope."""

    DIRECT_REQUESTED = "DIRECT_REQUESTED"
    GOVERNED_SUCCESS = "GOVERNED_SUCCESS"
    GOVERNED_FALLBACK_FEATURE_DISABLED = "GOVERNED_FALLBACK_FEATURE_DISABLED"
    GOVERNED_FALLBACK_PROVIDER_FAILURE = "GOVERNED_FALLBACK_PROVIDER_FAILURE"
    GOVERNED_FALLBACK_NOT_APPLICABLE = "GOVERNED_FALLBACK_NOT_APPLICABLE"
    GOVERNED_FALLBACK_INVALID_PLAN = "GOVERNED_FALLBACK_INVALID_PLAN"
    GOVERNED_FALLBACK_COMPILER_REJECTED = "GOVERNED_FALLBACK_COMPILER_REJECTED"
    GOVERNED_POLICY_INVARIANT_FAILURE = "GOVERNED_POLICY_INVARIANT_FAILURE"
    GOVERNED_EXECUTION_FAILURE = "GOVERNED_EXECUTION_FAILURE"


class ShadowComparison(StrEnum):
    MATCH = "SHADOW_MATCH"
    DIFFER = "SHADOW_DIFFER"


class GovernedRouteDecision(BaseModel):
    """Bounded internal route outcome; no raw prompt, SQL, or result rows in telemetry."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    mode: GovernedMetricsMode
    path: GovernedRoutePath
    status: GovernedRouteStatus
    applicable: bool | None = None
    metric_name: str | None = None
    dimensions: tuple[str, ...] = ()
    grounding: GovernedMetricGroundingDTO | None = None
    fallback_reason: GovernedFallbackReason | None = None
    user_result: TextToSqlResult
    governed_candidate: SqlCandidate | None = None
    governed_plan: QueryPlan | None = None
    governed_execution: QueryExecution | None = None
    shadow_comparison: ShadowComparison | None = None
    semantic_provenance: SemanticExecutionProvenance | None = None
    verified_memory_provenance: VerifiedMemoryProvenance | None = None
    diagnostics: dict[str, int | float | str | bool] = Field(default_factory=dict)


class _SafetyService(Protocol):
    def plan(self, candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure: ...

    def execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError: ...


class _VerifiedMemoryResidualRunner(Protocol):
    async def run(self, request: TextToSqlRequest) -> TextToSqlResult: ...

    async def run_shadow(
        self, request: TextToSqlRequest, baseline: TextToSqlResult
    ) -> tuple[TextToSqlResult, VerifiedMemoryProvenance]: ...


class GovernedMetricRouteService:
    """Select direct or governed execution without duplicating semantic rules."""

    _ROUTER_VERSION_HASH = semantic_hash("decision-sql-governed-route-v1")

    def __init__(
        self,
        direct_service: DirectQueryRunner,
        provider: MetricGroundingProvider,
        safety_service: _SafetyService,
        *,
        catalog: MetricCatalog | None = None,
        mode: GovernedMetricsMode = GovernedMetricsMode.OFF,
        shadow_execute: bool = False,
        tracer: trace.Tracer | None = None,
        result_comparator: Callable[[QueryExecution, QueryExecution], bool] | None = None,
        verified_memory: _VerifiedMemoryResidualRunner | None = None,
        provenance_sink: ProvenanceSink | None = None,
        allow_post_m1_fallback: bool = True,
        metric_context_provider: Callable[[str], str] | None = None,
    ) -> None:
        self.direct_service = direct_service
        self.provider = provider
        self.safety_service = safety_service
        self.catalog = catalog or build_m3_catalog()
        self.semantic_contract = build_semantic_contract(self.catalog)
        self.compiler = MetricCompiler(self.catalog, semantic_contract=self.semantic_contract)
        self.glossary = public_metric_glossary(self.catalog, self.semantic_contract)
        self.mode = mode
        self.shadow_execute = shadow_execute
        self.tracer = tracer or get_tracer()
        self.result_comparator = result_comparator
        self.verified_memory = verified_memory
        self.provenance_sink = provenance_sink or NoOpProvenanceSink()
        self.allow_post_m1_fallback = allow_post_m1_fallback
        self.metric_context_provider = metric_context_provider
        self._metric_names = frozenset(metric.name for metric in self.catalog.metrics)

    @classmethod
    def from_settings(
        cls,
        direct_service: DirectQueryRunner,
        provider: MetricGroundingProvider,
        safety_service: _SafetyService,
        settings: Settings,
        *,
        catalog: MetricCatalog | None = None,
        tracer: trace.Tracer | None = None,
        result_comparator: Callable[[QueryExecution, QueryExecution], bool] | None = None,
        verified_memory: _VerifiedMemoryResidualRunner | None = None,
        provenance_sink: ProvenanceSink | None = None,
        allow_post_m1_fallback: bool = True,
        metric_context_provider: Callable[[str], str] | None = None,
    ) -> "GovernedMetricRouteService":
        """Build a route from settings without changing the direct service defaults."""
        return cls(
            direct_service,
            provider,
            safety_service,
            catalog=catalog,
            mode=settings.governed_metrics_mode,
            shadow_execute=settings.governed_metrics_shadow_execute,
            tracer=tracer,
            result_comparator=result_comparator,
            verified_memory=verified_memory,
            provenance_sink=provenance_sink,
            allow_post_m1_fallback=allow_post_m1_fallback,
            metric_context_provider=metric_context_provider,
        )

    async def run(self, request: TextToSqlRequest) -> GovernedRouteDecision:
        started = perf_counter()
        event_recorder = recorder_for(self.provenance_sink, request)
        with self.tracer.start_as_current_span("decision_sql.route") as span:
            if self.mode is GovernedMetricsMode.OFF:
                direct = await self.direct_service.run(request)
                decision = self._direct_decision(
                    direct,
                    fallback_reason=GovernedFallbackReason.FEATURE_OFF,
                )
            else:
                shadow_direct = (
                    await self.direct_service.run(request)
                    if self.mode is GovernedMetricsMode.SHADOW
                    else None
                )
                decision = await self._governed_attempt(request, shadow_direct)
            route_latency_ms = (perf_counter() - started) * 1000
            decision = decision.model_copy(
                update={
                    "diagnostics": {
                        **decision.diagnostics,
                        "route_total_latency_ms": route_latency_ms,
                    }
                }
            )
            self._record_route(span, decision, route_latency_ms)
            event_recorder.emit(
                ProvenanceStage.ROUTER,
                ProvenanceEventType.ROUTE_DECIDED,
                {
                    "router_version_hash": self._ROUTER_VERSION_HASH,
                    "route_input_hash": semantic_hash(request.model_dump(mode="json")),
                    "route_output": decision.path.value,
                },
            )
            return decision

    async def _governed_attempt(
        self, request: TextToSqlRequest, direct: TextToSqlResult | None
    ) -> GovernedRouteDecision:
        proposal: GovernedMetricGroundingProposal | None = None
        grounding: GovernedMetricGroundingDTO | None = None
        event_recorder = recorder_for(self.provenance_sink, request)
        started = perf_counter()
        metric_context = (
            self.metric_context_provider(request.question)
            if self.metric_context_provider is not None
            else self.glossary
        )
        grounding_request_hash = semantic_hash(
            {
                "operation": "metric_grounding",
                "question": request.question,
                "context": metric_context,
            }
        )
        event_recorder.emit(
            ProvenanceStage.GOVERNED_GROUNDING_REQUEST,
            ProvenanceEventType.GOVERNED_GROUNDING_REQUESTED,
            {"grounding_request_hash": grounding_request_hash},
        )
        event_recorder.emit(
            ProvenanceStage.PROVIDER_REQUEST,
            ProvenanceEventType.PROVIDER_REQUEST_READY,
            {"provider_request_hash": grounding_request_hash},
        )
        with self.tracer.start_as_current_span("decision_sql.semantic.grounding") as span:
            try:
                proposal = await self.provider.propose_metric_grounding(
                    request.question, metric_context
                )
                if not isinstance(proposal, GovernedMetricGroundingProposal):
                    raise TypeError("provider returned invalid governed grounding")
                grounding = proposal.grounding
                event_recorder.emit(
                    ProvenanceStage.PROVIDER_RESPONSE,
                    ProvenanceEventType.PROVIDER_RESPONSE_RECEIVED,
                    {
                        "provider_response_id": None,
                        "raw_provider_output": grounding.model_dump_json(),
                    },
                )
                span.set_attribute("decision_sql.semantic.grounding_status", "success")
                span.set_attribute(
                    "decision_sql.semantic.grounding_latency_ms",
                    proposal.latency_ms or (perf_counter() - started) * 1000,
                )
                if proposal.prompt_tokens is not None:
                    span.set_attribute("decision_sql.semantic.input_tokens", proposal.prompt_tokens)
                if proposal.completion_tokens is not None:
                    span.set_attribute(
                        "decision_sql.semantic.output_tokens", proposal.completion_tokens
                    )
                event_recorder.emit(
                    ProvenanceStage.GOVERNED_GROUNDING_RESULT,
                    ProvenanceEventType.GOVERNED_GROUNDING_COMPLETED,
                    {
                        "grounding_output": grounding.model_dump(mode="json"),
                        "selected_metric_id": (
                            f"metric:{grounding.metric_name}"
                            if grounding.metric_name is not None
                            else None
                        ),
                        "selected_dimension_ids": list(grounding.dimensions),
                        "provider_request_hash": grounding_request_hash,
                        "provider_response_id": None,
                        "raw_provider_output": grounding.model_dump_json(),
                        "candidate_extraction_status": "NOT_APPLICABLE",
                        "candidate_sql_hash": None,
                    },
                )
            except Exception as error:
                span.set_attribute("decision_sql.semantic.grounding_status", "failure")
                event_recorder.emit(
                    ProvenanceStage.PROVIDER_RESPONSE,
                    ProvenanceEventType.PROVIDER_RESPONSE_RECEIVED,
                    {"provider_response_id": None, "raw_provider_output": None},
                )
                event_recorder.emit(
                    ProvenanceStage.GOVERNED_GROUNDING_RESULT,
                    ProvenanceEventType.GOVERNED_GROUNDING_COMPLETED,
                    {
                        "grounding_output": None,
                        "selected_metric_id": None,
                        "selected_dimension_ids": [],
                        "provider_request_hash": grounding_request_hash,
                        "provider_response_id": None,
                        "raw_provider_output": None,
                        "candidate_extraction_status": "NOT_APPLICABLE",
                        "candidate_sql_hash": None,
                    },
                )
                return await self._fallback(
                    request,
                    direct,
                    status=GovernedRouteStatus.GROUNDING_FAILURE,
                    reason=(
                        GovernedFallbackReason.PROVIDER_FAILURE
                        if isinstance(error, LLMProviderError)
                        else GovernedFallbackReason.INVALID_GROUNDING
                    ),
                    applicable=None,
                    grounding=None,
                )

        if grounding is None:
            return await self._fallback(
                request,
                direct,
                status=GovernedRouteStatus.GROUNDING_FAILURE,
                reason=GovernedFallbackReason.INVALID_GROUNDING,
                applicable=None,
                grounding=None,
            )
        if not grounding.applicable:
            return await self._fallback(
                request,
                direct,
                status=GovernedRouteStatus.NOT_APPLICABLE,
                reason=GovernedFallbackReason.NOT_APPLICABLE,
                applicable=False,
                grounding=grounding,
                use_verified_memory=True,
            )

        try:
            metric_request = grounding_to_request(grounding, self.catalog)
        except ValueError as error:
            reason = _validation_reason(str(error))
            return await self._fallback(
                request,
                direct,
                status=GovernedRouteStatus.VALIDATION_FAILURE,
                reason=reason,
                applicable=True,
                grounding=grounding,
            )
        compiler_input_hash = semantic_hash(metric_request.model_dump(mode="json"))
        event_recorder.emit(
            ProvenanceStage.GOVERNED_COMPILER_INPUT,
            ProvenanceEventType.GOVERNED_COMPILER_INPUT_READY,
            {"compiler_input_hash": compiler_input_hash},
        )
        try:
            provenance = metric_provenance(
                self.semantic_contract, metric_request.metric_name, metric_request.dimensions
            )
        except SemanticContractError:
            return await self._fallback(
                request,
                direct,
                status=GovernedRouteStatus.VALIDATION_FAILURE,
                reason=GovernedFallbackReason.INVALID_GROUNDING,
                applicable=True,
                grounding=grounding,
            )

        with self.tracer.start_as_current_span("decision_sql.semantic.compile") as span:
            compile_started = perf_counter()
            try:
                compiled = self.compiler.compile_metric(metric_request)
                span.set_attribute(
                    "decision_sql.semantic.compiler_latency_ms",
                    (perf_counter() - compile_started) * 1000,
                )
            except Exception:
                span.set_attribute("decision_sql.semantic.compiler_status", "failure")
                event_recorder.emit(
                    ProvenanceStage.GOVERNED_COMPILER_OUTPUT,
                    ProvenanceEventType.GOVERNED_COMPILER_COMPLETED,
                    {
                        "compiler_version_hash": self.semantic_contract.semantic_contract_hash,
                        "compiled_sql_hash": None,
                    },
                )
                return await self._fallback(
                    request,
                    direct,
                    status=GovernedRouteStatus.COMPILATION_FAILURE,
                    reason=GovernedFallbackReason.COMPILER_FAILURE,
                    applicable=True,
                    grounding=grounding,
                )
            if isinstance(compiled, MetricCompilationFailure):
                span.set_attribute("decision_sql.semantic.compiler_status", "failure")
                event_recorder.emit(
                    ProvenanceStage.GOVERNED_COMPILER_OUTPUT,
                    ProvenanceEventType.GOVERNED_COMPILER_COMPLETED,
                    {
                        "compiler_version_hash": self.semantic_contract.semantic_contract_hash,
                        "compiled_sql_hash": None,
                    },
                )
                reason = _compilation_reason(compiled.code)
                return await self._fallback(
                    request,
                    direct,
                    status=GovernedRouteStatus.COMPILATION_FAILURE,
                    reason=reason,
                    applicable=True,
                    grounding=grounding,
                )
            span.set_attribute("decision_sql.semantic.compiler_status", "success")
            event_recorder.emit(
                ProvenanceStage.GOVERNED_COMPILER_OUTPUT,
                ProvenanceEventType.GOVERNED_COMPILER_COMPLETED,
                {
                    "compiler_version_hash": self.semantic_contract.semantic_contract_hash,
                    "compiled_sql_hash": text_hash(compiled.sql),
                },
            )

        compiled = compiled.model_copy(update={"correlation_id": request.correlation_id})

        compile_latency = (perf_counter() - compile_started) * 1000
        plan_started = perf_counter()
        planned = self.safety_service.plan(compiled)
        m1_latency = (perf_counter() - plan_started) * 1000
        if isinstance(planned, SqlPlanFailure):
            if not self.allow_post_m1_fallback:
                return self._governed_policy_failure(
                    request,
                    grounding,
                    compiled,
                    planned,
                    provenance,
                    diagnostics={
                        "compile_latency_ms": compile_latency,
                        "m1_latency_ms": m1_latency,
                    },
                )
            return await self._fallback(
                request,
                direct,
                status=GovernedRouteStatus.M1_REJECTION,
                reason=GovernedFallbackReason.M1_REJECTED,
                applicable=True,
                grounding=grounding,
                candidate=compiled,
                diagnostics={"compile_latency_ms": compile_latency, "m1_latency_ms": m1_latency},
            )
        if self.mode is GovernedMetricsMode.SHADOW and not self.shadow_execute:
            return self._shadow_decision(
                direct,
                grounding,
                compiled,
                planned,
                status=GovernedRouteStatus.READY,
                provenance=provenance,
                diagnostics={"compile_latency_ms": compile_latency, "m1_latency_ms": m1_latency},
            )

        if not request.execute:
            return self._governed_decision(
                request,
                grounding,
                compiled,
                planned,
                None,
                status=GovernedRouteStatus.READY,
                provenance=provenance,
                diagnostics={"compile_latency_ms": compile_latency, "m1_latency_ms": m1_latency},
                proposal=proposal,
            )

        execution = self.safety_service.execute(planned)
        if isinstance(execution, SqlExecutionError):
            if not self.allow_post_m1_fallback:
                return self._governed_execution_failure(
                    request,
                    grounding,
                    compiled,
                    planned,
                    execution,
                    provenance,
                    diagnostics={
                        "compile_latency_ms": compile_latency,
                        "m1_latency_ms": m1_latency,
                    },
                )
            return await self._fallback(
                request,
                direct,
                status=GovernedRouteStatus.EXECUTION_FAILURE,
                reason=GovernedFallbackReason.COMPILER_FAILURE,
                applicable=True,
                grounding=grounding,
                candidate=compiled,
                plan=planned,
                diagnostics={"m1_latency_ms": m1_latency},
                governed_execution=execution,
            )

        if self.mode is GovernedMetricsMode.SHADOW:
            return self._shadow_decision(
                direct,
                grounding,
                compiled,
                planned,
                execution=execution,
                status=GovernedRouteStatus.SUCCESS,
                provenance=provenance,
                diagnostics={"m1_latency_ms": m1_latency},
            )
        return self._governed_decision(
            request,
            grounding,
            compiled,
            planned,
            execution,
            status=GovernedRouteStatus.SUCCESS,
            provenance=provenance,
            diagnostics={"compile_latency_ms": compile_latency, "m1_latency_ms": m1_latency},
            proposal=proposal,
        )

    async def _fallback(
        self,
        request: TextToSqlRequest,
        direct: TextToSqlResult | None,
        *,
        status: GovernedRouteStatus,
        reason: GovernedFallbackReason,
        applicable: bool | None,
        grounding: GovernedMetricGroundingDTO | None,
        candidate: SqlCandidate | None = None,
        plan: QueryPlan | None = None,
        provenance: SemanticExecutionProvenance | None = None,
        diagnostics: dict[str, int | float | str | bool] | None = None,
        governed_execution: QueryExecution | SqlExecutionError | None = None,
        use_verified_memory: bool = False,
    ) -> GovernedRouteDecision:
        memory_provenance = None
        if use_verified_memory and self.verified_memory is not None:
            if self.mode is GovernedMetricsMode.SHADOW and direct is not None:
                direct, memory_provenance = await self.verified_memory.run_shadow(
                    request, direct
                )
            elif self.mode is GovernedMetricsMode.ON and direct is None:
                direct = await self.verified_memory.run(request)
                memory_provenance = direct.verified_memory_provenance
        if direct is None:
            direct = await self.direct_service.run(request)
        return GovernedRouteDecision(
            mode=self.mode,
            path=(
                GovernedRoutePath.DIRECT_SQL_WITH_GOVERNED_SHADOW
                if self.mode is GovernedMetricsMode.SHADOW
                else GovernedRoutePath.DIRECT_SQL
            ),
            status=status,
            applicable=applicable,
            metric_name=(grounding.metric_name if grounding and grounding.applicable else None),
            dimensions=tuple(grounding.dimensions) if grounding else (),
            grounding=grounding,
            fallback_reason=reason,
            user_result=direct,
            governed_candidate=candidate,
            governed_plan=plan,
            governed_execution=(
                governed_execution if isinstance(governed_execution, QueryExecution) else None
            ),
            semantic_provenance=provenance,
            verified_memory_provenance=memory_provenance,
            diagnostics=diagnostics or {},
        )

    def _direct_decision(
        self, direct: TextToSqlResult, *, fallback_reason: GovernedFallbackReason
    ) -> GovernedRouteDecision:
        status = (
            GovernedRouteStatus.SUCCESS
            if direct.status is TextToSqlStatus.SUCCEEDED
            else (
                GovernedRouteStatus.READY
                if direct.status is TextToSqlStatus.PLANNED
                else GovernedRouteStatus.EXECUTION_FAILURE
            )
        )
        return GovernedRouteDecision(
            mode=self.mode,
            path=GovernedRoutePath.DIRECT_SQL,
            status=status,
            applicable=False,
            fallback_reason=fallback_reason,
            user_result=direct,
        )

    def _shadow_decision(
        self,
        direct: TextToSqlResult | None,
        grounding: GovernedMetricGroundingDTO,
        candidate: SqlCandidate,
        plan: QueryPlan,
        *,
        execution: QueryExecution | None = None,
        status: GovernedRouteStatus,
        provenance: SemanticExecutionProvenance,
        diagnostics: dict[str, int | float | str | bool],
    ) -> GovernedRouteDecision:
        assert direct is not None
        comparison = None
        if execution is not None and direct.execution is not None and self.result_comparator:
            comparison = (
                ShadowComparison.MATCH
                if self.result_comparator(execution, direct.execution)
                else ShadowComparison.DIFFER
            )
        if comparison is not None:
            diagnostics = {**diagnostics, "shadow_comparison": comparison.value}
        return GovernedRouteDecision(
            mode=self.mode,
            path=GovernedRoutePath.DIRECT_SQL_WITH_GOVERNED_SHADOW,
            status=status,
            applicable=True,
            metric_name=grounding.metric_name,
            dimensions=tuple(grounding.dimensions),
            grounding=grounding,
            fallback_reason=(
                GovernedFallbackReason.SHADOW_ONLY
                if execution is None
                else None
            ),
            user_result=direct,
            governed_candidate=candidate,
            governed_plan=plan,
            governed_execution=execution,
            semantic_provenance=provenance,
            shadow_comparison=comparison,
            diagnostics=diagnostics,
        )

    def _governed_decision(
        self,
        request: TextToSqlRequest,
        grounding: GovernedMetricGroundingDTO,
        candidate: SqlCandidate,
        plan: QueryPlan,
        execution: QueryExecution | None,
        *,
        status: GovernedRouteStatus,
        provenance: SemanticExecutionProvenance,
        diagnostics: dict[str, int | float | str | bool],
        proposal: GovernedMetricGroundingProposal | None = None,
    ) -> GovernedRouteDecision:
        user_result = TextToSqlResult(
            status=(
                TextToSqlStatus.SUCCEEDED
                if request.execute
                else TextToSqlStatus.PLANNED
            ),
            correlation_id=request.correlation_id,
            plan=plan,
            execution=execution,
            candidate=candidate,
            provider=proposal.provider if proposal is not None else "openai-compatible",
            model=proposal.model if proposal is not None else None,
            generation_latency_ms=proposal.latency_ms if proposal is not None else None,
            provider_calls_attempted=1,
            provider_calls_succeeded=1,
            generation_path=GenerationPath.GOVERNED_METRIC,
        )
        return GovernedRouteDecision(
            mode=self.mode,
            path=GovernedRoutePath.GOVERNED_METRIC,
            status=status,
            applicable=True,
            metric_name=grounding.metric_name,
            dimensions=tuple(grounding.dimensions),
            grounding=grounding,
            user_result=user_result,
            governed_candidate=candidate,
            governed_plan=plan,
            governed_execution=execution,
            semantic_provenance=provenance,
            diagnostics=diagnostics,
        )

    def _governed_policy_failure(
        self,
        request: TextToSqlRequest,
        grounding: GovernedMetricGroundingDTO,
        candidate: SqlCandidate,
        failure: SqlPlanFailure,
        provenance: SemanticExecutionProvenance,
        diagnostics: dict[str, int | float | str | bool],
    ) -> GovernedRouteDecision:
        result = TextToSqlResult(
            status=TextToSqlStatus.PLAN_REJECTED,
            correlation_id=request.correlation_id,
            failure_stage=failure.failure_stage,
            error=failure.error,
            plan_failure=failure,
            candidate=candidate,
            provider="openai-compatible",
            provider_calls_attempted=1,
            provider_calls_succeeded=1,
            generation_path=GenerationPath.GOVERNED_METRIC,
            diagnostics={**diagnostics, "governed_policy_invariant_failure": True},
        )
        return GovernedRouteDecision(
            mode=self.mode,
            path=GovernedRoutePath.GOVERNED_METRIC,
            status=GovernedRouteStatus.M1_REJECTION,
            applicable=True,
            metric_name=grounding.metric_name,
            dimensions=tuple(grounding.dimensions),
            grounding=grounding,
            fallback_reason=GovernedFallbackReason.M1_REJECTED,
            user_result=result,
            governed_candidate=candidate,
            semantic_provenance=provenance,
            diagnostics=diagnostics,
        )

    def _governed_execution_failure(
        self,
        request: TextToSqlRequest,
        grounding: GovernedMetricGroundingDTO,
        candidate: SqlCandidate,
        plan: QueryPlan,
        failure: SqlExecutionError,
        provenance: SemanticExecutionProvenance,
        diagnostics: dict[str, int | float | str | bool],
    ) -> GovernedRouteDecision:
        result = TextToSqlResult(
            status=TextToSqlStatus.EXECUTION_ERROR,
            correlation_id=request.correlation_id,
            failure_stage=failure.failure_stage,
            error=failure.error,
            plan=plan,
            execution_error=failure,
            candidate=candidate,
            provider="openai-compatible",
            provider_calls_attempted=1,
            provider_calls_succeeded=1,
            generation_path=GenerationPath.GOVERNED_METRIC,
            diagnostics=diagnostics,
        )
        return GovernedRouteDecision(
            mode=self.mode,
            path=GovernedRoutePath.GOVERNED_METRIC,
            status=GovernedRouteStatus.EXECUTION_FAILURE,
            applicable=True,
            metric_name=grounding.metric_name,
            dimensions=tuple(grounding.dimensions),
            grounding=grounding,
            user_result=result,
            governed_candidate=candidate,
            governed_plan=plan,
            semantic_provenance=provenance,
            diagnostics=diagnostics,
        )

    def _record_route(
        self, span: trace.Span, decision: GovernedRouteDecision, latency_ms: float
    ) -> None:
        span.set_attribute("decision_sql.route.mode", decision.mode.value)
        span.set_attribute("decision_sql.route.path", decision.path.value)
        span.set_attribute("decision_sql.route.status", decision.status.value)
        if decision.applicable is not None:
            span.set_attribute("decision_sql.semantic.applicable", decision.applicable)
        if decision.metric_name and decision.metric_name in self._metric_names:
            span.set_attribute("decision_sql.semantic.metric_name", decision.metric_name)
        span.set_attribute(
            "decision_sql.semantic.dimension_count", min(len(decision.dimensions), 10)
        )
        if decision.fallback_reason:
            span.set_attribute(
                "decision_sql.semantic.fallback_reason", decision.fallback_reason.value
            )
        if decision.semantic_provenance is not None:
            provenance = decision.semantic_provenance
            span.set_attribute("decision_sql.semantic.catalog_id", provenance.catalog_id)
            span.set_attribute(
                "decision_sql.semantic.catalog_version", provenance.catalog_version
            )
            span.set_attribute(
                "decision_sql.semantic.contract_hash_prefix",
                provenance.semantic_contract_hash[:16],
            )
            span.set_attribute("decision_sql.semantic.metric_id", provenance.metric_stable_id)
            span.set_attribute(
                "decision_sql.semantic.metric_version", provenance.metric_semantic_version
            )
            span.set_attribute(
                "decision_sql.semantic.lifecycle_status",
                provenance.metric_lifecycle_status.value,
            )
        if decision.verified_memory_provenance is not None:
            memory = decision.verified_memory_provenance
            span.set_attribute(
                "decision_sql.verified_memory.enabled",
                memory.mode is not VerifiedMemoryMode.OFF,
            )
            span.set_attribute("decision_sql.verified_memory.mode", memory.mode.value)
            span.set_attribute(
                "decision_sql.verified_memory.corpus_version", memory.corpus_version
            )
            span.set_attribute(
                "decision_sql.verified_memory.corpus_hash_prefix", memory.corpus_hash[:16]
            )
            span.set_attribute(
                "decision_sql.verified_memory.retriever_version", memory.retriever_version
            )
            span.set_attribute("decision_sql.verified_memory.k", memory.k)
            span.set_attribute(
                "decision_sql.verified_memory.hit_count", len(memory.retrieved_example_ids)
            )
            span.set_attribute(
                "decision_sql.verified_memory.outcome", memory.outcome.value
            )
            span.set_attribute("decision_sql.verified_memory.sampled", memory.sampled)
            if memory.fallback_reason is not None:
                span.set_attribute(
                    "decision_sql.verified_memory.fallback_reason",
                    memory.fallback_reason.value,
                )
            if memory.shadow_result_comparison is not None:
                span.set_attribute(
                    "decision_sql.verified_memory.shadow_result_comparison",
                    memory.shadow_result_comparison.value,
                )
        span.set_attribute("decision_sql.route.total_latency_ms", latency_ms)


def _validation_reason(message: str) -> GovernedFallbackReason:
    lowered = message.lower()
    if "unknown metric" in lowered:
        return GovernedFallbackReason.UNKNOWN_METRIC
    if "unknown dimension" in lowered:
        return GovernedFallbackReason.INVALID_DIMENSION
    return GovernedFallbackReason.INVALID_GROUNDING


def _compilation_reason(code: str) -> GovernedFallbackReason:
    if code == "AMBIGUOUS_RELATIONSHIP_PATH":
        return GovernedFallbackReason.AMBIGUOUS_RELATIONSHIP_PATH
    if code == "FANOUT_UNSAFE_DIMENSION_PATH":
        return GovernedFallbackReason.FANOUT_UNSAFE_PATH
    return GovernedFallbackReason.COMPILER_FAILURE
