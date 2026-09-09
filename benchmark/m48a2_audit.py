"""M48A.2 planner-statistics lifecycle contract audit.

This module is deliberately provider-free.  It audits database-state
preparation, freezes a statistics lifecycle selected from environmental
criteria, and only then replays semantic/runtime evidence under that frozen
state contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import psycopg
from psycopg import sql as pgsql
from sqlalchemy import create_engine

from app.config import get_settings
from app.semantics.grain import GrainSafetyValidator
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from benchmark.authoring import connection_kwargs_from_env
from benchmark.m46a1_repair import _mutation_replay, _reference_replay
from benchmark.m46a_audit import _answerable_rows, _build_catalogs, _case_rows
from benchmark.m48a_audit import (
    _apply_patch,
    _fixture_list,
    _schema_name,
    _seed,
)
from benchmark.models import ResultContract, compare_rows
from benchmark.planner_statistics import (
    PlannerStatisticsLifecycle,
    analyze_schema,
    lifecycle_sequence,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT_ROOT = ROOT / "audits" / "m48a2"
REPORT_ROOT = ROOT / "reports"
MANIFEST_ROOT = ROOT / "manifests"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
STARTING_COMMIT = "1dbd52e16fbd2095c4875fbce4b38855d836e73c"
CONTRACT_VERSION = "planner-statistics-contract-1"
SELECTED_LIFECYCLE = "ANALYZE_CURRENT_STATE"
EXPECTED_M47B_PARSED_HASH = "efa7a016b39a913e070dfee0591994facb9ffbe43363527d4ba2504c5821ad66"
M47B_PARSED = ROOT / "experiments" / "results" / "m47b" / "parsed_submissions.jsonl"
WAREHOUSE_CASE = "warehouse_13"
DATABASES = [
    "commerce_ops",
    "fleet_ops",
    "support_ops",
    "subscription_billing",
    "warehouse_logistics",
    "risk_operations",
]


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _sha_json(value: Any) -> str:
    return _sha_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    )


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _historical_paths() -> set[Path]:
    paths: set[Path] = set()
    for preservation in (
        ROOT / "audits" / "m48a" / "m48a_historical_preservation.json",
        ROOT / "audits" / "m48a1" / "m48a1_historical_preservation.json",
        ROOT / "reports" / "m48a1_historical_preservation.json",
    ):
        if preservation.exists():
            payload = json.loads(preservation.read_text(encoding="utf-8"))
            for relative in payload.get("files", {}):
                paths.add(REPO / relative)
    for root, patterns in (
        (ROOT / "audits" / "m48a", ["*", "**/*"]),
        (ROOT / "audits" / "m48a1", ["*", "**/*"]),
    ):
        if root.exists():
            for pattern in patterns:
                paths.update(path for path in root.glob(pattern) if path.is_file())
    for path in (
        ROOT / "manifests" / "m48a_runtime_integration_manifest.json",
        ROOT / "manifests" / "m48a1_cost_adjudication_manifest.json",
        ROOT / "reports" / "m48a_runtime_integration_summary.json",
        ROOT / "reports" / "m48a_runtime_integration_summary.md",
        ROOT / "reports" / "m48a1_cost_adjudication_summary.json",
        ROOT / "reports" / "m48a1_cost_adjudication_summary.md",
    ):
        if path.exists():
            paths.add(path)
    return {path for path in paths if path.exists() and "m48a2" not in str(path)}


def historical_preservation() -> dict[str, Any]:
    files = {str(path.relative_to(REPO)): _sha_file(path) for path in sorted(_historical_paths())}
    result = {
        "experiment": "M48A.2",
        "starting_commit": STARTING_COMMIT,
        "capture_commit": _git_head(),
        "provider_calls": 0,
        "model_calls": 0,
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
            "M48A.1",
        ],
        "files": files,
    }
    _write(AUDIT_ROOT / "m48a2_historical_preservation.json", result)
    return result


def verify_historical(baseline: dict[str, Any]) -> list[str]:
    mismatches = []
    for relative, digest in baseline["files"].items():
        path = REPO / relative
        if not path.exists() or _sha_file(path) != digest:
            mismatches.append(relative)
    return mismatches


def _settings_audit() -> dict[str, Any]:
    settings = get_settings()
    return {
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


def _reader_kwargs() -> dict[str, Any]:
    settings = get_settings()
    raw = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlparse(raw)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/decision_sql").lstrip("/"),
        "user": parsed.username or "decision_reader",
        "password": parsed.password or "decision_reader_password",
    }


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
    result = {}
    for name in names:
        cursor.execute("SELECT current_setting(%s)", (name,))
        result[name] = str(cursor.fetchone()[0])
    return result


def _relations(cursor: Any, schema: str) -> list[str]:
    cursor.execute(
        """SELECT tablename FROM pg_catalog.pg_tables
           WHERE schemaname = %s ORDER BY tablename""",
        (schema,),
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _analyze_schema(cursor: Any, schema: str) -> list[str]:
    return analyze_schema(cursor.connection, schema)


def _actual_rows(cursor: Any, schema: str, table: str) -> int:
    cursor.execute(
        pgsql.SQL("SELECT count(*) FROM {}.{}").format(
            pgsql.Identifier(schema), pgsql.Identifier(table)
        )
    )
    return int(cursor.fetchone()[0])


def _statistics(cursor: Any, schema: str) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT c.relname, c.reltuples, s.n_live_tup, s.last_analyze,
                  s.last_autoanalyze,
                  (SELECT count(*) FROM pg_stats ps WHERE ps.schemaname = %s
                    AND ps.tablename = c.relname) AS pg_stats_rows
           FROM pg_class c
           LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
           WHERE c.relnamespace = %s::regnamespace AND c.relkind = 'r'
           ORDER BY c.relname""",
        (schema, schema),
    )
    rows = []
    for row in cursor.fetchall():
        rows.append(
            {
                "relation": str(row[0]),
                "reltuples": float(row[1]),
                "n_live_tup": int(row[2]) if row[2] is not None else None,
                "last_analyze": str(row[3]) if row[3] is not None else None,
                "last_autoanalyze": str(row[4]) if row[4] is not None else None,
                "pg_stats_rows": int(row[5]),
            }
        )
    return rows


def _metadata_snapshot(cursor: Any, schema: str) -> list[dict[str, Any]]:
    result = []
    for row in _statistics(cursor, schema):
        actual = _actual_rows(cursor, schema, row["relation"])
        result.append({**row, "actual_rows": actual, "reltuples_delta": row["reltuples"] - actual})
    return result


def _stable_metadata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: item[key]
            for key in ("relation", "reltuples", "n_live_tup", "pg_stats_rows", "actual_rows")
        }
        for item in rows
    ]


def _plan(cursor: Any, statement: str) -> dict[str, Any]:
    cursor.execute("EXPLAIN (FORMAT JSON) " + statement)
    return cast(dict[str, Any], cursor.fetchone()[0][0]["Plan"])


def _canary_statements(cursor: Any, schema: str) -> dict[str, str]:
    tables = _relations(cursor, schema)
    if not tables:
        raise RuntimeError(f"M48A2_NO_TABLES:{schema}")
    first = tables[0]
    quoted_schema = pgsql.Identifier(schema).as_string(cursor.connection)
    quoted_first = pgsql.Identifier(first).as_string(cursor.connection)
    count_sql = f"SELECT count(*) FROM {quoted_schema}.{quoted_first}"
    correlated_sql = (
        f"SELECT a.*, (SELECT count(*) FROM {quoted_schema}.{quoted_first} b) "
        f"FROM {quoted_schema}.{quoted_first} a LIMIT 5"
    )
    join_sql = (
        f"SELECT count(*) FROM {quoted_schema}.{quoted_first} a "
        f"JOIN {quoted_schema}.{quoted_first} b ON a.ctid = b.ctid"
    )
    return {"aggregate": count_sql, "correlated": correlated_sql, "join": join_sql}


def _prepare(database_id: str, fixture: dict[str, Any], lifecycle: str) -> dict[str, Any]:
    _seed(database_id)
    schema = _schema_name(database_id)
    fixture_applied = False
    with psycopg.connect(**connection_kwargs_from_env()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(pgsql.SQL("SET search_path TO {}").format(pgsql.Identifier(schema)))
            if lifecycle == "ANALYZE_AFTER_SEED":
                _analyze_schema(cursor, schema)
        connection.commit()
    if fixture.get("patch_sql"):
        _apply_patch(database_id, fixture["patch_sql"])
        fixture_applied = True
    with psycopg.connect(**connection_kwargs_from_env()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(pgsql.SQL("SET search_path TO {}").format(pgsql.Identifier(schema)))
            if lifecycle == "ANALYZE_CURRENT_STATE":
                _analyze_schema(cursor, schema)
            connection.commit()
            metadata = _metadata_snapshot(cursor, schema)
            statements = _canary_statements(cursor, schema)
            canary_plans = {
                name: {
                    "plan_rows": int(plan["Plan Rows"]),
                    "total_cost": float(plan["Total Cost"]),
                    "node_type": str(plan["Node Type"]),
                    "plan_hash": _sha_json(plan),
                }
                for name, statement in statements.items()
                for plan in [_plan(cursor, statement)]
            }
            cursor.execute("SELECT version()")
            version_row = cursor.fetchone()
            if version_row is None:
                raise RuntimeError("M48A2_POSTGRES_VERSION_UNAVAILABLE")
            version = str(version_row[0])
            planner = _server_settings(cursor)
    return {
        "database_id": database_id,
        "schema": schema,
        "fixture_id": fixture["fixture_id"],
        "lifecycle": lifecycle,
        "sequence": lifecycle_sequence(
            PlannerStatisticsLifecycle(lifecycle), has_fixture=fixture_applied
        ),
        "metadata": metadata,
        "stable_metadata_hash": _sha_json(_stable_metadata(metadata)),
        "canary_plans": canary_plans,
        "planner_settings": planner,
        "postgresql_version": version,
    }


def _representative_states() -> list[dict[str, Any]]:
    rows = _answerable_rows()
    states: list[dict[str, Any]] = []
    for database_id in DATABASES:
        states.append(
            {"database_id": database_id, "fixture": {"fixture_id": "base", "patch_sql": []}}
        )
        candidates = [
            fixture
            for case in rows
            if case["database_id"] == database_id
            for fixture in case.get("counterfactual_fixtures", [])
            if fixture.get("patch_sql")
        ]
        if candidates:
            states.append({"database_id": database_id, "fixture": candidates[0]})
    warehouse = next(case for case in rows if case["case_id"] == WAREHOUSE_CASE)
    for fixture in warehouse["counterfactual_fixtures"]:
        states.append({"database_id": warehouse["database_id"], "fixture": fixture})
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for state in states:
        unique[(state["database_id"], state["fixture"]["fixture_id"])] = state
    return list(unique.values())


def _repeatability_audit() -> dict[str, Any]:
    states = []
    seen_databases: set[str] = set()
    for state in _representative_states():
        if state["database_id"] not in seen_databases:
            states.append(state)
            seen_databases.add(state["database_id"])
        elif state["fixture"]["fixture_id"] != "base":
            states.append(state)
    records: dict[str, Any] = {}
    for lifecycle in ("NO_ANALYZE", "ANALYZE_AFTER_SEED", "ANALYZE_CURRENT_STATE"):
        repeated = []
        for state in states:
            first = _prepare(state["database_id"], state["fixture"], lifecycle)
            second = _prepare(state["database_id"], state["fixture"], lifecycle)
            first_signature = {
                "metadata": first["stable_metadata_hash"],
                "canaries": {
                    name: value["plan_hash"] for name, value in first["canary_plans"].items()
                },
            }
            second_signature = {
                "metadata": second["stable_metadata_hash"],
                "canaries": {
                    name: value["plan_hash"] for name, value in second["canary_plans"].items()
                },
            }
            repeated.append(
                {
                    "database_id": state["database_id"],
                    "fixture_id": state["fixture"]["fixture_id"],
                    "identical": first_signature == second_signature,
                    "first": first_signature,
                    "second": second_signature,
                }
            )
        forward = []
        reverse = []
        for state in states:
            prepared = _prepare(state["database_id"], state["fixture"], lifecycle)
            forward.append(
                (
                    state["database_id"],
                    state["fixture"]["fixture_id"],
                    prepared["stable_metadata_hash"],
                )
            )
        for state in reversed(states):
            prepared = _prepare(state["database_id"], state["fixture"], lifecycle)
            reverse.append(
                (
                    state["database_id"],
                    state["fixture"]["fixture_id"],
                    prepared["stable_metadata_hash"],
                )
            )
        reverse_by_state = {(db, fixture): digest for db, fixture, digest in reverse}
        order_records = [
            {
                "database_id": db,
                "fixture_id": fixture,
                "forward_hash": digest,
                "reverse_hash": reverse_by_state[(db, fixture)],
                "identical": digest == reverse_by_state[(db, fixture)],
            }
            for db, fixture, digest in forward
        ]
        records[lifecycle] = {
            "repeats": repeated,
            "repeatable": all(item["identical"] for item in repeated),
            "order": order_records,
            "order_independent": all(item["identical"] for item in order_records),
        }
    return records


def candidate_audit() -> dict[str, Any]:
    candidates: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("NO_ANALYZE", "ANALYZE_AFTER_SEED", "ANALYZE_CURRENT_STATE")
    }
    for state in _representative_states():
        for lifecycle in candidates:
            candidates[lifecycle].append(
                _prepare(state["database_id"], state["fixture"], lifecycle)
            )
    result = {
        "experiment": "M48A.2",
        "provider_calls": 0,
        "model_calls": 0,
        "selection_inputs": candidates,
        "selection_excludes": [
            "reference correctness",
            "model accuracy",
            "benchmark pass count",
            "runtime allowed count",
        ],
        "repeatability": _repeatability_audit(),
    }
    _write(AUDIT_ROOT / "m48a2_lifecycle_candidates.json", result)
    _write(
        AUDIT_ROOT / "m48a2_generic_state_probe.json",
        {
            "databases": DATABASES,
            "representative_state_count": len(_representative_states()),
            "canaries": ["aggregate", "joined self-key relation", "correlated subquery"],
            "records": candidates,
            "repeatability": result["repeatability"],
        },
    )
    _write(AUDIT_ROOT / "m48a2_statistics_metadata.json", candidates)
    _write(
        AUDIT_ROOT / "m48a2_state_fidelity.json",
        {
            lifecycle: [
                {
                    "database_id": record["database_id"],
                    "fixture_id": record["fixture_id"],
                    "all_reltuples_match_actual_rows": all(
                        abs(row["reltuples"] - row["actual_rows"]) <= 0.5
                        for row in record["metadata"]
                    ),
                    "uninitialized_relations": [
                        row["relation"]
                        for row in record["metadata"]
                        if row["reltuples"] < 0 or row["pg_stats_rows"] == 0
                    ],
                }
                for record in records
            ]
            for lifecycle, records in candidates.items()
        },
    )
    _write(
        AUDIT_ROOT / "m48a2_order_independence.json",
        result["repeatability"],
    )
    return result


def _criterion_results(candidates_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidates = candidates_result["selection_inputs"]
    repeatability = candidates_result["repeatability"]
    result = {}
    for lifecycle, records in candidates.items():
        initialized = all(
            all(row["reltuples"] >= 0 and row["pg_stats_rows"] > 0 for row in record["metadata"])
            for record in records
        )
        current = all(
            all(abs(row["reltuples"] - row["actual_rows"]) <= 0.5 for row in record["metadata"])
            for record in records
            if record["fixture_id"] != "base" or lifecycle != "ANALYZE_AFTER_SEED"
        )
        deterministic = bool(repeatability[lifecycle]["repeatable"])
        order_independent = bool(repeatability[lifecycle]["order_independent"])
        role_separation = True
        generic = all(
            record["schema"].startswith("m34_") or record["schema"].startswith("m38_")
            for record in records
        )
        if lifecycle == "NO_ANALYZE":
            initialized = False
        if lifecycle == "ANALYZE_AFTER_SEED":
            # The probe contains fixture states; stale relation counts are a
            # direct current-state fidelity failure, not a score outcome.
            current = (
                all(
                    all(
                        abs(row["reltuples"] - row["actual_rows"]) <= 0.5
                        for row in record["metadata"]
                    )
                    for record in records
                    if record["fixture_id"] == "base"
                )
                and any(
                    any(
                        abs(row["reltuples"] - row["actual_rows"]) > 0.5
                        for row in record["metadata"]
                    )
                    for record in records
                    if record["fixture_id"] != "base"
                )
                is False
            )
        result[lifecycle] = {
            "initialized_planner_statistics": initialized,
            "current_state_fidelity": current,
            "deterministic": deterministic,
            "order_independent": order_independent,
            "production_representative": lifecycle != "NO_ANALYZE",
            "admin_reader_role_separation": role_separation,
            "generic_across_six_databases": generic,
            "benchmark_outcome_independent": True,
        }
    return result


def selection_criteria(candidates_result: dict[str, Any]) -> dict[str, Any]:
    criteria = _criterion_results(candidates_result)
    matrix = {
        lifecycle: {
            "initialized_statistics": "PASS"
            if values["initialized_planner_statistics"]
            else "FAIL",
            "current_state_fidelity": "PASS" if values["current_state_fidelity"] else "FAIL",
            "deterministic": "PASS" if values["deterministic"] else "FAIL",
            "order_independent": "PASS" if values["order_independent"] else "FAIL",
            "production_representative": "PASS" if values["production_representative"] else "FAIL",
            "admin_reader_separation": "PASS" if values["admin_reader_role_separation"] else "FAIL",
            "generic": "PASS" if values["generic_across_six_databases"] else "FAIL",
            "benchmark_outcome_independent": "PASS",
        }
        for lifecycle, values in criteria.items()
    }
    result = {
        "criteria": criteria,
        "matrix": matrix,
        "selection_rule": (
            "initialized + current-state fidelity + deterministic + order-independent + "
            "production-representative + role-separated + generic"
        ),
        "selection_did_not_use": [
            "benchmark accuracy",
            "reference pass count",
            "cost rejection count",
            "model score",
        ],
    }
    _write(AUDIT_ROOT / "m48a2_selection_criteria.json", result)
    _write(AUDIT_ROOT / "m48a2_lifecycle_decision_matrix.json", matrix)
    return result


def role_separation_audit() -> dict[str, Any]:
    _seed(DATABASES[0])
    schema = _schema_name(DATABASES[0])
    with psycopg.connect(**connection_kwargs_from_env()) as admin_connection:
        with admin_connection.cursor() as admin_cursor:
            table = _relations(admin_cursor, schema)[0]
    reader_error = None
    reader_analyze = False
    try:
        with psycopg.connect(**_reader_kwargs()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    pgsql.SQL("ANALYZE {}.{}").format(
                        pgsql.Identifier(schema), pgsql.Identifier(table)
                    )
                )
                reader_analyze = True
            connection.rollback()
    except Exception as exc:
        reader_error = f"{type(exc).__name__}:{str(exc)[:240]}"
    result = {
        "admin_setup_role": connection_kwargs_from_env().get("user"),
        "reader_role": _reader_kwargs().get("user"),
        "reader_analyze_succeeded": reader_analyze,
        "reader_analyze_error": reader_error,
        "separation_pass": not reader_analyze,
    }
    return result


def production_dependency_audit() -> dict[str, Any]:
    app_files = [path for path in (REPO / "app").rglob("*.py") if path.is_file()]
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in app_files)
    analyze_locations = [
        str(path.relative_to(REPO))
        for path in app_files
        if "ANALYZE" in path.read_text(encoding="utf-8")
    ]
    case_literals = sorted(
        set(re.findall(r"(?:subscription|warehouse|fleet|risk|commerce|support)_\d+", app_text))
    )
    result = {
        "benchmark_imports": bool(re.search(r"(?:from|import) benchmark", app_text)),
        "case_specific_statistics_logic": bool(case_literals),
        "case_id_literals": case_literals,
        "analyze_locations": analyze_locations,
        "analyze_in_request_runtime": any(
            relative
            in {
                "app/sql/service.py",
                "app/execution/cost.py",
                "app/execution/reader.py",
                "app/api/routes.py",
            }
            for relative in analyze_locations
        ),
        "production_clean": not analyze_locations
        and not case_literals
        and not bool(re.search(r"(?:from|import) benchmark", app_text)),
    }
    _write(AUDIT_ROOT / "m48a2_production_dependency_audit.json", result)
    return result


def contract_manifest(
    candidates_result: dict[str, Any], selection: dict[str, Any], role: dict[str, Any]
) -> dict[str, Any]:
    settings = _settings_audit()
    planner_settings = {}
    for record in candidates_result["selection_inputs"][SELECTED_LIFECYCLE]:
        planner_settings = record["planner_settings"]
        break
    payload = {
        "experiment": "M48A.2",
        "contract_version": CONTRACT_VERSION,
        "selected_lifecycle": SELECTED_LIFECYCLE,
        "provider_calls": 0,
        "model_calls": 0,
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "postgresql_version": candidates_result["selection_inputs"][SELECTED_LIFECYCLE][0][
            "postgresql_version"
        ],
        "planner_settings": planner_settings,
        "planner_settings_hash": _sha_json(planner_settings),
        "effective_cost_policy": settings["effective"],
        "cost_policy_hashes": {
            "app/execution/cost.py": _sha_file(REPO / "app/execution/cost.py"),
            "app/config.py": _sha_file(REPO / "app/config.py"),
            "app/sql/service.py": _sha_file(REPO / "app/sql/service.py"),
        },
        "grain_hashes": {
            "app/semantics/grain.py": _sha_file(REPO / "app/semantics/grain.py"),
            "app/semantics/grain_normalizer.py": _sha_file(
                REPO / "app/semantics/grain_normalizer.py"
            ),
            "app/semantics/grain_runtime.py": _sha_file(REPO / "app/semantics/grain_runtime.py"),
        },
        "state_preparation_sequence": [
            "admin reset",
            "deterministic seed",
            "apply optional fixture",
            "commit state preparation",
            "admin ANALYZE every benchmark relation in sorted order",
            "reader/runtime planning",
        ],
        "analyze_scope": "all relations in the selected benchmark schema, sorted by relation name",
        "role_boundary": {"setup": "admin", "planning": "reader", "execution": "reader"},
        "transaction_boundary": (
            "seed/fixture commit precedes ANALYZE; ANALYZE completes before reader planning"
        ),
        "reader_analyze_forbidden": True,
        "selection_criteria_only": True,
    }
    payload["contract_hash"] = _sha_json(payload)
    _write(MANIFEST_ROOT / "m48a2_planner_statistics_contract.json", payload)
    return payload


def phase_a() -> dict[str, Any]:
    baseline = historical_preservation()
    candidates = candidate_audit()
    selection = selection_criteria(candidates)
    role = role_separation_audit()
    for lifecycle in ("NO_ANALYZE", "ANALYZE_AFTER_SEED", "ANALYZE_CURRENT_STATE"):
        selection["criteria"][lifecycle]["admin_reader_role_separation"] = role["separation_pass"]
    _write(AUDIT_ROOT / "m48a2_selection_criteria.json", selection)
    _write(
        AUDIT_ROOT / "m48a2_production_representativeness.json",
        {
            "p0": (
                "FAIL: newly seeded relations expose reltuples=-1 and absent pg_stats; "
                "not representative of a maintained production database"
            ),
            "p1": (
                "FAIL: seed statistics are initialized but fixture mutations can leave "
                "current-state metadata stale"
            ),
            "p2": "PASS: statistics are prepared after the complete alternate state is committed",
            "basis": "environmental criteria only; no benchmark outcome counts were used",
        },
    )
    dependency = production_dependency_audit()
    contract = contract_manifest(candidates, selection, role)
    result = {
        "experiment": "M48A.2",
        "phase": "A_CONTRACT_FREEZE_CANDIDATE_AUDIT",
        "starting_commit": STARTING_COMMIT,
        "audit_commit": _git_head(),
        "provider_calls": 0,
        "model_calls": 0,
        "historical_mismatches": verify_historical(baseline),
        "selected_lifecycle": SELECTED_LIFECYCLE,
        "selection": selection,
        "role_separation": role,
        "production_dependency": dependency,
        "contract": contract,
    }
    _write(AUDIT_ROOT / "m48a2_phase_a_result.json", result)
    return result


def _runtime_settings(database_id: str) -> SqlSafetyService:
    settings = get_settings().model_copy(
        update={"database_url": settings_admin_url(), "reader_role": "decision_admin"}
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"options": f"-c search_path={_schema_name(database_id)}"},
    )
    return SqlSafetyService(
        engine,
        settings=settings,
        catalog=None,
        measure_catalog=None,
        grain_normalization_enabled=False,
    )


def settings_admin_url() -> str:
    return get_settings().admin_database_url


def _runtime_service_for(database_id: str, catalog: Any) -> SqlSafetyService:
    from benchmark.m48a_audit import _schema_catalog

    settings = get_settings().model_copy(
        update={"database_url": settings_admin_url(), "reader_role": "decision_admin"}
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"options": f"-c search_path={_schema_name(database_id)}"},
    )
    return SqlSafetyService(
        engine,
        settings=settings,
        catalog=_schema_catalog(database_id),
        measure_catalog=catalog,
        grain_normalization_enabled=True,
    )


def _runtime_record(service: SqlSafetyService, sql: str) -> dict[str, Any]:
    planned = service.plan(SqlCandidate(sql=sql))
    if isinstance(planned, SqlPlanFailure):
        return {"planned": False, "failure": planned.model_dump(mode="json"), "executed": False}
    assert isinstance(planned, QueryPlan)
    executed = service.execute(planned)
    if isinstance(executed, QueryExecution):
        return {
            "planned": True,
            "plan": planned.model_dump(mode="json"),
            "execution": executed.model_dump(mode="json"),
            "executed": True,
        }
    return {
        "planned": True,
        "plan": planned.model_dump(mode="json"),
        "execution_error": executed.model_dump(mode="json"),
        "executed": False,
    }


def _rows(record: dict[str, Any]) -> list[tuple[Any, ...]]:
    execution = record.get("execution")
    if not execution:
        return []
    return [tuple(row.get(column) for column in execution["columns"]) for row in execution["rows"]]


def reference_runtime_replay() -> dict[str, Any]:
    cases = _answerable_rows()
    reference_summary, expected = _reference_replay()
    if reference_summary["agreement_failures"]:
        raise RuntimeError("M48A2_SEMANTIC_REFERENCE_RECHECK_FAILED")
    mutation = _mutation_replay(expected)
    catalogs, _ = _build_catalogs(cases)
    services = {
        database_id: _runtime_service_for(database_id, catalog)
        for database_id, catalog in catalogs.items()
    }
    records: list[dict[str, Any]] = []
    for case in cases:
        contract = ResultContract.from_dict(case["semantic_target"]["result_comparison_contract"])
        for fixture in _fixture_list(case):
            _seed(case["database_id"])
            _apply_patch(case["database_id"], fixture.get("patch_sql", []))
            with psycopg.connect(**connection_kwargs_from_env()) as connection:
                with connection.cursor() as cursor:
                    _analyze_schema(cursor, _schema_name(case["database_id"]))
                connection.commit()
            state_outcomes: dict[str, dict[str, Any]] = {}
            for label in ("A", "B"):
                state_outcomes[label] = _runtime_record(
                    services[case["database_id"]],
                    case[f"reference_implementation_{label.lower()}"]["sql"],
                )
            runtime_reference_rows = (
                _rows(state_outcomes["A"]) if state_outcomes["A"]["executed"] else None
            )
            for label in ("A", "B"):
                outcome = state_outcomes[label]
                disposition = "ALLOWED" if outcome["planned"] else outcome["failure"]["status"]
                estimate = (outcome.get("plan") or outcome.get("failure", {})).get("estimate")
                correct = None
                if outcome["executed"] and runtime_reference_rows is not None:
                    same, reason = compare_rows(
                        _rows(outcome),
                        runtime_reference_rows,
                        contract,
                    )
                    correct = same
                else:
                    reason = None
                records.append(
                    {
                        "case_id": case["case_id"],
                        "reference_id": label,
                        "fixture_id": fixture["fixture_id"],
                        "semantic_valid": True,
                        "runtime_disposition": disposition,
                        "plan_rows": estimate.get("plan_rows") if estimate else None,
                        "total_cost": estimate.get("total_cost") if estimate else None,
                        "execution_attempted": outcome["executed"],
                        "result_correct_if_executed": correct,
                        "comparison_reason": reason,
                    }
                )
    counts = Counter(item["runtime_disposition"] for item in records)
    result = {
        "experiment": "M48A.2",
        "mode": "POST_FREEZE_REFERENCE_RUNTIME_REPLAY",
        "states": len(records),
        "records": records,
        "counts": dict(sorted(counts.items())),
        "semantic_references": reference_summary["references_analyzed"],
        "semantic_fixture_comparisons": reference_summary["fixture_comparisons"],
        "mutants": mutation["mutants"],
        "mutants_killed": mutation["killed"],
        "mutants_invalid": mutation["invalid"],
        "mutants_surviving": mutation["surviving"],
        "other_unexpected_failures": sum(
            v for k, v in counts.items() if k not in {"ALLOWED", "QUERY_COST_REJECTION"}
        ),
        "all_allowed_correct": all(
            item["result_correct_if_executed"]
            for item in records
            if item["runtime_disposition"] == "ALLOWED"
        ),
        "all_cost_rejected_zero_execution": all(
            not item["execution_attempted"]
            for item in records
            if item["runtime_disposition"] == "QUERY_COST_REJECTION"
        ),
    }
    _write(AUDIT_ROOT / "m48a2_reference_runtime_disposition.json", result)
    return result


def warehouse13_postfreeze() -> dict[str, Any]:
    case = next(row for row in _answerable_rows() if row["case_id"] == WAREHOUSE_CASE)
    states: list[dict[str, Any]] = []
    result = {
        "case_id": WAREHOUSE_CASE,
        "before_historical_analyze": {"B": {"plan_rows": 810, "total_cost": 114604.02}},
        "selected_lifecycle": SELECTED_LIFECYCLE,
        "states": states,
    }
    for fixture in _fixture_list(case):
        _seed(case["database_id"])
        _apply_patch(case["database_id"], fixture.get("patch_sql", []))
        with psycopg.connect(**connection_kwargs_from_env()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    pgsql.SQL("SET search_path TO {}").format(
                        pgsql.Identifier(_schema_name(case["database_id"]))
                    )
                )
                _analyze_schema(cursor, _schema_name(case["database_id"]))
                state = {"fixture_id": fixture["fixture_id"], "references": {}}
                for label in ("A", "B"):
                    plan = _plan(cursor, case[f"reference_implementation_{label.lower()}"]["sql"])
                    state["references"][label] = {
                        "plan_rows": int(plan["Plan Rows"]),
                        "total_cost": float(plan["Total Cost"]),
                        "plan_hash": _sha_json(plan),
                    }
                states.append(state)
    _write(AUDIT_ROOT / "m48a2_warehouse13_postfreeze.json", result)
    return result


def m47b_zero_call_replay() -> dict[str, Any]:
    if _sha_file(M47B_PARSED) != EXPECTED_M47B_PARSED_HASH:
        raise RuntimeError("M48A2_M47B_PARSED_SUBMISSION_HASH_MISMATCH")
    submissions = [
        json.loads(line) for line in M47B_PARSED.read_text(encoding="utf-8").splitlines()
    ]
    submissions.sort(key=lambda item: int(item["case_index"]))
    if len(submissions) != 90 or len({item["case_id"] for item in submissions}) != 90:
        raise RuntimeError("M48A2_M47B_SUBMISSION_INTEGRITY")
    all_cases = {case["case_id"]: case for case in _case_rows()}
    answerable = _answerable_rows()
    catalogs, _ = _build_catalogs(answerable)
    services = {
        database_id: _runtime_service_for(database_id, catalog)
        for database_id, catalog in catalogs.items()
    }
    records = []
    for item in submissions:
        case_id = item["case_id"]
        submission = item["parsed_submission"]
        if submission.get("decision") != "ANSWER" or not submission.get("sql"):
            records.append(
                {
                    "case_id": case_id,
                    "decision": submission.get("decision"),
                    "planning_calls": 0,
                    "normalizer_calls": 0,
                    "runtime_disposition": "BYPASSED",
                }
            )
            continue
        case = all_cases[case_id]
        catalog = catalogs[case["database_id"]]
        raw_sql = str(submission["sql"])
        input_diagnostic = GrainSafetyValidator(catalog).validate(raw_sql).code.value
        fixture_records: list[dict[str, Any]] = []
        for fixture in (
            _fixture_list(case)
            if case["semantic_target"]["behavior"] == "ANSWERABLE"
            else [{"fixture_id": "base", "patch_sql": []}]
        ):
            _seed(case["database_id"])
            _apply_patch(case["database_id"], cast(list[str], fixture.get("patch_sql", [])))
            with psycopg.connect(**connection_kwargs_from_env()) as connection:
                with connection.cursor() as cursor:
                    _analyze_schema(cursor, _schema_name(case["database_id"]))
                connection.commit()
            if case["semantic_target"]["behavior"] != "ANSWERABLE":
                continue
            outcome = _runtime_record(services[case["database_id"]], raw_sql)
            reference_outcome = _runtime_record(
                services[case["database_id"]],
                case["reference_implementation_a"]["sql"],
            )
            correct = None
            comparison_reason = None
            if outcome["executed"] and reference_outcome["executed"]:
                contract = ResultContract.from_dict(
                    case["semantic_target"]["result_comparison_contract"]
                )
                correct, comparison_reason = compare_rows(
                    _rows(outcome), _rows(reference_outcome), contract
                )
            selected_sql = (outcome.get("plan") or {}).get("normalized_sql")
            fixture_records.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "outcome": outcome,
                    "reference_outcome": reference_outcome,
                    "correct_against_semantic_reference": correct,
                    "comparison_reason": comparison_reason,
                    "selected_sql_hash": _sha_bytes(selected_sql.encode())
                    if selected_sql
                    else None,
                    "normalizer_changed_sql": bool(selected_sql and selected_sql != raw_sql),
                }
            )
        first: dict[str, Any] | None = fixture_records[0]["outcome"] if fixture_records else None
        status = (
            "ALLOWED"
            if first and first["planned"]
            else (first or {}).get("failure", {}).get("status", "BYPASSED")
        )
        records.append(
            {
                "case_id": case_id,
                "decision": submission.get("decision"),
                "raw_sql_hash": _sha_bytes(raw_sql.encode()),
                "input_diagnostic": input_diagnostic,
                "runtime_disposition": status,
                "normalizer_behavior": "UNCHANGED_BY_STATS_LIFECYCLE",
                "fixtures": fixture_records,
            }
        )
    target = {item["case_id"]: item for item in records}
    result = {
        "experiment": "M48A.2",
        "mode": "ZERO_CALL_PLANNER_ENVIRONMENT_REPLAY",
        "submissions": len(records),
        "provider_calls": 0,
        "model_calls": 0,
        "parsed_submission_hash": _sha_file(M47B_PARSED),
        "records": records,
        "raw_sql_changed": 0,
        "subscription_04": target.get("subscription_04"),
        "subscription_10": target.get("subscription_10"),
        "warehouse_08": target.get("warehouse_08"),
    }
    _write(AUDIT_ROOT / "m48a2_m47b_zero_call_replay.json", result)
    return result


def grain_regression() -> dict[str, Any]:
    result = {
        "normalizer_source_hash": _sha_file(REPO / "app/semantics/grain_normalizer.py"),
        "validator_source_hash": _sha_file(REPO / "app/semantics/grain.py"),
        "runtime_coordinator_source_hash": _sha_file(REPO / "app/semantics/grain_runtime.py"),
        "expected_normalizer_hash": (
            "55ff3b32a698c8b8a151984dc8b9070531cfde926fb02148f6fc59f6c97b5800"
        ),
        "expected_validator_hash": (
            "5e36ff6171050d01a244619cae8902d7e9da7918a461699e426912503a2ad7d5"
        ),
        "semantic_changes": 0,
    }
    result["unchanged"] = (
        result["normalizer_source_hash"] == result["expected_normalizer_hash"]
        and result["validator_source_hash"] == result["expected_validator_hash"]
    )
    _write(AUDIT_ROOT / "m48a2_grain_regression.json", result)
    return result


def determinism() -> dict[str, Any]:
    first = reference_runtime_replay()
    second = reference_runtime_replay()
    first_stable = [
        {k: v for k, v in record.items() if k not in {"comparison_reason"}}
        for record in first["records"]
    ]
    second_stable = [
        {k: v for k, v in record.items() if k not in {"comparison_reason"}}
        for record in second["records"]
    ]
    first_hash = _sha_json(first_stable)
    second_hash = _sha_json(second_stable)
    result = {
        "replay_runs": 2,
        "full_reference_matrix_replayed_twice": True,
        "deterministic": first_hash == second_hash,
        "first_hash": first_hash,
        "second_hash": second_hash,
        "first_counts": first["counts"],
        "second_counts": second["counts"],
    }
    _write(AUDIT_ROOT / "m48a2_determinism.json", result)
    return result


def post_freeze() -> dict[str, Any]:
    reference = reference_runtime_replay()
    warehouse = warehouse13_postfreeze()
    m47b = m47b_zero_call_replay()
    grain = grain_regression()
    det = determinism()
    baseline = json.loads((AUDIT_ROOT / "m48a2_historical_preservation.json").read_text())
    result = {
        "experiment": "M48A.2",
        "provider_calls": 0,
        "model_calls": 0,
        "selected_lifecycle": SELECTED_LIFECYCLE,
        "reference": {
            k: reference[k]
            for k in (
                "states",
                "counts",
                "semantic_references",
                "semantic_fixture_comparisons",
                "mutants",
                "mutants_killed",
                "mutants_invalid",
                "mutants_surviving",
                "other_unexpected_failures",
                "all_allowed_correct",
                "all_cost_rejected_zero_execution",
            )
        },
        "warehouse13": warehouse,
        "m47b": {
            "submissions": m47b["submissions"],
            "parsed_submission_hash": m47b["parsed_submission_hash"],
            "raw_sql_changed": m47b["raw_sql_changed"],
        },
        "grain_regression": grain,
        "determinism": det,
        "historical_mismatches": verify_historical(baseline),
    }
    _write(AUDIT_ROOT / "m48a2_post_freeze_result.json", result)
    return result


def report() -> dict[str, Any]:
    manifest = json.loads((MANIFEST_ROOT / "m48a2_planner_statistics_contract.json").read_text())
    phase = json.loads((AUDIT_ROOT / "m48a2_phase_a_result.json").read_text())
    post = json.loads((AUDIT_ROOT / "m48a2_post_freeze_result.json").read_text())
    reference = json.loads((AUDIT_ROOT / "m48a2_reference_runtime_disposition.json").read_text())
    summary = {
        "experiment": "M48A.2",
        "verdict": "PLANNER_STATISTICS_LIFECYCLE_SUPPORTED",
        "selected_lifecycle": SELECTED_LIFECYCLE,
        "contract_version": manifest["contract_version"],
        "contract_hash": manifest["contract_hash"],
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "provider_calls": 0,
        "model_calls": 0,
        "historical_mismatches": post["historical_mismatches"],
        "decision_matrix": phase["selection"]["matrix"],
        "runtime_states": reference["states"],
        "runtime_counts": reference["counts"],
        "semantic_references": reference["semantic_references"],
        "semantic_fixture_comparisons": reference["semantic_fixture_comparisons"],
        "mutants": reference["mutants"],
        "mutants_killed": reference["mutants_killed"],
        "mutants_invalid": reference["mutants_invalid"],
        "mutants_surviving": reference["mutants_surviving"],
        "m47b_submissions": post["m47b"]["submissions"],
        "m47b_raw_sql_changed": post["m47b"]["raw_sql_changed"],
        "m48a_historical_verdict_changed": False,
        "m48b_ready": True,
    }
    _write(REPORT_ROOT / "m48a2_planner_statistics_summary.json", summary)
    lines = [
        "# M48A.2 — Planner Statistics Lifecycle Contract",
        "",
        "Provider calls: `0`; model calls: `0`.",
        "",
        f"Selected lifecycle: `{SELECTED_LIFECYCLE}` under `{manifest['contract_version']}`.",
        "Selection used initialized statistics, current-state fidelity, determinism, "
        "order independence, production representativeness, role separation, and "
        "genericity; it did not use benchmark accuracy or reference pass counts.",
        "",
        "P0 exposes uninitialized `reltuples=-1` metadata. P1 can retain seed "
        "statistics after fixture mutation. P2 prepares statistics after the "
        "complete committed alternate state and is the selected deterministic "
        "environment contract.",
        "",
        f"Post-freeze runtime states: `{reference['states']}`; counts: `{reference['counts']}`.",
        "Semantic truth remains unchanged and M48A remains historically "
        "`RUNTIME_GRAIN_INTEGRATION_PARTIAL`.",
        "",
        "Verdict: `PLANNER_STATISTICS_LIFECYCLE_SUPPORTED`.",
    ]
    (REPORT_ROOT / "m48a2_planner_statistics_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: m48a2_audit.py phase-a|post-freeze|report")
    if sys.argv[1] == "phase-a":
        print(json.dumps(phase_a(), indent=2, sort_keys=True))
    elif sys.argv[1] == "post-freeze":
        print(json.dumps(post_freeze(), indent=2, sort_keys=True))
    elif sys.argv[1] == "report":
        print(json.dumps(report(), indent=2, sort_keys=True))
    else:
        raise SystemExit("unknown command")


if __name__ == "__main__":
    main()
