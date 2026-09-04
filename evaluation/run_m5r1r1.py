"""Bounded forensic reproduction for the two historical R1 DTO failures."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.generation.provider import (
    LLMProviderError,
    MalformedProviderResponse,
    OpenAICompatibleProvider,
)
from evaluation.m5r1_benchmark import build_benchmark
from evaluation.selective_answering import (
    R1Decision,
    local_r1_schema_hash,
    parse_r1_content,
    prompt_hash,
    provider_settings,
    r1_request_body,
    render_server_context,
    response_schema,
    response_schema_hash,
)

FAILED_CASE_IDS = ("m5r1-group-025-abstain", "m5r1-group-043-answer")
CONTROL_CASE_IDS = (
    "m5r1-group-001-answer",
    "m5r1-group-001-clarify",
    "m5r1-group-001-abstain",
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _shape(payload: Any, content: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__, "content_type": type(content).__name__}
    choices = payload.get("choices")
    first_choice = choices[0] if isinstance(choices, list) and choices else None
    message = first_choice.get("message") if isinstance(first_choice, dict) else None
    result: dict[str, Any] = {
        "payload_type": "object",
        "top_level_keys": sorted(str(key) for key in payload),
        "choice_count": len(choices) if isinstance(choices, list) else 0,
        "message_keys": sorted(str(key) for key in message) if isinstance(message, dict) else [],
        "content_type": type(content).__name__,
        "content_present": content is not None,
        "content_length": len(content) if isinstance(content, str) else None,
        "response_status": payload.get("status"),
    }
    if isinstance(message, dict):
        result["refusal_present"] = bool(message.get("refusal"))
    return result


def _validation_codes(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, str):
        return [{"stage": "RESPONSE_EXTRACTION", "code": "content_not_string"}]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        return [{"stage": "JSON_DECODE", "code": error.msg}]
    try:
        R1Decision.model_validate(parsed)
    except ValidationError as error:
        return [
            {"stage": "DTO_VALIDATION", "code": item["type"], "location": list(item["loc"])}
            for item in error.errors()
        ]
    return []


def _failure_stage(payload: Any, content: Any, codes: list[dict[str, Any]]) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
        return "UNEXPECTED_ENVELOPE"
    if content is None:
        return "EMPTY_CONTENT"
    if codes:
        return str(codes[0]["stage"])
    return "NONE"


def _root_cause(payload: Any, content: Any, codes: list[dict[str, Any]], status: str) -> str:
    if status == "TRANSPORT_FAILURE":
        return "TRANSPORT_FAILURE"
    if isinstance(payload, dict):
        choices = payload.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        finish_reason = (
            choices[0].get("finish_reason") if isinstance(choices, list) and choices else None
        )
        if isinstance(message, dict) and message.get("refusal"):
            return "PROVIDER_VALID_TERMINAL_STATE"
        if finish_reason in {"length", "content_filter"} or payload.get("status") == "incomplete":
            return "PROVIDER_VALID_TERMINAL_STATE"
    if codes or content is None:
        return "PROVIDER_STRICT_SCHEMA_VIOLATION"
    return "NONE"


async def _one(
    provider: OpenAICompatibleProvider, case_id: str, question: str, context: str, attempt: int
) -> dict[str, Any]:
    body = r1_request_body(provider, question, context)
    started = perf_counter()
    result: dict[str, Any] = {
        "case_id": case_id,
        "attempt": attempt,
        "request_schema_hash": response_schema_hash("R1"),
        "strict_requested": body["response_format"]["json_schema"]["strict"],
        "model": body["model"],
        "reasoning_effort": body.get("reasoning_effort"),
    }
    try:
        payload = await provider._post(body)
        choices = payload.get("choices") if isinstance(payload, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
        result["http_status"] = 200
        result["provider_request_id"] = provider._response_metadata.get("request_id")
        result["response_status"] = payload.get("status") if isinstance(payload, dict) else None
        result["finish_reason"] = (
            choices[0].get("finish_reason") if isinstance(choices, list) and choices else None
        )
        result["shape"] = _shape(payload, content)
        codes = _validation_codes(content)
        result["validation_errors"] = codes
        try:
            parse_r1_content(content if isinstance(content, str) else "")
            result["dto_valid"] = True
        except MalformedProviderResponse:
            result["dto_valid"] = False
        result["failure_stage"] = _failure_stage(payload, content, codes)
        result["root_cause"] = _root_cause(payload, content, codes, "VALID")
        usage = payload.get("usage") or {}
        result["input_tokens"] = usage.get("prompt_tokens")
        result["output_tokens"] = usage.get("completion_tokens")
    except LLMProviderError as error:
        detail = error.detail
        result.update(
            {
                "http_status": detail.status_code if detail else None,
                "provider_request_id": (
                    detail.request_id
                    if detail
                    else provider._response_metadata.get("request_id")
                ),
                "response_status": None,
                "finish_reason": None,
                "shape": {},
                "validation_errors": [],
                "dto_valid": False,
                "failure_stage": "TRANSPORT_FAILURE",
                "root_cause": _root_cause(None, None, [], "TRANSPORT_FAILURE"),
                "error_type": type(error).__name__,
            }
        )
    result["latency_ms"] = round((perf_counter() - started) * 1000, 3)
    return result


async def _run(selected: tuple[Any, ...], repeat_failed: bool) -> list[dict[str, Any]]:
    settings = provider_settings(get_settings())
    if not settings.llm_api_key:
        raise RuntimeError("M5R.1R.1 provider credential is not configured")
    provider = OpenAICompatibleProvider(settings)
    context = render_server_context()
    records: list[dict[str, Any]] = []
    for case in selected:
        records.append(await _one(provider, case.case_id, case.question, context, 1))
    if repeat_failed:
        for case in selected:
            if case.case_id in FAILED_CASE_IDS:
                records.append(await _one(provider, case.case_id, case.question, context, 2))
    return records


def _schema_comparison() -> dict[str, Any]:
    provider_schema = response_schema("R1")
    local_schema = R1Decision.model_json_schema()
    provider_properties = set(provider_schema["properties"])
    local_properties = set(local_schema["properties"])
    return {
        "provider_schema_hash": response_schema_hash("R1"),
        "local_dto_schema_hash": local_r1_schema_hash(),
        "same_property_names": provider_properties == local_properties,
        "provider_properties": sorted(provider_properties),
        "local_properties": sorted(local_properties),
        "provider_required": provider_schema["required"],
        "local_required": local_schema["required"],
        "provider_enum": provider_schema["properties"]["action"]["enum"],
        "local_enum": local_schema["$defs"]["SelectiveAction"]["enum"],
        "provider_additional_properties": provider_schema["additionalProperties"],
        "local_additional_properties": local_schema["additionalProperties"],
        "field_contract_compatible": (
            provider_properties == local_properties
            and provider_schema["required"] == local_schema["required"]
            and provider_schema["properties"]["action"]["enum"]
            == local_schema["$defs"]["SelectiveAction"]["enum"]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, default=Path("evaluation/results/m5r1r1"))
    args = parser.parse_args()
    cases_by_id = {case.case_id: case for case in build_benchmark()}
    selected_ids = FAILED_CASE_IDS + CONTROL_CASE_IDS
    selected = tuple(cases_by_id[case_id] for case_id in selected_ids)
    context = render_server_context()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-forensics"
    artifact_dir = args.artifact_dir / run_id
    records = asyncio.run(_run(selected, repeat_failed=True))
    _write_json(
        artifact_dir / "metadata.json",
        {
            "historical_run": "evaluation/results/m5r1r/20260903T231846Z-dev",
            "historical_failure_count": 2,
            "historical_failed_case_ids": list(FAILED_CASE_IDS),
            "selected_control_case_ids": list(CONTROL_CASE_IDS),
            "forensic_provider_calls": len(records),
            "benchmark_hash": "066d036596e54a9effe319f4cf58761a0da5b6572f20ac82cbaec7b8ae9ec767",
            "dev_hash": "301e001e1207b646d3469aab7607bdbff4db501b1c09df1fb329cecfbd6089c9",
            "holdout_hash": "087cca9818f9654c72bc0abd42c153c41152fba8ca82bec498d5944046a318ad",
            "r1_prompt_hash": prompt_hash("R1", context),
            "r1_response_schema_hash": response_schema_hash("R1"),
            "server_context_hash": sha256(context.encode()).hexdigest(),
            "local_dto_schema_hash": local_r1_schema_hash(),
            "model": "gpt-5.6-luna",
            "reasoning": "none",
            "temperature": "omitted/provider-default",
            "transport": "httpx 0.28.1; no OpenAI SDK",
            "raw_payloads_persisted": False,
            "raw_secrets_persisted": False,
        },
    )
    _write_json(
        artifact_dir / "failed_case_inventory.json",
        [{"case_id": case_id} for case_id in FAILED_CASE_IDS],
    )
    _write_json(artifact_dir / "reproduction_results.json", records)
    _write_json(artifact_dir / "local_schema_comparison.json", _schema_comparison())
    _write_json(
        artifact_dir / "request_schema_comparison.json",
        {
            "all_records_use_expected_schema_hash": all(
                record["request_schema_hash"] == response_schema_hash("R1") for record in records
            ),
            "all_records_request_strict_true": all(
                record["strict_requested"] is True for record in records
            ),
            "records": [
                {
                    "case_id": record["case_id"],
                    "attempt": record["attempt"],
                    "request_schema_hash": record["request_schema_hash"],
                    "strict_requested": record["strict_requested"],
                    "model": record["model"],
                    "reasoning_effort": record["reasoning_effort"],
                }
                for record in records
            ],
        },
    )
    failed = [record for record in records if record["case_id"] in FAILED_CASE_IDS]
    if all(record["dto_valid"] for record in failed):
        classification = "R1_INTERMITTENT_PROVIDER_PROTOCOL_FAILURE"
        confidence = "STRONGLY_SUPPORTED"
    elif all(not record["dto_valid"] for record in failed):
        classification = "R1_PROVIDER_STRICT_SCHEMA_VIOLATION_CONFIRMED"
        confidence = "STRONGLY_SUPPORTED"
    else:
        classification = "R1_ROOT_CAUSE_UNRESOLVED"
        confidence = "UNRESOLVED"
    _write_json(
        artifact_dir / "root_cause_summary.json",
        {
            "classification": classification,
            "confidence": confidence,
            "failed_reproduction_records": len(failed),
            "valid_failed_reproductions": sum(record["dto_valid"] for record in failed),
            "reproduction_is_diagnostic_not_quality": True,
        },
    )
    _write_json(
        artifact_dir / "final_decision.json",
        {
            "root_cause_classification": classification,
            "root_cause_confidence": confidence,
            "final_frozen_rerun_justified": False,
            "reason": (
                "Protocol reliability is not demonstrated; no 100-question rerun is "
                "permitted in M5R.1R.1."
            ),
        },
    )
    print(
        json.dumps(
            {"artifact_dir": str(artifact_dir), "classification": classification}, indent=2
        )
    )


if __name__ == "__main__":
    main()
