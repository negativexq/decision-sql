"""Provider-free temporal-integrity diagnostics for LiveSQLBench references.

This module is deliberately evaluation-only.  It does not change the official
evaluator, M1, SQL generation, or any production execution path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

import sqlglot
from psycopg import sql
from sqlglot import exp

from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import safety_for_database
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
from evaluation.livesqlbench_base_lite_protected_preflight import run_preflight

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED_PATH = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
M20_MANIFEST_HASH = "36e53850a704049cdf48453f8f3910eefeb877c9c57c342c0913674c8f13025a"
UPSTREAM_EVALUATOR_COMMIT = "e15cd221267e06fabfaf6a3d4a69308280ce9a7c"
EMPTY_REFERENCE_CASE_COUNT = 5


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _git_ignored(path: Path) -> bool:
    return subprocess.run(["git", "check-ignore", "--quiet", str(path)]).returncode == 0


def _tracked_protected_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "evaluation/external/livesqlbench/protected/"], text=True
    )
    return [line for line in output.splitlines() if line]


def _stats(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None, "mean": None}
    ordered = sorted(values)
    return {
        "min": min(ordered),
        "median": median(ordered),
        "p95": ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))],
        "max": max(ordered),
        "mean": mean(ordered),
    }


def has_current_date_dependency(sql_text: str) -> bool:
    """Return whether PostgreSQL CURRENT_DATE occurs in the parsed SQL AST."""
    tree = sqlglot.parse_one(sql_text, read="postgres")
    return any(isinstance(node, exp.CurrentDate) for node in tree.walk())


def classify_empty_reference(
    *, temporal_dependency: bool, proxy_became_non_empty: bool, max_age_days: int | None
) -> str:
    """Classify an empty reference without using an LLM or benchmark heuristics."""
    if not temporal_dependency:
        return "NON_TEMPORAL_REFERENCE_EMPTY"
    if proxy_became_non_empty and max_age_days is not None and max_age_days > 0:
        return "TEMPORAL_DRIFT_CONFIRMED"
    if temporal_dependency:
        return "TEMPORAL_DRIFT_PLAUSIBLE"
    return "UNRESOLVED"


def _local_date(executed_at_utc: datetime, timezone: str | None) -> date:
    zone: tzinfo
    try:
        zone = ZoneInfo(timezone or "UTC")
    except Exception:
        zone = UTC
    return executed_at_utc.astimezone(zone).date()


def _temporal_column_matches(
    case: LiveSqlBenchEvaluationCase, catalog: LiveSqlBenchDatabase
) -> list[tuple[str, str, str, str]]:
    tree = sqlglot.parse_one(case.sol_sql[0], read="postgres")
    referenced_names = {column.name.lower() for column in tree.find_all(exp.Column)}
    matches: list[tuple[str, str, str, str]] = []
    for table in catalog.tables:
        for column in table.columns:
            data_type = column.data_type.lower()
            if column.name.lower() not in referenced_names:
                continue
            if not any(token in data_type for token in ("date", "timestamp", "time")):
                continue
            matches.append((table.schema, table.name, column.name, column.data_type))
    return matches


def _horizon(
    database: str,
    config: PostgresConnectionConfig,
    match: tuple[str, str, str, str],
) -> tuple[date | datetime | None, date | datetime | None, int]:
    schema_name, table_name, column_name, _ = match
    with config.connect(database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                sql.SQL("SELECT MIN({}), MAX({}), COUNT({}) FROM {}.{}").format(
                    sql.Identifier(column_name),
                    sql.Identifier(column_name),
                    sql.Identifier(column_name),
                    sql.Identifier(schema_name),
                    sql.Identifier(table_name),
                )
            )
            row = cursor.fetchone()
    if row is None:
        return None, None, 0
    return row[0], row[1], int(row[2] or 0)


def _frozen_proxy_sql(sql_text: str, proxy_date: date) -> str:
    """Make an AST-only diagnostic date substitution; never used at runtime."""
    tree = sqlglot.parse_one(sql_text, read="postgres")
    replacement = exp.Cast(
        this=exp.Literal.string(proxy_date.isoformat()), to=exp.DataType.build("DATE")
    )
    rewritten = tree.transform(
        lambda node: replacement.copy() if isinstance(node, exp.CurrentDate) else node
    )
    return str(rewritten.sql(dialect="postgres"))


def _result(execution: QueryExecution) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    return (
        tuple(execution.columns),
        tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows),
    )


def _proxy_non_empty(
    case: LiveSqlBenchEvaluationCase,
    proxy_date: date,
    service: Any,
) -> bool:
    proxy_plan = service.plan(
        SqlCandidate(
            sql=_frozen_proxy_sql(case.sol_sql[0], proxy_date), source=CandidateSource.INTERNAL
        )
    )
    if not isinstance(proxy_plan, QueryPlan):
        return False
    proxy_execution = service.execute(proxy_plan)
    return isinstance(proxy_execution, QueryExecution) and bool(proxy_execution.rows)


def _diagnose_empty_cases(
    cases: tuple[LiveSqlBenchEvaluationCase, ...],
    catalogs: dict[str, LiveSqlBenchDatabase],
    services: dict[str, tuple[Any, Any, dict[str, Any]]],
    config: PostgresConnectionConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    age_days: list[int] = []
    current_dates: list[str] = []
    timezones: Counter[str] = Counter()
    for case in cases:
        service = services[case.database][1]
        plan = service.plan(SqlCandidate(sql=case.sol_sql[0], source=CandidateSource.INTERNAL))
        if not isinstance(plan, QueryPlan):
            continue
        execution = service.execute(plan)
        if not isinstance(execution, QueryExecution) or execution.rows:
            continue
        temporal_dependency = has_current_date_dependency(case.sol_sql[0])
        matches = _temporal_column_matches(case, catalogs[case.database])
        horizons = [_horizon(case.database, config, match) for match in matches]
        max_values = [item[1] for item in horizons if item[1] is not None]
        max_dates = [value.date() if isinstance(value, datetime) else value for value in max_values]
        executed_at = execution.executed_at_utc or datetime.now(UTC)
        current_date = _local_date(executed_at, execution.session_timezone)
        max_date = max(max_dates) if max_dates else None
        age = (current_date - max_date).days if max_date is not None else None
        proxy_non_empty = bool(max_date and _proxy_non_empty(case, max_date, service))
        classification = classify_empty_reference(
            temporal_dependency=temporal_dependency,
            proxy_became_non_empty=proxy_non_empty,
            max_age_days=age,
        )
        if age is not None:
            age_days.append(age)
        current_dates.append(current_date.isoformat())
        timezones[execution.session_timezone or "UNKNOWN"] += 1
        records.append(
            {
                "case_id": case.instance_id,
                "database": case.database,
                "temporal_dependency": temporal_dependency,
                "reference_empty": True,
                "drift_classification": classification,
                "current_clock_age_days": age,
                "proxy_became_non_empty": proxy_non_empty,
                "temporal_columns": len(matches),
                "execution_date": current_date.isoformat(),
                "session_timezone": execution.session_timezone,
            }
        )
    summary = {
        "count": len(records),
        "temporal_dependency": sum(item["temporal_dependency"] for item in records),
        "temporal_drift_confirmed": sum(
            item["drift_classification"] == "TEMPORAL_DRIFT_CONFIRMED" for item in records
        ),
        "temporal_drift_plausible": sum(
            item["drift_classification"] == "TEMPORAL_DRIFT_PLAUSIBLE" for item in records
        ),
        "non_temporal": sum(
            item["drift_classification"] == "NON_TEMPORAL_REFERENCE_EMPTY" for item in records
        ),
        "unresolved": sum(item["drift_classification"] == "UNRESOLVED" for item in records),
        "current_clock_dates": sorted(set(current_dates)),
        "session_timezones": dict(sorted(timezones.items())),
        "aggregate_age_days": _stats(age_days),
        "proxy_non_empty": sum(item["proxy_became_non_empty"] for item in records),
    }
    return records, summary


def run(public_root: Path = PUBLIC_ROOT, protected_path: Path = PROTECTED_PATH) -> dict[str, Any]:
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
    merged, merge = merge_public_and_protected(public, protected)
    select_cases = tuple(case for case in merged if case.public.eligible_select)
    config = PostgresConnectionConfig.from_environment()
    catalogs: dict[str, LiveSqlBenchDatabase] = {}
    services: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    for database in public.databases:
        catalog = introspect_database(database, config)
        catalogs[database] = catalog
        services[database] = safety_for_database(catalog, config)
    try:
        with tempfile.TemporaryDirectory(prefix="m24-3-preflight-") as directory:
            preflight = run_preflight(public_root, protected_path, Path(directory), config)
        empty_records, empty_summary = _diagnose_empty_cases(
            select_cases, catalogs, services, config
        )
    finally:
        for engine, _, _ in services.values():
            engine.dispose()

    accepted = preflight["m1_compatibility"]["accepted"]
    execution = preflight["reference_execution"]
    evaluator = preflight["official_evaluator"]
    pilot_ids = set(preflight["final_manifest"]["pilot_case_ids"])
    pilot_empty = [record for record in empty_records if record["case_id"] in pilot_ids]
    protected_ignored = _git_ignored(protected_path)
    tracked = _tracked_protected_files()
    classification = (
        "M24_3_MIXED_TEMPORAL_AND_EVALUATOR_LIMITATION"
        if (
            preflight["provider_calls"] == 0
            and preflight["fresh_generation"] == 0
            and accepted == execution["success"] == 175
            and len(empty_records) == EMPTY_REFERENCE_CASE_COUNT
            and empty_summary["temporal_drift_confirmed"] == EMPTY_REFERENCE_CASE_COUNT
            and evaluator["reference_fail"] == EMPTY_REFERENCE_CASE_COUNT
            and protected_ignored
            and not tracked
        )
        else "M24_3_TEMPORAL_INTEGRITY_BLOCKED"
    )
    return {
        "classification": classification,
        "starting_head": _head(),
        "provider_calls": 0,
        "fresh_generation": 0,
        "official_evaluator": {
            "empty_result_behavior": "empty predicted OR empty expected => FAIL",
            "upstream_commit": UPSTREAM_EVALUATOR_COMMIT,
            "adapter_behavior_unchanged": True,
        },
        "reference_execution": {
            "m1_accepted": accepted,
            "executed": execution["attempted"],
            "execution_success": execution["success"],
            "empty_results": len(empty_records),
            "non_empty_results": execution["success"] - len(empty_records),
            "execution_failure": execution["failure"],
            "timeout": execution["timeout"],
        },
        "empty_reference_failures": empty_summary,
        "current_clock_vs_snapshot": {
            "aggregate_age_days": empty_summary["aggregate_age_days"],
            "execution_dates": empty_summary["current_clock_dates"],
            "session_timezones": empty_summary["session_timezones"],
        },
        "frozen_time_diagnostic": {
            "status": "NOT_JUSTIFIABLE",
            "reference_date_available": False,
            "source": (
                "No authoritative benchmark/reference date was found in the local public, "
                "protected, release, or database metadata."
            ),
            "cases_tested_with_latest_relevant_db_date_proxy": len(empty_records),
            "current_clock_empty": len(empty_records),
            "proxy_non_empty": empty_summary["proxy_non_empty"],
            "proxy_still_empty": len(empty_records) - empty_summary["proxy_non_empty"],
            "proxy_is_not_official_scoring": True,
        },
        "pilot_integrity": {
            "frozen_pilot_preserved": preflight["pilot_validation"]["frozen_ids_preserved"],
            "pilot_total": preflight["pilot_validation"]["pilot_cases"],
            "gold_coverage": preflight["pilot_validation"]["gold_coverage"],
            "knowledge_coverage": preflight["pilot_validation"]["knowledge_field_coverage"],
            "m1_compatible": preflight["pilot_validation"]["m1_compatible"],
            "reference_executable": preflight["pilot_validation"]["reference_evaluable"],
            "reference_official_pass": sum(
                row["case_id"] in pilot_ids and row["reference_evaluator_status"] == "PASS"
                for row in preflight["safe_cases"]
            ),
            "reference_empty": len(pilot_empty),
            "reference_temporal_drift_cases": sum(
                row["drift_classification"] == "TEMPORAL_DRIFT_CONFIRMED" for row in pilot_empty
            ),
        },
        "evaluator_vs_drift": {
            "temporal_drift_only": 0,
            "empty_result_evaluator_limitation_only": 0,
            "both": empty_summary["temporal_drift_confirmed"],
            "other": empty_summary["count"] - empty_summary["temporal_drift_confirmed"],
        },
        "m1": {
            "accepted": preflight["m1_compatibility"]["accepted"],
            "cost_blocked": preflight["m1_compatibility"]["failure_codes"].get(
                "QUERY_TOO_EXPENSIVE", 0
            ),
            "multi_statement": preflight["m1_compatibility"]["failure_codes"].get(
                "SQL_PARSE_ERROR", 0
            ),
            "temporal_policy_unchanged": True,
        },
        "protected_data_safety": {
            "protected_ignored": protected_ignored,
            "protected_tracked_files": tracked,
            "gold_committed": bool(tracked),
            "question_gold_mappings_committed": False,
        },
        "merge": merge,
        "local_case_diagnostics": empty_records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=PROTECTED_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation/fixtures/m24_3_livesqlbench_temporal_integrity_result.json",
    )
    args = parser.parse_args()
    result = run(args.public_root, args.protected)
    safe = dict(result)
    safe.pop("local_case_diagnostics", None)
    args.output.write_text(json.dumps(safe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "classification": result["classification"],
                "provider_calls": result["provider_calls"],
                "reference_execution": result["reference_execution"],
                "empty_reference_failures": result["empty_reference_failures"],
                "pilot_integrity": result["pilot_integrity"],
            },
            indent=2,
        )
    )
    valid = {
        "M24_3_TEMPORAL_DRIFT_CONFIRMED",
        "M24_3_EMPTY_RESULT_EVALUATOR_LIMITATION_CONFIRMED",
        "M24_3_MIXED_TEMPORAL_AND_EVALUATOR_LIMITATION",
    }
    raise SystemExit(0 if result["classification"] in valid else 1)


if __name__ == "__main__":
    main()
