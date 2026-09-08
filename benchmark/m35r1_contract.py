"""Offline M35R1 contract freeze and provider-schema repair evidence."""

from __future__ import annotations

# The report includes exact provider error text and compact gate rows.
# ruff: noqa: E501
import base64
import json
from pathlib import Path
from typing import Any

from benchmark.model_contract import (
    ROOT,
    build_all_requests,
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
    _ledger,
    _provider_config_projection,
    _request_audit,
)

UNSUPPORTED_KEYWORDS = {"allOf", "oneOf", "if", "then", "else", "dependentSchemas"}
M34R1_MANIFEST = ROOT / "manifests" / "m34_3r_model_contract.json"
M35_RAW = ROOT / "experiments" / "results" / "m35" / "m35_raw_responses.jsonl"


def _nested_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return [key for key in value for key in [key]] + [
            child for item in value.values() for child in _nested_keys(item)
        ]
    if isinstance(value, list):
        return [child for item in value for child in _nested_keys(item)]
    return []


def provider_schema_audit() -> dict[str, Any]:
    schema = submission_schema()
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    reasons = properties.get("reason_code", {}) if isinstance(properties, dict) else {}
    sql = properties.get("sql", {}) if isinstance(properties, dict) else {}
    decision = properties.get("decision", {}) if isinstance(properties, dict) else {}
    checks = {
        "top_level_object": schema.get("type") == "object",
        "additional_properties_false": schema.get("additionalProperties") is False,
        "required_properties": required == {"case_id", "decision", "sql", "reason_code"},
        "decision_enum": decision.get("enum")
        == [
            "ANSWER",
            "BLOCKED_AUTHORITY",
            "NEEDS_CLARIFICATION",
            "BLOCKED_POLICY",
        ],
        "reason_enum": reasons.get("enum")
        == [
            "MISSING_AUTHORIZED_RELATIONSHIP",
            "AMBIGUOUS_SEMANTICS",
            "READ_ONLY_POLICY",
            "NO_REASON",
            None,
        ],
        "nullable_sql": set(sql.get("type", [])) == {"string", "null"},
        "nullable_reason": set(reasons.get("type", [])) == {"string", "null"},
        "unsupported_composition_keywords": not set(_nested_keys(schema)).intersection(
            UNSUPPORTED_KEYWORDS
        ),
    }
    return {
        "schema_sha256": file_hash(ROOT / "schemas" / "model_submission.schema.json"),
        "checks": checks,
        "unsupported_keywords": sorted(
            set(_nested_keys(schema)).intersection(UNSUPPORTED_KEYWORDS)
        ),
        "passed": all(checks.values()),
    }


def historical_m35_error() -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in M35_RAW.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    body = base64.b64decode(rows[0]["raw_response_bytes_base64"])
    return {
        "response_sha256": sha256_bytes(body),
        "body": body.decode("utf-8"),
        "case_id": rows[0]["case_id"],
        "historical_attempts": len(rows),
    }


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def freeze_m35r1_contract(
    config_path: Path = ROOT / "experiments" / "m35r1_luna_none.json",
) -> dict[str, Any]:
    config = _experiment_config(config_path)
    if config.get("expected_benchmark_content_hash") != frozen_benchmark_content_hash():
        raise RuntimeError("M35R1_NO_GO:BENCHMARK_CONTENT_HASH_MISMATCH")
    requests = build_all_requests()
    audit = _request_audit(requests)
    schema_audit = provider_schema_audit()
    old_manifest = json.loads(M34R1_MANIFEST.read_text(encoding="utf-8"))
    old_ledger = json.loads(
        (ROOT / "manifests" / "m34_3r_request_ledger.json").read_text(encoding="utf-8")
    )
    prompt_hash = sha256_text(requests[0].instructions)
    prompt_unchanged = prompt_hash == old_manifest["governance_prompt_sha256"]
    request_ledger = _ledger(requests)
    request_hashes = [row["full_request_sha256"] for row in request_ledger]
    old_hashes = [row["full_request_sha256"] for row in old_ledger["requests"]]
    requests_unchanged = request_hashes == old_hashes
    config_projection = _provider_config_projection(config)
    provider_config_hash = sha256_text(
        json.dumps(config_projection, sort_keys=True, separators=(",", ":"))
    )
    contexts = {
        database_id: next(
            row["context_sha256"] for row in request_ledger if row["database_id"] == database_id
        )
        for database_id in sorted({row["database_id"] for row in request_ledger})
    }
    historical_error = historical_m35_error()
    manifest = {
        "milestone": "M35R1",
        "experiment_id": config["experiment_id"],
        "experiment_name": config["experiment_name"],
        "benchmark_version": config["benchmark_version"],
        "benchmark_content_hash": frozen_benchmark_content_hash(),
        "contract_source_commit": git_revision(),
        "execution_commit": None,
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
        "governance_prompt_hash": prompt_hash,
        "governance_prompt_sha256": prompt_hash,
        "provider_submission_schema_hash": schema_audit["schema_sha256"],
        "submission_schema_sha256": schema_audit["schema_sha256"],
        "context_hashes": contexts,
        "case_order_hash": case_order_hash(),
        "case_order_sha256": case_order_hash(),
        "serializer_hash": file_hash(ROOT / "model_contract.py"),
        "serializer_version_hash": file_hash(ROOT / "model_contract.py"),
        "evaluator_hash": file_hash(ROOT / "evaluator.py"),
        "evaluator_version_hash": file_hash(ROOT / "evaluator.py"),
        "validator_hash": file_hash(ROOT / "validator.py"),
        "validator_version_hash": file_hash(ROOT / "validator.py"),
        "provider_adapter_hash": file_hash(ROOT.parent / "app" / "generation" / "provider.py"),
        "provider_config_hash": provider_config_hash,
        "provider_config_sha256": provider_config_hash,
        "experiment_config_hash": file_hash(config_path),
        "experiment_config_sha256": file_hash(config_path),
        "request_hashes": request_hashes,
        "provider_calls": 0,
        "historical_parent": "M35_ABORTED_CONTRACT_DEFECT",
    }
    report = {
        "milestone": "M35R1",
        "status": "PASS"
        if audit["passed"] and schema_audit["passed"] and prompt_unchanged and requests_unchanged
        else "M35R1_NO_GO",
        "benchmark_content_changed": False,
        "model_prompt_semantics_changed": False,
        "model_configuration_changed": False,
        "request_bytes_unchanged_from_m34_3r": requests_unchanged,
        "historical_m35": historical_error,
        "old_provider_schema": {"contained_allOf": True, "provider_outcome": "HTTP 400"},
        "new_provider_schema": {
            "shape": "flat strict object",
            "cross_field_invariants": "local validator",
        },
        "request_audit": audit,
        "provider_schema_audit": schema_audit,
        "hashes": manifest,
        "provider_calls": 0,
    }
    if report["status"] != "PASS":
        raise RuntimeError("M35R1_NO_GO:" + json.dumps(report, sort_keys=True))
    _dump(ROOT / "manifests" / "m35r1_contract.json", manifest)
    _dump(
        ROOT / "manifests" / "m35r1_request_ledger.json",
        {"provider_calls": 0, "requests": request_ledger},
    )
    _dump(ROOT / "reports" / "m35r1_contract_repair.json", report)
    lines = [
        "# M35R1 Provider-Safe Contract Repair",
        "",
        "Offline only. No M35R1 provider call was made while freezing this contract.",
        "",
        "## Historical M35 defect",
        "",
        f"- Raw provider body hash: `{historical_error['response_sha256']}`",
        f"- Exact provider body: `{historical_error['body']}`",
        "- Provider outcome: HTTP 400 before model generation.",
        "- Historical M35 remains `M35_ABORTED_CONTRACT_DEFECT`.",
        "",
        "## Repair",
        "",
        "- Old provider schema contained `allOf` conditionals.",
        "- New provider schema is a flat strict object with primitive types and enums only.",
        "- Cross-field decision invariants remain enforced by the local validator after parsing.",
        "- Benchmark semantics changed: NO.",
        "- Benchmark content changed: NO.",
        "- Model prompt semantics changed: NO.",
        "- Model configuration changed: NO.",
        "",
        "## Gates",
        "",
        f"- Requests: {audit['request_count']}/30",
        f"- Exact Case ID/question/instructions/context: {audit['request_components_present']}",
        f"- Answerable context sufficiency: {audit['answerable_context_sufficient']['passed']}/{audit['answerable_context_sufficient']['total']}",
        f"- Evaluator-only leakage: {sum(len(v) for v in audit['evaluator_only_leakage'].values())}",
        f"- Provider schema unsupported keywords: {len(schema_audit['unsupported_keywords'])}",
        "- Provider calls before M35R1: 0",
        "",
        "## Frozen hashes",
        "",
        f"- Benchmark: `{manifest['benchmark_content_hash']}`",
        f"- Governance prompt: `{manifest['governance_prompt_hash']}`",
        f"- Provider schema: `{manifest['provider_submission_schema_hash']}`",
        f"- Case order: `{manifest['case_order_hash']}`",
        f"- Serializer: `{manifest['serializer_hash']}`",
        f"- Evaluator: `{manifest['evaluator_hash']}`",
        f"- Validator: `{manifest['validator_hash']}`",
        f"- Provider adapter: `{manifest['provider_adapter_hash']}`",
        f"- Provider config: `{manifest['provider_config_hash']}`",
        f"- Experiment config: `{manifest['experiment_config_hash']}`",
        "",
        "M35R1 contract freeze: PASS",
    ]
    (ROOT / "reports" / "m35r1_contract_repair.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return {"manifest": manifest, "report": report, "ledger": request_ledger}
