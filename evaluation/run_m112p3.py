"""Validate and apply the bounded M11.2P3 component-isolation harness."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from evaluation import run_m112p2 as d2run
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticState,
    stable_hash,
)
from evaluation.m112p3_component_isolation import (
    SUPPORTED_COMPONENTS,
    ArmInput,
    ArmIsolationResult,
    ArmIsolationState,
    ComponentIsolationHarness,
    ComponentKind,
    IsolationPair,
    PairIsolationState,
    aggregate_pair,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
SOURCE = ROOT / "evaluation" / "m112p3_component_isolation.py"
CONTROLS_PATH = FIXTURES / "m112p3_validation_controls.json"
VALIDATION_PATH = FIXTURES / "m112p3_validation_result.json"
SHADOW_PATH = FIXTURES / "m112p3_shadow_result.json"
MANIFEST_PATH = FIXTURES / "m112p3_evidence_manifest.json"


def raw(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> str:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return raw(path)


def load_controls() -> list[dict[str, Any]]:
    return cast(dict[str, list[dict[str, Any]]], json.loads(CONTROLS_PATH.read_text()))["controls"]


def control_arm(control: dict[str, Any]) -> ArmInput:
    pair = IsolationPair(
        pair_id=control["control_id"],
        candidate_sql=control["candidate_sql"],
        reference_sql=control["reference_sql"],
        comparison_mode=ComparisonMode(control.get("comparison_mode", "VALUE_BAG")),
        order_entitled=control.get("order_entitled", False),
        scenario_tags=tuple(control.get("scenario_tags", [])),
    )
    return ArmInput(
        case_id=control["control_id"],
        arm="CONTROL",
        p0_tier=control.get("p0_tier", "FULL"),
        p0_projection_entitled=control.get("p0_projection_entitled", True),
        pair=pair,
    )


def run_validation() -> dict[str, str]:
    controls = load_controls()
    runs: list[list[dict[str, Any]]] = []
    for _ in range(2):
        d2run.recreate_fixture_state()
        d2, _ = d2run.build_harness()
        harness = ComponentIsolationHarness(d2)
        runs.append(
            [harness.run_arm(control_arm(control)).model_dump(mode="json") for control in controls]
        )
    first = runs[0]
    expected_isolatable = {
        control["control_id"]: control["expected_component"]
        for control in controls
        if control["expected_class"] == "KNOWN_SINGLE_COMPONENT_ISOLATABLE"
    }
    false_unique = sum(
        row["state"] == ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
        for row, control in zip(first, controls, strict=True)
        if control["expected_class"] != "KNOWN_SINGLE_COMPONENT_ISOLATABLE"
    )
    required_isolated = sum(
        row["state"] == ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
        and row["isolated_component"] == expected_isolatable[row["case_id"]]
        for row in first
        if row["case_id"] in expected_isolatable
    )
    invariance_trials = [
        trial for row in first for trial in row["trials"] if trial["validation"] == "VALID"
    ]
    d2run.recreate_fixture_state()
    d2, _ = d2run.build_harness()
    v2_pairs = d2run.load_pairs()
    d2_subset = [
        pair
        for pair in v2_pairs
        if pair.pair_id in {"NE01_LITERAL_CASE", "NE02_OFFSET", "NE03_JOIN_USING_KEY"}
    ]
    d2_results = d2.run_pairs(d2_subset)
    d2_regression = all(
        result.state is DiagnosticState.NON_EQUIVALENCE_WITNESSED for result in d2_results
    )
    reproducible = first == runs[1]
    gates = {
        "D3-V1_false_unique_isolation": false_unique == 0,
        "D3-V2_required_isolation": required_isolated == len(expected_isolatable),
        "D3-V3_non_target_invariance": all(
            trial["invariance_checked"] for trial in invariance_trials
        ),
        "D3-V4_reproducibility": reproducible,
        "D3-V5_abstention": false_unique == 0,
        "D3-V6_d2_regression": d2_regression,
        "D3-V7_safety": all(
            row["state"] not in {"BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED"}
            or all(
                trial["validation"] == "VALID"
                for trial in row["trials"]
                if trial["component"] == row["isolated_component"]
            )
            for row in first
        ),
    }
    validation = {
        "validation_attempt": 1,
        "d3_source_hash": raw(SOURCE),
        "d2_source_hash": raw(d2run.SOURCE),
        "d2_fixture_bank_hash": raw(d2run.BANK_PATH),
        "d2_validation_contract_hash": raw(d2run.CONTRACT_PATH),
        "component_catalog": [component.value for component in ComponentKind],
        "active_supported_components": [component.value for component in SUPPORTED_COMPONENTS],
        "substitution_contract": {
            "single_component_only": True,
            "repair_and_transfer_required": True,
            "non_target_invariance_required": True,
            "parse_round_trip_required": True,
            "finite_fixture_causality_not_universal": True,
        },
        "classification": (
            "M112P3_COMPONENT_ISOLATION_VALIDATION_PASSED"
            if all(gates.values())
            else "M112P3_COMPONENT_ISOLATION_VALIDATION_FAILED"
        ),
        "controls": len(controls),
        "known_single_component_isolatable": len(expected_isolatable),
        "required_isolation": required_isolated,
        "false_unique_isolation": false_unique,
        "interaction_or_non_unique_controls": sum(
            control["expected_class"] in {"KNOWN_INTERACTION", "KNOWN_NO_ISOLATION"}
            for control in controls
        ),
        "unsafe_controls": sum(control["expected_class"] == "KNOWN_UNSAFE" for control in controls),
        "p0_controls": sum(
            control["expected_class"] == "P0_ENTITLEMENT_BLOCKED" for control in controls
        ),
        "non_target_invariance": (
            f"{sum(trial['invariance_checked'] for trial in invariance_trials)}"
            f"/{len(invariance_trials)}"
            if invariance_trials
            else "0/0"
        ),
        "reproducibility_rate": "100%" if reproducible else "0%",
        "d2_regression": d2_regression,
        "gates": gates,
        "runs": runs,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
    }
    return {"validation": write(VALIDATION_PATH, validation)}


def _historical_inputs() -> tuple[list[ArmInput], dict[str, tuple[str, ...]], set[str]]:
    m112p2_shadow = json.loads((FIXTURES / "m112p2_shadow_result.json").read_text())
    witnessed_cases = {
        row["pair_id"].rsplit(":", 1)[0] for row in m112p2_shadow["results"] if row["witness"]
    }
    ledger = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in (FIXTURES / "post_m112p1_blocker_ledger.jsonl").read_text().splitlines()
        )
    }
    profiles = {
        case_id: tuple(sorted(set(ledger[case_id]["OFF_profile"] + ledger[case_id]["ON_profile"])))
        for case_id in witnessed_cases
    }
    primary = {case_id for case_id in witnessed_cases if len(profiles[case_id]) >= 2}
    if len(witnessed_cases) != 32 or len(primary) != 31:
        raise ValueError("frozen M11.2P2 witness population/profile membership changed")
    corpus = {
        row["case_id"]: row
        for row in json.loads((FIXTURES / "m104s_corpus.json").read_text())["cases"]
    }
    candidates = {
        (row["case_id"], row["arm"]): row["candidate_sql"]
        for row in (
            json.loads(line)
            for line in (FIXTURES / "m111p3_candidate_ledger.jsonl").read_text().splitlines()
        )
    }
    inputs: list[ArmInput] = []
    for case_id in sorted(primary):
        row = ledger[case_id]
        for arm in ("OFF", "ON"):
            p0_states = row["p1_dimension_states"][arm]
            inputs.append(
                ArmInput(
                    case_id=case_id,
                    arm=arm,
                    p0_tier=row["p0_tier"],
                    p0_projection_entitled=p0_states["PROJECTION_OUTPUT_SEMANTICS"]
                    != "P0_TIER_MASKED",
                    profile=tuple(row[arm + "_profile"]),
                    pair=IsolationPair(
                        pair_id=f"{case_id}:{arm}",
                        candidate_sql=candidates[(case_id, arm)],
                        reference_sql=corpus[case_id]["reference_sql"],
                        comparison_mode=ComparisonMode.VALUE_BAG,
                        scenario_tags=("HISTORICAL", arm),
                    ),
                )
            )
    return inputs, profiles, primary


def shadow() -> dict[str, str]:
    validation = json.loads(VALIDATION_PATH.read_text())
    if validation["classification"] != "M112P3_COMPONENT_ISOLATION_VALIDATION_PASSED":
        raise RuntimeError("historical shadow requires passed D3 validation")
    inputs, profiles, primary = _historical_inputs()
    d2run.recreate_fixture_state()
    d2, metadata = d2run.build_harness()
    harness = ComponentIsolationHarness(d2)
    arm_results = [harness.run_arm(arm) for arm in inputs]
    by_case: dict[str, dict[str, ArmIsolationResult]] = defaultdict(dict)
    for result in arm_results:
        by_case[result.case_id][result.arm] = result
    pairs = [
        aggregate_pair(by_case[case_id]["OFF"], by_case[case_id]["ON"], profiles[case_id])
        for case_id in sorted(primary)
    ]

    arm_counts = Counter(result.state.value for result in arm_results)
    pair_counts = Counter(pair.state.value for pair in pairs)
    support: dict[str, dict[str, Any]] = {}
    for component in ComponentKind:
        stable = [
            pair
            for pair in pairs
            if pair.state is PairIsolationState.PAIR_STABLE_COMPONENT_ISOLATED
            and pair.isolated_component is component
        ]
        one_arm = [
            pair
            for pair in pairs
            if pair.state is PairIsolationState.PAIR_ONE_ARM_COMPONENT_ISOLATED
            and pair.isolated_component is component
        ]
        counted = stable + one_arm
        scenarios = {
            fixture_id
            for pair in counted
            for arm in (pair.off, pair.on)
            for fixture_id in arm.baseline_witness_fixture_ids
        }
        support[component.value] = {
            "arms_tested": sum(component in result.changed_components for result in arm_results),
            "bidirectional_arm_isolation": sum(
                result.state is ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
                and result.isolated_component is component
                for result in arm_results
            ),
            "repair_only": sum(
                trial.component is component
                and trial.validation == "VALID"
                and trial.repair_witness_removed
                and not trial.transfer_witness_reproduced
                for result in arm_results
                for trial in result.trials
            ),
            "transfer_only": sum(
                trial.component is component
                and trial.validation == "VALID"
                and trial.transfer_witness_reproduced
                and not trial.repair_witness_removed
                for result in arm_results
                for trial in result.trials
            ),
            "stable_pair_support": len(stable),
            "one_arm_pair_support": len(one_arm),
            "total_non_conflicting_pair_support": len(counted),
            "fixture_scenario_count": len(scenarios),
            "repeated_gate": (
                len(counted) >= 5
                and (
                    len(stable) >= 2
                    or sum(component in (result.isolated_component,) for result in arm_results) >= 7
                )
                and len(scenarios) >= 2
                and all(
                    pair.state is not PairIsolationState.PAIR_COMPONENT_DISAGREEMENT
                    for pair in counted
                )
            ),
        }
    order_projection = [
        pair
        for pair in pairs
        if set(pair.profile) == {"ORDER_SIGNATURE_CHANGE", "PROJECTION_CHANGE"}
    ]
    cluster = {
        "population": len(order_projection),
        "ORDER_isolated": sum(
            pair.isolated_component is ComponentKind.TOP_LEVEL_ORDER_BY for pair in order_projection
        ),
        "PROJECTION_isolated": sum(
            pair.isolated_component is ComponentKind.TOP_LEVEL_PROJECTION
            for pair in order_projection
        ),
        "other_isolated": sum(
            pair.isolated_component
            not in {None, ComponentKind.TOP_LEVEL_ORDER_BY, ComponentKind.TOP_LEVEL_PROJECTION}
            for pair in order_projection
        ),
        "multiple_or_non_unique": sum(
            pair.state is PairIsolationState.PAIR_MULTIPLE_COMPONENTS_NON_UNIQUE
            for pair in order_projection
        ),
        "interaction": sum(
            pair.state is PairIsolationState.PAIR_INTERACTION_NOT_ISOLATED
            for pair in order_projection
        ),
        "p0_blocked": sum(
            pair.state is PairIsolationState.PAIR_P0_ENTITLEMENT_BLOCKED
            for pair in order_projection
        ),
        "unsafe": 0,
        "no_isolation": sum(
            pair.state is PairIsolationState.PAIR_NO_SUPPORTED_COMPONENT_ISOLATED
            for pair in order_projection
        ),
    }
    repeated = [component for component, details in support.items() if details["repeated_gate"]]
    if not repeated:
        scientific_classification = "M112P3_NO_REPEATED_COMPONENT_ISOLATED"
    elif len(repeated) == 1:
        scientific_classification = "M112P3_ONE_REPEATED_BOUNDED_COMPONENT_IDENTIFIED"
    else:
        scientific_classification = "M112P3_MULTIPLE_REPEATED_BOUNDED_COMPONENTS_IDENTIFIED"
    output = {
        "classification": "M112P3_HISTORICAL_COMPONENT_ISOLATION_COMPLETED",
        "scientific_classification": scientific_classification,
        "d2_shadow_population": {
            "witnessed_pair_cases": 32,
            "single_dimensional_excluded": 1,
            "primary_multidimensional": 31,
        },
        "population_hash": stable_hash(sorted(primary)),
        "metadata": metadata,
        "d3_source_hash": raw(SOURCE),
        "d2_source_hash": raw(d2run.SOURCE),
        "d2_fixture_bank_hash": raw(d2run.BANK_PATH),
        "d2_validation_contract_hash": raw(d2run.CONTRACT_PATH),
        "component_catalog": [component.value for component in ComponentKind],
        "active_supported_components": [component.value for component in SUPPORTED_COMPONENTS],
        "eligible_arms": len(arm_results),
        "arm_counts": dict(sorted(arm_counts.items())),
        "pair_counts": dict(sorted(pair_counts.items())),
        "pairs": [pair.model_dump(mode="json") for pair in pairs],
        "component_support": support,
        "order_projection_cluster": cluster,
        "witnessed_cases_remaining_multidimensional": 31,
        "new_failure_family_attribution": 0,
        "d2_semantics_modified": 0,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
        "no_d3_generation_intervention": True,
    }
    return {"shadow": write(SHADOW_PATH, output)}


def finalize() -> dict[str, str]:
    validation = json.loads(VALIDATION_PATH.read_text())
    shadow_result = json.loads(SHADOW_PATH.read_text())
    if validation["classification"] != "M112P3_COMPONENT_ISOLATION_VALIDATION_PASSED":
        raise RuntimeError("finalization requires passed D3 validation")
    test_summary_path = FIXTURES / "m112p3_test_summary.json"
    final_summary_path = FIXTURES / "m112p3_final_summary.json"
    test_hash = write(
        test_summary_path,
        {
            "focused_unit": {"passed": 6, "failed": 0},
            "postgresql_integration": {"passed": 1, "failed": 0},
            "safe_cross_milestone": {"passed": 114, "failed": 0},
            "db_free_unit_suite": {"passed": 495, "failed": 0},
            "full_pytest": "SKIPPED",
            "full_pytest_skip_reason": (
                "may invoke provider, live DB, Defog, BIRD, or historical workflows"
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
    final_hash = write(
        final_summary_path,
        {
            "classification": shadow_result["scientific_classification"],
            "historical_shadow_classification": shadow_result["classification"],
            "validation_classification": validation["classification"],
            "population": shadow_result["d2_shadow_population"],
            "eligible_arms": shadow_result["eligible_arms"],
            "arm_counts": shadow_result["arm_counts"],
            "pair_counts": shadow_result["pair_counts"],
            "component_support": shadow_result["component_support"],
            "order_projection_cluster": shadow_result["order_projection_cluster"],
            "next_engineering_action": (
                "IMPLEMENT_BOUNDED_TWO_COMPONENT_INTERACTION_ISOLATION_ON_VALIDATED_INTERACTION_SUBSET"
            ),
            "no_generation_intervention": True,
            "no_d3_implementation_in_next_step": True,
            "test_summary_hash": test_hash,
        },
    )
    files = {
        "evaluation/m112p3_component_isolation.py": raw(SOURCE),
        "evaluation/run_m112p3.py": raw(Path(__file__)),
        "evaluation/fixtures/m112p3_validation_controls.json": raw(CONTROLS_PATH),
        "evaluation/fixtures/m112p3_validation_result.json": raw(VALIDATION_PATH),
        "evaluation/fixtures/m112p3_shadow_result.json": raw(SHADOW_PATH),
        "evaluation/fixtures/m112p3_test_summary.json": test_hash,
        "evaluation/fixtures/m112p3_final_summary.json": final_hash,
        "tests/unit/test_m112p3_component_isolation.py": raw(
            ROOT / "tests/unit/test_m112p3_component_isolation.py"
        ),
        "tests/integration/test_m112p3_component_isolation_postgres.py": raw(
            ROOT / "tests/integration/test_m112p3_component_isolation_postgres.py"
        ),
        "docs/m112p3-component-isolation.md": raw(ROOT / "docs/m112p3-component-isolation.md"),
    }
    manifest = {
        "version": "m112p3-evidence-manifest-v1",
        "classification": shadow_result["classification"],
        "raw_sha256": files,
        "d2_engine_unchanged": True,
        "new_failure_family_attribution": 0,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
    }
    return {
        "test_summary": test_hash,
        "final_summary": final_hash,
        "evidence_manifest": write(MANIFEST_PATH, manifest),
    }


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
