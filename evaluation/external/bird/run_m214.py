"""M2.14 BIRD Mini-Dev PostgreSQL external calibration runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import sqlglot

from app.config import get_settings
from app.generation.provider import LLMProviderError, ModelIOCapture, OpenAICompatibleProvider
from evaluation.external.bird.benchmark import (
    BirdBenchmarkExecutor,
    BirdQuestion,
    QueryResult,
    load_questions,
    sha256_file,
    sha256_text,
)
from evaluation.external.bird.evaluator import evaluate
from evaluation.external.bird.features import ast_features, complexity_tags, structural_profile
from evaluation.external.bird.schema import schema_context

ROOT = Path(__file__).resolve().parents[3]
RESULT_ROOT = ROOT / "evaluation/results/m214"
MANIFEST_PATH = ROOT / "evaluation/external/bird/manifest.json"
DATA_ROOT_ENV = "BIRD_MINI_DEV_ROOT"
DATA_FILE = "mini_dev_postgresql.json"
DUMP_FILE = "BIRD_dev.sql"
ARCHIVE_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"
ARCHIVE_SHA256 = "cc48ba16838204e4e214512030cb572eeb5f7bcdd999bae4b9b6ff12ec13b92f"
UPSTREAM_COMMIT = "b3d4bcbbae9a96934ad812551eb400c7a3b23c12"
PROMPT_VERSION = "m214-bird-standard-evidence-v1"
PROMPT_TEMPLATE = (
    "SYSTEM: You generate one valid PostgreSQL SELECT query. Use only the supplied "
    "database schema. Return PostgreSQL SQL only, beginning with SELECT or WITH. Do "
    "not include explanation, Markdown, comments, or chain-of-thought.\n\n"
    "DATABASE SCHEMA:\n{schema}\n\nUSER: {question}\n\n"
    "BIRD expert evidence:\n{evidence}"
)


def data_root() -> Path:
    root = Path(os.environ.get(DATA_ROOT_ENV, "/bird"))
    if not root.is_dir():
        raise RuntimeError(f"{DATA_ROOT_ENV} must point to the extracted BIRD package")
    return root


def dataset_path() -> Path:
    path = data_root() / DATA_FILE
    if not path.is_file():
        raise RuntimeError(f"Missing BIRD dataset: {path}")
    return path


def dump_path() -> Path:
    path = data_root() / DUMP_FILE
    if not path.is_file():
        raise RuntimeError(f"Missing BIRD PostgreSQL dump: {path}")
    return path


def ensure_credential() -> None:
    if not os.getenv("DECISION_SQL_LLM_API_KEY") and os.getenv("OPENAI_API_KEY"):
        os.environ["DECISION_SQL_LLM_API_KEY"] = os.environ["OPENAI_API_KEY"]
    if not os.getenv("DECISION_SQL_LLM_API_KEY"):
        raise RuntimeError("No provider credential is configured")


def request_settings() -> Any:
    return get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
            "eval_capture_model_io": True,
        }
    )


def provider_question(question: BirdQuestion) -> str:
    return f"{question.question}\n\nBIRD expert evidence:\n{question.evidence}"


def provider_instruction() -> str:
    return (
        "You generate one valid PostgreSQL SELECT query. Use only the supplied database "
        "schema. Return PostgreSQL SQL only, beginning with SELECT or WITH. Do not include "
        "explanation, Markdown, comments, or chain-of-thought."
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


def load_dataset() -> list[BirdQuestion]:
    questions = load_questions(dataset_path())
    if len(questions) != 500:
        raise RuntimeError(f"Expected canonical 500 BIRD rows, found {len(questions)}")
    if len({question.db_id for question in questions}) != 11:
        raise RuntimeError("Expected canonical BIRD rows from exactly 11 logical databases")
    return questions


def profile(questions: list[BirdQuestion]) -> dict[str, Any]:
    features: dict[str, int] = {}
    structural: dict[str, int] = {}
    for question in questions:
        tags = ast_features(question.gold_sql)
        if tags.get("PARSE_ERROR"):
            raise RuntimeError(f"Gold SQL does not parse: question {question.question_id}")
        for key, value in tags.items():
            features[key] = features.get(key, 0) + int(bool(value))
        for key, structural_value in structural_profile(question.gold_sql).items():
            structural[key] = structural.get(key, 0) + int(bool(structural_value))
    return {
        "questions": len(questions),
        "logical_database_count": len({q.db_id for q in questions}),
        "database_counts": dict(Counter(q.db_id for q in questions)),
        "difficulty_counts": dict(Counter(q.difficulty for q in questions)),
        "evidence_present": sum(bool(q.evidence.strip()) for q in questions),
        "evidence_empty": sum(not q.evidence.strip() for q in questions),
        "gold_features": features,
        "gold_structural_feature_sums": structural,
        "complexity_distribution": dict(
            Counter(
                tag for q in questions for tag in complexity_tags(structural_profile(q.gold_sql))
            )
        ),
        "question_chars": {
            "average": mean(len(q.question) for q in questions),
            "p50": sorted(len(q.question) for q in questions)[len(questions) // 2],
        },
        "gold_sql_sha256s": {str(q.question_id): sha256_text(q.gold_sql) for q in questions},
    }


def validate_gold(
    questions: list[BirdQuestion], executor: BirdBenchmarkExecutor
) -> tuple[dict[str, QueryResult], dict[str, Any]]:
    cache: dict[str, QueryResult] = {}
    failures: list[dict[str, Any]] = []
    parse_success = 0
    for question in questions:
        try:
            executor._validate_select(question.gold_sql)
            parse_success += 1
            cache[str(question.index)] = executor.execute(question.gold_sql)
        except Exception as error:
            failures.append(
                {
                    "question_id": question.question_id,
                    "db_id": question.db_id,
                    "error_type": type(error).__name__,
                    "error": str(error)[:300],
                }
            )
    return cache, {
        "questions": len(questions),
        "parse_success": parse_success,
        "execution_success": len(cache),
        "failures": failures,
        "all_questions_executable": len(cache) == len(questions) and not failures,
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def prepare() -> dict[str, Any]:
    questions = load_dataset()
    contexts: dict[str, dict[str, Any]] = {}
    for db_id in sorted({question.db_id for question in questions}):
        text, metadata = schema_context(db_id)
        contexts[db_id] = {"text": text, "metadata": metadata}
    manifest = {
        "milestone": "M2.14",
        "repository": "bird-bench/mini_dev",
        "commit": UPSTREAM_COMMIT,
        "dataset_source": ARCHIVE_URL,
        "dataset_archive_sha256": ARCHIVE_SHA256,
        "dataset_file": str(dataset_path()),
        "dataset_file_sha256": sha256_file(dataset_path()),
        "dataset_split": "mini_dev_postgresql.json from MINIDEV package",
        "selected_subset": "canonical 500 SELECT-only Mini-Dev V1",
        "dataset_rows": len(questions),
        "logical_database_count": len({q.db_id for q in questions}),
        "physical_database": "bird",
        "postgres_dump": str(dump_path()),
        "postgres_dump_sha256": sha256_file(dump_path()),
        "dialect": "postgres",
        "reference_prompt_source": "llm/src/prompt.py in pinned bird-bench/mini_dev",
        "reference_prompt_notes": {
            "schema": "catalog-derived CREATE TABLE definitions, no FK metadata in prompt",
            "evidence": "expert evidence appended to the question prompt",
            "reasoning": "reference has a CoT phrase; DecisionSQL omits it",
            "response": (
                "SQL-only response; DecisionSQL allows one surrounding Markdown fence cleanup"
            ),
        },
        "schema_serialization": {db_id: value["metadata"] for db_id, value in contexts.items()},
        "prompt_version": PROMPT_VERSION,
        "prompt_template_sha256": sha256_text(PROMPT_TEMPLATE),
        "model": "gpt-5.6-luna",
        "reasoning": "none",
        "temperature": "provider_default",
        "k_shot": 0,
        "gold_leakage": False,
        "foreign_keys_exposed": False,
        "sample_rows_exposed": False,
        "benchmark_executor": {
            "container": "decision-sql-bird-postgres",
            "port": int(os.getenv("BIRD_DB_PORT", "55433")),
            "reader_role": os.getenv("BIRD_DB_USER", "bird_reader"),
            "credentials_persisted": False,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    (MANIFEST_PATH.parent / "dataset_profile.json").write_text(
        json.dumps(profile(questions), indent=2) + "\n"
    )
    (MANIFEST_PATH.parent / "schema_contexts.json").write_text(
        json.dumps(contexts, indent=2) + "\n"
    )
    return manifest


async def smoke(questions: list[BirdQuestion]) -> dict[str, Any]:
    ensure_credential()
    contexts = json.loads((MANIFEST_PATH.parent / "schema_contexts.json").read_text())
    provider = OpenAICompatibleProvider(request_settings())
    question = questions[0]
    schema = contexts[question.db_id]["text"]
    proposal = await provider.propose_window_transport(
        provider_question(question), schema, provider_instruction(), operation="BIRD_SQL"
    )
    capture = provider.consume_model_io()
    return {
        "question_id": question.question_id,
        "provider_success": True,
        "generated_sql": clean_sql(proposal.content),
        "input_tokens": proposal.prompt_tokens,
        "output_tokens": proposal.completion_tokens,
        "latency_ms": proposal.latency_ms,
        "model_io": capture.model_dump(mode="json")
        if isinstance(capture, ModelIOCapture)
        else None,
    }


async def run_all(
    run_id: str,
    questions: list[BirdQuestion],
    gold: dict[str, QueryResult],
    validation: dict[str, Any],
    result_kind: str = "dev",
) -> dict[str, Any]:
    ensure_credential()
    schema_data = json.loads((MANIFEST_PATH.parent / "schema_contexts.json").read_text())
    executor = BirdBenchmarkExecutor()
    provider = OpenAICompatibleProvider(request_settings())
    records: list[dict[str, Any]] = []
    io_records: list[dict[str, Any]] = []
    for position, question in enumerate(questions, start=1):
        started = time.perf_counter()
        record: dict[str, Any] = {
            "benchmark": "bird-mini-dev",
            "dataset": "mini_dev_postgresql",
            "index": question.index,
            "question_id": question.question_id,
            "db_id": question.db_id,
            "question": question.question,
            "evidence_sha256": sha256_text(question.evidence),
            "schema_context_hash": schema_data[question.db_id]["metadata"]["schema_context_sha256"],
            "gold_sql_sha256": sha256_text(question.gold_sql),
            "difficulty": question.difficulty,
            "gold_features": ast_features(question.gold_sql),
            "gold_structural_profile": structural_profile(question.gold_sql),
            "provider_success": False,
            "parse_success": False,
            "safety_accepted": False,
            "execution_success": False,
            "timeout": False,
            "execution_accuracy": False,
            "soft_f1": None,
        }
        try:
            proposal = await provider.propose_window_transport(
                provider_question(question),
                schema_data[question.db_id]["text"],
                provider_instruction(),
                operation="BIRD_SQL",
            )
            record.update(
                {
                    "provider_success": True,
                    "input_tokens": proposal.prompt_tokens,
                    "output_tokens": proposal.completion_tokens,
                    "provider_latency_ms": proposal.latency_ms,
                    "generated_sql": clean_sql(proposal.content),
                }
            )
            generated_sql = record["generated_sql"]
            try:
                sqlglot.parse_one(generated_sql, read="postgres")
                record["parse_success"] = True
            except Exception as error:
                record.update({"failure": "SQL_PARSE_FAILURE", "error": str(error)[:300]})
            if record["parse_success"]:
                try:
                    generated = executor.execute(generated_sql)
                    record.update({"safety_accepted": True, "execution_success": True})
                except ValueError as error:
                    record.update(
                        {"failure": "BENCHMARK_SAFETY_REJECTION", "error": str(error)[:300]}
                    )
                except Exception as error:
                    record.update(
                        {
                            "failure": "TIMEOUT"
                            if "timeout" in str(error).lower()
                            else "EXECUTION_ERROR",
                            "error": str(error)[:300],
                        }
                    )
                    record["timeout"] = record["failure"] == "TIMEOUT"
                if record["execution_success"]:
                    verdict = evaluate(generated, gold[str(question.index)])
                    record.update(verdict)
                    record["generated_features"] = ast_features(generated_sql)
                    if not record["execution_accuracy"]:
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
            print(f"BIRD: {position}/{len(questions)} provider calls complete", flush=True)
    output = RESULT_ROOT / result_kind / run_id
    output.mkdir(parents=True, exist_ok=False)
    (output / "upstream_manifest.json").write_text(MANIFEST_PATH.read_text())
    (output / "database_validation.json").write_text(
        json.dumps(validation, indent=2, default=str) + "\n"
    )
    (output / "results.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in records)
    )
    (output / "model_io.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in io_records)
    )
    summary = summarize(records)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    (output / "dataset_profile.json").write_text(
        (MANIFEST_PATH.parent / "dataset_profile.json").read_text()
    )
    (output / "schema_contexts.json").write_text(
        (MANIFEST_PATH.parent / "schema_contexts.json").read_text()
    )
    (output / "metadata.json").write_text(
        json.dumps(
            {
                "milestone": "M2.14",
                "run_id": run_id,
                "model": "gpt-5.6-luna",
                "reasoning": "none",
                "temperature": "provider_default",
                "k_shot": 0,
                "provider_calls": len(records),
                "question_count": len(questions),
                "m1_used": False,
                "window_ir_used": False,
                "repair": False,
                "fallback": False,
                "holdout_used": False,
                "created_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n"
    )
    return {"run_dir": str(output), "summary": summary}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)

    def count(predicate: Any) -> int:
        return sum(bool(predicate(record)) for record in records)

    latency = [
        float(r["provider_latency_ms"]) for r in records if r.get("provider_latency_ms") is not None
    ]
    input_tokens = [int(r["input_tokens"]) for r in records if r.get("input_tokens") is not None]
    output_tokens = [int(r["output_tokens"]) for r in records if r.get("output_tokens") is not None]
    by_db: dict[str, Any] = {}
    for db_id in sorted({r["db_id"] for r in records}):
        rows = [r for r in records if r["db_id"] == db_id]
        by_db[db_id] = {
            "questions": len(rows),
            "exact_correct": sum(r["execution_accuracy"] for r in rows),
            "parse_success": sum(r["parse_success"] for r in rows),
            "execution_success": sum(r["execution_success"] for r in rows),
        }
    by_difficulty: dict[str, Any] = {}
    for difficulty in sorted({r["difficulty"] for r in records}):
        rows = [r for r in records if r["difficulty"] == difficulty]
        by_difficulty[difficulty] = {
            "questions": len(rows),
            "exact_correct": sum(r["execution_accuracy"] for r in rows),
        }
    windows = [r for r in records if r["gold_features"].get("HAS_WINDOW")]
    ratio = [r for r in records if r["gold_structural_profile"].get("division")]
    temporal = [r for r in records if r["gold_structural_profile"].get("date_time")]
    return {
        "questions": total,
        "provider_success": count(lambda r: r["provider_success"]),
        "provider_failures": count(lambda r: not r["provider_success"]),
        "sql_parse_success": count(lambda r: r["parse_success"]),
        "benchmark_safety_rejections": count(
            lambda r: r.get("failure") == "BENCHMARK_SAFETY_REJECTION"
        ),
        "execution_success": count(lambda r: r["execution_success"]),
        "execution_errors": count(lambda r: r.get("failure") == "EXECUTION_ERROR"),
        "timeouts": count(lambda r: r["timeout"]),
        "result_mismatches": count(lambda r: r.get("failure") == "RESULT_MISMATCH"),
        "execution_accuracy": count(lambda r: r["execution_accuracy"]),
        "execution_accuracy_percentage": count(lambda r: r["execution_accuracy"]) / total * 100
        if total
        else 0,
        "soft_f1_average": mean(float(r["soft_f1"]) for r in records if r["soft_f1"] is not None)
        if any(r["soft_f1"] is not None for r in records)
        else None,
        "difficulty": by_difficulty,
        "database": by_db,
        "window": {
            "questions": len(windows),
            "exact_correct": sum(r["execution_accuracy"] for r in windows),
            "generated_window": sum(
                bool(r.get("generated_features", {}).get("HAS_WINDOW")) for r in windows
            ),
        },
        "ratio": {
            "questions": len(ratio),
            "exact_correct": sum(r["execution_accuracy"] for r in ratio),
        },
        "temporal": {
            "questions": len(temporal),
            "exact_correct": sum(r["execution_accuracy"] for r in temporal),
        },
        "failures": dict(Counter(r.get("failure") for r in records if r.get("failure"))),
        "tokens": {
            "input_total": sum(input_tokens),
            "output_total": sum(output_tokens),
            "input_average": mean(input_tokens) if input_tokens else None,
            "output_average": mean(output_tokens) if output_tokens else None,
            "input_p50": percentile([float(v) for v in input_tokens], 0.5),
            "output_p50": percentile([float(v) for v in output_tokens], 0.5),
        },
        "latency_ms": {
            "average": mean(latency) if latency else None,
            "p50": percentile(latency, 0.5),
            "p95": percentile(latency, 0.95),
        },
    }


def gold_preflight() -> dict[str, Any]:
    questions = load_dataset()
    _, validation = validate_gold(questions, BirdBenchmarkExecutor())
    return validation


def control_questions(questions: list[BirdQuestion], count: int) -> list[BirdQuestion]:
    """Select a stable 20–30-row control slice across all logical databases."""
    if count < 20 or count > 30:
        raise ValueError("control count must be between 20 and 30")
    if count > len(questions):
        raise ValueError("control count cannot exceed available questions")
    by_db: dict[str, list[BirdQuestion]] = {}
    for question in questions:
        by_db.setdefault(question.db_id, []).append(question)
    selected: list[BirdQuestion] = []
    ordered_dbs = sorted(by_db)
    offset = 0
    while len(selected) < count:
        for db_id in ordered_dbs:
            if offset < len(by_db[db_id]) and len(selected) < count:
                selected.append(by_db[db_id][offset])
        offset += 1
    return sorted(selected, key=lambda question: question.index)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--gold-preflight", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--control-count", type=int, default=25)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    if args.prepare:
        print(json.dumps(prepare(), indent=2))
    if args.gold_preflight:
        validation = gold_preflight()
        print(json.dumps(validation, indent=2, default=str))
        if not validation["all_questions_executable"]:
            raise SystemExit(1)
    if args.smoke:
        questions = load_dataset()
        validation = gold_preflight()
        if not validation["all_questions_executable"]:
            raise SystemExit("Gold preflight failed; smoke not run")
        print(json.dumps(asyncio.run(smoke(questions)), indent=2, default=str))
    if args.run:
        questions = load_dataset()
        ensure_credential()
        gold, validation = validate_gold(questions, BirdBenchmarkExecutor())
        if not validation["all_questions_executable"]:
            raise SystemExit("Gold preflight failed; provider calls are not permitted")
        run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        print(
            json.dumps(
                asyncio.run(run_all(run_id, questions, gold, validation)), indent=2, default=str
            )
        )
    if args.control:
        questions = control_questions(load_dataset(), args.control_count)
        ensure_credential()
        gold, validation = validate_gold(questions, BirdBenchmarkExecutor())
        if not validation["all_questions_executable"]:
            raise SystemExit("Gold preflight failed; provider calls are not permitted")
        run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        result = asyncio.run(run_all(run_id, questions, gold, validation, "control"))
        output = Path(result["run_dir"])
        (output / "control_manifest.json").write_text(
            json.dumps(
                {
                    "source_dataset_sha256": json.loads(MANIFEST_PATH.read_text())[
                        "dataset_file_sha256"
                    ],
                    "count": len(questions),
                    "question_ids": [question.question_id for question in questions],
                    "source_indices": [question.index for question in questions],
                },
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
