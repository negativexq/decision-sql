"""M29R: acquire canonical semantic plans with native strict JSON schema.

This evaluation-only runner is separate from the frozen M29 runner. It makes
one semantic-plan request per frozen case, records complete local responses in
the ignored protected results directory, and commits only bounded aggregates.
It does not fall back to SQL generation or perform semantic repair.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.generation.provider import OpenAICompatibleProvider, _semantic_query_plan_messages
from app.generation.semantic_plan_protocol import (
    provider_semantic_query_plan_schema,
    provider_semantic_query_plan_schema_hash,
)
from app.semantics.semantic_compiler import SemanticPlanValidator
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import SemanticQueryPlan, plan_to_ir
from evaluation.external.livesqlbench.m1 import catalog_for_m1
from evaluation.external.livesqlbench.protected import LiveSqlBenchEvaluationCase
from evaluation.m25_2_livesqlbench_direct_semantic_context import (
    _preflight,
    _read_jsonl,
    _read_unique_journal,
)
from evaluation.run_m29_semantic_plan import (
    DB_IMAGE_DIGEST,
    FINAL_MANIFEST,
    _component_scores,
    _leakage_free,
    _load_old_control,
    _plan_signature,
    _semantic_context,
)
from evaluation.semantic_oracle_ceiling import oracle_plan_from_reference_sql

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
DEFAULT_PROTECTED = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
JOURNAL = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m29r_semantic_plan_journal.jsonl"
)
LOCAL_CASES = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m29r_semantic_plan_cases.jsonl"
)
SAFE_RESULT = ROOT / "evaluation/fixtures/m29r_livesqlbench_semantic_plan_result.json"
PREFLIGHT_RESULT = ROOT / "evaluation/fixtures/m29r_semantic_plan_preflight.json"
MANIFEST_RESULT = ROOT / "evaluation/fixtures/m29r_semantic_plan_experiment_manifest.json"
EXPECTED_MODEL = "gpt-5.6-luna"
MAX_CALLS = 18


class M29RError(RuntimeError):
    """An M29R invariant failed before or during the fixed one-call run."""


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _settings() -> Settings:
    return get_settings().model_copy(
        update={
            "llm_model": EXPECTED_MODEL,
            "llm_temperature": 0.0,
            "llm_reasoning_effort": "none",
            "llm_prompt_profile": "legacy",
            "eval_capture_model_io": True,
        }
    )


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        stream.flush()


def _upsert(path: Path, row: dict[str, Any]) -> None:
    rows = [item for item in _read_jsonl(path) if item.get("case_id") != row.get("case_id")]
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for item in rows:
            stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")


def _journal() -> dict[str, dict[str, Any]]:
    rows = _read_unique_journal(JOURNAL)
    if len(rows) > MAX_CALLS:
        raise M29RError("M29R journal exceeds the one-call budget")
    for case_id, row in rows.items():
        if int(row.get("provider_calls", 0)) != 1:
            raise M29RError(f"M29R journal has invalid call count for {case_id}")
    return rows


def _prepare() -> tuple[
    list[LiveSqlBenchEvaluationCase], dict[str, str], dict[str, SemanticMappingSnapshot]
]:
    old_control = _load_old_control()
    cases, preflight, states, _ = _preflight(
        DEFAULT_PUBLIC_ROOT, DEFAULT_PROTECTED, validate_frozen_generation_prompt=True
    )
    ids = [case.instance_id for case in cases]
    manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    if len(ids) != 18 or len(set(ids)) != 18 or ids != manifest["pilot_case_ids"]:
        raise M29RError("M29R frozen case population/order mismatch")
    contexts: dict[str, str] = {}
    mappings: dict[str, SemanticMappingSnapshot] = {}
    for case in cases:
        mapping = SemanticMappingSnapshot.from_schema(
            catalog_for_m1(states[case.database][0]), database_id=case.database
        )
        context = _semantic_context(preflight["corrected_contexts"][case.instance_id], mapping)
        if not _leakage_free(case, context, old_control[case.instance_id].get("generated_sql")):
            raise M29RError(f"M29R provider context leakage: {case.instance_id}")
        contexts[case.instance_id] = context
        mappings[case.instance_id] = mapping
    return cases, contexts, mappings


def _manifest() -> dict[str, Any]:
    settings = _settings()
    schema = SemanticQueryPlan.model_json_schema()
    provider_schema = provider_semantic_query_plan_schema()
    return {
        "milestone": "M29R",
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "reasoning_effort": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "prompt_profile": settings.llm_prompt_profile,
        "canonical_schema_hash": _sha256_json(schema),
        "provider_schema_hash": provider_semantic_query_plan_schema_hash(),
        "provider_schema_bytes": len(
            json.dumps(provider_schema, ensure_ascii=False, separators=(",", ":")).encode()
        ),
        "response_format": {
            "type": "json_schema",
            "name": "semantic_query_plan",
            "strict": True,
        },
        "prompt_hash": _sha256_text(inspect.getsource(_semantic_query_plan_messages)),
        "db_image_digest": DB_IMAGE_DIGEST,
        "case_order_hash": _sha256_json(json.loads(FINAL_MANIFEST.read_text())["pilot_case_ids"]),
        "provider_calls": 0,
        "raw_sql_fallback": False,
        "semantic_retries": 0,
        "repair_calls": 0,
    }


async def _call_one(
    provider: OpenAICompatibleProvider,
    case: LiveSqlBenchEvaluationCase,
    context: str,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    if existing is not None:
        return existing
    started = time.perf_counter()
    try:
        proposal = await provider.propose_semantic_query_plan(case.runtime.question, context)
        capture = provider.consume_model_io()
        raw = capture.raw_assistant_content_full if capture is not None else None
        row: dict[str, Any] = {
            "case_id": case.instance_id,
            "provider_calls": 1,
            "provider_success": True,
            "response_received": raw is not None,
            "response_hash": capture.raw_assistant_content_sha256 if capture else None,
            "response_bytes": len(raw.encode()) if raw is not None else None,
            "json_parse": True,
            "provider_schema_valid": True,
            "canonical_schema_valid": True,
            "canonical_plan_valid": True,
            "model": proposal.model,
            "parsed_plan_hash": _sha256_json(proposal.plan.model_dump(mode="json")),
            "usage": {
                "input_tokens": capture.usage.get("prompt_tokens") if capture else None,
                "output_tokens": capture.usage.get("completion_tokens") if capture else None,
                "reasoning_tokens": capture.usage.get("reasoning_tokens") if capture else None,
                "cached_tokens": capture.usage.get("cached_prompt_tokens") if capture else None,
            },
            "provider_latency_ms": (time.perf_counter() - started) * 1000,
            "raw_response": raw,
            "request_format_type": (capture.request_config.get("response_format", {}) or {}).get(
                "type"
            )
            if capture
            else None,
            "strict": bool(
                (
                    (capture.request_config.get("response_format", {}) or {}).get("json_schema")
                    or {}
                ).get("strict")
            )
            if capture
            else False,
        }
        _append(JOURNAL, row)
        return row
    except Exception as error:
        capture = provider.consume_model_io()
        raw = capture.raw_assistant_content_full if capture is not None else None
        row = {
            "case_id": case.instance_id,
            "provider_calls": 1,
            "provider_success": False,
            "response_received": raw is not None,
            "response_hash": capture.raw_assistant_content_sha256 if capture else None,
            "response_bytes": len(raw.encode()) if raw is not None else None,
            "json_parse": False,
            "provider_schema_valid": False,
            "canonical_schema_valid": False,
            "canonical_plan_valid": False,
            "model": EXPECTED_MODEL,
            "provider_latency_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(error).__name__,
            "error_message": str(error)[:240],
            "raw_response": raw,
        }
        _append(JOURNAL, row)
        return row


def _safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"raw_response", "plan"}}


async def run() -> dict[str, Any]:
    cases, contexts, mappings = _prepare()
    settings = _settings()
    journal = _journal()
    MANIFEST_RESULT.write_text(json.dumps(_manifest(), indent=2, sort_keys=True) + "\n")
    provider = OpenAICompatibleProvider(settings)
    rows: list[dict[str, Any]] = []
    for case in cases:
        row = await _call_one(
            provider, case, contexts[case.instance_id], journal.get(case.instance_id)
        )
        if row.get("raw_response"):
            row["provider_success"] = True
            row["response_received"] = True
            row["json_parse"] = False
            row["provider_schema_valid"] = True
            try:
                raw_data = json.loads(row["raw_response"])
                row["json_parse"] = True
                plan = SemanticQueryPlan.model_validate(raw_data)
                row["canonical_schema_valid"] = True
                row["plan"] = plan.model_dump(mode="json")
                mapping = mappings[case.instance_id]
                SemanticPlanValidator(mapping).validate(plan_to_ir(plan))
                row["canonical_plan_valid"] = True
                try:
                    oracle = oracle_plan_from_reference_sql(
                        case.sol_sql[0], mapping, database_id=case.database
                    )
                except Exception as error:
                    error_code = getattr(getattr(error, "code", None), "value", None)
                    if getattr(error, "code", None) and error_code == "UNKNOWN_RELATIONSHIP":
                        oracle = None
                    else:
                        raise
                row["metadata_blocked"] = oracle is None
                if oracle is not None:
                    scores = _component_scores(plan, oracle, mapping)
                    row["component_scores"] = scores
                    row["exact_oracle_match"] = all(scores.values()) and (
                        _plan_signature(plan) == _plan_signature(oracle)
                    )
                row["primary_failure"] = None
            except Exception as error:
                row["canonical_plan_valid"] = False
                row["primary_failure"] = "PLAN_SCHEMA_FAILURE"
                row["validation_error"] = str(error)[:240]
                if "plan" not in row:
                    row["canonical_schema_valid"] = False
        else:
            row["provider_success"] = False
            row["primary_failure"] = "PROVIDER_FAILURE"
        _upsert(
            LOCAL_CASES,
            {**row, "question": case.runtime.question, "context": contexts[case.instance_id]},
        )
        rows.append(row)

    if len(rows) != 18 or sum(int(row.get("provider_calls", 0)) for row in rows) != 18:
        raise M29RError("M29R did not account for exactly 18 one-call cases")
    result = {
        "classification": "M29R_CANONICAL_PLAN_CONTRACT_ACQUISITION_COMPLETED",
        "experiment": {
            "milestone": "M29R",
            "cases": 18,
            "provider_calls": 18,
            "calls_per_case_max": 1,
            "intervention": "NATIVE_STRICT_PROVIDER_CANONICAL_SCHEMA",
        },
        "protocol": {
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
            "temperature": settings.llm_temperature,
            "canonical_schema_hash": _sha256_json(SemanticQueryPlan.model_json_schema()),
            "provider_schema_hash": provider_semantic_query_plan_schema_hash(),
            "prompt_hash": _manifest()["prompt_hash"],
            "db_image_digest": DB_IMAGE_DIGEST,
            "raw_sql_fallback": False,
        },
        "acquisition": {
            "requests": 18,
            "responses": sum(bool(row.get("response_received")) for row in rows),
            "json_parse": sum(bool(row.get("json_parse")) for row in rows),
            "provider_schema_valid": sum(bool(row.get("provider_schema_valid")) for row in rows),
            "canonical_schema_valid": sum(bool(row.get("canonical_schema_valid")) for row in rows),
            "canonical_plan_valid": sum(bool(row.get("canonical_plan_valid")) for row in rows),
            "provider_failures": sum(not bool(row.get("provider_success")) for row in rows),
        },
        "semantic_plan": {
            "exact_oracle_matches": sum(row.get("exact_oracle_match") is True for row in rows),
            "partial_or_wrong": sum(
                row.get("exact_oracle_match") is False
                for row in rows
                if not row.get("metadata_blocked")
            ),
            "metadata_blocked": sum(bool(row.get("metadata_blocked")) for row in rows),
            "component_scores": dict(
                Counter(
                    key
                    for row in rows
                    for key, value in row.get("component_scores", {}).items()
                    if value
                )
            ),
        },
        "provider_call_accounting": {
            "smoke_calls": 2,
            "frozen_primary_calls": 18,
            "semantic_retries": 0,
            "repair_calls": 0,
            "direct_calls": 0,
        },
        "cases": [_safe_row(row) for row in rows],
    }
    SAFE_RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="run the fixed 18-case primary")
    args = parser.parse_args()
    if not args.run:
        raise SystemExit("M29R primary is explicit: pass --run")
    print(json.dumps(asyncio.run(run()), sort_keys=True))


if __name__ == "__main__":
    main()
