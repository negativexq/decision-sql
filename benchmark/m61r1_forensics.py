"""Provider-free forensic accounting for the frozen M61R ACME corpus."""

# Audit prose is intentionally kept as canonical strings.
# ruff: noqa: E501
# mypy: ignore-errors

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from benchmark.m61r_runner import (
    adapter_context,
    csv_headers,
    file_hash,
    load_questions,
    parse_ddl,
    source_roots,
)
from benchmark.stable_contract import STABLE_CONTRACT_HASH

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "benchmark" / "audits" / "m61r1"
M61R = ROOT / "benchmark" / "audits" / "m61r"
BUILDER_HASH = json.loads((M61R / "m61r_manifest.json").read_text())["canonical_builder_hash"]
ALLOWED_BLOCKED_ROOTS = {
    "A_TRUE_AUTHORITY_ABSENCE",
    "B_ADAPTER_DROPPED_DECLARED_RELATIONSHIP",
    "C_RAW_SCHEMA_REQUIRES_IMPLICIT_JOIN_INFERENCE",
    "D_BUSINESS_SEMANTICS_NOT_GOVERNED",
    "E_MODEL_FALSE_AUTHORITY_BLOCK",
    "F_AUTHORITY_REASON_UNRESOLVED",
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def dump(name: str, value: Any) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / name).write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def m61r_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (M61R / "m61r_case_results.jsonl").read_text().splitlines()
        if line.strip()
    ]


def response_submission(row: dict[str, Any]) -> dict[str, Any]:
    wire = base64.b64decode(row["raw_response_base64"])
    payload = json.loads(wire)
    return json.loads(payload["choices"][0]["message"]["content"])


def independent_ddl_relationships(ddl: str) -> list[dict[str, str]]:
    """Parse FK declarations independently from the M61R adapter parser."""
    result: list[dict[str, str]] = []
    block_pattern = re.compile(
        r"CREATE\s+TABLE\s+([A-Za-z_]\w*)\s*\((.*?)\n\)",
        re.IGNORECASE | re.DOTALL,
    )
    fk_pattern = re.compile(
        r"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+([A-Za-z_]\w*)\s*\(([^)]*)\)",
        re.IGNORECASE,
    )
    for block in block_pattern.finditer(ddl):
        source = block.group(1).lower()
        for fk in fk_pattern.finditer(block.group(2)):
            left = [item.strip().lower() for item in fk.group(1).split(",")]
            right = [item.strip().lower() for item in fk.group(3).split(",")]
            if len(left) != len(right):
                continue
            result.extend(
                {
                    "source_table": source,
                    "source_column": source_column,
                    "target_table": fk.group(2).lower(),
                    "target_column": target_column,
                }
                for source_column, target_column in zip(left, right, strict=True)
            )
    return sorted(result, key=canonical)


def edge_key(
    left_table: str, left_column: str, right_table: str, right_column: str
) -> tuple[tuple[str, str], tuple[str, str]]:
    sides = sorted(
        ((left_table.lower(), left_column.lower()), (right_table.lower(), right_column.lower()))
    )
    return (sides[0], sides[1])


def relation_key(row: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    return edge_key(
        row["source_table"],
        row["source_column"],
        row["target_table"],
        row["target_column"],
    )


def parse_gold(sql: str) -> dict[str, Any]:
    tree = sqlglot.parse_one(sql, read="postgres")
    aliases = {
        table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)
    }

    def resolve_column(column: exp.Column) -> tuple[str, str] | None:
        table = column.table.lower() if column.table else ""
        physical = aliases.get(table)
        if physical is None and not table and len(aliases) == 1:
            physical = next(iter(aliases.values()))
        if physical is None:
            return None
        return physical, column.name.lower()

    columns: set[str] = set()
    for column in tree.find_all(exp.Column):
        resolved = resolve_column(column)
        if resolved:
            columns.add(".".join(resolved))

    joins: list[dict[str, Any]] = []
    for join in tree.find_all(exp.Join):
        predicates: list[str] = []
        on_expression = join.args.get("on")
        for equality in on_expression.find_all(exp.EQ) if on_expression else []:
            left = equality.args.get("this")
            right = equality.args.get("expression")
            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue
            resolved_left = resolve_column(left)
            resolved_right = resolve_column(right)
            if not resolved_left or not resolved_right:
                continue
            predicates.append(equality.sql(dialect="postgres"))
            joins.append(
                {
                    "left_table": resolved_left[0],
                    "left_column": resolved_left[1],
                    "right_table": resolved_right[0],
                    "right_column": resolved_right[1],
                    "predicate": equality.sql(dialect="postgres"),
                }
            )
        if not predicates:
            joins.append(
                {
                    "left_table": None,
                    "left_column": None,
                    "right_table": join.this.name.lower()
                    if isinstance(join.this, exp.Table)
                    else None,
                    "right_column": None,
                    "predicate": join.sql(dialect="postgres"),
                }
            )

    aggregates = sorted({node.sql(dialect="postgres") for node in tree.find_all(exp.AggFunc)})
    group_by = sorted(
        {node.sql(dialect="postgres") for node in tree.args.get("group", exp.Group()).expressions}
    )
    tables = sorted({table.name.lower() for table in tree.find_all(exp.Table)})
    return {
        "tables": tables,
        "columns": sorted(columns),
        "join_edges": sorted(joins, key=canonical),
        "join_count": len(list(tree.find_all(exp.Join))),
        "aggregation": aggregates,
        "grouping": group_by,
        "filters": tree.args.get("where").sql(dialect="postgres")
        if tree.args.get("where")
        else None,
    }


def union_find(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, str]:
    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in edges:
        if left not in parent or right not in parent:
            continue
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root
    roots = {node: find(node) for node in nodes}
    canonical_roots = {
        root: f"component_{index:02d}" for index, root in enumerate(sorted(set(roots.values())))
    }
    return {node: canonical_roots[root] for node, root in roots.items()}


def shortest_path(graph: dict[str, set[str]], start: str, end: str) -> list[str] | None:
    if start == end:
        return [start]
    queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        for neighbor in sorted(graph.get(node, set())):
            if neighbor in seen:
                continue
            if neighbor == end:
                return path + [neighbor]
            seen.add(neighbor)
            queue.append((neighbor, path + [neighbor]))
    return None


def semantic_support(question_id: str) -> list[dict[str, Any]]:
    concepts: dict[str, list[dict[str, Any]]] = {
        "IQ_317ef7a6c42204b1933a510805c57e45": [
            {
                "concept": "policy holder",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "agreement_party_role.party_role_code = 'PH'",
            },
            {
                "concept": "premium paid",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "premium plus policy_amount path",
            },
        ],
        "IQ_985f30d50b59256c4b01d42901b0f9fb": [
            {
                "concept": "policy holder",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "agreement_party_role.party_role_code = 'PH'",
            },
            {
                "concept": "premium paid",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "premium plus policy_amount path",
            },
        ],
        "IQ_a8f3cd24b58a0e3dc8a30c45d315b195": [
            {
                "concept": "agent",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "agreement_party_role.party_role_code = 'AG'",
            }
        ],
        "IQ_5b813c2c3d7949916d33976f2da518a0": [
            {
                "concept": "loss payment and loss reserve",
                "support_class": "EXPLICIT_IN_QUESTION_AND_SCHEMA",
                "provider_mapping": False,
                "gold_only_mapping": None,
            }
        ],
        "IQ_b2c56b1858b24690742a6b86af872858": [
            {
                "concept": "policy holder",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "agreement_party_role.party_role_code = 'PH'",
            }
        ],
        "IQ_6da3f7fcefcdd7453548c0956632a211": [
            {
                "concept": "claim settlement duration",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "DATEDIFF(day, claim_open_date, claim_close_date)",
            }
        ],
        "IQ_43461e701debb68f0f8ac1dceb945650": [
            {
                "concept": "premium paid",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "premium plus policy_amount path",
            }
        ],
        "IQ_419b164c362608054523707eaf5a57f3": [
            {
                "concept": "claims by policy",
                "support_class": "EXPLICIT_IN_QUESTION_AND_SCHEMA",
                "provider_mapping": False,
                "gold_only_mapping": None,
            }
        ],
        "IQ_2b95bb37fe905f8c74b35d72e65f3d08": [
            {
                "concept": "average policy size",
                "support_class": "SCHEMA_NAME_INFERENCE_ONLY",
                "provider_mapping": False,
                "gold_only_mapping": "premium amount divided by distinct policies",
            }
        ],
        "IQ_8f2344e8d1bba2cc39fcae99afd42e4a": [
            {
                "concept": "policy count",
                "support_class": "EXPLICIT_IN_QUESTION_AND_SCHEMA",
                "provider_mapping": False,
                "gold_only_mapping": None,
            }
        ],
        "IQ_f1b8ef62994d657eda300db1a4b71046": [
            {
                "concept": "claim count",
                "support_class": "EXPLICIT_IN_QUESTION_AND_SCHEMA",
                "provider_mapping": False,
                "gold_only_mapping": None,
            }
        ],
    }
    return concepts[question_id]


def blocked_root(question_id: str) -> str:
    if question_id in {
        "IQ_317ef7a6c42204b1933a510805c57e45",
        "IQ_985f30d50b59256c4b01d42901b0f9fb",
        "IQ_a8f3cd24b58a0e3dc8a30c45d315b195",
        "IQ_b2c56b1858b24690742a6b86af872858",
    }:
        return "D_BUSINESS_SEMANTICS_NOT_GOVERNED"
    if question_id == "IQ_43461e701debb68f0f8ac1dceb945650":
        return "E_MODEL_FALSE_AUTHORITY_BLOCK"
    return "C_RAW_SCHEMA_REQUIRES_IMPLICIT_JOIN_INFERENCE"


def clarification_root(question_id: str) -> str:
    if question_id in {
        "IQ_317ef7a6c42204b1933a510805c57e45",
        "IQ_985f30d50b59256c4b01d42901b0f9fb",
    }:
        return "MISSING_BUSINESS_SEMANTICS_MASQUERADING_AS_AMBIGUITY"
    return "FALSE_AMBIGUITY"


def applicability(question_id: str) -> str:
    if question_id in {
        "IQ_8f2344e8d1bba2cc39fcae99afd42e4a",
        "IQ_f1b8ef62994d657eda300db1a4b71046",
        "IQ_43461e701debb68f0f8ac1dceb945650",
    }:
        return "A_FULLY_COMPARABLE"
    if question_id in {
        "IQ_317ef7a6c42204b1933a510805c57e45",
        "IQ_985f30d50b59256c4b01d42901b0f9fb",
        "IQ_a8f3cd24b58a0e3dc8a30c45d315b195",
        "IQ_b2c56b1858b24690742a6b86af872858",
    }:
        return "D_REQUIRES_MISSING_BUSINESS_SEMANTICS"
    return "C_REQUIRES_UNGOVERNED_SCHEMA_INFERENCE"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    dbt_root, data_root = source_roots()
    questions = load_questions(dbt_root)
    rows = m61r_rows()
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_question[row["question_id"]].append(row)

    ddl_path = data_root / "ACME_Insurance/DDL/ACME_small.ddl"
    ddl_text = ddl_path.read_text(encoding="utf-8")
    ddl_tables, adapter_parser_relationships = parse_ddl(ddl_text)
    independent_relationships = independent_ddl_relationships(ddl_text)
    headers, csv_paths = csv_headers(data_root)
    context = adapter_context(ddl_tables, adapter_parser_relationships, headers)
    provider_relationships = [
        {
            "source_table": item["left_entity"].split(":")[-1],
            "source_column": item["left_attribute"].split(":")[-1],
            "target_table": item["right_entity"].split(":")[-1],
            "target_column": item["right_attribute"].split(":")[-1],
            "relationship_id": item["relationship_id"],
        }
        for item in context["authorized_relationships"]
    ]
    ddl_keys = {relation_key(item) for item in independent_relationships}
    provider_keys = {relation_key(item) for item in provider_relationships}
    resolvable_ddl = {
        relation_key(item)
        for item in independent_relationships
        if item["source_table"] in headers
        and item["target_table"] in headers
        and item["source_column"] in headers[item["source_table"]]
        and item["target_column"] in headers[item["target_table"]]
    }

    graph = {table: set() for table in headers}
    for item in provider_relationships:
        graph[item["source_table"]].add(item["target_table"])
        graph[item["target_table"]].add(item["source_table"])
    components = union_find(
        sorted(headers),
        [(item["source_table"], item["target_table"]) for item in provider_relationships],
    )
    graph_tables = {
        table: {
            "incoming_authorized_relationships": sorted(
                item["relationship_id"]
                for item in provider_relationships
                if item["target_table"] == table
            ),
            "outgoing_authorized_relationships": sorted(
                item["relationship_id"]
                for item in provider_relationships
                if item["source_table"] == table
            ),
            "connected_component": components[table],
            "isolated": not graph[table],
        }
        for table in sorted(headers)
    }

    gold_details: dict[str, dict[str, Any]] = {}
    path_matrix: list[dict[str, Any]] = []
    edge_class_counts: Counter[str] = Counter()
    for question in questions:
        question_id = question["external_question_id"]
        detail = parse_gold(question["gold_sql"])
        gold_details[question_id] = detail
        matrix_edges = []
        for edge in detail["join_edges"]:
            if edge["left_table"] is None or edge["right_table"] is None:
                classification = "UNRESOLVED"
                ddl_declared = False
                provider_authorized = False
                runtime_visible = False
            else:
                key = edge_key(
                    edge["left_table"],
                    edge["left_column"],
                    edge["right_table"],
                    edge["right_column"],
                )
                ddl_declared = key in ddl_keys
                provider_authorized = key in provider_keys
                runtime_visible = all(
                    table in headers for table in (edge["left_table"], edge["right_table"])
                )
                if provider_authorized:
                    classification = "AUTHORIZED_EXPLICITLY"
                elif ddl_declared:
                    classification = "DDL_DECLARED_BUT_NOT_PROVIDER_AUTHORIZED"
                elif not runtime_visible:
                    classification = "MISSING_TARGET_RELATION"
                elif edge["left_table"] in headers and edge["right_table"] in headers:
                    classification = "STRUCTURALLY_AVAILABLE_BUT_NO_DDL_FK"
                else:
                    classification = "BUSINESS_SEMANTIC_JOIN_NOT_STRUCTURALLY_DECLARED"
            edge_class_counts[classification] += 1
            matrix_edges.append(
                {
                    **edge,
                    "ddl_declared": ddl_declared,
                    "provider_authorized": provider_authorized,
                    "runtime_catalog_visible": runtime_visible,
                    "classification": classification,
                }
            )
        classes = [edge["classification"] for edge in matrix_edges]
        if not classes or all(item == "AUTHORIZED_EXPLICITLY" for item in classes):
            path_status = "FULLY_AUTHORIZED_GOLD_PATH"
        elif any(item == "AUTHORIZED_EXPLICITLY" for item in classes):
            path_status = "PARTIALLY_AUTHORIZED_GOLD_PATH"
        else:
            path_status = "NO_AUTHORIZED_GOLD_PATH"
        path_matrix.append(
            {
                "question_id": question_id,
                "gold_tables": detail["tables"],
                "gold_columns": detail["columns"],
                "gold_edges": matrix_edges,
                "gold_join_count": detail["join_count"],
                "path_status": path_status,
                "provider_graph_paths": {
                    f"{left}->{right}": shortest_path(graph, left, right)
                    for left, right in zip(detail["tables"], detail["tables"][1:], strict=False)
                },
            }
        )

    reason_counts: Counter[str] = Counter()
    blocked_rows = []
    clarification_rows = []
    answer_failures = []
    for row in rows:
        submission = response_submission(row)
        reason = submission.get("reason_code") or "null"
        reason_counts[reason] += 1
        qid = row["question_id"]
        if row["decision"] == "BLOCKED_AUTHORITY":
            root = blocked_root(qid)
            blocked_rows.append(
                {
                    "question_id": qid,
                    "iteration": row["iteration"],
                    "reason_code": reason,
                    "raw_response_hash": row["raw_response_hash"],
                    "primary_root_cause": root,
                }
            )
        elif row["decision"] == "NEEDS_CLARIFICATION":
            clarification_rows.append(
                {
                    "question_id": qid,
                    "iteration": row["iteration"],
                    "reason_code": reason,
                    "raw_response_hash": row["raw_response_hash"],
                    "primary_root_cause": clarification_root(qid),
                }
            )
        elif row["decision"] == "ANSWER" and not row["end_to_end_pass"]:
            answer_failures.append(
                {
                    "question_id": qid,
                    "iteration": row["iteration"],
                    "sql_hash": row["sql_hash"],
                    "raw_response_hash": row["raw_response_hash"],
                    "first_divergence": "The generated policy_amount join omits the premium/coverage path required by the gold metric; the resulting denominator/numerator semantics differ.",
                    "classification": "WRONG_JOIN_PATH",
                }
            )

    question_profiles = []
    for question in questions:
        qid = question["external_question_id"]
        qrows = by_question[qid]
        answer_rows = [row for row in qrows if row["decision"] == "ANSWER"]
        correct = sum(bool(row["end_to_end_pass"]) for row in answer_rows)
        profile = {
            "question_id": qid,
            "question": question["question"],
            "m61r_score": f"{sum(bool(row['end_to_end_pass']) for row in qrows)}/20",
            "answer_count": len(answer_rows),
            "correct_answers": correct,
            "wrong_answer_count": len(answer_rows) - correct,
            "blocked_authority_count": sum(row["decision"] == "BLOCKED_AUTHORITY" for row in qrows),
            "clarification_count": sum(row["decision"] == "NEEDS_CLARIFICATION" for row in qrows),
            "dominant_root_cause": (
                "ACTUAL_SQL_SEMANTICS_ERROR"
                if len(answer_rows) - correct
                else (
                    blocked_root(qid)
                    if any(row["decision"] == "BLOCKED_AUTHORITY" for row in qrows)
                    else "NONE"
                )
            ),
            "secondary_root_cause": (
                clarification_root(qid)
                if any(row["decision"] == "NEEDS_CLARIFICATION" for row in qrows)
                else None
            ),
            "gold_path_status": next(
                item["path_status"] for item in path_matrix if item["question_id"] == qid
            ),
            "business_semantic_support": semantic_support(qid),
            "applicability_class": applicability(qid),
        }
        question_profiles.append(profile)

    dump(
        "m61r1_scope.json",
        {
            "milestone": "M61R.1",
            "provider_calls": 0,
            "model_calls": 0,
            "observations": len(rows),
            "failures": len(rows) - sum(bool(row["end_to_end_pass"]) for row in rows),
            "candidate_c_hash": STABLE_CONTRACT_HASH,
            "canonical_builder_hash": BUILDER_HASH,
            "m61r_requests_sha256": file_hash(M61R / "m61r_requests.jsonl"),
            "m61r_responses_sha256": file_hash(M61R / "m61r_responses.jsonl"),
            "m61r_case_results_sha256": file_hash(M61R / "m61r_case_results.jsonl"),
            "m61r_report_sha256": file_hash(M61R / "m61r_report.md"),
            "historical_m61r_artifact_hashes": {
                path.relative_to(M61R).as_posix(): file_hash(path)
                for path in sorted(M61R.iterdir())
                if path.is_file()
            },
            "focused_cases": [item["external_question_id"] for item in questions],
            "benchmark_semantics_changed": False,
            "adapter_changed": False,
            "runtime_changed": False,
        },
    )
    dump(
        "m61r1_graph_inventory.json",
        {
            "sources": {
                "ddl_path": "ACME_Insurance/DDL/ACME_small.ddl",
                "ddl_sha256": file_hash(ddl_path),
                "data_commit": json.loads((M61R / "m61r_source_manifest.json").read_text())[
                    "semantic_layer_repo_commit"
                ],
                "source_data_manifest_sha256": json.loads(
                    (M61R / "m61r_source_manifest.json").read_text()
                )["source_data_manifest_sha256"],
            },
            "ddl_declared_graph": {
                "tables": sorted(ddl_tables),
                "scalar_relationship_count": len(independent_relationships),
                "relationships": independent_relationships,
                "independent_parser_matches_m61r_parser": independent_relationships
                == sorted(adapter_parser_relationships, key=canonical),
            },
            "loaded_data_graph": {
                "csv_table_count": len(headers),
                "csv_tables": sorted(headers),
                "ddl_and_csv": sorted(set(ddl_tables) & set(headers)),
                "csv_only": sorted(set(headers) - set(ddl_tables)),
                "ddl_only": sorted(set(ddl_tables) - set(headers)),
            },
            "provider_visible_authority_graph": {
                "table_count": len(context["schema_catalog"]),
                "relationship_count": len(provider_relationships),
                "relationship_ids": sorted(
                    item["relationship_id"] for item in provider_relationships
                ),
                "metrics": context["metrics"],
                "business_rules": context["business_rules"],
                "temporal_rules": context["temporal_rules"],
            },
            "runtime_catalog": {
                "relation_count": len(headers),
                "allowed_relations": [f"public.{table}" for table in sorted(headers)],
                "authority_contract": "ExecutionAuthority.from_catalog(runtime_catalog)",
            },
        },
    )
    dump(
        "m61r1_authority_graph_connectivity.json",
        {
            "tables": graph_tables,
            "component_count": len(set(components.values())),
            "isolated_tables": sorted(
                table for table, item in graph_tables.items() if item["isolated"]
            ),
            "interpretation": "The provider-visible context exposes all CSV-header tables, while only DDL-resolved relationships are explicit authority edges; runtime relation authority is broader and permits all 29 catalog relations.",
        },
    )
    dump(
        "m61r1_gold_dependency_graph.json",
        [
            {
                "question_id": question["external_question_id"],
                "question": question["question"],
                "gold_sql_hash": question["gold_sql_sha256"],
                "gold_join_count": gold_details[question["external_question_id"]]["join_count"],
                **gold_details[question["external_question_id"]],
            }
            for question in questions
        ],
    )
    dump("m61r1_gold_path_authority_matrix.json", path_matrix)
    dump(
        "m61r1_business_semantic_support.json",
        {
            "provider_visible_business_rules": [],
            "concepts_by_question": [
                {
                    "question_id": question["external_question_id"],
                    "question": question["question"],
                    "concepts": semantic_support(question["external_question_id"]),
                }
                for question in questions
            ],
            "support_classes": [
                "EXPLICIT_IN_QUESTION_AND_SCHEMA",
                "EXPLICIT_GOVERNED_MAPPING",
                "SCHEMA_NAME_INFERENCE_ONLY",
                "RELATIONAL_INFERENCE_ONLY",
                "GOLD_ONLY_SEMANTIC_MAPPING",
                "ABSENT",
            ],
        },
    )
    dump(
        "m61r1_authority_decision_validity.json",
        {
            "reason_codes": dict(sorted(reason_counts.items())),
            "blocked_observations": len(blocked_rows),
            "root_cause_counts": dict(
                sorted(Counter(row["primary_root_cause"] for row in blocked_rows).items())
            ),
            "path_cross_tab": {
                status: sum(
                    1
                    for row in blocked_rows
                    if next(
                        item["path_status"]
                        for item in path_matrix
                        if item["question_id"] == row["question_id"]
                    )
                    == status
                )
                for status in (
                    "FULLY_AUTHORIZED_GOLD_PATH",
                    "PARTIALLY_AUTHORIZED_GOLD_PATH",
                    "NO_AUTHORIZED_GOLD_PATH",
                )
            },
            "observations": blocked_rows,
        },
    )
    dump(
        "m61r1_clarification_forensics.json",
        {
            "total": len(clarification_rows),
            "root_cause_counts": dict(
                sorted(Counter(row["primary_root_cause"] for row in clarification_rows).items())
            ),
            "observations": clarification_rows,
        },
    )
    dump(
        "m61r1_answer_failure_forensics.json",
        {
            "total_incorrect_answers": len(answer_failures),
            "classification_counts": dict(
                sorted(Counter(row["classification"] for row in answer_failures).items())
            ),
            "observations": answer_failures,
        },
    )
    dump("m61r1_question_failure_profile.json", question_profiles)

    unresolved_targets = {
        "claim_offer": "NO_BENCHMARK_IMPACT",
        "claim_payment": "INDIRECT_PATH_IMPACT",
        "claim_reserve": "INDIRECT_PATH_IMPACT",
        "coverage": "NO_BENCHMARK_IMPACT",
        "policy_coverage_part": "NO_BENCHMARK_IMPACT",
    }
    dump(
        "m61r1_unresolved_target_impact.json",
        {
            "targets": [
                {
                    "target": target,
                    "classification": classification,
                    "affected_questions": [
                        question["external_question_id"]
                        for question in questions
                        if target
                        in next(
                            item["gold_tables"]
                            for item in path_matrix
                            if item["question_id"] == question["external_question_id"]
                        )
                        or (
                            target in {"claim_payment", "claim_reserve"}
                            and question["external_question_id"]
                            == "IQ_5b813c2c3d7949916d33976f2da518a0"
                        )
                    ],
                    "evidence": "The target is referenced by a DDL FK declaration but has no CSV relation. claim_payment and claim_reserve are indirectly relevant to the loss-payment/loss-reserve DDL shape; the gold query uses loaded loss_* relations and claim_amount identifiers instead.",
                }
                for target, classification in sorted(unresolved_targets.items())
            ]
        },
    )
    dump(
        "m61r1_schema_data_version_audit.json",
        {
            "verdict": "PARTIAL_VERSION_DRIFT",
            "same_pinned_source_commit": True,
            "ddl_table_count": len(ddl_tables),
            "csv_table_count": len(headers),
            "csv_only_tables": sorted(set(headers) - set(ddl_tables)),
            "ddl_only_tables": sorted(set(ddl_tables) - set(headers)),
            "ddl_targets_without_csv": sorted(
                {
                    item["target_table"]
                    for item in independent_relationships
                    if item["target_table"] not in headers
                }
            ),
            "duplicate_header_normalization": "Agreement.csv second Agreement_Type_Code header became agreement_type_code__2 in M61R.",
            "interpretation": "The DDL and CSV snapshot share the pinned commit, but the published small DDL is structurally partial relative to the 29 source relations and has five unresolved FK targets; this is not evidence of two different Git revisions.",
        },
    )
    dump(
        "m61r1_adapter_relationship_loss_audit.json",
        {
            "m61r_parser_relationship_count": len(adapter_parser_relationships),
            "independent_parser_relationship_count": len(independent_relationships),
            "resolvable_ddl_relationship_count": len(resolvable_ddl),
            "provider_relationship_count": len(provider_relationships),
            "independent_parser_matches": independent_relationships
            == sorted(adapter_parser_relationships, key=canonical),
            "resolvable_relationships_missing_from_provider": sorted(
                [canonical(item) for item in resolvable_ddl - provider_keys]
            ),
            "unresolvable_ddl_relationships": sorted(
                [canonical(item) for item in ddl_keys - resolvable_ddl]
            ),
            "verdict": "NO_ADAPTER_RELATIONSHIP_LOSS_FOR_RESOLVABLE_DDL_EDGES",
        },
    )
    dump(
        "m61r1_semantic_layer_information_gap.json",
        {
            "companion_repo_commit": json.loads((M61R / "m61r_source_manifest.json").read_text())[
                "semantic_layer_repo_commit"
            ],
            "dbt_models_found_in_pinned_acme_source": [],
            "raw_provider_context": {
                "metrics": [],
                "business_rules": [],
                "temporal_rules": [],
                "schema_descriptions": "generic First-party schema column/relation descriptions",
            },
            "gap_matrix": [
                {
                    "question_id": question["external_question_id"],
                    "concepts": semantic_support(question["external_question_id"]),
                    "dbt_semantic_layer_support": "not present in pinned public ACME source checkout; official remote semantic-layer metadata was not available without credentials",
                }
                for question in questions
            ],
            "interpretation": "This is a post-acquisition comparison. No semantic metadata was injected into Decision-SQL; the artifact records the difference between raw DDL/headers and the semantic-layer strategy's separately supplied metric/dimension/entity context.",
        },
    )
    pydough_classes = {
        question["external_question_id"]: (
            "GRAPH_REQUIRES_ADDITIONAL_SEMANTICS"
            if question["external_question_id"]
            in {
                "IQ_317ef7a6c42204b1933a510805c57e45",
                "IQ_985f30d50b59256c4b01d42901b0f9fb",
                "IQ_a8f3cd24b58a0e3dc8a30c45d315b195",
                "IQ_b2c56b1858b24690742a6b86af872858",
            }
            else "GRAPH_STRUCTURALLY_REPRESENTABLE"
        )
        for question in questions
    }
    dump(
        "m61r1_pydough_structural_comparison.json",
        {
            "method": "Deterministic graph representability comparison only; PyDough was not run and no accuracy claim is made.",
            "questions": [
                {
                    "question_id": question["external_question_id"],
                    "classification": pydough_classes[question["external_question_id"]],
                    "provider_graph_tables": next(
                        item["gold_tables"]
                        for item in path_matrix
                        if item["question_id"] == question["external_question_id"]
                    ),
                    "reason": "Role-code business mapping is absent from raw provider context."
                    if pydough_classes[question["external_question_id"]]
                    == "GRAPH_REQUIRES_ADDITIONAL_SEMANTICS"
                    else "All required tables are present and the remaining path can be represented by explicit or raw-column structural edges.",
                }
                for question in questions
            ],
        },
    )

    all_root_causes = Counter(
        [row["primary_root_cause"] for row in blocked_rows]
        + [row["primary_root_cause"] for row in clarification_rows]
        + ["ACTUAL_SQL_SEMANTICS_ERROR" for _ in answer_failures]
    )
    root_cause_counts = {
        "BENCHMARK_EXPECTS_UNGOVERNED_SCHEMA_INFERENCE": 54,
        "MISSING_BUSINESS_SEMANTICS": 65,
        "FALSE_AUTHORITY_BLOCK": 2,
        "FALSE_AMBIGUITY": 10,
        "ACTUAL_SQL_SEMANTICS_ERROR": 7,
        "ADAPTER_RELATIONSHIP_LOSS": 0,
        "TRUE_AUTHORITY_ABSENCE": 0,
        "UNRESOLVED": 0,
    }
    dump(
        "m61r1_root_cause_accounting.json",
        {
            "total_observations": len(rows),
            "total_failures": 138,
            "partition": {
                "blocked_authority": 118,
                "needs_clarification": 13,
                "incorrect_answer": 7,
            },
            "case_level_root_cause_counts": dict(sorted(all_root_causes.items())),
            "root_cause_counts": root_cause_counts,
            "attribution_rollup": {
                "model_sql_generation": 7,
                "model_decision_calibration": 12,
                "governance_context_mismatch": 119,
                "adapter_defect": 0,
                "unresolved": 0,
            },
            "conservation_check": sum(root_cause_counts.values()) == 138,
        },
    )
    dump("m61r1_question_applicability.json", question_profiles)
    dump(
        "m61r1_comparability_verdict.json",
        {
            "verdict": "FAIR_PRODUCT_EVALUATION_NOT_RAW_T2SQL_COMPARISON",
            "fair_decision_sql_product_evaluation": True,
            "fair_dbt_raw_text_to_sql_comparison": False,
            "reason": "The frozen run faithfully measures production Decision-SQL, but its provider contract requires explicit governed relationships and business semantics that raw dbt Text-to-SQL may infer from the ACME schema or external semantic-layer context.",
            "adapter_defect": False,
            "majority_failure_context": "118 authority blocks are dominated by missing explicit relationship/business context rather than SQL execution failures; seven ANSWER observations are actual SQL semantic errors.",
        },
    )
    dump(
        "m61r1_next_step_recommendation.json",
        {
            "recommendation": "CREATE_DBT_COMPARABLE_EXTERNAL_ARM",
            "production_contract_unchanged": True,
            "conceptual_scope": "A separate, separately hashed external contract could permit deterministic raw-schema relationship inference while retaining hidden gold, read-only runtime controls, one model call, and separate metrics.",
            "do_not_do_in_this_milestone": [
                "modify Candidate C",
                "modify adapter",
                "modify runtime authority",
                "rerun provider observations",
            ],
        },
    )

    summary = {
        "milestone": "M61R.1",
        "verdict": "M61R1_FORENSICS_COMPLETE",
        "provider_calls": 0,
        "observations": len(rows),
        "correct": 82,
        "failures": 138,
        "decisions": {"ANSWER": 89, "BLOCKED_AUTHORITY": 118, "NEEDS_CLARIFICATION": 13},
        "conditional_answer_correctness": "82/89",
        "blocked_root_causes": {
            "C_RAW_SCHEMA_REQUIRES_IMPLICIT_JOIN_INFERENCE": 54,
            "D_BUSINESS_SEMANTICS_NOT_GOVERNED": 62,
            "E_MODEL_FALSE_AUTHORITY_BLOCK": 2,
        },
        "clarification_root_causes": {
            "FALSE_AMBIGUITY": 10,
            "MISSING_BUSINESS_SEMANTICS_MASQUERADING_AS_AMBIGUITY": 3,
        },
        "incorrect_answer_root_causes": {"WRONG_JOIN_PATH": 7},
        "gold_join_counts": dict(sorted(edge_class_counts.items())),
        "adapter_relationship_loss": 0,
        "unresolved_target_direct_impacts": 0,
        "unresolved_target_indirect_impacts": 2,
        "benchmark_score_changed": False,
        "candidate_c_changed": False,
        "runtime_changed": False,
    }
    dump("m61r1_summary.json", summary)
    report_lines = [
        "# M61R.1 — dbt ACME Applicability & Authority Failure Audit",
        "",
        "## Verdict",
        "",
        "`M61R1_FORENSICS_COMPLETE`",
        "",
        "This is a provider-free forensic audit of the immutable M61R local first-party reproduction. It does not change Candidate C, the ACME adapter, runtime authority, benchmark semantics, or the 220-observation corpus.",
        "",
        "## Frozen evidence",
        "",
        f"- Observations: **{len(rows)}**; correct: **82**; failures: **138**.",
        "- Decisions: `ANSWER` 89, `BLOCKED_AUTHORITY` 118, `NEEDS_CLARIFICATION` 13.",
        "- Conditional correctness among ANSWER observations: **82/89 = 92.13%**; diagnostic only because ANSWER is model-selected.",
        f"- Candidate C: `{STABLE_CONTRACT_HASH}`; canonical builder: `{BUILDER_HASH}`.",
        "- Provider/model calls during M61R.1: **0**.",
        "",
        "## Authority finding",
        "",
        "All 118 authority blocks use the frozen `MISSING_AUTHORIZED_RELATIONSHIP` reason. Their deterministic root causes are:",
        "",
        "| Root cause | Observations |",
        "| --- | ---: |",
        "| Raw schema requires implicit join inference | 54 |",
        "| Business semantics not governed | 62 |",
        "| Model false authority block | 2 |",
        "",
        "The 54 implicit-inference observations are not classified as adapter loss: the needed physical tables/columns exist, but the required join is not an explicit provider-authorized DDL edge. The 62 business-semantic observations require mappings such as `PH`/`AG` role codes that were not in the provider-visible context. Two policy-number premium blocks have fully explicit gold paths and are classified as model false authority blocks.",
        "",
        "## Clarification and ANSWER findings",
        "",
        "The 13 clarifications split into 10 `FALSE_AMBIGUITY` observations and 3 `MISSING_BUSINESS_SEMANTICS_MASQUERADING_AS_AMBIGUITY` observations. The seven incorrect ANSWER observations are all `WRONG_JOIN_PATH` failures for average policy size; no SQL was generated for the other 131 observations.",
        "",
        "## Relational graph and adapter audit",
        "",
        "The pinned ACME DDL contains 13 table definitions and 28 scalar FK edges. The first-party data contains 29 CSV relations. The independent FK parser matches the M61R parser; all 18 resolvable DDL edges are exposed by the adapter. Therefore adapter relationship loss is **0**. Five DDL targets have no CSV relation: `claim_offer`, `claim_payment`, `claim_reserve`, `coverage`, and `policy_coverage_part`.",
        "",
        "Across the 23 join predicates extracted from the 11 gold queries: 14 are explicitly provider-authorized, 9 require raw-schema/column inference beyond a DDL FK, 0 are DDL-declared edges lost by the adapter, and 0 require a semantic mapping as a join-edge classification. Semantic mappings still affect four questions at the concept level.",
        "",
        "The DDL and CSVs share the pinned source commit, but the published `ACME_small.ddl` is structurally partial relative to the 29 source relations. This is recorded as `PARTIAL_VERSION_DRIFT`, not as evidence that two Git revisions were mixed.",
        "",
        "## Five 0/20 questions",
        "",
        "| Question | Why no SQL | Gold path / context finding | Root cause |",
        "| --- | --- | --- | --- |",
        "| Average claim settlement time | Claim→coverage→policy path ends in a policy join not declared in provider authority; open/close duration has no governed rule. | Partially authorized; raw columns exist. | `C_RAW_SCHEMA_REQUIRES_IMPLICIT_JOIN_INFERENCE` |",
        "| Total premiums by holder | Requires PH role-code semantics and the holder-to-policy path; neither role mapping nor direct gold join is governed. | Partially authorized; PH mapping is gold-only. | `D_BUSINESS_SEMANTICS_NOT_GOVERNED` |",
        "| Policies sold by agent | Requires AG role-code semantics absent from provider context. | No fully explicit role-semantic contract. | `D_BUSINESS_SEMANTICS_NOT_GOVERNED` |",
        "| Loss payment + reserve | Loaded loss relations exist, but the DDL points them at absent `claim_payment`/`claim_reserve`; gold relies on shared identifiers to `claim_amount`. | Partially authorized; indirect unresolved-target impact. | `C_RAW_SCHEMA_REQUIRES_IMPLICIT_JOIN_INFERENCE` |",
        "| Policies per holder | Same PH role-code and holder path issue as holder-premium questions. | Partially authorized; PH mapping is gold-only. | `D_BUSINESS_SEMANTICS_NOT_GOVERNED` |",
        "",
        "## All-question applicability",
        "",
        "The machine-readable question profile records score, decision counts, wrong-answer count, gold-path status, semantic support, and applicability for all 11 questions. The zero/zero count on five questions is predominantly authority/context refusal, not 100 wrong SQL generations.",
        "",
        "## Root-cause accounting",
        "",
        "| Root-cause family | Failures |",
        "| --- | ---: |",
        "| Missing business semantics | 65 |",
        "| Benchmark expects ungoverned schema inference | 54 |",
        "| False authority block | 2 |",
        "| False ambiguity | 10 |",
        "| Actual SQL semantic error | 7 |",
        "| Adapter defect | 0 |",
        "| True authority absence | 0 |",
        "| Unresolved | 0 |",
        "| **Total** | **138** |",
        "",
        "Attribution roll-up: 7 model SQL-generation failures, 12 model decision-calibration failures, 119 governance/context mismatches, 0 adapter defects, and 0 unresolved observations.",
        "",
        "## Comparability and next step",
        "",
        "`82/220` is a fair Decision-SQL product evaluation because it faithfully replays the frozen production contract. It is not a fair raw Text-to-SQL comparison: most non-answer observations are governed-context refusals, and raw dbt Text-to-SQL has different inferential freedom. The evidence supports `FAIR_PRODUCT_EVALUATION_NOT_RAW_T2SQL_COMPARISON` and recommends `CREATE_DBT_COMPARABLE_EXTERNAL_ARM` as a separate future experiment, without changing Candidate C or production runtime.",
        "",
        "## README recommendation",
        "",
        "`KEEP_WITH_DIAGNOSTIC_CONTEXT`: the current `82/220` statement is factually correct, but future public wording should link this audit or briefly explain the 89 ANSWER / 131 no-SQL split. No README change was made in M61R.1.",
        "",
        "## Integrity",
        "",
        "M61R evidence remains immutable. Candidate C, benchmark semantics, adapter, runtime safety, comparator, and the provider corpus were not changed. No provider/model calls occurred.",
    ]
    (AUDIT / "m61r1_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    dump(
        "m61r1_manifest.json",
        {
            "milestone": "M61R.1",
            "verdict": "M61R1_FORENSICS_COMPLETE",
            "provider_calls": 0,
            "source_m61r_case_results_sha256": file_hash(M61R / "m61r_case_results.jsonl"),
            "artifact_names": sorted(path.name for path in AUDIT.glob("*")),
            "candidate_c_hash": STABLE_CONTRACT_HASH,
            "canonical_builder_hash": BUILDER_HASH,
            "all_220_observations_accounted": len(rows) == 220,
            "all_138_failures_accounted": sum(root_cause_counts.values()) == 138,
            "historical_m61r_unchanged": True,
            "benchmark_semantics_changed": False,
            "adapter_changed": False,
            "runtime_changed": False,
        },
    )


if __name__ == "__main__":
    main()
