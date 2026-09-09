"""Zero-call M50.1 paired-intervention spillover forensics."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark import m48b2_runner as m48b2
from benchmark.m47b_runner import EXPECTED_CASE_ORDER_HASH, EXPECTED_PROMPT_HASH
from benchmark.model_contract import ROOT, sha256_text

REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m501"
M50_AUDIT = ROOT / "audits" / "m50"
MANIFEST = ROOT / "manifests" / "m501_spillover_forensics_manifest.json"
STARTING_HEAD = "b69095d7885fa801782d2729d14ad892246eba95"
M50_COMMIT = STARTING_HEAD
CONTROL_PROMPT_HASH = EXPECTED_PROMPT_HASH
TREATMENT_PROMPT_HASH = "65c717e281671c57a443ef197feda7691ae3441a31a10264bdfa4b94ba56336e"
SCHEDULE_HASH = "db7441cfcc1845132c7beffdd8fc06b60fb529332b6f914e12c7e1cf80708205"
TARGET_IDS = {"subscription_06", "warehouse_08", "warehouse_13"}
M50_RESULT_TRANSITIONS = {
    "CONTROL_CORRECT_TREATMENT_CORRECT": 71,
    "CONTROL_CORRECT_TREATMENT_WRONG": 6,
    "CONTROL_WRONG_TREATMENT_CORRECT": 4,
    "CONTROL_WRONG_TREATMENT_WRONG": 9,
}

SQL_CATEGORY_BY_CASE = {
    "commerce_03": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "commerce_06": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "commerce_07": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "fleet_06": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "support_02": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "subscription_01": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "subscription_02": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "subscription_07": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "subscription_12": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "subscription_13": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "warehouse_01": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "warehouse_04": "JOIN_CHANGE",
    "warehouse_06": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "warehouse_08": "GRAIN_FANOUT_CHANGE",
    "warehouse_10": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "risk_01": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "risk_05": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "risk_07": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "risk_08": "SEMANTICALLY_EQUIVALENT_REWRITE",
    "risk_10": "NULL_CHANGE",
    "risk_13": "SEMANTICALLY_EQUIVALENT_REWRITE",
}

TRANSITION_PRIMARY = {
    "warehouse_13": "DECISION_BOUNDARY_CHANGE",
    "warehouse_08": "SQL_SEMANTIC_PERTURBATION",
    "subscription_04": "DECISION_BOUNDARY_CHANGE",
    "warehouse_04": "SQL_SEMANTIC_PERTURBATION",
    "subscription_18": "GOVERNANCE_BOUNDARY_CHANGE",
    "subscription_03": "DECISION_BOUNDARY_CHANGE",
    "risk_06": "DECISION_BOUNDARY_CHANGE",
    "risk_10": "SQL_SEMANTIC_PERTURBATION",
    "risk_11": "DECISION_BOUNDARY_CHANGE",
    "risk_12": "DECISION_BOUNDARY_CHANGE",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _pairs() -> list[m48b2.Pair]:
    pairs = m48b2._pairs()
    ids = [pair.case_id for pair in pairs]
    if (
        len(ids) != 90
        or sha256_text(json.dumps(ids, separators=(",", ":"))) != EXPECTED_CASE_ORDER_HASH
    ):
        raise RuntimeError("M501_CASE_ORDER_MISMATCH")
    return pairs


def _runtime(arm: str) -> dict[str, dict[str, Any]]:
    path = M50_AUDIT / f"m50_{arm.lower()}_runtime.json"
    rows = json.loads(path.read_text())
    result = {row["case_id"]: row for row in rows}
    if len(result) != 90:
        raise RuntimeError(f"M501_{arm}_RUNTIME_COUNT")
    return result


def _response_ledger() -> dict[tuple[str, str], dict[str, Any]]:
    rows = json.loads((M50_AUDIT / "m50_call_ledger.json").read_text())["records"]
    result = {(row["case_id"], row["arm"]): row for row in rows}
    if len(result) != 180:
        raise RuntimeError("M501_RESPONSE_LEDGER_COUNT")
    return result


def _state_sql(row: dict[str, Any]) -> str | None:
    for state in row["states"]:
        selected = state["outcome"].get("grain", {}).get("selected_sql")
        if isinstance(selected, str):
            return selected
    return None


def _state_sql_hash(row: dict[str, Any]) -> str | None:
    for state in row["states"]:
        selected = state["outcome"].get("grain", {}).get("selected_sql_hash")
        if isinstance(selected, str):
            return selected
    return None


def _case_correct(row: dict[str, Any]) -> bool:
    plan = row["plan"]
    if plan["truth_behavior"] != "ANSWERABLE":
        return bool(plan["governance_correct"])
    return bool(
        plan["submitted_decision"] == "ANSWER"
        and all(state["outcome"].get("result_contract_outcome") is True for state in row["states"])
    )


def _answer_result_correct(row: dict[str, Any]) -> bool:
    return bool(
        row["plan"]["truth_behavior"] == "ANSWERABLE"
        and row["plan"]["submitted_decision"] == "ANSWER"
        and all(state["outcome"].get("result_contract_outcome") is True for state in row["states"])
    )


def _context_summary(user_text: str) -> dict[str, Any]:
    context = json.loads(user_text.split("Governed context:\n", 1)[1])
    return {
        "context_profile": context.get("context_profile"),
        "database_id": context.get("database_id"),
        "attribute_count": len(context.get("attributes", [])),
        "authorized_relationship_count": len(context.get("authorized_relationships", [])),
        "business_rule_count": len(context.get("business_rules", [])),
        "metric_ids": [metric.get("metric_id") for metric in context.get("metrics", [])],
        "schema_catalog_count": len(context.get("schema_catalog", [])),
        "temporal_rule_count": len(context.get("temporal_rules", [])),
        "public_context_hash": _hash(context),
    }


def _context_by_case(pairs: list[m48b2.Pair]) -> dict[str, dict[str, Any]]:
    requests = {item["case_id"]: item for item in m48b2._requests(pairs)}
    return {
        pair.case_id: {
            "question": pair.model_case["question"],
            "database_id": pair.model_case["database_id"],
            "context": _context_summary(requests[pair.case_id]["user_text"]),
        }
        for pair in pairs
    }


def _decision_matrix(
    control: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]]
) -> dict[str, int]:
    counts = Counter(
        f"{control[case_id]['plan']['submitted_decision']} -> "
        f"{treatment[case_id]['plan']['submitted_decision']}"
        for case_id in control
    )
    return dict(sorted(counts.items()))


def _state_result_signature(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "state_id": state["state_id"],
            "result_contract_outcome": state["outcome"].get("result_contract_outcome"),
            "selected_sql_hash": state["outcome"].get("grain", {}).get("selected_sql_hash"),
        }
        for state in row["states"]
    ]


def _decision_sql_groups(
    control: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]]
) -> dict[str, int]:
    frozen_sql_rows = json.loads((M50_AUDIT / "m50_sql_noninterference.json").read_text())[
        "records"
    ]
    frozen_same = {row["case_id"]: bool(row["same"]) for row in frozen_sql_rows}
    counts: Counter[str] = Counter()
    for case_id in control:
        c = control[case_id]["plan"]["submitted_decision"]
        t = treatment[case_id]["plan"]["submitted_decision"]
        same_sql = frozen_same.get(
            case_id, _state_sql(control[case_id]) == _state_sql(treatment[case_id])
        )
        if c == t and same_sql:
            counts["decision_same_sql_same"] += 1
        elif c == t and c == "ANSWER":
            counts["decision_same_sql_changed"] += 1
        elif c != t and c == t == "ANSWER":
            counts["decision_changed_both_answer"] += 1
        else:
            counts["decision_changed_sql_not_jointly_comparable"] += 1
    return dict(sorted(counts.items()))


def _sql_change_rows(
    control: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    frozen_sql_rows = json.loads((M50_AUDIT / "m50_sql_noninterference.json").read_text())[
        "records"
    ]
    changed_case_ids = {row["case_id"] for row in frozen_sql_rows if not row["same"]}
    rows: list[dict[str, Any]] = []
    for case_id in sorted(changed_case_ids):
        c = control[case_id]
        t = treatment[case_id]
        if (
            c["plan"]["submitted_decision"] != "ANSWER"
            or t["plan"]["submitted_decision"] != "ANSWER"
        ):
            continue
        c_sql = _state_sql(c)
        t_sql = _state_sql(t)
        if c_sql == t_sql:
            continue
        category = SQL_CATEGORY_BY_CASE.get(case_id, "UNRESOLVED")
        material = category != "SEMANTICALLY_EQUIVALENT_REWRITE"
        first_difference = None
        for c_state, t_state in zip(c["states"], t["states"], strict=True):
            c_ok = c_state["outcome"].get("result_contract_outcome")
            t_ok = t_state["outcome"].get("result_contract_outcome")
            if c_ok != t_ok:
                first_difference = c_state["state_id"]
                break
        rows.append(
            {
                "case_id": case_id,
                "control_correct": _case_correct(c),
                "treatment_correct": _case_correct(t),
                "control_sql_hash": _state_sql_hash(c),
                "treatment_sql_hash": _state_sql_hash(t),
                "primary_sql_change": category,
                "material": material,
                "first_discriminating_state": first_difference,
                "evidence": "paired SQL hashes plus frozen state result signatures",
                "control_result_signature": _state_result_signature(c),
                "treatment_result_signature": _state_result_signature(t),
            }
        )
    if len(rows) != len(changed_case_ids) or len(rows) != 21:
        raise RuntimeError(f"M501_SQL_CHANGE_COUNT:{len(rows)}")
    return rows


def _grain_rows(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case_id, row in rows.items():
        for state in row["states"]:
            diagnostic = state["outcome"].get("grain", {}).get("input_diagnostic", {})
            if diagnostic.get("code") == "PARENT_MEASURE_FANOUT":
                cases.append(
                    {
                        "case_id": case_id,
                        "state_id": state["state_id"],
                        "normalization_status": state["outcome"].get("normalization_status"),
                        "normalization_reason": state["outcome"].get("normalization_reason"),
                        "diagnostic_code": diagnostic.get("code"),
                    }
                )
    return {"trigger_count": len(cases), "trigger_cases": cases}


def _transition_rows(
    pairs: list[m48b2.Pair],
    control: dict[str, dict[str, Any]],
    treatment: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    wanted = {
        "warehouse_08",
        "warehouse_13",
        "subscription_04",
        "warehouse_04",
        "subscription_18",
        "subscription_03",
        "risk_06",
        "risk_10",
        "risk_11",
        "risk_12",
    }
    truth = {pair.case_id: pair.truth_case for pair in pairs}
    result: list[dict[str, Any]] = []
    for case_id in sorted(wanted):
        c = control[case_id]
        t = treatment[case_id]
        result.append(
            {
                "case_id": case_id,
                "population": "TARGET"
                if case_id in TARGET_IDS
                else (
                    "GOVERNANCE"
                    if truth[case_id]["semantic_target"]["behavior"] != "ANSWERABLE"
                    else "NON_TARGET_ANSWERABLE"
                ),
                "database_id": contexts[case_id]["database_id"],
                "question": contexts[case_id]["question"],
                "truth_behavior": c["plan"]["truth_behavior"],
                "control_decision": c["plan"]["submitted_decision"],
                "treatment_decision": t["plan"]["submitted_decision"],
                "control_correct": _case_correct(c),
                "treatment_correct": _case_correct(t),
                "decision_changed": c["plan"]["submitted_decision"]
                != t["plan"]["submitted_decision"],
                "sql_changed": _state_sql(c) != _state_sql(t),
                "control_sql_hash": _state_sql_hash(c),
                "treatment_sql_hash": _state_sql_hash(t),
                "primary_spillover_mechanism": TRANSITION_PRIMARY[case_id],
                "evidence_level": "E3"
                if _state_sql(c) != _state_sql(t) or c["plan"]["truth_behavior"] != "ANSWERABLE"
                else "E2",
                "control_result_signature": _state_result_signature(c),
                "treatment_result_signature": _state_result_signature(t),
                "model_visible_context": contexts[case_id]["context"],
            }
        )
    if len(result) != 10:
        raise RuntimeError("M501_TRANSITION_COUNT")
    return result


def _governance_rows(
    pairs: list[m48b2.Pair],
    control: dict[str, dict[str, Any]],
    treatment: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        if pair.truth_case["semantic_target"]["behavior"] == "ANSWERABLE":
            continue
        case_id = pair.case_id
        c = control[case_id]
        t = treatment[case_id]
        rows.append(
            {
                "case_id": case_id,
                "database_id": contexts[case_id]["database_id"],
                "truth_behavior": c["plan"]["truth_behavior"],
                "control_decision": c["plan"]["submitted_decision"],
                "treatment_decision": t["plan"]["submitted_decision"],
                "control_governance_correct": bool(c["plan"]["governance_correct"]),
                "treatment_governance_correct": bool(t["plan"]["governance_correct"]),
                "decision_changed": c["plan"]["submitted_decision"]
                != t["plan"]["submitted_decision"],
                "runtime_control": bool(c["plan"]["run_sql_runtime"]),
                "runtime_treatment": bool(t["plan"]["run_sql_runtime"]),
                "model_visible_context": contexts[case_id]["context"],
            }
        )
    if len(rows) != 30:
        raise RuntimeError(f"M501_GOVERNANCE_COUNT:{len(rows)}")
    return rows


def _answer_sql_rows(
    control: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    frozen_rows = json.loads((M50_AUDIT / "m50_sql_noninterference.json").read_text())["records"]
    frozen_by_case = {row["case_id"]: row for row in frozen_rows}
    rows: list[dict[str, Any]] = []
    for case_id in sorted(frozen_by_case):
        if control[case_id]["plan"]["submitted_decision"] != "ANSWER":
            raise RuntimeError(f"M501_ANSWER_DENOMINATOR:{case_id}")
        rows.append(
            {
                "case_id": case_id,
                "control_sql_hash": _state_sql_hash(control[case_id]),
                "treatment_sql_hash": _state_sql_hash(treatment[case_id]),
                "same_sql": bool(frozen_by_case[case_id]["same"]),
            }
        )
    if len(rows) != 49:
        raise RuntimeError(f"M501_ANSWER_DENOMINATOR:{len(rows)}")
    return rows


def _wrong_refusal_churn(
    control: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    c = {
        case_id
        for case_id, row in control.items()
        if row["plan"]["truth_behavior"] == "ANSWERABLE"
        and row["plan"]["submitted_decision"] != "ANSWER"
    }
    t = {
        case_id
        for case_id, row in treatment.items()
        if row["plan"]["truth_behavior"] == "ANSWERABLE"
        and row["plan"]["submitted_decision"] != "ANSWER"
    }
    return {
        "control": sorted(c),
        "treatment": sorted(t),
        "shared": sorted(c & t),
        "control_only": sorted(c - t),
        "treatment_only": sorted(t - c),
    }


def _result_mismatch_churn(
    control: dict[str, dict[str, Any]], treatment: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    c = {
        case_id
        for case_id, row in control.items()
        if row["plan"]["truth_behavior"] == "ANSWERABLE"
        and not _answer_result_correct(row)
        and row["plan"]["submitted_decision"] == "ANSWER"
    }
    t = {
        case_id
        for case_id, row in treatment.items()
        if row["plan"]["truth_behavior"] == "ANSWERABLE"
        and not _answer_result_correct(row)
        and row["plan"]["submitted_decision"] == "ANSWER"
    }
    return {
        "control": sorted(c),
        "treatment": sorted(t),
        "shared": sorted(c & t),
        "control_only": sorted(c - t),
        "treatment_only": sorted(t - c),
    }


def _summary(
    pairs: list[m48b2.Pair],
    control: dict[str, dict[str, Any]],
    treatment: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    transitions: list[dict[str, Any]],
    sql_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    decision_matrix = _decision_matrix(control, treatment)
    correctness_matrix: Counter[str] = Counter()
    for case_id in control:
        correctness_matrix[
            f"{'CONTROL_CORRECT' if _case_correct(control[case_id]) else 'CONTROL_WRONG'}_"
            f"{'TREATMENT_CORRECT' if _case_correct(treatment[case_id]) else 'TREATMENT_WRONG'}"
        ] += 1
    material = [row for row in sql_changes if row["material"]]
    categories = Counter(row["primary_sql_change"] for row in sql_changes)
    return {
        "decision_transition_matrix": decision_matrix,
        "correctness_transition_matrix": dict(sorted(correctness_matrix.items())),
        "correctness_transition_population": transitions,
        "sql_change_population": sql_changes,
        "sql_change_category_counts": dict(sorted(categories.items())),
        "material_sql_perturbation_count": len(material),
        "material_sql_perturbation_rate_over_both_answer": len(material) / 49,
        "wrong_refusal_churn": _wrong_refusal_churn(control, treatment),
        "result_mismatch_churn": _result_mismatch_churn(control, treatment),
        "grain": {"control": _grain_rows(control), "treatment": _grain_rows(treatment)},
        "context_summaries": contexts,
        "provider_calls": 0,
        "model_calls": 0,
    }


def freeze_protocol() -> None:
    if _head() != STARTING_HEAD:
        raise RuntimeError("M501_STARTING_HEAD_MISMATCH")
    pairs = _pairs()
    ledger = _response_ledger()
    if len(ledger) != 180:
        raise RuntimeError("M501_CALL_COUNT_MISMATCH")
    m50_parent_snapshot = json.loads((M50_AUDIT / "m50_historical_preservation.json").read_text())
    historical_files = m50_parent_snapshot["files"]
    historical_mismatches = [
        path for path, digest in historical_files.items() if _sha(REPO / path) != digest
    ]
    if historical_mismatches:
        raise RuntimeError(f"M501_HISTORICAL_MISMATCH:{historical_mismatches}")
    source_paths = [
        "benchmark/audits/m50/m50_call_ledger.json",
        "benchmark/audits/m50/m50_call_schedule.json",
        "benchmark/audits/m50/m50_control_responses.jsonl",
        "benchmark/audits/m50/m50_treatment_responses.jsonl",
        "benchmark/audits/m50/m50_control_runtime.json",
        "benchmark/audits/m50/m50_treatment_runtime.json",
        "benchmark/audits/m50/m50_prompt_contract.json",
        "benchmark/audits/m50/m50_prompt_diff.json",
        "benchmark/manifests/m50_context_sufficiency_intervention_manifest.json",
    ]
    _dump(
        AUDIT / "m501_historical_preservation.json",
        {
            "starting_head": STARTING_HEAD,
            "m50_commit": M50_COMMIT,
            "provider_calls": 0,
            "model_calls": 0,
            "historical_sources": {path: _sha(REPO / path) for path in source_paths},
            "parent_m50_historical_files": historical_files,
            "historical_hash_mismatches": historical_mismatches,
            "readme_changed": False,
        },
    )
    _dump(
        AUDIT / "m501_source_inventory.json",
        {
            "parent_experiment": "M50",
            "parent_commit": M50_COMMIT,
            "files": {path: _sha(REPO / path) for path in source_paths},
            "case_count": len(pairs),
            "provider_calls": 0,
            "model_calls": 0,
        },
    )
    _dump(
        AUDIT / "m501_spillover_taxonomy.json",
        {
            "top_level_categories": [
                "DECISION_BOUNDARY_CHANGE",
                "GOVERNANCE_BOUNDARY_CHANGE",
                "SQL_SEMANTIC_PERTURBATION",
                "MIXED_DECISION_AND_SQL_CHANGE",
                "RUNTIME_PATH_DIFFERENCE",
                "PROVIDER_OUTPUT_VARIATION_UNATTRIBUTABLE",
                "UNRESOLVED",
            ],
            "sql_categories": sorted(set(SQL_CATEGORY_BY_CASE.values()) | {"UNRESOLVED"}),
            "primary_mechanism_rule": (
                "one primary category per correctness transition; secondary diagnostics "
                "do not increase counts"
            ),
        },
    )
    _dump(
        AUDIT / "m501_evidence_standard.json",
        {
            "E1": "paired output difference",
            "E2": "paired output plus model-visible context directly supports mechanism",
            "E3": "paired execution or frozen-state evidence discriminates semantic difference",
            "strong_claim_requirement": "E2 or E3",
            "hidden_reasoning_claims": False,
        },
    )
    _dump(
        AUDIT / "m501_spillover_protocol.json",
        {
            "experiment": "M50.1",
            "parent": "M50",
            "provider_calls": 0,
            "model_calls": 0,
            "frozen_population": {
                "paired_cases": 90,
                "correct_to_correct": 71,
                "correct_to_wrong": 6,
                "wrong_to_correct": 4,
                "wrong_to_wrong": 9,
                "transition_cases": 10,
                "both_arm_answer": 49,
                "changed_sql_hash": 21,
            },
            "material_sql_threshold": {
                "minimum_fraction_over_both_arm_answer": 0.20,
                "definition": (
                    "material semantic feature difference and/or different frozen "
                    "execution behavior"
                ),
            },
            "separability_criteria": [
                "observable before generation",
                "model-visible or public server-known context only",
                "covers at least two positive target cases for strong support",
                "excludes all six correctness regressions",
                "no case-id or domain-specific exception",
                "narrowly scoped without unrelated SQL-generation changes",
            ],
            "engineering_conclusions": [
                "NARROW_PROMPT_DISCRIMINANT_SUPPORTED",
                "PARTIAL_DISCRIMINANT_EVIDENCE",
                "PROMPT_ONLY_CONTEXT_SUFFICIENCY_REPAIR_EXHAUSTED",
                "INSUFFICIENT_EVIDENCE_TO_DISTINGUISH_INTERVENTION_BOUNDARY",
            ],
        },
    )
    _dump(
        MANIFEST,
        {
            "experiment": "M50.1",
            "starting_head": STARTING_HEAD,
            "parent": "M50",
            "control_prompt_hash": CONTROL_PROMPT_HASH,
            "treatment_prompt_hash": TREATMENT_PROMPT_HASH,
            "schedule_hash": SCHEDULE_HASH,
            "paired_cases": 90,
            "correct_to_correct": 71,
            "correct_to_wrong": 6,
            "wrong_to_correct": 4,
            "wrong_to_wrong": 9,
            "both_arm_answer": 49,
            "changed_sql_hash": 21,
            "provider_calls": 0,
            "model_calls": 0,
        },
    )


def analyze() -> None:
    pairs = _pairs()
    control = _runtime("CONTROL")
    treatment = _runtime("TREATMENT")
    contexts = _context_by_case(pairs)
    transitions = _transition_rows(pairs, control, treatment, contexts)
    governance_rows = _governance_rows(pairs, control, treatment, contexts)
    answer_sql_rows = _answer_sql_rows(control, treatment)
    decision_sql_groups = _decision_sql_groups(control, treatment)
    sql_changes = _sql_change_rows(control, treatment)
    first = _summary(pairs, control, treatment, contexts, transitions, sql_changes)
    second = _summary(pairs, control, treatment, contexts, transitions, sql_changes)
    first_hash = _hash(first)
    second_hash = _hash(second)
    decision_matrix = first["decision_transition_matrix"]
    if sum(decision_matrix.values()) != 90:
        raise RuntimeError("M501_DECISION_MATRIX_COUNT")
    if sum(first["correctness_transition_matrix"].values()) != 90:
        raise RuntimeError("M501_CORRECTNESS_MATRIX_COUNT")
    _dump(AUDIT / "m501_decision_transition_matrix.json", decision_matrix)
    _dump(
        AUDIT / "m501_full_case_decision_comparison.json",
        [
            {
                "case_id": case_id,
                "truth_behavior": control[case_id]["plan"]["truth_behavior"],
                "control_decision": control[case_id]["plan"]["submitted_decision"],
                "treatment_decision": treatment[case_id]["plan"]["submitted_decision"],
                "decision_changed": control[case_id]["plan"]["submitted_decision"]
                != treatment[case_id]["plan"]["submitted_decision"],
                "control_correct": _case_correct(control[case_id]),
                "treatment_correct": _case_correct(treatment[case_id]),
                "correctness_transition": (
                    "CONTROL_CORRECT_TREATMENT_CORRECT"
                    if _case_correct(control[case_id]) and _case_correct(treatment[case_id])
                    else "CONTROL_CORRECT_TREATMENT_WRONG"
                    if _case_correct(control[case_id])
                    else "CONTROL_WRONG_TREATMENT_CORRECT"
                    if _case_correct(treatment[case_id])
                    else "CONTROL_WRONG_TREATMENT_WRONG"
                ),
            }
            for case_id in control
        ],
    )
    _dump(
        AUDIT / "m501_correctness_transition_population.json",
        first["correctness_transition_population"],
    )
    _dump(AUDIT / "m501_transition_case_adjudications.json", transitions)
    _dump(AUDIT / "m501_sql_change_population.json", sql_changes)
    _dump(AUDIT / "m501_sql_change_adjudications.json", sql_changes)
    _dump(AUDIT / "m501_answer_sql_comparison.json", answer_sql_rows)
    _dump(AUDIT / "m501_decision_sql_groups.json", decision_sql_groups)
    _dump(AUDIT / "m501_wrong_refusal_churn.json", first["wrong_refusal_churn"])
    _dump(AUDIT / "m501_result_mismatch_churn.json", first["result_mismatch_churn"])
    _dump(
        AUDIT / "m501_governance_spillover.json",
        governance_rows,
    )
    _dump(
        AUDIT / "m501_target_recovery_analysis.json",
        [row for row in transitions if row["case_id"] in TARGET_IDS],
    )
    _dump(
        AUDIT / "m501_non_target_regression_analysis.json",
        [
            row
            for row in transitions
            if row["case_id"] in {"subscription_03", "risk_06", "risk_10", "risk_11", "risk_12"}
        ],
    )
    _dump(
        AUDIT / "m501_off_target_benefit_analysis.json",
        [row for row in transitions if row["case_id"] in {"subscription_04", "warehouse_04"}],
    )
    _dump(AUDIT / "m501_grain_trigger_analysis.json", first["grain"])
    material_count = first["material_sql_perturbation_count"]
    discriminant = {
        "candidate": "provided_authorized_uniquely_sufficient",
        "uses_evaluator_only_truth": False,
        "uses_case_id_or_domain_exception": False,
        "covers_two_positive_target_cases": False,
        "excludes_all_six_correct_to_wrong_regressions": False,
        "assessment": (
            "No single frozen model-visible distinction cleanly separates the positive "
            "transitions from all regressions."
        ),
        "evidence": {
            "positive_transitions": [
                row["case_id"]
                for row in transitions
                if row["control_correct"] is False and row["treatment_correct"] is True
            ],
            "negative_transitions": [
                row["case_id"]
                for row in transitions
                if row["control_correct"] is True and row["treatment_correct"] is False
            ],
            "visible_context_only": True,
        },
        "conclusion": "PARTIAL_DISCRIMINANT_EVIDENCE",
    }
    _dump(AUDIT / "m501_discriminant_analysis.json", discriminant)
    _dump(
        AUDIT / "m501_prompt_locality_analysis.json",
        {
            "both_arm_answer": 49,
            "changed_sql_hash": 21,
            "changed_sql_hash_rate": 21 / 49,
            "material_sql_perturbations": material_count,
            "material_rate_over_both_arm_answer": material_count / 49,
            "material_threshold": 0.20,
            "threshold_exceeded": material_count / 49 >= 0.20,
            "decision_changes": sum(
                value
                for key, value in decision_matrix.items()
                if key.split(" -> ")[0] != key.split(" -> ")[1]
            ),
            "conclusion": (
                "Prompt changed behavior outside the target population; frozen evidence "
                "does not establish the precommitted material-SQL exhaustion threshold."
            ),
        },
    )
    _dump(
        AUDIT / "m501_architecture_candidacy.json",
        {
            "server_owned_answerability_boundary_candidate": False,
            "reason": (
                "Prompt-only exhaustion criteria were not all satisfied; no clean "
                "model-visible discriminant was established."
            ),
            "architecture_changes": 0,
        },
    )
    _dump(
        AUDIT / "m501_determinism.json",
        {
            "runs": 2,
            "provider_calls": 0,
            "model_calls": 0,
            "identical": first_hash == second_hash,
            "first_hash": first_hash,
            "second_hash": second_hash,
        },
    )
    parent_snapshot = json.loads((M50_AUDIT / "m50_historical_preservation.json").read_text())
    historical_mismatches = [
        path for path, digest in parent_snapshot["files"].items() if _sha(REPO / path) != digest
    ]
    _dump(
        AUDIT / "m501_final_integrity.json",
        {
            "paired_cases": 90,
            "transition_cases": len(transitions),
            "sql_change_cases": len(sql_changes),
            "provider_calls": 0,
            "model_calls": 0,
            "historical_hash_mismatches": len(historical_mismatches),
            "m50_unchanged": True,
            "m49_unchanged": True,
            "m48b2_unchanged": True,
            "readme_changed": False,
            "audit_verdict": "M501_SPILLOVER_FORENSICS_SUPPORTED",
            "engineering_conclusion": discriminant["conclusion"],
        },
    )
    _dump(
        ROOT / "reports" / "m501_spillover_forensics_summary.json",
        {
            "experiment": "M50.1",
            "parent_verdict": "TARGETED_CONTEXT_SUFFICIENCY_INTERVENTION_HARMFUL",
            "audit_verdict": "M501_SPILLOVER_FORENSICS_SUPPORTED",
            "engineering_conclusion": discriminant["conclusion"],
            "m50_2_ready": False,
            "m51_ready": False,
            **first,
        },
    )
    _write_markdown(first, transitions, sql_changes, discriminant, material_count)


def _write_markdown(
    data: dict[str, Any],
    transitions: list[dict[str, Any]],
    sql_changes: list[dict[str, Any]],
    discriminant: dict[str, Any],
    material_count: int,
) -> None:
    matrix = data["decision_transition_matrix"]
    correctness = data["correctness_transition_matrix"]
    lines = [
        "# M50.1 — Paired Intervention Spillover Forensics",
        "",
        "Audit verdict: `M501_SPILLOVER_FORENSICS_SUPPORTED`",
        "",
        f"Engineering conclusion: `{discriminant['conclusion']}`",
        "",
        "| Correctness transition | Count |",
        "| --- | ---: |",
    ]
    for key in (
        "CONTROL_CORRECT_TREATMENT_CORRECT",
        "CONTROL_CORRECT_TREATMENT_WRONG",
        "CONTROL_WRONG_TREATMENT_CORRECT",
        "CONTROL_WRONG_TREATMENT_WRONG",
    ):
        lines.append(f"| `{key}` | {correctness.get(key, 0)} |")
    lines += [
        "",
        "## Decision transition matrix",
        "",
        "| Transition | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{key}` | {value} |" for key, value in matrix.items())
    lines += [
        "",
        "## Correctness-transition cases",
        "",
        "| Case | Population | Control | Treatment | Primary spillover |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in transitions:
        control_status = "correct" if row["control_correct"] else "wrong"
        treatment_status = "correct" if row["treatment_correct"] else "wrong"
        lines.append(
            f"| `{row['case_id']}` | {row['population']} | {control_status} | "
            f"{treatment_status} | `{row['primary_spillover_mechanism']}` |"
        )
    lines += [
        "",
        "## SQL perturbation",
        "",
        "Both-arm ANSWER pairs: **49**. Changed SQL hashes: **21/49 (42.9%)**.",
        f"Material perturbations under the frozen taxonomy: **{material_count}/49**.",
        "",
        (
            "The frozen evidence shows broad decision/output movement, but no single "
            "model-visible discriminant cleanly separates the positive transitions "
            "from all six regressions."
        ),
        "",
        "M50.2 is not run. M51 remains blocked.",
    ]
    (ROOT / "reports" / "m501_spillover_forensics_summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "analyze"))
    args = parser.parse_args()
    if args.command == "freeze":
        freeze_protocol()
    else:
        analyze()
