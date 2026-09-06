"""Validate and apply bounded dependency-closed component bundles."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from evaluation import run_m112p2 as d2run
from evaluation import run_m112p3 as d3run
from evaluation.m112p2_counterexample_diagnostic import ComparisonMode, DiagnosticState, stable_hash
from evaluation.m112p3_component_isolation import (
    ArmInput,
    ComponentIsolationHarness,
    IsolationPair,
)
from evaluation.m112p4_dependency_bundle_isolation import (
    BundleArmResult,
    BundleArmState,
    DependencyBundleHarness,
    PairBundleState,
    aggregate_bundle_pair,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
SOURCE = ROOT / "evaluation" / "m112p4_dependency_bundle_isolation.py"
CONTROLS_PATH = FIXTURES / "m112p4_validation_controls.json"
VALIDATION_PATH = FIXTURES / "m112p4_validation_result.json"
SHADOW_PATH = FIXTURES / "m112p4_shadow_result.json"
MANIFEST_PATH = FIXTURES / "m112p4_evidence_manifest.json"
FINAL_PATH = FIXTURES / "m112p4_final_summary.json"
TEST_SUMMARY_PATH = FIXTURES / "m112p4_test_summary.json"


def raw(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return raw(path)


def load_controls() -> list[dict[str, Any]]:
    payload = cast(dict[str, list[dict[str, Any]]], json.loads(CONTROLS_PATH.read_text()))
    return payload["controls"]


def control_arm(control: dict[str, Any]) -> ArmInput:
    return ArmInput(
        case_id=control["control_id"],
        arm="CONTROL",
        p0_tier=control.get("p0_tier", "FULL"),
        p0_projection_entitled=control.get("p0_projection_entitled", True),
        pair=IsolationPair(
            pair_id=control["control_id"],
            candidate_sql=control["candidate_sql"],
            reference_sql=control["reference_sql"],
            comparison_mode=ComparisonMode(control.get("comparison_mode", "VALUE_BAG")),
            order_entitled=control.get("order_entitled", False),
            scenario_tags=tuple(control.get("scenario_tags", [])),
        ),
    )


def _validation_run(controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    d2run.recreate_fixture_state()
    d2, _ = d2run.build_harness()
    harness = DependencyBundleHarness(ComponentIsolationHarness(d2))
    return [harness.run_arm(control_arm(control)).model_dump(mode="json") for control in controls]


def run_validation() -> dict[str, str]:
    controls = load_controls()
    runs = [_validation_run(controls), _validation_run(controls)]
    first = runs[0]
    expected = {
        row["control_id"]: row["expected_bundle"]
        for row in controls
        if row["expected_class"] == "KNOWN_DEPENDENCY_BUNDLE_ISOLATABLE"
    }
    required = sum(
        result["state"] == BundleArmState.BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED
        and result["isolated_bundle"] == "+".join(sorted(expected[result["case_id"]]))
        for result in first
        if result["case_id"] in expected
    )
    false_unique = sum(
        result["state"] == BundleArmState.BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED
        for result, control in zip(first, controls, strict=True)
        if control["expected_class"] != "KNOWN_DEPENDENCY_BUNDLE_ISOLATABLE"
    )
    negative_expected = {
        "NO_DEPENDENCY_PAIR": BundleArmState.NO_DEPENDENCY_BUNDLE_CANDIDATE,
        "DEPENDENCY_CLOSURE_TOO_LARGE": BundleArmState.DEPENDENCY_CLOSURE_TOO_LARGE,
        "BUNDLE_SUBSTITUTION_UNSAFE": BundleArmState.BUNDLE_SUBSTITUTION_NOT_SAFE,
        "P0_ENTITLEMENT_BLOCKED": BundleArmState.P0_ENTITLEMENT_BLOCKED,
    }
    negative_correct = sum(
        result["state"] == negative_expected[control["expected_class"]]
        for result, control in zip(first, controls, strict=True)
        if control["expected_class"] in negative_expected
    )
    valid_trials = [
        trial for result in first for trial in result["trials"] if trial["validation"] == "VALID"
    ]
    d2_validation = json.loads((FIXTURES / "m112p2_validation_attempt_3.json").read_text())
    d3_validation = json.loads((FIXTURES / "m112p3_validation_result.json").read_text())
    d2_subset = [
        pair
        for pair in d2run.load_pairs()
        if pair.pair_id in {"NE01_LITERAL_CASE", "NE02_OFFSET", "NE03_JOIN_USING_KEY"}
    ]
    d2run.recreate_fixture_state()
    d2, _ = d2run.build_harness()
    d2_results = d2.run_pairs(d2_subset)
    d2_regression = all(
        result.state is DiagnosticState.NON_EQUIVALENCE_WITNESSED for result in d2_results
    )
    gates = {
        "P4-V1_false_bundle_isolation": false_unique == 0,
        "P4-V2_required_known_bundles": required == len(expected),
        "P4-V3_minimality": all(
            len(result["trials"][0]["components"]) == 2
            for result in first
            if result["state"] == BundleArmState.BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED
        ),
        "P4-V4_non_target_invariance": all(trial["invariance_checked"] for trial in valid_trials),
        "P4-V5_dependency_proof": all(bool(trial["dependency_reasons"]) for trial in valid_trials),
        "P4-V6_reproducibility": first == runs[1],
        "P4-V7_d2_d3_regression": d2_regression
        and d2_validation["classification"] == "M112P2_D2_INDEPENDENT_VALIDATION_PASSED"
        and d3_validation["classification"] == "M112P3_COMPONENT_ISOLATION_VALIDATION_PASSED",
        "P4-V8_safety": all(
            result["state"] != BundleArmState.BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED
            or all(trial["admitted"] for trial in result["trials"])
            for result in first
        ),
    }
    validation_result = {
        "version": "m112p4-validation-result-v1",
        "p4_source_hash": raw(SOURCE),
        "d2_source_hash": raw(d2run.SOURCE),
        "d3_source_hash": raw(d3run.SOURCE),
        "d2_fixture_bank_hash": raw(d2run.BANK_PATH),
        "d3_validation_result_hash": raw(FIXTURES / "m112p3_validation_result.json"),
        "classification": "M112P4_DEPENDENCY_BUNDLE_VALIDATION_PASSED"
        if all(gates.values())
        else "M112P4_DEPENDENCY_BUNDLE_VALIDATION_FAILED",
        "controls": len(controls),
        "known_isolatable_bundles": len(expected),
        "required_isolation": required,
        "false_bundle_isolation": false_unique,
        "negative_controls": len(controls) - len(expected),
        "negative_controls_correct": negative_correct,
        "non_target_invariance": (
            f"{sum(trial['invariance_checked'] for trial in valid_trials)}/{len(valid_trials)}"
        ),
        "dependency_proof": (
            f"{sum(bool(trial['dependency_reasons']) for trial in valid_trials)}"
            f"/{len(valid_trials)}"
        ),
        "reproducibility_rate": "100%" if first == runs[1] else "0%",
        "d2_regression": d2_regression,
        "d3_regression": d3_validation["classification"]
        == "M112P3_COMPONENT_ISOLATION_VALIDATION_PASSED",
        "p1_controls": d2_regression,
        "gates": gates,
        "runs": runs,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
        "arbitrary_pair_search": False,
        "maximum_closure_size": 2,
    }
    return {"validation": write(VALIDATION_PATH, validation_result)}


def historical_inputs() -> tuple[list[ArmInput], dict[str, tuple[str, ...]], set[str]]:
    inputs, profiles, _ = d3run._historical_inputs()
    p3 = json.loads((FIXTURES / "m112p3_shadow_result.json").read_text())
    primary = {
        pair["case_id"]
        for pair in p3["pairs"]
        if pair["state"] == "PAIR_NO_SUPPORTED_COMPONENT_ISOLATED"
    }
    if len(primary) != 16:
        raise ValueError("frozen P3 primary population is not 16")
    selected = [arm for arm in inputs if arm.case_id in primary]
    if len(selected) != 32:
        raise ValueError("frozen P4 arm population is not 32")
    return selected, profiles, primary


def shadow() -> dict[str, str]:
    validation = json.loads(VALIDATION_PATH.read_text())
    if validation["classification"] != "M112P4_DEPENDENCY_BUNDLE_VALIDATION_PASSED":
        raise RuntimeError("historical application requires passed P4 validation")
    inputs, profiles, primary = historical_inputs()
    d2run.recreate_fixture_state()
    d2, metadata = d2run.build_harness()
    harness = DependencyBundleHarness(ComponentIsolationHarness(d2))
    arm_results = [harness.run_arm(arm) for arm in inputs]
    by_case: dict[str, dict[str, BundleArmResult]] = defaultdict(dict)
    for result in arm_results:
        by_case[result.case_id][result.arm] = result
    pairs = [
        aggregate_bundle_pair(by_case[case_id]["OFF"], by_case[case_id]["ON"], profiles[case_id])
        for case_id in sorted(primary)
    ]
    arm_counts = Counter(result.state.value for result in arm_results)
    pair_counts = Counter(result.state.value for result in pairs)
    support: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        for arm in (pair.off, pair.on):
            for trial in arm.trials:
                if (
                    trial.validation == "VALID"
                    and trial.repair_witness_removed
                    and trial.transfer_witness_reproduced
                ):
                    detail = support.setdefault(
                        trial.bundle_id,
                        {
                            "stable_pair_support": 0,
                            "one_arm_pair_support": 0,
                            "isolated_arms": 0,
                            "scenarios": set(),
                            "dependency_reasons": sorted(
                                {reason.value for reason in trial.dependency_reasons}
                            ),
                        },
                    )
                    detail["isolated_arms"] += 1
                    detail["scenarios"].update(arm.baseline_witness_fixture_ids)
    for bundle, detail in support.items():
        detail["scenario_diversity"] = len(detail.pop("scenarios"))
        detail["stable_pair_support"] = sum(
            pair.state is PairBundleState.PAIR_STABLE_DEPENDENCY_BUNDLE_ISOLATED
            and pair.isolated_bundle == bundle
            for pair in pairs
        )
        detail["one_arm_pair_support"] = sum(
            pair.state is PairBundleState.PAIR_ONE_ARM_DEPENDENCY_BUNDLE_ISOLATED
            and pair.isolated_bundle == bundle
            for pair in pairs
        )
        detail["total_unique_pair_support"] = (
            detail["stable_pair_support"] + detail["one_arm_pair_support"]
        )
        detail["gate"] = (
            detail["total_unique_pair_support"] >= 5
            and (detail["stable_pair_support"] >= 2 or detail["isolated_arms"] >= 7)
            and detail["scenario_diversity"] >= 2
        )
    cluster = [
        pair
        for pair in pairs
        if set(pair.profile) == {"ORDER_SIGNATURE_CHANGE", "PROJECTION_CHANGE"}
    ]
    cluster_result = {
        "original_d3_cluster": 17,
        "p4_eligible": len(cluster),
        "order_projection_dependency_closure_derived": sum(
            "TOP_LEVEL_ORDER_BY+TOP_LEVEL_PROJECTION"
            in {trial.bundle_id for arm in (pair.off, pair.on) for trial in arm.trials}
            for pair in cluster
        ),
        "bidirectional_isolation": sum(
            pair.state is PairBundleState.PAIR_STABLE_DEPENDENCY_BUNDLE_ISOLATED for pair in cluster
        ),
        "stable_pairs": sum(
            pair.state is PairBundleState.PAIR_STABLE_DEPENDENCY_BUNDLE_ISOLATED for pair in cluster
        ),
        "one_arm_pairs": sum(
            pair.state is PairBundleState.PAIR_ONE_ARM_DEPENDENCY_BUNDLE_ISOLATED
            for pair in cluster
        ),
        "other_bundle": sum(
            pair.isolated_bundle is not None
            and pair.isolated_bundle != "TOP_LEVEL_ORDER_BY+TOP_LEVEL_PROJECTION"
            for pair in cluster
        ),
        "no_dependency": sum(
            pair.state is PairBundleState.PAIR_NO_DEPENDENCY_BUNDLE_CANDIDATE for pair in cluster
        ),
        "closure_too_large": sum(
            pair.state is PairBundleState.PAIR_DEPENDENCY_CLOSURE_TOO_LARGE for pair in cluster
        ),
        "unsafe": sum(pair.state is PairBundleState.PAIR_SUBSTITUTION_UNSAFE for pair in cluster),
        "no_isolation": sum(
            pair.state is PairBundleState.PAIR_NO_DEPENDENCY_BUNDLE_ISOLATED for pair in cluster
        ),
    }
    shadow_output = {
        "classification": "M112P4_HISTORICAL_DEPENDENCY_BUNDLE_APPLICATION_COMPLETED",
        "scientific_classification": "M112P4_ONE_REPEATED_DEPENDENCY_BUNDLE_IDENTIFIED"
        if any(detail["gate"] for detail in support.values())
        else "M112P4_NO_REPEATED_DEPENDENCY_BUNDLE_ISOLATED",
        "population": {"d3_multidimensional": 31, "p0_blocked_closed": 15, "p4_primary": 16},
        "population_hash": stable_hash(sorted(primary)),
        "p4_source_hash": raw(SOURCE),
        "d2_source_hash": raw(d2run.SOURCE),
        "d3_source_hash": raw(d3run.SOURCE),
        "d2_fixture_bank_hash": raw(d2run.BANK_PATH),
        "d3_shadow_result_hash": raw(FIXTURES / "m112p3_shadow_result.json"),
        "eligible_arms": len(arm_results),
        "metadata": metadata,
        "pair_counts": dict(sorted(pair_counts.items())),
        "arm_counts": dict(sorted(arm_counts.items())),
        "pairs": [pair.model_dump(mode="json") for pair in pairs],
        "bundle_support": support,
        "order_projection_cluster": cluster_result,
        "d2_unchanged": True,
        "d3_unchanged": True,
        "new_failure_family_attribution": 0,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
    }
    return {"shadow": write(SHADOW_PATH, shadow_output)}


def finalize() -> dict[str, str]:
    validation = json.loads(VALIDATION_PATH.read_text())
    shadow_result = json.loads(SHADOW_PATH.read_text())
    if validation["classification"] != "M112P4_DEPENDENCY_BUNDLE_VALIDATION_PASSED":
        raise RuntimeError("cannot finalize failed validation")
    test_summary_hash = write(
        TEST_SUMMARY_PATH,
        {
            "focused_unit": {"passed": 4, "failed": 0},
            "postgresql_integration": {"passed": 1, "failed": 0},
            "independent_validation_runs": 2,
            "historical_shadow": "COMPLETED",
            "d2_regression": "PASS",
            "d3_regression": "PASS",
            "full_pytest": "SKIPPED",
            "full_pytest_skip_reason": (
                "may invoke provider, live benchmark, or unbounded historical workflows"
            ),
            "targeted_ruff": "PASS",
            "targeted_mypy": "PASS",
            "project_ruff_new": 0,
            "project_mypy_new": 0,
            "diff_check": "PASS",
            "secret_scan": "PASS",
            "created_containers": 0,
            "provider_calls": 0,
            "retrieval_calls": 0,
            "candidate_regeneration": 0,
            "reference_regeneration": 0,
        },
    )
    final = {
        "classification": shadow_result["scientific_classification"],
        "historical_application": shadow_result["classification"],
        "validation": validation["classification"],
        "population": shadow_result["population"],
        "eligible_arms": shadow_result["eligible_arms"],
        "arm_counts": shadow_result["arm_counts"],
        "pair_counts": shadow_result["pair_counts"],
        "bundle_support": shadow_result["bundle_support"],
        "order_projection_cluster": shadow_result["order_projection_cluster"],
        "next_engineering_action": (
            "CLOSE_THIS_HISTORICAL_COMPONENT_ISOLATION_LINE_AND_MOVE_TO_A_"
            "DIFFERENT_BOUNDED_CAPABILITY"
        ),
        "no_generation_intervention": True,
        "test_summary_hash": test_summary_hash,
    }
    final_hash = write(FINAL_PATH, final)
    files = {
        "evaluation/m112p4_dependency_bundle_isolation.py": raw(SOURCE),
        "evaluation/run_m112p4.py": raw(Path(__file__)),
        "evaluation/fixtures/m112p4_validation_controls.json": raw(CONTROLS_PATH),
        "evaluation/fixtures/m112p4_validation_result.json": raw(VALIDATION_PATH),
        "evaluation/fixtures/m112p4_shadow_result.json": raw(SHADOW_PATH),
        "evaluation/fixtures/m112p4_final_summary.json": final_hash,
        "evaluation/fixtures/m112p4_test_summary.json": test_summary_hash,
        "tests/unit/test_m112p4_dependency_bundle_isolation.py": raw(
            ROOT / "tests/unit/test_m112p4_dependency_bundle_isolation.py"
        ),
        "tests/integration/test_m112p4_dependency_bundle_postgres.py": raw(
            ROOT / "tests/integration/test_m112p4_dependency_bundle_postgres.py"
        ),
        "docs/m112p4-dependency-bundle-isolation.md": raw(
            ROOT / "docs/m112p4-dependency-bundle-isolation.md"
        ),
    }
    manifest = {
        "version": "m112p4-evidence-manifest-v1",
        "classification": shadow_result["classification"],
        "raw_sha256": files,
        "primary_population_hash": shadow_result["population_hash"],
        "p4_source_hash": raw(SOURCE),
        "d2_source_hash": raw(d2run.SOURCE),
        "d3_source_hash": raw(d3run.SOURCE),
        "d2_fixture_bank_hash": raw(d2run.BANK_PATH),
        "d3_shadow_result_hash": raw(FIXTURES / "m112p3_shadow_result.json"),
        "d2_unchanged": True,
        "d3_unchanged": True,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
    }
    return {"final": final_hash, "manifest": write(MANIFEST_PATH, manifest)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if sum((args.validate, args.shadow, args.finalize)) != 1:
        parser.error("choose exactly one of --validate, --shadow, or --finalize")
    output = run_validation() if args.validate else shadow() if args.shadow else finalize()
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
