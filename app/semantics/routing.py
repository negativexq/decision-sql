"""Feature-gated orchestration for the accepted governed metric path."""

from collections.abc import Callable
from enum import StrEnum
from time import perf_counter
from typing import Protocol

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field

from app.config import GovernedMetricsMode, Settings
from app.generation.governed_metric_grounding import (
    GovernedMetricGroundingDTO,
    grounding_to_request,
)
from app.generation.provider import (
    GovernedMetricGroundingProposal,
    LLMProviderError,
)
from app.models.domain import TextToSqlRequest
from app.observability.tracing import get_tracer
from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.models import MetricCatalog
from app.sql.models import (
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus


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
    diagnostics: dict[str, int | float | str | bool] = Field(default_factory=dict)


class _SafetyService(Protocol):
    def plan(self, candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure: ...

    def execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError: ...


class GovernedMetricRouteService:
    """Select direct or governed execution without duplicating semantic rules."""

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
    ) -> None:
        self.direct_service = direct_service
        self.provider = provider
        self.safety_service = safety_service
        self.catalog = catalog or build_m3_catalog()
        self.compiler = MetricCompiler(self.catalog)
        self.glossary = public_metric_glossary(self.catalog)
        self.mode = mode
        self.shadow_execute = shadow_execute
        self.tracer = tracer or get_tracer()
        self.result_comparator = result_comparator
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
        )

    async def run(self, request: TextToSqlRequest) -> GovernedRouteDecision:
        started = perf_counter()
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
            self._record_route(span, decision, (perf_counter() - started) * 1000)
            return decision

    async def _governed_attempt(
        self, request: TextToSqlRequest, direct: TextToSqlResult | None
    ) -> GovernedRouteDecision:
        proposal: GovernedMetricGroundingProposal | None = None
        grounding: GovernedMetricGroundingDTO | None = None
        started = perf_counter()
        with self.tracer.start_as_current_span("decision_sql.semantic.grounding") as span:
            try:
                proposal = await self.provider.propose_metric_grounding(
                    request.question, self.glossary
                )
                if not isinstance(proposal, GovernedMetricGroundingProposal):
                    raise TypeError("provider returned invalid governed grounding")
                grounding = proposal.grounding
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
            except Exception as error:
                span.set_attribute("decision_sql.semantic.grounding_status", "failure")
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

        compiled = compiled.model_copy(update={"correlation_id": request.correlation_id})

        compile_latency = (perf_counter() - compile_started) * 1000
        plan_started = perf_counter()
        planned = self.safety_service.plan(compiled)
        m1_latency = (perf_counter() - plan_started) * 1000
        if isinstance(planned, SqlPlanFailure):
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
                diagnostics={"compile_latency_ms": compile_latency, "m1_latency_ms": m1_latency},
            )

        execution = self.safety_service.execute(planned)
        if isinstance(execution, SqlExecutionError):
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
                diagnostics={"m1_latency_ms": m1_latency},
            )
        return self._governed_decision(
            request,
            grounding,
            compiled,
            planned,
            execution,
            status=GovernedRouteStatus.SUCCESS,
            diagnostics={"compile_latency_ms": compile_latency, "m1_latency_ms": m1_latency},
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
        diagnostics: dict[str, int | float | str | bool] | None = None,
        governed_execution: QueryExecution | SqlExecutionError | None = None,
    ) -> GovernedRouteDecision:
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
        diagnostics: dict[str, int | float | str | bool],
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
            provider="openai-compatible",
            model=None,
            provider_calls_attempted=1,
            provider_calls_succeeded=1,
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
