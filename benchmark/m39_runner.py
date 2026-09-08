"""M39: first frozen v0.2.0-dev Luna baseline.

This runner is intentionally separate from the pilot-specific M35 runner.  It
uses the same proven OpenAI-compatible adapter, but consumes the frozen M38
90-case request ledger and combines the historical pilot evaluator with the
three new deterministic database packs.
"""

# The generated evidence report intentionally contains long contract rows.
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import base64
import json
import os
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.config import get_settings
from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    ProviderConfigurationError,
)
from benchmark.authoring import SCHEMA_NAMES as PILOT_SCHEMAS
from benchmark.authoring import connection_kwargs_from_env, seed_database
from benchmark.context import load_authority
from benchmark.m38_authoring import M38_DATABASES, seed_m38_database
from benchmark.m38_validation import validate_new_mutants, validate_new_references
from benchmark.model_contract import (
    ROOT,
    context_hash,
    file_hash,
    frozen_benchmark_content_hash,
    governance_instructions,
    request_leakage,
    serialize_governed_context_v1,
    sha256_bytes,
    sha256_text,
    submission_schema,
)
from benchmark.models import (
    ResultContract,
    Submission,
    compare_rows,
    validate_submission_invariants,
)
from benchmark.safety import SqlAdmissionError, execute_query, validate_read_only_select
from benchmark.validator import mutation_test, validate_references

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
DECISIONS = ("ANSWER", "BLOCKED_AUTHORITY", "NEEDS_CLARIFICATION", "BLOCKED_POLICY")
EXPECTED_DECISION = {
    "ANSWERABLE": "ANSWER",
    "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
    "AMBIGUOUS": "NEEDS_CLARIFICATION",
    "POLICY_BLOCKED": "BLOCKED_POLICY",
}
M39_CONFIG = ROOT / "experiments" / "m39_v020_luna_none.json"
M38_MANIFEST = ROOT / "manifests" / "m38_benchmark_manifest.json"
M38_LEDGER = ROOT / "manifests" / "m38_request_ledger.json"
M39_CONTRACT = ROOT / "manifests" / "m39_contract.json"
M39_LEDGER = ROOT / "manifests" / "m39_request_ledger.json"
RESULT_ROOT = ROOT / "experiments" / "results" / "m39"
SCHEMAS = {**PILOT_SCHEMAS, **M38_DATABASES}
DEV_DATABASES = {"commerce_ops", "fleet_ops", "support_ops", "subscription_billing"}
CONFIRMATION_DATABASES = {"warehouse_logistics", "risk_operations"}


class M39ProviderBlocked(RuntimeError):
    """A global provider/configuration failure that makes continuation unsafe."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write((json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())


def _sha(path: Path) -> str:
    return file_hash(path)


def _git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return "UNAVAILABLE"


def _load_config() -> dict[str, Any]:
    value = cast(dict[str, Any], json.loads(M39_CONFIG.read_text(encoding="utf-8")))
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
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RuntimeError(f"M39_CONFIG_MISMATCH:{key}")
    return value


def _load_rows(ledger: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for entry in ledger["requests"]:
        case_id = str(entry["case_id"])
        case_dir = "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"
        case = json.loads(
            (ROOT / "cases" / case_dir / f"{case_id}.json").read_text(encoding="utf-8")
        )
        truth = json.loads(
            (ROOT / "ground_truth" / case_dir / f"{case_id}.json").read_text(encoding="utf-8")
        )
        rows[case_id] = (case, truth)
    return rows


def _request_text(case: dict[str, Any]) -> tuple[str, str, int]:
    instructions = governance_instructions()
    question = str(case["question"])
    context = serialize_governed_context_v1(str(case["database_id"]))
    text = (
        "SYSTEM:\n"
        + instructions
        + "\n\nUSER:\nCase ID:\n"
        + str(case["case_id"])
        + "\n\nQuestion:\n"
        + question
        + "\n\nGoverned context:\n"
        + context
    )
    return text, context, len(text.encode("utf-8"))


def _build_requests(
    ledger: dict[str, Any], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for expected in ledger["requests"]:
        case = rows[str(expected["case_id"])][0]
        text, context, request_bytes = _request_text(case)
        request = {
            "case_id": case["case_id"],
            "database_id": case["database_id"],
            "request_text": text,
            "user_text": text.split("\n\nUSER:\n", 1)[1],
            "instructions": governance_instructions(),
            "question": case["question"],
            "serialized_context": context,
            "request_sha256": sha256_text(text),
            "request_bytes": request_bytes,
        }
        if request["request_sha256"] != expected["full_request_sha256"]:
            raise RuntimeError(f"M39_CONTRACT_DRIFT:request:{case['case_id']}")
        requests.append(request)
    return requests


def _provider_projection(config: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings().model_copy(
        update={
            "llm_model": config["model"],
            "llm_reasoning_effort": config["reasoning"],
            "llm_temperature": config["temperature"],
            "llm_timeout_seconds": config["timeout_seconds"],
        }
    )
    return {
        "provider": config["provider"],
        "endpoint_family": "chat_completions",
        "base_url": settings.llm_base_url,
        "model": settings.llm_model,
        "reasoning": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "timeout_seconds": settings.llm_timeout_seconds,
        "credential_configured": bool(settings.llm_api_key),
    }


def _contract_hashes(config: dict[str, Any], requests: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = json.loads(M38_MANIFEST.read_text(encoding="utf-8"))
    provider_projection = _provider_projection(config)
    return {
        "benchmark_content_hash": frozen_benchmark_content_hash(),
        "governance_prompt_hash": sha256_text(governance_instructions()),
        "submission_schema_hash": _sha(ROOT / "schemas" / "model_submission.schema.json"),
        "context_hashes": {
            database_id: context_hash(database_id)
            for database_id in sorted({request["database_id"] for request in requests})
        },
        "case_order_hash": sha256_text(
            json.dumps([request["case_id"] for request in requests], separators=(",", ":"))
        ),
        "serializer_hash": _sha(ROOT / "model_contract.py"),
        "evaluator_hash": _sha(ROOT / "evaluator.py"),
        "validator_hash": _sha(ROOT / "validator.py"),
        "provider_adapter_hash": _sha(ROOT.parent / "app" / "generation" / "provider.py"),
        "provider_config": provider_projection,
        "provider_config_hash": sha256_text(
            json.dumps(provider_projection, sort_keys=True, separators=(",", ":"))
        ),
        "experiment_config_hash": _sha(M39_CONFIG),
        "postgresql_version": manifest["postgresql_version"],
    }


def _assert_provider_schema() -> None:
    schema = submission_schema()
    forbidden = ("allOf", "oneOf", "if", "then", "else", "dependentSchemas")
    serialized = json.dumps(schema, sort_keys=True)
    if any(keyword in serialized for keyword in forbidden):
        raise RuntimeError("M39_CONTRACT_MISMATCH:unsupported_provider_schema_keyword")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise RuntimeError("M39_CONTRACT_MISMATCH:provider_schema_shape")
    if set(schema.get("required", [])) != {"case_id", "decision", "sql", "reason_code"}:
        raise RuntimeError("M39_CONTRACT_MISMATCH:provider_schema_required")


def _offline_gates(
    manifest: dict[str, Any],
    ledger: dict[str, Any],
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_hash = "32fb1f941773f4ec7d7ccc1fa38525e2730509b00c5df1f5f7dbb72cc5c77226"
    if (
        manifest.get("benchmark_content_hash") != expected_hash
        or frozen_benchmark_content_hash() != expected_hash
    ):
        raise RuntimeError("M39_CONTRACT_MISMATCH:benchmark_content_hash")
    if (
        manifest.get("benchmark_version") != "0.2.0-dev"
        or manifest.get("database_count") != 6
        or manifest.get("case_count") != 90
    ):
        raise RuntimeError("M39_CONTRACT_MISMATCH:benchmark_dimensions")
    distribution = Counter(case["task_type"] for case, _truth in rows.values())
    expected_distribution = {
        "ANSWERABLE": 60,
        "AUTHORITY_BLOCKED": 15,
        "AMBIGUOUS": 9,
        "POLICY_BLOCKED": 6,
    }
    if dict(distribution) != expected_distribution or len(requests) != 90:
        raise RuntimeError("M39_CONTRACT_MISMATCH:task_distribution")
    split_counts: Counter[str] = Counter()
    for case, _truth in rows.values():
        split_counts["DEV" if case["database_id"] in DEV_DATABASES else "CONFIRMATION"] += 1
    if dict(split_counts) != {"DEV": 50, "CONFIRMATION": 40}:
        raise RuntimeError("M39_CONTRACT_MISMATCH:split_distribution")
    if (
        ledger.get("provider_calls") != 0
        or ledger.get("case_order_sha256")
        != "3299ecb9046619cd7b2e2aed66ed2e4146e8286b0497202b3b3ebc151947c2c2"
    ):
        raise RuntimeError("M39_CONTRACT_MISMATCH:frozen_case_order")
    if (
        len(
            {
                request["serialized_context"]
                for request in requests
                if request["database_id"] == "commerce_ops"
            }
        )
        != 1
    ):
        raise RuntimeError("M39_CONTRACT_MISMATCH:context_determinism")
    for request in requests:
        if f"Case ID:\n{request['case_id']}\n" not in request["request_text"]:
            raise RuntimeError("M39_CONTRACT_MISMATCH:case_id_visibility")
        if (
            request["question"] not in request["request_text"]
            or request["instructions"] not in request["request_text"]
        ):
            raise RuntimeError("M39_CONTRACT_MISMATCH:request_component")
        case, truth = rows[request["case_id"]]
        if request_leakage(type("Request", (), {"request_text": request["request_text"]})(), case):
            raise RuntimeError(f"M39_CONTRACT_MISMATCH:leakage:{request['case_id']}")
        if case["task_type"] == "ANSWERABLE" and not all(
            fact_visible(request["database_id"], fact)
            for fact in truth.get("required_context_facts", [])
        ):
            raise RuntimeError(f"M39_CONTRACT_MISMATCH:context_sufficiency:{request['case_id']}")
    _assert_provider_schema()
    quality = {
        "context_sufficient": 60,
        "authority_sufficient": 60,
        "projection_sufficient": 60,
        "references": 120,
        "fixture_comparisons": 182,
        "mutants": 188,
        "mutants_killed": 188,
        "invalid_mutants": 0,
        "surviving_mutants": 0,
        "audit_clean": 90,
        "leakage": 0,
    }
    frozen_quality = {
        "context_sufficient": manifest["context_sufficient"],
        "authority_sufficient": manifest["authority_sufficient"],
        "projection_sufficient": manifest["projection_sufficient"],
        "references": manifest["reference_pairs"],
        "fixture_comparisons": manifest["fixture_comparisons"],
        "mutants": manifest["mutants"],
        "mutants_killed": manifest["mutants_killed"],
        "invalid_mutants": manifest["invalid_mutants"],
        "surviving_mutants": manifest["surviving_mutants"],
    }
    if frozen_quality != {key: quality[key] for key in frozen_quality}:
        raise RuntimeError("M39_CONTRACT_MISMATCH:frozen_quality_gate")
    audit = json.loads((ROOT / "audits" / "m38_case_audit.json").read_text(encoding="utf-8"))
    if sum(item.get("status") == "CLEAN" for item in audit["cases"]) != 90:
        raise RuntimeError("M39_CONTRACT_MISMATCH:adversarial_audit")
    leakage = json.loads((ROOT / "audits" / "m38_leakage_audit.json").read_text(encoding="utf-8"))
    if any(not item.get("clean", False) for item in leakage["cases"]):
        raise RuntimeError("M39_CONTRACT_MISMATCH:leakage")
    return quality


def fact_visible(database_id: str, fact: str) -> bool:
    authority = load_authority(database_id)
    section, _, raw = fact.partition(":")
    if section == "policy":
        return raw in authority["policy"].get("policy_id", "")
    if section == "relationships":
        return any(
            item["relationship_id"].endswith(raw) and item.get("authorized") is True
            for item in authority["relationships"]
        )
    if section in {"metrics", "temporal_rules"}:
        key = "metric_id" if section == "metrics" else "temporal_rule_id"
        return any(item[key].endswith(raw) for item in authority[section])
    return raw in json.dumps(authority, sort_keys=True)


def _seed(database_id: str) -> None:
    if database_id in M38_DATABASES:
        seed_m38_database(database_id)
    else:
        seed_database(database_id, connection_kwargs_from_env())


def _reference_gate() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    old = validate_references()
    new = validate_new_references()
    if not old.agreement or old.cases_executed != 40 or old.fixture_comparisons != 62:
        raise RuntimeError("M39_REFERENCE_GATE_FAILED:pilot")
    if not new["passed"] or new["reference_pairs"] != 80 or new["fixture_comparisons"] != 120:
        raise RuntimeError("M39_REFERENCE_GATE_FAILED:new")
    expected = {**old.expected_results, **new["expected"]}
    return expected, {"reference_pairs": 120, "fixture_comparisons": 182}


def _mutation_gate(old_reference: Any, new_reference: dict[str, Any]) -> dict[str, Any]:
    old = mutation_test(old_reference)
    new = validate_new_mutants(new_reference)
    result = {
        "authored": old["authored"] + new["authored"],
        "killed": old["killed"] + new["killed"],
        "invalid": old["invalid_mutants"] + new["invalid"],
        "survived": old["survived"] + new["survived"],
    }
    if result != {"authored": 188, "killed": 188, "invalid": 0, "survived": 0}:
        raise RuntimeError("M39_MUTATION_GATE_FAILED:" + json.dumps(result, sort_keys=True))
    return result


def freeze_m39_contract() -> dict[str, Any]:
    """Build the pre-call M39 contract artifacts without touching the provider."""
    config = _load_config()
    manifest = json.loads(M38_MANIFEST.read_text(encoding="utf-8"))
    ledger = json.loads(M38_LEDGER.read_text(encoding="utf-8"))
    rows = _load_rows(ledger)
    requests = _build_requests(ledger, rows)
    quality = _offline_gates(manifest, ledger, rows, requests)
    hashes = _contract_hashes(config, requests)
    expected, reference_gate = _reference_gate()
    old_reference = validate_references()
    new_reference = validate_new_references()
    mutation_gate = _mutation_gate(old_reference, new_reference)
    frozen_requests = [
        {
            "case_id": request["case_id"],
            "case_index": index,
            "database_id": request["database_id"],
            "split": "DEV" if request["database_id"] in DEV_DATABASES else "CONFIRMATION",
            "question_sha256": sha256_text(request["question"]),
            "context_sha256": context_hash(request["database_id"]),
            "instruction_sha256": hashes["governance_prompt_hash"],
            "submission_schema_sha256": hashes["submission_schema_hash"],
            "full_request_sha256": request["request_sha256"],
            "request_bytes": request["request_bytes"],
        }
        for index, request in enumerate(requests, 1)
    ]
    frozen_ledger = {
        "benchmark_version": "0.2.0-dev",
        "experiment_id": config["experiment_id"],
        "provider_calls": 0,
        "case_order": [request["case_id"] for request in requests],
        "case_order_sha256": hashes["case_order_hash"],
        "requests": frozen_requests,
    }
    contract = {
        "experiment_id": config["experiment_id"],
        "experiment_name": config["experiment_name"],
        "benchmark_version": config["benchmark_version"],
        "benchmark_content_hash": hashes["benchmark_content_hash"],
        "contract_source_commit": _git_revision(),
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
        "request_count": len(requests),
        "request_hashes_frozen": True,
        "quality_gates": quality,
        "reference_gate": reference_gate,
        "mutation_gate": mutation_gate,
        "provider_calls": 0,
        "expected_results_not_embedded": True,
        "note": "M39 contract frozen before live provider execution; benchmark content is consumed unchanged.",
    }
    _dump(ROOT / "manifests" / "m39_contract.json", contract)
    _dump(ROOT / "manifests" / "m39_request_ledger.json", frozen_ledger)
    return {"contract": contract, "ledger": frozen_ledger, "expected_results": expected}


def _parse(
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
        submission = Submission.from_dict_unchecked(value)
    except ValueError as error:
        return None, "PROVIDER_SCHEMA_FAILURE", str(error), value
    errors = validate_submission_invariants(expected_case_id, submission)
    if errors:
        return submission, "INVALID_SUBMISSION", errors[0], value
    return submission, "PASS", None, value


def _provider_metadata(payload: Any, capture: Any) -> dict[str, Any]:
    choice = payload.get("choices", [{}])[0] if isinstance(payload, dict) else {}
    choice = choice if isinstance(choice, dict) else {}
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("completion_tokens_details", {})
    details = details if isinstance(details, dict) else {}
    return {
        "request_id": getattr(capture, "request_id", None),
        "provider_response_id": payload.get("id") if isinstance(payload, dict) else None,
        "resolved_model": payload.get("model") if isinstance(payload, dict) else None,
        "finish_reason": choice.get("finish_reason"),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens")
            if isinstance(usage.get("prompt_tokens"), int)
            else None,
            "completion_tokens": usage.get("completion_tokens")
            if isinstance(usage.get("completion_tokens"), int)
            else None,
            "total_tokens": usage.get("total_tokens")
            if isinstance(usage.get("total_tokens"), int)
            else None,
            "reasoning_tokens": details.get("reasoning_tokens")
            if isinstance(details.get("reasoning_tokens"), int)
            else None,
        },
    }


def _error_detail(error: Exception) -> dict[str, Any]:
    detail = getattr(error, "detail", None)
    if detail is None:
        return {}
    return {
        "http_status": detail.status_code,
        "error_type": detail.error_type,
        "error_code": detail.error_code,
        "retryable": detail.retryable,
    }


def _classify_provider_error(error: Exception) -> tuple[str, bool]:
    if isinstance(error, ProviderConfigurationError):
        return "TRANSPORT_FAILURE", True
    if isinstance(error, LLMProviderError):
        detail = error.detail
        if detail and detail.status_code == 400 and detail.error_type == "invalid_request_error":
            if "response_format" in detail.message or "schema" in detail.message.lower():
                return "PROVIDER_SCHEMA_FAILURE", True
        if detail and detail.status_code in {401, 403, 404}:
            return "TRANSPORT_FAILURE", True
        return "TRANSPORT_FAILURE", False
    return "TRANSPORT_FAILURE", False


def _evaluate(
    case: dict[str, Any],
    truth: dict[str, Any],
    submission: Submission,
    expected_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    behavior = str(case["task_type"])
    expected_decision = EXPECTED_DECISION[behavior]
    result: dict[str, Any] = {
        "gold_behavior": behavior,
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
        result.update(official_category="WRONG_GOVERNED_DECISION", official_correct=False)
        return result
    if behavior != "ANSWERABLE":
        result.update(official_category="CORRECT", official_correct=submission.sql is None)
        if submission.sql is not None:
            result.update(official_category="WRONG_GOVERNED_DECISION", official_correct=False)
            result["diagnostics"] = {"non_answerable_sql_produced": True}
        return result
    if submission.sql is None:
        result.update(official_category="SQL_ADMISSION_FAILURE", official_correct=False)
        result["diagnostics"] = {"admission_reason": "MISSING_SQL"}
        return result
    try:
        validate_read_only_select(submission.sql)
    except SqlAdmissionError as error:
        result.update(official_category="SQL_ADMISSION_FAILURE", official_correct=False)
        result["diagnostics"] = {"admission_reason": str(error)}
        return result
    result["sql_admission"] = "PASS"
    contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
    fixtures = [{"fixture_id": "base", "patch_sql": []}, *truth["counterfactual_fixtures"]]
    all_passed = True
    first_failure: str | None = None
    for fixture in fixtures:
        fixture_id = fixture["fixture_id"]
        execution: dict[str, Any] = {"fixture_id": fixture_id, "status": None, "reason": None}
        try:
            _seed(case["database_id"])
            columns, rows = execute_query(
                connection_kwargs_from_env(),
                SCHEMAS[case["database_id"]],
                submission.sql,
                patch_sql=fixture["patch_sql"],
            )
            expected = expected_results[case["case_id"]]["fixtures"][fixture_id]
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
        except Exception as error:
            execution.update(
                {"status": "ERROR", "reason": f"{type(error).__name__}:{str(error)[:240]}"}
            )
            all_passed = False
            first_failure = first_failure or fixture_id
        if fixture_id == "base":
            result["base_execution"] = execution
            result["base_passed"] = execution["status"] == "PASS"
        else:
            result["fixture_execution"].append(execution)
    result["first_failing_fixture"] = first_failure
    result["all_fixtures_passed"] = all_passed
    if result["base_passed"] is False:
        result.update(
            official_category="RESULT_MISMATCH"
            if result["base_execution"]["status"] == "FAIL"
            else "EXECUTION_FAILURE",
            official_correct=False,
        )
    elif not all_passed:
        result.update(official_category="RESULT_MISMATCH", official_correct=False)
        result["diagnostics"] = {"BASE_ONLY_FALSE_POSITIVE": first_failure != "base"}
    else:
        result.update(official_category="CORRECT", official_correct=True)
    return result


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    return (
        values[0] if len(values) == 1 else statistics.quantiles(values, n=10, method="inclusive")[8]
    )


def _rate(correct: int, total: int) -> str:
    return f"{correct / total:.1%}" if total else "UNAVAILABLE"


def _score(rows: list[dict[str, Any]], behavior: str) -> dict[str, Any]:
    subset = [row for row in rows if row["gold_behavior"] == behavior]
    correct = sum(bool(row["official_correct"]) for row in subset)
    return {"correct": correct, "total": len(subset), "rate": _rate(correct, len(subset))}


def _summary(
    rows: list[dict[str, Any]],
    cases: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    calls: list[dict[str, Any]],
    config: dict[str, Any],
    hashes: dict[str, Any],
    quality: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    failure_counts = Counter(row["official_category"] for row in rows)
    for category in FAILURE_CATEGORIES:
        failure_counts.setdefault(category, 0)
    splits: dict[str, dict[str, Any]] = {
        "DEV": {"rows": [], "databases": DEV_DATABASES},
        "CONFIRMATION": {"rows": [], "databases": CONFIRMATION_DATABASES},
    }
    for row in rows:
        splits["DEV" if row["database_id"] in DEV_DATABASES else "CONFIRMATION"]["rows"].append(row)
    confusion: dict[str, dict[str, int]] = {}
    for behavior in EXPECTED_DECISION:
        values = {decision: 0 for decision in (*DECISIONS, "INVALID/NO_DECISION")}
        for row in rows:
            if row["gold_behavior"] == behavior:
                decision = row.get("model_decision")
                values[decision if decision in DECISIONS else "INVALID/NO_DECISION"] += 1
        confusion[behavior] = values
    answerable = [row for row in rows if row["gold_behavior"] == "ANSWERABLE"]
    admitted = [
        row
        for row in answerable
        if row.get("model_decision") == "ANSWER" and row.get("sql_admission") == "PASS"
    ]
    latencies = [float(row["latency_ms"]) for row in rows if row["provider_status"] == "SUCCESS"]
    usages = [row["usage"] for row in rows if row["provider_status"] == "SUCCESS"]

    def usage_metric(key: str) -> dict[str, Any]:
        values = [value[key] for value in usages if value.get(key) is not None]
        return {
            "available_cases": len(values),
            "median_per_case": statistics.median(values) if values else None,
            "total": sum(values) if values else None,
        }

    split_summary: dict[str, Any] = {}
    for split, info in splits.items():
        split_rows = info["rows"]
        split_summary[split] = {
            "governed_success": {
                "correct": sum(bool(row["official_correct"]) for row in split_rows),
                "total": len(split_rows),
            },
            "answerable": _score(split_rows, "ANSWERABLE"),
            "authority": _score(split_rows, "AUTHORITY_BLOCKED"),
            "ambiguity": _score(split_rows, "AMBIGUOUS"),
            "policy": _score(split_rows, "POLICY_BLOCKED"),
        }
        split_summary[split]["governed_success"]["rate"] = _rate(
            split_summary[split]["governed_success"]["correct"], len(split_rows)
        )
    domain: dict[str, Any] = {}
    for database_id in sorted({row["database_id"] for row in rows}):
        group = [row for row in rows if row["database_id"] == database_id]
        sql_group = [row for row in group if row["gold_behavior"] == "ANSWERABLE"]
        governance_group = [row for row in group if row["gold_behavior"] != "ANSWERABLE"]
        domain[database_id] = {
            "governed": {
                "correct": sum(bool(row["official_correct"]) for row in group),
                "total": len(group),
            },
            "answerable": {
                "correct": sum(bool(row["official_correct"]) for row in sql_group),
                "total": len(sql_group),
            },
            "governance_negative": {
                "correct": sum(bool(row["official_correct"]) for row in governance_group),
                "total": len(governance_group),
            },
        }
    base_only = [
        row["case_id"]
        for row in answerable
        if row.get("base_passed") is True and row.get("all_fixtures_passed") is False
    ]
    tag_breakdown: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"correct": 0, "total": 0, "rate": "UNAVAILABLE"}
    )
    difficulty: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"correct": 0, "total": 0, "rate": "UNAVAILABLE"}
    )
    for row in answerable:
        case, truth = cases[row["case_id"]]
        for tag in truth["semantic_target"].get(
            "query_shape_tags", truth["semantic_target"].get("mechanism_tags", [])
        ):
            tag_breakdown[tag]["total"] += 1
            tag_breakdown[tag]["correct"] += int(bool(row["official_correct"]))
        level = truth["semantic_target"].get("difficulty", case.get("difficulty", "UNSPECIFIED"))
        difficulty[level]["total"] += 1
        difficulty[level]["correct"] += int(bool(row["official_correct"]))
    for mapping in (tag_breakdown, difficulty):
        for value in mapping.values():
            value["rate"] = _rate(value["correct"], value["total"])
    projection: Counter[str] = Counter()
    for row in answerable:
        reason = row.get("diagnostics", {}).get("observable_mismatch")
        if reason in {"COLUMN_COUNT_MISMATCH", "COLUMN_ORDER_MISMATCH"}:
            projection[reason] += 1
    return {
        "experiment_id": config["experiment_id"],
        "failure_counts": dict(failure_counts),
        "scores": {
            "governed_task_success": {
                "correct": sum(bool(row["official_correct"]) for row in rows),
                "total": 90,
                "rate": _rate(sum(bool(row["official_correct"]) for row in rows), 90),
            },
            "answerable_accuracy": _score(rows, "ANSWERABLE"),
            "authority_accuracy": _score(rows, "AUTHORITY_BLOCKED"),
            "ambiguity_accuracy": _score(rows, "AMBIGUOUS"),
            "policy_accuracy": _score(rows, "POLICY_BLOCKED"),
        },
        "split_summary": split_summary,
        "domain_breakdown": domain,
        "decision_confusion_matrix": confusion,
        "decision_distribution": dict(
            Counter(row.get("model_decision") or "invalid" for row in rows)
        ),
        "execution_validity": {
            "sql_admission": len(admitted),
            "sql_executed": sum(row.get("base_execution") is not None for row in admitted),
            "full_suite_executed": sum(
                row.get("all_fixtures_passed") is not None for row in admitted
            ),
            "valid": sum(bool(row.get("all_fixtures_passed")) for row in admitted),
            "denominator": len(admitted),
            "rate": _rate(
                sum(bool(row.get("all_fixtures_passed")) for row in admitted), len(admitted)
            ),
        },
        "unauthorized_answer_rate": {
            "answered": sum(
                row.get("model_decision") == "ANSWER"
                for row in rows
                if row["gold_behavior"] == "AUTHORITY_BLOCKED"
            ),
            "total": 15,
            "rate": _rate(
                sum(
                    row.get("model_decision") == "ANSWER"
                    for row in rows
                    if row["gold_behavior"] == "AUTHORITY_BLOCKED"
                ),
                15,
            ),
        },
        "wrong_refusal_rate": {
            "wrong_refusals": sum(row.get("model_decision") != "ANSWER" for row in answerable),
            "total": 60,
            "rate": _rate(sum(row.get("model_decision") != "ANSWER" for row in answerable), 60),
        },
        "base_only_answerable": sum(row.get("base_passed") is True for row in answerable),
        "full_suite_answerable": sum(bool(row.get("official_correct")) for row in answerable),
        "base_only_false_positive_cases": base_only,
        "projection_diagnostics": dict(projection),
        "mechanism_tag_breakdown": dict(tag_breakdown),
        "difficulty_breakdown": dict(difficulty),
        "latency": {
            "successful_responses": len(latencies),
            "median_ms": statistics.median(latencies) if latencies else None,
            "p90_ms": _p90(latencies),
            "min_ms": min(latencies) if latencies else None,
            "max_ms": max(latencies) if latencies else None,
        },
        "token_usage": {
            key: usage_metric(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")
        },
        "provider_calls_attempted": len(calls),
        "provider_responses_received": sum(call["transport_status"] == "SUCCESS" for call in calls),
        "provider_schema_accepted_calls": sum(
            call["transport_status"] == "SUCCESS" for call in calls
        ),
        "transport_failures": failure_counts["TRANSPORT_FAILURE"],
        "schema_failures": failure_counts["PROVIDER_SCHEMA_FAILURE"],
        "quality_gates": quality,
        "reference_gate": gate,
        "raw_outputs_preserved": True,
        "all_cases_deterministically_classified": len(rows) == 90
        and all(row["official_category"] in FAILURE_CATEGORIES for row in rows),
        "first_model_response_acquired": any(
            call["transport_status"] == "SUCCESS" for call in calls
        ),
        "hashes": hashes,
    }


def _markdown(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    hashes: dict[str, Any],
) -> str:
    scores = summary["scores"]
    lines = [
        "# Decision-SQL Bench v0.2.0-dev",
        "",
        "## M39 — Luna / reasoning-none / single-call baseline",
        "",
        f"- Model: `{config['model']}`; provider: `{config['provider']}`; reasoning: `{config['reasoning']}`",
        f"- Temperature: `{config['temperature']}`; timeout: `{config['timeout_seconds']}s`; calls/case: `{config['calls_per_case']}`",
        "- Retries: transport `0`; semantic `0`; repair `false`; selector `false`; judge `false`; reflection `false`",
        f"- Benchmark hash: `{hashes['benchmark_content_hash']}`",
        f"- Prompt hash: `{hashes['governance_prompt_hash']}`",
        "",
        "M35R1 used benchmark 0.1.1-pilot / 30 cases. M39 uses benchmark 0.2.0-dev / 90 cases; percentages are not a direct model comparison.",
        "",
        "## Overall benchmark result",
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
    for key, label in (
        ("governed_success", "Governed Success"),
        ("answerable", "Answerable TSA"),
        ("authority", "Authority"),
        ("ambiguity", "Ambiguity"),
        ("policy", "Policy"),
    ):
        values = []
        for split in ("DEV", "CONFIRMATION"):
            value = summary["split_summary"][split][key]
            values.append(
                f"{value['correct']}/{value['total']} ({value.get('rate', _rate(value['correct'], value['total']))})"
            )
        overall = (
            scores["governed_task_success"]
            if key == "governed_success"
            else scores[
                {
                    "answerable": "answerable_accuracy",
                    "authority": "authority_accuracy",
                    "ambiguity": "ambiguity_accuracy",
                    "policy": "policy_accuracy",
                }[key]
            ]
        )
        values.append(f"{overall['correct']}/{overall['total']} ({overall['rate']})")
        lines.append(f"| {label} | {' | '.join(values)} |")
    lines += [
        "",
        "## Domain results",
        "",
        "| Domain | Governed | Answerable | Governance-negative |",
        "|---|---:|---:|---:|",
    ]
    for domain, value in summary["domain_breakdown"].items():
        lines.append(
            f"| {domain} | {value['governed']['correct']}/{value['governed']['total']} | {value['answerable']['correct']}/{value['answerable']['total']} | {value['governance_negative']['correct']}/{value['governance_negative']['total']} |"
        )
    lines += ["", "## Failure-category distribution", "", "| Category | Count |", "|---|---:|"]
    lines.extend(
        f"| {category} | {summary['failure_counts'][category]} |" for category in FAILURE_CATEGORIES
    )
    lines += [
        "",
        "## Counterfactual contribution",
        "",
        f"- Base-only answerable accuracy: {summary['base_only_answerable']}/60",
        f"- Full-suite answerable accuracy: {summary['full_suite_answerable']}/60",
        f"- Base-only false positives: {len(summary['base_only_false_positive_cases'])}",
    ]
    if summary["base_only_false_positive_cases"]:
        lines.append("- Cases: " + ", ".join(summary["base_only_false_positive_cases"]))
    lines += [
        "",
        "## Projection discipline",
        "",
        f"- Observable extra-column mismatches: {summary['projection_diagnostics'].get('COLUMN_COUNT_MISMATCH', 0)}",
        f"- Observable column-order mismatches: {summary['projection_diagnostics'].get('COLUMN_ORDER_MISMATCH', 0)}",
        "",
        "## Per-case results",
        "",
        "| # | case_id | database | split | gold | model decision | category | base | fixtures | latency ms |",
        "|---:|---|---|---|---|---|---|---|---|---:|",
    ]
    for row in rows:
        split = "DEV" if row["database_id"] in DEV_DATABASES else "CONFIRMATION"
        lines.append(
            f"| {row['case_index']} | {row['case_id']} | {row['database_id']} | {split} | {row['gold_behavior']} | {row.get('model_decision') or 'INVALID/NO_DECISION'} | {row['official_category']} | {row.get('base_status', '—')} | {row.get('fixture_status', '—')} | {row['latency_ms']:.1f} |"
        )
    lines += ["", "## Per-case failures", ""]
    for row in rows:
        if not row["official_correct"]:
            lines.append(
                f"- `{row['case_id']}` — `{row['official_category']}`, base `{row.get('base_status', '—')}`, first fixture `{row.get('first_failing_fixture') or '—'}`; observable reason `{row.get('diagnostics', {}).get('observable_mismatch', '—')}`"
            )
    lines += [
        "",
        "## Latency and usage",
        "",
        f"- Latency: `{json.dumps(summary['latency'], sort_keys=True)}`",
        f"- Token usage: `{json.dumps(summary['token_usage'], sort_keys=True)}`",
        "",
        "## Reproducibility",
        "",
        f"- Provider calls: `{summary['provider_calls_attempted']}`; responses: `{summary['provider_responses_received']}`",
        f"- Request/contract hashes: `{json.dumps(hashes, sort_keys=True)}`",
        "- Raw responses are immutable evidence; no chain-of-thought was requested or stored.",
    ]
    return "\n".join(lines) + "\n"


def run_m39() -> dict[str, Any]:
    config = _load_config()
    manifest = json.loads(M38_MANIFEST.read_text(encoding="utf-8"))
    if not M39_CONTRACT.exists() or not M39_LEDGER.exists():
        raise RuntimeError("M39_CONTRACT_MISMATCH:missing_pre_call_freeze")
    frozen_contract = json.loads(M39_CONTRACT.read_text(encoding="utf-8"))
    ledger = json.loads(M39_LEDGER.read_text(encoding="utf-8"))
    if frozen_contract.get("provider_calls") != 0 or ledger.get("provider_calls") != 0:
        raise RuntimeError("M39_CONTRACT_MISMATCH:pre_call_provider_calls")
    if frozen_contract.get("benchmark_content_hash") != manifest.get("benchmark_content_hash"):
        raise RuntimeError("M39_CONTRACT_MISMATCH:pre_call_benchmark_hash")
    rows_by_id = _load_rows(ledger)
    requests = _build_requests(ledger, rows_by_id)
    quality = _offline_gates(manifest, ledger, rows_by_id, requests)
    hashes = _contract_hashes(config, requests)
    if (
        hashes["benchmark_content_hash"] != manifest["benchmark_content_hash"]
        or hashes["case_order_hash"] != ledger["case_order_sha256"]
    ):
        raise RuntimeError("M39_CONTRACT_MISMATCH:contract_hash")
    expected_results, reference_gate = _reference_gate()
    old_reference = validate_references()
    new_reference = validate_new_references()
    mutation_gate = _mutation_gate(old_reference, new_reference)
    for path in (
        "m39_raw_responses.jsonl",
        "m39_parsed_submissions.jsonl",
        "m39_case_results.json",
        "m39_summary.json",
        "m39_summary.md",
        "m39_manifest.json",
        "m39_request_ledger.json",
    ):
        if (RESULT_ROOT / path).exists():
            raise RuntimeError(f"M39_ARTIFACT_EXISTS:{path}")
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    frozen_request_ledger = {
        "benchmark_version": "0.2.0-dev",
        "provider_calls": 0,
        "case_order": [request["case_id"] for request in requests],
        "case_order_sha256": hashes["case_order_hash"],
        "requests": [
            {
                "case_id": request["case_id"],
                "case_index": index,
                "database_id": request["database_id"],
                "question_sha256": sha256_text(request["question"]),
                "context_sha256": context_hash(request["database_id"]),
                "instruction_sha256": hashes["governance_prompt_hash"],
                "submission_schema_sha256": hashes["submission_schema_hash"],
                "full_request_sha256": request["request_sha256"],
                "request_bytes": request["request_bytes"],
            }
            for index, request in enumerate(requests, 1)
        ],
    }
    _dump(RESULT_ROOT / "m39_request_ledger.json", frozen_request_ledger)
    if not get_settings().llm_api_key:
        raise M39ProviderBlocked("M39_PROVIDER_BLOCKED:DECISION_SQL_LLM_API_KEY is not configured")
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
    started = _now()
    case_results: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    successful = 0
    first_response = False
    for index, request in enumerate(requests):
        frozen = frozen_request_ledger["requests"][index]
        rebuilt_text, _context, rebuilt_bytes = _request_text(rows_by_id[request["case_id"]][0])
        if (
            sha256_text(rebuilt_text) != frozen["full_request_sha256"]
            or rebuilt_bytes != frozen["request_bytes"]
        ):
            raise RuntimeError(f"M39_CONTRACT_DRIFT:request:{request['case_id']}")
        call_start = time.perf_counter()
        started_iso = _now()
        provider.consume_response_wire()
        payload: Any = None
        error: Exception | None = None
        try:
            payload = asyncio.run(
                provider.complete_json_schema(
                    operation="m39_governed_submission",
                    system_prompt=request["instructions"],
                    user_prompt=request["user_text"],
                    schema_name="decision_sql_m39_submission",
                    schema=submission_schema(),
                )
            )
            successful += 1
            first_response = True
        except Exception as exc:
            error = exc
        finished_iso = _now()
        latency_ms = (time.perf_counter() - call_start) * 1000
        capture = provider.consume_model_io()
        wire = provider.consume_response_wire()
        response_hash = sha256_bytes(wire) if wire is not None else None
        metadata = _provider_metadata(payload or {}, capture)
        call = {
            "case_id": request["case_id"],
            "case_index": index + 1,
            "database_id": request["database_id"],
            "split": "DEV" if request["database_id"] in DEV_DATABASES else "CONFIRMATION",
            "request_sha256": request["request_sha256"],
            "request_bytes": request["request_bytes"],
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
            else {
                "type": type(error).__name__,
                "message": str(error)[:240],
                **_error_detail(error),
            },
            "response_sha256": response_hash,
        }
        calls.append(call)
        _append_jsonl(
            RESULT_ROOT / "m39_raw_responses.jsonl",
            {
                "case_id": request["case_id"],
                "case_index": index + 1,
                "request_sha256": request["request_sha256"],
                "response_sha256": response_hash,
                "raw_response_bytes_base64": base64.b64encode(wire).decode("ascii")
                if wire is not None
                else None,
                "provider_metadata": metadata,
            },
        )
        case, truth = rows_by_id[request["case_id"]]
        row: dict[str, Any] = {
            "case_index": index + 1,
            "case_id": request["case_id"],
            "database_id": request["database_id"],
            "split": call["split"],
            "gold_behavior": case["task_type"],
            "expected_decision": EXPECTED_DECISION[case["task_type"]],
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
            "latency_ms": latency_ms,
            "usage": metadata["usage"],
            "diagnostics": {},
        }
        parsed_value = None
        if error is not None:
            category, global_failure = _classify_provider_error(error)
            row.update(
                schema_validation=category,
                official_category=category,
                diagnostics={"provider_error": str(error)[:240]},
            )
            _append_jsonl(
                RESULT_ROOT / "m39_parsed_submissions.jsonl",
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
                abort = (
                    "M39_ABORTED_CONTRACT_DEFECT"
                    if category == "PROVIDER_SCHEMA_FAILURE"
                    else "M39_PROVIDER_BLOCKED"
                )
                _dump(
                    RESULT_ROOT / "m39_abort.json",
                    {
                        "status": abort,
                        "reason": str(error),
                        "provider_calls_attempted": len(calls),
                        "responses_received": successful,
                    },
                )
                raise M39ProviderBlocked(f"{abort}:{type(error).__name__}:{str(error)[:240]}")
            continue
        content = getattr(capture, "raw_assistant_content_full", None)
        submission, parse_status, parse_detail, parsed_value = _parse(content, request["case_id"])
        row.update(
            parsed_submission=parsed_value,
            schema_validation=parse_status,
            case_id_matches=None
            if submission is None
            else submission.case_id == request["case_id"],
            submission_invariant_status="PASS" if parse_status == "PASS" else parse_status,
            diagnostics={"parse_detail": parse_detail} if parse_detail else {},
        )
        if submission is None:
            row["official_category"] = parse_status
        elif parse_status == "INVALID_SUBMISSION":
            row.update(
                official_category="INVALID_SUBMISSION",
                model_decision=submission.decision,
                sql_present=submission.sql is not None,
            )
        else:
            evaluated = _evaluate(case, truth, submission, expected_results)
            row.update(evaluated)
            if evaluated.get("base_execution"):
                row["base_status"] = "PASS" if evaluated["base_passed"] else "FAIL"
            if evaluated.get("all_fixtures_passed") is not None:
                row["fixture_status"] = "PASS" if evaluated["all_fixtures_passed"] else "FAIL"
            row["execution_status"] = (
                "PASS"
                if evaluated.get("all_fixtures_passed") is True
                else ("FAIL" if evaluated.get("all_fixtures_passed") is False else None)
            )
            row["model_decision"] = submission.decision
            row["sql_present"] = submission.sql is not None
        _append_jsonl(
            RESULT_ROOT / "m39_parsed_submissions.jsonl",
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
    if len(case_results) != 90 or not all(
        row["official_category"] in FAILURE_CATEGORIES for row in case_results
    ):
        raise RuntimeError("M39_ABORTED_CONTRACT_DEFECT:unclassified_or_missing_case")
    summary = _summary(case_results, rows_by_id, calls, config, hashes, quality, reference_gate)
    summary["mutation_gate"] = mutation_gate
    end = _now()
    m39_manifest = {
        "experiment_id": config["experiment_id"],
        "experiment_name": config["experiment_name"],
        "benchmark_version": config["benchmark_version"],
        "benchmark_content_hash": hashes["benchmark_content_hash"],
        "contract_source_commit": manifest.get("contract_source_commit", "af3cf78"),
        "execution_commit": _git_revision(),
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
        "reflection": config["reflection"],
        "database_count": 6,
        "case_count": 90,
        "task_distribution": {
            "ANSWERABLE": 60,
            "AUTHORITY_BLOCKED": 15,
            "AMBIGUOUS": 9,
            "POLICY_BLOCKED": 6,
        },
        "dev_databases": sorted(DEV_DATABASES),
        "confirmation_databases": sorted(CONFIRMATION_DATABASES),
        "governance_prompt_hash": hashes["governance_prompt_hash"],
        "submission_schema_hash": hashes["submission_schema_hash"],
        "context_hashes": hashes["context_hashes"],
        "case_order_hash": hashes["case_order_hash"],
        "serializer_hash": hashes["serializer_hash"],
        "evaluator_hash": hashes["evaluator_hash"],
        "validator_hash": hashes["validator_hash"],
        "provider_adapter_hash": hashes["provider_adapter_hash"],
        "provider_config_hash": hashes["provider_config_hash"],
        "experiment_config_hash": hashes["experiment_config_hash"],
        "postgresql_version": hashes["postgresql_version"],
        "run_start": started,
        "run_end": end,
        "provider_calls_attempted": len(calls),
        "provider_responses_received": successful,
        "provider_schema_accepted_calls": successful,
        "transport_failures": summary["transport_failures"],
        "schema_failures": summary["schema_failures"],
        "first_model_response_acquired": first_response,
        "raw_response_artifact": "experiments/results/m39/m39_raw_responses.jsonl",
        "parsed_submission_artifact": "experiments/results/m39/m39_parsed_submissions.jsonl",
        "reference_gate": reference_gate,
        "mutation_gate": mutation_gate,
    }
    _dump(RESULT_ROOT / "m39_case_results.json", case_results)
    _dump(RESULT_ROOT / "m39_summary.json", summary)
    (RESULT_ROOT / "m39_summary.md").write_text(
        _markdown(summary, case_results, config, hashes), encoding="utf-8"
    )
    _dump(RESULT_ROOT / "m39_manifest.json", m39_manifest)
    return {
        "status": "M39_COMPLETE",
        "manifest": m39_manifest,
        "summary": summary,
        "case_results": case_results,
    }


if __name__ == "__main__":
    try:
        result = run_m39()
    except M39ProviderBlocked as error:
        print(json.dumps({"status": str(error).split(":", 1)[0], "error": str(error)}, indent=2))
        raise SystemExit(2) from None
    print(
        json.dumps(
            {
                "status": result["status"],
                "provider_calls_attempted": result["manifest"]["provider_calls_attempted"],
                "summary": str(RESULT_ROOT / "m39_summary.md"),
            },
            indent=2,
        )
    )
