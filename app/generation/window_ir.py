"""Typed, untrusted semantic window IR for M2.12.

The IR deliberately describes window semantics only.  It contains no SQL
fragments and has no execution authority; callers must validate it against the
server-owned catalog before handing it to the deterministic compiler.
"""

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.catalog.models import SchemaCatalog


class WindowPattern(StrEnum):
    LATEST_PER_GROUP = "LATEST_PER_GROUP"
    TOP_N_PER_GROUP = "TOP_N_PER_GROUP"
    LAG = "LAG"
    LEAD = "LEAD"
    RUNNING_AGGREGATE = "RUNNING_AGGREGATE"
    MOVING_AGGREGATE = "MOVING_AGGREGATE"
    RANKING = "RANKING"
    SHARE_OF_TOTAL = "SHARE_OF_TOTAL"
    MIXED_MULTI_WINDOW = "MIXED_MULTI_WINDOW"


class WindowDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class WindowAggregate(StrEnum):
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class RankingFunction(StrEnum):
    ROW_NUMBER = "ROW_NUMBER"
    RANK = "RANK"
    DENSE_RANK = "DENSE_RANK"


class TiePolicy(StrEnum):
    EXACT_N = "EXACT_N"
    INCLUDE_TIES = "INCLUDE_TIES"


class WindowOrder(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    column: str = Field(min_length=3, max_length=120)
    direction: WindowDirection = WindowDirection.ASC


class FrameBound(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "UNBOUNDED_PRECEDING",
        "N_PRECEDING",
        "CURRENT_ROW",
        "N_FOLLOWING",
        "UNBOUNDED_FOLLOWING",
    ]
    value: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_value(self) -> "FrameBound":
        needs_value = self.kind in {"N_PRECEDING", "N_FOLLOWING"}
        if needs_value != (self.value is not None):
            raise ValueError("N_PRECEDING/N_FOLLOWING require a value; other bounds do not")
        return self


class RowsFrame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["ROWS"] = "ROWS"
    start: FrameBound
    end: FrameBound


class _ComputationBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LatestPerGroupSpec(_ComputationBase):
    pattern: Literal[WindowPattern.LATEST_PER_GROUP] = WindowPattern.LATEST_PER_GROUP
    partition_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(min_length=1, max_length=8)
    tie_breakers: tuple[WindowOrder, ...] = Field(default=(), max_length=8)


class TopNPerGroupSpec(_ComputationBase):
    pattern: Literal[WindowPattern.TOP_N_PER_GROUP] = WindowPattern.TOP_N_PER_GROUP
    partition_by: tuple[str, ...] = Field(min_length=1, max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(min_length=1, max_length=8)
    n: int = Field(ge=1, le=100)
    tie_policy: TiePolicy = TiePolicy.EXACT_N


class LagSpec(_ComputationBase):
    pattern: Literal[WindowPattern.LAG] = WindowPattern.LAG
    target: str = Field(min_length=3, max_length=120)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(min_length=1, max_length=8)
    offset: int = Field(default=1, ge=1, le=100)
    alias: str = Field(min_length=1, max_length=63)


class LeadSpec(_ComputationBase):
    pattern: Literal[WindowPattern.LEAD] = WindowPattern.LEAD
    target: str = Field(min_length=3, max_length=120)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(min_length=1, max_length=8)
    offset: int = Field(default=1, ge=1, le=100)
    alias: str = Field(min_length=1, max_length=63)


class RunningAggregateSpec(_ComputationBase):
    pattern: Literal[WindowPattern.RUNNING_AGGREGATE] = WindowPattern.RUNNING_AGGREGATE
    aggregate: WindowAggregate
    target: str = Field(min_length=3, max_length=120)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(min_length=1, max_length=8)
    alias: str = Field(min_length=1, max_length=63)


class MovingAggregateSpec(_ComputationBase):
    pattern: Literal[WindowPattern.MOVING_AGGREGATE] = WindowPattern.MOVING_AGGREGATE
    aggregate: WindowAggregate
    target: str = Field(min_length=3, max_length=120)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(min_length=1, max_length=8)
    frame: RowsFrame
    alias: str = Field(min_length=1, max_length=63)


class RankingSpec(_ComputationBase):
    pattern: Literal[WindowPattern.RANKING] = WindowPattern.RANKING
    function: RankingFunction
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(min_length=1, max_length=8)
    alias: str = Field(min_length=1, max_length=63)


class ShareOfTotalSpec(_ComputationBase):
    pattern: Literal[WindowPattern.SHARE_OF_TOTAL] = WindowPattern.SHARE_OF_TOTAL
    target: str = Field(min_length=3, max_length=120)
    partition_by: tuple[str, ...] = Field(default=(), max_length=8)
    scale: float | None = Field(default=None, ge=0, le=1000000)
    alias: str = Field(min_length=1, max_length=63)


WindowComputation = Annotated[
    LatestPerGroupSpec
    | TopNPerGroupSpec
    | LagSpec
    | LeadSpec
    | RunningAggregateSpec
    | MovingAggregateSpec
    | RankingSpec
    | ShareOfTotalSpec,
    Field(discriminator="pattern"),
]


class WindowQueryIR(BaseModel):
    """A semantic window query over exactly one catalog relation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_relation: str = Field(min_length=1, max_length=63)
    pattern: WindowPattern
    physical_outputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    computations: tuple[WindowComputation, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_pattern_shape(self) -> "WindowQueryIR":
        patterns = {computation.pattern for computation in self.computations}
        if self.pattern is WindowPattern.MIXED_MULTI_WINDOW:
            if len(self.computations) < 2:
                raise ValueError("MIXED_MULTI_WINDOW requires at least two computations")
        elif len(self.computations) != 1 or next(iter(patterns)) is not self.pattern:
            raise ValueError("pattern must match the single computation pattern")
        return self


class WindowQueryIRProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    ir: WindowQueryIR
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


def validate_window_ir(ir: WindowQueryIR, catalog: SchemaCatalog) -> None:
    """Validate semantic references against server-owned catalog metadata."""
    table = catalog.get_table(ir.source_relation)
    if table is None or not table.queryable:
        raise ValueError(f"Window IR source relation is not queryable: {ir.source_relation}")
    source_name = table.name.lower()
    references: list[str] = list(ir.physical_outputs)
    for computation in ir.computations:
        if isinstance(computation, (LatestPerGroupSpec, TopNPerGroupSpec, RankingSpec)):
            references.extend(computation.partition_by)
            references.extend(item.column for item in computation.order_by)
            if isinstance(computation, LatestPerGroupSpec):
                references.extend(item.column for item in computation.tie_breakers)
        elif isinstance(
            computation, (LagSpec, LeadSpec, RunningAggregateSpec, MovingAggregateSpec)
        ):
            references.append(computation.target)
            references.extend(computation.partition_by)
            references.extend(item.column for item in computation.order_by)
        elif isinstance(computation, ShareOfTotalSpec):
            references.append(computation.target)
            references.extend(computation.partition_by)
        else:  # pragma: no cover - guarded by the discriminated union
            raise ValueError("Unsupported window computation")
    for reference in references:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", reference)
        if match is None or match.group(1).lower() != source_name:
            raise ValueError(f"Window IR reference must belong to {source_name}: {reference}")
        column = table.get_column(match.group(2))
        if column is None or not column.queryable:
            raise ValueError(f"Window IR column is not queryable: {reference}")
    aliases: set[str] = set()
    physical_names = {reference.split(".", 1)[1].lower() for reference in ir.physical_outputs}
    for computation in ir.computations:
        alias = getattr(computation, "alias", None)
        if alias is None:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", alias):
            raise ValueError(f"Invalid computed output alias: {alias}")
        if alias.lower() in aliases or alias.lower() in physical_names:
            raise ValueError(f"Computed output alias collides with another output: {alias}")
        aliases.add(alias.lower())
    for computation in ir.computations:
        if isinstance(computation, MovingAggregateSpec):
            _validate_frame(computation.frame)


def _validate_frame(frame: RowsFrame) -> None:
    order = {
        "UNBOUNDED_PRECEDING": 0,
        "N_PRECEDING": 1,
        "CURRENT_ROW": 2,
        "N_FOLLOWING": 3,
        "UNBOUNDED_FOLLOWING": 4,
    }
    if order[frame.start.kind] > order[frame.end.kind]:
        raise ValueError("Window frame start must not follow frame end")
