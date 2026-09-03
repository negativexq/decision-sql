import pytest
from pydantic import ValidationError

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.window_ir import (
    FrameBound,
    MovingAggregateSpec,
    WindowPattern,
    WindowQueryIR,
    validate_window_ir,
)


def test_window_ir_is_discriminated_and_contains_no_raw_sql() -> None:
    ir = WindowQueryIR(
        source_relation="orders",
        pattern=WindowPattern.MOVING_AGGREGATE,
        physical_outputs=("orders.id",),
        computations=(
            {
                "pattern": "MOVING_AGGREGATE",
                "aggregate": "AVG",
                "target": "orders.total_amount",
                "partition_by": ("orders.customer_id",),
                "order_by": ({"column": "orders.ordered_at", "direction": "ASC"},),
                "frame": {
                    "mode": "ROWS",
                    "start": {"kind": "N_PRECEDING", "value": 2},
                    "end": {"kind": "CURRENT_ROW"},
                },
                "alias": "moving_amount",
            },
        ),
    )
    assert isinstance(ir.computations[0], MovingAggregateSpec)
    assert not {"sql", "where_sql", "join_graph", "expression_sql"} & set(
        WindowQueryIR.model_fields
    )


def test_frame_bounds_require_values_only_for_numeric_bounds() -> None:
    with pytest.raises(ValidationError):
        FrameBound(kind="N_PRECEDING")
    with pytest.raises(ValidationError):
        FrameBound(kind="CURRENT_ROW", value=1)


def test_invalid_frame_order_is_rejected_against_catalog() -> None:
    ir = WindowQueryIR(
        source_relation="orders",
        pattern="MOVING_AGGREGATE",
        physical_outputs=("orders.id",),
        computations=(
            {
                "pattern": "MOVING_AGGREGATE",
                "aggregate": "AVG",
                "target": "orders.total_amount",
                "order_by": ({"column": "orders.id", "direction": "ASC"},),
                "frame": {
                    "mode": "ROWS",
                    "start": {"kind": "N_FOLLOWING", "value": 2},
                    "end": {"kind": "N_PRECEDING", "value": 1},
                },
                "alias": "bad_frame",
            },
        ),
    )
    with pytest.raises(ValueError, match="frame"):
        validate_window_ir(ir, build_default_catalog(Base.metadata))


def test_window_ir_rejects_cross_relation_and_non_queryable_columns() -> None:
    with pytest.raises(ValueError):
        validate_window_ir(
            WindowQueryIR(
                source_relation="orders",
                pattern="RANKING",
                physical_outputs=("orders.id",),
                computations=(
                    {
                        "pattern": "RANKING",
                        "function": "RANK",
                        "order_by": ({"column": "products.id"},),
                        "alias": "rank_value",
                    },
                ),
            ),
            build_default_catalog(Base.metadata),
        )
    with pytest.raises(ValueError):
        validate_window_ir(
            WindowQueryIR(
                source_relation="customers",
                pattern="RANKING",
                physical_outputs=("customers.external_key",),
                computations=(
                    {
                        "pattern": "RANKING",
                        "function": "RANK",
                        "order_by": ({"column": "customers.id"},),
                        "alias": "rank_value",
                    },
                ),
            ),
            build_default_catalog(Base.metadata),
        )
