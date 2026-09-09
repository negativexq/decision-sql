"""M46B paired execution and frozen-evaluator evidence runner."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m39_runner as m39
from benchmark.m46b_contract import (
    RESULT_ROOT,
    TRUTH_HASH,
    TRUTH_VERSION,
    build_contract,
    m43_prompt,
)
from benchmark.model_contract import (
    frozen_benchmark_content_hash,
    sha256_bytes,
    sha256_text,
    submission_schema,
)

SCHEMA_NAME = "decision_sql_m46b_submission"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _append(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write((json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode())
        handle.flush()


def _artifact(arm: str, name: str) -> Path:
    return RESULT_ROOT / arm.lower() / name


def _row_base(
    request: dict[str, Any], case: dict[str, Any], usage: dict[str, Any], status: str
) -> dict[str, Any]:
    return {
        "arm": request["arm"],
        "case_index": request["case_index"],
        "case_id": request["case_id"],
        "database_id": request["database_id"],
        "split": request["split"],
        "gold_behavior": case["task_type"],
        "expected_decision": m39.EXPECTED_DECISION[case["task_type"]],
        "provider_status": status,
        "schema_validation": None,
        "case_id_matches": None,
        "submission_invariant_status": None,
        "parsed_submission": None,
        "model_decision": None,
        "sql_present": None,
        "sql_admission": None,
        "base_status": None,
        "fixture_status": None,
        "base_passed": None,
        "all_fixtures_passed": None,
        "first_failing_fixture": None,
        "official_category": None,
        "official_correct": False,
        "request_sha256": request["request_sha256"],
        "response_sha256": None,
        "latency_ms": None,
        "usage": usage,
        "diagnostics": {},
    }


def _attempt(
    provider: OpenAICompatibleProvider,
    request: dict[str, Any],
    case: dict[str, Any],
    truth: dict[str, Any],
    expected_results: dict[str, dict[str, Any]],
    first_response: bool,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    started = _now()
    begin = time.perf_counter()
    provider.consume_response_wire()
    payload: Any = None
    error: Exception | None = None
    try:
        payload = asyncio.run(
            provider.complete_json_schema(
                operation=f"m46b_{request['arm'].lower()}_submission",
                system_prompt=request["instructions"],
                user_prompt=request["user_text"],
                schema_name=SCHEMA_NAME,
                schema=submission_schema(),
            )
        )
    except Exception as exc:  # one attempt is one piece of evidence
        error = exc
    finished = _now()
    latency_ms = (time.perf_counter() - begin) * 1000
    capture = provider.consume_model_io()
    wire = provider.consume_response_wire()
    metadata = m39._provider_metadata(payload or {}, capture)
    response_hash = sha256_bytes(wire) if wire is not None else None
    call = {
        "arm": request["arm"],
        "case_id": request["case_id"],
        "case_index": request["case_index"],
        "database_id": request["database_id"],
        "split": request["split"],
        "request_sha256": request["request_sha256"],
        "request_bytes": request["request_bytes"],
        "started_at": started,
        "finished_at": finished,
        "latency_ms": latency_ms,
        "transport_status": "SUCCESS" if error is None else "FAILURE",
        "response_sha256": response_hash,
        **metadata,
        "provider_error": None
        if error is None
        else {
            "type": type(error).__name__,
            "message": str(error)[:240],
            **m39._error_detail(error),
        },
    }
    _append(
        _artifact(request["arm"], "raw_responses.jsonl"),
        {
            "arm": request["arm"],
            "case_id": request["case_id"],
            "case_index": request["case_index"],
            "database_id": request["database_id"],
            "request_sha256": request["request_sha256"],
            "request_bytes": request["request_bytes"],
            "request_text": request["request_text"],
            "response_sha256": response_hash,
            "raw_response_bytes_base64": base64.b64encode(wire).decode("ascii") if wire else None,
            "provider_metadata": metadata,
            "provider_error": call["provider_error"],
        },
    )
    row = _row_base(request, case, metadata["usage"], call["transport_status"])
    row.update({"response_sha256": response_hash, "latency_ms": latency_ms})
    if error is not None:
        category, global_failure = m39._classify_provider_error(error)
        row.update(
            {
                "schema_validation": category,
                "official_category": category,
                "diagnostics": {"provider_error": str(error)[:240]},
            }
        )
        _append(
            _artifact(request["arm"], "parsed_submissions.jsonl"),
            {
                "arm": request["arm"],
                "case_id": request["case_id"],
                "case_index": request["case_index"],
                "request_sha256": request["request_sha256"],
                "response_sha256": response_hash,
                "parsed_submission": None,
                "schema_validation": category,
                "case_id_matches": None,
                "provider_metadata": metadata,
            },
        )
        if global_failure and not first_response:
            raise RuntimeError(f"M46B_ABORTED:{type(error).__name__}:{str(error)[:240]}")
        return call, row, first_response

    first_response = True
    content = getattr(capture, "raw_assistant_content_full", None)
    submission, parse_status, parse_detail, parsed_value = m39._parse(content, request["case_id"])
    row.update(
        {
            "parsed_submission": parsed_value,
            "schema_validation": parse_status,
            "case_id_matches": None
            if submission is None
            else submission.case_id == request["case_id"],
            "submission_invariant_status": "PASS" if parse_status == "PASS" else parse_status,
            "diagnostics": {"parse_detail": parse_detail} if parse_detail else {},
        }
    )
    if submission is None:
        row["official_category"] = parse_status
    elif parse_status == "INVALID_SUBMISSION":
        row.update(
            {
                "official_category": "INVALID_SUBMISSION",
                "model_decision": submission.decision,
                "sql_present": submission.sql is not None,
            }
        )
    else:
        evaluated = m39._evaluate(case, truth, submission, expected_results)
        row.update(evaluated)
        row.update(
            {"model_decision": submission.decision, "sql_present": submission.sql is not None}
        )
        if evaluated.get("base_execution"):
            row["base_status"] = "PASS" if evaluated["base_passed"] else "FAIL"
        if evaluated.get("all_fixtures_passed") is not None:
            row["fixture_status"] = "PASS" if evaluated["all_fixtures_passed"] else "FAIL"
    _append(
        _artifact(request["arm"], "parsed_submissions.jsonl"),
        {
            "arm": request["arm"],
            "case_id": request["case_id"],
            "case_index": request["case_index"],
            "request_sha256": request["request_sha256"],
            "response_sha256": response_hash,
            "parsed_submission": parsed_value,
            "schema_validation": row["schema_validation"],
            "case_id_matches": row["case_id_matches"],
            "provider_metadata": metadata,
        },
    )
    return call, row, first_response


def run() -> dict[str, Any]:
    contract_data = build_contract()
    contract = contract_data["contract"]
    if frozen_benchmark_content_hash() != TRUTH_HASH:
        raise RuntimeError("M46B_CONTRACT_MISMATCH:truth_hash_at_run")
    if sha256_text(m43_prompt()) != contract["prompt_hash"]:
        raise RuntimeError("M46B_CONTRACT_MISMATCH:prompt_hash_at_run")
    if not get_settings().llm_api_key:
        raise RuntimeError("M46B_PROVIDER_BLOCKED:DECISION_SQL_LLM_API_KEY is not configured")
    for arm in ("CONTROL", "TREATMENT"):
        for filename in (
            "raw_responses.jsonl",
            "parsed_submissions.jsonl",
            "case_results.json",
            "calls.json",
        ):
            if _artifact(arm, filename).exists():
                raise RuntimeError(f"M46B_ARTIFACT_EXISTS:{filename}")
    settings = get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": 0.0,
            "llm_timeout_seconds": 90,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(settings)
    by_arm = {
        "CONTROL": {item["case_id"]: item for item in contract_data["control_requests"]},
        "TREATMENT": {item["case_id"]: item for item in contract_data["treatment_requests"]},
    }
    calls: dict[str, list[dict[str, Any]]] = {"CONTROL": [], "TREATMENT": []}
    results: dict[str, list[dict[str, Any]]] = {"CONTROL": [], "TREATMENT": []}
    first_response = False
    for scheduled in contract_data["schedule"]:
        arm = str(scheduled["arm"])
        request = by_arm[arm][str(scheduled["case_id"])]
        case, truth = contract_data["rows"][request["case_id"]]
        call, row, first_response = _attempt(
            provider,
            request,
            case,
            truth,
            contract_data["expected_results"],
            first_response,
        )
        calls[arm].append(call)
        results[arm].append(row)
    for arm in ("CONTROL", "TREATMENT"):
        calls[arm].sort(key=lambda item: item["case_index"])
        results[arm].sort(key=lambda item: item["case_index"])
        _dump(_artifact(arm, "calls.json"), calls[arm])
        _dump(_artifact(arm, "case_results.json"), results[arm])
        ledger = json.loads(_artifact(arm, "request_ledger.json").read_text())
        ledger["provider_calls"] = len(calls[arm])
        _dump(_artifact(arm, "request_ledger.json"), ledger)
    manifest = {
        "experiment": "M46B",
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "control_calls": len(calls["CONTROL"]),
        "treatment_calls": len(calls["TREATMENT"]),
        "total_provider_attempts": sum(len(items) for items in calls.values()),
        "genuine_responses": sum(
            sum(call["transport_status"] == "SUCCESS" for call in items) for items in calls.values()
        ),
        "first_model_response_acquired": first_response,
        "contract_source_commit": contract["starting_commit"],
        "execution_commit": m39._git_revision(),
        "provider_calls": 180,
        "hashes": contract["hashes"],
    }
    _dump(RESULT_ROOT / "m46b_manifest.json", manifest)
    return {"manifest": manifest, "rows": results, "calls": calls, "contract": contract_data}


if __name__ == "__main__":
    print(json.dumps(run()["manifest"], indent=2, sort_keys=True))
