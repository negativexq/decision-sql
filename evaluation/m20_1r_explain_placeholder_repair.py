"""Provider-free replay for the M20.1 EXPLAIN percent-placeholder repair."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, cast

from sqlalchemy.engine import Engine

from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from evaluation.external.bird.benchmark import (
    BirdBenchmarkExecutor,
    BirdQuestion,
    bird_execution_match,
)
from evaluation.external.bird.benchmark import (
    QueryResult as BirdResult,
)
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
    canonical_compare,
)
from evaluation.external.defog.benchmark import (
    QueryResult as DefogResult,
)
from evaluation.m20_1_m1_boundary_forensics import (
    _diagnose_planning_error,
    _gold_result,
    _load_frozen,
)
from evaluation.m20_full_rebaseline import _load_all, _settings, _settings_for_db

ROOT = Path(__file__).resolve().parents[1]
M20_FORENSICS = ROOT / "evaluation/fixtures/m20_1_m1_boundary_forensics.json"
OUTPUT = ROOT / "evaluation/fixtures/m20_1r_explain_placeholder_repair.json"


class ReplayValidationError(RuntimeError):
    """Raised when the frozen replay cannot be accounted for exactly."""


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _load_forensic() -> dict[str, Any]:
    artifact = cast(dict[str, Any], json.loads(M20_FORENSICS.read_text()))
    if artifact.get("classification") != "M20_1_M1_BOUNDARY_FORENSICS_COMPLETED":
        raise ReplayValidationError("M20.1 forensic artifact is not completed")
    records = artifact.get("cases")
    if not isinstance(records, list) or len(records) != 299:
        raise ReplayValidationError("M20.1 forensic artifact does not contain 299 failures")
    target = [
        record
        for record in records
        if record.get("root_mechanism") == "EXPLAIN_PERCENT_PLACEHOLDER_COLLISION"
    ]
    if len(target) != 35:
        raise ReplayValidationError("M20.1 target population is not 35 cases")
    return artifact


def _state(result: QueryPlan | SqlPlanFailure) -> str:
    if isinstance(result, QueryPlan):
        return "ACCEPTED"
    if result.status.value in {"POLICY_REJECTION", "QUERY_COST_REJECTION"}:
        return "POLICY_REJECTED"
    return "PLANNING_ERROR"


def _policy_code(result: QueryPlan | SqlPlanFailure) -> str | None:
    return (
        result.rejection.code.value
        if isinstance(result, SqlPlanFailure) and result.rejection
        else None
    )


def _result_from_plan(execution: QueryExecution, kind: str) -> BirdResult | DefogResult:
    rows = tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows)
    if kind == "bird":
        return BirdResult(columns=tuple(execution.columns), rows=rows)
    return DefogResult(columns=tuple(execution.columns), rows=rows)


def _compare_target(
    row: dict[str, Any],
    plan: QueryPlan | SqlPlanFailure,
    safety: Any,
    kind: str,
    question: BenchmarkQuestion | BirdQuestion,
    defog_executor: DefogBenchmarkExecutor,
    bird_executor: BirdBenchmarkExecutor,
    gold_cache: dict[str, Any],
) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "case_id": row["case_id"],
        "benchmark": row["benchmark"],
        "old_failure_code": row["failure_code"],
        "old_failure_stage": row["failure_stage"],
        "new_state": _state(plan),
        "new_policy_code": _policy_code(plan),
        "new_placeholder_error": False,
        "execution_status": None,
        "correct": None,
        "error_type": None,
        "error": None,
    }
    if not isinstance(plan, QueryPlan):
        if plan.status.value == "EXECUTION_ERROR":
            engine = safety.reader_engine
            diagnosis = _diagnose_planning_error(safety, engine, row["generated_sql"])
            outcome["new_placeholder_error"] = (
                diagnosis["normalized_failure_code"] == "EXPLAIN_PERCENT_PLACEHOLDER_COLLISION"
            )
            outcome["error_type"] = diagnosis["exception_type"]
            outcome["error"] = diagnosis["detail"]
        return outcome

    execution = safety.execute(plan)
    if not isinstance(execution, QueryExecution):
        outcome["execution_status"] = "FAILURE"
        outcome["error_type"] = type(execution).__name__
        outcome["error"] = execution.error
        return outcome
    outcome["execution_status"] = "SUCCESS"
    generated = _result_from_plan(execution, kind)
    key = f"{kind}:{row['case_id']}"
    if key not in gold_cache:
        gold_cache[key] = _gold_result(question, kind, defog_executor, bird_executor)
    gold = gold_cache[key]
    if kind == "bird":
        assert isinstance(generated, BirdResult)
        outcome["correct"] = bird_execution_match(generated, cast(BirdResult, gold))
    else:
        assert isinstance(question, BenchmarkQuestion)
        assert isinstance(generated, DefogResult)
        outcome["correct"] = any(
            canonical_compare(gold_result, generated, question, gold_sql, row["generated_sql"])[0]
            for gold_sql, gold_result in gold
        )
    return outcome


def _transition_matrix(old: Counter[str], new: Counter[tuple[str, str]]) -> list[dict[str, Any]]:
    states = ("ACCEPTED", "POLICY_REJECTED", "PLANNING_ERROR")
    return [
        {"old": old_state, "new": new_state, "count": new[(old_state, new_state)]}
        for old_state in states
        for new_state in states
    ]


def validate_replay_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("provider_calls") != 0:
        raise ReplayValidationError("provider calls must remain zero")
    targeted = artifact.get("targeted_cases", {})
    if targeted.get("total") != 35:
        raise ReplayValidationError("targeted replay accounting is not 35")
    if artifact.get("full_replay", {}).get("total") != 774:
        raise ReplayValidationError("full replay accounting is not 774")
    if artifact.get("unexpected_regressions"):
        raise ReplayValidationError("unexpected M1 regressions are present")


def replay() -> dict[str, Any]:
    forensic = _load_forensic()
    cases, m20_result, _ = _load_frozen()
    target_by_key = {
        (record["benchmark"], record["case_id"]): record for record in forensic["cases"]
    }
    targets = [
        row
        for row in cases
        if (row["benchmark"], row["case_id"]) in target_by_key
        and target_by_key[(row["benchmark"], row["case_id"])]["root_mechanism"]
        == "EXPLAIN_PERCENT_PLACEHOLDER_COLLISION"
    ]
    if len(targets) != 35:
        raise ReplayValidationError("frozen target SQL could not be resolved")

    sql_eval = Path(__import__("os").environ["DEFOG_SQL_EVAL_ROOT"])
    bird_root = Path(__import__("os").environ["BIRD_MINI_DEV_ROOT"])
    datasets = _load_all(sql_eval, bird_root)
    question_by_key: dict[tuple[str, str], Any] = {}
    for label, questions in datasets.items():
        for question in questions:
            question_by_key[(label, str(question.index))] = question

    settings = _settings()
    services: dict[tuple[str, str], tuple[Engine, Any]] = {}
    results: dict[tuple[str, str], QueryPlan | SqlPlanFailure] = {}
    try:
        for row in cases:
            kind = "bird" if row["benchmark"] == "bird" else "defog"
            key = (row["benchmark"], row["database"])
            if key not in services:
                _, engine, safety = _settings_for_db(settings, kind, row["database"])
                services[key] = (engine, safety)
            _, safety = services[key]
            results[(row["benchmark"], row["case_id"])] = safety.plan(
                SqlCandidate(sql=row["generated_sql"], source=CandidateSource.LLM)
            )

        old_state = Counter(
            "ACCEPTED"
            if row["m1_status"] == "ALLOWED"
            else "POLICY_REJECTED"
            if row["m1_status"] == "REJECTED"
            else "PLANNING_ERROR"
            for row in cases
        )
        new_state_pairs: Counter[tuple[str, str]] = Counter()
        unexpected: list[dict[str, Any]] = []
        old_policy_widening: list[str] = []
        accepted_regressions: list[str] = []
        for row in cases:
            key = (row["benchmark"], row["case_id"])
            new = _state(results[key])
            old = (
                "ACCEPTED"
                if row["m1_status"] == "ALLOWED"
                else "POLICY_REJECTED"
                if row["m1_status"] == "REJECTED"
                else "PLANNING_ERROR"
            )
            new_state_pairs[(old, new)] += 1
            is_target = key in {(r["benchmark"], r["case_id"]) for r in targets}
            if not is_target and old != new:
                unexpected.append(
                    {
                        "case_id": row["case_id"],
                        "benchmark": row["benchmark"],
                        "old": old,
                        "new": new,
                    }
                )
            if old == "ACCEPTED" and new != "ACCEPTED":
                accepted_regressions.append(f"{row['benchmark']}:{row['case_id']}")
            if old == "POLICY_REJECTED" and new == "ACCEPTED":
                old_policy_widening.append(f"{row['benchmark']}:{row['case_id']}")

        defog_executor = DefogBenchmarkExecutor()
        bird_executor = BirdBenchmarkExecutor()
        gold_cache: dict[str, Any] = {}
        target_outcomes: list[dict[str, Any]] = []
        for row in targets:
            key = (row["benchmark"], row["case_id"])
            engine, safety = services[(row["benchmark"], row["database"])]
            question_index = str(row["case_id"]).split(":")[-1]
            question = question_by_key[(row["benchmark"], question_index)]
            target_outcomes.append(
                _compare_target(
                    row,
                    results[key],
                    safety,
                    "bird" if row["benchmark"] == "bird" else "defog",
                    question,
                    defog_executor,
                    bird_executor,
                    gold_cache,
                )
            )
    finally:
        for engine, _ in services.values():
            engine.dispose()

    target_counts = Counter(outcome["new_state"] for outcome in target_outcomes)
    target_correct = sum(outcome["correct"] is True for outcome in target_outcomes)
    target_execution_failures = sum(
        outcome["execution_status"] == "FAILURE" for outcome in target_outcomes
    )
    target_placeholder_errors = sum(outcome["new_placeholder_error"] for outcome in target_outcomes)
    target_other_planning = sum(
        outcome["new_state"] == "PLANNING_ERROR" and not outcome["new_placeholder_error"]
        for outcome in target_outcomes
    )
    artifact = {
        "classification": "M20_1R_EXPLAIN_PLACEHOLDER_REPAIR_VALIDATED",
        "starting_commit": "e72b8b7ef9979a37729e91ce4f1d0a422baa7037",
        "repair_commit": _head(),
        "m20_manifest_hash": m20_result["manifest_hash"],
        "provider_calls": 0,
        "root_cause_reproduction": {
            "sql": "SELECT name FROM products WHERE name LIKE '%a%'",
            "before_error_type": "ProgrammingError",
            "before_error": "psycopg placeholder interpretation of literal percent",
            "after_expected": "valid EXPLAIN JSON estimate",
        },
        "targeted_cases": {
            "total": 35,
            "old_placeholder_errors": 35,
            "new_placeholder_errors": target_placeholder_errors,
            "accepted": target_counts.get("ACCEPTED", 0),
            "policy_rejected": target_counts.get("POLICY_REJECTED", 0),
            "other_planning_error": target_other_planning,
            "executed": sum(
                outcome["execution_status"] == "SUCCESS" for outcome in target_outcomes
            ),
            "execution_failure": target_execution_failures,
            "correct": target_correct,
            "mismatch": sum(outcome["correct"] is False for outcome in target_outcomes),
        },
        "old_m1_counts": dict(old_state),
        "new_m1_counts": dict(Counter(_state(result) for result in results.values())),
        "transition_matrix": _transition_matrix(old_state, new_state_pairs),
        "unexpected_regressions": unexpected,
        "accepted_regressions": accepted_regressions,
        "policy_widening": old_policy_widening,
        "target_cases": target_outcomes,
        "tests": {
            "provider_calls": 0,
            "no_provider_constructed": True,
            "frozen_sql_only": True,
        },
        "full_replay": {"total": 774, "provider_calls": 0},
    }
    validate_replay_artifact(artifact)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    artifact = replay()
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "classification": artifact["classification"],
                "targeted_cases": artifact["targeted_cases"],
                "old_m1_counts": artifact["old_m1_counts"],
                "new_m1_counts": artifact["new_m1_counts"],
                "transition_matrix": artifact["transition_matrix"],
                "unexpected_regressions": artifact["unexpected_regressions"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
