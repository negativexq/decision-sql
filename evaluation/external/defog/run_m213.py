"""M2.13 — Defog SQL-Eval PostgreSQL calibration runner."""

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
from pathlib import Path
from typing import Any, cast

import psycopg
import sqlglot

from app.config import get_settings
from app.generation.provider import LLMProviderError, ModelIOCapture, OpenAICompatibleProvider
from app.models.domain import QueryRequest
from evaluation.external.defog.benchmark import (
    DEFAULT_DBS,
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
    QueryResult,
    ast_features,
    canonical_compare,
    canonical_gold_variants,
    dataset_profile,
    format_reference_instructions,
    load_questions,
    schema_context,
    sha256_file,
    structural_profile,
)

ROOT = Path(__file__).resolve().parents[3]
DEFog_ROOT_ENV = "DEFOG_SQL_EVAL_ROOT"
DATA_ROOT_ENV = "DEFOG_DATA_ROOT"
MANIFEST = ROOT / "evaluation/external/defog/manifest.json"
RESULT_ROOT = ROOT / "evaluation/results/m213"
DATASETS = {
    "classic": "questions_gen_postgres.csv",
    "advanced": "instruct_advanced_postgres.csv",
}
PROMPT_TEMPLATE = (
    "You generate one read-only PostgreSQL analytical query. Use only the "
    "supplied benchmark schema. Return exactly one JSON object with a non-empty "
    "'sql' string and no Markdown. The user question is authoritative."
)


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def git_head(path: Path, override_env: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
        ).strip()
    except FileNotFoundError:
        value = os.getenv(override_env)
        if value:
            return value
        raise RuntimeError(
            f"git is unavailable; provide the pinned commit in {override_env}"
        ) from None


def roots() -> tuple[Path, Path]:
    sql_eval = Path(os.environ[DEFog_ROOT_ENV])
    data = Path(os.environ[DATA_ROOT_ENV])
    if not sql_eval.is_dir() or not data.is_dir():
        raise RuntimeError("DEFOG_SQL_EVAL_ROOT and DEFOG_DATA_ROOT must point to pinned checkouts")
    return sql_eval, data


def ensure_credential() -> None:
    if not os.getenv("DECISION_SQL_LLM_API_KEY") and os.getenv("OPENAI_API_KEY"):
        os.environ["DECISION_SQL_LLM_API_KEY"] = os.environ["OPENAI_API_KEY"]
    if not os.getenv("DECISION_SQL_LLM_API_KEY"):
        raise RuntimeError("No provider credential: set DECISION_SQL_LLM_API_KEY or OPENAI_API_KEY")


def load_all(sql_eval: Path) -> dict[str, list[BenchmarkQuestion]]:
    return {
        name: load_questions(sql_eval / "data" / filename, name)
        for name, filename in DATASETS.items()
    }


def prepare() -> dict[str, Any]:
    sql_eval, data = roots()
    datasets = load_all(sql_eval)
    schemas: dict[str, Any] = {}
    executor = DefogBenchmarkExecutor()
    for db_name in sorted({q.db_name for rows in datasets.values() for q in rows}):
        text, metadata = schema_context(executor, db_name)
        schemas[db_name] = {"text": text, "metadata": metadata}
    manifest = {
        "milestone": "M2.13",
        "sql_eval_repository": "defog-ai/sql-eval",
        "sql_eval_root": str(sql_eval),
        "sql_eval_commit": git_head(sql_eval, "DEFOG_SQL_EVAL_COMMIT"),
        "defog_data_repository": "defog-ai/defog-data",
        "defog_data_root": str(data),
        "defog_data_commit": git_head(data, "DEFOG_DATA_COMMIT"),
        "dialect": "postgres",
        "datasets": {
            name: {
                "path": str(sql_eval / "data" / filename),
                "sha256": sha256_file(sql_eval / "data" / filename),
                "questions": len(rows),
                "databases": sorted({q.db_name for q in rows}),
            }
            for name, filename in DATASETS.items()
            for rows in [datasets[name]]
        },
        "database_names": list(DEFAULT_DBS),
        "database_setup": {
            "container": "decision-sql-defog-postgres",
            "host": os.getenv("DEFOG_DB_HOST", "host.docker.internal"),
            "port": int(os.getenv("DEFOG_DB_PORT", "55432")),
            "user": os.getenv("DEFOG_DB_USER", "defog_reader"),
            "credentials_persisted": False,
        },
        "schema_contexts": {name: value["metadata"] for name, value in schemas.items()},
        "prompt_template": PROMPT_TEMPLATE,
        "prompt_template_sha256": text_sha(PROMPT_TEMPLATE),
        "advanced_instruction_field": "instructions",
        "full_instructions_used": False,
        "k_shot": 0,
        "model": "gpt-5.6-luna",
        "reasoning": "none",
        "temperature": "provider_default",
        "gold_leakage": False,
        "schema_contexts_text": schemas,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    manifest.pop("schema_contexts_text")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    for name, rows in datasets.items():
        profile = dataset_profile(rows)
        (MANIFEST.parent / f"{name}_dataset_profile.json").write_text(
            json.dumps(profile, indent=2) + "\n"
        )
    (MANIFEST.parent / "schema_contexts.json").write_text(
        json.dumps(schemas, indent=2, default=str) + "\n"
    )
    return manifest


def db_validation(
    questions: list[BenchmarkQuestion], executor: DefogBenchmarkExecutor
) -> tuple[dict[str, list[tuple[str, QueryResult]]], dict[str, Any]]:
    cache: dict[str, list[tuple[str, QueryResult]]] = {}
    failures: list[dict[str, Any]] = []
    for question in questions:
        variants = canonical_gold_variants(question.gold_sql)
        results: list[tuple[str, QueryResult]] = []
        for variant in variants:
            try:
                results.append((variant, executor.execute(question.db_name, variant)))
            except Exception as error:
                failures.append(
                    {
                        "dataset": question.dataset,
                        "index": question.index,
                        "db_name": question.db_name,
                        "error_type": type(error).__name__,
                        "error": str(error)[:300],
                    }
                )
        if results:
            cache[f"{question.dataset}:{question.index}"] = results
    return cache, {
        "questions": len(questions),
        "gold_variants": sum(len(v) for v in cache.values()),
        "questions_with_executable_gold": len(cache),
        "failures": failures,
        "all_questions_executable": len(cache) == len(questions) and not failures,
    }


def request_settings() -> Any:
    return get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
            "eval_capture_model_io": True,
        }
    )


def clean_sql(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("```sql"):
        text = text[6:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def schema_for(db_name: str) -> str:
    value = json.loads((MANIFEST.parent / "schema_contexts.json").read_text())
    return cast(str, value[db_name]["text"])


def generation_schema(question: BenchmarkQuestion) -> str:
    instruction = format_reference_instructions(question.instructions)
    return schema_for(question.db_name) + instruction


async def smoke(questions: list[BenchmarkQuestion], run_id: str) -> dict[str, Any]:
    ensure_credential()
    provider = OpenAICompatibleProvider(request_settings())
    question = questions[0]
    proposal = await provider.propose_sql(
        QueryRequest(question=question.question), None, generation_schema(question)
    )
    capture = provider.consume_model_io()
    result = {
        "run_id": run_id,
        "question": question.question,
        "dataset": question.dataset,
        "index": question.index,
        "provider_success": True,
        "sql": clean_sql(proposal.sql),
        "model": proposal.model,
        "input_tokens": proposal.prompt_tokens,
        "output_tokens": proposal.completion_tokens,
        "latency_ms": proposal.latency_ms,
        "model_io": capture.model_dump(mode="json")
        if isinstance(capture, ModelIOCapture)
        else None,
    }
    path = RESULT_ROOT / "smoke" / run_id
    path.mkdir(parents=True, exist_ok=False)
    (path / "smoke.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result


async def run_dataset(
    dataset_name: str,
    questions: list[BenchmarkQuestion],
    schema: dict[str, Any],
    gold: dict[str, list[tuple[str, QueryResult]]],
    executor: DefogBenchmarkExecutor,
    provider: OpenAICompatibleProvider,
    smoke_result: dict[str, Any] | None,
    io_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for position, question in enumerate(questions, start=1):
        started = time.perf_counter()
        key = f"{question.dataset}:{question.index}"
        record: dict[str, Any] = {
            "benchmark": "defog-sql-eval",
            "dataset": dataset_name,
            "index": question.index,
            "db_name": question.db_name,
            "query_category": question.query_category,
            "question": question.question,
            "instructions_sha256": text_sha(question.instructions),
            "schema_context_hash": schema[question.db_name]["metadata"]["schema_context_sha256"],
            "gold_sql_sha256": text_sha(question.gold_sql),
            "model": "gpt-5.6-luna",
            "reasoning": "none",
            "temperature": "provider_default",
            "k_shot": 0,
            "gold_features": ast_features(question.gold_sql),
            "gold_structural_profile": structural_profile(question.gold_sql),
            "provider_success": False,
            "parse_success": False,
            "safety_accepted": False,
            "execution_success": False,
            "timeout": False,
            "exact_match": False,
            "subset_match": False,
        }
        try:
            proposal = await provider.propose_sql(
                QueryRequest(question=question.question), None, generation_schema(question)
            )
            sql = clean_sql(proposal.sql)
            record.update(
                {
                    "provider_success": True,
                    "input_tokens": proposal.prompt_tokens,
                    "output_tokens": proposal.completion_tokens,
                    "provider_latency_ms": proposal.latency_ms,
                }
            )
            record["generated_sql"] = sql
            record["generated_sql_sha256"] = text_sha(sql)
            try:
                sqlglot.parse_one(sql, read="postgres")
                record["parse_success"] = True
            except Exception as error:
                record.update({"failure": "SQL_PARSE_FAILURE", "error": str(error)[:300]})
                records.append(record)
                continue
            try:
                generated = executor.execute(question.db_name, sql)
                record["safety_accepted"] = True
                record["execution_success"] = True
            except ValueError as error:
                record.update({"failure": "BENCHMARK_SAFETY_REJECTION", "error": str(error)[:300]})
                records.append(record)
                continue
            except psycopg.errors.QueryCanceled as error:
                record.update({"failure": "TIMEOUT", "timeout": True, "error": str(error)[:300]})
                records.append(record)
                continue
            except Exception as error:
                record.update({"failure": "EXECUTION_ERROR", "error": str(error)[:300]})
                records.append(record)
                continue
            for gold_sql, gold_result in gold[key]:
                exact, subset = canonical_compare(gold_result, generated, question, gold_sql, sql)
                record["exact_match"] |= exact
                record["subset_match"] |= subset
            if not record["exact_match"] and not record["subset_match"]:
                record["failure"] = "RESULT_MISMATCH"
        except LLMProviderError as error:
            record.update({"failure": "PROVIDER_FAILURE", "error": str(error)[:300]})
        finally:
            record["total_latency_ms"] = (time.perf_counter() - started) * 1000
            capture = provider.consume_model_io()
            if isinstance(capture, ModelIOCapture):
                record["model_io"] = capture.model_dump(mode="json")
                io_records.append(record["model_io"])
        records.append(record)
        if position % 10 == 0 or position == len(questions):
            print(
                f"{dataset_name}: {position}/{len(questions)} provider calls complete",
                flush=True,
            )
    return records


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    return {
        "questions": total,
        "provider_success": sum(r["provider_success"] for r in records),
        "provider_failures": sum(not r["provider_success"] for r in records),
        "parse_success": sum(r["parse_success"] for r in records),
        "safety_accepted": sum(r["safety_accepted"] for r in records),
        "safety_rejections": sum(r.get("failure") == "BENCHMARK_SAFETY_REJECTION" for r in records),
        "execution_success": sum(r["execution_success"] for r in records),
        "execution_errors": sum(r.get("failure") == "EXECUTION_ERROR" for r in records),
        "timeouts": sum(r["timeout"] for r in records),
        "exact_correct": sum(r["exact_match"] for r in records),
        "subset_correct": sum(r["subset_match"] for r in records),
        "failures": dict(Counter(r.get("failure") for r in records if r.get("failure"))),
        "category": {
            category: {
                "questions": sum(r["query_category"] == category for r in records),
                "exact_correct": sum(
                    r["query_category"] == category and r["exact_match"] for r in records
                ),
                "subset_correct": sum(
                    r["query_category"] == category and r["subset_match"] for r in records
                ),
                "parse_success": sum(
                    r["query_category"] == category and r["parse_success"] for r in records
                ),
                "execution_success": sum(
                    r["query_category"] == category and r["execution_success"] for r in records
                ),
            }
            for category in sorted({r["query_category"] for r in records})
        },
        "exact_percentage": sum(r["exact_match"] for r in records) / total * 100 if total else 0,
        "subset_percentage": sum(r["subset_match"] for r in records) / total * 100 if total else 0,
    }


async def full_run(run_id: str, smoke_result: dict[str, Any] | None) -> dict[str, Any]:
    ensure_credential()
    if not MANIFEST.exists():
        raise RuntimeError("Run --prepare before --run")
    sql_eval, _ = roots()
    datasets = load_all(sql_eval)
    schema = json.loads((MANIFEST.parent / "schema_contexts.json").read_text())
    all_questions = [q for rows in datasets.values() for q in rows]
    executor = DefogBenchmarkExecutor()
    gold, validation = db_validation(all_questions, executor)
    if not validation["all_questions_executable"]:
        raise RuntimeError("Gold execution preflight failed; no provider evaluation started")
    provider = OpenAICompatibleProvider(request_settings())
    io_records: list[dict[str, Any]] = []
    classic = await run_dataset(
        "classic", datasets["classic"], schema, gold, executor, provider, None, io_records
    )
    advanced = await run_dataset(
        "advanced", datasets["advanced"], schema, gold, executor, provider, None, io_records
    )
    output = RESULT_ROOT / "dev" / run_id
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(MANIFEST.read_text())
    (output / "upstream_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "database_validation.json").write_text(
        json.dumps(validation, indent=2, default=str) + "\n"
    )
    (output / "classic_results.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in classic)
    )
    (output / "advanced_results.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in advanced)
    )
    (output / "classic_summary.json").write_text(json.dumps(summary(classic), indent=2) + "\n")
    (output / "advanced_summary.json").write_text(json.dumps(summary(advanced), indent=2) + "\n")
    all_records = classic + advanced
    window_records = [r for r in all_records if r["gold_features"].get("HAS_WINDOW")]
    (output / "window_analysis.json").write_text(
        json.dumps(
            {
                "all_window_questions": len(window_records),
                "exact_correct": sum(r["exact_match"] for r in window_records),
                "subset_correct": sum(r["subset_match"] for r in window_records),
                "generated_window": sum(
                    bool(ast_features(r.get("generated_sql", "")).get("HAS_WINDOW"))
                    for r in window_records
                ),
            },
            indent=2,
        )
        + "\n"
    )
    (output / "category_analysis.json").write_text(
        json.dumps(summary(all_records)["category"], indent=2) + "\n"
    )
    (output / "model_io.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in io_records)
    )
    metadata = {
        "milestone": "M2.13",
        "run_id": run_id,
        "model": "gpt-5.6-luna",
        "reasoning": "none",
        "temperature": "provider_default",
        "k_shot": 0,
        "datasets": ["classic", "advanced"],
        "smoke_provider_calls": 1 if smoke_result else 0,
        "full_provider_calls": len(all_records),
        "provider_calls": len(all_records) + (1 if smoke_result else 0),
        "gold_leakage": False,
        "m1_used": False,
        "holdout_used": False,
        "smoke_run_id": smoke_result.get("run_id") if smoke_result else None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return {
        "classic": summary(classic),
        "advanced": summary(advanced),
        "database_validation": validation,
        "run_dir": str(output),
    }


def gold_preflight() -> dict[str, Any]:
    sql_eval, _ = roots()
    datasets = load_all(sql_eval)
    executor = DefogBenchmarkExecutor()
    questions = [q for rows in datasets.values() for q in rows]
    _, validation = db_validation(questions, executor)
    if not validation["all_questions_executable"]:
        raise RuntimeError(
            "Gold execution preflight failed; provider calls are not permitted: "
            + json.dumps(validation, default=str)
        )
    return validation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--smoke-run-id", default=None)
    args = parser.parse_args()
    if args.prepare:
        print(json.dumps(prepare(), indent=2))
    if args.smoke:
        sql_eval, _ = roots()
        validation = gold_preflight()
        result = asyncio.run(
            smoke(
                load_all(sql_eval)["classic"],
                args.smoke_run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            )
        )
        print(
            json.dumps(
                {
                    "gold_validation": validation,
                    "smoke": {k: v for k, v in result.items() if k != "model_io"},
                },
                indent=2,
            )
        )
    if args.run:
        smoke_result = None
        if args.smoke_run_id:
            smoke_path = RESULT_ROOT / "smoke" / args.smoke_run_id / "smoke.json"
            smoke_result = json.loads(smoke_path.read_text())
        result = asyncio.run(
            full_run(args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), smoke_result)
        )
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
