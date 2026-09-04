from __future__ import annotations

from evaluation.m95_binding_protocol import run


def test_m95_freezes_historical_population_and_preserves_regressions() -> None:
    result = run()
    assert result["summary"]["historical_cases"] == 150
    assert result["summary"]["development_cases"] == 90
    assert result["summary"]["validation_cases"] == 60
    assert result["summary"]["validation_case_id_hash"] == (
        "103b664450784986d23f2a6b842dd4fc00b9301226db3c3691b72bef06f668b2"
    )
    assert result["summary"]["comparator_source_hash"] == (
        "677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97"
    )
    assert result["m92_regression"]["classification"] == "RESULT_EQUIVALENCE_CONTRACT_ACCEPTED"
    assert result["m93_regression"]["classification"] == (
        "RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED"
    )


def test_m95_preserves_frozen_adversarial_failure_instead_of_tuning() -> None:
    result = run()
    assert result["adversarial"]["total"] == 40
    assert result["adversarial"]["passed"] == 36
    assert {
        row["case_id"] for row in result["adversarial"]["rows"] if not row["passed"]
    } == {
        "A10_COMMUTATIVE_ARITHMETIC",
        "A17_SELF_JOIN_AMBIGUITY",
        "A27_TWO_OUTPUTS_ONE_SLOT",
        "A28_ONE_OUTPUT_TWO_SLOTS",
    }
    assert result["classification"] == "BINDING_PROTOCOL_UNSAFE"
