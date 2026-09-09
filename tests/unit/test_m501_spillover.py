from benchmark.m501_spillover import (
    CONTROL_PROMPT_HASH,
    M50_RESULT_TRANSITIONS,
    SCHEDULE_HASH,
    STARTING_HEAD,
    TREATMENT_PROMPT_HASH,
    _pairs,
    _runtime,
)


def test_m501_parent_identity_and_runtime_populations() -> None:
    assert STARTING_HEAD == "b69095d7885fa801782d2729d14ad892246eba95"
    assert len(_pairs()) == 90
    assert len(_runtime("CONTROL")) == 90
    assert len(_runtime("TREATMENT")) == 90


def test_m501_frozen_prompt_and_schedule_contract() -> None:
    assert CONTROL_PROMPT_HASH == "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
    assert TREATMENT_PROMPT_HASH == (
        "65c717e281671c57a443ef197feda7691ae3441a31a10264bdfa4b94ba56336e"
    )
    assert SCHEDULE_HASH == "db7441cfcc1845132c7beffdd8fc06b60fb529332b6f914e12c7e1cf80708205"


def test_m501_frozen_correctness_transition_counts_sum_to_90() -> None:
    assert sum(M50_RESULT_TRANSITIONS.values()) == 90
    assert M50_RESULT_TRANSITIONS["CONTROL_CORRECT_TREATMENT_WRONG"] == 6
    assert M50_RESULT_TRANSITIONS["CONTROL_WRONG_TREATMENT_CORRECT"] == 4
