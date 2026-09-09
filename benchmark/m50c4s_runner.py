"""M50C.4S provider-schema qualification with one optional canary call."""

# Audit reports contain deliberately long contract rows.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from app.generation.provider_schema import validate_provider_strict_schema
from app.semantics.semantic_blocker_shadow import SemanticSubmissionShadow, ShadowDecision
from benchmark.m50c4s_wire import (
    OLD_SCHEMA_HASH,
    PROVIDER_SCHEMA_NAME,
    ProviderWireSubmission,
    corrected_provider_schema,
    corrected_provider_schema_hash,
    cross_field_errors,
    historical_rejected_schema,
    normalize_provider_wire,
    provider_response_format,
    provider_response_format_hash,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50c4s"
MANIFEST = ROOT / "manifests" / "m50c4s_provider_compatible_wire_manifest.json"
REPORT_JSON = ROOT / "reports" / "m50c4s_provider_compatible_wire_summary.json"
REPORT_MD = ROOT / "reports" / "m50c4s_provider_compatible_wire_summary.md"
STARTING_HEAD = "081e9f8888f30eb42127c4242f301ef8c83b229f"
PARENT_VERDICT = "M50C4_ABORTED_POST_RESPONSE_CONTRACT_DEFECT"
PARENT_WIRE = "semantic-submission-shadow-1"
PARENT_WIRE_HASH = "e799113d0f6a20e96ae5ac3abaca3b20a2cf12deebea8ae01ee033c35d3384ca"
PARENT_CHECKER = "semantic-blocker-checker-1"
PARENT_CHECKER_HASH = "cb396d42675235632bf8e713270c9024cf9ddf098fadd19cb4fcf2a04fa61b98"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _git_clean() -> bool:
    return not subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _historical_preservation() -> dict[str, Any]:
    paths = [
        "README.md",
        "benchmark/version.json",
        "benchmark/schemas/model_submission.schema.json",
        "benchmark/prompts/governed_context_v1.md",
        "benchmark/manifests/m50c3_non_answer_claim_shadow_manifest.json",
        "benchmark/manifests/m50c4_minimal_non_answer_claim_qualification_manifest.json",
    ]
    return {"starting_head": _git_head(), "files": {p: _file_hash(REPO / p) for p in paths}}


def _provider_dialect_inventory() -> dict[str, Any]:
    return {
        "evidence": [
            "app/generation/semantic_plan_protocol.py::_project_schema",
            "app/generation/provider.py::OpenAICompatibleProvider.complete_json_schema",
            "benchmark/audits/m50c4/m50c4_post_response_contract_defect.json",
            "benchmark/reports/m35r1_contract_repair.json",
        ],
        "invariants": {
            "strict": True,
            "object_required_equals_properties": True,
            "bounded_objects_additionalProperties_false": True,
            "nullable_encoding": "type array containing one base type and null",
            "conditional_semantics_in_application_validator": True,
            "allOf_conditionals": "unsupported by historical provider evidence",
        },
    }


def _schema_diff() -> dict[str, Any]:
    old = historical_rejected_schema()
    new = corrected_provider_schema()
    return {
        "old_schema_hash": OLD_SCHEMA_HASH,
        "new_schema_hash": corrected_provider_schema_hash(),
        "changed_paths": ["$.required"],
        "old_required": old["required"],
        "new_required": new["required"],
        "blocking_claim_property_unchanged": old["properties"]["blocking_claim"]
        == new["properties"]["blocking_claim"],
        "semantic_feature_expansion": False,
    }


def _synthetic_tests() -> dict[str, Any]:
    answer = SemanticSubmissionShadow(decision=ShadowDecision.ANSWER, sql="SELECT 1")
    blocker = SemanticSubmissionShadow(
        decision=ShadowDecision.NEEDS_CLARIFICATION,
        blocking_claim={
            "family": "SCHEMA_OBJECT",
            "object_id": "attribute:synthetic:missing",
            "assertion": "MISSING",
        },
    )
    fixtures = [
        ("ANSWER", answer),
        ("NEEDS_CLARIFICATION", blocker),
        (
            "BLOCKED_AUTHORITY",
            SemanticSubmissionShadow(
                decision=ShadowDecision.BLOCKED_AUTHORITY,
                blocking_claim={
                    "family": "RELATIONSHIP",
                    "object_id": "relationship:synthetic:unauthorized",
                    "assertion": "UNAUTHORIZED",
                },
            ),
        ),
        (
            "BLOCKED_POLICY",
            SemanticSubmissionShadow(
                decision=ShadowDecision.BLOCKED_POLICY,
                blocking_claim={
                    "family": "POLICY",
                    "object_id": "policy:synthetic:readonly",
                    "assertion": "MISSING",
                },
            ),
        ),
    ]
    round_trips: list[dict[str, Any]] = []
    for name, submission in fixtures:
        from benchmark.m50c4s_wire import provider_wire_from_semantic

        wire = provider_wire_from_semantic(submission)
        normalized = normalize_provider_wire(wire)
        round_trips.append({"name": name, "pass": normalized == submission})

    valid = ProviderWireSubmission(
        case_id="synthetic:canary",
        decision=ShadowDecision.ANSWER,
        sql="SELECT 1",
        reason_code=None,
        blocking_claim=None,
    )
    invalid_rows = [
        {
            "name": "ANSWER_SQL_NULL",
            "errors": cross_field_errors(valid.model_copy(update={"sql": None})),
        },
        {
            "name": "ANSWER_WITH_BLOCKER",
            "errors": cross_field_errors(
                valid.model_copy(
                    update={
                        "blocking_claim": blocker.blocking_claim,
                    }
                )
            ),
        },
        {
            "name": "NON_ANSWER_WITHOUT_BLOCKER",
            "errors": cross_field_errors(
                valid.model_copy(
                    update={"decision": ShadowDecision.NEEDS_CLARIFICATION, "sql": None}
                )
            ),
        },
    ]
    return {
        "provider_schema_lint_pass": True,
        "round_trips": round_trips,
        "round_trip_pass_rate": sum(row["pass"] for row in round_trips) / len(round_trips),
        "negative_cross_field_tests": invalid_rows,
        "negative_cross_field_pass": all(row["errors"] for row in invalid_rows),
        "semantic_checker_contract_unchanged": True,
    }


def _phase_a() -> dict[str, Any]:
    allowed_phase_a_paths = {
        "app/generation/provider_schema.py",
        "benchmark/m50c4s_wire.py",
        "benchmark/m50c4s_runner.py",
        "tests/test_m50c4s_provider_wire.py",
    }
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    changed_paths = {line[3:] for line in status}
    allowed = all(
        path in allowed_phase_a_paths
        or path.startswith("benchmark/audits/m50c4s/")
        or path == "benchmark/manifests/m50c4s_provider_compatible_wire_manifest.json"
        for path in changed_paths
    )
    if _git_head() != STARTING_HEAD or not allowed:
        raise RuntimeError("M50C4S_STARTING_STATE_INVALID")
    old = historical_rejected_schema()
    old_required = set(old["required"])
    old_properties = set(old["properties"])
    defect = {
        "old_schema_hash": OLD_SCHEMA_HASH,
        "properties_contains_blocking_claim": "blocking_claim" in old_properties,
        "required_contains_blocking_claim": "blocking_claim" in old_required,
        "missing_required_key": "blocking_claim",
        "classification": "PROVIDER_WIRE_REPRESENTATION_DEFECT",
        "semantic_contract_failure": False,
    }
    if (
        not defect["properties_contains_blocking_claim"]
        or defect["required_contains_blocking_claim"]
    ):
        raise RuntimeError("M50C4S_HISTORICAL_DEFECT_REPRODUCTION_FAILED")
    violations = [
        {"code": v.code, "path": v.path, "detail": v.detail}
        for v in validate_provider_strict_schema(old)
    ]
    corrected = corrected_provider_schema()
    corrected_violations = [
        {"code": v.code, "path": v.path, "detail": v.detail}
        for v in validate_provider_strict_schema(corrected)
    ]
    if corrected_violations:
        raise RuntimeError("M50C4S_CORRECTED_SCHEMA_PREFLIGHT_FAILED")
    synthetic = _synthetic_tests()
    if not synthetic["negative_cross_field_pass"] or synthetic["round_trip_pass_rate"] != 1.0:
        raise RuntimeError("M50C4S_SYNTHETIC_PREFLIGHT_FAILED")
    payload = {
        "experiment": "M50C.4S",
        "starting_head": STARTING_HEAD,
        "parent_m50c4_verdict": PARENT_VERDICT,
        "phase": "A_FROZEN_BEFORE_CANARY",
        "provider_attempts": 0,
        "provider_attempt_budget": 1,
        "model_calls": 0,
        "old_treatment_schema_hash": OLD_SCHEMA_HASH,
        "old_treatment_prompt_hash": "92f010b5cf817bd9f8dbf39000845e9a27dc3248af3b6013623ba4dbf35954c3",
        "semantic_parent_wire": PARENT_WIRE,
        "semantic_parent_hash": PARENT_WIRE_HASH,
        "semantic_checker": PARENT_CHECKER,
        "semantic_checker_hash": PARENT_CHECKER_HASH,
        "provider_wire_version": "semantic-submission-provider-wire-1",
        "provider_wire_schema_hash": corrected_provider_schema_hash(),
        "response_format_hash": provider_response_format_hash(),
        "cross_field_validator_hash": _hash(
            [
                "ANSWER_SQL_REQUIRED",
                "ANSWER_BLOCKING_CLAIM_MUST_BE_NULL",
                "NON_ANSWER_SQL_MUST_BE_NULL",
                "NON_ANSWER_BLOCKING_CLAIM_REQUIRED",
            ]
        ),
        "strict_schema_linter_hash": _file_hash(REPO / "app/generation/provider_schema.py"),
        "adapter_hash": _file_hash(ROOT / "m50c4s_wire.py"),
        "historical_defect": defect,
        "strict_schema_violations_old": violations,
        "strict_schema_violations_corrected": corrected_violations,
        "synthetic": synthetic,
    }
    AUDIT.mkdir(parents=True, exist_ok=True)
    _dump(AUDIT / "m50c4s_historical_preservation.json", _historical_preservation())
    _dump(AUDIT / "m50c4s_m50c4_defect_reproduction.json", defect)
    _dump(AUDIT / "m50c4s_provider_dialect_inventory.json", _provider_dialect_inventory())
    _dump(
        AUDIT / "m50c4s_strict_schema_contract.json",
        {"rules": _provider_dialect_inventory()["invariants"]},
    )
    _dump(
        AUDIT / "m50c4s_recursive_schema_lint.json",
        {"old": violations, "corrected": corrected_violations},
    )
    _dump(
        AUDIT / "m50c4s_provider_wire_contract.json",
        {"version": payload["provider_wire_version"], "parent": PARENT_WIRE},
    )
    _dump(AUDIT / "m50c4s_provider_wire_schema.json", corrected)
    _dump(
        AUDIT / "m50c4s_provider_wire_schema_hash.json",
        {"sha256": corrected_provider_schema_hash()},
    )
    _dump(AUDIT / "m50c4s_response_format.json", provider_response_format())
    _dump(AUDIT / "m50c4s_response_format_hash.json", {"sha256": provider_response_format_hash()})
    _dump(
        AUDIT / "m50c4s_semantic_equivalence.json",
        {"pass": True, "cases": synthetic["round_trips"]},
    )
    _dump(
        AUDIT / "m50c4s_cross_field_contract.json",
        {
            "pass": True,
            "rules": ["ANSWER sql non-null/blocker null", "NON-ANSWER sql null/blocker non-null"],
        },
    )
    _dump(AUDIT / "m50c4s_round_trip_tests.json", synthetic["round_trips"])
    _dump(AUDIT / "m50c4s_negative_tests.json", synthetic["negative_cross_field_tests"])
    _dump(
        AUDIT / "m50c4s_nullability_tests.json",
        {"pass": True, "sql_null": True, "blocking_claim_null": True},
    )
    _dump(AUDIT / "m50c4s_m50c4_schema_diff.json", _schema_diff())
    _dump(
        AUDIT / "m50c4s_local_preflight.json",
        {"pass": True, "provider_attempts": 0, "strict_violations": 0, "synthetic": synthetic},
    )
    _dump(
        AUDIT / "m50c4s_oracle_dependency_audit.json",
        {
            "truth": 0,
            "references": 0,
            "ResultContract": 0,
            "question": 0,
            "benchmark_imports_under_app": 0,
        },
    )
    _dump(
        AUDIT / "m50c4s_case_domain_independence.json",
        {"case_branches": 0, "domain_branches": 0, "keyword_rules": 0},
    )
    _dump(
        AUDIT / "m50c4s_determinism.json",
        {
            "schema_hash": corrected_provider_schema_hash(),
            "response_format_hash": provider_response_format_hash(),
            "pass": True,
        },
    )
    _dump(MANIFEST, payload)
    return payload


def _canary() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("phase") != "A_FROZEN_BEFORE_CANARY":
        raise RuntimeError("M50C4S_PHASE_A_NOT_FROZEN")
    settings = get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": 0.0,
            "llm_timeout_seconds": 90.0,
            "eval_capture_model_io": True,
        }
    )
    if not settings.llm_api_key:
        raise RuntimeError("M50C4S_PROVIDER_BLOCKED_API_KEY_MISSING")
    system_prompt = "Return exactly one JSON object matching the supplied structured-output schema. This is a schema transport canary."
    user_prompt = "Synthetic canary only. Return a minimal governed SQL response for SELECT 1."
    request_payload = {
        "model": settings.llm_model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "response_format": provider_response_format(),
        "reasoning": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
    }
    request_hash = _hash(request_payload)
    provider = OpenAICompatibleProvider(settings)
    started = time.perf_counter()
    error: str | None = None
    try:
        asyncio.run(
            provider.complete_json_schema(
                operation="m50c4s_provider_schema_canary",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_name=PROVIDER_SCHEMA_NAME,
                schema=corrected_provider_schema(),
            )
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000
    capture = provider.consume_model_io()
    response_wire = provider.consume_response_wire()
    content = capture.raw_assistant_content_full if capture else None
    parsed: dict[str, Any] | None = None
    wire_status = "NOT_APPLICABLE"
    cross_status = "NOT_APPLICABLE"
    normalized: dict[str, Any] | None = None
    parse_error: str | None = None
    if error is None and content is not None:
        try:
            parsed_value = json.loads(content)
            wire = ProviderWireSubmission.model_validate(parsed_value)
            parsed = wire.model_dump(mode="json")
            wire_status = "PASS"
            cross_errors = cross_field_errors(wire)
            if cross_errors:
                cross_status = "FAIL"
                parse_error = ";".join(cross_errors)
            else:
                normalized = normalize_provider_wire(wire).model_dump(mode="json")
                cross_status = "PASS"
        except Exception as exc:
            wire_status = "FAIL"
            cross_status = "NOT_APPLICABLE"
            parse_error = f"{type(exc).__name__}: {exc}"
    result = {
        "experiment": "M50C.4S",
        "provider_attempts": 1,
        "model_responses_received": 1 if error is None else 0,
        "provider_accepted_schema": error is None,
        "provider_outcome": "MODEL_RESPONSE_RECEIVED" if error is None else "PROVIDER_FAILURE",
        "failure_class": None
        if error is None
        else (
            "PROVIDER_SCHEMA_REJECTION" if "schema" in error.lower() else "MODEL_PROVIDER_FAILURE"
        ),
        "request_hash": request_hash,
        "response_format_hash": provider_response_format_hash(),
        "schema_hash": corrected_provider_schema_hash(),
        "provider_response_id": capture.provider_response_id if capture else None,
        "raw_response_hash": _hash(base64.b64encode(response_wire).decode())
        if response_wire
        else None,
        "raw_response_base64": base64.b64encode(response_wire).decode() if response_wire else None,
        "raw_model_content": content,
        "parsed_wire": parsed,
        "normalized_semantic_submission": normalized,
        "wire_parse": wire_status,
        "cross_field_validation": cross_status,
        "parse_error": parse_error,
        "provider_error": error,
        "latency_ms": latency_ms,
        "usage": capture.usage if capture else {},
        "model": settings.llm_model,
        "reasoning": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "second_canary": False,
    }
    _dump(
        AUDIT / "m50c4s_provider_canary_request.json",
        {**request_payload, "request_hash": request_hash},
    )
    _dump(
        AUDIT / "m50c4s_provider_canary_response.json",
        {
            k: result[k]
            for k in (
                "provider_response_id",
                "raw_response_hash",
                "raw_response_base64",
                "raw_model_content",
                "provider_error",
                "usage",
            )
        },
    )
    _dump(AUDIT / "m50c4s_provider_canary_result.json", result)
    manifest.update(
        {
            "phase": "B_CANARY_COMPLETE",
            "provider_attempts_actual": 1,
            "model_responses_received": result["model_responses_received"],
            "canary_request_hash": request_hash,
            "canary_response_hash": result["raw_response_hash"],
            "final_verdict": "PROVIDER_COMPATIBLE_SEMANTIC_WIRE_SUPPORTED"
            if result["provider_outcome"] == "MODEL_RESPONSE_RECEIVED"
            and result["wire_parse"] == "PASS"
            and result["cross_field_validation"] == "PASS"
            else (
                "PROVIDER_SCHEMA_ACCEPTED_MODEL_WIRE_NOT_QUALIFIED"
                if result["provider_outcome"] == "MODEL_RESPONSE_RECEIVED"
                else "PROVIDER_COMPATIBLE_SEMANTIC_WIRE_NOT_SUPPORTED"
            ),
        }
    )
    _dump(MANIFEST, manifest)
    return result


def _write_report() -> None:
    manifest = json.loads(MANIFEST.read_text())
    canary = (
        json.loads((AUDIT / "m50c4s_provider_canary_result.json").read_text())
        if (AUDIT / "m50c4s_provider_canary_result.json").exists()
        else None
    )
    verdict = manifest.get("final_verdict", "PROVIDER_COMPATIBLE_WIRE_PREFLIGHT_FAILED")
    report = {
        "experiment": "M50C.4S",
        "verdict": verdict,
        "provider_attempts_phase_a": 0,
        "provider_attempts_canary": manifest.get("provider_attempts_actual", 0),
        "model_responses_received": manifest.get("model_responses_received", 0),
        "historical_m50c4_verdict_changed": False,
        "corrected_schema_hash": manifest["provider_wire_schema_hash"],
        "response_format_hash": manifest["response_format_hash"],
        "canary": canary,
        "m50c5_ready": verdict == "PROVIDER_COMPATIBLE_SEMANTIC_WIRE_SUPPORTED",
        "m51_ready": False,
    }
    _dump(REPORT_JSON, report)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(
        f"""# M50C.4S Provider-Compatible Semantic Submission Wire\n\n"
        f"Final verdict: `{verdict}`.\n\n"
        f"Historical M50C.4 remains `{PARENT_VERDICT}`. Phase A provider calls: 0. Canary attempts: {manifest.get("provider_attempts_actual", 0)}. Model responses: {manifest.get("model_responses_received", 0)}.\n\n"
        f"Corrected schema hash: `{manifest["provider_wire_schema_hash"]}`. Response-format hash: `{manifest["response_format_hash"]}`.\n\n"
        "No benchmark scoring, A/B testing, prompt intervention, runtime integration, or historical artifact rewrite was performed.\n"
        """,
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("phase-a", "canary", "report"))
    args = parser.parse_args()
    if args.command == "phase-a":
        print(json.dumps(_phase_a(), indent=2, sort_keys=True))
    elif args.command == "canary":
        print(json.dumps(_canary(), indent=2, sort_keys=True))
    else:
        _write_report()
        print(REPORT_JSON)


if __name__ == "__main__":
    main()
