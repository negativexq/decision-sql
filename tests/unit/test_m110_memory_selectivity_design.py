"""Provider-free tests for the M11.0 policy boundary and frozen labels."""

import json
from pathlib import Path

import pytest

from evaluation.m110_memory_selectivity_design import (
    FEATURE_COLUMNS,
    build_label_manifest,
    evaluate_policy,
    policy_feature_view,
    threshold_candidates,
)

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_label_sanity_and_control_boundary() -> None:
    path = ROOT / "evaluation" / "fixtures" / "m110_compatibility_labels.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["memory_use_case_count"] == 105
    assert document["counts"] == {
        "primary_selectivity_positive": 61,
        "diag_a_primary_positive": 31,
        "diag_b_primary_positive": 30,
        "overtransfer_secondary": 21,
        "compatible_controls": 0,
        "correct_compatible_controls": 0,
        "compatible_but_wrong_controls": 0,
        "compatibility_unknown": 23,
    }
    assert document["control_status"] == "BLOCKED_NO_DETERMINISTIC_COMPATIBLE_CONTROLS"


def test_policy_feature_view_excludes_labels_and_partition() -> None:
    assert "s1" in FEATURE_COLUMNS
    with pytest.raises(ValueError, match="policy feature leakage"):
        policy_feature_view({"s1": 0.5, "correct": True})
    with pytest.raises(ValueError, match="policy feature leakage"):
        evaluate_policy(
            "P1_TOP1_MIN_SCORE",
            {"s1": 0.5, "partition": "DIAG_A"},
            {"tau": 0.4},
        )


def test_threshold_candidates_use_only_training_values_and_midpoints() -> None:
    assert threshold_candidates([0.1, 0.3, 0.3, 0.9]) == [0.1, 0.2, 0.3, 0.6, 0.9]


def test_policy_boundary_equality_and_missing_score_behavior() -> None:
    features = {
        "selected_count": 3,
        "s1": 0.5,
        "s2": 0.4,
        "s3": 0.2,
        "min_selected_score": 0.2,
        "top1_top2_gap": 0.1,
    }
    assert evaluate_policy("P0_CURRENT", features) is True
    assert evaluate_policy("P1_TOP1_MIN_SCORE", features, {"tau": 0.5}) is True
    assert evaluate_policy("P2_MIN_SELECTED_SCORE", features, {"tau": 0.2}) is True
    assert evaluate_policy("P3_TOP1_PLUS_MARGIN", features, {"tau": 0.5, "delta": 0.1}) is True
    missing = {**features, "s1": None, "min_selected_score": None}
    assert evaluate_policy("P1_TOP1_MIN_SCORE", missing, {"tau": 0.0}) is False
    assert evaluate_policy("P2_MIN_SELECTED_SCORE", missing, {"tau": 0.0}) is False


def test_ranked_score_order_and_derived_gap_are_not_mutated() -> None:
    ranked = {
        "selected_count": 3,
        "s1": 0.7,
        "s2": 0.4,
        "s3": 0.4,
        "min_selected_score": 0.4,
        "top1_top2_gap": 0.3,
    }
    before = dict(ranked)
    assert evaluate_policy("P3_TOP1_PLUS_MARGIN", ranked, {"tau": 0.7, "delta": 0.3}) is True
    assert ranked == before


def test_frozen_runtime_labels_reproduce_expected_primary_counts() -> None:
    document = build_label_manifest()
    assert document["counts"]["primary_selectivity_positive"] == 61
    assert document["counts"]["diag_a_primary_positive"] == 31
    assert document["counts"]["diag_b_primary_positive"] == 30
    assert document["counts"]["overtransfer_secondary"] == 21
