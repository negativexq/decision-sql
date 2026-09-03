import httpx
import pytest

from app.config import Settings
from app.generation.hard_query_plans import RatioPlan, TopKPlan, WindowPlan
from app.generation.intent import QueryIntent
from app.generation.provider import (
    LLMProviderError,
    MalformedProviderResponse,
    ModelIOCapture,
    OpenAICompatibleProvider,
    ProviderErrorDetail,
    SqlProposal,
    StaticLLMProvider,
    UnconfiguredLLMProvider,
    _intent_from_response,
    _operation_plan_messages,
    _proposal_from_response,
    _provider_error_detail,
    _window_ir_from_response,
)
from app.models.domain import QueryRequest, UserContext


@pytest.mark.asyncio
async def test_m0_provider_is_explicitly_unconfigured() -> None:
    provider = UnconfiguredLLMProvider()

    with pytest.raises(NotImplementedError, match="not enabled in M2"):
        await provider.propose_sql(
            QueryRequest(question="show revenue"),
            UserContext(user_id="user-1", tenant_id="tenant-1"),
            "",
        )


@pytest.mark.asyncio
async def test_static_provider_returns_structured_sql_proposal() -> None:
    proposal = await StaticLLMProvider("SELECT id FROM products").propose_sql(
        QueryRequest(question="list products"), None, "TABLE products"
    )

    assert isinstance(proposal, SqlProposal)
    assert proposal.sql == "SELECT id FROM products"


def test_openai_compatible_response_requires_structured_sql() -> None:
    proposal = _proposal_from_response(
        {
            "choices": [{"message": {"content": '{"sql":"SELECT id FROM products"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        "test-model",
    )

    assert proposal.provider == "openai-compatible"
    assert proposal.prompt_tokens == 10
    assert proposal.completion_tokens == 5

    with pytest.raises(MalformedProviderResponse):
        _proposal_from_response(
            {"choices": [{"message": {"content": "```sql\nSELECT 1\n```"}}]},
            "test-model",
        )


def test_openai_compatible_response_parses_structured_query_intent() -> None:
    proposal = _intent_from_response(
        {
            "choices": [{"message": {"content": '{"selected_tables":["products"],"limit":5}'}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        },
        "test-model",
    )

    assert isinstance(proposal.intent, QueryIntent)
    assert proposal.intent.selected_tables == ("products",)
    assert proposal.intent.limit == 5
    assert proposal.prompt_tokens == 20


def test_structured_query_intent_rejects_invalid_limit() -> None:
    with pytest.raises(MalformedProviderResponse):
        _intent_from_response(
            {"choices": [{"message": {"content": '{"limit":0}'}}]},
            "test-model",
        )


def test_narrow_operation_plan_prompts_and_models_are_category_specific() -> None:
    assert isinstance(
        TopKPlan(
            entity_outputs=("products.name",),
            measure={"semantic_label": "sales", "aggregation": "SUM"},
            group_by=("products.name",),
            order_direction="DESC",
            limit=5,
        ),
        TopKPlan,
    )
    assert isinstance(
        RatioPlan(
            numerator={"semantic_label": "n", "aggregation": "COUNT"},
            denominator={"semantic_label": "d", "aggregation": "COUNT"},
            grain="order",
        ),
        RatioPlan,
    )
    assert isinstance(
        WindowPlan(
            requested_outputs=("orders.id",),
            window_function="ROW_NUMBER",
            order_by=("orders.ordered_at",),
            order_direction="ASC",
        ),
        WindowPlan,
    )
    top_prompt = _operation_plan_messages("top_k", "top products", "TABLE products")[0]["content"]
    assert "TopKPlan" in top_prompt
    assert "complete SQL" in top_prompt


def test_window_ir_response_is_structured_and_contains_no_sql() -> None:
    proposal = _window_ir_from_response(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"source_relation":"orders","pattern":"RANKING",'
                            '"physical_outputs":["orders.id"],"computations":['
                            '{"pattern":"RANKING","function":"RANK",'
                            '"order_by":[{"column":"orders.id"}],"alias":"rank_value"}]}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        },
        "gpt-5.6-luna",
    )
    assert proposal.ir.source_relation == "orders"
    assert "sql" not in proposal.ir.model_dump()


@pytest.mark.asyncio
async def test_window_ir_provider_omits_temperature_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider(
        Settings(
            llm_api_key="test-key",
            llm_model="gpt-5.6-luna",
            llm_reasoning_effort="none",
            llm_temperature=None,
        )
    )
    captured: list[dict[str, object]] = []

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        captured.append(body)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"source_relation":"orders","pattern":"RANKING",'
                            '"physical_outputs":["orders.id"],"computations":['
                            '{"pattern":"RANKING","function":"RANK",'
                            '"order_by":[{"column":"orders.id"}],"alias":"rank_value"}]}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    proposal = await provider.propose_window_ir("rank orders", "TABLE orders")
    assert proposal.model == "gpt-5.6-luna"
    assert "temperature" not in captured[0]
    assert captured[0]["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_window_transport_can_omit_provider_native_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider(
        Settings(
            llm_api_key="test-key",
            llm_model="gpt-5.6-luna",
            llm_reasoning_effort="none",
            llm_temperature=None,
            eval_capture_model_io=True,
        )
    )
    captured: list[dict[str, object]] = []

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        captured.append(body)
        return {
            "model": "gpt-5.6-luna",
            "choices": [{"message": {"content": '{"pattern":"LAG"}'}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    result = await provider.propose_window_transport(
        "previous order amount",
        "TABLE orders",
        "Return one object",
        response_format=None,
        operation="PLAIN_JSON_TEXT",
    )
    assert result.content == '{"pattern":"LAG"}'
    assert "response_format" not in captured[0]
    capture = provider.consume_model_io()
    assert capture is not None
    assert "response_format" not in capture.request_config
    assert "Authorization" not in capture.model_dump_json()
    assert "test-key" not in capture.model_dump_json()


@pytest.mark.asyncio
async def test_openai_compatible_provider_requires_explicit_api_key() -> None:
    from app.generation.provider import ProviderConfigurationError

    provider = OpenAICompatibleProvider(Settings(llm_api_key=None))

    with pytest.raises(ProviderConfigurationError):
        await provider.propose_sql(QueryRequest(question="list products"), None, "TABLE products")


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

    detail = _provider_error_detail(response, "gpt-5.6-luna", secret)

    assert isinstance(detail, ProviderErrorDetail)
    assert detail.status_code == status_code
    assert detail.retryable is retryable
    assert detail.request_id == "req_smoke_test"
    assert secret not in detail.model_dump_json()
    assert "Authorization: Bearer " not in detail.message
    assert "[REDACTED]" in detail.message


def test_provider_error_preserves_typed_error_without_raw_http_exception() -> None:
    detail = ProviderErrorDetail(
        status_code=401,
        error_type="authentication_error",
        error_code="invalid_api_key",
        message="Authentication failed.",
        model="gpt-5.6-luna",
    )
    error = LLMProviderError("provider request failed", detail)

    assert isinstance(error, LLMProviderError)
    assert error.detail == detail
    assert "Authorization" not in str(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ("none", "low"))
async def test_reasoning_effort_is_forwarded_without_changing_generation_contract(
    effort: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        llm_api_key="test-key",
        llm_model="gpt-5.6-luna",
        llm_reasoning_effort=effort,
    )
    provider = OpenAICompatibleProvider(settings)
    captured: list[dict[str, object]] = []

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        captured.append(body)
        return {
            "choices": [{"message": {"content": '{"sql":"SELECT 1"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    proposal = await provider.propose_sql(QueryRequest(question="constant"), None, "TABLE t")

    assert proposal.model == "gpt-5.6-luna"
    assert captured[0]["model"] == "gpt-5.6-luna"
    assert captured[0]["reasoning_effort"] == effort
    assert captured[0]["temperature"] == 0
    assert captured[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_reasoning_arms_send_identical_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, object]] = []

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        bodies.append(body)
        return {
            "choices": [{"message": {"content": '{"sql":"SELECT 1"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    for effort in ("none", "low"):
        provider = OpenAICompatibleProvider(
            Settings(
                llm_api_key="test-key",
                llm_model="gpt-5.6-luna",
                llm_reasoning_effort=effort,
            )
        )
        monkeypatch.setattr(provider, "_post", fake_post)
        await provider.propose_sql(QueryRequest(question="constant"), None, "TABLE t")

    assert bodies[0]["messages"] == bodies[1]["messages"]
    assert bodies[0]["model"] == bodies[1]["model"] == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_unset_temperature_is_omitted_from_provider_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider(
        Settings(
            llm_api_key="test-key",
            llm_model="gpt-5.6-luna",
            llm_temperature=None,
            llm_reasoning_effort="low",
        )
    )
    captured: list[dict[str, object]] = []

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        captured.append(body)
        return {
            "choices": [{"message": {"content": '{"sql":"SELECT 1"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    await provider.propose_sql(QueryRequest(question="constant"), None, "TABLE t")

    assert "temperature" not in captured[0]
    assert captured[0]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_model_io_capture_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider(Settings(llm_api_key="test-key"))

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        del body
        return {"choices": [{"message": {"content": '{"sql":"SELECT 1"}'}}]}

    monkeypatch.setattr(provider, "_post", fake_post)
    await provider.propose_sql(QueryRequest(question="constant"), None, "TABLE t")

    assert provider.consume_model_io() is None


@pytest.mark.asyncio
async def test_model_io_capture_is_bounded_and_excludes_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider(
        Settings(
            llm_api_key="secret-test-key",
            llm_model="gpt-5.6-luna",
            llm_temperature=None,
            llm_reasoning_effort="none",
            eval_capture_model_io=True,
        )
    )

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        del body
        return {
            "model": "gpt-5.6-luna",
            "system_fingerprint": "fp_test",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"sql":"SELECT 1"}'},
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 3,
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    await provider.propose_sql(QueryRequest(question="constant"), None, "TABLE t")
    capture = provider.consume_model_io()

    assert isinstance(capture, ModelIOCapture)
    assert capture.request_config["model"] == "gpt-5.6-luna"
    assert capture.request_config["temperature"] is None
    assert capture.raw_assistant_content == '{"sql":"SELECT 1"}'
    assert capture.parsed_sql == "SELECT 1"
    assert capture.usage["reasoning_tokens"] == 1
    serialized = capture.model_dump_json()
    assert "secret-test-key" not in serialized
    assert "Authorization" not in serialized
