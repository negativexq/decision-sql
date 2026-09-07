"""M26 query-aware grounding preflight and frozen DIRECT pilot.

The preflight is provider-free.  Only ``--run-pilot`` constructs the existing
provider and permits one request for each already frozen pilot case.
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
from statistics import median
from typing import Any, cast

import sqlglot
from sqlalchemy import Engine

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider, _generation_messages
from app.grounding.query_aware import (
    GroundingContext,
    GroundingKnowledge,
    QueryAwareGrounder,
    render_grounding_semantic_addition,
    to_schema_context,
)
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import catalog_for_m1, safety_for_database
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
from evaluation.external.livesqlbench.semantic_context import load_semantic_resources
from evaluation.m25_1_livesqlbench_semantic_context import estimate_tokens
from evaluation.m25_2_livesqlbench_direct_semantic_context import (
    JournalProvider,
    _reference_result,
    _safe_record,
    _sql_shape,
)
from evaluation.m25_livesqlbench_direct_pilot import direct_evaluation_service, sha256_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED_PATH = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
FINAL_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
M25_ARTIFACT = ROOT / "evaluation/fixtures/m25_2_livesqlbench_direct_semantic_context_result.json"
PREflight_ARTIFACT = ROOT / "evaluation/fixtures/m26_query_aware_grounding_preflight_result.json"
PILOT_ARTIFACT = ROOT / "evaluation/fixtures/m26_livesqlbench_query_aware_grounding_result.json"
RESULTS_ROOT = ROOT / "evaluation/external/livesqlbench/protected/results"
JOURNAL = RESULTS_ROOT / "m26_query_aware_grounding_journal.jsonl"
DETAILS = RESULTS_ROOT / "m26_query_aware_grounding_cases.jsonl"
EXPECTED_PROMPT_HASH = "79430f8df0bbc66eaf6cfcff73b046af6b7330c85bbd6e938390d573ac82cf42"
EXPECTED_MODEL = "gpt-5.6-luna"
PILOT_SIZE = 18
SELECT_SIZE = 180


class M26PreflightError(RuntimeError):
    """Raised when a provider-free M26 invariant fails."""


class FrozenResolver:
    """Schema resolver containing one precomputed grounding context."""

    def __init__(self, schema: Any) -> None:
        self.schema = schema

    def resolve(
        self, question: str, mode: SchemaContextMode = SchemaContextMode.FULL_COMPACT
    ) -> Any:
        del question, mode
        return self.schema


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_ignored(path: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "--quiet", str(path)]).returncode == 0


def tracked_protected() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "evaluation/external/livesqlbench/protected/"], text=True
    )
    return [line for line in output.splitlines() if line]


def pilot_ids() -> tuple[str, ...]:
    manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    ids = tuple(manifest["pilot_case_ids"])
    if len(ids) != PILOT_SIZE or len(set(ids)) != PILOT_SIZE:
        raise M26PreflightError("frozen pilot IDs are not exactly 18 unique cases")
    return ids


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise M26PreflightError(f"non-object JSONL row: {path}")
                rows.append(value)
    return rows


def _knowledge_store(resource: Any) -> tuple[GroundingKnowledge, ...]:
    return tuple(
        GroundingKnowledge(
            identifier=str(entry.id),
            name=entry.knowledge,
            description=entry.description,
            definition=entry.definition,
        )
        for entry in resource.knowledge
    )


def _schema_and_context(
    question: str,
    catalog: Any,
    resource: Any,
    engine: Engine,
) -> tuple[GroundingContext, Any, str, str]:
    grounder = QueryAwareGrounder(
        catalog,
        column_meanings=dict(resource.column_meanings),
        knowledge=_knowledge_store(resource),
    )
    grounded = grounder.ground(question, engine)
    schema = to_schema_context(grounded)
    structural = serialize_schema_context(schema)
    semantic = render_grounding_semantic_addition(grounded)
    full_context = f"{structural}\n\n{semantic}"
    return grounded, schema, semantic, full_context


def _physical_tables(tree: Any, catalog: Any) -> set[str]:
    known = {table.name.casefold() for table in catalog.tables}
    return {
        table.name.casefold()
        for table in tree.find_all(sqlglot.exp.Table)
        if table.name.casefold() in known
    }


def _gold_columns(tree: Any, catalog: Any) -> set[str]:
    tables = {table.name.casefold(): table for table in catalog.tables}
    aliases: dict[str, str] = {}
    for table in tree.find_all(sqlglot.exp.Table):
        if table.name.casefold() in tables:
            aliases[table.name.casefold()] = table.name.casefold()
            if table.alias:
                aliases[table.alias.casefold()] = table.name.casefold()
    result: set[str] = set()
    for column in tree.find_all(sqlglot.exp.Column):
        qualifier = column.table.casefold() if column.table else None
        if qualifier:
            owner = aliases.get(qualifier)
            if owner is None:
                continue
            if tables[owner].get_column(column.name) is not None:
                result.add(f"{owner}.{column.name.casefold()}")
            continue
        owners = [
            table.name.casefold()
            for table in catalog.tables
            if table.get_column(column.name) is not None
        ]
        if len(owners) == 1:
            result.add(f"{owners[0]}.{column.name.casefold()}")
    return result


def _reference_recall(
    case: LiveSqlBenchEvaluationCase, grounded: GroundingContext, catalog: Any
) -> dict[str, float | int]:
    try:
        tree = sqlglot.parse_one(case.sol_sql[0], read="postgres")
    except sqlglot.errors.ParseError:
        return {"gold_tables": 0, "covered_tables": 0, "gold_columns": 0, "covered_columns": 0}
    selected_tables = {table.name.casefold() for table in grounded.selected_tables}
    selected_columns = {
        f"{table.casefold()}.{column.name.casefold()}"
        for table, column in grounded.selected_columns
    }
    gold_tables = _physical_tables(tree, catalog)
    gold_columns = _gold_columns(tree, catalog)
    covered_tables = len(gold_tables & selected_tables)
    covered_columns = len(gold_columns & selected_columns)
    return {
        "gold_tables": len(gold_tables),
        "covered_tables": covered_tables,
        "gold_columns": len(gold_columns),
        "covered_columns": covered_columns,
    }


def _expected_for_case(case: LiveSqlBenchEvaluationCase, safety: Any) -> LiveSqlBenchResult:
    return _reference_result(case, safety)


def _payload_safe(
    case: LiveSqlBenchEvaluationCase, messages: list[dict[str, str]], context: str
) -> bool:
    serialized = "\n".join(message["content"] for message in messages)
    if case.runtime.question not in serialized or context not in serialized:
        return False
    if any(sql and sql in serialized for sql in case.sol_sql):
        return False
    for forbidden in ("test_cases", "reference result", "evaluator result", "M25.2"):
        if forbidden.casefold() in serialized.casefold():
            return False
    return True


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _size(values: list[float]) -> dict[str, float | int | None]:
    return {
        "median": median(values) if values else None,
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
        "total": sum(values),
    }


def _build_preflight(public_root: Path, protected_path: Path) -> dict[str, Any]:
    if not public_root.is_dir():
        raise M26PreflightError(f"LiveSQLBench root unavailable: {public_root}")
    if not protected_path.is_file() or not git_ignored(protected_path) or tracked_protected():
        raise M26PreflightError("protected GT is not safely ignored and untracked")
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
    merged, merge = merge_public_and_protected(public, protected)
    if merge["exact_matches"] != 270 or merge["unmatched_public"] or merge["unmatched_protected"]:
        raise M26PreflightError(f"public/protected merge is not exact: {merge}")
    ids = pilot_ids()
    final_manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    m25 = json.loads(M25_ARTIFACT.read_text(encoding="utf-8"))
    if m25["official"] != {"correct": 6, "total": 18, "accuracy": 1 / 3}:
        raise M26PreflightError("M25.2 reference artifact is not the frozen 6/18 arm")
    if tuple(final_manifest["pilot_case_ids"]) != ids:
        raise M26PreflightError("pilot IDs are not frozen")
    by_id = {case.instance_id: case for case in merged}
    pilot = [by_id[case_id] for case_id in ids]
    select = [case for case in merged if case.public.category == "Query"]
    if len(select) != SELECT_SIZE:
        raise M26PreflightError(f"expected 180 SELECT cases, got {len(select)}")

    config = PostgresConnectionConfig.from_environment()
    states: dict[str, tuple[LiveSqlBenchDatabase, Engine, Any, Any]] = {}
    resources: dict[str, Any] = {}
    contexts: dict[str, GroundingContext] = {}
    schemas: dict[str, Any] = {}
    additions: dict[str, str] = {}
    full_contexts: dict[str, str] = {}
    recall: dict[str, dict[str, float | int]] = {}
    all_cases: list[LiveSqlBenchEvaluationCase] = []
    context_sizes: list[int] = []
    token_sizes: list[int] = []
    value_cases = 0
    for case in select:
        if case.database not in states:
            database = introspect_database(case.database, config)
            engine, safety, _ = safety_for_database(database, config)
            states[case.database] = (database, engine, safety, catalog_for_m1(database))
            resources[case.database] = load_semantic_resources(public_root, case.database)
        database, engine, _, catalog = states[case.database]
        del database
        grounded, schema, semantic, full = _schema_and_context(
            case.runtime.question, catalog, resources[case.database], engine
        )
        contexts[case.instance_id] = grounded
        schemas[case.instance_id] = schema
        additions[case.instance_id] = semantic
        full_contexts[case.instance_id] = full
        recall[case.instance_id] = _reference_recall(case, grounded, catalog)
        context_sizes.append(len(full))
        token_sizes.append(estimate_tokens(full))
        value_cases += int(bool(grounded.values))
        all_cases.append(case)

    pilot_recall = [recall[case_id] for case_id in ids]
    table_targets = sum(item["gold_tables"] for item in pilot_recall)
    table_hits = sum(item["covered_tables"] for item in pilot_recall)
    pilot_table_case_coverage = sum(
        item["gold_tables"] > 0 and item["covered_tables"] == item["gold_tables"]
        for item in pilot_recall
    )
    if pilot_table_case_coverage < 18 or (table_targets and table_hits / table_targets < 0.95):
        raise M26PreflightError(
            "frozen-pilot table recall gate failed: "
            f"full_cases={pilot_table_case_coverage}/18, tables={table_hits}/{table_targets}"
        )

    payload_hashes: dict[str, str] = {}
    for case_id in ids:
        case = by_id[case_id]
        messages = _generation_messages(case.runtime.question, full_contexts[case_id])
        if not _payload_safe(case, messages, full_contexts[case_id]):
            raise M26PreflightError(f"provider payload leakage for {case_id}")
        payload_hashes[case_id] = sha256_json(messages)
        if sha256_json(messages) != sha256_json(
            _generation_messages(case.runtime.question, full_contexts[case_id])
        ):
            raise M26PreflightError(f"non-deterministic provider payload for {case_id}")

    prompt_hash = sha256_text(inspect.getsource(_generation_messages))
    if prompt_hash != EXPECTED_PROMPT_HASH:
        raise M26PreflightError(f"production prompt hash changed: {prompt_hash}")
    settings = get_settings().model_copy(update={"llm_model": EXPECTED_MODEL})
    if settings.llm_model != m25["configuration"]["model"]:
        raise M26PreflightError("M25.2 model differs from current model")
    if settings.llm_temperature != m25["configuration"]["temperature"]:
        raise M26PreflightError("M25.2 temperature differs from current settings")
    if settings.llm_reasoning_effort != m25["configuration"]["reasoning_effort"]:
        raise M26PreflightError("M25.2 reasoning effort differs from current settings")

    return {
        "public": public,
        "protected": protected,
        "merged": merged,
        "pilot": pilot,
        "select": select,
        "states": states,
        "resources": resources,
        "contexts": contexts,
        "schemas": schemas,
        "additions": additions,
        "full_contexts": full_contexts,
        "recall": recall,
        "payload_hashes": payload_hashes,
        "settings": settings,
        "prompt_hash": prompt_hash,
        "manifest_hash": final_manifest["manifest_hash"],
        "protected_hash": protected.sha256,
        "context_sizes": context_sizes,
        "token_sizes": token_sizes,
        "value_cases": value_cases,
        "merge": merge,
        "table_hits": table_hits,
        "table_targets": table_targets,
        "pilot_table_case_coverage": pilot_table_case_coverage,
    }


def _safe_preflight_artifact(preflight: dict[str, Any]) -> dict[str, Any]:
    recall = list(preflight["recall"].values())
    pilot_recall = [preflight["recall"][case.instance_id] for case in preflight["pilot"]]
    pilot_knowledge_total = 0
    pilot_knowledge_hit = 0
    for case in preflight["pilot"]:
        references = {
            str(value) for value in case.runtime.external_knowledge if isinstance(value, int)
        }
        available = {
            entry.identifier for entry in preflight["contexts"][case.instance_id].knowledge
        }
        pilot_knowledge_total += len(references)
        pilot_knowledge_hit += len(references & available)
    selected_tables = [len(item.selected_tables) for item in preflight["contexts"].values()]
    selected_columns = [len(item.selected_columns) for item in preflight["contexts"].values()]
    selected_kb = [len(item.knowledge) for item in preflight["contexts"].values()]
    return {
        "classification": "M26_QUERY_AWARE_GROUNDING_READY",
        "provider_calls": 0,
        "grounding": {
            "table_first": True,
            "column_first": True,
            "fk_closure": True,
            "query_aware_kb": True,
            "database_value_linking": True,
        },
        "population": {"select_cases": len(preflight["select"]), "databases": 18},
        "contexts": {
            "constructed": len(preflight["contexts"]),
            "pilot_constructed": sum(
                case.instance_id in preflight["contexts"] for case in preflight["pilot"]
            ),
            "value_evidence_cases": preflight["value_cases"],
            "pilot_value_evidence_cases": sum(
                bool(preflight["contexts"][case.instance_id].values) for case in preflight["pilot"]
            ),
            "selected_tables": _size([float(value) for value in selected_tables]),
            "selected_columns": _size([float(value) for value in selected_columns]),
            "selected_kb": _size([float(value) for value in selected_kb]),
        },
        "retrieval_quality": {
            "pilot_full_table_coverage": preflight["pilot_table_case_coverage"],
            "pilot_table_recall": preflight["table_hits"] / preflight["table_targets"]
            if preflight["table_targets"]
            else None,
            "pilot_column_recall": (
                sum(item["covered_columns"] for item in pilot_recall)
                / sum(item["gold_columns"] for item in pilot_recall)
                if sum(item["gold_columns"] for item in pilot_recall)
                else None
            ),
            "pilot_required_kb_recall": (
                pilot_knowledge_hit / pilot_knowledge_total if pilot_knowledge_total else None
            ),
            "all_gold_table_recall": (
                sum(item["covered_tables"] for item in recall)
                / sum(item["gold_tables"] for item in recall)
                if sum(item["gold_tables"] for item in recall)
                else None
            ),
            "all_gold_column_recall": (
                sum(item["covered_columns"] for item in recall)
                / sum(item["gold_columns"] for item in recall)
                if sum(item["gold_columns"] for item in recall)
                else None
            ),
        },
        "context_size": {
            "chars": _size([float(value) for value in preflight["context_sizes"]]),
            "estimated_tokens": _size([float(value) for value in preflight["token_sizes"]]),
        },
        "configuration": {
            "prompt_hash": preflight["prompt_hash"],
            "manifest_hash": preflight["manifest_hash"],
            "protected_sha256": preflight["protected_hash"],
            "raw_protected_refs_used_for_filtering": False,
        },
        "protected_data_safety": {
            "ignored": git_ignored(PROTECTED_PATH),
            "tracked": tracked_protected(),
            "gold_committed": False,
            "local_details_tracked": False,
        },
    }


async def _run_pilot(preflight: dict[str, Any], *, resume: bool) -> dict[str, Any]:
    journal_rows = _read_jsonl(JOURNAL)
    journal = {str(row["case_id"]): row for row in journal_rows}
    detail_rows = _read_jsonl(DETAILS)
    detail_by_id = {str(row["case_id"]): row.get("record", {}) for row in detail_rows}
    ids = tuple(case.instance_id for case in preflight["pilot"])
    if len(journal) and not resume:
        raise M26PreflightError("M26 journal exists; use --resume to reuse completed calls")
    if set(journal) - set(ids) or any(
        row.get("actual_request_count") != 1 for row in journal.values()
    ):
        raise M26PreflightError("M26 journal violates the one-call contract")
    provider = JournalProvider(OpenAICompatibleProvider(preflight["settings"]), JOURNAL)
    records: list[dict[str, Any]] = []
    for case in preflight["pilot"]:
        existing = journal.get(case.instance_id)
        expected = _expected_for_case(case, preflight["states"][case.database][2])
        if existing is not None:
            result = None
            if existing.get("provider_status") == "PROVIDER_SUCCESS" and existing.get("sql"):
                candidate = existing["sql"]
                service = direct_evaluation_service(
                    cast(
                        SchemaContextResolver,
                        FrozenResolver(preflight["schemas"][case.instance_id]),
                    ),
                    provider,
                    preflight["states"][case.database][2],
                    preflight["settings"],
                )
                from evaluation.m25_livesqlbench_direct_pilot import _process_existing_sql

                processed = _process_existing_sql(
                    candidate,
                    preflight["states"][case.database][2],
                    expected,
                    bool(case.public.conditions.get("order", False)),
                )
                record = {
                    "case_id": case.instance_id,
                    "database": case.database,
                    "provider_status": existing["provider_status"],
                    "provider_calls": 1,
                    "protocol_status": "SUCCESS",
                    "sql_produced": True,
                    "m1_status": processed["m1_status"],
                    "m1_failure_code": processed["m1_failure_code"],
                    "execution_status": processed["execution_status"],
                    "official_evaluator_status": processed["official_status"],
                    "correct": processed["correct"],
                    "reference_empty": not expected.rows,
                    "sql_hash": sha256_text(candidate),
                    "sql_shape": _sql_shape(candidate),
                    "usage": existing.get("usage", {}),
                    "provider_latency_ms": existing.get("wall_latency_ms"),
                }
                old_detail = detail_by_id.get(case.instance_id, {})
                record["end_to_end_latency_ms"] = old_detail.get("end_to_end_latency_ms")
                record["reference_annotation"] = old_detail.get("reference_annotation")
                del result, service
            else:
                record = _safe_record(
                    case,
                    None,
                    existing,
                    expected,
                    sha256_text(preflight["full_contexts"][case.instance_id]),
                    None,
                )
            records.append(record)
            continue
        if len(journal) >= PILOT_SIZE:
            raise M26PreflightError("M26 provider call budget exhausted")
        service = direct_evaluation_service(
            cast(SchemaContextResolver, FrozenResolver(preflight["schemas"][case.instance_id])),
            provider,
            preflight["states"][case.database][2],
            preflight["settings"],
        )
        provider.case_id = case.instance_id
        provider.expected_context_hash = sha256_text(
            f"{serialize_schema_context(preflight['schemas'][case.instance_id])}\n\n"
            f"{preflight['additions'][case.instance_id]}"
        )
        started = time.perf_counter()
        try:
            result = await service.run_with_context_addition(
                TextToSqlRequest(
                    question=case.runtime.question,
                    correlation_id=f"m26:{case.instance_id}",
                    execute=True,
                ),
                preflight["additions"][case.instance_id],
            )
        except Exception:
            result = None
        journal = {str(row["case_id"]): row for row in _read_jsonl(JOURNAL)}
        row = journal[case.instance_id]
        record = _safe_record(
            case,
            result,
            row,
            expected,
            sha256_text(preflight["full_contexts"][case.instance_id]),
            started,
        )
        records.append(record)
        RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
        with DETAILS.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"case_id": case.instance_id, "record": record}, ensure_ascii=False, default=str
                )
                + "\n"
            )
            stream.flush()
    calls = sum(int(record.get("provider_calls", 0)) for record in records)
    if calls != PILOT_SIZE or any(int(record.get("provider_calls", 0)) > 1 for record in records):
        raise M26PreflightError("M26 did not satisfy exactly one call per pilot case")
    accepted = [record for record in records if record["m1_status"] == "ACCEPTED"]
    official_correct = sum(bool(record["correct"]) for record in records)
    old_correct = 6
    old_cases = _read_jsonl(RESULTS_ROOT / "m25_2_direct_semantic_context_cases.jsonl")
    old_by_id = {str(row["case_id"]): row.get("m26", row.get("m25_2", row)) for row in old_cases}
    transitions: Counter[str] = Counter()
    for record in records:
        old = bool(old_by_id.get(record["case_id"], {}).get("correct"))
        new = bool(record["correct"])
        transitions[f"{old}:{new}"] += 1
    b, c = transitions["True:False"], transitions["False:True"]
    p = (
        None
        if b + c == 0
        else min(1.0, 2 * sum(math.comb(b + c, i) for i in range(min(b, c) + 1)) / 2 ** (b + c))
    )
    usage_fields = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
    usage = {
        field: _size(
            [
                float(record["usage"][field])
                for record in records
                if record.get("usage", {}).get(field) is not None
            ]
        )
        for field in usage_fields
    }
    provider_ms = [
        float(record["provider_latency_ms"])
        for record in records
        if record.get("provider_latency_ms") is not None
    ]
    e2e_ms = [
        float(record["end_to_end_latency_ms"])
        for record in records
        if record.get("end_to_end_latency_ms") is not None
    ]
    shapes: Counter[str] = Counter()
    for record in records:
        shape = record.get("sql_shape", {})
        if not shape.get("parseable"):
            continue
        shapes[shape.get("join_bucket", "unknown") + "_join"] += 1
        for field in (
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
            shapes[field] += int(bool(shape.get(field)))
    return {
        "classification": "M26_QUERY_AWARE_GROUNDING_PILOT_COMPLETED",
        "experiment": {
            "cases": 18,
            "provider_calls": calls,
            "calls_per_case_max": 1,
            "intervention": "QUERY_AWARE_GROUNDING",
        },
        "old_arm": {"correct": old_correct, "total": 18},
        "m26": {"correct": official_correct, "total": 18, "accuracy": official_correct / 18},
        "grounding": {
            "table_first": True,
            "column_first": True,
            "fk_closure": True,
            "query_aware_kb": True,
            "database_value_linking": True,
            "raw_protected_refs_used_for_filtering": False,
            "pilot_table_recall": preflight["table_hits"] / preflight["table_targets"],
            "all_select_table_recall": _safe_preflight_artifact(preflight)["retrieval_quality"][
                "all_gold_table_recall"
            ],
            "all_select_column_recall": _safe_preflight_artifact(preflight)["retrieval_quality"][
                "all_gold_column_recall"
            ],
            "value_evidence_cases": preflight["value_cases"],
        },
        "context": {
            "m25_2_median_input_tokens": 13212,
            "m25_2_total_input_tokens": 239429,
            "m26_estimated_tokens": _size([float(value) for value in preflight["token_sizes"]]),
        },
        "paired": {
            "old_correct_new_correct": transitions["True:True"],
            "old_correct_new_wrong": b,
            "old_wrong_new_correct": c,
            "old_wrong_new_wrong": transitions["False:False"],
            "net_correct_delta": c - b,
            "mcnemar_exact_p": p,
        },
        "pipeline": {
            "provider_success": sum(
                record["provider_status"] == "PROVIDER_SUCCESS" for record in records
            ),
            "protocol_success": sum(record["protocol_status"] == "SUCCESS" for record in records),
            "sql_produced": sum(bool(record.get("sql_hash")) for record in records),
            "m1_accepted": len(accepted),
            "m1_rejected": sum(record["m1_status"] == "REJECTED" for record in records),
            "execution_attempted": len(accepted),
            "execution_success": sum(record["execution_status"] == "SUCCESS" for record in records),
            "execution_failure": sum(record["execution_status"] == "FAILURE" for record in records),
            "timeout": 0,
        },
        "m1_rejections": dict(
            sorted(
                Counter(
                    record["m1_failure_code"] for record in records if record["m1_failure_code"]
                ).items()
            )
        ),
        "sql_shapes": dict(sorted(shapes.items())),
        "usage": usage,
        "latency": {"provider_ms": _size(provider_ms), "end_to_end_ms": _size(e2e_ms)},
        "official": {"correct": official_correct, "total": 18, "accuracy": official_correct / 18},
        "reference_valid_diagnostic": {
            "correct": sum(
                bool(record["correct"]) for record in records if not record["reference_empty"]
            ),
            "total": sum(not record["reference_empty"] for record in records),
            "label": "DIAGNOSTIC ONLY",
        },
        "protected_data_safety": {
            "ignored": git_ignored(PROTECTED_PATH),
            "tracked": tracked_protected(),
            "gold_committed": False,
            "details_tracked": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=PROTECTED_PATH)
    parser.add_argument("--run-pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    preflight: dict[str, Any] | None = None
    try:
        preflight = _build_preflight(args.public_root, args.protected)
        safe = _safe_preflight_artifact(preflight)
        PREflight_ARTIFACT.write_text(
            json.dumps(safe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(json.dumps(safe, indent=2, ensure_ascii=False))
        if args.run_pilot:
            result = asyncio.run(_run_pilot(preflight, resume=args.resume))
            PILOT_ARTIFACT.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except M26PreflightError as error:
        print(
            json.dumps({"classification": "M26_QUERY_AWARE_GROUNDING_BLOCKED", "error": str(error)})
        )
        raise SystemExit(2) from error
    finally:
        if preflight is not None:
            for _, engine, _, _ in preflight["states"].values():
                engine.dispose()


if __name__ == "__main__":
    main()
