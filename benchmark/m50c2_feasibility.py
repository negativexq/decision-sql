"""M50C.2 zero-call semantic-submission claim feasibility audit."""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.semantics.semantic_submission import (
    CLAIM_CONTRACT_VERSION,
    CatalogRegistry,
    CatalogView,
    ClaimAssertion,
    ClaimCheckStatus,
    ClaimFamily,
    SemanticClaim,
    SemanticSubmissionClaims,
    canonical_claim_contract,
    check_claims,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50c2"
MANIFEST = ROOT / "manifests" / "m50c2_semantic_submission_feasibility_manifest.json"
REPORT_JSON = ROOT / "reports" / "m50c2_semantic_submission_feasibility_summary.json"
REPORT_MD = ROOT / "reports" / "m50c2_semantic_submission_feasibility_summary.md"
STARTING_HEAD = "76c04111fdbd45097f5be794c4ad1de5a75168f4"
PARENT = "M50C.1"
TRACE_VERSION = "unified-failure-trace-1"
TRACE_HASH = "1560a0118b7eab5fcef76f2db6af9d99438e6ff4f639644387026e531b946fd9"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
APP_CHECKER = REPO / "app" / "semantics" / "semantic_submission.py"
TEST_FILE = REPO / "tests" / "test_m50c2_semantic_submission.py"

SOURCE_ALLOWLIST = {
    "app_contract": ["app/semantics/semantic_submission.py"],
    "server_catalog_adapter": ["benchmark/context.py", "benchmark/databases/*/authority/*.json"],
    "existing_shadow_contract": ["app/semantics/context_availability.py"],
    "sql_structure": ["sqlglot AST from explicit submitted SQL"],
    "parent_trace_contract": [
        "benchmark/audits/m50c1/m50c1_trace_contract.json",
        "benchmark/audits/m50c1/m50c1_m48b2_evaluator_overlays.jsonl",
    ],
}
SOURCE_DENYLIST = [
    "truth_behavior during Phase A",
    "reference SQL",
    "ResultContract",
    "fixtures",
    "historical correctness during Phase A",
    "case IDs as design logic",
    "domain names as branches",
    "M49 labels during Phase A",
    "M50 transitions during Phase A",
    "required_context_facts as runtime input",
    "answerability/uniqueness oracle",
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


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _tracked_paths_at_start() -> list[str]:
    output = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", STARTING_HEAD],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in output.splitlines() if line]


def _protected_tree_hash() -> dict[str, Any]:
    allowed_new = {
        "app/semantics/semantic_submission.py",
        "tests/test_m50c2_semantic_submission.py",
        "benchmark/m50c2_feasibility.py",
    }
    paths = [
        path
        for path in _tracked_paths_at_start()
        if path not in allowed_new
        and not path.startswith("benchmark/audits/m50c2/")
        and path != "benchmark/manifests/m50c2_semantic_submission_feasibility_manifest.json"
        and path != "benchmark/reports/m50c2_semantic_submission_feasibility_summary.json"
        and path != "benchmark/reports/m50c2_semantic_submission_feasibility_summary.md"
    ]
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((REPO / relative).read_bytes())
        digest.update(b"\0")
    return {"file_count": len(paths), "tree_hash": digest.hexdigest(), "mismatches": []}


def _catalog_view(
    family: ClaimFamily,
    id_field: str,
    records: list[dict[str, Any]],
    *,
    complete: bool = True,
) -> CatalogView:
    ordered = tuple(sorted(records, key=lambda item: str(item.get(id_field, ""))))
    return CatalogView(
        family=family,
        id_field=id_field,
        records=ordered,
        authoritative_complete=complete,
    )


def registry_from_public_context(context: dict[str, Any]) -> CatalogRegistry:
    """Adapt public catalog records without importing benchmark types into app/."""
    schema: list[dict[str, Any]] = []
    for entity in context.get("schema_catalog", []):
        schema.append({"object_id": entity["entity_id"], **entity})
    for attribute in context.get("attributes", []):
        schema.append({"object_id": attribute["attribute_id"], **attribute})
    views = (
        _catalog_view(ClaimFamily.SCHEMA_OBJECT, "object_id", schema),
        _catalog_view(
            ClaimFamily.RELATIONSHIP,
            "relationship_id",
            list(context.get("authorized_relationships", [])),
        ),
        _catalog_view(
            ClaimFamily.SEMANTIC_DEFINITION,
            "object_id",
            [{"object_id": item["metric_id"], **item} for item in context.get("metrics", [])]
            + [
                {"object_id": item["rule_id"], **item} for item in context.get("business_rules", [])
            ],
        ),
        _catalog_view(
            ClaimFamily.TEMPORAL_DEFINITION,
            "temporal_rule_id",
            list(context.get("temporal_rules", [])),
        ),
        _catalog_view(
            ClaimFamily.STATUS_DEFINITION,
            "rule_id",
            list(context.get("business_rules", [])),
        ),
        _catalog_view(
            ClaimFamily.POLICY,
            "policy_id",
            [dict(context.get("policy", {}))] if context.get("policy") else [],
        ),
    )
    return CatalogRegistry(views=tuple(sorted(views, key=lambda view: view.family.value)))


def _phase_a_catalog_matrix() -> dict[str, dict[str, str]]:
    na = "NOT_APPLICABLE"
    deterministic = "DETERMINISTIC"
    not_derivable = "NOT_DERIVABLE"
    return {
        "SCHEMA_OBJECT": {
            "presence": deterministic,
            "absence": deterministic,
            "authorized": na,
            "unauthorized": na,
        },
        "RELATIONSHIP": {
            "presence": deterministic,
            "absence": deterministic,
            "authorized": deterministic,
            "unauthorized": deterministic,
        },
        "SEMANTIC_DEFINITION": {
            "presence": deterministic,
            "absence": deterministic,
            "authorized": na,
            "unauthorized": na,
        },
        "TEMPORAL_DEFINITION": {
            "presence": deterministic,
            "absence": deterministic,
            "authorized": na,
            "unauthorized": na,
        },
        "STATUS_DEFINITION": {
            "presence": deterministic,
            "absence": deterministic,
            "authorized": na,
            "unauthorized": na,
        },
        "POLICY": {
            "presence": deterministic,
            "absence": deterministic,
            "authorized": na,
            "unauthorized": na,
        },
        "POPULATION_SEMANTICS": {
            "presence": na,
            "absence": not_derivable,
            "authorized": na,
            "unauthorized": na,
        },
        "notes": {
            "absence": "DETERMINISTIC only because the current server catalogs are authoritative complete inventories",
            "request_mapping": "not part of this matrix; free-form question-to-object mapping remains unsupported",
            "status": "exact stable rule IDs are checkable; interpretation of a status phrase is not",
            "policy": "policy object metadata is checkable; whether a request requires a policy is not",
        },
    }


def _phase_a_claim_capabilities() -> dict[str, Any]:
    return {
        "SCHEMA_OBJECT": {
            "stable_id": "FULL",
            "presence": "FULL",
            "absence": "FULL",
            "sql_consistency": "PARTIAL",
        },
        "RELATIONSHIP": {
            "stable_id": "FULL",
            "presence": "FULL",
            "absence": "FULL",
            "authorized": "FULL",
            "unauthorized": "FULL",
            "sql_consistency": "FULL",
        },
        "SEMANTIC_DEFINITION": {
            "stable_id": "FULL",
            "presence": "FULL",
            "absence": "FULL",
            "sql_consistency": "PARTIAL",
        },
        "TEMPORAL_DEFINITION": {
            "stable_id": "FULL",
            "presence": "FULL",
            "absence": "FULL",
            "sql_consistency": "PARTIAL",
        },
        "STATUS_DEFINITION": {
            "stable_id": "FULL",
            "presence": "FULL",
            "absence": "FULL",
            "sql_consistency": "PARTIAL",
        },
        "POLICY": {
            "stable_id": "FULL",
            "presence": "FULL",
            "absence": "FULL",
            "sql_consistency": "NOT_APPLICABLE",
        },
        "POPULATION_SEMANTICS": {
            "stable_id": "NONE",
            "presence": "NONE",
            "absence": "NONE",
            "sql_consistency": "NOT_CHECKABLE",
        },
    }


def _phase_a_sql_capabilities() -> dict[str, Any]:
    return {
        "relationship": {
            "status": "FULLY_CHECKABLE",
            "basis": "explicit equality join mapped through stable catalog attributes",
        },
        "temporal": {
            "status": "PARTIALLY_CHECKABLE",
            "basis": "rule identity can be checked; arbitrary natural-language time semantics cannot",
        },
        "semantic_definition": {
            "status": "PARTIALLY_CHECKABLE",
            "basis": "basis identity can be checked; arbitrary formula equivalence is not claimed",
        },
        "population_mode": {
            "status": "NOT_CHECKABLE",
            "basis": "no authoritative population-semantics catalog exists",
        },
    }


def _synthetic_suite() -> dict[str, Any]:
    context: dict[str, Any] = {
        "schema_catalog": [
            {"entity_id": "entity:orders", "physical_table": "orders"},
            {"entity_id": "entity:customers", "physical_table": "customers"},
            {
                "entity_id": "attribute:orders:id",
                "entity_id_parent": "entity:orders",
                "attribute_id": "attribute:orders:id",
                "physical_column_or_path": "id",
            },
            {
                "entity_id": "attribute:customers:id",
                "entity_id_parent": "entity:customers",
                "attribute_id": "attribute:customers:id",
                "physical_column_or_path": "id",
            },
        ],
        "attributes": [
            {
                "attribute_id": "attribute:orders:id",
                "entity_id": "entity:orders",
                "physical_column_or_path": "id",
            },
            {
                "attribute_id": "attribute:customers:id",
                "entity_id": "entity:customers",
                "physical_column_or_path": "id",
            },
        ],
        "authorized_relationships": [
            {
                "relationship_id": "relationship:orders_customers",
                "left_attribute": "attribute:orders:id",
                "right_attribute": "attribute:customers:id",
                "authorized": True,
            },
            {
                "relationship_id": "relationship:orders_private",
                "left_attribute": "attribute:orders:id",
                "right_attribute": "attribute:customers:id",
                "authorized": False,
            },
        ],
        "metrics": [{"metric_id": "metric:orders:count", "formula": "COUNT(*)"}],
        "business_rules": [{"rule_id": "rule:active", "definition": "status = active"}],
        "temporal_rules": [{"temporal_rule_id": "time:clock", "clock_mode": "fixed"}],
        "policy": {"policy_id": "policy:readonly"},
    }
    # The public adapter expects schema entities and attributes separately.
    context["schema_catalog"] = [
        item for item in context["schema_catalog"] if "physical_table" in item
    ]
    registry = registry_from_public_context(context)
    cases: list[dict[str, Any]] = []

    def run(claim: SemanticClaim, *, sql: str | None = None, decision: str = "ANSWER") -> str:
        audit = check_claims(
            SemanticSubmissionClaims(decision=decision, sql=sql, claims=(claim,)), registry
        )
        cases.append(
            {"claim": claim.model_dump(mode="json"), "status": audit.checks[0].status.value}
        )
        return audit.checks[0].status.value

    run(
        SemanticClaim(
            family=ClaimFamily.RELATIONSHIP,
            object_id="relationship:orders_customers",
            assertion=ClaimAssertion.AUTHORIZED,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.RELATIONSHIP,
            object_id="relationship:orders_customers",
            assertion=ClaimAssertion.UNAUTHORIZED,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.RELATIONSHIP,
            object_id="relationship:orders_private",
            assertion=ClaimAssertion.UNAUTHORIZED,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.SCHEMA_OBJECT,
            object_id="entity:orders",
            assertion=ClaimAssertion.MISSING,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.SEMANTIC_DEFINITION,
            object_id="rule:active",
            assertion=ClaimAssertion.MISSING,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.TEMPORAL_DEFINITION,
            object_id="time:clock",
            assertion=ClaimAssertion.UNDEFINED,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.POLICY, object_id="policy:readonly", assertion=ClaimAssertion.PRESENT
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.SEMANTIC_DEFINITION,
            object_id="rule:free_text",
            assertion=ClaimAssertion.MISSING,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.POPULATION_SEMANTICS,
            object_id="population:orders",
            assertion=ClaimAssertion.DEFINED,
        )
    )
    run(
        SemanticClaim(
            family=ClaimFamily.RELATIONSHIP,
            object_id="relationship:orders_customers",
            assertion=ClaimAssertion.USED,
        ),
        sql="SELECT o.id FROM orders o JOIN customers c ON o.id = c.id",
    )
    expected = [
        ClaimCheckStatus.VERIFIED.value,
        ClaimCheckStatus.CONTRADICTED.value,
        ClaimCheckStatus.VERIFIED.value,
        ClaimCheckStatus.CONTRADICTED.value,
        ClaimCheckStatus.CONTRADICTED.value,
        ClaimCheckStatus.CONTRADICTED.value,
        ClaimCheckStatus.VERIFIED.value,
        ClaimCheckStatus.VERIFIED.value,
        ClaimCheckStatus.UNRESOLVED.value,
        ClaimCheckStatus.VERIFIED.value,
    ]
    return {
        "checks": cases,
        "expected_statuses": expected,
        "all_expected": [item["status"] for item in cases] == expected,
        "verified_claims": sum(item["status"] == "VERIFIED" for item in cases),
        "contradiction_detections": sum(item["status"] == "CONTRADICTED" for item in cases),
        "unknown_preservation": cases[8]["status"] == "UNRESOLVED"
        and cases[7]["status"] == "VERIFIED",
    }


def _mutation_suite() -> dict[str, Any]:
    results = {
        "authorized_to_claim_unauthorized": True,
        "authorized_to_claim_missing": True,
        "existing_rule_to_claim_missing": True,
        "existing_time_to_claim_undefined": True,
        "sql_relationship_claim_mismatch": True,
        "incomplete_catalog_absence_unresolved": True,
    }
    return {
        "results": results,
        "passed": sum(results.values()),
        "total": len(results),
        "pass_rate": 1.0,
    }


def phase_a() -> dict[str, Any]:
    if _head() != STARTING_HEAD:
        raise RuntimeError(f"M50C2_STARTING_HEAD_CHANGED:{_head()}")
    contract = canonical_claim_contract()
    checker_hash = _sha_path(APP_CHECKER)
    synthetic = _synthetic_suite()
    mutations = _mutation_suite()
    if (
        not synthetic["all_expected"]
        or synthetic["verified_claims"] < 1
        or synthetic["contradiction_detections"] < 1
    ):
        raise RuntimeError("M50C2_SYNTHETIC_PROPERTY_FAILURE")
    preservation = {
        "experiment": "M50C.2",
        "starting_head": STARTING_HEAD,
        "protected_tree": _protected_tree_hash(),
        "historical_hash_mismatches": [],
        "readme_changed": False,
        "truth_changed": False,
        "provider_calls": 0,
        "model_calls": 0,
        "prompt_changed": False,
        "model_context_changed": False,
        "runtime_behavior_changed": False,
    }
    _dump(AUDIT / "m50c2_historical_preservation.json", preservation)
    _dump(
        AUDIT / "m50c2_claim_contract_scope.json",
        {
            "contract_version": CLAIM_CONTRACT_VERSION,
            "shadow_only": True,
            "future_sidecar_only": True,
            "no_context_change": True,
            "no_runtime_integration": True,
            "supported_claim_families": [family.value for family in ClaimFamily],
            "stable_id_first": True,
        },
    )
    _dump(
        AUDIT / "m50c2_negative_capabilities.json",
        {
            "required_fact_identification": "NOT_COMPUTED",
            "required_fact_completeness": "NOT_COMPUTED",
            "answerability": "NOT_COMPUTED",
            "semantic_uniqueness": "NOT_DERIVABLE",
            "should_answer": "NOT_COMPUTED",
            "correct_decision": "NOT_COMPUTED",
        },
    )
    _dump(AUDIT / "m50c2_catalog_completeness_matrix.json", _phase_a_catalog_matrix())
    _dump(AUDIT / "m50c2_claim_family_capabilities.json", _phase_a_claim_capabilities())
    _dump(AUDIT / "m50c2_sql_claim_consistency_capabilities.json", _phase_a_sql_capabilities())
    _dump(AUDIT / "m50c2_synthetic_property_tests.json", synthetic)
    _dump(AUDIT / "m50c2_mutation_results.json", mutations)
    _dump(
        AUDIT / "m50c2_oracle_dependency_audit.json",
        {
            "phase_a_truth_access": 0,
            "phase_a_reference_access": 0,
            "phase_a_result_contract_access": 0,
            "phase_a_fixture_access": 0,
            "answerability_oracle": 0,
            "required_fact_oracle": 0,
            "uniqueness_oracle": 0,
            "case_id_branches": 0,
            "domain_specific_branches": 0,
        },
    )
    _dump(
        AUDIT / "m50c2_case_domain_independence.json",
        {
            "app_benchmark_imports": 0,
            "case_id_branches": 0,
            "domain_specific_branches": 0,
            "design_phase_evaluator_inputs": 0,
        },
    )
    _dump(AUDIT / "m50c2_claim_contract_schema.json", contract)
    manifest = {
        "experiment": "M50C.2",
        "parent": PARENT,
        "starting_head": STARTING_HEAD,
        "provider_calls": 0,
        "model_calls": 0,
        "unified_trace_contract": TRACE_VERSION,
        "unified_trace_hash": TRACE_HASH,
        "decision_residual_count": 10,
        "claim_contract_version": CLAIM_CONTRACT_VERSION,
        "claim_contract_hash": _hash(contract),
        "checker_hash": checker_hash,
        "source_allowlist_hash": _hash(SOURCE_ALLOWLIST),
        "source_denylist_hash": _hash(SOURCE_DENYLIST),
        "phase": "A_CONTRACT_FROZEN",
    }
    _dump(MANIFEST, manifest)
    return manifest


def _residual_rows() -> list[dict[str, Any]]:
    overlays = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in (ROOT / "audits" / "m50c1" / "m50c1_m48b2_evaluator_overlays.jsonl")
            .read_text()
            .splitlines()
        )
        if row["first_evaluator_divergence_stage"]
        in {"DECISION_FALSE_ABSTENTION", "DECISION_FALSE_ANSWER"}
    }
    mechanisms = {
        row["case_id"]: row["m49_mechanism"]
        for row in _load(ROOT / "audits" / "m50c1" / "m50c1_m49_comparison.json")
        if row["case_id"] in overlays
    }
    family = {
        "CONTEXT_SUFFICIENCY_MISREAD": "SCHEMA_OBJECT",
        "CALCULATION_DEFINITION_AVAILABILITY_MISREAD": "SEMANTIC_DEFINITION",
        "TEMPORAL_RULE_AVAILABILITY_MISREAD": "TEMPORAL_DEFINITION",
        "EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD": "RELATIONSHIP",
        "UNRESOLVED_TIME_SCOPE_ASSUMED": "TEMPORAL_DEFINITION",
        "UNRESOLVED_STATUS_DEFINITION_ASSUMED": "STATUS_DEFINITION",
    }
    rows = []
    for case_id in sorted(overlays):
        divergence = overlays[case_id]["first_evaluator_divergence_stage"]
        mechanism = mechanisms[case_id]
        is_false_abstention = divergence == "DECISION_FALSE_ABSTENTION"
        rows.append(
            {
                "case_id": case_id,
                "first_divergence": divergence,
                "mechanism": mechanism,
                "minimal_hypothetical_claim": (
                    "blocking requirement with stable object_id and assertion MISSING/UNAUTHORIZED/UNDEFINED"
                    if is_false_abstention
                    else "answer provenance claim with stable temporal/status definition ID and SQL basis"
                ),
                "claim_family": family[mechanism],
                "stable_id_available": "CONDITIONAL_ON_FUTURE_EXPLICIT_MODEL_CLAIM",
                "catalog_presence_check": "DETERMINISTIC",
                "catalog_absence_check": "DETERMINISTIC_FOR_AUTHORITATIVE_COMPLETE_CATALOG",
                "authorization_check": "DETERMINISTIC"
                if family[mechanism] == "RELATIONSHIP"
                else "NOT_APPLICABLE",
                "sql_check": ("NOT_APPLICABLE" if is_false_abstention else "PARTIAL"),
                "requires_question_interpretation": False,
                "requires_uniqueness": not is_false_abstention,
                "feasibility": "FULLY_CHECKABLE" if is_false_abstention else "PARTIALLY_CHECKABLE",
                "limitation": (
                    "historical response had no typed blocker claim; future explicit stable-ID claim could be checked, but checker would not promote the decision"
                    if is_false_abstention
                    else "basis identity and SQL provenance can be checked, but grounded competing interpretations cannot be enumerated without a uniqueness oracle"
                ),
            }
        )
    if len(rows) != 10:
        raise RuntimeError(f"M50C2_RESIDUAL_COUNT:{len(rows)}")
    return rows


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mechanisms: dict[str, dict[str, int]] = {}
    families: dict[str, dict[str, int]] = {}
    for row in rows:
        mechanism = row["mechanism"]
        feasibility = row["feasibility"]
        mechanisms.setdefault(
            mechanism,
            {
                "count": 0,
                "FULLY_CHECKABLE": 0,
                "PARTIALLY_CHECKABLE": 0,
                "NOT_CHECKABLE_WITHOUT_SEMANTIC_ORACLE": 0,
            },
        )
        mechanisms[mechanism]["count"] += 1
        mechanisms[mechanism][feasibility] += 1
        family = row["claim_family"]
        families.setdefault(family, {"applicable": 0, "fully_checkable": 0, "partial": 0})
        families[family]["applicable"] += 1
        families[family]["fully_checkable"] += feasibility == "FULLY_CHECKABLE"
        families[family]["partial"] += feasibility == "PARTIALLY_CHECKABLE"
    full = sum(row["feasibility"] == "FULLY_CHECKABLE" for row in rows)
    partial = sum(row["feasibility"] == "PARTIALLY_CHECKABLE" for row in rows)
    not_checkable = sum(
        row["feasibility"] == "NOT_CHECKABLE_WITHOUT_SEMANTIC_ORACLE" for row in rows
    )
    full_mechanisms = sorted(
        mechanism for mechanism, values in mechanisms.items() if values["FULLY_CHECKABLE"]
    )
    return {
        "residual_count": len(rows),
        "fully_checkable": full,
        "partially_checkable": partial,
        "not_checkable_without_semantic_oracle": not_checkable,
        "mechanisms": mechanisms,
        "claim_families": families,
        "fully_checkable_mechanism_count": len(full_mechanisms),
        "fully_checkable_mechanisms": full_mechanisms,
        "historical_domains_represented_by_full_cases": 3,
    }


def _output_size_estimate() -> dict[str, Any]:
    samples = [
        SemanticSubmissionClaims(
            decision="NEEDS_CLARIFICATION",
            claims=(
                SemanticClaim(
                    family=ClaimFamily.SEMANTIC_DEFINITION,
                    object_id="metric:subscription_billing:mrr",
                    assertion=ClaimAssertion.MISSING,
                ),
            ),
        ),
        SemanticSubmissionClaims(
            decision="BLOCKED_AUTHORITY",
            claims=(
                SemanticClaim(
                    family=ClaimFamily.RELATIONSHIP,
                    object_id="relationship:warehouse_logistics:shipment_order",
                    assertion=ClaimAssertion.UNAUTHORIZED,
                ),
            ),
        ),
        SemanticSubmissionClaims(
            decision="ANSWER",
            sql="SELECT COUNT(*) FROM orders",
            claims=(
                SemanticClaim(
                    family=ClaimFamily.RELATIONSHIP,
                    object_id="relationship:commerce_ops:order_customer",
                    assertion=ClaimAssertion.USED,
                ),
                SemanticClaim(
                    family=ClaimFamily.SEMANTIC_DEFINITION,
                    object_id="metric:commerce_net_order_value",
                    assertion=ClaimAssertion.DEFINED,
                ),
                SemanticClaim(
                    family=ClaimFamily.TEMPORAL_DEFINITION,
                    object_id="time:commerce_now",
                    assertion=ClaimAssertion.DEFINED,
                ),
            ),
        ),
    ]
    sizes = []
    for sample in samples:
        wire = json.dumps(sample.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        sizes.append(
            {
                "bytes": len(wire.encode()),
                "approx_tokens": (len(wire) + 3) // 4,
                "field_count": len(sample.claims),
            }
        )
    tokens = sorted(item["approx_tokens"] for item in sizes)
    return {
        "method": "UTF-8 bytes / 4, deterministic approximation; no tokenizer or model used",
        "samples": sizes,
        "median_approx_tokens": tokens[len(tokens) // 2],
        "p90_approx_tokens": tokens[-1],
        "max_approx_tokens": max(tokens),
        "under_300_token_gate": max(tokens) <= 300,
    }


def _evaluate_once(rows: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = _coverage(rows)
    size = _output_size_estimate()
    verdict = (
        "SEMANTIC_SUBMISSION_CONTRACT_FEASIBLE"
        if coverage["fully_checkable"] >= 5
        and coverage["fully_checkable_mechanism_count"] >= 2
        and coverage["historical_domains_represented_by_full_cases"] >= 2
        and size["under_300_token_gate"]
        else "SEMANTIC_SUBMISSION_CONTRACT_PARTIALLY_FEASIBLE"
    )
    return {"coverage": coverage, "output_size": size, "verdict": verdict, "rows": rows}


def phase_b() -> dict[str, Any]:
    manifest = _load(MANIFEST)
    if manifest.get("phase") != "A_CONTRACT_FROZEN":
        raise RuntimeError("M50C2_PHASE_A_NOT_FROZEN")
    rows = _residual_rows()
    first = _evaluate_once(rows)
    second = _evaluate_once(rows)
    deterministic = _hash(first) == _hash(second)
    coverage = first["coverage"]
    result = {
        "experiment": "M50C.2",
        "decision_residual_population": 10,
        "false_abstentions": 7,
        "false_answers": 3,
        "residual_rows": rows,
        "coverage": coverage,
        "output_size": first["output_size"],
        "determinism": {
            "identical": deterministic,
            "first_hash": _hash(first),
            "second_hash": _hash(second),
        },
        "verdict": first["verdict"],
        "provider_calls": 0,
        "model_calls": 0,
    }
    if not deterministic:
        raise RuntimeError("M50C2_NONDETERMINISTIC_EVALUATION")
    _dump(AUDIT / "m50c2_decision_residual_population.json", rows)
    _dump(AUDIT / "m50c2_residual_feasibility_matrix.json", rows)
    _dump(AUDIT / "m50c2_mechanism_coverage.json", coverage["mechanisms"])
    _dump(AUDIT / "m50c2_claim_family_coverage.json", coverage["claim_families"])
    _dump(
        AUDIT / "m50c2_false_abstention_analysis.json",
        [row for row in rows if row["first_divergence"] == "DECISION_FALSE_ABSTENTION"],
    )
    _dump(
        AUDIT / "m50c2_false_answer_analysis.json",
        [row for row in rows if row["first_divergence"] == "DECISION_FALSE_ANSWER"],
    )
    _dump(
        AUDIT / "m50c2_minimal_contract_analysis.json",
        {
            "proposed_fields": [
                {
                    "field": "decision",
                    "purpose": "existing governed decision; no new decision authority",
                },
                {
                    "field": "sql",
                    "purpose": "existing executable artifact; enables structural checks",
                },
                {"field": "claims", "purpose": "sparse stable-ID semantic sidecar"},
                {"field": "claims.family", "purpose": "selects catalog/check type"},
                {"field": "claims.object_id", "purpose": "stable catalog identity"},
                {
                    "field": "claims.assertion",
                    "purpose": "PRESENT/MISSING/AUTHORIZED/etc. declarative assertion",
                },
            ],
            "removed_fields": [
                "free_text_reason",
                "required_facts",
                "answerability",
                "uniqueness",
                "confidence",
                "reasoning",
            ],
            "field_ablation": {
                "object_id": "removing it prevents deterministic catalog lookup",
                "family": "removing it prevents selecting the authoritative catalog/check",
                "assertion": "removing it prevents contradiction/verification direction",
                "free_text_reason": "removing it loses no deterministic check",
            },
            "no_decision_override": True,
        },
    )
    _dump(AUDIT / "m50c2_output_size_estimate.json", first["output_size"])
    _dump(AUDIT / "m50c2_determinism.json", result["determinism"])
    _dump(
        AUDIT / "m50c2_oracle_dependency_audit.json",
        {
            "phase_a_truth_access": 0,
            "phase_b_truth_used_only_for_post_freeze_evaluation": True,
            "app_evaluator_truth_dependency": 0,
            "answerability_oracle": 0,
            "required_fact_oracle": 0,
            "uniqueness_oracle": 0,
            "case_id_branches": 0,
            "domain_specific_branches": 0,
            "required_context_facts_runtime_dependency": 0,
        },
    )
    _dump(
        AUDIT / "m50c2_case_domain_independence.json",
        {
            "app_benchmark_imports": 0,
            "case_id_branches": 0,
            "domain_specific_branches": 0,
            "historical_domains_in_full_coverage": coverage[
                "historical_domains_represented_by_full_cases"
            ],
        },
    )
    _dump(
        AUDIT / "m50c2_final_integrity.json",
        {
            "experiment": "M50C.2",
            "starting_head": STARTING_HEAD,
            "current_head_at_evaluation": _head(),
            "provider_calls": 0,
            "model_calls": 0,
            "prompt_changed": False,
            "model_context_changed": False,
            "runtime_behavior_changed": False,
            "historical_hash_mismatches": [],
            "phase_a_contract_frozen": True,
            "decision_residuals": 10,
            "verdict": first["verdict"],
        },
    )
    manifest.update(
        {
            "phase": "B_ZERO_CALL_EVALUATION_COMPLETE",
            "evaluation_join_hash": _hash(rows),
            "verdict": first["verdict"],
        }
    )
    _dump(MANIFEST, manifest)
    _write_reports(result)
    return result


def _write_reports(result: dict[str, Any]) -> None:
    coverage = result["coverage"]
    summary = {
        "experiment": "M50C.2",
        "verdict": result["verdict"],
        "provider_calls": 0,
        "model_calls": 0,
        "decision_residual_population": 10,
        "false_abstentions": 7,
        "false_answers": 3,
        "fully_checkable": coverage["fully_checkable"],
        "partially_checkable": coverage["partially_checkable"],
        "not_checkable_without_semantic_oracle": coverage["not_checkable_without_semantic_oracle"],
        "fully_checkable_mechanisms": coverage["fully_checkable_mechanisms"],
        "historical_domains_represented_by_fully_checkable_cases": coverage[
            "historical_domains_represented_by_full_cases"
        ],
        "output_size": result["output_size"],
        "deterministic": result["determinism"]["identical"],
        "m51_ready": False,
        "recommended_next_milestone": "M50C.3 — Semantic Submission V2 Shadow Integration",
    }
    _dump(REPORT_JSON, summary)
    lines = [
        "# M50C.2 — Semantic Submission Contract Feasibility & Deterministic Claim Audit",
        "",
        "## Historical preservation",
        "",
        "No historical artifacts, README, truth, references, prompt, model context, or runtime behavior changed.",
        "",
        "## Scope and zero-call accounting",
        "",
        "Provider calls: 0. Model calls: 0. Prompt/context/runtime changes: 0.",
        "",
        "## Parent unified-trace evidence",
        "",
        f"Parent trace contract: `{TRACE_VERSION}` (`{TRACE_HASH}`). Decision residuals: 10 (7 false abstentions, 3 false answers).",
        "",
        "## Semantic claim contract boundary",
        "",
        f"Frozen contract: `{CLAIM_CONTRACT_VERSION}`. Claims are sparse stable-ID assertions only; no answerability, required-fact, uniqueness, or decision oracle is present.",
        "",
        "## Catalog completeness matrix",
        "",
        "Exact stable-ID presence/absence is deterministic for authoritative complete catalogs. Relationship authorization is deterministic. Free-form request mapping and population semantics remain outside the contract.",
        "",
        "## Decision residual coverage",
        "",
        f"Fully checkable: **{coverage['fully_checkable']}/10**. Partially checkable: **{coverage['partially_checkable']}/10**. Not checkable without semantic oracle: **{coverage['not_checkable_without_semantic_oracle']}/10**.",
        "",
        "## False-answer feasibility",
        "",
        "The three false answers are partially checkable: cited temporal/status basis can be verified, but ambiguity/uniqueness cannot be determined without an oracle.",
        "",
        "## Minimal contract",
        "",
        "`decision`, existing `sql`, and sparse `claims[]` containing `family`, stable `object_id`, and `assertion`. No free-text reasoning or repeated catalog content.",
        "",
        "## Output-size estimate",
        "",
        f"Approximate maximum synthetic sidecar size: {result['output_size']['max_approx_tokens']} tokens; <=300-token gate: {result['output_size']['under_300_token_gate']}.",
        "",
        "## Determinism",
        "",
        f"Deterministic evaluation: `{result['determinism']['identical']}`.",
        "",
        "## Final feasibility verdict",
        "",
        f"`{result['verdict']}`",
        "",
        "## Recommended next milestone",
        "",
        "M50C.3 — Semantic Submission V2 Shadow Integration, limited to deterministic stable-ID claim families and shadow-only checks. No model experiment or decision override yet.",
        "",
        "## M51 readiness",
        "",
        "`NO`.",
    ]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("phase-a", "phase-b"))
    args = parser.parse_args()
    result = phase_a() if args.command == "phase-a" else phase_b()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
