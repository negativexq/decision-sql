"""M30.1: one-call semantic-plan baseline on the frozen 162-case remainder."""

from __future__ import annotations

import argparse
import asyncio
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
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.semantics.semantic_compiler import SemanticPlanValidator, SemanticQueryCompiler
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import SemanticQueryPlan, plan_to_ir
from app.semantics.semantic_validation import SemanticConsistencyValidator
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import catalog_for_m1, safety_for_database
from evaluation.external.livesqlbench.protected import (
    LiveSqlBenchEvaluationCase,
    load_protected_artifact,
    merge_public_and_protected,
)
from evaluation.external.livesqlbench.schema import PostgresConnectionConfig, introspect_database
from evaluation.external.livesqlbench.semantic_context import (
    load_semantic_resources,
    render_semantic_context,
)
from evaluation.m25_2_livesqlbench_direct_semantic_context import (
    _read_jsonl,
    _read_unique_journal,
    _reference_result,
)
from evaluation.run_m29_semantic_plan import (
    DB_IMAGE_DIGEST,
    _component_scores,
    _execute_and_score,
    _leakage_free,
    _m1_status,
    _semantic_context,
    _sha256_json,
    _sha256_text,
)
from evaluation.run_m29r1_semantic_plan import TIMEOUT_SECONDS
from evaluation.semantic_oracle_ceiling import oracle_plan_from_reference_sql

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
PILOT_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
MANIFEST = ROOT / "evaluation/fixtures/m30_1_semantic_plan_experiment_manifest.json"
CASE_MANIFEST = ROOT / "evaluation/datasets/m30_1_semantic_plan_manifest.json"
JOURNAL = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m30_1_semantic_plan_journal.jsonl"
)
LOCAL_CASES = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m30_1_semantic_plan_cases.jsonl"
)
RESULT = ROOT / "evaluation/fixtures/m30_1_semantic_plan_result.json"
EXPECTED_MODEL = "gpt-5.6-luna"
MAX_CALLS = 162


class M30_1Error(RuntimeError):
    """A frozen M30.1 invariant failed."""


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
        raise M30_1Error("M30.1 journal exceeds the 162-call budget")
    if any(int(row.get("provider_calls", 0)) != 1 for row in rows.values()):
        raise M30_1Error("M30.1 journal contains a non-single-call row")
    return rows


def _load_cases() -> tuple[list[LiveSqlBenchEvaluationCase], list[str], list[str]]:
    public = load_dataset(PUBLIC_ROOT / DATASET_FILENAME)
    protected = load_protected_artifact(PROTECTED)
    merged, merge = merge_public_and_protected(public, protected)
    if merge != {
        "public_rows": 270,
        "protected_rows": 270,
        "exact_matches": 270,
        "unmatched_public": 0,
        "unmatched_protected": 0,
        "duplicate_ids": 0,
        "merge_key": "instance_id",
    }:
        raise M30_1Error("public/protected merge differs from the frozen Base-Lite merge")
    eligible = sorted(
        (case for case in merged if case.public.eligible_select), key=lambda item: item.instance_id
    )
    pilot = json.loads(PILOT_MANIFEST.read_text(encoding="utf-8"))["pilot_case_ids"]
    if len(eligible) != 180 or len(pilot) != 18 or len(set(pilot)) != 18:
        raise M30_1Error("frozen Base-Lite population is not 180 SELECT plus 18 pilot cases")
    eligible_ids = [case.instance_id for case in eligible]
    if any(case_id not in eligible_ids for case_id in pilot):
        raise M30_1Error("pilot contains a case outside the eligible SELECT population")
    analysis = [case for case in eligible if case.instance_id not in set(pilot)]
    if len(analysis) != MAX_CALLS:
        raise M30_1Error("M30.1 analysis population is not the frozen 162-case remainder")
    return analysis, eligible_ids, list(pilot)


def _write_manifest(
    analysis: list[LiveSqlBenchEvaluationCase], eligible: list[str], pilot: list[str]
) -> None:
    settings = _settings()
    case_ids = [case.instance_id for case in analysis]
    case_manifest = {
        "milestone": "M30.1",
        "population": "official_livesqlbench_base_lite_select",
        "eligible_case_count": len(eligible),
        "diagnostic_case_count": len(pilot),
        "analysis_case_count": len(case_ids),
        "diagnostic_case_ids": pilot,
        "analysis_case_ids": case_ids,
        "untouched_holdout_case_ids": [],
        "split_source": (
            "livesqlbench_base_lite_final_manifest pilot_case_ids; lexicographic remainder"
        ),
    }
    CASE_MANIFEST.write_text(
        json.dumps(case_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    value = {
        "milestone": "M30.1",
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "reasoning_effort": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "timeout_seconds": settings.llm_timeout_seconds,
        "prompt_profile": settings.llm_prompt_profile,
        "canonical_schema_hash": _sha256_json(SemanticQueryPlan.model_json_schema()),
        "provider_schema_hash": provider_semantic_query_plan_schema_hash(),
        "prompt_hash": _sha256_text(inspect.getsource(_semantic_query_plan_messages)),
        "case_manifest_hash": _sha256_json(case_manifest),
        "case_order_hash": _sha256_json(case_ids),
        "db_image_digest": DB_IMAGE_DIGEST,
        "response_format": {"type": "json_schema", "name": "semantic_query_plan", "strict": True},
        "semantic_retries": 0,
        "repair_calls": 0,
        "direct_fallback": False,
    }
    MANIFEST.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prepare() -> tuple[
    list[LiveSqlBenchEvaluationCase],
    dict[str, str],
    dict[str, SemanticMappingSnapshot],
    dict[str, LiveSqlBenchResult | None],
    dict[str, Any],
    dict[str, bool],
    dict[str, str | None],
    dict[str, str | None],
]:
    cases, eligible, pilot = _load_cases()
    _write_manifest(cases, eligible, pilot)
    config = PostgresConnectionConfig.from_environment()
    contexts: dict[str, str] = {}
    mappings: dict[str, SemanticMappingSnapshot] = {}
    expected: dict[str, LiveSqlBenchResult | None] = {}
    safety_by_database: dict[str, Any] = {}
    metadata_blocked: dict[str, bool] = {}
    oracle_failure: dict[str, str | None] = {}
    reference_failure: dict[str, str | None] = {}
    for case in cases:
        if case.database not in safety_by_database:
            catalog = introspect_database(case.database, config)
            _, safety, _ = safety_for_database(catalog, config)
            safety_by_database[case.database] = safety
            mappings[case.database] = SemanticMappingSnapshot.from_schema(
                catalog_for_m1(catalog), database_id=case.database
            )
        safety = safety_by_database[case.database]
        mapping = mappings[case.database]
        structural = serialize_schema_context(
            SchemaContextResolver(safety.catalog).resolve(
                case.runtime.question, SchemaContextMode.FULL_COMPACT
            )
        )
        corrected = render_semantic_context(
            structural, load_semantic_resources(PUBLIC_ROOT, case.database)
        )
        context = _semantic_context(corrected, mapping)
        if not _leakage_free(case, context, None):
            raise M30_1Error(f"provider context leakage: {case.instance_id}")
        contexts[case.instance_id] = context
        try:
            expected[case.instance_id] = _reference_result(case, safety)
        except Exception as error:
            expected[case.instance_id] = None
            reference_failure[case.instance_id] = f"{type(error).__name__}: {str(error)[:180]}"
        try:
            oracle_plan_from_reference_sql(case.sol_sql[0], mapping, database_id=case.database)
        except Exception as error:
            if getattr(getattr(error, "code", None), "value", None) == "UNKNOWN_RELATIONSHIP":
                metadata_blocked[case.instance_id] = True
                oracle_failure[case.instance_id] = "MISSING_SERVER_OWNED_RELATIONSHIP_METADATA"
            else:
                metadata_blocked[case.instance_id] = False
                oracle_failure[case.instance_id] = f"{type(error).__name__}: {str(error)[:180]}"
        else:
            metadata_blocked[case.instance_id] = False
            oracle_failure[case.instance_id] = None
    return (
        cases,
        contexts,
        mappings,
        expected,
        safety_by_database,
        metadata_blocked,
        oracle_failure,
        reference_failure,
    )


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
            "provider_latency_ms": (time.perf_counter() - started) * 1000,
            "request_format_type": capture.request_config.get("response_format", {}).get("type")
            if capture
            else None,
            "strict": capture.request_config.get("response_format", {})
            .get("json_schema", {})
            .get("strict")
            if capture
            else False,
            "raw_response": raw,
        }
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
            "provider_schema_valid": False,
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
    (
        cases,
        contexts,
        mappings,
        expected,
        safety_by_database,
        metadata_blocked,
        oracle_failure,
        reference_failure,
    ) = _prepare()
    journal = _load_journal()
    provider = OpenAICompatibleProvider(_settings())
    rows: list[dict[str, Any]] = []
    for case in cases:
        row = await _call_one(
            provider, case, contexts[case.instance_id], journal.get(case.instance_id)
        )
        row["metadata_blocked"] = metadata_blocked[case.instance_id]
        row["oracle_failure"] = oracle_failure[case.instance_id]
        row["reference_failure"] = reference_failure.get(case.instance_id)
        raw = row.get("raw_response")
        plan: SemanticQueryPlan | None = None
        if raw is None:
            row["primary_failure"] = (
                "TRANSPORT_TIMEOUT"
                if row.get("error_type") == "LLMProviderError"
                else "PROVIDER_FAILURE"
            )
        else:
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
                    SemanticPlanValidator(mappings[case.database]).validate(plan_to_ir(plan))
                    row["canonical_semantic_valid"] = True
                except Exception as error:
                    row["primary_failure"] = "CANONICAL_SEMANTIC_FAILURE"
                    row["semantic_failure"] = str(error)[:240]
            except ValidationError as error:
                row["canonical_structural_valid"] = True
                row["canonical_schema_valid"] = False
                row["primary_failure"] = "CANONICAL_SEMANTIC_FAILURE"
                row["validation_error"] = str(error)[:240]
            except Exception as error:
                row["canonical_structural_valid"] = False
                row["canonical_schema_valid"] = False
                row["primary_failure"] = "CANONICAL_STRUCTURAL_FAILURE"
                row["validation_error"] = str(error)[:240]
        if plan is not None:
            try:
                oracle = oracle_plan_from_reference_sql(
                    case.sol_sql[0], mappings[case.database], database_id=case.database
                )
            except Exception:
                oracle = None
            if oracle is not None:
                row["component_scores"] = _component_scores(plan, oracle, mappings[case.database])
                row["exact_oracle_match"] = all(row["component_scores"].values())
        if row.get("canonical_semantic_valid") and not metadata_blocked[case.instance_id]:
            try:
                if plan is None:
                    raise M30_1Error("semantic-valid row has no canonical plan")
                compiled = SemanticQueryCompiler(mappings[case.database]).compile(plan_to_ir(plan))
                row["compiled_sql_hash"] = _sha256_text(compiled.sql)
                validation = SemanticConsistencyValidator(mappings[case.database]).validate(
                    plan_to_ir(plan), compiled
                )
                row["semantic_consistency_accepted"] = validation.accepted
                m1, planned = _m1_status(
                    safety_by_database[case.database], compiled.sql, f"m30.1:{case.instance_id}"
                )
                row["m1_status"] = m1["status"]
                row["m1_failure_code"] = m1["failure_code"]
                reference_result = expected[case.instance_id]
                if planned is not None and reference_result is not None:
                    execution = _execute_and_score(
                        safety_by_database[case.database],
                        planned,
                        reference_result,
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
                elif planned is not None:
                    execution = safety_by_database[case.database].execute(planned)
                    row["execution_status"] = "SUCCESS" if execution is not None else "FAILURE"
                    row["official_status"] = "EVALUATOR_LIMITATION"
                else:
                    row["execution_status"] = "NOT_REACHED"
            except Exception as error:
                row["primary_failure"] = "COMPILER_FAILURE"
                row["compiler_error"] = str(error)[:240]
        _upsert({**row, "question": case.runtime.question, "context": contexts[case.instance_id]})
        rows.append(row)
    if (
        len(rows) != MAX_CALLS
        or sum(int(row.get("provider_calls", 0)) for row in rows) != MAX_CALLS
    ):
        raise M30_1Error("M30.1 did not account for exactly 162 calls")
    result = {
        "classification": "M30.1_FROZEN_SCALE_OUT_SEMANTIC_BASELINE_COMPLETED",
        "experiment": {
            "milestone": "M30.1",
            "cases": MAX_CALLS,
            "provider_calls": MAX_CALLS,
            "calls_per_case_max": 1,
            "timeout_seconds": TIMEOUT_SECONDS,
            "intervention": "NONE",
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
        "population": {
            "eligible_select": 180,
            "diagnostic_pilot": 18,
            "analysis_cases": MAX_CALLS,
            "untouched_holdout": 0,
        },
        "acquisition": {
            "requests": MAX_CALLS,
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
            "exact_oracle_matches": sum(row.get("exact_oracle_match") is True for row in rows),
            "comparable_plans": sum("component_scores" in row for row in rows),
            "metadata_blocked": sum(bool(row.get("metadata_blocked")) for row in rows),
            "oracle_unavailable": sum(bool(row.get("oracle_failure")) for row in rows),
            "reference_unavailable": sum(bool(row.get("reference_failure")) for row in rows),
            "component_scores": {
                key: {
                    "applicable": sum(key in row.get("component_scores", {}) for row in rows),
                    "correct": sum(bool(row.get("component_scores", {}).get(key)) for row in rows),
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
            "frozen_primary_calls": MAX_CALLS,
            "semantic_retries": 0,
            "repair_calls": 0,
            "direct_calls": 0,
        },
        "cases": [_safe(row) for row in rows],
    }
    RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.preflight == args.run:
        raise SystemExit("choose exactly one of --preflight or --run")
    if args.preflight:
        cases, eligible, pilot = _load_cases()
        _write_manifest(cases, eligible, pilot)
        print(
            json.dumps(
                {
                    "classification": "M30.1_PREFLIGHT_PASSED",
                    "eligible": len(eligible),
                    "pilot": len(pilot),
                    "analysis": len(cases),
                    "provider_calls": 0,
                },
                sort_keys=True,
            )
        )
    else:
        print(json.dumps(asyncio.run(run()), sort_keys=True))


if __name__ == "__main__":
    main()
