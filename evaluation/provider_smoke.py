"""Make one minimal provider transport/structured-output smoke request."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    SqlProposal,
)
from app.models.domain import QueryRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one DecisionSQL provider smoke request")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/m25/provider-smoke-20260903"),
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit(
            "OPENAI_API_KEY must be mapped to DECISION_SQL_LLM_API_KEY; "
            "no provider call was made."
        )

    result = asyncio.run(_smoke(settings))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "metadata.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["provider_calls_failed"]:
        raise SystemExit(1)


async def _smoke(settings: Any) -> dict[str, Any]:
    provider = OpenAICompatibleProvider(settings)
    started = datetime.now(UTC)
    try:
        proposal = await provider.propose_sql(
            QueryRequest(question="Return a harmless constant query."),
            None,
            "Use no database objects. Return SELECT 1.",
        )
    except LLMProviderError as error:
        return {
            "timestamp": started.isoformat(),
            "provider": provider.provider_name,
            "endpoint": f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
            "structured_output": True,
            "provider_calls_attempted": 1,
            "provider_calls_succeeded": 0,
            "provider_calls_failed": 1,
            "error": error.detail.model_dump(mode="json") if error.detail else None,
        }
    if not isinstance(proposal, SqlProposal):
        raise TypeError("Smoke provider returned an invalid SqlProposal")
    return {
        "timestamp": started.isoformat(),
        "provider": proposal.provider,
        "endpoint": f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        "model": proposal.model,
        "reasoning_effort": settings.llm_reasoning_effort,
        "structured_output": True,
        "structured_output_parsed": True,
        "provider_calls_attempted": 1,
        "provider_calls_succeeded": 1,
        "provider_calls_failed": 0,
        "prompt_tokens": proposal.prompt_tokens,
        "completion_tokens": proposal.completion_tokens,
        "latency_ms": proposal.latency_ms,
        "request_id": None,
    }


if __name__ == "__main__":
    main()
