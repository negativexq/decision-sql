"""Regression tests for the zero-call M53 audit/refreeze boundary."""

from __future__ import annotations

import json

from benchmark.m53_runner import (
    AUDIT,
    CASES,
    RESPONSE_HASH,
    explicit_output_order,
    model_visible_hash,
    response_corpus_hash,
)


def test_output_order_detection_does_not_confuse_latest_selection() -> None:
    assert explicit_output_order("Return the latest subscription record") is False
    assert explicit_output_order("Return amounts highest first") is True


def test_frozen_response_corpus_is_unchanged() -> None:
    assert response_corpus_hash() == RESPONSE_HASH


def test_model_visible_hash_is_independent_of_evaluator_truth() -> None:
    case_path = sorted(CASES.glob("*.json"))[0]
    case = json.loads(case_path.read_text(encoding="utf-8"))
    before = model_visible_hash(case)
    evaluator_only_copy = dict(case)
    evaluator_only_copy["evaluator_only_truth_marker"] = "not model-visible"
    assert model_visible_hash(case) == before
    assert model_visible_hash(evaluator_only_copy) == before


def test_canonical_repair_ledger_covers_each_expansion_case_once() -> None:
    ledger = json.loads((AUDIT / "m53_expansion_defect_ledger.json").read_text())
    rows = ledger["cases"]
    case_ids = [row["case_id"] for row in rows]
    assert len(rows) == 90
    assert len(set(case_ids)) == 90
    assert ledger["all_cases"] == 90


def test_postrepair_score_status_does_not_impute_invalidated_responses() -> None:
    status = json.loads((AUDIT / "m53_score_status.json").read_text())
    assert status["zero_call_corrected_score_available"] is False
    assert status["official_post_m53_score_status"] == "POST_M53_SCORE_PENDING_FRESH_EVALUATION"
    assert status["invalidated_response_count"] == len(status["response_reuse"]["invalidated"])


def test_postrepair_spec_has_no_hidden_order_requirements() -> None:
    spec = json.loads((AUDIT / "m53_postrepair_spec_compliance.json").read_text())
    assert spec["row_order_required_only_when_requested"] is True
    assert spec["row_order_defects_remaining"] == 0
    assert spec["hidden_tie_semantics_remaining"] == 0
