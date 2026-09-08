"""Deterministic M40 contract repair and audit helpers.

This module is deliberately provider-free.  It repairs only benchmark-side
artifacts whose requirements can be derived from the schema, public authority,
questions, and independent references.  It never reads model outputs.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from benchmark.context import load_authority

ROOT = Path(__file__).resolve().parent
M40_VERSION = "0.2.1-dev"
M38_DATABASES = {"subscription_billing", "warehouse_logistics", "risk_operations"}
PILOT_DATABASES = {"commerce_ops", "fleet_ops", "support_ops"}
M39_ROOT = ROOT / "experiments" / "results" / "m39"
M38_CASES = ROOT / "cases" / "m38_dev"
M38_TRUTH = ROOT / "ground_truth" / "m38_dev"


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _schema_columns(database_id: str) -> dict[str, dict[str, str]]:
    text = (ROOT / "databases" / database_id / "schema.sql").read_text(encoding="utf-8")
    result: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"CREATE TABLE\s+(\w+)\s*\((.*?)\);", text, re.I | re.S):
        table, body = match.groups()
        result[table] = {}
        depth = 0
        parts: list[str] = []
        start = 0
        for index, character in enumerate(body):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            elif character == "," and depth == 0:
                parts.append(body[start:index])
                start = index + 1
        parts.append(body[start:])
        for line in parts:
            item = line.strip().split()
            if len(item) < 2 or item[0].upper() in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
                continue
            column = item[0].strip('"')
            type_text = item[1].upper()
            if type_text.startswith("TIMESTAMPTZ"):
                type_text = "TIMESTAMPTZ"
            elif type_text.startswith("TIMESTAMP"):
                type_text = "TIMESTAMP"
            elif type_text.startswith("NUMERIC") or type_text.startswith("DECIMAL"):
                type_text = "NUMERIC"
            elif type_text.startswith("VARCHAR") or type_text.startswith("CHAR"):
                type_text = "TEXT"
            result[table][column] = type_text
    return result


def _attribute(
    database_id: str, table: str, column: str, data_type: str, *, path: str | None = None
) -> dict[str, Any]:
    physical = path or column
    description = f"Physical {column} field on {table}."
    if path:
        description = f"Documented JSON scalar at {path} on {table}.{column}."
    return {
        "attribute_id": f"attribute:{database_id}:{table}:{physical.replace(' ', '_')}",
        "entity_id": f"entity:{database_id}:{table}",
        "physical_column_or_path": physical,
        "data_type": data_type,
        "semantic_description": description,
        "nullable": False,
        "visibility_provenance": "M40_SCHEMA_AND_PUBLIC_SEMANTICS",
    }


def complete_new_authority() -> dict[str, Any]:
    """Expose needed physical source columns without changing relationships."""
    changes: dict[str, Any] = {}
    json_paths = {
        ("subscription_billing", "usage_events", "payload"): [("units", "INTEGER")],
        ("warehouse_logistics", "delivery_events", "payload"): [("temperature_c", "NUMERIC")],
        ("risk_operations", "transactions", "payload"): [("risk_score", "NUMERIC")],
    }
    for database_id in sorted(M38_DATABASES):
        authority_root = ROOT / "databases" / database_id / "authority"
        attrs_path = authority_root / "attributes.json"
        attrs = cast(list[dict[str, Any]], json.loads(attrs_path.read_text(encoding="utf-8")))
        columns = _schema_columns(database_id)
        attrs = [
            item
            for item in attrs
            if (
                str(item.get("physical_column_or_path", "")).split(" #>> ", 1)[0]
                in columns.get(str(item.get("entity_id", "")).rsplit(":", 1)[-1], {})
                or " #>> " in str(item.get("physical_column_or_path", ""))
            )
        ]
        existing = {
            (
                str(item.get("entity_id", "")).rsplit(":", 1)[-1],
                str(item.get("physical_column_or_path", "")),
            )
            for item in attrs
        }
        added: list[str] = []
        for table in sorted(columns):
            for column, data_type in columns[table].items():
                if (table, column) not in existing:
                    attrs.append(_attribute(database_id, table, column, data_type))
                    added.append(f"{table}.{column}")
        for (db, table, column), paths in json_paths.items():
            if db != database_id:
                continue
            for key, data_type in paths:
                physical = f"{column} #>> '{{{key}}}'"
                if (table, physical) not in existing:
                    attrs.append(_attribute(database_id, table, column, data_type, path=physical))
                    added.append(f"{table}.{physical}")
        attrs.sort(key=lambda item: str(item.get("attribute_id", "")))
        _dump(attrs_path, attrs)
        changes[database_id] = {"added_attributes": added, "attribute_count": len(attrs)}
    return changes


def _table_aliases(sql: str, tables: dict[str, dict[str, str]]) -> dict[str, str]:
    aliases: dict[str, str] = {table: table for table in tables}
    for match in re.finditer(
        r"\b(?:FROM|JOIN)\s+([a-z_]\w*)(?:\s+(?:AS\s+)?([a-z_]\w*))?",
        sql,
        re.I,
    ):
        table, alias = match.groups()
        if table in tables:
            aliases[table] = table
            if alias and alias.upper() not in {
                "WHERE",
                "ON",
                "JOIN",
                "LEFT",
                "RIGHT",
                "FULL",
                "INNER",
                "GROUP",
                "ORDER",
            }:
                aliases[alias] = table
    return aliases


def _reference_source_fields(
    database_id: str, sqls: Iterable[str]
) -> tuple[set[tuple[str, str]], set[str]]:
    tables = _schema_columns(database_id)
    fields: set[tuple[str, str]] = set()
    json_fields: set[str] = set()
    for sql in sqls:
        aliases = _table_aliases(sql, tables)
        used_tables: set[str] = set()
        for match in re.finditer(
            r"\b(?:FROM|JOIN)\s+([a-z_]\w*)(?:\s+(?:AS\s+)?([a-z_]\w*))?",
            sql,
            re.I,
        ):
            table, alias = match.groups()
            if table in tables:
                used_tables.add(table)
                if alias and alias.upper() not in {
                    "WHERE",
                    "ON",
                    "JOIN",
                    "LEFT",
                    "RIGHT",
                    "FULL",
                    "INNER",
                    "GROUP",
                    "ORDER",
                }:
                    aliases[alias] = table
        # Qualified references are authoritative and cover SELECT, JOIN, WHERE,
        # GROUP, ORDER, and nested query expressions.
        for match in re.finditer(r"\b([a-z_]\w*)\.([a-z_]\w*)\b", sql, re.I):
            alias, column = match.groups()
            table = aliases.get(alias)
            if table in tables and column in tables[table]:
                fields.add((table, column))
        # Unqualified fields are assigned only when they have one possible
        # source among the tables named by the reference.
        for column in set(re.findall(r"\b([a-z_]\w*)\b", sql, re.I)):
            possible = [table for table in used_tables if column in tables.get(table, {})]
            if len(possible) == 1:
                fields.add((possible[0], column))
        for match in re.finditer(r"\b([a-z_]\w*)\s*[-][>]{1,2}\s*'?([a-z_]\w*)'", sql, re.I):
            alias, key = match.groups()
            table = aliases.get(alias)
            if table:
                json_fields.add(f"{table}.payload #>> '{{{key}}}'")
        for match in re.finditer(r"\bpayload\s*[-][>]{1,2}\s*'?([a-z_]\w*)'", sql, re.I):
            key = match.group(1)
            payload_tables = [table for table in used_tables if "payload" in tables.get(table, {})]
            if len(payload_tables) == 1:
                json_fields.add(f"{payload_tables[0]}.payload #>> '{{{key}}}'")
    return fields, json_fields


def _relationship_graph(database_id: str) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    relationships = [
        item
        for item in load_authority(database_id)["relationships"]
        if item.get("authorized") is True
    ]
    graph: dict[str, set[str]] = {}
    for relation in relationships:
        left = str(relation["left_entity"]).rsplit(":", 1)[-1]
        right = str(relation["right_entity"]).rsplit(":", 1)[-1]
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
    return relationships, graph


def _relationship_facts(database_id: str, tables: set[str]) -> list[str]:
    relationships, graph = _relationship_graph(database_id)
    by_pair: dict[frozenset[str], dict[str, Any]] = {}
    for relation in relationships:
        left = str(relation["left_entity"]).rsplit(":", 1)[-1]
        right = str(relation["right_entity"]).rsplit(":", 1)[-1]
        by_pair[frozenset((left, right))] = relation
    selected: set[str] = set()
    requested = sorted(tables)
    for start in requested:
        for goal in requested:
            if start >= goal or start not in graph or goal not in graph:
                continue
            queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
            seen = {start}
            path: list[str] | None = None
            while queue:
                node, current = queue.popleft()
                if node == goal:
                    path = current
                    break
                for nxt in sorted(graph.get(node, set())):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append((nxt, current + [nxt]))
            if path:
                for left, right in zip(path, path[1:], strict=False):
                    candidate = by_pair.get(frozenset((left, right)))
                    if candidate:
                        selected.add(str(candidate["relationship_id"]))
    return sorted(selected)


def _public_order(question: str) -> bool:
    lowered = question.lower()
    return bool(
        re.search(r"\bordered\s+by\b|\bascending\b|\bdescending\b|\bsorted\s+by\b", lowered)
    )


def _append_population_clarity(question: str, mode: str) -> str:
    all_anchor_suffix = (
        " Include entities with no matching records, using the stated NULL or zero behavior."
    )
    matching_suffix = " Include only groups represented by at least one qualifying source record."
    if all_anchor_suffix in question:
        return question[: question.index(all_anchor_suffix) + len(all_anchor_suffix)]
    if matching_suffix in question:
        return question[: question.index(matching_suffix) + len(matching_suffix)]
    if mode == "all-anchors":
        return question + all_anchor_suffix
    if (
        question.lower().startswith("for each ")
        and "represented by at least one" not in question.lower()
    ):
        return question + matching_suffix
    return question


def _semantic_facts(
    database_id: str, question: str, sqls: list[str], relationships: list[str]
) -> list[str]:
    fields, json_fields = _reference_source_fields(database_id, sqls)
    facts = [f"attributes:{database_id}:{table}:{column}" for table, column in sorted(fields)]
    facts.extend(
        f"attributes:{database_id}:{field.split('.', 1)[0]}:{field.split('.', 1)[1]}"
        for field in sorted(json_fields)
    )
    facts.extend(relationships)
    lowered = question.lower()
    authority = load_authority(database_id)
    if any(
        word in lowered
        for word in ("latest", "timestamp", "time", "date", "june", "after", "before", "closed")
    ):
        facts.append(f"temporal_rules:{database_id}:clock")
    for rule in authority["business_rules"]:
        name = str(rule.get("name", "")).lower()
        if (
            ("latest" in lowered and "latest" in name)
            or ("late" in lowered and "late" in name)
            or ("active" in lowered and "active" in name)
            or ("open" in lowered and "open" in name)
        ):
            facts.append(str(rule["rule_id"]).replace("rule:", "business_rules:", 1))
    for metric in authority["metrics"]:
        name = str(metric.get("name", "")).lower().replace("_", " ")
        if name in lowered or (name == "mrr" and "monthly recurring" in lowered):
            facts.append(str(metric["metric_id"]).replace("metric:", "metrics:", 1))
    return sorted(set(facts))


def repair_new_cases() -> dict[str, Any]:
    """Repair generated M38 contracts from public question/reference evidence."""
    changed: list[dict[str, Any]] = []
    answerable = 0
    for case_path in sorted(M38_CASES.glob("*.json")):
        case = cast(dict[str, Any], json.loads(case_path.read_text(encoding="utf-8")))
        truth_path = M38_TRUTH / case_path.name
        truth = cast(dict[str, Any], json.loads(truth_path.read_text(encoding="utf-8")))
        if case.get("task_type") != "ANSWERABLE":
            continue
        answerable += 1
        target = cast(dict[str, Any], truth["semantic_target"])
        sqls = [
            str(truth.get("reference_implementation_a", {}).get("sql", "")),
            str(truth.get("reference_implementation_b", {}).get("sql", "")),
        ]
        database_id = str(case["database_id"])
        if case["case_id"] == "warehouse_07":
            case["question"] = (
                "For each warehouse, return the warehouse ID and average hours from shipment time to pick."
            )
        population = (
            "all-anchors" if case["case_id"] in {"risk_12", "warehouse_13"} else "matching-only"
        )
        old_question = str(case["question"])
        case["question"] = _append_population_clarity(case["question"], population)
        row_order = _public_order(str(case["question"]))
        relationship_facts = _relationship_facts(
            database_id,
            {table for table, _column in _reference_source_fields(database_id, sqls)[0]},
        )
        facts = _semantic_facts(database_id, str(case["question"]), sqls, relationship_facts)
        target["relationships"] = relationship_facts
        target["population"] = population
        target["ordering"] = (
            {"columns": target.get("outputs", []), "direction": "ASC"} if row_order else {}
        )
        target["result_comparison_contract"]["row_order"] = row_order
        target["semantic_provenance"]["ordering"] = {
            "source": "QUESTION_EXPLICIT" if row_order else "NOT_APPLICABLE"
        }
        target["semantic_provenance"]["population"] = {"source": "QUESTION_EXPLICIT"}
        truth["required_context_facts"] = facts
        if old_question != case["question"] or target.get("population") != population:
            changed.append(
                {
                    "case_id": case["case_id"],
                    "old_question": old_question,
                    "new_question": case["question"],
                    "population": population,
                    "row_order": row_order,
                    "required_context_facts": facts,
                }
            )
        _dump(case_path, case)
        _dump(truth_path, truth)
    return {"answerable": answerable, "changed": changed, "changed_count": len(changed)}


def historical_m39_hashes() -> dict[str, str]:
    return {path.name: _sha(path) for path in sorted(M39_ROOT.glob("m39_*")) if path.is_file()}


def git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, check=True, capture_output=True, text=True
    ).stdout.strip()


def run_repairs() -> dict[str, Any]:
    before = historical_m39_hashes()
    authority = complete_new_authority()
    cases = repair_new_cases()
    return {
        "version": M40_VERSION,
        "starting_commit": git_revision(),
        "provider_calls": 0,
        "m39_hashes_before": before,
        "authority": authority,
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(run_repairs(), indent=2, sort_keys=True))
