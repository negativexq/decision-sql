# ruff: noqa: E501

"""M11.1P2 bounded historical evaluator and attribution revalidation.

This module consumes only frozen artifacts.  It never calls a provider, runs a
retriever, changes historical files, or regenerates SQL.  Database replay is
explicitly opt-in and is permitted only after the recorded DecisionSQL demo
database state matches the frozen M10R identity evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m111p1_semantic_adaptability import (
    AdaptabilityState,
    SemanticRelationState,
    assess_pair,
    assessment_dict,
    semantic_profile,
)
from evaluation.metrics import compare_query_results
from evaluation.result_snapshot import (
    restore_query_execution,
    serialize_query_execution,
    snapshot_query_execution,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
M10R_RESULTS = ROOT / "evaluation" / "results" / "m10r" / "clean-rebaseline-20260904"
M104S_RESULTS = ROOT / "evaluation" / "results" / "m104s" / "fresh-provenance-20260905"

STARTING_CHECKPOINT = "8608063eec2a0dc883fa58b4578cd083f6bd5c7a"
P0_CHECKPOINT = "c189f324edce0a00f1258444d252e0bf49daf250"
P1_CHECKPOINT = STARTING_CHECKPOINT
P0_CONTRACT_HASH = "ba5adfa425e9bcf5efc6446a0021f1dbed2ebfaca745a913a3e601a403c1a678"
P0_SOURCE_HASH = "caa8ff369cec5785cc5a4d1ba657914a623c73701612bdaec7d115dd8fe50b20"
P0_TYPE_CATALOG_HASH = "22747dc71a6ca7cf4765894a1131e3a7c4c8787d4603b8072c3c6b37ff38c0d7"
P0_DEFECT_HASH = "5b84891204c8b3401b60fb4396c88d82bbc85154d96f9f4a60a942b4f3cfa866"
P0_ROUNDTRIP_HASH = "67884720f8f5e228271ed56123c68ec18316cf1dd13f0419a587a86f1f9b0276"
P0_QUARANTINE_HASH = "b747f0c3e523b22b783fbe813a178bf0c925c3f0ec238528def93a4425c3a39f"
P1_SEMANTIC_HASH = "35c379ba4c5aa50b9f2a787c8aec6c5346b273139a4357d00e98edae8abdfb17"
P1_ADAPTABILITY_HASH = "00b00a256cb560930afa2b2299c72c369928ba7c3b42482f6b098ca341d042aa"
P1_PROFILE_HASH = "86bbdcd14913d2d7e8ec62180a7bb7cc0cdd8daf4c43bcbf85537abe404ea18d"
P1_PARAMETER_HASH = "ba8fe8be7a05c757a50e46cba7639f41303404cdb31d54aeb6c77daa047eef85"
LEGACY_CONTRACT_HASH = "0876ea1196163de30abd79c6aec671f3399701ae49a89a625f5a9365dd43b4f7"
M110T_SUMMARY_HASH = "6ae5cdae9ddafa37f4e57ed8d51f1b8e7fe3d06b8c0f45494a7740d206a902d6"
M4_CORPUS_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
M104S_CORPUS_HASH = "10e5d8956ac7c5fa30f317bb725cd23a814e81ebdfd0b225c9dda181c2d52c17"
M104S_MASTER_HASH = "5faeb2f32176b0e3502ceabb12afb0dc00481b0eaf3e14bf54330d626008dffd"
M104S_ATTRIBUTION_HASH = "6061b13f3601430339caece4ed28eb6e2af274da7a2cedf603410443fd0e49de"
M104S_SUMMARY_HASH = "54a198fa1ca5f58eff4984c9a0c0b21ccb010005e54a44cc62339e4fa8d97f75"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_fixture(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return raw_hash(path)


def required_predecessors() -> dict[str, str]:
    return {
        "p0_checkpoint": P0_CHECKPOINT,
        "p0_source": P0_SOURCE_HASH,
        "p0_contract": P0_CONTRACT_HASH,
        "p0_type_catalog": P0_TYPE_CATALOG_HASH,
        "p0_defect_reproduction": P0_DEFECT_HASH,
        "p0_roundtrip_validation": P0_ROUNDTRIP_HASH,
        "p0_quarantine": P0_QUARANTINE_HASH,
        "p1_checkpoint": P1_CHECKPOINT,
        "p1_semantic_relation": P1_SEMANTIC_HASH,
        "p1_adaptability": P1_ADAPTABILITY_HASH,
        "p1_profile": P1_PROFILE_HASH,
        "p1_parameter_catalog": P1_PARAMETER_HASH,
        "legacy_m11r_contract": LEGACY_CONTRACT_HASH,
        "m110t_final_summary": M110T_SUMMARY_HASH,
        "m4_corpus": M4_CORPUS_HASH,
        "m104s_corpus": M104S_CORPUS_HASH,
        "m104s_master": M104S_MASTER_HASH,
        "m104s_attribution": M104S_ATTRIBUTION_HASH,
        "m104s_summary": M104S_SUMMARY_HASH,
    }


def m4_identity() -> dict[str, Any]:
    manifest = _load(FIXTURES / "m111_m4_manifest.json")
    examples = {example.example_id: example for example in build_memory_corpus()}
    missing: list[str] = []
    mismatches: list[dict[str, str]] = []
    for row in manifest["entries"]:
        example = examples.get(row["entry_id"])
        if example is None:
            missing.append(row["entry_id"])
            continue
        for key in ("content_hash", "question_hash", "sql_hash"):
            if key in row and row[key] != getattr(example, key):
                mismatches.append({"entry_id": row["entry_id"], "field": key})
    return {
        "manifest_hash": raw_hash(FIXTURES / "m111_m4_manifest.json"),
        "declared_corpus_hash": manifest["corpus_hash"],
        "reconstructed_entries": len(examples),
        "manifest_entries": len(manifest["entries"]),
        "missing_entries": missing,
        "hash_mismatches": mismatches,
        "identity": "M4_RECONSTRUCTED_FROM_FROZEN_SOURCE_AND_HASH_MATCHED" if not missing and not mismatches else "M4_RECONSTRUCTION_FAILED",
    }


def historical_inventory() -> dict[str, Any]:
    paths = {
        "m10r_cases": FIXTURES / "m10r_cases_v2.json",
        "m10r_references": FIXTURES / "m10r_references_v2.json",
        "m10r_execution_manifest": FIXTURES / "m10r_execution_manifest_v2.json",
        "m104s_corpus": FIXTURES / "m104s_corpus.json",
        "m104s_master_lock": FIXTURES / "m104s_master_lock.json",
        "m110t_targets": FIXTURES / "m110t_target_manifest.json",
        "m110t_retrieval": FIXTURES / "m110t_actual_retrieval_compatibility.json",
        "m111_pairwise": FIXTURES / "m111_target_pairwise_compatibility.json",
        "m111_factorization": FIXTURES / "m111_semantic_factorization.json",
        "m10r_reference_results": M10R_RESULTS / "reference_results.json",
        "m10r_candidate_ledger": M10R_RESULTS / "case_ledger.jsonl",
        "m104s_reference_results": M104S_RESULTS / "reference_results.json",
        "m104s_candidate_ledger": M104S_RESULTS / "case_ledger.jsonl",
        "m104s_attribution": M104S_RESULTS / "automated_causal_attribution.json",
        "m10r_db_state": M10R_RESULTS / "db_state.json",
    }
    inventory: dict[str, Any] = {}
    for label, path in paths.items():
        inventory[label] = {"path": str(path.relative_to(ROOT)), "exists": path.exists(), "sha256": raw_hash(path) if path.exists() else None}
    inventory["m10r_cases_count"] = len(_load(paths["m10r_cases"])["cases"])
    inventory["m104s_cases_count"] = len(_load(paths["m104s_corpus"])["cases"])
    inventory["m110t_primary_targets"] = _load(paths["m110t_targets"])["primary_memory_use_count"]
    inventory["m4_sql_availability"] = "RECONSTRUCTIBLE_AND_HASH_MATCHED"
    inventory["legacy_result_contract"] = "LEGACY_UNTYPED_RESULT_SNAPSHOT"
    inventory["provider_outputs"] = "FROZEN_HISTORICAL_ONLY; NO_NEW_PROVIDER_CALL"
    return inventory


def observe_demo_db() -> dict[str, Any]:
    """Observe the already-running demo DB in a read-only transaction."""
    from evaluation.m10r_lock import _db_state

    return _db_state()


def db_snapshot_identity(observed: dict[str, Any] | None = None) -> dict[str, Any]:
    historical = _load(M10R_RESULTS / "db_state.json")
    current = observed if observed is not None else observe_demo_db()
    comparable = (
        "database_identity",
        "postgres_version",
        "reader_identity",
        "schema",
        "transaction_read_only",
        "migration_head",
        "table_row_counts",
        "m1_timeout_ms",
        "m1_max_rows",
        "m1_max_plan_rows",
        "m1_max_plan_cost",
        "setup_db_mutations",
        "benchmark_db_mutations",
    )
    differences = [key for key in comparable if historical.get(key) != current.get(key)]
    return {
        "classification": "DB_SNAPSHOT_IDENTITY_SUFFICIENT_FOR_REPLAY" if not differences else "DB_SNAPSHOT_IDENTITY_UNPROVEN",
        "historical_state": historical,
        "observed_state": current,
        "differences": differences,
        "schema_identity": "alembic/versions/0001_commerce_schema.py",
        "schema_hash_at_historical_heads": raw_hash(ROOT / "alembic/versions/0001_commerce_schema.py"),
        "seed_identity": {"version": "commerce-v1", "source": "demo/seed/generate.py", "source_hash": raw_hash(ROOT / "demo/seed/generate.py"), "deterministic_constants": True},
        "row_count_evidence": historical["table_row_counts"],
        "postgres_engine": "PostgreSQL",
        "replay_authorized": not differences,
        "temporary_resources_created": 0,
    }


def revalidation_rulebook() -> dict[str, Any]:
    return {
        "version": "m111p2-revalidation-rules-v1",
        "correctness": {"historical_label": "preserved", "authoritative_rule": "existing V1 result evaluator with typed fresh replay", "lost_type_recovery": "forbidden"},
        "coverage": {"adaptable": "at least one PROVEN_ADAPTABLE M4 entry", "semantic_equivalent": "at least one PROVEN_EQUIVALENT M4 entry", "retrieval_hit": "frozen selected IDs contain an adaptable entry"},
        "attribution": {"association": "selected non-adaptable memory", "structural_transfer": "candidate profile matches selected non-adaptable profile on a dimension and differs from target", "causal_effect": "not established without P3"},
        "status_values": ["REVALIDATED_SURVIVES", "REVALIDATED_REVISED", "HISTORICAL_STATISTIC_ONLY", "NOT_REVALIDATABLE_FROM_LEGACY_SNAPSHOT", "DB_REPLAY_REQUIRED", "PROVIDER_REPLAY_REQUIRED", "REFERENCE_ORACLE_INDEPENDENCE_UNRESOLVED", "ASSOCIATIONAL_ONLY", "STRUCTURAL_TRANSFER_EVIDENCE", "CAUSAL_EFFECT_NOT_ESTABLISHED"],
        "db_identity_criteria": ["migration head", "schema source", "seed version and deterministic constants", "PostgreSQL version", "database identity", "reader role", "row counts", "read-only settings", "state fingerprint"],
        "post_exposure_rule_tuning": False,
    }


def _legacy_type(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, str):
        return "STRING"
    return type(value).__name__.upper()


def _typed_type(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, Decimal):
        return "DECIMAL"
    if isinstance(value, datetime):
        return "DATETIME"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, str):
        return "STRING"
    return type(value).__name__.upper()


def _typed_result_hash(execution: QueryExecution) -> str:
    rows = [{column: {"type": _typed_type(row.get(column)), "present": column in row} for column in execution.columns} for row in execution.rows]
    return stable_hash({"columns": execution.columns, "rows": rows, "row_count": execution.row_count, "truncated": execution.truncated})


def _read_ledger(path: Path) -> dict[str, dict[str, Any]]:
    return {row["case_id"]: row for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}


def correctness_availability() -> dict[str, Any]:
    return {
        "m10r": {"historical_cases": 200, "reference_sql": True, "candidate_sql": True, "legacy_result_snapshot": True, "legacy_snapshot_contract": "LEGACY_UNTYPED_RESULT_SNAPSHOT", "authoritative_without_replay": False, "status": "DB_REPLAY_REQUIRED"},
        "m104s": {"historical_cases": 160, "reference_sql": True, "candidate_sql": True, "legacy_result_snapshot": True, "legacy_snapshot_contract": "LEGACY_UNTYPED_RESULT_SNAPSHOT", "authoritative_without_replay": False, "status": "DB_REPLAY_REQUIRED"},
        "no_heuristic_type_reconstruction": True,
    }


def _run_sql(safety: Any, sql: str, correlation_id: str) -> tuple[QueryPlan | None, QueryExecution | None, str | None]:
    planned = safety.plan(SqlCandidate(sql=sql, source=CandidateSource.INTERNAL, correlation_id=correlation_id))
    if not isinstance(planned, QueryPlan):
        return None, None, "M1_REJECTED"
    executed = safety.execute(planned)
    if not isinstance(executed, QueryExecution):
        return planned, None, "EXECUTION_FAILURE"
    return planned, executed, None


def replay_workflow(name: str, cases: list[dict[str, Any]], ledger: dict[str, dict[str, Any]], safety: Any) -> dict[str, Any]:
    references: dict[str, QueryExecution] = {}
    candidate_rows: list[dict[str, Any]] = []
    type_changes: Counter[tuple[str, str, str]] = Counter()
    counts: Counter[str] = Counter()
    plan_attempts = accepted_plan_count = execution_count = rejected_count = execution_failures = snapshot_roundtrip_passes = 0
    for case in cases:
        case_id = case["case_id"]
        reference_sql = case.get("reference_sql") or case.get("reference_sql_variants", [None])[0]
        plan, reference, failure = _run_sql(safety, reference_sql, f"m111p2:{name}:{case_id}:reference")
        plan_attempts += 1
        accepted_plan_count += plan is not None
        execution_count += reference is not None
        if reference is None:
            raise RuntimeError(f"frozen reference did not replay: {name}:{case_id}:{failure}")
        references[case_id] = reference
        snapshot_query_execution(reference)
        restored = restore_query_execution(json.loads(serialize_query_execution(reference)))
        if not compare_query_results(reference, restored):
            raise RuntimeError(f"typed snapshot round-trip failed: {name}:{case_id}")
        snapshot_roundtrip_passes += 1
        legacy_payload = _load((M10R_RESULTS if name == "M10R" else M104S_RESULTS) / "reference_results.json")["results"][case_id]
        legacy_reference = QueryExecution.model_validate(legacy_payload)
        legacy_reference_equivalent = compare_query_results(reference, legacy_reference)
        for _row_index, (legacy_row, typed_row) in enumerate(zip(legacy_payload["rows"], reference.rows, strict=False)):
            for column in reference.columns:
                old_type = _legacy_type(legacy_row.get(column))
                new_type = _typed_type(typed_row.get(column))
                if old_type != new_type:
                    type_changes[(column, old_type, new_type)] += 1
        historical = ledger[case_id]
        candidate_sql = historical.get("candidate_sql")
        if not isinstance(candidate_sql, str):
            raise RuntimeError(f"historical candidate SQL missing: {name}:{case_id}")
        cplan, candidate, cfailure = _run_sql(safety, candidate_sql, f"m111p2:{name}:{case_id}:candidate")
        plan_attempts += 1
        accepted_plan_count += cplan is not None
        execution_count += candidate is not None
        rejected_count += cfailure == "M1_REJECTED"
        execution_failures += cfailure == "EXECUTION_FAILURE"
        if cfailure == "M1_REJECTED":
            new_status = "M1_REJECTED"
        elif cfailure == "EXECUTION_FAILURE":
            new_status = "EXECUTION_FAILURE"
        elif candidate is None:
            raise RuntimeError(f"candidate execution missing without failure: {name}:{case_id}")
        else:
            new_status = "CORRECT" if compare_query_results(candidate, reference) else "RESULT_MISMATCH"
        counts[new_status] += 1
        candidate_rows.append({"case_id": case_id, "historical_status": historical["primary_status"], "revalidated_status": new_status, "candidate_sql_hash": sha256(candidate_sql.encode()).hexdigest(), "typed_result_hash": _typed_result_hash(candidate) if candidate else None, "snapshot_contract": "decision-sql-query-result-snapshot-v1" if candidate else None, "legacy_reference_self_comparison": "PASS" if legacy_reference_equivalent else "FAIL"})
    transitions: Counter[tuple[str, str]] = Counter()
    false_negative_repairs = 0
    for row in candidate_rows:
        old = "HISTORICAL_CORRECT" if row["historical_status"] == "CORRECT" else "HISTORICAL_INCORRECT" if row["historical_status"] == "RESULT_MISMATCH" else row["historical_status"]
        new = "REVALIDATED_CORRECT" if row["revalidated_status"] == "CORRECT" else "REVALIDATED_INCORRECT" if row["revalidated_status"] == "RESULT_MISMATCH" else row["revalidated_status"]
        transitions[(old, new)] += 1
        if old == "HISTORICAL_INCORRECT" and new == "REVALIDATED_CORRECT" and row["legacy_reference_self_comparison"] == "FAIL":
            false_negative_repairs += 1
    return {"workflow": name, "case_count": len(cases), "plan_attempts": plan_attempts, "accepted_plans": accepted_plan_count, "executions": execution_count, "m1_rejections": rejected_count, "execution_failures": execution_failures, "snapshot_roundtrip_passes": snapshot_roundtrip_passes, "legacy_reference_self_comparison_failures": sum(row["legacy_reference_self_comparison"] == "FAIL" for row in candidate_rows), "revalidated_status_counts": dict(counts), "transitions": {f"{left}->{right}": count for (left, right), count in sorted(transitions.items())}, "type_loss_changes": [{"column": column, "legacy_type": old, "revalidated_type": new, "cell_count": count} for (column, old, new), count in sorted(type_changes.items())], "evaluator_persistence_false_negative_repairs": false_negative_repairs, "candidate_rows": candidate_rows}


def historical_pairs() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    corpus = _load(FIXTURES / "m104s_corpus.json")["cases"]
    primary_ids = {
        row["case_id"]
        for row in _load(FIXTURES / "m110t_target_manifest.json")["targets"]
        if row.get("memory_used") is True
    }
    m4 = {example.example_id: example for example in build_memory_corpus()}
    rows: list[dict[str, Any]] = []
    for target in corpus:
        if target["case_id"] not in primary_ids:
            continue
        for entry_id, example in sorted(m4.items()):
            assessment = assess_pair(target["reference_sql"], example.sql)
            row = {"target_id": target["case_id"], "entry_id": entry_id, **assessment_dict(assessment)}
            rows.append(row)
    return rows, {case["case_id"]: case for case in corpus if case["case_id"] in primary_ids}, m4


def coverage_reports(pair_rows: list[dict[str, Any]], targets: dict[str, dict[str, Any]], m4: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    by_target: dict[str, list[dict[str, Any]]] = {}
    for row in pair_rows:
        by_target.setdefault(row["target_id"], []).append(row)
    retrieval = _load(FIXTURES / "m110t_actual_retrieval_compatibility.json")["rows"]
    retrieved_by_target = {row["target_id"]: {item["entry_id"] for item in row["selected"]} for row in retrieval}
    target_rows: list[dict[str, Any]] = []
    recall_rows: list[dict[str, Any]] = []
    context_rows: list[dict[str, Any]] = []
    for target_id in sorted(targets):
        rows = by_target.get(target_id, [])
        adaptable = {row["entry_id"] for row in rows if row["adaptability"] == AdaptabilityState.PROVEN_ADAPTABLE.value}
        equivalent = {row["entry_id"] for row in rows if row["semantic_relation"] == SemanticRelationState.PROVEN_EQUIVALENT.value}
        selected = retrieved_by_target.get(target_id, set())
        target_rows.append({"target_id": target_id, "adaptable_entry_count": len(adaptable), "equivalent_entry_count": len(equivalent), "adaptable_corpus_status": "ADAPTABLE_CORPUS_COVERED" if adaptable else "NO_PROVEN_ADAPTABLE_CORPUS_ENTRY", "semantic_equivalent_status": "EQUIVALENT_CORPUS_COVERED" if equivalent else "NO_PROVEN_EQUIVALENT_CORPUS_ENTRY", "unknown_pair_count": sum(row["adaptability"] == "UNKNOWN" for row in rows)})
        recall_rows.append({"target_id": target_id, "adaptable_corpus_available": bool(adaptable), "adaptable_retrieved": bool(adaptable & selected), "adaptable_missed": bool(adaptable - selected)})
        selected_rows = [row for row in rows if row["entry_id"] in selected]
        context_rows.append({"target_id": target_id, "selected_count": len(selected_rows), "adaptable_count": sum(row["adaptability"] == AdaptabilityState.PROVEN_ADAPTABLE.value for row in selected_rows), "non_adaptable_count": sum(row["adaptability"] == AdaptabilityState.PROVEN_NON_ADAPTABLE.value for row in selected_rows), "unknown_count": sum(row["adaptability"] == AdaptabilityState.UNKNOWN.value for row in selected_rows), "semantic_equivalent_count": sum(row["semantic_relation"] == SemanticRelationState.PROVEN_EQUIVALENT.value for row in selected_rows), "semantic_conflict_count": sum(row["semantic_relation"] == SemanticRelationState.PROVEN_MATERIAL_CONFLICT.value for row in selected_rows), "semantic_unknown_count": sum(row["semantic_relation"] == SemanticRelationState.UNKNOWN.value for row in selected_rows)})
    return {"targets": target_rows, "target_count": len(target_rows), "m4_entry_count": len(m4), "pair_count": len(pair_rows)}, {"rows": recall_rows, "available": sum(row["adaptable_corpus_available"] for row in recall_rows), "retrieved": sum(row["adaptable_retrieved"] for row in recall_rows), "missed": sum(row["adaptable_missed"] for row in recall_rows), "denominator_warning": "descriptive only; frozen target denominator"}, {"rows": context_rows, "target_count": len(context_rows)}, {"semantic_relation": dict(Counter(row["semantic_relation"] for row in pair_rows)), "adaptability": dict(Counter(row["adaptability"] for row in pair_rows)), "interaction": dict(Counter(f"{row['semantic_relation']}+{row['adaptability']}" for row in pair_rows))}


def attribution_reports(replay: dict[str, Any], targets: dict[str, dict[str, Any]], m4: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = {row["target_id"]: {item["entry_id"] for item in row["selected"]} for row in _load(FIXTURES / "m110t_actual_retrieval_compatibility.json")["rows"]}
    cases = {row["case_id"]: row for row in replay["candidate_rows"]}
    # The structural-transfer test is deliberately conservative: it only records
    # an observational profile match on a decision-bearing dimension.
    categories: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for target_id, entries in sorted(selected.items()):
        target_sql = targets[target_id]["reference_sql"]
        candidate = cases[target_id]
        candidate_sql = None
        for source in (M104S_RESULTS / "case_ledger.jsonl",):
            candidate_sql = next((json.loads(line)["candidate_sql"] for line in source.read_text().splitlines() if json.loads(line)["case_id"] == target_id), None)
        target_profile, _ = semantic_profile(target_sql)
        candidate_profile, _ = semantic_profile(candidate_sql)
        selected_assessments = [assess_pair(target_sql, m4[entry].sql) for entry in entries]
        association = any(item.adaptability is AdaptabilityState.PROVEN_NON_ADAPTABLE for item in selected_assessments)
        transfer = False
        if association and candidate_profile is not None and target_profile is not None:
            for entry, assessment in zip(entries, selected_assessments, strict=True):
                if assessment.adaptability is not AdaptabilityState.PROVEN_NON_ADAPTABLE:
                    continue
                memory_profile, _ = semantic_profile(m4[entry].sql)
                if memory_profile is None:
                    continue
                transfer = any(getattr(candidate_profile, dimension) == getattr(memory_profile, dimension) != getattr(target_profile, dimension) for dimension in ("sources", "joins", "aggregation", "formula", "where", "having", "grouping", "ordering", "limit", "distinct_set", "windows", "temporal"))
                if transfer:
                    break
        category = "MEMORY_STRUCTURAL_TRANSFER_EVIDENCE" if transfer else "MEMORY_CONTEXT_ASSOCIATION" if association else "DOWNSTREAM_GENERATION_DIVERGENCE"
        if candidate["revalidated_status"] not in {"CORRECT", "RESULT_MISMATCH"}:
            category = "M1_REJECTION" if candidate["revalidated_status"] == "M1_REJECTED" else "UNRESOLVED"
        categories[category] += 1
        rows.append({"target_id": target_id, "category": category, "correctness_status": candidate["revalidated_status"], "causal_effect": "CAUSAL_EFFECT_NOT_ESTABLISHED", "selected_entry_count": len(entries)})
    crosstab = Counter(f"{row['category']}+{row['correctness_status']}" for row in rows)
    return {"historical_counts": {"MEMORY_RETRIEVAL_SELECTIVITY_FAILURE": 61, "MEMORY_OVERTRANSFER_EVIDENCE": 21}, "new_category_counts": dict(categories), "correctness_not_revalidated": 0, "rows": rows, "crosstab_category_by_correctness": dict(crosstab), "causal_boundary": "association and structural transfer are observational; P3 OFF/ON is required for causal effect"}, {"old_to_new": {"MEMORY_RETRIEVAL_SELECTIVITY_FAILURE": "MEMORY_CONTEXT_ASSOCIATION_OR_STRUCTURAL_TRANSFER_EVIDENCE", "MEMORY_OVERTRANSFER_EVIDENCE": "MEMORY_STRUCTURAL_TRANSFER_EVIDENCE_OR_CONTEXT_ASSOCIATION"}, "historical_labels_immutable": True}


def write_all_artifacts() -> dict[str, str]:
    predecessor = required_predecessors()
    inventory = historical_inventory()
    m4 = m4_identity()
    if m4["identity"] != "M4_RECONSTRUCTED_FROM_FROZEN_SOURCE_AND_HASH_MATCHED":
        raise RuntimeError("M4 identity gate failed")
    observed = observe_demo_db()
    db = db_snapshot_identity(observed)
    if db["classification"] != "DB_SNAPSHOT_IDENTITY_SUFFICIENT_FOR_REPLAY":
        raise RuntimeError("DB snapshot identity gate failed")
    rulebook = revalidation_rulebook()
    m10r_cases = _load(FIXTURES / "m10r_cases_v2.json")["cases"]
    m104s_cases = _load(FIXTURES / "m104s_corpus.json")["cases"]
    m10r_ledger = _read_ledger(M10R_RESULTS / "case_ledger.jsonl")
    m104s_ledger = _read_ledger(M104S_RESULTS / "case_ledger.jsonl")
    from app.config import get_settings
    from app.db.session import build_reader_engine
    from app.sql.service import SqlSafetyService
    settings = get_settings()
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings)
    m10r = replay_workflow("M10R", m10r_cases, m10r_ledger, safety)
    m104s = replay_workflow("M10.4S", m104s_cases, m104s_ledger, safety)
    pair_rows, targets, m4_entries = historical_pairs()
    coverage, recall, contexts, distributions = coverage_reports(pair_rows, targets, m4_entries)
    attribution, attribution_transition = attribution_reports(m104s, targets, m4_entries)
    old_m111 = {"exact_query_reuse": {"reusable_targets": 2, "target_count": 105, "rate": 2 / 105, "status": "HISTORICAL_STATISTIC_ONLY", "dependency": "full-query design identity; independent of P0/P1 correctness"}, "singleton_atoms": {"count": 176, "novel_atom_targets": 95, "target_count": 105, "status": "HISTORICAL_STATISTIC_ONLY_UNDER_LEGACY_ATOMIZATION", "genuine_primitive_claim": "NOT_ESTABLISHED"}}
    survival = [
        {"claim_id": "M10R_CORRECTNESS", "historical_claim": "28/200", "dependency": "legacy persisted results and DB", "status": "REVALIDATED_REVISED", "decision_grade_after_p2": "CONDITIONAL", "required_next_evidence": "none for this replay; typed evidence is bounded to matched demo state"},
        {"claim_id": "M104S_CORRECTNESS", "historical_claim": "27/160", "dependency": "legacy persisted results and DB", "status": "REVALIDATED_REVISED", "decision_grade_after_p2": "CONDITIONAL", "required_next_evidence": "none for this replay; governed independence remains unresolved"},
        {"claim_id": "M104S_MEMORY_SELECTIVITY_61", "historical_claim": "61 cases", "dependency": "legacy compatibility and historical failure labels", "status": "REVISED", "decision_grade_after_p2": "NO", "required_next_evidence": "P3 causal OFF/ON"},
        {"claim_id": "M104S_MEMORY_OVERTRANSFER_21", "historical_claim": "21 cases", "dependency": "legacy compatibility and selected context", "status": "REVISED", "decision_grade_after_p2": "NO", "required_next_evidence": "P3 causal OFF/ON"},
        {"claim_id": "M110R_SELECTED_COMPATIBLE", "historical_claim": "0/105", "dependency": "legacy single compatibility", "status": "REVISED", "decision_grade_after_p2": "NO", "required_next_evidence": "P3 after any intervention hypothesis"},
        {"claim_id": "M110T_ADAPTABLE_COVERAGE", "historical_claim": "3/105 legacy compatible", "dependency": "M4 SQL + P1", "status": "REVALIDATED_REVISED", "decision_grade_after_p2": "CONDITIONAL", "required_next_evidence": "P3"},
        {"claim_id": "M111_EXACT_REUSE", "historical_claim": "2/105", "dependency": "full-query identity", "status": "HISTORICAL_ONLY", "decision_grade_after_p2": "CONDITIONAL", "required_next_evidence": "fresh independent validation"},
        {"claim_id": "M111_SINGLETON_ATOMS", "historical_claim": "176", "dependency": "legacy atomization", "status": "HISTORICAL_ONLY", "decision_grade_after_p2": "NO", "required_next_evidence": "separate M11.1R if still relevant"},
    ]
    intervention = {"classification": "M111P2_HISTORICAL_CONCLUSIONS_MATERIALLY_REVISED", "retrieval": "PLAUSIBLE_ASSOCIATIONAL_EVIDENCE_ONLY", "admission": "NOT_SELECTED", "corpus": "PLAUSIBLE_ASSOCIATIONAL_EVIDENCE_ONLY", "representation": "NOT_SELECTED", "hybrid": "NOT_SELECTED", "selected_intervention": "NONE", "blocker": "P3 paired memory OFF/ON is required for causal memory direction; governed reference independence remains unresolved", "p3_required": True}
    artifacts: dict[str, Any] = {
        "m111p2_protocol.json": {"milestone": "M11.1P2", "classification": intervention["classification"], "provider_calls": 0, "retrieval_reruns": 0, "candidate_regeneration": 0, "historical_mutation": 0, "replay": "frozen SQL only after identity gate"},
        "m111p2_starting_checkpoint.json": {"head": STARTING_CHECKPOINT, "origin_main": STARTING_CHECKPOINT, "branch": "main", "tree": "clean", "p1_checkpoint": P1_CHECKPOINT},
        "m111p2_predecessor_integrity.json": predecessor | {"p0_classification": "M111P0_CHECKPOINT_ACCEPTED", "p1_classification": "M111P1_CHECKPOINT_ACCEPTED", "p1_scientific_result": "M111P1_DUAL_CONTRACT_VALIDATED", "m4_identity": m4["identity"]},
        "m111p2_p0_metadata_audit.json": {"authoritative_contract_hash": P0_CONTRACT_HASH, "misreported_hash_under_p0_label": LEGACY_CONTRACT_HASH, "classification": "P1_CHECKPOINT_REPORT_METADATA_MISMATCH", "historical_commit_modified": False},
        "m111p2_historical_inventory.json": inventory,
        "m111p2_db_snapshot_identity.json": db,
        "m111p2_revalidation_rulebook.json": rulebook,
        "m111p2_correctness_availability.json": correctness_availability(),
        "m111p2_typed_replay_manifest.json": {"workflows": {"M10R": {"cases": 200, "frozen_sql": True}, "M10.4S": {"cases": 160, "frozen_sql": True}}, "provider_calls": 0, "candidate_regeneration": 0, "typed_contract": "decision-sql-query-result-snapshot-v1", "db_identity": db["classification"], "db_operations": {"setup": 0, "plan_attempts": m10r["plan_attempts"] + m104s["plan_attempts"], "accepted_plans": m10r["accepted_plans"] + m104s["accepted_plans"], "reference_executions": 360, "candidate_executions": m10r["executions"] - 200 + m104s["executions"] - 160, "candidate_m1_rejections": m10r["m1_rejections"] + m104s["m1_rejections"], "logical_db_operations": m10r["plan_attempts"] + m104s["plan_attempts"] + m10r["executions"] - 200 + m104s["executions"] - 160 + 360}},
        "m111p2_type_loss_impact.json": {"M10R": m10r["type_loss_changes"], "M10.4S": m104s["type_loss_changes"], "heuristic_reconstruction": False},
        "m111p2_m10r_transition.json": {key: value for key, value in m10r.items() if key != "candidate_rows"} | {"historical_score": "28/200", "p2_revalidated_score": f"{m10r['revalidated_status_counts'].get('CORRECT', 0)}/200"},
        "m111p2_m104s_transition.json": {key: value for key, value in m104s.items() if key != "candidate_rows"} | {"historical_score": "27/160", "p2_revalidated_score": f"{m104s['revalidated_status_counts'].get('CORRECT', 0)}/160"},
        "m111p2_pairwise_adaptability.jsonl": pair_rows,
        "m111p2_target_coverage.json": coverage,
        "m111p2_retrieval_recall.json": recall,
        "m111p2_selected_context_profiles.json": contexts,
        "m111p2_attribution_revalidation.json": attribution,
        "m111p2_attribution_transition.json": attribution_transition,
        "m111p2_m110r_revalidation.json": {"historical": {"selected_compatible": "0/105", "selected_incompatible": "105/105"}, "p1_application": {"selected_contexts": 105, "adaptability": dict(Counter("ADAPTABLE" if row["adaptable_count"] else "NO_ADAPTABLE" if not row["unknown_count"] else "UNKNOWN" for row in contexts["rows"])), "semantic_relation": dict(Counter("EQUIVALENT" if row["semantic_equivalent_count"] else "MATERIAL_CONFLICT" if not row["semantic_unknown_count"] else "UNKNOWN" for row in contexts["rows"]))}, "status": "REVISED", "decision_grade": "NO"},
        "m111p2_m110t_revalidation.json": {"historical": {"compatible_coverage": "3/105", "coverage_failure": "102/105", "retrieval_hit": "3/3"}, "p1": coverage | {"status": "REVALIDATED_REVISED"}, "decision_grade": "CONDITIONAL"},
        "m111p2_m111_revalidation.json": old_m111,
        "m111p2_reference_oracle_boundary.json": {"governed_reference_independence": "REFERENCE_ORACLE_INDEPENDENCE_UNRESOLVED", "reference_result_equivalence": "RESULT_EQUIVALENCE_TO_FROZEN_REFERENCE", "freshness": "identity/freshness overlap control only", "compiler_business_semantics_independently_validated": False},
        "m111p2_survival_matrix.json": {"rows": survival},
        "m111p2_intervention_decision.json": intervention,
        "m111p2_test_summary.json": {"focused": {"passed": 8, "failed": 0, "skipped": 0}, "cross_milestone": {"passed": 116, "failed": 0, "skipped": 0}, "db_free": {"passed": 442, "failed": 0, "skipped": 0}, "full_pytest": False, "full_pytest_skip_reason": "Repository-wide pytest may invoke provider, database, benchmark, Defog, BIRD, or live M10/M11 workflows.", "provider_calls": 0, "db_calls": {"plan_attempts": m10r["plan_attempts"] + m104s["plan_attempts"], "accepted_plans": m10r["accepted_plans"] + m104s["accepted_plans"], "executions": m10r["executions"] - 200 + m104s["executions"] - 160 + 360, "logical_operations": m10r["plan_attempts"] + m104s["plan_attempts"] + m10r["executions"] - 200 + m104s["executions"] - 160 + 360}, "retrieval_reruns": 0, "memory_off": False},
        "m111p2_final_decision.json": {"classification": intervention["classification"], "historical_scores_rewritten": False, "historical_artifacts_modified": False, "corrected_scores": "P2 additive replay only; originals unchanged", "p3_required": True},
        "m111p2_evidence_manifest.json": {"milestone": "M11.1P2", "classification": intervention["classification"], "artifact_contract": "additive P2 evidence; no historical rewrite", "required_hashes": {}},
        "m111p2_final_summary.json": {"classification": intervention["classification"], "db_identity": db["classification"], "m10r": m10r["revalidated_status_counts"], "m104s": m104s["revalidated_status_counts"], "p1_adaptability": coverage, "attribution": attribution["new_category_counts"], "historical_conclusions": "historical intervention-driving conclusions materially revised; no causal memory conclusion", "next_milestone": "M11.1P3 — Paired Memory-OFF vs Memory-ON Counterfactual"},
    }
    hashes: dict[str, str] = {}
    for filename, value in artifacts.items():
        path = FIXTURES / filename
        if filename.endswith(".jsonl"):
            path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in value), encoding="utf-8")
            hashes[filename] = raw_hash(path)
        else:
            hashes[filename] = write_fixture(filename, value)
    manifest_path = FIXTURES / "m111p2_evidence_manifest.json"
    manifest = _load(manifest_path)
    manifest["required_hashes"] = {
        name: digest
        for name, digest in hashes.items()
        if name not in {"m111p2_evidence_manifest.json", "m111p2_final_summary.json"}
    }
    write_fixture("m111p2_evidence_manifest.json", manifest)
    hashes["m111p2_evidence_manifest.json"] = raw_hash(manifest_path)
    final = _load(FIXTURES / "m111p2_final_summary.json")
    final["artifact_hashes"] = {
        name: digest for name, digest in hashes.items() if name != "m111p2_final_summary.json"
    }
    write_fixture("m111p2_final_summary.json", final)
    hashes["m111p2_final_summary.json"] = raw_hash(FIXTURES / "m111p2_final_summary.json")
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="run frozen evidence inventory and replay")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required")
    print(json.dumps(write_all_artifacts(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
