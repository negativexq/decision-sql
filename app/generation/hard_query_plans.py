"""Narrow, untrusted operation plans for the M2.11 diagnostic."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TopKMeasure(BaseModel):
    model_config = ConfigDict(frozen=True)

    semantic_label: str = Field(min_length=1, max_length=120)
    aggregation: str = Field(min_length=1, max_length=32)
    components: tuple[str, ...] = Field(default=(), max_length=8)


class TopKPlan(BaseModel):
    """Untrusted decomposition of only a grouped top-k operation."""

    model_config = ConfigDict(frozen=True)

    entity_outputs: tuple[str, ...] = Field(min_length=1, max_length=8)
    measure: TopKMeasure
    group_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_direction: Literal["ASC", "DESC"]
    limit: int = Field(ge=1, le=1000)


class RatioComponent(BaseModel):
    model_config = ConfigDict(frozen=True)

    semantic_label: str = Field(min_length=1, max_length=120)
    source_columns: tuple[str, ...] = Field(default=(), max_length=8)
    aggregation: str = Field(min_length=1, max_length=32)
    distinct: bool = False


class RatioPlan(BaseModel):
    """Untrusted decomposition of numerator/denominator arithmetic."""

    model_config = ConfigDict(frozen=True)

    numerator: RatioComponent
    denominator: RatioComponent
    grain: str = Field(min_length=1, max_length=80)
    scale: float | None = None


class WindowPlan(BaseModel):
    """Untrusted decomposition of one window operation."""

    model_config = ConfigDict(frozen=True)

    requested_outputs: tuple[str, ...] = Field(min_length=1, max_length=8)
    window_function: str = Field(min_length=1, max_length=32)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_direction: Literal["ASC", "DESC"]


class TopKPlanProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: TopKPlan
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class RatioPlanProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: RatioPlan
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


class WindowPlanProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan: WindowPlan
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


OperationPlan = TopKPlan | RatioPlan | WindowPlan
OperationPlanProposal = TopKPlanProposal | RatioPlanProposal | WindowPlanProposal


def operation_plan_references(plan: OperationPlan) -> tuple[str, ...]:
    """Return only explicit physical references for offline visibility checks."""
    if isinstance(plan, TopKPlan):
        values = [*plan.entity_outputs, *plan.group_by, *plan.measure.components]
    elif isinstance(plan, RatioPlan):
        values = [*plan.numerator.source_columns, *plan.denominator.source_columns]
    else:
        values = [*plan.requested_outputs, *plan.partition_by, *plan.order_by]
    references: list[str] = []
    for value in values:
        matches = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", value)
        references.extend(matches or ([value] if "." in value else []))
    return tuple(dict.fromkeys(references))


def validate_operation_plan_visibility(plan: OperationPlan, visible_columns: set[str]) -> None:
    """Reject only explicitly qualified references absent from supplied context."""
    invalid = sorted(
        reference.lower()
        for reference in operation_plan_references(plan)
        if "." in reference and reference.lower() not in visible_columns
    )
    if invalid:
        raise ValueError(
            "Operation plan references invisible schema columns: " + ", ".join(invalid)
        )
