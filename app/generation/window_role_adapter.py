"""Deterministic adapter from M2.12R.1 role DTOs to the fixed Window IR."""

from app.catalog.models import SchemaCatalog
from app.generation.window_ir import WindowQueryIR
from app.generation.window_minimal_adapter import MinimalWindowProviderAdapter
from app.generation.window_provider_patterns import (
    LagLeadProviderDTO,
    LatestPerGroupProviderDTO,
    MixedOperationProviderDTO,
    MixedWindowProviderDTO,
    MovingAggregateProviderDTO,
    ProviderAggregate,
    ProviderDirection,
    ProviderRankingFunction,
    ProviderTiePolicy,
    RankingProviderDTO,
    RunningAggregateProviderDTO,
    ShareOfTotalProviderDTO,
    TopNPerGroupProviderDTO,
)
from app.generation.window_provider_roles import (
    LagLeadRoleDTO,
    LatestPerGroupRoleDTO,
    MixedRoleOperationDTO,
    MixedWindowRoleDTO,
    MovingAggregateRoleDTO,
    RankingRoleDTO,
    RoleProviderDTO,
    RunningAggregateRoleDTO,
    ShareOfTotalRoleDTO,
    TopNPerGroupRoleDTO,
)


class RoleExplicitWindowProviderAdapter:
    """Map role names only; all identifier and IR validation remains existing code."""

    def __init__(self, catalog: SchemaCatalog) -> None:
        self._delegate = MinimalWindowProviderAdapter(catalog)

    def convert(self, dto: RoleProviderDTO) -> WindowQueryIR:
        if isinstance(dto, LatestPerGroupRoleDTO):
            return self._delegate.convert(
                LatestPerGroupProviderDTO(
                    source=dto.source_table,
                    outputs=dto.passthrough_columns,
                    partition_by=dto.partition_columns,
                    order_by=dto.order_column,
                    direction=ProviderDirection(dto.order_direction.value),
                    tie_breaker=dto.tie_breaker_column,
                    tie_breaker_direction=(
                        ProviderDirection(dto.tie_breaker_direction.value)
                        if dto.tie_breaker_direction
                        else None
                    ),
                )
            )
        if isinstance(dto, TopNPerGroupRoleDTO):
            return self._delegate.convert(
                TopNPerGroupProviderDTO(
                    source=dto.source_table,
                    outputs=dto.passthrough_columns,
                    partition_by=dto.partition_columns,
                    order_by=dto.order_column,
                    direction=ProviderDirection(dto.order_direction.value),
                    tie_breaker=dto.tie_breaker_column,
                    tie_breaker_direction=(
                        ProviderDirection(dto.tie_breaker_direction.value)
                        if dto.tie_breaker_direction
                        else None
                    ),
                    n=dto.top_n,
                    tie_policy=ProviderTiePolicy(dto.tie_policy.value),
                )
            )
        if isinstance(dto, LagLeadRoleDTO):
            return self._delegate.convert(
                LagLeadProviderDTO(
                    source=dto.source_table,
                    outputs=dto.passthrough_columns,
                    function="LAG" if dto.window_direction == "PREVIOUS" else "LEAD",
                    target=dto.input_value_column,
                    partition_by=dto.partition_columns,
                    order_by=(dto.order_column,),
                    direction=ProviderDirection(dto.order_direction.value),
                    offset=dto.offset_rows,
                )
            )
        if isinstance(dto, RunningAggregateRoleDTO):
            return self._delegate.convert(
                RunningAggregateProviderDTO(
                    source=dto.source_table,
                    outputs=dto.passthrough_columns,
                    aggregate=ProviderAggregate(dto.aggregate_function.value),
                    target=dto.input_value_column,
                    partition_by=dto.partition_columns,
                    order_by=(dto.order_column,),
                    direction=ProviderDirection(dto.order_direction.value),
                )
            )
        if isinstance(dto, MovingAggregateRoleDTO):
            return self._delegate.convert(
                MovingAggregateProviderDTO(
                    source=dto.source_table,
                    outputs=dto.passthrough_columns,
                    aggregate=ProviderAggregate(dto.aggregate_function.value),
                    target=dto.input_value_column,
                    partition_by=dto.partition_columns,
                    order_by=(dto.order_column,),
                    direction=ProviderDirection(dto.order_direction.value),
                    preceding=dto.preceding_rows,
                    include_current=dto.include_current_row,
                )
            )
        if isinstance(dto, RankingRoleDTO):
            return self._delegate.convert(
                RankingProviderDTO(
                    source=dto.source_table,
                    outputs=dto.passthrough_columns,
                    function=ProviderRankingFunction(dto.ranking_function.value),
                    partition_by=dto.partition_columns,
                    order_by=(dto.order_column,),
                    direction=ProviderDirection(dto.order_direction.value),
                )
            )
        if isinstance(dto, ShareOfTotalRoleDTO):
            return self._delegate.convert(
                ShareOfTotalProviderDTO(
                    source=dto.source_table,
                    outputs=dto.passthrough_columns,
                    target=dto.input_value_column,
                    partition_by=dto.partition_columns,
                    scale=dto.scale_factor,
                )
            )
        if not isinstance(dto, MixedWindowRoleDTO):
            raise TypeError(f"unsupported role DTO: {type(dto).__name__}")
        operations = tuple(self._mixed_operation(item) for item in dto.operations)
        return self._delegate.convert(
            MixedWindowProviderDTO(
                source=dto.source_table,
                outputs=dto.passthrough_columns,
                operations=operations,
            )
        )

    @staticmethod
    def _mixed_operation(operation: MixedRoleOperationDTO) -> MixedOperationProviderDTO:
        if operation.order_column is None and operation.operation != "SHARE_OF_TOTAL":
            raise ValueError("mixed operation requires order_column")
        return MixedOperationProviderDTO(
            function=operation.operation,
            target=operation.input_value_column,
            partition_by=operation.partition_columns,
            order_by=((operation.order_column,) if operation.order_column else ()),
            direction=(
                ProviderDirection(operation.order_direction.value)
                if operation.order_direction
                else None
            ),
            offset=operation.offset_rows,
            aggregate=(
                ProviderAggregate(operation.aggregate_function.value)
                if operation.aggregate_function
                else None
            ),
            preceding=operation.preceding_rows,
            include_current=operation.include_current_row,
            ranking_function=(
                ProviderRankingFunction(operation.ranking_function.value)
                if operation.ranking_function
                else None
            ),
            scale=operation.scale_factor,
        )
