from __future__ import annotations

# The evaluator keeps result-gate flow compact.
# ruff: noqa: E501
import json
from pathlib import Path
from typing import Any

from benchmark.authoring import SCHEMA_NAMES, connection_kwargs_from_env, seed_database
from benchmark.models import ResultContract, Submission, compare_rows
from benchmark.safety import SqlAdmissionError, execute_query
from benchmark.validator import load_pilot, validate_references


def evaluate_submission(
    submission: Submission, reference_results: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate one generic submission against already-validated reference results."""
    match = next(
        ((case, truth) for case, truth in load_pilot() if case["case_id"] == submission.case_id),
        None,
    )
    if match is None:
        return {"case_id": submission.case_id, "passed": False, "reason": "UNKNOWN_CASE"}
    case, truth = match
    expected = truth["semantic_target"]["behavior"]
    expected_decision = {
        "ANSWERABLE": "ANSWER",
        "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
        "AMBIGUOUS": "NEEDS_CLARIFICATION",
        "POLICY_BLOCKED": "BLOCKED_POLICY",
    }[expected]
    if submission.decision != expected_decision:
        return {
            "case_id": submission.case_id,
            "passed": False,
            "reason": "WRONG_GOVERNED_DECISION",
            "expected_decision": expected_decision,
        }
    if (
        submission.reason_code is not None
        and submission.reason_code not in Submission.ALLOWED_REASON_CODES
    ):
        return {"case_id": submission.case_id, "passed": False, "reason": "UNKNOWN_REASON_CODE"}
    if expected != "ANSWERABLE":
        if submission.sql is not None:
            return {
                "case_id": submission.case_id,
                "passed": False,
                "reason": "NON_ANSWERABLE_SQL_PRESENT",
            }
        return {"case_id": submission.case_id, "passed": True, "reason": "GOVERNED_DECISION_MATCH"}
    if not submission.sql:
        return {"case_id": submission.case_id, "passed": False, "reason": "MISSING_SQL"}
    try:
        seed_database(case["database_id"], connection_kwargs_from_env())
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        for fixture in [{"fixture_id": "base", "patch_sql": []}] + truth["counterfactual_fixtures"]:
            _columns, rows = execute_query(
                connection_kwargs_from_env(),
                SCHEMA_NAMES[case["database_id"]],
                submission.sql,
                patch_sql=fixture["patch_sql"],
            )
            expected_rows = reference_results[case["case_id"]]["fixtures"][fixture["fixture_id"]][
                "rows"
            ]
            same, reason = compare_rows(rows, expected_rows, contract)
            if not same:
                return {
                    "case_id": submission.case_id,
                    "passed": False,
                    "reason": reason,
                    "fixture_id": fixture["fixture_id"],
                }
    except SqlAdmissionError as exc:
        return {"case_id": submission.case_id, "passed": False, "reason": str(exc)}
    except Exception as exc:
        return {
            "case_id": submission.case_id,
            "passed": False,
            "reason": f"EXECUTION_ERROR:{type(exc).__name__}",
        }
    return {"case_id": submission.case_id, "passed": True, "reason": "ANSWER_TEST_SUITE_MATCH"}


def evaluate_file(path: Path) -> dict[str, Any]:
    references = validate_references()
    submissions = [
        Submission.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results = [
        evaluate_submission(submission, references.expected_results) for submission in submissions
    ]
    return {
        "submissions": len(results),
        "passed": sum(bool(item["passed"]) for item in results),
        "results": results,
    }
