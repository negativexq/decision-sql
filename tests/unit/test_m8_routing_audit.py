"""Provider-free M8 routing-slice invariants."""

from pathlib import Path

from evaluation.m8_routing_audit import (
    M8_CORPUS_ID,
    ROUTING_MECHANISMS,
    audit_hash,
    build_audit_cases,
    manifest,
    split_hash,
    validate_audit_cases,
)


def test_m8_slice_is_exactly_the_persisted_m7_false_governed_slice() -> None:
    cases = build_audit_cases(Path("evaluation/results/m7"))
    validation = validate_audit_cases(cases)

    assert validation["passed"] is True
    assert validation["case_count"] == 20
    assert validation["dev_count"] == 12
    assert validation["holdout_count"] == 8
    assert validation["harmful_count"] == 13
    assert validation["harmless_count"] == 7
    assert all(case.expected_route == "DIRECT" for case in cases)
    assert all(case.actual_route == "GOVERNED" for case in cases)


def test_m8_hashes_are_deterministic_and_manifest_is_anchored() -> None:
    cases = build_audit_cases(Path("evaluation/results/m7"))
    first = manifest(cases)
    second = manifest(build_audit_cases(Path("evaluation/results/m7")))

    assert first == second
    assert first["corpus_id"] == M8_CORPUS_ID
    assert first["full_hash"] == audit_hash(cases)
    assert first["dev_hash"] == split_hash(cases, "dev")
    assert first["holdout_hash"] == split_hash(cases, "holdout")
    assert first["m7_full_hash"] == (
        "f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043"
    )


def test_m8_mechanism_taxonomy_is_frozen_and_capability_partition_is_valid() -> None:
    cases = build_audit_cases(Path("evaluation/results/m7"))

    assert {case.primary_routing_mechanism for case in cases} <= set(ROUTING_MECHANISMS)
    assert all(
        set(case.unsupported_required_operations) <= set(case.required_operations)
        for case in cases
    )
    assert all(
        not case.unsupported_required_operations if case.coverage == "FULL" else True
        for case in cases
    )
    assert all(
        not case.governed_supported_operations if case.coverage == "NONE" else True
        for case in cases
    )
