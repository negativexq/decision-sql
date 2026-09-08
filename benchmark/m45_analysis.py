"""Deterministic paired analysis for the M45 additive-alignment ablation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent
M43_ROOT = ROOT / "experiments" / "results" / "m43"
M45_ROOT = ROOT / "experiments" / "results" / "m45"
INVENTORY = ROOT / "audits" / "m45_additive_alignment_inventory.json"
PRESERVATION = ROOT / "reports" / "m45_historical_preservation.json"
PAIRED = M45_ROOT / "m45_paired_analysis.json"
ALIGNMENT = M45_ROOT / "m45_additive_alignment_analysis.json"

APPLICABLE = ["subscription_04", "subscription_10", "warehouse_08"]
JSON_CASES = [
    "fleet_01",
    "fleet_06",
    "support_04",
    "risk_04",
    "risk_08",
    "subscription_12",
    "warehouse_09",
]
JSON_TARGETS = ["fleet_06", "warehouse_09"]
OTHER_CONTROLS = ["fleet_04", "warehouse_04", "risk_10"]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _rows(run: str) -> list[dict[str, Any]]:
    return list(_load(ROOT / "experiments" / "results" / run / f"{run}_case_results.json"))


def _decision(row: dict[str, Any]) -> str:
    return str(row.get("model_decision") or "INVALID/NO_DECISION")


def _sql(row: dict[str, Any]) -> str:
    return str((row.get("parsed_submission") or {}).get("sql") or "")


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def subset(behavior: str) -> list[dict[str, Any]]:
        return [row for row in rows if row["gold_behavior"] == behavior]

    def typed(behavior: str) -> dict[str, int]:
        values = subset(behavior)
        return {
            "correct": sum(bool(row["official_correct"]) for row in values),
            "total": len(values),
        }

    answerable = subset("ANSWERABLE")
    answered = [row for row in answerable if _decision(row) == "ANSWER"]
    return {
        "governed": {
            "correct": sum(bool(row["official_correct"]) for row in rows),
            "total": len(rows),
        },
        "answerable": typed("ANSWERABLE"),
        "answer_rate": {"answered": len(answered), "total": len(answerable)},
        "wrong_refusal": {
            "wrong_refusals": len(answerable) - len(answered),
            "total": len(answerable),
        },
        "conditional_sql": {
            "correct": sum(bool(row["official_correct"]) for row in answered),
            "total": len(answered),
        },
        "authority": typed("AUTHORITY_BLOCKED"),
        "ambiguity": typed("AMBIGUOUS"),
        "policy": typed("POLICY_BLOCKED"),
        "unauthorized_answer": {
            "answered": sum(_decision(row) == "ANSWER" for row in subset("AUTHORITY_BLOCKED")),
            "total": len(subset("AUTHORITY_BLOCKED")),
        },
        "failure_counts": dict(Counter(row["official_category"] for row in rows)),
    }


def _split_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        split: _score([row for row in rows if row["split"] == split])
        for split in ("DEV", "CONFIRMATION")
    }


def _sql_features(sql: str) -> dict[str, int | bool]:
    empty = {
        "join_count": 0,
        "aggregate_count": 0,
        "subquery_count": 0,
        "cte_count": 0,
        "grouping_key_count": 0,
        "distinct_count": 0,
        "parsed": False,
    }
    if not sql:
        return empty
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return empty
    return {
        "join_count": len(list(tree.find_all(exp.Join))),
        "aggregate_count": len(list(tree.find_all(exp.AggFunc))),
        "subquery_count": len(list(tree.find_all(exp.Subquery))),
        "cte_count": len(list(tree.find_all(exp.CTE))),
        "grouping_key_count": sum(len(group.expressions) for group in tree.find_all(exp.Group)),
        "distinct_count": len(list(tree.find_all(exp.Distinct))),
        "parsed": True,
    }


def _alignment_status(case_id: str, sql: str) -> str:
    if not sql:
        return "WRONG_REFUSAL"
    upper = " ".join(sql.upper().split())
    if "SUM(DISTINCT" in upper or "AVG(DISTINCT" in upper:
        return "DISTINCT_VALUE_HACK"
    if case_id == "warehouse_08":
        if "SUM(POL.ORDERED_QTY)" in upper and "RECEIPTS" in upper:
            return "DIRECT_FANOUT_AGGREGATION"
        if "GROUP BY PO_LINE_ID" in upper and "SUM(R.RECEIVED_QTY)" in upper:
            return "CORRECT_PARENT_CHILD_ALIGNMENT"
        return "OTHER"
    if case_id in {"subscription_04", "subscription_10"}:
        if "GROUP BY PAYMENT_ID" in upper and "SUM" in upper:
            return "CORRECT_PARENT_CHILD_ALIGNMENT"
        return "OTHER"
    return "NOT_APPLICABLE"


def _distinct_status(case_id: str, sql: str) -> str:
    upper = " ".join(sql.upper().split())
    if "SUM(DISTINCT" in upper or "AVG(DISTINCT" in upper:
        return "DISTINCT_VALUE_HACK"
    if case_id == "risk_05" and "SELECT DISTINCT ALERT_ID" in upper:
        return "DISTINCT_EXISTENCE_VALID"
    if "DISTINCT" in upper:
        return "DISTINCT_UNRESOLVED"
    return "NONE"


def _attribution(old: dict[str, Any], new: dict[str, Any]) -> str:
    case_id = old["case_id"]
    old_correct = bool(old["official_correct"])
    new_correct = bool(new["official_correct"])
    if case_id in APPLICABLE and old_correct != new_correct:
        return "DIRECT_ADDITIVE_ALIGNMENT_EFFECT"
    if case_id in JSON_CASES and old_correct and not new_correct:
        return "JSON_REGRESSION"
    if old["gold_behavior"] != "ANSWERABLE" and old_correct and not new_correct:
        return "GOVERNANCE_REGRESSION"
    if old_correct != new_correct:
        return "UNRELATED_VARIATION"
    return "UNCHANGED_CORRECT" if new_correct else "UNCHANGED_INCORRECT"


def _transition(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_sql = _sql(old)
    new_sql = _sql(new)
    return {
        "case_id": old["case_id"],
        "database_id": old["database_id"],
        "split": old["split"],
        "gold_behavior": old["gold_behavior"],
        "m43_decision": _decision(old),
        "m45_decision": _decision(new),
        "m43_category": old["official_category"],
        "m45_category": new["official_category"],
        "m43_correct": bool(old["official_correct"]),
        "m45_correct": bool(new["official_correct"]),
        "sql_changed": old_sql != new_sql,
        "m43_sql_sha256": hashlib.sha256(old_sql.encode()).hexdigest(),
        "m45_sql_sha256": hashlib.sha256(new_sql.encode()).hexdigest(),
        "attribution": _attribution(old, new),
    }


def main() -> None:
    m43_rows = _rows("m43")
    m45_rows = _rows("m45")
    old = {row["case_id"]: row for row in m43_rows}
    new = {row["case_id"]: row for row in m45_rows}
    if len(old) != 90 or len(new) != 90 or set(old) != set(new):
        raise RuntimeError("M45_ANALYSIS_CASE_SET_MISMATCH")
    inventory = _load(INVENTORY)
    if inventory["counts"].get("APPLICABLE") != 3 or inventory["uncertain_cases"]:
        raise RuntimeError("M45_ANALYSIS_INVENTORY_MISMATCH")

    transitions = [_transition(old[case_id], new[case_id]) for case_id in sorted(new)]
    applicable_rows = []
    for case_id in APPLICABLE:
        applicable_rows.append(
            {
                "case_id": case_id,
                "classification": "APPLICABLE",
                "m43_sql": _sql(old[case_id]),
                "m45_sql": _sql(new[case_id]),
                "m43_status": _alignment_status(case_id, _sql(old[case_id])),
                "m45_status": _alignment_status(case_id, _sql(new[case_id])),
                "m43_correct": bool(old[case_id]["official_correct"]),
                "m45_correct": bool(new[case_id]["official_correct"]),
                "m43_category": old[case_id]["official_category"],
                "m45_category": new[case_id]["official_category"],
                "m43_features": _sql_features(_sql(old[case_id])),
                "m45_features": _sql_features(_sql(new[case_id])),
                "m45_distinct_status": _distinct_status(case_id, _sql(new[case_id])),
            }
        )

    target = next(row for row in applicable_rows if row["case_id"] == "warehouse_08")
    target.update(
        {
            "parent_key": "purchase_order_lines.po_line_id",
            "parent_measure": "purchase_order_lines.ordered_qty",
            "child_relation": "receipts via receipts.po_line_id",
            "child_measure": "receipts.received_qty",
            "output_grain": "product_id",
            "m45_applicability_satisfied": True,
            "uses_child_aggregation_by_parent_key": False,
            "combines_parent_measure_once_per_parent": False,
            "fixed": False,
        }
    )

    negative_controls = []
    for case_id in ["risk_05"]:
        negative_controls.append(
            {
                "case_id": case_id,
                "m43_correct": bool(old[case_id]["official_correct"]),
                "m45_correct": bool(new[case_id]["official_correct"]),
                "m43_sql": _sql(old[case_id]),
                "m45_sql": _sql(new[case_id]),
                "m45_distinct_status": _distinct_status(case_id, _sql(new[case_id])),
                "regressed": bool(old[case_id]["official_correct"])
                and not bool(new[case_id]["official_correct"]),
                "applicability": "NOT_APPLICABLE_EXISTENCE_OR_FRACTION",
            }
        )

    json_rows = [
        {
            "case_id": case_id,
            "m43_correct": bool(old[case_id]["official_correct"]),
            "m45_correct": bool(new[case_id]["official_correct"]),
            "m43_sql": _sql(old[case_id]),
            "m45_sql": _sql(new[case_id]),
            "regression": bool(old[case_id]["official_correct"])
            and not bool(new[case_id]["official_correct"]),
        }
        for case_id in JSON_CASES
    ]

    changed = [row for row in transitions if row["m43_correct"] != row["m45_correct"]]
    paired = {
        "experiment_id": "m45_parent_child_additive_alignment",
        "control": "M43 / 0.2.1-dev",
        "provider_calls": 90,
        "m43": _score(m43_rows),
        "m45": _score(m45_rows),
        "m43_split": _split_score(m43_rows),
        "m45_split": _split_score(m45_rows),
        "decision_transitions": dict(
            Counter(f"{row['m43_decision']} -> {row['m45_decision']}" for row in transitions)
        ),
        "changed_correctness": changed,
        "transitions": transitions,
        "applicable_family": {
            "cases": applicable_rows,
            "m43_correct": sum(row["m43_correct"] for row in applicable_rows),
            "m45_correct": sum(row["m45_correct"] for row in applicable_rows),
            "m43_total": len(applicable_rows),
            "m45_total": len(applicable_rows),
        },
        "warehouse_08": target,
        "negative_controls": negative_controls,
        "json_retention": json_rows,
        "json_target_fixes_preserved": sum(
            bool(old[case_id]["official_correct"]) and bool(new[case_id]["official_correct"])
            for case_id in JSON_TARGETS
        ),
        "json_regressions": [row["case_id"] for row in json_rows if row["regression"]],
        "other_controls": [
            {
                "case_id": case_id,
                "m43_correct": bool(old[case_id]["official_correct"]),
                "m45_correct": bool(new[case_id]["official_correct"]),
                "m43_category": old[case_id]["official_category"],
                "m45_category": new[case_id]["official_category"],
                "sql_changed": _sql(old[case_id]) != _sql(new[case_id]),
            }
            for case_id in OTHER_CONTROLS
        ],
        "inventory_counts": inventory["counts"],
    }

    fanout_result = {
        "experiment_id": "m45_parent_child_additive_alignment",
        "offline_inventory": inventory,
        "applicable_cases": applicable_rows,
        "negative_controls": negative_controls,
        "confirmed_failures_before": [
            row["case_id"] for row in applicable_rows if not row["m43_correct"]
        ],
        "confirmed_failures_after": [
            row["case_id"] for row in applicable_rows if not row["m45_correct"]
        ],
        "target_fixed": bool(target["m45_correct"]),
        "distinct_value_hack_cases": [
            row["case_id"]
            for row in applicable_rows
            if row["m45_distinct_status"] == "DISTINCT_VALUE_HACK"
        ],
        "negative_control_regressions": [
            row["case_id"] for row in negative_controls if row["regressed"]
        ],
        "prompt_only_grain_repair_exhausted": not bool(target["m45_correct"]),
        "static_analysis_diagnostic_only": True,
    }

    _dump(PAIRED, paired)
    _dump(ALIGNMENT, fanout_result)
    preservation = _load(PRESERVATION)
    if preservation["provider_calls"] != 0:
        raise RuntimeError("M45_ANALYSIS_PRESERVATION_PROVIDER_CALLS")


if __name__ == "__main__":
    main()
