import json
from collections import Counter
from pathlib import Path

from evaluation.m22_filter_semantics_deep_forensics import (
    FILTER_SUBTYPES,
    _predicate_facts,
    validate_artifact,
)

ARTIFACT = Path("evaluation/fixtures/m22_filter_semantics_deep_forensics.json")


def test_m22_filter_population_is_exactly_accounted_for() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    validate_artifact(artifact)
    assert artifact["population"] == 61
    assert artifact["provider_calls"] == 0
    assert len(artifact["cases"]) == 61
    assert sum(item["count"] for item in artifact["primary_subtypes"].values()) == 61
    assert set(artifact["primary_subtypes"]) == set(FILTER_SUBTYPES)
    assert not any(case["benchmark"] == "defog_advanced" for case in artifact["cases"])


def test_m22_primary_subtype_counts_are_stable() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    counts = Counter(case["primary_subtype"] for case in artifact["cases"])
    assert counts == Counter(
        {
            "UNDETERMINED": 39,
            "WRONG_FILTER_VALUE": 9,
            "BOOLEAN_COMPOSITION": 2,
            "EXTRA_PREDICATE": 2,
            "MISSING_PREDICATE": 2,
            "WRONG_FILTER_COLUMN": 2,
            "NULL_OR_EXISTENCE": 1,
            "HAVING_VS_WHERE": 1,
            "TEMPORAL_FILTER": 1,
            "STRING_MATCHING": 1,
            "MULTI_CAUSAL": 1,
        }
    )


def test_predicate_extraction_is_deterministic_and_preserves_boolean_shape() -> None:
    sql = "SELECT id FROM t WHERE a = 1 AND (b = 2 OR c IS NULL)"
    assert _predicate_facts(sql) == _predicate_facts(sql)
    facts = _predicate_facts(sql)
    assert facts["tree"] == ("AND", "EQ", ("PAREN", ("OR", "EQ", "IS")))
    assert len(facts["atoms"]) == 3


def test_m22_artifact_has_no_production_change_or_counterfactual_execution() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    assert artifact["fresh_sql_generation"] == 0
    assert artifact["production_changes"] == 0
    assert artifact["counterfactual_evidence"]["cases_tested"] == 0
