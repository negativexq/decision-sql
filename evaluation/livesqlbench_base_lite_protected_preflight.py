"""Provider-free protected LiveSQLBench GT merge and reference preflight."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any

import sqlglot
from sqlglot import exp

from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.external.livesqlbench.loader import (
    DATASET_FILENAME,
    load_dataset,
    stable_json_hash,
)
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
    render_schema_context,
)

PUBLIC_MANIFEST_HASH = "8f8efaaebef80f67a62f1c6875e122e51da1323f5f04c3f07a052c7d4047f359"
M20_MANIFEST_HASH = "36e53850a704049cdf48453f8f3910eefeb877c9c57c342c0913674c8f13025a"
PROTECTED_FILENAME = "livesqlbench_gt_kg_testcases_0528.jsonl"


def _stats(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None, "mean": None}
    ordered = sorted(values)
    return {
        "min": min(values),
        "median": median(values),
        "p95": ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))],
        "max": max(values),
        "mean": mean(values),
    }


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _git_dirty() -> bool:
    return bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())


def _git_ignored(path: Path) -> bool:
    return (
        subprocess.run(["git", "check-ignore", "--quiet", str(path)], check=False).returncode == 0
    )


def _tracked_protected_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "evaluation/external/livesqlbench/protected/"], text=True
    )
    return [line for line in output.splitlines() if line]


def _shape(sql: str) -> dict[str, Any]:
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return {
            "parseable": False,
            "multi_statement": False,
            "select_or_set": False,
            "read_only": False,
        }
    if len(statements) != 1:
        return {
            "parseable": True,
            "multi_statement": True,
            "select_or_set": False,
            "read_only": False,
        }
    tree = statements[0]
    if tree is None:
        return {
            "parseable": False,
            "multi_statement": False,
            "select_or_set": False,
            "read_only": False,
        }
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Merge,
        exp.Command,
        exp.Copy,
        exp.Transaction,
        exp.Lock,
    )
    select_or_set = isinstance(tree, (exp.Select, exp.SetOperation))
    read_only = select_or_set and not any(tree.find(node) is not None for node in forbidden)
    return {
        "parseable": True,
        "multi_statement": False,
        "select_or_set": select_or_set,
        "read_only": read_only,
        "joins": len(list(tree.find_all(exp.Join))),
        "aggregation": any(isinstance(node, exp.AggFunc) for node in tree.walk()),
        "group_by": tree.find(exp.Group) is not None,
        "having": tree.find(exp.Having) is not None,
        "subquery": tree.find(exp.Subquery) is not None,
        "cte": tree.find(exp.CTE) is not None,
        "window": tree.find(exp.Window) is not None,
        "set_operation": isinstance(tree, exp.SetOperation),
        "distinct": any(isinstance(node, exp.Distinct) for node in tree.walk()),
        "order_by": tree.find(exp.Order) is not None,
        "limit": tree.find(exp.Limit) is not None,
    }


def _classify_m1_failure(sql: str, plan: Any, database: LiveSqlBenchDatabase | None = None) -> str:
    if "Multiple SQL statements are not allowed" in str(getattr(plan, "error", "")):
        return "LEGITIMATE_M1_POLICY_REJECTION"
    if (
        getattr(plan, "rejection", None) is not None
        and plan.rejection.code.value == "UNKNOWN_COLUMN"
        and database is not None
    ):
        object_name = str(plan.rejection.object or "")
        object_name_without_comment = object_name.split("/*", 1)[0].strip()
        try:
            tree = sqlglot.parse_one(sql, read="postgres")
            columns = {
                column.sql(dialect="postgres"): column for column in tree.find_all(exp.Column)
            }
            column = columns.get(object_name_without_comment)
            if column is None:
                column = next(
                    (
                        candidate
                        for rendered, candidate in columns.items()
                        if rendered.lower() == object_name_without_comment.lower()
                    ),
                    None,
                )
            qualifier = column.table.lower() if column is not None else ""
            cte_names = {
                cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
            }
            cte_aliases = set(cte_names)
            for table in tree.find_all(exp.Table):
                if table.name.lower() in cte_names:
                    cte_aliases.add(table.alias_or_name.lower())
            derived_names: set[str] = set()
            for node in tree.find_all(exp.Subquery):
                if node.alias_or_name:
                    derived_names.add(node.alias_or_name.lower())
            for lateral_node in tree.find_all(exp.Lateral):
                if lateral_node.alias_or_name:
                    derived_names.add(lateral_node.alias_or_name.lower())
            if qualifier in cte_aliases or qualifier in derived_names:
                return "M1_IMPLEMENTATION_DEFECT"
        except sqlglot.errors.ParseError:
            pass
    if getattr(plan, "rejection", None) is not None:
        return "LEGITIMATE_M1_POLICY_REJECTION"
    status = getattr(plan, "status", None)
    if str(status) == "SQL_PARSE_ERROR":
        return "DIALECT_OR_PARSER_INCOMPATIBILITY"
    if str(status) == "EXECUTION_ERROR":
        return "M1_IMPLEMENTATION_DEFECT"
    return "UNDETERMINED"


def _result(execution: QueryExecution) -> LiveSqlBenchResult:
    return LiveSqlBenchResult(
        tuple(execution.columns),
        tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows),
    )


def _safe_case_record(
    case: LiveSqlBenchEvaluationCase,
    shape: dict[str, Any],
    m1_status: str,
    failure_code: str | None,
    failure_classification: str | None,
    execution_status: str,
    evaluator_status: str,
) -> dict[str, Any]:
    return {
        "case_id": case.instance_id,
        "database": case.database,
        "category": case.public.category,
        "difficulty_tier": case.public.difficulty_tier,
        "knowledge_present": bool(case.runtime.external_knowledge),
        "test_case_count": len(case.test_cases),
        "default_query_conditions_present": bool(case.public.conditions),
        "gold_sql_present": bool(case.sol_sql),
        "gold_sql_shape": shape,
        "m1_status": m1_status,
        "m1_failure_code": failure_code,
        "m1_failure_classification": failure_classification,
        "execution_status": execution_status,
        "reference_evaluator_status": evaluator_status,
    }


def run_preflight(
    public_root: Path,
    protected_path: Path,
    output_dir: Path,
    config: PostgresConnectionConfig,
) -> dict[str, Any]:
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
    merged, merge = merge_public_and_protected(public, protected)
    merged_by_id = {case.instance_id: case for case in merged}
    select_cases = tuple(case for case in merged if case.public.eligible_select)
    management_cases = tuple(case for case in merged if not case.public.eligible_select)
    public_manifest = json.loads(
        (Path("evaluation/fixtures/livesqlbench_base_lite_preflight_manifest.json")).read_text()
    )
    pilot_ids = tuple(public_manifest["future_baseline"]["pilot_case_ids"])
    pilot_cases = tuple(merged_by_id[case_id] for case_id in pilot_ids if case_id in merged_by_id)

    schema_records: list[dict[str, Any]] = []
    schema_contexts: dict[str, str] = {}
    live_catalogs: dict[str, LiveSqlBenchDatabase] = {}
    database_errors: list[str] = []
    for database in public.databases:
        try:
            catalog = introspect_database(database, config)
            live_catalogs[database] = catalog
            context = render_schema_context(catalog)
            schema_contexts[database] = context
            schema_records.append(
                {
                    "database": database,
                    "select_cases": sum(case.database == database for case in select_cases),
                    "tables": catalog.table_count,
                    "columns": catalog.column_count,
                    "catalog_hash": catalog.stable_identity(),
                }
            )
        except Exception as error:
            database_errors.append(f"{database}: {type(error).__name__}")

    shape_rows = {case.instance_id: _shape(case.sol_sql[0]) for case in select_cases}
    shape_counts: dict[str, int] = {
        "joins": sum(int(shape.get("joins", 0) > 0) for shape in shape_rows.values()),
        "aggregation": sum(bool(shape.get("aggregation")) for shape in shape_rows.values()),
        "group_by": sum(bool(shape.get("group_by")) for shape in shape_rows.values()),
        "having": sum(bool(shape.get("having")) for shape in shape_rows.values()),
        "subquery": sum(bool(shape.get("subquery")) for shape in shape_rows.values()),
        "cte": sum(bool(shape.get("cte")) for shape in shape_rows.values()),
        "window": sum(bool(shape.get("window")) for shape in shape_rows.values()),
        "set_operations": sum(bool(shape.get("set_operation")) for shape in shape_rows.values()),
        "distinct": sum(bool(shape.get("distinct")) for shape in shape_rows.values()),
        "order_by": sum(bool(shape.get("order_by")) for shape in shape_rows.values()),
        "limit": sum(bool(shape.get("limit")) for shape in shape_rows.values()),
    }
    parse_counts = {
        "total": len(select_cases),
        "empty_gold_sql": sum(not case.sol_sql for case in select_cases),
        "parseable_postgresql": sum(bool(shape.get("parseable")) for shape in shape_rows.values()),
        "unparseable": sum(not bool(shape.get("parseable")) for shape in shape_rows.values()),
        "multi_statement": sum(bool(shape.get("multi_statement")) for shape in shape_rows.values()),
        "select_or_set": sum(bool(shape.get("select_or_set")) for shape in shape_rows.values()),
        "read_only": sum(bool(shape.get("read_only")) for shape in shape_rows.values()),
    }

    safe_cases: list[dict[str, Any]] = []
    failure_code_counts: Counter[str] = Counter()
    failure_class_counts: Counter[str] = Counter()
    m1_counts: Counter[str] = Counter()
    execution_counts: Counter[str] = Counter()
    evaluator_counts: Counter[str] = Counter()
    reference_results: dict[str, LiveSqlBenchResult] = {}
    evaluator_control_counts: Counter[str] = Counter()
    services: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    for case in select_cases:
        shape = shape_rows[case.instance_id]
        try:
            if case.database not in services:
                catalog = live_catalogs.get(case.database) or introspect_database(
                    case.database, config
                )
                services[case.database] = safety_for_database(catalog, config)
            engine, safety, _ = services[case.database]
            plan = safety.plan(SqlCandidate(sql=case.sol_sql[0], source=CandidateSource.INTERNAL))
            if isinstance(plan, QueryPlan):
                m1_counts["ACCEPTED"] += 1
                execution = safety.execute(plan)
                if isinstance(execution, QueryExecution):
                    execution_counts["SUCCESS"] += 1
                    result_rows = _result(execution)
                    reference_results[case.instance_id] = result_rows
                    evaluator_pass = soft_ex_match(
                        result_rows,
                        result_rows,
                        ordered=bool(case.public.conditions.get("order", False)),
                    )
                    evaluator_status = "PASS" if evaluator_pass else "FAIL"
                    evaluator_counts[evaluator_status] += 1
                    if evaluator_pass:
                        evaluator_control_counts["known_good_pass"] += 1
                        wrong_row = ("__LIVESQLBENCH_WRONG_RESULT__",) * max(
                            len(result_rows.columns), 1
                        )
                        wrong_result = LiveSqlBenchResult(
                            result_rows.columns,
                            result_rows.rows + (wrong_row,),
                        )
                        if not soft_ex_match(
                            wrong_result,
                            result_rows,
                            ordered=bool(case.public.conditions.get("order", False)),
                        ):
                            evaluator_control_counts["mutated_wrong_rejected"] += 1
                        else:
                            evaluator_control_counts["mutated_wrong_false_accept"] += 1
                    safe_cases.append(
                        _safe_case_record(
                            case,
                            shape,
                            "ACCEPTED",
                            None,
                            None,
                            "SUCCESS",
                            evaluator_status,
                        )
                    )
                else:
                    execution_counts["FAILURE"] += 1
                    evaluator_counts["NOT_EVALUATED"] += 1
                    safe_cases.append(
                        _safe_case_record(
                            case,
                            shape,
                            "ACCEPTED",
                            None,
                            None,
                            "FAILURE",
                            "NOT_EVALUATED",
                        )
                    )
            else:
                m1_status = "POLICY_REJECTED" if plan.rejection is not None else "PLANNING_ERROR"
                m1_counts[m1_status] += 1
                failure_code = (
                    plan.rejection.code.value if plan.rejection is not None else plan.status.value
                )
                classification = _classify_m1_failure(
                    case.sol_sql[0], plan, live_catalogs.get(case.database)
                )
                failure_code_counts[failure_code] += 1
                failure_class_counts[classification] += 1
                execution_counts["NOT_ATTEMPTED_M1"] += 1
                evaluator_counts["NOT_EVALUATED"] += 1
                safe_cases.append(
                    _safe_case_record(
                        case,
                        shape,
                        m1_status,
                        failure_code,
                        classification,
                        "NOT_ATTEMPTED_M1",
                        "NOT_EVALUATED",
                    )
                )
        except Exception as error:
            m1_counts["PLANNING_ERROR"] += 1
            failure_code = type(error).__name__
            failure_code_counts[failure_code] += 1
            failure_class_counts["M1_IMPLEMENTATION_DEFECT"] += 1
            execution_counts["NOT_ATTEMPTED_M1"] += 1
            evaluator_counts["NOT_EVALUATED"] += 1
            safe_cases.append(
                _safe_case_record(
                    case,
                    shape,
                    "PLANNING_ERROR",
                    failure_code,
                    "M1_IMPLEMENTATION_DEFECT",
                    "NOT_ATTEMPTED_M1",
                    "NOT_EVALUATED",
                )
            )
    for engine, _, _ in services.values():
        engine.dispose()

    knowledge_sizes = [
        len(json.dumps(case.runtime.external_knowledge, ensure_ascii=False))
        for case in select_cases
    ]
    context_sizes = [len(schema_contexts[case.database]) for case in select_cases]
    combined_sizes = [
        len(case.runtime.question)
        + len(json.dumps(case.runtime.external_knowledge, ensure_ascii=False))
        + len(schema_contexts[case.database])
        for case in select_cases
    ]
    test_case_sizes = [len(case.test_cases) for case in select_cases]
    m1_by_id = {record["case_id"]: record for record in safe_cases}
    public_ids = {case.instance_id for case in public.cases}
    protected_ids = {row.instance_id for row in protected.rows}
    protected_ignored = _git_ignored(protected_path)
    tracked_protected = _tracked_protected_files()
    merge_ok = (
        merge["public_rows"] == 270
        and merge["protected_rows"] == len(protected.rows)
        and merge["exact_matches"] == 270
        and not merge["unmatched_public"]
        and not merge["unmatched_protected"]
        and public_ids == protected_ids
    )
    m1_gate_ok = (
        m1_counts["ACCEPTED"] == len(select_cases)
        and not m1_counts["POLICY_REJECTED"]
        and not m1_counts["PLANNING_ERROR"]
    )
    execution_gate_ok = (
        execution_counts["SUCCESS"] == len(select_cases) and not execution_counts["FAILURE"]
    )
    evaluator_gate_ok = evaluator_counts["PASS"] == len(select_cases)
    artifact_ready = bool(
        protected_ignored
        and not tracked_protected
        and merge_ok
        and not database_errors
        and len(select_cases) == 180
        and parse_counts["parseable_postgresql"] == 180
        and m1_gate_ok
        and execution_gate_ok
        and evaluator_gate_ok
    )

    m1_config = {
        "max_plan_rows": 100_000,
        "max_plan_cost": 100_000.0,
        "max_result_rows": 1_000,
        "statement_timeout_ms": 5_000,
        "reader_role": config.user,
    }
    final_manifest = {
        "benchmark": "LiveSQLBench",
        "edition": "Base-Lite",
        "dialect": "PostgreSQL",
        "public_dataset": {
            "commit": "507d7d98f477b9d6a990628ea021bd2501cfd6e2",
            "manifest_hash": PUBLIC_MANIFEST_HASH,
            "sha256": public.source_sha256,
        },
        "protected_source": {
            "filename": PROTECTED_FILENAME,
            "sha256": protected.sha256,
            "rows": len(protected.rows),
            "field_names": list(protected.field_names),
        },
        "population": {
            "total": len(public.cases),
            "select": len(select_cases),
            "management": len(management_cases),
            "select_case_ids": [case.instance_id for case in select_cases],
        },
        "pilot_case_ids": list(pilot_ids),
        "database": {
            "connection_identity": config.public_identity(),
            "catalogs": schema_records,
            "image": "docker.io/shawnxxh/bird-interact-postgresql:latest",
            "image_digest": (
                "sha256:1ae45d7aa5d64dd8eb82e4058f56b4b9625d5035b9b9dc0d2afa0295d9d3053c"
            ),
        },
        "schema_renderer_hash": stable_json_hash(schema_contexts),
        "m1_config_hash": stable_json_hash(m1_config),
        "m1_config": m1_config,
        "evaluator_hash": stable_json_hash({"adapter": "official Query Soft-EX", "version": 1}),
        "runtime_message_construction_hash": stable_json_hash(
            {"fields": ["question", "external_knowledge", "schema_context"], "gold_fields": []}
        ),
        "decision_sql_commit": _git_head(),
        "decision_sql_dirty": _git_dirty(),
        "m20_manifest_hash": M20_MANIFEST_HASH,
    }
    final_manifest_hash = stable_json_hash(final_manifest)
    final_manifest["manifest_hash"] = final_manifest_hash
    result = {
        "classification": "LIVESQLBENCH_BASE_LITE_FINAL_PREFLIGHT_VALIDATED"
        if artifact_ready
        else "LIVESQLBENCH_BASE_LITE_FINAL_PREFLIGHT_BLOCKED",
        "provider_calls": 0,
        "fresh_generation": 0,
        "production_changes": 0,
        "protected_source": {
            "filename": PROTECTED_FILENAME,
            "sha256": protected.sha256,
            "rows": len(protected.rows),
            "field_names": list(protected.field_names),
            "field_presence": {
                field: {"present": len(protected.rows), "missing": 0}
                for field in protected.field_names
            },
        },
        "merge": merge,
        "select_population": {
            "select_cases": len(select_cases),
            "management_cases": len(management_cases),
            "databases": len(public.databases),
            "gold_coverage": sum(bool(case.sol_sql) for case in select_cases),
            "knowledge_non_empty": sum(
                bool(case.runtime.external_knowledge) for case in select_cases
            ),
            "knowledge_empty": sum(
                not bool(case.runtime.external_knowledge) for case in select_cases
            ),
            "knowledge_missing": 0,
        },
        "knowledge_coverage": {
            "select_non_empty": sum(bool(case.runtime.external_knowledge) for case in select_cases),
            "select_empty": sum(not bool(case.runtime.external_knowledge) for case in select_cases),
            "management_non_empty": sum(
                bool(case.evaluation.external_knowledge) for case in management_cases
            ),
            "size_chars": _stats(knowledge_sizes),
            "size_tokens_estimate": _stats([(size + 3) // 4 for size in knowledge_sizes]),
        },
        "gold_sql_coverage": {**parse_counts, "shape_counts": shape_counts},
        "test_case_coverage": {
            "select_with_test_cases": sum(bool(case.test_cases) for case in select_cases),
            "select_missing_test_cases": sum(not bool(case.test_cases) for case in select_cases),
            "total_test_cases": sum(test_case_sizes),
            "per_query": _stats(test_case_sizes),
            "query_default_conditions": sum(bool(case.public.conditions) for case in select_cases),
        },
        "m1_compatibility": {
            "gold_total": len(select_cases),
            "accepted": m1_counts["ACCEPTED"],
            "rejected": m1_counts["POLICY_REJECTED"],
            "planning_errors": m1_counts["PLANNING_ERROR"],
            "failure_codes": dict(sorted(failure_code_counts.items())),
            "failure_review": dict(sorted(failure_class_counts.items())),
        },
        "reference_execution": {
            "m1_accepted": m1_counts["ACCEPTED"],
            "attempted": m1_counts["ACCEPTED"],
            "success": execution_counts["SUCCESS"],
            "failure": execution_counts["FAILURE"],
            "timeout": 0,
        },
        "official_evaluator": {
            "reference_evaluable": evaluator_counts["PASS"] + evaluator_counts["FAIL"],
            "reference_pass": evaluator_counts["PASS"],
            "reference_fail": evaluator_counts["FAIL"],
            "not_evaluable": evaluator_counts["NOT_EVALUATED"],
            "equivalence_controls": {
                "real_reference_cases_tested": evaluator_control_counts["known_good_pass"],
                "known_good_pass": evaluator_control_counts["known_good_pass"],
                "mutated_wrong_rejected": evaluator_control_counts["mutated_wrong_rejected"],
                "mutated_wrong_false_accept": evaluator_control_counts[
                    "mutated_wrong_false_accept"
                ],
                "mutation_kind": "bounded result-row append; offline evaluator control only",
            },
        },
        "context_statistics": {
            "question_chars": _stats([len(case.runtime.question) for case in select_cases]),
            "external_knowledge_chars": _stats(knowledge_sizes),
            "schema_chars": _stats(context_sizes),
            "combined_chars": _stats(combined_sizes),
            "combined_tokens_estimate": _stats([(size + 3) // 4 for size in combined_sizes]),
        },
        "database_coverage": [
            {
                **record,
                "gold_coverage": sum(
                    case.database == record["database"] and bool(case.sol_sql)
                    for case in select_cases
                ),
                "test_case_coverage": sum(
                    case.database == record["database"] and bool(case.test_cases)
                    for case in select_cases
                ),
                "knowledge_non_empty": sum(
                    case.database == record["database"] and bool(case.runtime.external_knowledge)
                    for case in select_cases
                ),
            }
            for record in schema_records
        ],
        "pilot_validation": {
            "frozen_ids_preserved": len(pilot_cases) == len(pilot_ids),
            "pilot_cases": len(pilot_cases),
            "gold_coverage": sum(bool(case.sol_sql) for case in pilot_cases),
            "test_case_coverage": sum(bool(case.test_cases) for case in pilot_cases),
            "knowledge_field_coverage": sum(
                case.evaluation.external_knowledge is not None for case in pilot_cases
            ),
            "m1_compatible": sum(
                m1_by_id[case.instance_id]["m1_status"] == "ACCEPTED" for case in pilot_cases
            ),
            "reference_evaluable": sum(
                m1_by_id[case.instance_id]["reference_evaluator_status"] != "NOT_EVALUATED"
                for case in pilot_cases
            ),
        },
        "database_errors": database_errors,
        "protected_git": {
            "ignored": protected_ignored,
            "tracked_files": tracked_protected,
            "gold_contents_committed": bool(tracked_protected),
        },
        "runtime_separation": {
            "question": True,
            "external_knowledge": True,
            "schema": True,
            "sol_sql": False,
            "test_cases": False,
            "reference_results": False,
        },
        "safe_cases": safe_cases,
        "final_manifest": final_manifest,
        "final_manifest_hash": final_manifest_hash,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "livesqlbench_base_lite_final_manifest.json").write_text(
        json.dumps(final_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "livesqlbench_base_lite_protected_preflight_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--protected", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/fixtures"))
    args = parser.parse_args()
    result = run_preflight(
        args.public_root,
        args.protected,
        args.output_dir,
        PostgresConnectionConfig.from_environment(),
    )
    print(
        json.dumps(
            {
                "classification": result["classification"],
                "manifest_hash": result["final_manifest_hash"],
                "provider_calls": result["provider_calls"],
                "merge": result["merge"],
                "m1": result["m1_compatibility"],
                "execution": result["reference_execution"],
                "evaluator": result["official_evaluator"],
            },
            indent=2,
        )
    )
    raise SystemExit(0 if result["classification"].endswith("VALIDATED") else 1)


if __name__ == "__main__":
    main()
