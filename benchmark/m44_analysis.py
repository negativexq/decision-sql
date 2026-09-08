"""Deterministic post-run analysis for the M44 fanout ablation."""

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
M44_ROOT = ROOT / "experiments" / "results" / "m44"
INVENTORY = ROOT / "audits" / "m44_fanout_inventory.json"
PRESERVATION = ROOT / "reports" / "m44_historical_preservation.json"
PAIRED = M44_ROOT / "m44_paired_analysis.json"
FANOUT = M44_ROOT / "m44_fanout_analysis.json"

FANOUT_CASES = ["risk_05", "subscription_04", "subscription_10", "warehouse_08"]
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

    answerable = subset("ANSWERABLE")
    answered = [row for row in answerable if _decision(row) == "ANSWER"]

    def typed(behavior: str) -> dict[str, int]:
        values = subset(behavior)
        return {
            "correct": sum(bool(row["official_correct"]) for row in values),
            "total": len(values),
        }

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
    if not sql:
        return {
            "join_count": 0,
            "aggregate_count": 0,
            "subquery_count": 0,
            "cte_count": 0,
            "grouping_key_count": 0,
            "distinct_count": 0,
            "parsed": False,
        }
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return {
            "join_count": 0,
            "aggregate_count": 0,
            "subquery_count": 0,
            "cte_count": 0,
            "grouping_key_count": 0,
            "distinct_count": 0,
            "parsed": False,
        }
    groups = list(tree.find_all(exp.Group))
    grouping_key_count = sum(len(group.expressions) for group in groups)
    return {
        "join_count": len(list(tree.find_all(exp.Join))),
        "aggregate_count": len(list(tree.find_all(exp.AggFunc))),
        "subquery_count": len(list(tree.find_all(exp.Subquery))),
        "cte_count": len(list(tree.find_all(exp.CTE))),
        "grouping_key_count": grouping_key_count,
        "distinct_count": len(list(tree.find_all(exp.Distinct))),
        "parsed": True,
    }


def _fanout_sql_status(case_id: str, sql: str) -> str:
    if not sql:
        return "UNRESOLVED"
    upper = sql.upper()
    if case_id == "warehouse_08":
        if "SUM(POL.ORDERED_QTY)" in upper and "RECEIPTS" in upper:
            return "PARENT_MEASURE_DUPLICATION_RISK"
        if (
            "GROUP BY L.PO_LINE_ID" in upper
            or "PO_LINE_ID" in upper
            and "SUM(R.RECEIVED_QTY)" in upper
        ):
            return "NATIVE_GRAIN_CALCULATION"
    if case_id == "risk_05":
        if "SELECT DISTINCT ALERT_ID" in upper:
            return "DISTINCT_AS_FANOUT_HACK"
        if "LEFT JOIN INVESTIGATIONS" in upper and "COUNT(*)" in upper:
            return "PARENT_MEASURE_DUPLICATION_RISK"
    if case_id in {"subscription_04", "subscription_10"}:
        if "GROUP BY PAYMENT_ID" in upper or "SUM(REFUND" in upper and "LEFT JOIN" in upper:
            return "PREAGGREGATED_MANY_SIDE"
    return "FANOUT_SAFE"


def _transition(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_correct = bool(old["official_correct"])
    new_correct = bool(new["official_correct"])
    case_id = old["case_id"]
    if case_id == "warehouse_08" and old_correct != new_correct:
        attribution = "TARGET_FANOUT_EFFECT"
    elif case_id in FANOUT_CASES and old_correct != new_correct:
        attribution = "PLAUSIBLY_GRAIN_RELATED"
    elif case_id in JSON_CASES and old_correct and not new_correct:
        attribution = "JSON_REGRESSION"
    elif old["gold_behavior"] != "ANSWERABLE" and old_correct and not new_correct:
        attribution = "GOVERNANCE_REGRESSION"
    elif old_correct != new_correct:
        attribution = "UNRELATED_VARIATION"
    else:
        attribution = "UNCHANGED_CORRECT" if new_correct else "UNCHANGED_INCORRECT"
    return {
        "case_id": case_id,
        "database_id": old["database_id"],
        "split": old["split"],
        "gold_behavior": old["gold_behavior"],
        "m43_decision": _decision(old),
        "m44_decision": _decision(new),
        "m43_category": old["official_category"],
        "m44_category": new["official_category"],
        "m43_correct": old_correct,
        "m44_correct": new_correct,
        "sql_changed": _sql(old) != _sql(new),
        "m43_sql_sha256": hashlib.sha256(_sql(old).encode()).hexdigest(),
        "m44_sql_sha256": hashlib.sha256(_sql(new).encode()).hexdigest(),
        "attribution": attribution,
    }


def main() -> None:
    m43_rows = _rows("m43")
    m44_rows = _rows("m44")
    old = {row["case_id"]: row for row in m43_rows}
    new = {row["case_id"]: row for row in m44_rows}
    assert len(new) == 90 and set(old) == set(new)
    inventory = _load(INVENTORY)
    transitions = [_transition(old[case_id], new[case_id]) for case_id in sorted(new)]
    fanout_rows = []
    for case_id in FANOUT_CASES:
        fanout_rows.append(
            {
                "case_id": case_id,
                "classification": "FANOUT_SENSITIVE",
                "m43_sql": _sql(old[case_id]),
                "m44_sql": _sql(new[case_id]),
                "m43_static_status": _fanout_sql_status(case_id, _sql(old[case_id])),
                "m44_static_status": _fanout_sql_status(case_id, _sql(new[case_id])),
                "m43_correct": bool(old[case_id]["official_correct"]),
                "m44_correct": bool(new[case_id]["official_correct"]),
                "m43_category": old[case_id]["official_category"],
                "m44_category": new[case_id]["official_category"],
                "m43_features": _sql_features(_sql(old[case_id])),
                "m44_features": _sql_features(_sql(new[case_id])),
                "distinct_as_fanout_hack": _fanout_sql_status(case_id, _sql(new[case_id]))
                == "DISTINCT_AS_FANOUT_HACK",
            }
        )
    target = {
        "case_id": "warehouse_08",
        "m43_decision": _decision(old["warehouse_08"]),
        "m44_decision": _decision(new["warehouse_08"]),
        "m43_sql": _sql(old["warehouse_08"]),
        "m44_sql": _sql(new["warehouse_08"]),
        "native_measure_grain": "purchase_order_lines.po_line_id",
        "fanout_boundary": "purchase_order_lines 1:N receipts via receipt_line",
        "output_grain": "product_id",
        "m43_result": {
            "category": old["warehouse_08"]["official_category"],
            "correct": bool(old["warehouse_08"]["official_correct"]),
            "first_failing_fixture": old["warehouse_08"]["first_failing_fixture"],
        },
        "m44_result": {
            "category": new["warehouse_08"]["official_category"],
            "correct": bool(new["warehouse_08"]["official_correct"]),
            "first_failing_fixture": new["warehouse_08"]["first_failing_fixture"],
        },
        "fixed": False,
        "uses_native_grain_safe_semantics": False,
    }
    json_retention = []
    for case_id in JSON_CASES:
        json_retention.append(
            {
                "case_id": case_id,
                "m43_correct": bool(old[case_id]["official_correct"]),
                "m44_correct": bool(new[case_id]["official_correct"]),
                "m43_sql": _sql(old[case_id]),
                "m44_sql": _sql(new[case_id]),
                "regression": bool(old[case_id]["official_correct"])
                and not bool(new[case_id]["official_correct"]),
            }
        )
    changed = [row for row in transitions if row["m43_correct"] != row["m44_correct"]]
    fanout_regressions = [
        row["case_id"] for row in fanout_rows if row["m43_correct"] and not row["m44_correct"]
    ]
    paired = {
        "experiment_id": "m44_native_grain_fanout",
        "control": "M43 / 0.2.1-dev",
        "provider_calls": 90,
        "m43": _score(m43_rows),
        "m44": _score(m44_rows),
        "m43_split": _split_score(m43_rows),
        "m44_split": _split_score(m44_rows),
        "decision_transitions": dict(
            Counter(f"{row['m43_decision']} -> {row['m44_decision']}" for row in transitions)
        ),
        "changed_correctness": changed,
        "fanout_cases": fanout_rows,
        "warehouse_08": target,
        "json_retention": json_retention,
        "json_target_fixes_preserved": sum(
            bool(old[case_id]["official_correct"]) and bool(new[case_id]["official_correct"])
            for case_id in JSON_TARGETS
        ),
        "json_regressions": [row["case_id"] for row in json_retention if row["regression"]],
        "fanout_sensitive_regressions": fanout_regressions,
        "distinct_as_fanout_hack_cases": [
            row["case_id"] for row in fanout_rows if row["distinct_as_fanout_hack"]
        ],
        "other_controls": [
            {
                "case_id": case_id,
                "m43_category": old[case_id]["official_category"],
                "m44_category": new[case_id]["official_category"],
                "m43_correct": bool(old[case_id]["official_correct"]),
                "m44_correct": bool(new[case_id]["official_correct"]),
                "sql_changed": _sql(old[case_id]) != _sql(new[case_id]),
            }
            for case_id in OTHER_CONTROLS
        ],
        "inventory_counts": inventory["counts"],
    }
    fanout_result = {
        "experiment_id": "m44_native_grain_fanout",
        "offline_inventory": inventory,
        "fanout_cases": fanout_rows,
        "confirmed_failures_before": ["warehouse_08"],
        "confirmed_failures_after": ["warehouse_08"],
        "fanout_sensitive_cases_with_official_failure_after": [
            case_id for case_id in FANOUT_CASES if not new[case_id]["official_correct"]
        ],
        "risk_05_is_execution_regression_not_confirmed_numeric_fanout": True,
        "target_fixed": False,
        "fanout_sensitive_regressions": fanout_regressions,
        "distinct_as_fanout_hack_cases": [
            row["case_id"] for row in fanout_rows if row["distinct_as_fanout_hack"]
        ],
        "static_analysis_diagnostic_only": True,
    }
    _dump(PAIRED, paired)
    _dump(FANOUT, fanout_result)
    preservation = _load(PRESERVATION)
    assert preservation["provider_calls"] == 0


if __name__ == "__main__":
    main()
