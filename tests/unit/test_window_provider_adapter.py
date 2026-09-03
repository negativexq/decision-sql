import hashlib

import pytest

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.window_provider_adapter import ProviderWindowIRAdapter
from app.generation.window_provider_dto import ProviderWindowIRDTO


def _adapter() -> ProviderWindowIRAdapter:
    return ProviderWindowIRAdapter(build_default_catalog(Base.metadata))


def _lag() -> ProviderWindowIRDTO:
    return ProviderWindowIRDTO(
        pattern="LAG",
        source_relation="orders",
        physical_outputs=("orders.id", "orders.customer_id"),
        target="orders.total_amount",
        partition_by=("orders.customer_id",),
        order_by=({"column": "orders.ordered_at", "direction": "ASC"},),
        offset=1,
        alias="previous_amount",
    )


def test_adapter_converts_to_strict_internal_ir_deterministically() -> None:
    first = _adapter().convert(_lag())
    second = _adapter().convert(_lag())
    assert first == second
    assert (
        hashlib.sha256(first.model_dump_json().encode()).hexdigest()
        == hashlib.sha256(second.model_dump_json().encode()).hexdigest()
    )
    assert first.computations[0].pattern == "LAG"


def test_adapter_rejects_missing_pattern_fields_and_unknown_catalog_objects() -> None:
    with pytest.raises(ValueError, match="requires target"):
        _adapter().convert(
            ProviderWindowIRDTO(
                pattern="LAG",
                source_relation="orders",
                physical_outputs=("orders.id",),
                order_by=({"column": "orders.id"},),
                alias="previous",
            )
        )
    with pytest.raises(ValueError, match="not queryable"):
        _adapter().convert(
            ProviderWindowIRDTO(
                pattern="RANKING",
                source_relation="customers",
                physical_outputs=("customers.external_key",),
                order_by=({"column": "customers.id"},),
                ranking_function="RANK",
                alias="rank_value",
            )
        )


def test_adapter_receives_no_raw_sql_escape_hatch() -> None:
    assert "expression_sql" not in ProviderWindowIRDTO.model_fields
