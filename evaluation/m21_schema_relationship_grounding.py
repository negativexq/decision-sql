"""M21 targeted evaluation of an explicit server-owned schema renderer."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy.engine import Engine

from app.catalog.models import (
    ContextMetadata,
    RelevantColumn,
    RelevantRelationship,
    RelevantTable,
    SchemaContext,
    Sensitivity,
)
from app.config import Settings
from app.retrieval.context import serialize_schema_context_v2
from app.sql.models import CandidateSource, SqlCandidate, SqlExecutionError, SqlPlanFailure
from evaluation.external.bird.benchmark import BirdBenchmarkExecutor
from evaluation.external.bird.schema import BIRD_TABLES
from evaluation.external.defog.benchmark import DefogBenchmarkExecutor
from evaluation.m20_2_residual_semantic_forensics import _ast_facts
from evaluation.m20_full_rebaseline import (
    M20FailureStage,
    M20PreflightFailure,
    _bird_gold_preflight,
    _dataset_roots,
    _defog_gold_preflight,
    _generate,
    _load_all,
    _new_record,
    _settings,
    _settings_for_db,
)

ROOT = Path(__file__).resolve().parents[1]
M20_CASES = ROOT / "evaluation/fixtures/m20_full_benchmark_cases.jsonl"
M20_MANIFEST = ROOT / "evaluation/fixtures/m20_prebenchmark_manifest.json"
M20_FORENSICS = ROOT / "evaluation/fixtures/m20_2_residual_semantic_forensics.json"
M21_MANIFEST = ROOT / "evaluation/fixtures/m21_schema_relationship_grounding_manifest.json"
M21_RESULT = ROOT / "evaluation/fixtures/m21_schema_relationship_grounding.json"

STARTING_COMMIT = "50ca58513dcf683e1eaa3056244450ada69a307f"
EXPECTED_CASES = {"bird": 500, "defog_classic": 210, "defog_advanced": 64}


def _sha(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _git_clean() -> bool:
    return not subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--short"], text=True
    ).strip()


def _git_clean_for_run() -> bool:
    status = subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--short"], text=True
    ).splitlines()
    return all(
        line.endswith("evaluation/fixtures/m21_schema_relationship_grounding_manifest.json")
        for line in status
    )


def _case_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["benchmark"]), str(row["case_id"])


def _question_key(question: Any, label: str) -> tuple[str, str]:
    if label == "bird":
        return label, str(question.index)
    return label, f"{question.dataset}:{question.index}"


def _pg_connection(kind: str, name: str) -> dict[str, Any]:
    if kind == "bird":
        return {
            "host": os.getenv("BIRD_DB_HOST", "host.docker.internal"),
            "port": int(os.getenv("BIRD_DB_PORT", "55433")),
            "user": os.getenv("BIRD_DB_USER", "bird_reader"),
            "password": os.getenv("BIRD_DB_PASSWORD", "bird_reader_password"),
            "dbname": "bird",
        }
    return {
        "host": os.getenv("DEFOG_DB_HOST", "host.docker.internal"),
        "port": int(os.getenv("DEFOG_DB_PORT", "55432")),
        "user": os.getenv("DEFOG_DB_USER", "defog_reader"),
        "password": os.getenv("DEFOG_DB_PASSWORD", "defog_reader_password"),
        "dbname": name,
    }


def _benchmark_columns(kind: str, name: str) -> list[tuple[str, str, str, bool]]:
    allowed = {table.lower() for table in BIRD_TABLES.get(name, ())} if kind == "bird" else None
    with psycopg.connect(**_pg_connection(kind, name)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
                       c.udt_name, c.ordinal_position,
                       CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN true ELSE false END
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage k
                  ON k.table_schema = c.table_schema AND k.table_name = c.table_name
                 AND k.column_name = c.column_name
                LEFT JOIN information_schema.table_constraints tc
                  ON tc.constraint_schema = k.constraint_schema
                 AND tc.constraint_name = k.constraint_name
                WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY c.table_schema, c.table_name, c.ordinal_position
                """
            )
            rows = cursor.fetchall()
    result = []
    for schema, table, column, data_type, udt_name, _, primary_key in rows:
        if allowed is not None and str(table).lower() not in allowed:
            continue
        result.append(
            (f"{schema}.{table}", str(column), str(data_type or udt_name), bool(primary_key))
        )
    return result


def _database_relationships(kind: str, name: str) -> list[tuple[str, str, str, str]]:
    with psycopg.connect(**_pg_connection(kind, name)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_ns.nspname, source_table.relname, source_column.attname,
                       target_ns.nspname, target_table.relname, target_column.attname
                FROM pg_constraint fk
                JOIN pg_class source_table ON source_table.oid = fk.conrelid
                JOIN pg_namespace source_ns ON source_ns.oid = source_table.relnamespace
                JOIN pg_class target_table ON target_table.oid = fk.confrelid
                JOIN pg_namespace target_ns ON target_ns.oid = target_table.relnamespace
                CROSS JOIN LATERAL unnest(fk.conkey) WITH ORDINALITY source_key(attnum, position)
                CROSS JOIN LATERAL unnest(fk.confkey) WITH ORDINALITY target_key(attnum, position)
                JOIN pg_attribute source_column
                  ON source_column.attrelid = source_table.oid
                 AND source_column.attnum = source_key.attnum
                JOIN pg_attribute target_column
                  ON target_column.attrelid = target_table.oid
                 AND target_column.attnum = target_key.attnum
                 AND target_key.position = source_key.position
                WHERE fk.contype = 'f'
                  AND source_ns.nspname NOT IN ('information_schema', 'pg_catalog')
                ORDER BY source_ns.nspname, source_table.relname, source_column.attname,
                         target_ns.nspname, target_table.relname, target_column.attname
                """
            )
            rows = cursor.fetchall()
    allowed = {table.lower() for table in BIRD_TABLES.get(name, ())} if kind == "bird" else None
    relationships = [
        (
            f"{source_schema}.{source_table}",
            str(source_column),
            f"{target_schema}.{target_table}",
            str(target_column),
        )
        for (
            source_schema,
            source_table,
            source_column,
            target_schema,
            target_table,
            target_column,
        ) in rows
        if allowed is None
        or (str(source_table).lower() in allowed and str(target_table).lower() in allowed)
    ]
    return relationships


def _bird_declared_relationships(
    root: Path, db_id: str
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str, str]]]:
    tables = next(
        row for row in json.loads((root / "dev_tables.json").read_text()) if row["db_id"] == db_id
    )
    table_names = [str(value) for value in tables["table_names_original"]]
    columns = {
        index: (table_names[table_index], str(column))
        for index, (table_index, column) in enumerate(tables["column_names_original"])
        if int(table_index) >= 0
    }
    primary_indices: set[int] = set()
    for value in tables.get("primary_keys", []):
        primary_indices.update(value if isinstance(value, list) else [value])
    primary = {
        (table, column) for index, (table, column) in columns.items() if index in primary_indices
    }
    relationships = set()
    for source_index, target_index in tables.get("foreign_keys", []):
        source_table, source_column = columns[int(source_index)]
        target_table, target_column = columns[int(target_index)]
        relationships.add((source_table, source_column, target_table, target_column))
    return primary, relationships


def _schema_context_v2(kind: str, name: str, bird_root: Path) -> tuple[str, dict[str, Any]]:
    columns = _benchmark_columns(kind, name)
    relationships = set(_database_relationships(kind, name))
    declared_primary: set[tuple[str, str]] = set()
    if kind == "bird":
        declared_primary, declared_relationships = _bird_declared_relationships(bird_root, name)
        existing_tables = {table for table, _, _, _ in columns}
        for source_table, source_column, target_table, target_column in declared_relationships:
            source = next(
                (
                    table
                    for table in existing_tables
                    if table.split(".", 1)[-1].lower() == source_table.lower()
                ),
                None,
            )
            target = next(
                (
                    table
                    for table in existing_tables
                    if table.split(".", 1)[-1].lower() == target_table.lower()
                ),
                None,
            )
            if source and target:
                relationships.add((source, source_column, target, target_column))
        declared_primary = {
            (
                next(
                    (
                        table
                        for table in existing_tables
                        if table.split(".", 1)[-1].lower() == table_name.lower()
                    ),
                    table_name,
                ),
                column,
            )
            for table_name, column in declared_primary
        }
    primary = {
        (table, column) for table, column, _, is_primary in columns if is_primary
    } | declared_primary
    table_names = sorted({table for table, _, _, _ in columns})
    rel_by_column: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for source_table, source_column, target_table, target_column in relationships:
        rel_by_column.setdefault((source_table, source_column), set()).add(
            (target_table, target_column)
        )
    relevant_tables = []
    relevant_columns = []
    for table in table_names:
        table_columns = []
        for column_table, column, column_type, _ in columns:
            if column_table != table:
                continue
            targets = rel_by_column.get((table, column), set())
            target_endpoint: tuple[str, str] | None = (
                next(iter(targets)) if len(targets) == 1 else None
            )
            item = RelevantColumn(
                table_name=table,
                name=column,
                type=column_type,
                description="",
                sensitivity=Sensitivity.PUBLIC,
                primary_key=(table, column) in primary,
                foreign_key_table=target_endpoint[0] if target_endpoint else None,
                foreign_key_column=target_endpoint[1] if target_endpoint else None,
            )
            table_columns.append(item)
            relevant_columns.append(item)
        relevant_tables.append(
            RelevantTable(name=table, description="", columns=tuple(table_columns))
        )
    relevant_relationships = tuple(
        RelevantRelationship(
            source_table=source_table,
            source_column=source_column,
            target_table=target_table,
            target_column=target_column,
        )
        for source_table, source_column, target_table, target_column in sorted(relationships)
    )
    context = SchemaContext(
        tables=tuple(relevant_tables),
        relationships=relevant_relationships,
        selected_columns=tuple(relevant_columns),
        context_metadata=ContextMetadata(
            mode="full",
            seed_table_count=len(relevant_tables),
            selected_table_count=len(relevant_tables),
            selected_column_count=len(relevant_columns),
            relationship_count=len(relevant_relationships),
        ),
    )
    text = serialize_schema_context_v2(context)
    metadata = {
        "kind": kind,
        "name": name,
        "version": "SchemaContextV2",
        "tables": len(relevant_tables),
        "columns": len(relevant_columns),
        "relationships": len(relevant_relationships),
        "chars": len(text),
        "tokens_estimate": len(text.split()),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    return text, metadata


def _select_sets(
    datasets: dict[str, list[Any]],
    forensic: dict[str, Any],
    m20_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    schema = [row for row in forensic["cases"] if row["primary_root_cause"] == "SCHEMA_GROUNDING"]
    schema.sort(key=lambda row: _sha(("M21_SPLIT", row["benchmark"], row["case_id"])))
    dev = [
        row
        for row in schema
        if int(_sha(("M21_SPLIT", row["benchmark"], row["case_id"]))[:8], 16) % 3 == 0
    ]
    holdout = [row for row in schema if row not in dev]
    correct = [row for row in m20_rows if row["m1_status"] == "ALLOWED" and row["correct"] is True]

    def facts(row: dict[str, Any]) -> dict[str, Any]:
        return _ast_facts(row["generated_sql"])

    selected: list[dict[str, Any]] = []
    strata: tuple[tuple[str, Callable[[dict[str, Any]], bool]], ...] = (
        ("bird", lambda row: row["benchmark"] == "bird"),
        ("classic", lambda row: row["benchmark"] == "defog_classic"),
        ("zero_join", lambda row: facts(row).get("join_count", 0) == 0),
        ("one_join", lambda row: facts(row).get("join_count", 0) == 1),
        ("two_join", lambda row: facts(row).get("join_count", 0) >= 2),
        ("relationship_heavy", lambda row: facts(row).get("join_count", 0) > 0),
        (
            "aggregation_join",
            lambda row: bool(facts(row).get("aggregation")) and facts(row).get("join_count", 0) > 0,
        ),
    )
    for _, predicate in strata:
        matches = [row for row in correct if predicate(row) and row not in selected]
        matches.sort(key=lambda row: _sha(("M21_CONTROL", row["benchmark"], row["case_id"])))
        if matches:
            selected.append(matches[0])
    remaining = [row for row in correct if row not in selected]
    remaining.sort(key=lambda row: _sha(("M21_CONTROL", row["benchmark"], row["case_id"])))
    selected.extend(remaining[: max(0, 30 - len(selected))])
    if len(selected) != 30:
        raise M20PreflightFailure(f"M21 control selection expected 30, found {len(selected)}")
    if not dev or not holdout:
        raise M20PreflightFailure("M21 schema split is empty")
    return dev, holdout, selected


def _question_maps(datasets: dict[str, list[Any]]) -> dict[tuple[str, str], Any]:
    return {
        _question_key(question, label): question
        for label, questions in datasets.items()
        for question in questions
    }


def _p0_map(m20_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(str(row["benchmark"]), str(row["case_id"])): row for row in m20_rows}


def _case_spec(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": row["benchmark"],
        "case_id": str(row["case_id"]),
        "database": row["database"],
    }


def _manifest(
    datasets: dict[str, list[Any]],
    old_contexts: dict[tuple[str, str], str],
    old_metadata: dict[tuple[str, str], dict[str, Any]],
    v2_contexts: dict[tuple[str, str], str],
    v2_metadata: dict[tuple[str, str], dict[str, Any]],
    dev: list[dict[str, Any]],
    holdout: list[dict[str, Any]],
    control: list[dict[str, Any]],
    settings: Settings,
    sql_eval: Path,
    bird_root: Path,
) -> dict[str, Any]:
    old_metadata_json = {f"{key[0]}:{key[1]}": value for key, value in sorted(old_metadata.items())}
    v2_metadata_json = {f"{key[0]}:{key[1]}": value for key, value in sorted(v2_metadata.items())}
    return {
        "classification": "M21_PREPROVIDER_MANIFEST",
        "source_checkpoint": STARTING_COMMIT,
        "starting_commit": _git_head(),
        "m20_manifest_hash": json.loads(M20_MANIFEST.read_text())["manifest_hash"],
        "m20_forensics_hash": _sha_file(M20_FORENSICS),
        "dataset_hashes": {
            "bird": _sha_file(bird_root / "mini_dev_postgresql.json"),
            "defog_classic": _sha_file(sql_eval / "data/questions_gen_postgres.csv"),
            "defog_advanced": _sha_file(sql_eval / "data/instruct_advanced_postgres.csv"),
        },
        "datasets": {key: len(value) for key, value in datasets.items()},
        "sets": {
            "dev": [_case_spec(row) for row in dev],
            "target_holdout": [_case_spec(row) for row in holdout],
            "control": [_case_spec(row) for row in control],
        },
        "provider": {
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "temperature": "provider_default",
            "reasoning_effort": settings.llm_reasoning_effort,
            "timeout_seconds": settings.llm_timeout_seconds,
        },
        "generation_instructions": "M20 direct generation unchanged",
        "renderer": {
            "v1": {
                key[0] + ":" + key[1]: old_metadata[key].get("schema_context_sha256")
                for key in sorted(old_metadata)
            },
            "v2": {
                key[0] + ":" + key[1]: value["sha256"] for key, value in sorted(v2_metadata.items())
            },
            "v2_function": "app.retrieval.context.serialize_schema_context_v2",
        },
        "m1": {
            "max_plan_rows": settings.max_plan_rows,
            "max_plan_cost": settings.max_plan_cost,
            "max_result_rows": settings.max_result_rows,
            "statement_timeout_ms": settings.statement_timeout_ms,
        },
        "modes": {
            "memory": "OFF",
            "retrieval": "OFF",
            "queryplan": "DISABLED",
            "window_ir": "DISABLED",
            "governed": "DISABLED",
            "repair": 0,
            "judge": 0,
            "best_of_n": False,
        },
        "call_budget": len(dev) + len(holdout) + len(control),
        "planned_provider_calls": len(dev) + len(holdout) + len(control),
        "old_context_metadata": old_metadata_json,
        "new_context_metadata": v2_metadata_json,
    }


def _write_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(manifest)
    manifest["manifest_hash"] = _sha(manifest)
    M21_MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def preflight() -> dict[str, Any]:
    if not _git_clean():
        raise M20PreflightFailure("M21 requires the clean starting checkpoint")
    sql_eval, bird_root, _ = _dataset_roots()
    datasets = _load_all(sql_eval, bird_root)
    forensic = json.loads(M20_FORENSICS.read_text())
    m20_rows = [json.loads(line) for line in M20_CASES.read_text().splitlines() if line.strip()]
    if forensic["accounting"]["semantic_mismatch"] != 189:
        raise M20PreflightFailure("M20.2 frozen accounting is not 189")
    old_contexts: dict[tuple[str, str], str] = {}
    old_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    from evaluation.m20_full_rebaseline import _schema_contexts

    old_contexts, old_metadata = _schema_contexts(datasets)
    v2_contexts: dict[tuple[str, str], str] = {}
    v2_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    keys = sorted(old_contexts)
    for key in keys:
        v2_contexts[key], v2_metadata[key] = _schema_context_v2(key[0], key[1], bird_root)
    dev, holdout, control = _select_sets(datasets, forensic, m20_rows)
    selected = dev + holdout + control
    selected_keys = {(row["benchmark"], str(row["case_id"])) for row in selected}
    if len(selected_keys) != len(selected):
        raise M20PreflightFailure("M21 sets overlap")
    questions = _question_maps(datasets)
    for row in selected:
        key = (row["benchmark"], str(row["case_id"]))
        if key not in questions:
            raise M20PreflightFailure(f"M21 question missing: {key}")
        context_text = v2_contexts[
            ("bird" if row["benchmark"] == "bird" else "defog", row["database"])
        ]
        if any(token in context_text.lower() for token in ("gold_sql", "gold_result", "case_id")):
            raise M20PreflightFailure("schema renderer contains forbidden evaluation metadata")
    settings = _settings()
    if not settings.llm_api_key:
        raise M20PreflightFailure("provider credential is not configured")
    manifest = _manifest(
        datasets,
        old_contexts,
        old_metadata,
        v2_contexts,
        v2_metadata,
        dev,
        holdout,
        control,
        settings,
        sql_eval,
        bird_root,
    )
    return {
        "manifest": _write_manifest(manifest),
        "datasets": datasets,
        "old_contexts": old_contexts,
        "old_metadata": old_metadata,
        "v2_contexts": v2_contexts,
        "v2_metadata": v2_metadata,
        "dev": dev,
        "holdout": holdout,
        "control": control,
        "sql_eval": sql_eval,
        "bird_root": bird_root,
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))], 3)


async def _run_p1_case(
    provider: Any,
    safety: Any,
    question: Any,
    label: str,
    context: str,
    gold: Any,
    manifest: dict[str, Any],
    p0_correct: bool,
    p0_row: dict[str, Any],
    reference_sql: str | None,
) -> dict[str, Any]:
    kind = "bird" if label == "bird" else "defog"
    record = _new_record(question, label, kind, context, manifest)
    record["arm"] = "P1_SCHEMA_CONTEXT_V2"
    record["p0_correct"] = p0_correct
    record["p0_sql"] = p0_row.get("generated_sql")
    started = time.perf_counter()
    sql_started = time.perf_counter()
    sql, proposal, capture, provider_error = await _generate(provider, question, kind, context)
    record["provider_calls"] = 1
    record["latency_ms"]["generation"] = (time.perf_counter() - sql_started) * 1000
    if capture is not None:
        record.update(
            {
                "input_tokens": capture.usage.get("prompt_tokens"),
                "output_tokens": capture.usage.get("completion_tokens"),
                "reasoning_tokens": capture.usage.get("reasoning_tokens"),
                "provider_response_id": capture.provider_response_id,
                "finish_reason": capture.finish_reason,
            }
        )
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
    planned = safety.plan(
        SqlCandidate(sql=sql, source=CandidateSource.LLM, correlation_id=record["case_id"])
    )
    record["latency_ms"]["m1"] = (time.perf_counter() - m1_started) * 1000
    if isinstance(planned, SqlPlanFailure):
        record.update(
            {
                "m1_status": "REJECTED",
                "failure_stage": M20FailureStage.M1.value,
                "failure_code": planned.status.value,
            }
        )
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["m1_status"] = "ALLOWED"
    execution_started = time.perf_counter()
    execution = safety.execute(planned)
    record["latency_ms"]["execution"] = (time.perf_counter() - execution_started) * 1000
    if isinstance(execution, SqlExecutionError):
        record.update(
            {
                "execution_status": "FAILURE",
                "failure_stage": M20FailureStage.EXECUTION.value,
                "failure_code": "EXECUTION_FAILURE",
            }
        )
        record["latency_ms"]["total"] = (time.perf_counter() - started) * 1000
        return record
    record["execution_status"] = "SUCCESS"
    from evaluation.m20_full_rebaseline import _evaluate, _execution_result

    generated = _execution_result(execution)
    correct = _evaluate(kind, question, generated, gold, sql)
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
    p1_facts = _ast_facts(sql)
    record["p1_ast_facts"] = p1_facts
    if reference_sql is not None:
        reference_facts = _ast_facts(reference_sql)
        p0_facts = _ast_facts(p0_row.get("generated_sql") or "")
        record["mechanism"] = {
            "p0_relation_aligned": p0_facts.get("tables") == reference_facts.get("tables")
            and p0_facts.get("join_edges") == reference_facts.get("join_edges"),
            "p1_relation_aligned": p1_facts.get("tables") == reference_facts.get("tables")
            and p1_facts.get("join_edges") == reference_facts.get("join_edges"),
            "reference_tables": reference_facts.get("tables"),
            "p0_tables": p0_facts.get("tables"),
            "p1_tables": p1_facts.get("tables"),
        }
    return record


async def run_authoritative(state: dict[str, Any]) -> dict[str, Any]:
    manifest = state["manifest"]
    if _git_head() != manifest["starting_commit"] or not _git_clean_for_run():
        raise M20PreflightFailure("M21 manifest no longer matches clean HEAD")
    datasets = state["datasets"]
    selected = state["dev"] + state["holdout"] + state["control"]
    questions = _question_maps(datasets)
    p0 = _p0_map([json.loads(line) for line in M20_CASES.read_text().splitlines() if line.strip()])
    forensic = json.loads(M20_FORENSICS.read_text())
    forensic_by_key = {(row["benchmark"], str(row["case_id"])): row for row in forensic["cases"]}
    defog = DefogBenchmarkExecutor()
    bird = BirdBenchmarkExecutor()
    gold: dict[tuple[str, str], Any] = {}
    for row in selected:
        label = row["benchmark"]
        question = questions[(label, str(row["case_id"]))]
        if label == "bird":
            cache, _ = _bird_gold_preflight([question], bird)
            gold[(label, str(row["case_id"]))] = cache[str(question.index)]
        else:
            defog_cache, _ = _defog_gold_preflight([question], defog)
            gold[(label, str(row["case_id"]))] = defog_cache[f"{question.dataset}:{question.index}"]
    settings = _settings()
    engines: dict[tuple[str, str], tuple[Engine, Any]] = {}
    for row in selected:
        kind = "bird" if row["benchmark"] == "bird" else "defog"
        key = (kind, row["database"])
        if key not in engines:
            _, engine, safety = _settings_for_db(settings, kind, row["database"])
            engines[key] = (engine, safety)
    from app.generation.provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(settings)
    records: list[dict[str, Any]] = []
    selected.sort(key=lambda row: _sha(("M21_CALL_ORDER", row["benchmark"], row["case_id"])))
    for row in selected:
        label = row["benchmark"]
        question = questions[(label, str(row["case_id"]))]
        kind = "bird" if label == "bird" else "defog"
        key = (kind, row["database"])
        forensic_row = forensic_by_key.get((label, str(row["case_id"])))
        reference_sql = forensic_row.get("reference_sql") if forensic_row else None
        p0_row = p0.get((label, str(row["case_id"])), {"generated_sql": None})
        p0_correct = bool(p0_row.get("correct"))
        if label != "bird" and row in state["control"]:
            p0_correct = True
        records.append(
            await _run_p1_case(
                provider,
                engines[key][1],
                question,
                label,
                state["v2_contexts"][key],
                gold[(label, str(row["case_id"]))],
                manifest,
                p0_correct,
                p0_row,
                reference_sql,
            )
        )
    for engine, _ in engines.values():
        engine.dispose()

    def matrix(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "A": sum(r["p0_correct"] and r.get("correct") is True for r in rows),
            "B": sum(r["p0_correct"] and r.get("correct") is False for r in rows),
            "C": sum(not r["p0_correct"] and r.get("correct") is True for r in rows),
            "D": sum(not r["p0_correct"] and r.get("correct") is False for r in rows),
        }

    groups = {
        "dev": [
            r
            for r in records
            if any(
                r["case_id"] == str(x["case_id"]) and r["benchmark"] == x["benchmark"]
                for x in state["dev"]
            )
        ],
        "target_holdout": [
            r
            for r in records
            if any(
                r["case_id"] == str(x["case_id"]) and r["benchmark"] == x["benchmark"]
                for x in state["holdout"]
            )
        ],
        "control": [
            r
            for r in records
            if any(
                r["case_id"] == str(x["case_id"]) and r["benchmark"] == x["benchmark"]
                for x in state["control"]
            )
        ],
    }
    stages = Counter(r.get("failure_stage") for r in records if r.get("failure_stage"))
    mechanism = {
        "relation_table_divergence_resolved": sum(
            r.get("mechanism", {}).get("p1_relation_aligned") is True
            and r.get("mechanism", {}).get("p0_relation_aligned") is False
            for r in groups["target_holdout"]
        ),
        "relation_table_divergence_preserved": sum(
            r.get("mechanism", {}).get("p1_relation_aligned") is False
            and r.get("mechanism", {}).get("p0_relation_aligned") is False
            for r in groups["target_holdout"]
        ),
        "different_semantic_failure_introduced": sum(
            r.get("correct") is False and r.get("mechanism", {}).get("p1_relation_aligned") is True
            for r in groups["target_holdout"]
        ),
        "m1_reject_introduced": sum(
            r.get("m1_status") == "REJECTED" for r in groups["target_holdout"]
        ),
        "execution_failure_introduced": sum(
            r.get("execution_status") == "FAILURE" for r in groups["target_holdout"]
        ),
    }
    all_contexts = list(state["v2_metadata"].values())
    aggregate = {
        "classification": "M21_SCHEMA_GROUNDING_INTERVENTION_VALIDATED"
        if sum(r.get("correct") is True for r in groups["target_holdout"]) > 0
        and sum(r.get("correct") is False for r in groups["control"]) == 0
        and mechanism["relation_table_divergence_resolved"] > 0
        else "M21_SCHEMA_GROUNDING_HYPOTHESIS_NOT_SUPPORTED",
        "starting_commit": manifest["starting_commit"],
        "experiment_commit": _git_head(),
        "m20_manifest_hash": manifest["m20_manifest_hash"],
        "m21_manifest_hash": manifest["manifest_hash"],
        "provider_calls": sum(r["provider_calls"] for r in records),
        "provider_call_budget": manifest["planned_provider_calls"],
        "sets": {
            name: {
                "count": len(items),
                "paired": matrix(items),
                "p1_correct": sum(r.get("correct") is True for r in items),
                "p1_m1_rejected": sum(r.get("m1_status") == "REJECTED" for r in items),
                "p1_execution_failed": sum(r.get("execution_status") == "FAILURE" for r in items),
            }
            for name, items in groups.items()
        },
        "mechanism": mechanism,
        "failure_stages": dict(stages),
        "schema_context_cost": {
            "v1_chars": {
                key[0] + ":" + key[1]: state["old_metadata"][key].get("schema_context_chars")
                for key in sorted(state["old_metadata"])
            },
            "v2_chars": {
                key[0] + ":" + key[1]: value["chars"]
                for key, value in sorted(state["v2_metadata"].items())
            },
            "v2_token_estimate": {
                key[0] + ":" + key[1]: value["tokens_estimate"]
                for key, value in sorted(state["v2_metadata"].items())
            },
        },
        "latency_ms": {
            name: {
                "p50": _percentile(
                    [
                        float(r["latency_ms"]["total"])
                        for r in items
                        if r["latency_ms"]["total"] is not None
                    ],
                    0.5,
                ),
                "p95": _percentile(
                    [
                        float(r["latency_ms"]["total"])
                        for r in items
                        if r["latency_ms"]["total"] is not None
                    ],
                    0.95,
                ),
            }
            for name, items in (
                ("target_holdout", groups["target_holdout"]),
                ("control", groups["control"]),
            )
        },
        "token_usage": {
            name: {
                "input_total": sum(r["input_tokens"] or 0 for r in items) or None,
                "output_total": sum(r["output_tokens"] or 0 for r in items) or None,
            }
            for name, items in (
                ("target_holdout", groups["target_holdout"]),
                ("control", groups["control"]),
            )
        },
        "secondary_spillover": "not run",
        "cases": records,
        "context_metadata": all_contexts,
    }
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.preflight == args.run:
        parser.error("choose exactly one of --preflight or --run")
    try:
        state = preflight()
        if args.preflight:
            print(
                json.dumps(
                    {
                        "manifest_hash": state["manifest"]["manifest_hash"],
                        "dev": len(state["dev"]),
                        "holdout": len(state["holdout"]),
                        "control": len(state["control"]),
                        "provider_calls": 0,
                    },
                    indent=2,
                )
            )
            return
        result = asyncio.run(run_authoritative(state))
        M21_RESULT.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
        print(
            json.dumps(
                {
                    "classification": result["classification"],
                    "sets": result["sets"],
                    "provider_calls": result["provider_calls"],
                },
                indent=2,
                sort_keys=True,
            )
        )
    except M20PreflightFailure as error:
        print(
            json.dumps(
                {
                    "classification": "M21_VALIDATION_INVALID",
                    "provider_constructed": False,
                    "provider_calls": 0,
                    "error": str(error),
                },
                indent=2,
            )
        )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
