import json
import re
from time import perf_counter
from typing import Any, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
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
from app.generation.result_shape import ResultShapeProposal
from app.generation.window_ir import WindowQueryIR, WindowQueryIRProposal
from app.models.domain import QueryRequest, UserContext


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
    parsed_sql: str | None = None
    parsed_result_shape: dict[str, Any] | None = None
    parsed_operation_plan: dict[str, Any] | None = None
    parsed_window_ir: dict[str, Any] | None = None
    parsed_metric_grounding: dict[str, Any] | None = None
    usage: dict[str, int | None] = Field(default_factory=dict)
    latency_ms: float | None = None
    finish_reason: str | None = None
    system_fingerprint: str | None = None
    request_id: str | None = None


class LLMProviderError(RuntimeError):
    def __init__(self, message: str, detail: ProviderErrorDetail | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ProviderConfigurationError(LLMProviderError):
    pass


class MalformedProviderResponse(LLMProviderError):
    pass


class LLMProvider(Protocol):
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

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._last_model_io: ModelIOCapture | None = None
        self._model_io_history: list[ModelIOCapture] = []
        self._response_metadata: dict[str, str | None] = {}

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
        response_format: dict[str, str] | None = None,
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
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        _add_temperature(body, self.settings.llm_temperature)
        _add_reasoning_effort(body, self.settings.llm_reasoning_effort)
        self._begin_model_io("sql", request.question, schema_context, messages)
        started = perf_counter()
        payload = await self._post(body)
        proposal = _proposal_from_response(payload, self.settings.llm_model)
        proposal = proposal.model_copy(update={"latency_ms": (perf_counter() - started) * 1000})
        self._complete_model_io(
            payload,
            parsed_sql=proposal.sql,
            parsed_result_shape=None,
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

    def _begin_model_io(
        self,
        operation: str,
        question: str,
        schema_context: str,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
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
        raw_content: str | None,
        latency_ms: float | None,
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
        self._last_model_io = self._last_model_io.model_copy(
            update={
                "response_model": payload.get("model") if isinstance(payload, dict) else None,
                "raw_assistant_content": raw_content,
                "parsed_sql": parsed_sql,
                "parsed_result_shape": parsed_result_shape,
                "parsed_operation_plan": parsed_operation_plan,
                "parsed_window_ir": parsed_window_ir,
                "parsed_metric_grounding": parsed_metric_grounding,
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


class StaticLLMProvider:
    """Deterministic provider useful for tests and explicit local evaluation fixtures."""

    def __init__(self, sql: str, model: str = "static-test") -> None:
        self.proposal = SqlProposal(sql=sql, provider="static", model=model)

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
