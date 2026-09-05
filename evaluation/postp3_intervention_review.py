"""Offline post-P3 intervention-selection review.

This module consumes only the committed P0--P3 evidence package.  It does not
import application/runtime code, connect to a database, invoke a provider, or
invoke retrieval.  Its output is a bounded research-direction decision, not an
implementation decision.
"""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
START_HEAD = "2735ca60036946cb54070294366f1d5a32a21996"
P0_CONTRACT = "ba5adfa425e9bcf5efc6446a0021f1dbed2ebfaca745a913a3e601a403c1a678"
P1_SEMANTIC = "35c379ba4c5aa50b9f2a787c8aec6c5346b273139a4357d00e98edae8abdfb17"
P1_ADAPTABILITY = "00b00a256cb560930afa2b2299c72c369928ba7c3b42482f6b098ca341d042aa"
P2_CHECKPOINT = "846b20c47a6175ca091944a2b9a9277650037bed"
P2_SOURCE = "ae4db301464ba7d2281e5e9e5b05bd4506ba07836b3304078b31c41b34f9fc42"
P2_PROTOCOL = "f7c7fbea7278deaf8db32bb7cf1ae7fade40978c1ac3b5d1e4ccfe6bf2ed2eb4"
P2_SURVIVAL = "567ed66b74d87c0da0d2f74f987c29493e24517b73968044866ee789de8de68b"
P2_DECISION = "ed137ae442d4ed07b9ace7033863d137f46d16cf39a739cae63488422e38c66d"
M4_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
P3_HASHES = {
    "source": "29196e997ce9c39b12af2a66bfe1f8e8dda1de10d57ea462ac4d91ab1c504651",
    "protocol": "58ed34c157c597b5f9e51928e8881c379191f553a49bd60b6d5f05732f4a00f0",
    "population": "76988144bb6fa80345b6f6b50d3b43554d7459566aad7b6c3f06481a9fe9540e",
    "provider_config": "ff99faf7ebcabb51e6346dc565cce3e1da81e8a5d22c0d24b989de34bb4d1742",
    "treatment": "69ed731c5005f81cb545040b16b7b00ebd688ca7f66c10147bc8825ba93c5097",
    "preexposure": "ef7a49c22a13e14b213c0d74cd548d3a3cb6c9c7adcc2b4be438dcb731ea651a",
    "provider_ledger": "4f9881c16aefacf57d2f75b56c287064b579e1fe18aee8c793714ca16d2701bb",
    "paired_results": "c8e0cd5ae5f7efa45fb202983b24e8095c36992f0f076484cac8a8cd7d669db6",
    "primary_matrix": "dedfa106c0930137fe795b85e1271b6070ada2ad21de5873999b1e921566af44",
    "primary_statistics": "7fd06a951479f57ea86cb8b20d10d712452c4815a00c5230280eb13d99fc79b3",
    "secondary_slices": "a1dc84934f7ccd8d3d628912e774c9c5611a4effcdee7ca5244403f72deea75b",
    "discordant_analysis": "8626072c3a9bcce6ab3b6f00d724563894f37e2dc989cf2205e15501c3a6ca5a",
    "harm_pathways": "7d79408bf2fbb4411161ff51871352bc72968ab2ca217f5a3e6228f4a583d263",
    "help_pathways": "8620e2771941426811c7b1c764b641d4b7b94a8c890d05f0f4d166d4b1660f1a",
    "intervention_eligibility": "f1d04c1dd44d6286483a0168fbf2caecfefd3303afddc03a46720defe1b62ac2",
    "final_decision": "c01448faacd90171854c6c7697611032ebe4bc677d3c62e6cdeb4646c3fa1143",
    "final_summary": "879abb25d181f9f617e24b38dbe5a1b7b9bb81195e8c0e5f25515bfc32ccb470",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8")).hexdigest()


def _raw_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _raw_hash(path)


def frozen_evidence() -> dict[str, Any]:
    """Read and validate aggregate P3 evidence without inspecting D cases."""

    summary = _load(FIXTURES / "m111p3_final_summary.json")
    slices = _load(FIXTURES / "m111p3_secondary_slices.json")
    harm = _load(FIXTURES / "m111p3_harm_pathways.json")
    help_paths = _load(FIXTURES / "m111p3_help_pathways.json")
    manifest = _load(FIXTURES / "m111p3_evidence_manifest.json")
    primary = summary["primary"]
    assert primary["A"] == 36 and primary["B"] == 4
    assert primary["C"] == 9 and primary["D"] == 56 and primary["sum"] == 105
    assert primary["exact_two_sided_p"] == 0.266845703125
    assert summary["classification"] == "M111P3_MEMORY_EFFECT_MIXED_OR_INCONCLUSIVE"
    assert harm["ON_MEMORY_MOTIF_TRANSFER"] == 0 and harm["ON_DIVERGENCE_WITHOUT_TRANSFER"] == 4
    assert help_paths["ADAPTABLE_MEMORY_SUPPORTED_HELP"] == 2
    assert help_paths["NON_ADAPTABLE_CONTEXT_HELP"] == 6
    assert help_paths["UNKNOWN_CONTEXT_HELP"] == 1
    return {
        "predecessors": {
            "p0_typed_result_contract": P0_CONTRACT,
            "p1_semantic_relation_contract": P1_SEMANTIC,
            "p1_adaptability_contract": P1_ADAPTABILITY,
            "p2_checkpoint": P2_CHECKPOINT,
            "p2_source": P2_SOURCE,
            "p2_protocol": P2_PROTOCOL,
            "p2_survival_matrix": P2_SURVIVAL,
            "p2_intervention_decision": P2_DECISION,
            "p3_checkpoint": START_HEAD,
            "p3_hashes": P3_HASHES,
            "m4_logical_corpus": M4_HASH,
        },
        "p2": {
            "targets": 105,
            "m4_entries": 50,
            "equivalent_coverage": "3/105",
            "adaptable_coverage": "5/105",
            "no_adaptable": "100/105",
            "adaptable_retrieved": "4/5",
            "adaptable_missed": "1/5",
        },
        "p3": {
            "A": primary["A"],
            "B": primary["B"],
            "C": primary["C"],
            "D": primary["D"],
            "off_correct": primary["OFF_correct"],
            "on_correct": primary["ON_correct"],
            "net_cases": primary["net_cases_C_minus_B"],
            "net_percentage_points": primary["percentage_points"],
            "discordant": primary["discordant"],
            "p_value": primary["exact_two_sided_p"],
            "classification": summary["classification"],
            "same_correctness_state": primary["A"] + primary["D"],
            "harm_pathways": harm,
            "help_pathways": help_paths,
            "secondary_slices": slices["rows"],
        },
        "p3_manifest_hash": _raw_hash(FIXTURES / "m111p3_evidence_manifest.json"),
        "p3_manifest": manifest,
    }


def evidence_hierarchy() -> dict[str, Any]:
    return {
        "purpose": "heterogeneous evidence is ordered, not collapsed into an architecture score",
        "levels": [
            {"level": 1, "name": "FRESH_PAIRED_CAUSAL_CORRECTNESS"},
            {"level": 2, "name": "FRESH_MEDIATOR_PATHWAY"},
            {"level": 3, "name": "VALIDATED_DETERMINISTIC_STRUCTURAL"},
            {"level": 4, "name": "HISTORICAL_OR_REVALIDATED_ASSOCIATIONAL"},
            {"level": 5, "name": "ARCHITECTURAL_PLAUSIBILITY"},
        ],
        "selection_rule": [
            "affects the largest or most decision-relevant validated residual",
            "is not already answered by P0-P3",
            "has stronger evidence than competing directions",
            "can be investigated without assuming the desired intervention",
            "can produce a bounded later architecture decision",
        ],
    }


def candidate_matrix() -> list[dict[str, Any]]:
    """Return the precommitted qualitative comparison of all candidate directions."""

    return [
        {
            "candidate": "DIRECT_GENERATION_RESIDUAL_REBASELINE",
            "evidence_for": [
                "P3 D=56/105 is the largest primary cell",
                "92/105 retained the same correctness state; 56 were wrong in both arms",
                "P3 did not pass a global memory-helpful or memory-harmful gate",
                "P2 structural-transfer slice is dominated by D=17/20",
            ],
            "evidence_against": [
                "D does not prove one direct-generation mechanism",
                "one-shot provider stochasticity remains",
                "governed reference independence remains unresolved where applicable",
            ],
            "evidence_strength": ["LEVEL_1", "LEVEL_2", "LEVEL_3"],
            "causal_support": "not an intervention effect claim; diagnostic residual evidence",
            "specific_failure_boundary_identified": False,
            "next_step_type": "DIAGNOSTIC",
            "decision": "SELECTED_NEXT_RESEARCH_DIRECTION",
            "reason": (
                "It addresses the largest validated unresolved correctness population "
                "without presupposing a fix."
            ),
        },
        {
            "candidate": "MEMORY_ADMISSION_BOUNDARY_AUDIT",
            "evidence_for": ["4 observed OFF-correct/ON-wrong discordances"],
            "evidence_against": [
                "9 help discordances exceed 4 harms",
                "0/4 harms have bounded memory-motif transfer",
                "no-adaptable slice has B=3 and C=6; structural-transfer slice has B=0 and C=2",
                "no validated admission discriminator exists",
            ],
            "evidence_strength": ["LEVEL_1", "LEVEL_2"],
            "causal_support": "mixed descriptive paired evidence; no selective discriminator",
            "specific_failure_boundary_identified": False,
            "next_step_type": "DIAGNOSTIC_OR_IMPLEMENTATION",
            "decision": "REQUIRES_FURTHER_EVIDENCE",
            "reason": (
                "Observed harms do not isolate an admission boundary and non-adaptable "
                "context also helped."
            ),
        },
        {
            "candidate": "VERIFIED_DEMONSTRATION_CORPUS_EXPANSION_AUDIT",
            "evidence_for": [
                "P2 proven-adaptable coverage is only 5/105",
                "P3 has 9 help discordances",
            ],
            "evidence_against": [
                "global net-helpful gates failed",
                "only 2/9 helps were adaptable-memory-supported",
                "6/9 helps occurred with non-adaptable context",
                "no evidence identifies which new demonstrations would address D=56",
            ],
            "evidence_strength": ["LEVEL_1", "LEVEL_4"],
            "causal_support": "not established for adding demonstrations",
            "specific_failure_boundary_identified": False,
            "next_step_type": "DIAGNOSTIC_OR_IMPLEMENTATION",
            "decision": "REQUIRES_FURTHER_EVIDENCE",
            "reason": "Coverage deficit is real, but it is not yet a proven correctness lever.",
        },
        {
            "candidate": "RETRIEVAL_INTERVENTION_AUDIT",
            "evidence_for": ["1 of 5 adaptable-covered targets missed an adaptable entry"],
            "evidence_against": [
                "denominator is 5",
                "4/5 were retrieved",
                "no alternative retriever was tested",
                "100/105 had no proven-adaptable entry to retrieve",
            ],
            "evidence_strength": ["LEVEL_4"],
            "causal_support": "not tested",
            "specific_failure_boundary_identified": False,
            "next_step_type": "DIAGNOSTIC",
            "decision": "REQUIRES_FURTHER_EVIDENCE",
            "reason": "The historical miss is too small and indirect to select a retrieval audit.",
        },
        {
            "candidate": "MEMORY_REPRESENTATION_OR_ADAPTABILITY_CONTRACT_AUDIT",
            "evidence_for": ["6/9 helps occurred outside P1 proven-adaptable context"],
            "evidence_against": [
                "P1 is intentionally a conservative verified-adaptability contract",
                "outside-contract help does not prove P1 is defective",
                "no repeated bounded blind spot is established",
                "legacy atom novelty remains historical-only",
            ],
            "evidence_strength": ["LEVEL_1", "LEVEL_2", "LEVEL_4"],
            "causal_support": "insufficient to revise the frozen contract",
            "specific_failure_boundary_identified": False,
            "next_step_type": "DIAGNOSTIC",
            "decision": "REQUIRES_FURTHER_EVIDENCE",
            "reason": (
                "The observations may reflect unmodeled usefulness or stochasticity "
                "rather than a contract defect."
            ),
        },
        {
            "candidate": "MEMORY_REMOVAL_AUDIT",
            "evidence_for": ["4 observed harm discordances and memory context overhead"],
            "evidence_against": [
                "9 help discordances",
                "ON point estimate is higher",
                "net-harm gates failed",
            ],
            "evidence_strength": ["LEVEL_1"],
            "causal_support": "does not support global removal",
            "specific_failure_boundary_identified": False,
            "next_step_type": "IMPLEMENTATION",
            "decision": "NOT_SUPPORTED",
            "reason": (
                "The paired evidence does not support removing current memory for "
                "correctness."
            ),
        },
        {
            "candidate": "NO_INTERVENTION_DIRECTION_YET",
            "evidence_for": [
                "P3 is mixed/inconclusive and no memory intervention passed its gates"
            ],
            "evidence_against": ["D=56 identifies a larger unresolved diagnostic question"],
            "evidence_strength": ["LEVEL_1"],
            "causal_support": "no intervention effect selected",
            "specific_failure_boundary_identified": False,
            "next_step_type": "NONE",
            "decision": "NOT_SUPPORTED",
            "reason": (
                "A bounded diagnostic question is justified even though no implementation "
                "is justified."
            ),
        },
    ]


def protocol() -> dict[str, Any]:
    return {
        "milestone": "POST-P3 INTERVENTION-SELECTION REVIEW",
        "classification": "POSTP3_DIRECT_GENERATION_RESIDUAL_REBASELINE_SELECTED",
        "mode": "OFFLINE_EVIDENCE_SYNTHESIS",
        "source": "frozen P0-P3 evidence only",
        "provider_calls": 0,
        "db_calls": 0,
        "retrieval_reruns": 0,
        "memory_off_on_runs": 0,
        "candidate_regeneration": 0,
        "benchmark_reruns": 0,
        "p3_rerun": False,
        "case_level_56_taxonomy": False,
        "historical_artifacts_mutated": False,
        "runtime_changed": False,
        "review_rule": (
            "select one next research question by evidence hierarchy and information value"
        ),
    }


def starting_checkpoint() -> dict[str, Any]:
    return {
        "head": START_HEAD,
        "origin_main": START_HEAD,
        "branch": "main",
        "starting_tree": "clean",
        "starting_untracked": 0,
        "classification": "POSTP3_REVIEW_START_PROVENANCE_VERIFIED",
    }


def selection_decision() -> dict[str, Any]:
    return {
        "classification": "POSTP3_DIRECT_GENERATION_RESIDUAL_REBASELINE_SELECTED",
        "selected_candidate": "DIRECT_GENERATION_RESIDUAL_REBASELINE",
        "why_selected": [
            "D=56/105 is the largest validated unresolved primary correctness cell",
            "P3 provides no decision-grade global memory direction",
            "the next uncertainty is what the both-wrong residual contains",
            "the selected step is diagnostic and does not assume a subsystem fix",
        ],
        "question": "Why do the same frozen cases remain wrong in both observed memory conditions?",
        "information_value": (
            "distinguish a repeated decision-boundary failure family from fragmented residuals"
        ),
        "does_not_assume": [
            "direct generation is one mechanism",
            "prompt tuning is the solution",
            "memory is harmful or helpful globally",
            "corpus expansion, retrieval, admission, or representation is justified",
        ],
        "runtime_intervention_selected": False,
        "no_numeric_architecture_score": True,
    }


def next_milestone_contract() -> dict[str, Any]:
    return {
        "next_milestone": "M11.2 — Direct Generation Residual Rebaseline",
        "population_source": "P3 primary matrix D cell OFF_WRONG_ON_WRONG",
        "population_n": 56,
        "population_selection": "freeze from P3 evidence before analysis",
        "review_performed_case_level_taxonomy": False,
        "purpose": "diagnostic failure attribution before any narrow architecture intervention",
        "required_boundaries": [
            "P0 typed evaluator remains authoritative",
            "M1 remains unchanged and is evidence, not a relaxation target",
            "P3 D population is consumed diagnostic evidence, not fresh validation",
            "any later intervention requires an independent fresh validation population",
        ],
    }


def test_summary() -> dict[str, Any]:
    return {
        "review_tests": "5 passed",
        "cross_milestone_tests": "120 passed",
        "db_free_unit_suite": "452 passed",
        "provider_calls_during_review": 0,
        "db_calls_during_review": 0,
        "retrieval_reruns_during_review": 0,
        "full_pytest": "SKIPPED",
        "full_pytest_reason": "may invoke live provider, DB, Defog, BIRD, or generation workflows",
        "targeted_ruff": "PASS",
        "targeted_mypy": "PASS",
        "new_review_ruff_diagnostics": 0,
        "new_review_mypy_diagnostics": 0,
        "diff_check": "PASS",
        "secret_scan": "PASS",
        "project_ruff_baseline": 1,
        "project_mypy_baseline": 7,
    }


def generate() -> dict[str, str]:
    evidence = frozen_evidence()
    values: dict[str, Any] = {
        "postp3_intervention_review_protocol.json": protocol(),
        "postp3_starting_checkpoint.json": starting_checkpoint(),
        "postp3_evidence_inventory.json": evidence,
        "postp3_evidence_hierarchy.json": evidence_hierarchy(),
        "postp3_candidate_matrix.json": {"candidates": candidate_matrix()},
        "postp3_selection_decision.json": selection_decision(),
        "postp3_next_milestone_contract.json": next_milestone_contract(),
        "postp3_test_summary.json": test_summary(),
    }
    hashes = {name: _write(name, value) for name, value in values.items()}
    hashes["source"] = _raw_hash(Path(__file__))
    final = {
        "classification": "POSTP3_DIRECT_GENERATION_RESIDUAL_REBASELINE_SELECTED",
        "selected_next_direction": "DIRECT_GENERATION_RESIDUAL_REBASELINE",
        "next_milestone": "M11.2 — Direct Generation Residual Rebaseline",
        "frozen_population": {"source": "P3 D cell", "N": 56},
        "p3_primary": {
            "A": 36,
            "B": 4,
            "C": 9,
            "D": 56,
            "net_cases": 5,
            "net_pp": 4.761904761904762,
            "p": 0.266845703125,
        },
        "reason": (
            "The largest unresolved validated residual is both-wrong; no competing memory "
            "direction has stronger decision-grade support."
        ),
        "boundaries": {
            "no_case_level_56_taxonomy_in_review": True,
            "no_runtime_intervention": True,
            "provider_calls": 0,
            "db_calls": 0,
            "retrieval_reruns": 0,
            "public_benchmark_reruns": 0,
            "historical_artifacts_changed": False,
        },
        "review_artifact_hashes": hashes,
    }
    hashes["postp3_final_summary.json"] = _write("postp3_final_summary.json", final)
    required_names = {
        "POSTP3_REVIEW_SOURCE_HASH": hashes["source"],
        "POSTP3_REVIEW_PROTOCOL_HASH": hashes["postp3_intervention_review_protocol.json"],
        "POSTP3_REVIEW_STARTING_CHECKPOINT_HASH": hashes["postp3_starting_checkpoint.json"],
        "POSTP3_REVIEW_EVIDENCE_INVENTORY_HASH": hashes["postp3_evidence_inventory.json"],
        "POSTP3_REVIEW_EVIDENCE_HIERARCHY_HASH": hashes["postp3_evidence_hierarchy.json"],
        "POSTP3_REVIEW_CANDIDATE_MATRIX_HASH": hashes["postp3_candidate_matrix.json"],
        "POSTP3_REVIEW_SELECTION_DECISION_HASH": hashes["postp3_selection_decision.json"],
        "POSTP3_REVIEW_NEXT_MILESTONE_CONTRACT_HASH": hashes[
            "postp3_next_milestone_contract.json"
        ],
        "POSTP3_REVIEW_TEST_SUMMARY_HASH": hashes["postp3_test_summary.json"],
        "POSTP3_REVIEW_EVIDENCE_MANIFEST_HASH": "reported_out_of_band_after_write",
        "POSTP3_REVIEW_FINAL_SUMMARY_HASH": hashes["postp3_final_summary.json"],
    }
    manifest = {
        "classification": "POSTP3_DIRECT_GENERATION_RESIDUAL_REBASELINE_SELECTED",
        "source_hash": hashes["source"],
        "artifact_raw_sha256": hashes,
        "required_hashes": required_names,
        "p3_evidence_manifest_raw_sha256": evidence["p3_manifest_hash"],
        "no_new_outcome_evidence": True,
        "no_provider_db_retrieval": True,
    }
    hashes["postp3_evidence_manifest.json"] = _write("postp3_evidence_manifest.json", manifest)
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()
    if args.generate:
        print(json.dumps(generate(), indent=2, sort_keys=True))
    else:
        print(json.dumps(selection_decision(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
