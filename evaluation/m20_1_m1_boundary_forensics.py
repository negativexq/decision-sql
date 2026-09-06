"""Provider-free forensic analysis of the frozen M20 M1 boundary outcomes.

This module deliberately consumes the M20 generated SQL artifact.  It never
constructs an LLM provider and never regenerates a candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

import sqlglot
from sqlalchemy.engine import Engine
from sqlglot import exp

from app.sql.models import CandidateSource, PolicyCode, SqlCandidate, SqlPlanFailure
from app.sql.policy import SAFE_FUNCTIONS
from evaluation.external.bird.benchmark import (
    BirdBenchmarkExecutor,
    BirdQuestion,
    bird_execution_match,
)
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
    canonical_compare,
    canonical_gold_variants,
)
from evaluation.m20_full_rebaseline import (
    _load_all,
    _settings,
    _settings_for_db,
)

ROOT = Path(__file__).resolve().parents[1]
M20_CASES = ROOT / "evaluation/fixtures/m20_full_benchmark_cases.jsonl"
M20_RESULT = ROOT / "evaluation/fixtures/m20_full_benchmark_result.json"
M20_MANIFEST = ROOT / "evaluation/fixtures/m20_prebenchmark_manifest.json"
OUTPUT = ROOT / "evaluation/fixtures/m20_1_m1_boundary_forensics.json"

EXPECTED_COUNTS = {"bird": 500, "defog_classic": 210, "defog_advanced": 64}
EXPECTED_M1 = {"ALLOWED": 475, "REJECTED": 264, "ERROR": 35}
MAX_ERROR_LENGTH = 240


class ForensicValidationError(RuntimeError):
    """Raised when frozen M20 evidence cannot support a complete analysis."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _load_frozen() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    cases = [json.loads(line) for line in M20_CASES.read_text().splitlines() if line.strip()]
    result = json.loads(M20_RESULT.read_text())
    manifest = json.loads(M20_MANIFEST.read_text())
    if result.get("classification") != "M20_FULL_REBASELINE_COMPLETED":
        raise ForensicValidationError("M20 result is not the completed frozen baseline")
    if len(cases) != sum(EXPECTED_COUNTS.values()):
        raise ForensicValidationError(f"expected 774 cases, found {len(cases)}")
    ids = [(str(row.get("benchmark")), str(row.get("case_id"))) for row in cases]
    if len(set(ids)) != len(ids):
        raise ForensicValidationError("M20 case IDs are not unique")
    counts = Counter(str(row.get("m1_status")) for row in cases)
    if counts != Counter(EXPECTED_M1):
        raise ForensicValidationError(f"M20 M1 accounting mismatch: {counts}")
    if result.get("commit_sha") != "8439b1e10bb53dbf58b7d78a81418da33f18ca58":
        raise ForensicValidationError("M20 result commit does not match frozen baseline")
    if result.get("manifest_hash") != manifest.get("manifest_hash"):
        raise ForensicValidationError("M20 manifest hash does not match result")
    return cases, result, manifest


def validate_artifact(artifact: dict[str, Any]) -> None:
    """Fail-closed structural validation for a completed forensic artifact."""
    records = artifact.get("cases")
    if not isinstance(records, list) or len(records) != 299:
        raise ForensicValidationError("forensic artifact must contain 299 failure records")
    keys = [(record.get("benchmark"), record.get("case_id")) for record in records]
    if len(set(keys)) != len(keys):
        raise ForensicValidationError("forensic case records are not unique")
    if any(record.get("m20_m1_status") == "ALLOWED" for record in records):
        raise ForensicValidationError("an accepted M20 case appears in the failure artifact")
    statuses = Counter(record.get("m20_m1_status") for record in records)
    if statuses != Counter({"REJECTED": 264, "ERROR": 35}):
        raise ForensicValidationError(f"forensic stage accounting mismatch: {statuses}")
    classifications = Counter(record.get("contract_classification") for record in records)
    if sum(classifications.values()) != 299:
        raise ForensicValidationError("forensic classification accounting mismatch")
    if artifact.get("summary", {}).get("unaccounted") != 0:
        raise ForensicValidationError("forensic artifact is not fail-closed")


def _parse(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return None


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return function.name.upper()
    if isinstance(function, exp.TimestampTrunc):
        return "DATE_TRUNC"
    return str(function.sql_name()).upper()  # type: ignore[no-untyped-call]


def _shape(sql: str) -> dict[str, Any]:
    tree = _parse(sql)
    if tree is None:
        return {"parseable": False}
    arithmetic = tuple(
        sorted(
            {
                type(node).__name__
                for node in tree.walk()
                if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod))
            }
        )
    )
    functions = tuple(sorted({_function_name(node) for node in tree.find_all(exp.Func)}))
    return {
        "parseable": True,
        "statement_type": type(tree).__name__,
        "join_count": sum(1 for _ in tree.find_all(exp.Join)),
        "aggregation_present": any(isinstance(node, exp.AggFunc) for node in tree.walk()),
        "group_by_present": tree.args.get("group") is not None,
        "having_present": tree.args.get("having") is not None,
        "subquery_present": any(isinstance(node, exp.Subquery) for node in tree.walk()),
        "cte_present": any(isinstance(node, exp.CTE) for node in tree.walk()),
        "window_present": any(isinstance(node, exp.Window) for node in tree.walk()),
        "distinct_present": bool(tree.args.get("distinct")),
        "order_by_present": tree.args.get("order") is not None,
        "limit_present": tree.args.get("limit") is not None,
        "arithmetic_nodes": arithmetic,
        "ratio_or_division_present": exp.Div in {type(node) for node in tree.walk()},
        "function_names": functions,
        "max_function_depth": _max_function_depth(tree),
    }


def _max_function_depth(tree: exp.Expression) -> int:
    def depth(node: exp.Expression) -> int:
        parents = 0
        current = node.parent
        while current is not None:
            if isinstance(current, exp.Func):
                parents += 1
            current = current.parent
        return parents

    return max((depth(node) for node in tree.find_all(exp.Func)), default=0)


def _safe_forensic_sql(sql: str) -> tuple[bool, str]:
    """Conservative read-only gate for optional diagnostic execution."""
    tree = _parse(sql)
    if tree is None:
        return False, "UNPARSEABLE"
    if not isinstance(tree, exp.Select):
        return False, "NON_SELECT"
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Command,
        exp.Copy,
        exp.Transaction,
        exp.Merge,
        exp.Alter,
        exp.Lock,
    )
    if any(tree.find(node) is not None for node in forbidden):
        return False, "UNSAFE_OPERATION"
    safe_extra_functions = {
        "ABS",
        "DATE_PART",
        "LEFT",
        "LENGTH",
        "LOWER",
        "NULLIF",
        "REPLACE",
        "RIGHT",
        "ROUND",
        "SUBSTRING",
        "TRIM",
        "UPPER",
    }
    unsafe_names = {
        "DBLINK",
        "DBLINK_EXEC",
        "CURRENT_SETTING",
        "LO_EXPORT",
        "LO_IMPORT",
        "NEXTVAL",
        "PG_ADVISORY_LOCK",
        "PG_ADVISORY_XACT_LOCK",
        "PG_CANCEL_BACKEND",
        "PG_NOTIFY",
        "PG_READ_FILE",
        "PG_SLEEP",
        "SET_CONFIG",
        "SETVAL",
    }
    for function in tree.find_all(exp.Func):
        name = _function_name(function)
        if name in unsafe_names or name.startswith(("DBLINK", "PG_", "LO_")):
            return False, "UNSAFE_FUNCTION"
        if (
            isinstance(function, exp.Anonymous)
            and name not in SAFE_FUNCTIONS | safe_extra_functions
        ):
            return False, "UNKNOWN_FUNCTION"
    return True, "READ_ONLY_SELECT"


def _safe_error(error: BaseException) -> str:
    text = str(error)
    text = re.sub(r"\[SQL:.*", "[SQL omitted]", text, flags=re.DOTALL)
    return text[:MAX_ERROR_LENGTH]


def _replay(safety: Any, sql: str) -> dict[str, Any]:
    planned = safety.plan(SqlCandidate(sql=sql, source=CandidateSource.LLM))
    if not isinstance(planned, SqlPlanFailure):
        return {
            "status": "ALLOWED",
            "rejection_code": None,
            "failure_stage": None,
            "error_type": None,
            "error": None,
            "object": None,
            "estimate": None,
        }
    return {
        "status": planned.status.value,
        "rejection_code": planned.rejection.code.value if planned.rejection else None,
        "failure_stage": planned.failure_stage.value if planned.failure_stage else None,
        "error_type": None,
        "error": planned.error,
        "object": planned.rejection.object if planned.rejection else None,
        "estimate": planned.estimate.model_dump() if planned.estimate else None,
    }


def _diagnose_planning_error(safety: Any, engine: Engine, sql: str) -> dict[str, Any]:
    """Recover the earliest existing sub-stage hidden by M1's safe error envelope."""
    parsed = safety.parser.parse(sql)
    rejection = safety.policy.validate(parsed)
    if rejection is not None:
        return {
            "earliest_failure_stage": "AST_POLICY_EVALUATION",
            "normalized_failure_code": rejection.code.value,
            "exception_type": None,
            "detail": rejection.message,
        }
    normalized = parsed.expression.sql(dialect="postgres")
    try:
        with engine.connect() as connection:
            with connection.begin():
                safety.executor.configure_transaction(connection)
                estimate = safety.cost_gate.explain(connection, normalized)
                if safety.cost_gate.exceeds(
                    estimate, safety.settings.max_plan_rows, safety.settings.max_plan_cost
                ):
                    return {
                        "earliest_failure_stage": "COST_POLICY_EVALUATION",
                        "normalized_failure_code": PolicyCode.QUERY_TOO_EXPENSIVE.value,
                        "exception_type": None,
                        "detail": "cost policy exceeded during replay",
                    }
    except Exception as error:
        detail = _safe_error(error)
        placeholder = "placeholder" in detail.lower() or "incomplete placeholder" in detail.lower()
        return {
            "earliest_failure_stage": "EXPLAIN_EXECUTION",
            "normalized_failure_code": (
                "EXPLAIN_PERCENT_PLACEHOLDER_COLLISION"
                if placeholder
                else "EXPLAIN_EXECUTION_FAILURE"
            ),
            "exception_type": type(error).__name__,
            "detail": detail,
        }
    return {
        "earliest_failure_stage": "PLAN_CONSTRUCTION",
        "normalized_failure_code": "PLAN_CONSTRUCTION_FAILURE",
        "exception_type": None,
        "detail": "M1 returned a planning error after parse, policy, and EXPLAIN succeeded",
    }


def _gold_result(
    question: BenchmarkQuestion | BirdQuestion,
    kind: str,
    defog_executor: DefogBenchmarkExecutor,
    bird_executor: BirdBenchmarkExecutor,
) -> Any:
    if kind == "bird":
        assert isinstance(question, BirdQuestion)
        return bird_executor.execute(question.gold_sql)
    assert isinstance(question, BenchmarkQuestion)
    return [
        (variant, defog_executor.execute(question.db_name, variant))
        for variant in canonical_gold_variants(question.gold_sql)
    ]


def _forensic_execution(
    row: dict[str, Any],
    question: BenchmarkQuestion | BirdQuestion,
    kind: str,
    defog_executor: DefogBenchmarkExecutor,
    bird_executor: BirdBenchmarkExecutor,
    gold_cache: dict[str, Any],
) -> dict[str, Any]:
    safe, reason = _safe_forensic_sql(row["generated_sql"])
    if not safe:
        return {
            "status": "NOT_ATTEMPTED_SAFETY",
            "safety_reason": reason,
            "would_execution_succeed": None,
            "would_result_match": None,
            "error_type": None,
            "error": None,
        }
    try:
        if kind == "bird":
            generated_bird = bird_executor.execute(row["generated_sql"])
        else:
            assert isinstance(question, BenchmarkQuestion)
            generated_defog = defog_executor.execute(question.db_name, row["generated_sql"])
        key = f"{kind}:{row['case_id']}"
        if key not in gold_cache:
            gold_cache[key] = _gold_result(question, kind, defog_executor, bird_executor)
        gold = gold_cache[key]
        if kind == "bird":
            matches = bird_execution_match(generated_bird, cast(Any, gold))
        else:
            assert isinstance(question, BenchmarkQuestion)
            matches = any(
                canonical_compare(
                    gold_result,
                    generated_defog,
                    question,
                    gold_sql,
                    row["generated_sql"],
                )[0]
                for gold_sql, gold_result in gold
            )
        return {
            "status": "SUCCESS",
            "safety_reason": reason,
            "would_execution_succeed": True,
            "would_result_match": bool(matches),
            "error_type": None,
            "error": None,
        }
    except Exception as error:
        return {
            "status": "EXECUTION_FAILURE",
            "safety_reason": reason,
            "would_execution_succeed": False,
            "would_result_match": None,
            "error_type": type(error).__name__,
            "error": _safe_error(error),
        }


def _classify(row: dict[str, Any], replay: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    if row["m1_status"] == "REJECTED":
        if replay["status"] not in {"POLICY_REJECTION", "QUERY_COST_REJECTION"}:
            return "UNDETERMINED"
        return "LEGITIMATE_BOUNDARY_OUTCOME"
    if diagnosis["normalized_failure_code"] == "EXPLAIN_PERCENT_PLACEHOLDER_COLLISION":
        return "IMPLEMENTATION_DEFECT"
    if diagnosis["earliest_failure_stage"] in {"PLAN_CONSTRUCTION", "AST_POLICY_EVALUATION"}:
        return "IMPLEMENTATION_DEFECT"
    return "UNDETERMINED"


def _policy_config(code: str | None, settings: Any) -> dict[str, Any] | None:
    if code == PolicyCode.QUERY_TOO_EXPENSIVE.value:
        return {
            "max_plan_rows": settings.max_plan_rows,
            "max_plan_cost": settings.max_plan_cost,
        }
    return None


def _root_mechanisms(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["root_mechanism"]].append(record)
    result = []
    recommendations = {
        "EXPLAIN_PERCENT_PLACEHOLDER_COLLISION": {
            "candidate_general_fix": (
                "Make QueryCostGate EXPLAIN submission parameter/literal-safe "
                "without changing policy limits."
            ),
            "safety_risk": (
                "low if EXPLAIN remains read-only and the existing cost gate is unchanged"
            ),
            "code_path": "app/execution/cost.py: QueryCostGate.explain",
        },
        "FORBIDDEN_FUNCTION": {
            "candidate_general_fix": (
                "none supported by this forensic run; analytical allowlist is an explicit M1 policy"
            ),
            "safety_risk": "policy decision required",
            "code_path": "app/sql/policy.py: SQLPolicy._validate_functions",
        },
        "FORBIDDEN_TABLE": {
            "candidate_general_fix": "none; object allowlist is an explicit M1 policy",
            "safety_risk": "high; changing object scope changes the trust boundary",
            "code_path": "app/sql/policy.py: SQLPolicy.validate",
        },
        "UNKNOWN_COLUMN": {
            "candidate_general_fix": "none; catalog resolution rejected the referenced column",
            "safety_risk": "policy/catalog decision required",
            "code_path": "app/sql/policy.py: SQLPolicy._validate_columns",
        },
        "UNKNOWN_TABLE": {
            "candidate_general_fix": "none; catalog resolution rejected the referenced table",
            "safety_risk": "policy/catalog decision required",
            "code_path": "app/sql/policy.py: SQLPolicy.validate",
        },
        "QUERY_TOO_EXPENSIVE": {
            "candidate_general_fix": "none; do not relax the cost bound from benchmark evidence",
            "safety_risk": "high; explicit policy tradeoff",
            "code_path": "app/sql/service.py: QueryCostGate.exceeds",
        },
    }
    for mechanism, items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        recommendation = recommendations.get(mechanism, {})
        result.append(
            {
                "mechanism": mechanism,
                "count": len(items),
                "benchmarks": dict(sorted(Counter(item["benchmark"] for item in items).items())),
                "contract_classification": dict(
                    sorted(Counter(item["contract_classification"] for item in items).items())
                ),
                "example_case_ids": [item["case_id"] for item in items[:5]],
                **recommendation,
            }
        )
    return result


def analyze() -> dict[str, Any]:
    cases, m20_result, manifest = _load_frozen()
    frozen_failures = [row for row in cases if row["m1_status"] != "ALLOWED"]
    if len(frozen_failures) != 299:
        raise ForensicValidationError("M20 failure population is not 299")

    # Dataset questions are only used as offline evaluator context.  No model
    # or generation path is constructed here.
    sql_eval = Path(__import__("os").environ["DEFOG_SQL_EVAL_ROOT"])
    bird_root = Path(__import__("os").environ["BIRD_MINI_DEV_ROOT"])
    datasets = _load_all(sql_eval, bird_root)
    question_by_id: dict[tuple[str, str], Any] = {}
    for label, questions in datasets.items():
        for question in questions:
            key = (label, str(question.index))
            question_by_id[key] = question

    settings = _settings()
    services: dict[tuple[str, str], tuple[Engine, Any]] = {}
    defog_executor = DefogBenchmarkExecutor()
    bird_executor = BirdBenchmarkExecutor()
    gold_cache: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    try:
        for row in frozen_failures:
            kind = "bird" if row["benchmark"] == "bird" else "defog"
            service_key = (kind, row["database"])
            if service_key not in services:
                _, engine, safety = _settings_for_db(settings, kind, row["database"])
                services[service_key] = (engine, safety)
            engine, safety = services[service_key]
            replay = _replay(safety, row["generated_sql"])
            diagnosis = (
                _diagnose_planning_error(safety, engine, row["generated_sql"])
                if row["m1_status"] == "ERROR"
                else {
                    "earliest_failure_stage": "AST_POLICY_EVALUATION"
                    if replay["status"] == "POLICY_REJECTION"
                    else "COST_POLICY_EVALUATION",
                    "normalized_failure_code": replay["rejection_code"],
                    "exception_type": None,
                    "detail": replay["error"],
                }
            )
            question = question_by_id[(row["benchmark"], str(row["case_id"]).split(":")[-1])]
            forensic = _forensic_execution(
                row,
                question,
                kind,
                defog_executor,
                bird_executor,
                gold_cache,
            )
            classification = _classify(row, replay, diagnosis)
            root = (
                diagnosis["normalized_failure_code"]
                if row["m1_status"] == "ERROR"
                else replay["rejection_code"] or "UNDETERMINED_M1_FAILURE"
            )
            records.append(
                {
                    "case_id": row["case_id"],
                    "benchmark": row["benchmark"],
                    "database": row["database"],
                    "generated_sql": row["generated_sql"],
                    "m20_m1_status": row["m1_status"],
                    "m20_failure_code": row["failure_code"],
                    "m20_failure_stage": row["failure_stage"],
                    "m1_replay": replay,
                    "earliest_failure_stage": diagnosis["earliest_failure_stage"],
                    "exception_type": diagnosis["exception_type"],
                    "normalized_failure_code": diagnosis["normalized_failure_code"],
                    "failure_detail": (
                        str(diagnosis["detail"])[:MAX_ERROR_LENGTH]
                        if diagnosis["detail"] is not None
                        else None
                    ),
                    "policy_configuration": _policy_config(replay["rejection_code"], settings),
                    "relevant_object": replay["object"],
                    "sql_shape": _shape(row["generated_sql"]),
                    "forensic_execution": forensic,
                    "contract_classification": classification,
                    "root_mechanism": root,
                }
            )
    finally:
        for engine, _ in services.values():
            engine.dispose()

    if (
        len(records) != 299
        or len({(record["benchmark"], record["case_id"]) for record in records}) != 299
    ):
        raise ForensicValidationError("failure records are not accounted for exactly once")
    classifications = Counter(record["contract_classification"] for record in records)
    root_counts = Counter(record["root_mechanism"] for record in records)
    if sum(classifications.values()) != 299 or sum(root_counts.values()) != 299:
        raise ForensicValidationError("classification/root accounting mismatch")

    benchmark_breakdown: dict[str, Any] = {}
    for benchmark in EXPECTED_COUNTS:
        subset = [record for record in records if record["benchmark"] == benchmark]
        benchmark_breakdown[benchmark] = {
            "total": EXPECTED_COUNTS[benchmark],
            "accepted": sum(
                row["m1_status"] == "ALLOWED" for row in cases if row["benchmark"] == benchmark
            ),
            "policy_rejection": sum(
                row["m1_status"] == "REJECTED" for row in cases if row["benchmark"] == benchmark
            ),
            "planning_error": sum(
                row["m1_status"] == "ERROR" for row in cases if row["benchmark"] == benchmark
            ),
            "legitimate": sum(
                row["contract_classification"] == "LEGITIMATE_BOUNDARY_OUTCOME" for row in subset
            ),
            "false_positive": sum(
                row["contract_classification"] == "FALSE_POSITIVE_BOUNDARY_FAILURE"
                for row in subset
            ),
            "implementation_defect": sum(
                row["contract_classification"] == "IMPLEMENTATION_DEFECT" for row in subset
            ),
            "undetermined": sum(row["contract_classification"] == "UNDETERMINED" for row in subset),
        }

    recoverability: Counter[str] = Counter()
    for record in records:
        forensic = record["forensic_execution"]
        if forensic["status"] == "NOT_ATTEMPTED_SAFETY":
            recoverability["not_attempted_for_safety"] += 1
        elif forensic["status"] == "EXECUTION_FAILURE":
            recoverability["would_execution_fail"] += 1
        elif forensic["would_result_match"]:
            recoverability["would_be_correct"] += 1
        else:
            recoverability["would_mismatch"] += 1

    artifact = {
        "classification": "M20_1_M1_BOUNDARY_FORENSICS_COMPLETED",
        "baseline_commit": m20_result["commit_sha"],
        "analysis_commit": _head(),
        "baseline_manifest_hash": m20_result["manifest_hash"],
        "baseline_artifacts": {
            "cases_sha256": _sha256(M20_CASES),
            "result_sha256": _sha256(M20_RESULT),
            "manifest_sha256": _sha256(M20_MANIFEST),
            "manifest_recorded_hash": manifest.get("manifest_hash"),
        },
        "total_cases": len(cases),
        "m1_failures": len(records),
        "summary": {
            "planning_errors": sum(row["m20_m1_status"] == "ERROR" for row in records),
            "policy_rejections": sum(row["m20_m1_status"] == "REJECTED" for row in records),
            "legitimate": classifications.get("LEGITIMATE_BOUNDARY_OUTCOME", 0),
            "false_positive": classifications.get("FALSE_POSITIVE_BOUNDARY_FAILURE", 0),
            "implementation_defect": classifications.get("IMPLEMENTATION_DEFECT", 0),
            "undetermined": classifications.get("UNDETERMINED", 0),
            "unaccounted": len([row for row in cases if row["m1_status"] != "ALLOWED"])
            - len(records),
        },
        "replay": {
            "replayed_failures": len(records),
            "m20_status_reproduced": sum(
                record["m1_replay"]["status"]
                in (
                    {"POLICY_REJECTION", "QUERY_COST_REJECTION"}
                    if record["m20_m1_status"] == "REJECTED"
                    else {"EXECUTION_ERROR"}
                )
                for record in records
            ),
            "provider_calls": 0,
        },
        "recoverability": dict(sorted(recoverability.items())),
        "root_mechanisms": _root_mechanisms(records),
        "benchmark_breakdown": benchmark_breakdown,
        "cases": records,
    }
    validate_artifact(artifact)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    artifact = analyze()
    validate_artifact(artifact)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "classification": artifact["classification"],
                "m1_failures": artifact["m1_failures"],
                "summary": artifact["summary"],
                "recoverability": artifact["recoverability"],
                "root_mechanisms": artifact["root_mechanisms"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
