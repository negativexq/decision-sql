"""M20 fail-closed full direct-SQL benchmark rebaseline.

This module deliberately keeps benchmark scoring in the existing Defog/BIRD
evaluators while routing generated SQL through the production M1 boundary.
It is an evaluation harness, not a new generation architecture.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine

from app.catalog.models import ColumnMetadata, SchemaCatalog, TableMetadata
from app.config import Settings, get_settings
from app.generation.provider import (
    LLMProviderError,
    ModelIOCapture,
    OpenAICompatibleProvider,
    SqlProposal,
)
from app.models.domain import QueryRequest
from app.provenance.canonical import text_hash
from app.sql.models import (
    CandidateSource,
    QueryExecution,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)
from app.sql.service import SqlSafetyService
from evaluation.external.bird.benchmark import (
    BirdBenchmarkExecutor,
    BirdQuestion,
    bird_execution_match,
)
from evaluation.external.bird.benchmark import (
    QueryResult as BirdResult,
)
from evaluation.external.bird.benchmark import (
    load_questions as load_bird_questions,
)
from evaluation.external.bird.benchmark import (
    sha256_file as bird_sha256_file,
)
from evaluation.external.bird.schema import BIRD_TABLES
from evaluation.external.bird.schema import schema_context as bird_schema_context
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
    canonical_compare,
    format_reference_instructions,
    schema_context,
)
from evaluation.external.defog.benchmark import (
    QueryResult as DefogResult,
)
from evaluation.external.defog.benchmark import (
    load_questions as load_defog_questions,
)
from evaluation.external.defog.benchmark import (
    sha256_file as defog_sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]
M20_MANIFEST = ROOT / "evaluation/fixtures/m20_prebenchmark_manifest.json"
M20_RESULT = ROOT / "evaluation/fixtures/m20_full_benchmark_result.json"
M20_CASES = ROOT / "evaluation/fixtures/m20_full_benchmark_cases.jsonl"
EXPECTED = {"bird": 500, "defog_classic": 210, "defog_advanced": 64}


class M20FailureStage(StrEnum):
    PREFLIGHT = "PREFLIGHT"
    PROVIDER_PROTOCOL = "PROVIDER_PROTOCOL"
    SQL_GENERATION = "SQL_GENERATION"
    M1 = "M1"
    EXECUTION = "EXECUTION"
    RESULT_EVALUATION = "RESULT_EVALUATION"


class M20PreflightFailure(RuntimeError):
    """Raised before provider construction when any global gate fails."""


def _sha_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()


def _working_tree_provenance() -> dict[str, Any]:
    status = subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--short"], text=True
    )
    tracked_diff = subprocess.check_output(
        ["git", "-C", str(ROOT), "diff", "--binary"], text=False
    )
    untracked = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard"],
        text=True,
    ).splitlines()
    digest = hashlib.sha256()
    digest.update(tracked_diff)
    for relative in untracked:
        if relative in {
            "evaluation/fixtures/m20_prebenchmark_manifest.json",
            "evaluation/fixtures/m20_full_benchmark_result.json",
            "evaluation/fixtures/m20_full_benchmark_cases.jsonl",
        }:
            continue
        path = ROOT / relative
        if path.is_file():
            digest.update(relative.encode())
            digest.update(path.read_bytes())
    return {
        "clean": not status.strip(),
        "status_sha256": hashlib.sha256(status.encode()).hexdigest(),
        "code_state_sha256": digest.hexdigest(),
        "untracked_files": untracked,
    }


def _settings() -> Settings:
    return get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
            "eval_capture_model_io": True,
        }
    )


def _provider_identity(settings: Settings) -> dict[str, Any]:
    return {
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "temperature": "provider_default",
        "reasoning_effort": settings.llm_reasoning_effort,
        "timeout_seconds": settings.llm_timeout_seconds,
    }


def _dataset_roots() -> tuple[Path, Path, Path]:
    sql_eval = Path(os.environ.get("DEFOG_SQL_EVAL_ROOT", "/external/sql-eval"))
    bird_root = Path(os.environ.get("BIRD_MINI_DEV_ROOT", "/bird"))
    output = M20_RESULT.parent
    if not sql_eval.is_dir():
        raise M20PreflightFailure(f"missing DEFOG_SQL_EVAL_ROOT: {sql_eval}")
    if not bird_root.is_dir():
        raise M20PreflightFailure(f"missing BIRD_MINI_DEV_ROOT: {bird_root}")
    output.mkdir(parents=True, exist_ok=True)
    if not os.access(output, os.W_OK):
        raise M20PreflightFailure(f"result destination is not writable: {output}")
    return sql_eval, bird_root, output


def _load_all(sql_eval: Path, bird_root: Path) -> dict[str, list[Any]]:
    datasets: dict[str, list[Any]] = {
        "defog_classic": load_defog_questions(
            sql_eval / "data/questions_gen_postgres.csv", "classic"
        ),
        "defog_advanced": load_defog_questions(
            sql_eval / "data/instruct_advanced_postgres.csv", "advanced"
        ),
        "bird": load_bird_questions(bird_root / "mini_dev_postgresql.json"),
    }
    for label, rows in datasets.items():
        expected = EXPECTED[label]
        if len(rows) != expected:
            raise M20PreflightFailure(
                f"{label}: expected {expected}, found {len(rows)}"
            )
        ids = [
            f"{row.dataset}:{row.index}"
            if isinstance(row, BenchmarkQuestion)
            else str(row.index)
            for row in rows
        ]
        if len(set(ids)) != expected:
            raise M20PreflightFailure(f"{label}: duplicate case IDs")
    return datasets


def _defog_gold_preflight(
    rows: list[BenchmarkQuestion], executor: DefogBenchmarkExecutor
) -> tuple[dict[str, list[tuple[str, DefogResult]]], dict[str, Any]]:
    cache: dict[str, list[tuple[str, DefogResult]]] = {}
    failures: list[dict[str, Any]] = []
    for question in rows:
        results: list[tuple[str, DefogResult]] = []
        try:
            from evaluation.external.defog.benchmark import canonical_gold_variants

            for variant in canonical_gold_variants(question.gold_sql):
                results.append((variant, executor.execute(question.db_name, variant)))
        except Exception as error:
            failures.append(
                {
                    "case_id": f"{question.dataset}:{question.index}",
                    "error_type": type(error).__name__,
                    "error": str(error)[:240],
                }
            )
        if results:
            cache[f"{question.dataset}:{question.index}"] = results
    return cache, {"cases": len(rows), "validated": len(cache), "failures": failures}


def _bird_gold_preflight(
    rows: list[BirdQuestion], executor: BirdBenchmarkExecutor
) -> tuple[dict[str, BirdResult], dict[str, Any]]:
    cache: dict[str, BirdResult] = {}
    failures: list[dict[str, Any]] = []
    for question in rows:
        try:
            executor._validate_select(question.gold_sql)
            cache[str(question.index)] = executor.execute(question.gold_sql)
        except Exception as error:
            failures.append(
                {
                    "case_id": str(question.index),
                    "error_type": type(error).__name__,
                    "error": str(error)[:240],
                }
            )
    return cache, {"cases": len(rows), "validated": len(cache), "failures": failures}


def _schema_contexts(
    datasets: dict[str, list[Any]],
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], dict[str, Any]]]:
    defog_executor = DefogBenchmarkExecutor()
    contexts: dict[tuple[str, str], str] = {}
    metadata: dict[tuple[str, str], dict[str, Any]] = {}
    for db_name in sorted(
        {
            q.db_name
            for label in ("defog_classic", "defog_advanced")
            for q in datasets[label]
        }
    ):
        text, meta = schema_context(defog_executor, db_name)
        contexts[("defog", db_name)] = text
        metadata[("defog", db_name)] = meta
    for db_id in sorted({q.db_id for q in datasets["bird"]}):
        text, meta = bird_schema_context(db_id)
        contexts[("bird", db_id)] = text
        metadata[("bird", db_id)] = meta
    return contexts, metadata


def _database_connection(kind: str, name: str) -> tuple[URL, str, int, str, str]:
    if kind == "bird":
        host = os.getenv("BIRD_DB_HOST", "127.0.0.1")
        port = int(os.getenv("BIRD_DB_PORT", "55433"))
        user = os.getenv("BIRD_DB_USER", "bird_reader")
        password = os.getenv("BIRD_DB_PASSWORD", "bird_reader_password")
        database = "bird"
    else:
        host = os.getenv("DEFOG_DB_HOST", "127.0.0.1")
        port = int(os.getenv("DEFOG_DB_PORT", "55432"))
        user = os.getenv("DEFOG_DB_USER", "defog_reader")
        password = os.getenv("DEFOG_DB_PASSWORD", "defog_reader_password")
        database = name
    return (
        URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=host,
            port=port,
            database=database,
        ),
        user,
        port,
        host,
        database,
    )


def _m1_catalog(kind: str, name: str) -> SchemaCatalog:
    import psycopg

    _, user, port, host, database = _database_connection(kind, name)
    with psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=(
            os.getenv("BIRD_DB_PASSWORD", "bird_reader_password")
            if kind == "bird"
            else os.getenv("DEFOG_DB_PASSWORD", "defog_reader_password")
        ),
        dbname=database,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
            rows = cursor.fetchall()
    allowed = (
        {value.lower() for value in BIRD_TABLES.get(name, ())}
        if kind == "bird"
        else None
    )
    tables: dict[str, list[ColumnMetadata]] = {}
    for table, column, data_type in rows:
        if allowed is not None and table.lower() not in allowed:
            continue
        tables.setdefault(str(table), []).append(
            ColumnMetadata(name=str(column), type=str(data_type), description="")
        )
    return SchemaCatalog(
        tables=tuple(
            TableMetadata(name=table, description="", columns=tuple(columns))
            for table, columns in sorted(tables.items())
        )
    )


def _settings_for_db(
    settings: Settings, kind: str, name: str
) -> tuple[Settings, Engine, SqlSafetyService]:
    url, user, _, _, _ = _database_connection(kind, name)
    db_settings = settings.model_copy(
        update={
            "database_url": url.render_as_string(hide_password=False),
            "reader_role": user,
        }
    )
    engine = create_engine(url, pool_size=1, max_overflow=0, pool_pre_ping=True)
    safety = SqlSafetyService(
        engine, settings=db_settings, catalog=_m1_catalog(kind, name)
    )
    return db_settings, engine, safety


def _prompt_hash(question: Any, kind: str, context: str) -> str:
    if kind == "bird":
        prompt = question.question + "\n\nBIRD expert evidence:\n" + question.evidence
    else:
        prompt = (
            question.question
            + "\n"
            + format_reference_instructions(question.instructions)
        )
    return text_hash(prompt + "\n\n" + context)


def _clean_sql(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("```sql"):
        text = text[6:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _execution_result(result: QueryExecution) -> Any:
    rows = tuple(
        tuple(row.get(column) for column in result.columns) for row in result.rows
    )
    return BirdResult(columns=tuple(result.columns), rows=rows)


def _record_capture(record: dict[str, Any], capture: ModelIOCapture | None) -> None:
    if capture is None:
        return
    record.update(
        {
            "input_tokens": capture.usage.get("prompt_tokens"),
            "output_tokens": capture.usage.get("completion_tokens"),
            "reasoning_tokens": capture.usage.get("reasoning_tokens"),
            "provider_response_id": capture.provider_response_id,
            "finish_reason": capture.finish_reason,
            "model": capture.response_model,
            "model_io_failure_stage": capture.failure_stage,
        }
    )


async def _generate(
    provider: OpenAICompatibleProvider,
    question: Any,
    kind: str,
    context: str,
) -> tuple[str | None, SqlProposal | None, ModelIOCapture | None, str | None]:
    try:
        if kind == "bird":
            content = await provider.propose_window_transport(
                question.question + "\n\nBIRD expert evidence:\n" + question.evidence,
                context,
                "You generate one valid PostgreSQL SELECT query. Return SQL only.",
                operation="BIRD_SQL",
            )
            sql = _clean_sql(content.content)
            return sql, None, provider.consume_model_io(), None
        proposal = await provider.propose_sql(
            QueryRequest(question=question.question),
            None,
            context + format_reference_instructions(question.instructions),
        )
        return _clean_sql(proposal.sql), proposal, provider.consume_model_io(), None
    except LLMProviderError as error:
        return None, None, provider.consume_model_io(), type(error).__name__
    except Exception as error:
        return None, None, provider.consume_model_io(), type(error).__name__


def _evaluate(kind: str, question: Any, generated: Any, gold: Any, sql: str) -> bool:
    if kind == "bird":
        return bird_execution_match(generated, gold)
    for gold_sql, gold_result in gold:
        exact, _ = canonical_compare(gold_result, generated, question, gold_sql, sql)
        if exact:
            return True
    return False


def _manifest(
    datasets: dict[str, list[Any]],
    metadata: dict[tuple[str, str], dict[str, Any]],
    settings: Settings,
    sql_eval: Path,
    bird_root: Path,
) -> dict[str, Any]:
    return {
        "milestone": "M20",
        "commit_sha": _git_head(),
        "repository": _working_tree_provenance(),
        "benchmark": {
            "bird_dataset_sha256": bird_sha256_file(
                bird_root / "mini_dev_postgresql.json"
            ),
            "defog_classic_sha256": defog_sha256_file(
                sql_eval / "data/questions_gen_postgres.csv"
            ),
            "defog_advanced_sha256": defog_sha256_file(
                sql_eval / "data/instruct_advanced_postgres.csv"
            ),
            "counts": {key: len(value) for key, value in datasets.items()},
        },
        "provider": _provider_identity(settings),
        "generation_mode": "DIRECT",
        "memory_enabled": False,
        "retrieval_enabled": False,
        "governed_semantics_enabled": False,
        "queryplan_enabled": False,
        "window_ir_enabled": False,
        "repair_calls": 0,
        "judge_calls": 0,
        "best_of_n": False,
        "retry_policy": "no semantic retry; existing provider adapter only",
        "m1": {
            "max_plan_rows": settings.max_plan_rows,
            "max_plan_cost": settings.max_plan_cost,
            "max_result_rows": settings.max_result_rows,
            "statement_timeout_ms": settings.statement_timeout_ms,
        },
        "schema_context_hashes": {
            f"{kind}:{name}": value.get("schema_context_sha256")
            for (kind, name), value in sorted(metadata.items())
        },
        "evaluator": {
            "defog": "pinned sql-eval compare_df/subset_df",
            "bird": "existing bird_execution_match",
        },
    }


def _global_preflight() -> tuple[
    dict[str, list[Any]],
    dict[str, Any],
    dict[tuple[str, str], str],
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str], tuple[Engine, SqlSafetyService]],
    dict[str, Any],
]:
    sql_eval, bird_root, _ = _dataset_roots()
    try:
        datasets = _load_all(sql_eval, bird_root)
        contexts, metadata = _schema_contexts(datasets)
        defog_executor = DefogBenchmarkExecutor()
        bird_executor = BirdBenchmarkExecutor()
        _, classic_validation = _defog_gold_preflight(
            datasets["defog_classic"], defog_executor
        )
        _, advanced_validation = _defog_gold_preflight(
            datasets["defog_advanced"], defog_executor
        )
        _, bird_validation = _bird_gold_preflight(datasets["bird"], bird_executor)
    except M20PreflightFailure:
        raise
    except Exception as error:
        raise M20PreflightFailure(
            f"benchmark/database/evaluator preflight error: {type(error).__name__}: {error}"
        ) from error
    validations = {
        "defog_classic": classic_validation,
        "defog_advanced": advanced_validation,
        "bird": bird_validation,
    }
    if any(
        value["validated"] != value["cases"] or value["failures"]
        for value in validations.values()
    ):
        raise M20PreflightFailure(
            "gold/database execution preflight failed: "
            + json.dumps(validations, default=str)
        )
    settings = _settings()
    if not settings.llm_api_key:
        raise M20PreflightFailure("provider credential is not configured")
    if (
        settings.max_plan_rows <= 0
        or settings.max_plan_cost <= 0
        or settings.max_result_rows <= 0
    ):
        raise M20PreflightFailure("M1 bounds must be positive")
    safety_cache: dict[tuple[str, str], tuple[Engine, SqlSafetyService]] = {}
    for key in sorted(contexts):
        kind, name = key
        try:
            _, engine, safety = _settings_for_db(settings, kind, name)
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        except Exception as error:
            for existing_engine, _ in safety_cache.values():
                existing_engine.dispose()
            raise M20PreflightFailure(
                f"M1 database preflight error for {kind}:{name}: {type(error).__name__}: {error}"
            ) from error
        safety_cache[key] = (engine, safety)
    manifest = _manifest(datasets, metadata, settings, sql_eval, bird_root)
    manifest["manifest_hash"] = _sha_json(manifest)
    M20_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return datasets, validations, contexts, metadata, safety_cache, manifest


def _new_record(
    question: Any, label: str, kind: str, context: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    db_name = question.db_id if kind == "bird" else question.db_name
    return {
        "case_id": str(question.index)
        if kind == "bird"
        else f"{question.dataset}:{question.index}",
        "benchmark": label,
        "database": db_name,
        "status": "INCORRECT",
        "correct": False,
        "generated_sql": None,
        "failure_stage": None,
        "failure_code": None,
        "m1_status": None,
        "execution_status": None,
        "latency_ms": {
            "generation": None,
            "m1": None,
            "execution": None,
            "total": None,
        },
        "provider_calls": 0,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "model": manifest["provider"]["model"],
        "prompt_hash": _prompt_hash(question, kind, context),
        "schema_context_hash": text_hash(context),
        "commit_sha": manifest["commit_sha"],
    }


async def _run_case(
    provider: OpenAICompatibleProvider,
    safety: SqlSafetyService,
    question: Any,
    label: str,
    kind: str,
    context: str,
    gold: Any,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    record = _new_record(question, label, kind, context, manifest)
    started = time.perf_counter()
    generation_started = time.perf_counter()
    sql, proposal, capture, provider_error = await _generate(
        provider, question, kind, context
    )
    record["provider_calls"] = 1
    record["latency_ms"]["generation"] = (
        time.perf_counter() - generation_started
    ) * 1000
    _record_capture(record, capture)
    if proposal is not None:
        record["model"] = proposal.model
    if provider_error or sql is None:
        record.update(
            {
                "failure_stage": M20FailureStage.PROVIDER_PROTOCOL.value,
                "failure_code": provider_error or "EMPTY_PROVIDER_RESPONSE",
            }
        )
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["generated_sql"] = sql
    m1_started = time.perf_counter()
    try:
        planned = safety.plan(
            SqlCandidate(
                sql=sql, source=CandidateSource.LLM, correlation_id=record["case_id"]
            )
        )
    except Exception as error:
        record.update(
            {
                "failure_stage": M20FailureStage.M1.value,
                "failure_code": type(error).__name__,
            }
        )
        record["latency_ms"]["m1"] = (time.perf_counter() - m1_started) * 1000
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["latency_ms"]["m1"] = (time.perf_counter() - m1_started) * 1000
    if isinstance(planned, SqlPlanFailure):
        m1_status = (
            "REJECTED"
            if planned.status.value
            in {"SQL_PARSE_ERROR", "POLICY_REJECTION", "QUERY_COST_REJECTION"}
            else "ERROR"
        )
        record.update(
            {
                "m1_status": m1_status,
                "failure_stage": M20FailureStage.M1.value,
                "failure_code": planned.status.value,
            }
        )
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["m1_status"] = "ALLOWED"
    execution_started = time.perf_counter()
    try:
        execution = safety.execute(planned)
    except Exception as error:
        record.update(
            {
                "failure_stage": M20FailureStage.EXECUTION.value,
                "failure_code": type(error).__name__,
            }
        )
        record["latency_ms"]["execution"] = (
            time.perf_counter() - execution_started
        ) * 1000
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["latency_ms"]["execution"] = (time.perf_counter() - execution_started) * 1000
    if isinstance(execution, SqlExecutionError):
        record.update(
            {
                "failure_stage": M20FailureStage.EXECUTION.value,
                "failure_code": "EXECUTION_FAILURE",
            }
        )
        record["execution_status"] = "FAILURE"
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["execution_status"] = "SUCCESS"
    generated = _execution_result(execution)
    try:
        correct = _evaluate(kind, question, generated, gold, sql)
    except Exception as error:
        record.update(
            {
                "failure_stage": M20FailureStage.RESULT_EVALUATION.value,
                "failure_code": type(error).__name__,
            }
        )
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["correct"] = correct
    record["status"] = "CORRECT" if correct else "INCORRECT"
    if not correct:
        record.update(
            {
                "failure_stage": M20FailureStage.RESULT_EVALUATION.value,
                "failure_code": "RESULT_MISMATCH",
            }
        )
    record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
    return record


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    latency = [
        r["latency_ms"]["total"]
        for r in records
        if r["latency_ms"]["total"] is not None
    ]
    ordered = sorted(float(value) for value in latency)

    def percentile(fraction: float) -> float | None:
        if not ordered:
            return None
        return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]

    stage_counts = Counter(r["failure_stage"] for r in records if r["failure_stage"])
    return {
        "correct": sum(bool(r["correct"]) for r in records),
        "total": len(records),
        "provider_calls": sum(int(r["provider_calls"]) for r in records),
        "m1_accepted": sum(r["m1_status"] == "ALLOWED" for r in records),
        "m1_rejected": sum(r["m1_status"] == "REJECTED" for r in records),
        "m1_errors": sum(r["m1_status"] == "ERROR" for r in records),
        "execution_succeeded": sum(r["execution_status"] == "SUCCESS" for r in records),
        "execution_failed": sum(r["execution_status"] == "FAILURE" for r in records),
        "failure_stages": dict(stage_counts),
        "latency_ms": {"p50": percentile(0.50), "p95": percentile(0.95)},
        "stage_latency_ms": {
            stage: {
                "p50": percentile_for_stage(records, stage, 0.50),
                "p95": percentile_for_stage(records, stage, 0.95),
            }
            for stage in ("generation", "m1", "execution")
        },
        "input_tokens": sum(
            r["input_tokens"] for r in records if r["input_tokens"] is not None
        )
        or None,
        "output_tokens": sum(
            r["output_tokens"] for r in records if r["output_tokens"] is not None
        )
        or None,
    }


def percentile_for_stage(
    records: list[dict[str, Any]], stage: str, fraction: float
) -> float | None:
    values = sorted(
        float(record["latency_ms"][stage])
        for record in records
        if record["latency_ms"].get(stage) is not None
    )
    if not values:
        return None
    return values[min(len(values) - 1, round((len(values) - 1) * fraction))]


async def _run_all(
    datasets: dict[str, list[Any]],
    contexts: dict[tuple[str, str], str],
    validations: dict[str, Any],
    safety_cache: dict[tuple[str, str], tuple[Engine, SqlSafetyService]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    settings = _settings()
    provider = OpenAICompatibleProvider(settings)
    gold: dict[str, Any] = {}
    defog_executor = DefogBenchmarkExecutor()
    bird_executor = BirdBenchmarkExecutor()
    gold["defog_classic"], _ = _defog_gold_preflight(
        datasets["defog_classic"], defog_executor
    )
    gold["defog_advanced"], _ = _defog_gold_preflight(
        datasets["defog_advanced"], defog_executor
    )
    gold["bird"], _ = _bird_gold_preflight(datasets["bird"], bird_executor)
    records: list[dict[str, Any]] = []
    for label in ("bird", "defog_classic", "defog_advanced"):
        kind = "bird" if label == "bird" else "defog"
        for position, question in enumerate(datasets[label], start=1):
            name = question.db_id if kind == "bird" else question.db_name
            key = (kind, name)
            context = contexts[key]
            record = await _run_case(
                provider,
                safety_cache[key][1],
                question,
                label,
                kind,
                context,
                gold[label][str(question.index)]
                if kind == "bird"
                else gold[label][f"{question.dataset}:{question.index}"],
                manifest,
            )
            records.append(record)
            if position % 10 == 0 or position == len(datasets[label]):
                print(f"{label}: {position}/{len(datasets[label])}", flush=True)
    for engine, _ in safety_cache.values():
        engine.dispose()
    aggregate = {
        "classification": "M20_FULL_REBASELINE_COMPLETED",
        "commit_sha": manifest["commit_sha"],
        "manifest_hash": manifest["manifest_hash"],
        "model": _provider_identity(settings),
        "benchmarks": {
            label: _summary([r for r in records if r["benchmark"] == label])
            for label in ("bird", "defog_classic", "defog_advanced")
        },
        "provider_calls": sum(r["provider_calls"] for r in records),
        "provider_failures": sum(
            r["failure_stage"] == M20FailureStage.PROVIDER_PROTOCOL.value
            for r in records
        ),
        "failure_stages": dict(
            Counter(r["failure_stage"] for r in records if r["failure_stage"])
        ),
        "repair_calls": 0,
        "judge_calls": 0,
        "retrieval_calls": 0,
        "memory_calls": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    M20_CASES.write_text(
        "".join(json.dumps(record, default=str) + "\n" for record in records)
    )
    M20_RESULT.write_text(json.dumps(aggregate, indent=2, default=str) + "\n")
    return aggregate


async def _run_smoke(
    datasets: dict[str, list[Any]],
    contexts: dict[tuple[str, str], str],
    safety_cache: dict[tuple[str, str], tuple[Engine, SqlSafetyService]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Run ten fresh cases per benchmark without creating authoritative output."""
    settings = _settings()
    provider = OpenAICompatibleProvider(settings)
    defog_executor = DefogBenchmarkExecutor()
    bird_executor = BirdBenchmarkExecutor()
    gold: dict[str, Any] = {}
    gold["defog_classic"], _ = _defog_gold_preflight(
        datasets["defog_classic"][:10], defog_executor
    )
    gold["defog_advanced"], _ = _defog_gold_preflight(
        datasets["defog_advanced"][:10], defog_executor
    )
    gold["bird"], _ = _bird_gold_preflight(datasets["bird"][:10], bird_executor)
    records: list[dict[str, Any]] = []
    for label in ("bird", "defog_classic", "defog_advanced"):
        kind = "bird" if label == "bird" else "defog"
        for question in datasets[label][:10]:
            name = question.db_id if kind == "bird" else question.db_name
            context = contexts[(kind, name)]
            key = str(question.index)
            if kind != "bird":
                key = f"{question.dataset}:{question.index}"
            records.append(
                await _run_case(
                    provider,
                    safety_cache[(kind, name)][1],
                    question,
                    label,
                    kind,
                    context,
                    gold[label][key],
                    manifest,
                )
            )
    return {
        "classification": "M20_SMOKE_NON_AUTHORITATIVE",
        "cases_per_benchmark": 10,
        "provider_calls": sum(record["provider_calls"] for record in records),
        "benchmarks": {
            label: _summary(
                [record for record in records if record["benchmark"] == label]
            )
            for label in ("bird", "defog_classic", "defog_advanced")
        },
    }


def run_command() -> dict[str, Any]:
    """Run only after the global fail-closed preflight has fully passed."""
    datasets, validations, contexts, _, safety_cache, manifest = _global_preflight()
    return asyncio.run(
        _run_all(datasets, contexts, validations, safety_cache, manifest)
    )


def smoke_command() -> dict[str, Any]:
    datasets, _, contexts, _, safety_cache, manifest = _global_preflight()
    return asyncio.run(_run_smoke(datasets, contexts, safety_cache, manifest))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.run == args.smoke:
        parser.error("M20 requires exactly one of --smoke or --run")
    try:
        result = smoke_command() if args.smoke else run_command()
    except M20PreflightFailure as error:
        failure = {
            "classification": "M20_VALIDATION_INVALID",
            "failure_stage": M20FailureStage.PREFLIGHT.value,
            "error": str(error),
            "provider_constructed": False,
            "provider_calls": 0,
        }
        M20_RESULT.parent.mkdir(parents=True, exist_ok=True)
        M20_RESULT.write_text(json.dumps(failure, indent=2) + "\n")
        raise SystemExit(json.dumps(failure)) from error
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
