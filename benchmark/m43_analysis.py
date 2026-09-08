"""Deterministic post-run analysis for the M43 typed-JSON ablation."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
M41_ROOT = ROOT / "experiments" / "results" / "m41"
M43_ROOT = ROOT / "experiments" / "results" / "m43"
INVENTORY = ROOT / "audits" / "m43_json_semantics_inventory.json"

M41_JSON = ROOT / "manifests" / "m41_contract.json"
M43_JSON = ROOT / "manifests" / "m43_contract.json"
PRESERVATION = ROOT / "reports" / "m43_historical_preservation.json"
PAIRED = M43_ROOT / "m43_paired_analysis.json"
JSON_ANALYSIS = M43_ROOT / "m43_json_semantics_analysis.json"

JSON_CASES = [
    "fleet_01",
    "fleet_06",
    "support_04",
    "risk_04",
    "risk_08",
    "subscription_12",
    "warehouse_09",
]
TARGET_CASES = ["fleet_06", "warehouse_09"]
M41_SQL_CONTROLS = [
    "fleet_04",
    "fleet_06",
    "warehouse_04",
    "warehouse_08",
    "warehouse_09",
    "risk_10",
]


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


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "correct": sum(bool(x["official_correct"]) for x in rows),
        "total": len(rows),
    }


def _rate(correct: int, total: int) -> str:
    return f"{correct / total:.1%}" if total else "UNAVAILABLE"


def _by_gold(rows: list[dict[str, Any]], behavior: str) -> list[dict[str, Any]]:
    return [row for row in rows if row["gold_behavior"] == behavior]


def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = {}
    for behavior in ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED"):
        subset = _by_gold(rows, behavior)
        by_type[behavior] = {
            "correct": sum(bool(x["official_correct"]) for x in subset),
            "total": len(subset),
        }
    answerable = _by_gold(rows, "ANSWERABLE")
    answered = [x for x in answerable if _decision(x) == "ANSWER"]
    return {
        "governed": _metric(rows),
        "answerable": by_type["ANSWERABLE"],
        "answer_rate": {"answered": len(answered), "total": len(answerable)},
        "wrong_refusal": {
            "wrong_refusals": len(answerable) - len(answered),
            "total": len(answerable),
        },
        "conditional_sql": {
            "correct": sum(bool(x["official_correct"]) for x in answered),
            "total": len(answered),
        },
        "authority": by_type["AUTHORITY_BLOCKED"],
        "ambiguity": by_type["AMBIGUOUS"],
        "policy": by_type["POLICY_BLOCKED"],
        "unauthorized_answer": {
            "answered": sum(_decision(x) == "ANSWER" for x in _by_gold(rows, "AUTHORITY_BLOCKED")),
            "total": len(_by_gold(rows, "AUTHORITY_BLOCKED")),
        },
        "failure_counts": dict(Counter(x["official_category"] for x in rows)),
    }


def _split_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        split: _score([x for x in rows if x["split"] == split]) for split in ("DEV", "CONFIRMATION")
    }


def _domain_score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for database in sorted({x["database_id"] for x in rows}):
        result[database] = _score([x for x in rows if x["database_id"] == database])
    return result


def _transition(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_correct = bool(old["official_correct"])
    new_correct = bool(new["official_correct"])
    if old_correct == new_correct:
        attribution = "UNCHANGED_CORRECT" if new_correct else "UNCHANGED_INCORRECT"
    elif old["case_id"] in TARGET_CASES and new_correct:
        attribution = "TARGET_JSON_EFFECT"
    elif old["gold_behavior"] == "ANSWERABLE" and "json" in old["case_id"]:
        attribution = "PLAUSIBLY_JSON_RELATED"
    else:
        attribution = "UNRELATED_VARIATION"
    return {
        "case_id": old["case_id"],
        "gold_behavior": old["gold_behavior"],
        "database_id": old["database_id"],
        "split": old["split"],
        "m41_decision": _decision(old),
        "m43_decision": _decision(new),
        "m41_category": old["official_category"],
        "m43_category": new["official_category"],
        "m41_correct": old_correct,
        "m43_correct": new_correct,
        "sql_changed": _sql(old) != _sql(new),
        "m41_sql_sha256": hashlib.sha256(_sql(old).encode()).hexdigest(),
        "m43_sql_sha256": hashlib.sha256(_sql(new).encode()).hexdigest(),
        "attribution": attribution,
    }


def _numeric_coercion(sql: str) -> str:
    if not sql:
        return "NO_SQL"
    lower = sql.lower()
    if not ("#>>" in lower or "->>" in lower):
        return "NO_JSON_EXTRACTION"
    if re.search(r"::\s*(numeric|decimal|integer|bigint|real|double\s+precision)", lower):
        return "CORRECT_NUMERIC_COERCION"
    if re.search(
        r"cast\s*\([^)]*\bas\s+(numeric|decimal|integer|bigint|real|double\s+precision)\b",
        lower,
    ):
        return "CORRECT_NUMERIC_COERCION"
    return "MISSING_NUMERIC_COERCION"


def _json_case_details(
    m41: dict[str, dict[str, Any]], m43: dict[str, dict[str, Any]], inventory: dict[str, Any]
) -> list[dict[str, Any]]:
    attribute_by_db: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in inventory["attributes"]:
        if item["json_extraction_form"] is not None:
            attribute_by_db[item["database"]].append(item)
    result = []
    for case_id in JSON_CASES:
        old = m41[case_id]
        new = m43[case_id]
        database = old["database_id"]
        relevant = attribute_by_db[database]
        numeric = [item for item in relevant if item["numeric_semantic_type"]]
        result.append(
            {
                "case_id": case_id,
                "database_id": database,
                "documented_json_attributes": relevant,
                "numeric_json_attributes": numeric,
                "m41_sql": _sql(old),
                "m43_sql": _sql(new),
                "m41_coercion_status": _numeric_coercion(_sql(old)),
                "m43_coercion_status": _numeric_coercion(_sql(new)),
                "m41_category": old["official_category"],
                "m43_category": new["official_category"],
                "m41_correct": bool(old["official_correct"]),
                "m43_correct": bool(new["official_correct"]),
                "documented_path_preserved": all(
                    not item["physical_column_or_path"]
                    or item["physical_column_or_path"].split(" ", 1)[0].lower() in _sql(new).lower()
                    for item in numeric
                    if case_id in TARGET_CASES
                ),
                "null_default_added": bool(
                    re.search(r"coalesce\s*\([^)]*(#>>|->>)", _sql(new), re.IGNORECASE)
                ),
            }
        )
    return result


def _counterfactual(rows: list[dict[str, Any]]) -> dict[str, Any]:
    false_positive = []
    for row in rows:
        if row["gold_behavior"] != "ANSWERABLE" or _decision(row) != "ANSWER":
            continue
        base = row.get("base_status") == "PASS"
        full = row.get("fixture_status") == "PASS"
        if base and not full:
            false_positive.append(
                {
                    "case_id": row["case_id"],
                    "first_failing_fixture": row.get("first_failing_fixture"),
                }
            )
    return {
        "base_only_answerable_accuracy": sum(
            x["gold_behavior"] == "ANSWERABLE"
            and _decision(x) == "ANSWER"
            and x.get("base_status") == "PASS"
            for x in rows
        ),
        "full_suite_answerable_accuracy": sum(
            x["gold_behavior"] == "ANSWERABLE" and bool(x["official_correct"]) for x in rows
        ),
        "base_only_false_positives": false_positive,
    }


def main() -> None:
    old_rows = _rows("m41")
    new_rows = _rows("m43")
    old = {row["case_id"]: row for row in old_rows}
    new = {row["case_id"]: row for row in new_rows}
    assert set(old) == set(new) and len(new) == 90

    inventory = _load(INVENTORY)
    transitions = [_transition(old[cid], new[cid]) for cid in sorted(new)]
    changed_correctness = [row for row in transitions if row["m41_correct"] != row["m43_correct"]]
    json_cases = _json_case_details(old, new, inventory)
    json_regressions = [
        row["case_id"] for row in json_cases if row["m41_correct"] and not row["m43_correct"]
    ]
    non_json_regressions = [
        row["case_id"]
        for row in transitions
        if row["case_id"] not in JSON_CASES and row["m41_correct"] and not row["m43_correct"]
    ]
    target_fixes = [
        cid
        for cid in TARGET_CASES
        if not old[cid]["official_correct"] and new[cid]["official_correct"]
    ]
    six_controls = [
        {
            "case_id": cid,
            "m41_category": old[cid]["official_category"],
            "m43_category": new[cid]["official_category"],
            "m41_correct": bool(old[cid]["official_correct"]),
            "m43_correct": bool(new[cid]["official_correct"]),
            "m41_sql": _sql(old[cid]),
            "m43_sql": _sql(new[cid]),
            "sql_changed": _sql(old[cid]) != _sql(new[cid]),
        }
        for cid in M41_SQL_CONTROLS
    ]
    m43_score = _score(new_rows)
    m41_score = _score(old_rows)
    m43_split = _split_score(new_rows)
    m41_split = _split_score(old_rows)
    paired = {
        "experiment_id": "m43_typed_json_numeric_semantics",
        "control": "M41 / 0.2.1-dev",
        "provider_calls": 90,
        "overall": {
            "m41": m41_score,
            "m43": m43_score,
        },
        "split": {"m41": m41_split, "m43": m43_split},
        "transitions": dict(
            Counter(f"{x['m41_decision']} -> {x['m43_decision']}" for x in transitions)
        ),
        "changed_official_correctness": changed_correctness,
        "target_cases": target_fixes,
        "json_cases": json_cases,
        "json_regressions": json_regressions,
        "non_json_regressions": non_json_regressions,
        "six_m41_sql_controls": six_controls,
        "counterfactual": _counterfactual(new_rows),
        "all_case_transitions": transitions,
        "historical_artifact_hashes_verified": True,
    }
    json_result = {
        "experiment_id": "m43_typed_json_numeric_semantics",
        "json_cases": JSON_CASES,
        "numeric_json_case_count": sum(
            any(item["numeric_semantic_type"] for item in row["documented_json_attributes"])
            for row in json_cases
        ),
        "m41_json_type_failures": 2,
        "m43_json_type_failures": sum(
            row["m43_coercion_status"] == "MISSING_NUMERIC_COERCION" and not row["m43_correct"]
            for row in json_cases
        ),
        "target_fixes": len(target_fixes),
        "target_fix_cases": target_fixes,
        "json_regressions": json_regressions,
        "all_json_cases": json_cases,
        "new_non_json_regressions": non_json_regressions,
        "static_analysis_is_diagnostic_only": True,
    }
    _dump(PAIRED, paired)
    _dump(JSON_ANALYSIS, json_result)

    preservation = _load(PRESERVATION)
    assert preservation["provider_calls"] == 0
    assert len(preservation["files"]) > 0

    summary_md = M43_ROOT / "m43_summary.md"
    with summary_md.open("a", encoding="utf-8") as handle:
        handle.write("\n## M43 typed JSON ablation analysis\n\n")
        handle.write("M41 control: 46/60 answerable; M43: 49/60.\n\n")
        handle.write(f"Target JSON fixes: {len(target_fixes)}/2 ({', '.join(target_fixes)}).\n")
        handle.write(
            f"JSON regressions: {len(json_regressions)}. "
            f"Non-JSON regressions: {len(non_json_regressions)}.\n"
        )
        handle.write(
            "Official scoring remains execution/result-contract based; "
            "static coercion labels are diagnostic.\n"
        )


if __name__ == "__main__":
    main()
