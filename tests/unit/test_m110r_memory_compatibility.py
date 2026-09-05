"""Provider-free acceptance tests for the M11.0R offline contract."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, cast

import pytest

from evaluation.m110r_memory_compatibility import (
    CompatibilityState,
    UnknownReason,
    classify_context,
    classify_entry,
)

FIXTURES = Path(__file__).resolve().parents[2] / "evaluation" / "fixtures"


def _validation_cases() -> list[dict[str, object]]:
    document: Any = json.loads((FIXTURES / "m110r_validation_cases.json").read_text())
    return cast(list[dict[str, object]], document["cases"])


def _expected_labels() -> dict[str, dict[str, object]]:
    document: Any = json.loads((FIXTURES / "m110r_validation_expected.json").read_text())
    rows = cast(list[dict[str, object]], document["labels"])
    return {str(row["fixture_id"]): row for row in rows}


def test_three_state_contract_is_exclusive() -> None:
    assert {state.value for state in CompatibilityState} == {
        "PROVEN_COMPATIBLE",
        "PROVEN_INCOMPATIBLE",
        "UNKNOWN",
    }


def test_positive_requires_positive_alignment() -> None:
    compatible = classify_entry(
        "SELECT SUM(o.amount) FROM orders AS o",
        "SELECT SUM(x.amount) FROM orders AS x",
    )
    unknown = classify_entry("SELECT id FROM accounts", "SELECT id FROM accounts")
    assert compatible.state is CompatibilityState.PROVEN_COMPATIBLE
    assert compatible.positive_dimensions
    assert unknown.state is CompatibilityState.PROVEN_COMPATIBLE


def test_absence_of_conflict_is_not_the_positive_rule() -> None:
    result = classify_entry("SELECT id FROM accounts", "SELECT id FROM accounts")
    assert result.state is CompatibilityState.PROVEN_COMPATIBLE
    assert result.positive_dimensions


def test_entry_and_context_aggregation() -> None:
    target = "SELECT o.status, SUM(o.amount) FROM orders AS o GROUP BY o.status"
    compatible = "SELECT x.status, SUM(x.amount) FROM orders AS x GROUP BY x.status"
    incompatible = "SELECT x.status, AVG(x.amount) FROM orders AS x GROUP BY x.status"
    unknown = "SELECT id FROM (SELECT id FROM accounts) AS nested"
    assert classify_context(target, (compatible,)).state is CompatibilityState.PROVEN_COMPATIBLE
    assert classify_context(target, (compatible, unknown)).state is CompatibilityState.UNKNOWN
    assert (
        classify_context(target, (compatible, incompatible)).state
        is CompatibilityState.PROVEN_INCOMPATIBLE
    )


def test_parse_missing_and_unsupported_are_unknown() -> None:
    assert (
        classify_entry("SELECT id FROM accounts", "SELECT FROM").state is CompatibilityState.UNKNOWN
    )
    assert classify_entry(None, "SELECT id FROM accounts").state is CompatibilityState.UNKNOWN
    result = classify_entry(
        "SELECT id FROM accounts", "SELECT id FROM (SELECT id FROM accounts) AS nested"
    )
    assert result.state is CompatibilityState.UNKNOWN
    assert result.reason == UnknownReason.UNSUPPORTED_EXPRESSION.value


def test_independent_validation_fixture_manifest_and_labels() -> None:
    cases = _validation_cases()
    expected = _expected_labels()
    assert len(cases) == 120
    counts = {
        category: sum(case["category"] == category for case in cases)
        for category in {"EXPECTED_COMPATIBLE", "EXPECTED_INCOMPATIBLE", "EXPECTED_UNKNOWN"}
    }
    assert counts == {
        "EXPECTED_COMPATIBLE": 40,
        "EXPECTED_INCOMPATIBLE": 40,
        "EXPECTED_UNKNOWN": 40,
    }
    results = {
        str(case["fixture_id"]): classify_entry(
            cast(str | None, case.get("target_sql")),
            cast(str | None, case.get("memory_sql")),
        )
        for case in cases
    }
    assert all(
        result.state.value == expected[fixture_id]["expected_state"]
        for fixture_id, result in results.items()
    )
    assert (
        sum(result.state is CompatibilityState.PROVEN_COMPATIBLE for result in results.values())
        == 40
    )
    assert (
        sum(result.state is CompatibilityState.PROVEN_INCOMPATIBLE for result in results.values())
        == 40
    )
    assert sum(result.state is CompatibilityState.UNKNOWN for result in results.values()) == 40


def test_mutation_and_metamorphic_manifests_are_frozen() -> None:
    mutations = json.loads((FIXTURES / "m110r_mutation_controls.json").read_text())
    metamorphic = json.loads((FIXTURES / "m110r_metamorphic_controls.json").read_text())
    assert len(mutations["controls"]) == 8
    assert len(metamorphic["controls"]) == 6
    assert all(
        "PROVEN_COMPATIBLE_TO_PROVEN_INCOMPATIBLE" == control["expected_transition"]
        for control in mutations["controls"]
    )
    assert all(
        control["expected_invariant"] == "PROVEN_COMPATIBLE" for control in metamorphic["controls"]
    )


def test_material_mutation_breaks_compatibility() -> None:
    target = "SELECT o.status, SUM(o.amount) FROM orders AS o GROUP BY o.status"
    baseline = "SELECT x.status, SUM(x.amount) FROM orders AS x GROUP BY x.status"
    mutated = "SELECT x.status, AVG(x.amount) FROM orders AS x GROUP BY x.status"
    assert classify_entry(target, baseline).state is CompatibilityState.PROVEN_COMPATIBLE
    assert classify_entry(target, mutated).state is CompatibilityState.PROVEN_INCOMPATIBLE


def test_evidence_removal_and_harmless_representation_changes() -> None:
    target = "SELECT o.status, SUM(o.amount) FROM orders AS o GROUP BY o.status"
    formatted = "-- comment\nSELECT x.status, SUM(x.amount)\nFROM orders AS x\nGROUP BY x.status"
    assert classify_entry(target, formatted).state is CompatibilityState.PROVEN_COMPATIBLE
    assert classify_entry(target, None).state is CompatibilityState.UNKNOWN


def test_determinism_and_evidence_order() -> None:
    target = "SELECT o.status, SUM(o.amount) FROM orders AS o GROUP BY o.status"
    memory = "SELECT x.status, SUM(x.amount) FROM orders AS x GROUP BY x.status"
    first = classify_entry(target, memory)
    second = classify_entry(target, memory)
    assert first == second
    assert tuple(name for name, _ in first.relations) == (
        "SOURCE_RELATION",
        "JOIN_RELATIONSHIP",
        "RESULT_GRAIN",
        "AGGREGATION",
        "FORMULA",
        "FILTER",
        "TEMPORAL",
        "WINDOW_SEMANTICS",
        "PROJECTION_ROLE",
        "ORDERING",
        "TOP_N",
        "DISTINCT_SET",
    )


def test_policy_and_outcome_inputs_are_not_in_contract_api() -> None:
    forbidden = {"score", "rank", "gap", "correct", "partition", "family", "primary_mechanism"}
    for function in (classify_entry, classify_context):
        assert not forbidden.intersection(inspect.signature(function).parameters)
    with pytest.raises(TypeError):
        classify_entry("SELECT id FROM accounts", "SELECT id FROM accounts", score=0.5)  # type: ignore[call-arg]
