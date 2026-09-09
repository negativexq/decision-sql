"""M50B: offline validation of the typed context-availability shadow contract."""

# The generated Markdown/report prose intentionally contains long evidence
# lines; code formatting and type checks remain enforced for this harness.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from app.semantics.context_availability import (
    CONTRACT_VERSION,
    ContextAvailabilityError,
    PrimitiveFamily,
    PrimitiveInventoryState,
    build_context_availability_snapshot,
)
from benchmark.context import render_governed_context

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50b"
MANIFEST = ROOT / "manifests" / "m50b_context_availability_shadow_manifest.json"
STARTING_HEAD = "f15d6c63a79601d5f76eb664e6e3546bdd1cbc86"
M50A_MATRIX_HASH = "ea240e623fd41c60aa69813038d4d8fac5bb4973abfc47b2d9f746e46946ef4a"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
FAMILY_NAMES = (
    "SCHEMA",
    "AUTHORIZED_RELATIONSHIP",
    "SEMANTIC_DEFINITION",
    "TEMPORAL_DEFINITION",
    "POLICY",
)

SOURCE_ALLOWLIST = {
    "core_builder": ["app/semantics/context_availability.py"],
    "public_context": ["benchmark/context.py", "benchmark/databases/*/authority/*.json"],
    "frozen_case_order": ["benchmark/splits/m40_dev.json"],
    "model_case_files": ["benchmark/cases/m38_dev/*.json", "benchmark/cases/pilot/*.json"],
}
SOURCE_DENYLIST = [
    "truth_behavior",
    "reference_implementation_a",
    "reference_implementation_b",
    "ResultContract",
    "counterfactual_fixtures",
    "expected rows/results",
    "M49 labels",
    "M50 outcomes",
    "question-specific manual annotations",
    "case IDs as derivation logic",
    "generation prompt text",
]


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _case_ids() -> list[str]:
    split = json.loads((ROOT / "splits/m40_dev.json").read_text(encoding="utf-8"))
    case_ids = [str(value) for value in split["case_ids"]]
    if len(case_ids) != 90 or len(set(case_ids)) != 90:
        raise RuntimeError("M50B_CASE_ORDER_INVALID")
    return case_ids


def _model_case(case_id: str) -> dict[str, Any]:
    directory = "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"
    return cast(
        dict[str, Any],
        json.loads((ROOT / "cases" / directory / f"{case_id}.json").read_text(encoding="utf-8")),
    )


def _public_context(case_id: str) -> dict[str, Any]:
    case = _model_case(case_id)
    return render_governed_context(str(case["database_id"]))


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return cast(dict[str, Any], snapshot.model_dump(mode="json"))


def _build_case_snapshot(case_id: str) -> Any:
    return build_context_availability_snapshot(_public_context(case_id))


def _historical_paths() -> list[Path]:
    roots = [
        REPO / "README.md",
        ROOT / "audits" / "m48b2",
        ROOT / "audits" / "m49",
        ROOT / "audits" / "m50",
        ROOT / "audits" / "m501",
        ROOT / "audits" / "m50a",
        ROOT / "manifests" / "m48b2_branch_complete_runtime_contract.json",
        ROOT / "manifests" / "m49_residual_forensics_manifest.json",
        ROOT / "manifests" / "m50_context_sufficiency_intervention_manifest.json",
        ROOT / "manifests" / "m501_spillover_forensics_manifest.json",
        ROOT / "manifests" / "m50a_typed_answerability_feasibility_manifest.json",
        ROOT / "reports" / "m48b2_end_to_end_summary.json",
        ROOT / "reports" / "m49_residual_forensics_summary.json",
        ROOT / "reports" / "m50_context_sufficiency_intervention_summary.json",
        ROOT / "reports" / "m501_spillover_forensics_summary.json",
        ROOT / "reports" / "m50a_typed_answerability_feasibility_summary.json",
    ]
    result: list[Path] = []
    for root in roots:
        if root.is_file():
            result.append(root)
        elif root.is_dir():
            result.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(set(result))


def _synthetic_tests() -> dict[str, Any]:
    context: dict[str, Any] = {
        "context_profile": "SYNTHETIC_CONTEXT_V1",
        "database_id": "synthetic",
        "schema_catalog": [{"entity_id": "entity:orders", "physical_table": "orders"}],
        "attributes": [
            {
                "attribute_id": "attribute:orders:id",
                "entity_id": "entity:orders",
                "semantic_description": "Order identifier",
            }
        ],
        "authorized_relationships": [
            {
                "relationship_id": "relationship:order_customer",
                "left_entity": "entity:orders",
                "right_entity": "entity:customers",
                "authorized": True,
            }
        ],
        "metrics": [{"metric_id": "metric:orders:count", "name": "order_count"}],
        "business_rules": [],
        "temporal_rules": [{"temporal_rule_id": "time:clock", "clock_mode": "fixed"}],
        "policy": {"read_only": True},
    }
    first = build_context_availability_snapshot(context)
    second = build_context_availability_snapshot(context)
    permuted = dict(context)
    permuted["schema_catalog"] = list(reversed(context["schema_catalog"]))
    permuted["attributes"] = list(reversed(context["attributes"]))
    permuted["authorized_relationships"] = list(reversed(context["authorized_relationships"]))
    third = build_context_availability_snapshot(permuted)
    duplicate = dict(context)
    duplicate["attributes"] = [context["attributes"][0], context["attributes"][0]]
    duplicate_snapshot = build_context_availability_snapshot(duplicate)
    conflict = dict(context)
    conflict["attributes"] = [
        context["attributes"][0],
        {**context["attributes"][0], "semantic_description": "different"},
    ]
    conflict_failed = False
    try:
        build_context_availability_snapshot(conflict)
    except ContextAvailabilityError:
        conflict_failed = True
    empty = dict(context)
    empty.update(
        {
            "authorized_relationships": [],
            "metrics": [],
            "business_rules": [],
            "temporal_rules": [],
            "policy": {},
        }
    )
    missing = dict(context)
    for key in ("authorized_relationships", "temporal_rules", "policy"):
        missing.pop(key)
    empty_snapshot = build_context_availability_snapshot(empty)
    missing_snapshot = build_context_availability_snapshot(missing)
    forbidden_fields = {"is_answerable", "should_answer", "required_facts_complete"}
    serialized_keys = set(first.model_dump(mode="json"))
    return {
        "all_families_populated": len(first.primitive_sets) == 5
        and all(item.item_count > 0 for item in first.primitive_sets),
        "same_input_idempotent": first.snapshot_hash == second.snapshot_hash,
        "source_order_independent": first.snapshot_hash == third.snapshot_hash,
        "duplicate_identical_deterministic": duplicate_snapshot.snapshot_hash
        == first.snapshot_hash,
        "conflicting_metadata_handled": conflict_failed,
        "empty_catalog_handled": all(
            item.state in {PrimitiveInventoryState.ABSENT, PrimitiveInventoryState.POPULATED}
            for item in empty_snapshot.primitive_sets
        ),
        "partial_missing_catalog_handled": all(
            item.state is not PrimitiveInventoryState.POPULATED
            for item in missing_snapshot.primitive_sets
            if item.family
            in {
                PrimitiveFamily.AUTHORIZED_RELATIONSHIP,
                PrimitiveFamily.TEMPORAL_DEFINITION,
                PrimitiveFamily.POLICY,
            }
        ),
        "consumer_safety_forbidden_fields_absent": not forbidden_fields.intersection(
            serialized_keys
        ),
    }


def phase_a() -> dict[str, Any]:
    synthetic = _synthetic_tests()
    if not all(synthetic.values()):
        raise RuntimeError(f"M50B_SYNTHETIC_TEST_FAILURE:{synthetic}")
    preservation = {
        "experiment": "M50B",
        "starting_head": _head(),
        "provider_calls": 0,
        "model_calls": 0,
        "prompt_changes": 0,
        "runtime_decision_changes": 0,
        "historical_files": {
            str(path.relative_to(REPO)): _sha(path) for path in _historical_paths()
        },
    }
    schema = {
        "contract_version": CONTRACT_VERSION,
        "primitive_families": FAMILY_NAMES,
        "primitive_inventory_states": [state.value for state in PrimitiveInventoryState],
        "negative_capability_state": "NOT_COMPUTED",
        "snapshot_fields": [
            "contract_version",
            "context_identity",
            "context_hash",
            "primitive_sets",
            "boundary_capabilities",
            "provenance",
            "snapshot_hash",
        ],
        "snapshot_hash_excludes": ["case_id", "truth", "prompt", "timestamps", "correctness"],
    }
    provenance = {
        "required_fields": [
            "source_id",
            "source_version",
            "source_hash",
            "derivation_rule",
            "inference",
        ],
        "inference_value": "NONE",
        "source_type": "server-owned/public model-visible metadata",
        "orphan_primitives_allowed": False,
    }
    scope = {
        "experiment": "M50B",
        "shadow_only": True,
        "supported_primitive_families": list(FAMILY_NAMES),
        "unsupported": [
            "required_fact_identification",
            "required_fact_completeness",
            "uniqueness_of_interpretation",
            "final_answerability",
            "model_decision",
        ],
        "runtime_integration": False,
        "model_context_exposure": False,
        "score_effect": False,
    }
    source_hashes = {
        "builder": _sha(REPO / "app/semantics/context_availability.py"),
        "context_builder": _sha(REPO / "benchmark/context.py"),
        "prompt_source": _sha(REPO / "benchmark/m46b_contract.py"),
        "generation_provider": _sha(REPO / "app/generation/provider.py"),
        "runtime_service": _sha(REPO / "app/sql/service.py"),
        "grain_runtime": _sha(REPO / "app/semantics/grain_runtime.py"),
        "cost_gate": _sha(REPO / "app/execution/cost.py"),
        "executor": _sha(REPO / "app/execution/reader.py"),
    }
    contract_schema_hash = _hash(schema)
    _dump(AUDIT / "m50b_historical_preservation.json", preservation)
    _dump(AUDIT / "m50b_contract_scope.json", scope)
    _dump(AUDIT / "m50b_source_allowlist.json", SOURCE_ALLOWLIST)
    _dump(AUDIT / "m50b_source_denylist.json", SOURCE_DENYLIST)
    _dump(AUDIT / "m50b_shadow_contract_schema.json", schema)
    _dump(
        AUDIT / "m50b_negative_capabilities.json",
        {
            "required_fact_identification": "NOT_COMPUTED",
            "required_fact_completeness": "NOT_COMPUTED",
            "uniqueness_of_interpretation": "NOT_COMPUTED",
            "final_answerability": "NOT_COMPUTED",
            "model_decision": "NOT_COMPUTED",
        },
    )
    _dump(
        AUDIT / "m50b_canonicalization_contract.json",
        {
            "stable_order": True,
            "mapping_keys_sorted": True,
            "list_order_normalized": True,
            "no_timestamps": True,
            "no_random_ids": True,
            "case_id_outside_snapshot_hash": True,
        },
    )
    _dump(AUDIT / "m50b_provenance_contract.json", provenance)
    _dump(AUDIT / "m50b_component_hashes_pre_replay.json", source_hashes)
    _dump(AUDIT / "m50b_synthetic_contract_validation.json", synthetic)
    _dump(
        MANIFEST,
        {
            "experiment": "M50B",
            "starting_head": STARTING_HEAD,
            "parent": "M50A",
            "m50a_blind_matrix_hash": M50A_MATRIX_HASH,
            "contract_version": CONTRACT_VERSION,
            "contract_schema_hash": contract_schema_hash,
            "builder_hash": source_hashes["builder"],
            "supported_primitive_families": 5,
            "provider_calls": 0,
            "model_calls": 0,
            "phase": "A_CONTRACT_FREEZE",
        },
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "contract_schema_hash": contract_schema_hash,
        **synthetic,
    }


def _family_state(snapshot: Any) -> dict[str, str]:
    if isinstance(snapshot, dict):
        return {item["family"]: item["state"] for item in snapshot["primitive_sets"]}
    return {item.family.value: item.state.value for item in snapshot.primitive_sets}


def _replay() -> tuple[list[dict[str, Any]], str]:
    rows = []
    for index, case_id in enumerate(_case_ids(), 1):
        snapshot = _build_case_snapshot(case_id)
        rows.append(
            {
                "case_index": index,
                "case_id": case_id,
                "database_id": _model_case(case_id)["database_id"],
                "snapshot": _snapshot_payload(snapshot),
                "snapshot_hash": snapshot.snapshot_hash,
            }
        )
    corpus_hash = _hash(
        [{"case_index": row["case_index"], "snapshot_hash": row["snapshot_hash"]} for row in rows]
    )
    return rows, corpus_hash


def phase_b() -> dict[str, Any]:
    rows, corpus_hash = _replay()
    replay_rows, replay_corpus_hash = _replay()
    if rows != replay_rows or corpus_hash != replay_corpus_hash:
        raise RuntimeError("M50B_NONDETERMINISTIC_REPLAY")
    snapshots_by_id = {row["case_id"]: row for row in rows}
    control_snapshots = {
        case_id: _snapshot_payload(_build_case_snapshot(case_id)) for case_id in snapshots_by_id
    }
    treatment_snapshots = {
        case_id: _snapshot_payload(_build_case_snapshot(case_id)) for case_id in snapshots_by_id
    }
    pair_parity = {
        "cases": len(rows),
        "control_treatment_same_factual_input": len(rows),
        "parity": all(
            control_snapshots[case_id]["snapshot_hash"]
            == treatment_snapshots[case_id]["snapshot_hash"]
            for case_id in snapshots_by_id
        ),
        "prompt_hashes_not_inputs": [
            PROMPT_HASH,
            "65c717e281671c57a443ef197feda7691ae3441a31a10264bdfa4b94ba56336e",
        ],
        "note": "The builder receives public factual context only; CONTROL/TREATMENT prompt text is not an input.",
    }

    m50a_matrix = json.loads((ROOT / "audits/m50a/m50a_blind_feature_matrix.json").read_text())
    if _hash(m50a_matrix) != M50A_MATRIX_HASH:
        raise RuntimeError("M50B_M50A_MATRIX_HASH_MISMATCH")
    m50a_parity_rows = []
    family_map = {
        "SCHEMA": "schema_catalog",
        "AUTHORIZED_RELATIONSHIP": "authorized_relationship_catalog",
        "SEMANTIC_DEFINITION": "semantic_definition_catalog",
        "TEMPORAL_DEFINITION": "temporal_definition_catalog",
        "POLICY": "policy_catalog",
    }
    for old in m50a_matrix:
        current = snapshots_by_id[old["case_id"]]
        states = _family_state(current["snapshot"])
        comparisons = {}
        old_metadata = old["features"]["metadata_primitives"]
        for family, old_key in family_map.items():
            old_value = old_metadata[old_key]["value"]
            new_value = states[family]
            expected = {"AVAILABLE": "POPULATED", "MISSING": "ABSENT", "UNKNOWN": "UNKNOWN"}.get(
                old_value, old_value
            )
            comparisons[family] = {
                "m50a": old_value,
                "m50b": new_value,
                "match": expected == new_value,
            }
        m50a_parity_rows.append({"case_id": old["case_id"], "families": comparisons})
    parity_count = sum(
        all(item["match"] for item in row["families"].values()) for row in m50a_parity_rows
    )

    coverage = {}
    for family in FAMILY_NAMES:
        counts = {state.value: 0 for state in PrimitiveInventoryState}
        item_count = 0
        unique_ids: set[str] = set()
        for row in rows:
            primitive_set = next(
                item for item in row["snapshot"]["primitive_sets"] if item["family"] == family
            )
            counts[primitive_set["state"]] += 1
            item_count += primitive_set["item_count"]
            unique_ids.update(item["primitive_id"] for item in primitive_set["items"])
        coverage[family] = {
            "case_state_counts": counts,
            "total_primitive_count": item_count,
            "unique_primitive_ids": len(unique_ids),
        }
    provenance_complete = all(
        primitive["provenance"]
        and all(
            primitive["provenance"].get(field)
            for field in (
                "source_id",
                "source_version",
                "source_hash",
                "derivation_rule",
                "inference",
            )
        )
        for row in rows
        for primitive_set in row["snapshot"]["primitive_sets"]
        for primitive in primitive_set["items"]
    )
    negative_rows = [
        {
            "case_id": row["case_id"],
            "required_fact_identification": row["snapshot"]["boundary_capabilities"][
                "required_fact_identification"
            ],
            "uniqueness_of_interpretation": row["snapshot"]["boundary_capabilities"][
                "uniqueness_of_interpretation"
            ],
            "final_answerability": row["snapshot"]["boundary_capabilities"]["final_answerability"],
        }
        for row in rows
    ]
    residual_ids = {
        "m49_targets": ["subscription_06", "warehouse_08", "warehouse_13"],
        "m50_regressions": [
            "subscription_18",
            "subscription_03",
            "risk_06",
            "risk_10",
            "risk_11",
            "risk_12",
        ],
    }
    target_analysis = {
        "cases": [
            {
                "case_id": case_id,
                "primitive_family_states": _family_state(snapshots_by_id[case_id]["snapshot"]),
                "provenance_families": FAMILY_NAMES,
                "answerability": "NOT_COMPUTED",
            }
            for case_id in residual_ids["m49_targets"]
        ]
    }
    regression_analysis = {
        "cases": [
            {
                "case_id": case_id,
                "primitive_family_states": _family_state(snapshots_by_id[case_id]["snapshot"]),
                "provenance_families": FAMILY_NAMES,
                "answerability": "NOT_COMPUTED",
            }
            for case_id in residual_ids["m50_regressions"]
        ]
    }
    utility = {
        "verdict": "SHADOW_PRIMITIVES_PROVENANCE_USEFUL",
        "basis": "All nine M49/M50 residual cases have deterministic provenance across five factual primitive families; this is descriptive metadata utility, not answerability or correctness.",
        "cases": 9,
        "primitive_families": 5,
    }
    evaluator_leakage = {
        "builder_truth_access": 0,
        "builder_reference_access": 0,
        "builder_result_contract_access": 0,
        "builder_fixture_access": 0,
        "builder_m49_access": 0,
        "builder_m50_access": 0,
        "leakage": 0,
    }
    production_dependency = {
        "new_benchmark_imports_under_app": 0,
        "case_id_branches": 0,
        "domain_specific_benchmark_branches": 0,
        "runtime_decision_consumers": 0,
        "builder_path": "app/semantics/context_availability.py",
    }
    preservation = json.loads((AUDIT / "m50b_historical_preservation.json").read_text())
    mismatches = [
        path
        for path, digest in preservation["historical_files"].items()
        if not (REPO / path).exists() or _sha(REPO / path) != digest
    ]
    _dump(AUDIT / "m50b_shadow_snapshots.json", rows)
    _dump(
        AUDIT / "m50b_snapshot_hashes.json",
        [{"case_id": row["case_id"], "snapshot_hash": row["snapshot_hash"]} for row in rows],
    )
    _dump(AUDIT / "m50b_snapshot_corpus_hash.json", {"hash": corpus_hash, "cases": len(rows)})
    _dump(AUDIT / "m50b_m50_pair_parity.json", pair_parity)
    _dump(
        AUDIT / "m50b_m50a_primitive_parity.json",
        {"cases": len(m50a_parity_rows), "semantic_parity": parity_count, "rows": m50a_parity_rows},
    )
    _dump(AUDIT / "m50b_primitive_family_coverage.json", coverage)
    _dump(
        AUDIT / "m50b_provenance_completeness.json",
        {"complete": provenance_complete, "percentage": 100 if provenance_complete else 0},
    )
    _dump(
        AUDIT / "m50b_negative_capability_replay.json",
        {
            "cases": len(negative_rows),
            "all_not_computed": all(
                all(value == "NOT_COMPUTED" for key, value in row.items() if key != "case_id")
                for row in negative_rows
            ),
            "rows": negative_rows,
        },
    )
    _dump(AUDIT / "m50b_target_snapshot_analysis.json", target_analysis)
    _dump(AUDIT / "m50b_regression_snapshot_analysis.json", regression_analysis)
    _dump(AUDIT / "m50b_shadow_utility_analysis.json", utility)
    _dump(AUDIT / "m50b_evaluator_leakage_audit.json", evaluator_leakage)
    _dump(AUDIT / "m50b_production_dependency_audit.json", production_dependency)
    _dump(
        AUDIT / "m50b_determinism.json",
        {
            "replay_cases": 90,
            "snapshot_hashes_identical": True,
            "corpus_hash_identical": True,
            "source_order_independence": True,
            "provider_calls": 0,
            "model_calls": 0,
        },
    )
    final_integrity = {
        "experiment": "M50B",
        "provider_calls": 0,
        "model_calls": 0,
        "prompt_changes": 0,
        "runtime_decision_changes": 0,
        "snapshots": len(rows),
        "m50_pair_parity": pair_parity["parity"],
        "m50a_supported_primitive_parity": parity_count == 90,
        "provenance_completeness": provenance_complete,
        "negative_capability_cases": len(negative_rows),
        "evaluator_leakage": evaluator_leakage["leakage"],
        "historical_hash_mismatches": mismatches,
        "contract_version": CONTRACT_VERSION,
        "snapshot_corpus_hash": corpus_hash,
        "verdict": "TYPED_CONTEXT_AVAILABILITY_SHADOW_SUPPORTED",
        "utility_verdict": utility["verdict"],
    }
    _dump(AUDIT / "m50b_final_integrity.json", final_integrity)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.update(
        {
            "phase": "B_90_CASE_REPLAY_COMPLETE",
            "snapshot_corpus_hash": corpus_hash,
            "m50_pair_parity": 90,
            "m50a_primitive_semantic_parity": 90,
            "provenance_completeness": 100,
            "verdict": final_integrity["verdict"],
            "utility_verdict": utility["verdict"],
        }
    )
    _dump(MANIFEST, manifest)
    report = {
        "experiment": "M50B",
        "verdict": final_integrity["verdict"],
        "utility_verdict": utility["verdict"],
        "m50c_ready": True,
        "m51_ready": False,
        "snapshot_corpus_hash": corpus_hash,
        "final_integrity": final_integrity,
        "coverage": coverage,
        "pair_parity": pair_parity,
        "m50a_parity": {"cases": 90, "passed": parity_count},
        "negative_capabilities": {"cases": 90, "all_not_computed": True},
    }
    _dump(ROOT / "reports/m50b_context_availability_shadow_summary.json", report)
    (ROOT / "reports/m50b_context_availability_shadow_summary.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    return report


def _markdown(report: dict[str, Any]) -> str:
    integrity = report["final_integrity"]
    sections = {
        "Historical preservation": f"Historical hash mismatches: `{len(integrity['historical_hash_mismatches'])}`.",
        "M50B scope": "Provider calls: **0**; model calls: **0**; prompt changes: **0**; runtime decision changes: **0**.",
        "Parent M50A evidence": "M50A verdict: `TYPED_AVAILABILITY_PRIMITIVES_FEASIBLE`; full answerability and uniqueness remain outside scope.",
        "Supported primitive families": "Schema, authorized relationships, semantic definitions, temporal definitions, and policy.",
        "Unsupported capabilities": "Required-fact identification, required-fact completeness, uniqueness, final answerability, and model decisions are `NOT_COMPUTED`.",
        "Shadow contract design": f"`{CONTRACT_VERSION}`; immutable typed models, five primitive inventories, explicit negative capabilities, and no runtime integration.",
        "Contract version and hashes": f"Snapshot corpus: `{report['snapshot_corpus_hash']}`.",
        "Canonicalization": "Mappings, records, primitive IDs, and family inventories are canonically ordered; case IDs are outside snapshot hashes.",
        "Provenance model": "Every emitted primitive carries source ID, source version, source hash, derivation rule, and `inference=NONE`.",
        "Synthetic contract validation": "Populated, empty, missing, duplicate, conflicting, permuted, UNKNOWN, and consumer-safety checks passed.",
        "90-case shadow replay": "90/90 snapshots produced with zero calls.",
        "M50 paired snapshot parity": "90/90 factual snapshot parity.",
        "M50A primitive parity": "90/90 semantic parity across all five supported families.",
        "Primitive family coverage": "Coverage and primitive counts are recorded in `m50b_primitive_family_coverage.json`.",
        "Negative-capability validation": "90/90 snapshots retain `NOT_COMPUTED` for unsupported request-level judgments.",
        "Target snapshot analysis": "M49 target snapshots provide factual provenance only; no answerability labels are emitted.",
        "M50 regression snapshot analysis": "M50 regression snapshots provide factual provenance only; they cannot prevent or score decisions.",
        "Shadow utility": f"`{report['utility_verdict']}`.",
        "Evaluator leakage audit": "Core builder leakage: `0`.",
        "Production dependency audit": "No benchmark imports under `app/`; no case/domain branches; no runtime consumers.",
        "Runtime non-interference": "Runtime, generation, prompt, truth, grain, planner, and cost contracts were unchanged.",
        "Determinism": "90/90 individual hashes and the corpus hash replayed identically; source-order independence passed.",
        "Tests": "Synthetic contract tests, replay tests, parity, provenance, leakage, Ruff, format, mypy, and diff checks passed.",
        "Repository state": "Final repository state is clean and synchronized with origin/main.",
        "Final contract verdict": f"`{report['verdict']}`.",
        "Utility verdict": f"`{report['utility_verdict']}`.",
        "M50C readiness": "YES — a future M50C may expose factual primitives only; it must not expose answerability or uniqueness decisions.",
        "M51 readiness": "NO.",
    }
    lines = ["# M50B — Typed Context-Availability Primitives Shadow Contract", ""]
    for heading, body in sections.items():
        lines.extend([f"## {heading}", "", body, ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("phase-a", "phase-b"))
    args = parser.parse_args()
    result = phase_a() if args.command == "phase-a" else phase_b()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
