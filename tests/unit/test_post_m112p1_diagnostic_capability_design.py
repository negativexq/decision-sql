from __future__ import annotations

from evaluation import post_m112p1_diagnostic_capability_design as design


def test_phase_a_catalog_and_contracts_are_closed() -> None:
    assert len(design.CANDIDATES) == 9
    assert len(design.OBLIGATIONS) == 9
    assert len(design.PROOF) == 6
    assert len(design.safety_gates()["gates"]) == 10
    assert design.applicability_contract()["primary_coverage_counts_only"] == "DIRECTLY_APPLICABLE"
    assert design.selection_rule()["composite_allowed"] is True


def test_preexposure_manifest_does_not_read_case_records() -> None:
    assert design.preexposure_manifest({"contract": "hash"})["case_level_records_read"] == 0
    assert design.preexposure_manifest({"contract": "hash"})["case_level_exposure_started"] is False


def test_applicability_is_structural_and_does_not_create_failure_labels() -> None:
    row = {
        "case_id": "illustrative-only",
        "p0_tier": "CORE",
        "reference_sql_hash": "r",
        "OFF_candidate_hash": "o",
        "ON_candidate_hash": "n",
        "pair_profile": ["ORDER_SIGNATURE_CHANGE", "PROJECTION_CHANGE"],
        "primary_resolution_blocker": "STATIC_SEMANTIC_PROOF_LIMIT",
    }
    record = design._ledger_row(row, False)
    assert record["applicability"]["D2"] == "DIRECTLY_APPLICABLE"
    assert record["primary_resolution_obligation"] == "STATIC_REWRITE_EQUIVALENCE_PROOF_REQUIRED"
    assert "failure_family" not in record


def test_compound_cases_require_abstention_when_components_interact() -> None:
    controls = design.design_controls()["controls"]
    assert controls["component_interaction"] == "COMPONENT_CAUSALITY_NOT_ISOLATED"
    assert controls["finite_match"] == "NO_COUNTEREXAMPLE_FOUND"
    assert controls["witness"] == "NON_EQUIVALENCE_WITNESSED"


def test_d2_is_bounded_and_does_not_claim_equivalence() -> None:
    validation = design.validation_contract()
    assert validation["validation_threshold"]["decision_grade_false_positives"] == 0
    assert validation["consumed_population_excluded"] is True
    assert design.proof_strength_contract()["finite_fixture_match_is_not_equivalence"] is True


def test_d4_d8_safety_is_explicit() -> None:
    catalog = design.candidate_catalog()
    assert "D4" in catalog["candidates"][3]["id"]
    assert "same-model" not in design.disqualifiers()["hard_disqualifiers"]
    assert design._comparison([], [])["rows"][-2]["candidate"] == "D7"


def test_control_population_is_descriptive_only() -> None:
    rows = design._control_rows()
    assert len(rows) == 7
    assert sum(row["resolution_mechanism"] == "M1_METADATA_ONLY" for row in rows) == 4
    assert sum(row["resolution_mechanism"] == "WINDOW_SPECIFIC_RULE" for row in rows) == 3
    assert all(row["new_family_support"] is False for row in rows)


def test_no_side_effectful_diagnostic_path_is_declared() -> None:
    protocol = design.protocol()
    assert protocol["no_provider_db_retrieval_execution"] is True
    assert protocol["no_new_failure_family_attribution"] is True
