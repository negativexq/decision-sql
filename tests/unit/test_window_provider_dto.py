import pytest
from pydantic import ValidationError

from app.generation.window_provider_dto import ProviderWindowIRDTO


def test_provider_dto_is_flat_and_forbids_sql_escape_hatches() -> None:
    dto = ProviderWindowIRDTO(
        pattern="LAG",
        source_relation="orders",
        physical_outputs=("orders.id",),
        target="orders.total_amount",
        order_by=({"column": "orders.ordered_at", "direction": "ASC"},),
        offset=1,
        alias="previous_amount",
    )
    assert dto.pattern.value == "LAG"
    assert not {"sql", "expression_sql", "where_sql", "join", "from_sql"} & set(
        ProviderWindowIRDTO.model_fields
    )
    with pytest.raises(ValidationError):
        ProviderWindowIRDTO.model_validate(
            {
                **dto.model_dump(),
                "sql": "DROP TABLE orders",
            }
        )


def test_provider_dto_rejects_invalid_enum_bounds_and_alias() -> None:
    with pytest.raises(ValidationError):
        ProviderWindowIRDTO(
            pattern="LAG",
            source_relation="orders",
            physical_outputs=("orders.id",),
            target="orders.total_amount",
            order_by=({"column": "orders.id", "direction": "SIDEWAYS"},),
            offset=0,
            alias="bad alias",
        )


def test_provider_dto_accepts_derived_window_fields_without_sql() -> None:
    dto = ProviderWindowIRDTO(
        pattern="MOVING_AGGREGATE",
        source_relation="orders",
        physical_outputs=("orders.id",),
        target="orders.total_amount",
        aggregate="AVG",
        order_by=({"column": "orders.ordered_at", "direction": "ASC"},),
        frame_mode="ROWS",
        frame_start_kind="N_PRECEDING",
        frame_start_value=2,
        frame_end_kind="CURRENT_ROW",
        alias="moving_amount",
    )
    assert dto.frame_start_value == 2
