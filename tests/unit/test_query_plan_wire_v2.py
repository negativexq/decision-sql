import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.generation.provider import (
    OpenAICompatibleProvider,
    QueryPlanProviderBoundaryError,
    QueryPlanProviderFailureStage,
)
from app.semantics.query_plan_v1 import QueryPlanV1
from app.semantics.query_plan_wire_v2 import (
    QueryPlanWireV2,
    query_plan_wire_v2_schema,
    query_plan_wire_v2_schema_hash,
    wire_to_query_plan_v1,
)


def _wire(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "applicable": True,
        "source": "products",
        "joins": [],
        "projection": ["products.id"],
        "filters": [],
    }
    value.update(updates)
    return value


def test_wire_schema_is_explicit_and_versionable() -> None:
    schema = query_plan_wire_v2_schema()
    assert schema["required"] == ["applicable", "source", "joins", "projection", "filters"]
    assert query_plan_wire_v2_schema_hash()
    assert "sql" not in schema["properties"]


def test_wire_is_strict_and_canonicalizes_without_repair() -> None:
    wire = QueryPlanWireV2.model_validate(_wire())
    plan = wire_to_query_plan_v1(wire)
    assert isinstance(plan, QueryPlanV1)
    assert plan.source == "products"
    with pytest.raises(ValidationError):
        QueryPlanWireV2.model_validate(_wire(raw_sql="SELECT 1"))


def test_null_predicates_have_no_value_slot() -> None:
    with pytest.raises(ValidationError):
        QueryPlanWireV2.model_validate(
            _wire(
                filters=[
                    {
                        "column_id": "products.id",
                        "operator": "IS_NULL",
                        "value": {"kind": "integer", "value": 1},
                    }
                ]
            )
        )


class FakeProvider(OpenAICompatibleProvider):
    def __init__(self, content: str | None) -> None:
        super().__init__(
            Settings(
                _env_file=None,
                llm_api_key="unit-test-key",
                llm_model="gpt-5.6-luna",
                eval_capture_model_io=True,
            )
        )
        self.content = content

    async def _post(self, body: dict[str, object]) -> object:
        del body
        return {
            "id": "response-1",
            "model": "gpt-5.6-luna",
            "choices": [{"message": {"content": self.content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }


@pytest.mark.asyncio
async def test_failure_capture_preserves_wire_stage_and_bounded_response() -> None:
    provider = FakeProvider(json.dumps({"applicable": True, "source": "products"}))
    with pytest.raises(QueryPlanProviderBoundaryError) as raised:
        await provider.propose_query_plan_wire_v2("question", "context")
    assert (
        raised.value.diagnostic.stage
        is QueryPlanProviderFailureStage.WIRE_SCHEMA_VALIDATION_FAILURE
    )
    assert raised.value.diagnostic.validation_errors
    assert raised.value.diagnostic.response_id == "response-1"
    capture = provider.consume_model_io()
    assert capture is not None
    assert capture.failure_stage == "WIRE_SCHEMA_VALIDATION_FAILURE"
    assert capture.raw_assistant_content_sha256
    assert capture.request_id is None


@pytest.mark.asyncio
async def test_invalid_json_capture_is_bounded_and_stage_specific() -> None:
    provider = FakeProvider("{" + ("x" * 5000))
    with pytest.raises(QueryPlanProviderBoundaryError) as raised:
        await provider.propose_query_plan_wire_v2("question", "context")
    assert raised.value.diagnostic.stage is QueryPlanProviderFailureStage.JSON_PARSE_FAILURE
    assert raised.value.diagnostic.response_truncated is True
    assert len(raised.value.diagnostic.response_preview or "") == 4096
    assert raised.value.diagnostic.response_sha256


@pytest.mark.asyncio
async def test_success_capture_contains_wire_and_canonical_hash_context() -> None:
    provider = FakeProvider(json.dumps(_wire()))
    proposal = await provider.propose_query_plan_wire_v2("question", "context")
    assert proposal.plan.content_hash
    capture = provider.consume_model_io()
    assert capture is not None
    assert capture.failure_stage is None
    assert capture.parsed_operation_plan is not None
    assert capture.raw_assistant_content_sha256
    assert capture.provider_response_id == "response-1"
