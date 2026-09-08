"""Provider-free checks plus one non-primary M29R.1 protocol smoke."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from app.generation.semantic_plan_protocol import (
    provider_schema_errors,
    provider_semantic_query_plan_schema_hash,
)
from app.semantics.semantic_query import SemanticQueryPlan

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/fixtures/m29r1_semantic_plan_preflight.json"


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def run() -> dict[str, object]:
    settings = get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_temperature": 0.0,
            "llm_reasoning_effort": "none",
            "llm_prompt_profile": "legacy",
            "llm_timeout_seconds": 90.0,
            "eval_capture_model_io": True,
        }
    )
    context = (
        "SYNTHETIC SERVER-OWNED SEMANTIC CONTEXT\n"
        "database_id: smoke\n"
        "entity_id: entity:smoke\n"
        "attribute_id: attribute:smoke.value\n"
        "No relationships are available."
    )
    provider = OpenAICompatibleProvider(settings)
    error: str | None = None
    plan_valid = False
    provider_valid = False
    try:
        proposal = await provider.propose_semantic_query_plan(
            "Return a minimal plan selecting the smoke value attribute.", context
        )
        payload = proposal.plan.model_dump(mode="json")
        provider_valid = not provider_schema_errors(payload)
        SemanticQueryPlan.model_validate(payload)
        plan_valid = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
    capture = provider.consume_model_io()
    response_format = capture.request_config.get("response_format", {}) if capture else {}
    result: dict[str, object] = {
        "classification": (
            "M29R1_PROTOCOL_PREFLIGHT_PASSED" if plan_valid else "M29R1_PROTOCOL_PREFLIGHT_FAILED"
        ),
        "provider_calls": 1,
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "reasoning_effort": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "timeout_seconds": settings.llm_timeout_seconds,
        "response_received": bool(capture and capture.raw_assistant_content_full is not None),
        "json_parse": plan_valid,
        "provider_schema_valid": provider_valid,
        "canonical_conversion_valid": plan_valid,
        "canonical_plan_valid": plan_valid,
        "response_format_type": (
            response_format.get("type") if isinstance(response_format, dict) else None
        ),
        "strict": bool(
            isinstance(response_format, dict)
            and isinstance(response_format.get("json_schema"), dict)
            and response_format["json_schema"].get("strict") is True
        ),
        "canonical_schema_hash": _hash_json(SemanticQueryPlan.model_json_schema()),
        "provider_schema_hash": provider_semantic_query_plan_schema_hash(),
        "response_hash": capture.raw_assistant_content_sha256 if capture else None,
        "response_bytes": (
            len(capture.raw_assistant_content_full.encode())
            if capture and capture.raw_assistant_content_full is not None
            else None
        ),
        "error": error,
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), sort_keys=True))
