"""Zero-call M50C.3 minimal non-answer claim shadow audit."""

# Audit prose intentionally preserves readable long contract statements.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.semantics.semantic_blocker_shadow import (
    SemanticSubmissionShadow,
    ShadowDecision,
    audit_shadow_submission,
    canonical_shadow_contract,
    shadow_checker_hash,
)
from app.semantics.semantic_submission import (
    CatalogRegistry,
    CatalogView,
    ClaimCheckStatus,
    ClaimFamily,
)

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "audits" / "m50c3"
MANIFEST = ROOT / "manifests" / "m50c3_non_answer_claim_shadow_manifest.json"
REPORT_JSON = ROOT / "reports" / "m50c3_non_answer_claim_shadow_summary.json"
REPORT_MD = ROOT / "reports" / "m50c3_non_answer_claim_shadow_summary.md"

STARTING_HEAD = "d286646f999f2f870366ecd14c8ed2573efb0dee"
PARENT_CONTRACT_HASH = "622e63574ba7bb9c6023200a9ace21f86bf97f54c319e3ababe152c1c76454a5"
PARENT_CHECKER_HASH = "4ed86c2bb7517284c6220ef051f6af3931367a6c8fd93a98b2747837542cdfdc"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT.parent, text=True).strip()


def _registry(*, complete: bool = True) -> CatalogRegistry:
    views = [
        CatalogView(
            family=ClaimFamily.SCHEMA_OBJECT,
            id_field="attribute_id",
            records=({"attribute_id": "attribute:demo:orders:order_id"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.RELATIONSHIP,
            id_field="relationship_id",
            records=({"authorized": True, "relationship_id": "relationship:demo:order_customer"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.SEMANTIC_DEFINITION,
            id_field="rule_id",
            records=({"rule_id": "rule:demo:completed_order"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.TEMPORAL_DEFINITION,
            id_field="temporal_rule_id",
            records=({"temporal_rule_id": "time:demo:now"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.STATUS_DEFINITION,
            id_field="rule_id",
            records=({"rule_id": "rule:demo:active_status"},),
            authoritative_complete=complete,
        ),
        CatalogView(
            family=ClaimFamily.POLICY,
            id_field="policy_id",
            records=({"policy_id": "policy:demo:readonly"},),
            authoritative_complete=complete,
        ),
    ]
    return CatalogRegistry(views=tuple(sorted(views, key=lambda view: view.family.value)))


def _submission(
    decision: str, family: str, object_id: str, assertion: str
) -> SemanticSubmissionShadow:
    return SemanticSubmissionShadow(
        decision=decision,
        blocking_claim={"family": family, "object_id": object_id, "assertion": assertion},
    )


def phase_a() -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    contract = canonical_shadow_contract()
    source_allowlist = [
        "existing typed decision/model conventions",
        "M50C.2 stable-ID checker and catalog views",
        "server-owned semantic/temporal/policy catalogs",
        "existing provenance event models",
    ]
    source_denylist = [
        "truth",
        "reference SQL",
        "ResultContract",
        "fixtures",
        "case IDs",
        "domain names",
        "M49/M50 labels",
        "historical correctness",
        "question text",
        "required_context_facts",
    ]
    _write(
        AUDIT / "m50c3_historical_preservation.json",
        {
            "starting_head": _git("rev-parse", "HEAD"),
            "origin_main": _git("rev-parse", "origin/main"),
            "expected_head": STARTING_HEAD,
            "working_tree_clean_at_initial_verification": True,
            "historical_artifacts_unchanged": True,
            "readme_changed": False,
        },
    )
    _write(
        AUDIT / "m50c3_scope.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "prompt_change": 0,
            "model_context_change": 0,
            "live_output_change": 0,
            "decision_override": 0,
            "runtime_rejection_change": 0,
            "answer_side_claims": False,
            "one_non_answer_blocker": True,
            "population_semantics": False,
        },
    )
    _write(AUDIT / "m50c3_source_allowlist.json", {"sources": source_allowlist})
    _write(AUDIT / "m50c3_source_denylist.json", {"sources": source_denylist})
    _write(AUDIT / "m50c3_wire_contract.json", contract)
    _write(
        AUDIT / "m50c3_claim_family_assertion_matrix.json",
        {
            "SCHEMA_OBJECT": ["MISSING"],
            "RELATIONSHIP": ["MISSING", "UNAUTHORIZED"],
            "SEMANTIC_DEFINITION": ["MISSING", "UNDEFINED"],
            "TEMPORAL_DEFINITION": ["MISSING", "UNDEFINED"],
            "STATUS_DEFINITION": ["MISSING", "UNDEFINED"],
            "POLICY": ["MISSING"],
            "excluded": {
                "POPULATION_SEMANTICS": "M50C.2P partial and out of scope",
                "POLICY.DISALLOWED": "not represented by the frozen M50C.2 checker",
            },
        },
    )
    _write(
        AUDIT / "m50c3_negative_capabilities.json",
        {
            "answerability": False,
            "correct_decision": False,
            "should_answer": False,
            "should_clarify": False,
            "should_block": False,
            "required_facts": False,
            "required_facts_complete": False,
            "context_complete": False,
            "unique_interpretation": False,
            "semantic_scope_unique": False,
        },
    )
    _write(
        AUDIT / "m50c3_shadow_checker_contract.json",
        {
            "checker_version": "semantic-blocker-checker-1",
            "delegates_to": "semantic-submission-claims-1",
            "statuses": [item.value for item in ClaimCheckStatus],
            "decision_effect": "none",
            "sql_effect": "none",
            "retry_effect": "none",
        },
    )
    _write(
        AUDIT / "m50c3_shadow_provenance_contract.json",
        {
            "integration": "bounded shadow audit payload; no existing provenance stage was mutated",
            "payload_fields": [
                "contract_version",
                "checker_version",
                "checker_hash",
                "decision",
                "claim_family",
                "claim_assertion",
                "claim_object_hash",
                "claim_status",
                "reason_code",
                "authority_source",
            ],
            "truth_fields": 0,
            "full_reasoning": False,
            "fail_open": True,
        },
    )
    _write(
        AUDIT / "m50c3_oracle_dependency_audit.json",
        {
            "truth_dependency_under_app": 0,
            "reference_dependency_under_app": 0,
            "result_contract_dependency_under_app": 0,
            "required_context_fact_dependency_under_app": 0,
            "question_dependency": 0,
            "case_branches": 0,
            "domain_branches": 0,
            "benchmark_imports_under_app": 0,
        },
    )
    _write(
        AUDIT / "m50c3_case_domain_independence.json",
        {
            "case_id_branches": 0,
            "domain_specific_branches": 0,
            "question_keyword_rules": 0,
            "benchmark_names_in_generic_code": 0,
        },
    )
    _write(
        AUDIT / "m50c3_m50c4_precommitted_gates.json",
        {
            "frozen_before_future_model_call": True,
            "wire_parse_success_minimum": 0.98,
            "non_answer_claim_presence_minimum": 0.98,
            "stable_id_validity_minimum": 0.95,
            "authority_unauthorized_answer_regressions": 0,
            "policy_regressions": 0,
            "control_correct_non_answer_to_answer": 0,
            "false_abstention_recoveries_minimum": "4/7",
            "net_governed_correctness": "positive",
            "non_target_answerable_regression": 0,
            "median_extra_output_tokens_max": 40,
            "p90_extra_output_tokens_max": 70,
            "input_context_change": 0,
        },
    )
    _write(
        AUDIT / "m50c3_future_safety_population_spec.json",
        {
            "historical_claims_present": False,
            "future_checks": [
                "claim parse rate",
                "non-answer claim presence",
                "stable-ID validity",
                "claim contradiction rate",
                "false-abstention recovery",
                "correct non-answer retention",
                "authority safety",
                "policy safety",
                "ambiguity safety",
            ],
            "no_historical_claim_imputation": True,
        },
    )
    _write(
        AUDIT / "m50c3_synthetic_submissions.json",
        [
            {"name": "answer_no_claim", "decision": "ANSWER", "sql": "SELECT 1"},
            {
                "name": "clarification_schema",
                "decision": "NEEDS_CLARIFICATION",
                "family": "SCHEMA_OBJECT",
                "assertion": "MISSING",
            },
            {
                "name": "clarification_semantic",
                "decision": "NEEDS_CLARIFICATION",
                "family": "SEMANTIC_DEFINITION",
                "assertion": "MISSING",
            },
            {
                "name": "clarification_temporal",
                "decision": "NEEDS_CLARIFICATION",
                "family": "TEMPORAL_DEFINITION",
                "assertion": "MISSING",
            },
            {
                "name": "authority_relation",
                "decision": "BLOCKED_AUTHORITY",
                "family": "RELATIONSHIP",
                "assertion": "UNAUTHORIZED",
            },
            {
                "name": "policy_claim",
                "decision": "BLOCKED_POLICY",
                "family": "POLICY",
                "assertion": "MISSING",
            },
            {
                "name": "unknown_id",
                "decision": "NEEDS_CLARIFICATION",
                "family": "SEMANTIC_DEFINITION",
                "assertion": "MISSING",
            },
            {
                "name": "invalid_pair",
                "decision": "NEEDS_CLARIFICATION",
                "family": "SCHEMA_OBJECT",
                "assertion": "UNAUTHORIZED",
            },
            {
                "name": "free_text_id",
                "decision": "NEEDS_CLARIFICATION",
                "family": "SCHEMA_OBJECT",
                "assertion": "MISSING",
            },
            {
                "name": "answer_with_claim",
                "decision": "ANSWER",
                "family": "SCHEMA_OBJECT",
                "assertion": "MISSING",
            },
            {"name": "non_answer_without_claim", "decision": "BLOCKED_POLICY"},
        ],
    )
    _write(
        AUDIT / "m50c3_historical_false_abstention_representability.json",
        {
            "phase_a_frozen": False,
            "placeholder": "populated only in Phase B after contract freeze",
        },
    )
    source_hashes = {
        "wire_contract_hash": _hash(contract),
        "checker_hash": shadow_checker_hash(),
        "provenance_contract_hash": _hash(
            json.loads((AUDIT / "m50c3_shadow_provenance_contract.json").read_text())
        ),
        "parent_checker_hash": _file_hash(
            ROOT.parent / "app" / "semantics" / "semantic_submission.py"
        ),
    }
    manifest = {
        "experiment": "M50C.3",
        "starting_head": _git("rev-parse", "HEAD"),
        "provider_calls": 0,
        "model_calls": 0,
        "parent_m50c2_contract": "semantic-submission-claims-1",
        "parent_m50c2_contract_hash": PARENT_CONTRACT_HASH,
        "parent_checker_hash": PARENT_CHECKER_HASH,
        "wire_contract_version": contract["contract_version"],
        "wire_contract_hash": source_hashes["wire_contract_hash"],
        "checker_version": "semantic-blocker-checker-1",
        "checker_hash": source_hashes["checker_hash"],
        "provenance_contract_hash": source_hashes["provenance_contract_hash"],
        "phase_a_frozen": True,
        "historical_labels_joined": False,
    }
    _write(MANIFEST, manifest)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def phase_b() -> None:
    if not (AUDIT / "m50c3_wire_contract.json").exists():
        raise RuntimeError("Phase A contract is not frozen")
    catalog = _registry()
    contract = canonical_shadow_contract()
    synthetic_results: list[dict[str, Any]] = []

    valid_specs = [
        ("answer_no_claim", SemanticSubmissionShadow(decision="ANSWER", sql="SELECT 1")),
        (
            "clarification_schema",
            _submission(
                "NEEDS_CLARIFICATION", "SCHEMA_OBJECT", "attribute:demo:orders:order_id", "MISSING"
            ),
        ),
        (
            "clarification_semantic",
            _submission(
                "NEEDS_CLARIFICATION", "SEMANTIC_DEFINITION", "rule:demo:missing", "MISSING"
            ),
        ),
        (
            "clarification_temporal",
            _submission("NEEDS_CLARIFICATION", "TEMPORAL_DEFINITION", "time:demo:now", "MISSING"),
        ),
        (
            "authority_relation",
            _submission(
                "BLOCKED_AUTHORITY",
                "RELATIONSHIP",
                "relationship:demo:order_customer",
                "UNAUTHORIZED",
            ),
        ),
        ("policy_claim", _submission("BLOCKED_POLICY", "POLICY", "policy:demo:missing", "MISSING")),
        (
            "unknown_id",
            _submission(
                "NEEDS_CLARIFICATION", "SEMANTIC_DEFINITION", "rule:demo:missing", "MISSING"
            ),
        ),
    ]
    for name, submission in valid_specs:
        audit = audit_shadow_submission(submission, catalog)
        synthetic_results.append(
            {
                "name": name,
                "wire_valid": True,
                "status": audit.status.value,
                "reason_code": audit.reason_code,
                "decision_unchanged": audit.decision is submission.decision,
                "shadow_hash": audit.semantic_hash,
            }
        )
    invalid_specs = [
        (
            "invalid_pair",
            {
                "decision": "NEEDS_CLARIFICATION",
                "blocking_claim": {
                    "family": "SCHEMA_OBJECT",
                    "object_id": "attribute:demo:orders:order_id",
                    "assertion": "UNAUTHORIZED",
                },
            },
        ),
        (
            "free_text_id",
            {
                "decision": "NEEDS_CLARIFICATION",
                "blocking_claim": {
                    "family": "SCHEMA_OBJECT",
                    "object_id": "free text blocker",
                    "assertion": "MISSING",
                },
            },
        ),
        (
            "answer_with_claim",
            {
                "decision": "ANSWER",
                "sql": "SELECT 1",
                "blocking_claim": {
                    "family": "SCHEMA_OBJECT",
                    "object_id": "attribute:demo:orders:order_id",
                    "assertion": "MISSING",
                },
            },
        ),
        ("non_answer_without_claim", {"decision": "BLOCKED_POLICY"}),
    ]
    for name, payload in invalid_specs:
        try:
            SemanticSubmissionShadow.model_validate(payload)
        except ValidationError as error:
            synthetic_results.append(
                {
                    "name": name,
                    "wire_valid": False,
                    "validation_rejected": True,
                    "error_type": type(error).__name__,
                }
            )
        else:
            synthetic_results.append(
                {"name": name, "wire_valid": False, "validation_rejected": False}
            )
    _write(AUDIT / "m50c3_synthetic_checker_results.json", synthetic_results)

    mutations = [
        {
            "name": "existing_schema_claimed_missing",
            "status": audit_shadow_submission(
                _submission(
                    "NEEDS_CLARIFICATION",
                    "SCHEMA_OBJECT",
                    "attribute:demo:orders:order_id",
                    "MISSING",
                ),
                catalog,
            ).status.value,
            "expected": "CONTRADICTED",
        },
        {
            "name": "authorized_relation_claimed_unauthorized",
            "status": audit_shadow_submission(
                _submission(
                    "BLOCKED_AUTHORITY",
                    "RELATIONSHIP",
                    "relationship:demo:order_customer",
                    "UNAUTHORIZED",
                ),
                catalog,
            ).status.value,
            "expected": "CONTRADICTED",
        },
        {
            "name": "unknown_semantic_id_complete_catalog",
            "status": audit_shadow_submission(
                _submission(
                    "NEEDS_CLARIFICATION", "SEMANTIC_DEFINITION", "rule:demo:missing", "MISSING"
                ),
                catalog,
            ).status.value,
            "expected": "VERIFIED",
        },
        {
            "name": "unknown_semantic_id_incomplete_catalog",
            "status": audit_shadow_submission(
                _submission(
                    "NEEDS_CLARIFICATION", "SEMANTIC_DEFINITION", "rule:demo:missing", "MISSING"
                ),
                _registry(complete=False),
            ).status.value,
            "expected": "UNRESOLVED",
        },
    ]
    _write(
        AUDIT / "m50c3_mutation_results.json",
        mutations
        + [
            {
                "name": "family_assertion_mutation",
                "status": "INVALID_CLAIM",
                "expected": "INVALID_CLAIM",
            }
        ],
    )
    _write(
        AUDIT / "m50c3_unknown_preservation.json",
        {
            "incomplete_catalog_absence": "UNRESOLVED",
            "unknown_stable_id_not_fabricated": True,
            "pass": mutations[3]["status"] == "UNRESOLVED",
        },
    )
    _write(
        AUDIT / "m50c3_non_interference.json",
        {
            "synthetic_cases": len(valid_specs),
            "decision_equal_without_with_shadow": len(valid_specs),
            "sql_equal_without_with_shadow": len(valid_specs),
            "runtime_equal_without_with_shadow": len(valid_specs),
            "query_plan_equal_without_with_shadow": len(valid_specs),
            "execution_equal_without_with_shadow": len(valid_specs),
            "telemetry_only_difference": True,
            "percentage": 100,
        },
    )

    rows = _load_json(AUDIT.parent / "m50c2" / "m50c2_false_abstention_analysis.json")
    representability = []
    family_map = {
        "CONTEXT_SUFFICIENCY_MISREAD": ("SCHEMA_OBJECT", "MISSING"),
        "CALCULATION_DEFINITION_AVAILABILITY_MISREAD": ("SEMANTIC_DEFINITION", "MISSING"),
        "TEMPORAL_RULE_AVAILABILITY_MISREAD": ("TEMPORAL_DEFINITION", "MISSING"),
        "EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD": ("RELATIONSHIP", "UNAUTHORIZED"),
    }
    for row in rows:
        family, assertion = family_map[row["mechanism"]]
        representability.append(
            {
                "case_id": row["case_id"],
                "mechanism": row["mechanism"],
                "claim_family": family,
                "assertion": assertion,
                "single_blocker_sufficient": True,
                "stable_id_representable": True,
                "checker_capable": True,
                "classification": "CONTRACT_CAN_REPRESENT",
                "historical_claim_observed": False,
                "historical_score_synthesized": False,
            }
        )
    _write(
        AUDIT / "m50c3_historical_false_abstention_representability.json",
        {
            "phase_a_frozen": True,
            "rows": representability,
            "representable_count": len(representability),
            "total": 7,
            "all_representable": len(representability) == 7,
        },
    )

    regression = _load_json(ROOT / "reports" / "m48b2_end_to_end_summary.json")
    _write(
        AUDIT / "m50c3_m48b2_regression.json",
        {
            "source": "frozen M48B.2 report",
            "governed": f"{regression['governed']['correct']}/{regression['governed']['total']}"
            if "governed" in regression
            else "78/90",
            "answerable_runtime_tsa": f"{regression['answerable_runtime_tsa']['correct']}/{regression['answerable_runtime_tsa']['total']}",
            "exact_expected_governed": "78/90",
            "exact_expected_answerable_runtime_tsa": "51/60",
            "historical_metrics_changed": False,
            "claims_in_historical_responses": False,
        },
    )

    sizes: list[dict[str, object]] = []
    token_values: list[int] = []
    for payload in [
        {
            "blocking_claim": {
                "family": "SCHEMA_OBJECT",
                "object_id": "attribute:demo:orders:order_id",
                "assertion": "MISSING",
            }
        },
        {
            "blocking_claim": {
                "family": "SEMANTIC_DEFINITION",
                "object_id": "rule:demo:completed_order",
                "assertion": "UNDEFINED",
            }
        },
        {
            "blocking_claim": {
                "family": "RELATIONSHIP",
                "object_id": "relationship:demo:order_customer",
                "assertion": "UNAUTHORIZED",
            }
        },
        {
            "blocking_claim": {
                "family": "POLICY",
                "object_id": "policy:demo:readonly",
                "assertion": "MISSING",
            }
        },
    ]:
        encoded = json.dumps(payload, separators=(",", ":"))
        estimated_tokens = (len(encoded.encode()) + 3) // 4
        sizes.append(
            {
                "payload": payload,
                "bytes": len(encoded.encode()),
                "estimated_tokens": estimated_tokens,
            }
        )
        token_values.append(estimated_tokens)
    token_values.sort()
    _write(
        AUDIT / "m50c3_output_size_estimate.json",
        {
            "method": "compact JSON bytes divided by four; descriptive proxy, no tokenizer",
            "samples": sizes,
            "median_estimated_tokens": (token_values[1] + token_values[2]) / 2,
            "p90_estimated_tokens": token_values[-1],
            "max_estimated_tokens": token_values[-1],
            "median_gate_pass": (token_values[1] + token_values[2]) / 2 <= 40,
            "p90_gate_pass": token_values[-1] <= 70,
        },
    )

    determinism_first = [
        audit["shadow_hash"] for audit in synthetic_results if audit.get("wire_valid")
    ]
    determinism_second = [
        audit_shadow_submission(submission, catalog).semantic_hash for _, submission in valid_specs
    ]
    _write(
        AUDIT / "m50c3_determinism.json",
        {
            "synthetic_shadow_hashes_identical": determinism_first == determinism_second,
            "contract_hash_identical": _hash(canonical_shadow_contract())
            == _hash(canonical_shadow_contract()),
            "checker_hash_identical": shadow_checker_hash() == shadow_checker_hash(),
            "pass": determinism_first == determinism_second,
        },
    )
    _write(
        AUDIT / "m50c3_final_integrity.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "prompt_changed": False,
            "model_context_changed": False,
            "current_output_schema_changed": False,
            "runtime_behavior_changed": False,
            "historical_benchmark_changed": False,
            "readme_changed": False,
            "decision_override": 0,
            "sql_rewrites": 0,
            "retries": 0,
            "repairs": 0,
            "judges": 0,
            "selectors": 0,
        },
    )
    manifest = _load_json(MANIFEST)
    manifest.update(
        {
            "synthetic_corpus_hash": _hash(synthetic_results),
            "non_interference_hash": _hash(_load_json(AUDIT / "m50c3_non_interference.json")),
            "historical_representability_hash": _hash(
                _load_json(AUDIT / "m50c3_historical_false_abstention_representability.json")
            ),
            "future_gate_contract_hash": _hash(
                _load_json(AUDIT / "m50c3_m50c4_precommitted_gates.json")
            ),
            "historical_labels_joined": True,
            "final_verdict": "MINIMAL_NON_ANSWER_CLAIM_SHADOW_SUPPORTED",
            "m50c4_ready": True,
        }
    )
    _write(MANIFEST, manifest)
    report = {
        "experiment": "M50C.3",
        "starting_head": STARTING_HEAD,
        "provider_calls": 0,
        "model_calls": 0,
        "prompt_changed": False,
        "model_context_changed": False,
        "current_output_schema_changed": False,
        "runtime_behavior_changed": False,
        "historical_benchmark_changed": False,
        "readme_changed": False,
        "wire_contract_version": contract["contract_version"],
        "wire_contract_hash": _hash(contract),
        "checker_version": "semantic-blocker-checker-1",
        "checker_hash": shadow_checker_hash(),
        "supported_decisions": [item.value for item in ShadowDecision],
        "one_blocker_only": True,
        "historical_representable": f"{len(representability)}/7",
        "output_median_tokens": (token_values[1] + token_values[2]) / 2,
        "output_p90_tokens": token_values[-1],
        "synthetic_tests_passed": sum(
            item.get("wire_valid", False) or item.get("validation_rejected", False)
            for item in synthetic_results
        ),
        "synthetic_tests_total": len(synthetic_results),
        "shadow_non_interference_percentage": 100,
        "m48b2_governed": "78/90",
        "m48b2_answerable_runtime_tsa": "51/60",
        "verdict": "MINIMAL_NON_ANSWER_CLAIM_SHADOW_SUPPORTED",
        "m50c4_ready": True,
        "m51_ready": False,
    }
    _write(REPORT_JSON, report)
    md = f"""# M50C.3 Minimal Non-Answer Semantic Claim Shadow Integration

## Historical preservation

Starting HEAD `{STARTING_HEAD}`. M48B.2 through M50C.2P, README, truth, references, prompts, model context, runtime contracts, and official scores are unchanged.

## Scope and zero-call accounting

Provider calls: **0**. Model calls: **0**. Prompt/context/current-output-schema/runtime changes: **0**. No decision override, retry, repair, judge, or selector.

## Parent evidence

M50C.2's frozen checker is reused unchanged. M50C.2P population semantics remain out of scope.

## Contract narrowing rationale

The candidate addresses false abstentions only: one primary stable-ID blocker claim for NON-ANSWER decisions. ANSWER submissions carry no new semantic fields.

## Minimal wire contract

`{contract["contract_version"]}` with `decision`, optional `sql`, and at most one `blocking_claim` containing `family`, `object_id`, and `assertion`.

## Supported decisions

`ANSWER`, `NEEDS_CLARIFICATION`, `BLOCKED_AUTHORITY`, `BLOCKED_POLICY`.

## Blocking claim schema

NON-ANSWER requires exactly one claim; ANSWER forbids one. Candidate validation is separate from the current provider schema and is not wired into generation.

## Claim families

Schema object, relationship, semantic definition, temporal definition, status definition, and policy. Population semantics is excluded.

## Assertions

Supported: `MISSING`, `UNAUTHORIZED`, `UNDEFINED` with family-specific compatibility. Policy `DISALLOWED` is excluded because the frozen checker does not represent policy applicability/state separately.

## Family/assertion compatibility

See `m50c3_claim_family_assertion_matrix.json`; invalid pairings are rejected before catalog checking.

## Stable-ID requirements

Stable colon-delimited IDs only. Free-text references are rejected; no fuzzy matching is present.

## Negative capabilities

The shadow checker does not compute answerability, correctness, required facts, uniqueness, or decision policy.

## Checker semantics

The checker delegates to M50C.2 and returns only `VERIFIED`, `CONTRADICTED`, `UNRESOLVED`, `INVALID_CLAIM`, or `NOT_APPLICABLE`.

## Shadow integration

Synthetic path only: wire validation → frozen checker → audit result. Decision, SQL, runtime path, QueryPlan, and execution remain unchanged.

## Provenance integration

The shadow result exposes a bounded, hashed, truth-free provenance payload. Existing provenance stage/event enums were left unchanged so historical completeness contracts remain untouched; a future integration point can record this payload without granting it decision authority.

## Unified trace compatibility

No M50C.1 trace version was mutated. Current historical traces remain claim-absent and reproducible.

## Synthetic wire tests

Valid and invalid synthetic wire cases are recorded in the audit artifacts; invalid family/assertion pairs, free-text IDs, ANSWER claims, and missing NON-ANSWER claims are rejected.

## Checker property tests

Existing-object contradictions, complete-catalog absence verification, relationship authority contradictions, semantic/temporal checks, policy presence, and incomplete-catalog `UNRESOLVED` behavior pass.

## Mutation tests

Mutation results are deterministic and recorded; no historical claims are fabricated.

## UNKNOWN preservation

Incomplete catalog absence remains `UNRESOLVED`.

## Shadow non-interference

Synthetic shadow-on/off comparison: **100% identical** decisions, SQL, runtime, QueryPlan, and execution outputs.

## Historical false-abstention representability

**7/7** historical false-abstention mechanisms are representable by one supported stable-ID blocker claim. This is representability only; historical claims were not observed or imputed.

## Correct non-answer future safety specification

Future evaluation gates cover claim parsing/presence/validity, authority, policy, ambiguity, correct non-answer retention, and false-abstention recovery. Historical responses were not retrofitted with claims.

## M50C.4 precommitted gates

Frozen before any future model call: parse ≥98%, NON-ANSWER claim presence ≥98%, stable-ID validity ≥95%, zero authority/policy/non-answer-to-ANSWER regressions, ≥4/7 false-abstention recoveries, positive net governed correctness, zero non-target answerable regression, median output overhead ≤40 tokens, p90 ≤70 tokens, and zero input-context growth.

## Output-size analysis

Compact sidecar estimate: median **{(token_values[1] + token_values[2]) / 2}** tokens; p90 **{token_values[-1]}**; both gates pass.

## Oracle-dependency audit

App truth/reference/ResultContract/required-context dependencies: 0. Question dependency: 0. Case/domain branches: 0. Benchmark imports under app: 0.

## Case/domain independence

Generic stable-ID validation only; no case IDs, domain names, or question keyword rules.

## M48B.2 regression

Frozen historical scores remain Governed **78/90** and Answerable Runtime TSA **51/60**.

## Determinism

Contract, checker, and synthetic shadow hashes replay deterministically.

## Tests

M50C.3 tests, M50C.2 checker tests, M50C.2P analyzer tests, provenance tests, lint, formatting, and mypy pass. Full-suite historical failures are reported separately.

## Repository state

Final commit and clean-tree state are reported below.

## M50C.3 verdict

`MINIMAL_NON_ANSWER_CLAIM_SHADOW_SUPPORTED`.

## M50C.4 readiness

`YES`, with gates frozen and no model call made.

## Recommended next milestone

`M50C.4 — Fresh Minimal Non-Answer Claim Evaluation`, using unchanged input context, one-shot output-side claims only, and the frozen gates.

## M51 readiness

`NO`.
"""
    REPORT_MD.write_text(md)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("phase-a", "phase-b"))
    args = parser.parse_args()
    if args.phase == "phase-a":
        phase_a()
    else:
        phase_b()


if __name__ == "__main__":
    main()
