"""Minimal pattern-specific provider DTOs for the M2.12R diagnostic."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class ProviderAggregate(StrEnum):
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class ProviderRankingFunction(StrEnum):
    ROW_NUMBER = "ROW_NUMBER"
    RANK = "RANK"
    DENSE_RANK = "DENSE_RANK"


class ProviderTiePolicy(StrEnum):
    EXACT_N = "EXACT_N"
    INCLUDE_TIES = "INCLUDE_TIES"


class _ProviderBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=63)
    outputs: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("source", "outputs", mode="before")
    @classmethod
    def reject_sql_like_values(cls, value: object) -> object:
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if isinstance(item, str) and any(marker in item for marker in (";", "--", "/*", "*/")):
                raise ValueError("provider identifiers cannot contain SQL markers")
        return value


class LatestPerGroupProviderDTO(_ProviderBase):
    partition_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_by: str = Field(min_length=1, max_length=63)
    direction: ProviderDirection
    tie_breaker: str | None = Field(default=None, max_length=63)
    tie_breaker_direction: ProviderDirection | None = None


class TopNPerGroupProviderDTO(_ProviderBase):
    partition_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_by: str = Field(min_length=1, max_length=63)
    direction: ProviderDirection
    tie_breaker: str | None = Field(default=None, max_length=63)
    tie_breaker_direction: ProviderDirection | None = None
    n: int = Field(ge=1, le=100)
    tie_policy: ProviderTiePolicy


class LagLeadProviderDTO(_ProviderBase):
    function: Literal["LAG", "LEAD"]
    target: str = Field(min_length=1, max_length=63)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    direction: ProviderDirection
    offset: int = Field(ge=1, le=100)


class RunningAggregateProviderDTO(_ProviderBase):
    aggregate: ProviderAggregate
    target: str = Field(min_length=1, max_length=63)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    direction: ProviderDirection


class MovingAggregateProviderDTO(_ProviderBase):
    aggregate: ProviderAggregate
    target: str = Field(min_length=1, max_length=63)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    direction: ProviderDirection
    preceding: int = Field(ge=1, le=100)
    include_current: bool


class RankingProviderDTO(_ProviderBase):
    function: ProviderRankingFunction
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    direction: ProviderDirection


class ShareOfTotalProviderDTO(_ProviderBase):
    target: str = Field(min_length=1, max_length=63)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    scale: float | None = Field(default=None, ge=0, le=1_000_000)


class MixedOperationProviderDTO(BaseModel):
    """Bounded operation vocabulary for the existing mixed benchmark family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    function: Literal[
        "LAG",
        "LEAD",
        "RUNNING_AGGREGATE",
        "MOVING_AGGREGATE",
        "RANKING",
        "SHARE_OF_TOTAL",
    ]
    target: str | None = Field(default=None, max_length=63)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[str, ...] = Field(default=(), max_length=8)
    direction: ProviderDirection | None = None
    offset: int | None = Field(default=None, ge=1, le=100)
    aggregate: ProviderAggregate | None = None
    preceding: int | None = Field(default=None, ge=1, le=100)
    include_current: bool | None = None
    ranking_function: ProviderRankingFunction | None = None
    scale: float | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("target", "partition_by", "order_by", mode="before")
    @classmethod
    def reject_sql_like_values(cls, value: object) -> object:
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if isinstance(item, str) and any(marker in item for marker in (";", "--", "/*", "*/")):
                raise ValueError("provider identifiers cannot contain SQL markers")
        return value


class MixedWindowProviderDTO(_ProviderBase):
    operations: tuple[MixedOperationProviderDTO, ...] = Field(min_length=2, max_length=2)


MinimalProviderDTO = (
    LatestPerGroupProviderDTO
    | TopNPerGroupProviderDTO
    | LagLeadProviderDTO
    | RunningAggregateProviderDTO
    | MovingAggregateProviderDTO
    | RankingProviderDTO
    | ShareOfTotalProviderDTO
    | MixedWindowProviderDTO
)


PATTERN_DTOS: dict[str, type[BaseModel]] = {
    "LATEST_PER_GROUP": LatestPerGroupProviderDTO,
    "TOP_N_PER_GROUP": TopNPerGroupProviderDTO,
    "LAG": LagLeadProviderDTO,
    "LEAD": LagLeadProviderDTO,
    "RUNNING_AGGREGATE": RunningAggregateProviderDTO,
    "MOVING_AGGREGATE": MovingAggregateProviderDTO,
    "RANKING": RankingProviderDTO,
    "SHARE_OF_TOTAL": ShareOfTotalProviderDTO,
    "MIXED_MULTI_WINDOW": MixedWindowProviderDTO,
}
