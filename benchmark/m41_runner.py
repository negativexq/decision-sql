"""M41 clean v0.2.1 single-call Luna baseline.

The provider adapter and submission parser are reused from the proven M39
implementation.  M41 has its own contract, ledger, result directory, and
offline gates, so historical M39 evidence cannot be overwritten.
"""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import base64
import json
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m39_runner as m39
from benchmark.m40_audit import run_audit
from benchmark.model_contract import (
    context_hash,
    file_hash,
    frozen_benchmark_content_hash,
    governance_instructions,
    request_leakage,
    sha256_bytes,
    sha256_text,
    submission_schema,
)

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "experiments" / "m41_v021_luna_none.json"
M40_MANIFEST = ROOT / "manifests" / "m40_repaired_benchmark_manifest.json"
M40_SPLIT = ROOT / "splits" / "m40_dev.json"
CONTRACT_PATH = ROOT / "manifests" / "m41_contract.json"
LEDGER_PATH = ROOT / "manifests" / "m41_request_ledger.json"
RESULT_ROOT = ROOT / "experiments" / "results" / "m41"
EXPECTED_HASH = "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
EXPECTED_ORDER_HASH = ""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _load_config() -> dict[str, Any]:
    config = cast(dict[str, Any], json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    expected = {
        "model": "gpt-5.6-luna",
        "provider": "openai-compatible",
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": 90,
        "calls_per_case": 1,
        "transport_retries": 0,
        "semantic_retries": 0,
        "repair": False,
        "selector": False,
        "judge": False,
        "reflection": False,
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise RuntimeError(f"M41_CONFIG_MISMATCH:{key}")
    return config


def _rows(case_ids: list[str]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for case_id in case_ids:
        directory = (
            "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"
        )
        case = json.loads(
            (ROOT / "cases" / directory / f"{case_id}.json").read_text(encoding="utf-8")
        )
        truth = json.loads(
            (ROOT / "ground_truth" / directory / f"{case_id}.json").read_text(encoding="utf-8")
        )
        rows[case_id] = (case, truth)
    return rows


def _requests(
    case_ids: list[str], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = rows[case_id][0]
        text, context, request_bytes = m39._request_text(case)
        result.append(
            {
                "case_id": case_id,
                "database_id": case["database_id"],
                "split": "DEV" if case["database_id"] in m39.DEV_DATABASES else "CONFIRMATION",
                "request_text": text,
                "user_text": text.split("\n\nUSER:\n", 1)[1],
                "instructions": governance_instructions(),
                "question": case["question"],
                "serialized_context": context,
                "request_sha256": sha256_text(text),
                "request_bytes": request_bytes,
                "leakage": request_leakage(type("Request", (), {"request_text": text})(), case),
            }
        )
    return result


def _contract_hashes(config: dict[str, Any], requests: list[dict[str, Any]]) -> dict[str, Any]:
    provider_projection = m39._provider_projection(config)
    return {
        "benchmark_content_hash": frozen_benchmark_content_hash(),
        "governance_prompt_hash": sha256_text(governance_instructions()),
        "submission_schema_hash": file_hash(ROOT / "schemas" / "model_submission.schema.json"),
        "context_hashes": {
            db: context_hash(db) for db in sorted({r["database_id"] for r in requests})
        },
        "case_order_hash": sha256_text(
            json.dumps([r["case_id"] for r in requests], separators=(",", ":"))
        ),
        "serializer_hash": file_hash(ROOT / "model_contract.py"),
        "evaluator_hash": file_hash(ROOT / "evaluator.py"),
        "validator_hash": file_hash(ROOT / "validator.py"),
        "provider_adapter_hash": file_hash(ROOT.parent / "app" / "generation" / "provider.py"),
        "provider_config": provider_projection,
        "provider_config_hash": sha256_text(
            json.dumps(provider_projection, sort_keys=True, separators=(",", ":"))
        ),
        "experiment_config_hash": file_hash(CONFIG_PATH),
        "postgresql_version": "16.15",
    }


def freeze_m41_contract() -> dict[str, Any]:
    config = _load_config()
    manifest = json.loads(M40_MANIFEST.read_text(encoding="utf-8"))
    split = json.loads(M40_SPLIT.read_text(encoding="utf-8"))
    case_ids = list(split["case_ids"])
    rows = _rows(case_ids)
    requests = _requests(case_ids, rows)
    if (
        manifest["benchmark_version"] != config["benchmark_version"]
        or manifest["benchmark_content_hash"] != EXPECTED_HASH
        or frozen_benchmark_content_hash() != EXPECTED_HASH
    ):
        raise RuntimeError("M41_CONTRACT_MISMATCH:benchmark")
    if len(requests) != 90 or len(set(case_ids)) != 90:
        raise RuntimeError("M41_CONTRACT_MISMATCH:case_count")
    distribution = Counter(case["task_type"] for case, _truth in rows.values())
    if distribution != Counter(
        {"ANSWERABLE": 60, "AUTHORITY_BLOCKED": 15, "AMBIGUOUS": 9, "POLICY_BLOCKED": 6}
    ):
        raise RuntimeError("M41_CONTRACT_MISMATCH:distribution")
    for request in requests:
        if (
            request["leakage"]
            or f"Case ID:\n{request['case_id']}\n" not in request["request_text"]
            or request["question"] not in request["request_text"]
        ):
            raise RuntimeError(f"M41_CONTRACT_MISMATCH:request:{request['case_id']}")
    quality = run_audit()
    if not quality["passed"]:
        raise RuntimeError("M41_CONTRACT_MISMATCH:M40_quality_gate")
    m39._assert_provider_schema()
    hashes = _contract_hashes(config, requests)
    frozen_requests = []
    for index, request in enumerate(requests, 1):
        frozen_requests.append(
            {
                "case_index": index,
                "case_id": request["case_id"],
                "database_id": request["database_id"],
                "split": request["split"],
                "question_sha256": sha256_text(request["question"]),
                "context_sha256": context_hash(request["database_id"]),
                "instruction_sha256": hashes["governance_prompt_hash"],
                "submission_schema_sha256": hashes["submission_schema_hash"],
                "full_request_sha256": request["request_sha256"],
                "request_bytes": request["request_bytes"],
                "request_text": request["request_text"],
            }
        )
    ledger = {
        "experiment_id": config["experiment_id"],
        "benchmark_version": config["benchmark_version"],
        "provider_calls": 0,
        "case_order": case_ids,
        "case_order_sha256": hashes["case_order_hash"],
        "requests": frozen_requests,
    }
    contract = {
        "experiment_id": config["experiment_id"],
        "benchmark_version": config["benchmark_version"],
        "benchmark_content_hash": EXPECTED_HASH,
        "contract_source_commit": m39._git_revision(),
        "model_config": {
            key: config[key]
            for key in (
                "model",
                "provider",
                "reasoning",
                "temperature",
                "timeout_seconds",
                "calls_per_case",
                "transport_retries",
                "semantic_retries",
                "repair",
                "selector",
                "judge",
                "reflection",
            )
        },
        "hashes": hashes,
        "quality_gates": {"m40_passed": True, "provider_calls": 0},
        "request_count": 90,
        "request_hashes_frozen": True,
        "provider_calls": 0,
        "historical_parent": "M39 / 0.2.0-dev",
    }
    _dump(CONTRACT_PATH, contract)
    _dump(LEDGER_PATH, ledger)
    return {"contract": contract, "ledger": ledger}


def _observable_reason(evaluated: dict[str, Any]) -> str | None:
    base = evaluated.get("base_execution") or {}
    if base.get("status") == "FAIL":
        return str(base.get("reason"))
    for fixture in evaluated.get("fixture_execution", []):
        if fixture.get("status") == "FAIL":
            return str(fixture.get("reason"))
    return None


def _summary(
    rows: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    config: dict[str, Any],
    hashes: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    failures = Counter(row["official_category"] for row in rows)
    for category in m39.FAILURE_CATEGORIES:
        failures.setdefault(category, 0)
    answerable = [row for row in rows if row["gold_behavior"] == "ANSWERABLE"]

    def correct(group: list[dict[str, Any]]) -> int:
        return sum(bool(row["official_correct"]) for row in group)

    def score(group: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "correct": correct(group),
            "total": len(group),
            "rate": f"{correct(group) / len(group):.1%}" if group else "UNAVAILABLE",
        }

    splits = {}
    for split in ("DEV", "CONFIRMATION"):
        group = [row for row in rows if row["split"] == split]
        splits[split] = {
            "governed_success": {**score(group)},
            "answerable": score([r for r in group if r["gold_behavior"] == "ANSWERABLE"]),
            "authority": score([r for r in group if r["gold_behavior"] == "AUTHORITY_BLOCKED"]),
            "ambiguity": score([r for r in group if r["gold_behavior"] == "AMBIGUOUS"]),
            "policy": score([r for r in group if r["gold_behavior"] == "POLICY_BLOCKED"]),
        }
    latency = [float(c["latency_ms"]) for c in calls if c["transport_status"] == "SUCCESS"]
    usage = [c["usage"] for c in calls if c["transport_status"] == "SUCCESS"]
    base_only = [
        r["case_id"]
        for r in answerable
        if r.get("base_passed") is True and not r.get("all_fixtures_passed")
    ]
    admitted = [
        r
        for r in answerable
        if r.get("model_decision") == "ANSWER" and r.get("sql_admission") == "PASS"
    ]
    domains = {}
    for db in sorted({r["database_id"] for r in rows}):
        group = [r for r in rows if r["database_id"] == db]
        domains[db] = {
            "governed": score(group),
            "answerable": score([r for r in group if r["gold_behavior"] == "ANSWERABLE"]),
            "authority": score([r for r in group if r["gold_behavior"] == "AUTHORITY_BLOCKED"]),
            "ambiguity": score([r for r in group if r["gold_behavior"] == "AMBIGUOUS"]),
            "policy": score([r for r in group if r["gold_behavior"] == "POLICY_BLOCKED"]),
            "wrong_refusal": sum(
                r.get("model_decision") != "ANSWER"
                for r in group
                if r["gold_behavior"] == "ANSWERABLE"
            ),
        }
    token_summary = {
        key: {
            "available": sum(v.get(key) is not None for v in usage),
            "total": sum(v[key] for v in usage if v.get(key) is not None),
            "median": statistics.median([v[key] for v in usage if v.get(key) is not None])
            if any(v.get(key) is not None for v in usage)
            else None,
        }
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")
    }
    return {
        "experiment_id": config["experiment_id"],
        "benchmark_version": config["benchmark_version"],
        "failure_counts": dict(failures),
        "scores": {
            "governed_task_success": score(rows),
            "answerable_accuracy": score(answerable),
            "authority_accuracy": score(
                [r for r in rows if r["gold_behavior"] == "AUTHORITY_BLOCKED"]
            ),
            "ambiguity_accuracy": score([r for r in rows if r["gold_behavior"] == "AMBIGUOUS"]),
            "policy_accuracy": score([r for r in rows if r["gold_behavior"] == "POLICY_BLOCKED"]),
        },
        "split_summary": splits,
        "domain_breakdown": domains,
        "decision_distribution": dict(Counter(r.get("model_decision") or "invalid" for r in rows)),
        "unauthorized_answer_rate": {
            "answered": sum(
                r.get("model_decision") == "ANSWER"
                for r in rows
                if r["gold_behavior"] == "AUTHORITY_BLOCKED"
            ),
            "total": 15,
        },
        "wrong_refusal_rate": {
            "wrong_refusals": sum(r.get("model_decision") != "ANSWER" for r in answerable),
            "total": 60,
        },
        "answerable_decision_rate": {
            "answered": sum(r.get("model_decision") == "ANSWER" for r in answerable),
            "total": 60,
        },
        "conditional_sql_correctness": {
            "correct": sum(bool(r.get("official_correct")) for r in admitted),
            "total": len(admitted),
        },
        "sql_funnel": {
            "answer_selected": sum(r.get("model_decision") == "ANSWER" for r in answerable),
            "submission_valid": sum(
                r.get("submission_invariant_status") == "PASS" for r in answerable
            ),
            "sql_admission": len(admitted),
            "base_match": sum(bool(r.get("base_passed")) for r in answerable),
            "full_suite_match": correct(answerable),
        },
        "base_only_answerable_accuracy": {
            "correct": sum(r.get("base_passed") is True for r in answerable),
            "total": 60,
        },
        "full_suite_answerable_accuracy": {"correct": correct(answerable), "total": 60},
        "base_only_false_positive_cases": base_only,
        "projection_diagnostics": dict(
            Counter(
                r.get("diagnostics", {}).get("observable_mismatch")
                for r in answerable
                if r.get("diagnostics", {}).get("observable_mismatch")
            )
        ),
        "latency": {
            "successful_responses": len(latency),
            "median_ms": statistics.median(latency) if latency else None,
            "p90_ms": sorted(latency)[max(0, int(len(latency) * 0.9) - 1)] if latency else None,
            "min_ms": min(latency) if latency else None,
            "max_ms": max(latency) if latency else None,
        },
        "token_usage": token_summary,
        "provider_calls_attempted": len(calls),
        "provider_responses_received": sum(c["transport_status"] == "SUCCESS" for c in calls),
        "provider_schema_accepted_calls": sum(c["transport_status"] == "SUCCESS" for c in calls),
        "schema_failures": failures["PROVIDER_SCHEMA_FAILURE"],
        "transport_failures": failures["TRANSPORT_FAILURE"],
        "first_model_response_acquired": any(c["transport_status"] == "SUCCESS" for c in calls),
        "raw_outputs_preserved": True,
        "all_cases_deterministically_classified": len(rows) == 90
        and all(r["official_category"] in m39.FAILURE_CATEGORIES for r in rows),
        "quality_gates": quality,
        "hashes": hashes,
    }


def run_m41() -> dict[str, Any]:
    config = _load_config()
    if not CONTRACT_PATH.exists() or not LEDGER_PATH.exists():
        raise RuntimeError("M41_CONTRACT_MISMATCH:missing_pre_call_freeze")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    if contract.get("provider_calls") != 0 or ledger.get("provider_calls") != 0:
        raise RuntimeError("M41_CONTRACT_MISMATCH:provider_calls_before_run")
    if (
        contract.get("benchmark_content_hash") != EXPECTED_HASH
        or frozen_benchmark_content_hash() != EXPECTED_HASH
    ):
        raise RuntimeError("M41_CONTRACT_MISMATCH:benchmark_hash")
    case_ids = list(ledger["case_order"])
    rows_by_id = _rows(case_ids)
    requests = _requests(case_ids, rows_by_id)
    hashes = _contract_hashes(config, requests)
    if hashes["case_order_hash"] != ledger["case_order_sha256"] or any(
        r["request_sha256"] != e["full_request_sha256"]
        for r, e in zip(requests, ledger["requests"], strict=True)
    ):
        raise RuntimeError("M41_CONTRACT_DRIFT:request_ledger")
    quality = run_audit()
    if not quality["passed"]:
        raise RuntimeError("M41_CONTRACT_MISMATCH:quality_gate")
    expected_results = quality["expected_results"]
    m39._assert_provider_schema()
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    _dump(RESULT_ROOT / "m41_request_ledger.json", ledger)
    for name in (
        "m41_raw_responses.jsonl",
        "m41_parsed_submissions.jsonl",
        "m41_case_results.json",
        "m41_summary.json",
        "m41_summary.md",
        "m41_manifest.json",
    ):
        if (RESULT_ROOT / name).exists():
            raise RuntimeError(f"M41_ARTIFACT_EXISTS:{name}")
    if not get_settings().llm_api_key:
        raise RuntimeError("M41_PROVIDER_BLOCKED:DECISION_SQL_LLM_API_KEY is not configured")
    settings = get_settings().model_copy(
        update={
            "llm_model": config["model"],
            "llm_reasoning_effort": config["reasoning"],
            "llm_temperature": config["temperature"],
            "llm_timeout_seconds": config["timeout_seconds"],
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(settings)
    run_start = _now()
    calls: list[dict[str, Any]] = []
    case_results: list[dict[str, Any]] = []
    successful = 0
    first_response = False
    for index, request in enumerate(requests):
        frozen = ledger["requests"][index]
        if sha256_text(request["request_text"]) != frozen["full_request_sha256"]:
            raise RuntimeError(f"M41_CONTRACT_DRIFT:request:{request['case_id']}")
        started = _now()
        begin = time.perf_counter()
        provider.consume_response_wire()
        payload: Any = None
        error: Exception | None = None
        try:
            payload = asyncio.run(
                provider.complete_json_schema(
                    operation="m41_governed_submission",
                    system_prompt=request["instructions"],
                    user_prompt=request["user_text"],
                    schema_name="decision_sql_m41_submission",
                    schema=submission_schema(),
                )
            )
            successful += 1
            first_response = True
        except Exception as exc:
            error = exc
        finished = _now()
        latency = (time.perf_counter() - begin) * 1000
        capture = provider.consume_model_io()
        wire = provider.consume_response_wire()
        response_hash = sha256_bytes(wire) if wire is not None else None
        metadata = m39._provider_metadata(payload or {}, capture)
        call = {
            "case_id": request["case_id"],
            "case_index": index + 1,
            "database_id": request["database_id"],
            "split": request["split"],
            "request_sha256": request["request_sha256"],
            "request_bytes": request["request_bytes"],
            "request_text": request["request_text"],
            "provider": config["provider"],
            "model": config["model"],
            "reasoning": config["reasoning"],
            "temperature": config["temperature"],
            "timeout_seconds": config["timeout_seconds"],
            "started_at": started,
            "finished_at": finished,
            "latency_ms": latency,
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
        calls.append(call)
        m39._append_jsonl(
            RESULT_ROOT / "m41_raw_responses.jsonl",
            {
                "case_id": request["case_id"],
                "case_index": index + 1,
                "database_id": request["database_id"],
                "split": request["split"],
                "request_sha256": request["request_sha256"],
                "request_bytes": request["request_bytes"],
                "request_text": request["request_text"],
                "response_sha256": response_hash,
                "raw_response_bytes_base64": base64.b64encode(wire).decode("ascii")
                if wire is not None
                else None,
                "provider_metadata": metadata,
                "provider_error": call["provider_error"],
            },
        )
        case, truth = rows_by_id[request["case_id"]]
        row: dict[str, Any] = {
            "case_index": index + 1,
            "case_id": request["case_id"],
            "database_id": request["database_id"],
            "split": request["split"],
            "gold_behavior": case["task_type"],
            "expected_decision": m39.EXPECTED_DECISION[case["task_type"]],
            "provider_status": call["transport_status"],
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
            "response_sha256": response_hash,
            "latency_ms": latency,
            "usage": metadata["usage"],
            "diagnostics": {},
        }
        parsed_value = None
        if error is not None:
            category, global_failure = m39._classify_provider_error(error)
            row.update(
                {
                    "schema_validation": category,
                    "official_category": category,
                    "diagnostics": {"provider_error": str(error)[:240]},
                }
            )
            m39._append_jsonl(
                RESULT_ROOT / "m41_parsed_submissions.jsonl",
                {
                    "case_id": request["case_id"],
                    "case_index": index + 1,
                    "request_sha256": request["request_sha256"],
                    "response_sha256": response_hash,
                    "parsed_submission": None,
                    "schema_validation": category,
                    "case_id_matches": None,
                    "provider_metadata": metadata,
                },
            )
            case_results.append(row)
            if global_failure and not first_response:
                _dump(
                    RESULT_ROOT / "m41_abort.json",
                    {
                        "status": "M41_ABORTED_CONTRACT_DEFECT"
                        if category == "PROVIDER_SCHEMA_FAILURE"
                        else "M41_PROVIDER_BLOCKED",
                        "provider_calls_attempted": len(calls),
                        "responses_received": successful,
                        "error": str(error),
                    },
                )
                raise RuntimeError(f"M41_ABORTED:{type(error).__name__}:{str(error)[:240]}")
            continue
        content = getattr(capture, "raw_assistant_content_full", None)
        submission, parse_status, parse_detail, parsed_value = m39._parse(
            content, request["case_id"]
        )
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
            row["model_decision"] = submission.decision
            row["sql_present"] = submission.sql is not None
            if evaluated.get("base_execution"):
                row["base_status"] = "PASS" if evaluated["base_passed"] else "FAIL"
            if evaluated.get("all_fixtures_passed") is not None:
                row["fixture_status"] = "PASS" if evaluated["all_fixtures_passed"] else "FAIL"
            observable = _observable_reason(evaluated)
            if observable:
                row.setdefault("diagnostics", {})["observable_mismatch"] = observable
        m39._append_jsonl(
            RESULT_ROOT / "m41_parsed_submissions.jsonl",
            {
                "case_id": request["case_id"],
                "case_index": index + 1,
                "request_sha256": request["request_sha256"],
                "response_sha256": response_hash,
                "parsed_submission": parsed_value,
                "schema_validation": row["schema_validation"],
                "case_id_matches": row["case_id_matches"],
                "provider_metadata": metadata,
            },
        )
        case_results.append(row)
    if len(case_results) != 90 or any(
        row["official_category"] not in m39.FAILURE_CATEGORIES for row in case_results
    ):
        raise RuntimeError("M41_ABORTED_CONTRACT_DEFECT:unclassified_cases")
    summary = _summary(
        case_results,
        calls,
        config,
        hashes,
        {
            "m40": True,
            "reference_fixture_gate": quality["reference_validation"],
            "mutation_gate": quality["mutation_validation"],
        },
    )
    run_end = _now()
    manifest = {
        "experiment_id": config["experiment_id"],
        "experiment_name": config["experiment_name"],
        "benchmark_version": config["benchmark_version"],
        "benchmark_content_hash": EXPECTED_HASH,
        "contract_source_commit": contract["contract_source_commit"],
        "execution_commit": m39._git_revision(),
        **{
            key: config[key]
            for key in (
                "model",
                "provider",
                "reasoning",
                "temperature",
                "timeout_seconds",
                "calls_per_case",
                "transport_retries",
                "semantic_retries",
                "repair",
                "selector",
                "judge",
                "reflection",
            )
        },
        "database_count": 6,
        "case_count": 90,
        "task_distribution": dict(
            Counter(case["task_type"] for case, _truth in rows_by_id.values())
        ),
        "dev_databases": sorted(m39.DEV_DATABASES),
        "confirmation_databases": sorted(m39.CONFIRMATION_DATABASES),
        "hashes": hashes,
        "run_start": run_start,
        "run_end": run_end,
        "provider_calls_attempted": len(calls),
        "provider_responses_received": successful,
        "provider_schema_accepted_calls": successful,
        "raw_response_artifact": "benchmark/experiments/results/m41/m41_raw_responses.jsonl",
        "first_model_response_acquired": first_response,
        "reference_gate": quality["reference_validation"],
        "mutation_gate": quality["mutation_validation"],
    }
    _dump(RESULT_ROOT / "m41_case_results.json", case_results)
    _dump(RESULT_ROOT / "m41_summary.json", summary)
    _dump(RESULT_ROOT / "m41_manifest.json", manifest)
    (RESULT_ROOT / "m41_summary.md").write_text(
        _markdown(summary, case_results, config), encoding="utf-8"
    )
    return {"status": "M41_COMPLETE", "manifest": manifest, "summary": summary}


def _markdown(summary: dict[str, Any], rows: list[dict[str, Any]], config: dict[str, Any]) -> str:
    scores = summary["scores"]
    lines = [
        "# Decision-SQL Bench v0.2.1-dev",
        "",
        "## M41 — Luna / reasoning-none / single-call baseline",
        "",
        f"Model: `{config['model']}`; provider: `{config['provider']}`; reasoning: `{config['reasoning']}`; temperature: `{config['temperature']}`; calls/case: `{config['calls_per_case']}`",
        "",
        "| Metric | Correct / Total | Rate |",
        "|---|---:|---:|",
    ]
    for key, label in (
        ("governed_task_success", "Governed Task Success"),
        ("answerable_accuracy", "Answerable Test-Suite Accuracy"),
        ("authority_accuracy", "Authority-Blocked Accuracy"),
        ("ambiguity_accuracy", "Ambiguity Accuracy"),
        ("policy_accuracy", "Policy-Blocked Accuracy"),
    ):
        value = scores[key]
        lines.append(f"| {label} | {value['correct']} / {value['total']} | {value['rate']} |")
    lines += [
        "",
        "## DEV vs CONFIRMATION",
        "",
        "| Metric | DEV | CONFIRMATION | OVERALL |",
        "|---|---:|---:|---:|",
    ]
    for key, label, overall_key in (
        ("governed_success", "Governed Success", "governed_task_success"),
        ("answerable", "Answerable TSA", "answerable_accuracy"),
        ("authority", "Authority", "authority_accuracy"),
        ("ambiguity", "Ambiguity", "ambiguity_accuracy"),
        ("policy", "Policy", "policy_accuracy"),
    ):
        values = [summary["split_summary"][split][key] for split in ("DEV", "CONFIRMATION")]
        overall = scores[overall_key]
        lines.append(
            f"| {label} | {values[0]['correct']}/{values[0]['total']} ({values[0]['rate']}) | {values[1]['correct']}/{values[1]['total']} ({values[1]['rate']}) | {overall['correct']}/{overall['total']} ({overall['rate']}) |"
        )
    lines += [
        "",
        "## Counterfactual contribution",
        "",
        f"Base-only answerable accuracy: {summary['base_only_answerable_accuracy']['correct']}/60",
        f"Full-suite answerable accuracy: {summary['full_suite_answerable_accuracy']['correct']}/60",
        f"Base-only false positives: {len(summary['base_only_false_positive_cases'])} ({', '.join(summary['base_only_false_positive_cases']) or 'none'})",
        "",
        "## Failures",
        "",
        "| case_id | database | split | gold | decision | category | first fixture |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if not row["official_correct"]:
            lines.append(
                f"| {row['case_id']} | {row['database_id']} | {row['split']} | {row['gold_behavior']} | {row.get('model_decision') or 'INVALID/NO_DECISION'} | {row['official_category']} | {row.get('first_failing_fixture') or '—'} |"
            )
    lines += [
        "",
        "## Reproducibility",
        "",
        f"Provider calls attempted: {summary['provider_calls_attempted']}; genuine responses: {summary['provider_responses_received']}; semantic retries: 0; repairs: 0; judges/selectors/reflection: 0",
        f"Latency: `{json.dumps(summary['latency'], sort_keys=True)}`",
        f"Token usage: `{json.dumps(summary['token_usage'], sort_keys=True)}`",
        "Raw responses are immutable evidence; no chain-of-thought was requested or stored.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(json.dumps(freeze_m41_contract(), indent=2, sort_keys=True))
