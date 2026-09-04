from evaluation.m96_evaluator_applicability_audit import (
    FROZEN_V1_HASH,
    FROZEN_V2_HASH,
    STRATEGY_IDS,
    disagreement_audit,
    run,
)


def test_m96_strategy_audit_accepts_fixed_v1_primary_shadow_protocol() -> None:
    result = run()
    assert result["classification"] == "V1_AUTHORITATIVE_V2_SHADOW_JUSTIFIED"
    assert result["frozen_hashes"]["binder_v1_pre"] == FROZEN_V1_HASH
    assert result["frozen_hashes"]["binder_v1_post"] == FROZEN_V1_HASH
    assert result["frozen_hashes"]["binder_v2_pre"] == FROZEN_V2_HASH
    assert result["frozen_hashes"]["binder_v2_post"] == FROZEN_V2_HASH
    assert result["disagreement_total"] == 54
    assert result["metrics"]["m94_clear_v1_false_negatives"] == 40
    assert result["metrics"]["m94_clear_v2_contract_recoveries"] == 14
    assert result["metrics"]["m94_true_semantic_disagreements"] == 0
    assert result["m10_cases_consumed"] == 0


def test_all_five_strategy_definitions_are_frozen() -> None:
    result = run()
    assert tuple(result["strategies"]) == STRATEGY_IDS
    assert result["strategies"]["S2_V1_AUTHORITATIVE_V2_SHADOW"]["primary_eligible"] is True
    assert result["strategies"]["S4_DETERMINISTIC_GENERATED_SIDE_APPLICABILITY"][
        "primary_eligible"
    ] is False


def test_disagreement_audit_is_reconstructible_without_database() -> None:
    rows = disagreement_audit()
    assert len(rows) == 54
    assert {row["classification"] for row in rows} == {
        "CLEAR_V1_FALSE_NEGATIVE",
        "CLEAR_V2_CONTRACT_RECOVERY",
    }
