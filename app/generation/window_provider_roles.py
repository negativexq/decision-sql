"""Role-explicit, provider-facing Window DTOs for M2.12R.1.

These models are deliberately not the compiler IR.  They are small transport
contracts; :mod:`window_role_adapter` converts them into the existing strict
``WindowQueryIR``.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RoleDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class RoleAggregate(StrEnum):
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class RoleRankingFunction(StrEnum):
    ROW_NUMBER = "ROW_NUMBER"
    RANK = "RANK"
    DENSE_RANK = "DENSE_RANK"


class RoleTiePolicy(StrEnum):
    EXACT_N = "EXACT_N"
    INCLUDE_TIES = "INCLUDE_TIES"


class _RoleBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_table: str = Field(min_length=1, max_length=63)
    passthrough_columns: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("source_table", "passthrough_columns", mode="before")
    @classmethod
    def reject_sql_like_values(cls, value: object) -> object:
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if isinstance(item, str) and any(x in item for x in (";", "--", "/*", "*/")):
                raise ValueError("provider identifiers cannot contain SQL markers")
        return value


class LatestPerGroupRoleDTO(_RoleBase):
    partition_columns: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_column: str = Field(min_length=1, max_length=63)
    order_direction: RoleDirection
    tie_breaker_column: str | None = Field(default=None, max_length=63)
    tie_breaker_direction: RoleDirection | None = None


class TopNPerGroupRoleDTO(_RoleBase):
    partition_columns: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_column: str = Field(min_length=1, max_length=63)
    order_direction: RoleDirection
    top_n: int = Field(ge=1, le=100)
    tie_policy: RoleTiePolicy
    tie_breaker_column: str | None = Field(default=None, max_length=63)
    tie_breaker_direction: RoleDirection | None = None


class LagLeadRoleDTO(_RoleBase):
    window_direction: Literal["PREVIOUS", "NEXT"]
    input_value_column: str = Field(min_length=1, max_length=63)
    partition_columns: tuple[str, ...] = Field(default=(), max_length=8)
    order_column: str = Field(min_length=1, max_length=63)
    order_direction: RoleDirection
    offset_rows: int = Field(ge=1, le=100)


class RunningAggregateRoleDTO(_RoleBase):
    input_value_column: str = Field(min_length=1, max_length=63)
    aggregate_function: RoleAggregate
    partition_columns: tuple[str, ...] = Field(default=(), max_length=8)
    order_column: str = Field(min_length=1, max_length=63)
    order_direction: RoleDirection


class MovingAggregateRoleDTO(_RoleBase):
    input_value_column: str = Field(min_length=1, max_length=63)
    aggregate_function: RoleAggregate
    partition_columns: tuple[str, ...] = Field(default=(), max_length=8)
    order_column: str = Field(min_length=1, max_length=63)
    order_direction: RoleDirection
    preceding_rows: int = Field(ge=1, le=100)
    include_current_row: bool


class RankingRoleDTO(_RoleBase):
    ranking_function: RoleRankingFunction
    partition_columns: tuple[str, ...] = Field(default=(), max_length=8)
    order_column: str = Field(min_length=1, max_length=63)
    order_direction: RoleDirection


class ShareOfTotalRoleDTO(_RoleBase):
    input_value_column: str = Field(min_length=1, max_length=63)
    partition_columns: tuple[str, ...] = Field(default=(), max_length=8)
    scale_factor: float | None = Field(default=None, ge=0, le=1_000_000)


class MixedRoleOperationDTO(BaseModel):
    """Small bounded mixed DTO retained only for the frozen mixed family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal[
        "LAG", "LEAD", "RUNNING_AGGREGATE", "MOVING_AGGREGATE", "RANKING", "SHARE_OF_TOTAL"
    ]
    input_value_column: str | None = Field(default=None, max_length=63)
    partition_columns: tuple[str, ...] = Field(default=(), max_length=8)
    order_column: str | None = Field(default=None, max_length=63)
    order_direction: RoleDirection | None = None
    offset_rows: int | None = Field(default=None, ge=1, le=100)
    aggregate_function: RoleAggregate | None = None
    preceding_rows: int | None = Field(default=None, ge=1, le=100)
    include_current_row: bool | None = None
    ranking_function: RoleRankingFunction | None = None
    scale_factor: float | None = Field(default=None, ge=0, le=1_000_000)


class MixedWindowRoleDTO(_RoleBase):
    operations: tuple[MixedRoleOperationDTO, ...] = Field(min_length=2, max_length=2)


RoleProviderDTO = (
    LatestPerGroupRoleDTO
    | TopNPerGroupRoleDTO
    | LagLeadRoleDTO
    | RunningAggregateRoleDTO
    | MovingAggregateRoleDTO
    | RankingRoleDTO
    | ShareOfTotalRoleDTO
    | MixedWindowRoleDTO
)

ROLE_PATTERN_DTOS: dict[str, type[BaseModel]] = {
    "LATEST_PER_GROUP": LatestPerGroupRoleDTO,
    "TOP_N_PER_GROUP": TopNPerGroupRoleDTO,
    "LAG": LagLeadRoleDTO,
    "LEAD": LagLeadRoleDTO,
    "RUNNING_AGGREGATE": RunningAggregateRoleDTO,
    "MOVING_AGGREGATE": MovingAggregateRoleDTO,
    "RANKING": RankingRoleDTO,
    "SHARE_OF_TOTAL": ShareOfTotalRoleDTO,
    "MIXED_MULTI_WINDOW": MixedWindowRoleDTO,
}
