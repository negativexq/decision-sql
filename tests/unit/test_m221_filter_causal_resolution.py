import json
from collections import Counter
from pathlib import Path

from evaluation.m22_1_filter_causal_resolution import validate_artifact

ARTIFACT = Path("evaluation/fixtures/m22_1_filter_causal_resolution.json")


def _artifact() -> dict:
    artifact = json.loads(ARTIFACT.read_text())
    validate_artifact(artifact)
    return artifact


def test_m221_reconciles_the_unresolved_and_full_filter_populations() -> None:
    artifact = _artifact()
    cases = artifact["cases"]
    unresolved = [case for case in cases if case["m22_previous_subtype"] == "UNDETERMINED"]

    assert len(unresolved) == 39
    assert all(case["final_causal_class"] for case in unresolved)
    assert all("counterfactuals" in case for case in unresolved)
    assert sum(artifact["causal_resolution"]["39_unresolved"].values()) == 39
    assert sum(artifact["causal_resolution"]["revised_61_cases"].values()) == 61


def test_m221_does_not_use_review_dictionary_absence_as_a_default() -> None:
    artifact = _artifact()
    unresolved = [
        case for case in artifact["cases"] if case["m22_previous_subtype"] == "UNDETERMINED"
    ]

    assert len(unresolved) == artifact["population"]["m22_undetermined"]
    assert all(
        case["evidence_level"] in {"COUNTERFACTUAL_CONFIRMED", "UNRESOLVED"} for case in unresolved
    )
    assert artifact["method"]["manual_default_rule"].startswith("not used")


def test_m221_wrong_value_split_and_provider_budget_are_frozen() -> None:
    artifact = _artifact()

    assert artifact["provider_calls"] == 0
    assert sum(artifact["value_failure_split"]["counts"].values()) == 9
    assert artifact["value_failure_split"]["db_lookup_adds_new_information"] == 2
    assert artifact["value_failure_split"]["db_lookup_duplicates_existing_evidence"] == 7


def test_m221_revised_ledger_has_unique_case_identity_and_stable_counts() -> None:
    artifact = _artifact()
    ledger = artifact["revised_61_case_ledger"]
    identities = [(case["benchmark"], case["case_id"]) for case in ledger]

    assert len(identities) == 61
    assert len(set(identities)) == 61
    assert Counter(case["final_causal_class"] for case in ledger) == Counter(
        artifact["causal_resolution"]["revised_61_cases"]
    )
