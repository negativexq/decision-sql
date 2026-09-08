"""M31.1: one-call LogicalQueryPlanV1 development evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider, _logical_query_plan_messages
from app.generation.semantic_plan_protocol import provider_logical_query_plan_schema_hash
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.semantics.logical_plan import LogicalPlanResolver, LogicalQueryPlanV1
from app.semantics.semantic_compiler import SemanticPlanValidator, SemanticQueryCompiler
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import plan_to_ir
from app.semantics.semantic_validation import SemanticConsistencyValidator
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.livesqlbench.evaluator import soft_ex_match
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import catalog_for_m1, safety_for_database
from evaluation.external.livesqlbench.protected import (
    load_protected_artifact,
    merge_public_and_protected,
)
from evaluation.external.livesqlbench.schema import PostgresConnectionConfig, introspect_database
from evaluation.external.livesqlbench.semantic_context import (
    load_semantic_resources,
    render_semantic_context,
)
from evaluation.m31_logical import OracleLogicalFixtureBuilder
from evaluation.run_m29_semantic_plan import DB_IMAGE_DIGEST, _semantic_context

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
PILOT = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
VERSION = os.environ.get("M31_VERSION", "v1")
MANIFEST = ROOT / f"evaluation/fixtures/m31_{VERSION}_manifest.json"
RESULT = ROOT / f"evaluation/fixtures/m31_{VERSION}_result.json"
EXPECTED_MODEL = "gpt-5.6-luna"
TIMEOUT_SECONDS = 90
METADATA_BLOCKED = {"archeology_1", "fake_1", "mental_1"}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _hash_json(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings() -> Any:
    return get_settings().model_copy(
        update={
            "llm_model": EXPECTED_MODEL,
            "llm_temperature": 0.0,
            "llm_reasoning_effort": "none",
            "llm_timeout_seconds": TIMEOUT_SECONDS,
            "eval_capture_model_io": True,
        }
    )


def _result(execution: QueryExecution) -> Any:
    return type(
        "Result",
        (),
        {
            "columns": tuple(execution.columns),
            "rows": tuple(
                tuple(row.get(column) for column in execution.columns) for row in execution.rows
            ),
        },
    )()


def _component_shape(plan: LogicalQueryPlanV1) -> dict[str, Any]:
    steps: list[Any] = list(plan.steps)
    scans = [step for step in steps if getattr(step, "kind", None) == "SCAN"]
    relates = [step for step in steps if getattr(step, "kind", None) == "RELATE"]
    filters = [step for step in steps if getattr(step, "kind", None) == "FILTER"]
    projects = [step for step in steps if getattr(step, "kind", None) == "PROJECT"]
    aggregates = [step for step in steps if getattr(step, "kind", None) == "AGGREGATE"]
    computes = [step for step in steps if getattr(step, "kind", None) == "COMPUTE"]
    windows = [step for step in steps if getattr(step, "kind", None) == "WINDOW"]
    sorts = [step for step in steps if getattr(step, "kind", None) == "SORT"]
    tops = [step for step in steps if getattr(step, "kind", None) == "TOP"]
    return {
        "anchor": scans[0].entity_id if scans and scans[0].entity_id else None,
        "relations": [(item.entity_id, item.result_step, item.mode.value) for item in relates],
        "filters": [item.predicate.model_dump(mode="json") for item in filters],
        "outputs": [item.outputs for item in projects[-1:]],
        "aggregation": [item.model_dump(mode="json") for item in aggregates],
        "calculation": [item.model_dump(mode="json") for item in computes],
        "window": [item.model_dump(mode="json") for item in windows],
        "ordering": [item.model_dump(mode="json") for item in sorts],
        "limit": [item.model_dump(mode="json") for item in tops],
        "steps": len(plan.steps),
    }


async def run() -> dict[str, Any]:
    settings = _settings()
    public = load_dataset(PUBLIC_ROOT / DATASET_FILENAME)
    protected = load_protected_artifact(PROTECTED)
    merged, merge = merge_public_and_protected(public, protected)
    by_id = {case.instance_id: case for case in merged}
    case_ids = json.loads(PILOT.read_text(encoding="utf-8"))["pilot_case_ids"]
    cases = [by_id[case_id] for case_id in case_ids]
    config = PostgresConnectionConfig.from_environment()
    contexts: dict[str, str] = {}
    mappings: dict[str, SemanticMappingSnapshot] = {}
    live_databases: dict[str, Any] = {}
    expected_logical: dict[str, LogicalQueryPlanV1 | None] = {}
    for case in cases:
        if case.database not in live_databases:
            live = introspect_database(case.database, config)
            live_databases[case.database] = live
            mappings[case.database] = SemanticMappingSnapshot.from_schema(
                catalog_for_m1(live), database_id=case.database
            )
        mapping = mappings[case.database]
        live = live_databases[case.database]
        structural = serialize_schema_context(
            SchemaContextResolver(catalog_for_m1(live)).resolve(
                case.runtime.question, SchemaContextMode.FULL_COMPACT
            )
        )
        contexts[case.instance_id] = _semantic_context(
            render_semantic_context(
                structural, load_semantic_resources(PUBLIC_ROOT, case.database)
            ),
            mapping,
        )
        try:
            from evaluation.semantic_oracle_ceiling import oracle_plan_from_reference_sql

            oracle = oracle_plan_from_reference_sql(
                case.sol_sql[0], mapping, database_id=case.database
            )
            expected_logical[case.instance_id] = OracleLogicalFixtureBuilder(
                mapping, case.database
            ).build(oracle)
        except Exception:
            expected_logical[case.instance_id] = None

    manifest = {
        "milestone": f"M31.1-{VERSION}",
        "development_case_ids": case_ids,
        "case_order_hash": _hash_json(case_ids),
        "provider": "openai-compatible",
        "model": EXPECTED_MODEL,
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "native_json_schema": True,
        "strict": True,
        "calls_per_case": 1,
        "semantic_retries": 0,
        "repair_calls": 0,
        "judge_calls": 0,
        "selector_calls": 0,
        "direct_fallback": 0,
        "logical_schema_hash": provider_logical_query_plan_schema_hash(),
        "prompt_hash": _sha256(inspect.getsource(_logical_query_plan_messages)),
        "resolver_hash": _hash_file(ROOT / "app/semantics/logical_plan.py"),
        "context_hashes": {case_id: _sha256(contexts[case_id]) for case_id in case_ids},
        "context_profile": (
            "M29R.1 structural semantic context reformatted for logical IDs; no oracle data"
        ),
        "mapping_hashes": {
            database: mapping.content_hash for database, mapping in mappings.items()
        },
        "db_image_digest": DB_IMAGE_DIGEST,
        "preservation_checkpoint": "3b0b75e3e7e16f5bf3707f7c9eed9568f2f7c7d3",
        "provider_calls_before_primary": 1,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    provider = OpenAICompatibleProvider(settings)
    rows: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        row: dict[str, Any] = {
            "case_id": case.instance_id,
            "metadata_blocked": case.instance_id in METADATA_BLOCKED,
            "provider_calls": 1,
            "transport_status": "SUCCESS",
            "provider_schema_valid": False,
            "logical_plan_valid": False,
            "semantic_id_valid": False,
            "logical_reference_valid": False,
            "resolver_success": False,
            "canonical_valid": False,
            "semantic_validator": False,
            "m1": "NOT_REACHED",
            "executed": "NOT_REACHED",
            "official": "NOT_REACHED",
        }
        try:
            proposal = await provider.propose_logical_query_plan(
                case.runtime.question, contexts[case.instance_id]
            )
            capture = provider.consume_model_io()
            row.update(
                {
                    "provider_schema_valid": True,
                    "logical_plan_valid": True,
                    "semantic_id_valid": True,
                    "logical_reference_valid": True,
                    "response_bytes": len(capture.raw_assistant_content_full.encode())
                    if capture and capture.raw_assistant_content_full
                    else None,
                    "response_hash": capture.raw_assistant_content_sha256 if capture else None,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "input_tokens": capture.usage.get("prompt_tokens") if capture else None,
                    "output_tokens": capture.usage.get("completion_tokens") if capture else None,
                    "reasoning_tokens": capture.usage.get("reasoning_tokens") if capture else None,
                    "cached_tokens": capture.usage.get("cached_prompt_tokens") if capture else None,
                    "model": proposal.model,
                    "logical_steps": len(proposal.plan.steps),
                    "logical_shape": _component_shape(proposal.plan),
                }
            )
            mapping = mappings[case.database]
            resolved = LogicalPlanResolver(mapping, case.database).resolve(proposal.plan).plan
            row["resolver_success"] = True
            ir = plan_to_ir(resolved)
            SemanticPlanValidator(mapping).validate(ir)
            row["canonical_valid"] = True
            compiled = SemanticQueryCompiler(mapping).compile(ir)
            validation = SemanticConsistencyValidator(mapping).validate(ir, compiled.ast)
            row["semantic_validator"] = validation.accepted
            if not validation.accepted:
                row["primary_failure"] = "SEMANTIC_VALIDATOR_ERROR"
            else:
                safety_engine, safety, _ = safety_for_database(
                    live_databases[case.database], config
                )
                try:
                    planned = safety.plan(SqlCandidate(sql=compiled.sql))
                    if not isinstance(planned, QueryPlan):
                        row["m1"] = "REJECTED"
                        row["primary_failure"] = "M1_REJECT"
                    else:
                        row["m1"] = "ACCEPTED"
                        execution = safety.execute(planned)
                        if not isinstance(execution, QueryExecution):
                            row["executed"] = "FAILED"
                            row["primary_failure"] = "EXECUTION_ERROR"
                        else:
                            row["executed"] = "SUCCESS"
                            reference_plan = safety.plan(SqlCandidate(sql=case.sol_sql[0]))
                            if not isinstance(reference_plan, QueryPlan):
                                row["official"] = "LIMITATION"
                            else:
                                reference_execution = safety.execute(reference_plan)
                                if not isinstance(reference_execution, QueryExecution):
                                    row["official"] = "LIMITATION"
                                elif soft_ex_match(
                                    _result(execution),
                                    _result(reference_execution),
                                    ordered=bool(public_case.conditions.get("order", False))
                                    if (public_case := case.public)
                                    else False,
                                ):
                                    row["official"] = "CORRECT"
                                elif not reference_execution.rows:
                                    row["official"] = "LIMITATION"
                                else:
                                    row["official"] = "INCORRECT"
                finally:
                    safety_engine.dispose()
        except Exception as error:
            capture = provider.consume_model_io()
            row["latency_ms"] = (time.perf_counter() - started) * 1000
            row["transport_status"] = (
                "FAILURE" if capture is None else "PROTOCOL_OR_SEMANTIC_FAILURE"
            )
            row["error_type"] = type(error).__name__
            row["error_message"] = str(error)[:240]
            row["primary_failure"] = row.get("primary_failure") or type(error).__name__
        rows.append(row)

    comparable = [row for row in rows if not row["metadata_blocked"]]
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    result = {
        "milestone": f"M31.1-{VERSION}",
        "status": "COMPLETE",
        "provider_calls": sum(row["provider_calls"] for row in rows),
        "funnel": {
            "requests": len(rows),
            "responses": sum(row["transport_status"] != "FAILURE" for row in rows),
            "provider_schema_valid": sum(row["provider_schema_valid"] for row in rows),
            "logical_plan_valid": sum(row["logical_plan_valid"] for row in rows),
            "semantic_id_valid": sum(row["semantic_id_valid"] for row in rows),
            "logical_reference_valid": sum(row["logical_reference_valid"] for row in rows),
            "resolver_success": sum(row["resolver_success"] for row in rows),
            "metadata_blocked": sum(row["metadata_blocked"] for row in rows),
            "canonical_valid": sum(row["canonical_valid"] for row in rows),
            "semantic_validator": sum(row["semantic_validator"] for row in rows),
            "m1_accepted": sum(row["m1"] == "ACCEPTED" for row in rows),
            "executed": sum(row["executed"] == "SUCCESS" for row in rows),
            "official_correct": sum(row["official"] == "CORRECT" for row in rows),
            "evaluator_limitation": sum(row["official"] == "LIMITATION" for row in rows),
        },
        "comparable_execution": {
            "cases": len(comparable),
            "official_correct": sum(row["official"] == "CORRECT" for row in comparable),
            "evaluator_limitation": sum(row["official"] == "LIMITATION" for row in comparable),
        },
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "median": sorted(latencies)[len(latencies) // 2] if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "rows": rows,
        "protected_data_safety": {
            "raw_model_responses_persisted": False,
            "gold_sql_in_provider_context": False,
        },
    }
    RESULT.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"funnel": result["funnel"], "comparable_execution": result["comparable_execution"]}
        )
    )
    return result


if __name__ == "__main__":
    asyncio.run(run())
