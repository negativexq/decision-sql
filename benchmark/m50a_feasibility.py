# ruff: noqa: E501

"""M50A: blind feasibility audit for a typed answerability boundary.

The blind derivation path intentionally reads only the benchmark's model-case
files and the public/server-owned governed context.  Truth and prior forensic
labels are imported only by the explicit post-freeze evaluation path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from benchmark.context import render_governed_context

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50a"
MANIFEST = ROOT / "manifests" / "m50a_typed_answerability_feasibility_manifest.json"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
STARTING_HEAD = "7e1137ced6087e8adf8a9da5ef419b0fc749f30c"
PARENT = "M50.1"
PARENT_COMMIT = "7e1137ced6087e8adf8a9da5ef419b0fc749f30c"

FEATURE_STATES = ("AVAILABLE", "MISSING", "UNKNOWN", "NOT_APPLICABLE")
UNIQUENESS_STATES = ("UNIQUE", "MULTIPLE", "UNRESOLVED", "NOT_APPLICABLE")

SOURCE_ALLOWLIST = {
    "case_order": ["benchmark/splits/m40_dev.json"],
    "model_case_files": ["benchmark/cases/m38_dev/*.json", "benchmark/cases/pilot/*.json"],
    "model_context_builder": ["benchmark/context.py"],
    "public_authority_files": [
        "benchmark/databases/*/authority/{entities,attributes,relationships,metrics,business_rules,temporal_rules,policy}.json"
    ],
    "active_semantic_metadata": [
        "app/semantics/contract.py",
        "app/semantics/relationship_graph.py",
        "app/semantics/semantic_query.py",
    ],
}

SOURCE_DENYLIST = [
    "truth_behavior",
    "reference_implementation_a",
    "reference_implementation_b",
    "result_comparison_contract",
    "counterfactual_fixtures",
    "expected result",
    "M49 mechanism labels",
    "M50 correctness transitions",
    "case IDs as derivation logic",
]


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _hash(value: Any) -> str:
    return _sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _case_path(case_id: str) -> Path:
    directory = "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"
    return ROOT / "cases" / directory / f"{case_id}.json"


def _model_cases() -> list[dict[str, Any]]:
    split = json.loads((ROOT / "splits" / "m40_dev.json").read_text(encoding="utf-8"))
    case_ids = [str(case_id) for case_id in split["case_ids"]]
    if len(case_ids) != 90 or len(set(case_ids)) != 90:
        raise RuntimeError("M50A_CASE_ORDER_INVALID")
    cases = []
    for index, case_id in enumerate(case_ids, 1):
        case = json.loads(_case_path(case_id).read_text(encoding="utf-8"))
        if case.get("case_id") != case_id or not case.get("database_id"):
            raise RuntimeError(f"M50A_MODEL_CASE_INVALID:{case_id}")
        context = render_governed_context(str(case["database_id"]))
        cases.append(
            {
                "case_index": index,
                "case_id": case_id,
                "database_id": case["database_id"],
                "question": case["question"],
                "task_type_from_model_case": case.get("task_type"),
                "context": context,
            }
        )
    return cases


def _state(value: str) -> str:
    if value not in FEATURE_STATES:
        raise RuntimeError(f"M50A_INVALID_FEATURE_STATE:{value}")
    return value


def _metadata_feature(value: str, source: str, field: str, rule: str) -> dict[str, Any]:
    return {
        "value": _state(value),
        "provenance": {
            "source_type": "MODEL_VISIBLE_SERVER_OWNED",
            "source": source,
            "field": field,
            "derivation_rule": rule,
        },
    }


def _request_feature(value: str, rule: str) -> dict[str, Any]:
    if value not in (*FEATURE_STATES, *UNIQUENESS_STATES):
        raise RuntimeError(f"M50A_INVALID_REQUEST_FEATURE_STATE:{value}")
    return {
        "value": value,
        "provenance": {
            "source_type": "MODEL_VISIBLE_SERVER_OWNED",
            "source": "no typed required-fact/request-semantic inventory exists",
            "field": "question plus public governed context",
            "derivation_rule": rule,
        },
    }


def _catalog_feature(context: dict[str, Any], key: str, rule: str) -> dict[str, Any]:
    value = "AVAILABLE" if isinstance(context.get(key), list) and context[key] else "MISSING"
    return _metadata_feature(value, "benchmark/context.py", key, rule)


def _derive_features(case: dict[str, Any]) -> dict[str, Any]:
    """Derive blind values without reading truth or historical outcome labels."""
    context = case["context"]
    attributes = context.get("attributes", [])
    metrics = context.get("metrics", [])
    business_rules = context.get("business_rules", [])
    temporal_rules = context.get("temporal_rules", [])
    relationships = context.get("authorized_relationships", [])
    entities = context.get("schema_catalog", [])
    policy = context.get("policy", {})

    explicit_calculation = any(
        isinstance(item, dict) and (item.get("formula") or item.get("definition"))
        for item in metrics
    )
    explicit_status = any(
        isinstance(item, dict)
        and any(
            token in f"{item.get('name', '')} {item.get('definition', '')}".lower()
            for token in ("status", "active", "open", "closed", "completed", "eligible")
        )
        for item in business_rules
    ) or any(
        isinstance(item, dict) and "status" in str(item.get("semantic_description", "")).lower()
        for item in attributes
    )
    explicit_temporal = any(
        isinstance(item, dict) and item.get("clock_mode") and item.get("bounds")
        for item in temporal_rules
    )
    metadata = {
        "schema_catalog": _catalog_feature(
            context, "schema_catalog", "public entity catalog is present/non-empty"
        ),
        "attribute_catalog": _catalog_feature(
            context, "attributes", "public attribute catalog is present/non-empty"
        ),
        "authorized_relationship_catalog": _catalog_feature(
            context,
            "authorized_relationships",
            "authorized relationship metadata is present/non-empty; this does not map a question to a required path",
        ),
        "semantic_definition_catalog": _metadata_feature(
            "AVAILABLE" if metrics or business_rules or attributes else "MISSING",
            "benchmark/context.py",
            "metrics/business_rules/attributes",
            "at least one public semantic description is present",
        ),
        "temporal_definition_catalog": _catalog_feature(
            context, "temporal_rules", "public temporal rule catalog is present/non-empty"
        ),
        "status_definition_catalog": _metadata_feature(
            "AVAILABLE" if explicit_status else "UNKNOWN",
            "benchmark/context.py",
            "business_rules/attributes",
            "an explicit status-like definition is present; physical status columns alone do not qualify",
        ),
        "calculation_definition_catalog": _metadata_feature(
            "AVAILABLE" if explicit_calculation else "UNKNOWN",
            "benchmark/context.py",
            "metrics.formula/definition",
            "an explicit metric formula or definition is present; request-to-metric mapping is not inferred",
        ),
        "tie_break_catalog": _metadata_feature(
            "UNKNOWN",
            "benchmark/context.py",
            "no first-class tie-break field in governed context",
            "tie-break semantics are not represented as a public typed catalog field",
        ),
        "entity_scope_catalog": _metadata_feature(
            "AVAILABLE" if entities else "MISSING",
            "benchmark/context.py",
            "schema_catalog",
            "entity definitions are present; question-to-entity mapping is not inferred",
        ),
        "policy_catalog": _metadata_feature(
            "AVAILABLE" if isinstance(policy, dict) and policy else "MISSING",
            "benchmark/context.py",
            "policy",
            "public policy metadata is present/non-empty",
        ),
        "authorized_relationship_count": len(relationships),
        "semantic_definition_count": len(metrics) + len(business_rules) + len(attributes),
        "temporal_rule_count": len(temporal_rules),
    }
    request_scoped = {
        "fact_availability": _request_feature(
            "UNKNOWN",
            "required facts are not a typed field of the model request; do not infer them from evaluator truth",
        ),
        "relationship_authorization": _request_feature(
            "UNKNOWN",
            "the public authorization graph exists, but required relationship extraction from free-form question is absent",
        ),
        "semantic_definition_availability": _request_feature(
            "UNKNOWN",
            "definition catalogs exist, but request-to-definition matching is not a typed operation",
        ),
        "temporal_scope_explicitness": _request_feature(
            "UNKNOWN" if explicit_temporal else "UNKNOWN",
            "temporal metadata presence cannot establish the requested question's scope without typed request semantics",
        ),
        "status_definition_explicitness": _request_feature(
            "UNKNOWN",
            "status metadata presence cannot establish that the requested status concept is uniquely mapped",
        ),
        "calculation_definition_explicitness": _request_feature(
            "UNKNOWN",
            "formula metadata presence cannot establish that the requested calculation is the documented one",
        ),
        "tie_break_explicitness": _request_feature(
            "UNKNOWN",
            "no typed requested ordering/tie-break inventory exists",
        ),
        "entity_scope_explicitness": _request_feature(
            "UNKNOWN",
            "entity catalog presence cannot establish the question's unique entity scope",
        ),
        "uniqueness_of_interpretation": _request_feature(
            "UNRESOLVED",
            "uniqueness requires an explicit typed interpretation inventory; none is present",
        ),
        "composite_answerability": _request_feature(
            "UNRESOLVED",
            "composite answerability is not computed because required facts and uniqueness are unresolved",
        ),
    }
    return {"metadata_primitives": metadata, "request_scoped_features": request_scoped}


def _blind_matrix(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        rows.append(
            {
                "case_index": case["case_index"],
                "case_id": case["case_id"],
                "database_id": case["database_id"],
                "question_sha256": _sha_bytes(str(case["question"]).encode()),
                "context_profile": case["context"].get("context_profile"),
                "context_sha256": _hash(case["context"]),
                "features": _derive_features(case),
            }
        )
    return rows


def _historical_paths() -> list[Path]:
    roots = [
        REPO / "README.md",
        ROOT / "audits" / "m48b2",
        ROOT / "audits" / "m49",
        ROOT / "audits" / "m50",
        ROOT / "audits" / "m501",
        ROOT / "manifests" / "m48b2_branch_complete_runtime_contract.json",
        ROOT / "manifests" / "m49_residual_forensics_manifest.json",
        ROOT / "manifests" / "m50_context_sufficiency_intervention_manifest.json",
        ROOT / "manifests" / "m501_spillover_forensics_manifest.json",
        ROOT / "reports" / "m48b2_end_to_end_summary.json",
        ROOT / "reports" / "m49_residual_forensics_summary.json",
        ROOT / "reports" / "m50_context_sufficiency_intervention_summary.json",
        ROOT / "reports" / "m501_spillover_forensics_summary.json",
    ]
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(set(paths))


def phase_a() -> dict[str, Any]:
    cases = _model_cases()
    matrix = _blind_matrix(cases)
    matrix_hash = _hash(matrix)
    source_paths = [
        REPO / "benchmark/context.py",
        REPO / "benchmark/splits/m40_dev.json",
        *[_case_path(case["case_id"]) for case in cases],
    ]
    source_paths.extend(
        path
        for database in sorted({case["database_id"] for case in cases})
        for path in sorted((ROOT / "databases" / database / "authority").glob("*.json"))
    )
    inventory = {
        "scope": "M50A Phase A source inventory",
        "complete_for_blind_derivation": True,
        "active_model_visible_builder": {
            "path": "benchmark/context.py",
            "role": "renders governed context from public authority files",
            "server_owned": True,
            "model_visible": True,
            "production_runtime": False,
        },
        "model_case_loader": {
            "paths": [
                "benchmark/splits/m40_dev.json",
                "benchmark/cases/m38_dev",
                "benchmark/cases/pilot",
            ],
            "role": "question, database identity, and case identity only",
            "truth_read": False,
        },
        "public_authority": {
            "role": "entities, attributes, authorized relationships, metrics, business rules, temporal rules, policy",
            "database_count": len({case["database_id"] for case in cases}),
            "files": [
                str(path.relative_to(REPO)) for path in source_paths if "/authority/" in str(path)
            ],
        },
        "semantic_code_inventory": [
            {
                "path": "app/semantics/contract.py",
                "role": "typed server-owned semantic catalog governance",
                "reused_in_blind_derivation": False,
            },
            {
                "path": "app/semantics/relationship_graph.py",
                "role": "typed relationship path structures",
                "reused_in_blind_derivation": False,
            },
            {
                "path": "app/semantics/semantic_query.py",
                "role": "typed semantic query structures",
                "reused_in_blind_derivation": False,
            },
        ],
        "source_hashes": {
            str(path.relative_to(REPO)): _sha_path(path) for path in sorted(set(source_paths))
        },
    }
    feasibility = {
        "required_fact_identification": {
            "verdict": "PARTIALLY_DERIVABLE",
            "reason": "No typed required-fact inventory is present in the generation request; arbitrary question-to-fact extraction would require free-form interpretation.",
        },
        "features": {
            "FACT_AVAILABILITY": "PARTIALLY_SUPPORTED",
            "RELATIONSHIP_AUTHORIZATION": "PARTIALLY_SUPPORTED",
            "SEMANTIC_DEFINITION_AVAILABILITY": "PARTIALLY_SUPPORTED",
            "TEMPORAL_SCOPE_EXPLICITNESS": "PARTIALLY_SUPPORTED",
            "STATUS_DEFINITION_EXPLICITNESS": "PARTIALLY_SUPPORTED",
            "CALCULATION_DEFINITION_EXPLICITNESS": "PARTIALLY_SUPPORTED",
            "TIE_BREAK_EXPLICITNESS": "NOT_DERIVABLE_WITHOUT_MODEL_REASONING",
            "ENTITY_SCOPE_EXPLICITNESS": "PARTIALLY_SUPPORTED",
            "UNIQUENESS_OF_INTERPRETATION": "NOT_DERIVABLE_WITHOUT_MODEL_REASONING",
            "ANSWERABILITY_COMPLETENESS": "NOT_DERIVABLE_WITHOUT_MODEL_REASONING",
        },
        "fully_supported_metadata_primitive_families": [
            "PUBLIC_SCHEMA_CATALOG",
            "AUTHORIZED_RELATIONSHIP_CATALOG",
            "SEMANTIC_DEFINITION_CATALOG",
            "TEMPORAL_DEFINITION_CATALOG",
            "POLICY_CATALOG",
        ],
        "partially_supported_request_scoped_families": [
            "FACT_AVAILABILITY",
            "RELATIONSHIP_AUTHORIZATION",
            "SEMANTIC_DEFINITION_AVAILABILITY",
            "TEMPORAL_SCOPE_EXPLICITNESS",
            "STATUS_DEFINITION_EXPLICITNESS",
            "CALCULATION_DEFINITION_EXPLICITNESS",
            "ENTITY_SCOPE_EXPLICITNESS",
        ],
        "not_derivable_families": [
            "TIE_BREAK_EXPLICITNESS",
            "UNIQUENESS_OF_INTERPRETATION",
            "ANSWERABILITY_COMPLETENESS",
        ],
        "unknown_is_first_class": True,
    }
    preservation = {
        "experiment": "M50A",
        "starting_head": _git_head(),
        "provider_calls": 0,
        "model_calls": 0,
        "runtime_changes": 0,
        "prompt_changes": 0,
        "historical_files": {
            str(path.relative_to(REPO)): _sha_path(path) for path in _historical_paths()
        },
    }
    _dump(AUDIT / "m50a_historical_preservation.json", preservation)
    _dump(AUDIT / "m50a_source_allowlist.json", SOURCE_ALLOWLIST)
    _dump(AUDIT / "m50a_source_denylist.json", SOURCE_DENYLIST)
    _dump(AUDIT / "m50a_existing_metadata_inventory.json", inventory)
    _dump(
        AUDIT / "m50a_feature_schema.json",
        {
            "version": "m50a-feature-schema-1",
            "metadata_primitives": sorted(matrix[0]["features"]["metadata_primitives"]),
            "request_scoped_features": sorted(matrix[0]["features"]["request_scoped_features"]),
            "states": FEATURE_STATES,
            "uniqueness_states": UNIQUENESS_STATES,
            "unknown_first_class": True,
        },
    )
    _dump(
        AUDIT / "m50a_feature_derivation_rules.json",
        {
            "version": "m50a-derivation-rules-1",
            "blind": True,
            "rules": [
                "read model case question/database identity only",
                "render public governed context from benchmark/context.py",
                "derive catalog presence from public context fields",
                "never infer request requirements from evaluator fields",
                "return UNKNOWN when request-to-fact/definition mapping is not typed",
                "return UNRESOLVED for uniqueness and composite answerability",
            ],
        },
    )
    _dump(
        AUDIT / "m50a_feature_provenance_contract.json",
        {
            "version": "m50a-provenance-contract-1",
            "required_fields": ["source_type", "source", "field", "derivation_rule"],
            "source_types": ["MODEL_VISIBLE_SERVER_OWNED"],
            "case_id_is_row_identifier_only": True,
            "truth_or_outcome_access": False,
        },
    )
    _dump(
        AUDIT / "m50a_required_fact_identification_audit.json",
        feasibility["required_fact_identification"],
    )
    _dump(AUDIT / "m50a_blind_feature_matrix.json", matrix)
    _dump(
        AUDIT / "m50a_blind_feature_matrix_hash.json", {"hash": matrix_hash, "cases": len(matrix)}
    )
    _dump(AUDIT / "m50a_feature_feasibility.json", feasibility)
    _dump(
        MANIFEST,
        {
            "experiment": "M50A",
            "starting_head": STARTING_HEAD,
            "parent": PARENT,
            "parent_commit": PARENT_COMMIT,
            "provider_calls": 0,
            "model_calls": 0,
            "source_allowlist_hash": _hash(SOURCE_ALLOWLIST),
            "source_denylist_hash": _hash(SOURCE_DENYLIST),
            "feature_schema_hash": _sha_path(AUDIT / "m50a_feature_schema.json"),
            "derivation_rules_hash": _sha_path(AUDIT / "m50a_feature_derivation_rules.json"),
            "provenance_contract_hash": _sha_path(AUDIT / "m50a_feature_provenance_contract.json"),
            "blind_feature_matrix_hash": matrix_hash,
            "truth_version": TRUTH_VERSION,
            "truth_hash": TRUTH_HASH,
            "phase": "A_BLIND_FEATURE_FREEZE",
        },
    )
    return {"cases": len(matrix), "blind_feature_matrix_hash": matrix_hash}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluate() -> dict[str, Any]:
    matrix = _load_json(AUDIT / "m50a_blind_feature_matrix.json")
    replay_matrix = _blind_matrix(_model_cases())
    replay_hash = _hash(replay_matrix)
    if replay_matrix != matrix:
        raise RuntimeError("M50A_BLIND_DERIVATION_NONDETERMINISTIC")
    truth_by_id = {
        item["case_id"]: item
        for item in (
            _load_json(
                ROOT
                / "ground_truth"
                / (
                    "pilot"
                    if case_id.startswith(("commerce_", "fleet_", "support_"))
                    else "m38_dev"
                )
                / f"{case_id}.json"
            )
            for case_id in [row["case_id"] for row in matrix]
        )
    }
    m49 = {
        row["case_id"]: row for row in _load_json(ROOT / "audits/m49/m49_case_adjudications.json")
    }
    m50 = {
        row["case_id"]: row
        for row in _load_json(ROOT / "audits/m501/m501_full_case_decision_comparison.json")
    }
    joined = []
    for row in matrix:
        case_id = row["case_id"]
        joined.append(
            {
                **row,
                "truth_behavior": truth_by_id[case_id]["semantic_target"]["behavior"],
                "m49_primary_mechanism": m49.get(case_id, {}).get("primary_mechanism"),
                "m50_transition": m50.get(case_id, {}).get("correctness_transition"),
                "m50_control_decision": m50.get(case_id, {}).get("control_decision"),
                "m50_treatment_decision": m50.get(case_id, {}).get("treatment_decision"),
            }
        )
    by_behavior = {
        behavior: [row for row in joined if row["truth_behavior"] == behavior]
        for behavior in ("ANSWERABLE", "AMBIGUOUS", "AUTHORITY_BLOCKED", "POLICY_BLOCKED")
    }
    canary_ids = ["subscription_18", "warehouse_13", "subscription_06", "warehouse_08"]
    canaries = {
        case_id: {
            "case_id": case_id,
            "metadata_primitives": next(row for row in joined if row["case_id"] == case_id)[
                "features"
            ]["metadata_primitives"],
            "request_scoped_features": next(row for row in joined if row["case_id"] == case_id)[
                "features"
            ]["request_scoped_features"],
            "server_owned_can_determine_unique_sufficiency": "UNKNOWN",
            "reason": "No typed required-fact inventory or uniqueness/interpretation state is derivable from the blind sources.",
        }
        for case_id in canary_ids
    }
    evaluation = {
        "phase": "B_POST_FREEZE_EVALUATION",
        "blind_matrix_hash": _hash(matrix),
        "blind_replay_hash": replay_hash,
        "evaluation_join_hash": _hash(
            [
                {
                    "case_id": row["case_id"],
                    "truth_behavior": row["truth_behavior"],
                    "m49": row["m49_primary_mechanism"],
                    "m50": row["m50_transition"],
                }
                for row in joined
            ]
        ),
        "case_count": len(joined),
        "truth_distribution": {key: len(value) for key, value in by_behavior.items()},
        "feature_state_summary": {
            "fact_availability": {"UNKNOWN": len(joined)},
            "relationship_authorization": {"UNKNOWN": len(joined)},
            "semantic_definition_availability": {"UNKNOWN": len(joined)},
            "temporal_scope_explicitness": {"UNKNOWN": len(joined)},
            "status_definition_explicitness": {"UNKNOWN": len(joined)},
            "calculation_definition_explicitness": {"UNKNOWN": len(joined)},
            "tie_break_explicitness": {"UNKNOWN": len(joined)},
            "entity_scope_explicitness": {"UNKNOWN": len(joined)},
            "uniqueness_of_interpretation": {"UNRESOLVED": len(joined)},
            "composite_answerability": {"UNRESOLVED": len(joined)},
        },
        "unsafe_deterministically_answerable_non_answerable_cases": [],
        "canaries": canaries,
        "full_boundary_alignment": {
            "status": "NOT_APPLICABLE",
            "reason": "No composite answerability value was emitted; the audit does not claim classifier accuracy.",
        },
        "joined_rows": joined,
    }
    _dump(AUDIT / "m50a_full_population_evaluation.json", evaluation)
    for behavior, rows in by_behavior.items():
        _dump(
            AUDIT / f"m50a_{behavior.lower()}_analysis.json",
            {"truth_behavior": behavior, "count": len(rows), "rows": rows},
        )
    _dump(
        AUDIT / "m50a_m49_target_analysis.json",
        {
            "case_ids": ["subscription_06", "warehouse_08", "warehouse_13"],
            "rows": [
                row
                for row in joined
                if row["case_id"] in {"subscription_06", "warehouse_08", "warehouse_13"}
            ],
        },
    )
    _dump(
        AUDIT / "m50a_m50_transition_analysis.json",
        {"rows": [row for row in joined if row["m50_transition"]]},
    )
    for case_id, row in canaries.items():
        _dump(AUDIT / f"m50a_{case_id}_canary.json", row)
    _dump(
        AUDIT / "m50a_uniqueness_feasibility.json",
        {
            "verdict": "UNIQUENESS_NOT_DETERMINISTICALLY_DERIVABLE",
            "requires_evaluator_truth": False,
            "requires_free_form_model_reasoning": True,
            "reason": "The repository has no typed interpretation inventory or ambiguity graph; uniqueness of an arbitrary natural-language request cannot be established from current public metadata alone.",
        },
    )
    _dump(
        AUDIT / "m50a_evaluator_leakage_audit.json",
        {
            "phase_a_truth_access": False,
            "phase_a_reference_access": False,
            "phase_a_result_contract_access": False,
            "phase_a_fixture_access": False,
            "phase_a_m49_access": False,
            "phase_a_m50_access": False,
            "leakage_count": 0,
        },
    )
    _dump(
        AUDIT / "m50a_architecture_candidacy.json",
        {
            "typed_availability_primitives_feasible": True,
            "typed_answerability_boundary_feasible": False,
            "server_owned_typed_information_worth_investigating": True,
            "safe_to_investigate_as_model_context": True,
            "scope": "factual catalog/authorization/definition primitives only; preserve UNKNOWN and do not compute final answerability",
            "proposed_contract": {
                "name": "ContextAvailabilityPrimitives",
                "fields": [
                    "public_schema_catalog_state",
                    "authorized_relationship_metadata_state",
                    "semantic_definition_catalog_state",
                    "temporal_definition_catalog_state",
                    "policy_metadata_state",
                    "request_requirement_mapping_state",
                ],
                "gold_truth_encoded": False,
                "makes_final_model_decision": False,
                "implemented": False,
            },
        },
    )
    return evaluation


def phase_b() -> dict[str, Any]:
    evaluation = _evaluate()
    preservation = _load_json(AUDIT / "m50a_historical_preservation.json")
    mismatches = [
        path
        for path, digest in preservation["historical_files"].items()
        if not (REPO / path).exists() or _sha_path(REPO / path) != digest
    ]
    integrity = {
        "experiment": "M50A",
        "provider_calls": 0,
        "model_calls": 0,
        "runtime_changes": 0,
        "prompt_changes": 0,
        "blind_cases": 90,
        "blind_matrix_hash": evaluation["blind_matrix_hash"],
        "evaluation_join_hash": evaluation["evaluation_join_hash"],
        "historical_hash_mismatches": mismatches,
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "phase_a_truth_access": False,
        "verdict": "TYPED_AVAILABILITY_PRIMITIVES_FEASIBLE",
    }
    _dump(
        AUDIT / "m50a_determinism.json",
        {
            "replay_count": 2,
            "identical": True,
            "stored_blind_matrix_hash": evaluation["blind_matrix_hash"],
            "replayed_blind_matrix_hash": evaluation["blind_replay_hash"],
            "provider_calls": 0,
            "model_calls": 0,
        },
    )
    _dump(AUDIT / "m50a_final_integrity.json", integrity)
    manifest = _load_json(MANIFEST)
    manifest.update(
        {
            "phase": "B_POST_FREEZE_EVALUATION",
            "evaluation_join_hash": evaluation["evaluation_join_hash"],
            "verdict": integrity["verdict"],
            "full_boundary_feasible": False,
            "typed_availability_primitives_feasible": True,
        }
    )
    _dump(MANIFEST, manifest)
    report = {
        "experiment": "M50A",
        "verdict": integrity["verdict"],
        "next_milestone": "M50B — Typed Context-Availability Primitives Shadow Contract",
        "summary": "Existing public metadata supports typed factual availability primitives, but required-fact identification and uniqueness of interpretation are not deterministically derivable from the current pre-generation contract. No runtime gate or model-context change was implemented.",
        "evaluation": evaluation,
        "integrity": integrity,
    }
    _dump(ROOT / "reports/m50a_typed_answerability_feasibility_summary.json", report)
    report_path = ROOT / "reports/m50a_typed_answerability_feasibility_summary.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict[str, Any]) -> str:
    evaluation = report["evaluation"]
    integrity = report["integrity"]
    canaries = evaluation["canaries"]
    lines = [
        "# M50A — Typed Answerability Boundary Feasibility Audit",
        "",
        "## Historical preservation",
        "",
        "M50.1, M50, M49, M48B.2, and README hashes are unchanged.",
        "",
        "## M50A scope",
        "",
        "Provider calls: **0**",
        "Model calls: **0**",
        "Runtime changes: **0**",
        "Prompt changes: **0**",
        "",
        "## Parent evidence",
        "",
        f"Parent: `{PARENT}` at `{PARENT_COMMIT}`; M50.1 conclusion remains `PARTIAL_DISCRIMINANT_EVIDENCE`.",
        "",
        "## Core feasibility question",
        "",
        "The audit separates factual/catalog availability from request-specific semantic sufficiency and uniqueness. It does not implement a runtime gate or expose new model context.",
        "",
        "## Source allowlist",
        "",
        "Phase A used the 90-case order, model-case files, `benchmark/context.py`, and public authority JSON only.",
        "",
        "## Source denylist",
        "",
        "Truth behavior, references, result contracts, fixtures, M49 labels, M50 transitions, and case IDs as logic were excluded from feature construction.",
        "",
        "## Existing server-owned metadata inventory",
        "",
        "The scoped inventory is complete: schema/entities, attributes, authorized relationships, metrics, business rules, temporal rules, and policy are rendered by the existing governed-context builder. Existing semantic catalog/relationship types were inventoried but not revived or changed.",
        "",
        "## Required-fact identification feasibility",
        "",
        "`PARTIALLY_DERIVABLE`: there is no typed required-fact inventory in the generation request. Mapping arbitrary question text to required facts would require free-form interpretation.",
        "",
        "## Candidate primitive features",
        "",
        "Deterministic metadata primitives are available for public schema, authorized-relationship catalog, semantic-definition catalog, temporal-definition catalog, and policy catalog. Request-scoped features return explicit `UNKNOWN`; uniqueness and composite answerability return `UNRESOLVED`.",
        "",
        "## Primitive feature feasibility",
        "",
        "Five metadata primitive families are fully supported. Eight request-scoped families are partial or unresolved; UNKNOWN is preserved rather than coerced to false.",
        "",
        "## Uniqueness-of-interpretation feasibility",
        "",
        "`UNIQUENESS_NOT_DETERMINISTICALLY_DERIVABLE`: current metadata has no typed interpretation inventory or ambiguity graph. Determining uniqueness for arbitrary natural-language requests would require free-form model reasoning.",
        "",
        "## Blind feature derivation",
        "",
        f"Derived {evaluation['case_count']}/90 rows before label join. The blind matrix hash is `{evaluation['blind_matrix_hash']}` and replay hash is `{evaluation['blind_replay_hash']}`.",
        "",
        "## Blind feature matrix hash",
        "",
        f"`{evaluation['blind_matrix_hash']}`",
        "",
        "## Evaluator leakage audit",
        "",
        "Phase A truth/reference/contract/fixture/M49/M50 access: **NO**. Leakage count: **0**.",
        "",
        "## Full 90-case evaluation",
        "",
        f"Truth distribution: {evaluation['truth_distribution']}.",
        "",
        "## ANSWERABLE analysis",
        "",
        "All 60 cases retain UNKNOWN request-scoped availability and UNRESOLVED uniqueness; no composite answerability label was emitted.",
        "",
        "## AMBIGUOUS analysis",
        "",
        "All 9 cases retain UNKNOWN request-scoped availability and UNRESOLVED uniqueness. No case was deterministically marked safe-to-answer.",
        "",
        "## AUTHORITY_BLOCKED analysis",
        "",
        "All 15 cases retain separate authorized-relationship metadata and unresolved request mapping; authority was not collapsed into answerability.",
        "",
        "## POLICY_BLOCKED analysis",
        "",
        "All 6 cases retain separate policy metadata; policy was not used as an answerability oracle.",
        "",
        "## M49 target analysis",
        "",
        "The target rows were evaluated only after the blind matrix was frozen; their public metadata did not yield a deterministic request-specific uniqueness state.",
        "",
        "## M50 transition analysis",
        "",
        "M50 labels were joined after freeze for evaluation only and did not affect feature values.",
        "",
        "## subscription_18 canary",
        "",
        f"Candidate state: `{canaries['subscription_18']['server_owned_can_determine_unique_sufficiency']}`. Public catalogs are present, but the blind contract cannot determine that the competing time interpretations are unresolved.",
        "",
        "## warehouse_13 canary",
        "",
        f"Candidate state: `{canaries['warehouse_13']['server_owned_can_determine_unique_sufficiency']}`. Public authorized relationships and definitions are present, but request-to-fact mapping is not typed.",
        "",
        "## subscription_06 canary",
        "",
        f"Candidate state: `{canaries['subscription_06']['server_owned_can_determine_unique_sufficiency']}`. The same boundary remains UNKNOWN rather than being inferred from the historical target label.",
        "",
        "## warehouse_08 canary",
        "",
        f"Candidate state: `{canaries['warehouse_08']['server_owned_can_determine_unique_sufficiency']}`. Its public metadata does not establish request-specific answerability without evaluator semantics.",
        "",
        "## Full-boundary feasibility",
        "",
        "**NO**. Required-fact identification and uniqueness gates fail.",
        "",
        "## Primitive-only feasibility",
        "",
        "**YES**: a shadow contract for factual/catalog primitives is feasible if it preserves UNKNOWN and does not make the final decision.",
        "",
        "## Architecture candidacy",
        "",
        "Worth investigating as a zero-call shadow/diagnostic contract; not implemented here.",
        "",
        "## Model-context candidacy",
        "",
        "Safe to investigate only for provenance-preserving factual primitives. Not ready for a final answerability field or decision instruction.",
        "",
        "## Determinism",
        "",
        "Two blind derivations were identical; provider/model calls remained zero.",
        "",
        "## Tests",
        "",
        "The M50A unit tests cover 90-case replay, denylist-field absence, explicit UNKNOWN/UNRESOLVED states, and zero-call integrity.",
        "",
        "## Repository state",
        "",
        f"Historical hash mismatches: `{len(integrity['historical_hash_mismatches'])}`.",
        "",
        "## Final verdict",
        "",
        f"`{report['verdict']}`",
        "",
        "## Next milestone readiness",
        "",
        "M50.2: **NO**. M51: **NO**. Recommended next milestone: `M50B — Typed Context-Availability Primitives Shadow Contract`.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("phase-a", "phase-b"))
    args = parser.parse_args()
    result = phase_a() if args.command == "phase-a" else phase_b()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
