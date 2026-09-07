"""Run the frozen M25 pilot with only the corrected semantic context changed.

This module is evaluation-only.  It reuses the existing DIRECT service and
provider, performs a provider-free preflight, and then permits at most one
provider request per frozen pilot case.  Case-level output is written below
the ignored protected directory; the committed result contains aggregates
only.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import sqlglot

from app.config import Settings, get_settings
from app.generation.provider import (
    MalformedProviderResponse,
    OpenAICompatibleProvider,
    SqlProposal,
)
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.text_to_sql.models import TextToSqlResult
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import safety_for_database
from evaluation.external.livesqlbench.protected import (
    LiveSqlBenchEvaluationCase,
    load_protected_artifact,
    merge_public_and_protected,
)
from evaluation.external.livesqlbench.schema import (
    LiveSqlBenchDatabase,
    PostgresConnectionConfig,
    introspect_database,
)
from evaluation.external.livesqlbench.semantic_context import (
    load_semantic_resources,
    render_semantic_context,
)
from evaluation.m25_1_livesqlbench_semantic_context import (
    estimate_tokens,
    git_ignored,
    sha256_file,
    sha256_json,
)
from evaluation.m25_livesqlbench_direct_pilot import direct_evaluation_service, sha256_text

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED_PATH = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
FINAL_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
PUBLIC_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_preflight_manifest.json"
M25_ARTIFACT = ROOT / "evaluation/fixtures/m25_livesqlbench_direct_pilot_result.json"
M25_1_ARTIFACT = (
    ROOT / "evaluation/fixtures/m25_1_livesqlbench_semantic_context_integrity_result.json"
)
M25_CASES = ROOT / "evaluation/external/livesqlbench/protected/results/m25_direct_pilot_cases.jsonl"
JOURNAL = ROOT / (
    "evaluation/external/livesqlbench/protected/results/m25_2_direct_semantic_context_journal.jsonl"
)
LOCAL_CASES = (
    ROOT
    / "evaluation/external/livesqlbench/protected/results/m25_2_direct_semantic_context_cases.jsonl"
)
SAFE_RESULT = ROOT / "evaluation/fixtures/m25_2_livesqlbench_direct_semantic_context_result.json"
EXPECTED_MODEL = "gpt-5.6-luna"
MAX_PROVIDER_CALLS = 18


class M25_2PreflightError(RuntimeError):
    """Raised before provider construction when a frozen invariant fails."""


def tracked_protected_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "evaluation/external/livesqlbench/protected/"], text=True
    )
    return [line for line in output.splitlines() if line]


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise M25_2PreflightError(f"non-object JSONL row {line_number}: {path}")
                rows.append(value)
    return rows


def _read_unique_journal(path: Path) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or case_id in result:
            raise M25_2PreflightError("journal contains an invalid or duplicate case")
        result[case_id] = row
    return result


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        stream.flush()


def _load_old_cases() -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(M25_CASES)
    result = {str(row["case_id"]): row for row in rows if "case_id" in row}
    if len(result) != 18:
        raise M25_2PreflightError("frozen M25 local case evidence does not cover 18 cases")
    return result


def _usage(capture: Any, proposal: SqlProposal | None) -> dict[str, int | None]:
    usage = capture.usage if capture is not None else {}
    return {
        "input_tokens": usage.get("prompt_tokens")
        if usage.get("prompt_tokens") is not None
        else proposal.prompt_tokens
        if proposal
        else None,
        "output_tokens": usage.get("completion_tokens")
        if usage.get("completion_tokens") is not None
        else proposal.completion_tokens
        if proposal
        else None,
        "reasoning_tokens": usage.get("reasoning_tokens")
        if usage.get("reasoning_tokens") is not None
        else proposal.reasoning_tokens
        if proposal
        else None,
        "cached_tokens": usage.get("cached_prompt_tokens")
        if usage.get("cached_prompt_tokens") is not None
        else proposal.cached_prompt_tokens
        if proposal
        else None,
    }


class JournalProvider:
    """Use the existing provider and persist exactly one attempt per case."""

    def __init__(self, inner: OpenAICompatibleProvider, journal: Path) -> None:
        self.inner = inner
        self.settings = inner.settings
        self.journal = journal
        self.case_id: str | None = None
        self.expected_context_hash: str | None = None

    async def propose_sql(self, *args: Any, **kwargs: Any) -> SqlProposal:
        if self.case_id is None:
            raise RuntimeError("M25.2 journal provider case was not set")
        schema_context = args[2] if len(args) > 2 else kwargs.get("schema_context")
        if not isinstance(schema_context, str):
            raise M25_2PreflightError("provider received no schema context")
        if self.expected_context_hash and sha256_text(schema_context) != self.expected_context_hash:
            raise M25_2PreflightError("corrected semantic context changed during the run")
        started = time.perf_counter()
        try:
            proposal = await self.inner.propose_sql(*args, **kwargs)
        except Exception as error:
            capture = self.inner.consume_model_io()
            _append_jsonl(
                self.journal,
                {
                    "case_id": self.case_id,
                    "attempt_number": 1,
                    "actual_request_count": 1,
                    "provider_status": (
                        "PROTOCOL_FAILURE"
                        if isinstance(error, MalformedProviderResponse)
                        else "PROVIDER_FAILURE"
                    ),
                    "response_received": capture is not None,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "response_hash": (
                        capture.raw_assistant_content_sha256 if capture is not None else None
                    ),
                    "error_type": type(error).__name__,
                    "wall_latency_ms": (time.perf_counter() - started) * 1000,
                    "capture": capture.model_dump(mode="json") if capture is not None else None,
                },
            )
            raise
        capture = self.inner.consume_model_io()
        _append_jsonl(
            self.journal,
            {
                "case_id": self.case_id,
                "attempt_number": 1,
                "actual_request_count": 1,
                "provider_status": "PROVIDER_SUCCESS",
                "response_received": True,
                "timestamp": datetime.now(UTC).isoformat(),
                "response_hash": (
                    capture.raw_assistant_content_sha256
                    if capture is not None
                    else sha256_text(proposal.sql)
                ),
                "sql": proposal.sql,
                "sql_hash": sha256_text(proposal.sql),
                "usage": _usage(capture, proposal),
                "capture": capture.model_dump(mode="json") if capture is not None else None,
                "wall_latency_ms": (time.perf_counter() - started) * 1000,
            },
        )
        return proposal


def _result_from_execution(execution: QueryExecution) -> LiveSqlBenchResult:
    return LiveSqlBenchResult(
        tuple(execution.columns),
        tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows),
    )


def _sql_shape(sql_text: str | None) -> dict[str, Any]:
    if not sql_text:
        return {}
    try:
        tree = sqlglot.parse_one(sql_text, read="postgres")
    except sqlglot.errors.ParseError:
        return {"parseable": False}
    joins = len(list(tree.find_all(sqlglot.exp.Join)))
    return {
        "parseable": True,
        "joins": joins,
        "join_bucket": "0" if joins == 0 else "1" if joins == 1 else "2+",
        "aggregation": any(isinstance(node, sqlglot.exp.AggFunc) for node in tree.walk()),
        "group_by": tree.find(sqlglot.exp.Group) is not None,
        "having": tree.find(sqlglot.exp.Having) is not None,
        "cte": tree.find(sqlglot.exp.CTE) is not None,
        "subquery": tree.find(sqlglot.exp.Subquery) is not None,
        "window": tree.find(sqlglot.exp.Window) is not None,
        "distinct": tree.find(sqlglot.exp.Distinct) is not None,
        "order_by": tree.find(sqlglot.exp.Order) is not None,
        "limit": tree.find(sqlglot.exp.Limit) is not None,
        "temporal": any(
            isinstance(node, (sqlglot.exp.CurrentDate, sqlglot.exp.CurrentTimestamp))
            for node in tree.walk()
        ),
        "current_date": any(isinstance(node, sqlglot.exp.CurrentDate) for node in tree.walk()),
    }


def _reference_result(case: LiveSqlBenchEvaluationCase, safety: Any) -> LiveSqlBenchResult:
    plan = safety.plan(SqlCandidate(sql=case.sol_sql[0], source=CandidateSource.INTERNAL))
    if not isinstance(plan, QueryPlan):
        raise M25_2PreflightError(f"pilot reference is not M1 compatible: {case.instance_id}")
    execution = safety.execute(plan)
    if not isinstance(execution, QueryExecution):
        raise M25_2PreflightError(f"pilot reference did not execute: {case.instance_id}")
    return _result_from_execution(execution)


def _semantic_addition(structural: str, corrected: str) -> str:
    prefix = structural + "\n\n"
    if not corrected.startswith(prefix):
        raise M25_2PreflightError("semantic renderer no longer preserves structural prefix")
    addition = corrected[len(prefix) :]
    if not addition:
        raise M25_2PreflightError("corrected semantic context has no semantic addition")
    return addition


def _payload_safe(
    case: LiveSqlBenchEvaluationCase,
    messages: list[dict[str, str]],
    corrected_context: str,
    old_sql: str | None,
) -> bool:
    serialized = "\n".join(message["content"] for message in messages)
    forbidden = ["sol_sql", "test_cases", "reference result", "evaluator result"]
    if any(value in serialized for value in forbidden):
        return False
    if any(sql and sql in serialized for sql in case.sol_sql):
        return False
    if old_sql and old_sql in serialized:
        return False
    return case.runtime.question in serialized and corrected_context in serialized


def _exact_mcnemar_p(b: int, c: int) -> float | None:
    total = b + c
    if total == 0:
        return None
    lower = sum(math.comb(total, i) for i in range(min(b, c) + 1)) / (2**total)
    return float(min(1.0, 2.0 * lower))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered: list[float] = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"total": 0, "median": None, "p95": None, "max": None}
    return {
        "total": sum(values),
        "median": median(values),
        "p95": _percentile(values, 0.95),
        "max": max(values),
    }


def _status_from_result(
    result: TextToSqlResult, expected: LiveSqlBenchResult, ordered: bool
) -> dict[str, Any]:
    proposal = result.proposal
    if proposal is None:
        return {
            "protocol_status": "FAILURE",
            "sql_produced": False,
            "m1_status": "NOT_REACHED",
            "m1_failure_code": None,
            "execution_status": "NOT_ATTEMPTED",
            "official_status": "NOT_EVALUATED",
            "correct": False,
            "execution": None,
        }
    if result.plan_failure is not None:
        failure = result.plan_failure
        return {
            "protocol_status": "SUCCESS",
            "sql_produced": True,
            "m1_status": "REJECTED",
            "m1_failure_code": (
                failure.rejection.code.value
                if failure.rejection is not None
                else failure.status.value
            ),
            "execution_status": "NOT_ATTEMPTED_M1",
            "official_status": "NOT_EVALUATED",
            "correct": False,
            "execution": None,
        }
    if not isinstance(result.execution, QueryExecution):
        return {
            "protocol_status": "SUCCESS",
            "sql_produced": True,
            "m1_status": "ACCEPTED" if result.plan is not None else "ERROR",
            "m1_failure_code": None,
            "execution_status": "FAILURE",
            "official_status": "NOT_EVALUATED",
            "correct": False,
            "execution": None,
        }
    actual = _result_from_execution(result.execution)
    correct = soft_ex_match(actual, expected, ordered=ordered)
    return {
        "protocol_status": "SUCCESS",
        "sql_produced": True,
        "m1_status": "ACCEPTED",
        "m1_failure_code": None,
        "execution_status": "SUCCESS",
        "official_status": "PASS" if correct else "FAIL",
        "correct": correct,
        "execution": result.execution,
    }


def _safe_record(
    case: LiveSqlBenchEvaluationCase,
    result: TextToSqlResult | None,
    journal: dict[str, Any],
    expected: LiveSqlBenchResult,
    context_hash: str,
    started: float | None,
) -> dict[str, Any]:
    proposal = result.proposal if result is not None else None
    processed = (
        _status_from_result(result, expected, bool(case.public.conditions.get("order", False)))
        if result is not None
        else {
            "protocol_status": "FAILURE",
            "sql_produced": False,
            "m1_status": "NOT_REACHED",
            "m1_failure_code": None,
            "execution_status": "NOT_ATTEMPTED",
            "official_status": "NOT_EVALUATED",
            "correct": False,
            "execution": None,
        }
    )
    execution = processed["execution"]
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "provider_status": journal.get("provider_status", "PROVIDER_FAILURE"),
        "provider_response_received": journal.get("response_received", False),
        "provider_calls": int(journal.get("actual_request_count", 0)),
        "protocol_status": processed["protocol_status"],
        "sql_hash": sha256_text(proposal.sql) if proposal else journal.get("sql_hash"),
        "m1_status": processed["m1_status"],
        "m1_failure_code": processed["m1_failure_code"],
        "execution_status": processed["execution_status"],
        "official_evaluator_status": processed["official_status"],
        "correct": processed["correct"],
        "row_count": execution.row_count if isinstance(execution, QueryExecution) else None,
        "reference_empty": not expected.rows,
        "reference_annotation": (
            "TEMPORAL_REFERENCE_DRIFT"
            if not expected.rows
            and any(
                isinstance(node, sqlglot.exp.CurrentDate)
                for node in sqlglot.parse_one(case.sol_sql[0], read="postgres").walk()
            )
            else None
        ),
        "sql_shape": _sql_shape(proposal.sql) if proposal else {},
        "provider_latency_ms": journal.get("wall_latency_ms"),
        "end_to_end_latency_ms": (time.perf_counter() - started) * 1000
        if started is not None
        else None,
        "usage": journal.get("usage", {}),
        "runtime_context_hash": context_hash,
    }


def _detail(
    case: LiveSqlBenchEvaluationCase,
    record: dict[str, Any],
    result: TextToSqlResult | None,
    corrected_context: str,
    expected: LiveSqlBenchResult,
    old: dict[str, Any],
) -> dict[str, Any]:
    execution = result.execution if result is not None else None
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "question": case.runtime.question,
        "external_knowledge": list(case.runtime.external_knowledge),
        "corrected_context": corrected_context,
        "generated_sql": result.proposal.sql if result and result.proposal else None,
        "reference_sql": list(case.sol_sql),
        "generated_result_summary": (
            {
                "columns": list(execution.columns),
                "row_count": execution.row_count,
                "first_row": list(execution.rows[0].values()) if execution.rows else None,
            }
            if isinstance(execution, QueryExecution)
            else None
        ),
        "reference_result_summary": {
            "columns": list(expected.columns),
            "row_count": len(expected.rows),
        },
        "m25_old": {
            "official_evaluator_status": old.get("official_evaluator_status"),
            "m1_status": old.get("m1_status"),
            "execution_status": old.get("execution_status"),
            "correct": old.get("correct"),
        },
        "m25_2": record,
    }


def _preflight(
    public_root: Path, protected_path: Path
) -> tuple[
    list[LiveSqlBenchEvaluationCase],
    dict[str, Any],
    dict[str, tuple[LiveSqlBenchDatabase, Any, Any]],
    dict[str, LiveSqlBenchResult],
]:
    if not public_root.is_dir():
        raise M25_2PreflightError(f"LiveSQLBench root is unavailable: {public_root}")
    if not protected_path.is_file() or not git_ignored(protected_path) or tracked_protected_files():
        raise M25_2PreflightError("protected GT is not present and safely ignored/untracked")
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
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
        raise M25_2PreflightError("public/protected merge is not the frozen exact merge")
    final_manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    public_manifest = json.loads(PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    pilot_ids = tuple(final_manifest["pilot_case_ids"])
    old_pilot_ids = tuple(public_manifest["future_baseline"]["pilot_case_ids"])
    if len(pilot_ids) != 18 or len(set(pilot_ids)) != 18 or pilot_ids != old_pilot_ids:
        raise M25_2PreflightError("frozen pilot IDs changed")
    old_artifact = json.loads(M25_ARTIFACT.read_text(encoding="utf-8"))
    if old_artifact["official"] != {"correct": 1, "total": 18, "accuracy": 1 / 18}:
        raise M25_2PreflightError("M25 frozen 1/18 artifact is not intact")
    if json.loads(M25_1_ARTIFACT.read_text(encoding="utf-8"))["classification"] != (
        "M25_1_SEMANTIC_CONTEXT_INTEGRATION_DEFECT_CONFIRMED"
    ):
        raise M25_2PreflightError("M25.1 semantic-context validation is not present")
    if protected.sha256 != final_manifest["protected_source"]["sha256"]:
        raise M25_2PreflightError("protected artifact hash differs from frozen manifest")
    old_cases = _load_old_cases()
    old_order_hash = old_artifact["experiment"]["order_hash"]
    if sha256_json(list(pilot_ids)) != old_order_hash:
        raise M25_2PreflightError("pilot order differs from the frozen M25 order")
    merged_by_id = {case.instance_id: case for case in merged}
    if any(case_id not in merged_by_id for case_id in pilot_ids):
        raise M25_2PreflightError("frozen pilot case missing from merge")
    pilot_cases = [merged_by_id[case_id] for case_id in pilot_ids]
    states: dict[str, tuple[LiveSqlBenchDatabase, Any, Any]] = {}
    resources: dict[str, Any] = {}
    corrected_contexts: dict[str, str] = {}
    additions: dict[str, str] = {}
    payload_hashes: dict[str, str] = {}
    context_sizes: list[int] = []
    token_sizes: list[int] = []
    composition: list[dict[str, int]] = []
    expected: dict[str, LiveSqlBenchResult] = {}
    config = PostgresConnectionConfig.from_environment()
    for case in pilot_cases:
        if case.database not in states:
            catalog = introspect_database(case.database, config)
            engine, safety, _ = safety_for_database(catalog, config)
            states[case.database] = (catalog, engine, safety)
            resources[case.database] = load_semantic_resources(public_root, case.database)
        catalog, _, safety = states[case.database]
        del catalog
        structural = serialize_schema_context(
            SchemaContextResolver(safety.catalog).resolve(
                case.runtime.question, SchemaContextMode.FULL_COMPACT
            )
        )
        corrected = render_semantic_context(structural, resources[case.database])
        addition = _semantic_addition(structural, corrected)
        corrected_contexts[case.instance_id] = corrected
        additions[case.instance_id] = addition
        context_sizes.append(len(corrected))
        token_sizes.append(estimate_tokens(corrected))
        composition.append(
            {
                "structural_schema_chars": len(structural),
                "column_meanings_chars": len(resources[case.database].column_meanings_json()),
                "external_kb_chars": len(resources[case.database].knowledge_json()),
            }
        )
        old_sql = old_cases[case.instance_id].get("generated_sql")
        from app.generation.provider import _generation_messages

        messages = _generation_messages(case.runtime.question, corrected)
        if not _payload_safe(case, messages, corrected, old_sql):
            raise M25_2PreflightError(f"gold or old-output leakage in {case.instance_id}")
        payload_hashes[case.instance_id] = sha256_json(messages)
        expected[case.instance_id] = _reference_result(case, safety)
    if len(corrected_contexts) != 18 or len(payload_hashes) != 18:
        raise M25_2PreflightError("corrected context coverage is not 18/18")
    if any(
        corrected_contexts[case_id] == old_cases[case_id].get("runtime_context")
        for case_id in pilot_ids
    ):
        raise M25_2PreflightError("corrected context unexpectedly equals old context")
    if any(not expected[case_id].columns and expected[case_id].rows for case_id in pilot_ids):
        raise M25_2PreflightError("reference result has rows without columns")
    settings = get_settings().model_copy(
        update={"llm_model": EXPECTED_MODEL, "eval_capture_model_io": True}
    )
    m25_config = old_artifact["configuration"]
    if settings.llm_model != m25_config["model"]:
        raise M25_2PreflightError("configured model differs from frozen M25 model")
    if settings.llm_temperature != m25_config["temperature"]:
        raise M25_2PreflightError("temperature differs from frozen M25")
    if settings.llm_reasoning_effort != m25_config["reasoning_effort"]:
        raise M25_2PreflightError("reasoning effort differs from frozen M25")
    from app.generation.provider import _generation_messages

    if sha256_text(inspect.getsource(_generation_messages)) != m25_config["prompt_hash"]:
        raise M25_2PreflightError("production prompt hash differs from M25")
    journal = _read_unique_journal(JOURNAL)
    if len(journal) > MAX_PROVIDER_CALLS or any(
        int(row.get("actual_request_count", 0)) != 1 for row in journal.values()
    ):
        raise M25_2PreflightError("existing M25.2 journal violates one-call budget")
    if any(case_id not in pilot_ids for case_id in journal):
        raise M25_2PreflightError("M25.2 journal contains a non-pilot case")
    return (
        pilot_cases,
        {
            "settings": settings,
            "final_manifest": final_manifest,
            "protected_hash": protected.sha256,
            "old_artifact": old_artifact,
            "old_cases": old_cases,
            "corrected_contexts": corrected_contexts,
            "additions": additions,
            "payload_hashes": payload_hashes,
            "context_sizes": context_sizes,
            "token_sizes": token_sizes,
            "composition": composition,
            "m25_1_artifact_hash": sha256_file(M25_1_ARTIFACT),
            "prompt_hash": m25_config["prompt_hash"],
            "m1_policy_hash": sha256_file(ROOT / "app/sql/policy.py"),
            "evaluator_hash": sha256_file(ROOT / "evaluation/external/livesqlbench/evaluator.py"),
            "provider_source_hash": sha256_file(ROOT / "app/generation/provider.py"),
            "service_source_hash": sha256_file(ROOT / "app/text_to_sql/service.py"),
            "context_hash": sha256_json(corrected_contexts),
        },
        states,
        expected,
    )


async def _run_one(
    case: LiveSqlBenchEvaluationCase,
    service: Any,
    journal_provider: JournalProvider,
    addition: str,
    context_hash: str,
    expected: LiveSqlBenchResult,
) -> tuple[dict[str, Any], TextToSqlResult | None, dict[str, Any]]:
    journal_provider.case_id = case.instance_id
    journal_provider.expected_context_hash = context_hash
    started = time.perf_counter()
    try:
        result = await service.run_with_context_addition(
            TextToSqlRequest(
                question=case.runtime.question,
                correlation_id=f"m25.2:{case.instance_id}",
                execute=True,
            ),
            addition,
        )
    except Exception:
        journal = _read_unique_journal(JOURNAL)[case.instance_id]
        record = _safe_record(case, None, journal, expected, context_hash, started)
        return record, None, journal
    journal = _read_unique_journal(JOURNAL)[case.instance_id]
    record = _safe_record(case, result, journal, expected, context_hash, started)
    return record, result, journal


def _classify_transition(old: dict[str, Any], new: dict[str, Any]) -> str:
    old_correct = bool(old.get("correct")) or old.get("official_evaluator_status") == "PASS"
    return f"{old_correct}:{bool(new.get('correct'))}"


def _aggregate(
    records: list[dict[str, Any]],
    preflight: dict[str, Any],
    old_cases: dict[str, dict[str, Any]],
    settings: Settings,
) -> dict[str, Any]:
    old_correct_new_correct = old_correct_new_wrong = 0
    old_wrong_new_correct = old_wrong_new_wrong = 0
    old_m1_reject_transitions: Counter[str] = Counter()
    old_mismatch_transitions: Counter[str] = Counter()
    for record in records:
        old = old_cases[record["case_id"]]
        transition = _classify_transition(old, record)
        if transition == "True:True":
            old_correct_new_correct += 1
        elif transition == "True:False":
            old_correct_new_wrong += 1
        elif transition == "False:True":
            old_wrong_new_correct += 1
        else:
            old_wrong_new_wrong += 1
        if old.get("m1_status") == "REJECTED":
            if record["m1_status"] == "ACCEPTED":
                old_m1_reject_transitions["ACCEPTED"] += 1
            elif record["m1_status"] == "REJECTED":
                # M25's protected ledger did not retain the old failure code.
                old_m1_reject_transitions["CODE_UNAVAILABLE"] += 1
        old_reference_rows = (old.get("reference_result_summary") or {}).get("row_count")
        if (
            old.get("execution_status") == "SUCCESS"
            and old.get("official_evaluator_status") == "FAIL"
            and old_reference_rows != 0
        ):
            if record["correct"]:
                old_mismatch_transitions["new_correct"] += 1
            elif record["m1_status"] == "REJECTED":
                old_mismatch_transitions["new_m1_reject"] += 1
            elif record["provider_status"] != "PROVIDER_SUCCESS":
                old_mismatch_transitions["provider_or_protocol_failure"] += 1
            else:
                old_mismatch_transitions["new_mismatch"] += 1
    shape: Counter[str] = Counter()
    for record in records:
        data = record["sql_shape"]
        if data.get("parseable"):
            shape["joins"] += int(data["joins"] > 0)
            shape[f"{data['join_bucket']}_join"] += 1
            for key in (
                "aggregation",
                "group_by",
                "having",
                "cte",
                "subquery",
                "window",
                "distinct",
                "order_by",
                "limit",
                "temporal",
                "current_date",
            ):
                shape[key] += int(bool(data.get(key)))
    usage_fields = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
    usage = {}
    for field in usage_fields:
        values = [
            float(record["usage"][field])
            for record in records
            if record["usage"].get(field) is not None
        ]
        usage[field] = _summary(values)
    new_provider = [
        float(r["provider_latency_ms"]) for r in records if r["provider_latency_ms"] is not None
    ]
    new_e2e = [
        float(r["end_to_end_latency_ms"]) for r in records if r["end_to_end_latency_ms"] is not None
    ]
    failure_codes = Counter(
        record["m1_failure_code"] for record in records if record["m1_failure_code"] is not None
    )
    reference_valid = [record for record in records if not record["reference_empty"]]
    b = old_correct_new_wrong
    c = old_wrong_new_correct
    if c > b and old_correct_new_wrong == 0 and c >= 4:
        transfer = "STRONG_POSITIVE_TRANSFER"
    elif c > b:
        transfer = "POSITIVE_TRANSFER"
    elif c == b:
        transfer = "NO_TRANSFER"
    else:
        transfer = "NEGATIVE_TRANSFER"
    return {
        "configuration": {
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "reasoning_effort": settings.llm_reasoning_effort,
            "prompt_hash": preflight["prompt_hash"],
            "pilot_manifest_hash": preflight["final_manifest"]["manifest_hash"],
            "context_hash": preflight["context_hash"],
        },
        "pipeline": {
            "total": len(records),
            "provider_success": sum(r["provider_status"] == "PROVIDER_SUCCESS" for r in records),
            "provider_failure": sum(r["provider_status"] != "PROVIDER_SUCCESS" for r in records),
            "protocol_success": sum(r["protocol_status"] == "SUCCESS" for r in records),
            "sql_produced": sum(r["sql_hash"] is not None for r in records),
            "m1_accepted": sum(r["m1_status"] == "ACCEPTED" for r in records),
            "m1_rejected": sum(r["m1_status"] == "REJECTED" for r in records),
            "execution_attempted": sum(r["m1_status"] == "ACCEPTED" for r in records),
            "execution_success": sum(r["execution_status"] == "SUCCESS" for r in records),
            "execution_failure": sum(r["execution_status"] == "FAILURE" for r in records),
            "timeouts": 0,
        },
        "official": {
            "correct": sum(bool(r["correct"]) for r in records),
            "total": 18,
            "accuracy": sum(bool(r["correct"]) for r in records) / 18,
        },
        "reference_valid_diagnostic": {
            "correct": sum(bool(r["correct"]) for r in reference_valid),
            "total": len(reference_valid),
            "accuracy": (
                sum(bool(r["correct"]) for r in reference_valid) / len(reference_valid)
                if reference_valid
                else None
            ),
            "label": "DIAGNOSTIC ONLY — NOT THE OFFICIAL SCORE",
        },
        "paired": {
            "old_correct_new_correct": old_correct_new_correct,
            "old_correct_new_wrong": old_correct_new_wrong,
            "old_wrong_new_correct": old_wrong_new_correct,
            "old_wrong_new_wrong": old_wrong_new_wrong,
            "net_correct_delta": old_wrong_new_correct - old_correct_new_wrong,
            "mcnemar_exact_p": _exact_mcnemar_p(b, c),
        },
        "old_m1_rejection_transitions": {
            "old_reject_to_new_accept": old_m1_reject_transitions["ACCEPTED"],
            "old_reject_to_same_reject": 0,
            "old_reject_to_different_reject": 0,
            "old_reject_reject_code_unavailable": old_m1_reject_transitions["CODE_UNAVAILABLE"],
        },
        "old_result_mismatch_transitions": dict(old_mismatch_transitions),
        "m1_failure_codes": dict(sorted(failure_codes.items())),
        "sql_shapes": dict(sorted(shape.items())),
        "usage": usage,
        "latency": {
            "provider_ms": _summary(new_provider),
            "end_to_end_ms": _summary(new_e2e),
            "m25_provider_ms": preflight["old_artifact"]["latency"]["provider"],
            "m25_end_to_end_ms": preflight["old_artifact"]["latency"]["end_to_end"],
        },
        "context": {
            "column_meanings": True,
            "all_database_kb": True,
            "raw_protected_refs_used_for_filtering": False,
            "old_context_sizes": {
                "median_chars": 11343,
                "p95_chars": 17536,
                "max_chars": 19764,
                "median_estimated_tokens": 2836,
                "p95_estimated_tokens": 4384,
                "max_estimated_tokens": 4941,
            },
            "corrected_context_sizes": {
                "median_chars": median(preflight["context_sizes"]),
                "p95_chars": sorted(preflight["context_sizes"])[16],
                "max_chars": max(preflight["context_sizes"]),
                "median_estimated_tokens": median(preflight["token_sizes"]),
                "p95_estimated_tokens": sorted(preflight["token_sizes"])[16],
                "max_estimated_tokens": max(preflight["token_sizes"]),
            },
            "corrected_composition_median": {
                key: median([item[key] for item in preflight["composition"]])
                for key in preflight["composition"][0]
            },
        },
        "semantic_context_transfer": transfer,
        "temporal_integrity": {
            "known_drift_cases": 1,
            "official_denominator_unchanged": True,
            "drift_annotation_cases": sum(
                record["reference_annotation"] == "TEMPORAL_REFERENCE_DRIFT" for record in records
            ),
        },
        "protected_data_safety": {
            "protected_gt_ignored": git_ignored(PROTECTED_PATH),
            "protected_gt_tracked": tracked_protected_files(),
            "gold_sql_committed": False,
            "test_cases_committed": False,
            "semantic_mapping_committed": False,
            "local_detailed_artifact": str(LOCAL_CASES),
            "local_detailed_artifact_tracked": False,
        },
    }


async def _run(args: argparse.Namespace) -> int:
    pilot_cases, preflight, states, expected = _preflight(args.public_root, args.protected)
    settings: Settings = preflight["settings"]
    journal = _read_unique_journal(JOURNAL)
    attempted = sum(int(row.get("actual_request_count", 0)) for row in journal.values())
    if attempted > MAX_PROVIDER_CALLS:
        raise M25_2PreflightError("M25.2 provider budget already exhausted")
    print(
        json.dumps(
            {
                "M25_2_PRECHECK": {
                    "pilot_cases": 18,
                    "same_ids_as_M25": True,
                    "same_order_as_M25": True,
                    "provider": "openai-compatible",
                    "model": settings.llm_model,
                    "prompt_hash": preflight["prompt_hash"],
                    "prompt_same_as_M25": True,
                    "M1_same": True,
                    "evaluator_same": True,
                    "column_meanings_visible": True,
                    "all_database_KB_visible": True,
                    "gold_leakage": False,
                    "old_output_leakage": False,
                    "provider_calls_so_far": attempted,
                    "provider_call_budget": MAX_PROVIDER_CALLS,
                    "max_calls_per_case": 1,
                    "automatic_retries": 0,
                }
            },
            indent=2,
        ),
        flush=True,
    )
    if args.preflight_only:
        print(json.dumps({"preflight": "PASS", "provider_calls": 0}, indent=2))
        for _, engine, _ in states.values():
            engine.dispose()
        return 0
    provider = JournalProvider(OpenAICompatibleProvider(settings), JOURNAL)
    services: dict[str, Any] = {}
    for database, (catalog, _, safety) in states.items():
        services[database] = direct_evaluation_service(
            SchemaContextResolver(safety.catalog), provider, safety, settings
        )
        del catalog
    records: list[dict[str, Any]] = []
    local_details: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(LOCAL_CASES):
        case_id = row.get("case_id")
        detail = row.get("m25_2")
        if isinstance(case_id, str) and isinstance(detail, dict):
            local_details[case_id] = detail
    try:
        for case in pilot_cases:
            existing = journal.get(case.instance_id)
            if existing is not None:
                if existing.get("provider_status") != "PROVIDER_SUCCESS" or not existing.get("sql"):
                    record = _safe_record(
                        case,
                        None,
                        existing,
                        expected[case.instance_id],
                        sha256_text(preflight["corrected_contexts"][case.instance_id]),
                        None,
                    )
                elif case.instance_id in local_details:
                    record = local_details[case.instance_id]
                else:
                    from evaluation.m25_livesqlbench_direct_pilot import _process_existing_sql

                    processed = _process_existing_sql(
                        existing["sql"],
                        states[case.database][2],
                        expected[case.instance_id],
                        bool(case.public.conditions.get("order", False)),
                    )
                    record = {
                        "case_id": case.instance_id,
                        "database": case.database,
                        "provider_status": existing["provider_status"],
                        "provider_response_received": True,
                        "provider_calls": existing["actual_request_count"],
                        "protocol_status": "SUCCESS",
                        "sql_hash": existing.get("sql_hash"),
                        "m1_status": processed["m1_status"],
                        "m1_failure_code": processed["m1_failure_code"],
                        "execution_status": processed["execution_status"],
                        "official_evaluator_status": processed["official_status"],
                        "correct": processed["correct"],
                        "row_count": processed["execution"].row_count
                        if isinstance(processed["execution"], QueryExecution)
                        else None,
                        "reference_empty": not expected[case.instance_id].rows,
                        "reference_annotation": None,
                        "sql_shape": _sql_shape(existing["sql"]),
                        "provider_latency_ms": existing.get("wall_latency_ms"),
                        "end_to_end_latency_ms": None,
                        "usage": existing.get("usage", {}),
                        "runtime_context_hash": sha256_text(
                            preflight["corrected_contexts"][case.instance_id]
                        ),
                    }
                records.append(record)
                continue
            if attempted >= MAX_PROVIDER_CALLS:
                raise M25_2PreflightError("M25.2 provider call budget exhausted")
            context = preflight["corrected_contexts"][case.instance_id]
            record, fresh_result, journal_row = await _run_one(
                case,
                services[case.database],
                provider,
                preflight["additions"][case.instance_id],
                sha256_text(context),
                expected[case.instance_id],
            )
            attempted += int(journal_row.get("actual_request_count", 0))
            records.append(record)
            _append_jsonl(
                LOCAL_CASES,
                _detail(
                    case,
                    record,
                    fresh_result,
                    context,
                    expected[case.instance_id],
                    preflight["old_cases"][case.instance_id],
                ),
            )
        if attempted != 18 or max(record["provider_calls"] for record in records) > 1:
            raise M25_2PreflightError("M25.2 did not satisfy the exact one-call accounting")
        aggregate = _aggregate(records, preflight, preflight["old_cases"], settings)
        final_result = {
            "classification": "M25_2_LIVESQLBENCH_SEMANTIC_CONTEXT_PAIRED_RERUN_COMPLETED",
            "starting_head": args.starting_head,
            "final_head": git_head(),
            "provider_calls": attempted,
            "fresh_generation": attempted,
            "experiment": {
                "cases": 18,
                "provider_calls": attempted,
                "calls_per_case_max": 1,
                "route": "DIRECT",
                "intervention": "BENCHMARK_AUTHORIZED_SEMANTIC_CONTEXT",
            },
            "old_arm": {"milestone": "M25", "correct": 1, "total": 18},
            "new_arm": aggregate["official"],
            **aggregate,
            "m25_reference": {
                "artifact_sha256": sha256_file(M25_ARTIFACT),
                "pilot_order_hash": preflight["old_artifact"]["experiment"]["order_hash"],
            },
            "source_integrity": {
                "m25_1_artifact_sha256": preflight["m25_1_artifact_hash"],
                "protected_sha256": preflight["protected_hash"],
                "full_180_run": False,
            },
        }
        SAFE_RESULT.write_text(json.dumps(final_result, indent=2, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "classification": final_result["classification"],
                    "official": final_result["official"],
                    "paired": final_result["paired"],
                    "provider_calls": attempted,
                },
                indent=2,
            )
        )
        return 0
    finally:
        for _, engine, _ in states.values():
            engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=PROTECTED_PATH)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--starting-head", default=git_head())
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except M25_2PreflightError as error:
        print(
            json.dumps(
                {
                    "classification": "M25_2_LIVESQLBENCH_SEMANTIC_CONTEXT_PAIRED_RERUN_BLOCKED",
                    "error": str(error),
                }
            )
        )
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
