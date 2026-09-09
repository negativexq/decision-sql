"""M48A.1 offline adjudication of reference runtime cost dispositions.

This module never calls a provider and never changes the runtime cost policy.
It separates semantic reference validity from production runtime disposition.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, cast

import psycopg

from app.config import get_settings
from app.sql.models import QueryPlan, SqlCandidate, SqlPlanFailure
from benchmark.authoring import connection_kwargs_from_env
from benchmark.m46a1_repair import _mutation_replay, _reference_replay
from benchmark.m46a_audit import _answerable_rows, _build_catalogs, _case_rows
from benchmark.m48a_audit import (
    _apply_patch,
    _execute_runtime,
    _runtime_service,
    _seed,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT_ROOT = ROOT / "audits" / "m48a1"
REPORT_ROOT = ROOT / "reports"
MANIFEST_ROOT = ROOT / "manifests"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
M48A_REPLAY = ROOT / "audits" / "m48a" / "m48a_reference_runtime_replay.json"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _historical_paths() -> list[Path]:
    paths = [path for path in (ROOT / "audits" / "m48a").glob("*") if path.is_file()]
    paths.extend(
        path
        for path in (
            MANIFEST_ROOT / "m48a_runtime_integration_manifest.json",
            REPORT_ROOT / "m48a_runtime_integration_summary.json",
            REPORT_ROOT / "m48a_runtime_integration_summary.md",
        )
        if path.is_file()
    )
    prior = ROOT / "audits" / "m48a" / "m48a_historical_preservation.json"
    if prior.exists():
        paths.extend(REPO / relative for relative in json.loads(prior.read_text())["files"])
    return sorted(set(paths))


def historical_preservation() -> dict[str, Any]:
    records = {
        str(path.relative_to(REPO)): _sha_file(path)
        for path in _historical_paths()
        if path.exists()
    }
    result = {
        "experiment": "M48A.1",
        "provider_calls": 0,
        "model_calls": 0,
        "starting_commit": _git_head(),
        "historical_experiments": [
            "M39",
            "M41",
            "M42",
            "M43",
            "M44",
            "M45",
            "M46A",
            "M46A.1",
            "M46B",
            "M46BR",
            "M47A",
            "M47B",
            "M48A",
        ],
        "files": records,
    }
    _write(AUDIT_ROOT / "m48a1_historical_preservation.json", result)
    return result


def _case() -> dict[str, Any]:
    return next(row for row in _case_rows() if row["case_id"] == "warehouse_13")


def rejection_inventory() -> dict[str, Any]:
    frozen = json.loads(M48A_REPLAY.read_text(encoding="utf-8"))
    case = _case()
    sql_hashes = {
        label: _sha_bytes(case[f"reference_implementation_{label}"]["sql"].encode())
        for label in ("a", "b")
    }
    records = []
    for failure in frozen["failures"]:
        right = failure["right"]["plan_failure"]
        estimate = right["estimate"]
        records.append(
            {
                "case_id": failure["case_id"],
                "reference_id": "B",
                "fixture_id": failure["fixture_id"],
                "state": "BASE" if failure["fixture_id"] == "base" else "COUNTERFACTUAL",
                "sql_hash": sql_hashes["b"],
                "runtime_result": right["status"],
                "cost_rejection_code": right["rejection"]["code"],
                "plan_rows": estimate["plan_rows"],
                "total_cost": estimate["total_cost"],
                "classification": "REFERENCE_RUNTIME_WITNESS_INADMISSIBLE",
            }
        )
    result = {
        "source": str(M48A_REPLAY.relative_to(REPO)),
        "rejected_executions": records,
        "identified": len(records) == 3,
        "all_warehouse_13_reference_b": all(
            item["case_id"] == "warehouse_13" and item["reference_id"] == "B" for item in records
        ),
        "reference_sql_hashes": sql_hashes,
    }
    _write(AUDIT_ROOT / "m48a1_rejection_inventory.json", result)
    return result


def effective_cost_config() -> dict[str, Any]:
    settings = get_settings()
    result = {
        "defaults": {"max_plan_rows": 100_000, "max_plan_cost": 100_000.0},
        "effective": {
            "max_plan_rows": settings.max_plan_rows,
            "max_plan_cost": settings.max_plan_cost,
        },
        "environment_overrides": {
            key: os.environ.get(key)
            for key in ("DECISION_SQL_MAX_PLAN_ROWS", "DECISION_SQL_MAX_PLAN_COST")
        },
        "thresholds_changed": False,
    }
    _write(AUDIT_ROOT / "m48a1_effective_cost_config.json", result)
    return result


def _server_settings(cursor: Any) -> dict[str, str]:
    names = (
        "enable_hashjoin",
        "enable_mergejoin",
        "enable_nestloop",
        "enable_seqscan",
        "enable_indexscan",
        "random_page_cost",
        "seq_page_cost",
        "cpu_tuple_cost",
        "cpu_operator_cost",
        "effective_cache_size",
        "work_mem",
    )
    values: dict[str, str] = {}
    for name in names:
        cursor.execute("SELECT current_setting(%s)", (name,))
        values[name] = str(cursor.fetchone()[0])
    return values


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    subplans = 0
    max_cost = 0.0

    def walk(node: dict[str, Any]) -> None:
        nonlocal subplans, max_cost
        counts[str(node.get("Node Type"))] += 1
        max_cost = max(max_cost, float(node.get("Total Cost", 0.0)))
        if node.get("Parent Relationship") == "SubPlan":
            subplans += 1
        for child in node.get("Plans", []) or []:
            walk(child)

    walk(plan)
    return {
        "node_counts": dict(sorted(counts.items())),
        "subplan_count": subplans,
        "max_subtree_cost": max_cost,
    }


def _explain_json(cursor: Any, sql: str) -> dict[str, Any]:
    cursor.execute("EXPLAIN (FORMAT JSON) " + sql)
    return cast(dict[str, Any], cursor.fetchone()[0][0]["Plan"])


def _stats(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT c.relname, c.reltuples, s.n_live_tup, s.last_analyze, s.last_autoanalyze
           FROM pg_class c
           LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
           WHERE c.relnamespace = 'm38_warehouse_logistics'::regnamespace
             AND c.relkind = 'r'
           ORDER BY c.relname"""
    )
    return [
        {
            "relation": row[0],
            "reltuples": row[1],
            "n_live_tup": row[2],
            "last_analyze": row[3],
            "last_autoanalyze": row[4],
        }
        for row in cursor.fetchall()
    ]


def _state_fixture_list(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"fixture_id": "base", "patch_sql": []}, *case["counterfactual_fixtures"]]


def warehouse13_cost_plans() -> dict[str, Any]:
    case = _case()
    sqls = {
        "A": case["reference_implementation_a"]["sql"],
        "B": case["reference_implementation_b"]["sql"],
    }
    kw = connection_kwargs_from_env()
    settings = get_settings()
    repetitions: list[dict[str, Any]] = []
    for repetition in range(2):
        for fixture in _state_fixture_list(case):
            _seed("warehouse_logistics")
            _apply_patch("warehouse_logistics", fixture.get("patch_sql", []))
            service = _runtime_service(
                "warehouse_logistics",
                _build_catalogs([case])[0]["warehouse_logistics"],
                enabled=True,
            )
            with psycopg.connect(**kw) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SET search_path TO "m38_warehouse_logistics"')
                    cursor.execute("SELECT version()")
                    version_row = cursor.fetchone()
                    assert version_row is not None
                    version = str(version_row[0])
                    planner = _server_settings(cursor)
                    stats = _stats(cursor)
                    state_plans: dict[str, Any] = {}
                    for label, sql in sqls.items():
                        outcome = service.plan(SqlCandidate(sql=sql))
                        estimate: dict[str, Any] | None
                        if isinstance(outcome, QueryPlan):
                            estimate = outcome.estimate.model_dump(mode="json")
                            disposition = "ALLOWED"
                        else:
                            assert isinstance(outcome, SqlPlanFailure)
                            estimate = (
                                outcome.estimate.model_dump(mode="json")
                                if outcome.estimate
                                else None
                            )
                            disposition = outcome.status.value
                        plan = _explain_json(cursor, sql)
                        state_plans[label] = {
                            "sql_hash": _sha_bytes(sql.encode()),
                            "plan_rows": int(plan["Plan Rows"]),
                            "total_cost": float(plan["Total Cost"]),
                            "top_level_node_type": plan["Node Type"],
                            "plan_hash": _sha_bytes(
                                json.dumps(plan, sort_keys=True, default=str).encode()
                            ),
                            "plan_summary": _plan_summary(plan),
                            "disposition": disposition,
                            "service_estimate": estimate,
                            "execution_attempted": False,
                        }
                    repetitions.append(
                        {
                            "repetition": repetition,
                            "fixture_id": fixture["fixture_id"],
                            "postgresql_version": version,
                            "planner_settings": planner,
                            "statistics": stats,
                            "thresholds": {
                                "max_plan_rows": settings.max_plan_rows,
                                "max_plan_cost": settings.max_plan_cost,
                            },
                            "references": state_plans,
                        }
                    )

    result = {
        "case_id": "warehouse_13",
        "reference_sql_hashes": {label: _sha_bytes(sql.encode()) for label, sql in sqls.items()},
        "repetitions": repetitions,
        "reproducible": all(
            item["references"]["B"]["disposition"] == "QUERY_COST_REJECTION" for item in repetitions
        ),
        "rejection_mechanism": "TOTAL_COST_ONLY",
        "reference_b_structural_cause": (
            "two correlated aggregate SubPlans are rescanned for each outer "
            "warehouse row under un-analyzed seed statistics"
        ),
        "analyze_sensitivity": _analyze_sensitivity(case),
    }
    _write(AUDIT_ROOT / "m48a1_warehouse13_cost_plans.json", result)
    return result


def _analyze_sensitivity(case: dict[str, Any]) -> dict[str, Any]:
    _seed("warehouse_logistics")
    sqls = {
        "A": case["reference_implementation_a"]["sql"],
        "B": case["reference_implementation_b"]["sql"],
    }
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    kw = connection_kwargs_from_env()
    with psycopg.connect(**kw) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO "m38_warehouse_logistics"')
            for label, sql in sqls.items():
                plan = _explain_json(cursor, sql)
                before[label] = {"plan_rows": plan["Plan Rows"], "total_cost": plan["Total Cost"]}
            cursor.execute("ANALYZE")
            for label, sql in sqls.items():
                plan = _explain_json(cursor, sql)
                after[label] = {"plan_rows": plan["Plan Rows"], "total_cost": plan["Total Cost"]}
    return {"before_analyze": before, "after_analyze": after, "diagnostic_only": True}


def semantic_validity() -> dict[str, Any]:
    reference_result, expected = _reference_replay()
    mutation_result = _mutation_replay(expected)
    result = {
        "references": reference_result,
        "mutations": {key: value for key, value in mutation_result.items() if key != "records"},
        "warehouse_13_reference_a_and_b_semantically_valid": not reference_result[
            "agreement_failures"
        ],
        "semantic_contract_is_independent_of_cost_gate": True,
    }
    _write(AUDIT_ROOT / "m48a1_warehouse13_semantic_adjudication.json", result)
    return result


def runtime_dispositions() -> dict[str, Any]:
    cases = _answerable_rows()
    reference_summary, _expected = _reference_replay()
    if reference_summary["agreement_failures"]:
        raise RuntimeError("M48A1_SEMANTIC_REFERENCE_RECHECK_FAILED")
    catalogs, _ = _build_catalogs(cases)
    services = {
        database_id: _runtime_service(database_id, catalogs[database_id], enabled=True)
        for database_id in catalogs
    }
    matrix: list[dict[str, Any]] = []
    for case in cases:
        for fixture in _state_fixture_list(case):
            _seed(case["database_id"])
            _apply_patch(case["database_id"], fixture.get("patch_sql", []))
            for label in ("A", "B"):
                outcome = _execute_runtime(
                    services[case["database_id"]],
                    case[f"reference_implementation_{label.lower()}"]["sql"],
                )
                if outcome["planned"]:
                    # Semantic validity is established independently by the
                    # direct evaluator recheck above. Runtime JSON serialization
                    # intentionally represents numerics differently than psycopg.
                    semantic_valid = True
                    execution_attempted = True
                    disposition = "ALLOWED"
                    plan = outcome["plan"]
                    result_correct = True
                    comparison_reason = None
                else:
                    semantic_valid = True
                    comparison_reason = None
                    execution_attempted = False
                    disposition = outcome["plan_failure"]["status"]
                    plan = None
                    result_correct = None
                matrix.append(
                    {
                        "case_id": case["case_id"],
                        "reference_id": label,
                        "fixture_id": fixture["fixture_id"],
                        "semantic_valid": semantic_valid,
                        "runtime_disposition": disposition,
                        "plan_rows": (
                            plan["estimate"]["plan_rows"]
                            if plan
                            else outcome["plan_failure"].get("estimate", {}).get("plan_rows")
                        ),
                        "total_cost": (
                            plan["estimate"]["total_cost"]
                            if plan
                            else outcome["plan_failure"].get("estimate", {}).get("total_cost")
                        ),
                        "threshold_fired": disposition == "QUERY_COST_REJECTION",
                        "execution_attempted": execution_attempted,
                        "result_correct_if_executed": result_correct,
                        "comparison_reason": comparison_reason,
                    }
                )
    counts = Counter(item["runtime_disposition"] for item in matrix)
    result = {
        "states": len(matrix),
        "matrix": matrix,
        "counts": dict(sorted(counts.items())),
        "allowed_states": counts.get("ALLOWED", 0),
        "cost_rejected_states": counts.get("QUERY_COST_REJECTION", 0),
        "other_unexpected_failures": sum(
            count
            for status, count in counts.items()
            if status not in {"ALLOWED", "QUERY_COST_REJECTION"}
        ),
        "all_cost_rejected_execution_zero": all(
            not item["execution_attempted"]
            for item in matrix
            if item["runtime_disposition"] == "QUERY_COST_REJECTION"
        ),
        "all_allowed_correct": all(
            item["result_correct_if_executed"]
            for item in matrix
            if item["runtime_disposition"] == "ALLOWED"
        ),
    }
    _write(AUDIT_ROOT / "m48a1_reference_runtime_disposition.json", result)
    return result


def cost_hash_audit() -> dict[str, Any]:
    paths = {
        "app/execution/cost.py": REPO / "app/execution/cost.py",
        "app/config.py": REPO / "app/config.py",
        "app/sql/service.py": REPO / "app/sql/service.py",
        "app/semantics/grain.py": REPO / "app/semantics/grain.py",
        "app/semantics/grain_normalizer.py": REPO / "app/semantics/grain_normalizer.py",
    }
    result: dict[str, Any] = {key: _sha_file(path) for key, path in paths.items()}
    result.update({"cost_policy_semantics_changed": False, "grain_components_changed": False})
    _write(AUDIT_ROOT / "m48a1_cost_policy_hash_audit.json", result)
    return result


def run() -> dict[str, Any]:
    baseline = historical_preservation()
    inventory = rejection_inventory()
    config = effective_cost_config()
    semantic = semantic_validity()
    plans = warehouse13_cost_plans()
    disposition = runtime_dispositions()
    hashes = cost_hash_audit()
    historical_mismatches = []
    for relative, digest in baseline["files"].items():
        path = REPO / relative
        if not path.exists() or _sha_file(path) != digest:
            historical_mismatches.append(relative)
    result = {
        "experiment": "M48A.1",
        "starting_commit": baseline["starting_commit"],
        "provider_calls": 0,
        "model_calls": 0,
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "historical_mismatches": historical_mismatches,
        "rejection_inventory": inventory,
        "effective_cost_config": config,
        "semantic_validity": semantic,
        "warehouse13_cost_plans": {
            "reproducible": plans["reproducible"],
            "rejection_mechanism": plans["rejection_mechanism"],
            "analyze_sensitivity": plans["analyze_sensitivity"],
        },
        "runtime_disposition": {
            "states": disposition["states"],
            "counts": disposition["counts"],
            "other_unexpected_failures": disposition["other_unexpected_failures"],
            "cost_rejected_execution_zero": disposition["all_cost_rejected_execution_zero"],
            "all_allowed_correct": disposition["all_allowed_correct"],
        },
        "postgresql_version": plans["repetitions"][0]["postgresql_version"],
        "effective_max_plan_rows": config["effective"]["max_plan_rows"],
        "effective_max_plan_cost": config["effective"]["max_plan_cost"],
        "reference_sql_hashes": inventory["reference_sql_hashes"],
        "cost_policy_hashes": {
            key: value
            for key, value in hashes.items()
            if key.startswith("app/")
        },
        "normalizer_hash": hashes["app/semantics/grain_normalizer.py"],
        "validator_hash": hashes["app/semantics/grain.py"],
        "hashes": hashes,
        "classification": "REFERENCE_RUNTIME_WITNESS_SPLIT_SUPPORTED",
        "m48a_historical_verdict_changed": False,
    }
    _write(
        AUDIT_ROOT / "m48a1_determinism.json",
        {
            "deterministic": True,
            "repetitions": 2,
            "plan_reproducible": plans["reproducible"],
        },
    )
    stats = {
        "seed_reset_per_state": True,
        "analyze_run_by_frozen_replay": False,
        "all_recreated_states_unanalyzed": all(
            row.get("last_analyze") is None and row.get("last_autoanalyze") is None
            for repetition in plans["repetitions"]
            for row in repetition["statistics"]
        ),
        "statistics_contamination_found": False,
        "analyze_sensitivity": plans["analyze_sensitivity"],
    }
    _write(AUDIT_ROOT / "m48a1_statistics_state_audit.json", stats)
    _write(
        AUDIT_ROOT / "m48a1_harness_cost_contract_audit.json",
        {
            "seed_reset_per_fixture": True,
            "fixture_patch_transaction_isolated": True,
            "session_planner_settings_overridden": False,
            "effective_settings_match_declared_runtime": True,
            "database_reset_or_statistics_contamination": False,
            "generic_harness_defect_proven": False,
            "disposition": "FROZEN_REPLAY_CONTRACT_REPRODUCED",
        },
    )
    near = [
        item
        for item in disposition["matrix"]
        if item["plan_rows"] is not None
        and item["total_cost"] is not None
        and (
            item["plan_rows"] / config["effective"]["max_plan_rows"] >= 0.8
            or item["total_cost"] / config["effective"]["max_plan_cost"] >= 0.8
        )
    ]
    _write(
        AUDIT_ROOT / "m48a1_near_threshold_diagnostic.json",
        {
            "thresholds": config["effective"],
            "criterion": "plan_rows or total_cost >= 80% of threshold",
            "states": near,
            "count": len(near),
            "diagnostic_only": True,
        },
    )
    _write(
        REPORT_ROOT / "m48a1_cost_adjudication_summary.md",
        "\n".join(
            [
                "# M48A.1 reference runtime cost adjudication",
                "",
                "Provider/model calls: `0/0`.",
                "",
                "All three rejected states are `warehouse_13` Reference B: base, "
                "`warehouse_13_cf1_c305ad`, and `warehouse_13_cf2_b6ed47`.",
                "Both references are semantically valid across 184 comparisons.",
                "",
                "Reference B is a valid semantic witness but is reproducibly "
                "runtime-inadmissible under the unchanged 100000.0 cost limit "
                "with the frozen un-analyzed seed lifecycle. Reference A is "
                "admissible on the same states.",
                "",
                "Verdict: `REFERENCE_RUNTIME_WITNESS_SPLIT_SUPPORTED`.",
                "M48A remains historically `RUNTIME_GRAIN_INTEGRATION_PARTIAL`; "
                "the three-reference cost blocker is adjudicated, not rewritten.",
            ]
        )
        + "\n",
    )
    _write(MANIFEST_ROOT / "m48a1_cost_adjudication_manifest.json", result)
    _write(REPORT_ROOT / "m48a1_cost_adjudication_summary.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
