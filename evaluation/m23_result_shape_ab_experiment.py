"""M23 bounded paired experiment for the existing ResultShape capability.

The runner is deliberately evaluation-only.  It selects an outcome-blind
holdout, freezes a manifest before provider exposure, and keeps ResultShape
validation as telemetry rather than an execution gate.  M1 remains the only
SQL safety authority in both arms.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlglot import exp, parse_one

from app.generation.provider import ModelIOCapture, OpenAICompatibleProvider, SqlProposal
from app.generation.result_shape import ResultShapeProposal, validate_result_shape
from app.models.domain import QueryRequest
from app.provenance.canonical import text_hash
from app.sql.models import CandidateSource, SqlCandidate, SqlExecutionError, SqlPlanFailure
from app.sql.service import SqlSafetyService
from evaluation.external.bird.benchmark import BirdBenchmarkExecutor
from evaluation.external.defog.benchmark import (
    DefogBenchmarkExecutor,
    format_reference_instructions,
)
from evaluation.m20_full_rebaseline import (
    _defog_gold_preflight,
    _evaluate,
    _execution_result,
    _load_all,
    _schema_contexts,
    _settings,
    _settings_for_db,
)
from evaluation.m22_2_projection_semantics_forensics import projection_facts

ROOT = Path(__file__).resolve().parents[1]
M20_MANIFEST_HASH = "36e53850a704049cdf48453f8f3910eefeb877c9c57c342c0913674c8f13025a"
M22_3 = ROOT / "evaluation/fixtures/m22_3_existing_result_shape_capability_audit.json"
MANIFEST = ROOT / "evaluation/fixtures/m23_result_shape_manifest.json"
RESULT = ROOT / "evaluation/fixtures/m23_result_shape_ab_experiment.json"
KNOWN_DEV = {
    "bird:187",
    "bird:288",
    "bird:356",
    "bird:427",
    "bird:72",
    "defog_classic:classic:105",
    "defog_classic:classic:20",
    "defog_classic:classic:31",
}


class M23PreflightError(RuntimeError):
    """Raised before provider construction when a global experiment gate fails."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _clean_repository_observed() -> bool:
    status = subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--short"], text=True
    ).splitlines()
    ignored_outputs = {
        "evaluation/fixtures/m23_result_shape_manifest.json",
        "evaluation/fixtures/m23_result_shape_ab_experiment.json",
    }
    return not [line for line in status if line[3:] not in ignored_outputs]


def _case_id(question: Any, label: str) -> str:
    if label == "bird":
        return f"bird:{question.index}"
    return f"defog_classic:{question.dataset}:{question.index}"


def _question_text(question: Any, label: str) -> str:
    if label == "bird":
        return question.question + "\n\nBIRD expert evidence:\n" + question.evidence
    return question.question


def _strata(question: Any, label: str) -> set[str]:
    """Outcome-blind projection-sensitive strata from runtime input only."""
    del label
    text = question.question.lower()
    values: set[str] = set()
    if re.search(r"\b(and|along with|as well as|both)\b|,", text):
        values.add("MULTI_OUTPUT")
    if re.search(r"\b(count|number|average|avg|sum|total|minimum|maximum|lowest|highest)\b", text):
        values.add("AGGREGATION_WITH_OUTPUT")
    if re.search(r"\b(each|every|their|with|associated|belong|for all)\b", text):
        values.add("JOIN_LIKELY")
    if re.search(r"\b(list|show|which|what|who|name|names|return|find|identify)\b", text):
        values.add("PHYSICAL_OUTPUT_FOCUSED")
    values.add("SINGLE_OUTPUT" if "MULTI_OUTPUT" not in values else "MULTI_OUTPUT")
    return values


STRATA_ORDER = (
    "MULTI_OUTPUT",
    "AGGREGATION_WITH_OUTPUT",
    "JOIN_LIKELY",
    "PHYSICAL_OUTPUT_FOCUSED",
    "SINGLE_OUTPUT",
)


def select_primary(datasets: dict[str, list[Any]], target_count: int = 30) -> list[dict[str, Any]]:
    """Select 15 BIRD and 15 Classic cases without outcome/gold inspection."""
    if target_count != 30:
        raise ValueError("M23's frozen design uses exactly 30 primary cases")
    selected: list[dict[str, Any]] = []
    for label in ("bird", "defog_classic"):
        rows = [q for q in datasets[label] if _case_id(q, label) not in KNOWN_DEV]
        chosen: list[Any] = []
        used: set[str] = set()
        for stratum in STRATA_ORDER:
            candidates = sorted(
                (q for q in rows if stratum in _strata(q, label)),
                key=lambda q: hashlib.sha256(_case_id(q, label).encode()).hexdigest(),
            )
            for question in candidates:
                if _case_id(question, label) not in used:
                    chosen.append(question)
                    used.add(_case_id(question, label))
                    break
            if len(chosen) >= 15:
                break
        if len(chosen) < 15:
            for question in sorted(
                rows,
                key=lambda q: hashlib.sha256(_case_id(q, label).encode()).hexdigest(),
            ):
                if _case_id(question, label) not in used:
                    chosen.append(question)
                    used.add(_case_id(question, label))
                if len(chosen) == 15:
                    break
        if len(chosen) != 15:
            raise M23PreflightError(f"{label}: could not select 15 primary cases")
        selected.extend(
            {
                "label": label,
                "kind": "bird" if label == "bird" else "defog",
                "case_id": _case_id(question, label),
                "strata": sorted(_strata(question, label)),
                "index": question.index,
                "database": question.db_id if label == "bird" else question.db_name,
            }
            for question in chosen
        )
    selected.sort(key=lambda row: (row["label"], row["case_id"]))
    for row in selected:
        digest = hashlib.sha256(row["case_id"].encode()).hexdigest()
        row["arm_order"] = "CONTROL_FIRST" if int(digest[:8], 16) % 2 == 0 else "RESULTSHAPE_FIRST"
    return selected


def _primary_selection_payload(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "label": row["label"],
            "case_id": row["case_id"],
            "database": row["database"],
            "strata": row["strata"],
            "arm_order": row["arm_order"],
        }
        for row in selected
    ]


def _runtime_question(question: Any, label: str) -> str:
    return _question_text(question, label)


def _prompt_identity() -> dict[str, str]:
    provider = ROOT / "app/generation/provider.py"
    result_shape = ROOT / "app/generation/result_shape.py"
    return {
        "provider_source_sha256": sha256_file(provider),
        "result_shape_contract_sha256": sha256_file(result_shape),
        "sql_generation_prompt": "existing _generation_messages; no M23 wording change",
        "result_shape_prompt": "existing _result_shape_messages; no M23 wording change",
    }


def _manifest(
    datasets: dict[str, list[Any]],
    contexts: dict[tuple[str, str], str],
    selected: list[dict[str, Any]],
    settings: Any,
    sql_eval: Path,
    bird_root: Path,
) -> dict[str, Any]:
    return {
        "milestone": "M23",
        "starting_commit": _head(),
        "repository_clean_observed": _clean_repository_observed(),
        "historical_artifacts": {
            "m20_manifest_hash": M20_MANIFEST_HASH,
            "m22_3_artifact_sha256": sha256_file(M22_3),
            "m22_3_classification": "EXISTING_RESULTSHAPE_SUFFICIENT_FOR_M23",
        },
        "datasets": {
            "bird": {
                "sha256": sha256_file(bird_root / "mini_dev_postgresql.json"),
                "count_loaded": len(datasets["bird"]),
            },
            "defog_classic": {
                "sha256": sha256_file(sql_eval / "data/questions_gen_postgres.csv"),
                "count_loaded": len(datasets["defog_classic"]),
            },
        },
        "populations": {
            "primary_holdout": _primary_selection_payload(selected),
            "mechanism_dev": {"status": "NOT_RUN", "ids": []},
            "selection_rule": (
                "Runtime question/evidence text only; deterministic lexical strata and "
                "SHA-256 tie-break; 15 BIRD + 15 Defog Classic; known discovery IDs excluded."
            ),
        },
        "arm_order": {row["case_id"]: row["arm_order"] for row in selected},
        "provider": {
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "temperature": "provider_default",
            "reasoning_effort": settings.llm_reasoning_effort,
            "timeout_seconds": settings.llm_timeout_seconds,
        },
        "prompt_identity": _prompt_identity(),
        "schema_context": {
            "renderer": "existing M20 schema_context; no M21 V2 promotion",
            "hashes": {
                f"{kind}:{name}": text_hash(value)
                for (kind, name), value in sorted(contexts.items())
            },
            "chars": {
                f"{kind}:{name}": len(value) for (kind, name), value in sorted(contexts.items())
            },
            "approx_tokens": {
                f"{kind}:{name}": (len(value) + 3) // 4
                for (kind, name), value in sorted(contexts.items())
            },
        },
        "m1": {
            "max_plan_rows": settings.max_plan_rows,
            "max_plan_cost": settings.max_plan_cost,
            "max_result_rows": settings.max_result_rows,
            "statement_timeout_ms": settings.statement_timeout_ms,
        },
        "evaluator": {
            "bird": "existing bird_execution_match",
            "defog": "pinned sql-eval canonical_compare",
        },
        "configuration": {
            "memory": False,
            "queryplan": False,
            "window_ir": False,
            "governed": False,
            "retrieval": False,
            "repair_calls": 0,
            "judge_calls": 0,
            "best_of_n": False,
            "semantic_retry": False,
        },
        "call_budget": {
            "primary_cases": len(selected),
            "control_sql": len(selected),
            "result_shape_proposals": len(selected),
            "result_shape_sql": len(selected),
            "planned_total": len(selected) * 3,
        },
        "control_protocol_note": (
            "Both arms use existing provider.propose_sql for SQL generation so the "
            "ResultShape guidance is the only arm variable. BIRD evidence is included "
            "identically in the user question; this is not a byte-for-byte M20 BIRD "
            "raw-transport rerun."
        ),
    }


def _question_maps(datasets: dict[str, list[Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, rows in datasets.items():
        for question in rows:
            result[_case_id(question, label)] = question
    return result


def _gold_preflight(selected: list[dict[str, Any]], questions: dict[str, Any]) -> dict[str, Any]:
    defog_rows = [questions[row["case_id"]] for row in selected if row["kind"] == "defog"]
    bird_rows = [questions[row["case_id"]] for row in selected if row["kind"] == "bird"]
    defog_executor = DefogBenchmarkExecutor()
    bird_executor = BirdBenchmarkExecutor()
    defog_gold, defog_status = _defog_gold_preflight(defog_rows, defog_executor)
    bird_gold: dict[str, Any] = {}
    bird_failures: list[dict[str, Any]] = []
    for question in bird_rows:
        try:
            bird_executor._validate_select(question.gold_sql)
            bird_gold[str(question.index)] = bird_executor.execute(question.gold_sql)
        except Exception as error:
            bird_failures.append(
                {"case_id": _case_id(question, "bird"), "error_type": type(error).__name__}
            )
    status = {
        "defog": {**defog_status, "validated": len(defog_gold)},
        "bird": {"cases": len(bird_rows), "validated": len(bird_gold), "failures": bird_failures},
    }
    if any(value["validated"] != value["cases"] or value["failures"] for value in status.values()):
        raise M23PreflightError("selected gold/evaluator preflight failed: " + json.dumps(status))
    return {"defog": defog_gold, "bird": bird_gold}


def _capture_metrics(capture: ModelIOCapture | None) -> dict[str, Any]:
    if capture is None:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "cached_tokens": None,
            "latency_ms": None,
        }
    return {
        "input_tokens": capture.usage.get("prompt_tokens"),
        "output_tokens": capture.usage.get("completion_tokens"),
        "reasoning_tokens": capture.usage.get("reasoning_tokens"),
        "cached_tokens": capture.usage.get("cached_tokens"),
        "latency_ms": capture.latency_ms,
        "failure_stage": capture.failure_stage,
        "response_model": capture.response_model,
        "technical_retry_count": capture.technical_retry_count,
    }


def _projection_facts_safe(sql: str | None) -> dict[str, Any]:
    if not sql:
        return {"arity": None, "expressions": []}
    try:
        return projection_facts(sql)
    except Exception:
        return {"arity": None, "expressions": []}


def _projection_error(sql: str | None, reference_sql: str) -> str | None:
    actual = _projection_facts_safe(sql)
    expected = _projection_facts_safe(reference_sql)
    if actual["arity"] is None or expected["arity"] is None:
        return "UNASSESSABLE"
    if actual["arity"] > expected["arity"]:
        return "EXTRA"
    if actual["arity"] < expected["arity"]:
        return "MISSING"
    actual_sources = [
        tuple((column.get("table"), column.get("column")) for column in item.get("columns", []))
        for item in actual["expressions"]
    ]
    expected_sources = [
        tuple((column.get("table"), column.get("column")) for column in item.get("columns", []))
        for item in expected["expressions"]
    ]
    return None if actual_sources == expected_sources else "WRONG_OR_EXPRESSION"


def _proposal_quality(proposal: ResultShapeProposal | None, gold_sql: str) -> str:
    if proposal is None:
        return "PROPOSAL_UNASSESSABLE"
    facts = _projection_facts_safe(gold_sql)
    if facts["arity"] is None or len(proposal.outputs) != facts["arity"]:
        return "PROPOSAL_WRONG"
    physical_expected = sum(
        1
        for fact in facts["expressions"]
        if fact.get("expression_type") == "COLUMN" and len(fact.get("columns", [])) == 1
    )
    physical_provided = sum(
        int(output.kind.value == "PHYSICAL_COLUMN" and output.source_hint is not None)
        for output in proposal.outputs
    )
    if physical_provided != physical_expected:
        return "PROPOSAL_PARTIALLY_CORRECT"
    for output, fact in zip(proposal.outputs, facts["expressions"], strict=True):
        columns = fact.get("columns", [])
        if output.kind.value == "PHYSICAL_COLUMN" and columns and output.source_hint:
            expected = (
                f"{columns[0].get('table')}.{columns[0].get('column')}"
                if columns[0].get("table")
                else columns[0].get("column")
            )
            if output.source_hint.lower() != str(expected).lower():
                return "PROPOSAL_WRONG"
    return "PROPOSAL_CORRECT"


def _sql_component_presence(sql: str | None) -> dict[str, Any]:
    if not sql:
        return {}
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return {"parse": False}
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    return {
        "parse": True,
        "tables": sorted({table.name.lower() for table in tree.find_all(exp.Table)}),
        "joins": len(list(tree.find_all(exp.Join))),
        "where": tree.find(exp.Where) is not None,
        "group_by": tree.find(exp.Group) is not None,
        "having": tree.find(exp.Having) is not None,
        "order_by": tree.find(exp.Order) is not None,
        "limit": tree.find(exp.Limit) is not None,
        "aggregation": any(isinstance(node, exp.AggFunc) for node in tree.walk()),
        "subquery": any(isinstance(node, exp.Subquery) for node in tree.walk()),
        "window": any(isinstance(node, exp.Window) for node in tree.walk()),
        "projection_arity": len(select.expressions) if select else None,
    }


def _semantic_drift(control_sql: str | None, result_sql: str | None) -> dict[str, bool | None]:
    control = _sql_component_presence(control_sql)
    result = _sql_component_presence(result_sql)
    if not control or not result or not control.get("parse") or not result.get("parse"):
        return {
            key: None
            for key in ("table", "join", "filter", "aggregation", "order_limit", "subquery_window")
        }
    return {
        "table": control.get("tables") != result.get("tables"),
        "join": control.get("joins") != result.get("joins"),
        "filter": control.get("where") != result.get("where"),
        "aggregation": control.get("aggregation") != result.get("aggregation")
        or control.get("group_by") != result.get("group_by")
        or control.get("having") != result.get("having"),
        "order_limit": control.get("order_by") != result.get("order_by")
        or control.get("limit") != result.get("limit"),
        "subquery_window": control.get("subquery") != result.get("subquery")
        or control.get("window") != result.get("window"),
    }


def _new_arm_record() -> dict[str, Any]:
    return {
        "provider_calls": 0,
        "sql": None,
        "sql_hash": None,
        "provider": None,
        "tokens": {"input": None, "output": None, "reasoning": None, "cached": None},
        "latency_ms": {"provider": None, "m1": None, "execution": None, "total": None},
        "m1_status": None,
        "execution_status": None,
        "correct": False,
        "failure_stage": None,
        "failure_code": None,
        "projection": {},
    }


async def _run_sql_arm(
    provider: OpenAICompatibleProvider,
    safety: SqlSafetyService,
    question: Any,
    label: str,
    context: str,
    gold: Any,
    arm: str,
    result_shape: ResultShapeProposal | None = None,
) -> tuple[dict[str, Any], ResultShapeProposal | None, dict[str, Any] | None]:
    record = _new_arm_record()
    started = time.perf_counter()
    runtime_question = _runtime_question(question, label)
    try:
        proposal: SqlProposal = await provider.propose_sql(
            QueryRequest(question=runtime_question),
            None,
            context
            + (format_reference_instructions(question.instructions) if label != "bird" else ""),
            result_shape=result_shape,
        )
        capture = provider.consume_model_io()
        record["provider_calls"] = 1
        capture_values = _capture_metrics(capture)
        record["tokens"] = {
            "input": capture_values["input_tokens"],
            "output": capture_values["output_tokens"],
            "reasoning": capture_values["reasoning_tokens"],
            "cached": capture_values["cached_tokens"],
        }
        record["latency_ms"]["provider"] = capture_values["latency_ms"]
        record["provider"] = proposal.model
        sql = proposal.sql.strip()
    except Exception as error:
        capture = provider.consume_model_io()
        record["provider_calls"] = 1
        capture_values = _capture_metrics(capture)
        record["tokens"] = {
            "input": capture_values["input_tokens"],
            "output": capture_values["output_tokens"],
            "reasoning": capture_values["reasoning_tokens"],
            "cached": capture_values["cached_tokens"],
        }
        record["latency_ms"]["provider"] = capture_values["latency_ms"]
        record.update({"failure_stage": "PROVIDER_PROTOCOL", "failure_code": type(error).__name__})
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record, result_shape, capture_values
    record.update(
        {"sql": sql, "sql_hash": text_hash(sql), "projection": _projection_facts_safe(sql)}
    )
    m1_started = time.perf_counter()
    try:
        planned = safety.plan(
            SqlCandidate(
                sql=sql, source=CandidateSource.LLM, correlation_id=f"m23:{label}:{question.index}"
            )
        )
    except Exception as error:
        record.update(
            {"m1_status": "ERROR", "failure_stage": "M1", "failure_code": type(error).__name__}
        )
        record["latency_ms"]["m1"] = (time.perf_counter() - m1_started) * 1000
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record, result_shape, capture_values
    record["latency_ms"]["m1"] = (time.perf_counter() - m1_started) * 1000
    if isinstance(planned, SqlPlanFailure):
        record.update(
            {"m1_status": "REJECTED", "failure_stage": "M1", "failure_code": planned.status.value}
        )
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record, result_shape, capture_values
    record["m1_status"] = "ALLOWED"
    validation: dict[str, Any] | None = None
    if result_shape is not None:
        try:
            validation = validate_result_shape(result_shape, planned.normalized_sql).model_dump(
                mode="json"
            )
        except Exception as error:
            validation = {
                "accepted": False,
                "projection_status": "PROJECTION_UNCERTAIN",
                "message": type(error).__name__,
            }
    record["result_shape_validation"] = validation
    execution_started = time.perf_counter()
    try:
        execution = safety.execute(planned)
    except Exception as error:
        record.update(
            {
                "execution_status": "FAILURE",
                "failure_stage": "EXECUTION",
                "failure_code": type(error).__name__,
            }
        )
        record["latency_ms"]["execution"] = (time.perf_counter() - execution_started) * 1000
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record, result_shape, capture_values
    record["latency_ms"]["execution"] = (time.perf_counter() - execution_started) * 1000
    if isinstance(execution, SqlExecutionError):
        record.update(
            {
                "execution_status": "FAILURE",
                "failure_stage": "EXECUTION",
                "failure_code": "EXECUTION_FAILURE",
            }
        )
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record, result_shape, capture_values
    record["execution_status"] = "SUCCESS"
    try:
        record["correct"] = _evaluate(
            label if label == "bird" else "defog_classic",
            question,
            _execution_result(execution),
            gold,
            sql,
        )
    except Exception as error:
        record.update({"failure_stage": "RESULT_EVALUATION", "failure_code": type(error).__name__})
    if not record["correct"] and record["failure_stage"] is None:
        record.update({"failure_stage": "RESULT_EVALUATION", "failure_code": "RESULT_MISMATCH"})
    record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
    return record, result_shape, capture_values


def _gold_for(label: str, question: Any, gold: dict[str, Any]) -> Any:
    return (
        gold["bird"][str(question.index)]
        if label == "bird"
        else gold["defog"][f"{question.dataset}:{question.index}"]
    )


def _proposal_for_quality(label: str, question: Any, proposal: ResultShapeProposal | None) -> str:
    return _proposal_quality(proposal, question.gold_sql)


async def run_experiment(
    datasets: dict[str, list[Any]],
    contexts: dict[tuple[str, str], str],
    selected: list[dict[str, Any]],
    safety_cache: dict[tuple[str, str], tuple[Engine, SqlSafetyService]],
    manifest: dict[str, Any],
    pilot_count: int = 10,
    existing_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    del pilot_count
    provider = OpenAICompatibleProvider(_settings())
    questions = _question_maps(datasets)
    gold = _gold_preflight(selected, questions)
    records: list[dict[str, Any]] = list(existing_records or [])
    existing_ids = {record["case_id"] for record in records}
    ordered = sorted(selected, key=lambda row: (row["arm_order"], row["case_id"]))
    ordered = [row for row in ordered if row["case_id"] not in existing_ids]
    for position, item in enumerate(ordered, 1):
        question = questions[item["case_id"]]
        key = (item["kind"], item["database"])
        context = contexts[key]
        case_started = time.perf_counter()
        control = _new_arm_record()
        resultshape = _new_arm_record()
        proposal: ResultShapeProposal | None = None
        proposal_capture: dict[str, Any] | None = None
        proposal_quality = "PROPOSAL_UNASSESSABLE"
        if item["arm_order"] == "CONTROL_FIRST":
            control, _, _ = await _run_sql_arm(
                provider,
                safety_cache[key][1],
                question,
                item["label"],
                context,
                _gold_for(item["label"], question, gold),
                "CONTROL",
            )
        try:
            proposal_started = time.perf_counter()
            proposal = await provider.propose_result_shape(
                _runtime_question(question, item["label"]), context
            )
            capture = provider.consume_model_io()
            proposal_capture = _capture_metrics(capture)
            proposal_capture["wall_latency_ms"] = (time.perf_counter() - proposal_started) * 1000
        except Exception as error:
            capture = provider.consume_model_io()
            proposal_capture = _capture_metrics(capture)
            proposal_capture["wall_latency_ms"] = None
            proposal_capture["failure_code"] = type(error).__name__
        proposal_quality = _proposal_for_quality(item["label"], question, proposal)
        resultshape, _, _ = await _run_sql_arm(
            provider,
            safety_cache[key][1],
            question,
            item["label"],
            context,
            _gold_for(item["label"], question, gold),
            "RESULTSHAPE",
            proposal,
        )
        if item["arm_order"] == "RESULTSHAPE_FIRST":
            control, _, _ = await _run_sql_arm(
                provider,
                safety_cache[key][1],
                question,
                item["label"],
                context,
                _gold_for(item["label"], question, gold),
                "CONTROL",
            )
        control_correct = bool(control["correct"])
        result_correct = bool(resultshape["correct"])
        state = (
            "A"
            if control_correct and result_correct
            else "B"
            if control_correct
            else "C"
            if result_correct
            else "D"
        )
        resultshape["proposal"] = proposal.model_dump(mode="json") if proposal else None
        resultshape["proposal_capture"] = proposal_capture
        resultshape["proposal_quality"] = proposal_quality
        resultshape["proposal_compliance"] = resultshape.get("result_shape_validation")
        record = {
            "case_id": item["case_id"],
            "benchmark": item["label"],
            "database": item["database"],
            "question": question.question,
            "reference_projection": _projection_facts_safe(question.gold_sql),
            "strata": item["strata"],
            "arm_order": item["arm_order"],
            "control": control,
            "resultshape": resultshape,
            "paired_state": state,
            "recovery": state == "C",
            "regression": state == "B",
            "semantic_drift": _semantic_drift(control.get("sql"), resultshape.get("sql")),
            "question_hash": text_hash(question.question),
            "schema_context_hash": text_hash(context),
            "gold_used_only_offline": True,
            "case_total_latency_ms": (time.perf_counter() - case_started) * 1000,
        }
        records.append(record)
        print(f"M23 primary: {position}/{len(ordered)} {item['case_id']}", flush=True)
    for engine, _ in safety_cache.values():
        engine.dispose()
    return _aggregate(manifest, records)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def _arm_summary(records: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    arms = [record[arm] for record in records]
    provider_latencies = [
        float(a["latency_ms"]["provider"]) for a in arms if a["latency_ms"]["provider"] is not None
    ]
    totals = [float(a["latency_ms"]["total"]) for a in arms if a["latency_ms"]["total"] is not None]
    return {
        "cases": len(arms),
        "provider_calls": sum(a["provider_calls"] for a in arms),
        "correct": sum(bool(a["correct"]) for a in arms),
        "m1_accepted": sum(a["m1_status"] == "ALLOWED" for a in arms),
        "m1_rejected": sum(a["m1_status"] == "REJECTED" for a in arms),
        "m1_errors": sum(a["m1_status"] == "ERROR" for a in arms),
        "execution_success": sum(a["execution_status"] == "SUCCESS" for a in arms),
        "execution_failure": sum(a["execution_status"] == "FAILURE" for a in arms),
        "failure_stages": dict(Counter(a["failure_stage"] for a in arms if a["failure_stage"])),
        "tokens": {
            key: sum(a["tokens"][key] for a in arms if a["tokens"].get(key) is not None) or None
            for key in ("input", "output", "reasoning", "cached")
        },
        "latency_ms": {
            "provider_median": _percentile(provider_latencies, 0.5),
            "provider_p95": _percentile(provider_latencies, 0.95),
            "total_median": _percentile(totals, 0.5),
            "total_p95": _percentile(totals, 0.95),
        },
    }


def _aggregate(manifest: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    matrix = Counter(record["paired_state"] for record in records)
    projection_metrics: dict[str, Any] = {}
    for arm in ("control", "resultshape"):
        projection_metrics[arm] = {"extra": None, "missing": None, "wrong_or_expression": None}
    for record in records:
        validation = record["resultshape"].get("result_shape_validation")
        if validation:
            record["resultshape"]["projection_telemetry"] = validation
        for arm in ("control", "resultshape"):
            expected = record["reference_projection"]
            actual = _projection_facts_safe(record[arm].get("sql"))
            if actual["arity"] is None or expected["arity"] is None:
                error = "UNASSESSABLE"
            elif actual["arity"] > expected["arity"]:
                error = "EXTRA"
            elif actual["arity"] < expected["arity"]:
                error = "MISSING"
            else:
                actual_sources = [
                    tuple(
                        (column.get("table"), column.get("column"))
                        for column in item.get("columns", [])
                    )
                    for item in actual["expressions"]
                ]
                expected_sources = [
                    tuple(
                        (column.get("table"), column.get("column"))
                        for column in item.get("columns", [])
                    )
                    for item in expected["expressions"]
                ]
                error = None if actual_sources == expected_sources else "WRONG_OR_EXPRESSION"
            record[arm]["projection_error"] = error
            if error == "EXTRA":
                projection_metrics[arm]["extra"] = (projection_metrics[arm]["extra"] or 0) + 1
            elif error == "MISSING":
                projection_metrics[arm]["missing"] = (projection_metrics[arm]["missing"] or 0) + 1
            elif error == "WRONG_OR_EXPRESSION":
                projection_metrics[arm]["wrong_or_expression"] = (
                    projection_metrics[arm]["wrong_or_expression"] or 0
                ) + 1
    drift = Counter()
    for record in records:
        for key, value in record["semantic_drift"].items():
            if value is True:
                drift[key] += 1
    proposal_quality = Counter(record["resultshape"].get("proposal_quality") for record in records)
    failure_stage = Counter()
    for record in records:
        if not record["resultshape"]["correct"]:
            stage = record["resultshape"].get("failure_stage")
            if stage in {"PROVIDER_PROTOCOL", None}:
                stage = (
                    "S1_RESULTSHAPE_PROPOSAL_WRONG"
                    if record["resultshape"].get("proposal_quality") == "PROPOSAL_WRONG"
                    else "S6_OTHER"
                )
            elif stage == "M1":
                stage = "S4_M1_REJECT"
            elif stage == "EXECUTION":
                stage = "S5_EXECUTION_FAILURE"
            elif stage == "RESULT_EVALUATION":
                validation = record["resultshape"].get("result_shape_validation") or {}
                stage = (
                    "S3_RESULTSHAPE_PROPOSAL_CORRECT_SQL_COMPLIANT_BUT_SEMANTICALLY_WRONG"
                    if validation.get("accepted")
                    else "S2_RESULTSHAPE_PROPOSAL_CORRECT_SQL_NONCOMPLIANT"
                )
            failure_stage[stage] += 1
    return {
        "classification": "M23_RESULTSHAPE_HYPOTHESIS_NOT_SUPPORTED",
        "starting_commit": manifest["starting_commit"],
        "manifest_hash": manifest["manifest_hash"],
        "provider_calls": {
            "planned": manifest["call_budget"]["planned_total"],
            "actual": sum(
                r["control"]["provider_calls"] + r["resultshape"]["provider_calls"] + 1
                for r in records
            ),
            "control_sql": sum(r["control"]["provider_calls"] for r in records),
            "result_shape_proposals": len(records),
            "result_shape_sql": sum(r["resultshape"]["provider_calls"] for r in records),
            "retries": sum(
                r["control"]["tokens"].get("retry", 0) + r["resultshape"]["tokens"].get("retry", 0)
                for r in records
            ),
        },
        "populations": {
            "primary_holdout": [r["case_id"] for r in records],
            "mechanism_dev": {"status": "NOT_RUN", "ids": []},
        },
        "primary_results": {
            "control_correct": sum(r["control"]["correct"] for r in records),
            "resultshape_correct": sum(r["resultshape"]["correct"] for r in records),
            "total": len(records),
        },
        "paired_matrix": {key: matrix.get(key, 0) for key in ("A", "B", "C", "D")},
        "projection_metrics": projection_metrics,
        "proposal_quality": dict(proposal_quality),
        "failure_stage_analysis": dict(failure_stage),
        "m1": {
            "control": _arm_summary(records, "control"),
            "resultshape": _arm_summary(records, "resultshape"),
        },
        "execution": {
            "control": _arm_summary(records, "control"),
            "resultshape": _arm_summary(records, "resultshape"),
        },
        "semantic_drift": dict(drift),
        "cost": {
            "control": _arm_summary(records, "control"),
            "resultshape_sql": _arm_summary(records, "resultshape"),
            "resultshape_proposal": {
                "calls": len(records),
                "input_tokens": sum(
                    (r["resultshape"].get("proposal_capture") or {}).get("input_tokens") or 0
                    for r in records
                )
                or None,
                "output_tokens": sum(
                    (r["resultshape"].get("proposal_capture") or {}).get("output_tokens") or 0
                    for r in records
                )
                or None,
                "median_latency_ms": _percentile(
                    [
                        float(
                            (r["resultshape"].get("proposal_capture") or {}).get("wall_latency_ms")
                        )
                        for r in records
                        if (r["resultshape"].get("proposal_capture") or {}).get("wall_latency_ms")
                        is not None
                    ],
                    0.5,
                ),
                "p95_latency_ms": _percentile(
                    [
                        float(
                            (r["resultshape"].get("proposal_capture") or {}).get("wall_latency_ms")
                        )
                        for r in records
                        if (r["resultshape"].get("proposal_capture") or {}).get("wall_latency_ms")
                        is not None
                    ],
                    0.95,
                ),
            },
        },
        "mechanism_dev_results": {"status": "NOT_RUN"},
        "cases": records,
    }


def build_preflight() -> tuple[
    dict[str, Any],
    dict[str, list[Any]],
    dict[tuple[str, str], str],
    dict[str, Any],
    dict[tuple[str, str], tuple[Engine, SqlSafetyService]],
]:
    from os import environ

    sql_eval = Path(environ.get("DEFOG_SQL_EVAL_ROOT", "/external/sql-eval"))
    bird_root = Path(environ.get("BIRD_MINI_DEV_ROOT", "/bird"))
    if not sql_eval.is_dir() or not bird_root.is_dir():
        raise M23PreflightError("dataset roots are unavailable")
    datasets = _load_all(sql_eval, bird_root)
    datasets = {
        "bird": datasets["bird"],
        "defog_classic": datasets["defog_classic"],
        "defog_advanced": [],
    }
    contexts, _ = _schema_contexts(datasets)
    selected = select_primary(datasets)
    questions = _question_maps(datasets)
    _gold_preflight(selected, questions)
    settings = _settings()
    if not settings.llm_api_key:
        raise M23PreflightError("provider credential is not configured")
    safety_cache: dict[tuple[str, str], tuple[Engine, SqlSafetyService]] = {}
    for kind, name in sorted({(row["kind"], row["database"]) for row in selected}):
        db_settings, engine, safety = _settings_for_db(settings, kind, name)
        del db_settings
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        safety_cache[(kind, name)] = (engine, safety)
    manifest = _manifest(datasets, contexts, selected, settings, sql_eval, bird_root)
    manifest["manifest_hash"] = sha256_json(manifest)
    if MANIFEST.exists():
        existing = json.loads(MANIFEST.read_text())
        if existing.get("manifest_hash") != manifest["manifest_hash"]:
            raise M23PreflightError("existing M23 manifest differs from deterministic preflight")
    else:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest, datasets, contexts, {"selected": selected}, safety_cache


async def main_async() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if sum((args.preflight, args.pilot, args.resume)) != 1:
        parser.error("choose exactly one of --preflight, --pilot, or --resume")
    try:
        manifest, datasets, contexts, selected_meta, safety_cache = build_preflight()
        if args.preflight:
            print(
                json.dumps(
                    {
                        "classification": "M23_PREFLIGHT_READY",
                        "manifest_hash": manifest["manifest_hash"],
                        "primary": len(selected_meta["selected"]),
                    }
                )
            )
            for engine, _ in safety_cache.values():
                engine.dispose()
            return 0
        existing_records: list[dict[str, Any]] = []
        if args.resume:
            if not RESULT.exists():
                raise M23PreflightError("pilot result is missing for resume")
            pilot = json.loads(RESULT.read_text())
            if pilot.get("manifest_hash") != manifest["manifest_hash"]:
                raise M23PreflightError("pilot result manifest hash does not match preflight")
            existing_records = pilot.get("cases", [])
            if len(existing_records) != 10:
                raise M23PreflightError("resume requires exactly 10 completed pilot cases")
        run_selected = selected_meta["selected"][:10] if args.pilot else selected_meta["selected"]
        result = await run_experiment(
            datasets,
            contexts,
            run_selected,
            safety_cache,
            manifest,
            existing_records=existing_records,
        )
        if args.pilot:
            result["classification"] = "M23_PILOT_NON_AUTHORITATIVE"
            result["pilot"] = {"cases": 10, "continuation_required": True}
        elif args.resume:
            result["pilot"] = {"cases": 10, "continuation_required": False}
        RESULT.write_text(json.dumps(result, indent=2, default=str) + "\n")
        print(
            json.dumps(
                {
                    "classification": result["classification"],
                    "manifest_hash": result["manifest_hash"],
                    "provider_calls": result["provider_calls"],
                },
                indent=2,
            )
        )
        return 0
    except M23PreflightError as error:
        failure = {
            "classification": "M23_PREFLIGHT_FAILED",
            "error": str(error),
            "provider_calls": 0,
        }
        RESULT.write_text(json.dumps(failure, indent=2) + "\n")
        print(json.dumps(failure))
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
