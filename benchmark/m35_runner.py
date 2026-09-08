"""M35 — first single-call Luna baseline for the governed pilot.

The runner is deliberately benchmark-local.  The application provider adapter
only supplies one native structured-output transport call; all parsing,
identity binding, SQL admission, execution, and scoring remain here.
"""

from __future__ import annotations

# Markdown report rows intentionally keep their exact columns readable.
# ruff: noqa: E501
import asyncio
import base64
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    ProviderConfigurationError,
)
from benchmark.authoring import SCHEMA_NAMES, connection_kwargs_from_env, seed_database
from benchmark.model_contract import (
    ROOT,
    BenchmarkRequest,
    build_all_requests,
    build_benchmark_request,
    case_order_hash,
    file_hash,
    frozen_benchmark_content_hash,
    git_revision,
    sha256_bytes,
    sha256_text,
    submission_schema,
)
from benchmark.model_runner import (
    _experiment_config,
    _provider_config_projection,
    _request_audit,
)
from benchmark.models import ResultContract, Submission, compare_rows
from benchmark.safety import SqlAdmissionError, execute_query, validate_read_only_select
from benchmark.validator import load_pilot, mutation_test, validate_references

FAILURE_CATEGORIES = (
    "TRANSPORT_FAILURE",
    "PROVIDER_SCHEMA_FAILURE",
    "INVALID_SUBMISSION",
    "WRONG_GOVERNED_DECISION",
    "SQL_ADMISSION_FAILURE",
    "EXECUTION_FAILURE",
    "RESULT_MISMATCH",
    "CORRECT",
)
DECISION_COLUMNS = (
    "ANSWER",
    "BLOCKED_AUTHORITY",
    "NEEDS_CLARIFICATION",
    "BLOCKED_POLICY",
    "INVALID/NO_DECISION",
)
EXPECTED_DECISION = {
    "ANSWERABLE": "ANSWER",
    "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
    "AMBIGUOUS": "NEEDS_CLARIFICATION",
    "POLICY_BLOCKED": "BLOCKED_POLICY",
}


class M35ProviderBlocked(RuntimeError):
    """Raised when a global provider configuration failure makes continuation unsafe."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _append_jsonl_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        encoded = (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _ensure_new_artifact(path: Path) -> None:
    if path.exists():
        raise RuntimeError(f"M35_ARTIFACT_EXISTS:{path}")


def _assistant_content(payload: Any) -> str | None:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def _usage(payload: Any) -> dict[str, int | None]:
    raw = payload.get("usage") if isinstance(payload, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    values: dict[str, int | None] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(key)
        values[key] = value if isinstance(value, int) else None
    details = raw.get("completion_tokens_details")
    if isinstance(details, dict):
        value = details.get("reasoning_tokens")
        values["reasoning_tokens"] = value if isinstance(value, int) else None
    return values


def _provider_metadata(payload: Any, capture: Any) -> dict[str, Any]:
    choice = payload.get("choices", [{}])[0] if isinstance(payload, dict) else {}
    choice = choice if isinstance(choice, dict) else {}
    return {
        "request_id": getattr(capture, "request_id", None),
        "provider_response_id": (payload.get("id") if isinstance(payload, dict) else None),
        "resolved_model": payload.get("model") if isinstance(payload, dict) else None,
        "finish_reason": choice.get("finish_reason"),
        "usage": _usage(payload),
    }


def _parse_submission(
    content: str | None, expected_case_id: str
) -> tuple[Submission | None, str, str | None, dict[str, Any] | None]:
    if content is None:
        return None, "PROVIDER_SCHEMA_FAILURE", "MISSING_STRUCTURED_CONTENT", None
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None, "PROVIDER_SCHEMA_FAILURE", "INVALID_JSON", None
    if not isinstance(value, dict):
        return None, "PROVIDER_SCHEMA_FAILURE", "JSON_OBJECT_REQUIRED", None
    try:
        submission = Submission.from_dict(value)
    except ValueError as error:
        return None, "PROVIDER_SCHEMA_FAILURE", str(error), value
    if submission.case_id != expected_case_id:
        return submission, "INVALID_SUBMISSION", "CASE_ID_MISMATCH", value
    return submission, "PASS", None, value


def _gold_behavior(case: dict[str, Any]) -> str:
    return str(case["task_type"])


def _evaluate_submission(
    case: dict[str, Any],
    truth: dict[str, Any],
    submission: Submission,
    reference_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    gold_behavior = _gold_behavior(case)
    expected_decision = EXPECTED_DECISION[gold_behavior]
    result: dict[str, Any] = {
        "gold_behavior": gold_behavior,
        "expected_decision": expected_decision,
        "model_decision": submission.decision,
        "sql_present": submission.sql is not None,
        "base_execution": None,
        "fixture_execution": [],
        "first_failing_fixture": None,
        "base_passed": None,
        "all_fixtures_passed": None,
        "diagnostics": {},
    }
    if submission.decision != expected_decision:
        result.update({"official_category": "WRONG_GOVERNED_DECISION", "official_correct": False})
        return result
    if gold_behavior != "ANSWERABLE":
        if submission.sql is not None:
            result["diagnostics"] = {"non_answerable_sql_produced": True}
        result.update({"official_category": "CORRECT", "official_correct": submission.sql is None})
        if submission.sql is not None:
            result["official_category"] = "WRONG_GOVERNED_DECISION"
            result["official_correct"] = False
        return result

    if submission.sql is None:
        result.update({"official_category": "SQL_ADMISSION_FAILURE", "official_correct": False})
        result["diagnostics"] = {"admission_reason": "MISSING_SQL"}
        return result
    try:
        validate_read_only_select(submission.sql)
    except SqlAdmissionError as error:
        result.update({"official_category": "SQL_ADMISSION_FAILURE", "official_correct": False})
        result["diagnostics"] = {"admission_reason": str(error)}
        return result

    contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
    fixtures = [{"fixture_id": "base", "patch_sql": []}] + truth["counterfactual_fixtures"]
    base_passed = False
    first_failure: str | None = None
    all_passed = True
    for fixture in fixtures:
        fixture_id = str(fixture["fixture_id"])
        execution: dict[str, Any] = {"fixture_id": fixture_id, "status": None, "reason": None}
        try:
            seed_database(case["database_id"], connection_kwargs_from_env())
            columns, rows = execute_query(
                connection_kwargs_from_env(),
                SCHEMA_NAMES[case["database_id"]],
                submission.sql,
                patch_sql=fixture["patch_sql"],
            )
            expected = reference_results[case["case_id"]]["fixtures"][fixture_id]
            same, reason = compare_rows(rows, expected["rows"], contract)
            execution.update(
                {
                    "status": "PASS" if same else "FAIL",
                    "reason": reason,
                    "columns": columns,
                    "row_count": len(rows),
                }
            )
            if not same:
                all_passed = False
                first_failure = first_failure or fixture_id
        except SqlAdmissionError as error:
            execution.update({"status": "FAIL", "reason": str(error)})
            all_passed = False
            first_failure = first_failure or fixture_id
        except Exception as error:  # pragma: no cover - exercised against live PostgreSQL
            execution.update(
                {
                    "status": "ERROR",
                    "reason": f"{type(error).__name__}:{str(error)[:240]}",
                }
            )
            all_passed = False
            first_failure = first_failure or fixture_id
        if fixture_id == "base":
            base_passed = execution["status"] == "PASS"
            result["base_execution"] = execution
            result["base_passed"] = base_passed
        else:
            result["fixture_execution"].append(execution)

    result["first_failing_fixture"] = first_failure
    result["all_fixtures_passed"] = all_passed
    if not base_passed:
        result.update({"official_category": "EXECUTION_FAILURE", "official_correct": False})
        if result["base_execution"].get("status") == "FAIL":
            result["official_category"] = "RESULT_MISMATCH"
        return result
    if not all_passed:
        result.update({"official_category": "RESULT_MISMATCH", "official_correct": False})
        if first_failure and first_failure != "base":
            result["diagnostics"] = {"BASE_ONLY_FALSE_POSITIVE": True}
        return result
    result.update({"official_category": "CORRECT", "official_correct": True})
    return result


def _failure_for_provider_error(error: Exception) -> tuple[str, bool]:
    if isinstance(error, ProviderConfigurationError):
        return "TRANSPORT_FAILURE", True
    if isinstance(error, LLMProviderError):
        detail = error.detail
        if detail is not None and detail.status_code in {401, 403, 404}:
            return "TRANSPORT_FAILURE", True
        if detail is not None and detail.retryable:
            return "TRANSPORT_FAILURE", False
    return "PROVIDER_SCHEMA_FAILURE", False


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[8]


def _contract_hashes(
    config_path: Path, config: dict[str, Any], requests: list[BenchmarkRequest]
) -> dict[str, Any]:
    return {
        "benchmark_content_hash": frozen_benchmark_content_hash(),
        "governance_prompt_hash": sha256_text(requests[0].instructions),
        "submission_schema_hash": file_hash(ROOT / "schemas" / "model_submission.schema.json"),
        "context_hashes": {
            database_id: sha256_text(
                next(r.serialized_context for r in requests if r.database_id == database_id)
            )
            for database_id in sorted({r.database_id for r in requests})
        },
        "case_order_hash": case_order_hash(),
        "serializer_hash": file_hash(ROOT / "model_contract.py"),
        "evaluator_hash": file_hash(ROOT / "evaluator.py"),
        "validator_hash": file_hash(ROOT / "validator.py"),
        "provider_config": _provider_config_projection(config),
        "provider_config_hash": sha256_text(
            json.dumps(_provider_config_projection(config), sort_keys=True, separators=(",", ":"))
        ),
        "experiment_config_hash": file_hash(config_path),
    }


def _assert_contract(
    config_path: Path,
    config: dict[str, Any],
    requests: list[BenchmarkRequest],
    frozen_manifest: dict[str, Any],
    frozen_ledger: dict[str, Any],
) -> dict[str, Any]:
    audit = _request_audit(requests)
    hashes = _contract_hashes(config_path, config, requests)
    expected_values = {
        "benchmark_content_hash": hashes["benchmark_content_hash"],
        "governance_prompt_sha256": hashes["governance_prompt_hash"],
        "submission_schema_sha256": hashes["submission_schema_hash"],
        "context_hashes": hashes["context_hashes"],
        "case_order_sha256": hashes["case_order_hash"],
        "serializer_version_hash": hashes["serializer_hash"],
        "evaluator_version_hash": hashes["evaluator_hash"],
        "validator_version_hash": hashes["validator_hash"],
        "provider_config_sha256": hashes["provider_config_hash"],
        "experiment_config_sha256": hashes["experiment_config_hash"],
    }
    mismatches = [
        key for key, value in expected_values.items() if frozen_manifest.get(key) != value
    ]
    frozen_requests = frozen_ledger.get("requests", [])
    if len(frozen_requests) != len(requests):
        mismatches.append("request_count")
    else:
        for request, frozen in zip(requests, frozen_requests, strict=True):
            if (
                frozen.get("case_id") != request.case_id
                or frozen.get("full_request_sha256") != request.request_sha256
            ):
                mismatches.append(f"request:{request.case_id}")
    if mismatches:
        raise RuntimeError("M35_CONTRACT_MISMATCH:" + ",".join(sorted(set(mismatches))))
    if config.get("expected_benchmark_content_hash") != hashes["benchmark_content_hash"]:
        raise RuntimeError("M35_CONTRACT_MISMATCH:benchmark_content_hash_config")
    if not audit["passed"]:
        raise RuntimeError("M34.3R_NO_GO:" + json.dumps(audit, sort_keys=True))
    return {"audit": audit, "hashes": hashes}


def _summary_markdown(
    summary: dict[str, Any],
    case_results: list[dict[str, Any]],
    hashes: dict[str, Any],
    config: dict[str, Any],
) -> str:
    scores = summary["scores"]
    lines = [
        "# Decision-SQL Bench v0.1.1-pilot",
        "",
        "## M35 — Luna / reasoning-none / single-call baseline",
        "",
        f"- Model: `{config['model']}`",
        f"- Provider: `{config['provider']}`",
        f"- Reasoning: `{config['reasoning']}`",
        f"- Temperature: `{config['temperature']}`",
        f"- Calls/case: `{config['calls_per_case']}`",
        f"- Repair: `{config['repair']}`; selector: `{config['selector']}`; judge: `{config['judge']}`",
        f"- Benchmark hash: `{hashes['benchmark_content_hash']}`",
        f"- Prompt hash: `{hashes['governance_prompt_hash']}`",
        "",
        "| Metric | Correct / Total | Rate |",
        "|---|---:|---:|",
        f"| Governed Task Success | {scores['governed_task_success']['correct']} / 30 | {scores['governed_task_success']['rate']} |",
        f"| Answerable Test-Suite Accuracy | {scores['answerable_accuracy']['correct']} / 20 | {scores['answerable_accuracy']['rate']} |",
        f"| Authority-Blocked Accuracy | {scores['authority_accuracy']['correct']} / 5 | {scores['authority_accuracy']['rate']} |",
        f"| Ambiguity Detection | {scores['ambiguity_accuracy']['correct']} / 3 | {scores['ambiguity_accuracy']['rate']} |",
        f"| Policy-Blocked Accuracy | {scores['policy_accuracy']['correct']} / 2 | {scores['policy_accuracy']['rate']} |",
        "",
        f"- Unauthorized Answer Rate: {summary['unauthorized_answer_rate']} (5 authority-blocked cases)",
        f"- Wrong Refusal Rate: {summary['wrong_refusal_rate']} (20 answerable cases)",
        f"- Execution Validity: {summary['execution_validity']['valid']} / {summary['execution_validity']['denominator']} ({summary['execution_validity']['rate']})",
        "",
        "## Failure counts",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    for category in FAILURE_CATEGORIES:
        lines.append(f"| {category} | {summary['failure_counts'].get(category, 0)} |")
    lines += [
        "",
        "## Decision confusion matrix",
        "",
        "| Gold behavior | " + " | ".join(DECISION_COLUMNS) + " |",
        "|---|" + "---:|" * len(DECISION_COLUMNS),
    ]
    for behavior in ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED"):
        row = summary["decision_confusion_matrix"][behavior]
        lines.append(
            "| " + behavior + " | " + " | ".join(str(row[c]) for c in DECISION_COLUMNS) + " |"
        )
    lines += [
        "",
        "## Counterfactual value",
        "",
        f"Base-only false positives: {len(summary['base_only_false_positive_cases'])}",
    ]
    if summary["base_only_false_positive_cases"]:
        lines.append("Cases: " + ", ".join(summary["base_only_false_positive_cases"]))
    lines += [
        "",
        "## Per-case results",
        "",
        "| case_id | database | gold | model decision | provider/schema | admission | base | counterfactual suite | category | correct | latency ms |",
        "|---|---|---|---|---|---|---|---|---|---:|---:|",
    ]
    for row in case_results:
        lines.append(
            "| {case_id} | {database_id} | {gold_behavior} | {model_decision} | {provider_schema_status} | {sql_admission} | {base} | {fixtures} | {official_category} | {correct} | {latency_ms} |".format(
                case_id=row["case_id"],
                database_id=row["database_id"],
                gold_behavior=row.get("gold_behavior", "—"),
                model_decision=row.get("model_decision") or "INVALID/NO_DECISION",
                provider_schema_status=row.get("schema_validation", "—"),
                sql_admission=row.get("sql_admission", "—"),
                base=row.get("base_status", "—"),
                fixtures=row.get("fixture_status", "—"),
                official_category=row["official_category"],
                correct="YES" if row["official_correct"] else "NO",
                latency_ms=row.get("latency_ms"),
            )
        )
    non_answerable = [row for row in case_results if row.get("gold_behavior") != "ANSWERABLE"]
    lines += [
        "",
        "## Governance cases",
        "",
        "| case_id | gold behavior | model decision | correct? | SQL wrongly produced? |",
        "|---|---|---|---:|---:|",
    ]
    for row in non_answerable:
        lines.append(
            f"| {row['case_id']} | {row['gold_behavior']} | {row.get('model_decision') or 'INVALID/NO_DECISION'} | {'YES' if row['official_correct'] else 'NO'} | {'YES' if row.get('sql_present') else 'NO'} |"
        )
    answerable = [row for row in case_results if row.get("gold_behavior") == "ANSWERABLE"]
    lines += [
        "",
        "## Answerable SQL cases",
        "",
        "| case_id | decision | admission | execution | base match | all fixtures match | official category |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in answerable:
        lines.append(
            f"| {row['case_id']} | {row.get('model_decision') or 'INVALID/NO_DECISION'} | {row.get('sql_admission', '—')} | {row.get('execution_status', '—')} | {row.get('base_passed', '—')} | {row.get('all_fixtures_passed', '—')} | {row['official_category']} |"
        )
    lines += [
        "",
        "## Provider timing and usage",
        "",
        f"- Provider latency: {summary['latency']}",
        f"- Token usage: {summary['token_usage']}",
        f"- Provider calls attempted: {summary['provider_calls_attempted']}",
        f"- Responses received: {summary['provider_responses_received']}",
    ]
    failures = [row for row in case_results if not row["official_correct"]]
    lines += ["", "## Non-correct cases", ""]
    for row in failures:
        lines.append(
            f"- `{row['case_id']}` — gold `{row.get('gold_behavior', '—')}`, model `{row.get('model_decision') or 'INVALID/NO_DECISION'}`, earliest `{row['official_category']}`, first fixture `{row.get('first_failing_fixture') or '—'}`"
        )
    return "\n".join(lines) + "\n"


def run_m35(config_path: Path = ROOT / "experiments" / "m35_luna_none.json") -> dict[str, Any]:
    config = _experiment_config(config_path)
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
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise RuntimeError(f"M35_CONFIG_MISMATCH:{key}")

    requests = build_all_requests()
    manifest_path = ROOT / "manifests" / "m34_3r_model_contract.json"
    ledger_path = ROOT / "manifests" / "m34_3r_request_ledger.json"
    if not manifest_path.exists() or not ledger_path.exists():
        raise RuntimeError("M34.3R_NO_GO:MISSING_FROZEN_ARTIFACTS")
    frozen_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frozen_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    contract = _assert_contract(config_path, config, requests, frozen_manifest, frozen_ledger)

    # Truth gates are completed before the first provider call and are reused for scoring.
    reference_run = validate_references()
    if (
        not reference_run.agreement
        or reference_run.cases_executed != 40
        or reference_run.fixture_comparisons != 62
    ):
        raise RuntimeError(
            "M35_REFERENCE_GATE_FAILED:" + json.dumps(reference_run.failures, sort_keys=True)
        )
    mutations = mutation_test(reference_run)
    if (
        not mutations["passed"]
        or mutations["killed"] != 61
        or mutations["survived"] != 0
        or mutations["invalid_mutants"] != 0
    ):
        raise RuntimeError("M35_MUTATION_GATE_FAILED:" + json.dumps(mutations, sort_keys=True))

    result_root = ROOT / "experiments" / "results" / "m35"
    result_root.mkdir(parents=True, exist_ok=True)
    raw_path = result_root / "m35_raw_responses.jsonl"
    parsed_path = result_root / "m35_parsed_submissions.jsonl"
    for path in (
        raw_path,
        parsed_path,
        result_root / "m35_case_results.json",
        result_root / "m35_summary.json",
        result_root / "m35_summary.md",
        result_root / "m35_manifest.json",
        result_root / "m35_request_ledger.json",
    ):
        _ensure_new_artifact(path)
    provider_key = get_settings().llm_api_key
    if not provider_key:
        raise M35ProviderBlocked("M35_PROVIDER_BLOCKED:DECISION_SQL_LLM_API_KEY is not configured")

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
    cases = {case["case_id"]: (case, truth) for case, truth in load_pilot()}
    started_at = _now()
    case_results: list[dict[str, Any]] = []
    call_ledger: list[dict[str, Any]] = []
    successful_responses = 0
    provider_blocked: str | None = None

    for index, request in enumerate(requests):
        frozen = frozen_ledger["requests"][index]
        rebuilt = build_benchmark_request(request.case_id)
        if rebuilt.request_sha256 != frozen["full_request_sha256"]:
            raise RuntimeError(f"M35_CONTRACT_MISMATCH:request:{request.case_id}")
        call_started = time.perf_counter()
        started_iso = _now()
        provider.consume_response_wire()
        payload: Any = None
        error: Exception | None = None
        try:
            payload = asyncio.run(
                provider.complete_json_schema(
                    operation="m35_governed_submission",
                    system_prompt=request.instructions,
                    user_prompt=request.user_text,
                    schema_name="decision_sql_m35_submission",
                    schema=submission_schema(),
                )
            )
            successful_responses += 1
        except Exception as exc:  # each case has exactly one attempted provider call
            error = exc
        finished_iso = _now()
        latency_ms = (time.perf_counter() - call_started) * 1000
        capture = provider.consume_model_io()
        response_wire = provider.consume_response_wire()
        response_hash = sha256_bytes(response_wire) if response_wire is not None else None
        metadata = _provider_metadata(payload or {}, capture)
        call_row = {
            "case_id": request.case_id,
            "database_id": request.database_id,
            "case_index": index,
            "request_sha256": request.request_sha256,
            "request_bytes": request.request_bytes,
            "provider": config["provider"],
            "model": config["model"],
            "reasoning": config["reasoning"],
            "temperature": config["temperature"],
            "timeout_seconds": config["timeout_seconds"],
            "started_at": started_iso,
            "finished_at": finished_iso,
            "latency_ms": latency_ms,
            "transport_status": "SUCCESS" if error is None else "FAILURE",
            "provider_request_id": metadata["request_id"],
            "provider_response_id": metadata["provider_response_id"],
            "usage": metadata["usage"],
            "provider_error": None
            if error is None
            else {"type": type(error).__name__, "message": str(error)[:240]},
            "response_sha256": response_hash,
        }
        call_ledger.append(call_row)
        _append_jsonl_once(
            raw_path,
            {
                "case_id": request.case_id,
                "case_index": index,
                "request_sha256": request.request_sha256,
                "response_sha256": response_hash,
                "raw_response_bytes_base64": base64.b64encode(response_wire).decode("ascii")
                if response_wire is not None
                else None,
            },
        )

        case, truth = cases[request.case_id]
        row: dict[str, Any] = {
            "case_id": request.case_id,
            "database_id": request.database_id,
            "case_index": index,
            "request_sha256": request.request_sha256,
            "provider_status": call_row["transport_status"],
            "response_sha256": response_hash,
            "latency_ms": latency_ms,
            "usage": metadata["usage"],
            "parsed_submission": None,
            "case_id_matches": None,
            "schema_validation": None,
            "sql_present": None,
            "sql_admission": None,
            "base_execution": None,
            "fixture_execution": [],
            "official_category": None,
            "official_correct": False,
            "model_decision": None,
            "expected_decision": EXPECTED_DECISION[case["task_type"]],
            "diagnostics": {},
            "gold_behavior": case["task_type"],
        }
        parsed_value: dict[str, Any] | None = None
        parse_status: str = "NOT_ATTEMPTED"
        case_id_matches: bool | None = None
        if error is not None:
            category, is_global = _failure_for_provider_error(error)
            row.update(
                {
                    "schema_validation": category,
                    "official_category": category,
                    "diagnostics": {"provider_error": str(error)[:240]},
                }
            )
            if is_global and successful_responses == 0:
                provider_blocked = f"M35_PROVIDER_BLOCKED:{type(error).__name__}:{str(error)[:240]}"
        else:
            content = getattr(capture, "raw_assistant_content_full", None)
            submission, parse_status, parse_detail, parsed_value = _parse_submission(
                content, request.case_id
            )
            case_id_matches = None if submission is None else submission.case_id == request.case_id
            row.update(
                {
                    "parsed_submission": parsed_value,
                    "schema_validation": parse_status,
                    "case_id_matches": case_id_matches,
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
                evaluated = _evaluate_submission(
                    case, truth, submission, reference_run.expected_results
                )
                row.update(evaluated)
                row["sql_admission"] = (
                    "PASS"
                    if evaluated["official_category"]
                    in {"CORRECT", "RESULT_MISMATCH", "EXECUTION_FAILURE"}
                    and submission.decision == "ANSWER"
                    and evaluated.get("base_execution") is not None
                    else (
                        "FAIL"
                        if evaluated["official_category"] == "SQL_ADMISSION_FAILURE"
                        else None
                    )
                )
                row["execution_status"] = (
                    "PASS"
                    if evaluated.get("all_fixtures_passed") is True
                    else ("FAIL" if evaluated.get("all_fixtures_passed") is False else None)
                )
                row["base_status"] = (
                    "PASS"
                    if evaluated.get("base_passed") is True
                    else ("FAIL" if evaluated.get("base_passed") is False else None)
                )
                row["fixture_status"] = (
                    "PASS"
                    if evaluated.get("all_fixtures_passed") is True
                    else ("FAIL" if evaluated.get("all_fixtures_passed") is False else None)
                )
                row["sql_present"] = submission.sql is not None
        _append_jsonl_once(
            parsed_path,
            {
                "case_id": request.case_id,
                "request_sha256": request.request_sha256,
                "response_sha256": response_hash,
                "parsed_submission": parsed_value,
                "schema_validation": row["schema_validation"],
                "case_id_matches": row["case_id_matches"],
                "provider_metadata": metadata,
            },
        )
        case_results.append(row)
        if provider_blocked:
            break

    if provider_blocked:
        _dump(
            result_root / "m35_provider_blocked.json",
            {
                "status": "M35_PROVIDER_BLOCKED",
                "reason": provider_blocked,
                "provider_calls_attempted": len(case_results),
                "responses_received": successful_responses,
            },
        )
        raise M35ProviderBlocked(provider_blocked)
    if len(case_results) != 30:
        raise RuntimeError(f"M35_ABORTED_CONTRACT_DEFECT:only_{len(case_results)}_cases_completed")

    for row in case_results:
        if row["official_category"] is None:
            raise RuntimeError(f"M35_ABORTED_CONTRACT_DEFECT:unclassified:{row['case_id']}")

    failure_counts = Counter(row["official_category"] for row in case_results)
    gold_groups = defaultdict(list)
    for row in case_results:
        gold_groups[row["gold_behavior"]].append(row)

    def score(behavior: str) -> dict[str, Any]:
        rows = gold_groups[behavior]
        correct = sum(bool(row["official_correct"]) for row in rows)
        total = len(rows)
        return {"correct": correct, "total": total, "rate": f"{correct / total:.1%}"}

    confusion: dict[str, dict[str, int]] = {}
    for behavior in ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED"):
        values = {key: 0 for key in DECISION_COLUMNS}
        for row in gold_groups[behavior]:
            decision = row.get("model_decision")
            values[decision if decision in DECISION_COLUMNS[:-1] else "INVALID/NO_DECISION"] += 1
        confusion[behavior] = values
    admitted_rows = [
        row
        for row in gold_groups["ANSWERABLE"]
        if row.get("model_decision") == "ANSWER" and row.get("sql_admission") == "PASS"
    ]
    valid_execution = sum(bool(row.get("all_fixtures_passed")) for row in admitted_rows)
    authority_answers = sum(
        row.get("model_decision") == "ANSWER" for row in gold_groups["AUTHORITY_BLOCKED"]
    )
    wrong_refusals = sum(row.get("model_decision") != "ANSWER" for row in gold_groups["ANSWERABLE"])
    successful_latencies = [
        float(row["latency_ms"]) for row in case_results if row["provider_status"] == "SUCCESS"
    ]
    all_usage = [row["usage"] for row in case_results if row["provider_status"] == "SUCCESS"]

    def usage_metric(key: str) -> dict[str, Any]:
        values = [item[key] for item in all_usage if item.get(key) is not None]
        return {
            "available_cases": len(values),
            "median_per_case": statistics.median(values) if values else None,
            "total": sum(values) if values else None,
        }

    tag_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in gold_groups["ANSWERABLE"]:
        case = cases[row["case_id"]][0]
        for tag in cases[row["case_id"]][1]["semantic_target"].get(
            "mechanism_tags", case.get("mechanism_tags", [])
        ):
            tag_counts[tag]["total"] += 1
            tag_counts[tag]["correct"] += int(bool(row["official_correct"]))
    domain_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "overall_correct": 0,
            "overall_total": 0,
            "answerable_correct": 0,
            "answerable_total": 0,
            "governance_correct": 0,
        }
    )
    for row in case_results:
        domain = row["database_id"]
        d = domain_counts[domain]
        d["overall_total"] += 1
        d["overall_correct"] += int(bool(row["official_correct"]))
        d["governance_correct"] += int(row.get("model_decision") == row.get("expected_decision"))
        if row["gold_behavior"] == "ANSWERABLE":
            d["answerable_total"] += 1
            d["answerable_correct"] += int(bool(row["official_correct"]))
    base_only = [
        row["case_id"]
        for row in case_results
        if row.get("official_category") == "RESULT_MISMATCH"
        and row.get("base_passed") is True
        and row.get("all_fixtures_passed") is False
    ]
    summary = {
        "experiment_id": config["experiment_id"],
        "failure_counts": dict(failure_counts),
        "scores": {
            "governed_task_success": {
                "correct": sum(bool(row["official_correct"]) for row in case_results),
                "total": 30,
                "rate": f"{sum(bool(row['official_correct']) for row in case_results) / 30:.1%}",
            },
            "answerable_accuracy": score("ANSWERABLE"),
            "authority_accuracy": score("AUTHORITY_BLOCKED"),
            "ambiguity_accuracy": score("AMBIGUOUS"),
            "policy_accuracy": score("POLICY_BLOCKED"),
        },
        "execution_validity": {
            "valid": valid_execution,
            "denominator": len(admitted_rows),
            "rate": f"{valid_execution / len(admitted_rows):.1%}"
            if admitted_rows
            else "UNAVAILABLE",
        },
        "unauthorized_answer_rate": f"{authority_answers}/5 ({authority_answers / 5:.1%})",
        "wrong_refusal_rate": f"{wrong_refusals}/20 ({wrong_refusals / 20:.1%})",
        "decision_confusion_matrix": confusion,
        "decision_distribution": dict(
            Counter(row.get("model_decision") or "invalid" for row in case_results)
        ),
        "base_only_false_positive_cases": base_only,
        "domain_breakdown": dict(domain_counts),
        "mechanism_tag_breakdown": dict(tag_counts),
        "latency": {
            "successful_responses": len(successful_latencies),
            "median_ms": statistics.median(successful_latencies) if successful_latencies else None,
            "p90_ms": _p90(successful_latencies),
            "min_ms": min(successful_latencies) if successful_latencies else None,
            "max_ms": max(successful_latencies) if successful_latencies else None,
        },
        "token_usage": {
            "input": usage_metric("prompt_tokens"),
            "output": usage_metric("completion_tokens"),
            "total": usage_metric("total_tokens"),
            "reasoning": usage_metric("reasoning_tokens"),
        },
        "provider_calls_attempted": len(case_results),
        "provider_responses_received": successful_responses,
        "transport_failures": failure_counts.get("TRANSPORT_FAILURE", 0),
        "schema_failures": failure_counts.get("PROVIDER_SCHEMA_FAILURE", 0),
        "raw_outputs_preserved": True,
        "all_cases_deterministically_classified": True,
    }
    m35_manifest = {
        "experiment_id": config["experiment_id"],
        "benchmark_version": config["benchmark_version"],
        "experiment_name": "m35_luna_none",
        "benchmark_content_hash": contract["hashes"]["benchmark_content_hash"],
        "contract_source_commit": frozen_manifest.get("contract_source_commit"),
        "execution_commit": git_revision(),
        "model": config["model"],
        "provider": config["provider"],
        "reasoning": config["reasoning"],
        "temperature": config["temperature"],
        "timeout_seconds": config["timeout_seconds"],
        "calls_per_case": config["calls_per_case"],
        "transport_retries": config["transport_retries"],
        "semantic_retries": config["semantic_retries"],
        "repair": config["repair"],
        "selector": config["selector"],
        "judge": config["judge"],
        "governance_prompt_hash": contract["hashes"]["governance_prompt_hash"],
        "submission_schema_hash": contract["hashes"]["submission_schema_hash"],
        "context_hashes": contract["hashes"]["context_hashes"],
        "case_order_hash": contract["hashes"]["case_order_hash"],
        "serializer_hash": contract["hashes"]["serializer_hash"],
        "evaluator_hash": contract["hashes"]["evaluator_hash"],
        "validator_hash": contract["hashes"]["validator_hash"],
        "provider_config_hash": contract["hashes"]["provider_config_hash"],
        "experiment_config_hash": contract["hashes"]["experiment_config_hash"],
        "run_start": started_at,
        "run_end": _now(),
        "provider_calls_attempted": len(case_results),
        "provider_responses_received": successful_responses,
        "reference_gate": {
            "cases": reference_run.cases_executed,
            "fixture_comparisons": reference_run.fixture_comparisons,
        },
        "mutation_gate": {
            "authored": mutations["authored"],
            "killed": mutations["killed"],
            "survived": mutations["survived"],
            "invalid": mutations["invalid_mutants"],
        },
        "raw_response_artifact": str(raw_path.relative_to(ROOT)),
        "parsed_submission_artifact": str(parsed_path.relative_to(ROOT)),
    }
    _dump(
        result_root / "m35_request_ledger.json",
        {"provider_calls_attempted": len(call_ledger), "requests": call_ledger},
    )
    _dump(result_root / "m35_case_results.json", case_results)
    _dump(result_root / "m35_summary.json", summary)
    (result_root / "m35_summary.md").write_text(
        _summary_markdown(summary, case_results, contract["hashes"], config), encoding="utf-8"
    )
    _dump(result_root / "m35_manifest.json", m35_manifest)
    return {
        "status": "M35_COMPLETE",
        "manifest": m35_manifest,
        "summary": summary,
        "case_results": case_results,
    }
