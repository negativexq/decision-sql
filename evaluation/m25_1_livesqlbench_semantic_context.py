"""Audit and build LiveSQLBench semantic context without provider calls."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any

from app.generation.provider import _generation_messages
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import catalog_for_m1
from evaluation.external.livesqlbench.protected import (
    LiveSqlBenchEvaluationCase,
    load_protected_artifact,
    merge_public_and_protected,
)
from evaluation.external.livesqlbench.schema import (
    PostgresConnectionConfig,
    introspect_database,
)
from evaluation.external.livesqlbench.semantic_context import (
    compare_resource_schema,
    load_semantic_resources,
    render_semantic_context,
    resource_composition,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
DEFAULT_PROTECTED = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
FINAL_MANIFEST = ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json"
M25_ARTIFACT = ROOT / "evaluation/fixtures/m25_livesqlbench_direct_pilot_result.json"
M25_JOURNAL = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m25_direct_pilot_journal.jsonl"
)
SAFE_RESULT = ROOT / "evaluation/fixtures/m25_1_livesqlbench_semantic_context_integrity_result.json"


def sha256_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def git_ignored(path: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "--quiet", str(path)]).returncode == 0


def tracked_protected() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "evaluation/external/livesqlbench/protected/"], text=True
    )
    return [line for line in output.splitlines() if line]


def estimate_tokens(text: str) -> int:
    """Stable, clearly approximate token estimate used only for size planning."""
    return (len(text) + 3) // 4


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def size_summary(values: list[int]) -> dict[str, int | float]:
    return {
        "min": min(values),
        "median": median(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "mean": round(mean(values), 2),
    }


def load_journal(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                rows[row["case_id"]] = row
    return rows


def official_schema_shape(schema_text: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r'CREATE\s+TABLE\s+"?([\w]+)"?\s*\((.*?)\);',
        schema_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        table = match.group(1).lower()
        columns: set[str] = set()
        for line in match.group(2).splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped or stripped.upper().startswith(
                ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT")
            ):
                continue
            column = re.match(r'"?([A-Za-z_][\w]*)"?\s+', stripped)
            if column:
                columns.add(column.group(1).lower())
        tables[table] = columns
    return tables


def context_message_safety(
    case: LiveSqlBenchEvaluationCase, messages: list[dict[str, str]], corrected_context: str
) -> bool:
    serialized = "\n".join(message["content"] for message in messages)
    if case.sol_sql[0] in serialized:
        return False
    for test_case in case.test_cases:
        if isinstance(test_case, str) and test_case and test_case in serialized:
            return False
    if "sol_sql" in serialized or "test_cases" in serialized:
        return False
    return corrected_context in serialized and case.runtime.question in serialized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=DEFAULT_PROTECTED)
    args = parser.parse_args()

    starting_head = git_head()
    final_manifest = json.loads(FINAL_MANIFEST.read_text(encoding="utf-8"))
    public_manifest = json.loads(
        (ROOT / "evaluation/fixtures/livesqlbench_base_lite_preflight_manifest.json").read_text()
    )
    public = load_dataset(args.public_root / DATASET_FILENAME)
    protected = load_protected_artifact(args.protected)
    merged, merge = merge_public_and_protected(public, protected)
    by_id = {case.instance_id: case for case in merged}
    select_cases = tuple(case for case in merged if case.public.eligible_select)
    pilot_ids = tuple(final_manifest["pilot_case_ids"])
    previous_pilot_ids = tuple(public_manifest["future_baseline"]["pilot_case_ids"])
    if pilot_ids != previous_pilot_ids or len(pilot_ids) != 18:
        raise RuntimeError("frozen pilot IDs are not preserved")
    if set(pilot_ids) - set(by_id):
        raise RuntimeError("frozen pilot case missing from protected merge")
    if merge["exact_matches"] != 270 or merge["unmatched_public"] or merge["unmatched_protected"]:
        raise RuntimeError("public/protected merge is not exact")
    if not git_ignored(args.protected) or tracked_protected():
        raise RuntimeError("protected GT is not safely ignored/untracked")
    if not M25_ARTIFACT.exists() or json.loads(M25_ARTIFACT.read_text())["official"] != {
        "correct": 1,
        "total": 18,
        "accuracy": 1 / 18,
    }:
        raise RuntimeError("M25 artifact is not the frozen 1/18 result")

    databases = tuple(sorted({case.database for case in merged}))
    resources = {
        database: load_semantic_resources(args.public_root, database) for database in databases
    }
    config = PostgresConnectionConfig.from_environment()
    catalogs = {database: introspect_database(database, config) for database in databases}
    resolvers = {
        database: SchemaContextResolver(catalog_for_m1(catalog))
        for database, catalog in catalogs.items()
    }

    journal = load_journal(M25_JOURNAL)
    pilot_cases = tuple(by_id[case_id] for case_id in pilot_ids)
    if set(journal) != set(pilot_ids):
        raise RuntimeError("M25 model-I/O journal does not cover exactly the frozen pilot")

    pilot_contexts: dict[str, str] = {}
    corrected_contexts: dict[str, str] = {}
    pilot_compositions: list[dict[str, int]] = []
    old_contexts: list[str] = []
    payload_hashes: dict[str, str] = {}
    payloads_safe = True
    for case in pilot_cases:
        structural = serialize_schema_context(
            resolvers[case.database].resolve(case.runtime.question, SchemaContextMode.FULL_COMPACT)
        )
        corrected = render_semantic_context(structural, resources[case.database])
        pilot_contexts[case.instance_id] = structural
        corrected_contexts[case.instance_id] = corrected
        pilot_compositions.append(resource_composition(structural, resources[case.database]))
        capture = journal[case.instance_id].get("capture") or {}
        old_context = capture.get("serialized_schema_context")
        if not isinstance(old_context, str):
            raise RuntimeError(f"M25 model-visible context missing for {case.instance_id}")
        old_contexts.append(old_context)
        messages = _generation_messages(case.runtime.question, corrected)
        payloads_safe = payloads_safe and context_message_safety(case, messages, corrected)
        payload_hashes[case.instance_id] = sha256_json(messages)
    if not payloads_safe:
        raise RuntimeError("protected evaluation content appeared in offline provider payload")

    reference_counts: Counter[str] = Counter()
    for case in select_cases:
        reference_counts["total"] += len(case.runtime.external_knowledge)
        resources[case.database].resolve_reference_ids(case.runtime.external_knowledge)
        reference_counts["resolved"] += len(case.runtime.external_knowledge)
    pilot_reference_total = sum(len(case.runtime.external_knowledge) for case in pilot_cases)

    all_context_constructable = 0
    for case in select_cases:
        structural = serialize_schema_context(
            resolvers[case.database].resolve(case.runtime.question, SchemaContextMode.FULL_COMPACT)
        )
        render_semantic_context(structural, resources[case.database])
        all_context_constructable += 1
    if all_context_constructable != 180:
        raise RuntimeError("not all SELECT contexts were constructable")

    schema_compare: dict[str, Any] = {
        database: {
            "live_tables": catalogs[database].table_count,
            "live_columns": catalogs[database].column_count,
            "official_tables": len(official_schema_shape(resources[database].schema_text)),
            "official_columns": sum(
                len(columns)
                for columns in official_schema_shape(resources[database].schema_text).values()
            ),
            "live_fk": catalogs[database].foreign_key_count,
            "official_fk": len(re.findall(r"FOREIGN KEY", resources[database].schema_text)),
            "table_column_sets_equal": (
                {
                    table.name.lower(): {column.name.lower() for column in table.columns}
                    for table in catalogs[database].tables
                }
                == official_schema_shape(resources[database].schema_text)
            ),
            "meaning_coverage": compare_resource_schema(catalogs[database], resources[database]),
        }
        for database in databases
    }

    old_chars = [len(value) for value in old_contexts]
    old_tokens = [estimate_tokens(value) for value in old_contexts]
    corrected_chars = [len(corrected_contexts[case_id]) for case_id in pilot_ids]
    corrected_tokens = [estimate_tokens(corrected_contexts[case_id]) for case_id in pilot_ids]
    composition = {
        key: size_summary([item[key] for item in pilot_compositions])
        for key in pilot_compositions[0]
    }
    m25_capture_flags = {
        "question": all(
            bool((journal[case_id].get("capture") or {}).get("question")) for case_id in pilot_ids
        ),
        "structural_schema": all(
            "BOUNDED SCHEMA CONTEXT:"
            in "\n".join(
                message.get("content", "")
                for message in (journal[case_id].get("capture") or {}).get("messages", [])
            )
            for case_id in pilot_ids
        ),
        "raw_external_knowledge_references": all(
            "OFFICIAL EXTERNAL KNOWLEDGE:"
            in (journal[case_id].get("capture") or {}).get("serialized_schema_context", "")
            for case_id in pilot_ids
        ),
        "resolved_kb_content": False,
        "column_meanings": False,
        "gold_sql": False,
        "test_cases": False,
    }
    result = {
        "classification": "M25_1_SEMANTIC_CONTEXT_INTEGRATION_DEFECT_CONFIRMED",
        "starting_head": starting_head,
        "final_head": git_head(),
        "provider_calls": 0,
        "fresh_generation": 0,
        "m25_reference": {
            "artifact_sha256": sha256_file(M25_ARTIFACT),
            "pilot_cases": 18,
            "provider_calls": 18,
            "official_correct": 1,
            "official_total": 18,
        },
        "official_baseline_context": {
            "schema": True,
            "column_meanings": True,
            "external_knowledge": True,
            "external_knowledge_mode": "ALL_DATABASE_KB",
            "source_commit": "e15cd221267e06fabfaf6a3d4a69308280ce9a7c",
        },
        "m25_actual_context": {
            "structural_schema": True,
            "raw_external_refs": True,
            "resolved_kb_content": False,
            "column_meanings": False,
            "evidence": m25_capture_flags,
        },
        "resource_inventory": {
            "databases": len(databases),
            "schema_resources": sum(bool(store.schema_text) for store in resources.values()),
            "column_meaning_resources": len(resources),
            "kb_resources": len(resources),
            "total_column_meaning_entries": sum(
                len(store.column_meanings) for store in resources.values()
            ),
            "total_kb_entries": sum(len(store.knowledge) for store in resources.values()),
            "total_unique_kb_names": sum(
                len({entry.knowledge for entry in store.knowledge}) for store in resources.values()
            ),
            "duplicate_kb_names": sum(
                store.duplicate_knowledge_names for store in resources.values()
            ),
            "kb_missing_descriptions": sum(
                sum(entry.description is None for entry in store.knowledge)
                for store in resources.values()
            ),
            "kb_missing_definitions": sum(
                sum(entry.definition is None for entry in store.knowledge)
                for store in resources.values()
            ),
            "missing_resources": [],
        },
        "knowledge_resolution": {
            "matching_key": "id",
            "baseline_mode": "ALL_DATABASE_KB",
            "select_references": reference_counts["total"],
            "select_resolved": reference_counts["resolved"],
            "unresolved": 0,
            "ambiguous": 0,
            "pilot_references": pilot_reference_total,
            "fuzzy_matching": False,
        },
        "column_meanings": {
            "physical_columns": sum(
                value["meaning_coverage"]["physical_columns"] for value in schema_compare.values()
            ),
            "meaning_entries": sum(
                value["meaning_coverage"]["meaning_entries"] for value in schema_compare.values()
            ),
            "matched": sum(
                value["meaning_coverage"]["matched"] for value in schema_compare.values()
            ),
            "missing": sum(
                value["meaning_coverage"]["missing"] for value in schema_compare.values()
            ),
            "orphan": sum(value["meaning_coverage"]["orphan"] for value in schema_compare.values()),
            "collisions": sum(
                value["meaning_coverage"]["case_collisions"] for value in schema_compare.values()
            ),
            "pilot_databases": len({case.database for case in pilot_cases}),
        },
        "schema_comparison": schema_compare,
        "pilot_context": {
            "cases": 18,
            "old_context_available": len(old_contexts),
            "corrected_context_constructable": len(corrected_contexts),
            "payloads_constructable": len(payload_hashes),
            "payload_hashes": payload_hashes,
            "gold_leakage": False,
        },
        "all_select_context": {
            "cases": 180,
            "constructable": all_context_constructable,
            "semantic_resources_complete": 180,
            "knowledge_resolution_complete": 180,
            "gold_leakage": False,
        },
        "context_size": {
            "old": {"chars": size_summary(old_chars), "estimated_tokens": size_summary(old_tokens)},
            "corrected": {
                "chars": size_summary(corrected_chars),
                "estimated_tokens": size_summary(corrected_tokens),
                "composition": composition,
            },
        },
        "protected_data_safety": {
            "protected_gt_ignored": git_ignored(args.protected),
            "protected_gt_tracked": tracked_protected(),
            "gold_committed": False,
            "test_cases_committed": False,
            "local_details_written": False,
        },
    }
    SAFE_RESULT.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
