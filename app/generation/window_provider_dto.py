"""Provider-friendly transport DTO for the M2.12.1 diagnostic only."""

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderPattern(StrEnum):
    LATEST_PER_GROUP = "LATEST_PER_GROUP"
    TOP_N_PER_GROUP = "TOP_N_PER_GROUP"
    LAG = "LAG"
    LEAD = "LEAD"
    RUNNING_AGGREGATE = "RUNNING_AGGREGATE"
    MOVING_AGGREGATE = "MOVING_AGGREGATE"
    RANKING = "RANKING"
    SHARE_OF_TOTAL = "SHARE_OF_TOTAL"


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


class ProviderOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    column: str = Field(min_length=3, max_length=120)
    direction: ProviderDirection = ProviderDirection.ASC


class ProviderWindowIRDTO(BaseModel):
    """Flat LLM transport object; it is not the compiler-facing IR."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: ProviderPattern
    source_relation: str = Field(min_length=1, max_length=63)
    physical_outputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[ProviderOrder, ...] = Field(default=(), max_length=8)
    tie_breakers: tuple[ProviderOrder, ...] = Field(default=(), max_length=8)
    target: str | None = Field(default=None, min_length=3, max_length=120)
    offset: int | None = Field(default=None, ge=1, le=100)
    n: int | None = Field(default=None, ge=1, le=100)
    tie_policy: ProviderTiePolicy | None = None
    aggregate: ProviderAggregate | None = None
    ranking_function: ProviderRankingFunction | None = None
    frame_mode: str | None = None
    frame_start_kind: str | None = None
    frame_start_value: int | None = Field(default=None, ge=1, le=1000)
    frame_end_kind: str | None = None
    frame_end_value: int | None = Field(default=None, ge=1, le=1000)
    scale: float | None = Field(default=None, ge=0, le=1000000)
    alias: str | None = Field(default=None, min_length=1, max_length=63)

    @model_validator(mode="after")
    def validate_alias(self) -> "ProviderWindowIRDTO":
        if (
            self.alias is not None
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.alias) is None
        ):
            raise ValueError("invalid provider output alias")
        return self


class ProviderLagDTO(ProviderWindowIRDTO):
    pattern: Literal[ProviderPattern.LAG] = ProviderPattern.LAG


class ProviderRankingDTO(ProviderWindowIRDTO):
    pattern: Literal[ProviderPattern.RANKING] = ProviderPattern.RANKING
