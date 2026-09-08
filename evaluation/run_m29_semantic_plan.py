"""Run the frozen M29 typed semantic-plan generation experiment.

This is an evaluation-only harness.  The semantic arm makes one provider
request per frozen case, parses a strict :class:`SemanticQueryPlan`, and then
uses the canonical deterministic compiler and the normal LiveSQLBench M1 /
execution boundary.  Oracle plans are loaded only after provider acquisition
for comparison; they are never included in provider context.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import math
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.generation.provider import (
    OpenAICompatibleProvider,
    SemanticQueryPlanProposal,
    _semantic_query_plan_messages,
)
from app.semantics.semantic_compiler import SemanticPlanValidator, SemanticQueryCompiler
from app.semantics.semantic_mapping import (
    SemanticMappingSnapshot,
    render_semantic_mapping_context,
)
from app.semantics.semantic_query import (
    Expression,
    SemanticQueryPlan,
    plan_to_ir,
)
from app.semantics.semantic_validation import SemanticConsistencyValidator
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.external.livesqlbench.m1 import catalog_for_m1
from evaluation.external.livesqlbench.protected import LiveSqlBenchEvaluationCase
from evaluation.m25_2_livesqlbench_direct_semantic_context import (
    _preflight,
    _read_jsonl,
    _read_unique_journal,
    _result_from_execution,
)
from evaluation.semantic_oracle_ceiling import oracle_plan_from_reference_sql

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
DEFAULT_PROTECTED = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
FINAL_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
M25_2_ARTIFACT = ROOT / "evaluation/fixtures/m25_2_livesqlbench_direct_semantic_context_result.json"
M25_2_CASES = (
    ROOT
    / "evaluation/external/livesqlbench/protected/results/m25_2_direct_semantic_context_cases.jsonl"
)
JOURNAL = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m29_semantic_plan_journal.jsonl"
)
LOCAL_CASES = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m29_semantic_plan_cases.jsonl"
)
SAFE_RESULT = ROOT / "evaluation/fixtures/m29_livesqlbench_semantic_plan_result.json"
EXPECTED_MODEL = "gpt-5.6-luna"
MAX_CALLS = 18
DB_IMAGE_DIGEST = "sha256:1ae45d7aa5d64dd8eb82e4058f56b4b9625d5035b9b9dc0d2afa0295d9d3053c"


class M29Error(RuntimeError):
    """A frozen experiment invariant failed."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _upsert_jsonl(path: Path, row: dict[str, Any]) -> None:
    rows = [item for item in _read_jsonl(path) if item.get("case_id") != row.get("case_id")]
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in rows),
        encoding="utf-8",
    )


def _settings() -> Settings:
    settings = get_settings().model_copy(
        update={
            "llm_model": EXPECTED_MODEL,
            "llm_temperature": 0.0,
            "llm_reasoning_effort": "none",
            "llm_prompt_profile": "legacy",
            "eval_capture_model_io": True,
        }
    )
    return settings


def _load_old_control() -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(M25_2_CASES)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = row.get("case_id")
        arm = row.get("m25_2")
        if isinstance(case_id, str) and isinstance(arm, dict):
            result[case_id] = arm
    if len(result) != 18:
        raise M29Error("M25.2 detailed control evidence does not cover exactly 18 cases")
    return result


def _load_journal() -> dict[str, dict[str, Any]]:
    rows = _read_unique_journal(JOURNAL)
    if len(rows) > MAX_CALLS:
        raise M29Error("M29 journal exceeds the 18-call budget")
    for case_id, row in rows.items():
        if int(row.get("provider_calls", row.get("actual_request_count", 0))) != 1:
            raise M29Error(f"M29 journal has invalid call count for {case_id}")
    return rows


def _semantic_context(base_context: str, mapping: SemanticMappingSnapshot) -> str:
    return (
        f"{base_context}\n\n"
        "SERVER-OWNED SEMANTIC VOCABULARY (select IDs only; the server resolves "
        "physical names and relationships):\n"
        f"{render_semantic_mapping_context(mapping)}"
    )


def _leakage_free(
    case: LiveSqlBenchEvaluationCase,
    context: str,
    old_sql: str | None,
) -> bool:
    serialized = context.casefold()
    forbidden_keys = ("sol_sql", "test_cases", "reference result", "evaluator result")
    if any(marker in serialized for marker in forbidden_keys):
        return False
    if any(sql and sql in context for sql in case.sol_sql):
        return False
    if old_sql and old_sql in context:
        return False
    return True


def _expr_signature(expression: Expression | None) -> Any:
    if expression is None:
        return None
    data = expression.model_dump(mode="json", exclude_none=True)
    if data.get("kind") == "attribute":
        return {
            "kind": "attribute",
            "attribute_id": data.get("attribute_id"),
            "relation_ref": data.get("relation_ref"),
        }
    if data.get("kind") in {"literal", "interval", "star"}:
        return data
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in {"semantic_role", "description", "alias"}:
            continue
        if isinstance(value, dict):
            result[key] = _json_signature(value)
        elif isinstance(value, list):
            result[key] = [_json_signature(item) for item in value]
        else:
            result[key] = value
    return result


def _json_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _json_signature(item)
            for key, item in sorted(value.items())
            if key not in {"semantic_role", "description", "alias"}
        }
    if isinstance(value, list):
        return [_json_signature(item) for item in value]
    return value


def _plan_signature(plan: SemanticQueryPlan) -> dict[str, Any]:
    ir = plan_to_ir(plan)
    population = ir.population_contract.model_dump(mode="json", exclude_none=True)
    source: Any = ir.from_entity_id
    if source is None:
        if ir.from_source is None:
            raise M29Error("canonical plan has no relation source")
        source = ir.from_source.model_dump(mode="json")
    return {
        "source": source,
        "population": _json_signature(population),
        "joins": [item.model_dump(mode="json", exclude_none=True) for item in ir.joins],
        "select": [
            {"position": item.position, "expression": _expr_signature(item.expression)}
            for item in ir.select
        ],
        "where": _expr_signature(ir.where),
        "group_by": [_expr_signature(item) for item in ir.group_by],
        "having": _expr_signature(ir.having),
        "order_by": [
            {"expression": _expr_signature(item.expression), "direction": item.direction.value}
            for item in ir.order_by
        ],
        "distinct": ir.distinct,
        "limit": ir.limit,
        "offset": ir.offset,
        "calculation": _json_signature(
            ir.calculation_contract.model_dump(mode="json", exclude_none=True)
            if ir.calculation_contract is not None
            else None
        ),
        "ctes": [
            {
                "cte_id": item.cte_id,
                "exports": [export.model_dump(mode="json") for export in item.exported_attributes],
                "query": _plan_signature(
                    SemanticQueryPlan(
                        database_id=item.query.database_id,
                        from_entity_id=item.query.from_entity_id,
                        from_source=item.query.from_source,
                        population_contract=item.query.population_contract,
                        outputs=tuple(
                            {
                                "position": select.position,
                                "semantic_role": select.alias or f"output_{select.position}",
                                "expression": select.expression,
                                "alias": select.alias,
                            }
                            for select in item.query.select
                        ),
                        joins=item.query.joins,
                        where=item.query.where,
                        group_by=item.query.group_by,
                        having=item.query.having,
                        order_by=item.query.order_by,
                        distinct=item.query.distinct,
                        limit=item.query.limit,
                        offset=item.query.offset,
                        calculation_contract=item.query.calculation_contract,
                        ctes=item.query.ctes,
                        derived_relations=item.query.derived_relations,
                    )
                ),
            }
            for item in ir.ctes
        ],
        "derived_relations": [item.relation_id for item in ir.derived_relations],
    }


def _entity_ids(plan: SemanticQueryPlan, mapping: SemanticMappingSnapshot) -> set[str]:
    result: set[str] = set()
    if plan.from_entity_id is not None:
        result.add(plan.from_entity_id)
    if plan.from_source is not None and hasattr(plan.from_source, "entity_id"):
        result.add(plan.from_source.entity_id)
    for join in plan.joins:
        ids = list(join.relationship_path)
        if join.relationship_id is not None:
            ids.append(join.relationship_id)
        for relationship_id in ids:
            try:
                relation = mapping.relationship(relationship_id)
            except Exception:
                continue
            result.update((relation.from_entity_id, relation.to_entity_id))
    return result


def _component_scores(
    generated: SemanticQueryPlan, oracle: SemanticQueryPlan, mapping: SemanticMappingSnapshot
) -> dict[str, bool]:
    generated_signature = _plan_signature(generated)
    oracle_signature = _plan_signature(oracle)
    return {
        "entities": _entity_ids(generated, mapping) == _entity_ids(oracle, mapping),
        "relationships": generated_signature["joins"] == oracle_signature["joins"],
        "population": generated_signature["population"] == oracle_signature["population"],
        "grain": (
            generated_signature["population"].get("result_grain_attribute_ids")
            == oracle_signature["population"].get("result_grain_attribute_ids")
            and generated_signature["population"].get("aggregation_grain_attribute_ids")
            == oracle_signature["population"].get("aggregation_grain_attribute_ids")
        ),
        "outputs": generated_signature["select"] == oracle_signature["select"],
        "filters": (
            generated_signature["where"] == oracle_signature["where"]
            and generated_signature["having"] == oracle_signature["having"]
        ),
        "aggregation": (
            _aggregate_signatures(generated) == _aggregate_signatures(oracle)
            and generated_signature["group_by"] == oracle_signature["group_by"]
        ),
        "calculation": generated_signature["calculation"] == oracle_signature["calculation"],
        "ordering": generated_signature["order_by"] == oracle_signature["order_by"],
        "limit": generated_signature["limit"] == oracle_signature["limit"],
        "temporal": _temporal_signature(generated) == _temporal_signature(oracle),
        "window": _window_signature(generated) == _window_signature(oracle),
        "nested_structure": (
            generated_signature["ctes"] == oracle_signature["ctes"]
            and generated_signature["derived_relations"] == oracle_signature["derived_relations"]
        ),
    }


def _aggregate_signatures(plan: SemanticQueryPlan) -> list[Any]:
    result: list[Any] = []
    for output in plan.outputs:
        if output.expression is not None:
            result.extend(_find_kind(output.expression, "aggregate"))
    return result


def _find_kind(value: Any, kind: str) -> list[Any]:
    if isinstance(value, dict):
        result: list[Any] = []
        if value.get("kind") == kind:
            result.append(_json_signature(value))
        for item in value.values():
            result.extend(_find_kind(item, kind))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_find_kind(item, kind))
        return result
    if hasattr(value, "model_dump"):
        return _find_kind(value.model_dump(mode="json", exclude_none=True), kind)
    return []


def _temporal_signature(plan: SemanticQueryPlan) -> Any:
    values = _find_kind(plan.where, "function") + _find_kind(plan.where, "interval")
    return _json_signature(values)


def _window_signature(plan: SemanticQueryPlan) -> Any:
    values: list[Any] = []
    for output in plan.outputs:
        values.extend(_find_kind(output.expression, "window"))
    return _json_signature(values)


def _plan_failure(error: Exception) -> str:
    code = getattr(error, "code", None)
    return code.value if code is not None else type(error).__name__


def _m1_status(
    safety: Any, sql: str, correlation_id: str
) -> tuple[dict[str, Any], QueryPlan | None]:
    planned = safety.plan(
        SqlCandidate(
            sql=sql, source=CandidateSource.SEMANTIC_QUERY_COMPILER, correlation_id=correlation_id
        )
    )
    if not isinstance(planned, QueryPlan):
        return {
            "status": "REJECTED",
            "failure_code": planned.rejection.code.value
            if planned.rejection is not None
            else planned.status.value,
            "explain": "REJECTED",
        }, None
    return {"status": "ACCEPTED", "failure_code": None, "explain": "ACCEPTED"}, planned


def _execute_and_score(
    safety: Any,
    plan: QueryPlan,
    expected: LiveSqlBenchResult,
    ordered: bool,
) -> dict[str, Any]:
    execution = safety.execute(plan)
    if not isinstance(execution, QueryExecution):
        return {
            "status": "FAILURE",
            "correct": False,
            "limitation": False,
            "row_count": None,
            "latency_ms": None,
            "error": getattr(execution, "error", "EXECUTION_ERROR"),
        }
    actual = _result_from_execution(execution)
    correct = soft_ex_match(actual, expected, ordered=ordered)
    limitation = not expected.rows
    return {
        "status": "SUCCESS",
        "correct": bool(correct) and not limitation,
        "limitation": limitation,
        "row_count": execution.row_count,
        "latency_ms": execution.latency_ms,
        "error": None,
    }


def _failure_class(
    *,
    oracle_available: bool,
    proposal: SemanticQueryPlanProposal | None,
    plan_error: str | None,
    plan_valid: bool,
    compiled: bool,
    semantic_valid: bool,
    m1: str,
    execution: str,
    official: str,
    component_scores: dict[str, bool] | None,
) -> str:
    if not oracle_available:
        return "METADATA_BLOCKED"
    if proposal is None:
        return plan_error or "PLAN_PARSE_FAILURE"
    if not plan_valid:
        return plan_error or "PLAN_INVALID"
    if not compiled:
        return plan_error or "COMPILER_FAILURE"
    if not semantic_valid:
        return "SEMANTIC_VALIDATOR_FAILURE"
    if m1 != "ACCEPTED":
        return "M1_REJECTION"
    if execution != "SUCCESS":
        return "EXECUTION_FAILURE"
    if official == "EVALUATOR_LIMITATION":
        return "EVALUATOR_LIMITATION"
    if official != "CORRECT":
        if component_scores and all(component_scores.values()):
            return "ENGINE_PARITY_REGRESSION"
        return "PLAN_SEMANTIC_WRONG"
    return "CORRECT"


def _safe_case_record(
    case: LiveSqlBenchEvaluationCase,
    row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "question_hash": row["question_hash"],
        "semantic_context_hash": row["semantic_context_hash"],
        "response_hash": row.get("response_hash"),
        "parsed_plan_hash": row.get("parsed_plan_hash"),
        "plan_status": row.get("plan_status"),
        "primary_failure": row.get("primary_failure"),
        "oracle_plan_match": row.get("oracle_plan_match"),
        "metadata_blocked": row.get("metadata_blocked", False),
        "reference_evaluator_limitation": row.get("reference_evaluator_limitation", False),
        "m1_status": row.get("m1_status"),
        "execution_status": row.get("execution_status"),
        "official_status": row.get("official_status"),
        "direct_status": row.get("direct_status"),
        "provider_calls": row.get("provider_calls", 0),
        "provider_success": row.get("provider_success", False),
        "response_received": row.get("response_received", False),
        "json_parse_status": row.get("json_parse_status", "NOT_AVAILABLE"),
        "usage": row.get("usage", {}),
        "provider_latency_ms": row.get("provider_latency_ms"),
        "end_to_end_latency_ms": row.get("end_to_end_latency_ms"),
        "component_scores": row.get("component_scores", {}),
        "consistency": row.get("consistency", {}),
    }


def _usage(capture: Any) -> dict[str, int | None]:
    usage = capture.usage if capture is not None else {}
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "cached_tokens": usage.get("cached_prompt_tokens"),
    }


def _response_received(row: dict[str, Any]) -> bool:
    if "response_received" in row:
        return bool(row["response_received"])
    return bool(row.get("response_hash")) or bool(
        (row.get("capture") or {}).get("raw_assistant_content")
    )


def _captured_json_status(row: dict[str, Any]) -> str:
    capture = row.get("capture") or {}
    raw = capture.get("raw_assistant_content")
    if not isinstance(raw, str):
        return "NOT_AVAILABLE"
    if capture.get("raw_assistant_content_truncated"):
        return "UNVERIFIED_CAPTURE_TRUNCATED"
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        return "INVALID"
    return "VERIFIED"


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "total": sum(values) if values else 0,
        "median": sorted(values)[len(values) // 2] if values else None,
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _usage_summary(rows: list[dict[str, Any]], key: str) -> dict[str, int | None]:
    values = [row["usage"][key] for row in rows if row.get("usage", {}).get(key) is not None]
    if not values:
        return {"total": None, "median": None, "max": None}
    ordered = sorted(values)
    return {
        "total": sum(values),
        "median": ordered[len(ordered) // 2],
        "max": max(values),
    }


def _mcnemar(b: int, c: int) -> float | None:
    total = b + c
    if not total:
        return None
    lower = sum(math.comb(total, index) for index in range(min(b, c) + 1)) / (2**total)
    return float(min(1.0, 2.0 * lower))


def _old_correct(old: dict[str, Any]) -> bool:
    return old.get("official_evaluator_status") == "PASS"


def run_preflight(public_root: Path, protected_path: Path) -> dict[str, Any]:
    """Validate the complete M29 input contract without constructing a provider."""
    old_control = _load_old_control()
    pilot_cases, preflight, states, _ = _preflight(
        public_root, protected_path, validate_frozen_generation_prompt=True
    )
    pilot_ids = [case.instance_id for case in pilot_cases]
    manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    if len(pilot_ids) != 18 or len(set(pilot_ids)) != 18:
        raise M29Error("M29 pilot population is not exactly 18 unique cases")
    if tuple(pilot_ids) != tuple(manifest["pilot_case_ids"]):
        raise M29Error("M29 pilot IDs/order differ from the frozen manifest")
    settings = _settings()
    contexts: dict[str, str] = {}
    mappings: dict[str, SemanticMappingSnapshot] = {}
    for case in pilot_cases:
        mapping = SemanticMappingSnapshot.from_schema(
            catalog_for_m1(states[case.database][0]), database_id=case.database
        )
        context = _semantic_context(preflight["corrected_contexts"][case.instance_id], mapping)
        if not _leakage_free(case, context, old_control[case.instance_id].get("generated_sql")):
            raise M29Error(f"semantic context leakage detected for {case.instance_id}")
        messages = _semantic_query_plan_messages(case.runtime.question, context)
        serialized = "\n".join(message["content"] for message in messages)
        if case.runtime.question not in serialized or context not in serialized:
            raise M29Error(f"semantic provider payload is incomplete for {case.instance_id}")
        contexts[case.instance_id] = context
        mappings[case.instance_id] = mapping
    if len(contexts) != 18 or len(mappings) != 18:
        raise M29Error("M29 semantic context coverage is not 18/18")
    result = {
        "classification": "M29_SEMANTIC_PLAN_PREFLIGHT_PASSED",
        "provider_calls": 0,
        "pilot_cases": 18,
        "pilot_order_hash": _sha256_json(pilot_ids),
        "model": settings.llm_model,
        "provider": "openai-compatible",
        "reasoning_effort": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "prompt_profile": settings.llm_prompt_profile,
        "semantic_plan_prompt_hash": _sha256_text(inspect.getsource(_semantic_query_plan_messages)),
        "semantic_schema_hash": _sha256_json(SemanticQueryPlan.model_json_schema()),
        "context_hash": _sha256_json(contexts),
        "context_lengths": {
            "median": sorted(map(len, contexts.values()))[len(contexts) // 2],
            "max": max(map(len, contexts.values())),
        },
        "full_m25_2_context": True,
        "m26_query_aware_grounding": False,
        "db_value_probing": False,
        "gold_leakage": False,
        "old_output_leakage": False,
        "raw_sql_fallback": False,
        "journal_existing_cases": len(_load_journal()),
        "protected_data_safety": {
            "protected_gt_used_for_pilot_identity_and_evaluation_only": True,
            "protected_gt_tracked": False,
            "gold_visible_to_provider": False,
            "oracle_plan_visible_to_provider": False,
        },
    }
    preflight_path = ROOT / "evaluation/fixtures/m29_semantic_plan_preflight.json"
    preflight_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


async def _call_one(
    provider: OpenAICompatibleProvider,
    case: LiveSqlBenchEvaluationCase,
    context: str,
    existing: dict[str, Any] | None,
) -> tuple[SemanticQueryPlanProposal | None, dict[str, Any]]:
    if existing is not None:
        plan_data = existing.get("plan")
        plan = SemanticQueryPlan.model_validate(plan_data) if isinstance(plan_data, dict) else None
        proposal = (
            SemanticQueryPlanProposal(
                plan=plan,
                provider="openai-compatible",
                model=existing.get("model", EXPECTED_MODEL),
                prompt_tokens=existing.get("usage", {}).get("input_tokens"),
                completion_tokens=existing.get("usage", {}).get("output_tokens"),
                reasoning_tokens=existing.get("usage", {}).get("reasoning_tokens"),
                cached_prompt_tokens=existing.get("usage", {}).get("cached_tokens"),
                latency_ms=existing.get("provider_latency_ms"),
            )
            if plan is not None
            else None
        )
        return proposal, existing

    started = time.perf_counter()
    try:
        proposal = await provider.propose_semantic_query_plan(case.runtime.question, context)
        capture = provider.consume_model_io()
        row = {
            "case_id": case.instance_id,
            "provider_calls": 1,
            "provider_success": True,
            "response_received": True,
            "model": proposal.model,
            "response_hash": capture.raw_assistant_content_sha256 if capture else None,
            "plan": proposal.plan.model_dump(mode="json"),
            "parsed_plan_hash": _sha256_json(proposal.plan.model_dump(mode="json")),
            "usage": _usage(capture),
            "provider_latency_ms": (time.perf_counter() - started) * 1000,
            "capture": capture.model_dump(mode="json") if capture else None,
        }
        _append_jsonl(JOURNAL, row)
        return proposal, row
    except Exception as error:
        capture = provider.consume_model_io()
        row = {
            "case_id": case.instance_id,
            "provider_calls": 1,
            "provider_success": False,
            "response_received": capture is not None and capture.raw_assistant_content is not None,
            "model": EXPECTED_MODEL,
            "response_hash": capture.raw_assistant_content_sha256 if capture else None,
            "usage": _usage(capture),
            "provider_latency_ms": (time.perf_counter() - started) * 1000,
            "error_type": type(error).__name__,
            "error_code": getattr(getattr(error, "detail", None), "error_code", None),
            "error_message": str(error)[:240],
            "capture": capture.model_dump(mode="json") if capture else None,
        }
        _append_jsonl(JOURNAL, row)
        return None, row


async def run_experiment(public_root: Path, protected_path: Path) -> dict[str, Any]:
    old_control = _load_old_control()
    pilot_cases, preflight, states, expected = _preflight(
        public_root, protected_path, validate_frozen_generation_prompt=True
    )
    pilot_ids = [case.instance_id for case in pilot_cases]
    if len(pilot_ids) != 18 or len(set(pilot_ids)) != 18:
        raise M29Error("M29 pilot population is not exactly 18 unique cases")
    manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    if tuple(pilot_ids) != tuple(manifest["pilot_case_ids"]):
        raise M29Error("M29 pilot IDs/order differ from the frozen manifest")
    frozen_m25 = json.loads(M25_2_ARTIFACT.read_text(encoding="utf-8"))
    if frozen_m25.get("official") != {"correct": 6, "total": 18, "accuracy": 1 / 3}:
        raise M29Error("M25.2 frozen control is not the historical 6/18 arm")
    settings = _settings()
    if settings.llm_model != EXPECTED_MODEL or settings.llm_temperature != 0.0:
        raise M29Error("M29 provider settings do not match the frozen M25.2 model contract")
    if settings.llm_reasoning_effort != "none":
        raise M29Error("M29 reasoning setting differs from M25.2")

    prompt_source = inspect.getsource(_semantic_query_plan_messages)
    prompt_hash = _sha256_text(prompt_source)
    schema_hash = _sha256_json(SemanticQueryPlan.model_json_schema())
    question_manifest_hash = _sha256_json(pilot_ids)
    journal = _load_journal()
    if any(case_id not in pilot_ids for case_id in journal):
        raise M29Error("M29 journal contains a non-pilot case")

    contexts: dict[str, str] = {}
    mappings: dict[str, SemanticMappingSnapshot] = {}
    for case in pilot_cases:
        live_database = states[case.database][0]
        mapping = SemanticMappingSnapshot.from_schema(
            catalog_for_m1(live_database), database_id=case.database
        )
        context = _semantic_context(preflight["corrected_contexts"][case.instance_id], mapping)
        old_sql = old_control[case.instance_id].get("generated_sql")
        if not _leakage_free(case, context, old_sql):
            raise M29Error(f"semantic context leakage detected for {case.instance_id}")
        contexts[case.instance_id] = context
        mappings[case.instance_id] = mapping

    provider = OpenAICompatibleProvider(settings)
    rows: list[dict[str, Any]] = []
    component_keys = (
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

    for case in pilot_cases:
        started = time.perf_counter()
        mapping = mappings[case.instance_id]
        context = contexts[case.instance_id]
        proposal, journal_row = await _call_one(
            provider, case, context, journal.get(case.instance_id)
        )
        row: dict[str, Any] = {
            "case_id": case.instance_id,
            "database": case.database,
            "question_hash": _sha256_text(case.runtime.question),
            "semantic_context_hash": _sha256_text(context),
            "provider_calls": journal_row.get("provider_calls", 0),
            "provider_success": _response_received(journal_row),
            "response_received": _response_received(journal_row),
            "json_parse_status": _captured_json_status(journal_row),
            "response_hash": journal_row.get("response_hash"),
            "parsed_plan_hash": journal_row.get("parsed_plan_hash"),
            "usage": journal_row.get("usage", {}),
            "provider_latency_ms": journal_row.get("provider_latency_ms"),
            "direct_status": "CORRECT"
            if _old_correct(old_control[case.instance_id])
            else "WRONG_OR_LIMITATION",
            "m1_status": "NOT_REACHED",
            "execution_status": "NOT_REACHED",
            "official_status": "NOT_REACHED",
            "plan_status": "NOT_PARSED",
            "component_scores": {},
            "consistency": {},
            "metadata_blocked": False,
            "reference_evaluator_limitation": not bool(expected[case.instance_id].rows),
        }

        try:
            oracle_plan: SemanticQueryPlan | None
            try:
                oracle_plan = oracle_plan_from_reference_sql(
                    case.sol_sql[0], mapping, database_id=case.database
                )
            except Exception as error:
                if _plan_failure(error) != "UNKNOWN_RELATIONSHIP":
                    raise
                oracle_plan = None
                row["metadata_blocked"] = True
                row["oracle_failure"] = "MISSING_SERVER_OWNED_RELATIONSHIP_METADATA"

            if proposal is None:
                row["plan_status"] = "PLAN_SCHEMA_FAILURE"
                row["primary_failure"] = "PLAN_SCHEMA_FAILURE"
                rows.append(row)
                _upsert_jsonl(
                    LOCAL_CASES, {**row, "question": case.runtime.question, "context": context}
                )
                continue

            row["plan_status"] = "SCHEMA_VALID"
            ir = plan_to_ir(proposal.plan)
            SemanticPlanValidator(mapping).validate(ir)
            row["plan_status"] = "CANONICAL_VALID"
            if oracle_plan is not None:
                scores = _component_scores(proposal.plan, oracle_plan, mapping)
                row["component_scores"] = scores
                row["oracle_plan_match"] = all(scores.values()) and (
                    _plan_signature(proposal.plan) == _plan_signature(oracle_plan)
                )
            compiler = SemanticQueryCompiler(mapping)
            compiled = compiler.compile(ir)
            row["compiled_sql_hash"] = _sha256_text(compiled.sql)
            validation = SemanticConsistencyValidator(mapping).validate(ir, compiled)
            row["consistency"] = {
                "accepted": validation.accepted,
                "failure_codes": [code.value for code, _ in validation.failures],
            }
            if not validation.accepted:
                row["plan_status"] = "SEMANTIC_VALIDATOR_FAILURE"
                row["primary_failure"] = "SEMANTIC_VALIDATOR_FAILURE"
                rows.append(row)
                _upsert_jsonl(
                    LOCAL_CASES,
                    {
                        **row,
                        "question": case.runtime.question,
                        "context": context,
                        "plan": proposal.plan.model_dump(mode="json"),
                        "compiled_sql": compiled.sql,
                    },
                )
                continue
            if oracle_plan is None:
                row["plan_status"] = "CANONICAL_VALID_METADATA_BLOCKED"
                row["primary_failure"] = "METADATA_BLOCKED"
                rows.append(row)
                _upsert_jsonl(
                    LOCAL_CASES,
                    {
                        **row,
                        "question": case.runtime.question,
                        "context": context,
                        "plan": proposal.plan.model_dump(mode="json"),
                        "compiled_sql": compiled.sql,
                    },
                )
                continue
            safety = states[case.database][2]
            m1, planned = _m1_status(safety, compiled.sql, f"m29:{case.instance_id}")
            row["m1_status"] = m1["status"]
            row["m1_failure_code"] = m1["failure_code"]
            row["explain_status"] = m1["explain"]
            if planned is None:
                row["primary_failure"] = "M1_REJECTION"
                rows.append(row)
                _upsert_jsonl(
                    LOCAL_CASES,
                    {
                        **row,
                        "question": case.runtime.question,
                        "context": context,
                        "plan": proposal.plan.model_dump(mode="json"),
                        "compiled_sql": compiled.sql,
                    },
                )
                continue
            expected_result = expected[case.instance_id]
            execution = _execute_and_score(
                safety, planned, expected_result, bool(case.public.conditions.get("order", False))
            )
            row["execution_status"] = execution["status"]
            row["execution_row_count"] = execution["row_count"]
            row["execution_latency_ms"] = execution["latency_ms"]
            row["official_status"] = (
                "EVALUATOR_LIMITATION"
                if execution["limitation"]
                else "CORRECT"
                if execution["correct"]
                else "INCORRECT"
            )
            row["primary_failure"] = _failure_class(
                oracle_available=True,
                proposal=proposal,
                plan_error=None,
                plan_valid=True,
                compiled=True,
                semantic_valid=True,
                m1=row["m1_status"],
                execution=row["execution_status"],
                official=row["official_status"],
                component_scores=row["component_scores"],
            )
            rows.append(row)
            _upsert_jsonl(
                LOCAL_CASES,
                {
                    **row,
                    "question": case.runtime.question,
                    "context": context,
                    "plan": proposal.plan.model_dump(mode="json"),
                    "compiled_sql": compiled.sql,
                    "gold_sql": case.sol_sql[0],
                    "expected_result": {
                        "columns": expected_result.columns,
                        "row_count": len(expected_result.rows),
                    },
                },
            )
        except Exception as error:
            row["primary_failure"] = _plan_failure(error)
            row["error_type"] = type(error).__name__
            row["error_message"] = str(error)[:240]
            rows.append(row)
            _upsert_jsonl(
                LOCAL_CASES, {**row, "question": case.runtime.question, "context": context}
            )
        finally:
            row["end_to_end_latency_ms"] = (time.perf_counter() - started) * 1000

    if sum(int(row.get("provider_calls", 0)) for row in rows) != MAX_CALLS:
        raise M29Error("M29 provider request count is not exactly 18")
    if any(int(row.get("provider_calls", 0)) > 1 for row in rows):
        raise M29Error("M29 exceeded one provider call per case")
    if len(rows) != 18:
        raise M29Error("M29 did not produce exactly 18 case records")

    comparable = [
        row
        for row in rows
        if not row["metadata_blocked"]
        and row["official_status"] != "EVALUATOR_LIMITATION"
        and not row["reference_evaluator_limitation"]
    ]
    transitions: Counter[str] = Counter()
    for row in comparable:
        old = _old_correct(old_control[row["case_id"]])
        new = row["official_status"] == "CORRECT"
        transitions[
            "BOTH_CORRECT"
            if old and new
            else "DIRECT_ONLY"
            if old
            else "SEMANTIC_ONLY"
            if new
            else "BOTH_WRONG"
        ] += 1
    b = transitions["DIRECT_ONLY"]
    c = transitions["SEMANTIC_ONLY"]
    component_totals: dict[str, dict[str, int]] = {}
    for key in component_keys:
        applicable = [row for row in rows if key in row.get("component_scores", {})]
        component_totals[key] = {
            "applicable": len(applicable),
            "correct": sum(bool(row["component_scores"][key]) for row in applicable),
        }
    counts = {
        "provider_requests": sum(int(row.get("provider_calls", 0)) for row in rows),
        "provider_successes": sum(bool(row.get("provider_success")) for row in rows),
        "responses_received": sum(bool(row.get("response_received")) for row in rows),
        "plan_schema_valid": sum(
            row["plan_status"]
            in {"SCHEMA_VALID", "CANONICAL_VALID", "CANONICAL_VALID_METADATA_BLOCKED"}
            for row in rows
        ),
        "canonical_plan_valid": sum(
            row["plan_status"] in {"CANONICAL_VALID", "CANONICAL_VALID_METADATA_BLOCKED"}
            for row in rows
        ),
        "compiled": sum("compiled_sql_hash" in row for row in rows),
        "semantic_valid": sum(row.get("consistency", {}).get("accepted", False) for row in rows),
        "m1_accepted": sum(row.get("m1_status") == "ACCEPTED" for row in rows),
        "executed": sum(row.get("execution_status") == "SUCCESS" for row in rows),
        "official_correct": sum(row.get("official_status") == "CORRECT" for row in rows),
        "evaluator_limitations": sum(
            row.get("official_status") == "EVALUATOR_LIMITATION" for row in rows
        ),
        "metadata_blocked": sum(bool(row.get("metadata_blocked")) for row in rows),
    }
    safe_cases = [_safe_case_record(case, row) for case, row in zip(pilot_cases, rows, strict=True)]
    safe = {
        "classification": "M29_CANONICAL_SEMANTIC_PLAN_EXPERIMENT_COMPLETED",
        "experiment": {
            "milestone": "M29",
            "cases": 18,
            "provider_calls": counts["provider_requests"],
            "calls_per_case_max": max(int(row.get("provider_calls", 0)) for row in rows),
            "route": "SEMANTIC",
            "intervention": "TYPED_SEMANTIC_QUERY_PLAN_OUTPUT",
        },
        "checkpoint": {"commit": _git_head(), "parent": "70eea3cf07e967f9d7141e3592794817516ddb42"},
        "protocol": {
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "reasoning_effort": settings.llm_reasoning_effort,
            "temperature": settings.llm_temperature,
            "prompt_profile": settings.llm_prompt_profile,
            "semantic_plan_prompt_hash": prompt_hash,
            "semantic_schema_hash": schema_hash,
            "question_manifest_hash": question_manifest_hash,
            "db_image_digest": DB_IMAGE_DIGEST,
            "full_m25_2_context": True,
            "m26_query_aware_grounding": False,
            "raw_sql_fallback": False,
            "oracle_visible_to_provider": False,
        },
        "control": {
            "milestone": "M25.2",
            "correct": 6,
            "total": 18,
            "source": "persisted_frozen_case_evidence",
        },
        "semantic_acquisition": {
            **counts,
            "json_parse_success_verified": sum(
                row.get("json_parse_status") == "VERIFIED" for row in rows
            ),
            "json_parse_unverified_capture_truncated": sum(
                row.get("json_parse_status") == "UNVERIFIED_CAPTURE_TRUNCATED" for row in rows
            ),
            "json_parse_invalid": sum(row.get("json_parse_status") == "INVALID" for row in rows),
            "schema_valid": counts["plan_schema_valid"],
            "canonical_valid": counts["canonical_plan_valid"],
        },
        "plan_accuracy": {
            "exact_oracle_matches": sum(row.get("oracle_plan_match") is True for row in rows),
            "partial_or_incorrect": sum(
                row.get("oracle_plan_match") is False for row in rows if not row["metadata_blocked"]
            ),
            "metadata_blocked": counts["metadata_blocked"],
            "component_accuracy": component_totals,
        },
        "semantic_execution_funnel": {
            "valid_plans": counts["canonical_plan_valid"],
            "compiled": counts["compiled"],
            "semantic_valid": counts["semantic_valid"],
            "m1_accepted": counts["m1_accepted"],
            "executed": counts["executed"],
            "official_correct": counts["official_correct"],
            "evaluator_limitation": counts["evaluator_limitations"],
        },
        "paired": {
            "comparable_cases": len(comparable),
            "both_correct": transitions["BOTH_CORRECT"],
            "direct_only": transitions["DIRECT_ONLY"],
            "semantic_only": transitions["SEMANTIC_ONLY"],
            "both_wrong": transitions["BOTH_WRONG"],
            "net_correct_delta": transitions["SEMANTIC_ONLY"] - transitions["DIRECT_ONLY"],
            "mcnemar_exact_two_sided_p": _mcnemar(b, c),
            "excluded_metadata_blocked": counts["metadata_blocked"],
            "excluded_evaluator_limitation": sum(
                row["reference_evaluator_limitation"] for row in rows
            ),
        },
        "failure_taxonomy": dict(Counter(row.get("primary_failure") for row in rows)),
        "latency": {
            "provider_ms": _summary(
                [
                    float(row["provider_latency_ms"])
                    for row in rows
                    if row.get("provider_latency_ms") is not None
                ]
            ),
            "end_to_end_ms": _summary(
                [
                    float(row["end_to_end_latency_ms"])
                    for row in rows
                    if row.get("end_to_end_latency_ms") is not None
                ]
            ),
        },
        "usage": {
            key: _usage_summary(rows, key)
            for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
        },
        "cases": safe_cases,
        "protected_data_safety": {
            "protected_gt_ignored": True,
            "protected_gt_tracked": False,
            "gold_committed": False,
            "oracle_plan_committed": False,
            "local_detailed_artifact_tracked": False,
        },
    }
    SAFE_RESULT.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return safe


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=DEFAULT_PROTECTED)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_only:
        print(json.dumps(run_preflight(args.public_root, args.protected)))
        return
    result = asyncio.run(run_experiment(args.public_root, args.protected))
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "classification",
                    "experiment",
                    "semantic_acquisition",
                    "semantic_execution_funnel",
                    "paired",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
