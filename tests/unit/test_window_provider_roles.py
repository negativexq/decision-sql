import hashlib

import pytest
from pydantic import ValidationError

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.window_provider_roles import (
    ROLE_PATTERN_DTOS,
    LagLeadRoleDTO,
    MovingAggregateRoleDTO,
)
from app.generation.window_role_adapter import RoleExplicitWindowProviderAdapter


def adapter() -> RoleExplicitWindowProviderAdapter:
    return RoleExplicitWindowProviderAdapter(build_default_catalog(Base.metadata))


def test_role_contract_uses_explicit_physical_roles_and_no_alias() -> None:
    fields = set(LagLeadRoleDTO.model_fields)
    assert {"source_table", "passthrough_columns", "input_value_column"} <= fields
    assert not {"source", "outputs", "target", "alias", "sql"} & fields
    assert "input_value_column" in fields


def test_role_adapter_qualifies_only_source_local_columns_and_owns_alias() -> None:
    dto = LagLeadRoleDTO(
        source_table="orders",
        passthrough_columns=["id", "customer_id"],
        input_value_column="total_amount",
        partition_columns=["customer_id"],
        order_column="ordered_at",
        order_direction="ASC",
        window_direction="PREVIOUS",
        offset_rows=1,
    )
    first = adapter().convert(dto)
    second = adapter().convert(dto)
    assert first.computations[0].alias == "__m212r_lag_0"
    assert first.model_dump_json() == second.model_dump_json()
    assert hashlib.sha256(first.model_dump_json().encode()).hexdigest() == hashlib.sha256(
        second.model_dump_json().encode()
    ).hexdigest()


def test_computed_result_name_is_not_a_physical_input_column() -> None:
    with pytest.raises(ValueError):
        adapter().convert(
            LagLeadRoleDTO(
                source_table="orders",
                passthrough_columns=["id"],
                input_value_column="cumulative_total_amount",
                partition_columns=["customer_id"],
                order_column="ordered_at",
                order_direction="ASC",
                window_direction="PREVIOUS",
                offset_rows=1,
            )
        )


def test_role_adapter_does_not_search_other_tables_or_fuzzy_match() -> None:
    with pytest.raises(ValueError):
        adapter().convert(
            LagLeadRoleDTO(
                source_table="refunds",
                passthrough_columns=["id"],
                input_value_column="amount",
                partition_columns=["customer_id"],
                order_column="refunded_at",
                order_direction="ASC",
                window_direction="NEXT",
                offset_rows=1,
            )
        )
    with pytest.raises(ValueError):
        adapter().convert(
            LagLeadRoleDTO(
                source_table="orders",
                passthrough_columns=["id"],
                input_value_column="total_amunt",
                partition_columns=["customer_id"],
                order_column="ordered_at",
                order_direction="ASC",
                window_direction="PREVIOUS",
                offset_rows=1,
            )
        )


def test_role_dtos_forbid_sql_fields_and_invalid_parameters() -> None:
    with pytest.raises(ValidationError):
        LagLeadRoleDTO(
            source_table="orders",
            passthrough_columns=["id"],
            input_value_column="total_amount",
            partition_columns=["customer_id"],
            order_column="ordered_at",
            order_direction="ASC",
            window_direction="PREVIOUS",
            offset_rows=0,
            sql="SELECT 1",
        )
    with pytest.raises(ValidationError):
        MovingAggregateRoleDTO(
            source_table="orders",
            passthrough_columns=["id"],
            input_value_column="total_amount",
            aggregate_function="AVG",
            partition_columns=["customer_id"],
            order_column="ordered_at",
            order_direction="ASC",
            preceding_rows=0,
            include_current_row=True,
        )


def test_all_simple_families_have_pattern_specific_models() -> None:
    assert ROLE_PATTERN_DTOS["LAG"] is ROLE_PATTERN_DTOS["LEAD"]
    assert ROLE_PATTERN_DTOS["LAG"] is not ROLE_PATTERN_DTOS["RANKING"]
    assert ROLE_PATTERN_DTOS["MOVING_AGGREGATE"] is not ROLE_PATTERN_DTOS["RUNNING_AGGREGATE"]
