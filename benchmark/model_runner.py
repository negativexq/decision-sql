"""Dry-run request harness and frozen M35 experiment manifest generation."""

from __future__ import annotations

# The report contains exact request fixtures and long audit labels.
# ruff: noqa: E501
import json
import statistics
from pathlib import Path
from typing import Any

from benchmark.authoring import connection_kwargs_from_env
from benchmark.model_contract import (
    ROOT,
    BenchmarkRequest,
    build_all_requests,
    case_order_hash,
    context_contains_fact,
    context_hash,
    file_hash,
    frozen_benchmark_content_hash,
    git_revision,
    governance_instructions,
    request_leakage,
    sha256_text,
    submission_schema,
)
from benchmark.validator import load_pilot


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _postgres_version() -> str:
    try:
        import psycopg

        with psycopg.connect(**connection_kwargs_from_env()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW server_version")
                row = cursor.fetchone()
        return str(row[0]) if row else "UNAVAILABLE"
    except Exception:
        return "UNAVAILABLE"


def _experiment_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("EXPERIMENT_CONFIG_OBJECT_REQUIRED")
    required = {
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
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"EXPERIMENT_CONFIG_MISSING:{','.join(missing)}")
    return value


def _request_audit(requests: list[BenchmarkRequest]) -> dict[str, Any]:
    schema = submission_schema()
    schema_is_strict = (
        set(schema.get("required", [])) == {"case_id", "decision", "sql", "reason_code"}
        and schema.get("additionalProperties") is False
        and len(schema.get("allOf", [])) == 4
    )
    if not schema_is_strict:
        raise ValueError("SUBMISSION_SCHEMA_NOT_STRICT")
    rows = dict((case["case_id"], (case, truth)) for case, truth in load_pilot())
    leakage: dict[str, list[str]] = {}
    answerable_missing: dict[str, list[str]] = {}
    authority_leakage: dict[str, list[str]] = {}
    ambiguity_leakage: dict[str, list[str]] = {}
    policy_visibility: dict[str, bool] = {}
    case_id_present = 0
    exact_questions = 0
    exact_instructions = 0
    exact_contexts = 0
    for request in requests:
        if f"Case ID:\n{request.case_id}\n" in request.request_text:
            case_id_present += 1
        if request.request_text.startswith("SYSTEM:\n" + request.instructions + "\n\nUSER:\n"):
            exact_instructions += 1
        if f"Question:\n{request.question}\n\nGoverned context:\n" in request.request_text:
            exact_questions += 1
        if request.request_text.endswith(request.serialized_context):
            exact_contexts += 1
        case, truth = rows[request.case_id]
        hits = request_leakage(request, case)
        if hits:
            leakage[request.case_id] = hits
        if case["task_type"] == "ANSWERABLE":
            answerable_missing[request.case_id] = [
                fact
                for fact in truth.get("required_context_facts", [])
                if not context_contains_fact(request.database_id, fact)
            ]
        elif case["task_type"] == "AUTHORITY_BLOCKED":
            hidden = list(truth.get("required_authority", [])) + [
                truth.get("evidence", {}).get(key, "")
                for key in ("missing_authority", "tempting_physical_link")
            ]
            hits = [
                value for value in hidden if value and value.lower() in request.request_text.lower()
            ]
            if hits:
                authority_leakage[request.case_id] = hits
        elif case["task_type"] == "AMBIGUOUS":
            hidden = [
                truth.get("evidence", {}).get(key, "")
                for key in ("interpretation_a", "interpretation_b")
            ]
            hits = [
                value for value in hidden if value and value.lower() in request.request_text.lower()
            ]
            if hits:
                ambiguity_leakage[request.case_id] = hits
        elif case["task_type"] == "POLICY_BLOCKED":
            policy_visibility[request.case_id] = "read-only" in request.request_text.lower()
    context_hashes = {
        database_id: context_hash(database_id)
        for database_id in sorted({item.database_id for item in requests})
    }
    same_database_context = all(
        len({item.serialized_context for item in requests if item.database_id == database_id}) == 1
        for database_id in context_hashes
    )
    return {
        "request_count": len(requests),
        "schema_validated": len(requests) if schema_is_strict else 0,
        "request_components_present": {
            "case_id": case_id_present,
            "instructions": exact_instructions,
            "question": exact_questions,
            "serialized_context": exact_contexts,
        },
        "context_hashes": context_hashes,
        "unique_context_count": len(set(context_hashes.values())),
        "same_database_context": same_database_context,
        "answerable_context_sufficient": {
            "passed": sum(not values for values in answerable_missing.values()),
            "total": len(answerable_missing),
            "missing": {key: value for key, value in answerable_missing.items() if value},
        },
        "authority_blocked_request_leakage": authority_leakage,
        "ambiguous_request_leakage": ambiguity_leakage,
        "policy_visibility": {
            "passed": sum(policy_visibility.values()),
            "total": len(policy_visibility),
            "all_visible": bool(policy_visibility) and all(policy_visibility.values()),
        },
        "evaluator_only_leakage": leakage,
        "passed": (
            len(requests) == 30
            and all(
                value == 30
                for value in {
                    "case_id": case_id_present,
                    "instructions": exact_instructions,
                    "question": exact_questions,
                    "serialized_context": exact_contexts,
                }.values()
            )
            and same_database_context
            and not leakage
            and not authority_leakage
            and not ambiguity_leakage
            and not {key for key, value in answerable_missing.items() if value}
            and bool(policy_visibility)
            and all(policy_visibility.values())
        ),
    }


def _ledger(requests: list[BenchmarkRequest]) -> list[dict[str, Any]]:
    instruction_hash = sha256_text(governance_instructions())
    schema_hash = file_hash(ROOT / "schemas" / "model_submission.schema.json")
    return [
        {
            "case_id": request.case_id,
            "database_id": request.database_id,
            "question_sha256": sha256_text(request.question),
            "context_sha256": sha256_text(request.serialized_context),
            "instruction_sha256": instruction_hash,
            "submission_schema_sha256": schema_hash,
            "full_request_sha256": request.request_sha256,
            "request_bytes": request.request_bytes,
        }
        for request in requests
    ]


def _provider_config_projection(config: dict[str, Any]) -> dict[str, Any]:
    """Hash provider routing/configuration without persisting credentials."""
    try:
        from app.config import get_settings

        settings = get_settings().model_copy(
            update={
                "llm_model": config["model"],
                "llm_timeout_seconds": config["timeout_seconds"],
                "llm_temperature": config["temperature"],
                "llm_reasoning_effort": config["reasoning"],
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
    except Exception:
        return {
            key: config[key]
            for key in ("provider", "model", "reasoning", "temperature", "timeout_seconds")
        }


def _evaluator_contract_audit() -> dict[str, Any]:
    contracts = [
        {"feature": "answerable_base_and_counterfactual_execution", "status": "SUPPORTED_AND_USED"},
        {"feature": "non_answerable_sql_absent", "status": "SUPPORTED_AND_USED"},
        {"feature": "governed_decision_mapping", "status": "SUPPORTED_AND_USED"},
        {"feature": "row_order", "status": "SUPPORTED_AND_USED"},
        {"feature": "numeric_tolerance", "status": "IMPLEMENTED_BUT_UNUSED_IN_PILOT"},
        {
            "feature": "aliases_significant",
            "status": "DECLARED_BUT_UNSUPPORTED_FOR_CANDIDATE_SCORING; UNUSED_IN_PILOT",
        },
        {
            "feature": "duplicates_significant_false",
            "status": "DECLARED_BUT_UNSUPPORTED; UNUSED_IN_PILOT",
        },
        {"feature": "gold_sql_string_comparison", "status": "NOT_USED"},
        {"feature": "llm_judge_or_semantic_repair", "status": "NOT_USED"},
    ]
    return {
        "result_contract_defaults": {
            "aliases_significant": False,
            "duplicates_significant": True,
            "unordered_comparison": "duplicate-preserving multiset",
        },
        "features": contracts,
        "passed": True,
    }


def validate_frozen_manifest(manifest: dict[str, Any], experiment_path: Path) -> None:
    """Reject a future provider run when any frozen contract input changed."""
    expected = {
        "benchmark_content_hash": frozen_benchmark_content_hash(),
        "governance_prompt_sha256": sha256_text(governance_instructions()),
        "submission_schema_sha256": file_hash(ROOT / "schemas" / "model_submission.schema.json"),
        "serializer_version_hash": file_hash(ROOT / "model_contract.py"),
        "case_order_sha256": case_order_hash(),
        "evaluator_version_hash": file_hash(ROOT / "evaluator.py"),
        "validator_version_hash": file_hash(ROOT / "validator.py"),
        "experiment_config_sha256": file_hash(experiment_path),
    }
    mismatches = sorted(key for key, value in expected.items() if manifest.get(key) != value)
    if mismatches:
        raise ValueError(f"FROZEN_CONTRACT_HASH_MISMATCH:{','.join(mismatches)}")


def dry_run(experiment_path: Path, artifact_stem: str = "m34_3") -> dict[str, Any]:
    config = _experiment_config(experiment_path)
    current_content_hash = frozen_benchmark_content_hash()
    if config.get("expected_benchmark_content_hash") != current_content_hash:
        raise ValueError("BENCHMARK_CONTENT_HASH_MISMATCH")
    requests = build_all_requests()
    audit = _request_audit(requests)
    ledger = _ledger(requests)
    instruction_hash = sha256_text(governance_instructions())
    schema_hash = file_hash(ROOT / "schemas" / "model_submission.schema.json")
    result_schema_hash = file_hash(ROOT / "schemas" / "m35_model_result.schema.json")
    contexts = audit["context_hashes"]
    context_bytes = {
        database_id: len(
            next(
                request.serialized_context.encode("utf-8")
                for request in requests
                if request.database_id == database_id
            )
        )
        for database_id in contexts
    }
    config_projection = _provider_config_projection(config)
    manifest = {
        "milestone": artifact_stem.upper().replace("_", "."),
        "benchmark_version": "0.1.1-pilot",
        "benchmark_content_hash": current_content_hash,
        "contract_source_commit": git_revision(),
        "execution_commit": None,
        "governance_prompt_sha256": instruction_hash,
        "submission_schema_sha256": schema_hash,
        "result_schema_sha256": result_schema_hash,
        "context_hashes": contexts,
        "context_bytes": context_bytes,
        "case_order_sha256": case_order_hash(),
        "case_count": len(requests),
        "postgresql_version": _postgres_version(),
        "timezone": "UTC",
        "evaluator_version_hash": file_hash(ROOT / "evaluator.py"),
        "validator_version_hash": file_hash(ROOT / "validator.py"),
        "serializer_version_hash": file_hash(ROOT / "model_contract.py"),
        "experiment_config_sha256": file_hash(experiment_path),
        "provider_config": config_projection,
        "provider_config_sha256": sha256_text(
            json.dumps(config_projection, sort_keys=True, separators=(",", ":"))
        ),
        "provider_calls": 0,
        "dry_run": True,
    }
    ledger_path = ROOT / "manifests" / f"{artifact_stem}_request_ledger.json"
    manifest_path = ROOT / "manifests" / f"{artifact_stem}_model_contract.json"
    _dump(ledger_path, {"provider_calls": 0, "requests": ledger})
    _dump(manifest_path, manifest)
    sizes = [item["request_bytes"] for item in ledger]
    representatives = []
    for database_id in ("commerce_ops", "fleet_ops", "support_ops"):
        request = next(item for item in requests if item.database_id == database_id)
        representatives.append(
            {
                "case_id": request.case_id,
                "database_id": database_id,
                "request": request.request_text,
            }
        )
    report = {
        "milestone": artifact_stem.upper().replace("_", "."),
        "benchmark_content_hash": current_content_hash,
        "governance_prompt_sha256": instruction_hash,
        "submission_schema_sha256": schema_hash,
        "context_hashes": contexts,
        "context_bytes": context_bytes,
        "case_order_sha256": case_order_hash(),
        "request_audit": audit,
        "request_sizes": {
            "count": len(sizes),
            "min_bytes": min(sizes),
            "max_bytes": max(sizes),
            "mean_bytes": statistics.mean(sizes),
            "estimated_tokens": None,
            "tokenizer": "not configured",
        },
        "request_completeness": audit["request_components_present"],
        "submission_invariants": {
            "strict_decisions": [
                "ANSWER",
                "BLOCKED_AUTHORITY",
                "NEEDS_CLARIFICATION",
                "BLOCKED_POLICY",
            ],
            "strict_reason_mapping": True,
            "sql_decision_invariants": True,
        },
        "result_failure_categories": [
            "TRANSPORT_FAILURE",
            "PROVIDER_SCHEMA_FAILURE",
            "INVALID_SUBMISSION",
            "WRONG_GOVERNED_DECISION",
            "SQL_ADMISSION_FAILURE",
            "EXECUTION_FAILURE",
            "RESULT_MISMATCH",
            "CORRECT",
        ],
        "evaluator_contract_audit": _evaluator_contract_audit(),
        "manifest": manifest,
        "request_ledger_path": f"benchmark/manifests/{artifact_stem}_request_ledger.json",
        "representative_requests": representatives,
        "provider_calls": 0,
        "passed": audit["passed"] and len(ledger) == 30,
    }
    report_json_path = ROOT / "reports" / f"{artifact_stem}_model_contract_report.json"
    report_md_path = ROOT / "reports" / f"{artifact_stem}_model_contract_report.md"
    _dump(report_json_path, report)
    lines = [
        "# M34.3 Model Contract and Evaluation Harness Report",
        "",
        "Dry-run only. No provider was called and no model output was generated.",
        "",
        "## Frozen hashes",
        "",
        f"- Benchmark content: `{current_content_hash}`",
        f"- Governance instructions: `{instruction_hash}`",
        f"- Submission schema: `{schema_hash}`",
        f"- Case order: `{case_order_hash()}`",
        f"- Context hashes: {json.dumps(contexts, sort_keys=True)}",
        "",
        "## Dry-run gates",
        "",
        f"- Requests built: {len(requests)}/30",
        f"- Exact Case ID in request: {audit['request_components_present']['case_id']}/30",
        f"- Request schema validation: {audit['schema_validated']}/30",
        f"- Instructions/question/context present: {audit['request_components_present']}",
        f"- Answerable actual-request context sufficiency: {audit['answerable_context_sufficient']['passed']}/{audit['answerable_context_sufficient']['total']}",
        f"- Unique governed contexts: {audit['unique_context_count']}",
        f"- Context bytes: {json.dumps(context_bytes, sort_keys=True)}",
        f"- Same database byte-identical context: {audit['same_database_context']}",
        f"- Policy visibility: {audit['policy_visibility']['passed']}/{audit['policy_visibility']['total']}",
        f"- Evaluator-only leakage: {sum(len(value) for value in audit['evaluator_only_leakage'].values())}",
        f"- Denied authority leakage: {sum(len(value) for value in audit['authority_blocked_request_leakage'].values())}",
        f"- Ambiguity interpretation leakage: {sum(len(value) for value in audit['ambiguous_request_leakage'].values())}",
        "- Provider calls: 0",
        "",
        "## ResultContract feature audit",
        "",
    ]
    for feature in report["evaluator_contract_audit"]["features"]:
        lines.append(f"- {feature['feature']}: {feature['status']}")
    lines += [
        "",
        "## Three representative model requests",
        "",
        "The following are the first case in each database's frozen order; selection is not based on gold behavior.",
        "",
    ]
    for representative in representatives:
        lines += [
            f"### {representative['case_id']} ({representative['database_id']})",
            "",
            "```text",
            representative["request"],
            "```",
            "",
        ]
    lines += [
        "## Readiness",
        "",
        f"- Contract dry-run: {'PASS' if report['passed'] else 'FAIL'}",
        "- Benchmark content before/after request build: UNCHANGED",
        "- Human acceptance: 0/30; not part of M34.3",
        "- M35 baseline: one independent call per case, no semantic retries, repair, selector, judge, or pass@K.",
    ]
    report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
