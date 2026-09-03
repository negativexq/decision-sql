import pytest
from pydantic import ValidationError

from app.generation.window_provider_patterns import (
    PATTERN_DTOS,
    LagLeadProviderDTO,
    MovingAggregateProviderDTO,
    RankingProviderDTO,
)


def test_m212r_uses_pattern_specific_dtos_without_raw_sql() -> None:
    assert set(PATTERN_DTOS) == {
        "LATEST_PER_GROUP",
        "TOP_N_PER_GROUP",
        "LAG",
        "LEAD",
        "RUNNING_AGGREGATE",
        "MOVING_AGGREGATE",
        "RANKING",
        "SHARE_OF_TOTAL",
        "MIXED_MULTI_WINDOW",
    }
    assert not {"sql", "where", "from", "join", "expression_sql"} & set(
        LagLeadProviderDTO.model_fields
    )


def test_pattern_specific_dtos_reject_extra_fields_and_bad_parameters() -> None:
    with pytest.raises(ValidationError):
        RankingProviderDTO(
            source="orders",
            outputs=("id",),
            function="RANK",
            order_by=("total_amount",),
            direction="DESC",
            sql="SELECT 1",
        )
    with pytest.raises(ValidationError):
        MovingAggregateProviderDTO(
            source="orders",
            outputs=("id",),
            aggregate="AVG",
            target="total_amount",
            order_by=("ordered_at",),
            direction="ASC",
            preceding=0,
            include_current=True,
        )
