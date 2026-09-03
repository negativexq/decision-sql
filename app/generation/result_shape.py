"""Small, untrusted result-shape contract used by the M2.8 experiment."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse_one


class ResultShapeKind(StrEnum):
    ROW_FILTER = "ROW_FILTER"
    AGGREGATE = "AGGREGATE"
    GROUPED_AGGREGATE = "GROUPED_AGGREGATE"
    GROUPED_TOP_K = "GROUPED_TOP_K"
    RATIO = "RATIO"
    WINDOW = "WINDOW"
    OTHER = "OTHER"


class ResultOutputKind(StrEnum):
    PHYSICAL_COLUMN = "PHYSICAL_COLUMN"
    DERIVED_VALUE = "DERIVED_VALUE"


class ResultShapeOutput(BaseModel):
    """One requested output; it contains no authorization or join authority."""

    model_config = ConfigDict(frozen=True)

    semantic_label: str = Field(min_length=1, max_length=120)
    kind: ResultOutputKind
    source_hint: str | None = Field(default=None, min_length=1, max_length=160)


class ResultShapeProposal(BaseModel):
    """Untrusted, deliberately narrow output contract proposed by a provider."""

    model_config = ConfigDict(frozen=True)

    outputs: tuple[ResultShapeOutput, ...] = Field(min_length=1, max_length=32)
    shape: ResultShapeKind
    explicit_limit: int | None = Field(default=None, ge=1, le=1000)
    explicit_order_direction: Literal["ASC", "DESC"] | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class ProjectionStatus(StrEnum):
    EXACT = "PROJECTION_EXACT"
    EXTRA = "PROJECTION_EXTRA"
    MISSING = "PROJECTION_MISSING"
    WRONG = "PROJECTION_WRONG"
    DERIVED_EXPRESSION_MISMATCH = "PROJECTION_DERIVED_EXPRESSION_MISMATCH"
    UNCERTAIN = "PROJECTION_UNCERTAIN"


class ResultShapeValidation(BaseModel):
    """Deterministic contract consistency result, not an authorization result."""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    projection_status: ProjectionStatus
    expected_arity: int
    actual_arity: int | None = None
    physical_outputs_matched: int = 0
    physical_outputs_expected: int = 0
    unexpected_projection_count: int = 0
    missing_projection_count: int = 0
    limit_matches: bool | None = None
    order_direction_matches: bool | None = None
    message: str


def validate_result_shape(proposal: ResultShapeProposal, sql: str) -> ResultShapeValidation:
    """Validate only the generated projection and explicitly requested shape details."""

    tree = parse_one(sql, dialect="postgres")
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    expected_arity = len(proposal.outputs)
    if select is None:
        return ResultShapeValidation(
            accepted=False,
            projection_status=ProjectionStatus.UNCERTAIN,
            expected_arity=expected_arity,
            message="Generated SQL has no inspectable SELECT projection.",
        )

    expressions = tuple(_unwrap_alias(item) for item in select.expressions)
    actual_arity = len(expressions)
    aliases = {
        table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)
    }
    physical_outputs = tuple(
        output for output in proposal.outputs if output.kind is ResultOutputKind.PHYSICAL_COLUMN
    )
    matched = sum(
        _matches_source_hint(
            expressions[index] if index < len(expressions) else None,
            output.source_hint,
            aliases,
        )
        for index, output in enumerate(proposal.outputs)
        if output.kind is ResultOutputKind.PHYSICAL_COLUMN
    )
    extra = max(0, actual_arity - expected_arity)
    missing = max(0, expected_arity - actual_arity)
    if extra:
        status = ProjectionStatus.EXTRA
    elif missing:
        status = ProjectionStatus.MISSING
    elif matched < len(physical_outputs):
        status = ProjectionStatus.WRONG
    elif any(output.kind is ResultOutputKind.DERIVED_VALUE for output in proposal.outputs):
        status = ProjectionStatus.EXACT
    else:
        status = ProjectionStatus.EXACT

    limit_matches = _limit_matches(tree, proposal.explicit_limit)
    direction_matches = _direction_matches(tree, proposal.explicit_order_direction)
    accepted = (
        actual_arity == expected_arity
        and status not in {ProjectionStatus.WRONG, ProjectionStatus.UNCERTAIN}
        and limit_matches is not False
        and direction_matches is not False
    )
    return ResultShapeValidation(
        accepted=accepted,
        projection_status=status,
        expected_arity=expected_arity,
        actual_arity=actual_arity,
        physical_outputs_matched=matched,
        physical_outputs_expected=len(physical_outputs),
        unexpected_projection_count=extra,
        missing_projection_count=missing,
        limit_matches=limit_matches,
        order_direction_matches=direction_matches,
        message=(
            "Generated projection and explicit shape constraints match."
            if accepted
            else "Generated SQL is inconsistent with the proposed result shape."
        ),
    )


def _unwrap_alias(expression: exp.Expression) -> exp.Expression:
    return expression.this if isinstance(expression, exp.Alias) else expression


def _matches_source_hint(
    expression: exp.Expression | None,
    source_hint: str | None,
    aliases: dict[str, str],
) -> bool:
    if not source_hint:
        return True
    if not isinstance(expression, exp.Column):
        return False
    expression_name = expression.sql(dialect="postgres").lower()
    normalized_hint = source_hint.lower()
    if "." not in expression_name:
        return expression_name == normalized_hint.rsplit(".", 1)[-1]
    expression_table, expression_column = expression_name.rsplit(".", 1)
    resolved_table = aliases.get(expression_table, expression_table)
    return f"{resolved_table}.{expression_column}" == normalized_hint


def _limit_matches(tree: exp.Expression, expected: int | None) -> bool | None:
    if expected is None:
        return None
    limit = tree.find(exp.Limit)
    if (
        limit is None
        or not isinstance(limit.expression, exp.Literal)
        or not limit.expression.is_int
    ):
        return False
    return int(limit.expression.this) == expected


def _direction_matches(tree: exp.Expression, expected: str | None) -> bool | None:
    if expected is None:
        return None
    ordered = next(iter(tree.find_all(exp.Ordered)), None)
    if ordered is None:
        return False
    actual = "DESC" if ordered.args.get("desc") else "ASC"
    return actual == expected
