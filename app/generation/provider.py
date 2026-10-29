import json
import re
from time import perf_counter
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from app.config import Settings, get_settings
from app.decision.exploration import Exploration
from app.generation.decision_contract import (
    ProductionDecision,
    production_decision_prompt,
    production_decision_schema,
)


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


class RuntimeRejectionFeedback(BaseModel):
    """What the runtime tells the model when it asks for one repaired decision.

    Built only from the runtime's own rejection: stage, code, and a message that
    never contains result rows or database error text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    previous: ProductionDecision
    stage: str
    code: str
    message: str


class EarlierAnswer(BaseModel):
    """The question a follow-up builds on, and the SQL the model proposed for it.

    Only the model's own proposal is sent back: the SQL that ran may carry stored
    values the runtime wrote in, which unshared columns never show the model.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    sql: str


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


class LLMProviderError(RuntimeError):
    def __init__(self, message: str, detail: ProviderErrorDetail | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ProviderConfigurationError(LLMProviderError):
    pass


class MalformedProviderResponse(LLMProviderError):
    pass


class LLMProvider(Protocol):
    async def propose_decision(
        self,
        question: str,
        schema_context: str,
        repair: RuntimeRejectionFeedback | None = None,
        earlier: EarlierAnswer | None = None,
        exploration: Exploration | None = None,
    ) -> ProductionDecisionProposal:
        """Return the canonical typed production decision in one call.

        With ``repair``, the same conversation continues with the runtime's
        rejection of the previous decision and asks for a replacement.  With
        ``earlier``, the question follows up on an earlier answer.  With
        ``exploration``, the request says whether the model may still explore and
        carries the results of the probes it ran.
        """


class OpenAICompatibleProvider:
    """Small adapter for providers implementing the OpenAI chat-completions contract."""

    provider_name = "openai-compatible"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def propose_decision(
        self,
        question: str,
        schema_context: str,
        repair: RuntimeRejectionFeedback | None = None,
        earlier: EarlierAnswer | None = None,
        exploration: Exploration | None = None,
    ) -> ProductionDecisionProposal:
        """Request one typed decision; SQL and decision always arrive in the same response."""
        if not self.settings.llm_api_key:
            raise ProviderConfigurationError("DECISION_SQL_LLM_API_KEY is not configured")
        messages = _production_decision_messages(
            question, schema_context, repair, earlier, exploration
        )
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
        started = perf_counter()
        payload = await self._post(body)
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
            raise MalformedProviderResponse(
                "Provider response did not contain a valid production decision"
            ) from error
        return proposal

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
                return response.json()
        except httpx.HTTPStatusError as error:
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


_LOOK_FIRST = {
    "NEEDS_CLARIFICATION": (
        "You returned NEEDS_CLARIFICATION without exploring. Before asking the user, "
        "check with probes whether the data or the context settles it: whether the "
        "readings give different results, what the columns involved store, and whether "
        "a column or description in the context already answers it. Return "
        "NEEDS_CLARIFICATION again only if no probe can settle the question."
    ),
    "BLOCKED_AUTHORITY": (
        "You returned BLOCKED_AUTHORITY without exploring. Before refusing, check "
        "whether the tables and relationships in the context already answer the "
        "question, and use probes to confirm it. Return BLOCKED_AUTHORITY again only "
        "if no authorized path can answer it."
    ),
}


_READINGS = (
    "You returned NEEDS_CLARIFICATION with these readings:\n{readings}\n"
    "Return EXPLORE with exactly one probe per reading, in the same order: the complete "
    "SQL that answers the question under that reading, as you would return it for "
    "ANSWER. The runtime runs every probe; when all of them return the same result, the "
    "question does not change the answer and the user is not asked. If a reading needs "
    "something the data does not hold, such as a conversion rate or a mapping of codes, "
    "do not stand in a value for it: return NEEDS_CLARIFICATION again."
)


def _production_decision_messages(
    question: str,
    schema_context: str,
    repair: RuntimeRejectionFeedback | None = None,
    earlier: EarlierAnswer | None = None,
    exploration: Exploration | None = None,
) -> list[dict[str, str]]:
    user = "Case ID:\noperator\n\nQuestion:\n" + question
    if earlier is not None:
        user += "\n\nEarlier answer:\n" + json.dumps(
            earlier.model_dump(), ensure_ascii=False, indent=2
        )
    user += "\n\nGoverned context:\n" + schema_context
    if exploration is not None:
        if exploration.results:
            results = [item.model_dump(exclude_none=True) for item in exploration.results]
            user += "\n\nExploration results:\n" + json.dumps(results, ensure_ascii=False, indent=2)
        if exploration.returned_without_looking is not None:
            user += "\n\n" + _LOOK_FIRST[exploration.returned_without_looking]
        if exploration.readings:
            listed = "\n".join(
                f"{number}. {reading}" for number, reading in enumerate(exploration.readings, 1)
            )
            user += "\n\n" + _READINGS.format(readings=listed)
        user += (
            "\n\nExploration is open."
            if exploration.open
            else "\n\nExploration is closed: return a final decision."
        )
    messages = [
        {"role": "system", "content": production_decision_prompt()},
        {"role": "user", "content": user},
    ]
    if repair is not None:
        rejection = {"stage": repair.stage, "code": repair.code, "message": repair.message}
        messages += [
            {"role": "assistant", "content": repair.previous.model_dump_json()},
            {
                "role": "user",
                "content": "Runtime feedback:\n"
                + json.dumps(rejection, ensure_ascii=False, indent=2),
            },
        ]
    return messages


def _assistant_content(payload: Any) -> str | None:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


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
