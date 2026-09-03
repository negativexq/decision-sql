"""Feature-gated runtime integration for the frozen verified-memory retriever."""

from hashlib import sha256
from time import perf_counter
from typing import Protocol

from opentelemetry import trace

from app.catalog.models import SchemaContext
from app.config import Settings, VerifiedMemoryMode, get_settings
from app.memory.models import (
    MemoryCorpusError,
    VerifiedQueryExample,
    validate_memory_corpus,
)
from app.memory.prompt import render_verified_examples
from app.memory.provenance import (
    ShadowResultComparison,
    VerifiedMemoryFallbackReason,
    VerifiedMemoryOutcome,
    VerifiedMemoryProvenance,
)
from app.memory.retrieval import (
    RetrievedExample,
    RetrieverConfig,
    RetrieverVariant,
    VerifiedQueryRetriever,
)
from app.models.domain import TextToSqlRequest
from app.observability.tracing import get_tracer
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.text_to_sql.models import GenerationPath, TextToSqlResult, TextToSqlStatus

FROZEN_MEMORY_CORPUS_ID = "decisionsql-demo-verified-query-memory"
FROZEN_MEMORY_CORPUS_VERSION = 1
FROZEN_MEMORY_CORPUS_HASH = (
    "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
)
FROZEN_RETRIEVER_CONFIG = RetrieverConfig(
    version="m4-retriever-v1",
    variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA,
    k=3,
    lexical_weight=0.75,
    schema_weight=0.25,
    structural_weight=0.0,
)


class MemoryDirectRunner(Protocol):
    context_resolver: SchemaContextResolver

    async def run(self, request: TextToSqlRequest) -> TextToSqlResult: ...

    async def run_with_context_addition(
        self, request: TextToSqlRequest, context_addition: str
    ) -> TextToSqlResult: ...


class VerifiedMemoryRuntime:
    """Optional residual-direct augmentation; governed routing owns ordering."""

    def __init__(
        self,
        direct_service: MemoryDirectRunner,
        corpus: tuple[VerifiedQueryExample, ...],
        *,
        settings: Settings | None = None,
        tracer: trace.Tracer | None = None,
        expected_corpus_hash: str = FROZEN_MEMORY_CORPUS_HASH,
    ) -> None:
        self.direct_service = direct_service
        config = settings or get_settings()
        self.mode = config.verified_query_memory_mode
        self.shadow_sample_rate = config.verified_query_memory_shadow_sample_rate
        self.shadow_execute = config.verified_query_memory_shadow_execute
        self.tracer = tracer or get_tracer()
        self.retriever_config = FROZEN_RETRIEVER_CONFIG
        self.corpus_hash = expected_corpus_hash
        self.retriever: VerifiedQueryRetriever | None = None
        if self.mode is not VerifiedMemoryMode.OFF:
            actual_hash = validate_memory_corpus(corpus, expected_corpus_hash)
            self.corpus_hash = actual_hash
            if not corpus:
                raise MemoryCorpusError("verified memory corpus is empty")
            self.retriever = VerifiedQueryRetriever(corpus)

    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        """Run the authoritative ON-mode residual direct path."""
        if self.mode is not VerifiedMemoryMode.ON:
            return await self.direct_service.run(request)
        return await self._run_memory(request, execute=request.execute)

    async def run_shadow(
        self, request: TextToSqlRequest, baseline: TextToSqlResult
    ) -> tuple[TextToSqlResult, VerifiedMemoryProvenance]:
        """Run sampled memory generation diagnostically while preserving baseline."""
        if self.mode is not VerifiedMemoryMode.SHADOW:
            return baseline, self._provenance(
                outcome=VerifiedMemoryOutcome.DISABLED,
                reason=VerifiedMemoryFallbackReason.FEATURE_OFF,
                sampled=False,
            )
        if not self._is_sampled(request):
            provenance = self._provenance(
                outcome=VerifiedMemoryOutcome.NOT_SAMPLED,
                reason=VerifiedMemoryFallbackReason.NOT_SAMPLED,
                sampled=False,
            )
            return self._annotate(baseline, provenance, used=False), provenance
        memory_result = await self._run_memory(
            request.model_copy(update={"execute": self.shadow_execute}),
            execute=self.shadow_execute,
            fallback_result=baseline,
        )
        memory_provenance = memory_result.verified_memory_provenance
        if memory_provenance is None:
            raise RuntimeError("memory result omitted required provenance")
        comparison = None
        if baseline.execution is not None and memory_result.execution is not None:
            comparison = (
                ShadowResultComparison.RESULTS_EQUAL
                if baseline.execution.rows == memory_result.execution.rows
                else ShadowResultComparison.RESULTS_DIFFER
            )
        if comparison is not None:
            memory_provenance = memory_provenance.model_copy(
                update={"fallback_reason": None, "shadow_result_comparison": comparison}
            )
        return self._annotate(baseline, memory_provenance, used=False), memory_provenance

    async def _run_memory(
        self,
        request: TextToSqlRequest,
        *,
        execute: bool,
        fallback_result: TextToSqlResult | None = None,
    ) -> TextToSqlResult:
        if self.retriever is None:
            provenance = self._provenance(
                outcome=VerifiedMemoryOutcome.DISABLED,
                reason=VerifiedMemoryFallbackReason.FEATURE_OFF,
                sampled=False,
            )
            return self._annotate(
                await self._fallback_direct(request, fallback_result), provenance, used=False
            )
        started = perf_counter()
        try:
            schema_objects = _schema_objects(
                self.direct_service.context_resolver, request.question
            )
            with self.tracer.start_as_current_span("decision_sql.memory.retrieve") as span:
                retrieved = self.retriever.retrieve(
                    request.question, schema_objects, self.retriever_config
                )
                latency_ms = (perf_counter() - started) * 1000
                span.set_attribute("decision_sql.verified_memory.hit_count", len(retrieved))
                span.set_attribute("decision_sql.verified_memory.retrieval_latency_ms", latency_ms)
                if retrieved:
                    span.set_attribute(
                        "decision_sql.verified_memory.top1_score", retrieved[0].final_score
                    )
        except Exception:
            provenance = self._provenance(
                outcome=VerifiedMemoryOutcome.RETRIEVAL_FAILURE,
                reason=VerifiedMemoryFallbackReason.RETRIEVAL_ERROR,
                sampled=True,
                latency_ms=(perf_counter() - started) * 1000,
            )
            return self._annotate(
                await self._fallback_direct(request, fallback_result), provenance, used=False
            )

        if not retrieved:
            provenance = self._provenance(
                outcome=VerifiedMemoryOutcome.NO_HIT,
                reason=VerifiedMemoryFallbackReason.NO_RETRIEVAL_HIT,
                sampled=True,
                latency_ms=(perf_counter() - started) * 1000,
            )
            return self._annotate(
                await self._fallback_direct(request, fallback_result), provenance, used=False
            )

        try:
            with self.tracer.start_as_current_span("decision_sql.memory.prompt") as span:
                span.set_attribute("decision_sql.verified_memory.example_count", len(retrieved))
                addition = render_verified_examples(tuple(item.example for item in retrieved))
        except Exception:
            provenance = self._provenance(
                retrieved=retrieved,
                outcome=VerifiedMemoryOutcome.MEMORY_GENERATION_FAILURE,
                reason=VerifiedMemoryFallbackReason.PROVIDER_ERROR,
                sampled=True,
                latency_ms=(perf_counter() - started) * 1000,
            )
            return self._annotate(
                await self._fallback_direct(request, fallback_result), provenance, used=False
            )
        memory_request = request.model_copy(update={"execute": execute})
        try:
            result = await self.direct_service.run_with_context_addition(memory_request, addition)
        except Exception:
            provenance = self._provenance(
                retrieved=retrieved,
                outcome=VerifiedMemoryOutcome.MEMORY_GENERATION_FAILURE,
                reason=VerifiedMemoryFallbackReason.PROVIDER_ERROR,
                sampled=True,
                latency_ms=(perf_counter() - started) * 1000,
            )
            return self._failed_result(memory_request, provenance)
        outcome, reason = _result_outcome(result)
        provenance = self._provenance(
            retrieved=retrieved,
            outcome=outcome,
            reason=reason,
            sampled=True,
            latency_ms=(perf_counter() - started) * 1000,
        )
        return self._annotate(result, provenance, used=outcome is VerifiedMemoryOutcome.SUCCESS)

    async def _fallback_direct(
        self, request: TextToSqlRequest, fallback_result: TextToSqlResult | None
    ) -> TextToSqlResult:
        if fallback_result is not None:
            return fallback_result
        return await self.direct_service.run(request)

    def _is_sampled(self, request: TextToSqlRequest) -> bool:
        key = request.correlation_id or request.question
        bucket = int(sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return bucket < self.shadow_sample_rate

    def _provenance(
        self,
        *,
        outcome: VerifiedMemoryOutcome,
        reason: VerifiedMemoryFallbackReason | None,
        sampled: bool,
        retrieved: tuple[RetrievedExample, ...] = (),
        latency_ms: float = 0.0,
    ) -> VerifiedMemoryProvenance:
        return VerifiedMemoryProvenance(
            mode=self.mode,
            corpus_id=FROZEN_MEMORY_CORPUS_ID,
            corpus_version=FROZEN_MEMORY_CORPUS_VERSION,
            corpus_hash=self.corpus_hash,
            retriever_version=self.retriever_config.version,
            k=self.retriever_config.k,
            retrieved_example_ids=tuple(item.example.example_id for item in retrieved),
            retrieval_scores=tuple(item.final_score for item in retrieved),
            retrieval_latency_ms=max(0.0, latency_ms),
            sampled=sampled,
            outcome=outcome,
            fallback_reason=reason,
        )

    @staticmethod
    def _annotate(
        result: TextToSqlResult, provenance: VerifiedMemoryProvenance, *, used: bool
    ) -> TextToSqlResult:
        path = (
            GenerationPath.DIRECT_SQL_WITH_VERIFIED_MEMORY
            if used
            else GenerationPath.DIRECT_SQL
        )
        return result.model_copy(
            update={
                "generation_path": path,
                "verified_memory_used": used,
                "verified_memory_provenance": provenance,
            }
        )

    def _failed_result(
        self, request: TextToSqlRequest, provenance: VerifiedMemoryProvenance
    ) -> TextToSqlResult:
        return TextToSqlResult(
            status=TextToSqlStatus.SQL_GENERATION_ERROR,
            correlation_id=request.correlation_id,
            error="Verified-memory SQL generation failed.",
            generation_path=GenerationPath.DIRECT_SQL_WITH_VERIFIED_MEMORY,
            verified_memory_used=False,
            verified_memory_provenance=provenance,
            provider_calls_attempted=1,
            provider_calls_failed=1,
        )


def _result_outcome(
    result: TextToSqlResult,
) -> tuple[VerifiedMemoryOutcome, VerifiedMemoryFallbackReason | None]:
    if result.status in {TextToSqlStatus.SUCCEEDED, TextToSqlStatus.PLANNED}:
        return VerifiedMemoryOutcome.SUCCESS, None
    if result.status is TextToSqlStatus.PLAN_REJECTED:
        return VerifiedMemoryOutcome.M1_REJECTION, VerifiedMemoryFallbackReason.M1_REJECTION
    if result.status is TextToSqlStatus.EXECUTION_ERROR:
        return VerifiedMemoryOutcome.EXECUTION_ERROR, VerifiedMemoryFallbackReason.EXECUTION_ERROR
    return (
        VerifiedMemoryOutcome.MEMORY_GENERATION_FAILURE,
        VerifiedMemoryFallbackReason.PROVIDER_ERROR,
    )


def _schema_objects(resolver: SchemaContextResolver, question: str) -> tuple[str, ...]:
    context: SchemaContext = resolver.resolve(question, mode=SchemaContextMode.FULL_COMPACT)
    values = {table.name for table in context.tables}
    values.update(f"{column.table_name}.{column.name}" for column in context.selected_columns)
    return tuple(sorted(values))
