from __future__ import annotations

from collections import Counter

from evaluation import post_m112p1_fragmentation_review as review


def test_preexposure_contracts_are_closed() -> None:
    assert len(review.BLOCKERS) == 12
    assert review.protocol()["taxonomy_frozen_before_case_exposure"] is True
    assert review.blocker_taxonomy()["post_exposure_additions"] is False
    assert review.decision_rule()["dominant"]["single_capability_min"] == 24
    assert review.decision_rule()["mixed"]["collective_capability_min"] == 15
    assert review.decision_rule()["fragmented"]["profiles_min"] == 4


def test_design_controls_cover_required_boundaries() -> None:
    controls = review.design_controls()["controls"]
    assert controls["taxonomy_without_detector"] == "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP"
    assert controls["represented_but_not_provable"] == "STATIC_SEMANTIC_PROOF_LIMIT"
    assert controls["core_projection_only"] == "P0_TIER_OR_OUTPUT_MASK_BOUNDARY"
    assert controls["unsupported_node"] == "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE"
    assert controls["profile_not_failure_family"] is True
    assert controls["p1_labels_not_authority"] is True


def test_primary_blocker_controls_are_deterministic() -> None:
    base: dict[str, object] = {"unsupported_constructs": []}
    cases = {
        "CORE projection": (
            {**base, "p0_tier": "CORE", "pair_profile": ["PROJECTION_CHANGE"]},
            "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
        ),
        "FULL projection": (
            {**base, "p0_tier": "FULL", "pair_profile": ["PROJECTION_CHANGE"]},
            "STATIC_SEMANTIC_PROOF_LIMIT",
        ),
        "join": (
            {**base, "p0_tier": "FULL", "pair_profile": ["JOIN_SIGNATURE_CHANGE"]},
            "SCHEMA_RELATIONSHIP_SEMANTICS_LIMIT",
        ),
        "where": (
            {**base, "p0_tier": "FULL", "pair_profile": ["WHERE_SIGNATURE_CHANGE"]},
            "VALUE_DOMAIN_SEMANTICS_LIMIT",
        ),
        "distinct": (
            {**base, "p0_tier": "FULL", "pair_profile": ["DISTINCT_SET_CHANGE"]},
            "DATA_DEPENDENT_SEMANTICS_REQUIRED",
        ),
        "compound": (
            {
                **base,
                "p0_tier": "FULL",
                "pair_profile": [
                    "SOURCE_SET_CHANGE",
                    "JOIN_SIGNATURE_CHANGE",
                    "WHERE_SIGNATURE_CHANGE",
                    "PROJECTION_CHANGE",
                ],
            },
            "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
        ),
    }
    for profile, expected in cases.values():
        assert review._primary_blocker(profile, {})[0] == expected


def test_literal_and_structural_safety_is_preserved() -> None:
    completed = review._feature("SELECT id FROM t WHERE status = 'COMPLETED'")
    lowercase = review._feature("SELECT id FROM t WHERE status = 'completed'")
    offset_zero = review._feature("SELECT id FROM t ORDER BY id OFFSET 0")
    offset_many = review._feature("SELECT id FROM t ORDER BY id OFFSET 100")
    using_id = review._feature("SELECT * FROM a JOIN b USING (id)")
    using_name = review._feature("SELECT * FROM a JOIN b USING (name)")
    assert completed and lowercase and offset_zero and offset_many and using_id and using_name
    assert completed["full"] != lowercase["full"]
    assert offset_zero["offset"] != offset_many["offset"]
    assert using_id["joins"] != using_name["joins"]


def test_structural_profiles_are_descriptive_only() -> None:
    reference = review._feature("SELECT id FROM t WHERE status = 'COMPLETED'")
    candidate = review._feature("SELECT id, sku FROM t WHERE status = 'completed'")
    assert reference and candidate
    profile = review._profile(reference, candidate, "CORE")
    assert "WHERE_SIGNATURE_CHANGE" in profile
    assert "PROJECTION_CHANGE" in profile
    assert review.structural_profile_contract()["profile_is_not_semantic_failure"] is True


def test_decision_rule_does_not_turn_unresolved_coverage_into_fragmentation() -> None:
    blockers = Counter({name: 0 for name in review.BLOCKERS})
    blockers["STATIC_SEMANTIC_PROOF_LIMIT"] = 20
    blockers["OBSERVED_HETEROGENEOUS_DIVERGENCE"] = 20
    blockers["COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY"] = 6
    blockers["RESOLUTION_BLOCKER_UNRESOLVED"] = 0
    profile_summary = {
        "characterized_profiles": 46,
        "distinct_profile_count": 4,
        "largest_profile_support": 8,
        "top_profiles": [{"profile": str(index), "support": 5} for index in range(4)],
    }
    reachability = {
        family: {"implementation_reachability": "PARTIAL_DETERMINISTIC_POSITIVE_RULE"}
        for family in review.FAMILIES
    }
    result = review._decision(blockers, profile_summary, reachability)
    assert result["classification"] == "POSTM112P1_MIXED_FRAGMENTATION_AND_ATTRIBUTION_LIMIT"


def test_reachability_audit_exposes_unimplemented_family_paths() -> None:
    audit = review.implementation_coverage()
    assert audit["static_case_map_count"] == 0
    assert audit["protocol_vs_implementation"] == "PROTOCOL_BROADER_THAN_IMPLEMENTED_ATTRIBUTION"
    assert (
        audit["families"]["NULL_ZERO_INCLUSION_SEMANTICS"]["reachability"]
        == "NO_POSITIVE_ATTRIBUTION_PATH"
    )
    assert audit["families"]["M1_REJECTION"]["reachability"] == "M1_METADATA_ONLY"


def test_review_forbids_side_effectful_evidence_paths() -> None:
    protocol = review.protocol()
    assert protocol["zero_provider_db_retrieval"] is True
    assert protocol["no_implementation"] is True
    assert review.predecessor_integrity()["historical_artifacts_mutated"] is False
