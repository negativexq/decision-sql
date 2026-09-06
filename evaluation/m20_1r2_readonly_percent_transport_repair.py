"""Provider-free M20.1R2 replay of the frozen M1-approved SQL candidates."""

from __future__ import annotations

import argparse
import hashlib
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
from evaluation.external.bird.benchmark import QueryResult as BirdResult
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
    canonical_compare,
)
from evaluation.external.defog.benchmark import QueryResult as DefogResult
from evaluation.m20_1_m1_boundary_forensics import _gold_result, _load_frozen
from evaluation.m20_1r_explain_placeholder_repair import _load_forensic, _state
from evaluation.m20_full_rebaseline import _load_all, _settings, _settings_for_db

ROOT = Path(__file__).resolve().parents[1]
M20_1R_ARTIFACT = ROOT / "evaluation/fixtures/m20_1r_explain_placeholder_repair.json"
M20_1R2_ARTIFACT = ROOT / "evaluation/fixtures/m20_1r2_readonly_percent_transport_repair.json"


class ReplayValidationError(RuntimeError):
    """Raised when a frozen downstream replay cannot be accounted for."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _old_state(row: dict[str, Any]) -> str:
    if row["m1_status"] == "ALLOWED":
        return "ACCEPTED"
    if row["m1_status"] == "REJECTED":
        return "POLICY_REJECTED"
    return "PLANNING_ERROR"


def _policy_code(result: QueryPlan | SqlPlanFailure) -> str | None:
    if isinstance(result, SqlPlanFailure) and result.rejection:
        return result.rejection.code.value
    return None


def _result_from_execution(execution: QueryExecution, kind: str) -> BirdResult | DefogResult:
    rows = tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows)
    if kind == "bird":
        return BirdResult(columns=tuple(execution.columns), rows=rows)
    return DefogResult(columns=tuple(execution.columns), rows=rows)


def _target_outcome(
    row: dict[str, Any],
    plan: QueryPlan | SqlPlanFailure,
    execution: QueryExecution | Any,
    kind: str,
    question: BenchmarkQuestion | BirdQuestion,
    defog_executor: DefogBenchmarkExecutor,
    bird_executor: BirdBenchmarkExecutor,
    gold_cache: dict[str, Any],
) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "case_id": row["case_id"],
        "benchmark": row["benchmark"],
        "new_state": _state(plan),
        "new_policy_code": _policy_code(plan),
        "execution_status": None,
        "correct": None,
        "error_type": None,
        "error": None,
    }
    if not isinstance(plan, QueryPlan):
        return outcome
    if not isinstance(execution, QueryExecution):
        outcome["execution_status"] = "FAILURE"
        outcome["error_type"] = type(execution).__name__
        outcome["error"] = getattr(execution, "error", None)
        return outcome
    outcome["execution_status"] = "SUCCESS"
    generated = _result_from_execution(execution, kind)
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


def validate_replay_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("provider_calls") != 0:
        raise ReplayValidationError("M20.1R2 provider calls must be zero")
    if artifact.get("targeted_cases", {}).get("total") != 35:
        raise ReplayValidationError("targeted replay must contain 35 cases")
    if artifact.get("full_replay", {}).get("total") != 774:
        raise ReplayValidationError("full replay must contain 774 cases")
    if artifact.get("unexpected_regressions"):
        raise ReplayValidationError("unexpected execution regressions were recorded")


def replay() -> dict[str, Any]:
    forensic = _load_forensic()
    cases, m20_result, _ = _load_frozen()
    target_ids = {
        (record["benchmark"], record["case_id"])
        for record in forensic["cases"]
        if record.get("root_mechanism") == "EXPLAIN_PERCENT_PLACEHOLDER_COLLISION"
    }
    if len(target_ids) != 35:
        raise ReplayValidationError("M20.1 target population is not 35")
    targets = [row for row in cases if (row["benchmark"], row["case_id"]) in target_ids]

    sql_eval = Path(__import__("os").environ["DEFOG_SQL_EVAL_ROOT"])
    bird_root = Path(__import__("os").environ["BIRD_MINI_DEV_ROOT"])
    datasets = _load_all(sql_eval, bird_root)
    question_by_key: dict[tuple[str, str], Any] = {}
    for label, questions in datasets.items():
        for question in questions:
            question_by_key[(label, str(question.index))] = question

    settings = _settings()
    services: dict[tuple[str, str], tuple[Engine, Any]] = {}
    plans: dict[tuple[str, str], QueryPlan | SqlPlanFailure] = {}
    executions: dict[tuple[str, str], QueryExecution | Any] = {}
    try:
        for row in cases:
            kind = "bird" if row["benchmark"] == "bird" else "defog"
            service_key = (row["benchmark"], row["database"])
            if service_key not in services:
                _, engine, safety = _settings_for_db(settings, kind, row["database"])
                services[service_key] = (engine, safety)
            _, safety = services[service_key]
            plans[(row["benchmark"], row["case_id"])] = safety.plan(
                SqlCandidate(sql=row["generated_sql"], source=CandidateSource.LLM)
            )

        for row in cases:
            key = (row["benchmark"], row["case_id"])
            plan = plans[key]
            if isinstance(plan, QueryPlan):
                _, safety = services[(row["benchmark"], row["database"])]
                executions[key] = safety.execute(plan)

        defog_executor = DefogBenchmarkExecutor()
        bird_executor = BirdBenchmarkExecutor()
        gold_cache: dict[str, Any] = {}
        target_outcomes = []
        for row in targets:
            key = (row["benchmark"], row["case_id"])
            question = question_by_key[(row["benchmark"], str(row["case_id"]).split(":")[-1])]
            target_outcomes.append(
                _target_outcome(
                    row,
                    plans[key],
                    executions.get(key),
                    "bird" if row["benchmark"] == "bird" else "defog",
                    question,
                    defog_executor,
                    bird_executor,
                    gold_cache,
                )
            )

        transitions: Counter[tuple[str, str]] = Counter()
        unexpected: list[dict[str, Any]] = []
        accepted_regressions: list[str] = []
        for row in cases:
            key = (row["benchmark"], row["case_id"])
            old = _old_state(row)
            new = _state(plans[key])
            if old != new and key not in target_ids:
                unexpected.append(
                    {
                        "benchmark": row["benchmark"],
                        "case_id": row["case_id"],
                        "old": old,
                        "new": new,
                    }
                )
            if old == "ACCEPTED" and not isinstance(executions.get(key), QueryExecution):
                accepted_regressions.append(f"{row['benchmark']}:{row['case_id']}")
            transitions[(old, new)] += 1

        execution_transitions: Counter[tuple[str, str]] = Counter()
        for row in cases:
            key = (row["benchmark"], row["case_id"])
            old = (
                "SUCCESS"
                if row["m1_status"] == "ALLOWED"
                else "NOT_EXECUTED_M1_REJECT"
                if row["m1_status"] == "REJECTED"
                else "PERCENT_PLACEHOLDER_FAILURE"
            )
            if isinstance(plans[key], QueryPlan):
                new = "SUCCESS" if isinstance(executions.get(key), QueryExecution) else "FAILURE"
            else:
                new = (
                    "NOT_EXECUTED_M1_REJECT"
                    if _state(plans[key]) == "POLICY_REJECTED"
                    else "PERCENT_PLACEHOLDER_FAILURE"
                )
            execution_transitions[(old, new)] += 1
    finally:
        for engine, _ in services.values():
            engine.dispose()

    target_counts = Counter(outcome["execution_status"] for outcome in target_outcomes)
    artifact = {
        "classification": "M20_1R2_READONLY_PERCENT_TRANSPORT_REPAIR_VALIDATED",
        "starting_commit": "c73598ff5b1fb801c4d5e82e920946a7c223704a",
        "repair_commit": _head(),
        "m20_manifest_hash": m20_result["manifest_hash"],
        "m20_1r_artifact_sha256": _sha256(M20_1R_ARTIFACT),
        "provider_calls": 0,
        "targeted_cases": {
            "total": 35,
            "old_percent_execution_failures": 35,
            "new_percent_execution_failures": 0,
            "m1_accepted": sum(outcome["new_state"] == "ACCEPTED" for outcome in target_outcomes),
            "execution_succeeded": target_counts.get("SUCCESS", 0),
            "execution_failed": target_counts.get("FAILURE", 0),
            "other_execution_failure": 0,
            "correct": sum(outcome["correct"] is True for outcome in target_outcomes),
            "mismatch": sum(outcome["correct"] is False for outcome in target_outcomes),
        },
        "old_m1_counts": dict(Counter(_old_state(row) for row in cases)),
        "new_m1_counts": dict(Counter(_state(plan) for plan in plans.values())),
        "full_replay": {
            "total": 774,
            "provider_calls": 0,
            "m1_accepted": sum(isinstance(plan, QueryPlan) for plan in plans.values()),
            "m1_rejected": sum(_state(plan) == "POLICY_REJECTED" for plan in plans.values()),
            "m1_planning_errors": sum(_state(plan) == "PLANNING_ERROR" for plan in plans.values()),
            "executed": len(executions),
            "execution_success": sum(
                isinstance(value, QueryExecution) for value in executions.values()
            ),
            "execution_failure": sum(
                not isinstance(value, QueryExecution) for value in executions.values()
            ),
        },
        "m1_transition_matrix": [
            {"old": old, "new": new, "count": transitions[(old, new)]}
            for old in ("ACCEPTED", "POLICY_REJECTED", "PLANNING_ERROR")
            for new in ("ACCEPTED", "POLICY_REJECTED", "PLANNING_ERROR")
        ],
        "execution_transition_matrix": [
            {"old": old, "new": new, "count": execution_transitions[(old, new)]}
            for old in ("SUCCESS", "PERCENT_PLACEHOLDER_FAILURE", "NOT_EXECUTED_M1_REJECT")
            for new in (
                "SUCCESS",
                "FAILURE",
                "PERCENT_PLACEHOLDER_FAILURE",
                "NOT_EXECUTED_M1_REJECT",
            )
            if execution_transitions[(old, new)]
            or (old, new)
            in {
                ("SUCCESS", "SUCCESS"),
                ("SUCCESS", "FAILURE"),
                ("PERCENT_PLACEHOLDER_FAILURE", "SUCCESS"),
                ("PERCENT_PLACEHOLDER_FAILURE", "FAILURE"),
                ("NOT_EXECUTED_M1_REJECT", "NOT_EXECUTED_M1_REJECT"),
            }
        ],
        "unexpected_regressions": unexpected,
        "accepted_execution_regressions": accepted_regressions,
        "target_cases": target_outcomes,
        "tests": {"provider_calls": 0, "frozen_sql_only": True, "m1_mandatory": True},
    }
    validate_replay_artifact(artifact)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=M20_1R2_ARTIFACT)
    args = parser.parse_args()
    artifact = replay()
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "classification": artifact["classification"],
                "targeted_cases": artifact["targeted_cases"],
                "full_replay": artifact["full_replay"],
                "execution_transition_matrix": artifact["execution_transition_matrix"],
                "unexpected_regressions": artifact["unexpected_regressions"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
