"""The runtime's own words to the model, and the checks that decide when to use them.

Feedback names what was rejected or looked wrong (a rule, a column, an empty
result) and never quotes database error text or stored values the model did not
already see. The result checks read a query's result to decide whether the run
gets its repair round.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.generation.decision_contract import ProductionDecision
from app.generation.provider import RuntimeRejectionFeedback
from app.sql.models import (
    PolicyCode,
    QueryExecution,
    QueryPlan,
    SqlPlanFailure,
    SqlSafetyStatus,
    UnmatchedValue,
)
from app.sql.results import keeps_ties


def looks_empty(execution: QueryExecution) -> bool:
    """No rows, or one row of only zeros and NULLs: what a filter matching nothing returns."""
    if execution.row_count == 0:
        return True
    if execution.row_count != 1 or not execution.rows:
        return False
    return all(
        value is None or (isinstance(value, int | float | Decimal) and value == 0)
        for value in execution.rows[0].values()
    )


def empty_result_feedback(
    previous: ProductionDecision, unmatched: tuple[UnmatchedValue, ...]
) -> RuntimeRejectionFeedback:
    named = "; ".join(f"{item.table}.{item.column} = '{item.literal}'" for item in unmatched[:3])
    missing = (
        f" The column stores no such value for {named}, even ignoring case, spacing, and "
        "punctuation."
        if named
        else ""
    )
    message = (
        "The query ran and matched no rows (or only zeros and NULLs)."
        f"{missing} Check each filter against the question: a condition the question asks "
        "you to report, rather than to require, belongs in the selected columns, and a "
        "literal should be spelled as the column stores it. If the empty result is the "
        "answer, keep the query."
    )
    return RuntimeRejectionFeedback(
        previous=previous, stage="result_check", code="EMPTY_RESULT", message=message[:600]
    )


def range_feedback(
    previous: ProductionDecision, columns: tuple[str, ...]
) -> RuntimeRejectionFeedback:
    named = ", ".join(columns[:3])
    message = (
        f"The query ran, and the percentage column {named} holds a value below 0 or above "
        "100. A share of a whole lies between 0 and 100: check the numerator, the "
        "denominator, and the factor of 100 against the question. If the question asks for "
        "a change or a formula that can leave that range, keep the query."
    )
    return RuntimeRejectionFeedback(
        previous=previous, stage="result_check", code="OUT_OF_RANGE", message=message[:600]
    )


EXECUTION_MESSAGES = {
    "INVALID_TEXT_CONVERSION": (
        "A cast or comparison met a stored text value that is not in the target type's "
        "format, such as a letter in a column cast to a number. A WHERE filter does not "
        "protect a cast: PostgreSQL may evaluate the cast first. Guard the cast itself, "
        "for example CASE WHEN col <> '' AND TRANSLATE(col, '0123456789', '') = '' "
        "THEN col::numeric END, or compare as text. Regular expressions (~, SIMILAR TO) "
        "are not allowed."
    ),
    "NUMERIC_OUT_OF_RANGE": "A number grew beyond its type's range. Use a wider type.",
    "DIVISION_BY_ZERO": "A division met a zero divisor. Guard the divisor with NULLIF.",
    "INVALID_DATETIME_FORMAT": (
        "A text value did not fit the date or time format it was cast to. Match the "
        "stored format, or convert only the values that fit."
    ),
    "DATETIME_OUT_OF_RANGE": "A date or time value fell outside its valid range.",
    "SUBQUERY_RETURNED_SEVERAL_ROWS": (
        "A subquery used as a single value returned more than one row. Make it return "
        "one row, or compare with IN or EXISTS."
    ),
    "TYPE_MISMATCH": "An expression has a different data type than its context requires.",
    "NO_SUCH_FUNCTION_FOR_TYPES": (
        "A function or operator was applied to argument types it does not accept. Cast the "
        "arguments to the types it takes."
    ),
    "STATEMENT_TIMEOUT": (
        "The query ran past the time limit. Read fewer rows, or filter before joining."
    ),
}


def execution_feedback(previous: ProductionDecision, error_class: str) -> RuntimeRejectionFeedback:
    """The runtime's own words for a database failure; the database's text never goes back."""
    message = f"The query failed while running: {EXECUTION_MESSAGES[error_class]}"
    return RuntimeRejectionFeedback(
        previous=previous, stage="execution", code=error_class, message=message[:600]
    )


def tied(execution: QueryExecution, planned: QueryPlan | SqlPlanFailure) -> bool:
    """Several rows that all share the extreme value the query keeps."""
    return (
        execution.row_count > 1
        and isinstance(planned, QueryPlan)
        and keeps_ties(planned.normalized_sql)
    )


# Rejections the model can fix by rewriting its own SQL.  Authority, write
# statements, forbidden tables or columns, and system catalogs are final: a
# second attempt there would only probe the boundary.
_REPAIRABLE_POLICY_CODES = frozenset(
    {
        PolicyCode.EMPTY_SQL,
        PolicyCode.UNKNOWN_TABLE,
        PolicyCode.UNKNOWN_COLUMN,
        PolicyCode.FORBIDDEN_FUNCTION,
        PolicyCode.UNAUTHORIZED_JOIN,
    }
)

_FANOUT_MESSAGE = (
    "A measure from a parent table is aggregated after a join to a child table that repeats "
    "its rows, so the result would count it more than once. Aggregate the measure at its own "
    "table's grain, or test the child rows with EXISTS instead of joining them."
)


_GRAIN_MESSAGES = {
    "DISTINCT_VALUE_FANOUT_MASK": (
        "DISTINCT inside an aggregate hides the rows a join repeats, but it also merges "
        "equal values from different rows, so the result is still wrong. Aggregate the "
        "measure at its own table's grain, or test the child rows with EXISTS."
    ),
    "UNSAFE_ROLLUP": (
        "A measure that cannot be added up (a ratio, balance, or derived value) is summed. "
        "Use the aggregation its definition allows."
    ),
    "INCOMPATIBLE_MEASURE_GRAINS": (
        "Measures from tables at different grains are combined in one aggregate. Aggregate "
        "each at its own grain before combining them."
    ),
}


def failure_code(planned: SqlPlanFailure) -> str:
    if planned.rejection is not None:
        return planned.rejection.code.value
    return planned.semantic_reason or planned.status.value


def repair_feedback(
    planned: object, previous: ProductionDecision, settings: Settings
) -> RuntimeRejectionFeedback | None:
    """Describe a fixable rejection in the runtime's own words, or None when it is final.

    Execution errors are never sent back: database error text can quote stored values.
    """
    if isinstance(planned, QueryPlan):
        return value_feedback(planned, previous)
    if not isinstance(planned, SqlPlanFailure):
        return None
    if planned.status is SqlSafetyStatus.SQL_PARSE_ERROR:
        stage, code = "sql_parse", SqlSafetyStatus.SQL_PARSE_ERROR.value
        message = planned.error or "The SQL could not be parsed."
    elif (
        planned.status is SqlSafetyStatus.POLICY_REJECTION
        and planned.rejection is not None
        and planned.rejection.code in _REPAIRABLE_POLICY_CODES
    ):
        stage, code, message = (
            "global_policy",
            planned.rejection.code.value,
            planned.rejection.message,
        )
    elif planned.status is SqlSafetyStatus.QUERY_COST_REJECTION and planned.estimate is not None:
        stage, code = "cost_gate", PolicyCode.QUERY_TOO_EXPENSIVE.value
        message = (
            f"The query plan is estimated at {planned.estimate.plan_rows} rows and cost "
            f"{planned.estimate.total_cost:.0f}; the limits are {settings.max_plan_rows} rows "
            f"and cost {settings.max_plan_cost:.0f}. Write a query whose plan stays within them."
        )
    elif planned.status is SqlSafetyStatus.SEMANTIC_REJECTION:
        stage, code = "grain_safety", planned.semantic_reason or planned.status.value
        message = _GRAIN_MESSAGES.get(code, _FANOUT_MESSAGE)
    else:
        return None
    return RuntimeRejectionFeedback(
        previous=previous, stage=stage, code=code, message=" ".join(message.split())[:600]
    )


def value_feedback(
    plan: QueryPlan, previous: ProductionDecision
) -> RuntimeRejectionFeedback | None:
    """Tell the model a text it guessed is not stored, without naming the values that are.

    A model that saw the column's values chose the text knowingly (the value may be
    absent from the data), so only guesses at unshared columns are sent back.
    """
    blind = [item for item in plan.unmatched_values if not item.shared]
    if not blind:
        return None
    named = "; ".join(f"{item.table}.{item.column} = '{item.literal}'" for item in blind[:3])
    message = (
        f"The query filters on {named}, but the column stores no such value, even ignoring "
        "case, spacing, and punctuation, so the filter matches no rows. If the question "
        "refers to a value the column spells differently, write a filter that finds it; "
        "if the value is absent from the data, keep the query."
    )
    return RuntimeRejectionFeedback(
        previous=previous, stage="value_check", code="VALUE_NOT_STORED", message=message[:600]
    )


def repair_skip_reason(planned: object, repairable: bool) -> str:
    if not isinstance(planned, SqlPlanFailure):
        return "NOT_NEEDED"
    return "FINAL_REJECTION" if repairable else "REPAIR_NOT_ALLOWED"


def runtime_from_plan(result: object) -> tuple[str, str | None]:
    if not isinstance(result, SqlPlanFailure):
        return "PLANNED", None
    if result.authority_rejection is not None:
        return "AUTHORITY_REJECTED", result.authority_rejection.code.value
    if result.rejection is not None:
        if result.status.value == "POLICY_REJECTION":
            return "POLICY_REJECTED", result.rejection.code.value
        return "RUNTIME_REJECTED", result.rejection.code.value
    return "RUNTIME_REJECTED", result.semantic_reason or result.status.value


def comparable(execution: QueryExecution) -> tuple[int, list[str]]:
    """A result's column count and rows, row order ignored, for comparing readings."""
    rows = sorted(
        repr(tuple(row.get(name) for name in execution.columns)) for row in execution.rows
    )
    return len(execution.columns), rows
