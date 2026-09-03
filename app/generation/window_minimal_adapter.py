"""Deterministic adapter for the M2.12R pattern-specific provider DTOs."""

from collections.abc import Sequence

from app.catalog.models import SchemaCatalog, TableMetadata
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
    WindowComputation,
    WindowDirection,
    WindowOrder,
    WindowPattern,
    WindowQueryIR,
    validate_window_ir,
)
from app.generation.window_provider_patterns import (
    LagLeadProviderDTO,
    LatestPerGroupProviderDTO,
    MixedOperationProviderDTO,
    MixedWindowProviderDTO,
    MovingAggregateProviderDTO,
    ProviderDirection,
    RankingProviderDTO,
    RunningAggregateProviderDTO,
    ShareOfTotalProviderDTO,
    TopNPerGroupProviderDTO,
)


class MinimalWindowProviderAdapter:
    """Map provider choices to strict IR without semantic inference or repair."""

    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog

    def convert(
        self,
        dto: (
            LatestPerGroupProviderDTO
            | TopNPerGroupProviderDTO
            | LagLeadProviderDTO
            | RunningAggregateProviderDTO
            | MovingAggregateProviderDTO
            | RankingProviderDTO
            | ShareOfTotalProviderDTO
            | MixedWindowProviderDTO
        ),
    ) -> WindowQueryIR:
        table = self._table(dto.source)
        outputs = self._columns(table, dto.outputs)
        computation: WindowComputation
        if isinstance(dto, LatestPerGroupProviderDTO):
            computation = LatestPerGroupSpec(
                partition_by=self._columns(table, dto.partition_by),
                order_by=(
                    WindowOrder(
                        column=self._column(table, dto.order_by),
                        direction=_direction(dto.direction),
                    ),
                ),
                tie_breakers=self._tie_breakers(
                    table, dto.tie_breaker, dto.tie_breaker_direction or dto.direction
                ),
            )
            return self._ir(table, WindowPattern.LATEST_PER_GROUP, outputs, (computation,))
        if isinstance(dto, TopNPerGroupProviderDTO):
            computation = TopNPerGroupSpec(
                partition_by=self._columns(table, dto.partition_by),
                order_by=(
                    WindowOrder(
                        column=self._column(table, dto.order_by),
                        direction=_direction(dto.direction),
                    ),
                ),
                n=dto.n,
                tie_policy=TiePolicy(dto.tie_policy.value),
            )
            if dto.tie_breaker is not None:
                computation = computation.model_copy(
                    update={
                        "order_by": (
                            *computation.order_by,
                            WindowOrder(
                                column=self._column(table, dto.tie_breaker),
                                direction=_direction(dto.tie_breaker_direction or dto.direction),
                            ),
                        )
                    }
                )
            return self._ir(table, WindowPattern.TOP_N_PER_GROUP, outputs, (computation,))
        if isinstance(dto, LagLeadProviderDTO):
            computation = self._lag_lead(table, dto, 0)
            return self._ir(table, WindowPattern(dto.function), outputs, (computation,))
        if isinstance(dto, RunningAggregateProviderDTO):
            computation = RunningAggregateSpec(
                aggregate=WindowAggregate(dto.aggregate.value),
                target=self._column(table, dto.target),
                partition_by=self._columns(table, dto.partition_by),
                order_by=self._orders(table, dto.order_by, dto.direction),
                alias=_alias(WindowPattern.RUNNING_AGGREGATE, 0),
            )
            return self._ir(table, WindowPattern.RUNNING_AGGREGATE, outputs, (computation,))
        if isinstance(dto, MovingAggregateProviderDTO):
            computation = self._moving(table, dto, 0)
            return self._ir(table, WindowPattern.MOVING_AGGREGATE, outputs, (computation,))
        if isinstance(dto, RankingProviderDTO):
            computation = RankingSpec(
                function=dto.function.value,
                partition_by=self._columns(table, dto.partition_by),
                order_by=self._orders(table, dto.order_by, dto.direction),
                alias=_alias(WindowPattern.RANKING, 0),
            )
            return self._ir(table, WindowPattern.RANKING, outputs, (computation,))
        if isinstance(dto, ShareOfTotalProviderDTO):
            computation = ShareOfTotalSpec(
                target=self._column(table, dto.target),
                partition_by=self._columns(table, dto.partition_by),
                scale=dto.scale,
                alias=_alias(WindowPattern.SHARE_OF_TOTAL, 0),
            )
            return self._ir(table, WindowPattern.SHARE_OF_TOTAL, outputs, (computation,))
        computations = tuple(
            self._mixed_operation(table, operation, index)
            for index, operation in enumerate(dto.operations)
        )
        return self._ir(table, WindowPattern.MIXED_MULTI_WINDOW, outputs, computations)

    def _mixed_operation(
        self, table: TableMetadata, operation: MixedOperationProviderDTO, index: int
    ) -> WindowComputation:
        function = operation.function
        computation: WindowComputation
        if function in {"LAG", "LEAD"}:
            if operation.target is None or operation.direction is None or operation.offset is None:
                raise ValueError("mixed LAG/LEAD requires target, direction, and offset")
            dto = LagLeadProviderDTO(
                source=table.name,
                outputs=(table.name + "." + table.columns[0].name,),
                function=function,
                target=operation.target,
                partition_by=operation.partition_by,
                order_by=operation.order_by,
                direction=operation.direction,
                offset=operation.offset,
            )
            computation = self._lag_lead(table, dto, index)
        elif function == "RUNNING_AGGREGATE":
            if (
                operation.target is None
                or operation.aggregate is None
                or operation.direction is None
            ):
                raise ValueError(
                    "mixed running aggregate requires target, aggregate, and direction"
                )
            computation = RunningAggregateSpec(
                aggregate=WindowAggregate(operation.aggregate.value),
                target=self._column(table, operation.target),
                partition_by=self._columns(table, operation.partition_by),
                order_by=self._orders(table, operation.order_by, operation.direction),
                alias=_alias(WindowPattern.RUNNING_AGGREGATE, index),
            )
        elif function == "MOVING_AGGREGATE":
            if (
                operation.target is None
                or operation.aggregate is None
                or operation.direction is None
                or operation.preceding is None
                or operation.include_current is None
            ):
                raise ValueError("mixed moving aggregate has missing semantic fields")
            computation = self._moving(
                table,
                MovingAggregateProviderDTO(
                    source=table.name,
                    outputs=(table.name + "." + table.columns[0].name,),
                    target=operation.target,
                    partition_by=operation.partition_by,
                    order_by=operation.order_by,
                    direction=operation.direction,
                    aggregate=operation.aggregate,
                    preceding=operation.preceding,
                    include_current=operation.include_current,
                ),
                index,
            )
        elif function == "RANKING":
            if operation.ranking_function is None or operation.direction is None:
                raise ValueError("mixed ranking requires function and direction")
            computation = RankingSpec(
                function=operation.ranking_function.value,
                partition_by=self._columns(table, operation.partition_by),
                order_by=self._orders(table, operation.order_by, operation.direction),
                alias=_alias(WindowPattern.RANKING, index),
            )
        else:
            if operation.target is None:
                raise ValueError("mixed share requires target")
            computation = ShareOfTotalSpec(
                target=self._column(table, operation.target),
                partition_by=self._columns(table, operation.partition_by),
                scale=operation.scale,
                alias=_alias(WindowPattern.SHARE_OF_TOTAL, index),
            )
        return computation

    def _lag_lead(
        self, table: TableMetadata, dto: LagLeadProviderDTO, index: int
    ) -> LagSpec | LeadSpec:
        values = {
            "target": self._column(table, dto.target),
            "partition_by": self._columns(table, dto.partition_by),
            "order_by": self._orders(table, dto.order_by, dto.direction),
            "offset": dto.offset,
            "alias": _alias(WindowPattern(dto.function), index),
        }
        return LagSpec(**values) if dto.function == "LAG" else LeadSpec(**values)

    def _moving(
        self, table: TableMetadata, dto: MovingAggregateProviderDTO, index: int
    ) -> MovingAggregateSpec:
        end = (
            {"kind": "CURRENT_ROW"} if dto.include_current else {"kind": "N_PRECEDING", "value": 1}
        )
        return MovingAggregateSpec(
            aggregate=WindowAggregate(dto.aggregate.value),
            target=self._column(table, dto.target),
            partition_by=self._columns(table, dto.partition_by),
            order_by=self._orders(table, dto.order_by, dto.direction),
            frame={
                "mode": "ROWS",
                "start": {"kind": "N_PRECEDING", "value": dto.preceding},
                "end": end,
            },
            alias=_alias(WindowPattern.MOVING_AGGREGATE, index),
        )

    def _ir(
        self,
        table: TableMetadata,
        pattern: WindowPattern,
        outputs: tuple[str, ...],
        computations: Sequence[WindowComputation],
    ) -> WindowQueryIR:
        ir = WindowQueryIR(
            source_relation=table.name,
            pattern=pattern,
            physical_outputs=outputs,
            computations=tuple(computations),
        )
        validate_window_ir(ir, self.catalog)
        return ir

    def _table(self, source: str) -> TableMetadata:
        table = self.catalog.get_table(source)
        if table is None or not table.queryable:
            raise ValueError(f"source relation is not queryable: {source}")
        return table

    @staticmethod
    def _column(table: TableMetadata, reference: str) -> str:
        if "." in reference:
            parts = reference.split(".")
            if len(parts) != 2 or parts[0].lower() != table.name.lower():
                raise ValueError(f"column does not belong to source relation: {reference}")
            name = parts[1]
        else:
            name = reference
        column = table.get_column(name)
        if column is None or not column.queryable:
            raise ValueError(f"source-local column is not queryable: {reference}")
        return f"{table.name}.{column.name}"

    def _columns(self, table: TableMetadata, references: Sequence[str]) -> tuple[str, ...]:
        return tuple(self._column(table, reference) for reference in references)

    def _orders(
        self, table: TableMetadata, references: Sequence[str], direction: ProviderDirection
    ) -> tuple[WindowOrder, ...]:
        if not references:
            raise ValueError("at least one order column is required")
        return tuple(
            WindowOrder(column=self._column(table, reference), direction=_direction(direction))
            for reference in references
        )

    def _tie_breakers(
        self, table: TableMetadata, reference: str | None, direction: ProviderDirection
    ) -> tuple[WindowOrder, ...]:
        if reference is None:
            return ()
        return (
            WindowOrder(column=self._column(table, reference), direction=_direction(direction)),
        )


def _direction(value: ProviderDirection) -> WindowDirection:
    return WindowDirection(value.value)


def _alias(pattern: WindowPattern, index: int) -> str:
    return f"__m212r_{pattern.value.lower()}_{index}"
