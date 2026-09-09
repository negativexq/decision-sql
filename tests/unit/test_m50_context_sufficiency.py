from benchmark.m46b_contract import m43_prompt
from benchmark.m47b_runner import EXPECTED_PROMPT_HASH
from benchmark.m50_context_sufficiency import (
    TARGET_IDS,
    _case_order,
    _pairs,
    _prompt_diff,
    _requests,
    _schedule,
    _treatment_prompt,
    _truth_sets,
)
from benchmark.model_contract import sha256_text


def test_m50_populations_are_frozen() -> None:
    populations = _truth_sets(_pairs())
    assert populations["target_case_ids"] == list(TARGET_IDS)
    assert populations["target_count"] == 3
    assert populations["governance_count"] == 30
    assert populations["non_target_answerable_count"] == 57


def test_m50_prompt_contract_has_one_treatment_append() -> None:
    control = m43_prompt()
    treatment = _treatment_prompt()
    assert sha256_text(control) == EXPECTED_PROMPT_HASH
    assert treatment.startswith(control + "\n\n")
    assert _prompt_diff(control, treatment).count("+") > 1


def test_m50_schedule_is_balanced_and_paired() -> None:
    schedule = _schedule(_case_order(_pairs()))
    assert len(schedule) == 180
    assert sum(row["arm"] == "CONTROL" for row in schedule) == 90
    assert sum(row["arm"] == "TREATMENT" for row in schedule) == 90
    assert len({(row["case_id"], row["arm"]) for row in schedule}) == 180


def test_m50_control_and_treatment_share_model_visible_user_text() -> None:
    requests = _requests(_pairs())
    assert all(
        requests["CONTROL"][case_id]["user_text"] == requests["TREATMENT"][case_id]["user_text"]
        for case_id in requests["CONTROL"]
    )
