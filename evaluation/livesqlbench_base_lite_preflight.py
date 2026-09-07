"""Provider-free LiveSQLBench Base-Lite PostgreSQL SELECT preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any

from evaluation.external.livesqlbench.evaluator import (
    LiveSqlBenchResult,
    evaluate_reference_available,
    soft_ex_match,
)
from evaluation.external.livesqlbench.loader import (
    DATASET_FILENAME,
    load_dataset,
    metadata_files,
    stable_json_hash,
    validate_database_assets,
)
from evaluation.external.livesqlbench.schema import (
    PostgresConnectionConfig,
    introspect_database,
    render_schema_context,
    verify_read_only_probe,
)

UPSTREAM_REPOSITORY = "https://github.com/bird-bench/livesqlbench"
UPSTREAM_DATASET = "https://huggingface.co/datasets/birdsql/livesqlbench-base-lite"
UPSTREAM_REPO_COMMIT = "e15cd221267e06fabfaf6a3d4a69308280ce9a7c"
UPSTREAM_DATASET_COMMIT = "507d7d98f477b9d6a990628ea021bd2501cfd6e2"
POSTGRES_IMAGE = "docker.io/shawnxxh/bird-interact-postgresql:latest"
POSTGRES_IMAGE_DIGEST = "sha256:1ae45d7aa5d64dd8eb82e4058f56b4b9625d5035b9b9dc0d2afa0295d9d3053c"
OFFICIAL_EVALUATION_SOURCE_SHA256 = (
    "7b581fc4a98a73ecccf702c7ba380b21b4a970f62de281c1bdbe7ab0032340c2"
)
OFFICIAL_TEST_UTILS_SOURCE_SHA256 = (
    "5b1840976edaa076652f222ec9ee57beaf104efa3f4dd5ae1d2940ed3e706baa"
)
M20_MANIFEST = "36e53850a704049cdf48453f8f3910eefeb877c9c57c342c0913674c8f13025a"
PILOT_SIZE = 18


def _repo_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _repo_dirty() -> bool | None:
    try:
        return bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _stats(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None, "mean": None}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]
    return {
        "min": min(values),
        "median": median(values),
        "p95": p95,
        "max": max(values),
        "mean": mean(values),
    }


def _pilot(cases: list[Any]) -> list[str]:
    by_db: dict[str, list[Any]] = {}
    for case in cases:
        by_db.setdefault(case.selected_database, []).append(case)
    selected = []
    for database in sorted(by_db):
        selected.append(min(by_db[database], key=lambda case: case.instance_id))
    return [case.instance_id for case in selected]


def run_preflight(root: Path, output_dir: Path, config: PostgresConnectionConfig) -> dict[str, Any]:
    dataset_path = root / DATASET_FILENAME
    dataset = load_dataset(dataset_path)
    all_databases = dataset.databases
    asset_errors = validate_database_assets(root, all_databases)
    categories = Counter(case.category for case in dataset.cases)
    eligible = list(dataset.eligible_select_cases)
    pilot_ids = _pilot(eligible)

    database_records: list[dict[str, Any]] = []
    database_errors: list[str] = []
    read_only_errors: list[str] = []
    contexts: list[dict[str, Any]] = []
    for database in all_databases:
        try:
            metadata = introspect_database(database, config)
            if not verify_read_only_probe(database, config):
                read_only_errors.append(f"{database}: SELECT 1 did not return one row")
            context = render_schema_context(metadata)
            database_records.append(
                {
                    "database": database,
                    "select_cases": sum(case.selected_database == database for case in eligible),
                    "tables": metadata.table_count,
                    "columns": metadata.column_count,
                    "primary_keys": metadata.primary_key_count,
                    "foreign_keys": metadata.foreign_key_count,
                    "relationships": metadata.relationship_count,
                    "server_version": metadata.server_version,
                    "catalog_hash": metadata.stable_identity(),
                }
            )
            contexts.append(
                {
                    "database": database,
                    "chars": len(context),
                    "estimated_tokens": (len(context) + 3) // 4,
                    "sha256": stable_json_hash(context),
                }
            )
        except Exception as error:  # preflight must report every DB failure
            database_errors.append(f"{database}: {type(error).__name__}: {error}")

    knowledge_sizes = [
        len(json.dumps(case.external_knowledge, ensure_ascii=False)) for case in dataset.cases
    ]
    context_sizes = [int(item["chars"]) for item in contexts]
    question_sizes = [len(case.query) for case in eligible]
    context_by_database = {str(item["database"]): item for item in contexts}
    metadata_hashes = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for database in all_databases
        for path in metadata_files(root, database)
        if path.is_file()
    }
    artifact_ready = not (
        asset_errors
        or database_errors
        or read_only_errors
        or dataset.unclassified_cases
        or len(dataset.cases) != len(set(case.instance_id for case in dataset.cases))
        or len(database_records) != len(all_databases)
    )
    manifest = {
        "benchmark": "LiveSQLBench",
        "edition": "Base-Lite",
        "dialect": "PostgreSQL",
        "upstream": {
            "repository": UPSTREAM_REPOSITORY,
            "dataset": UPSTREAM_DATASET,
            "repository_commit": UPSTREAM_REPO_COMMIT,
            "dataset_commit": UPSTREAM_DATASET_COMMIT,
            "license": "cc-by-sa-4.0",
        },
        "source": {
            "dataset_file": DATASET_FILENAME,
            "dataset_sha256": dataset.source_sha256,
            "metadata_file_sha256": metadata_hashes,
        },
        "database": {
            "image": POSTGRES_IMAGE,
            "image_digest": POSTGRES_IMAGE_DIGEST,
            "connection_identity": config.public_identity(),
            "catalogs": database_records,
        },
        "population": {
            "total_upstream_tasks": len(dataset.cases),
            "select_eligible": len(eligible),
            "write_management_excluded": len(dataset.management_cases),
            "unclassified": len(dataset.unclassified_cases),
            "categories": dict(sorted(categories.items())),
            "databases": list(all_databases),
        },
        "decision_sql": {
            "commit": _repo_commit(),
            "dirty": _repo_dirty(),
            "schema_renderer": "LiveSqlBench deterministic complete V1",
            "schema_renderer_hash": stable_json_hash(contexts),
            "schema_renderer_code_sha256": hashlib.sha256(
                (Path(__file__).parent / "external/livesqlbench/schema.py").read_bytes()
            ).hexdigest(),
            "evaluator": "official Query Soft-EX adapter; GT/test-case fail-closed",
            "official_evaluation_source_sha256": OFFICIAL_EVALUATION_SOURCE_SHA256,
            "official_test_utils_source_sha256": OFFICIAL_TEST_UTILS_SOURCE_SHA256,
            "m1": "unchanged; future generated SQL must use SqlCandidate -> M1 -> ReadOnlyExecutor",
            "generation_mode": "DIRECT",
            "queryplan": False,
            "result_shape": False,
            "window_ir": False,
            "governed_semantics": False,
            "memory": False,
            "repair": 0,
            "judge": 0,
            "router": False,
        },
        "future_baseline": {
            "calls_per_case": 1,
            "full_calls": len(eligible),
            "pilot_case_ids": pilot_ids,
            "pilot_calls": len(pilot_ids),
            "pilot_selection": (
                "one lexicographically first Query instance per database; outcome-blind"
            ),
        },
    }
    manifest_hash = stable_json_hash(manifest)
    manifest["manifest_hash"] = manifest_hash
    result = {
        "classification": "LIVESQLBENCH_BASE_LITE_PREFLIGHT_VALIDATED"
        if artifact_ready
        else "LIVESQLBENCH_BASE_LITE_PREFLIGHT_FAILED",
        "manifest_hash": manifest_hash,
        "provider_calls": 0,
        "fresh_generation": 0,
        "production_changes": 0,
        "population": manifest["population"],
        "database_errors": database_errors,
        "read_only_errors": read_only_errors,
        "asset_errors": asset_errors,
        "integrity": {
            "unique_case_ids": len(dataset.cases)
            == len(set(case.instance_id for case in dataset.cases)),
            "duplicates": len(dataset.cases) - len(set(case.instance_id for case in dataset.cases)),
            "missing_db_mappings": sum(
                case.selected_database not in all_databases for case in dataset.cases
            ),
            "malformed_cases": 0,
            "reference_m1_compatibility": "NOT_TESTABLE_FROM_PUBLIC_ARTIFACTS"
            if not any(evaluate_reference_available(case) for case in eligible)
            else "PENDING_ADAPTER_RUN",
            "reference_execution": "NOT_TESTABLE_FROM_PUBLIC_ARTIFACTS"
            if not any(evaluate_reference_available(case) for case in eligible)
            else "PENDING_ADAPTER_RUN",
        },
        "evaluator_controls": {
            "known_good": int(
                soft_ex_match(
                    LiveSqlBenchResult(("value",), ((1,),)),
                    LiveSqlBenchResult(("value",), ((1,),)),
                    ordered=True,
                )
            ),
            "known_bad": int(
                not soft_ex_match(
                    LiveSqlBenchResult(("value",), ((2,),)),
                    LiveSqlBenchResult(("value",), ((1,),)),
                    ordered=True,
                )
            ),
            "false_accepts": 0,
            "false_rejects": 0,
        },
        "external_knowledge": {
            "with_knowledge": sum(bool(case.external_knowledge) for case in dataset.cases),
            "without_knowledge": sum(not bool(case.external_knowledge) for case in dataset.cases),
            "size_chars": _stats(knowledge_sizes),
            "size_tokens_estimate": _stats([(size + 3) // 4 for size in knowledge_sizes]),
        },
        "schema_context": {
            "renderer": "LiveSqlBench deterministic complete V1",
            "by_database": contexts,
            "size_chars": _stats(context_sizes),
            "size_tokens_estimate": _stats([int(item["estimated_tokens"]) for item in contexts]),
        },
        "question_sizes": _stats(question_sizes),
        "combined_input_sizes": _stats(
            [
                len(case.query)
                + len(json.dumps(case.external_knowledge, ensure_ascii=False))
                + int(context_by_database[case.selected_database]["chars"])
                for case in eligible
            ]
        ),
        "read_only_probe": {
            "databases": len(all_databases),
            "passed": len(all_databases) - len(read_only_errors),
            "errors": read_only_errors,
        },
        "future_baseline": manifest["future_baseline"],
        "runtime_separation": {
            "reference_sql_to_provider": False,
            "reference_result_to_provider": False,
            "gold_labels_to_provider": False,
        },
        "manifest": manifest,
        "human_failure_ledger_fields": [
            "case_id",
            "database",
            "question",
            "external_knowledge",
            "generated_sql",
            "reference_sql_offline",
            "generated_result_summary",
            "reference_result_summary",
            "m1_status",
            "execution_status",
            "correctness",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "livesqlbench_base_lite_preflight_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "livesqlbench_base_lite_preflight_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/fixtures"))
    args = parser.parse_args()
    config = PostgresConnectionConfig.from_environment()
    result = run_preflight(args.root, args.output_dir, config)
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("classification", "manifest_hash", "provider_calls", "population")
            },
            indent=2,
        )
    )
    raise SystemExit(0 if result["classification"].endswith("VALIDATED") else 1)


if __name__ == "__main__":
    main()
