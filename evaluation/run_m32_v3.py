"""M32 v3: alignment, SQL synthesis, and one bounded diagnostic review."""

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
    _query_alignment_messages,
    _review_aligned_sql_messages,
)
from app.generation.semantic_plan_protocol import (
    provider_query_alignment_schema,
    query_sql_response_format,
)
from app.semantics.m32_alignment import (
    AlignmentGrounder,
    AlignmentValidationError,
    validate_alignment,
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

VERSION = "v3"
MANIFEST = ROOT / "evaluation/fixtures/m32_v3_manifest.json"
RESULT = ROOT / "evaluation/fixtures/m32_v3_result.json"
RAW_ROOT = ROOT / "evaluation/external/livesqlbench/protected/results/m32" / VERSION


def _hash_json(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _write_capture(case_id: str, stage: str, capture: Any | None) -> None:
    if capture is None:
        return
    path = RAW_ROOT / case_id
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{stage}.json").write_text(
        json.dumps(capture.model_dump(mode="json"), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _manifest(
    cases: list[Any], contexts: dict[str, str], mappings: dict[str, Any]
) -> dict[str, Any]:
    alignment = provider_query_alignment_schema()
    sql = query_sql_response_format()["json_schema"]["schema"]
    return {
        "milestone": "M32-v3",
        "architecture": "alignment -> sql -> bounded review",
        "review_axis": "SQL synthesis using M1/EXPLAIN diagnostics only",
        "preservation_checkpoint": "1d69df0",
        "architecture_checkpoint": "c79b5c4bc6e4897a7f0112ce35b9a154152ac26a",
        "provider": "openai-compatible",
        "model": MODEL,
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "calls_per_groundable_case": 3,
        "semantic_retries": 0,
        "repair_calls": 0,
        "selector_calls": 0,
        "pass_at_k": 0,
        "case_order_hash": _hash_json([case.instance_id for case in cases]),
        "authority_manifest_hash": _hash_file(AUTHORITY_MANIFEST),
        "alignment_schema_hash": _hash_json(alignment),
        "sql_schema_hash": _hash_json(sql),
        "alignment_prompt_hash": _sha256(inspect.getsource(_query_alignment_messages)),
        "sql_prompt_hash": _sha256(inspect.getsource(_aligned_sql_messages)),
        "review_prompt_hash": _sha256(inspect.getsource(_review_aligned_sql_messages)),
        "alignment_code_hash": _hash_file(ROOT / "app/semantics/m32_alignment.py"),
        "base_context_hashes": {case_id: _sha256(contexts[case_id]) for case_id in contexts},
        "mapping_hashes": {
            database: mapping.content_hash for database, mapping in mappings.items()
        },
        "db_image_digest": DB_IMAGE_DIGEST,
        "raw_artifact_root": str(RAW_ROOT),
    }


def _row(case: Any, metadata_blocked: set[str]) -> dict[str, Any]:
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "authority_metadata_blocked": case.instance_id in metadata_blocked,
        "alignment_call_made": False,
        "alignment_response_received": False,
        "alignment_schema_valid": False,
        "alignment_semantic_valid": False,
        "grounding_status": "NOT_REACHED",
        "sql_call_made": False,
        "sql_response_received": False,
        "sql_schema_valid": False,
        "initial_m1": "NOT_REACHED",
        "initial_explain": "NOT_REACHED",
        "review_call_made": False,
        "review_response_received": False,
        "review_schema_valid": False,
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
            validate_alignment(alignment, mappings[case.database])
            row["alignment_semantic_valid"] = True
            expected_alignment = expected[case.instance_id]
            if expected_alignment is not None:
                components = alignment_component_match(alignment, expected_alignment)
                row["alignment_components"] = components
                row["alignment_correct"] = all(components.values())

            grounded = AlignmentGrounder(mappings[case.database], case.database).ground(
                case.runtime.question, alignment
            )
            row["grounding_status"] = "SUCCESS"
            row["grounded_context_hash"] = _sha256(grounded.serialize())

            row["sql_call_made"] = True
            initial = await provider.propose_aligned_sql(
                case.runtime.question, grounded.serialize()
            )
            initial_capture = provider.consume_model_io()
            _write_capture(case.instance_id, "sql", initial_capture)
            row["sql_response_received"] = initial_capture is not None
            row["sql_schema_valid"] = True
            row["sql_response_hash"] = (
                initial_capture.raw_assistant_content_sha256 if initial_capture else None
            )
            row["initial_sql_hash"] = _sha256(initial.sql)
            row["initial_sql_latency_ms"] = initial.latency_ms

            safety = states[case.database][2]
            initial_plan = safety.plan(SqlCandidate(sql=initial.sql))
            if isinstance(initial_plan, QueryPlan):
                row["initial_m1"] = "ACCEPTED"
                row["initial_explain"] = "ACCEPTED"
                diagnostic = json.dumps(
                    initial_plan.estimate.model_dump(mode="json"), sort_keys=True
                )
            else:
                row["initial_m1"] = "REJECTED"
                diagnostic = (
                    initial_plan.status.value
                    if isinstance(initial_plan, SqlPlanFailure)
                    else "M1_REJECT"
                )

            row["review_call_made"] = True
            reviewed = await provider.review_aligned_sql(
                case.runtime.question,
                grounded.serialize(),
                initial.sql,
                row["initial_m1"],
                diagnostic,
            )
            review_capture = provider.consume_model_io()
            _write_capture(case.instance_id, "review", review_capture)
            row["review_response_received"] = review_capture is not None
            row["review_schema_valid"] = True
            row["review_response_hash"] = (
                review_capture.raw_assistant_content_sha256 if review_capture else None
            )
            row["reviewed_sql_hash"] = _sha256(reviewed.sql)
            row["review_latency_ms"] = reviewed.latency_ms

            final_plan = safety.plan(SqlCandidate(sql=reviewed.sql))
            if not isinstance(final_plan, QueryPlan):
                row["m1"] = "REJECTED"
                row["primary_failure"] = "M1_REJECT"
                if isinstance(final_plan, SqlPlanFailure):
                    row["m1_failure"] = final_plan.status.value
                continue
            row["m1"] = "ACCEPTED"
            row["explain"] = "ACCEPTED"
            execution = safety.execute(final_plan)
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
            if row["alignment_call_made"] and not row["alignment_response_received"]:
                _write_capture(case.instance_id, "alignment", capture)
                row["primary_failure"] = "ALIGNMENT_TRANSPORT_FAILURE"
            elif row["sql_call_made"] and not row["sql_response_received"]:
                _write_capture(case.instance_id, "sql", capture)
                row["primary_failure"] = "SQL_TRANSPORT_FAILURE"
            elif row["review_call_made"] and not row["review_response_received"]:
                _write_capture(case.instance_id, "review", capture)
                row["primary_failure"] = "REVIEW_TRANSPORT_FAILURE"
            else:
                row["primary_failure"] = "PROVIDER_ERROR"
            row["provider_error_type"] = type(error).__name__
        except AlignmentValidationError as error:
            row["primary_failure"] = "SERVER_" + error.code
            row["validation_error"] = error.code
        except Exception as error:
            capture = provider.consume_model_io()
            if row["alignment_call_made"] and not row["alignment_response_received"]:
                _write_capture(case.instance_id, "alignment", capture)
                row["primary_failure"] = "ALIGNMENT_PROTOCOL_ERROR"
            elif row["sql_call_made"] and not row["sql_response_received"]:
                _write_capture(case.instance_id, "sql", capture)
                row["primary_failure"] = "SQL_PROTOCOL_ERROR"
            elif row["review_call_made"] and not row["review_response_received"]:
                _write_capture(case.instance_id, "review", capture)
                row["primary_failure"] = "REVIEW_PROTOCOL_ERROR"
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
                        "review": row["review_response_received"],
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
    components: dict[str, dict[str, int]] = {}
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
    aligned = [row for row in rows if row.get("alignment_correct") is True]
    return {
        "milestone": "M32-v3",
        "status": "COMPLETE",
        "manifest_hash": _hash_json(manifest),
        "funnel": {
            "cases": len(rows),
            "alignment_calls": sum(row["alignment_call_made"] for row in rows),
            "alignment_responses": sum(row["alignment_response_received"] for row in rows),
            "alignment_schema_valid": sum(row["alignment_schema_valid"] for row in rows),
            "alignment_semantic_valid": sum(row["alignment_semantic_valid"] for row in rows),
            "groundable": sum(row["grounding_status"] == "SUCCESS" for row in rows),
            "sql_calls": sum(row["sql_call_made"] for row in rows),
            "sql_responses": sum(row["sql_response_received"] for row in rows),
            "sql_schema_valid": sum(row["sql_schema_valid"] for row in rows),
            "review_calls": sum(row["review_call_made"] for row in rows),
            "review_responses": sum(row["review_response_received"] for row in rows),
            "review_schema_valid": sum(row["review_schema_valid"] for row in rows),
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
        "sql_correct_conditional_on_alignment": {
            "aligned_cases": len(aligned),
            "correct": sum(row["official"] == "CORRECT" for row in aligned),
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
            "review": sum(row["review_call_made"] for row in rows),
            "repair": 0,
        },
        "rows": rows,
        "cases": [case.instance_id for case in cases],
        "raw_artifacts": str(RAW_ROOT),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
