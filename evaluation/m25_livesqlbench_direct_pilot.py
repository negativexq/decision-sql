"""Run the frozen, one-call LiveSQLBench DIRECT pilot.

The runner uses the existing TextToSqlService DIRECT path.  It has no retry,
repair, judge, routing, ResultShape, QueryPlan, or provider-side second call.
Protected case-level output is written below the already ignored protected
directory; the committed result contains only safe aggregate metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import subprocess
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlglot

from app.config import Settings, get_settings
from app.generation.provider import (
    MalformedProviderResponse,
    OpenAICompatibleProvider,
    SqlProposal,
)
from app.models.domain import TextToSqlRequest
from app.observability.tracing import get_tracer
from app.provenance.sink import NoOpProvenanceSink
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.text_to_sql.models import GenerationMode, GenerationStrategy, TextToSqlResult
from app.text_to_sql.service import TextToSqlService
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
from evaluation.livesqlbench_base_lite_protected_preflight import run_preflight

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED_PATH = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
FINAL_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
PUBLIC_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_preflight_manifest.json"
M24_3_ARTIFACT = ROOT / "evaluation/fixtures/m24_3_livesqlbench_temporal_integrity_result.json"
SAFE_RESULT = ROOT / "evaluation/fixtures/m25_livesqlbench_direct_pilot_result.json"
PROTECTED_RESULTS = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m25_direct_pilot_cases.jsonl"
)
JOURNAL = ROOT / "evaluation/external/livesqlbench/protected/results/m25_direct_pilot_journal.jsonl"
EXPECTED_MODEL = "gpt-5.6-luna"
MAX_PROVIDER_CALLS = 18


class M25PreflightError(RuntimeError):
    """Raised before provider construction when a pilot invariant fails."""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def git_ignored(path: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "--quiet", str(path)]).returncode == 0


def tracked_protected_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "evaluation/external/livesqlbench/protected/"], text=True
    )
    return [line for line in output.splitlines() if line]


def generation_prompt_hash() -> str:
    from app.generation import provider

    return sha256_text(inspect.getsource(provider._generation_messages))


def external_knowledge_context(case: LiveSqlBenchEvaluationCase) -> str:
    return "OFFICIAL EXTERNAL KNOWLEDGE:\n" + json.dumps(
        list(case.runtime.external_knowledge), ensure_ascii=False, sort_keys=True
    )


def runtime_schema_text(schema_context: str, case: LiveSqlBenchEvaluationCase) -> str:
    return f"{schema_context}\n\n{external_knowledge_context(case)}"


def result_from_execution(execution: QueryExecution) -> LiveSqlBenchResult:
    return LiveSqlBenchResult(
        tuple(execution.columns),
        tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows),
    )


def sql_shape(sql_text: str | None) -> dict[str, Any]:
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


def _usage(capture: Any, proposal: SqlProposal | None) -> dict[str, int | None]:
    usage = capture.usage if capture is not None else {}
    return {
        "input_tokens": (
            usage.get("prompt_tokens")
            if usage.get("prompt_tokens") is not None
            else proposal.prompt_tokens
            if proposal is not None
            else None
        ),
        "output_tokens": (
            usage.get("completion_tokens")
            if usage.get("completion_tokens") is not None
            else proposal.completion_tokens
            if proposal is not None
            else None
        ),
        "reasoning_tokens": (
            usage.get("reasoning_tokens")
            if usage.get("reasoning_tokens") is not None
            else proposal.reasoning_tokens
            if proposal is not None
            else None
        ),
        "cached_tokens": (
            usage.get("cached_prompt_tokens")
            if usage.get("cached_prompt_tokens") is not None
            else proposal.cached_prompt_tokens
            if proposal is not None
            else None
        ),
    }


def _read_journal(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            case_id = record.get("case_id")
            if not isinstance(case_id, str) or case_id in records:
                raise M25PreflightError(f"invalid or duplicate journal row {line_number}")
            records[case_id] = record
    return records


def _append_journal(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        stream.flush()


class JournalProvider:
    """Delegates the existing provider and journals its one SQL request."""

    def __init__(self, inner: OpenAICompatibleProvider, journal: Path) -> None:
        self.inner = inner
        self.settings = inner.settings
        self.journal = journal
        self.case_id: str | None = None
        self.expected_context_hash: str | None = None

    async def propose_sql(self, *args: Any, **kwargs: Any) -> SqlProposal:
        if self.case_id is None:
            raise RuntimeError("journal provider case_id was not set")
        schema_context = args[2] if len(args) > 2 else kwargs.get("schema_context")
        if (
            self.expected_context_hash is not None
            and isinstance(schema_context, str)
            and sha256_text(schema_context) != self.expected_context_hash
        ):
            raise M25PreflightError("runtime context changed after preflight")
        started = time.perf_counter()
        try:
            proposal = await self.inner.propose_sql(*args, **kwargs)
        except Exception as error:
            capture = self.inner.consume_model_io()
            response_hash = capture.raw_assistant_content_sha256 if capture is not None else None
            _append_journal(
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
                    "response_hash": response_hash,
                    "error_type": type(error).__name__,
                    "wall_latency_ms": (time.perf_counter() - started) * 1000,
                    "capture": capture.model_dump(mode="json") if capture is not None else None,
                },
            )
            raise
        capture = self.inner.consume_model_io()
        _append_journal(
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


def direct_evaluation_service(
    resolver: SchemaContextResolver,
    provider: Any,
    safety: Any,
    settings: Settings,
) -> TextToSqlService:
    """Create the existing direct runner without constructing the unused M3 route.

    LiveSQLBench catalogs are intentionally outside Decision-SQL's fixed governed
    metric catalog.  TextToSqlService._run(..., result_shape_contract=False) is
    the unchanged one-shot direct implementation; its constructor also builds an
    unrelated governed route, which cannot validate an arbitrary external schema.
    This evaluation-only object initializes precisely the direct-run fields and
    never exposes the governed path.
    """
    service = object.__new__(TextToSqlService)
    service.context_resolver = resolver
    service.provider = provider
    service.safety_service = safety
    service.context_mode = SchemaContextMode.FULL_COMPACT
    service.generation_mode = GenerationMode.ONE_SHOT
    service.strategy = GenerationStrategy.M2_ONE_SHOT
    service.tracer = get_tracer()
    service.schema_serializer = serialize_schema_context
    service.provenance_sink = NoOpProvenanceSink()
    service.settings = settings
    return service


def _preflight(
    public_root: Path, protected_path: Path, config: PostgresConnectionConfig
) -> tuple[
    list[LiveSqlBenchEvaluationCase],
    dict[str, Any],
    dict[str, tuple[LiveSqlBenchDatabase, Any, Any]],
    dict[str, LiveSqlBenchResult],
]:
    if not git_ignored(protected_path) or tracked_protected_files():
        raise M25PreflightError("protected GT is not ignored/untracked")
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
    merged, merge = merge_public_and_protected(public, protected)
    final_manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    public_manifest = json.loads(PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    pilot_ids = final_manifest["pilot_case_ids"]
    previous_ids = public_manifest["future_baseline"]["pilot_case_ids"]
    if pilot_ids != previous_ids or len(pilot_ids) != 18 or len(set(pilot_ids)) != 18:
        raise M25PreflightError("frozen pilot IDs changed or are not unique")
    merged_by_id = {case.instance_id: case for case in merged}
    if any(case_id not in merged_by_id for case_id in pilot_ids):
        raise M25PreflightError("frozen pilot case is missing from protected merge")
    if (
        merge["public_rows"] != 270
        or merge["protected_rows"] != 270
        or merge["exact_matches"] != 270
        or merge["unmatched_public"]
        or merge["unmatched_protected"]
        or protected.sha256 != final_manifest["protected_source"]["sha256"]
    ):
        raise M25PreflightError("public/protected merge does not match frozen manifest")
    if not M24_3_ARTIFACT.exists() or json.loads(M24_3_ARTIFACT.read_text())["classification"] != (
        "M24_3_MIXED_TEMPORAL_AND_EVALUATOR_LIMITATION"
    ):
        raise M25PreflightError("M24.3 temporal-integrity artifact is not validated")

    # This is the complete provider-free global gate, including the 180-case
    # reference replay. It does not construct a provider.
    with tempfile.TemporaryDirectory(prefix="m25-preflight-") as directory:
        global_preflight = run_preflight(public_root, protected_path, Path(directory), config)
    if global_preflight["provider_calls"] != 0:
        raise M25PreflightError("provider calls occurred during preflight")
    if global_preflight["m1_compatibility"]["accepted"] != 175:
        raise M25PreflightError("unexpected global M1 compatibility before pilot")

    pilot_cases = [merged_by_id[case_id] for case_id in pilot_ids]
    states: dict[str, tuple[LiveSqlBenchDatabase, Any, Any]] = {}
    expected: dict[str, LiveSqlBenchResult] = {}
    contexts: dict[str, Any] = {}
    for case in pilot_cases:
        if case.database not in states:
            catalog = introspect_database(case.database, config)
            engine, safety, _ = safety_for_database(catalog, config)
            states[case.database] = (catalog, engine, safety)
        catalog, _, safety = states[case.database]
        resolver = SchemaContextResolver(safety.catalog)
        context = resolver.resolve(case.runtime.question, SchemaContextMode.FULL_COMPACT)
        contexts[case.instance_id] = serialize_schema_context(context)
        plan = safety.plan(SqlCandidate(sql=case.sol_sql[0], source=CandidateSource.INTERNAL))
        if not isinstance(plan, QueryPlan):
            raise M25PreflightError(f"pilot gold is not M1-compatible: {case.instance_id}")
        execution = safety.execute(plan)
        if not isinstance(execution, QueryExecution):
            raise M25PreflightError(f"pilot gold did not execute: {case.instance_id}")
        expected[case.instance_id] = result_from_execution(execution)
    if len(expected) != 18:
        raise M25PreflightError("pilot reference execution did not cover 18 cases")
    pilot_reference_pass = sum(
        soft_ex_match(
            expected[case.instance_id],
            expected[case.instance_id],
            ordered=bool(case.public.conditions.get("order", False)),
        )
        for case in pilot_cases
    )
    empty_reference = sum(not expected[case.instance_id].rows for case in pilot_cases)
    temporal_empty = sum(
        not expected[case.instance_id].rows and has_current_date(case.sol_sql[0])
        for case in pilot_cases
    )
    if (pilot_reference_pass, empty_reference, temporal_empty) != (17, 1, 1):
        raise M25PreflightError("pilot reference integrity changed unexpectedly")
    safe_preflight = global_preflight["safe_cases"]
    by_id = {row["case_id"]: row for row in safe_preflight}
    if any(by_id[case_id]["m1_status"] != "ACCEPTED" for case_id in pilot_ids):
        raise M25PreflightError("pilot gold is not M1-compatible according to global gate")
    if any(by_id[case_id]["execution_status"] != "SUCCESS" for case_id in pilot_ids):
        raise M25PreflightError("pilot gold execution is not successful according to global gate")
    settings = get_settings()
    if not settings.llm_api_key:
        raise M25PreflightError("provider credential is not configured")
    settings = settings.model_copy(
        update={"llm_model": EXPECTED_MODEL, "eval_capture_model_io": True}
    )
    return (
        pilot_cases,
        {
            "global": global_preflight,
            "pilot_reference_pass": pilot_reference_pass,
            "pilot_empty": empty_reference,
            "pilot_temporal_empty": temporal_empty,
            "final_manifest": final_manifest,
            "prompt_hash": generation_prompt_hash(),
            "provider_source_hash": sha256_file(ROOT / "app/generation/provider.py"),
            "service_source_hash": sha256_file(ROOT / "app/text_to_sql/service.py"),
            "m1_policy_hash": sha256_file(ROOT / "app/sql/policy.py"),
            "settings": settings,
            "contexts": contexts,
        },
        states,
        expected,
    )


def has_current_date(sql_text: str) -> bool:
    tree = sqlglot.parse_one(sql_text, read="postgres")
    return any(isinstance(node, sqlglot.exp.CurrentDate) for node in tree.walk())


def _process_existing_sql(
    sql_text: str, safety: Any, expected: LiveSqlBenchResult, ordered: bool
) -> dict[str, Any]:
    plan = safety.plan(SqlCandidate(sql=sql_text, source=CandidateSource.LLM))
    if not isinstance(plan, QueryPlan):
        code = plan.rejection.code.value if plan.rejection is not None else plan.status.value
        return {
            "protocol_success": True,
            "sql_produced": True,
            "m1_status": "REJECTED",
            "m1_failure_code": code,
            "execution_status": "NOT_ATTEMPTED_M1",
            "official_status": "NOT_EVALUATED",
            "correct": False,
            "execution": None,
        }
    execution = safety.execute(plan)
    if not isinstance(execution, QueryExecution):
        return {
            "protocol_success": True,
            "sql_produced": True,
            "m1_status": "ACCEPTED",
            "m1_failure_code": None,
            "execution_status": "FAILURE",
            "official_status": "NOT_EVALUATED",
            "correct": False,
            "execution": None,
        }
    correct = soft_ex_match(result_from_execution(execution), expected, ordered=ordered)
    return {
        "protocol_success": True,
        "sql_produced": True,
        "m1_status": "ACCEPTED",
        "m1_failure_code": None,
        "execution_status": "SUCCESS",
        "official_status": "PASS" if correct else "FAIL",
        "correct": correct,
        "execution": execution,
    }


async def _fresh_case(
    case: LiveSqlBenchEvaluationCase,
    service: TextToSqlService,
    journal_provider: JournalProvider,
    context_hash: str,
    expected: LiveSqlBenchResult,
) -> tuple[dict[str, Any], TextToSqlResult, dict[str, Any]]:
    journal_provider.case_id = case.instance_id
    journal_provider.expected_context_hash = context_hash
    started = time.perf_counter()
    result = await service.run_with_context_addition(
        TextToSqlRequest(
            question=case.runtime.question,
            correlation_id=f"m25:{case.instance_id}",
            execute=True,
        ),
        external_knowledge_context(case),
    )
    journal = _read_journal(JOURNAL)[case.instance_id]
    proposal = result.proposal
    if proposal is None:
        processed = {
            "protocol_success": False,
            "sql_produced": False,
            "m1_status": "NOT_REACHED",
            "m1_failure_code": None,
            "execution_status": "NOT_ATTEMPTED",
            "official_status": "NOT_EVALUATED",
            "correct": False,
            "execution": None,
        }
    else:
        if isinstance(result.plan, QueryPlan) and isinstance(result.execution, QueryExecution):
            correct = soft_ex_match(
                result_from_execution(result.execution),
                expected,
                ordered=bool(case.public.conditions.get("order", False)),
            )
            processed = {
                "protocol_success": True,
                "sql_produced": True,
                "m1_status": "ACCEPTED",
                "m1_failure_code": None,
                "execution_status": "SUCCESS",
                "official_status": "PASS" if correct else "FAIL",
                "correct": correct,
                "execution": result.execution,
            }
        elif result.plan_failure is not None:
            processed = {
                "protocol_success": True,
                "sql_produced": True,
                "m1_status": "REJECTED",
                "m1_failure_code": (
                    result.plan_failure.rejection.code.value
                    if result.plan_failure.rejection is not None
                    else result.plan_failure.status.value
                ),
                "execution_status": "NOT_ATTEMPTED_M1",
                "official_status": "NOT_EVALUATED",
                "correct": False,
                "execution": None,
            }
        else:
            processed = {
                "protocol_success": True,
                "sql_produced": True,
                "m1_status": "ACCEPTED" if result.plan is not None else "ERROR",
                "m1_failure_code": None,
                "execution_status": "FAILURE",
                "official_status": "NOT_EVALUATED",
                "correct": False,
                "execution": None,
            }
    return (
        {
            "case_id": case.instance_id,
            "database": case.database,
            "provider_status": journal["provider_status"],
            "provider_response_received": journal["response_received"],
            "provider_calls": journal["actual_request_count"],
            "protocol_status": "SUCCESS" if proposal is not None else "FAILURE",
            "sql_hash": sha256_text(proposal.sql) if proposal else None,
            "m1_status": processed["m1_status"],
            "m1_failure_code": processed["m1_failure_code"],
            "execution_status": processed["execution_status"],
            "official_evaluator_status": processed["official_status"],
            "correct": processed["correct"],
            "row_count": (
                processed["execution"].row_count
                if isinstance(processed["execution"], QueryExecution)
                else None
            ),
            "reference_empty": not expected.rows,
            "reference_annotation": (
                "TEMPORAL_REFERENCE_DRIFT"
                if not expected.rows and has_current_date(case.sol_sql[0])
                else None
            ),
            "sql_shape": sql_shape(proposal.sql) if proposal else {},
            "provider_latency_ms": journal.get("usage", {}).get("latency_ms")
            or journal.get("wall_latency_ms"),
            "end_to_end_latency_ms": (time.perf_counter() - started) * 1000,
            "usage": journal.get("usage", {}),
            "runtime_context_hash": context_hash,
        },
        result,
        journal,
    )


async def run_pilot(
    pilot_cases: list[LiveSqlBenchEvaluationCase],
    contexts: dict[str, str],
    states: dict[str, tuple[LiveSqlBenchDatabase, Any, Any]],
    expected: dict[str, LiveSqlBenchResult],
    settings: Settings,
    *,
    resume: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    journal = _read_journal(JOURNAL)
    if journal and not resume:
        raise M25PreflightError("journal already contains attempts; use --resume")
    if any(record.get("actual_request_count", 0) != 1 for record in journal.values()):
        raise M25PreflightError("journal contains a non-single provider attempt")
    provider = JournalProvider(OpenAICompatibleProvider(settings), JOURNAL)
    services: dict[str, TextToSqlService] = {}
    for database, (_, _, safety) in states.items():
        resolver = SchemaContextResolver(safety.catalog)
        services[database] = direct_evaluation_service(
            resolver,
            provider,
            safety,
            settings=settings,
        )
    records: list[dict[str, Any]] = []
    for case in pilot_cases:
        if case.instance_id in journal:
            journal_row = journal[case.instance_id]
            if journal_row.get("provider_status") != "PROVIDER_SUCCESS" or not journal_row.get(
                "sql"
            ):
                record = {
                    "case_id": case.instance_id,
                    "database": case.database,
                    "provider_status": journal_row.get("provider_status"),
                    "provider_response_received": journal_row.get("response_received", False),
                    "provider_calls": journal_row.get("actual_request_count", 0),
                    "protocol_status": "FAILURE",
                    "sql_hash": journal_row.get("sql_hash"),
                    "m1_status": "NOT_REACHED",
                    "m1_failure_code": None,
                    "execution_status": "NOT_ATTEMPTED",
                    "official_evaluator_status": "NOT_EVALUATED",
                    "correct": False,
                    "row_count": None,
                    "reference_empty": not expected[case.instance_id].rows,
                    "reference_annotation": None,
                    "sql_shape": {},
                    "provider_latency_ms": journal_row.get("wall_latency_ms"),
                    "end_to_end_latency_ms": None,
                    "usage": journal_row.get("usage", {}),
                    "runtime_context_hash": sha256_text(
                        runtime_schema_text(contexts[case.instance_id], case)
                    ),
                }
            else:
                processed = _process_existing_sql(
                    journal_row["sql"],
                    states[case.database][2],
                    expected[case.instance_id],
                    bool(case.public.conditions.get("order", False)),
                )
                record = {
                    "case_id": case.instance_id,
                    "database": case.database,
                    "provider_status": journal_row["provider_status"],
                    "provider_response_received": True,
                    "provider_calls": journal_row["actual_request_count"],
                    "protocol_status": "SUCCESS",
                    "sql_hash": journal_row.get("sql_hash"),
                    "m1_status": processed["m1_status"],
                    "m1_failure_code": processed["m1_failure_code"],
                    "execution_status": processed["execution_status"],
                    "official_evaluator_status": processed["official_status"],
                    "correct": processed["correct"],
                    "row_count": (
                        processed["execution"].row_count
                        if isinstance(processed["execution"], QueryExecution)
                        else None
                    ),
                    "reference_empty": not expected[case.instance_id].rows,
                    "reference_annotation": (
                        "TEMPORAL_REFERENCE_DRIFT"
                        if not expected[case.instance_id].rows and has_current_date(case.sol_sql[0])
                        else None
                    ),
                    "sql_shape": sql_shape(journal_row["sql"]),
                    "provider_latency_ms": journal_row.get("wall_latency_ms"),
                    "end_to_end_latency_ms": None,
                    "usage": journal_row.get("usage", {}),
                    "runtime_context_hash": sha256_text(
                        runtime_schema_text(contexts[case.instance_id], case)
                    ),
                }
            records.append(record)
            continue
        if (
            sum(int(item.get("actual_request_count", 0)) for item in journal.values())
            >= MAX_PROVIDER_CALLS
        ):
            raise M25PreflightError("provider call budget exhausted")
        safe, result, journal_row = await _fresh_case(
            case,
            services[case.database],
            provider,
            sha256_text(runtime_schema_text(contexts[case.instance_id], case)),
            expected[case.instance_id],
        )
        records.append(safe)
        detail = {
            "case_id": case.instance_id,
            "database": case.database,
            "question": case.runtime.question,
            "external_knowledge": list(case.runtime.external_knowledge),
            "generated_sql": result.proposal.sql if result.proposal else None,
            "generated_result_summary": (
                {
                    "columns": result.execution.columns,
                    "row_count": result.execution.row_count,
                    "first_row": list(result.execution.rows[0].values())
                    if result.execution and result.execution.rows
                    else None,
                }
                if result.execution
                else None
            ),
            "reference_sql": list(case.sol_sql),
            "reference_result_summary": {
                "row_count": len(expected[case.instance_id].rows),
                "columns": list(expected[case.instance_id].columns),
            },
            "m1_status": safe["m1_status"],
            "execution_status": safe["execution_status"],
            "official_evaluator_status": safe["official_evaluator_status"],
            "journal": journal_row,
        }
        PROTECTED_RESULTS.parent.mkdir(parents=True, exist_ok=True)
        with PROTECTED_RESULTS.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(detail, ensure_ascii=False, default=str) + "\n")
    return records, {"provider": provider}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _aggregate(
    records: list[dict[str, Any]], preflight: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    total = len(records)
    correct = sum(record["correct"] for record in records)
    stages: Counter[str] = Counter()
    for record in records:
        if record["provider_status"] == "PROVIDER_FAILURE":
            stages["PROVIDER_FAILURE"] += 1
        elif record["provider_status"] == "PROTOCOL_FAILURE":
            stages["PROTOCOL_FAILURE"] += 1
        elif record["protocol_status"] != "SUCCESS":
            stages["PROTOCOL_FAILURE"] += 1
        elif record["m1_status"] != "ACCEPTED":
            stages["M1_REJECTION"] += 1
        elif record["execution_status"] != "SUCCESS":
            stages["EXECUTION_FAILURE"] += 1
        elif record["reference_annotation"] == "TEMPORAL_REFERENCE_DRIFT":
            stages["KNOWN_REFERENCE_INTEGRITY_LIMITATION"] += 1
        elif record["official_evaluator_status"] == "FAIL":
            stages["OFFICIAL_RESULT_MISMATCH"] += 1
    shape_counts: Counter[str] = Counter()
    for record in records:
        shape = record["sql_shape"]
        if shape.get("parseable"):
            shape_counts["joins"] += int(shape["joins"] > 0)
            shape_counts[f"{shape['join_bucket']}_join"] += 1
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
                shape_counts[key] += int(bool(shape.get(key)))
    provider_latencies = [
        float(r["provider_latency_ms"]) for r in records if r["provider_latency_ms"] is not None
    ]
    e2e_latencies = [
        float(r["end_to_end_latency_ms"]) for r in records if r["end_to_end_latency_ms"] is not None
    ]
    usage_fields = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
    usage: dict[str, Any] = {}
    for field in usage_fields:
        values = [int(r["usage"].get(field)) for r in records if r["usage"].get(field) is not None]
        usage[field] = {
            "total": sum(values) if values else None,
            "median": _percentile([float(v) for v in values], 0.5),
            "p95": _percentile([float(v) for v in values], 0.95),
            "available_cases": len(values),
        }
    max_calls = max((int(r["provider_calls"]) for r in records), default=0)
    failure_codes = Counter(
        r["m1_failure_code"] for r in records if r["m1_failure_code"] is not None
    )
    reference_valid = [r for r in records if not r["reference_empty"]]
    return {
        "configuration": {
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "reasoning_effort": settings.llm_reasoning_effort,
            "prompt_hash": preflight["prompt_hash"],
            "runtime_context_hash": sha256_json(
                {r["case_id"]: r["runtime_context_hash"] for r in records}
            ),
            "provider_source_hash": preflight["provider_source_hash"],
            "service_source_hash": preflight["service_source_hash"],
            "m1_policy_hash": preflight["m1_policy_hash"],
        },
        "pipeline": {
            "total": total,
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
        "official": {"correct": correct, "total": total, "accuracy": correct / total},
        "reference_valid_diagnostic": {
            "correct": sum(r["correct"] for r in reference_valid),
            "total": len(reference_valid),
            "accuracy": sum(r["correct"] for r in reference_valid) / len(reference_valid)
            if reference_valid
            else None,
            "label": "DIAGNOSTIC ONLY — NOT THE OFFICIAL SCORE",
        },
        "temporal_integrity": {
            "known_drift_cases": preflight["pilot_temporal_empty"],
            "official_denominator_unchanged": True,
            "drift_annotation_cases": sum(
                r["reference_annotation"] == "TEMPORAL_REFERENCE_DRIFT" for r in records
            ),
        },
        "failure_taxonomy": dict(sorted(stages.items())),
        "m1_rejections": dict(sorted(failure_codes.items())),
        "sql_shapes": dict(sorted(shape_counts.items())),
        "latency": {
            "provider": {
                "median_ms": _percentile(provider_latencies, 0.5),
                "p95_ms": _percentile(provider_latencies, 0.95),
                "max_ms": max(provider_latencies) if provider_latencies else None,
            },
            "execution": {
                "available": False,
                "reason": (
                    "ReadOnlyExecutor QueryExecution.latency_ms is not populated "
                    "with timing telemetry."
                ),
            },
            "end_to_end": {
                "median_ms": _percentile(e2e_latencies, 0.5),
                "p95_ms": _percentile(e2e_latencies, 0.95),
                "max_ms": max(e2e_latencies) if e2e_latencies else None,
            },
        },
        "usage": usage,
        "call_accounting": {
            "provider_calls_attempted": sum(r["provider_calls"] for r in records),
            "provider_calls_succeeded": sum(
                r["provider_calls"] for r in records if r["provider_status"] == "PROVIDER_SUCCESS"
            ),
            "provider_calls_failed": sum(
                r["provider_calls"] for r in records if r["provider_status"] != "PROVIDER_SUCCESS"
            ),
            "calls_per_case": dict(
                sorted(Counter(str(r["provider_calls"]) for r in records).items())
            ),
            "max_calls_per_case": max_calls,
            "automatic_retries": 0,
            "repair_calls": 0,
            "judge_calls": 0,
            "planning_calls": 0,
            "result_shape_calls": 0,
            "other_llm_calls": 0,
        },
        "protected_data_safety": {
            "protected_gt_ignored": git_ignored(PROTECTED_PATH),
            "protected_gt_tracked": tracked_protected_files(),
            "gold_sql_committed": False,
            "question_gold_mapping_committed": False,
            "detailed_local_artifact": str(PROTECTED_RESULTS),
            "detailed_local_artifact_tracked": False,
        },
        "cases": [
            {
                key: value
                for key, value in record.items()
                if key not in {"usage", "runtime_context_hash"}
            }
            for record in records
        ],
    }


async def main_async(args: argparse.Namespace) -> int:
    config = PostgresConnectionConfig.from_environment()
    states: dict[str, tuple[LiveSqlBenchDatabase, Any, Any]] = {}
    try:
        pilot_cases, context_meta, states, expected = _preflight(
            args.public_root, args.protected, config
        )
        settings: Settings = context_meta["settings"]
        final_manifest_hash = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))[
            "manifest_hash"
        ]
        journal = _read_journal(JOURNAL)
        if len(journal) > MAX_PROVIDER_CALLS:
            raise M25PreflightError("journal already exceeds provider budget")
        print(
            json.dumps(
                {
                    "PRECHECK": {
                        "pilot_cases": 18,
                        "provider_calls_so_far": sum(
                            int(row.get("actual_request_count", 0)) for row in journal.values()
                        ),
                        "max_provider_calls": MAX_PROVIDER_CALLS,
                        "calls_per_case_max": 1,
                        "prompt_hash": context_meta["prompt_hash"],
                        "pilot_manifest_hash": final_manifest_hash,
                        "gold_leakage": False,
                        "provider_retry_count": 0,
                        "reference_executable": "18/18",
                        "known_temporal_reference_drift": 1,
                        "model": settings.llm_model,
                    }
                },
                indent=2,
            ),
            flush=True,
        )
        if args.preflight_only:
            print(json.dumps({"preflight": "PASS", "provider_calls": 0}, indent=2))
            return 0
        records, _ = await run_pilot(
            pilot_cases,
            context_meta["contexts"],
            states,
            expected,
            settings,
            resume=args.resume,
        )
        if sum(r["provider_calls"] for r in records) != 18:
            raise M25PreflightError("pilot did not account for exactly 18 provider attempts")
        if max(r["provider_calls"] for r in records) > 1:
            raise M25PreflightError("a pilot case received more than one provider call")
        result = {
            "classification": "M25_LIVESQLBENCH_DIRECT_PILOT_COMPLETED",
            "starting_head": args.starting_head,
            "final_head": git_head(),
            "provider_calls": 18,
            "fresh_generation": 18,
            "production_changes": 0,
            "experiment": {
                "pilot_cases": 18,
                "provider_calls": 18,
                "calls_per_case_max": 1,
                "fresh_generation": 18,
                "route": "DIRECT",
                "pilot_manifest_hash": final_manifest_hash,
                "order_hash": sha256_json([case.instance_id for case in pilot_cases]),
            },
            **_aggregate(records, context_meta, settings),
        }
        SAFE_RESULT.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n")
        print(
            json.dumps(
                {
                    "classification": result["classification"],
                    "provider_calls": result["provider_calls"],
                    "official": result["official"],
                    "reference_valid_diagnostic": result["reference_valid_diagnostic"],
                    "failure_taxonomy": result["failure_taxonomy"],
                },
                indent=2,
            )
        )
        return 0
    except M25PreflightError as error:
        print(
            json.dumps(
                {"classification": "M25_DIRECT_PILOT_PREFLIGHT_BLOCKED", "error": str(error)}
            )
        )
        return 2
    finally:
        # The pilot's SQL executor engines are owned by the per-database states.
        # They are disposed even if a provider or protocol failure occurs.
        for _, engine, _ in states.values():
            engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=PROTECTED_PATH)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--starting-head", default=git_head())
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
