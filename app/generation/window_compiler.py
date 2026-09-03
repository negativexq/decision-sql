"""Deterministic PostgreSQL compiler for the M2.12 semantic window IR."""

import re

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
    WindowComputation,
    WindowOrder,
    WindowPattern,
    WindowQueryIR,
    validate_window_ir,
)


class WindowIRValidationError(ValueError):
    pass


class WindowSqlCompiler:
    """Compile validated semantic decisions into a stable PostgreSQL query."""

    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog

    def compile(self, ir: WindowQueryIR) -> str:
        try:
            validate_window_ir(ir, self.catalog)
        except ValueError as error:
            raise WindowIRValidationError(str(error)) from error
        if ir.pattern in {WindowPattern.LATEST_PER_GROUP, WindowPattern.TOP_N_PER_GROUP}:
            if len(ir.computations) != 1:
                raise WindowIRValidationError("row-filter window patterns cannot be mixed")
            return self._compile_row_filter(ir, ir.computations[0])
        if any(
            isinstance(computation, (LatestPerGroupSpec, TopNPerGroupSpec))
            for computation in ir.computations
        ):
            raise WindowIRValidationError("row-filter window patterns cannot be mixed")
        source = _q(ir.source_relation)
        outputs = [_q(reference.split(".", 1)[1]) for reference in ir.physical_outputs]
        expressions = [*outputs]
        for computation in ir.computations:
            expressions.append(self._compile_computation(computation))
        return f"SELECT {', '.join(expressions)} FROM {source}"

    def _compile_row_filter(self, ir: WindowQueryIR, computation: WindowComputation) -> str:
        source = _q(ir.source_relation)
        outputs = [_q(reference.split(".", 1)[1]) for reference in ir.physical_outputs]
        if isinstance(computation, LatestPerGroupSpec):
            window_order = [*computation.order_by, *computation.tie_breakers]
            predicate = '"__decision_rank_0" = 1'
            function = "ROW_NUMBER()"
            partition = computation.partition_by
        elif isinstance(computation, TopNPerGroupSpec):
            predicate = f'"__decision_rank_0" <= {computation.n}'
            function = "ROW_NUMBER()" if computation.tie_policy is TiePolicy.EXACT_N else "RANK()"
            window_order = list(computation.order_by)
            partition = computation.partition_by
        else:  # pragma: no cover - guarded by the pattern validator
            raise WindowIRValidationError("unsupported row-filter pattern")
        window = self._window_clause(partition, window_order)
        inner = (
            f"SELECT {', '.join(outputs)}, {function} OVER ({window}) "
            'AS "__decision_rank_0" '
            f"FROM {source}"
        )
        return (
            f'SELECT {", ".join(outputs)} FROM ({inner}) AS "__decision_window_0" WHERE {predicate}'
        )

    def _compile_computation(self, computation: WindowComputation) -> str:
        if isinstance(computation, LagSpec):
            function = f"LAG({_q(computation.target.split('.', 1)[1])}, {computation.offset})"
            return self._aliased_window(
                function, computation.partition_by, computation.order_by, computation.alias
            )
        if isinstance(computation, LeadSpec):
            function = f"LEAD({_q(computation.target.split('.', 1)[1])}, {computation.offset})"
            return self._aliased_window(
                function, computation.partition_by, computation.order_by, computation.alias
            )
        if isinstance(computation, RunningAggregateSpec):
            function = f"{computation.aggregate.value}({_q(computation.target.split('.', 1)[1])})"
            clause = self._window_clause(computation.partition_by, computation.order_by)
            clause += " ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
            return f"{function} OVER ({clause}) AS {_q(computation.alias)}"
        if isinstance(computation, MovingAggregateSpec):
            function = f"{computation.aggregate.value}({_q(computation.target.split('.', 1)[1])})"
            clause = self._window_clause(computation.partition_by, computation.order_by)
            clause += f" {self._frame(computation)}"
            return f"{function} OVER ({clause}) AS {_q(computation.alias)}"
        if isinstance(computation, RankingSpec):
            return self._aliased_window(
                f"{computation.function.value}()",
                computation.partition_by,
                computation.order_by,
                computation.alias,
            )
        if isinstance(computation, ShareOfTotalSpec):
            target = _q(computation.target.split(".", 1)[1])
            clause = self._window_clause(
                computation.partition_by,
                (),
            )
            expression = f"{target} / SUM({target}) OVER ({clause})"
            if computation.scale is not None:
                expression = f"{expression} * {_number(computation.scale)}"
            return f"{expression} AS {_q(computation.alias)}"
        raise WindowIRValidationError("unsupported window computation")

    def _aliased_window(
        self,
        function: str,
        partition_by: tuple[str, ...],
        order_by: tuple[WindowOrder, ...],
        alias: str,
    ) -> str:
        return f"{function} OVER ({self._window_clause(partition_by, order_by)}) AS {_q(alias)}"

    @staticmethod
    def _window_clause(
        partition_by: tuple[str, ...] | list[str],
        order_by: tuple[WindowOrder, ...] | list[WindowOrder],
    ) -> str:
        parts: list[str] = []
        if partition_by:
            parts.append(
                "PARTITION BY " + ", ".join(_q(item.split(".", 1)[1]) for item in partition_by)
            )
        if order_by:
            parts.append(
                "ORDER BY "
                + ", ".join(
                    f"{_q(item.column.split('.', 1)[1])} {item.direction.value}"
                    for item in order_by
                )
            )
        return " ".join(parts)

    @staticmethod
    def _frame(computation: MovingAggregateSpec) -> str:
        return (
            f"ROWS BETWEEN {_frame_bound(computation.frame.start)} "
            f"AND {_frame_bound(computation.frame.end)}"
        )


def _frame_bound(bound: object) -> str:
    kind = bound.kind  # type: ignore[attr-defined]
    value = bound.value  # type: ignore[attr-defined]
    if kind == "UNBOUNDED_PRECEDING":
        return "UNBOUNDED PRECEDING"
    if kind == "N_PRECEDING":
        return f"{value} PRECEDING"
    if kind == "CURRENT_ROW":
        return "CURRENT ROW"
    if kind == "N_FOLLOWING":
        return f"{value} FOLLOWING"
    return "UNBOUNDED FOLLOWING"


def _q(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", identifier):
        raise WindowIRValidationError("compiler received an invalid identifier")
    return f'"{identifier}"'


def _number(value: float) -> str:
    rendered = format(value, ".15g")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", rendered):
        raise WindowIRValidationError("compiler received an invalid numeric scale")
    return rendered
