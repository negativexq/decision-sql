from collections import Counter

from app.generation.governed_metric_grounding import GovernedMetricGroundingDTO
from app.semantics.catalog import build_m3_catalog
from evaluation.m12r_governed_semantic_generation import (
    SPLIT_SALT,
    _call_order,
    _p_value,
    enumerate_plans,
    normalize_question,
    prompt_hashes,
)


def test_m12r_enumerates_the_frozen_m3_state_space() -> None:
    plans = enumerate_plans(build_m3_catalog())
    assert len(plans) == 109
    assert len({item["plan_hash"] for item in plans}) == 109
    assert Counter(item["semantic_category"] for item in plans) == {
        "SCALAR": 10,
        "ONE_DIMENSION": 31,
        "TWO_DIMENSIONS": 68,
    }


def test_m12r_plan_is_strict_and_has_no_sql_escape_hatch() -> None:
    assert set(GovernedMetricGroundingDTO.model_fields) == {
        "applicable",
        "metric_name",
        "dimensions",
    }
    try:
        GovernedMetricGroundingDTO.model_validate(
            {"applicable": True, "metric_name": "x", "dimensions": [], "sql": "SELECT 1"}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("raw SQL must not be accepted by the governed plan DTO")


def test_m12r_call_order_and_p_value_are_deterministic() -> None:
    assert _call_order("m12r-plan-000") == _call_order("m12r-plan-000")
    assert _p_value(0, 0) == 1.0
    assert _p_value(1, 3) == _p_value(3, 1)
    assert SPLIT_SALT


def test_m12r_prompt_context_hash_is_stable() -> None:
    first = prompt_hashes("same frozen context")
    second = prompt_hashes("same frozen context")
    assert first == second
    assert normalize_question("  Show\nRevenue. ") == "show revenue."
