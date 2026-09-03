import hashlib

import pytest

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.window_ir import MovingAggregateSpec
from app.generation.window_minimal_adapter import MinimalWindowProviderAdapter
from app.generation.window_provider_patterns import (
    LagLeadProviderDTO,
    MovingAggregateProviderDTO,
)


def _adapter() -> MinimalWindowProviderAdapter:
    return MinimalWindowProviderAdapter(build_default_catalog(Base.metadata))


def test_adapter_qualifies_source_local_columns_and_owns_alias() -> None:
    dto = LagLeadProviderDTO(
        source="orders",
        outputs=("id", "customer_id"),
        function="LAG",
        target="total_amount",
        partition_by=("customer_id",),
        order_by=("ordered_at", "id"),
        direction="ASC",
        offset=2,
    )
    ir = _adapter().convert(dto)
    assert ir.source_relation == "orders"
    assert ir.physical_outputs == ("orders.id", "orders.customer_id")
    assert ir.computations[0].alias == "__m212r_lag_0"


def test_adapter_maps_minimal_moving_frame_deterministically() -> None:
    dto = MovingAggregateProviderDTO(
        source="payments",
        outputs=("id", "amount"),
        aggregate="AVG",
        target="amount",
        partition_by=("order_id",),
        order_by=("paid_at", "id"),
        direction="ASC",
        preceding=3,
        include_current=False,
    )
    first = _adapter().convert(dto)
    second = _adapter().convert(dto)
    assert first.model_dump_json() == second.model_dump_json()
    assert (
        hashlib.sha256(first.model_dump_json().encode()).hexdigest()
        == hashlib.sha256(second.model_dump_json().encode()).hexdigest()
    )
    computation = first.computations[0]
    assert isinstance(computation, MovingAggregateSpec)
    assert computation.frame.start.value == 3
    assert computation.frame.end.value == 1


def test_adapter_rejects_cross_table_unknown_and_non_queryable_columns() -> None:
    with pytest.raises(ValueError):
        _adapter().convert(
            LagLeadProviderDTO(
                source="orders",
                outputs=("products.id",),
                function="LAG",
                target="total_amount",
                order_by=("ordered_at",),
                direction="ASC",
                offset=1,
            )
        )
    with pytest.raises(ValueError):
        _adapter().convert(
            LagLeadProviderDTO(
                source="orders",
                outputs=("id",),
                function="LAG",
                target="missing_column",
                order_by=("ordered_at",),
                direction="ASC",
                offset=1,
            )
        )
    with pytest.raises(ValueError):
        _adapter().convert(
            LagLeadProviderDTO(
                source="customers",
                outputs=("external_key",),
                function="LAG",
                target="id",
                order_by=("id",),
                direction="ASC",
                offset=1,
            )
        )
