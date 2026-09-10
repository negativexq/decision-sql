import json
import re
from enum import StrEnum
from time import perf_counter
from typing import Any, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings, get_settings
from app.generation.blueprint import (
    BlueprintSqlProposal,
    blueprint_messages,
    parse_blueprint_payload,
)
from app.generation.decision_contract import (
    ProductionDecision,
    production_decision_prompt,
    production_decision_schema,
)
from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.generation.hard_query_plans import (
    OperationPlan,
    RatioPlan,
    RatioPlanProposal,
    TopKPlan,
    TopKPlanProposal,
    WindowPlan,
    WindowPlanProposal,
)
from app.generation.intent import IntentProposal, QueryIntent
from app.generation.quality_pack import render_query_quality_pack
from app.generation.result_shape import ResultShapeProposal
from app.generation.semantic_plan_protocol import (
    logical_query_plan_response_format,
    logical_synthesis_response_format,
    query_alignment_response_format,
    query_sql_response_format,
    schema_alignment_response_format,
    semantic_query_plan_response_format,
)
from app.generation.window_ir import WindowQueryIR, WindowQueryIRProposal
from app.models.domain import QueryRequest, UserContext
from app.provenance.canonical import bounded_text, semantic_hash, text_hash
from app.provenance.models import (
    ProvenanceEventType,
    ProvenanceSink,
    ProvenanceStage,
    recorder_for_identity,
)
from app.provenance.sink import NoOpProvenanceSink
from app.semantics.logical_plan import LogicalQueryPlanV1
from app.semantics.m32_alignment import LogicalSynthesisV1, QueryAlignmentV1, SchemaAlignmentV1
from app.semantics.query_plan_v1 import QueryPlanV1
from app.semantics.query_plan_wire_v2 import (
    QueryPlanWireV2,
    query_plan_wire_v2_prompt,
    wire_to_query_plan_v1,
)
from app.semantics.semantic_query import SemanticQueryPlan


class SqlProposal(BaseModel):
    """Structured, untrusted provider output; it has no execution authority."""

    model_config = ConfigDict(frozen=True)

    sql: str = Field(min_length=1)
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None
    confidence: float | None = None


SQLProposal = SqlProposal


class ProductionDecisionProposal(BaseModel):
    """One typed decision and optional SQL from one provider response."""

    model_config = ConfigDict(frozen=True)

    decision: ProductionDecision
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class GovernedMetricGroundingProposal(BaseModel):
    """One untrusted semantic-name selection for the M3 experiment."""

    model_config = ConfigDict(frozen=True)

    grounding: GovernedMetricGroundingDTO
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class QueryPlanV1Proposal(BaseModel):
    """One untrusted, strictly bounded relational plan proposal."""

    model_config = ConfigDict(frozen=True)

    plan: QueryPlanV1
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class SemanticQueryPlanProposal(BaseModel):
    """One untrusted canonical semantic plan; it contains no SQL field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: SemanticQueryPlan
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class LogicalQueryPlanProposal(BaseModel):
    """One untrusted compositional logical plan proposal for M31."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: LogicalQueryPlanV1
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class QueryAlignmentProposal(BaseModel):
    """One untrusted M32 semantic alignment proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alignment: QueryAlignmentV1
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class SchemaAlignmentProposal(BaseModel):
    """One untrusted M32 v2A schema-selection proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alignment: SchemaAlignmentV1
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class LogicalSynthesisProposal(BaseModel):
    """One untrusted M32 v2A logical-synthesis proposal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    synthesis: LogicalSynthesisV1
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class QueryPlanWireV2Proposal(BaseModel):
    """Untrusted versioned wire proposal before canonical QueryPlan V1 conversion."""

    model_config = ConfigDict(frozen=True)

    wire: QueryPlanWireV2
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None

    @property
    def plan(self) -> QueryPlanV1:
        return wire_to_query_plan_v1(self.wire)


class ProviderTransportProposal(BaseModel):
    """Raw, bounded assistant content for transport-format diagnostics."""

    model_config = ConfigDict(frozen=True)

    content: str | None = None
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class ProviderErrorDetail(BaseModel):
    """Bounded, sanitized provider failure metadata."""

    model_config = ConfigDict(frozen=True)

    status_code: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    message: str
    request_id: str | None = None
    model: str
    endpoint_family: str = "chat_completions"
    retryable: bool = False


class ModelIOCapture(BaseModel):
    """Evaluation-only model-boundary capture; never emitted as runtime telemetry."""

    model_config = ConfigDict(frozen=True)

    operation: str
    question: str
    serialized_schema_context: str
    request_config: dict[str, Any]
    messages: list[dict[str, str]]
    response_model: str | None = None
    raw_assistant_content: str | None = None
    raw_assistant_content_full: str | None = None
    raw_assistant_content_sha256: str | None = None
    raw_assistant_content_truncated: bool = False
    parsed_sql: str | None = None
    parsed_result_shape: dict[str, Any] | None = None
    parsed_operation_plan: dict[str, Any] | None = None
    parsed_window_ir: dict[str, Any] | None = None
    parsed_metric_grounding: dict[str, Any] | None = None
    parsed_blueprint: dict[str, Any] | None = None
    parsed_blueprint_warnings: tuple[str, ...] = ()
    usage: dict[str, int | None] = Field(default_factory=dict)
    latency_ms: float | None = None
    finish_reason: str | None = None
    system_fingerprint: str | None = None
    request_id: str | None = None
    provider_response_id: str | None = None
    failure_stage: str | None = None
    failure_metadata: dict[str, Any] = Field(default_factory=dict)
    attempt_count: int = 1
    technical_retry_count: int = 0


class LLMProviderError(RuntimeError):
    def __init__(self, message: str, detail: ProviderErrorDetail | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ProviderConfigurationError(LLMProviderError):
    pass


class MalformedProviderResponse(LLMProviderError):
    pass


class QueryPlanProviderFailureStage(StrEnum):
    TRANSPORT_FAILURE = "TRANSPORT_FAILURE"
    HTTP_PROVIDER_FAILURE = "HTTP_PROVIDER_FAILURE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    RESPONSE_EXTRACTION_FAILURE = "RESPONSE_EXTRACTION_FAILURE"
    JSON_PARSE_FAILURE = "JSON_PARSE_FAILURE"
    WIRE_SCHEMA_VALIDATION_FAILURE = "WIRE_SCHEMA_VALIDATION_FAILURE"
    CANONICALIZATION_FAILURE = "CANONICALIZATION_FAILURE"


class QueryPlanProviderDiagnostic(BaseModel):
    """Bounded failure metadata safe for experiment evidence."""

    model_config = ConfigDict(frozen=True)

    stage: QueryPlanProviderFailureStage
    response_preview: str | None = None
    response_sha256: str | None = None
    response_truncated: bool = False
    response_id: str | None = None
    http_status: int | None = None
    finish_reason: str | None = None
    resolved_model: str | None = None
    attempt_count: int = 1
    technical_retry_count: int = 0
    validation_errors: tuple[dict[str, str], ...] = ()


class QueryPlanProviderBoundaryError(LLMProviderError):
    def __init__(
        self,
        message: str,
        diagnostic: QueryPlanProviderDiagnostic,
        detail: ProviderErrorDetail | None = None,
    ) -> None:
        super().__init__(message, detail)
        self.diagnostic = diagnostic


class LLMProvider(Protocol):
    async def propose_decision(
        self, question: str, schema_context: str
    ) -> ProductionDecisionProposal:
        """Return the canonical typed production decision in one call."""

    async def propose_schema_alignment(
        self, question: str, schema_context: str
    ) -> SchemaAlignmentProposal:
        """Return one untrusted M32 v2A schema selection."""

    async def propose_logical_synthesis(
        self, question: str, grounded_schema_context: str
    ) -> LogicalSynthesisProposal:
        """Return one untrusted M32 v2A semantic computation."""

    async def propose_query_alignment(
        self, question: str, schema_context: str
    ) -> QueryAlignmentProposal:
        """Return one untrusted M32 semantic alignment."""

    async def propose_aligned_sql(self, question: str, grounded_context: str) -> SqlProposal:
        """Return one untrusted SQL proposal from grounded M32 context."""

    async def review_aligned_sql(
        self,
        question: str,
        grounded_context: str,
        initial_sql: str,
        m1_status: str,
        explain_diagnostic: str,
    ) -> SqlProposal:
        """Return one bounded SQL revision using only server diagnostics."""

    async def propose_logical_query_plan(
        self, question: str, schema_context: str
    ) -> LogicalQueryPlanProposal:
        """Return one untrusted compositional logical plan."""

    async def propose_semantic_query_plan(
        self, question: str, schema_context: str
    ) -> SemanticQueryPlanProposal:
        """Return one untrusted canonical semantic plan, never SQL."""

    async def propose_query_plan_v1(
        self, question: str, schema_context: str
    ) -> QueryPlanV1Proposal:
        """Return one untrusted bounded QueryPlan V1 proposal."""

    async def propose_query_plan_wire_v2(
        self, question: str, schema_context: str
    ) -> QueryPlanWireV2Proposal:
        """Return one untrusted versioned wire proposal for canonical QueryPlan V1."""

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        """Return one untrusted governed metric/dimension selection."""

    async def propose_window_ir(self, question: str, schema_context: str) -> WindowQueryIRProposal:
        """Return one untrusted semantic WindowQueryIR object."""

    async def propose_top_k_plan(self, question: str, schema_context: str) -> TopKPlanProposal:
        """Return an untrusted narrow top-k decomposition."""

    async def propose_ratio_plan(self, question: str, schema_context: str) -> RatioPlanProposal:
        """Return an untrusted narrow ratio decomposition."""

    async def propose_window_plan(self, question: str, schema_context: str) -> WindowPlanProposal:
        """Return an untrusted narrow window decomposition."""

    async def propose_result_shape(self, question: str, schema_context: str) -> ResultShapeProposal:
        """Return a narrow, untrusted output contract."""

    async def propose_intent(self, question: str, schema_context: str) -> IntentProposal:
        """Return a structural intent with no execution authority."""

    async def propose_sql(
        self,
        request: QueryRequest,
        user_context: UserContext | None,
        schema_context: str,
        query_intent: QueryIntent | None = None,
        result_shape: ResultShapeProposal | None = None,
        operation_plan: OperationPlan | None = None,
    ) -> SqlProposal:
        """Return a SQL proposal. This interface has no execution authority."""


class OpenAICompatibleProvider:
    """Small adapter for providers implementing the OpenAI chat-completions contract."""

    provider_name = "openai-compatible"

    def __init__(
        self, settings: Settings | None = None, provenance_sink: ProvenanceSink | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.provenance_sink = provenance_sink or NoOpProvenanceSink()
        self._last_model_io: ModelIOCapture | None = None
        self._model_io_history: list[ModelIOCapture] = []
        self._response_metadata: dict[str, str | None] = {}
        self._last_response_wire: bytes | None = None

    async def complete_json_schema(
        self,
        *,
        operation: str,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any:
        """Make one provider-native strict JSON Schema request.

        This is intentionally a transport-only primitive for benchmark adapters.
        It does not parse, repair, retry, or otherwise interpret the response.
        """
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        }
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(operation, user_prompt, system_prompt, messages, response_format)
        started = perf_counter()
        try:
            payload = await self._post(body)
        except Exception:
            # The caller consumes the capture and wire bytes for deterministic
            # failure accounting; no retry or repair is performed here.
            raise
        self._complete_model_io(
            payload,
            parsed_sql=None,
            raw_content=_assistant_content(payload),
            latency_ms=(perf_counter() - started) * 1000,
        )
        return payload

    async def propose_decision(
        self, question: str, schema_context: str
    ) -> ProductionDecisionProposal:
        """Request one typed decision; SQL is never generated in a second call."""
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _production_decision_messages(question, schema_context)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "decision_sql_production_decision",
                "strict": True,
                "schema": production_decision_schema(),
            },
        }
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(
            "production_decision", question, schema_context, messages, response_format
        )
        started = perf_counter()
        try:
            payload = await self._post(body)
        except Exception:
            self._complete_model_io(
                None,
                parsed_sql=None,
                raw_content=None,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="PRODUCTION_DECISION_PROVIDER",
                failure_metadata={"exception_type": "provider_error"},
            )
            raise
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            decision = ProductionDecision.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = ProductionDecisionProposal(
                decision=decision,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="PRODUCTION_DECISION_ADMISSION",
                failure_metadata={"exception_type": type(error).__name__},
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid production decision"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=decision.sql,
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_intent(self, question: str, schema_context: str) -> IntentProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _intent_messages(question, schema_context)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("intent", question, schema_context, messages)
        started = perf_counter()
        payload = await self._post(body)
        proposal = _intent_from_response(payload, self.settings.llm_model).model_copy(
            update={"latency_ms": (perf_counter() - started) * 1000}
        )
        self._complete_model_io(
            payload,
            parsed_sql=None,
            raw_content=_assistant_content(payload),
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_schema_alignment(
        self, question: str, schema_context: str
    ) -> SchemaAlignmentProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _schema_alignment_messages(question, schema_context)
        response_format = schema_alignment_response_format()
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(
            "m32_schema_alignment", question, schema_context, messages, response_format
        )
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            alignment = SchemaAlignmentV1.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = SchemaAlignmentProposal(
                alignment=alignment,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                parsed_operation_plan=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="M32_SCHEMA_ALIGNMENT_PROTOCOL",
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid M32 schema alignment"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=alignment.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_logical_synthesis(
        self, question: str, grounded_schema_context: str
    ) -> LogicalSynthesisProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _logical_synthesis_messages(question, grounded_schema_context)
        response_format = logical_synthesis_response_format()
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(
            "m32_logical_synthesis",
            question,
            grounded_schema_context,
            messages,
            response_format,
        )
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            synthesis = LogicalSynthesisV1.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = LogicalSynthesisProposal(
                synthesis=synthesis,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                parsed_operation_plan=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="M32_LOGICAL_SYNTHESIS_PROTOCOL",
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid M32 logical synthesis"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=synthesis.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_query_alignment(
        self, question: str, schema_context: str
    ) -> QueryAlignmentProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _query_alignment_messages(question, schema_context)
        response_format = query_alignment_response_format()
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("m32_alignment", question, schema_context, messages, response_format)
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            alignment = QueryAlignmentV1.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = QueryAlignmentProposal(
                alignment=alignment,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                parsed_operation_plan=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="M32_ALIGNMENT_PROTOCOL",
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid M32 query alignment"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=alignment.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_aligned_sql(self, question: str, grounded_context: str) -> SqlProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _aligned_sql_messages(question, grounded_context)
        response_format = query_sql_response_format()
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("m32_sql", question, grounded_context, messages, response_format)
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            if not isinstance(data, dict):
                raise TypeError("structured output is not an object")
            sql = data.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                raise TypeError("sql is missing")
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = SqlProposal(
                sql=sql,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="M32_SQL_PROTOCOL",
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid M32 SQL object"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=proposal.sql,
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def review_aligned_sql(
        self,
        question: str,
        grounded_context: str,
        initial_sql: str,
        m1_status: str,
        explain_diagnostic: str,
    ) -> SqlProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _review_aligned_sql_messages(
            question, grounded_context, initial_sql, m1_status, explain_diagnostic
        )
        response_format = query_sql_response_format()
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(
            "m32_sql_review", question, grounded_context, messages, response_format
        )
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            if not isinstance(data, dict):
                raise TypeError("structured output is not an object")
            sql = data.get("sql")
            if not isinstance(sql, str) or not sql.strip():
                raise TypeError("sql is missing")
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = SqlProposal(
                sql=sql,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="M32_SQL_REVIEW_PROTOCOL",
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid M32 SQL review object"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=proposal.sql,
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_logical_query_plan(
        self, question: str, schema_context: str
    ) -> LogicalQueryPlanProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _logical_query_plan_messages(question, schema_context)
        response_format = logical_query_plan_response_format()
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(
            "logical_query_plan_v1", question, schema_context, messages, response_format
        )
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            plan = LogicalQueryPlanV1.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = LogicalQueryPlanProposal(
                plan=plan,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="LOGICAL_PLAN_PROTOCOL",
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid logical query plan"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=plan.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_semantic_query_plan(
        self, question: str, schema_context: str
    ) -> SemanticQueryPlanProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _semantic_query_plan_messages(question, schema_context)
        response_format = semantic_query_plan_response_format()
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(
            "semantic_query_plan", question, schema_context, messages, response_format
        )
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            plan = SemanticQueryPlan.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = SemanticQueryPlanProposal(
                plan=plan,
                provider="openai-compatible",
                model=payload.get("model") or self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="SEMANTIC_PLAN_PROTOCOL",
            )
            raise MalformedProviderResponse(
                "Provider response did not contain a valid semantic query plan"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=plan.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_query_plan_v1(
        self, question: str, schema_context: str
    ) -> QueryPlanV1Proposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _query_plan_v1_messages(question, schema_context)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("query_plan_v1", question, schema_context, messages)
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            if not isinstance(data, dict):
                raise TypeError("structured output is not an object")
            plan = QueryPlanV1.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = QueryPlanV1Proposal(
                plan=plan,
                provider="openai-compatible",
                model=self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError) as error:
            raise MalformedProviderResponse(
                "Provider response did not contain a valid QueryPlan V1"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=plan.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_query_plan_wire_v2(
        self, question: str, schema_context: str
    ) -> QueryPlanWireV2Proposal:
        """Request the explicit V2 wire contract and canonically parse it."""
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _query_plan_wire_v2_messages(question, schema_context)
        response_format = {"type": "json_object"}
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            # The configured OpenAI-compatible endpoint is only verified for JSON mode.
            "response_format": response_format,
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(
            "query_plan_wire_v2",
            question,
            schema_context,
            messages,
            response_format,
        )
        started = perf_counter()
        try:
            payload = await self._post(body)
        except LLMProviderError as error:
            if isinstance(error, MalformedProviderResponse):
                stage = QueryPlanProviderFailureStage.RESPONSE_EXTRACTION_FAILURE
            else:
                stage = (
                    QueryPlanProviderFailureStage.HTTP_PROVIDER_FAILURE
                    if error.detail is not None and error.detail.status_code is not None
                    else QueryPlanProviderFailureStage.TRANSPORT_FAILURE
                )
            diagnostic = QueryPlanProviderDiagnostic(
                stage=stage,
                response_id=error.detail.request_id if error.detail else None,
                http_status=error.detail.status_code if error.detail else None,
                resolved_model=self.settings.llm_model,
                attempt_count=1,
            )
            self._complete_model_io(
                None,
                parsed_sql=None,
                raw_content=None,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage=stage.value,
                failure_metadata=diagnostic.model_dump(mode="json"),
            )
            raise QueryPlanProviderBoundaryError(str(error), diagnostic, error.detail) from error
        content = _assistant_content(payload)
        base_metadata = _query_plan_response_metadata(
            payload, self._response_metadata, self.settings.llm_model
        )
        if content is None:
            diagnostic = QueryPlanProviderDiagnostic(
                stage=QueryPlanProviderFailureStage.EMPTY_RESPONSE, **base_metadata
            )
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=None,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage=diagnostic.stage.value,
                failure_metadata=diagnostic.model_dump(mode="json"),
            )
            raise QueryPlanProviderBoundaryError(
                "Provider returned an empty plan response", diagnostic
            )
        try:
            data = json.loads(content)
        except json.JSONDecodeError as error:
            diagnostic = QueryPlanProviderDiagnostic(
                stage=QueryPlanProviderFailureStage.JSON_PARSE_FAILURE,
                **base_metadata,
                **_response_diagnostic_fields(content),
            )
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage=diagnostic.stage.value,
                failure_metadata=diagnostic.model_dump(mode="json"),
            )
            raise QueryPlanProviderBoundaryError(
                "Provider plan response was not valid JSON", diagnostic
            ) from error
        if not isinstance(data, dict):
            errors = (
                {
                    "loc": "__root__",
                    "type": "object_type",
                    "msg": "JSON value must be an object",
                },
            )
            diagnostic = QueryPlanProviderDiagnostic(
                stage=QueryPlanProviderFailureStage.WIRE_SCHEMA_VALIDATION_FAILURE,
                **base_metadata,
                **_response_diagnostic_fields(content),
                validation_errors=errors,
            )
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage=diagnostic.stage.value,
                failure_metadata=diagnostic.model_dump(mode="json"),
            )
            raise QueryPlanProviderBoundaryError(
                "Provider plan response was not an object", diagnostic
            )
        try:
            wire = QueryPlanWireV2.model_validate(data)
        except ValidationError as error:
            diagnostic = QueryPlanProviderDiagnostic(
                stage=QueryPlanProviderFailureStage.WIRE_SCHEMA_VALIDATION_FAILURE,
                **base_metadata,
                **_response_diagnostic_fields(content),
                validation_errors=_safe_validation_errors(error),
            )
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage=diagnostic.stage.value,
                failure_metadata=diagnostic.model_dump(mode="json"),
            )
            raise QueryPlanProviderBoundaryError(
                "Provider plan failed the QueryPlanWireV2 schema", diagnostic
            ) from error
        try:
            plan = wire_to_query_plan_v1(wire)
        except (TypeError, ValueError, ValidationError) as error:
            diagnostic = QueryPlanProviderDiagnostic(
                stage=QueryPlanProviderFailureStage.CANONICALIZATION_FAILURE,
                **base_metadata,
                **_response_diagnostic_fields(content),
                validation_errors=_safe_validation_errors(error),
            )
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage=diagnostic.stage.value,
                failure_metadata=diagnostic.model_dump(mode="json"),
            )
            raise QueryPlanProviderBoundaryError(
                "Provider plan could not be canonicalized", diagnostic
            ) from error
        usage = payload.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        proposal = QueryPlanWireV2Proposal(
            wire=wire,
            provider="openai-compatible",
            model=payload.get("model") or self.settings.llm_model,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
            cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
            latency_ms=(perf_counter() - started) * 1000,
        )
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=plan.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _metric_grounding_messages(question, glossary)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("metric_grounding", question, glossary, messages)
        started = perf_counter()
        payload = await self._post(body)
        grounding = _metric_grounding_from_response(payload)
        usage = payload.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        proposal = GovernedMetricGroundingProposal(
            grounding=grounding,
            provider="openai-compatible",
            model=payload.get("model") or self.settings.llm_model,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
            cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
            latency_ms=(perf_counter() - started) * 1000,
        )
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_metric_grounding=grounding.model_dump(mode="json"),
            raw_content=_assistant_content(payload),
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_window_ir(self, question: str, schema_context: str) -> WindowQueryIRProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _window_ir_messages(question, schema_context)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("window_ir", question, schema_context, messages)
        started = perf_counter()
        payload = await self._post(body)
        try:
            proposal = _window_ir_from_response(payload, self.settings.llm_model).model_copy(
                update={"latency_ms": (perf_counter() - started) * 1000}
            )
        except MalformedProviderResponse:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=_assistant_content(payload),
                latency_ms=(perf_counter() - started) * 1000,
            )
            raise
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_window_ir=proposal.ir.model_dump(mode="json"),
            raw_content=_assistant_content(payload),
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_window_transport(
        self,
        question: str,
        schema_text: str,
        instruction: str,
        response_format: dict[str, Any] | None = None,
        operation: str = "window_transport",
    ) -> ProviderTransportProposal:
        """Make one diagnostic request without interpreting its representation."""
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = [
            {"role": "system", "content": f"{instruction}\n\nSCHEMA:\n{schema_text}"},
            {"role": "user", "content": question},
        ]
        body: dict[str, Any] = {"model": self.settings.llm_model, "messages": messages}
        if response_format is not None:
            body["response_format"] = response_format
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(operation, question, schema_text, messages, response_format)
        started = perf_counter()
        payload = await self._post(body)
        usage = payload.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        proposal = ProviderTransportProposal(
            content=_assistant_content(payload),
            provider="openai-compatible",
            model=payload.get("model") or self.settings.llm_model,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
            cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
            latency_ms=(perf_counter() - started) * 1000,
        )
        self._complete_model_io(
            payload,
            parsed_sql=None,
            raw_content=proposal.content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_top_k_plan(self, question: str, schema_context: str) -> TopKPlanProposal:
        return cast(
            TopKPlanProposal,
            await self._propose_operation_plan(
                "top_k", question, schema_context, TopKPlan, TopKPlanProposal
            ),
        )

    async def propose_ratio_plan(self, question: str, schema_context: str) -> RatioPlanProposal:
        return cast(
            RatioPlanProposal,
            await self._propose_operation_plan(
                "ratio", question, schema_context, RatioPlan, RatioPlanProposal
            ),
        )

    async def propose_window_plan(self, question: str, schema_context: str) -> WindowPlanProposal:
        return cast(
            WindowPlanProposal,
            await self._propose_operation_plan(
                "window", question, schema_context, WindowPlan, WindowPlanProposal
            ),
        )

    async def _propose_operation_plan(
        self,
        operation: str,
        question: str,
        schema_context: str,
        plan_type: type[OperationPlan],
        proposal_type: type[TopKPlanProposal] | type[RatioPlanProposal] | type[WindowPlanProposal],
    ) -> TopKPlanProposal | RatioPlanProposal | WindowPlanProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _operation_plan_messages(operation, question, schema_context)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io(operation + "_plan", question, schema_context, messages)
        started = perf_counter()
        payload = await self._post(body)
        content = _assistant_content(payload)
        try:
            data = json.loads(content) if content is not None else None
            if not isinstance(data, dict):
                raise TypeError("structured output is not an object")
            plan = plan_type.model_validate(data)
            usage = payload.get("usage") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            proposal = proposal_type(
                plan=plan,
                provider="openai-compatible",
                model=self.settings.llm_model,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except (TypeError, ValueError, KeyError) as error:
            raise MalformedProviderResponse(
                f"Provider response did not contain structured {operation} plan"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_operation_plan=plan.model_dump(mode="json"),
            raw_content=content,
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_result_shape(self, question: str, schema_context: str) -> ResultShapeProposal:
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _result_shape_messages(question, schema_context)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("result_shape", question, schema_context, messages)
        started = perf_counter()
        payload = await self._post(body)
        proposal = _result_shape_from_response(payload, self.settings.llm_model).model_copy(
            update={"latency_ms": (perf_counter() - started) * 1000}
        )
        self._complete_model_io(
            payload,
            parsed_sql=None,
            parsed_result_shape=proposal.model_dump(mode="json"),
            raw_content=_assistant_content(payload),
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_sql(
        self,
        request: QueryRequest,
        user_context: UserContext | None,
        schema_context: str,
        query_intent: QueryIntent | None = None,
        result_shape: ResultShapeProposal | None = None,
        operation_plan: OperationPlan | None = None,
    ) -> SqlProposal:
        del user_context
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _generation_messages(
            request.question, schema_context, query_intent, result_shape, operation_plan
        )
        if self.settings.llm_prompt_profile == "hardened":
            messages = _harden_generation_messages(messages)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        provenance = recorder_for_identity(
            self.provenance_sink,
            getattr(request, "correlation_id", None)
            or f"question:{semantic_hash(request.question)}",
        )
        provenance.emit(
            ProvenanceStage.PROVIDER_REQUEST,
            ProvenanceEventType.PROVIDER_REQUEST_READY,
            {"provider_request_hash": semantic_hash(body)},
        )
        self._begin_model_io("sql", request.question, schema_context, messages)
        started = perf_counter()
        try:
            payload = await self._post(body)
        except Exception as error:
            self._complete_model_io(
                None,
                parsed_sql=None,
                raw_content=None,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="PROVIDER_PROTOCOL",
                failure_metadata={"exception_type": type(error).__name__},
            )
            provenance.emit(
                ProvenanceStage.PROVIDER_RESPONSE,
                ProvenanceEventType.PROVIDER_RESPONSE_RECEIVED,
                {
                    "provider_response_id": self._response_metadata.get("request_id"),
                    "raw_provider_output": None,
                },
            )
            provenance.emit(
                ProvenanceStage.CANDIDATE_EXTRACTION,
                ProvenanceEventType.CANDIDATE_EXTRACTION_COMPLETED,
                {"candidate_extraction_status": "FAILURE", "candidate_sql_hash": None},
            )
            raise
        raw_content = _assistant_content(payload)
        provenance.emit(
            ProvenanceStage.PROVIDER_RESPONSE,
            ProvenanceEventType.PROVIDER_RESPONSE_RECEIVED,
            {
                "provider_response_id": self._response_metadata.get("request_id"),
                "raw_provider_output": bounded_text(raw_content),
            },
        )
        try:
            proposal = _proposal_from_response(payload, self.settings.llm_model)
        except Exception as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                raw_content=raw_content,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="PROVIDER_PROTOCOL",
                failure_metadata={"exception_type": type(error).__name__},
            )
            provenance.emit(
                ProvenanceStage.CANDIDATE_EXTRACTION,
                ProvenanceEventType.CANDIDATE_EXTRACTION_COMPLETED,
                {"candidate_extraction_status": "FAILURE", "candidate_sql_hash": None},
            )
            raise
        proposal = proposal.model_copy(update={"latency_ms": (perf_counter() - started) * 1000})
        provenance.emit(
            ProvenanceStage.CANDIDATE_EXTRACTION,
            ProvenanceEventType.CANDIDATE_EXTRACTION_COMPLETED,
            {
                "candidate_extraction_status": "SUCCESS",
                "candidate_sql_hash": text_hash(proposal.sql),
            },
        )
        self._complete_model_io(
            payload,
            parsed_sql=proposal.sql,
            parsed_result_shape=None,
            raw_content=_assistant_content(payload),
            latency_ms=proposal.latency_ms,
        )
        return proposal

    async def propose_blueprint_sql(
        self, request: QueryRequest, schema_context: str
    ) -> BlueprintSqlProposal:
        """Return one untrusted semantic blueprint and SQL in one request."""
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = blueprint_messages(request.question, schema_context)
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("blueprint_sql", request.question, schema_context, messages)
        started = perf_counter()
        payload: Any = None
        try:
            payload = await self._post(body)
        except Exception as error:
            self._complete_model_io(
                None,
                parsed_sql=None,
                parsed_blueprint=None,
                raw_content=None,
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="PROVIDER_TRANSPORT",
                failure_metadata={"exception_type": type(error).__name__},
            )
            raise
        try:
            usage = payload.get("usage") if isinstance(payload, dict) else {}
            usage = usage if isinstance(usage, dict) else {}
            completion_details = usage.get("completion_tokens_details") or {}
            prompt_details = usage.get("prompt_tokens_details") or {}
            raw_content = _assistant_content(payload)
            proposal = parse_blueprint_payload(
                raw_content,
                model=self.settings.llm_model,
                provider=self.provider_name,
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
                cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except Exception as error:
            self._complete_model_io(
                payload,
                parsed_sql=None,
                parsed_blueprint=None,
                raw_content=_assistant_content(payload),
                latency_ms=(perf_counter() - started) * 1000,
                failure_stage="PROVIDER_PROTOCOL",
                failure_metadata={"exception_type": type(error).__name__},
            )
            if isinstance(error, MalformedProviderResponse):
                raise
            raise MalformedProviderResponse(
                "Provider response did not contain a valid blueprint and SQL"
            ) from error
        self._complete_model_io(
            payload,
            parsed_sql=proposal.sql,
            parsed_blueprint=proposal.blueprint.model_dump(mode="json"),
            parsed_blueprint_warnings=proposal.parse_warnings,
            raw_content=_assistant_content(payload),
            latency_ms=proposal.latency_ms,
        )
        return proposal

    def consume_model_io(self) -> ModelIOCapture | None:
        """Return and clear the latest capture for an evaluation harness."""
        capture = self._last_model_io
        self._model_io_history = []
        self._last_model_io = None
        return capture

    def consume_model_io_history(self) -> list[ModelIOCapture]:
        """Return all captures since the previous consumption for evaluation only."""
        captures = [*self._model_io_history]
        if self._last_model_io is not None:
            captures.append(self._last_model_io)
        self._model_io_history = []
        self._last_model_io = None
        return captures

    def consume_response_wire(self) -> bytes | None:
        """Return and clear the exact most recent HTTP response body bytes."""
        wire = self._last_response_wire
        self._last_response_wire = None
        return wire

    def _begin_model_io(
        self,
        operation: str,
        question: str,
        schema_context: str,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> None:
        if not self.settings.eval_capture_model_io:
            return
        self._response_metadata = {}
        if self._last_model_io is not None:
            self._model_io_history.append(self._last_model_io)
        effective_response_format = response_format
        if effective_response_format is None and operation.upper() not in {
            "WINDOW_TRANSPORT",
            "PLAIN_JSON_TEXT",
            "WINDOW_DSL",
            "BIRD_SQL",
        }:
            effective_response_format = {"type": "json_object"}
        request_config: dict[str, Any] = {
            "model": self.settings.llm_model,
            "reasoning_effort": self.settings.llm_reasoning_effort,
            "temperature": self.settings.llm_temperature,
            "prompt_profile": self.settings.llm_prompt_profile,
            "endpoint_family": "chat_completions",
            "timeout_seconds": self.settings.llm_timeout_seconds,
        }
        if effective_response_format is not None:
            request_config["response_format"] = effective_response_format
        self._last_model_io = ModelIOCapture(
            operation=operation,
            question=question,
            serialized_schema_context=schema_context,
            request_config=request_config,
            messages=messages,
        )

    def _complete_model_io(
        self,
        payload: Any,
        *,
        parsed_sql: str | None,
        parsed_result_shape: dict[str, Any] | None = None,
        parsed_operation_plan: dict[str, Any] | None = None,
        parsed_window_ir: dict[str, Any] | None = None,
        parsed_metric_grounding: dict[str, Any] | None = None,
        parsed_blueprint: dict[str, Any] | None = None,
        parsed_blueprint_warnings: tuple[str, ...] = (),
        raw_content: str | None,
        latency_ms: float | None,
        failure_stage: str | None = None,
        failure_metadata: dict[str, Any] | None = None,
        attempt_count: int = 1,
        technical_retry_count: int = 0,
    ) -> None:
        if not self.settings.eval_capture_model_io or self._last_model_io is None:
            return
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        completion_details = usage.get("completion_tokens_details")
        completion_details = completion_details if isinstance(completion_details, dict) else {}
        prompt_details = usage.get("prompt_tokens_details")
        prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
        choice = payload.get("choices", [{}])[0] if isinstance(payload, dict) else {}
        choice = choice if isinstance(choice, dict) else {}
        bounded_content, content_truncated = _bounded_response_content(raw_content)
        self._last_model_io = self._last_model_io.model_copy(
            update={
                "response_model": payload.get("model") if isinstance(payload, dict) else None,
                "raw_assistant_content": bounded_content,
                "raw_assistant_content_full": raw_content,
                "raw_assistant_content_sha256": (
                    text_hash(raw_content) if raw_content is not None else None
                ),
                "raw_assistant_content_truncated": content_truncated,
                "parsed_sql": parsed_sql,
                "parsed_result_shape": parsed_result_shape,
                "parsed_operation_plan": parsed_operation_plan,
                "parsed_window_ir": parsed_window_ir,
                "parsed_metric_grounding": parsed_metric_grounding,
                "parsed_blueprint": parsed_blueprint,
                "parsed_blueprint_warnings": parsed_blueprint_warnings,
                "usage": {
                    "prompt_tokens": _optional_int(usage.get("prompt_tokens")),
                    "completion_tokens": _optional_int(usage.get("completion_tokens")),
                    "reasoning_tokens": _optional_int(completion_details.get("reasoning_tokens")),
                    "cached_prompt_tokens": _optional_int(prompt_details.get("cached_tokens")),
                },
                "latency_ms": latency_ms,
                "finish_reason": _bounded_string(choice.get("finish_reason")),
                "system_fingerprint": _bounded_string(
                    payload.get("system_fingerprint") if isinstance(payload, dict) else None
                ),
                "request_id": self._response_metadata.get("request_id"),
                "provider_response_id": _bounded_request_id(
                    payload.get("id") if isinstance(payload, dict) else None
                ),
                "failure_stage": failure_stage,
                "failure_metadata": failure_metadata or {},
                "attempt_count": attempt_count,
                "technical_retry_count": technical_retry_count,
            }
        )

    def _set_capture_request_id(self) -> None:
        if self.settings.eval_capture_model_io and self._last_model_io is not None:
            self._last_model_io = self._last_model_io.model_copy(
                update={"request_id": self._response_metadata.get("request_id")}
            )

    async def _post(self, body: dict[str, Any]) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                self._last_response_wire = bytes(response.content)
                response.raise_for_status()
                payload = response.json()
                self._response_metadata = {
                    "request_id": _bounded_request_id(
                        response.headers.get("x-request-id") or response.headers.get("request-id")
                    )
                }
                return payload
        except httpx.HTTPStatusError as error:
            self._response_metadata = {
                "request_id": _bounded_request_id(
                    error.response.headers.get("x-request-id")
                    or error.response.headers.get("request-id")
                )
            }
            self._set_capture_request_id()
            detail = _provider_error_detail(
                error.response,
                model=self.settings.llm_model,
                secret=self.settings.llm_api_key,
            )
            raise LLMProviderError("OpenAI-compatible provider request failed", detail) from error
        except httpx.RequestError as error:
            detail = ProviderErrorDetail(
                error_type="network_error",
                error_code=type(error).__name__,
                message="Provider network request failed.",
                model=self.settings.llm_model,
                retryable=True,
            )
            raise LLMProviderError("OpenAI-compatible provider request failed", detail) from error
        except ValueError as error:
            raise MalformedProviderResponse("Provider returned invalid JSON") from error


def _query_plan_wire_v2_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": query_plan_wire_v2_prompt(schema_context)},
        {"role": "user", "content": question},
    ]


def _bounded_response_content(content: str | None, limit: int = 4096) -> tuple[str | None, bool]:
    if content is None:
        return None, False
    return content[:limit], len(content) > limit


def _response_diagnostic_fields(content: str) -> dict[str, Any]:
    preview, truncated = _bounded_response_content(content)
    return {
        "response_preview": preview,
        "response_sha256": text_hash(content),
        "response_truncated": truncated,
    }


def _query_plan_response_metadata(
    payload: Any, response_metadata: dict[str, str | None], model: str
) -> dict[str, Any]:
    choice = payload.get("choices", [{}])[0] if isinstance(payload, dict) else {}
    choice = choice if isinstance(choice, dict) else {}
    return {
        "response_id": response_metadata.get("request_id")
        or _bounded_request_id(payload.get("id") if isinstance(payload, dict) else None),
        "resolved_model": payload.get("model") or model if isinstance(payload, dict) else model,
        "finish_reason": _bounded_string(choice.get("finish_reason")),
        "attempt_count": 1,
    }


def _safe_validation_errors(error: Exception) -> tuple[dict[str, str], ...]:
    if not isinstance(error, ValidationError):
        return ({"loc": "__root__", "type": type(error).__name__, "msg": str(error)[:240]},)
    safe: list[dict[str, str]] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "__root__"
        safe.append(
            {
                "loc": location,
                "type": str(item.get("type", "validation_error")),
                "msg": str(item.get("msg", "validation failed"))[:240],
            }
        )
    return tuple(safe[:20])


class StaticLLMProvider:
    """Deterministic provider useful for tests and explicit local evaluation fixtures."""

    def __init__(
        self,
        sql: str | None = None,
        model: str = "static-test",
        query_plan: QueryPlanV1 | None = None,
        semantic_plan: SemanticQueryPlan | None = None,
        logical_plan: LogicalQueryPlanV1 | None = None,
        decision: ProductionDecision | None = None,
    ) -> None:
        if sql is None and decision is None:
            raise ValueError("Static provider requires sql or decision")
        legacy_sql = sql or (decision.sql if decision is not None else None) or "SELECT 1"
        self.proposal = SqlProposal(sql=legacy_sql, provider="static", model=model)
        self.decision = decision or ProductionDecision(decision="ANSWER", sql=sql)
        self.decision_proposal = ProductionDecisionProposal(
            decision=self.decision, provider="static", model=model
        )
        self.query_plan = query_plan
        self.semantic_plan = semantic_plan
        self.logical_plan = logical_plan

    async def propose_decision(
        self, question: str, schema_context: str
    ) -> ProductionDecisionProposal:
        del question, schema_context
        return self.decision_proposal

    async def propose_schema_alignment(
        self, question: str, schema_context: str
    ) -> SchemaAlignmentProposal:
        del question, schema_context
        raise NotImplementedError("static M32 schema alignment is not configured")

    async def propose_logical_synthesis(
        self, question: str, grounded_schema_context: str
    ) -> LogicalSynthesisProposal:
        del question, grounded_schema_context
        raise NotImplementedError("static M32 logical synthesis is not configured")

    async def propose_query_alignment(
        self, question: str, schema_context: str
    ) -> QueryAlignmentProposal:
        del question, schema_context
        raise NotImplementedError("static M32 alignment is not configured")

    async def propose_aligned_sql(self, question: str, grounded_context: str) -> SqlProposal:
        del question, grounded_context
        return self.proposal

    async def review_aligned_sql(
        self,
        question: str,
        grounded_context: str,
        initial_sql: str,
        m1_status: str,
        explain_diagnostic: str,
    ) -> SqlProposal:
        del question, grounded_context, initial_sql, m1_status, explain_diagnostic
        return self.proposal

    async def propose_query_plan_v1(
        self, question: str, schema_context: str
    ) -> QueryPlanV1Proposal:
        del question, schema_context
        if self.query_plan is None:
            raise NotImplementedError("static QueryPlan V1 proposal is not configured")
        return QueryPlanV1Proposal(
            plan=self.query_plan, provider="static", model=self.proposal.model
        )

    async def propose_semantic_query_plan(
        self, question: str, schema_context: str
    ) -> SemanticQueryPlanProposal:
        del question, schema_context
        if self.semantic_plan is None:
            raise NotImplementedError("static semantic query plan proposal is not configured")
        return SemanticQueryPlanProposal(
            plan=self.semantic_plan, provider="static", model=self.proposal.model
        )

    async def propose_logical_query_plan(
        self, question: str, schema_context: str
    ) -> LogicalQueryPlanProposal:
        del question, schema_context
        if self.logical_plan is None:
            raise NotImplementedError("static logical query plan proposal is not configured")
        return LogicalQueryPlanProposal(
            plan=self.logical_plan, provider="static", model=self.proposal.model
        )

    async def propose_query_plan_wire_v2(
        self, question: str, schema_context: str
    ) -> QueryPlanWireV2Proposal:
        del question, schema_context
        if self.query_plan is None:
            raise NotImplementedError("static QueryPlan wire proposal is not configured")
        wire = QueryPlanWireV2.model_validate(self.query_plan.model_dump(mode="json"))
        return QueryPlanWireV2Proposal(wire=wire, provider="static", model=self.proposal.model)

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        return GovernedMetricGroundingProposal(
            grounding=GovernedMetricGroundingDTO(metric_name="completed_revenue", dimensions=()),
            provider="static",
            model=self.proposal.model,
        )

    async def propose_window_ir(self, question: str, schema_context: str) -> WindowQueryIRProposal:
        del question, schema_context
        ir = WindowQueryIR(
            source_relation="products",
            pattern="RANKING",
            physical_outputs=("products.id",),
            computations=(
                {
                    "pattern": "RANKING",
                    "function": "ROW_NUMBER",
                    "order_by": ({"column": "products.id", "direction": "ASC"},),
                    "alias": "row_number",
                },
            ),
        )
        return WindowQueryIRProposal(ir=ir, provider="static", model=self.proposal.model)

    async def propose_top_k_plan(self, question: str, schema_context: str) -> TopKPlanProposal:
        del question, schema_context
        return TopKPlanProposal(
            plan=TopKPlan(
                entity_outputs=("result.entity",),
                measure={"semantic_label": "result", "aggregation": "COUNT"},
                group_by=("result.entity",),
                order_direction="DESC",
                limit=1,
            ),
            provider="static",
            model=self.proposal.model,
        )

    async def propose_ratio_plan(self, question: str, schema_context: str) -> RatioPlanProposal:
        del question, schema_context
        return RatioPlanProposal(
            plan=RatioPlan(
                numerator={"semantic_label": "numerator", "aggregation": "COUNT"},
                denominator={"semantic_label": "denominator", "aggregation": "COUNT"},
                grain="row",
            ),
            provider="static",
            model=self.proposal.model,
        )

    async def propose_window_plan(self, question: str, schema_context: str) -> WindowPlanProposal:
        del question, schema_context
        return WindowPlanProposal(
            plan=WindowPlan(
                requested_outputs=("result.value",),
                window_function="ROW_NUMBER",
                order_by=("result.value",),
                order_direction="ASC",
            ),
            provider="static",
            model=self.proposal.model,
        )

    async def propose_result_shape(self, question: str, schema_context: str) -> ResultShapeProposal:
        del question, schema_context
        return ResultShapeProposal(
            outputs=({"semantic_label": "result", "kind": "DERIVED_VALUE"},),
            shape="OTHER",
            provider="static",
            model=self.proposal.model,
        )

    async def propose_intent(self, question: str, schema_context: str) -> IntentProposal:
        del question, schema_context
        return IntentProposal(intent=QueryIntent(), provider="static", model=self.proposal.model)

    async def propose_sql(
        self,
        request: QueryRequest,
        user_context: UserContext | None,
        schema_context: str,
        query_intent: QueryIntent | None = None,
        result_shape: ResultShapeProposal | None = None,
        operation_plan: OperationPlan | None = None,
    ) -> SqlProposal:
        del request, user_context, schema_context, query_intent, result_shape, operation_plan
        return self.proposal


class UnconfiguredLLMProvider:
    async def propose_decision(
        self, question: str, schema_context: str
    ) -> ProductionDecisionProposal:
        del question, schema_context
        raise ProviderConfigurationError("No production decision provider is configured")

    async def propose_schema_alignment(
        self, question: str, schema_context: str
    ) -> SchemaAlignmentProposal:
        del question, schema_context
        raise NotImplementedError("M32 schema alignment generation is not configured")

    async def propose_logical_synthesis(
        self, question: str, grounded_schema_context: str
    ) -> LogicalSynthesisProposal:
        del question, grounded_schema_context
        raise NotImplementedError("M32 logical synthesis generation is not configured")

    async def propose_query_alignment(
        self, question: str, schema_context: str
    ) -> QueryAlignmentProposal:
        del question, schema_context
        raise NotImplementedError("M32 alignment generation is not configured")

    async def propose_aligned_sql(self, question: str, grounded_context: str) -> SqlProposal:
        del question, grounded_context
        raise NotImplementedError("M32 aligned SQL generation is not configured")

    async def review_aligned_sql(
        self,
        question: str,
        grounded_context: str,
        initial_sql: str,
        m1_status: str,
        explain_diagnostic: str,
    ) -> SqlProposal:
        del question, grounded_context, initial_sql, m1_status, explain_diagnostic
        raise NotImplementedError("M32 SQL review generation is not configured")

    async def propose_logical_query_plan(
        self, question: str, schema_context: str
    ) -> LogicalQueryPlanProposal:
        del question, schema_context
        raise NotImplementedError("logical query plan generation is not configured")

    async def propose_semantic_query_plan(
        self, question: str, schema_context: str
    ) -> SemanticQueryPlanProposal:
        del question, schema_context
        raise NotImplementedError("semantic query plan generation is not configured")

    async def propose_query_plan_v1(
        self, question: str, schema_context: str
    ) -> QueryPlanV1Proposal:
        del question, schema_context
        raise NotImplementedError("QueryPlan V1 generation is not configured")

    async def propose_query_plan_wire_v2(
        self, question: str, schema_context: str
    ) -> QueryPlanWireV2Proposal:
        del question, schema_context
        raise NotImplementedError("QueryPlan Wire V2 generation is not configured")

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        del question, glossary
        raise NotImplementedError("M3 governed metric grounding is not configured")

    async def propose_window_ir(self, question: str, schema_context: str) -> WindowQueryIRProposal:
        del question, schema_context
        raise NotImplementedError("M2.12 Window IR generation is not enabled")

    async def propose_top_k_plan(self, question: str, schema_context: str) -> TopKPlanProposal:
        del question, schema_context
        raise NotImplementedError("M2.11 operation planning is not enabled")

    async def propose_ratio_plan(self, question: str, schema_context: str) -> RatioPlanProposal:
        del question, schema_context
        raise NotImplementedError("M2.11 operation planning is not enabled")

    async def propose_window_plan(self, question: str, schema_context: str) -> WindowPlanProposal:
        del question, schema_context
        raise NotImplementedError("M2.11 operation planning is not enabled")

    async def propose_result_shape(self, question: str, schema_context: str) -> ResultShapeProposal:
        del question, schema_context
        raise NotImplementedError("LLM result-shape generation is not enabled in M2")

    async def propose_intent(self, question: str, schema_context: str) -> IntentProposal:
        del question, schema_context
        raise NotImplementedError("LLM intent grounding is not enabled in M2")

    async def propose_sql(
        self,
        request: QueryRequest,
        user_context: UserContext | None,
        schema_context: str,
        query_intent: QueryIntent | None = None,
        result_shape: ResultShapeProposal | None = None,
        operation_plan: OperationPlan | None = None,
    ) -> SqlProposal:
        del request, user_context, schema_context, query_intent, result_shape, operation_plan
        raise NotImplementedError("LLM generation is intentionally not enabled in M2")


def _intent_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Identify only the structural ingredients needed for one read-only PostgreSQL "
        "analytical query. Use only tables and fully-qualified columns in the bounded "
        "schema context. Do not provide reasoning. Return exactly one JSON object with "
        "selected_tables, selected_columns, joins (objects with source_table, "
        "source_column, target_table, target_column), filters, aggregations, group_by, "
        "order_by, limit, and window_operations."
        f"\n\nBOUNDED SCHEMA CONTEXT:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _production_decision_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    user = (
        "Case ID:\noperator\n\nQuestion:\n" + question + "\n\nGoverned context:\n" + schema_context
    )
    return [
        {"role": "system", "content": production_decision_prompt()},
        {"role": "user", "content": user},
    ]


def _query_plan_v1_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Propose only one bounded QueryPlan V1 JSON object for the user's request. "
        "Return no markdown, explanation, SQL, raw identifiers outside the supplied "
        "context, or extra fields. The only fields are applicable, source, joins, "
        "projection, and filters. Use exactly the server-owned table and column IDs. "
        "Use at most two connected joins, one to three projection column IDs, and at "
        "most two AND predicates. Each join contains only relationship_id and join_type "
        "(INNER or LEFT). Each predicate contains only column_id, operator, and an "
        "optional typed value with kind/value. Supported operators are EQ, NE, LT, LTE, "
        "GT, GTE, IS_NULL, and IS_NOT_NULL. Never emit sql, raw_sql, expression, "
        "on_clause, where_sql, order_by, group_by, having, window, function, or any "
        "other field. All evaluation requests are pre-scoped as applicable; do not "
        "use applicability to select a runtime route."
        f"\n\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _semantic_query_plan_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Propose one bounded semantic query plan for the user's PostgreSQL question. "
        "Return exactly the structured object described by the response schema. Use the "
        "exact schema field names and enum values; do not use aliases such as id, "
        "column_id, entity_id, args, operands, or data_type. Return JSON only and never "
        "emit SQL, SQL fragments, ON predicates, physical table names, physical column "
        "names, or arbitrary function names. Use only the server-owned semantic IDs in "
        "the context. The top-level contract uses database_id, from_entity_id, "
        "from_source, population_contract, outputs, joins, where, group_by, having, "
        "order_by, distinct, limit, offset, calculation_contract, description, ctes, "
        "and derived_relations. Nested objects must use their canonical names, including "
        "population_contract.base_entity_ids, output.position and semantic_role, with "
        "output positions contiguous from zero, "
        "attribute.attribute_id, function.arguments, logical.terms, and literal.value_type. "
        "Each output has exactly one of attribute_id or expression; a plan has exactly one "
        "of from_entity_id or from_source. A relationship join uses relationship_id or "
        "relationship_path, never ad-hoc join keys. Logical nodes contain at least two "
        "terms. "
        "Expressions must be typed nodes with kinds attribute, literal, aggregate, "
        "binary, logical, not, between, in, is_null, cast, case, function, window, or "
        "exists. Functions and operators must use the bounded enum values in the supplied "
        "contract. Join objects contain only relationship_id and join_type; the server "
        "owns join paths and predicates. Populate optional fields with their schema-defined "
        "null or empty value when applicable. Do not provide reasoning or extra fields."
        f"\n\nSERVER-OWNED SEMANTIC CONTEXT:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _logical_query_plan_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Translate the question into the provided LogicalQueryPlanV1. Express only the "
        "logical query meaning and return exactly the structured plan required by the "
        "schema. Use only supplied semantic entity and attribute IDs. Build the minimum "
        "necessary ordered sequence of SCAN, RELATE, FILTER, PROJECT, AGGREGATE, COMPUTE, "
        "WINDOW, SORT, and TOP steps. Earlier step results may be referenced only by their "
        "integer step index. Use MATCHING when the question asks for entities with a "
        "matching relation; use PRESERVE_LEFT when it explicitly asks to include unmatched "
        "left-side entities. Filters, typed values, grouping, calculations, windows, order, "
        "and limits must be represented structurally. When a nested scalar/filter explicitly "
        "refers to an enclosing row, use the bounded outer_attribute reference with its "
        "semantic attribute ID and scope depth; do not use it for hidden server mechanics. "
        "A scalar_result reference is valid only when its referenced step produces exactly "
        "one scalar output; use result with a slot for a multi-output step. Keep the final "
        "step as the smallest result needed by the question, and use PROJECT to expose the "
        "requested values. "
        "Do not emit SQL, physical table or "
        "column names, relationship IDs, join keys, aliases, output positions, CTE names, "
        "raw expressions, or arbitrary function/operator names. Do not add operations not "
        "requested. Return JSON only; do not provide reasoning."
        f"\n\nBOUNDED SEMANTIC CATALOG:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _query_alignment_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Align the user's PostgreSQL question to the supplied semantic catalog. Return "
        "only the QueryAlignmentV1 structured object. Select only supplied semantic entity, "
        "attribute, and relationship IDs. Capture the requested population, explicit filters, "
        "aggregations, grouping, calculation, temporal meaning, ordering, limit, and bounded "
        "query shape. The short logic_summary must describe the intended computation in plain "
        "language and must not contain SQL. Do not emit SQL, physical table or column names, "
        "join conditions, aliases, CTE names, execution steps, compiler fields, or arbitrary "
        "IDs. Do not add intent that is not present in the question. Return JSON only."
        f"\n\nBOUNDED SEMANTIC DATABASE EVIDENCE:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _schema_alignment_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Select the semantic database objects needed to answer the user's PostgreSQL "
        "question. Return only the SchemaAlignmentV1 structured object. Use only supplied "
        "semantic entity and attribute IDs. Select the smallest sufficient set of entities "
        "and attributes, including attributes needed as requested outputs, filters, grouping, "
        "aggregation inputs, calculations, ordering, or temporal interpretation. Do not emit "
        "relationship IDs, physical tables or columns, SQL, aliases, query steps, or execution "
        "mechanics. The logic_summary is a short plain-language description, not SQL. Return "
        "JSON only."
        f"\n\nBOUNDED SEMANTIC DATABASE EVIDENCE:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _logical_synthesis_messages(
    question: str, grounded_schema_context: str
) -> list[dict[str, str]]:
    system = (
        "Describe the logical meaning of the user's PostgreSQL question using only the "
        "validated, server-grounded schema below. Return only the LogicalSynthesisV1 "
        "structured object. Express population, explicit filters, aggregation, grouping, "
        "calculation, temporal meaning, ordering, limit, and bounded query shape. Use only "
        "the supplied semantic attribute IDs. Do not select new entities or attributes, emit "
        "SQL, physical names, join conditions, aliases, CTE names, execution steps, or raw "
        "expressions. The logic_summary is short plain language and not SQL. Do not invent "
        "intent not stated by the question. Return JSON only."
        f"\n\nVALIDATED GROUNDED SCHEMA:\n{grounded_schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _aligned_sql_messages(question: str, grounded_context: str) -> list[dict[str, str]]:
    system = (
        "Generate one read-only PostgreSQL SELECT query answering the user's question. "
        "Use the validated semantic alignment and server-grounded schema below. Use only "
        "the supplied physical tables and columns, and only the authorized relationships. "
        "Respect the population, filters, aggregation, grouping, calculation, temporal, "
        "ordering, limit, nesting, and correlation semantics in the alignment. Do not invent "
        "tables, columns, join keys, or values. Return exactly one JSON object with only a "
        "non-empty sql string. Do not return Markdown or explanation."
        f"\n\nGROUNDED SQL CONTEXT:\n{grounded_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _review_aligned_sql_messages(
    question: str,
    grounded_context: str,
    initial_sql: str,
    m1_status: str,
    explain_diagnostic: str,
) -> list[dict[str, str]]:
    system = (
        "Review one PostgreSQL SELECT query against the validated semantic alignment and "
        "server-grounded schema. Return exactly one JSON object with only a non-empty sql "
        "string. Correct the query if it fails the alignment, uses an unauthorized schema "
        "object, or violates the supplied server diagnostic. Use only supplied physical "
        "tables, columns, and authorized relationships. Do not use evaluator results, gold "
        "SQL, expected rows, or unstated user intent. Preserve correct portions. Return JSON "
        "only, with no explanation."
        f"\n\nQUESTION:\n{question}"
        f"\n\nGROUNDED CONTEXT:\n{grounded_context}"
        f"\n\nINITIAL SQL:\n{initial_sql}"
        f"\n\nM1 STATUS: {m1_status}\nEXPLAIN DIAGNOSTIC: {explain_diagnostic}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _metric_grounding_messages(question: str, glossary: str) -> list[dict[str, str]]:
    system = (
        "Select a governed business metric only when the question directly asks for "
        "one of the listed metrics. Otherwise return not applicable. Return exactly "
        "one JSON object with only these keys: applicable, metric_name, and dimensions. "
        "Use only names from the supplied glossary. For not applicable, use false, "
        "null, and an empty list. Do not return SQL, physical tables, physical columns, "
        "formulas, filters, joins, aliases, confidence, or reasoning."
        f"\n\nPUBLIC GOVERNED SEMANTIC GLOSSARY:\n{glossary}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _generation_messages(
    question: str,
    schema_context: str,
    query_intent: QueryIntent | None = None,
    result_shape: ResultShapeProposal | None = None,
    operation_plan: OperationPlan | None = None,
) -> list[dict[str, str]]:
    intent_guidance = ""
    if query_intent is not None:
        intent_guidance = (
            "\n\nPROPOSED STRUCTURAL INTENT (untrusted guidance; verify against schema):\n"
            f"{query_intent.model_dump_json(exclude_none=True)}"
        )
    shape_guidance = ""
    if result_shape is not None:
        shape_guidance = (
            "\n\nRESULT SHAPE CONTRACT (untrusted generation guidance):\n"
            "Return exactly the requested outputs. Do not add convenience, debug, or "
            "context columns. Preserve explicit limit and order direction when present.\n"
            f"{
                result_shape.model_dump_json(
                    exclude_none=True,
                    exclude={
                        'provider',
                        'model',
                        'prompt_tokens',
                        'completion_tokens',
                        'reasoning_tokens',
                        'cached_prompt_tokens',
                        'latency_ms',
                    },
                )
            }"
        )
    operation_plan_guidance = ""
    if operation_plan is not None:
        operation_plan_guidance = (
            "\n\nNARROW OPERATION PLAN (untrusted decomposition aid; use the original "
            "question and schema to resolve omissions):\n"
            "Generate one PostgreSQL read-only query for the original question. "
            "Do not add analytical operations not implied by the question or plan.\n"
            f"{operation_plan.model_dump_json(exclude_none=True)}"
        )
    system = (
        "You generate one read-only PostgreSQL analytical query. "
        "Use only the bounded schema context below. Do not invent tables or columns. "
        "Return exactly one JSON object with a non-empty 'sql' string and no Markdown.\n\n"
        f"BOUNDED SCHEMA CONTEXT:\n{schema_context}{intent_guidance}{shape_guidance}"
        f"{operation_plan_guidance}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def _harden_generation_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Add audited generation guidance while preserving the frozen legacy builder."""
    if not messages:
        return messages
    first = messages[0]
    return [
        {**first, "content": f"{first['content']}\n\n{render_query_quality_pack()}"},
        *messages[1:],
    ]


def _result_shape_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Identify the minimum result shape for one read-only PostgreSQL analytical query. "
        "Use only the supplied structural schema. Return exactly one JSON object with "
        "outputs (semantic_label, kind PHYSICAL_COLUMN or DERIVED_VALUE, optional "
        "fully-qualified source_hint), shape (ROW_FILTER, AGGREGATE, GROUPED_AGGREGATE, "
        "GROUPED_TOP_K, RATIO, WINDOW, or OTHER), optional explicit_limit, and optional "
        "explicit_order_direction (ASC or DESC). Do not provide reasoning, tables, joins, "
        "filters, formulas, values, or examples.\n\n"
        f"FULL STRUCTURAL SCHEMA CONTEXT:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _window_ir_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    system = (
        "Map the analytics question to one typed WindowQueryIR for a single queryable "
        "PostgreSQL table. Choose only the semantic window pattern, physical outputs, "
        "partitioning, ordering, target, frame, and bounded parameters. Do not write SQL, "
        "SQL fragments, joins, filters, formulas, or reasoning. Return exactly one JSON "
        "object matching the WindowQueryIR contract. Supported patterns are "
        "LATEST_PER_GROUP, TOP_N_PER_GROUP, LAG, LEAD, RUNNING_AGGREGATE, "
        "MOVING_AGGREGATE, RANKING, SHARE_OF_TOTAL, and MIXED_MULTI_WINDOW.\n\n"
        f"FULL STRUCTURAL SCHEMA CONTEXT:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _operation_plan_messages(
    operation: str, question: str, schema_context: str
) -> list[dict[str, str]]:
    instructions = {
        "top_k": (
            "Return a compact TopKPlan with entity_outputs, measure containing "
            "semantic_label/aggregation/components, group_by, order_direction, and limit. "
            "Do not include joins, FROM, WHERE SQL, complete SQL, or reasoning."
        ),
        "ratio": (
            "Return a compact RatioPlan with numerator and denominator components "
            "(semantic_label/source_columns/aggregation/distinct), grain, and optional scale. "
            "Do not define business metrics, formulas, complete SQL, or reasoning."
        ),
        "window": (
            "Return a compact WindowPlan with requested_outputs, window_function, "
            "partition_by, order_by, and order_direction. Do not include joins, FROM, WHERE "
            "SQL, complete SQL, or reasoning."
        ),
    }
    system = (
        "Identify only the structural operation decisions needed for the original question. "
        "Use only fully-qualified tables and columns visible in the supplied PostgreSQL "
        "schema. Return exactly one JSON object and do not provide reasoning. "
        f"{instructions[operation]}\n\nFULL STRUCTURAL SCHEMA CONTEXT:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _proposal_from_response(payload: Any, model: str) -> SqlProposal:
    try:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not text")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("structured output is not an object")
        sql = data.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise TypeError("sql is missing")
        usage = payload.get("usage") or {}
        return SqlProposal(
            sql=sql,
            provider="openai-compatible",
            model=model,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            reasoning_tokens=_optional_int(
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            ),
            cached_prompt_tokens=_optional_int(
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            ),
            confidence=_optional_float(data.get("confidence")),
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise MalformedProviderResponse(
            "Provider response did not contain structured SQL"
        ) from error


def _result_shape_from_response(payload: Any, model: str) -> ResultShapeProposal:
    try:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not text")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("structured output is not an object")
        usage = payload.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        return ResultShapeProposal(
            **data,
            provider="openai-compatible",
            model=model,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
            cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise MalformedProviderResponse(
            "Provider response did not contain structured ResultShape"
        ) from error


def _window_ir_from_response(payload: Any, model: str) -> WindowQueryIRProposal:
    try:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not text")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("structured output is not an object")
        usage = payload.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        return WindowQueryIRProposal(
            ir=WindowQueryIR.model_validate(data),
            provider="openai-compatible",
            model=model,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            reasoning_tokens=_optional_int(completion_details.get("reasoning_tokens")),
            cached_prompt_tokens=_optional_int(prompt_details.get("cached_tokens")),
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise MalformedProviderResponse(
            "Provider response did not contain structured WindowQueryIR"
        ) from error


def _assistant_content(payload: Any) -> str | None:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def _metric_grounding_from_response(payload: Any) -> GovernedMetricGroundingDTO:
    try:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not text")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("structured output is not an object")
        return GovernedMetricGroundingDTO.model_validate(data)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise MalformedProviderResponse(
            "Provider response did not contain governed metric grounding"
        ) from error


def _intent_from_response(payload: Any, model: str) -> IntentProposal:
    try:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not text")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("structured output is not an object")
        return IntentProposal(
            intent=QueryIntent.model_validate(data),
            provider="openai-compatible",
            model=model,
            prompt_tokens=_optional_int((payload.get("usage") or {}).get("prompt_tokens")),
            completion_tokens=_optional_int((payload.get("usage") or {}).get("completion_tokens")),
            reasoning_tokens=_optional_int(
                ((payload.get("usage") or {}).get("completion_tokens_details") or {}).get(
                    "reasoning_tokens"
                )
            ),
            cached_prompt_tokens=_optional_int(
                ((payload.get("usage") or {}).get("prompt_tokens_details") or {}).get(
                    "cached_tokens"
                )
            ),
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise MalformedProviderResponse(
            "Provider response did not contain structured QueryIntent"
        ) from error


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _optional_float(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) else None


def _add_reasoning_effort(body: dict[str, Any], reasoning_effort: str | None) -> None:
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort


def _add_temperature(body: dict[str, Any], temperature: float | None) -> None:
    if temperature is not None:
        body["temperature"] = temperature


def _provider_error_detail(
    response: httpx.Response, model: str, secret: str | None
) -> ProviderErrorDetail:
    payload: Any = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    error_payload = payload.get("error") if isinstance(payload, dict) else None
    error_payload = error_payload if isinstance(error_payload, dict) else {}
    request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
    return ProviderErrorDetail(
        status_code=response.status_code,
        error_type=_bounded_string(error_payload.get("type")),
        error_code=_bounded_string(error_payload.get("code")),
        message=_sanitize_message(
            error_payload.get("message") or "Provider returned an HTTP error.", secret
        ),
        request_id=_bounded_request_id(request_id),
        model=model,
        retryable=response.status_code == 408
        or response.status_code == 409
        or response.status_code == 429
        or response.status_code >= 500,
    )


def _bounded_string(value: Any, limit: int = 120) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return " ".join(value.split())[:limit]


def _sanitize_message(value: Any, secret: str | None) -> str:
    message = " ".join(str(value).split())
    if secret:
        message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", message)
    message = re.sub(r"(?i)(api[_ -]?key|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", message)
    return message[:500]


def _bounded_request_id(value: str | None) -> str | None:
    if value is None or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        return None
    return value
