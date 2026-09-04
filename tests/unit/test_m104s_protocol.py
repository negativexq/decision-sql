"""Provider- and DB-free M10.4S pre-exposure protocol tests."""

from __future__ import annotations

import json

from evaluation.m104s_corpus import CORPUS_VERSION, FIXTURES, freshness_report
from evaluation.m104s_protocol import load_cases, validate_manifest


def test_m104s_has_frozen_quota_and_partition_shape() -> None:
    result = validate_manifest()
    assert result["cases"] == 160
    assert result["expected_governed"] == 40
    assert result["expected_direct"] == 120
    assert result["partition_counts"] == {"DIAG_A": 80, "DIAG_B": 80}
    assert all(value == 4 for value in result["governed_metric_counts"].values())
    assert all(value == 10 for value in result["direct_family_counts"].values())


def test_m104s_first_freshness_gate_is_zero_against_consumed_evidence() -> None:
    report = freshness_report(tuple(load_cases()))
    assert report["status"] == "PASS"
    assert report["canonical_reference_overlap"] == 0
    assert report["invalid_m104_canonical_reference_overlap"] == 0
    assert report["m4_canonical_reference_overlap"] == 0


def test_m104s_fixture_is_new_and_gold_is_present() -> None:
    cases = load_cases()
    assert all(case["case_id"].startswith("m104s-") for case in cases)
    assert all(case["reference_sql"].strip() for case in cases)
    gold = json.loads((FIXTURES / "m104s_semantic_gold.json").read_text())
    assert gold["corpus_version"] == CORPUS_VERSION
    assert set(gold["cases"]) == {case["case_id"] for case in cases}


def test_m104s_protocol_remains_gold_blind_at_runtime_boundary() -> None:
    source = (FIXTURES.parent.parent / "evaluation" / "run_m104s.py").read_text()
    assert 'question=case["question"]' in source
    assert 'expected_route' not in source.split('request =', 1)[1].split(')', 1)[0]
    assert "semantic_gold" not in source
