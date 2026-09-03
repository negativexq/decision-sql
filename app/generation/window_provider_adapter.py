"""Deterministic adapter from M2.12.1 transport DTO to strict WindowQueryIR."""

from typing import Any

from app.catalog.models import SchemaCatalog
from app.generation.window_ir import (
    LagSpec,
    LatestPerGroupSpec,
    LeadSpec,
    MovingAggregateSpec,
    RankingSpec,
    RunningAggregateSpec,
    ShareOfTotalSpec,
    TiePolicy,
    TopNPerGroupSpec,
    WindowAggregate,
    WindowDirection,
    WindowOrder,
    WindowPattern,
    WindowQueryIR,
    validate_window_ir,
)
from app.generation.window_provider_dto import ProviderPattern, ProviderWindowIRDTO


class ProviderWindowIRAdapter:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog

    def convert(self, dto: ProviderWindowIRDTO) -> WindowQueryIR:
        pattern = dto.pattern
        common = {
            "source_relation": dto.source_relation,
            "physical_outputs": dto.physical_outputs,
        }
        orders = tuple(
            WindowOrder(column=item.column, direction=WindowDirection(item.direction.value))
            for item in dto.order_by
        )
        computation: Any
        if pattern is ProviderPattern.LATEST_PER_GROUP:
            computation = LatestPerGroupSpec(
                partition_by=dto.partition_by,
                order_by=orders,
                tie_breakers=tuple(
                    WindowOrder(column=item.column, direction=WindowDirection(item.direction.value))
                    for item in dto.tie_breakers
                ),
            )
        elif pattern is ProviderPattern.TOP_N_PER_GROUP:
            if dto.n is None or dto.tie_policy is None:
                raise ValueError("TOP_N_PER_GROUP requires n and tie_policy")
            computation = TopNPerGroupSpec(
                partition_by=dto.partition_by,
                order_by=orders,
                n=dto.n,
                tie_policy=TiePolicy(dto.tie_policy.value),
            )
        elif pattern in {ProviderPattern.LAG, ProviderPattern.LEAD}:
            if dto.target is None or dto.offset is None or dto.alias is None:
                raise ValueError("LAG/LEAD requires target, offset, and alias")
            values = {
                "target": dto.target,
                "partition_by": dto.partition_by,
                "order_by": orders,
                "offset": dto.offset,
                "alias": dto.alias,
            }
            computation = (
                LagSpec(**values) if pattern is ProviderPattern.LAG else LeadSpec(**values)
            )
        elif pattern in {ProviderPattern.RUNNING_AGGREGATE, ProviderPattern.MOVING_AGGREGATE}:
            if dto.target is None or dto.aggregate is None or dto.alias is None:
                raise ValueError("aggregate window requires target, aggregate, and alias")
            if pattern is ProviderPattern.RUNNING_AGGREGATE:
                computation = RunningAggregateSpec(
                    aggregate=WindowAggregate(dto.aggregate.value),
                    target=dto.target,
                    partition_by=dto.partition_by,
                    order_by=orders,
                    alias=dto.alias,
                )
            else:
                if (
                    dto.frame_mode != "ROWS"
                    or dto.frame_start_kind is None
                    or dto.frame_end_kind is None
                ):
                    raise ValueError("MOVING_AGGREGATE requires a ROWS frame")
                computation = MovingAggregateSpec(
                    aggregate=WindowAggregate(dto.aggregate.value),
                    target=dto.target,
                    partition_by=dto.partition_by,
                    order_by=orders,
                    frame={
                        "mode": "ROWS",
                        "start": {"kind": dto.frame_start_kind, **_value(dto.frame_start_value)},
                        "end": {"kind": dto.frame_end_kind, **_value(dto.frame_end_value)},
                    },
                    alias=dto.alias,
                )
        elif pattern is ProviderPattern.RANKING:
            if dto.ranking_function is None or dto.alias is None:
                raise ValueError("RANKING requires ranking_function and alias")
            computation = RankingSpec(
                function=dto.ranking_function.value,
                partition_by=dto.partition_by,
                order_by=orders,
                alias=dto.alias,
            )
        elif pattern is ProviderPattern.SHARE_OF_TOTAL:
            if dto.target is None or dto.alias is None:
                raise ValueError("SHARE_OF_TOTAL requires target and alias")
            computation = ShareOfTotalSpec(
                target=dto.target,
                partition_by=dto.partition_by,
                scale=dto.scale,
                alias=dto.alias,
            )
        else:  # pragma: no cover - ProviderPattern is exhaustive
            raise ValueError(f"unsupported provider pattern: {pattern}")
        ir = WindowQueryIR(
            **common,
            pattern=WindowPattern(pattern.value),
            computations=(computation,),
        )
        validate_window_ir(ir, self.catalog)
        return ir


def _value(value: int | None) -> dict[str, int]:
    return {"value": value} if value is not None else {}
