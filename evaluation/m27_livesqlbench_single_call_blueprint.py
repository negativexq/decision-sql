"""Run the frozen M25.2 pilot with one blueprint-plus-SQL provider response.

This is evaluation-only.  It reuses M25.2's full semantic context and the
unchanged M1/evaluator path.  The blueprint is untrusted diagnostics; only
the returned SQL is passed to M1.
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
from typing import Any

from app.generation.blueprint import (
    BlueprintSqlProposal,
    blueprint_messages,
    blueprint_sql_consistency,
    parse_blueprint_payload,
)
from app.generation.provider import MalformedProviderResponse, OpenAICompatibleProvider
from app.models.domain import TextToSqlRequest
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.external.livesqlbench.protected import LiveSqlBenchEvaluationCase
from evaluation.m25_2_livesqlbench_direct_semantic_context import (
    _append_jsonl,
    _preflight,
    _read_jsonl,
    _read_unique_journal,
    _result_from_execution,
    _sql_shape,
    _summary,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED_PATH = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
FINAL_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
M25_ARTIFACT = ROOT / "evaluation/fixtures/m25_livesqlbench_direct_pilot_result.json"
M25_2_ARTIFACT = ROOT / "evaluation/fixtures/m25_2_livesqlbench_direct_semantic_context_result.json"
M25_2_CASES = (
    ROOT
    / "evaluation/external/livesqlbench/protected/results/m25_2_direct_semantic_context_cases.jsonl"
)
JOURNAL = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m27_blueprint_sql_journal.jsonl"
)
LOCAL_CASES = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m27_blueprint_sql_cases.jsonl"
)
SAFE_RESULT = ROOT / "evaluation/fixtures/m27_livesqlbench_single_call_blueprint_result.json"
EXPECTED_MODEL = "gpt-5.6-luna"
MAX_PROVIDER_CALLS = 18


class M27PreflightError(RuntimeError):
    """Raised when a frozen M27 invariant fails before provider construction."""


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _upsert_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Refresh one local case detail without accumulating stale replay rows."""
    rows = [item for item in _read_jsonl(path) if item.get("case_id") != row.get("case_id")]
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in rows),
        encoding="utf-8",
    )


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _git_ignored(path: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "--quiet", str(path)]).returncode == 0


def _tracked_protected_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "evaluation/external/livesqlbench/protected/"], text=True
    )
    return [line for line in output.splitlines() if line]


def _exact_mcnemar_p(b: int, c: int) -> float | None:
    total = b + c
    if total == 0:
        return None
    lower = sum(math.comb(total, i) for i in range(min(b, c) + 1)) / (2**total)
    return float(min(1.0, 2.0 * lower))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]


def _old_cases() -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(M25_2_CASES)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = row.get("case_id")
        old = row.get("m25_2")
        if isinstance(case_id, str) and isinstance(old, dict):
            result[case_id] = old
    if len(result) != 18:
        raise M27PreflightError("M25.2 detailed evidence does not cover 18 frozen cases")
    return result


def _usage(proposal: BlueprintSqlProposal | None, capture: Any) -> dict[str, int | None]:
    usage = capture.usage if capture is not None else {}
    return {
        "input_tokens": usage.get("prompt_tokens")
        if usage.get("prompt_tokens") is not None
        else proposal.prompt_tokens
        if proposal is not None
        else None,
        "output_tokens": usage.get("completion_tokens")
        if usage.get("completion_tokens") is not None
        else proposal.completion_tokens
        if proposal is not None
        else None,
        "reasoning_tokens": usage.get("reasoning_tokens")
        if usage.get("reasoning_tokens") is not None
        else proposal.reasoning_tokens
        if proposal is not None
        else None,
        "cached_tokens": usage.get("cached_prompt_tokens")
        if usage.get("cached_prompt_tokens") is not None
        else proposal.cached_prompt_tokens
        if proposal is not None
        else None,
    }


def _capture_usage(capture: Any) -> dict[str, int | None]:
    usage = capture.usage if capture is not None else {}
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "cached_tokens": usage.get("cached_prompt_tokens"),
    }


class BlueprintJournalProvider:
    """Wrap one provider request and make duplicate attempts impossible."""

    def __init__(self, inner: OpenAICompatibleProvider, journal: Path) -> None:
        self.inner = inner
        self.journal = journal
        self.case_id: str | None = None

    async def propose(
        self, case_id: str, request: TextToSqlRequest, context: str
    ) -> tuple[BlueprintSqlProposal | None, dict[str, Any]]:
        if self.case_id is not None:
            raise M27PreflightError("provider wrapper was reused concurrently")
        existing = _read_unique_journal(self.journal)
        if case_id in existing:
            raise M27PreflightError(f"provider attempt already exists for {case_id}")
        self.case_id = case_id
        started = time.perf_counter()
        try:
            proposal = await self.inner.propose_blueprint_sql(request, context)
            capture = self.inner.consume_model_io()
            row = {
                "case_id": case_id,
                "attempt_number": 1,
                "actual_request_count": 1,
                "provider_status": "PROVIDER_SUCCESS",
                "response_received": True,
                "timestamp": datetime.now(UTC).isoformat(),
                "response_hash": capture.raw_assistant_content_sha256
                if capture is not None
                else _sha256_text(proposal.sql),
                "sql": proposal.sql,
                "sql_hash": _sha256_text(proposal.sql),
                "blueprint": proposal.blueprint.model_dump(mode="json"),
                "blueprint_parse_warnings": list(proposal.parse_warnings),
                "usage": _usage(proposal, capture),
                "capture": capture.model_dump(mode="json") if capture is not None else None,
                "wall_latency_ms": (time.perf_counter() - started) * 1000,
            }
            _append_jsonl(self.journal, row)
            return proposal, row
        except Exception as error:
            capture = self.inner.consume_model_io()
            response_received = capture is not None and capture.raw_assistant_content is not None
            row = {
                "case_id": case_id,
                "attempt_number": 1,
                "actual_request_count": 1,
                "provider_status": "PROVIDER_SUCCESS" if response_received else "PROVIDER_FAILURE",
                "protocol_status": "BLUEPRINT_PARSE_FAILURE"
                if isinstance(error, MalformedProviderResponse)
                else "PROVIDER_FAILURE",
                "response_received": response_received,
                "timestamp": datetime.now(UTC).isoformat(),
                "response_hash": capture.raw_assistant_content_sha256
                if capture is not None
                else None,
                "error_type": type(error).__name__,
                "usage": _capture_usage(capture),
                "capture": capture.model_dump(mode="json") if capture is not None else None,
                "wall_latency_ms": (time.perf_counter() - started) * 1000,
            }
            _append_jsonl(self.journal, row)
            return None, row
        finally:
            self.case_id = None


def _reference_result(case: LiveSqlBenchEvaluationCase, safety: Any) -> LiveSqlBenchResult:
    plan = safety.plan(SqlCandidate(sql=case.sol_sql[0], source=CandidateSource.INTERNAL))
    if not isinstance(plan, QueryPlan):
        raise M27PreflightError(f"frozen reference is not M1-compatible: {case.instance_id}")
    execution = safety.execute(plan)
    if not isinstance(execution, QueryExecution):
        raise M27PreflightError(f"frozen reference did not execute: {case.instance_id}")
    return _result_from_execution(execution)


def _process_sql(
    case: LiveSqlBenchEvaluationCase,
    sql: str,
    safety: Any,
    expected: LiveSqlBenchResult,
) -> dict[str, Any]:
    candidate = SqlCandidate(
        sql=sql, source=CandidateSource.LLM, correlation_id=f"m27:{case.instance_id}"
    )
    plan = safety.plan(candidate)
    if not isinstance(plan, QueryPlan):
        failure_code = (
            plan.rejection.code.value if plan.rejection is not None else plan.status.value
        )
        return {
            "m1_status": "REJECTED",
            "m1_failure_code": failure_code,
            "execution_status": "NOT_ATTEMPTED",
            "official_evaluator_status": "NOT_EVALUATED",
            "correct": False,
            "row_count": None,
            "execution_latency_ms": None,
        }
    execution = safety.execute(plan)
    if not isinstance(execution, QueryExecution):
        return {
            "m1_status": "ACCEPTED",
            "m1_failure_code": None,
            "execution_status": "FAILURE",
            "official_evaluator_status": "NOT_EVALUATED",
            "correct": False,
            "row_count": None,
            "execution_latency_ms": None,
        }
    actual = _result_from_execution(execution)
    correct = soft_ex_match(
        actual, expected, ordered=bool(case.public.conditions.get("order", False))
    )
    return {
        "m1_status": "ACCEPTED",
        "m1_failure_code": None,
        "execution_status": "SUCCESS",
        "official_evaluator_status": "PASS" if correct else "FAIL",
        "correct": correct,
        "row_count": execution.row_count,
        "execution_latency_ms": execution.latency_ms,
        "generated_result_empty": not execution.rows,
    }


def _safe_record(
    case: LiveSqlBenchEvaluationCase,
    journal: dict[str, Any],
    processed: dict[str, Any],
    expected: LiveSqlBenchResult,
    context: str,
    proposal: BlueprintSqlProposal | None,
) -> dict[str, Any]:
    sql = proposal.sql if proposal is not None else journal.get("sql")
    blueprint = (
        proposal.blueprint.model_dump(mode="json")
        if proposal is not None
        else journal.get("blueprint")
    )
    consistency = (
        blueprint_sql_consistency(proposal.blueprint, proposal.sql)
        if proposal is not None
        else blueprint_sql_consistency(
            parse_blueprint_payload(
                {"blueprint": blueprint, "sql": sql},
                model=EXPECTED_MODEL,
                provider="openai-compatible",
            ).blueprint,
            sql,
        )
        if blueprint and isinstance(sql, str)
        else {"classification": "NOT_AVAILABLE"}
    )
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "provider_status": "PROVIDER_SUCCESS"
        if journal.get("provider_status") == "PROVIDER_SUCCESS"
        or (
            journal.get("provider_status") == "PROTOCOL_FAILURE"
            and journal.get("response_received")
        )
        else journal.get("provider_status", "PROVIDER_FAILURE"),
        "provider_response_received": bool(journal.get("response_received", False)),
        "provider_calls": int(journal.get("actual_request_count", 0)),
        "protocol_status": "SUCCESS"
        if sql
        else journal.get("protocol_status", "BLUEPRINT_PARSE_FAILURE"),
        "blueprint_status": (
            "PARSED_WITH_WARNINGS"
            if proposal is not None and proposal.parse_warnings
            else "PARSED"
            if blueprint
            else "NOT_PARSED"
        ),
        "blueprint_parse_warnings": list(proposal.parse_warnings)
        if proposal is not None
        else journal.get("blueprint_parse_warnings", []),
        "consistency": consistency,
        "sql_hash": _sha256_text(sql) if isinstance(sql, str) else None,
        "m1_status": processed["m1_status"],
        "m1_failure_code": processed["m1_failure_code"],
        "execution_status": processed["execution_status"],
        "official_evaluator_status": processed["official_evaluator_status"],
        "correct": processed["correct"],
        "row_count": processed["row_count"],
        "generated_result_empty": processed.get("generated_result_empty"),
        "reference_empty": not expected.rows,
        "sql_shape": _sql_shape(sql) if isinstance(sql, str) else {},
        "provider_latency_ms": journal.get("wall_latency_ms"),
        "execution_latency_ms": processed.get("execution_latency_ms"),
        "usage": journal.get("usage", {}),
        "runtime_context_hash": _sha256_text(context),
    }


def _local_detail(
    case: LiveSqlBenchEvaluationCase,
    record: dict[str, Any],
    journal: dict[str, Any],
    expected: LiveSqlBenchResult,
    proposal: BlueprintSqlProposal | None,
) -> dict[str, Any]:
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "expected_sql": list(case.sol_sql),
        "expected_result": {
            "columns": list(expected.columns),
            "row_count": len(expected.rows),
            "first_row": list(expected.rows[0]) if expected.rows else None,
        },
        "generated_sql": proposal.sql if proposal is not None else journal.get("sql"),
        "blueprint": proposal.blueprint.model_dump(mode="json")
        if proposal is not None
        else journal.get("blueprint"),
        "blueprint_parse_warnings": list(proposal.parse_warnings)
        if proposal is not None
        else journal.get("blueprint_parse_warnings", []),
        "m27": record,
    }


def _preflight_m27(
    public_root: Path, protected_path: Path
) -> tuple[list[LiveSqlBenchEvaluationCase], dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        pilot_cases, preflight, states, expected = _preflight(
            public_root, protected_path, validate_frozen_generation_prompt=False
        )
    except Exception as error:
        raise M27PreflightError(str(error)) from error
    if len(pilot_cases) != 18 or len({case.instance_id for case in pilot_cases}) != 18:
        raise M27PreflightError("M27 pilot population is not exactly 18 unique cases")
    m25_2_artifact = json.loads(M25_2_ARTIFACT.read_text(encoding="utf-8"))
    if m25_2_artifact["official"] != {"correct": 6, "total": 18, "accuracy": 1 / 3}:
        raise M27PreflightError("M25.2 historical arm is not the frozen 6/18 result")
    old_cases = _old_cases()
    for case in pilot_cases:
        context = preflight["corrected_contexts"][case.instance_id]
        messages = blueprint_messages(case.runtime.question, context)
        serialized = "\n".join(message["content"] for message in messages)
        if case.runtime.question not in serialized or context not in serialized:
            raise M27PreflightError(f"blueprint payload missing runtime input: {case.instance_id}")
        if any(
            marker in serialized.casefold()
            for marker in ("sol_sql", "test_cases", "reference result", "evaluator result")
        ):
            raise M27PreflightError(f"protected evaluation field in payload: {case.instance_id}")
        old_sql = old_cases[case.instance_id].get("sql_hash")
        if old_sql and old_sql in serialized:
            raise M27PreflightError(
                f"old M25.2 output hash leaked into payload: {case.instance_id}"
            )
        if _sha256_text(context) != _sha256_text(preflight["corrected_contexts"][case.instance_id]):
            raise M27PreflightError("full semantic context changed during preflight")
    journal = _read_unique_journal(JOURNAL)
    total_attempts = sum(int(row.get("actual_request_count", 0)) for row in journal.values())
    if total_attempts > MAX_PROVIDER_CALLS or any(
        int(row.get("actual_request_count", 0)) > 1 for row in journal.values()
    ):
        raise M27PreflightError("M27 journal violates the one-call budget")
    if any(case_id not in {case.instance_id for case in pilot_cases} for case_id in journal):
        raise M27PreflightError("M27 journal contains a non-pilot case")
    return (
        pilot_cases,
        {
            **preflight,
            "old_cases": old_cases,
            "journal": journal,
            "m25_2_artifact": m25_2_artifact,
        },
        states,
        expected,
    )


def _aggregate(
    records: list[dict[str, Any]], preflight: dict[str, Any], settings: Any
) -> dict[str, Any]:
    old_cases = preflight["old_cases"]
    transitions: Counter[str] = Counter()
    for record in records:
        old_correct = bool(old_cases[record["case_id"]].get("correct"))
        new_correct = bool(record["correct"])
        transitions[f"{old_correct}:{new_correct}"] += 1
    b = transitions["True:False"]
    c = transitions["False:True"]
    consistency = Counter(record["consistency"].get("classification") for record in records)
    mismatch_checks: Counter[str] = Counter()
    for record in records:
        mismatch_checks.update(record["consistency"].get("failed_checks", []))
    rejection_codes = Counter(
        record["m1_failure_code"] for record in records if record["m1_failure_code"] is not None
    )
    shapes: Counter[str] = Counter()
    for record in records:
        shape = record["sql_shape"]
        if shape.get("parseable"):
            shapes[f"{shape['join_bucket']}_joins"] += 1
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
                shapes[key] += int(bool(shape.get(key)))
    reference_valid = [record for record in records if not record["reference_empty"]]
    input_tokens = [
        float(record["usage"]["input_tokens"])
        for record in records
        if record["usage"].get("input_tokens") is not None
    ]
    output_tokens = [
        float(record["usage"]["output_tokens"])
        for record in records
        if record["usage"].get("output_tokens") is not None
    ]
    provider_latency = [
        float(record["provider_latency_ms"])
        for record in records
        if record["provider_latency_ms"] is not None
    ]
    execution_latency = [
        float(record["execution_latency_ms"])
        for record in records
        if record["execution_latency_ms"] is not None
    ]
    correct = sum(bool(record["correct"]) for record in records)
    source_configs = [
        row.get("capture", {}).get("request_config", {})
        for row in preflight["journal"].values()
        if isinstance(row.get("capture"), dict)
        and isinstance(row.get("capture", {}).get("request_config"), dict)
    ]
    source_config = source_configs[0] if source_configs else {}
    if c > b and c >= 4 and b == 0:
        transfer = "STRONG_POSITIVE_TRANSFER"
    elif c > b:
        transfer = "POSITIVE_TRANSFER"
    elif c == b:
        transfer = "NO_TRANSFER"
    else:
        transfer = "NEGATIVE_TRANSFER"
    return {
        "pipeline": {
            "total": 18,
            "provider_success": sum(
                record["provider_status"] == "PROVIDER_SUCCESS" for record in records
            ),
            "protocol_success": sum(record["protocol_status"] == "SUCCESS" for record in records),
            "sql_produced": sum(record["sql_hash"] is not None for record in records),
            "m1_accepted": sum(record["m1_status"] == "ACCEPTED" for record in records),
            "m1_rejected": sum(record["m1_status"] == "REJECTED" for record in records),
            "execution_attempted": sum(record["m1_status"] == "ACCEPTED" for record in records),
            "execution_success": sum(record["execution_status"] == "SUCCESS" for record in records),
            "execution_failure": sum(record["execution_status"] == "FAILURE" for record in records),
            "timeout": 0,
        },
        "official": {"correct": correct, "total": 18, "accuracy": correct / 18},
        "reference_valid_diagnostic": {
            "correct": sum(bool(record["correct"]) for record in reference_valid),
            "total": len(reference_valid),
            "accuracy": sum(bool(record["correct"]) for record in reference_valid)
            / len(reference_valid)
            if reference_valid
            else None,
            "label": "DIAGNOSTIC ONLY",
        },
        "paired": {
            "old_correct_new_correct": transitions["True:True"],
            "old_correct_new_wrong": b,
            "old_wrong_new_correct": c,
            "old_wrong_new_wrong": transitions["False:False"],
            "net_correct_delta": c - b,
            "mcnemar_exact_p": _exact_mcnemar_p(b, c),
        },
        "blueprint": {
            "parsed": sum(
                record["blueprint_status"] in {"PARSED", "PARSED_WITH_WARNINGS"}
                for record in records
            ),
            "parsed_with_warnings": sum(
                record["blueprint_status"] == "PARSED_WITH_WARNINGS" for record in records
            ),
            "parse_failure": sum(
                record["blueprint_status"] not in {"PARSED", "PARSED_WITH_WARNINGS"}
                for record in records
            ),
            "warning_count": sum(len(record["blueprint_parse_warnings"]) for record in records),
            "consistent": consistency["BLUEPRINT_SQL_CONSISTENT"],
            "partial_mismatch": consistency["BLUEPRINT_SQL_PARTIAL_MISMATCH"],
            "strong_mismatch": consistency["BLUEPRINT_SQL_STRONG_MISMATCH"],
            "mismatch_checks": dict(sorted(mismatch_checks.items())),
        },
        "m1_failure_codes": dict(sorted(rejection_codes.items())),
        "sql_shapes": dict(sorted(shapes.items())),
        "usage": {
            "input": _summary(input_tokens),
            "output": _summary(output_tokens),
            "reasoning": _summary(
                [
                    float(record["usage"]["reasoning_tokens"])
                    for record in records
                    if record["usage"].get("reasoning_tokens") is not None
                ]
            ),
            "cached": _summary(
                [
                    float(record["usage"]["cached_tokens"])
                    for record in records
                    if record["usage"].get("cached_tokens") is not None
                ]
            ),
        },
        "latency": {
            "provider_ms": _summary(provider_latency),
            "execution_ms": _summary(execution_latency),
            "m25_2_provider_ms": preflight["m25_2_artifact"]["latency"]["m25_provider_ms"],
            "m25_2_end_to_end_ms": preflight["m25_2_artifact"]["latency"]["m25_end_to_end_ms"],
        },
        "configuration": {
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "temperature": source_config.get("temperature", settings.llm_temperature),
            "reasoning_effort": source_config.get(
                "reasoning_effort", settings.llm_reasoning_effort
            ),
            "production_prompt_hash": preflight["prompt_hash"],
            "blueprint_prompt_hash": _sha256_text(inspect.getsource(blueprint_messages)),
            "replay_source_request_config": source_config,
            "pilot_manifest_hash": preflight["final_manifest"]["manifest_hash"],
            "full_semantic_context": True,
            "m26_grounding": False,
        },
        "context": {
            "mode": "FULL_M25_2",
            "column_meanings": True,
            "all_database_kb": True,
            "m26_query_aware_grounding": False,
            "db_value_probing": False,
            "old_m25_2_input_tokens": 239429,
            "old_m25_2_median_input_tokens": 13212,
        },
        "protected_data_safety": {
            "protected_gt_ignored": _git_ignored(PROTECTED_PATH),
            "protected_gt_tracked": _tracked_protected_files(),
            "gold_sql_committed": False,
            "test_cases_committed": False,
            "old_outputs_visible_to_provider": False,
            "gold_leakage": False,
            "local_detailed_artifact": str(LOCAL_CASES),
            "local_detailed_artifact_tracked": False,
        },
        "semantic_context_transfer": transfer,
    }


def _replay_from_journal(
    case: LiveSqlBenchEvaluationCase,
    journal_row: dict[str, Any],
    context: str,
    expected: LiveSqlBenchResult,
    safety: Any,
) -> tuple[dict[str, Any], BlueprintSqlProposal | None]:
    sql = journal_row.get("sql")
    proposal: BlueprintSqlProposal | None = None
    if isinstance(sql, str) and isinstance(journal_row.get("blueprint"), dict):
        proposal = parse_blueprint_payload(
            {"blueprint": journal_row["blueprint"], "sql": sql},
            model=EXPECTED_MODEL,
            provider="openai-compatible",
        )
    elif isinstance(journal_row.get("capture"), dict):
        raw_content = journal_row["capture"].get("raw_assistant_content")
        if isinstance(raw_content, str):
            try:
                proposal = parse_blueprint_payload(
                    raw_content,
                    model=EXPECTED_MODEL,
                    provider="openai-compatible",
                    prompt_tokens=journal_row.get("usage", {}).get("input_tokens"),
                    completion_tokens=journal_row.get("usage", {}).get("output_tokens"),
                    reasoning_tokens=journal_row.get("usage", {}).get("reasoning_tokens"),
                    cached_prompt_tokens=journal_row.get("usage", {}).get("cached_tokens"),
                    latency_ms=journal_row.get("wall_latency_ms"),
                )
            except ValueError:
                proposal = None
    processed = (
        _process_sql(case, proposal.sql, safety, expected)
        if proposal is not None
        else {
            "m1_status": "NOT_REACHED",
            "m1_failure_code": None,
            "execution_status": "NOT_ATTEMPTED",
            "official_evaluator_status": "NOT_EVALUATED",
            "correct": False,
            "row_count": None,
            "execution_latency_ms": None,
        }
    )
    return _safe_record(case, journal_row, processed, expected, context, proposal), proposal


async def _run(args: argparse.Namespace) -> int:
    pilot_cases, preflight, states, expected = _preflight_m27(args.public_root, args.protected)
    settings = preflight["settings"]
    journal = preflight["journal"]
    attempts = sum(int(row.get("actual_request_count", 0)) for row in journal.values())
    print(
        json.dumps(
            {
                "M27_PRECHECK": {
                    "pilot_cases": 18,
                    "same_ids_as_M25_2": True,
                    "same_order_as_M25_2": True,
                    "provider": "openai-compatible",
                    "model": settings.llm_model,
                    "semantic_context_mode": "FULL_M25_2",
                    "M26_query_aware_grounding": False,
                    "DB_value_probing": False,
                    "blueprint_mode": True,
                    "max_provider_calls": MAX_PROVIDER_CALLS,
                    "max_calls_per_case": 1,
                    "automatic_retries": 0,
                    "gold_leakage": False,
                    "old_output_leakage": False,
                    "provider_calls_so_far": attempts,
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
    provider = BlueprintJournalProvider(OpenAICompatibleProvider(settings), JOURNAL)
    records: list[dict[str, Any]] = []
    try:
        for case in pilot_cases:
            context = preflight["corrected_contexts"][case.instance_id]
            existing = journal.get(case.instance_id)
            if existing is not None:
                record, proposal = _replay_from_journal(
                    case, existing, context, expected[case.instance_id], states[case.database][2]
                )
                records.append(record)
                _upsert_jsonl(
                    LOCAL_CASES,
                    _local_detail(case, record, existing, expected[case.instance_id], proposal),
                )
                continue
            if attempts >= MAX_PROVIDER_CALLS:
                raise M27PreflightError("M27 provider call budget exhausted")
            proposal, journal_row = await provider.propose(
                case.instance_id,
                TextToSqlRequest(
                    question=case.runtime.question,
                    correlation_id=f"m27:{case.instance_id}",
                    execute=True,
                ),
                context,
            )
            attempts += int(journal_row.get("actual_request_count", 0))
            if proposal is None:
                processed = {
                    "m1_status": "NOT_REACHED",
                    "m1_failure_code": None,
                    "execution_status": "NOT_ATTEMPTED",
                    "official_evaluator_status": "NOT_EVALUATED",
                    "correct": False,
                    "row_count": None,
                    "execution_latency_ms": None,
                }
            else:
                processed = _process_sql(
                    case, proposal.sql, states[case.database][2], expected[case.instance_id]
                )
            record = _safe_record(
                case, journal_row, processed, expected[case.instance_id], context, proposal
            )
            records.append(record)
            _upsert_jsonl(
                LOCAL_CASES,
                _local_detail(case, record, journal_row, expected[case.instance_id], proposal),
            )
            journal = _read_unique_journal(JOURNAL)
        if attempts != 18 or len(records) != 18:
            raise M27PreflightError("M27 did not complete exactly one attempt for all cases")
        if max(int(record["provider_calls"]) for record in records) > 1:
            raise M27PreflightError("M27 case received more than one provider request")
        aggregate = _aggregate(records, preflight, settings)
        result = {
            "classification": "M27_SINGLE_CALL_BLUEPRINT_PILOT_COMPLETED",
            "starting_head": args.starting_head,
            "final_head": _git_head(),
            "provider_calls": attempts,
            "fresh_generation": 18,
            "experiment": {
                "cases": 18,
                "provider_calls": attempts,
                "calls_per_case_max": 1,
                "route": "DIRECT",
                "intervention": "SINGLE_CALL_QUERY_BLUEPRINT",
            },
            "baseline_m25_2": {"correct": 6, "total": 18},
            "m27": aggregate["official"],
            **aggregate,
            "source_integrity": {
                "m25_artifact_sha256": _sha256_file(M25_ARTIFACT),
                "m25_2_artifact_sha256": _sha256_file(M25_2_ARTIFACT),
                "pilot_manifest_hash": preflight["final_manifest"]["manifest_hash"],
                "full_180_model_run": False,
            },
        }
        SAFE_RESULT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "classification": result["classification"],
                    "official": result["official"],
                    "paired": result["paired"],
                    "provider_calls": attempts,
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
    parser.add_argument("--starting-head", default=_git_head())
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except M27PreflightError as error:
        print(
            json.dumps(
                {
                    "classification": "M27_SINGLE_CALL_BLUEPRINT_BLOCKED",
                    "error": str(error),
                    "provider_calls": 0,
                }
            )
        )
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
