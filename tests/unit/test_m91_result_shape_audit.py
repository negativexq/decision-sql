"""Offline tests for the M9.1 result-shape contract audit."""

from evaluation.m9_direct_structure_audit import build_reconstruction
from evaluation.m91_result_shape_audit import (
    DETAILS,
    DIAGNOSTICS,
    GRAIN_RELATIONS,
    PRIMARY_CATEGORIES,
    build_audit_records,
    build_audit_slice,
    e2_projection_equivalent,
    final_decision,
    stable_hash,
    validate,
)


def test_m91_slice_has_frozen_31_case_invariants() -> None:
    cases = build_audit_slice()
    validation = validate(cases)
    assert validation["passed"] is True
    assert validation["case_count"] == 31
    assert validation["split_counts"] == {"dev": 24, "holdout": 7}


def test_m91_manifest_hash_is_deterministic() -> None:
    cases = build_audit_slice()
    payload = [{"case_id": case.case_id, "split": case.split} for case in cases]
    assert stable_hash(payload) == stable_hash(payload)
    assert len({case.case_id for case in cases}) == 31


def test_m91_adjudication_covers_every_slice_case() -> None:
    records = build_audit_records(build_audit_slice())
    assert len(records) == 31
    assert {record.primary_category for record in records} <= set(PRIMARY_CATEGORIES)
    assert {record.detailed_mechanism for record in records} <= set(DETAILS)
    assert {record.grain_relation for record in records} <= set(GRAIN_RELATIONS)
    assert {record.diagnostic_class for record in records} <= set(DIAGNOSTICS)
    assert sum(record.evaluator_artifact for record in records) == 10


def test_e2_refuses_grain_changing_extra_grouping_key() -> None:
    assert not e2_projection_equivalent(
        ("region", "rep", "revenue"),
        (("North", "A", 10), ("North", "B", 12)),
        ("region", "revenue"),
        (("North", 22),),
        ("region", "revenue"),
        "SUPERSET_GRAIN",
    )


def test_e2_accepts_non_grain_changing_extra_column_and_preserves_rows() -> None:
    assert e2_projection_equivalent(
        ("region", "revenue", "region_id"),
        (("North", 22, 1), ("South", 10, 2)),
        ("region", "revenue"),
        (("North", 22), ("South", 10)),
        ("region", "revenue"),
        "EXACT_GRAIN",
    )


def test_e2_refuses_missing_required_field_and_deduplication() -> None:
    assert not e2_projection_equivalent(
        ("revenue",),
        ((22,),),
        ("region", "revenue"),
        (("North", 22),),
        ("region", "revenue"),
        "EXACT_GRAIN",
    )
    assert not e2_projection_equivalent(
        ("region", "revenue", "region_id"),
        (("North", 10, 1), ("North", 12, 2)),
        ("region", "revenue"),
        (("North", 22),),
        ("region", "revenue"),
        "EXACT_GRAIN",
    )


def test_m91_final_decision_is_evaluator_hardening() -> None:
    from evaluation.m91_result_shape_audit import evaluate

    decision = final_decision(evaluate(build_audit_slice()))
    assert decision["classification"] == "EVALUATOR_EQUIVALENCE_HARDENING_JUSTIFIED"
    assert decision["next_milestone"] == "M9.2 — Result Equivalence Contract Regression Suite"


def test_m91_reuses_m9_without_changing_120_case_population() -> None:
    assert len(build_reconstruction()) == 120
