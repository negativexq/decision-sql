"""Deterministic post-run analysis for the M42 authority ablation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, cast

import sqlglot
from sqlglot import exp

from benchmark.context import load_authority

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
M41_ROOT = ROOT / "experiments" / "results" / "m41"
M42_ROOT = ROOT / "experiments" / "results" / "m42"
M42_MANIFEST = M42_ROOT / "m42_manifest.json"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _rows(prefix: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        _load(ROOT / "experiments" / "results" / prefix / f"{prefix}_case_results.json"),
    )


def _decision(row: dict[str, Any]) -> str:
    return str(row.get("model_decision") or "INVALID/NO_DECISION")


def _case_path(case_id: str) -> Path:
    directory = "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"
    return ROOT / "ground_truth" / directory / f"{case_id}.json"


def _case_truth(case_id: str) -> dict[str, Any]:
    return cast(dict[str, Any], _load(_case_path(case_id)))


def _table_name(entity_id: str) -> str:
    return entity_id.rsplit(":", 1)[-1]


def _authority_graph(
    database_id: str,
) -> tuple[set[str], dict[str, set[str]], list[dict[str, Any]]]:
    authority = load_authority(database_id)
    tables = {_table_name(str(entity["entity_id"])) for entity in authority["entities"]}
    graph: dict[str, set[str]] = {table: set() for table in tables}
    edges = []
    for relation in authority["relationships"]:
        if relation.get("authorized") is not True:
            continue
        left = _table_name(str(relation["left_entity"]))
        right = _table_name(str(relation["right_entity"]))
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
        edges.append(
            {
                "relationship_id": relation["relationship_id"],
                "left": left,
                "right": right,
            }
        )
    return tables, graph, edges


def _path(
    graph: dict[str, set[str]], start: str, target: str, allowed: set[str]
) -> list[str] | None:
    if start == target:
        return [start]
    queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
    seen = {start}
    while queue:
        node, route = queue.popleft()
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in allowed or neighbor in seen:
                continue
            candidate = route + [neighbor]
            if neighbor == target:
                return candidate
            seen.add(neighbor)
            queue.append((neighbor, candidate))
    return None


def _sql_tables(sql: str | None, database_id: str) -> dict[str, Any]:
    if not sql:
        return {"status": "NO_SQL", "tables": [], "paths": [], "fully_authorized": None}
    tables, graph, _edges = _authority_graph(database_id)
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:
        return {"status": "PARSE_FAILURE", "error": str(exc), "tables": []}
    used = sorted({table.name for table in tree.find_all(exp.Table) if table.name in tables})
    paths = []
    fully_authorized = True
    for index, left in enumerate(used):
        for right in used[index + 1 :]:
            route = _path(graph, left, right, set(used))
            paths.append({"from": left, "to": right, "authorized_path": route})
            if route is None:
                fully_authorized = False
    return {
        "status": "OK",
        "tables": used,
        "paths": paths,
        "fully_authorized": fully_authorized,
        "join_count": len(list(tree.find_all(exp.Join))),
    }


def _authority_depths(
    m41_rows: list[dict[str, Any]], m42_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    prior = _load(Path("evaluation/forensics/m41/m41_authority_path_depth.json"))
    old_paths = {item["case_id"]: item["path_depth"]["bucket"] for item in prior["cases"]}
    new_by_id = {row["case_id"]: row for row in m42_rows}
    buckets: dict[str, dict[str, Any]] = {
        bucket: {"cases": 0, "answered": 0, "wrong_refusal": 0, "correct": 0}
        for bucket in ("0-hop", "1-hop", "2-hop", "3+-hop", "unresolved")
    }
    cases = []
    for old in m41_rows:
        if old["gold_behavior"] != "ANSWERABLE":
            continue
        bucket = old_paths.get(old["case_id"], "unresolved")
        new = new_by_id[old["case_id"]]
        item = {
            "case_id": old["case_id"],
            "database_id": old["database_id"],
            "split": old["split"],
            "bucket": bucket,
            "m41_decision": _decision(old),
            "m42_decision": _decision(new),
            "m41_correct": bool(old["official_correct"]),
            "m42_correct": bool(new["official_correct"]),
        }
        cases.append(item)
        stats = buckets[bucket]
        stats["cases"] += 1
        stats["answered"] += _decision(new) == "ANSWER"
        stats["wrong_refusal"] += _decision(new) != "ANSWER"
        stats["correct"] += bool(new["official_correct"])
    for stats in buckets.values():
        total = stats["cases"]
        answers = stats["answered"]
        stats["answer_rate"] = f"{answers / total:.1%}" if total else "UNAVAILABLE"
        stats["wrong_refusal_rate"] = (
            f"{stats['wrong_refusal'] / total:.1%}" if total else "UNAVAILABLE"
        )
        stats["full_correctness"] = f"{stats['correct'] / total:.1%}" if total else "UNAVAILABLE"
    return {"buckets": buckets, "cases": cases}


def _write_manifest(
    contract: dict[str, Any], summary: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    raw_lines = M42_ROOT.joinpath("m42_raw_responses.jsonl").read_text().splitlines()
    manifest = {
        "experiment_id": "m42_authority_composition_clarification",
        "experiment_name": "m42_authority_composition_clarification",
        "benchmark_version": "0.2.1-dev",
        "benchmark_content_hash": contract["benchmark_content_hash"],
        "contract_source_commit": contract["contract_source_commit"],
        "execution_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "model_config": contract["model_config"],
        "database_count": 6,
        "case_count": 90,
        "task_distribution": dict(Counter(row["gold_behavior"] for row in rows)),
        "dev_databases": ["commerce_ops", "fleet_ops", "support_ops", "subscription_billing"],
        "confirmation_databases": ["warehouse_logistics", "risk_operations"],
        "hashes": contract["hashes"],
        "old_m41_prompt_hash": contract["old_m41_prompt_hash"],
        "new_m42_prompt_hash": contract["new_m42_prompt_hash"],
        "run_start": None,
        "run_end": None,
        "timing_metadata_note": (
            "Harness timestamps were not persisted by the original M42 runner; per-case "
            "latency is preserved in case results."
        ),
        "provider_calls_attempted": len(raw_lines),
        "provider_responses_received": sum(row["provider_status"] == "SUCCESS" for row in rows),
        "provider_schema_accepted_calls": sum(row["provider_status"] == "SUCCESS" for row in rows),
        "raw_response_artifact": "benchmark/experiments/results/m42/m42_raw_responses.jsonl",
        "raw_response_sha256": hashlib.sha256(
            M42_ROOT.joinpath("m42_raw_responses.jsonl").read_bytes()
        ).hexdigest(),
        "raw_response_lines": len(raw_lines),
        "first_model_response_acquired": True,
        "summary_scores": summary["scores"],
        "historical_parent": "M41 / 0.2.1-dev",
    }
    _dump(M42_MANIFEST, manifest)


def main() -> None:
    m41_rows = _rows("m41")
    m42_rows = _rows("m42")
    m41_by_id = {row["case_id"]: row for row in m41_rows}
    m42_by_id = {row["case_id"]: row for row in m42_rows}
    contract = _load(ROOT / "manifests/m42_contract.json")
    summary = _load(M42_ROOT / "m42_summary.json")
    _write_manifest(contract, summary, m42_rows)

    transitions: Counter[str] = Counter()
    by_gold: dict[str, Counter[str]] = defaultdict(Counter)
    by_split: dict[str, Counter[str]] = defaultdict(Counter)
    by_db: dict[str, Counter[str]] = defaultdict(Counter)
    changed = []
    for case_id in m42_by_id:
        old = m41_by_id[case_id]
        new = m42_by_id[case_id]
        key = f"{_decision(old)} -> {_decision(new)}"
        transitions[key] += 1
        by_gold[old["gold_behavior"]][key] += 1
        by_split[old["split"]][key] += 1
        by_db[old["database_id"]][key] += 1
        if bool(old["official_correct"]) != bool(new["official_correct"]):
            changed.append(
                {
                    "case_id": case_id,
                    "gold_behavior": old["gold_behavior"],
                    "database_id": old["database_id"],
                    "split": old["split"],
                    "m41_decision": _decision(old),
                    "m42_decision": _decision(new),
                    "m41_category": old["official_category"],
                    "m42_category": new["official_category"],
                    "sql_changed": old.get("parsed_submission", {}).get("sql")
                    != new.get("parsed_submission", {}).get("sql"),
                    "attribution": "DIRECTLY_RELEVANT"
                    if old["gold_behavior"] == "ANSWERABLE"
                    and ("AUTHORITY" in _decision(old) or "AUTHORITY" in _decision(new))
                    else "UNRELATED_VARIATION",
                }
            )

    target_ids = ["warehouse_03", "warehouse_07", "warehouse_13", "risk_11"]
    targets = []
    for case_id in target_ids:
        old = m41_by_id[case_id]
        new = m42_by_id[case_id]
        truth = _case_truth(case_id)["semantic_target"]
        sql = (new.get("parsed_submission") or {}).get("sql")
        targets.append(
            {
                "case_id": case_id,
                "database_id": old["database_id"],
                "m41_decision": _decision(old),
                "m42_decision": _decision(new),
                "m41_reason": (old.get("parsed_submission") or {}).get("reason_code"),
                "m42_reason": (new.get("parsed_submission") or {}).get("reason_code"),
                "authorized_relationship_ids": truth.get("relationships", []),
                "m42_sql": sql,
                "m42_sql_authority": _sql_tables(sql, old["database_id"]),
                "m42_official_category": new["official_category"],
                "m42_official_correct": bool(new["official_correct"]),
            }
        )

    authority_cases = [row for row in m42_rows if row["gold_behavior"] == "AUTHORITY_BLOCKED"]
    authority_safety = []
    for new in authority_cases:
        old = m41_by_id[new["case_id"]]
        sql = (new.get("parsed_submission") or {}).get("sql")
        authority_safety.append(
            {
                "case_id": new["case_id"],
                "database_id": new["database_id"],
                "m41_decision": _decision(old),
                "m42_decision": _decision(new),
                "m42_sql": sql,
                "m42_sql_authority": _sql_tables(sql, new["database_id"]),
                "authority_safety_regression": _decision(new) == "ANSWER",
            }
        )
    ambiguity_safety = []
    for new in m42_rows:
        if new["gold_behavior"] == "AMBIGUOUS":
            old = m41_by_id[new["case_id"]]
            ambiguity_safety.append(
                {
                    "case_id": new["case_id"],
                    "database_id": new["database_id"],
                    "m41_decision": _decision(old),
                    "m42_decision": _decision(new),
                    "m41_correct": bool(old["official_correct"]),
                    "m42_correct": bool(new["official_correct"]),
                }
            )

    transition_result = {
        "experiment_id": "m42_authority_composition_clarification",
        "overall": dict(transitions),
        "by_gold_behavior": {key: dict(value) for key, value in by_gold.items()},
        "by_split": {key: dict(value) for key, value in by_split.items()},
        "by_database": {key: dict(value) for key, value in by_db.items()},
        "changed_official_correctness": changed,
        "provider_calls": 90,
    }
    governance = {
        "target_false_authority_cases": targets,
        "authority_blocked_safety": authority_safety,
        "authority_safety_regression_count": sum(
            item["authority_safety_regression"] for item in authority_safety
        ),
        "ambiguity_safety": ambiguity_safety,
        "m41_false_authority_count": 4,
        "m42_false_authority_cases": [
            row["case_id"]
            for row in m42_rows
            if row["gold_behavior"] == "ANSWERABLE" and _decision(row) == "BLOCKED_AUTHORITY"
        ],
        "m42_false_authority_count": sum(
            row["gold_behavior"] == "ANSWERABLE" and _decision(row) == "BLOCKED_AUTHORITY"
            for row in m42_rows
        ),
        "target_cases_corrected": sum(item["m42_official_correct"] for item in targets),
    }
    _dump(M42_ROOT / "m42_paired_analysis.json", transition_result)
    _dump(M42_ROOT / "m42_governance_transition_analysis.json", governance)

    depth = _authority_depths(m41_rows, m42_rows)
    _dump(REPO / "evaluation/forensics/m42/m42_authority_path_depth.json", depth)

    residual = []
    for row in m42_rows:
        if row["official_correct"]:
            continue
        decision = _decision(row)
        if row["gold_behavior"] == "ANSWERABLE" and decision == "BLOCKED_AUTHORITY":
            mechanism = "FALSE_AUTHORITY_BLOCK"
        elif row["gold_behavior"] == "ANSWERABLE" and decision == "NEEDS_CLARIFICATION":
            mechanism = "FALSE_AMBIGUITY"
        elif row["gold_behavior"] != "ANSWERABLE" and decision == "ANSWER":
            mechanism = "WRONG_GOVERNANCE_DECISION"
        elif row["official_category"] == "EXECUTION_FAILURE":
            mechanism = "OTHER_SQL_SEMANTIC_ERROR"
        elif row["official_category"] == "RESULT_MISMATCH":
            mechanism = "OTHER_SQL_SEMANTIC_ERROR"
        else:
            mechanism = row["official_category"]
        residual.append(
            {
                "case_id": row["case_id"],
                "database_id": row["database_id"],
                "split": row["split"],
                "gold_behavior": row["gold_behavior"],
                "model_decision": decision,
                "official_category": row["official_category"],
                "primary_mechanism": mechanism,
                "first_failing_fixture": row.get("first_failing_fixture"),
                "diagnostics": row.get("diagnostics", {}),
            }
        )
    forensic = {
        "experiment_id": "m42_authority_composition_clarification",
        "provider_calls": 0,
        "failure_counts": dict(Counter(item["primary_mechanism"] for item in residual)),
        "failures": residual,
        "m41_sql_secondary_variation_cases": [
            {
                "case_id": case_id,
                "m41_category": m41_by_id[case_id]["official_category"],
                "m42_category": m42_by_id[case_id]["official_category"],
                "m41_correct": bool(m41_by_id[case_id]["official_correct"]),
                "m42_correct": bool(m42_by_id[case_id]["official_correct"]),
                "classification": "UNINTENDED_SECONDARY_VARIATION",
            }
            for case_id in (
                "fleet_04",
                "fleet_06",
                "warehouse_04",
                "warehouse_08",
                "warehouse_09",
                "risk_10",
            )
        ],
    }
    _dump(REPO / "evaluation/forensics/m42/m42_failure_forensics.json", forensic)

    sql_forensics = []
    for row in residual:
        if row["model_decision"] == "ANSWER":
            full = m42_by_id[row["case_id"]]
            sql_forensics.append(
                {
                    "case_id": row["case_id"],
                    "category": row["official_category"],
                    "sql": (full.get("parsed_submission") or {}).get("sql"),
                    "authority_mapping": _sql_tables(
                        (full.get("parsed_submission") or {}).get("sql"), full["database_id"]
                    ),
                    "observable_diagnostics": full.get("diagnostics", {}),
                }
            )
    _dump(REPO / "evaluation/forensics/m42/m42_sql_forensics.json", sql_forensics)
    _dump(
        REPO / "evaluation/forensics/m42/m42_complexity_analysis.json",
        {
            "note": (
                "M42 preserves the frozen M41 structural complexity metadata; causal "
                "inference is not made."
            ),
            "provider_calls": 0,
            "m41_m42_rows": len(m42_rows),
        },
    )


if __name__ == "__main__":
    main()
