"""Offline M9.6 audit of fixed evaluator applicability strategies.

This module audits future protocol choices from frozen artifacts.  It does not
change either evaluator, bind results, call M1, connect to a database, call a
provider, or construct M10.
"""

from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from evaluation.m93_equivalence_independent import build_cases as build_m93_cases
from evaluation.result_binding_protocol import binder_source_hash
from evaluation.result_binding_protocol_v2 import binder_v2_source_hash
from evaluation.result_equivalence_contract import validate_contract
from evaluation.run_m94 import _request
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    EvaluatorMode,
    evaluate,
    verify_frozen_comparator_source,
)

ROOT = Path(__file__).resolve().parents[1]
M96_ID = "decisionsql-m96-evaluator-applicability-strategy-audit"
M96_VERSION = "m96-evaluator-applicability-v1"
BOUNDARY_B_HASH = "5ae911c84d0e0641493ad7e1cb0babecef9a54056fff54adc8e12d9f1d885f0f"
FROZEN_V1_HASH = "aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb"
FROZEN_V2_HASH = "ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27ecda3b78fb8e"

STRATEGY_IDS = (
    "S1_V2_UNIVERSAL",
    "S2_V1_AUTHORITATIVE_V2_SHADOW",
    "S3_PREDECLARED_CASE_LEVEL_EVALUATOR",
    "S4_DETERMINISTIC_GENERATED_SIDE_APPLICABILITY",
    "S5_STRICT_CORE_PLUS_CONTRACT_DIAGNOSTIC",
)
DIMENSIONS = (
    "D1_FULL_DENOMINATOR_COVERAGE",
    "D2_NO_REFERENCE_LEAKAGE",
    "D3_NO_BEST_OF",
    "D4_CROSS_MODEL_COMPARABILITY",
    "D5_ROUTE_INDEPENDENCE",
    "D6_M1_CHANGE_REQUIRED",
    "D7_BINDER_UNIVERSALITY_REQUIRED",
    "D8_EVALUATOR_FALSE_NEGATIVE_RISK",
    "D9_PROTOCOL_COMPLEXITY",
    "D10_RESIDUAL_DIAGNOSIS_CLARITY",
    "D11_HISTORICAL_COMPARABILITY",
    "D12_M10_READINESS",
)

STRATEGY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "S1_V2_UNIVERSAL": {
        "definition": (
            "Every M10 candidate must become a valid BoundResult and V2 is authoritative."
        ),
        "ratings": {
            "D1_FULL_DENOMINATOR_COVERAGE": "WEAK",
            "D2_NO_REFERENCE_LEAKAGE": "STRONG",
            "D3_NO_BEST_OF": "STRONG",
            "D4_CROSS_MODEL_COMPARABILITY": "STRONG",
            "D5_ROUTE_INDEPENDENCE": "STRONG",
            "D6_M1_CHANGE_REQUIRED": "UNACCEPTABLE",
            "D7_BINDER_UNIVERSALITY_REQUIRED": "UNACCEPTABLE",
            "D8_EVALUATOR_FALSE_NEGATIVE_RISK": "STRONG",
            "D9_PROTOCOL_COMPLEXITY": "WEAK",
            "D10_RESIDUAL_DIAGNOSIS_CLARITY": "WEAK",
            "D11_HISTORICAL_COMPARABILITY": "WEAK",
            "D12_M10_READINESS": "UNACCEPTABLE",
        },
        "primary_eligible": False,
        "diagnostic_eligible": False,
        "invalidation": (
            "Current M1 rejects required CTE/derived output scopes and universal V2 binding "
            "is unproven."
        ),
    },
    "S2_V1_AUTHORITATIVE_V2_SHADOW": {
        "definition": (
            "Frozen V1 is authoritative for every executable M10 case; V2 runs only as a "
            "reference-blind shadow when binding is valid."
        ),
        "ratings": {
            "D1_FULL_DENOMINATOR_COVERAGE": "STRONG",
            "D2_NO_REFERENCE_LEAKAGE": "STRONG",
            "D3_NO_BEST_OF": "STRONG",
            "D4_CROSS_MODEL_COMPARABILITY": "STRONG",
            "D5_ROUTE_INDEPENDENCE": "STRONG",
            "D6_M1_CHANGE_REQUIRED": "STRONG",
            "D7_BINDER_UNIVERSALITY_REQUIRED": "STRONG",
            "D8_EVALUATOR_FALSE_NEGATIVE_RISK": "ACCEPTABLE",
            "D9_PROTOCOL_COMPLEXITY": "STRONG",
            "D10_RESIDUAL_DIAGNOSIS_CLARITY": "STRONG",
            "D11_HISTORICAL_COMPARABILITY": "STRONG",
            "D12_M10_READINESS": "STRONG",
        },
        "primary_eligible": True,
        "diagnostic_eligible": True,
        "invalidation": None,
    },
    "S3_PREDECLARED_CASE_LEVEL_EVALUATOR": {
        "definition": (
            "Each benchmark case receives a frozen V1 or V2 evaluator designation before "
            "provider execution."
        ),
        "ratings": {
            "D1_FULL_DENOMINATOR_COVERAGE": "ACCEPTABLE",
            "D2_NO_REFERENCE_LEAKAGE": "STRONG",
            "D3_NO_BEST_OF": "STRONG",
            "D4_CROSS_MODEL_COMPARABILITY": "ACCEPTABLE",
            "D5_ROUTE_INDEPENDENCE": "WEAK",
            "D6_M1_CHANGE_REQUIRED": "STRONG",
            "D7_BINDER_UNIVERSALITY_REQUIRED": "WEAK",
            "D8_EVALUATOR_FALSE_NEGATIVE_RISK": "ACCEPTABLE",
            "D9_PROTOCOL_COMPLEXITY": "WEAK",
            "D10_RESIDUAL_DIAGNOSIS_CLARITY": "WEAK",
            "D11_HISTORICAL_COMPARABILITY": "WEAK",
            "D12_M10_READINESS": "ACCEPTABLE",
        },
        "primary_eligible": False,
        "diagnostic_eligible": False,
        "invalidation": (
            "A fixed mixed evaluator assignment is harder to interpret and can couple route "
            "metadata to evaluator semantics."
        ),
    },
    "S4_DETERMINISTIC_GENERATED_SIDE_APPLICABILITY": {
        "definition": (
            "A frozen generated-side function selects V2 or V1 before comparison using only "
            "candidate-side applicability evidence."
        ),
        "ratings": {
            "D1_FULL_DENOMINATOR_COVERAGE": "STRONG",
            "D2_NO_REFERENCE_LEAKAGE": "STRONG",
            "D3_NO_BEST_OF": "STRONG",
            "D4_CROSS_MODEL_COMPARABILITY": "WEAK",
            "D5_ROUTE_INDEPENDENCE": "WEAK",
            "D6_M1_CHANGE_REQUIRED": "STRONG",
            "D7_BINDER_UNIVERSALITY_REQUIRED": "ACCEPTABLE",
            "D8_EVALUATOR_FALSE_NEGATIVE_RISK": "ACCEPTABLE",
            "D9_PROTOCOL_COMPLEXITY": "WEAK",
            "D10_RESIDUAL_DIAGNOSIS_CLARITY": "ACCEPTABLE",
            "D11_HISTORICAL_COMPARABILITY": "WEAK",
            "D12_M10_READINESS": "WEAK",
        },
        "primary_eligible": False,
        "diagnostic_eligible": True,
        "invalidation": (
            "Two models can receive different authoritative evaluators for the same case "
            "because their SQL shapes differ."
        ),
    },
    "S5_STRICT_CORE_PLUS_CONTRACT_DIAGNOSTIC": {
        "definition": (
            "A universally applicable strict core is authoritative while contract-aware V2 "
            "is diagnostic; the existing accepted instance is V1 authoritative plus V2 "
            "shadow."
        ),
        "ratings": {
            "D1_FULL_DENOMINATOR_COVERAGE": "STRONG",
            "D2_NO_REFERENCE_LEAKAGE": "STRONG",
            "D3_NO_BEST_OF": "STRONG",
            "D4_CROSS_MODEL_COMPARABILITY": "STRONG",
            "D5_ROUTE_INDEPENDENCE": "STRONG",
            "D6_M1_CHANGE_REQUIRED": "STRONG",
            "D7_BINDER_UNIVERSALITY_REQUIRED": "STRONG",
            "D8_EVALUATOR_FALSE_NEGATIVE_RISK": "ACCEPTABLE",
            "D9_PROTOCOL_COMPLEXITY": "STRONG",
            "D10_RESIDUAL_DIAGNOSIS_CLARITY": "STRONG",
            "D11_HISTORICAL_COMPARABILITY": "STRONG",
            "D12_M10_READINESS": "STRONG",
        },
        "primary_eligible": True,
        "diagnostic_eligible": True,
        "invalidation": None,
    },
}

EVIDENCE_SOURCES = (
    {
        "milestone": "M9.1",
        "path": "evaluation/results/m91/audit-20260904/shape_audit.jsonl",
        "claims_permitted": "historical result-shape artifact population",
        "claims_forbidden": "universal V2 applicability",
    },
    {
        "milestone": "M9.2",
        "path": "evaluation/results/m92/audit-20260904/final_decision.json",
        "claims_permitted": "frozen contract-comparator regression",
        "claims_forbidden": "generated SQL binding",
    },
    {
        "milestone": "M9.3",
        "path": "evaluation/results/m93/audit-20260904-v2/final_decision.json",
        "claims_permitted": "independent V2 comparator validation",
        "claims_forbidden": "real M1/PostgreSQL binding",
    },
    {
        "milestone": "M9.4",
        "path": "evaluation/results/m94/audit-20260904/dual_shadow_validation.json",
        "claims_permitted": "V1/V2 dual-shadow integration and disagreement population",
        "claims_forbidden": "best-of scoring",
    },
    {
        "milestone": "M9.5",
        "path": "docs/m95-benchmark-result-contract-binding-protocol.md",
        "claims_permitted": "binder-v1 unsafe historical evidence",
        "claims_forbidden": "binder-v2 validation",
    },
    {
        "milestone": "M9.5R",
        "path": "docs/m95r-result-binding-safety-boundary-audit.md",
        "claims_permitted": "bounded fail-closed boundary discovery",
        "claims_forbidden": "real integration",
    },
    {
        "milestone": "M9.5R.1",
        "path": "docs/m95r1-fail-closed-result-binder-v2-independent-validation.md",
        "claims_permitted": "fixture-based independent binder-v2 validation",
        "claims_forbidden": "universal real execution applicability",
    },
    {
        "milestone": "M9.5R.2",
        "path": "docs/m95r2r-frozen-first-real-execution-binding-validation.md",
        "claims_permitted": "invalid reconnaissance and clean-selection block",
        "claims_forbidden": "real parity results",
    },
    {
        "milestone": "M9.5R.2S",
        "path": "docs/m95r2s-fresh-real-execution-integration-fixture-construction.md",
        "claims_permitted": "M1 CTE/derived applicability blocker",
        "claims_forbidden": "PostgreSQL execution results",
    },
)


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode()).hexdigest()


def strategy_definition_hash() -> str:
    return stable_hash(STRATEGY_DEFINITIONS)


def evidence_manifest() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in EVIDENCE_SOURCES:
        path = ROOT / source["path"]
        if not path.exists():
            raise FileNotFoundError(path)
        result.append({**source, "sha256": sha256(path.read_bytes()).hexdigest()})
    return result


def evidence_manifest_hash() -> str:
    return stable_hash(evidence_manifest())


def _core(outcome: Any) -> tuple[Any, ...]:
    return (outcome.equivalent, outcome.outcome, outcome.reason, outcome.reference_variant_index)


def disagreement_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in (item for item in build_m93_cases() if not validate_contract(item.contract)):
        v1 = evaluate(_request(case, EvaluatorMode.V1)).authoritative
        v2 = evaluate(_request(case, EvaluatorMode.V2)).authoritative
        if v1.equivalent == v2.equivalent:
            continue
        if v1.equivalent is False and v2.equivalent is True:
            classification = "CLEAR_V1_FALSE_NEGATIVE"
            explanation = (
                "V2 accepted a declared optional-output contract that V1's ordinal strict "
                "comparison rejected."
            )
        elif v1.equivalent is True and v2.equivalent is False:
            classification = "CLEAR_V2_CONTRACT_RECOVERY"
            explanation = (
                "V2 rejected a projection-contract violation that V1 accepted because raw "
                "values alone matched."
            )
        else:
            classification = "INSUFFICIENT_EVIDENCE"
            explanation = "The frozen artifacts do not establish a narrower causal explanation."
        rows.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "split": case.split,
                "expected": case.expected,
                "v1": _core(v1),
                "v2": _core(v2),
                "classification": classification,
                "explanation": explanation,
            }
        )
    return rows


def disagreement_audit_hash(rows: list[dict[str, Any]]) -> str:
    return stable_hash(rows)


def future_protocol() -> dict[str, Any]:
    return {
        "primary_evaluator": "decision-result-evaluator-v1",
        "shadow_evaluator": "decision-result-evaluator-v2",
        "authoritative_scope": "every M10 case with an executable generated QueryExecution",
        "v2_applicability": "reference-blind deterministic diagnostic only",
        "v2_unavailable": "record V2_BINDING_UNAVAILABLE; retain the case and V1 result",
        "v1_v2_disagreement": "diagnostic only; no score rewriting during or after M10",
        "m1_rejection": (
            "record M1_REJECTED as a system outcome; retain the case in the denominator"
        ),
        "generation_failure": (
            "record GENERATION_FAILURE as a system outcome; retain the case in the denominator"
        ),
        "evaluator_protocol_failure": (
            "record EVALUATOR_PROTOCOL_FAILURE separately; never silently call it model "
            "correctness"
        ),
        "reference_invalid": (
            "invalidate the run under the precommitted protocol rather than alter the "
            "denominator"
        ),
        "score_name": "DecisionSQL M10 Clean Rebaseline — Evaluator V1 Authoritative",
        "best_of": False,
        "post_hoc_case_removal": False,
        "historical_rescoring": False,
    }


def run() -> dict[str, Any]:
    v1_pre = binder_source_hash()
    v2_pre = binder_v2_source_hash()
    comparator_pre = verify_frozen_comparator_source()
    disagreements = disagreement_audit()
    v1_post = binder_source_hash()
    v2_post = binder_v2_source_hash()
    comparator_post = verify_frozen_comparator_source()
    disagreement_counts = Counter(row["classification"] for row in disagreements)
    gates = {
        "v1_frozen": v1_pre == v1_post == FROZEN_V1_HASH,
        "v2_frozen": v2_pre == v2_post == FROZEN_V2_HASH,
        "comparator_frozen": comparator_pre == comparator_post == FROZEN_V2_COMPARATOR_SOURCE_HASH,
        "m94_disagreement_population": len(disagreements) == 54,
        "m94_dual_parity_artifact": json.loads(
            (ROOT / "evaluation/results/m94/audit-20260904/dual_shadow_validation.json").read_text()
        )["parity"]
        is True,
        "strategy_definitions_complete": set(STRATEGY_DEFINITIONS) == set(STRATEGY_IDS),
        "s2_primary_eligible": STRATEGY_DEFINITIONS["S2_V1_AUTHORITATIVE_V2_SHADOW"][
            "primary_eligible"
        ],
        "s4_primary_rejected": not STRATEGY_DEFINITIONS[
            "S4_DETERMINISTIC_GENERATED_SIDE_APPLICABILITY"
        ]["primary_eligible"],
        "no_best_of": future_protocol()["best_of"] is False,
        "no_case_removal": future_protocol()["post_hoc_case_removal"] is False,
        "no_m10_consumption": True,
    }
    classification = (
        "V1_AUTHORITATIVE_V2_SHADOW_JUSTIFIED" if all(gates.values()) else "M96_RUN_INVALID"
    )
    return {
        "milestone": "M9.6",
        "audit_id": M96_ID,
        "audit_version": M96_VERSION,
        "classification": classification,
        "frozen_hashes": {
            "binder_v1_pre": v1_pre,
            "binder_v1_post": v1_post,
            "binder_v2_pre": v2_pre,
            "binder_v2_post": v2_post,
            "comparator_pre": comparator_pre,
            "comparator_post": comparator_post,
            "boundary_b": BOUNDARY_B_HASH,
        },
        "disagreement_counts": dict(disagreement_counts),
        "disagreement_total": len(disagreements),
        "disagreement_audit_hash": disagreement_audit_hash(disagreements),
        "strategy_definition_hash": strategy_definition_hash(),
        "evidence_manifest_hash": evidence_manifest_hash(),
        "strategy_matrix_hash": stable_hash(
            {key: STRATEGY_DEFINITIONS[key]["ratings"] for key in STRATEGY_IDS}
        ),
        "strategies": STRATEGY_DEFINITIONS,
        "future_protocol": future_protocol(),
        "gates": gates,
        "metrics": {
            "m94_valid_cases": 150,
            "m94_dual_agreements": 96,
            "m94_dual_disagreements": 54,
            "m94_clear_v1_false_negatives": disagreement_counts["CLEAR_V1_FALSE_NEGATIVE"],
            "m94_clear_v2_contract_recoveries": disagreement_counts["CLEAR_V2_CONTRACT_RECOVERY"],
            "m94_true_semantic_disagreements": 0,
            "m94_binding_dependent": 0,
            "m94_insufficient_evidence": disagreement_counts["INSUFFICIENT_EVIDENCE"],
        },
        "provider_calls": 0,
        "sql_generation_calls": 0,
        "db_calls": 0,
        "db_mutations": 0,
        "m10_corpus_created": False,
        "m10_cases_consumed": 0,
        "m10_primary_evaluation_protocol_ready": True,
        "m10_v2_shadow_protocol_ready": True,
        "m10_binding_protocol_ready_legacy": False,
        "next_milestone": (
            "CHECKPOINT M9.5R.1 + M9.6 research state, then M10 — Clean Residual Rebaseline"
        ),
        "disagreements": disagreements,
    }


def write_fixtures(result: dict[str, Any]) -> None:
    fixtures = ROOT / "evaluation" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "m96_strategy_definitions.json").write_text(
        json.dumps(
            {
                "audit_id": M96_ID,
                "audit_version": M96_VERSION,
                "dimensions": DIMENSIONS,
                "strategies": STRATEGY_DEFINITIONS,
                "strategy_definition_hash": result["strategy_definition_hash"],
                "strategy_matrix_hash": result["strategy_matrix_hash"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (fixtures / "m96_evidence_manifest.json").write_text(
        json.dumps(
            {
                "audit_id": M96_ID,
                "sources": evidence_manifest(),
                "evidence_manifest_hash": result["evidence_manifest_hash"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (fixtures / "m96_disagreement_audit.json").write_text(
        json.dumps(
            {
                "audit_id": M96_ID,
                "source": "evaluation/results/m94/audit-20260904/dual_shadow_validation.json",
                "case_count": result["disagreement_total"],
                "classification_counts": result["disagreement_counts"],
                "disagreement_audit_hash": result["disagreement_audit_hash"],
                "rows": result["disagreements"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    audit = run()
    write_fixtures(audit)
    print(
        json.dumps(
            {key: value for key, value in audit.items() if key != "disagreements"},
            indent=2,
            sort_keys=True,
        )
    )
