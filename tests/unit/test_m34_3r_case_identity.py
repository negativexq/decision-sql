from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark.m35_runner import _parse_submission
from benchmark.model_contract import build_benchmark_request


def _submission(case_id: str) -> str:
    return json.dumps(
        {
            "case_id": case_id,
            "decision": "BLOCKED_AUTHORITY",
            "sql": None,
            "reason_code": "MISSING_AUTHORIZED_RELATIONSHIP",
        }
    )


def test_request_contains_exact_case_id_and_question() -> None:
    request = build_benchmark_request("commerce_01")
    assert "USER:\nCase ID:\ncommerce_01\n\nQuestion:\n" in request.request_text
    assert request.question in request.request_text
    assert request.request_text.endswith(request.serialized_context)


def test_correct_identity_is_valid() -> None:
    submission, status, detail, _value = _parse_submission(
        _submission("commerce_01"), "commerce_01"
    )
    assert submission is not None
    assert status == "PASS"
    assert detail is None


@pytest.mark.parametrize("case_id", ["fleet_01", "abc"])
def test_wrong_or_invented_identity_is_invalid_submission(case_id: str) -> None:
    submission, status, detail, _value = _parse_submission(_submission(case_id), "commerce_01")
    assert submission is not None
    assert status == "INVALID_SUBMISSION"
    assert detail == "CASE_ID_MISMATCH"


def test_missing_or_invalid_schema_is_provider_schema_failure() -> None:
    missing, status, detail, _value = _parse_submission('{"case_id":"commerce_01"}', "commerce_01")
    assert missing is None
    assert status == "PROVIDER_SCHEMA_FAILURE"
    assert detail == "SUBMISSION_FIELDS"


@pytest.mark.asyncio
async def test_native_json_schema_provider_call_is_one_mocked_call() -> None:
    provider = OpenAICompatibleProvider(
        Settings(
            DECISION_SQL_LLM_API_KEY="test-key",
            DECISION_SQL_LLM_MODEL="gpt-5.6-luna",
            DECISION_SQL_LLM_TEMPERATURE=0.0,
            DECISION_SQL_LLM_REASONING_EFFORT="none",
            DECISION_SQL_LLM_EVAL_CAPTURE_MODEL_IO=True,
        )
    )
    calls: list[dict[str, object]] = []
    response = {
        "id": "response-1",
        "model": "gpt-5.6-luna",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": _submission("commerce_01")},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        calls.append(body)
        provider._last_response_wire = json.dumps(response).encode("utf-8")
        return response

    provider._post = fake_post  # type: ignore[method-assign]
    payload = await provider.complete_json_schema(
        operation="test",
        system_prompt="system",
        user_prompt="user",
        schema_name="test_schema",
        schema={"type": "object"},
    )
    assert payload == response
    assert len(calls) == 1
    assert calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "test_schema", "strict": True, "schema": {"type": "object"}},
    }
    assert provider.consume_response_wire() is not None
