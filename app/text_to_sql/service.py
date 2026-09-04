from collections.abc import Callable
from time import perf_counter
from typing import Any

from opentelemetry import trace

from app.catalog.models import SchemaContext
from app.generation.hard_query_plans import OperationPlan
from app.generation.intent import IntentProposal, QueryIntent
from app.generation.provider import (
    LLMProvider,
    LLMProviderError,
    ProviderErrorDetail,
    SqlProposal,
)
from app.generation.result_shape import (
    ResultShapeProposal,
    ResultShapeValidation,
    validate_result_shape,
)
from app.models.domain import FailureStage, TextToSqlRequest
from app.observability.tracing import get_tracer
from app.provenance.canonical import semantic_hash, text_hash
from app.provenance.models import (
    ProvenanceEventType,
    ProvenanceSink,
    ProvenanceStage,
    recorder_for,
)
from app.provenance.sink import NoOpProvenanceSink
from app.retrieval.context import (
    SchemaContextMode,
    SchemaContextResolver,
    SchemaResolutionError,
    serialize_schema_context,
)
from app.sql.models import CandidateSource, SqlCandidate, SqlExecutionError, SqlPlanFailure
from app.sql.service import SqlSafetyService
from app.text_to_sql.grounding import (
    IntentVisibilityError,
    compare_intent_to_sql,
    diagnostic_flags,
    validate_intent_visibility,
)
from app.text_to_sql.models import (
    GenerationMode,
    GenerationStrategy,
    TextToSqlResult,
    TextToSqlStatus,
)


class TextToSqlService:
    """M2 coordinator; M1 remains the sole SQL authorization and execution authority."""

    def __init__(
        self,
        context_resolver: SchemaContextResolver,
        provider: LLMProvider,
        safety_service: SqlSafetyService,
        context_mode: SchemaContextMode = SchemaContextMode.RETRIEVED,
        strategy: GenerationStrategy | None = None,
        tracer: trace.Tracer | None = None,
        generation_mode: GenerationMode | None = None,
        schema_serializer: Callable[[SchemaContext], str] = serialize_schema_context,
        provenance_sink: ProvenanceSink | None = None,
    ) -> None:
        self.context_resolver = context_resolver
        self.provider = provider
        self.safety_service = safety_service
        self.context_mode = context_mode
        self.generation_mode = (
            generation_mode
            if generation_mode is not None
            else (strategy.mode if strategy is not None else GenerationMode.ONE_SHOT)
        )
        self.strategy = (
            GenerationStrategy.M25_GROUNDED
            if self.generation_mode is GenerationMode.GROUNDED
            else GenerationStrategy.M2_ONE_SHOT
        )
        self.tracer = tracer or get_tracer()
        self.schema_serializer = schema_serializer
        self.provenance_sink = provenance_sink or NoOpProvenanceSink()

    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        """Run the unchanged M2 question-to-SQL path."""
        return await self._run(request, result_shape_contract=False)

    async def run_with_context_addition(
        self, request: TextToSqlRequest, context_addition: str
    ) -> TextToSqlResult:
        """Run direct SQL generation with bounded server-owned prompt context."""
        return await self._run(
            request,
            result_shape_contract=False,
            context_addition=context_addition,
        )

    async def run_result_shape_contract(self, request: TextToSqlRequest) -> TextToSqlResult:
        """Run M2.8 with a narrow, untrusted result-shape proposal."""
        return await self._run(request, result_shape_contract=True)

    async def run_with_operation_plan(
        self, request: TextToSqlRequest, operation_plan: OperationPlan
    ) -> TextToSqlResult:
        """Run one SQL generation call with an untrusted narrow plan aid."""
        return await self._run(request, result_shape_contract=False, operation_plan=operation_plan)

    async def _run(
        self,
        request: TextToSqlRequest,
        *,
        result_shape_contract: bool,
        operation_plan: OperationPlan | None = None,
        context_addition: str | None = None,
    ) -> TextToSqlResult:
        started = perf_counter()
        provenance = recorder_for(self.provenance_sink, request)
        provider_calls_attempted = 0
        provider_calls_succeeded = 0
        provider_calls_failed = 0
        with self.tracer.start_as_current_span("decision_sql.text_to_sql") as root_span:
            root_span.set_attribute("decision_sql.context_mode", self.context_mode.name)
            root_span.set_attribute("decision_sql.generation_mode", self.generation_mode.value)
            root_span.set_attribute("decision_sql.result_shape_contract", result_shape_contract)
            try:
                context = self._resolve_context(request.question)
            except SchemaResolutionError as error:
                root_span.set_attribute(
                    "decision_sql.final_stage", FailureStage.SCHEMA_RETRIEVAL_ERROR
                )
                return TextToSqlResult(
                    status=TextToSqlStatus.CONTEXT_RESOLUTION_ERROR,
                    correlation_id=request.correlation_id,
                    failure_stage=FailureStage.SCHEMA_RETRIEVAL_ERROR,
                    error=str(error),
                    strategy=self.strategy,
                    generation_mode=self.generation_mode,
                    provider_calls_attempted=provider_calls_attempted,
                    provider_calls_succeeded=provider_calls_succeeded,
                    provider_calls_failed=provider_calls_failed,
                )
            root_span.set_attribute(
                "decision_sql.selected_table_count", context.context_metadata.selected_table_count
            )
            root_span.set_attribute(
                "decision_sql.selected_column_count", context.context_metadata.selected_column_count
            )
            root_span.set_attribute(
                "decision_sql.relationship_count", context.context_metadata.relationship_count
            )

            schema_text = self.schema_serializer(context)
            schema_context_hash = text_hash(schema_text)
            if context_addition:
                schema_text = f"{schema_text}\n\n{context_addition}"
            memory_context_hash = text_hash(context_addition) if context_addition else None
            provenance.emit(
                ProvenanceStage.GENERATION_CONTEXT,
                ProvenanceEventType.MEMORY_CONTEXT_RENDERED,
                {
                    "schema_context_hash": schema_context_hash,
                    "generation_context_manifest_hash": semantic_hash(
                        {
                            "schema_context_hash": schema_context_hash,
                            "memory_context_hash": memory_context_hash,
                            "final_context_hash": text_hash(schema_text),
                            "context_mode": self.context_mode.name,
                            "generation_mode": self.generation_mode.value,
                        }
                    ),
                    "rendered_memory_context_hash": memory_context_hash,
                },
            )
            result_shape_proposal: ResultShapeProposal | None = None
            if result_shape_contract:
                with self.tracer.start_as_current_span(
                    "decision_sql.result_shape.generate"
                ) as span:
                    try:
                        provider_calls_attempted += 1
                        result_shape_proposal = await self.provider.propose_result_shape(
                            request.question, schema_text
                        )
                        if not isinstance(result_shape_proposal, ResultShapeProposal):
                            raise LLMProviderError("Provider returned an invalid result shape")
                        provider_calls_succeeded += 1
                        _validate_result_shape_visibility(result_shape_proposal, context)
                    except Exception as error:
                        if provider_calls_attempted > provider_calls_succeeded:
                            provider_calls_failed += 1
                        span.set_attribute("decision_sql.result_shape_status", "error")
                        root_span.set_attribute(
                            "decision_sql.final_stage", FailureStage.RESULT_VALIDATION_ERROR
                        )
                        return TextToSqlResult(
                            status=TextToSqlStatus.RESULT_SHAPE_GENERATION_ERROR,
                            correlation_id=request.correlation_id,
                            failure_stage=FailureStage.RESULT_VALIDATION_ERROR,
                            error="Result-shape generation failed.",
                            context=context,
                            provider_error=_provider_error_detail_from_exception(error),
                            result_shape_proposal=result_shape_proposal,
                            strategy=self.strategy,
                            generation_mode=self.generation_mode,
                            provider_calls_attempted=provider_calls_attempted,
                            provider_calls_succeeded=provider_calls_succeeded,
                            provider_calls_failed=provider_calls_failed,
                        )
                    span.set_attribute("decision_sql.result_shape_status", "success")
                    span.set_attribute(
                        "decision_sql.result_shape_output_count",
                        len(result_shape_proposal.outputs),
                    )
            intent_proposal: IntentProposal | None = None
            intent: QueryIntent | None = None
            if self.generation_mode is GenerationMode.GROUNDED:
                with self.tracer.start_as_current_span("decision_sql.intent.generate") as span:
                    try:
                        provider_calls_attempted += 1
                        intent_proposal = await self.provider.propose_intent(
                            request.question, schema_text
                        )
                        if not isinstance(intent_proposal, IntentProposal):
                            raise LLMProviderError("Provider returned an invalid QueryIntent")
                        provider_calls_succeeded += 1
                        validate_intent_visibility(intent_proposal.intent, context)
                        intent = intent_proposal.intent
                    except IntentVisibilityError:
                        span.set_attribute("decision_sql.intent_status", "invalid_visibility")
                        root_span.set_attribute(
                            "decision_sql.final_stage", FailureStage.SCHEMA_LINKING_ERROR
                        )
                        return TextToSqlResult(
                            status=TextToSqlStatus.QUERY_INTENT_GENERATION_ERROR,
                            correlation_id=request.correlation_id,
                            failure_stage=FailureStage.SCHEMA_LINKING_ERROR,
                            error=(
                                "QueryIntent references schema objects outside the bounded context."
                            ),
                            context=context,
                            strategy=self.strategy,
                            generation_mode=self.generation_mode,
                            intent_proposal=intent_proposal,
                            intent_prompt_tokens=(
                                intent_proposal.prompt_tokens if intent_proposal else None
                            ),
                            intent_completion_tokens=(
                                intent_proposal.completion_tokens if intent_proposal else None
                            ),
                            intent_latency_ms=(
                                intent_proposal.latency_ms if intent_proposal else None
                            ),
                            provider_calls_attempted=provider_calls_attempted,
                            provider_calls_succeeded=provider_calls_succeeded,
                            provider_calls_failed=provider_calls_failed,
                        )
                    except Exception as error:
                        if provider_calls_attempted > provider_calls_succeeded:
                            provider_calls_failed += 1
                        span.set_attribute("decision_sql.intent_status", "error")
                        root_span.set_attribute(
                            "decision_sql.final_stage", FailureStage.QUERY_INTENT_GENERATION_ERROR
                        )
                        return TextToSqlResult(
                            status=TextToSqlStatus.QUERY_INTENT_GENERATION_ERROR,
                            correlation_id=request.correlation_id,
                            failure_stage=FailureStage.QUERY_INTENT_GENERATION_ERROR,
                            error="QueryIntent grounding failed.",
                            context=context,
                            strategy=self.strategy,
                            generation_mode=self.generation_mode,
                            provider_error=_provider_error_detail_from_exception(error),
                            provider_calls_attempted=provider_calls_attempted,
                            provider_calls_succeeded=provider_calls_succeeded,
                            provider_calls_failed=provider_calls_failed,
                        )
                    span.set_attribute("decision_sql.intent_status", "success")
                    span.set_attribute(
                        "decision_sql.intent_table_count", len(intent.selected_tables)
                    )
                    span.set_attribute(
                        "decision_sql.intent_column_count", len(intent.selected_columns)
                    )
            with self.tracer.start_as_current_span("decision_sql.sql.generate") as span:
                try:
                    provider_calls_attempted += 1
                    proposal_kwargs: dict[str, Any] = {}
                    if result_shape_contract:
                        proposal_kwargs["result_shape"] = result_shape_proposal
                    else:
                        proposal_kwargs["query_intent"] = intent
                    if operation_plan is not None:
                        proposal_kwargs["operation_plan"] = operation_plan
                    proposal = await self.provider.propose_sql(
                        request, None, schema_text, **proposal_kwargs
                    )
                    if not isinstance(proposal, SqlProposal):
                        raise LLMProviderError("Provider returned an invalid SQL proposal")
                    provider_calls_succeeded += 1
                except Exception as error:
                    if provider_calls_attempted > provider_calls_succeeded:
                        provider_calls_failed += 1
                    span.set_attribute("decision_sql.generation_status", "error")
                    root_span.set_attribute(
                        "decision_sql.final_stage", FailureStage.SQL_GENERATION_ERROR
                    )
                    return TextToSqlResult(
                        status=TextToSqlStatus.SQL_GENERATION_ERROR,
                        correlation_id=request.correlation_id,
                        failure_stage=FailureStage.SQL_GENERATION_ERROR,
                        error="SQL generation failed.",
                        context=context,
                        strategy=self.strategy,
                        generation_mode=self.generation_mode,
                        intent_proposal=intent_proposal,
                        intent=intent,
                        provider_error=_provider_error_detail_from_exception(error),
                        provider_calls_attempted=provider_calls_attempted,
                        provider_calls_succeeded=provider_calls_succeeded,
                        provider_calls_failed=provider_calls_failed,
                    )
                span.set_attribute("decision_sql.generation_status", "success")
                span.set_attribute("decision_sql.provider", proposal.provider)
                span.set_attribute("decision_sql.model", proposal.model)

            candidate = SqlCandidate(
                sql=proposal.sql,
                source=CandidateSource.LLM,
                correlation_id=request.correlation_id,
            )
            planned = self.safety_service.plan(candidate)
            base: dict[str, Any] = {
                "correlation_id": request.correlation_id,
                "context": context,
                "proposal": proposal,
                "candidate": candidate,
                "provider": proposal.provider,
                "model": proposal.model,
                "intent_prompt_tokens": (
                    intent_proposal.prompt_tokens if intent_proposal else None
                ),
                "intent_completion_tokens": (
                    intent_proposal.completion_tokens if intent_proposal else None
                ),
                "intent_latency_ms": intent_proposal.latency_ms if intent_proposal else None,
                "generation_latency_ms": proposal.latency_ms,
                "strategy": self.strategy,
                "generation_mode": self.generation_mode,
                "intent_proposal": intent_proposal,
                "intent": intent,
                "result_shape_proposal": result_shape_proposal,
                "result_shape_validation": None,
                "provider_calls_attempted": provider_calls_attempted,
                "provider_calls_succeeded": provider_calls_succeeded,
                "provider_calls_failed": provider_calls_failed,
                "diagnostics": {
                    "selected_table_count": context.context_metadata.selected_table_count,
                    "selected_column_count": context.context_metadata.selected_column_count,
                    "relationship_count": context.context_metadata.relationship_count,
                },
            }
            if intent is not None:
                with self.tracer.start_as_current_span("decision_sql.intent.compare") as span:
                    try:
                        intent_diagnostics = compare_intent_to_sql(intent, proposal.sql)
                        flags = diagnostic_flags(intent_diagnostics)
                        for key, value in flags.items():
                            span.set_attribute(f"decision_sql.{key}", value)
                    except Exception:
                        intent_diagnostics = None
                    base["grounding_diagnostics"] = (
                        intent_diagnostics.model_dump(mode="json") if intent_diagnostics else None
                    )
            if isinstance(planned, SqlPlanFailure):
                root_span.set_attribute(
                    "decision_sql.final_stage",
                    planned.failure_stage.value if planned.failure_stage else "unknown",
                )
                root_span.set_attribute("decision_sql.candidate_outcome", planned.status)
                return TextToSqlResult(
                    status=TextToSqlStatus.PLAN_REJECTED,
                    failure_stage=planned.failure_stage,
                    error=planned.error,
                    plan_failure=planned,
                    **base,
                )

            result_shape_validation: ResultShapeValidation | None = None
            if result_shape_proposal is not None:
                try:
                    result_shape_validation = validate_result_shape(
                        result_shape_proposal, planned.normalized_sql
                    )
                except Exception:
                    result_shape_validation = ResultShapeValidation(
                        accepted=False,
                        projection_status="PROJECTION_UNCERTAIN",
                        expected_arity=len(result_shape_proposal.outputs),
                        message="Generated SQL projection could not be inspected.",
                    )
                base["result_shape_validation"] = result_shape_validation
                if not result_shape_validation.accepted:
                    root_span.set_attribute(
                        "decision_sql.final_stage", FailureStage.RESULT_VALIDATION_ERROR
                    )
                    return TextToSqlResult(
                        status=TextToSqlStatus.RESULT_SHAPE_REJECTED,
                        failure_stage=FailureStage.RESULT_VALIDATION_ERROR,
                        error=result_shape_validation.message,
                        plan=planned,
                        **base,
                    )

            if not request.execute:
                root_span.set_attribute("decision_sql.final_stage", "planned")
                return TextToSqlResult(status=TextToSqlStatus.PLANNED, plan=planned, **base)

            execution = self.safety_service.execute(planned)
            if isinstance(execution, SqlExecutionError):
                root_span.set_attribute("decision_sql.final_stage", FailureStage.EXECUTION_ERROR)
                return TextToSqlResult(
                    status=TextToSqlStatus.EXECUTION_ERROR,
                    failure_stage=FailureStage.EXECUTION_ERROR,
                    error=execution.error,
                    plan=planned,
                    execution_error=execution,
                    **base,
                )
            root_span.set_attribute("decision_sql.final_stage", "executed")
            root_span.set_attribute(
                "decision_sql.total_latency_ms", (perf_counter() - started) * 1000
            )
            return TextToSqlResult(
                status=TextToSqlStatus.SUCCEEDED,
                plan=planned,
                execution=execution,
                **base,
            )

    def _resolve_context(self, question: str) -> SchemaContext:
        with self.tracer.start_as_current_span("decision_sql.context.resolve"):
            with self.tracer.start_as_current_span("decision_sql.schema.retrieve"):
                return self.context_resolver.resolve(question, mode=self.context_mode)


def _validate_result_shape_visibility(
    proposal: ResultShapeProposal, context: SchemaContext
) -> None:
    visible_columns = {
        f"{table.name.lower()}.{column.name.lower()}"
        for table in context.tables
        for column in table.columns
    }
    for output in proposal.outputs:
        if output.source_hint and output.kind.value == "PHYSICAL_COLUMN":
            if output.source_hint.lower() not in visible_columns:
                raise ValueError("ResultShape references a column outside the schema context")


def _provider_error_detail_from_exception(
    value: object,
) -> ProviderErrorDetail | None:
    if isinstance(value, LLMProviderError):
        return value.detail
    return None
