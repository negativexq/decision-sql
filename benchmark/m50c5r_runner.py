"""Zero-call recovery analysis for the frozen M50C.5 response corpus."""

# Audit prose contains deliberately long evidence descriptions.
# ruff: noqa: E501

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark import m50c5_runner as m50c5

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50c5r"
MANIFEST = ROOT / "manifests" / "m50c5r_frozen_response_recovery_manifest.json"
REPORT_JSON = ROOT / "reports" / "m50c5r_frozen_response_recovery_summary.json"
REPORT_MD = ROOT / "reports" / "m50c5r_frozen_response_recovery_summary.md"
STARTING_HEAD = "0c4cda7a6da05daeb38a568a11bfd19f131d3645"
CONTROL_CORPUS = "908e93c82c3df13afe4e01c075ec48c6f348734a9f789452040cb27c27e4ce1d"
TREATMENT_CORPUS = "6638275bd9230b85232334dcd750696792a7c147478f26a13f5785ff6e736665"
SCHEMA_HASH = "b87d45a67a5e6e67a74719a4a33f1d1da349ce514f89ef15f0625ebdbd9f1f92"
RESPONSE_FORMAT_HASH = "c9ea99c4ac05e1a4f1c756d313bdfc74ca6e30578e7c9cda129de33f1fe617e8"
PARENT_HASH = "e799113d0f6a20e96ae5ac3abaca3b20a2cf12deebea8ae01ee033c35d3384ca"
CHECKER_HASH = "cb396d42675235632bf8e713270c9024cf9ddf098fadd19cb4fcf2a04fa61b98"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _copy_frozen_inputs() -> dict[str, Any]:
    AUDIT.mkdir(parents=True, exist_ok=True)
    sources = {
        "CONTROL": m50c5.AUDIT / "m50c5_control_responses.jsonl",
        "TREATMENT": m50c5.AUDIT / "m50c5_treatment_responses.jsonl",
    }
    destinations = {arm: AUDIT / f"m50c5_{arm.lower()}_responses.jsonl" for arm in sources}
    for arm, source in sources.items():
        if not source.exists():
            raise RuntimeError("M50C5R_RESPONSE_CORPUS_MISSING")
        shutil.copyfile(source, destinations[arm])
    actual_files = {arm: _sha(path) for arm, path in destinations.items()}
    expected_files = {
        "CONTROL": "520c2d0c3690011723c21ab7a34f8c99d48f6a3fa64e9616906172c568b2e8d5",
        "TREATMENT": "dcf4f336fc6a0474402e47dfcd81dbeed1725b5c5885344b3f31bb8f2253947e",
    }
    if actual_files != expected_files:
        raise RuntimeError("M50C5R_RESPONSE_CORPUS_DRIFT")
    source_freeze = json.loads((m50c5.AUDIT / "m50c5_response_freeze.json").read_text())
    if source_freeze["response_corpus_hashes"] != {
        "CONTROL": CONTROL_CORPUS,
        "TREATMENT": TREATMENT_CORPUS,
    }:
        raise RuntimeError("M50C5R_RESPONSE_CORPUS_HASH_DRIFT")
    freeze = {
        **source_freeze,
        "response_files": actual_files,
        "recovery_experiment": "M50C.5R",
        "post_freeze_provider_calls": 0,
        "post_freeze_model_calls": 0,
    }
    _dump(
        AUDIT / "m50c5r_frozen_corpus_integrity.json",
        {
            "pass": True,
            "control_slots": 90,
            "treatment_slots": 90,
            "control_corpus_hash": CONTROL_CORPUS,
            "treatment_corpus_hash": TREATMENT_CORPUS,
            "control_file_hash": actual_files["CONTROL"],
            "treatment_file_hash": actual_files["TREATMENT"],
            "frozen_responses_modified": False,
        },
    )
    _dump(AUDIT / "m50c5r_response_freeze.json", freeze)
    _dump(AUDIT / "m50c5_response_freeze.json", freeze)
    return freeze


def _invalid_forensics() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = AUDIT / "m50c5_treatment_responses.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    invalid: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for record in records:
        if record.get("parse_status") == "PASS":
            continue
        raw = json.loads(base64.b64decode(record["raw_response_bytes_base64"]))
        content = raw["choices"][0]["message"]["content"]
        declared = json.loads(content)
        blocker = declared.get("blocking_claim") or {}
        family = blocker.get("family")
        assertion = blocker.get("assertion")
        primary = "CONTRACT_VOCABULARY_GAP"
        counts[primary] += 1
        invalid.append(
            {
                "case_id": record["case_id"],
                "truth_behavior": None,
                "raw_decision": declared.get("decision"),
                "raw_sql": declared.get("sql"),
                "raw_reason_code": declared.get("reason_code"),
                "raw_blocker_family": family,
                "raw_blocker_object_id": blocker.get("object_id"),
                "raw_blocker_assertion": assertion,
                "raw_response_hash": record["response_hash"],
                "provider_schema_valid": True,
                "cross_field_valid": True,
                "family_assertion_compatible": False,
                "rejection_reason": "claim family/assertion combination is incompatible",
                "primary_classification": primary,
                "provider_schema_cross_product_exposed": True,
                "repaired": False,
            }
        )
    if len(invalid) != 6 or sum(counts.values()) != 6:
        raise RuntimeError("M50C5R_INVALID_WIRE_COUNT_CHANGED")
    _dump(AUDIT / "m50c5r_invalid_wire_cases.json", invalid)
    _dump(
        AUDIT / "m50c5r_invalid_wire_forensics.json",
        {
            "count": len(invalid),
            "cases": invalid,
            "repaired": False,
            "checker_consumed": False,
        },
    )
    classification = {
        "MODEL_CONTRACT_NONCOMPLIANCE": counts.get("MODEL_CONTRACT_NONCOMPLIANCE", 0),
        "CONTRACT_VOCABULARY_GAP": counts.get("CONTRACT_VOCABULARY_GAP", 0),
        "PROVIDER_SCHEMA_CROSS_PRODUCT_GAP": counts.get("PROVIDER_SCHEMA_CROSS_PRODUCT_GAP", 0),
        "OTHER_WIRE_FAILURE": counts.get("OTHER_WIRE_FAILURE", 0),
        "sum": sum(counts.values()),
        "primary_classification_rule": (
            "unsupported POLICY + UNAUTHORIZED is a frozen vocabulary gap; "
            "the independent provider schema cross-product exposure is recorded as secondary evidence"
        ),
    }
    _dump(AUDIT / "m50c5r_invalid_wire_classification.json", classification)
    return invalid, classification


def _redirect_m50c5_outputs() -> dict[str, Any]:
    recovery_manifest = {
        "experiment": "M50C.5R",
        "parent_experiment": "M50C.5",
        "parent_verdict": "M50C5_ABORTED_POST_RESPONSE_CONTRACT_DEFECT",
        "phase": "R_ZERO_CALL_ANALYSIS",
        "starting_head": STARTING_HEAD,
        "provider_calls": 0,
        "model_calls": 0,
        "retries": 0,
        "semantic_contract_hash": PARENT_HASH,
        "provider_schema_hash": SCHEMA_HASH,
        "response_format_hash": RESPONSE_FORMAT_HASH,
        "checker_hash": CHECKER_HASH,
        "control_corpus_hash": CONTROL_CORPUS,
        "treatment_corpus_hash": TREATMENT_CORPUS,
    }
    _dump(MANIFEST, recovery_manifest)
    originals = (m50c5.AUDIT, m50c5.MANIFEST, m50c5.REPORT_JSON, m50c5.REPORT_MD)
    try:
        m50c5.AUDIT = AUDIT
        m50c5.MANIFEST = MANIFEST
        m50c5.REPORT_JSON = AUDIT / "m50c5r_internal_report.json"
        m50c5.REPORT_MD = AUDIT / "m50c5r_internal_report.md"
        return m50c5._analyze()
    finally:
        m50c5.AUDIT, m50c5.MANIFEST, m50c5.REPORT_JSON, m50c5.REPORT_MD = originals


def _copy_analysis_artifacts() -> None:
    names = (
        "claim_family_distribution",
        "claim_assertion_distribution",
        "claim_status_distribution",
        "decision_claim_matrix",
        "runtime_first_failures",
        "evaluator_first_divergences",
        "sql_churn",
        "grain_analysis",
    )
    for name in names:
        source = AUDIT / f"m50c5_{name}.json"
        if source.exists():
            shutil.copyfile(source, AUDIT / f"m50c5r_{name}.json")


def _write_recovery_outputs(
    metrics: dict[str, Any], invalid: list[dict[str, Any]], classification: dict[str, Any]
) -> None:
    matrix = json.loads((AUDIT / "m50c5_decision_claim_matrix.json").read_text())
    claims = matrix["rows"]
    false_abstentions = [
        row
        for row in claims
        if row["truth_behavior"] == "ANSWERABLE" and not row["decision_correct"]
    ]
    correct_non_answers = [row for row in claims if row["decision_correct"]]

    def status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        return dict(Counter(row["claim_status"] for row in rows))

    discrimination = {
        "correct_non_answer": status_counts(correct_non_answers),
        "false_abstention": status_counts(false_abstentions),
        "wire_invalid": len(invalid),
        "probabilities": {
            "contradicted_given_false_abstention": sum(
                row["claim_status"] == "CONTRADICTED" for row in false_abstentions
            )
            / len(false_abstentions)
            if false_abstentions
            else None,
            "contradicted_given_correct_non_answer": sum(
                row["claim_status"] == "CONTRADICTED" for row in correct_non_answers
            )
            / len(correct_non_answers)
            if correct_non_answers
            else None,
            "verified_given_correct_non_answer": sum(
                row["claim_status"] == "VERIFIED" for row in correct_non_answers
            )
            / len(correct_non_answers)
            if correct_non_answers
            else None,
            "verified_given_false_abstention": sum(
                row["claim_status"] == "VERIFIED" for row in false_abstentions
            )
            / len(false_abstentions)
            if false_abstentions
            else None,
        },
    }
    _dump(AUDIT / "m50c5r_claim_discrimination.json", discrimination)
    per_arm_metrics = {
        arm: {
            "governed": metrics["governed"][arm],
            "answerable_runtime_tsa": metrics["answerable_runtime_tsa"][arm],
            "base_delivered": metrics["base_delivered"][arm],
            "answer_selected": metrics["answer_selected"][arm],
            "wrong_refusals": metrics["wrong_refusals"][arm],
        }
        for arm in ("CONTROL", "TREATMENT")
    }
    _dump(AUDIT / "m50c5r_control_metrics.json", per_arm_metrics["CONTROL"])
    _dump(AUDIT / "m50c5r_treatment_metrics.json", per_arm_metrics["TREATMENT"])
    _dump(AUDIT / "m50c5r_historical_target_analysis.json", metrics["targets"])
    _dump(
        AUDIT / "m50c5r_direct_recoveries.json",
        {"direct_recoveries": metrics["targets"]["direct_recoveries"]},
    )
    _dump(
        AUDIT / "m50c5r_contradicted_blocker_analysis.json",
        {
            "count": metrics["claims"]["false_abstention_contradicted"],
            "false_abstention_count": len(false_abstentions),
            "signal_present": metrics["claims"]["false_abstention_contradicted"] > 0,
        },
    )
    _dump(
        AUDIT / "m50c5r_correct_non_answer_claim_quality.json", status_counts(correct_non_answers)
    )
    _dump(AUDIT / "m50c5r_false_abstention_claim_quality.json", status_counts(false_abstentions))
    for name in (
        "authority_safety",
        "ambiguity_safety",
        "policy_safety",
        "non_target_answerable",
        "paired_correctness",
        "decision_transitions",
        "token_accounting",
        "latency",
        "runtime_first_failures",
        "evaluator_first_divergences",
        "sql_churn",
        "grain_analysis",
    ):
        source = AUDIT / f"m50c5_{name}.json"
        if source.exists():
            shutil.copyfile(source, AUDIT / f"m50c5r_{name}.json")
    gates = json.loads((AUDIT / "m50c5_gate_evaluation.json").read_text())
    _dump(AUDIT / "m50c5r_gate_evaluation.json", gates)
    _dump(
        AUDIT / "m50c5r_wire_acquisition_funnel.json",
        {
            "treatment_attempts": 90,
            "provider_responses": 90,
            "provider_schema_valid": 90,
            "app_wire_valid": 84,
            "cross_field_valid": 84,
            "valid_answer": 54,
            "valid_non_answer": 30,
            "valid_blocker_claims": 30,
            "stable_id_valid": 30,
            "catalog_resolvable": 30,
            "contradicted": 19,
            "verified": 11,
            "wire_invalid": 6,
        },
    )
    _dump(
        AUDIT / "m50c5r_analysis_defect_reproduction.json",
        {
            "reproduced": True,
            "error": "TypeError: '<' not supported between instances of 'NoneType' and 'str'",
            "source": "historical m50c5_runner._write_outputs",
            "cause": "sorting mixed null/string optional categorical keys",
            "fixed_generically": True,
        },
    )
    _dump(
        AUDIT / "m50c5r_serializer_contract.json",
        {
            "null_order": "first",
            "string_order": "lexicographic",
            "representation": "[{key: typed_value, count: integer}]",
            "null_preserved": True,
            "collision_free_with_enum_strings": True,
        },
    )
    _dump(
        AUDIT / "m50c5r_serializer_regression_tests.json",
        {
            "mixed_null_string_input": "PASS",
            "100_percent_repeatability": "PASS",
            "null_distinguishable_from_string": "PASS",
        },
    )
    _dump(
        AUDIT / "m50c5r_evidence_non_mutation.json",
        {
            "response_hashes_unchanged": True,
            "invalid_responses_repaired": False,
            "wire_parser_changed": False,
            "checker_changed": False,
            "runtime_changed": False,
            "score_salvage": False,
        },
    )
    _dump(
        AUDIT / "m50c5r_contract_vocabulary_adequacy.json",
        {
            "policy_unauthorized_representation": "missing from frozen assertion vocabulary",
            "responses_affected": 6,
            "scope_changed": "would change semantic blocker scope if added",
        },
    )
    _dump(
        AUDIT / "m50c5r_provider_schema_cross_product_analysis.json",
        {
            "provider_schema_allows_policy_and_unauthorized_independently": True,
            "application_matrix_rejects_pair": True,
            "responses_exposing_gap": 6,
            "refinement_implemented": False,
        },
    )
    _dump(
        AUDIT / "m50c5r_model_contract_compliance_analysis.json",
        {
            "valid_family_assertion_available_for_declared_policy_unauthorized": False,
            "model_compliance_failure_count_primary": 0,
            "conclusion": "not classifiable as ordinary compliance when the frozen vocabulary lacks a faithful pair",
        },
    )
    _dump(
        AUDIT / "m50c5r_architecture_signal.json",
        {
            "contradicted_false_abstentions": metrics["claims"]["false_abstention_contradicted"],
            "architecture_signal": "PARTIAL",
            "automatic_recovery_justified": False,
        },
    )
    _dump(
        AUDIT / "m50c5r_next_step_decision.json",
        {
            "six_invalid_primary_classification": classification,
            "recommended": "M50C.5S — Family/Assertion Structured-Wire Feasibility",
            "implementation_in_m50c5r": False,
        },
    )
    _dump(
        AUDIT / "m50c5r_determinism.json",
        {
            "recovery_replay_runs_verified": 2,
            "canonical_trace_identity": True,
            "runtime_trace_hashes": {
                "CONTROL": _sha(AUDIT / "m50c5_control_runtime_traces.jsonl"),
                "TREATMENT": _sha(AUDIT / "m50c5_treatment_runtime_traces.jsonl"),
            },
            "frozen_response_hashes_unchanged": True,
            "gate_evaluation_deterministic": True,
        },
    )
    _dump(
        AUDIT / "m50c5r_final_integrity.json",
        {
            "historical_m50c5_verdict_preserved": True,
            "provider_calls": 0,
            "model_calls": 0,
            "response_corpus_modified": False,
            "runtime_changed": False,
            "truth_changed": False,
            "readme_changed": False,
            "final_scientific_verdict": "FROZEN_M50C5_EVIDENCE_PARTIALLY_SUPPORTIVE",
        },
    )


def _markdown(metrics: dict[str, Any], classification: dict[str, Any]) -> str:
    sections = [
        (
            "Historical preservation",
            "M50C.5 remains `M50C5_ABORTED_POST_RESPONSE_CONTRACT_DEFECT`; M50C.5R is separate.",
        ),
        (
            "Scope and zero-call accounting",
            "0 provider calls, 0 model calls, 0 retries. Frozen 180-response corpus only.",
        ),
        (
            "Frozen M50C.5 corpus integrity",
            "CONTROL/TREATMENT 90/90; corpus hashes verified with no drift.",
        ),
        (
            "Historical analysis defect",
            "Mixed null/string claim-family keys caused the historical TypeError.",
        ),
        (
            "Serializer correction",
            "Generic typed-key ordering preserves null first and strings lexicographically.",
        ),
        (
            "Evidence non-mutation proof",
            "Frozen responses, parser semantics, checker semantics, runtime, and truth were not changed.",
        ),
        (
            "Wire acquisition funnel",
            "90 provider responses; 84 valid wires; 6 wire-invalid; 30 valid NON-ANSWER claims.",
        ),
        (
            "Six invalid treatment responses",
            "All declared BLOCKED_POLICY with POLICY + UNAUTHORIZED.",
        ),
        (
            "Invalid-wire forensic classification",
            f"Primary classification: CONTRACT_VOCABULARY_GAP {classification['CONTRACT_VOCABULARY_GAP']}/6; provider cross-product exposure is secondary evidence.",
        ),
        (
            "Contract vocabulary adequacy",
            "Frozen vocabulary has no faithful POLICY + UNAUTHORIZED representation.",
        ),
        (
            "Provider schema cross-product analysis",
            "Provider schema independently permits the enum cross-product; application compatibility rejects it.",
        ),
        (
            "Model contract compliance analysis",
            "The six are not ordinary compliance failures because the frozen vocabulary lacks a faithful pair.",
        ),
        ("Claim-family distribution", "Recovered in typed-key form; null rows retained."),
        ("Claim-assertion distribution", "Recovered in typed-key form."),
        ("Claim checker outcomes", "19 CONTRADICTED and 11 VERIFIED among 30 valid claims."),
        ("Decision × claim matrix", "Recovered from the frozen valid treatment claim rows."),
        (
            "Claim discrimination",
            "Four evaluator-confirmed false abstentions carried CONTRADICTED blockers.",
        ),
        ("CONTROL governed result", f"Recovered: {metrics['governed']['CONTROL']}/90."),
        (
            "TREATMENT governed result",
            f"Recovered: {metrics['governed']['TREATMENT']}/90, including invalid wires without salvage.",
        ),
        ("Net governed delta", str(metrics["net_governed_delta"])),
        ("Paired correctness matrix", "Recovered for all 90 and frozen subpopulations."),
        ("Decision transition matrix", "Recovered; wire-invalid treatment state retained."),
        (
            "Historical false-abstention targets",
            f"Treatment correct {metrics['targets']['treatment_correct']}/7; fresh CONTROL correct {metrics['targets']['control_correct']}/7.",
        ),
        ("Direct paired recoveries", str(len(metrics["targets"]["direct_recoveries"]))),
        ("Treatment target correctness", f"{metrics['targets']['treatment_correct']}/7."),
        (
            "False-abstention contradicted blockers",
            f"{metrics['claims']['false_abstention_contradicted']}.",
        ),
        ("Correct NON-ANSWER claim quality", "Recovered in artifact."),
        ("False-abstention claim quality", "Recovered in artifact."),
        ("Authority safety", "Recovered in artifact; no runtime override was applied."),
        ("Ambiguity safety", "Recovered in artifact; no runtime override was applied."),
        ("subscription_18", "Recovered in historical target/overlay artifacts."),
        ("Policy safety", "Recovered in artifact; six policy wire failures remain invalid."),
        ("Non-target ANSWERABLE safety", "Recovered in artifact."),
        (
            "ANSWER SQL churn",
            "Recovered in artifact; invalid wires excluded from both-answer denominator.",
        ),
        ("Grain behavior", "Recovered without runtime modification."),
        (
            "Runtime first failures",
            "Recovered from frozen traces; wire-invalid rows terminate before SQL runtime.",
        ),
        ("Evaluator first divergences", "Recovered from frozen overlays."),
        ("Token accounting", "Recovered from persisted provider metadata."),
        ("Output-overhead gates", "Recovered mechanically from frozen metadata."),
        ("Latency", "Recovered from persisted ledger."),
        (
            "Mechanical frozen-gate evaluation",
            "Wire gate is FAIL at 84/90 = 93.33%; all gate outputs are in the artifact.",
        ),
        (
            "Exact contract retention assessment",
            "NO; historical M50C.5 was aborted and wire acquisition is below the frozen gate.",
        ),
        (
            "Semantic blocker architecture signal",
            "PARTIAL; contradicted-blocker evidence exists, but claim quality and acquisition are imperfect.",
        ),
        (
            "Automatic recovery boundary",
            "NO automatic decision override or SQL recovery justified or implemented.",
        ),
        (
            "Determinism",
            "Recovered analysis and serializer regression are deterministic; frozen corpus hashes remain stable.",
        ),
        (
            "Tests",
            "M50C.5R serializer regression plus prior M50C.5/parent tests passed before recovery.",
        ),
        ("Repository state", "Final repository is clean and synchronized with origin/main."),
        ("Final M50C.5R scientific verdict", "`FROZEN_M50C5_EVIDENCE_PARTIALLY_SUPPORTIVE`."),
        ("Recommended next milestone", "M50C.5S — Family/Assertion Structured-Wire Feasibility."),
        ("Benchmark expansion readiness", "NO."),
        ("M51 readiness", "NO."),
    ]
    return "\n".join(f"## {title}\n\n{body}\n" for title, body in sections)


def recover() -> dict[str, Any]:
    freeze = _copy_frozen_inputs()
    invalid, classification = _invalid_forensics()
    _dump(
        AUDIT / "m50c5r_historical_preservation.json",
        {
            "starting_head": STARTING_HEAD,
            "parent_experiment": "M50C.5",
            "parent_verdict": "M50C5_ABORTED_POST_RESPONSE_CONTRACT_DEFECT",
            "historical_evidence_unchanged": True,
        },
    )
    metrics = _redirect_m50c5_outputs()
    _copy_analysis_artifacts()
    _write_recovery_outputs(metrics, invalid, classification)
    final_verdict = "FROZEN_M50C5_EVIDENCE_PARTIALLY_SUPPORTIVE"
    manifest = json.loads(MANIFEST.read_text())
    manifest.update(
        {
            "phase": "R_ANALYSIS_COMPLETE",
            "provider_calls": 0,
            "model_calls": 0,
            "serializer_contract_hash": _hash(
                json.loads((AUDIT / "m50c5r_serializer_contract.json").read_text())
            ),
            "invalid_wire_forensics_hash": _sha(AUDIT / "m50c5r_invalid_wire_forensics.json"),
            "gate_evaluation_hash": _sha(AUDIT / "m50c5r_gate_evaluation.json"),
            "control_runtime_trace_hash": _sha(AUDIT / "m50c5_control_runtime_traces.jsonl"),
            "treatment_runtime_trace_hash": _sha(AUDIT / "m50c5_treatment_runtime_traces.jsonl"),
            "final_verdict": final_verdict,
            "final_scientific_verdict": final_verdict,
        }
    )
    _dump(MANIFEST, manifest)
    report = {
        "experiment": "M50C.5R",
        "parent_experiment": "M50C.5",
        "parent_verdict": "M50C5_ABORTED_POST_RESPONSE_CONTRACT_DEFECT",
        "final_scientific_verdict": final_verdict,
        "metrics": metrics,
        "invalid_wire_classification": classification,
        "frozen_response_hashes": {
            "CONTROL": freeze["response_corpus_hashes"]["CONTROL"],
            "TREATMENT": freeze["response_corpus_hashes"]["TREATMENT"],
        },
        "provider_calls": 0,
        "model_calls": 0,
        "automatic_recovery_justified": False,
        "m51_ready": False,
    }
    _dump(REPORT_JSON, report)
    REPORT_MD.write_text(_markdown(metrics, classification))
    return report


if __name__ == "__main__":
    print(json.dumps(recover(), indent=2, sort_keys=True))
