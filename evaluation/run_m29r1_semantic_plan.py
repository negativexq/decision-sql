"""M29R.1: complete canonical semantic-plan acquisition after protocol repair.

The run uses one fixed 90-second request per frozen case. It is deliberately
separate from M29 and M29R journals and never falls back to raw SQL generation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.generation.provider import OpenAICompatibleProvider, _semantic_query_plan_messages
from app.generation.semantic_plan_protocol import (
    provider_schema_errors,
    provider_semantic_query_plan_schema_hash,
)
from app.semantics.semantic_compiler import SemanticPlanValidator, SemanticQueryCompiler
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import SemanticQueryPlan, plan_to_ir
from app.semantics.semantic_validation import SemanticConsistencyValidator
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
    _execute_and_score,
    _leakage_free,
    _load_old_control,
    _m1_status,
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
    ROOT / "evaluation/external/livesqlbench/protected/results/m29r1_semantic_plan_journal.jsonl"
)
LOCAL_CASES = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m29r1_semantic_plan_cases.jsonl"
)
SAFE_RESULT = ROOT / "evaluation/fixtures/m29r1_livesqlbench_semantic_plan_result.json"
MANIFEST = ROOT / "evaluation/fixtures/m29r1_semantic_plan_experiment_manifest.json"
TIMEOUT_SECONDS = 90.0
EXPECTED_MODEL = "gpt-5.6-luna"
MAX_CALLS = 18


class M29R1Error(RuntimeError):
    """A fixed M29R.1 invariant failed."""


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
            "llm_timeout_seconds": TIMEOUT_SECONDS,
            "eval_capture_model_io": True,
        }
    )


def _append(row: dict[str, Any]) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        stream.flush()


def _upsert(row: dict[str, Any]) -> None:
    rows = [item for item in _read_jsonl(LOCAL_CASES) if item.get("case_id") != row.get("case_id")]
    rows.append(row)
    LOCAL_CASES.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_CASES.write_text(
        "".join(json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in rows),
        encoding="utf-8",
    )


def _load_journal() -> dict[str, dict[str, Any]]:
    rows = _read_unique_journal(JOURNAL)
    if len(rows) > MAX_CALLS:
        raise M29R1Error("M29R.1 journal exceeds 18 calls")
    if any(int(row.get("provider_calls", 0)) != 1 for row in rows.values()):
        raise M29R1Error("M29R.1 journal contains a non-single-call row")
    return rows


def _prepare() -> tuple[
    list[LiveSqlBenchEvaluationCase],
    dict[str, str],
    dict[str, SemanticMappingSnapshot],
    dict[str, Any],
    dict[str, tuple[Any, Any, Any]],
]:
    old_control = _load_old_control()
    cases, preflight, states, expected = _preflight(
        DEFAULT_PUBLIC_ROOT, DEFAULT_PROTECTED, validate_frozen_generation_prompt=True
    )
    ids = [case.instance_id for case in cases]
    manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    if len(ids) != 18 or len(set(ids)) != 18 or ids != manifest["pilot_case_ids"]:
        raise M29R1Error("frozen M29R.1 case order/population mismatch")
    contexts: dict[str, str] = {}
    mappings: dict[str, SemanticMappingSnapshot] = {}
    state_by_case: dict[str, tuple[Any, Any, Any]] = {}
    for case in cases:
        mapping = SemanticMappingSnapshot.from_schema(
            catalog_for_m1(states[case.database][0]), database_id=case.database
        )
        context = _semantic_context(preflight["corrected_contexts"][case.instance_id], mapping)
        if not _leakage_free(case, context, old_control[case.instance_id].get("generated_sql")):
            raise M29R1Error(f"provider context leakage: {case.instance_id}")
        contexts[case.instance_id] = context
        mappings[case.instance_id] = mapping
        state_by_case[case.instance_id] = states[case.database]
    return cases, contexts, mappings, expected, state_by_case


def _write_manifest(ids: list[str]) -> None:
    settings = _settings()
    value = {
        "milestone": "M29R.1",
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "reasoning_effort": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "timeout_seconds": settings.llm_timeout_seconds,
        "prompt_profile": settings.llm_prompt_profile,
        "canonical_schema_hash": _sha256_json(SemanticQueryPlan.model_json_schema()),
        "provider_schema_hash": provider_semantic_query_plan_schema_hash(),
        "prompt_hash": _sha256_text(inspect.getsource(_semantic_query_plan_messages)),
        "case_order_hash": _sha256_json(ids),
        "db_image_digest": DB_IMAGE_DIGEST,
        "response_format": {"type": "json_schema", "name": "semantic_query_plan", "strict": True},
        "semantic_retries": 0,
        "repair_calls": 0,
        "raw_sql_fallback": False,
    }
    MANIFEST.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validation_type(error: Exception) -> str | None:
    if not isinstance(error, ValidationError):
        return None
    errors = error.errors()
    return errors[0].get("type") if errors else None


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
        raw = capture.raw_assistant_content_full if capture else None
        row: dict[str, Any] = {
            "case_id": case.instance_id,
            "provider_calls": 1,
            "provider_success": raw is not None,
            "response_received": raw is not None,
            "response_bytes": len(raw.encode()) if raw is not None else None,
            "response_hash": capture.raw_assistant_content_sha256 if capture else None,
            "json_parse": True,
            "provider_schema_valid": True,
            "canonical_structural_valid": True,
            "canonical_schema_valid": True,
            "canonical_semantic_valid": False,
            "model": proposal.model,
            "parsed_plan_hash": _sha256_json(proposal.plan.model_dump(mode="json")),
            "usage": {
                "input_tokens": capture.usage.get("prompt_tokens") if capture else None,
                "output_tokens": capture.usage.get("completion_tokens") if capture else None,
                "reasoning_tokens": capture.usage.get("reasoning_tokens") if capture else None,
                "cached_tokens": capture.usage.get("cached_prompt_tokens") if capture else None,
            },
            "provider_latency_ms": (time.perf_counter() - started) * 1000,
            "request_format_type": (
                capture.request_config.get("response_format", {}).get("type") if capture else None
            ),
            "strict": (
                capture.request_config.get("response_format", {})
                .get("json_schema", {})
                .get("strict")
                if capture
                else False
            ),
            "raw_response": raw,
        }
        _append(row)
        return row
    except Exception as error:
        capture = provider.consume_model_io()
        raw = capture.raw_assistant_content_full if capture else None
        row = {
            "case_id": case.instance_id,
            "provider_calls": 1,
            "provider_success": raw is not None,
            "response_received": raw is not None,
            "response_bytes": len(raw.encode()) if raw is not None else None,
            "response_hash": capture.raw_assistant_content_sha256 if capture else None,
            "json_parse": raw is not None,
            "provider_schema_valid": raw is not None,
            "canonical_structural_valid": False,
            "canonical_schema_valid": False,
            "canonical_semantic_valid": False,
            "model": EXPECTED_MODEL,
            "provider_latency_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(error).__name__,
            "error_message": str(error)[:240],
            "raw_response": raw,
        }
        _append(row)
        return row


def _safe(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"raw_response", "plan"}}


async def run() -> dict[str, Any]:
    cases, contexts, mappings, expected, states = _prepare()
    ids = [case.instance_id for case in cases]
    _write_manifest(ids)
    journal = _load_journal()
    metadata_blocked_by_case: dict[str, bool] = {}
    for case in cases:
        try:
            oracle_plan_from_reference_sql(
                case.sol_sql[0], mappings[case.instance_id], database_id=case.database
            )
        except Exception as error:
            if getattr(getattr(error, "code", None), "value", None) != "UNKNOWN_RELATIONSHIP":
                raise
            metadata_blocked_by_case[case.instance_id] = True
        else:
            metadata_blocked_by_case[case.instance_id] = False
    provider = OpenAICompatibleProvider(_settings())
    rows: list[dict[str, Any]] = []
    for case in cases:
        row = await _call_one(
            provider, case, contexts[case.instance_id], journal.get(case.instance_id)
        )
        row["metadata_blocked"] = metadata_blocked_by_case[case.instance_id]
        raw = row.get("raw_response")
        if raw is not None:
            try:
                data = json.loads(raw)
                structural_errors = provider_schema_errors(data)
                row["provider_schema_valid"] = not structural_errors
                row["provider_schema_errors"] = list(structural_errors)
                plan = SemanticQueryPlan.model_validate(data)
                row["canonical_structural_valid"] = True
                row["canonical_schema_valid"] = True
                row["plan"] = plan.model_dump(mode="json")
                try:
                    SemanticPlanValidator(mappings[case.instance_id]).validate(plan_to_ir(plan))
                    row["canonical_semantic_valid"] = True
                    row["primary_failure"] = None
                except Exception as error:
                    row["primary_failure"] = "CANONICAL_SEMANTIC_FAILURE"
                    row["semantic_failure"] = str(error)[:240]
                try:
                    oracle = oracle_plan_from_reference_sql(
                        case.sol_sql[0], mappings[case.instance_id], database_id=case.database
                    )
                except Exception as error:
                    oracle = None
                    if (
                        getattr(getattr(error, "code", None), "value", None)
                        != "UNKNOWN_RELATIONSHIP"
                    ):
                        raise
                row["metadata_blocked"] = oracle is None
                if oracle is not None:
                    row["component_scores"] = _component_scores(
                        plan, oracle, mappings[case.instance_id]
                    )
                    row["exact_oracle_match"] = all(row["component_scores"].values()) and (
                        _plan_signature(plan) == _plan_signature(oracle)
                    )
            except ValidationError as error:
                row["canonical_schema_valid"] = False
                row["canonical_structural_valid"] = _validation_type(error) not in {
                    "string_too_long",
                    "string_too_short",
                    "too_long",
                    "too_short",
                    "greater_than",
                    "less_than",
                    "int_parsing",
                    "string_type",
                    "list_type",
                    "dict_type",
                }
                row["primary_failure"] = (
                    "CANONICAL_SEMANTIC_FAILURE"
                    if row["canonical_structural_valid"]
                    else "CANONICAL_STRUCTURAL_FAILURE"
                )
                row["validation_error"] = str(error)[:240]
            except Exception as error:
                row["primary_failure"] = "CANONICAL_STRUCTURAL_FAILURE"
                row["validation_error"] = str(error)[:240]
        else:
            row["primary_failure"] = (
                "TRANSPORT_TIMEOUT"
                if row.get("error_type") == "LLMProviderError"
                else "PROVIDER_FAILURE"
            )

        if row.get("canonical_semantic_valid") and not row.get("metadata_blocked"):
            try:
                compiled = SemanticQueryCompiler(mappings[case.instance_id]).compile(
                    plan_to_ir(SemanticQueryPlan.model_validate(row["plan"]))
                )
                row["compiled_sql_hash"] = _sha256_text(compiled.sql)
                validation = SemanticConsistencyValidator(mappings[case.instance_id]).validate(
                    plan_to_ir(SemanticQueryPlan.model_validate(row["plan"])), compiled
                )
                row["semantic_consistency_accepted"] = validation.accepted
                safety = states[case.instance_id][2]
                m1, planned = _m1_status(safety, compiled.sql, f"m29r1:{case.instance_id}")
                row["m1_status"] = m1["status"]
                row["m1_failure_code"] = m1["failure_code"]
                if planned is not None:
                    execution = _execute_and_score(
                        safety,
                        planned,
                        expected[case.instance_id],
                        bool(case.public.conditions.get("order", False)),
                    )
                    row["execution_status"] = execution["status"]
                    row["official_status"] = (
                        "EVALUATOR_LIMITATION"
                        if execution["limitation"]
                        else "CORRECT"
                        if execution["correct"]
                        else "INCORRECT"
                    )
                else:
                    row["execution_status"] = "NOT_REACHED"
            except Exception as error:
                row["primary_failure"] = "COMPILER_FAILURE"
                row["compiler_error"] = str(error)[:240]
        _upsert({**row, "question": case.runtime.question, "context": contexts[case.instance_id]})
        rows.append(row)

    if len(rows) != 18 or sum(int(row.get("provider_calls", 0)) for row in rows) != 18:
        raise M29R1Error("M29R.1 did not account for exactly 18 calls")
    comparable = [row for row in rows if not row.get("metadata_blocked")]
    result = {
        "classification": "M29R1_CANONICAL_PLAN_ACQUISITION_COMPLETED",
        "experiment": {
            "milestone": "M29R.1",
            "cases": 18,
            "provider_calls": 18,
            "calls_per_case_max": 1,
            "timeout_seconds": TIMEOUT_SECONDS,
            "intervention": "TIMEOUT_AND_PROVIDER_SCHEMA_PARITY",
        },
        "protocol": {
            "provider": "openai-compatible",
            "model": EXPECTED_MODEL,
            "reasoning_effort": "none",
            "temperature": 0.0,
            "timeout_seconds": TIMEOUT_SECONDS,
            "canonical_schema_hash": _sha256_json(SemanticQueryPlan.model_json_schema()),
            "provider_schema_hash": provider_semantic_query_plan_schema_hash(),
            "prompt_hash": _sha256_text(inspect.getsource(_semantic_query_plan_messages)),
            "db_image_digest": DB_IMAGE_DIGEST,
            "raw_sql_fallback": False,
        },
        "acquisition": {
            "requests": 18,
            "responses": sum(bool(row.get("response_received")) for row in rows),
            "transport_timeouts": sum(
                row.get("primary_failure") == "TRANSPORT_TIMEOUT" for row in rows
            ),
            "provider_schema_valid": sum(bool(row.get("provider_schema_valid")) for row in rows),
            "canonical_structural_valid": sum(
                bool(row.get("canonical_structural_valid")) for row in rows
            ),
            "canonical_schema_valid": sum(bool(row.get("canonical_schema_valid")) for row in rows),
            "canonical_semantic_plan_valid": sum(
                bool(row.get("canonical_semantic_valid")) for row in rows
            ),
        },
        "semantic_plan": {
            "exact_oracle_matches": sum(
                row.get("exact_oracle_match") is True for row in comparable
            ),
            "partial_or_wrong": sum(row.get("exact_oracle_match") is False for row in comparable),
            "metadata_blocked": sum(bool(row.get("metadata_blocked")) for row in rows),
            "component_scores": {
                key: {
                    "applicable": sum(key in row.get("component_scores", {}) for row in comparable),
                    "correct": sum(
                        bool(row.get("component_scores", {}).get(key)) for row in comparable
                    ),
                }
                for key in (
                    "entities",
                    "relationships",
                    "population",
                    "grain",
                    "outputs",
                    "filters",
                    "aggregation",
                    "calculation",
                    "ordering",
                    "limit",
                    "temporal",
                    "window",
                    "nested_structure",
                )
            },
        },
        "execution": {
            "semantic_valid": sum(bool(row.get("canonical_semantic_valid")) for row in rows),
            "compiled": sum("compiled_sql_hash" in row for row in rows),
            "semantic_consistency_accepted": sum(
                bool(row.get("semantic_consistency_accepted")) for row in rows
            ),
            "m1_accepted": sum(row.get("m1_status") == "ACCEPTED" for row in rows),
            "executed": sum(row.get("execution_status") == "SUCCESS" for row in rows),
            "official_correct": sum(row.get("official_status") == "CORRECT" for row in rows),
            "evaluator_limitation": sum(
                row.get("official_status") == "EVALUATOR_LIMITATION" for row in rows
            ),
        },
        "provider_call_accounting": {
            "frozen_primary_calls": 18,
            "semantic_retries": 0,
            "repair_calls": 0,
            "direct_calls": 0,
        },
        "cases": [_safe(row) for row in rows],
    }
    SAFE_RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        raise SystemExit("M29R.1 primary is explicit: pass --run")
    print(json.dumps(asyncio.run(run()), sort_keys=True))


if __name__ == "__main__":
    main()
