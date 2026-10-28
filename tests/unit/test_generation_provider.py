import json

import httpx
import pytest

from app.config import Settings
from app.generation.provider import (
    LLMProviderError,
    MalformedProviderResponse,
    OpenAICompatibleProvider,
    ProviderConfigurationError,
    _provider_error_detail,
)

DECISION = {
    "case_id": "operator",
    "decision": "ANSWER",
    "sql": "SELECT status, SUM(total_amount) FROM orders GROUP BY status",
    "reason_code": None,
}


def _provider(
    monkeypatch: pytest.MonkeyPatch, content: str, **settings: object
) -> tuple[OpenAICompatibleProvider, list[dict[str, object]]]:
    provider = OpenAICompatibleProvider(
        Settings(llm_api_key="test-key", llm_model="gpt-6-luna", **settings)  # type: ignore[arg-type]
    )
    bodies: list[dict[str, object]] = []

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        bodies.append(body)
        return {
            "model": "gpt-6-luna",
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    return provider, bodies


@pytest.mark.asyncio
async def test_provider_requires_explicit_api_key() -> None:
    provider = OpenAICompatibleProvider(Settings(llm_api_key=None))
    with pytest.raises(ProviderConfigurationError):
        await provider.propose_decision("revenue by status", "TABLE orders")


@pytest.mark.asyncio
async def test_decision_is_requested_once_with_strict_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, bodies = _provider(monkeypatch, json.dumps(DECISION))

    proposal = await provider.propose_decision("revenue by status", "TABLE orders")

    assert len(bodies) == 1
    response_format = bodies[0]["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["json_schema"]["strict"] is True
    assert proposal.decision.decision.value == "ANSWER"
    assert proposal.decision.sql == DECISION["sql"]
    assert proposal.model == "gpt-6-luna"
    assert proposal.reasoning_tokens == 2


@pytest.mark.asyncio
async def test_unset_temperature_is_omitted_and_reasoning_effort_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, bodies = _provider(
        monkeypatch, json.dumps(DECISION), llm_temperature=None, llm_reasoning_effort="low"
    )

    await provider.propose_decision("revenue by status", "TABLE orders")

    assert "temperature" not in bodies[0]
    assert bodies[0]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_invalid_decision_payload_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = _provider(monkeypatch, '{"sql": "SELECT 1"}')
    with pytest.raises(MalformedProviderResponse):
        await provider.propose_decision("revenue by status", "TABLE orders")


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    ((400, False), (401, False), (403, False), (429, True), (500, True)),
)
def test_provider_error_detail_is_bounded_and_classified(status_code: int, retryable: bool) -> None:
    secret = "test-api-key-that-must-not-escape"
    response = httpx.Response(
        status_code,
        json={
            "error": {
                "type": "invalid_request_error",
                "code": "test_code",
                "message": f"bad request Authorization: Bearer {secret}",
            }
        },
        headers={"x-request-id": "req_smoke_test"},
    )

    detail = _provider_error_detail(response, "gpt-6-luna", secret)

    assert detail.status_code == status_code
    assert detail.retryable is retryable
    assert detail.request_id == "req_smoke_test"
    assert secret not in detail.message
    assert "[REDACTED]" in detail.message


@pytest.mark.asyncio
async def test_http_error_surfaces_typed_detail_without_raw_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}}, request=request)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = OpenAICompatibleProvider(Settings(llm_api_key="test-key"))

    with pytest.raises(LLMProviderError) as raised:
        await provider.propose_decision("revenue by status", "TABLE orders")

    assert raised.value.detail is not None
    assert raised.value.detail.status_code == 429
    assert raised.value.detail.retryable is True
