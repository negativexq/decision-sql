"""Deterministic residual-failure diagnostics for the frozen M41 run."""

# Diagnostic markdown contains intentionally long evidence rows.
# ruff: noqa: E501

from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from benchmark.context import load_authority

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "experiments" / "results" / "m41"
CASES = ROOT / "cases"
TRUTH = ROOT / "ground_truth"
AUDITS = ROOT / "audits" / "m40"
OUT = ROOT.parent / "evaluation" / "forensics" / "m41"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_dir(case_id: str) -> str:
    return "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"


def _case_truth(case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    directory = _case_dir(case_id)
    return (
        _read(CASES / directory / f"{case_id}.json"),
        _read(TRUTH / directory / f"{case_id}.json"),
    )


def _sql_summary(sql: str | None) -> dict[str, Any]:
    if not sql:
        return {"parse_status": "NO_SQL"}
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read="postgres")
        tables = sorted({table.name for table in tree.find_all(exp.Table)})
        columns = sorted(
            {
                f"{column.table}.{column.name}" if column.table else column.name
                for column in tree.find_all(exp.Column)
            }
        )
        return {
            "parse_status": "PASS",
            "tables": tables,
            "columns": columns,
            "joins": sum(1 for _ in tree.find_all(exp.Join)),
            "where_count": sum(1 for _ in tree.find_all(exp.Where)),
            "group_by_count": sum(1 for _ in tree.find_all(exp.Group)),
            "order_by_count": sum(1 for _ in tree.find_all(exp.Order)),
            "aggregate_count": sum(1 for _ in tree.find_all(exp.AggFunc)),
            "window_count": sum(1 for _ in tree.find_all(exp.Window)),
            "subquery_count": sum(1 for _ in tree.find_all(exp.Subquery)),
        }
    except Exception as error:  # pragma: no cover - defensive diagnostic path
        return {"parse_status": "FAIL", "error": f"{type(error).__name__}:{error}"}


def _required_entities(coverage: dict[str, Any]) -> list[str]:
    entities = []
    for field in coverage.get("physical_fields", []):
        table = field.split(".", 1)[0]
        if table not in entities:
            entities.append(table)
    return entities


def _graph(database_id: str) -> dict[str, list[tuple[str, str]]]:
    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for relationship in load_authority(database_id)["relationships"]:
        if relationship.get("authorized") is not True:
            continue
        left = str(relationship["left_entity"]).split(":")[-1]
        right = str(relationship["right_entity"]).split(":")[-1]
        relation_id = str(relationship["relationship_id"])
        graph[left].append((right, relation_id))
        graph[right].append((left, relation_id))
    return graph


def _shortest_path(
    graph: dict[str, list[tuple[str, str]]], start: str, goal: str
) -> list[dict[str, str]] | None:
    queue: deque[tuple[str, list[dict[str, str]]]] = deque([(start, [])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        if node == goal:
            return path
        for neighbor, relation_id in graph.get(node, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(
                    (
                        neighbor,
                        [*path, {"from": node, "to": neighbor, "relationship_id": relation_id}],
                    )
                )
    return None


def _path_depth(database_id: str, entities: list[str]) -> dict[str, Any]:
    if len(entities) <= 1:
        return {"bucket": "0-hop", "anchor": entities[0] if entities else None, "paths": []}
    graph = _graph(database_id)
    best: tuple[int, str, list[dict[str, Any]]] | None = None
    for anchor in entities:
        paths = []
        maximum = 0
        connected = True
        for target in entities:
            if target == anchor:
                continue
            path = _shortest_path(graph, anchor, target)
            if path is None:
                connected = False
                break
            maximum = max(maximum, len(path))
            paths.append({"target": target, "path": path})
        if connected and (best is None or maximum < best[0]):
            best = (maximum, anchor, paths)
    if best is None:
        return {"bucket": "unresolved", "entities": entities, "paths": []}
    bucket = "3+-hop" if best[0] >= 3 else f"{best[0]}-hop"
    return {"bucket": bucket, "anchor": best[1], "paths": best[2]}


def _mechanism(row: dict[str, Any], truth: dict[str, Any]) -> tuple[str, list[str], str]:
    case_id = row["case_id"]
    if row["gold_behavior"] == "ANSWERABLE" and row.get("model_decision") != "ANSWER":
        if row.get("model_decision") == "BLOCKED_AUTHORITY":
            return (
                "FALSE_AUTHORITY_BLOCK",
                [],
                "An authorized answer path is present in the repaired public graph.",
            )
        if row.get("model_decision") == "NEEDS_CLARIFICATION":
            return (
                "FALSE_AMBIGUITY",
                [],
                "The repaired answerable contract has a single declared interpretation.",
            )
        return (
            "WRONG_GOVERNANCE_DECISION",
            [],
            "The answerable case did not receive an ANSWER decision.",
        )
    if row["gold_behavior"] != "ANSWERABLE":
        return (
            "WRONG_GOVERNANCE_DECISION",
            [],
            "The model decision did not match the governed negative-case behavior.",
        )
    if case_id == "fleet_04":
        return (
            "TEMPORAL_BOUNDARY_ERROR",
            ["FILTER_SCOPE_ERROR"],
            "The query starts the 30-day interval at June 1 instead of the declared May 31 benchmark-time boundary.",
        )
    if case_id == "fleet_06":
        return (
            "JSON_TYPE_COERCION_ERROR",
            ["JSON_PATH_ERROR"],
            "The documented JSON scalar is extracted as text with #>>, without numeric coercion.",
        )
    if case_id == "warehouse_04":
        return (
            "RELATIONSHIP_PATH_ERROR",
            ["CALCULATION_ERROR"],
            "The query stops at purchase-order lines and sums ordered quantity; it omits the authorized receipt hop needed for received quantity.",
        )
    if case_id == "warehouse_08":
        return (
            "AGGREGATION_GRAIN_ERROR",
            ["FANOUT_ERROR"],
            "The query aggregates after joining receipts at product grain, so ordered quantity can be repeated per receipt instead of being computed per line first.",
        )
    if case_id == "warehouse_09":
        return (
            "JSON_TYPE_COERCION_ERROR",
            ["JSON_PATH_ERROR"],
            "The numeric JSON temperature comparison is performed on #>> text rather than an explicitly cast numeric value.",
        )
    if case_id == "risk_10":
        return (
            "NULL_SEMANTICS_ERROR",
            ["ANTI_JOIN_ERROR"],
            "The anti-existence predicate tests cleared_at IS NULL, reversing the visible meaning of a cleared watchlist match.",
        )
    tags = truth.get("semantic_target", {}).get("query_shape_tags", [])
    if "json" in tags:
        return "JSON_PATH_ERROR", [], "JSON-tagged SQL failed the frozen result contract."
    if "temporal" in tags:
        return (
            "TEMPORAL_BOUNDARY_ERROR",
            [],
            "Temporal-tagged SQL failed the frozen result contract.",
        )
    return "OTHER_SQL_SEMANTIC_ERROR", [], "The generated SQL failed the frozen result contract."


def _coverage(
    case_id: str, coverage_by_case: dict[str, Any], truth: dict[str, Any]
) -> dict[str, Any]:
    item = coverage_by_case.get(case_id, {})
    target = truth.get("semantic_target", {})
    tags = target.get("query_shape_tags", [])
    if not item:
        return {
            "context_sufficient": None,
            "missing_fields": [],
            "missing_json_paths": [],
            "missing_tie_break_fields": [],
            "ordering_sufficient": None,
            "population_sufficient": None,
            "temporal_sufficient": None,
            "json_sufficient": None,
        }
    return {
        "context_sufficient": bool(item.get("passed")),
        "missing_fields": item.get("missing_fields", []),
        "missing_json_paths": item.get("missing_json_paths", []),
        "missing_tie_break_fields": item.get("missing_tie_break_fields", []),
        "ordering_sufficient": target.get("semantic_provenance", {})
        .get("ordering", {})
        .get("source")
        != "HIDDEN",
        "population_sufficient": target.get("semantic_provenance", {})
        .get("population", {})
        .get("source")
        not in {"HIDDEN", None},
        "temporal_sufficient": "temporal" not in tags or bool(item.get("passed")),
        "json_sufficient": "json" not in tags or not item.get("missing_json_paths"),
    }


def build() -> dict[str, Any]:
    rows = _read(RESULTS / "m41_case_results.json")
    coverage = {
        item["case_id"]: item for item in _read(AUDITS / "m40_context_coverage.json")["cases"]
    }
    failures = []
    depth_rows = []
    for row in rows:
        case, truth = _case_truth(row["case_id"])
        entities = _required_entities(coverage.get(row["case_id"], {}))
        depth = _path_depth(row["database_id"], entities)
        depth_rows.append(
            {
                "case_id": row["case_id"],
                "database_id": row["database_id"],
                "split": row["split"],
                "answerable": row["gold_behavior"] == "ANSWERABLE",
                "path_depth": depth,
                "model_decision": row.get("model_decision"),
                "official_correct": row["official_correct"],
            }
        )
        if row["official_correct"]:
            continue
        primary, secondary, rationale = _mechanism(row, truth)
        failures.append(
            {
                "case_id": row["case_id"],
                "database_id": row["database_id"],
                "split": row["split"],
                "gold_behavior": row["gold_behavior"],
                "model_decision": row.get("model_decision"),
                "official_category": row["official_category"],
                "question": case["question"],
                "sql": row.get("parsed_submission", {}).get("sql")
                if row.get("parsed_submission")
                else None,
                "first_failing_fixture": row.get("first_failing_fixture"),
                "base_passed": row.get("base_passed"),
                "all_fixtures_passed": row.get("all_fixtures_passed"),
                "primary_mechanism": primary,
                "secondary_mechanisms": secondary,
                "rationale": rationale,
                "coverage": _coverage(row["case_id"], coverage, truth),
                "authority_path_depth": depth,
                "sql_decomposition": _sql_summary(
                    row.get("parsed_submission", {}).get("sql")
                    if row.get("parsed_submission")
                    else None
                ),
                "benchmark_review_candidate": False,
            }
        )
    primary_counts = Counter(item["primary_mechanism"] for item in failures)
    secondary_counts = Counter(
        mechanism for item in failures for mechanism in item["secondary_mechanisms"]
    )
    summary = {
        "experiment_id": "m41_v021_luna_none",
        "provider_calls": 0,
        "failure_count": len(failures),
        "primary_mechanism_counts": dict(primary_counts),
        "secondary_mechanism_counts": dict(secondary_counts),
        "context_defect_failures": sum(
            item["coverage"]["context_sufficient"] is False for item in failures
        ),
        "benchmark_review_candidates": 0,
        "note": "Deterministic post-run classification; no provider calls and no model reasoning inferred.",
    }
    return {"failures": failures, "summary": summary, "depth_rows": depth_rows}


def write() -> None:
    result = build()
    OUT.mkdir(parents=True, exist_ok=True)
    _write(
        OUT / "m41_failure_forensics.json",
        {"failures": result["failures"], "summary": result["summary"]},
    )
    governance = [
        item
        for item in result["failures"]
        if item["primary_mechanism"]
        in {"FALSE_AUTHORITY_BLOCK", "FALSE_AMBIGUITY", "WRONG_GOVERNANCE_DECISION"}
    ]
    _write(OUT / "m41_governance_forensics.json", {"provider_calls": 0, "cases": governance})
    _write(
        OUT / "m41_sql_forensics.json",
        {
            "provider_calls": 0,
            "cases": [item for item in result["failures"] if item["sql"] is not None],
        },
    )
    answerable_depth = [item for item in result["depth_rows"] if item["answerable"]]
    _write(
        OUT / "m41_authority_path_depth.json",
        {
            "provider_calls": 0,
            "cases": answerable_depth,
            "buckets": _depth_summary(answerable_depth),
        },
    )
    _write(OUT / "m41_complexity_analysis.json", _complexity(result["depth_rows"]))
    _write(OUT / "m41_failure_forensics.md", _failure_markdown(result))


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _depth_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, int]] = {}
    for bucket in ("0-hop", "1-hop", "2-hop", "3+-hop", "unresolved"):
        subset = [row for row in rows if row["path_depth"]["bucket"] == bucket]
        buckets[bucket] = {
            "cases": len(subset),
            "answered": sum(row["model_decision"] == "ANSWER" for row in subset),
            "wrong_refusal": sum(row["model_decision"] != "ANSWER" for row in subset),
            "correct": sum(row["official_correct"] for row in subset),
        }
    return buckets


def _complexity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [row for row in rows if row["answerable"]]
    for row in answerable:
        _case, truth = _case_truth(row["case_id"])
        target = truth["semantic_target"]
        reference_sql = truth.get("reference_implementation_a", {}).get("sql")
        reference = _sql_summary(reference_sql)
        bucket = row["path_depth"].get("bucket", "0-hop")
        hop_text = bucket.split("-", 1)[0]
        row["complexity"] = {
            "entity_count": len(row["path_depth"].get("paths", [])) + 1,
            "relationship_hops": int(hop_text) if hop_text.isdigit() else None,
            "predicate_count": reference.get("where_count", 0),
            "aggregate_count": reference.get("aggregate_count", 0),
            "grouping_keys": len(target.get("grouping", [])),
            "nested_depth": reference.get("subquery_count", 0),
            "window_count": reference.get("window_count", 0),
            "set_operation_count": len(
                re.findall(r"\b(?:UNION|INTERSECT|EXCEPT)\b", reference_sql or "", re.I)
            ),
            "temporal_rule_count": int(bool(target.get("temporal_semantics"))),
            "calculation_count": len(target.get("calculations", [])),
            "mechanism_count": len(target.get("query_shape_tags", [])),
            "correct": row["official_correct"],
            "split": row["split"],
        }
    metrics = (
        "entity_count",
        "relationship_hops",
        "predicate_count",
        "aggregate_count",
        "grouping_keys",
        "nested_depth",
        "window_count",
        "set_operation_count",
        "temporal_rule_count",
        "calculation_count",
        "mechanism_count",
    )
    groups = {
        "overall_correct": [row for row in answerable if row["official_correct"]],
        "overall_incorrect": [row for row in answerable if not row["official_correct"]],
        "DEV_correct": [
            row for row in answerable if row["split"] == "DEV" and row["official_correct"]
        ],
        "DEV_incorrect": [
            row for row in answerable if row["split"] == "DEV" and not row["official_correct"]
        ],
        "CONFIRMATION_correct": [
            row for row in answerable if row["split"] == "CONFIRMATION" and row["official_correct"]
        ],
        "CONFIRMATION_incorrect": [
            row
            for row in answerable
            if row["split"] == "CONFIRMATION" and not row["official_correct"]
        ],
    }
    comparisons = {
        label: {
            metric: statistics.median(
                [
                    row["complexity"][metric]
                    for row in subset
                    if row["complexity"][metric] is not None
                ]
            )
            if subset
            else None
            for metric in metrics
        }
        for label, subset in groups.items()
    }
    return {
        "provider_calls": 0,
        "note": "Structural reference/authority-path summaries; descriptive, not causal inference.",
        "comparisons": comparisons,
        "cases": answerable,
    }


def _failure_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# M41 residual failure forensics",
        "",
        "Deterministic analysis of frozen M41 outputs; provider calls: 0.",
        "",
        "## Primary mechanisms",
        "",
        "| Mechanism | Count |",
        "|---|---:|",
    ]
    for mechanism, count in sorted(summary["primary_mechanism_counts"].items()):
        lines.append(f"| {mechanism} | {count} |")
    lines += [
        "",
        "## Failures",
        "",
        "| Case | Split | Category | Primary mechanism | First fixture |",
        "|---|---|---|---|---|",
    ]
    for item in result["failures"]:
        lines.append(
            f"| {item['case_id']} | {item['split']} | {item['official_category']} | {item['primary_mechanism']} | {item.get('first_failing_fixture') or '—'} |"
        )
    lines += [
        "",
        "No model reasoning was inferred; descriptions refer to observable decisions, SQL, context coverage, and evaluator outcomes.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    write()
