"""Offline provider/DB/retrieval-free tests for M11.0T."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import cast

from evaluation.m110t_memory_coverage_recall import (
    PRIMARY_STATES,
    PairObservation,
    TargetObservation,
    corpus_concentration,
    coverage_summary,
    dominance,
    entry_coverage,
    purity_summary,
    recall_summary,
    scan_target,
    summarize_states,
)


def _entries() -> tuple[tuple[str, str], ...]:
    return (
        ("m4:a", "SELECT o.status, SUM(o.amount) FROM orders AS o GROUP BY o.status"),
        ("m4:b", "SELECT o.status, AVG(o.amount) FROM orders AS o GROUP BY o.status"),
        ("m4:c", "SELECT id FROM (SELECT id FROM accounts) AS nested"),
    )


def _target(selected: tuple[str, ...]) -> tuple[tuple[PairObservation, ...], TargetObservation]:
    return scan_target(
        "target-1",
        "SELECT x.status, SUM(x.amount) FROM orders AS x GROUP BY x.status",
        _entries(),
        selected,
        partition="DIAG_A",
    )


def test_primary_states_are_closed() -> None:
    assert PRIMARY_STATES == (
        "CORPUS_COVERAGE_FAILURE",
        "RETRIEVAL_RECALL_FAILURE",
        "SELECTIVITY_ADMISSION_FAILURE",
        "COMPATIBLE_CONTEXT_RETRIEVED",
        "UNRESOLVED",
    )


def test_target_decision_priority_and_count_integrity() -> None:
    pairs, coverage_failure = scan_target("t0", "SELECT id FROM users", _entries(), ("m4:b",))
    assert len(pairs) == 3
    assert coverage_failure.state == "CORPUS_COVERAGE_FAILURE"

    _, recall_failure = _target(("m4:b",))
    assert recall_failure.state == "RETRIEVAL_RECALL_FAILURE"

    _, admission_failure = _target(("m4:a", "m4:b"))
    assert admission_failure.state == "SELECTIVITY_ADMISSION_FAILURE"

    _, pure = _target(("m4:a",))
    assert pure.state == "COMPATIBLE_CONTEXT_RETRIEVED"


def test_coverage_recall_and_purity_summaries() -> None:
    _, first = _target(("m4:a",))
    _, second = _target(("m4:b",))
    observations = (first, second)
    coverage = coverage_summary(observations)
    recall = recall_summary(observations)
    purity = purity_summary(observations)
    assert coverage["targets_with_compatible"] == 2
    assert recall["compatible_top_k_hit_targets"] == 1
    assert recall["compatible_top_k_miss_targets"] == 1
    assert purity["all_compatible_contexts"] == 1


def test_entry_coverage_and_deterministic_concentration() -> None:
    pairs_a, first = _target(("m4:a",))
    pairs_b, second = _target(("m4:a",))
    assert pairs_a == pairs_b
    assert first == second
    rows = entry_coverage(pairs_a, 1)
    assert all(
        int(cast(int, row["compatible_target_count"]))
        + int(cast(int, row["incompatible_target_count"]))
        + int(cast(int, row["unknown_target_count"]))
        == 1
        for row in rows
    )
    concentration = corpus_concentration(pairs_a)
    assert concentration["broadest_entry_target_count"] == 1
    assert concentration["top_1_cumulative_coverage"] == 1


def test_dominance_requires_both_partitions_and_minimums() -> None:
    result = dominance(
        {"CORPUS_COVERAGE_FAILURE": {"DIAG_A": 52, "DIAG_B": 53}},
        105,
    )
    coverage_result = cast(dict[str, object], result["CORPUS_COVERAGE_FAILURE"])
    assert coverage_result["dominant"] is True
    assert summarize_states((_target(("m4:b",))[1],)) == {
        state: 1 if state == "RETRIEVAL_RECALL_FAILURE" else 0 for state in PRIMARY_STATES
    }


def test_score_and_outcome_values_are_not_decision_inputs() -> None:
    assert "VerifiedQueryRetriever" not in inspect.getsource(scan_target)
    assert "score" not in inspect.signature(scan_target).parameters
    assert "v1_correct" in inspect.signature(scan_target).parameters
    # Correctness is stored only as a post-label cross-tab field and does not
    # affect the deterministic state decision.
    _, without = scan_target("t", "SELECT id FROM users", _entries(), ("m4:b",), v1_correct=False)
    _, with_correct = scan_target(
        "t", "SELECT id FROM users", _entries(), ("m4:b",), v1_correct=True
    )
    assert without.state == with_correct.state == "CORPUS_COVERAGE_FAILURE"


def test_historical_selected_ids_must_be_from_frozen_entry_set() -> None:
    try:
        scan_target("t", "SELECT id FROM users", _entries(), ("m4:missing",))
    except ValueError as error:
        assert "missing from corpus" in str(error)
    else:
        raise AssertionError("missing historical selected ID was accepted")


def test_frozen_population_and_pair_counts() -> None:
    root = Path(__file__).parents[2] / "evaluation" / "fixtures"
    targets = json.loads((root / "m110t_target_manifest.json").read_text())
    corpus = json.loads((root / "m110t_m4_manifest.json").read_text())
    assert targets["total_cases"] == 160
    assert targets["primary_memory_use_count"] == 105
    assert targets["secondary_no_memory_count"] == 55
    assert corpus["entry_count"] == 50
    assert targets["primary_memory_use_count"] * corpus["entry_count"] == 5250
