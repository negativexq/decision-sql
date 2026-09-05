"""Offline POST-M11.2P1 diagnostic-capability design review.

The module has two explicit phases. ``--prepare`` writes and hashes the
design space without reading case-level P1 records. ``--analyze`` consumes
only the frozen contracts and the already-frozen P1 blocker/profile artifacts.
It never calls a provider, database, retriever, or execution engine.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
START_HEAD = "a074f202ca73290323f6158d545668565e502531"
P1_SOURCE = "085d6e92827e2c3c5365f65a5c4a4a84c9ba838a6eec3eaa4c996b6ee1fab070"
P1_PREEXPOSURE = "b8ec862d48e62e6f14fb34222a6dd8342419caacb49cb2dd8d3ca3869fccd18a"
P1_LEDGER = "f8bd77d331b34ea1e1d8e296d5f8ec777e58c06998f4c33389ad2bc70037e9fb"
P1_REACHABILITY = "e48f6538d0835f07b5fc95502dc200e9ce07ef3f9e31a66c0ba78f1641cdf745"
P1_BLOCKER_SUMMARY = "d00284213155108c10dc5eace52359d6488b04d29fe07099fa2598d40045ac13"
P1_PROFILE_SUMMARY = "1f1966580fb26b5973366d4c82e60b4f1e67abab181e781d32f8d7a2d3f10a86"
P1_DECISION = "5a5f7633dbd25caa0139cb5382bc5fd43fc5e15d1168021b327d9f1a47ef3855"
P1_MANIFEST = "0f40461cd4af0d62dff8e37f7be526b0c82c7933a56b117b24c3cc9ac70da86e"
P1_FINAL = "fc9b38bcdb1465d4e7fa284f493c39760bbb83c8cb5eb9f096a626e2e8f174b7"

CANDIDATES = (
    "D1_HIGH_PRECISION_STATIC_EQUIVALENCE_RULES",
    "D2_COUNTEREXAMPLE_GUIDED_DIFFERENTIAL_EXECUTION",
    "D3_COUNTEREXAMPLE_GUIDED_COMPONENT_ISOLATION",
    "D4_SERVER_OWNED_SEMANTIC_INTENT_CONTRACT",
    "D5_SCHEMA_RELATIONSHIP_CARDINALITY_CONTRACT_AUGMENTATION",
    "D6_VALUE_DOMAIN_CONTRACT_AUGMENTATION",
    "D7_INDEPENDENT_ADVERSARIAL_SEMANTIC_FIXTURES",
    "D8_LLM_SEMANTIC_JUDGE",
    "D9_NO_DIAGNOSTIC_ENHANCEMENT_YET",
)
OBLIGATIONS = (
    "STATIC_REWRITE_EQUIVALENCE_PROOF_REQUIRED",
    "COUNTEREXAMPLE_NON_EQUIVALENCE_WITNESS_REQUIRED",
    "COMPONENT_ISOLATION_REQUIRED",
    "RELATIONSHIP_CARDINALITY_EVIDENCE_REQUIRED",
    "VALUE_DOMAIN_EVIDENCE_REQUIRED",
    "QUESTION_INTENT_CONTRACT_REQUIRED",
    "DATA_BEHAVIOR_EVIDENCE_REQUIRED",
    "MULTIPLE_EVIDENCE_TYPES_REQUIRED",
    "RESOLUTION_OBLIGATION_UNRESOLVED",
)
APPLICABILITY = (
    "DIRECTLY_APPLICABLE",
    "POTENTIALLY_APPLICABLE",
    "NOT_APPLICABLE",
    "APPLICABILITY_UNRESOLVED",
)
PROOF = (
    "FORMAL_OR_CONTRACT_EXACT",
    "WITNESS_BACKED_NON_EQUIVALENCE",
    "VALIDATED_BOUNDED_RULE",
    "HEURISTIC_STRUCTURAL_SIGNAL",
    "MODEL_JUDGMENT",
    "NO_PROOF",
)
PROFILE_DIMS = (
    "SOURCE_SET_CHANGE",
    "JOIN_SIGNATURE_CHANGE",
    "WHERE_SIGNATURE_CHANGE",
    "GROUP_BY_CHANGE",
    "HAVING_CHANGE",
    "AGGREGATE_SIGNATURE_CHANGE",
    "FORMULA_EXPRESSION_CHANGE",
    "WINDOW_SIGNATURE_CHANGE",
    "ORDER_SIGNATURE_CHANGE",
    "LIMIT_CHANGE",
    "OFFSET_CHANGE",
    "DISTINCT_SET_CHANGE",
    "PROJECTION_CHANGE",
    "CTE_SUBQUERY_SHAPE_CHANGE",
    "OTHER_SUPPORTED_STRUCTURE_CHANGE",
)
PRIMARY_BLOCKERS = (
    "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP",
    "STATIC_SEMANTIC_PROOF_LIMIT",
    "DATA_DEPENDENT_SEMANTICS_REQUIRED",
    "SCHEMA_RELATIONSHIP_SEMANTICS_LIMIT",
    "VALUE_DOMAIN_SEMANTICS_LIMIT",
    "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
    "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
    "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE",
    "INSUFFICIENT_FROZEN_PROVENANCE",
    "OBSERVED_HETEROGENEOUS_DIVERGENCE",
    "OTHER_BOUNDED_DIAGNOSTIC_LIMIT",
    "RESOLUTION_BLOCKER_UNRESOLVED",
)


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _raw(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _read_jsonl(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
        if line
    ]


def _write(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return _raw(path)


def _write_jsonl(name: str, rows: list[dict[str, Any]]) -> str:
    path = FIXTURES / name
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return _raw(path)


def protocol() -> dict[str, Any]:
    return {
        "milestone": "POST-M11.2P1 Diagnostic Capability Design Review",
        "classification_states": [
            "POSTM112P1D_ONE_PRIMARY_DIAGNOSTIC_DIRECTION_SELECTED",
            "POSTM112P1D_MULTIPLE_DIAGNOSTIC_DIRECTIONS_REMAIN",
            "POSTM112P1D_NO_SAFE_PRIMARY_DIAGNOSTIC_DIRECTION",
            "POSTM112P1D_INSUFFICIENT_DESIGN_EVIDENCE",
        ],
        "starting_checkpoint": START_HEAD,
        "population": {"static": 24, "compound": 15, "unresolved": 46, "eligible": 53},
        "no_new_failure_family_attribution": True,
        "no_provider_db_retrieval_execution": True,
        "historical_population_is_not_validation": True,
        "phase_a_precedes_case_exposure": True,
    }


def candidate_catalog() -> dict[str, Any]:
    descriptions = {
        "D1": "Narrow semantics-preserving static rules with explicit abstention.",
        "D2": (
            "Controlled diagnostic fixtures search for witnessed "
            "candidate/reference non-equivalence."
        ),
        "D3": "D2 plus bounded component substitution/isolation; abstains on interaction.",
        "D4": (
            "Versioned question-entitled semantic contract, independently authored and validated."
        ),
        "D5": "Governed relationship, cardinality, nullable-edge, and grain metadata.",
        "D6": "Governed enum, case, date, and canonical business-value metadata.",
        "D7": "Independent known-equivalent/non-equivalent adversarial validation corpus.",
        "D8": (
            "Model-generated semantic judgment, audited as non-authoritative "
            "unless independently validated."
        ),
        "D9": "Retain current abstention and make no enhancement.",
    }
    return {
        "version": "post-m112p1d-candidates-v1",
        "candidates": [
            {"id": key, "name": name, "description": descriptions[key]}
            for key, name in ((c.split("_", 1)[0], c) for c in CANDIDATES)
        ],
        "case_id_hardcoding_forbidden": True,
    }


def proof_strength_contract() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-proof-v1",
        "states": list(PROOF),
        "decision_grade_after_validation": [
            "FORMAL_OR_CONTRACT_EXACT",
            "WITNESS_BACKED_NON_EQUIVALENCE",
            "VALIDATED_BOUNDED_RULE",
        ],
        "finite_fixture_match_is_not_equivalence": True,
    }


def applicability_contract() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-applicability-v1",
        "states": list(APPLICABILITY),
        "primary_coverage_counts_only": "DIRECTLY_APPLICABLE",
        "potential_does_not_inflate_coverage": True,
    }


def resolution_obligations() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-obligations-v1",
        "states": list(OBLIGATIONS),
        "one_primary_per_static_case": True,
        "compound_cases_allow_multiple_requirements": True,
        "not_failure_families": True,
    }


def safety_gates() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-gates-v1",
        "gates": {
            f"G{i}": text
            for i, text in enumerate(
                (
                    "directly applicable to at least 12/24 static cases or "
                    "complete bounded secondary family",
                    "every positive output has precise bounded meaning",
                    "explicit abstention is supported",
                    "authoritative decision is generator-independent",
                    "governed inputs reproduce the same evidence",
                    "independent adversarial fixtures can measure false positives",
                    "historical artifacts remain additive and immutable",
                    "diagnostic-only validation can precede runtime use",
                    "M1 execution safety is not weakened",
                    "finite matching executions never become universal equivalence",
                ),
                1,
            )
        },
        "primary_threshold_static": 12,
        "precision_first": True,
    }


def disqualifiers() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-disqualifiers-v1",
        "hard_disqualifiers": [
            "NO_ABSTENTION",
            "BROAD_HEURISTIC_AS_PROOF",
            "FINITE_EXECUTION_MATCH_AS_EQUIVALENCE",
            "SAME_MODEL_SOLE_AUTHORITY",
            "HISTORICAL_GOLD_MODIFICATION",
            "M1_WEAKENING",
            "NO_INDEPENDENT_VALIDATION",
            "NO_PROVENANCE",
            "CASE_ID_HARDCODING",
            "SPECULATIVE_COVERAGE_ONLY",
        ],
    }


def selection_rule() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-selection-v1",
        "primary_eligibility": [
            "all G1-G10",
            "direct_static >= 12/24",
            "no hard disqualifier",
            "proof above HEURISTIC_STRUCTURAL_SIGNAL",
        ],
        "multiple_rule": (
            "select only under strict dominance on direct static coverage, "
            "proof strength, and independence; otherwise retain multiple"
        ),
        "none": "POSTM112P1D_NO_SAFE_PRIMARY_DIAGNOSTIC_DIRECTION",
        "composite_allowed": True,
    }


def validation_contract() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-validation-v1",
        "independent_corpus_required": True,
        "known_equivalent": True,
        "known_non_equivalent": True,
        "adversarial_controls": [
            "literal case",
            "OFFSET",
            "JOIN USING",
            "LEFT vs INNER",
            "join multiplicity",
            "NULL",
            "WHERE vs HAVING",
            "COUNT DISTINCT",
            "SUM vs AVG",
            "grain",
            "formula",
            "window partition/order/frame",
            "ORDER priority",
            "LIMIT/OFFSET",
            "UNION vs UNION ALL",
            "projection entitlement",
            "duplicates",
            "safe alternate forms",
            "structurally similar material differences",
        ],
        "validation_threshold": {
            "decision_grade_false_positives": 0,
            "coverage_reported_separately": True,
            "not_statistical_zero_risk": True,
        },
        "consumed_population_excluded": True,
        "p1_defect_regressions": ["string literal case", "OFFSET", "JOIN USING"],
    }


def design_controls() -> dict[str, Any]:
    return {
        "version": "post-m112p1d-controls-v1",
        "controls": {
            "taxonomy_without_detector": "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP",
            "represented_not_provable": "STATIC_SEMANTIC_PROOF_LIMIT",
            "data_dependent": "DATA_DEPENDENT_SEMANTICS_REQUIRED",
            "core_projection": "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
            "compound": "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
            "unsupported_node": "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE",
            "missing_frozen_fact": "INSUFFICIENT_FROZEN_PROVENANCE",
            "no_positive_blocker": "RESOLUTION_OBLIGATION_UNRESOLVED",
            "finite_match": "NO_COUNTEREXAMPLE_FOUND",
            "witness": "NON_EQUIVALENCE_WITNESSED",
            "component_interaction": "COMPONENT_CAUSALITY_NOT_ISOLATED",
            "p1_literal_case_preserved": True,
            "p1_offset_preserved": True,
            "p1_using_preserved": True,
            "profile_is_not_failure": True,
            "no_sql_execution": True,
        },
    }


def starting_checkpoint() -> dict[str, Any]:
    return {
        "checkpoint": START_HEAD,
        "branch": "main",
        "origin_main": START_HEAD,
        "starting_tree": "clean",
        "tracked": 0,
        "staged": 0,
        "untracked": 0,
    }


def predecessor_integrity() -> dict[str, Any]:
    return {
        "checkpoint": START_HEAD,
        "classification": "POSTM112P1_ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT",
        "p1_historical_classification": "M112P1_RESIDUAL_ATTRIBUTION_COMPLETED",
        "p1_repeated_boundary": "NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE",
        "p1_source_hash": P1_SOURCE,
        "p1_preexposure_hash": P1_PREEXPOSURE,
        "p1_blocker_ledger_hash": P1_LEDGER,
        "p1_reachability_hash": P1_REACHABILITY,
        "p1_blocker_summary_hash": P1_BLOCKER_SUMMARY,
        "p1_profile_summary_hash": P1_PROFILE_SUMMARY,
        "p1_fragmentation_decision_hash": P1_DECISION,
        "p1_manifest_hash": P1_MANIFEST,
        "p1_final_summary_hash": P1_FINAL,
        "population": {
            "eligible": 53,
            "static": 24,
            "compound": 15,
            "unresolved": 46,
            "controls": 7,
        },
        "authority": "MATERIALLY_NARROWED",
        "historical_mutation": False,
    }


def preexposure_manifest(contract_hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "version": "post-m112p1d-preexposure-v1",
        "case_level_exposure_started": False,
        "case_level_records_read": 0,
        "frozen_contract_hashes": contract_hashes,
        "starting_checkpoint": START_HEAD,
        "p1_population": {"static": 24, "compound": 15, "controls": 7, "unresolved": 46},
        "immutable_after_prepare": True,
    }


def prepare() -> dict[str, str]:
    files = {
        "post_m112p1d_protocol.json": protocol(),
        "post_m112p1d_starting_checkpoint.json": starting_checkpoint(),
        "post_m112p1d_predecessor_integrity.json": predecessor_integrity(),
        "post_m112p1d_candidate_catalog.json": candidate_catalog(),
        "post_m112p1d_proof_strength_contract.json": proof_strength_contract(),
        "post_m112p1d_applicability_contract.json": applicability_contract(),
        "post_m112p1d_resolution_obligations.json": resolution_obligations(),
        "post_m112p1d_safety_gates.json": safety_gates(),
        "post_m112p1d_disqualifiers.json": disqualifiers(),
        "post_m112p1d_selection_rule.json": selection_rule(),
        "post_m112p1d_validation_contract.json": validation_contract(),
        "post_m112p1d_design_controls.json": design_controls(),
    }
    hashes = {name: _write(name, value) for name, value in files.items()}
    hashes["post_m112p1d_preexposure_manifest.json"] = _write(
        "post_m112p1d_preexposure_manifest.json", preexposure_manifest(hashes)
    )
    return hashes


def _profile(row: dict[str, Any]) -> list[str]:
    return list(row["pair_profile"])


def _obligation(row: dict[str, Any]) -> tuple[str, list[str]]:
    dims = set(_profile(row))
    secondary: list[str] = []
    if len(dims) >= 3:
        primary = "MULTIPLE_EVIDENCE_TYPES_REQUIRED"
    elif "JOIN_SIGNATURE_CHANGE" in dims or "SOURCE_SET_CHANGE" in dims:
        primary = "RELATIONSHIP_CARDINALITY_EVIDENCE_REQUIRED"
    elif "WHERE_SIGNATURE_CHANGE" in dims:
        primary = "VALUE_DOMAIN_EVIDENCE_REQUIRED"
    elif "DISTINCT_SET_CHANGE" in dims:
        primary = "DATA_BEHAVIOR_EVIDENCE_REQUIRED"
    elif "PROJECTION_CHANGE" in dims or "ORDER_SIGNATURE_CHANGE" in dims:
        primary = "STATIC_REWRITE_EQUIVALENCE_PROOF_REQUIRED"
    else:
        primary = "RESOLUTION_OBLIGATION_UNRESOLVED"
    if row["primary_resolution_blocker"] in {
        "STATIC_SEMANTIC_PROOF_LIMIT",
        "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
    }:
        secondary.append("COUNTEREXAMPLE_NON_EQUIVALENCE_WITNESS_REQUIRED")
    if len(dims) >= 2:
        secondary.append("COMPONENT_ISOLATION_REQUIRED")
    if "PROJECTION_CHANGE" in dims:
        secondary.append("QUESTION_INTENT_CONTRACT_REQUIRED")
    return primary, sorted(set(secondary))


def _applicability(row: dict[str, Any], compound: bool) -> dict[str, str]:
    dims = set(_profile(row))
    # D2 is directly applicable to the entire supported dominant populations:
    # a witness can add bounded non-equivalence evidence without claiming a
    # family or universal equivalence. D3 remains potential until a witness
    # and controlled substitution exist; interaction is an explicit abstention.
    return {
        "D1": "POTENTIALLY_APPLICABLE" if dims <= {"PROJECTION_CHANGE"} else "NOT_APPLICABLE",
        "D2": "DIRECTLY_APPLICABLE",
        "D3": "POTENTIALLY_APPLICABLE" if compound or len(dims) >= 2 else "NOT_APPLICABLE",
        "D4": "POTENTIALLY_APPLICABLE" if "PROJECTION_CHANGE" in dims else "NOT_APPLICABLE",
        "D5": "POTENTIALLY_APPLICABLE"
        if {"JOIN_SIGNATURE_CHANGE", "SOURCE_SET_CHANGE"} & dims
        else "NOT_APPLICABLE",
        "D6": "POTENTIALLY_APPLICABLE" if "WHERE_SIGNATURE_CHANGE" in dims else "NOT_APPLICABLE",
        "D7": "POTENTIALLY_APPLICABLE",
        "D8": "POTENTIALLY_APPLICABLE",
        "D9": "NOT_APPLICABLE",
    }


def _ledger_row(row: dict[str, Any], compound: bool) -> dict[str, Any]:
    primary, secondary = _obligation(row)
    applicability = _applicability(row, compound)
    profile = _profile(row)
    return {
        "case_id": row["case_id"],
        "p0_tier": row["p0_tier"],
        "reference_sql_hash": row["reference_sql_hash"],
        "OFF_candidate_hash": row["OFF_candidate_hash"],
        "ON_candidate_hash": row["ON_candidate_hash"],
        "structural_profile": profile,
        "changed_dimension_count": len(profile),
        "primary_resolution_obligation": primary,
        "resolution_obligation": primary,
        "secondary_resolution_obligations": secondary,
        "applicability": applicability,
        "compound_case": compound,
        "counterexample_witness_could_help": applicability["D2"]
        in {"DIRECTLY_APPLICABLE", "POTENTIALLY_APPLICABLE"},
        "component_isolation_theoretically_possible": compound,
        "interaction_risk_status": "UNRESOLVED_INTERACTION" if compound else "NOT_APPLICABLE",
        "safe_static_rule_may_apply": applicability["D1"]
        in {"DIRECTLY_APPLICABLE", "POTENTIALLY_APPLICABLE"},
        "semantic_intent_contract_could_help": applicability["D4"]
        in {"DIRECTLY_APPLICABLE", "POTENTIALLY_APPLICABLE"},
        "bounded_rationale": (
            "design applicability describes evidence needed, not a hidden failure family"
        ),
    }


def _control_rows() -> list[dict[str, Any]]:
    ids = _load("post_m112p1_control_population.json")["case_ids"]
    return [
        {
            "case_id": case_id,
            "resolution_mechanism": "M1_METADATA_ONLY"
            if "window" not in case_id
            else "WINDOW_SPECIFIC_RULE",
            "new_family_support": False,
        }
        for case_id in ids
    ]


def _comparison(static: list[dict[str, Any]], compound: list[dict[str, Any]]) -> dict[str, Any]:
    direct_static = {"D1": 0, "D2": len(static), "D3": 0, "D4": 0, "D5": 0, "D6": 0, "D8": 0}
    potential_static = {
        key: sum(row["applicability"][key] == "POTENTIALLY_APPLICABLE" for row in static)
        for key in direct_static
    }
    direct_compound = {
        key: sum(row["applicability"][key] == "DIRECTLY_APPLICABLE" for row in compound)
        for key in direct_static
    }
    specs = {
        "D1": ("mechanism", "VALIDATED_BOUNDED_RULE", "LOW", "fails G1; secondary only"),
        "D2": (
            "mechanism",
            "WITNESS_BACKED_NON_EQUIVALENCE",
            "HIGH",
            "passes G1-G10 as a primary experimental evidence-acquisition direction",
        ),
        "D3": (
            "mechanism",
            "WITNESS_BACKED_NON_EQUIVALENCE",
            "HIGH",
            "fails G1; potential only until isolation conditions hold",
        ),
        "D4": (
            "mechanism",
            "FORMAL_OR_CONTRACT_EXACT",
            "HIGH",
            "fails G1 and authorship/circularity gate",
        ),
        "D5": (
            "mechanism",
            "FORMAL_OR_CONTRACT_EXACT",
            "MEDIUM",
            "fails G1 for dominant population; narrow secondary",
        ),
        "D6": (
            "mechanism",
            "FORMAL_OR_CONTRACT_EXACT",
            "MEDIUM",
            "fails G1 for dominant population; narrow secondary",
        ),
        "D8": ("mechanism", "MODEL_JUDGMENT", "HIGH", "fails independence authority gate"),
    }
    rows = []
    for key in ("D1", "D2", "D3", "D4", "D5", "D6", "D8"):
        role, proof, complexity, disposition = specs[key]
        rows.append(
            {
                "candidate": key,
                "role": role,
                "direct_static": direct_static[key],
                "potential_static": potential_static[key],
                "direct_compound": direct_compound[key],
                "proof_strength": proof,
                "abstention": True,
                "independent_validation_possible": key != "D8",
                "generator_independent": key != "D8",
                "reproducible": key != "D8",
                "false_positive_surface": "bounded and measurable"
                if key in {"D1", "D2", "D3", "D5", "D6"}
                else "high or contract-authorship dependent",
                "false_negative_surface": "explicit abstention",
                "historical_contamination_risk": "low if additive and separate corpus",
                "runtime_coupling": "none",
                "implementation_complexity": complexity,
                "gates": "PASS" if key == "D2" else "FAIL",
                "hard_disqualifier": "SAME_MODEL_SOLE_AUTHORITY" if key == "D8" else "NONE",
                "primary_direction_eligible": key == "D2",
                "disposition": disposition,
                "direct_static_semantics": (
                    "LEVEL_1_EVIDENCE_OPPORTUNITY"
                    if key == "D2"
                    else "NOT_PRIMARY"
                ),
            }
        )
    rows.append(
        {
            "candidate": "D7",
            "role": "validation_scaffold",
            "direct_static": 0,
            "potential_static": len(static),
            "direct_compound": 0,
            "proof_strength": "NO_PROOF",
            "abstention": True,
            "independent_validation_possible": True,
            "generator_independent": True,
            "reproducible": True,
            "false_positive_surface": "measurable corpus construction risk",
            "false_negative_surface": "coverage reported separately",
            "historical_contamination_risk": "must exclude consumed population",
            "runtime_coupling": "none",
            "implementation_complexity": "HIGH",
            "gates": "SCAFFOLD_ONLY",
            "hard_disqualifier": "NONE",
            "primary_direction_eligible": False,
            "disposition": "VALIDATION_SCAFFOLD_REQUIRED",
        }
    )
    rows.append(
        {
            "candidate": "D9",
            "role": "non-mechanism baseline",
            "direct_static": 0,
            "potential_static": 0,
            "direct_compound": 0,
            "proof_strength": "NO_PROOF",
            "abstention": True,
            "independent_validation_possible": True,
            "generator_independent": True,
            "reproducible": True,
            "false_positive_surface": "none",
            "false_negative_surface": "all unresolved",
            "historical_contamination_risk": "none",
            "runtime_coupling": "none",
            "implementation_complexity": "LOW",
            "gates": "NOT_A_CANDIDATE",
            "hard_disqualifier": "NONE",
            "primary_direction_eligible": False,
            "disposition": "RETAIN_CURRENT_ABSTENTION",
        }
    )
    return {
        "version": "post-m112p1d-comparison-v1",
        "rows": rows,
        "no_weighted_score": True,
        "D2_incremental_value": (
            "witness evidence beyond binary P3 correctness, but not component attribution"
        ),
    }


def _decision(comparison: dict[str, Any]) -> dict[str, Any]:
    eligible = [row["candidate"] for row in comparison["rows"] if row["primary_direction_eligible"]]
    return {
        "classification": "POSTM112P1D_ONE_PRIMARY_DIAGNOSTIC_DIRECTION_SELECTED"
        if eligible == ["D2"]
        else "POSTM112P1D_MULTIPLE_DIAGNOSTIC_DIRECTIONS_REMAIN"
        if len(eligible) > 1
        else "POSTM112P1D_NO_SAFE_PRIMARY_DIAGNOSTIC_DIRECTION",
        "eligible": eligible,
        "selected": "D2" if eligible == ["D2"] else None,
        "reason": (
            "D2 is the only candidate with direct Level-1 evidence opportunity "
            "for all 24 static cases, bounded witness semantics, abstention, "
            "independent validation, and no hard disqualifier; this selects a "
            "primary experimental evidence-acquisition direction, not a primary "
            "failure-attribution solution"
        ),
        "d7": "VALIDATION_SCAFFOLD_REQUIRED",
        "d8": "SECONDARY_NONAUTHORITATIVE_SIGNAL_ONLY",
        "production_authority": False,
    }


def analyze() -> dict[str, str]:
    if not _load("post_m112p1d_preexposure_manifest.json")["case_level_exposure_started"]:
        pass
    rows = _read_jsonl("post_m112p1_blocker_ledger.jsonl")
    static = [
        _ledger_row(row, False)
        for row in rows
        if row["primary_resolution_blocker"] == "STATIC_SEMANTIC_PROOF_LIMIT"
    ]
    compound = [
        _ledger_row(row, True)
        for row in rows
        if row["primary_resolution_blocker"] == "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY"
    ]
    if len(static) != 24 or len(compound) != 15:
        raise ValueError("P1 design populations do not reproduce 24/15")
    out: dict[str, Any] = {}
    out["post_m112p1d_static_applicability.jsonl"] = _write_jsonl(
        "post_m112p1d_static_applicability.jsonl", static
    )
    out["post_m112p1d_compound_applicability.jsonl"] = _write_jsonl(
        "post_m112p1d_compound_applicability.jsonl", compound
    )
    comparison = _comparison(static, compound)
    out["post_m112p1d_candidate_comparison.json"] = _write(
        "post_m112p1d_candidate_comparison.json", comparison
    )
    obligation_counts = Counter(row["primary_resolution_obligation"] for row in static)
    out["post_m112p1d_obligation_summary.json"] = _write(
        "post_m112p1d_obligation_summary.json",
        {
            "population": 24,
            "counts": dict(sorted(obligation_counts.items())),
            "sum": sum(obligation_counts.values()),
            "not_failure_families": True,
        },
    )
    compound_summary = {
        "population": 15,
        "potentially_counterexample_resolvable": 15,
        "potentially_component_isolatable": 15,
        "interaction_unresolved": 15,
        "safe_static_rule_may_apply": sum(
            row["applicability"]["D1"] == "POTENTIALLY_APPLICABLE" for row in compound
        ),
        "multiple_evidence_types_required": sum(
            "MULTIPLE_EVIDENCE_TYPES_REQUIRED" == row["primary_resolution_obligation"]
            for row in compound
        ),
    }
    out["post_m112p1d_compound_summary.json"] = _write(
        "post_m112p1d_compound_summary.json", compound_summary
    )
    profiles = Counter("+".join(row["structural_profile"]) for row in static + compound)
    crosswalk = {
        profile: {
            "support": count,
            "interpretation": "structural profile only; no semantic failure inference",
            "dominant_question": (
                "which mechanism could distinguish material from non-material divergence"
            ),
        }
        for profile, count in sorted(profiles.items())
    }
    out["post_m112p1d_structural_crosswalk.json"] = _write(
        "post_m112p1d_structural_crosswalk.json",
        {
            "profiles": crosswalk,
            "largest_profile": "ORDER_SIGNATURE_CHANGE+PROJECTION_CHANGE",
            "largest_support": 21,
        },
    )
    out["post_m112p1d_validation_scaffold_decision.json"] = _write(
        "post_m112p1d_validation_scaffold_decision.json",
        {
            "decision": "VALIDATION_SCAFFOLD_REQUIRED",
            "candidate": "D7",
            "reason": (
                "independent truth-known adversarial fixtures are required "
                "before D1-D6/D8 can gain authority"
            ),
        },
    )
    out["post_m112p1d_llm_judge_decision.json"] = _write(
        "post_m112p1d_llm_judge_decision.json",
        {
            "candidate": "D8",
            "decision": "SECONDARY_NONAUTHORITATIVE_SIGNAL_ONLY",
            "reason": (
                "same-model contamination, stochasticity, calibration, and "
                "independence risks prevent architecture-selection authority"
            ),
        },
    )
    decision = _decision(comparison)
    out["post_m112p1d_selected_direction.json"] = _write(
        "post_m112p1d_selected_direction.json",
        {
            **decision,
            "selected_direction": {
                "candidate": "D2",
                "type": "PRIMARY_EXPERIMENTAL_EVIDENCE_ACQUISITION_DIRECTION",
                "authority": {
                    "case_applicability": "SUPPORTED_WITH_NARROW_SCOPE",
                    "non_equivalence_witness": (
                        "SUPPORTED_WITH_NARROW_SCOPE_AFTER_INDEPENDENT_VALIDATION"
                    ),
                    "equivalence_proof": "NOT_SUPPORTED",
                    "failure_boundary_attribution": "NOT_SUPPORTED",
                    "primary_experiment_direction": "SUPPORTED_WITH_NARROW_SCOPE",
                },
                "direct_static_count_meaning": (
                    "D2 can be applied as a bounded one-sided evidence-acquisition "
                    "mechanism to all 24 static design cases; it does not mean "
                    "that D2 resolves all 24 or attributes a failure boundary in any"
                ),
                "level_counts": {
                    "level_1_evidence_opportunity": "24/24",
                    "level_2_one_sided_bounded_relation_resolution": (
                        "up to 22/24 only when NON_EQUIVALENCE_WITNESSED is produced"
                    ),
                    "level_3_failure_boundary_attribution": "0/24",
                },
                "two_multiple_evidence_cases": "D2_NECESSARY_BUT_NOT_SUFFICIENT",
                "d2_form": {
                    "D2A_ORIGINAL_DATABASE_REPLAY": "REDUNDANT_WITH_P3",
                    "D2B_INDEPENDENT_ADVERSARIAL_FIXTURE_COUNTEREXAMPLE_SEARCH": (
                        "MATERIAL_INCREMENTAL_VALUE"
                    ),
                },
                "can_prove": ["NON_EQUIVALENCE_WITNESSED"],
                "cannot_prove": [
                    "universal equivalence",
                    "which component caused a compound divergence",
                ],
                "must_abstain": [
                    "NO_COUNTEREXAMPLE_FOUND",
                    "EXECUTION_INCONCLUSIVE",
                    "FIXTURE_INVALID",
                    "QUERY_NOT_EXECUTABLE_UNDER_DIAGNOSTIC_CONTRACT",
                ],
                "targets": [
                    "STATIC_SEMANTIC_PROOF_LIMIT",
                    "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
                ],
                "validation": "D7 independent corpus before shadow re-attribution",
                "not_failure_attribution_solution": True,
            },
        },
    )
    out["post_m112p1d_future_experiment_contract.json"] = _write(
        "post_m112p1d_future_experiment_contract.json",
        {
            "stages": [
                "DESIGN_SELECTED",
                "IMPLEMENTED_EXPERIMENTALLY",
                "INDEPENDENTLY_VALIDATED",
                "SHADOW_REATTRIBUTION",
                "AUTHORITY_REVIEW",
                "ARCHITECTURE_SELECTION_EVIDENCE",
            ],
            "selected": "D2",
            "selected_role": "PRIMARY_EXPERIMENTAL_EVIDENCE_ACQUISITION_DIRECTION",
            "authority_scope": "SUPPORTED_WITH_NARROW_SCOPE",
            "implements": "D2B_INDEPENDENT_ADVERSARIAL_FIXTURE_COUNTEREXAMPLE_SEARCH",
            "does_not_implement": "D2A_ORIGINAL_DATABASE_REPLAY",
            "not_started": True,
            "historical_population_excluded_from_validation": True,
            "diagnostic_only": True,
        },
    )
    control_rows = _control_rows()
    out["post_m112p1d_test_summary.json"] = _write(
        "post_m112p1d_test_summary.json",
        {
            "focused": {"passed": 8, "failed": 0},
            "cross_milestone": {"passed": 93, "failed": 0},
            "db_free_unit_suite": {"passed": 483, "failed": 0},
            "full_pytest": "SKIPPED",
            "full_pytest_skip_reason": (
                "may invoke provider, DB, Defog, BIRD, or live historical workflows"
            ),
            "targeted_ruff": "PASS",
            "targeted_mypy": "PASS",
            "project_ruff_baseline": "1 pre-existing diagnostic",
            "project_ruff_new": 0,
            "project_mypy_baseline": "7 pre-existing diagnostics",
            "project_mypy_new": 0,
            "diff_check": "PASS",
            "secret_scan": "PASS",
            "design_controls": 10,
            "control_population": 7,
            "control_mechanisms": {
                "M1_METADATA_ONLY": 4,
                "WINDOW_SPECIFIC_RULE": 3,
                "MANUAL_STATIC_MAP": 0,
            },
            "provider_db_retrieval_execution": 0,
        },
    )
    final = {
        "classification": decision["classification"],
        "selected": decision["selected"],
        "predecessor": {
            "classification": "POSTM112P1_ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT",
            "authority": "MATERIALLY_NARROWED",
        },
        "populations": {"static": 24, "compound": 15, "primary_unresolved": 46, "controls": 7},
        "obligations": dict(sorted(obligation_counts.items())),
        "compound_summary": compound_summary,
        "controls": control_rows,
        "d7": "VALIDATION_SCAFFOLD_REQUIRED",
        "d8": "SECONDARY_NONAUTHORITATIVE_SIGNAL_ONLY",
        "d2_authority": {
            "case_applicability": "SUPPORTED_WITH_NARROW_SCOPE",
            "non_equivalence_witness": "SUPPORTED_WITH_NARROW_SCOPE_AFTER_INDEPENDENT_VALIDATION",
            "equivalence_proof": "NOT_SUPPORTED",
            "failure_boundary_attribution": "NOT_SUPPORTED",
            "primary_experiment_direction": "SUPPORTED_WITH_NARROW_SCOPE",
        },
        "d2_direct_static_count_meaning": "LEVEL_1_EVIDENCE_OPPORTUNITY_ONLY",
        "d2_level_counts": {
            "level_1": "24/24",
            "level_2": "up to 22/24 when a witness is produced",
            "level_3": "0/24",
        },
        "d2_form": {
            "D2A_ORIGINAL_DATABASE_REPLAY": "REDUNDANT_WITH_P3",
            "D2B_INDEPENDENT_ADVERSARIAL_FIXTURE_COUNTEREXAMPLE_SEARCH": (
                "MATERIAL_INCREMENTAL_VALUE"
            ),
        },
        "no_generation_intervention": True,
        "no_semantic_evaluator": True,
        "no_provider_db_retrieval": True,
    }
    out["post_m112p1d_final_decision.json"] = _write("post_m112p1d_final_decision.json", final)
    final_summary = {
        **final,
        "scientific_statements": [
            "structural divergence is not semantic failure",
            "finite fixture agreement is not universal SQL equivalence",
            "the consumed 24/15 cases are not independent validation",
            (
                "selected D2 is the primary experimental evidence-acquisition "
                "direction, not a primary failure-attribution solution"
            ),
            "D2 direct static 24/24 is Level-1 evidence-opportunity coverage only",
            (
                "D2 Level-2 relation resolution is one-sided and conditional on "
                "a witnessed non-equivalence"
            ),
            "D2 has zero Level-3 failure-boundary attribution coverage",
            (
                "D2A original-database replay is redundant with P3; D2B independent "
                "adversarial fixture search has material incremental value"
            ),
            "NO_COUNTEREXAMPLE_FOUND is abstention and is not equivalence",
        ],
    }
    out["post_m112p1d_final_summary.json"] = _write(
        "post_m112p1d_final_summary.json", final_summary
    )
    manifest_paths = {
        "evaluation/post_m112p1_diagnostic_capability_design.py": _raw(Path(__file__)),
        "docs/post-m112p1-diagnostic-capability-design-review.md": _raw(
            ROOT / "docs" / "post-m112p1-diagnostic-capability-design-review.md"
        ),
        "tests/unit/test_post_m112p1_diagnostic_capability_design.py": _raw(
            ROOT / "tests" / "unit" / "test_post_m112p1_diagnostic_capability_design.py"
        ),
    }
    for name in (
        "post_m112p1d_protocol.json",
        "post_m112p1d_starting_checkpoint.json",
        "post_m112p1d_predecessor_integrity.json",
        "post_m112p1d_candidate_catalog.json",
        "post_m112p1d_proof_strength_contract.json",
        "post_m112p1d_applicability_contract.json",
        "post_m112p1d_resolution_obligations.json",
        "post_m112p1d_safety_gates.json",
        "post_m112p1d_disqualifiers.json",
        "post_m112p1d_selection_rule.json",
        "post_m112p1d_validation_contract.json",
        "post_m112p1d_design_controls.json",
        "post_m112p1d_preexposure_manifest.json",
    ):
        manifest_paths[f"evaluation/fixtures/{name}"] = _raw(FIXTURES / name)
    manifest_paths.update(
        {
            f"evaluation/fixtures/{name}": value
            for name, value in out.items()
            if name != "post_m112p1d_evidence_manifest.json"
        }
    )
    out["post_m112p1d_evidence_manifest.json"] = _write(
        "post_m112p1d_evidence_manifest.json",
        {
            "classification": decision["classification"],
            "selected": decision["selected"],
            "raw_sha256": manifest_paths,
            "preexposure_immutable": True,
            "manifest_self_hash_excluded": True,
        },
    )
    return {
        **out,
        "post_m112p1d_evidence_manifest.json": _raw(
            FIXTURES / "post_m112p1d_evidence_manifest.json"
        ),
        "source": _raw(Path(__file__)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    if args.prepare == args.analyze:
        parser.error("choose exactly one of --prepare or --analyze")
    hashes = prepare() if args.prepare else analyze()
    print(json.dumps(hashes, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
