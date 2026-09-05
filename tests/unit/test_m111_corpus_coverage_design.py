"""Provider/DB-free tests for the M11.1 design audit mechanics."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from evaluation.m111_corpus_coverage_design import (
    build_hypothetical_candidates,
    build_semantic_signatures,
    build_target_pairwise_matrix,
    candidate_coverage_report,
    coverage_sets,
    cross_target_reuse,
    factorization_report,
    greedy_set_cover,
    select_intervention_family,
    validate_self_compatibility,
)


def _targets() -> tuple[dict[str, object], ...]:
    return (
        {
            "case_id": "target-a",
            "reference_sql": (
                "SELECT o.status, SUM(o.amount) AS total FROM orders AS o GROUP BY o.status"
            ),
        },
        {
            "case_id": "target-b",
            "reference_sql": (
                "SELECT x.status, SUM(x.amount) AS total FROM orders AS x GROUP BY x.status"
            ),
        },
        {
            "case_id": "target-c",
            "reference_sql": "SELECT o.status, AVG(o.amount) FROM orders AS o GROUP BY o.status",
        },
    )


def test_protocol_and_starting_checkpoint_are_frozen() -> None:
    root = Path(__file__).parents[2] / "evaluation" / "fixtures"
    protocol = json.loads((root / "m111_protocol.json").read_text())
    checkpoint = json.loads((root / "m111_starting_checkpoint.json").read_text())
    assert protocol["starting_checkpoint"] == "4c5b48982f3c4fb8ed7d034a9560ed6496f3c774"
    assert (
        protocol["compatibility_contract"]["id"] == "decision-sql-memory-compatibility-evidence-v1"
    )
    assert checkpoint["checkpoint_sha"] == protocol["starting_checkpoint"]


def test_hypothetical_adapter_self_gate_and_pair_count() -> None:
    targets = _targets()
    candidates = build_hypothetical_candidates(targets)
    self_result = validate_self_compatibility(targets, candidates)
    assert self_result["valid"] is True
    assert self_result["compatible"] == 3
    pairs = build_target_pairwise_matrix(targets, candidates)
    assert len(pairs) == 9
    assert sum(pair.diagonal for pair in pairs) == 3


def test_adapter_rejects_missing_reference_evidence() -> None:
    try:
        build_hypothetical_candidates(({"case_id": "missing"},))
    except ValueError as error:
        assert "lacks frozen reference SQL" in str(error)
    else:
        raise AssertionError("missing reference evidence was accepted")


def test_unsupported_signature_is_incomplete_not_optimistically_compatible() -> None:
    signatures = build_semantic_signatures(
        ({"case_id": "nested", "reference_sql": "SELECT id FROM (SELECT id FROM users) AS q"},)
    )
    assert signatures[0].complete is False
    assert signatures[0].full_signature_hash is None


def test_cross_target_reuse_excludes_diagonal_and_builds_coverage_sets() -> None:
    targets = _targets()
    candidates = build_hypothetical_candidates(targets)
    pairs = build_target_pairwise_matrix(targets, candidates)
    reuse = cross_target_reuse(pairs, [target["case_id"] for target in targets])
    coverage = coverage_sets(pairs)
    assert reuse["targets_with_off_diagonal_compatible_candidate"] == 2
    assert reuse["self_only_targets"] == 1
    assert coverage["hyp:target-a"]["coverage_count"] == 2
    assert candidate_coverage_report(coverage)["buckets"] == {
        "only_self": 1,
        "two_to_three": 2,
        "four_to_ten": 0,
        "over_ten": 0,
    }


def test_greedy_set_cover_is_deterministic_and_reaches_all_targets() -> None:
    coverage = {
        "hyp:a": {"source_target_id": "a", "coverage_set": ["a", "b"]},
        "hyp:b": {"source_target_id": "b", "coverage_set": ["b", "c"]},
        "hyp:c": {"source_target_id": "c", "coverage_set": ["c"]},
    }
    first = greedy_set_cover(coverage, ("a", "b", "c"))
    second = greedy_set_cover(dict(reversed(tuple(coverage.items()))), ("c", "b", "a"))
    assert first == second
    assert first["fully_covered"] is True
    assert first["representatives_needed_for_rate"]["1.0"] == 2


def test_factorization_distinguishes_reused_and_novel_atoms() -> None:
    signatures = build_semantic_signatures(_targets())
    report = factorization_report(signatures)
    assert report["complete_signature_targets"] == 3
    assert report["distinct_full_signatures"] == 2
    assert report["repeated_full_signatures"] == 1
    assert report["singleton_full_signatures"] == 1
    assert report["compositionally_novel_target_count"] == 0


def test_selection_is_precommitted_and_never_authorizes_implementation() -> None:
    result = select_intervention_family(
        "EXACT_QUERY_REPRESENTATION_REUSE_WEAK",
        {
            "semantic_pressure_classification": "REUSABLE_SEMANTIC_REPRESENTATION_PRESSURE_WEAK",
            "new_semantic_content_pressure": "NEW_SEMANTIC_CONTENT_PRESSURE_STRONG",
        },
        {"target_count": 3},
    )
    assert (
        result["classification"] == "M111_NEW_SEMANTIC_COVERAGE_REQUIRED_REPRESENTATION_UNRESOLVED"
    )
    assert result["implementation_authorized"] is False


def test_design_code_has_no_runtime_retrieval_or_outcome_inputs() -> None:
    source = inspect.getsource(__import__("evaluation.m111_corpus_coverage_design", fromlist=["x"]))
    assert "VerifiedQueryRetriever" not in source
    assert "v1_correct" not in source
    assert "P1" not in source
    assert "retrieval_score" not in source
