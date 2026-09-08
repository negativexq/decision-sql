from __future__ import annotations

# The comparator keeps typed matching logic compact.
# ruff: noqa: E501
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ResultContract:
    column_count: int
    row_order: bool = False
    aliases_significant: bool = False
    duplicates_significant: bool = True
    numeric_tolerance: str | None = None
    timestamp_timezone: str = "UTC"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResultContract:
        return cls(
            column_count=int(value["column_count"]),
            row_order=bool(value.get("row_order", False)),
            aliases_significant=bool(value.get("aliases_significant", False)),
            duplicates_significant=bool(value.get("duplicates_significant", True)),
            numeric_tolerance=value.get("numeric_tolerance"),
            timestamp_timezone=str(value.get("timestamp_timezone", "UTC")),
        )


def _normal(value: Any) -> Any:
    if value is None:
        return ("NULL",)
    if isinstance(value, Decimal):
        return ("DECIMAL", format(value, "f"))
    if isinstance(value, float):
        return ("FLOAT", format(value, ".17g"))
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        current = current.astimezone(UTC)
        return ("TIMESTAMP", current.isoformat())
    if isinstance(value, date):
        return ("DATE", value.isoformat())
    if isinstance(value, bool):
        return ("BOOL", value)
    if isinstance(value, (int, str, bytes)):
        return (type(value).__name__.upper(), value)
    if isinstance(value, dict):
        return ("JSON", tuple(sorted((str(k), _normal(v)) for k, v in value.items())))
    if isinstance(value, list):
        return ("LIST", tuple(_normal(v) for v in value))
    return (type(value).__name__, str(value))


def compare_rows(
    candidate: list[tuple[Any, ...]],
    expected: list[tuple[Any, ...]],
    contract: ResultContract,
) -> tuple[bool, str]:
    if any(len(row) != contract.column_count for row in candidate + expected):
        return False, "COLUMN_COUNT_MISMATCH"
    if contract.numeric_tolerance is not None:
        tolerance = Decimal(contract.numeric_tolerance)

        def cell_equal(left_value: Any, right_value: Any) -> bool:
            if left_value is None or right_value is None:
                return left_value is right_value
            if (
                isinstance(left_value, (int, float, Decimal))
                and not isinstance(left_value, bool)
                and isinstance(right_value, (int, float, Decimal))
                and not isinstance(right_value, bool)
            ):
                return abs(Decimal(str(left_value)) - Decimal(str(right_value))) <= tolerance
            return bool(_normal(left_value) == _normal(right_value))

        def row_equal(left_row: tuple[Any, ...], right_row: tuple[Any, ...]) -> bool:
            return len(left_row) == len(right_row) and all(
                cell_equal(left, right) for left, right in zip(left_row, right_row, strict=True)
            )

        if contract.row_order:
            same = len(candidate) == len(expected) and all(
                row_equal(left, right) for left, right in zip(candidate, expected, strict=True)
            )
        else:
            unmatched = list(expected)
            same = True
            for left in candidate:
                match_index = next(
                    (index for index, right in enumerate(unmatched) if row_equal(left, right)), None
                )
                if match_index is None:
                    same = False
                    break
                unmatched.pop(match_index)
            same = same and not unmatched
        return (True, "MATCH") if same else (False, "RESULT_MISMATCH")
    if contract.row_order:
        ordered_left = [[_normal(value) for value in row] for row in candidate]
        ordered_right = [[_normal(value) for value in row] for row in expected]
        return (True, "MATCH") if ordered_left == ordered_right else (False, "RESULT_MISMATCH")
    else:
        left_rows = [tuple(_normal(value) for value in row) for row in candidate]
        right_rows = [tuple(_normal(value) for value in row) for row in expected]
        unordered_left = Counter(left_rows)
        unordered_right = Counter(right_rows)
    if unordered_left == unordered_right:
        return True, "MATCH"
    return False, "RESULT_MISMATCH"


@dataclass(frozen=True)
class Submission:
    case_id: str
    decision: str
    sql: str | None = None
    reason_code: str | None = None

    ALLOWED_DECISIONS = frozenset(
        {"ANSWER", "BLOCKED_AUTHORITY", "NEEDS_CLARIFICATION", "BLOCKED_POLICY"}
    )
    ALLOWED_REASON_CODES = frozenset(
        {"MISSING_AUTHORIZED_RELATIONSHIP", "AMBIGUOUS_SEMANTICS", "READ_ONLY_POLICY", "NO_REASON"}
    )
    REQUIRED_REASON_CODES = {
        "BLOCKED_AUTHORITY": "MISSING_AUTHORIZED_RELATIONSHIP",
        "NEEDS_CLARIFICATION": "AMBIGUOUS_SEMANTICS",
        "BLOCKED_POLICY": "READ_ONLY_POLICY",
    }

    @classmethod
    def from_dict_unchecked(cls, value: dict[str, Any]) -> Submission:
        if set(value) != {"case_id", "decision", "sql", "reason_code"}:
            raise ValueError("SUBMISSION_FIELDS")
        if not isinstance(value["case_id"], str) or not value["case_id"]:
            raise ValueError("SUBMISSION_CASE_ID")
        if not isinstance(value["decision"], str):
            raise ValueError("SUBMISSION_DECISION_TYPE")
        if value["sql"] is not None and not isinstance(value["sql"], str):
            raise ValueError("SUBMISSION_SQL_TYPE")
        if value["reason_code"] is not None and (not isinstance(value["reason_code"], str)):
            raise ValueError("SUBMISSION_REASON_CODE_TYPE")
        return cls(
            case_id=value["case_id"],
            decision=value["decision"],
            sql=value.get("sql"),
            reason_code=value.get("reason_code"),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Submission:
        submission = cls.from_dict_unchecked(value)
        errors = validate_submission_invariants(None, submission)
        if errors:
            raise ValueError(errors[0])
        return submission


def validate_submission_invariants(
    request_case_id: str | None, parsed_submission: Submission
) -> tuple[str, ...]:
    """Validate cross-field semantics after provider-shaped JSON parsing."""
    errors: list[str] = []
    if request_case_id is not None and parsed_submission.case_id != request_case_id:
        errors.append("CASE_ID_MISMATCH")
    if parsed_submission.decision not in Submission.ALLOWED_DECISIONS:
        errors.append("UNKNOWN_DECISION")
    if (
        parsed_submission.reason_code is not None
        and parsed_submission.reason_code not in Submission.ALLOWED_REASON_CODES
    ):
        errors.append("UNKNOWN_REASON_CODE")
    if parsed_submission.decision == "ANSWER":
        if not isinstance(parsed_submission.sql, str) or not parsed_submission.sql.strip():
            errors.append("ANSWER_SQL_REQUIRED")
        if parsed_submission.reason_code is not None:
            errors.append("ANSWER_REASON_MUST_BE_NULL")
    elif parsed_submission.decision in Submission.REQUIRED_REASON_CODES:
        if parsed_submission.sql is not None:
            errors.append("NON_ANSWER_SQL_MUST_BE_NULL")
        expected_reason = Submission.REQUIRED_REASON_CODES[parsed_submission.decision]
        if parsed_submission.reason_code != expected_reason:
            code = {
                "BLOCKED_AUTHORITY": "AUTHORITY_REASON_MISMATCH",
                "NEEDS_CLARIFICATION": "AMBIGUITY_REASON_MISMATCH",
                "BLOCKED_POLICY": "POLICY_REASON_MISMATCH",
            }[parsed_submission.decision]
            errors.append(code)
    return tuple(dict.fromkeys(errors))
