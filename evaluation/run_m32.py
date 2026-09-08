"""M32 task-aligned direct SQL experiment runner.

This evaluation-only harness keeps SQL as the model authority.  The first
model call produces semantic alignment; the server validates and grounds it;
the second model call produces strict structured SQL.  Gold SQL and reference
results are consumed only by the evaluator after provider calls.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    _aligned_sql_messages,
    _query_alignment_messages,
)
from app.generation.semantic_plan_protocol import (
    provider_query_alignment_schema,
    query_alignment_response_format,
    query_sql_response_format,
)
from app.semantics.m32_alignment import (
    AlignmentGrounder,
    AlignmentValidationError,
    QueryAlignmentV1,
    validate_alignment,
)
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from evaluation.external.livesqlbench.evaluator import soft_ex_match
from evaluation.external.livesqlbench.m1 import catalog_for_m1
from evaluation.external.livesqlbench.protected import (
    LiveSqlBenchEvaluationCase,
)
from evaluation.m25_2_livesqlbench_direct_semantic_context import _preflight
from evaluation.m32_alignment import (
    alignment_component_match,
    alignment_is_sufficient,
    oracle_alignment_from_plan,
)
from evaluation.run_m29_semantic_plan import (
    DB_IMAGE_DIGEST,
    _semantic_context,
)
from evaluation.semantic_oracle_ceiling import oracle_plan_from_reference_sql

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
PILOT = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
AUTHORITY_MANIFEST = ROOT / "evaluation/fixtures/m31_authority_normalized_manifest.json"
ORACLE_ARTIFACT = ROOT / "evaluation/fixtures/m32_oracle_alignment_ceiling.json"
PREFLIGHT_ARTIFACT = ROOT / "evaluation/fixtures/m32_provider_preflight.json"
VERSION = os.environ.get("M32_VERSION", "v1")
MANIFEST = ROOT / f"evaluation/fixtures/m32_{VERSION}_manifest.json"
RESULT = ROOT / f"evaluation/fixtures/m32_{VERSION}_result.json"
RAW_ROOT = ROOT / "evaluation/external/livesqlbench/protected/results/m32" / VERSION
MODEL = "gpt-5.6-luna"
TIMEOUT_SECONDS = 90


def _metadata_blocked_ids() -> set[str]:
    manifest = json.loads(AUTHORITY_MANIFEST.read_text(encoding="utf-8"))
    return {
        str(row["case_id"])
        for row in manifest["cases"]
        if row["authority_normalized_classification"] == "METADATA_BLOCKED"
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings() -> Settings:
    return get_settings().model_copy(
        update={
            "llm_model": MODEL,
            "llm_temperature": 0.0,
            "llm_reasoning_effort": "none",
            "llm_prompt_profile": "legacy",
            "llm_timeout_seconds": TIMEOUT_SECONDS,
            "eval_capture_model_io": True,
        }
    )


def _load_cases() -> tuple[
    list[LiveSqlBenchEvaluationCase],
    dict[str, str],
    dict[str, SemanticMappingSnapshot],
    dict[str, tuple[Any, Any, Any]],
    dict[str, QueryAlignmentV1 | None],
]:
    cases, preflight, states, _expected = _preflight(
        PUBLIC_ROOT, PROTECTED, validate_frozen_generation_prompt=False
    )
    pilot_ids = json.loads(PILOT.read_text(encoding="utf-8"))["pilot_case_ids"]
    if [case.instance_id for case in cases] != pilot_ids:
        raise RuntimeError("M32 case order differs from the frozen 18-case pilot")
    contexts: dict[str, str] = {}
    mappings: dict[str, SemanticMappingSnapshot] = {}
    oracle_alignments: dict[str, QueryAlignmentV1 | None] = {}
    metadata_blocked = _metadata_blocked_ids()
    for case in cases:
        catalog, _engine, _safety = states[case.database]
        mapping = SemanticMappingSnapshot.from_schema(
            catalog_for_m1(catalog), database_id=case.database
        )
        context = _semantic_context(preflight["corrected_contexts"][case.instance_id], mapping)
        contexts[case.instance_id] = context
        mappings[case.database] = mapping
        if case.instance_id not in metadata_blocked:
            try:
                oracle = oracle_plan_from_reference_sql(
                    case.sol_sql[0], mapping, database_id=case.database
                )
                oracle_alignments[case.instance_id] = oracle_alignment_from_plan(oracle, mapping)
            except Exception as error:
                raise RuntimeError(
                    f"legitimate oracle alignment fixture failed for {case.instance_id}: {error}"
                ) from error
        else:
            oracle_alignments[case.instance_id] = None
    return cases, contexts, mappings, states, oracle_alignments


def build_oracle_ceiling() -> dict[str, Any]:
    cases, contexts, mappings, _states, alignments = _load_cases()
    rows: list[dict[str, Any]] = []
    for case in cases:
        alignment = alignments[case.instance_id]
        if alignment is None:
            rows.append(
                {
                    "case_id": case.instance_id,
                    "classification": "METADATA_BLOCKED",
                    "alignment_fixture": False,
                    "grounder_success": False,
                    "reason": "authority-normalized metadata blocker",
                }
            )
            continue
        grounded = alignment_is_sufficient(alignment, mappings[case.database], case.database)
        rows.append(
            {
                "case_id": case.instance_id,
                "classification": "LEGITIMATELY_SERVER_REPRESENTABLE",
                "alignment_fixture": True,
                "alignment_hash": _hash_json(alignment.model_dump(mode="json")),
                "grounder_success": grounded,
                "context_bytes": len(contexts[case.instance_id]),
            }
        )
    legitimate = [row for row in rows if row["classification"] != "METADATA_BLOCKED"]
    result = {
        "milestone": "M32.0",
        "status": "PASS"
        if len(legitimate) == 15 and all(row["grounder_success"] for row in legitimate)
        else "NO_GO",
        "total_cases": len(rows),
        "legitimate_cases": len(legitimate),
        "alignment_sufficient": sum(row["alignment_fixture"] for row in legitimate),
        "grounder_success": sum(row["grounder_success"] for row in legitimate),
        "metadata_blocked": [
            row["case_id"] for row in rows if row["classification"] == "METADATA_BLOCKED"
        ],
        "gold_derived_metadata_added": False,
        "rows": rows,
    }
    ORACLE_ARTIFACT.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if result["status"] != "PASS":
        raise RuntimeError("M32 oracle alignment/grounding ceiling failed")
    return result


def _manifest(
    cases: list[LiveSqlBenchEvaluationCase],
    contexts: dict[str, str],
    mappings: dict[str, SemanticMappingSnapshot],
    preflight_calls: int,
) -> dict[str, Any]:
    alignment_schema = provider_query_alignment_schema()
    sql_schema = query_sql_response_format()
    ids = [case.instance_id for case in cases]
    return {
        "milestone": f"M32-{VERSION}",
        "preservation_checkpoint": "1d69df0",
        "architecture_checkpoint": "c79b5c4bc6e4897a7f0112ce35b9a154152ac26a",
        "provider": "openai-compatible",
        "model": MODEL,
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "alignment_calls_per_case": 1,
        "sql_calls_per_groundable_case": 1,
        "semantic_retries": 0,
        "repair_calls": 0,
        "review_calls": 0,
        "selector_calls": 0,
        "pass_at_k": 0,
        "case_order_hash": _hash_json(ids),
        "authority_manifest_hash": _hash_file(AUTHORITY_MANIFEST),
        "alignment_schema_hash": _hash_json(alignment_schema),
        "alignment_schema_bytes": len(json.dumps(alignment_schema, separators=(",", ":"))),
        "alignment_schema_definitions": len(alignment_schema.get("$defs", {})),
        "alignment_schema_anyOf": len(alignment_schema.get("anyOf", [])),
        "sql_schema_hash": _hash_json(sql_schema),
        "alignment_prompt_hash": _sha256(inspect.getsource(_query_alignment_messages)),
        "sql_prompt_hash": _sha256(inspect.getsource(_aligned_sql_messages)),
        "alignment_code_hash": _hash_file(ROOT / "app/semantics/m32_alignment.py"),
        "provider_code_hash": _hash_file(ROOT / "app/generation/provider.py"),
        "context_hashes": {case_id: _sha256(contexts[case_id]) for case_id in ids},
        "context_profile": "M29R.1 corrected semantic context plus server mapping IDs",
        "mapping_hashes": {
            database: mapping.content_hash for database, mapping in mappings.items()
        },
        "db_image_digest": DB_IMAGE_DIGEST,
        "preflight_provider_calls": preflight_calls,
        "raw_artifact_root": str(RAW_ROOT),
    }


def _write_capture(case_id: str, stage: str, capture: Any | None) -> None:
    if capture is None:
        return
    path = RAW_ROOT / case_id
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{stage}.json").write_text(
        json.dumps(capture.model_dump(mode="json"), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _base_row(case: LiveSqlBenchEvaluationCase) -> dict[str, Any]:
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "authority_metadata_blocked": case.instance_id in _metadata_blocked_ids(),
        "alignment_call_made": False,
        "alignment_response_received": False,
        "alignment_schema_valid": False,
        "alignment_semantic_valid": False,
        "grounding_status": "NOT_REACHED",
        "sql_call_made": False,
        "sql_response_received": False,
        "sql_schema_valid": False,
        "m1": "NOT_REACHED",
        "explain": "NOT_REACHED",
        "executed": "NOT_REACHED",
        "official": "NOT_REACHED",
        "primary_failure": None,
        "secondary_failures": [],
        "alignment_correct": None,
        "alignment_components": {},
        "alignment_response_hash": None,
        "sql_response_hash": None,
        "generated_sql_hash": None,
    }


async def run_v1() -> dict[str, Any]:
    oracle = build_oracle_ceiling()
    del oracle
    settings = _settings()
    cases, contexts, mappings, states, expected_alignments = _load_cases()
    manifest = _manifest(cases, contexts, mappings, preflight_calls=2)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provider = OpenAICompatibleProvider(settings)
    rows: list[dict[str, Any]] = []
    for ordinal, case in enumerate(cases, start=1):
        started = time.perf_counter()
        row = _base_row(case)
        row["ordinal"] = ordinal
        try:
            row["alignment_call_made"] = True
            alignment_proposal = await provider.propose_query_alignment(
                case.runtime.question, contexts[case.instance_id]
            )
            alignment_capture = provider.consume_model_io()
            _write_capture(case.instance_id, "alignment", alignment_capture)
            row["alignment_response_received"] = alignment_capture is not None
            row["alignment_schema_valid"] = True
            row["alignment_response_hash"] = (
                alignment_capture.raw_assistant_content_sha256 if alignment_capture else None
            )
            row["alignment_latency_ms"] = alignment_proposal.latency_ms
            alignment = alignment_proposal.alignment
            try:
                validate_alignment(alignment, mappings[case.database])
                row["alignment_semantic_valid"] = True
            except AlignmentValidationError as error:
                row["primary_failure"] = f"ALIGNMENT_{error.code}"
                continue
            expected = expected_alignments[case.instance_id]
            if expected is not None:
                components = alignment_component_match(alignment, expected)
                row["alignment_components"] = components
                row["alignment_correct"] = all(components.values())
            try:
                grounded = AlignmentGrounder(mappings[case.database], case.database).ground(
                    case.runtime.question, alignment
                )
                row["grounding_status"] = "SUCCESS"
                row["grounded_context_hash"] = _sha256(grounded.serialize())
                row["grounded_context_bytes"] = len(grounded.serialize())
            except AlignmentValidationError as error:
                row["grounding_status"] = error.code
                if (
                    error.code in {"MISSING_SERVER_RELATIONSHIP", "UNKNOWN_RELATIONSHIP"}
                    and case.instance_id in _metadata_blocked_ids()
                ):
                    row["primary_failure"] = "METADATA_BLOCKED"
                else:
                    row["primary_failure"] = "SERVER_GROUNDING_ERROR"
                continue
            row["sql_call_made"] = True
            sql_proposal = await provider.propose_aligned_sql(
                case.runtime.question, grounded.serialize()
            )
            sql_capture = provider.consume_model_io()
            _write_capture(case.instance_id, "sql", sql_capture)
            row["sql_response_received"] = sql_capture is not None
            row["sql_schema_valid"] = True
            row["sql_response_hash"] = (
                sql_capture.raw_assistant_content_sha256 if sql_capture else None
            )
            row["generated_sql_hash"] = _sha256(sql_proposal.sql)
            row["sql_latency_ms"] = sql_proposal.latency_ms
            safety = states[case.database][2]
            planned = safety.plan(SqlCandidate(sql=sql_proposal.sql))
            if not isinstance(planned, QueryPlan):
                row["m1"] = "REJECTED"
                row["explain"] = "NOT_REACHED"
                row["primary_failure"] = "M1_REJECT"
                if isinstance(planned, SqlPlanFailure):
                    row["m1_failure"] = planned.status.value
                continue
            row["m1"] = "ACCEPTED"
            row["explain"] = "ACCEPTED"
            row["explain_estimate"] = planned.estimate.model_dump(mode="json")
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution):
                row["executed"] = "FAILED"
                row["primary_failure"] = "EXECUTION_ERROR"
                continue
            row["executed"] = "SUCCESS"
            reference = states[case.database][2].plan(SqlCandidate(sql=case.sol_sql[0]))
            if not isinstance(reference, QueryPlan):
                row["official"] = "LIMITATION"
                row["primary_failure"] = "EVALUATOR_LIMITATION"
            else:
                reference_execution = states[case.database][2].execute(reference)
                if not isinstance(reference_execution, QueryExecution):
                    row["official"] = "LIMITATION"
                    row["primary_failure"] = "EVALUATOR_LIMITATION"
                elif soft_ex_match(
                    _result(execution),
                    _result(reference_execution),
                    ordered=bool(case.public.conditions.get("order", False)),
                ):
                    row["official"] = "CORRECT"
                else:
                    row["official"] = "INCORRECT"
                    row["primary_failure"] = "SQL_SYNTHESIS_ERROR"
        except LLMProviderError as error:
            capture = provider.consume_model_io()
            stage = capture.operation if capture is not None else "transport"
            if row["alignment_call_made"] and not row["alignment_response_received"]:
                _write_capture(case.instance_id, "alignment", capture)
                row["primary_failure"] = "ALIGNMENT_TRANSPORT_FAILURE"
            elif row["sql_call_made"] and not row["sql_response_received"]:
                _write_capture(case.instance_id, "sql", capture)
                row["primary_failure"] = "SQL_TRANSPORT_FAILURE"
            else:
                row["primary_failure"] = "PROVIDER_ERROR"
            row["provider_error_type"] = type(error).__name__
            row["provider_stage"] = stage
        except Exception as error:
            capture = provider.consume_model_io()
            if row["alignment_call_made"] and not row["alignment_response_received"]:
                _write_capture(case.instance_id, "alignment", capture)
                row["primary_failure"] = "ALIGNMENT_PROTOCOL_ERROR"
            elif row["sql_call_made"] and not row["sql_response_received"]:
                _write_capture(case.instance_id, "sql", capture)
                row["primary_failure"] = "SQL_PROTOCOL_ERROR"
            else:
                row["primary_failure"] = row["primary_failure"] or type(error).__name__
            row["error_type"] = type(error).__name__
            row["error_message"] = str(error)[:240]
        finally:
            row["total_latency_ms"] = (time.perf_counter() - started) * 1000
            rows.append(row)
            print(
                json.dumps(
                    {
                        "completed": ordinal,
                        "case_id": case.instance_id,
                        "alignment": row["alignment_semantic_valid"],
                        "grounding": row["grounding_status"],
                        "m1": row["m1"],
                        "official": row["official"],
                        "failure": row["primary_failure"],
                    }
                ),
                flush=True,
            )
    result = _summarize(rows, cases, manifest)
    RESULT.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return result


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


def _summarize(
    rows: list[dict[str, Any]], cases: list[LiveSqlBenchEvaluationCase], manifest: dict[str, Any]
) -> dict[str, Any]:
    primary = Counter(row.get("primary_failure") for row in rows if row.get("primary_failure"))
    comparable = [row for row in rows if not row["authority_metadata_blocked"]]
    fully_aligned = [row for row in rows if row.get("alignment_correct") is True]
    matrix = {
        "alignment_correct_sql_correct": sum(
            row.get("alignment_correct") is True and row.get("official") == "CORRECT"
            for row in rows
        ),
        "alignment_correct_sql_wrong": sum(
            row.get("alignment_correct") is True and row.get("official") == "INCORRECT"
            for row in rows
        ),
        "alignment_wrong_sql_correct": sum(
            row.get("alignment_correct") is False and row.get("official") == "CORRECT"
            for row in rows
        ),
        "alignment_wrong_sql_wrong": sum(
            row.get("alignment_correct") is False and row.get("official") == "INCORRECT"
            for row in rows
        ),
    }
    component_counts: dict[str, dict[str, int]] = {}
    for component in (
        "entities",
        "attributes",
        "relationships",
        "population",
        "filters",
        "aggregation",
        "grouping",
        "calculation",
        "ordering",
        "limit",
        "temporal",
        "query_shape",
    ):
        applicable = [row for row in rows if component in row.get("alignment_components", {})]
        component_counts[component] = {
            "applicable": len(applicable),
            "correct": sum(bool(row["alignment_components"].get(component)) for row in applicable),
        }
    latencies = [float(row["total_latency_ms"]) for row in rows if row.get("total_latency_ms")]
    return {
        "milestone": f"M32-{VERSION}",
        "status": "COMPLETE",
        "manifest_hash": _hash_json(manifest),
        "funnel": {
            "cases": len(rows),
            "alignment_calls": sum(row["alignment_call_made"] for row in rows),
            "alignment_responses": sum(row["alignment_response_received"] for row in rows),
            "alignment_schema_valid": sum(row["alignment_schema_valid"] for row in rows),
            "alignment_semantic_valid": sum(row["alignment_semantic_valid"] for row in rows),
            "groundable": sum(row["grounding_status"] == "SUCCESS" for row in rows),
            "metadata_blocked": sum(row["grounding_status"] == "METADATA_BLOCKED" for row in rows),
            "sql_calls": sum(row["sql_call_made"] for row in rows),
            "sql_responses": sum(row["sql_response_received"] for row in rows),
            "sql_schema_valid": sum(row["sql_schema_valid"] for row in rows),
            "m1_accepted": sum(row["m1"] == "ACCEPTED" for row in rows),
            "explain_accepted": sum(row["explain"] == "ACCEPTED" for row in rows),
            "executed": sum(row["executed"] == "SUCCESS" for row in rows),
            "official_correct": sum(row["official"] == "CORRECT" for row in rows),
            "evaluator_limitation": sum(row["official"] == "LIMITATION" for row in rows),
        },
        "authority_normalized": {
            "cases": len(comparable),
            "official_correct": sum(row["official"] == "CORRECT" for row in comparable),
            "executed": sum(row["executed"] == "SUCCESS" for row in comparable),
        },
        "alignment_component_accuracy": component_counts,
        "alignment_sql_matrix": matrix,
        "sql_correct_conditional_on_alignment": {
            "aligned_cases": len(fully_aligned),
            "correct": sum(row.get("official") == "CORRECT" for row in fully_aligned),
        },
        "primary_failure_counts": dict(primary),
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "median": sorted(latencies)[len(latencies) // 2] if latencies else None,
            "p90": sorted(latencies)[min(len(latencies) - 1, int((len(latencies) - 1) * 0.9))]
            if latencies
            else None,
            "max": max(latencies) if latencies else None,
        },
        "provider_calls": {
            "alignment": len(rows),
            "sql": sum(row["sql_call_made"] for row in rows),
            "review": 0,
            "repair": 0,
        },
        "rows": rows,
        "cases": [case.instance_id for case in cases],
        "raw_artifacts": str(RAW_ROOT),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("oracle", "preflight", "v1"), default="oracle")
    args = parser.parse_args()
    if args.phase == "oracle":
        print(json.dumps(build_oracle_ceiling(), indent=2))
        return
    if args.phase == "preflight":
        result = asyncio.run(run_provider_smoke())
        PREFLIGHT_ARTIFACT.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return
    result = asyncio.run(run_v1())
    print(json.dumps(result["funnel"], indent=2))


async def run_provider_smoke() -> dict[str, Any]:
    """Run one synthetic two-call provider smoke before the frozen 18."""

    from app.catalog.default import build_default_catalog
    from app.db.models import Base
    from app.db.session import build_reader_engine
    from app.sql.service import SqlSafetyService

    settings = _settings()
    provider = OpenAICompatibleProvider(settings)
    catalog = build_default_catalog(Base.metadata)
    mapping = SemanticMappingSnapshot.from_schema(catalog)
    if not mapping.entities or not mapping.attributes:
        raise RuntimeError("default catalog has no synthetic alignment target")
    entity = mapping.entities[0]
    attribute = next(item for item in mapping.attributes if item.entity_id == entity.entity_id)
    context = "SERVER-OWNED SEMANTIC MAPPING:\n" + json.dumps(
        mapping.model_dump(mode="json"), sort_keys=True
    )
    question = (
        f"List the {attribute.description or attribute.attribute_id} values from "
        f"{entity.description or entity.entity_id}."
    )
    alignment_proposal = await provider.propose_query_alignment(question, context)
    alignment_capture = provider.consume_model_io()
    validate_alignment(alignment_proposal.alignment, mapping)
    grounded = AlignmentGrounder(mapping, "synthetic").ground(
        question, alignment_proposal.alignment
    )
    sql_proposal = await provider.propose_aligned_sql(question, grounded.serialize())
    sql_capture = provider.consume_model_io()
    engine = build_reader_engine(settings)
    service = SqlSafetyService(engine, settings=settings, catalog=catalog)
    planned = service.plan(SqlCandidate(sql=sql_proposal.sql))
    execution = service.execute(planned) if isinstance(planned, QueryPlan) else planned
    engine.dispose()
    return {
        "milestone": "M32.0",
        "status": "PASSED" if isinstance(execution, QueryExecution) else "M1_OR_EXECUTION_FAILED",
        "model": MODEL,
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "response_format": {
            "alignment": query_alignment_response_format(),
            "sql": query_sql_response_format(),
        },
        "alignment_response": alignment_capture is not None,
        "alignment_schema_valid": True,
        "grounder_success": True,
        "sql_response": sql_capture is not None,
        "sql_schema_valid": True,
        "m1": isinstance(planned, QueryPlan),
        "explain": isinstance(planned, QueryPlan),
        "executed": isinstance(execution, QueryExecution),
        "alignment_latency_ms": alignment_proposal.latency_ms,
        "sql_latency_ms": sql_proposal.latency_ms,
        "alignment_response_hash": alignment_capture.raw_assistant_content_sha256
        if alignment_capture
        else None,
        "sql_response_hash": sql_capture.raw_assistant_content_sha256 if sql_capture else None,
        "gold_derived_metadata_added": False,
    }


if __name__ == "__main__":
    main()
