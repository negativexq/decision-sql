from __future__ import annotations

import inspect

from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.m95r_binding_safety_audit import (
    BOUNDARY_B,
    FROZEN_BINDER_SOURCE_HASH,
    ROOT_CAUSES,
    _load_manifest,
    run,
    simulate_boundary,
)
from evaluation.result_binding_protocol import binder_source_hash, projection_fingerprints
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    verify_frozen_comparator_source,
)


def test_m95r_loads_the_consumed_failure_population() -> None:
    manifest = _load_manifest()
    assert len(manifest["independent_incorrect_projections"]) == 8
    assert len(manifest["adversarial_failures"]) == 4
    assert len(manifest["correct_control_sample"]) == 30


def test_m95r_frozen_sources_remain_unchanged() -> None:
    assert binder_source_hash() == FROZEN_BINDER_SOURCE_HASH
    assert verify_frozen_comparator_source() == FROZEN_V2_COMPARATOR_SOURCE_HASH


def test_m95r_boundary_api_has_no_reference_or_evaluator_inputs() -> None:
    names = set(inspect.signature(simulate_boundary).parameters)
    assert not names & {
        "reference_sql",
        "reference_result",
        "reference_values",
        "evaluator",
        "expected_correctness",
    }


def test_m95r_boundary_b_refuses_self_join_ambiguity() -> None:
    schema_map = build_schema_semantic_map()
    target = projection_fingerprints(
        "SELECT a.name FROM customers a JOIN customers b ON a.id=b.id", schema_map
    )
    assert simulate_boundary(
        "SELECT name AS name FROM customers a JOIN customers b ON a.id=b.id",
        target,
        schema_map,
        BOUNDARY_B,
    ) == (False,)


def test_m95r_run_meets_the_exploratory_boundary_gates() -> None:
    result = run()
    assert result["classification"] == "BINDING_SAFETY_BOUNDARY_IDENTIFIED"
    assert result["m10_binding_protocol_ready"] is False
    assert result["m95"]["independent_incorrect_bindings"] == 8
    assert result["m95"]["adversarial_failures"] == 4
    assert result["root_cause_counts"]["B13_OTHER_OVERBINDING"] == 8
    assert result["boundaries"][BOUNDARY_B]["known_incorrect_bindings_remaining"] == 0
    assert result["boundaries"][BOUNDARY_B]["correct_projections_retained"] == 109
    assert result["boundaries"][BOUNDARY_B]["correct_case_full_evaluability"] == 49
    assert all(result["gates"].values())
    assert set(result["root_cause_counts"]) == set(ROOT_CAUSES)
    assert result["m10_corpus_created"] is False
    assert result["m10_cases_consumed"] == 0
