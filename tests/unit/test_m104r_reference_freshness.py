"""M10.4R protocol regression and safety tests."""

# ruff: noqa: E501

from __future__ import annotations

import json

import pytest

from evaluation.m104r_freshness_audit import KNOWN_CASES, ROOT, stable_hash
from evaluation.reference_freshness import (
    ReferenceCanonicalizationError,
    canonical_reference_fingerprint,
    consumed_reference_entries,
    consumed_reference_raw_values,
    post_run_canonical_overlap,
    pre_run_canonical_overlap,
)

FIXTURES = ROOT / "evaluation" / "fixtures"
RESULTS = ROOT / "evaluation" / "results" / "m104r" / "canonical-freshness-v1"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_all_known_m104_misses_are_canonical_overlaps() -> None:
    regressions = _json("m104r_known_overlap_regressions.json")["records"]
    entries = consumed_reference_entries(ROOT)
    assert tuple(item["case_id"] for item in regressions) == KNOWN_CASES
    assert all(item["canonical_equal"] for item in regressions)
    assert all(item["verdict"] == "PRIOR_CANONICAL_REFERENCE_OVERLAP" for item in regressions)
    for item in regressions:
        candidate = next(case for case in _json("m104_corpus.json")["cases"] if case["case_id"] == item["case_id"])
        matches = pre_run_canonical_overlap(candidate["reference_sql"], tuple(entry for entry in entries if entry["source_artifact"] != "evaluation/fixtures/m104_corpus.json"))
        assert item["prior_source_artifact"] in {match["source_artifact"] for match in matches}


def test_legacy_trimmed_gate_missed_all_known_cases() -> None:
    raw_values = consumed_reference_raw_values(ROOT)
    cases = _json("m104_corpus.json")["cases"]
    old_keys = {value.strip().lower() for value in raw_values.values()}
    misses = [case for case in cases if case["case_id"] in KNOWN_CASES and case["reference_sql"].strip().lower() not in old_keys]
    assert len(misses) == 7


def test_positive_controls_are_canonical_duplicates() -> None:
    controls = _json("m104r_positive_controls.json")["controls"]
    assert all(canonical_reference_fingerprint(item["base_sql"]).fingerprint == canonical_reference_fingerprint(item["variant_sql"]).fingerprint for item in controls)


def test_material_and_scope_controls_remain_distinct() -> None:
    controls = _json("m104r_negative_controls.json")
    base = canonical_reference_fingerprint(controls["base_sql"]).fingerprint
    assert all(canonical_reference_fingerprint(item["variant_sql"]).fingerprint != base for item in controls["controls"])


def test_parse_failure_has_no_raw_text_fallback() -> None:
    with pytest.raises(ReferenceCanonicalizationError):
        canonical_reference_fingerprint("SELECT FROM")
    with pytest.raises(ReferenceCanonicalizationError):
        canonical_reference_fingerprint("SELECT 1; SELECT 2")


def test_pre_and_post_run_callers_have_identical_verdicts() -> None:
    sql = "SELECT o.id FROM orders AS o WHERE o.status = 'completed'"
    entry = {"canonical_reference_fingerprint": canonical_reference_fingerprint(sql).fingerprint}
    entries = (entry,)
    assert pre_run_canonical_overlap(sql, entries) == post_run_canonical_overlap(sql, entries)


def test_historical_manifest_contains_all_invalid_exposed_cases() -> None:
    entries = _json("m104r_consumed_reference_manifest.json")["entries"]
    invalid = [entry for entry in entries if entry["source_artifact"] == "evaluation/fixtures/m104_corpus.json"]
    assert len(invalid) == 160
    assert {entry["consumption_role"] for entry in invalid} == {"INVALID_EXPOSED"}


def test_protocol_artifacts_report_zero_protocol_activity() -> None:
    evidence = json.loads((RESULTS / "repair_evidence_manifest.json").read_text(encoding="utf-8"))
    assert evidence["provider_calls"] == 0
    assert evidence["database_calls"] == 0
    assert evidence["new_corpus_cases"] == 0
    assert evidence["m11_selected"] is False


def test_reference_edit_forces_fingerprint_recomputation() -> None:
    first = canonical_reference_fingerprint("SELECT 1")
    second = canonical_reference_fingerprint("SELECT 2")
    assert first.fingerprint != second.fingerprint


def test_consumed_manifest_drift_changes_manifest_identity() -> None:
    manifest = _json("m104r_consumed_reference_manifest.json")
    original = stable_hash(manifest["entries"])
    changed = stable_hash(manifest["entries"] + [{"source_artifact": "synthetic-drift"}])
    assert original != changed
