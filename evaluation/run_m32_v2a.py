"""M32 v2A: schema alignment, logical synthesis, then SQL generation.

This is the evidence-driven staged variant selected after M32 v1.  It keeps
SQL as the model authority and adds no executable intermediate representation.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections import Counter
from typing import Any

from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    _aligned_sql_messages,
    _logical_synthesis_messages,
    _schema_alignment_messages,
)
from app.generation.semantic_plan_protocol import (
    logical_synthesis_response_format,
    query_sql_response_format,
    schema_alignment_response_format,
)
from app.semantics.m32_alignment import (
    AlignmentGrounder,
    AlignmentValidationError,
    LogicalSynthesisV1,
    combine_schema_and_logic,
    validate_alignment,
    validate_logical_synthesis,
    validate_schema_alignment,
)
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from evaluation.external.livesqlbench.evaluator import soft_ex_match
from evaluation.m32_alignment import alignment_component_match
from evaluation.run_m29_semantic_plan import DB_IMAGE_DIGEST
from evaluation.run_m32 import (
    AUTHORITY_MANIFEST,
    MODEL,
    ROOT,
    TIMEOUT_SECONDS,
    _hash_file,
    _load_cases,
    _metadata_blocked_ids,
    _result,
    _settings,
    _sha256,
)

VERSION = "v2"
MANIFEST = ROOT / "evaluation/fixtures/m32_v2_manifest.json"
RESULT = ROOT / "evaluation/fixtures/m32_v2_result.json"
V2_RAW_ROOT = ROOT / "evaluation/external/livesqlbench/protected/results/m32" / VERSION


def _hash_json(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _write_capture(case_id: str, stage: str, capture: Any | None) -> None:
    if capture is None:
        return
    path = V2_RAW_ROOT / case_id
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{stage}.json").write_text(
        json.dumps(capture.model_dump(mode="json"), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _manifest(
    cases: list[Any], contexts: dict[str, str], mappings: dict[str, Any]
) -> dict[str, Any]:
    schema = schema_alignment_response_format()["json_schema"]["schema"]
    logic = logical_synthesis_response_format()["json_schema"]["schema"]
    sql = query_sql_response_format()["json_schema"]["schema"]
    return {
        "milestone": "M32-v2A",
        "architecture": "schema_alignment -> logical_synthesis -> sql",
        "preservation_checkpoint": "1d69df0",
        "architecture_checkpoint": "c79b5c4bc6e4897a7f0112ce35b9a154152ac26a",
        "provider": "openai-compatible",
        "model": MODEL,
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "calls_per_case": 3,
        "semantic_retries": 0,
        "repair_calls": 0,
        "review_calls": 0,
        "selector_calls": 0,
        "pass_at_k": 0,
        "case_order_hash": _hash_json([case.instance_id for case in cases]),
        "authority_manifest_hash": _hash_file(AUTHORITY_MANIFEST),
        "schema_alignment_schema_hash": _hash_json(schema),
        "logical_synthesis_schema_hash": _hash_json(logic),
        "sql_schema_hash": _hash_json(sql),
        "schema_alignment_prompt_hash": _sha256(inspect.getsource(_schema_alignment_messages)),
        "logical_synthesis_prompt_hash": _sha256(inspect.getsource(_logical_synthesis_messages)),
        "sql_prompt_hash": _sha256(inspect.getsource(_aligned_sql_messages)),
        "base_context_hashes": {case_id: _sha256(contexts[case_id]) for case_id in contexts},
        "mapping_hashes": {
            database: mapping.content_hash for database, mapping in mappings.items()
        },
        "db_image_digest": DB_IMAGE_DIGEST,
        "raw_artifact_root": str(V2_RAW_ROOT),
    }


def _row(case: Any, metadata_blocked: set[str]) -> dict[str, Any]:
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "authority_metadata_blocked": case.instance_id in metadata_blocked,
        "schema_call_made": False,
        "schema_response_received": False,
        "schema_valid": False,
        "schema_semantic_valid": False,
        "schema_response_hash": None,
        "logic_call_made": False,
        "logic_response_received": False,
        "logic_schema_valid": False,
        "logic_semantic_valid": False,
        "logic_response_hash": None,
        "grounding_status": "NOT_REACHED",
        "sql_call_made": False,
        "sql_response_received": False,
        "sql_schema_valid": False,
        "sql_response_hash": None,
        "generated_sql_hash": None,
        "m1": "NOT_REACHED",
        "explain": "NOT_REACHED",
        "executed": "NOT_REACHED",
        "official": "NOT_REACHED",
        "primary_failure": None,
    }


async def run() -> dict[str, Any]:
    cases, contexts, mappings, states, expected = _load_cases()
    manifest = _manifest(cases, contexts, mappings)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provider = OpenAICompatibleProvider(_settings())
    metadata_blocked = _metadata_blocked_ids()
    rows: list[dict[str, Any]] = []

    for ordinal, case in enumerate(cases, start=1):
        started = time.perf_counter()
        row = _row(case, metadata_blocked)
        row["ordinal"] = ordinal
        try:
            row["schema_call_made"] = True
            schema_proposal = await provider.propose_schema_alignment(
                case.runtime.question, contexts[case.instance_id]
            )
            schema_capture = provider.consume_model_io()
            _write_capture(case.instance_id, "schema", schema_capture)
            row["schema_response_received"] = schema_capture is not None
            row["schema_response_hash"] = (
                schema_capture.raw_assistant_content_sha256 if schema_capture else None
            )
            row["schema_latency_ms"] = schema_proposal.latency_ms
            schema_alignment = schema_proposal.alignment
            row["schema_valid"] = True
            validate_schema_alignment(schema_alignment, mappings[case.database])
            row["schema_semantic_valid"] = True

            # Ground only the selected schema before logical synthesis.  The empty
            # semantic stage is server-created and is never model-facing.
            empty_logic = LogicalSynthesisV1(logic_summary="schema selection")
            schema_only = combine_schema_and_logic(schema_alignment, empty_logic)
            schema_grounded = AlignmentGrounder(mappings[case.database], case.database).ground(
                case.runtime.question, schema_only
            )

            row["logic_call_made"] = True
            logic_proposal = await provider.propose_logical_synthesis(
                case.runtime.question, schema_grounded.serialize()
            )
            logic_capture = provider.consume_model_io()
            _write_capture(case.instance_id, "logic", logic_capture)
            row["logic_response_received"] = logic_capture is not None
            row["logic_response_hash"] = (
                logic_capture.raw_assistant_content_sha256 if logic_capture else None
            )
            row["logic_latency_ms"] = logic_proposal.latency_ms
            logic = logic_proposal.synthesis
            row["logic_schema_valid"] = True
            validate_logical_synthesis(
                logic,
                mappings[case.database],
                set(schema_alignment.relevant_attribute_ids),
            )
            row["logic_semantic_valid"] = True

            alignment = combine_schema_and_logic(schema_alignment, logic)
            validate_alignment(alignment, mappings[case.database])
            exp = expected[case.instance_id]
            if exp is not None:
                components = alignment_component_match(alignment, exp)
                row["alignment_components"] = components
                row["alignment_correct"] = all(components.values())

            grounded = AlignmentGrounder(mappings[case.database], case.database).ground(
                case.runtime.question, alignment
            )
            row["grounding_status"] = "SUCCESS"
            row["grounded_context_hash"] = _sha256(grounded.serialize())

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
                row["primary_failure"] = "M1_REJECT"
                if isinstance(planned, SqlPlanFailure):
                    row["m1_failure"] = planned.status.value
                continue
            row["m1"] = "ACCEPTED"
            row["explain"] = "ACCEPTED"
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution):
                row["executed"] = "FAILED"
                row["primary_failure"] = "EXECUTION_ERROR"
                continue
            row["executed"] = "SUCCESS"
            reference = safety.plan(SqlCandidate(sql=case.sol_sql[0]))
            if not isinstance(reference, QueryPlan):
                row["official"] = "LIMITATION"
                row["primary_failure"] = "EVALUATOR_LIMITATION"
            else:
                reference_execution = safety.execute(reference)
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
            if row["schema_call_made"] and not row["schema_response_received"]:
                _write_capture(case.instance_id, "schema", capture)
                row["primary_failure"] = "SCHEMA_ALIGNMENT_TRANSPORT_FAILURE"
            elif row["logic_call_made"] and not row["logic_response_received"]:
                _write_capture(case.instance_id, "logic", capture)
                row["primary_failure"] = "LOGICAL_SYNTHESIS_TRANSPORT_FAILURE"
            elif row["sql_call_made"] and not row["sql_response_received"]:
                _write_capture(case.instance_id, "sql", capture)
                row["primary_failure"] = "SQL_TRANSPORT_FAILURE"
            else:
                row["primary_failure"] = "PROVIDER_ERROR"
            row["provider_error_type"] = type(error).__name__
        except AlignmentValidationError as error:
            row["primary_failure"] = "SERVER_" + error.code
            row["validation_error"] = error.code
        except Exception as error:
            capture = provider.consume_model_io()
            if row["schema_call_made"] and not row["schema_response_received"]:
                _write_capture(case.instance_id, "schema", capture)
                row["primary_failure"] = "SCHEMA_ALIGNMENT_PROTOCOL_ERROR"
            elif row["logic_call_made"] and not row["logic_response_received"]:
                _write_capture(case.instance_id, "logic", capture)
                row["primary_failure"] = "LOGICAL_SYNTHESIS_PROTOCOL_ERROR"
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
                        "schema": row["schema_semantic_valid"],
                        "logic": row["logic_semantic_valid"],
                        "grounding": row["grounding_status"],
                        "m1": row["m1"],
                        "official": row["official"],
                        "failure": row["primary_failure"],
                    }
                ),
                flush=True,
            )

    result = summarize(rows, cases, manifest)
    RESULT.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return result


def summarize(
    rows: list[dict[str, Any]], cases: list[Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    primary = Counter(row.get("primary_failure") for row in rows if row.get("primary_failure"))
    comparable = [row for row in rows if not row["authority_metadata_blocked"]]
    components = {}
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
        components[component] = {
            "applicable": len(applicable),
            "correct": sum(bool(row["alignment_components"].get(component)) for row in applicable),
        }
    latencies = [float(row["total_latency_ms"]) for row in rows if row.get("total_latency_ms")]
    return {
        "milestone": "M32-v2A",
        "status": "COMPLETE",
        "manifest_hash": _hash_json(manifest),
        "funnel": {
            "cases": len(rows),
            "schema_calls": sum(row["schema_call_made"] for row in rows),
            "schema_responses": sum(row["schema_response_received"] for row in rows),
            "schema_valid": sum(row["schema_valid"] for row in rows),
            "schema_semantic_valid": sum(row["schema_semantic_valid"] for row in rows),
            "logic_calls": sum(row["logic_call_made"] for row in rows),
            "logic_responses": sum(row["logic_response_received"] for row in rows),
            "logic_schema_valid": sum(row["logic_schema_valid"] for row in rows),
            "logic_semantic_valid": sum(row["logic_semantic_valid"] for row in rows),
            "groundable": sum(row["grounding_status"] == "SUCCESS" for row in rows),
            "sql_calls": sum(row["sql_call_made"] for row in rows),
            "sql_responses": sum(row["sql_response_received"] for row in rows),
            "sql_schema_valid": sum(row["sql_schema_valid"] for row in rows),
            "m1_accepted": sum(row["m1"] == "ACCEPTED" for row in rows),
            "executed": sum(row["executed"] == "SUCCESS" for row in rows),
            "official_correct": sum(row["official"] == "CORRECT" for row in rows),
            "evaluator_limitation": sum(row["official"] == "LIMITATION" for row in rows),
        },
        "authority_normalized": {
            "cases": len(comparable),
            "official_correct": sum(row["official"] == "CORRECT" for row in comparable),
            "executed": sum(row["executed"] == "SUCCESS" for row in comparable),
        },
        "alignment_component_accuracy": components,
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
            "schema_alignment": len(rows),
            "logical_synthesis": sum(row["logic_call_made"] for row in rows),
            "sql": sum(row["sql_call_made"] for row in rows),
            "review": 0,
            "repair": 0,
        },
        "rows": rows,
        "cases": [case.instance_id for case in cases],
        "raw_artifacts": str(V2_RAW_ROOT),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
