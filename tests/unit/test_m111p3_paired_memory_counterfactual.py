from evaluation.m111p3_paired_memory_counterfactual import (
    arm_order,
    classify_primary,
    exact_discordant_p,
    load_population,
    statistical_rulebook,
)


def test_primary_population_is_frozen_105_direct_cases() -> None:
    population = load_population()
    assert len(population) == 105
    assert len({row["case_id"] for row in population}) == 105
    assert {row["expected_route"] for row in population} == {"DIRECT"}


def test_arm_order_is_balanced_and_deterministic() -> None:
    population = load_population()
    first = arm_order(population)
    second = arm_order(population)
    assert first == second
    assert first["off_first"] == 53
    assert first["on_first"] == 52


def test_exact_discordant_binomial_controls() -> None:
    assert exact_discordant_p(0, 0) == 1.0
    assert exact_discordant_p(0, 6) == exact_discordant_p(6, 0)
    assert exact_discordant_p(0, 6) == 2 / 64
    assert exact_discordant_p(3, 3) == 1.0


def test_primary_matrix_and_classification_controls() -> None:
    assert classify_primary(105, 0, 0, 0) == "M111P3_MEMORY_NO_OBSERVED_PAIRED_EFFECT"
    assert classify_primary(99, 0, 6, 0) == "M111P3_MEMORY_NET_HELPFUL"
    assert classify_primary(99, 6, 0, 0) == "M111P3_MEMORY_NET_HARMFUL"
    assert classify_primary(100, 2, 2, 1) == "M111P3_MEMORY_EFFECT_MIXED_OR_INCONCLUSIVE"


def test_statistical_rulebook_freezes_primary_only() -> None:
    rulebook = statistical_rulebook()
    assert rulebook["primary_population"] == 105
    assert rulebook["minimum_net_cases"] == 6
    assert rulebook["slices"] == "descriptive only; no secondary p-value decision"
