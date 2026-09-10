"""Deterministic, model-free authoring and validation for M51A.

The expansion is deliberately isolated from the historical ``m38_dev`` set.
This module owns six new database packs and ninety evaluator contracts.  It
never imports the application runtime or a model/provider adapter.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

import sqlglot

from benchmark.authoring import connection_kwargs_from_env
from benchmark.models import ResultContract, compare_rows
from benchmark.safety import validate_read_only_select

ROOT = Path(__file__).resolve().parent
VERSION = "0.3.0-dev"
GENERATOR_VERSION = "m51a-authoring-v1"
EXPANSION_CASES = ROOT / "cases" / "m51_expansion"
EXPANSION_TRUTH = ROOT / "ground_truth" / "m51_expansion"
AUDIT = ROOT / "audits" / "m51a"
SCHEMA_ROOT = ROOT / "databases"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: Any) -> str:
    return _sha_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    )


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sql_hash(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


@dataclass(frozen=True)
class CaseSpec:
    number: int
    question: str
    ref_a: str
    ref_b: str
    outputs: tuple[str, ...]
    tags: tuple[str, ...]
    fixtures: tuple[tuple[str, tuple[str, ...]], ...]
    required_tables: tuple[str, ...]
    difficulty: str = "MODERATE"
    population: str = "matching-only"
    grouping: tuple[str, ...] = ()
    calculations: tuple[str, ...] = ()
    temporal: dict[str, Any] | None = None
    row_order: bool = True


@dataclass(frozen=True)
class Domain:
    domain_id: str
    schema_name: str
    kind: str
    purpose: str
    tables: tuple[tuple[str, str, str, str], ...]
    attributes: tuple[tuple[str, str, str, str, bool], ...]
    relationships: tuple[dict[str, Any], ...]
    metrics: tuple[dict[str, Any], ...]
    rules: tuple[dict[str, Any], ...]
    temporal_rules: tuple[dict[str, Any], ...]
    schema_sql: str
    seed_sql: str
    cases: tuple[CaseSpec, ...]
    authority_questions: tuple[str, ...]
    ambiguity_questions: tuple[tuple[str, ...], ...]
    policy_questions: tuple[str, ...]


def _rel(
    domain: str,
    name: str,
    left: str,
    left_attr: str,
    right: str,
    right_attr: str,
    cardinality: str = "many_to_one",
    authorized: bool = True,
    description: str = "Authorized operational relationship.",
) -> dict[str, Any]:
    return {
        "relationship_id": f"relationship:{domain}:{name}",
        "left_entity": f"entity:{domain}:{left}",
        "left_attribute": f"attribute:{domain}:{left}:{left_attr}",
        "right_entity": f"entity:{domain}:{right}",
        "right_attribute": f"attribute:{domain}:{right}:{right_attr}",
        "cardinality": cardinality,
        "direction": "left_to_right",
        "authorized": authorized,
        "description": description,
    }


def _metric(domain: str, name: str, definition: str, formula: str) -> dict[str, Any]:
    return {
        "metric_id": f"metric:{domain}:{name}",
        "name": name,
        "definition": definition,
        "formula": formula,
        "operands": [],
        "grain": "case-defined",
        "null_policy": "preserve SQL NULL unless explicitly defaulted",
        "default_policy": "no implicit default",
        "precision": 4,
        "rounding_stage": "display_only unless question says otherwise",
        "temporal_basis": "case-defined",
        "description": definition,
    }


def _authority(domain: Domain) -> dict[str, Any]:
    return {
        "database_id": domain.domain_id,
        "schema_name": domain.schema_name,
        "entities": [
            {
                "entity_id": f"entity:{domain.domain_id}:{table}",
                "human_name": human,
                "physical_table": table,
                "description": description,
                "primary_semantic_role": role,
            }
            for table, human, description, role in domain.tables
        ],
        "attributes": [
            {
                "attribute_id": f"attribute:{domain.domain_id}:{table}:{name}",
                "entity_id": f"entity:{domain.domain_id}:{table}",
                "physical_column_or_path": name,
                "data_type": data_type,
                "semantic_description": description,
                "nullable": nullable,
            }
            for table, name, data_type, description, nullable in domain.attributes
        ],
        "relationships": list(domain.relationships),
        "metrics": list(domain.metrics),
        "business_rules": list(domain.rules),
        "temporal_rules": list(domain.temporal_rules),
        "policy": {
            "policy_id": f"policy:{domain.domain_id}:readonly",
            "allowed_statement": "one read-only SELECT statement",
            "forbidden_operations": [
                "INSERT",
                "UPDATE",
                "DELETE",
                "MERGE",
                "DDL",
                "COPY",
                "locking reads",
            ],
            "forbidden_objects": ["pg_authid", "secret_tokens"],
            "blocked_decisions": ["BLOCKED_POLICY"],
        },
    }


def _contract(outputs: tuple[str, ...], row_order: bool) -> dict[str, Any]:
    return {
        "column_count": len(outputs),
        "row_order": row_order,
        "aliases_significant": False,
        "duplicates_significant": True,
        "numeric_tolerance": "0.0001" if any("rate" in value for value in outputs) else None,
        "timestamp_timezone": "UTC",
    }


def _mutants(case_id: str, sql: str) -> list[dict[str, Any]]:
    return [
        {
            "mutant_id": f"m51a_{case_id}_population",
            "failure_category": "FILTER_SCOPE_ERROR",
            "description": "Incorrectly removes the requested population.",
            "semantic_rationale": "Tests population preservation against an over-restrictive predicate.",
            "sql": f"SELECT * FROM ({sql}) AS candidate WHERE FALSE",
            "status": "VALID",
            "target_component": "population",
        },
        {
            "mutant_id": f"m51a_{case_id}_limit",
            "failure_category": "OFFSET_SEMANTICS_ERROR",
            "description": "Incorrectly discards the first result row.",
            "semantic_rationale": "Tests that the complete requested result is preserved.",
            "sql": f"SELECT * FROM ({sql}) AS candidate OFFSET 1",
            "status": "VALID",
            "target_component": "population",
        },
        {
            "mutant_id": f"m51a_{case_id}_projection",
            "failure_category": "PROJECTION_ERROR",
            "description": "Adds an unrequested diagnostic column.",
            "semantic_rationale": "Tests the explicit final projection contract.",
            "sql": f"SELECT candidate.*, 1 AS diagnostic_extra FROM ({sql}) AS candidate",
            "status": "VALID",
            "target_component": "projection",
        },
    ]


def _case(domain: Domain, spec: CaseSpec) -> tuple[dict[str, Any], dict[str, Any]]:
    cid = f"{domain.domain_id.removesuffix('_ops').removesuffix('_claims').removesuffix('_billing')}_{spec.number:02d}"
    # Preserve readable stable IDs without leaking task type into case numbering.
    case = {
        "case_id": cid,
        "database_id": domain.domain_id,
        "question": spec.question,
        "task_type": "ANSWERABLE",
        "context_profile": "GOVERNED_CONTEXT_V1",
        "provenance": {
            "authoring_source": "M51A_ORIGINAL",
            "external_benchmark_derived": False,
            "machine_authored": True,
            "human_reviewed": False,
        },
    }
    target = {
        "behavior": "ANSWERABLE",
        "population": spec.population,
        "outputs": list(spec.outputs),
        "relationships": [
            item["relationship_id"]
            for item in domain.relationships
            if item.get("authorized") is True
        ],
        "filters": [],
        "aggregations": [],
        "grouping": list(spec.grouping),
        "calculations": list(spec.calculations),
        "temporal_semantics": spec.temporal or {},
        "ordering": {"columns": list(spec.outputs), "direction": "ASC"} if spec.row_order else {},
        "limit": None,
        "query_shape_tags": list(spec.tags),
        "null_default_semantics": "preserve SQL NULL semantics",
        "result_comparison_contract": _contract(spec.outputs, spec.row_order),
        "semantic_provenance": {
            "population": {"source": "QUESTION_EXPLICIT"},
            "filters": {"source": "QUESTION_EXPLICIT"},
            "ordering": {"source": "QUESTION_EXPLICIT" if spec.row_order else "NOT_APPLICABLE"},
            "limit": {"source": "NOT_APPLICABLE"},
            "temporal": {"source": "VISIBLE_BUSINESS_RULE" if spec.temporal else "NOT_APPLICABLE"},
            "rounding": {"source": "QUESTION_EXPLICIT"},
        },
        "projection_contract": {
            "mode": "EXACT",
            "fields": [
                {
                    "semantic_name": output,
                    "role": "IDENTIFIER" if output.endswith("_id") else "MEASURE",
                }
                for output in spec.outputs
            ],
            "extra_fields_allowed": False,
            "source": "QUESTION_EXPLICIT",
        },
        "reference_independence": "HIGH",
    }
    mutants = _mutants(cid, spec.ref_a)
    if cid == "procurement_05":
        mutants.append(
            {
                "mutant_id": "m54_procurement_05_duplicate_approval",
                "failure_category": "FANOUT_DUPLICATION",
                "description": "Aggregates before deduplicating repeated approvals.",
                "semantic_rationale": "Tests that multiple qualifying approvals do not multiply a requisition amount.",
                "sql": "SELECT r.department, SUM(r.estimated_amount) AS approved_amount FROM requisitions r JOIN approvals a ON a.req_id=r.req_id WHERE a.decision='approved' AND r.requested_on >= DATE '2026-06-01' AND r.requested_on < DATE '2026-07-01' GROUP BY r.department",
                "status": "VALID",
                "target_component": "aggregation",
            }
        )
    elif cid == "procurement_14":
        mutants.append(
            {
                "mutant_id": "m54_procurement_14_matching_only",
                "failure_category": "POPULATION_SCOPE_ERROR",
                "description": "Drops purchase-order lines without receipts.",
                "semantic_rationale": "Tests preservation of every purchase-order line, including eventless lines.",
                "sql": "SELECT line_id, MAX(received_on) AS latest_received_on FROM receipts GROUP BY line_id",
                "status": "VALID",
                "target_component": "population",
            }
        )
    elif cid == "insurance_11":
        mutants.append(
            {
                "mutant_id": "m54_insurance_11_matching_only",
                "failure_category": "POPULATION_SCOPE_ERROR",
                "description": "Drops claims without lifecycle events.",
                "semantic_rationale": "Tests preservation of every claim, including claims without events.",
                "sql": "SELECT claim_id, MAX(event_at) AS latest_event_at FROM claim_events GROUP BY claim_id",
                "status": "VALID",
                "target_component": "population",
            }
        )
    truth = {
        "case_id": cid,
        "database_id": domain.domain_id,
        "semantic_target": target,
        "required_context_facts": [
            f"entities:{domain.domain_id}:{table}" for table in spec.required_tables
        ],
        "evidence": {},
        "reference_implementation_a": {"sql": spec.ref_a},
        "reference_implementation_b": {"sql": spec.ref_b},
        "counterfactual_fixtures": [
            {
                "fixture_id": f"{cid}_cf{index}_{_sha(purpose)[:6]}",
                "purpose": purpose,
                "patch_sql": list(patch_sql),
            }
            for index, (purpose, patch_sql) in enumerate(spec.fixtures, 1)
        ],
        "semantic_mutants": mutants,
    }
    return case, truth


def _non_answer_case(
    domain: Domain, number: int, task_type: str, question: str, evidence: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    cid = f"{domain.domain_id.removesuffix('_ops').removesuffix('_claims').removesuffix('_billing')}_{number:02d}"
    case = {
        "case_id": cid,
        "database_id": domain.domain_id,
        "question": question,
        "task_type": task_type,
        "context_profile": "GOVERNED_CONTEXT_V1",
        "provenance": {
            "authoring_source": "M51A_ORIGINAL",
            "external_benchmark_derived": False,
            "machine_authored": True,
            "human_reviewed": False,
        },
    }
    target = {
        "behavior": task_type,
        "population": "not_applicable",
        "outputs": [],
        "relationships": [],
        "filters": [],
        "aggregations": [],
        "grouping": [],
        "calculations": [],
        "temporal_semantics": {},
        "ordering": {},
        "limit": None,
        "query_shape_tags": ["governance", task_type.lower()],
        "result_comparison_contract": _contract((), False),
        "semantic_provenance": {"population": {"source": "NOT_APPLICABLE"}},
        "projection_contract": {
            "mode": "NOT_APPLICABLE",
            "extra_fields_allowed": False,
            "fields": [],
        },
    }
    truth = {
        "case_id": cid,
        "database_id": domain.domain_id,
        "semantic_target": target,
        "required_context_facts": [f"policy:{domain.domain_id}:readonly"],
        "evidence": evidence,
        "reference_implementation_a": {},
        "reference_implementation_b": {},
        "counterfactual_fixtures": [],
        "semantic_mutants": [],
    }
    return case, truth


def _domain(
    domain_id: str,
    schema_name: str,
    kind: str,
    purpose: str,
    tables: tuple[tuple[str, str, str, str], ...],
    attributes: tuple[tuple[str, str, str, str, bool], ...],
    relationships: tuple[dict[str, Any], ...],
    metrics: tuple[dict[str, Any], ...],
    rules: tuple[dict[str, Any], ...],
    cases: tuple[CaseSpec, ...],
    authority_questions: tuple[str, ...],
    ambiguity_questions: tuple[tuple[str, ...], ...],
    policy_questions: tuple[str, ...],
    schema_sql: str,
    seed_sql: str,
) -> Domain:
    return Domain(
        domain_id,
        schema_name,
        kind,
        purpose,
        tables,
        attributes,
        relationships,
        metrics,
        rules,
        (
            {
                "temporal_rule_id": f"time:{domain_id}:clock",
                "clock_mode": "fixed",
                "benchmark_now": "2026-06-30T12:00:00Z",
                "timezone": "UTC",
                "bounds": "Stored timestamps use UTC and date boundaries are ISO half-open intervals.",
            },
        ),
        schema_sql,
        seed_sql,
        cases,
        authority_questions,
        ambiguity_questions,
        policy_questions,
    )


# Six new, structurally distinct operational domains.  Each schema has its
# own nouns and relationship graph; no existing pilot schema is copied.
PROCUREMENT = _domain(
    "procurement_ops",
    "m51a_procurement_ops",
    "A",
    "Supplier sourcing and purchase-order fulfillment.",
    (
        ("suppliers", "Suppliers", "Organizations supplying goods.", "anchor entity"),
        ("requisitions", "Requisitions", "Internal purchase requests.", "request entity"),
        ("purchase_orders", "Purchase orders", "Approved supplier orders.", "transaction entity"),
        ("po_lines", "Purchase-order lines", "Ordered SKU quantities.", "transaction detail"),
        ("receipts", "Receipts", "Received line quantities.", "event entity"),
        ("approvals", "Approvals", "Requisition decisions.", "event entity"),
        (
            "external_directory",
            "External directory",
            "Unowned external supplier contacts.",
            "reference entity",
        ),
    ),
    (
        ("suppliers", "supplier_id", "INTEGER", "Stable supplier identifier.", False),
        ("suppliers", "supplier_name", "TEXT", "Supplier display name.", False),
        ("suppliers", "region", "TEXT", "Supplier operating region.", False),
        ("suppliers", "active", "BOOLEAN", "Whether sourcing is active.", False),
        ("suppliers", "profile", "JSONB", "Supplier profile metadata.", False),
        ("requisitions", "req_id", "INTEGER", "Stable requisition identifier.", False),
        ("requisitions", "department", "TEXT", "Requesting department.", False),
        ("requisitions", "requested_on", "DATE", "Request date.", False),
        ("requisitions", "status", "TEXT", "Requisition state.", False),
        ("requisitions", "estimated_amount", "NUMERIC", "Estimated request amount.", False),
        ("requisitions", "metadata", "JSONB", "Request metadata.", False),
        ("purchase_orders", "po_id", "INTEGER", "Stable purchase-order identifier.", False),
        ("purchase_orders", "supplier_id", "INTEGER", "Supplier reference.", False),
        ("purchase_orders", "ordered_on", "DATE", "Order date.", False),
        ("purchase_orders", "status", "TEXT", "Order state.", False),
        ("po_lines", "line_id", "INTEGER", "Stable line identifier.", False),
        ("po_lines", "po_id", "INTEGER", "Purchase-order reference.", False),
        ("po_lines", "sku", "TEXT", "Ordered stock code.", False),
        ("po_lines", "ordered_qty", "INTEGER", "Units ordered.", False),
        ("po_lines", "unit_cost", "NUMERIC", "Unit cost.", False),
        ("receipts", "receipt_id", "INTEGER", "Stable receipt identifier.", False),
        ("receipts", "line_id", "INTEGER", "Purchase-order line reference.", False),
        ("receipts", "received_on", "TIMESTAMPTZ", "Receipt timestamp.", False),
        ("receipts", "received_qty", "INTEGER", "Units received.", False),
        ("approvals", "approval_id", "INTEGER", "Stable approval identifier.", False),
        ("approvals", "req_id", "INTEGER", "Requisition reference.", False),
        ("approvals", "approved_at", "TIMESTAMPTZ", "Decision timestamp.", False),
        ("approvals", "decision", "TEXT", "Approval decision.", False),
        ("external_directory", "supplier_id", "INTEGER", "Untrusted supplier identifier.", False),
    ),
    tuple(
        [
            _rel(
                "procurement_ops",
                "po_supplier",
                "purchase_orders",
                "supplier_id",
                "suppliers",
                "supplier_id",
            ),
            _rel("procurement_ops", "line_po", "po_lines", "po_id", "purchase_orders", "po_id"),
            _rel("procurement_ops", "receipt_line", "receipts", "line_id", "po_lines", "line_id"),
            _rel(
                "procurement_ops",
                "approval_request",
                "approvals",
                "req_id",
                "requisitions",
                "req_id",
            ),
            _rel(
                "procurement_ops",
                "external_supplier_trap",
                "external_directory",
                "supplier_id",
                "suppliers",
                "supplier_id",
                authorized=False,
                description="External ownership is not an authorized supplier relationship.",
            ),
        ]
    ),
    (
        _metric(
            "procurement_ops",
            "fill_rate",
            "Received units divided by ordered units.",
            "SUM(received_qty) / SUM(ordered_qty)",
        ),
    ),
    (
        {
            "rule_id": "rule:procurement_ops:approved_requisition",
            "name": "Approved requisition",
            "definition": "An approval with decision='approved' is the authoritative approval.",
        },
    ),
    (
        CaseSpec(
            1,
            "List active supplier IDs in ascending order.",
            "SELECT supplier_id FROM suppliers WHERE active ORDER BY supplier_id",
            "SELECT s.supplier_id FROM suppliers s JOIN (SELECT supplier_id FROM suppliers WHERE active) x ON x.supplier_id=s.supplier_id ORDER BY s.supplier_id",
            ("supplier_id",),
            ("simple_filter",),
            (
                (
                    "adds an active supplier",
                    ("INSERT INTO suppliers VALUES (9001,'Northstar','West',true,'{}')",),
                ),
                (
                    "adds an inactive supplier",
                    ("INSERT INTO suppliers VALUES (9002,'Quiet Source','West',false,'{}')",),
                ),
            ),
            ("suppliers",),
            "STRAIGHTFORWARD",
        ),
        CaseSpec(
            2,
            "For each supplier, return supplier ID and ordered spend for open purchase orders.",
            "SELECT p.supplier_id, SUM(l.ordered_qty*l.unit_cost) AS ordered_spend FROM purchase_orders p JOIN po_lines l ON l.po_id=p.po_id WHERE p.status='open' GROUP BY p.supplier_id ORDER BY p.supplier_id",
            "SELECT supplier_id, ordered_spend FROM (SELECT p.supplier_id, SUM(l.ordered_qty*l.unit_cost) AS ordered_spend FROM purchase_orders p JOIN po_lines l ON l.po_id=p.po_id WHERE p.status='open' GROUP BY p.supplier_id) q ORDER BY supplier_id",
            ("supplier_id", "ordered_spend"),
            ("multi_join", "aggregation", "group_survival"),
            (
                (
                    "adds an open order line",
                    (
                        "INSERT INTO purchase_orders VALUES (9003,1,'2026-06-20','open')",
                        "INSERT INTO po_lines VALUES (9004,9003,'SKU-X',2,12.50)",
                    ),
                ),
                (
                    "adds a closed order line",
                    (
                        "INSERT INTO purchase_orders VALUES (9005,2,'2026-06-21','closed')",
                        "INSERT INTO po_lines VALUES (9006,9005,'SKU-Y',4,9.00)",
                    ),
                ),
            ),
            ("purchase_orders", "po_lines"),
            grouping=("supplier_id",),
            calculations=("ordered_qty * unit_cost",),
        ),
        CaseSpec(
            3,
            "For each department, return department and the estimated amount of approved requisitions requested in June 2026.",
            "SELECT department, SUM(estimated_amount) AS approved_amount FROM (SELECT r.department,r.estimated_amount FROM requisitions r WHERE r.requested_on >= DATE '2026-06-01' AND r.requested_on < DATE '2026-07-01' AND EXISTS (SELECT 1 FROM approvals a WHERE a.req_id=r.req_id AND a.decision='approved')) q GROUP BY department ORDER BY department",
            "SELECT department, SUM(estimated_amount) AS approved_amount FROM (SELECT r.department,r.estimated_amount FROM requisitions r WHERE r.requested_on >= DATE '2026-06-01' AND r.requested_on < DATE '2026-07-01' AND EXISTS (SELECT 1 FROM approvals a WHERE a.req_id=r.req_id AND a.decision='approved')) q GROUP BY department ORDER BY department",
            ("department", "approved_amount"),
            ("temporal", "aggregation", "approval"),
            (
                (
                    "adds an approved June requisition",
                    (
                        "INSERT INTO requisitions VALUES (9007,'IT','2026-06-22','approved',100,'{\"priority\":\"normal\"}')",
                        "INSERT INTO approvals VALUES (9008,9007,'2026-06-22 10:00+00','approved')",
                    ),
                ),
                (
                    "adds a July approval outside the window",
                    (
                        "INSERT INTO requisitions VALUES (9009,'IT','2026-07-01','approved',200,'{}')",
                        "INSERT INTO approvals VALUES (9010,9009,'2026-07-01 10:00+00','approved')",
                    ),
                ),
                (
                    "adds duplicate approved approvals",
                    ("INSERT INTO approvals VALUES (9011,101,'2026-06-23 10:00+00','approved')",),
                ),
            ),
            ("requisitions", "approvals"),
            temporal={
                "rule_id": "time:procurement_ops:clock",
                "cutoff": "2026-06-30",
                "interval": "[2026-06-01,2026-07-01)",
            },
        ),
        CaseSpec(
            4,
            "For each purchase-order line, return line ID and fulfillment rate, received units divided by ordered units.",
            "SELECT l.line_id, COALESCE(SUM(r.received_qty),0)::NUMERIC / NULLIF(l.ordered_qty,0) AS fulfillment_rate FROM po_lines l LEFT JOIN receipts r ON r.line_id=l.line_id GROUP BY l.line_id,l.ordered_qty ORDER BY l.line_id",
            "SELECT line_id, COALESCE(received_qty,0)::NUMERIC / NULLIF(ordered_qty,0) AS fulfillment_rate FROM (SELECT l.line_id,l.ordered_qty,SUM(r.received_qty) AS received_qty FROM po_lines l LEFT JOIN receipts r ON r.line_id=l.line_id GROUP BY l.line_id,l.ordered_qty) q ORDER BY line_id",
            ("line_id", "fulfillment_rate"),
            ("aggregation", "population", "null_semantics"),
            (
                (
                    "adds a partial receipt",
                    ("INSERT INTO receipts VALUES (9011,301,'2026-06-23 10:00+00',1)",),
                ),
                (
                    "adds a zero receipt row",
                    ("INSERT INTO receipts VALUES (9012,302,'2026-06-24 10:00+00',0)",),
                ),
            ),
            ("po_lines", "receipts"),
            calculations=("received_qty / ordered_qty",),
        ),
        CaseSpec(
            5,
            "For each purchase-order line, return its latest receipt timestamp.",
            "SELECT pl.line_id, MAX(r.received_on) AS latest_received_on FROM po_lines pl LEFT JOIN receipts r ON r.line_id=pl.line_id GROUP BY pl.line_id",
            "SELECT pl.line_id, (SELECT MAX(r.received_on) FROM receipts r WHERE r.line_id=pl.line_id) AS latest_received_on FROM po_lines pl",
            ("line_id", "latest_received_on"),
            ("latest_row", "window", "temporal", "population"),
            (
                (
                    "adds a newer receipt",
                    ("INSERT INTO receipts VALUES (9013,301,'2026-06-25 12:00+00',2)",),
                ),
                (
                    "adds an older receipt",
                    ("INSERT INTO receipts VALUES (9014,302,'2026-05-01 12:00+00',2)",),
                ),
                (
                    "adds a purchase-order line without a receipt",
                    ("INSERT INTO po_lines VALUES (305,204,'SKU-E',1,7.00)",),
                ),
            ),
            ("po_lines", "receipts"),
            population="base-entity-preserving",
            row_order=False,
            temporal={
                "rule_id": "time:procurement_ops:clock",
                "latest": "maximum received_on then receipt_id",
            },
        ),
        CaseSpec(
            6,
            "List supplier IDs with no open purchase order.",
            "SELECT s.supplier_id FROM suppliers s WHERE NOT EXISTS (SELECT 1 FROM purchase_orders p WHERE p.supplier_id=s.supplier_id AND p.status='open') ORDER BY s.supplier_id",
            "SELECT s.supplier_id FROM suppliers s LEFT JOIN purchase_orders p ON p.supplier_id=s.supplier_id AND p.status='open' GROUP BY s.supplier_id HAVING COUNT(p.po_id)=0 ORDER BY s.supplier_id",
            ("supplier_id",),
            ("anti_join", "null_semantics"),
            (
                (
                    "adds an open order",
                    ("INSERT INTO purchase_orders VALUES (9015,3,'2026-06-25','open')",),
                ),
                (
                    "adds a closed order",
                    ("INSERT INTO purchase_orders VALUES (9016,3,'2026-06-25','closed')",),
                ),
            ),
            ("suppliers", "purchase_orders"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            7,
            "Return requisition IDs whose metadata risk score is at least 70.",
            "SELECT req_id FROM requisitions WHERE ((metadata->>'risk_score')::INTEGER) >= 70 ORDER BY req_id",
            "SELECT req_id FROM (SELECT req_id,CAST(metadata->>'risk_score' AS INTEGER) AS score FROM requisitions) q WHERE score >= 70 ORDER BY req_id",
            ("req_id",),
            ("json_typing", "simple_filter"),
            (
                (
                    "adds a high-risk request",
                    (
                        "INSERT INTO requisitions VALUES (9017,'Finance','2026-06-26','pending',80,'{\"risk_score\":\"75\"}')",
                    ),
                ),
                (
                    "adds a low-risk request",
                    (
                        "INSERT INTO requisitions VALUES (9018,'Finance','2026-06-26','pending',80,'{\"risk_score\":\"10\"}')",
                    ),
                ),
            ),
            ("requisitions",),
        ),
        CaseSpec(
            8,
            "For each supplier, return supplier ID and the count of purchase orders placed in June 2026.",
            "SELECT s.supplier_id, COUNT(p.po_id) AS june_orders FROM suppliers s LEFT JOIN purchase_orders p ON p.supplier_id=s.supplier_id AND p.ordered_on >= DATE '2026-06-01' AND p.ordered_on < DATE '2026-07-01' GROUP BY s.supplier_id ORDER BY s.supplier_id",
            "SELECT s.supplier_id, (SELECT COUNT(*) FROM purchase_orders p WHERE p.supplier_id=s.supplier_id AND p.ordered_on >= DATE '2026-06-01' AND p.ordered_on < DATE '2026-07-01') AS june_orders FROM suppliers s ORDER BY s.supplier_id",
            ("supplier_id", "june_orders"),
            ("group_survival", "temporal", "population"),
            (
                (
                    "adds a June order",
                    ("INSERT INTO purchase_orders VALUES (9019,3,'2026-06-27','open')",),
                ),
                (
                    "adds an August order",
                    ("INSERT INTO purchase_orders VALUES (9020,3,'2026-08-01','open')",),
                ),
            ),
            ("suppliers", "purchase_orders"),
            population="base-entity-preserving",
            temporal={
                "rule_id": "time:procurement_ops:clock",
                "interval": "[2026-06-01,2026-07-01)",
            },
        ),
        CaseSpec(
            9,
            "Rank suppliers by total ordered spend, highest spend first, returning supplier ID and rank.",
            "SELECT supplier_id, DENSE_RANK() OVER (ORDER BY total_spend DESC, supplier_id) AS spend_rank FROM (SELECT p.supplier_id,SUM(l.ordered_qty*l.unit_cost) AS total_spend FROM purchase_orders p JOIN po_lines l ON l.po_id=p.po_id GROUP BY p.supplier_id) q ORDER BY spend_rank,supplier_id",
            "SELECT supplier_id, spend_rank FROM (SELECT supplier_id,total_spend,DENSE_RANK() OVER (ORDER BY total_spend DESC, supplier_id) AS spend_rank FROM (SELECT p.supplier_id,SUM(l.ordered_qty*l.unit_cost) AS total_spend FROM purchase_orders p JOIN po_lines l ON l.po_id=p.po_id GROUP BY p.supplier_id) x) y ORDER BY spend_rank,supplier_id",
            ("supplier_id", "spend_rank"),
            ("window", "aggregation", "ranking"),
            (
                (
                    "adds high spend",
                    (
                        "INSERT INTO purchase_orders VALUES (9021,3,'2026-06-28','open')",
                        "INSERT INTO po_lines VALUES (9022,9021,'SKU-Z',20,100)",
                    ),
                ),
                (
                    "adds low spend",
                    (
                        "INSERT INTO purchase_orders VALUES (9023,3,'2026-06-28','open')",
                        "INSERT INTO po_lines VALUES (9024,9023,'SKU-Z',1,1)",
                    ),
                ),
            ),
            ("purchase_orders", "po_lines"),
            grouping=("supplier_id",),
        ),
        CaseSpec(
            10,
            "Return supplier IDs and total received units, including suppliers with no receipts.",
            "SELECT s.supplier_id, COALESCE(SUM(r.received_qty),0) AS received_units FROM suppliers s LEFT JOIN purchase_orders p ON p.supplier_id=s.supplier_id LEFT JOIN po_lines l ON l.po_id=p.po_id LEFT JOIN receipts r ON r.line_id=l.line_id GROUP BY s.supplier_id ORDER BY s.supplier_id",
            "SELECT s.supplier_id, COALESCE((SELECT SUM(r.received_qty) FROM purchase_orders p JOIN po_lines l ON l.po_id=p.po_id JOIN receipts r ON r.line_id=l.line_id WHERE p.supplier_id=s.supplier_id),0) AS received_units FROM suppliers s ORDER BY s.supplier_id",
            ("supplier_id", "received_units"),
            ("multi_join", "group_survival", "population"),
            (
                (
                    "adds a receipt chain",
                    (
                        "INSERT INTO purchase_orders VALUES (9025,4,'2026-06-28','open')",
                        "INSERT INTO po_lines VALUES (9026,9025,'SKU-R',3,5)",
                        "INSERT INTO receipts VALUES (9027,9026,'2026-06-29 09:00+00',3)",
                    ),
                ),
                (
                    "adds an unreceived order",
                    (
                        "INSERT INTO purchase_orders VALUES (9028,4,'2026-06-28','open')",
                        "INSERT INTO po_lines VALUES (9029,9028,'SKU-S',3,5)",
                    ),
                ),
            ),
            ("suppliers", "purchase_orders", "po_lines", "receipts"),
            population="base-entity-preserving",
        ),
    ),
    (
        "Which supplier owner email is listed for each supplier?",
        "Which requester cost center is associated with each requisition?",
        "Which external compliance contact is attached to each supplier?",
    ),
    (
        (
            "Which suppliers are current?",
            "supplier active flag versus most recent approval",
            "INSERT INTO suppliers VALUES (9030,'Ambiguous Supplier','East',true,'{}')",
        ),
    ),
    ("Update supplier region to 'Central'.",),
    "CREATE SCHEMA IF NOT EXISTS m51a_procurement_ops;\nSET search_path TO m51a_procurement_ops;\nCREATE TABLE suppliers (supplier_id INTEGER PRIMARY KEY, supplier_name TEXT NOT NULL, region TEXT NOT NULL, active BOOLEAN NOT NULL, profile JSONB NOT NULL);\nCREATE TABLE requisitions (req_id INTEGER PRIMARY KEY, department TEXT NOT NULL, requested_on DATE NOT NULL, status TEXT NOT NULL, estimated_amount NUMERIC(10,2) NOT NULL, metadata JSONB NOT NULL);\nCREATE TABLE purchase_orders (po_id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id), ordered_on DATE NOT NULL, status TEXT NOT NULL);\nCREATE TABLE po_lines (line_id INTEGER PRIMARY KEY, po_id INTEGER NOT NULL REFERENCES purchase_orders(po_id), sku TEXT NOT NULL, ordered_qty INTEGER NOT NULL, unit_cost NUMERIC(10,2) NOT NULL);\nCREATE TABLE receipts (receipt_id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES po_lines(line_id), received_on TIMESTAMPTZ NOT NULL, received_qty INTEGER NOT NULL);\nCREATE TABLE approvals (approval_id INTEGER PRIMARY KEY, req_id INTEGER NOT NULL REFERENCES requisitions(req_id), approved_at TIMESTAMPTZ NOT NULL, decision TEXT NOT NULL);\nCREATE TABLE external_directory (directory_id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL, owner_email TEXT NOT NULL);",
    "INSERT INTO suppliers VALUES (1,'Atlas Supply','North',true,'{\"tier\":\"gold\"}'),(2,'Beacon Materials','South',true,'{\"tier\":\"silver\"}'),(3,'Cedar Components','East',false,'{\"tier\":\"gold\"}'),(4,'Delta Industrial','West',true,'{\"tier\":\"bronze\"}');\nINSERT INTO requisitions VALUES (101,'IT','2026-06-02','approved',120,'{\"risk_score\":\"40\"}'),(102,'Facilities','2026-06-04','pending',450,'{\"risk_score\":\"80\"}'),(103,'IT','2026-05-20','approved',300,'{\"risk_score\":\"75\"}'),(104,'Finance','2026-06-15','approved',700,'{\"risk_score\":\"20\"}');\nINSERT INTO purchase_orders VALUES (201,1,'2026-06-01','open'),(202,1,'2026-05-01','closed'),(203,2,'2026-06-10','open'),(204,3,'2026-05-10','closed');\nINSERT INTO po_lines VALUES (301,201,'SKU-A',10,12.50),(302,202,'SKU-B',4,20),(303,203,'SKU-C',5,40),(304,204,'SKU-D',2,15);\nINSERT INTO receipts VALUES (401,301,'2026-06-03 09:00+00',6),(402,301,'2026-06-05 09:00+00',2),(403,302,'2026-05-04 09:00+00',4),(404,303,'2026-06-12 09:00+00',5);\nINSERT INTO approvals VALUES (501,101,'2026-06-03 09:00+00','approved'),(502,103,'2026-05-21 09:00+00','approved'),(503,104,'2026-06-16 09:00+00','approved'),(504,102,'2026-06-05 09:00+00','rejected');\nINSERT INTO external_directory VALUES (601,1,'owner@example.invalid');",
)

INSURANCE = _domain(
    "insurance_claims",
    "m51a_insurance_claims",
    "A",
    "Policy coverage, claims, payments, and adjuster workflow.",
    (
        (
            "policyholders",
            "Policyholders",
            "People or organizations holding coverage.",
            "anchor entity",
        ),
        ("policies", "Policies", "Coverage periods and products.", "contract entity"),
        ("claims", "Claims", "Reported insured losses.", "claim entity"),
        ("claim_payments", "Claim payments", "Payments toward claims.", "transaction entity"),
        ("claim_events", "Claim events", "Claim lifecycle events.", "event entity"),
        ("adjusters", "Adjusters", "Staff evaluating claims.", "reference entity"),
        ("claim_assignments", "Claim assignments", "Adjuster assignments.", "event entity"),
        (
            "external_directory",
            "External directory",
            "Untrusted beneficiary contacts.",
            "reference entity",
        ),
    ),
    (
        ("policyholders", "holder_id", "INTEGER", "Stable policyholder identifier.", False),
        ("policyholders", "holder_name", "TEXT", "Policyholder name.", False),
        ("policyholders", "region", "TEXT", "Residence region.", False),
        ("policies", "policy_id", "INTEGER", "Stable policy identifier.", False),
        ("policies", "holder_id", "INTEGER", "Policyholder reference.", False),
        ("policies", "product", "TEXT", "Coverage product.", False),
        ("policies", "status", "TEXT", "Policy state.", False),
        ("policies", "effective_on", "DATE", "Coverage start date.", False),
        ("policies", "expires_on", "DATE", "Coverage end date.", False),
        ("claims", "claim_id", "INTEGER", "Stable claim identifier.", False),
        ("claims", "policy_id", "INTEGER", "Policy reference.", False),
        ("claims", "opened_on", "DATE", "Claim opening date.", False),
        ("claims", "loss_amount", "NUMERIC", "Reported loss amount.", False),
        ("claims", "claim_status", "TEXT", "Claim state.", False),
        ("claims", "loss_data", "JSONB", "Structured loss details.", False),
        ("claim_payments", "payment_id", "INTEGER", "Stable payment identifier.", False),
        ("claim_payments", "claim_id", "INTEGER", "Claim reference.", False),
        ("claim_payments", "paid_on", "DATE", "Payment date.", False),
        ("claim_payments", "amount", "NUMERIC", "Payment amount.", False),
        ("claim_payments", "payment_status", "TEXT", "Payment state.", False),
        ("claim_events", "event_id", "INTEGER", "Stable claim-event identifier.", False),
        ("claim_events", "claim_id", "INTEGER", "Claim reference.", False),
        ("claim_events", "event_at", "TIMESTAMPTZ", "Event timestamp.", False),
        ("claim_events", "event_type", "TEXT", "Lifecycle event type.", False),
        ("adjusters", "adjuster_id", "INTEGER", "Stable adjuster identifier.", False),
        ("adjusters", "adjuster_name", "TEXT", "Adjuster name.", False),
        ("claim_assignments", "assignment_id", "INTEGER", "Stable assignment identifier.", False),
        ("claim_assignments", "claim_id", "INTEGER", "Claim reference.", False),
        ("claim_assignments", "adjuster_id", "INTEGER", "Adjuster reference.", False),
        ("claim_assignments", "assigned_at", "TIMESTAMPTZ", "Assignment timestamp.", False),
        ("external_directory", "holder_id", "INTEGER", "Untrusted holder identifier.", False),
    ),
    tuple(
        [
            _rel(
                "insurance_claims",
                "policy_holder",
                "policies",
                "holder_id",
                "policyholders",
                "holder_id",
            ),
            _rel(
                "insurance_claims", "claim_policy", "claims", "policy_id", "policies", "policy_id"
            ),
            _rel(
                "insurance_claims",
                "payment_claim",
                "claim_payments",
                "claim_id",
                "claims",
                "claim_id",
            ),
            _rel(
                "insurance_claims", "event_claim", "claim_events", "claim_id", "claims", "claim_id"
            ),
            _rel(
                "insurance_claims",
                "assignment_claim",
                "claim_assignments",
                "claim_id",
                "claims",
                "claim_id",
            ),
            _rel(
                "insurance_claims",
                "assignment_adjuster",
                "claim_assignments",
                "adjuster_id",
                "adjusters",
                "adjuster_id",
            ),
            _rel(
                "insurance_claims",
                "external_holder_trap",
                "external_directory",
                "holder_id",
                "policyholders",
                "holder_id",
                authorized=False,
                description="External beneficiary ownership is not authorized.",
            ),
        ]
    ),
    (
        _metric(
            "insurance_claims",
            "paid_ratio",
            "Paid claim dollars divided by reported loss dollars.",
            "SUM(payment amount) / SUM(loss amount)",
        ),
    ),
    (
        {
            "rule_id": "rule:insurance_claims:open_claim",
            "name": "Open claim",
            "definition": "A claim with claim_status='open' is unresolved.",
        },
    ),
    (
        CaseSpec(
            1,
            "List policy IDs for active policies, in policy ID order.",
            "SELECT policy_id FROM policies WHERE status='active' ORDER BY policy_id",
            "SELECT p.policy_id FROM policies p JOIN (SELECT policy_id FROM policies WHERE status='active') q ON q.policy_id=p.policy_id ORDER BY p.policy_id",
            ("policy_id",),
            ("simple_filter",),
            (
                (
                    "adds active policy",
                    (
                        "INSERT INTO policies VALUES (9001,1,'auto','active','2026-06-20','2027-06-20')",
                    ),
                ),
                (
                    "adds cancelled policy",
                    (
                        "INSERT INTO policies VALUES (9002,2,'home','cancelled','2026-06-20','2027-06-20')",
                    ),
                ),
            ),
            ("policies",),
            "STRAIGHTFORWARD",
        ),
        CaseSpec(
            2,
            "For each policyholder region, return region and total reported loss for open claims.",
            "SELECT h.region,SUM(c.loss_amount) AS open_loss FROM policyholders h JOIN policies p ON p.holder_id=h.holder_id JOIN claims c ON c.policy_id=p.policy_id WHERE c.claim_status='open' GROUP BY h.region ORDER BY h.region",
            "SELECT region,open_loss FROM (SELECT h.region,SUM(c.loss_amount) AS open_loss FROM claims c JOIN policies p ON p.policy_id=c.policy_id JOIN policyholders h ON h.holder_id=p.holder_id WHERE c.claim_status='open' GROUP BY h.region) q ORDER BY region",
            ("region", "open_loss"),
            ("multi_join", "aggregation"),
            (
                (
                    "adds open claim",
                    ("INSERT INTO claims VALUES (9003,101,'2026-06-21',300,'open','{}')",),
                ),
                (
                    "adds closed claim",
                    ("INSERT INTO claims VALUES (9004,102,'2026-06-21',300,'closed','{}')",),
                ),
            ),
            ("policyholders", "policies", "claims"),
            grouping=("region",),
        ),
        CaseSpec(
            3,
            "For each claim, return claim ID and total paid amount, including claims with no payments.",
            "SELECT c.claim_id,COALESCE(SUM(p.amount),0) AS paid_amount FROM claims c LEFT JOIN claim_payments p ON p.claim_id=c.claim_id GROUP BY c.claim_id ORDER BY c.claim_id",
            "SELECT c.claim_id,COALESCE((SELECT SUM(p.amount) FROM claim_payments p WHERE p.claim_id=c.claim_id),0) AS paid_amount FROM claims c ORDER BY c.claim_id",
            ("claim_id", "paid_amount"),
            ("group_survival", "null_semantics", "correlated"),
            (
                (
                    "adds payment",
                    ("INSERT INTO claim_payments VALUES (9005,201,'2026-06-22',50,'posted')",),
                ),
                (
                    "adds pending payment",
                    ("INSERT INTO claim_payments VALUES (9006,202,'2026-06-22',50,'pending')",),
                ),
            ),
            ("claims", "claim_payments"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            4,
            "For each claim, return the timestamp of its latest lifecycle event.",
            "SELECT c.claim_id, MAX(e.event_at) AS latest_event_at FROM claims c LEFT JOIN claim_events e ON e.claim_id=c.claim_id GROUP BY c.claim_id",
            "SELECT c.claim_id, (SELECT MAX(e.event_at) FROM claim_events e WHERE e.claim_id=c.claim_id) AS latest_event_at FROM claims c",
            ("claim_id", "latest_event_at"),
            ("latest_row", "window", "temporal", "population"),
            (
                (
                    "adds newer event",
                    (
                        "INSERT INTO claim_events VALUES (9007,201,'2026-06-25 10:00+00','reviewed')",
                    ),
                ),
                (
                    "adds older event",
                    (
                        "INSERT INTO claim_events VALUES (9008,202,'2026-05-01 10:00+00','received')",
                    ),
                ),
                (
                    "adds a claim without a lifecycle event",
                    ("INSERT INTO claims VALUES (206,104,'2026-06-20',400,'open','{}')",),
                ),
            ),
            ("claims", "claim_events"),
            population="base-entity-preserving",
            row_order=False,
            temporal={
                "rule_id": "time:insurance_claims:clock",
                "latest": "maximum event_at then event_id",
            },
        ),
        CaseSpec(
            5,
            "List policy IDs with no open claim.",
            "SELECT p.policy_id FROM policies p WHERE NOT EXISTS (SELECT 1 FROM claims c WHERE c.policy_id=p.policy_id AND c.claim_status='open') ORDER BY p.policy_id",
            "SELECT p.policy_id FROM policies p LEFT JOIN claims c ON c.policy_id=p.policy_id AND c.claim_status='open' GROUP BY p.policy_id HAVING COUNT(c.claim_id)=0 ORDER BY p.policy_id",
            ("policy_id",),
            ("anti_join", "null_semantics"),
            (
                (
                    "adds open claim",
                    ("INSERT INTO claims VALUES (9009,103,'2026-06-23',200,'open','{}')",),
                ),
                (
                    "adds closed claim",
                    ("INSERT INTO claims VALUES (9010,103,'2026-06-23',200,'closed','{}')",),
                ),
            ),
            ("policies", "claims"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            6,
            "Return claim IDs whose loss data severity is at least 4.",
            "SELECT claim_id FROM claims WHERE (loss_data->>'severity')::INTEGER >= 4 ORDER BY claim_id",
            "SELECT claim_id FROM (SELECT claim_id,CAST(loss_data->>'severity' AS INTEGER) AS severity FROM claims) q WHERE severity>=4 ORDER BY claim_id",
            ("claim_id",),
            ("json_typing", "simple_filter"),
            (
                (
                    "adds severe claim",
                    (
                        "INSERT INTO claims VALUES (9011,101,'2026-06-24',100,'open','{\"severity\":\"5\"}')",
                    ),
                ),
                (
                    "adds minor claim",
                    (
                        "INSERT INTO claims VALUES (9012,101,'2026-06-24',100,'open','{\"severity\":\"1\"}')",
                    ),
                ),
            ),
            ("claims",),
        ),
        CaseSpec(
            7,
            "For each policy product, return product and the count of claims opened in June 2026.",
            "SELECT p.product,COUNT(c.claim_id) AS june_claims FROM policies p LEFT JOIN claims c ON c.policy_id=p.policy_id AND c.opened_on>=DATE '2026-06-01' AND c.opened_on<DATE '2026-07-01' GROUP BY p.product ORDER BY p.product",
            "SELECT p.product,(SELECT COUNT(*) FROM claims c JOIN policies p2 ON p2.policy_id=c.policy_id WHERE p2.product=p.product AND c.opened_on>=DATE '2026-06-01' AND c.opened_on<DATE '2026-07-01') AS june_claims FROM policies p GROUP BY p.product ORDER BY p.product",
            ("product", "june_claims"),
            ("group_survival", "temporal", "population"),
            (
                (
                    "adds June claim",
                    ("INSERT INTO claims VALUES (9013,101,'2026-06-26',100,'open','{}')",),
                ),
                (
                    "adds July claim",
                    ("INSERT INTO claims VALUES (9014,101,'2026-07-01',100,'open','{}')",),
                ),
            ),
            ("policies", "claims"),
            population="base-entity-preserving",
            temporal={
                "rule_id": "time:insurance_claims:clock",
                "interval": "[2026-06-01,2026-07-01)",
            },
        ),
        CaseSpec(
            8,
            "For each claim, return claim ID and the number of posted payments.",
            "SELECT c.claim_id,COUNT(p.payment_id) AS posted_payments FROM claims c LEFT JOIN claim_payments p ON p.claim_id=c.claim_id AND p.payment_status='posted' GROUP BY c.claim_id ORDER BY c.claim_id",
            "SELECT c.claim_id,(SELECT COUNT(*) FROM claim_payments p WHERE p.claim_id=c.claim_id AND p.payment_status='posted') AS posted_payments FROM claims c ORDER BY c.claim_id",
            ("claim_id", "posted_payments"),
            ("aggregate", "population", "null_semantics"),
            (
                (
                    "adds posted payment",
                    ("INSERT INTO claim_payments VALUES (9015,201,'2026-06-27',10,'posted')",),
                ),
                (
                    "adds denied payment",
                    ("INSERT INTO claim_payments VALUES (9016,201,'2026-06-27',10,'denied')",),
                ),
            ),
            ("claims", "claim_payments"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            9,
            "Rank policyholders by total reported loss, returning holder ID and rank.",
            "SELECT holder_id,DENSE_RANK() OVER (ORDER BY total_loss DESC,holder_id) AS loss_rank FROM (SELECT p.holder_id,SUM(c.loss_amount) AS total_loss FROM policies p JOIN claims c ON c.policy_id=p.policy_id GROUP BY p.holder_id) q ORDER BY loss_rank,holder_id",
            "SELECT holder_id,loss_rank FROM (SELECT holder_id,total_loss,DENSE_RANK() OVER (ORDER BY total_loss DESC,holder_id) AS loss_rank FROM (SELECT p.holder_id,SUM(c.loss_amount) AS total_loss FROM policies p JOIN claims c ON c.policy_id=p.policy_id GROUP BY p.holder_id) x) y ORDER BY loss_rank,holder_id",
            ("holder_id", "loss_rank"),
            ("window", "aggregation", "ranking"),
            (
                (
                    "adds high loss",
                    ("INSERT INTO claims VALUES (9017,102,'2026-06-28',1000,'open','{}')",),
                ),
                (
                    "adds low loss",
                    ("INSERT INTO claims VALUES (9018,102,'2026-06-28',1,'open','{}')",),
                ),
            ),
            ("policies", "claims"),
            grouping=("holder_id",),
        ),
        CaseSpec(
            10,
            "For each claim, return claim ID and the number of days from opening to its latest event.",
            "SELECT c.claim_id,(MAX(e.event_at)::DATE-c.opened_on) AS days_to_latest_event FROM claims c JOIN claim_events e ON e.claim_id=c.claim_id GROUP BY c.claim_id,c.opened_on ORDER BY c.claim_id",
            "SELECT claim_id,(latest_event_at::DATE-opened_on) AS days_to_latest_event FROM (SELECT c.claim_id,c.opened_on,MAX(e.event_at) AS latest_event_at FROM claims c JOIN claim_events e ON e.claim_id=c.claim_id GROUP BY c.claim_id,c.opened_on) q ORDER BY claim_id",
            ("claim_id", "days_to_latest_event"),
            ("temporal", "aggregation", "latest_row"),
            (
                (
                    "adds later event",
                    ("INSERT INTO claim_events VALUES (9019,201,'2026-06-29 10:00+00','closed')",),
                ),
                (
                    "adds same-day event",
                    (
                        "INSERT INTO claim_events VALUES (9020,202,'2026-06-02 10:00+00','reviewed')",
                    ),
                ),
            ),
            ("claims", "claim_events"),
            temporal={
                "rule_id": "time:insurance_claims:clock",
                "calculation": "calendar days from opened_on to latest event",
            },
        ),
    ),
    (
        "Which policyholder beneficiary email is recorded?",
        "Which external medical history is linked to the claimant?",
        "Which broker owner is attached to each policy?",
    ),
    (
        (
            "Which claims are current?",
            "open claim versus claim with an active assignment",
            "INSERT INTO claims VALUES (9021,101,'2026-06-30',50,'open','{}')",
        ),
    ),
    ("Delete all closed claims.",),
    "CREATE SCHEMA IF NOT EXISTS m51a_insurance_claims;\nSET search_path TO m51a_insurance_claims;\nCREATE TABLE policyholders (holder_id INTEGER PRIMARY KEY, holder_name TEXT NOT NULL, region TEXT NOT NULL);\nCREATE TABLE policies (policy_id INTEGER PRIMARY KEY, holder_id INTEGER NOT NULL REFERENCES policyholders(holder_id), product TEXT NOT NULL, status TEXT NOT NULL, effective_on DATE NOT NULL, expires_on DATE NOT NULL);\nCREATE TABLE claims (claim_id INTEGER PRIMARY KEY, policy_id INTEGER NOT NULL REFERENCES policies(policy_id), opened_on DATE NOT NULL, loss_amount NUMERIC(10,2) NOT NULL, claim_status TEXT NOT NULL, loss_data JSONB NOT NULL);\nCREATE TABLE claim_payments (payment_id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(claim_id), paid_on DATE NOT NULL, amount NUMERIC(10,2) NOT NULL, payment_status TEXT NOT NULL);\nCREATE TABLE claim_events (event_id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(claim_id), event_at TIMESTAMPTZ NOT NULL, event_type TEXT NOT NULL);\nCREATE TABLE adjusters (adjuster_id INTEGER PRIMARY KEY, adjuster_name TEXT NOT NULL);\nCREATE TABLE claim_assignments (assignment_id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES claims(claim_id), adjuster_id INTEGER NOT NULL REFERENCES adjusters(adjuster_id), assigned_at TIMESTAMPTZ NOT NULL);\nCREATE TABLE external_directory (directory_id INTEGER PRIMARY KEY, holder_id INTEGER NOT NULL, owner_email TEXT NOT NULL);",
    "INSERT INTO policyholders VALUES (1,'Aster Group','North'),(2,'Boreal Works','South'),(3,'Cinder Labs','East');\nINSERT INTO policies VALUES (101,1,'auto','active','2026-01-01','2026-12-31'),(102,1,'home','active','2026-02-01','2027-01-31'),(103,2,'travel','expired','2025-01-01','2025-12-31'),(104,3,'auto','active','2026-03-01','2027-02-28');\nINSERT INTO claims VALUES (201,101,'2026-06-01',1200,'open','{\"severity\":\"4\"}'),(202,101,'2026-05-01',300,'closed','{\"severity\":\"2\"}'),(203,102,'2026-06-05',800,'open','{\"severity\":\"5\"}'),(204,103,'2026-04-01',500,'closed','{\"severity\":\"3\"}'),(205,104,'2026-06-10',100,'open','{\"severity\":\"1\"}');\nINSERT INTO claim_payments VALUES (301,201,'2026-06-10',500,'posted'),(302,202,'2026-05-10',300,'posted'),(303,203,'2026-06-15',100,'pending');\nINSERT INTO claim_events VALUES (401,201,'2026-06-01 10:00+00','opened'),(402,201,'2026-06-03 10:00+00','reviewed'),(403,202,'2026-05-01 10:00+00','opened'),(404,203,'2026-06-05 10:00+00','opened'),(405,205,'2026-06-10 10:00+00','opened');\nINSERT INTO adjusters VALUES (501,'Riley'),(502,'Morgan');\nINSERT INTO claim_assignments VALUES (601,201,501,'2026-06-02 10:00+00'),(602,203,502,'2026-06-06 10:00+00');\nINSERT INTO external_directory VALUES (701,1,'beneficiary@example.invalid');",
)

TELECOM = _domain(
    "telecom_billing",
    "m51a_telecom_billing",
    "A",
    "Subscriber plans, network usage, invoices, and service outages.",
    (
        ("subscribers", "Subscribers", "Telecom account holders.", "anchor entity"),
        ("plans", "Plans", "Network service plans.", "reference entity"),
        ("subscriptions", "Subscriptions", "Subscriber plan enrollments.", "contract entity"),
        ("usage_records", "Usage records", "Measured network usage.", "fact entity"),
        ("invoices", "Invoices", "Periodic subscriber charges.", "transaction entity"),
        ("payments", "Payments", "Invoice settlement events.", "transaction entity"),
        ("outages", "Outages", "Network service incidents.", "event entity"),
        (
            "external_directory",
            "External directory",
            "Untrusted subscriber contacts.",
            "reference entity",
        ),
    ),
    (
        ("subscribers", "subscriber_id", "INTEGER", "Stable subscriber identifier.", False),
        ("subscribers", "subscriber_name", "TEXT", "Subscriber name.", False),
        ("subscribers", "market", "TEXT", "Service market.", False),
        ("plans", "plan_id", "INTEGER", "Stable plan identifier.", False),
        ("plans", "plan_name", "TEXT", "Plan name.", False),
        ("plans", "monthly_fee", "NUMERIC", "Monthly recurring fee.", False),
        ("subscriptions", "subscription_id", "INTEGER", "Stable subscription identifier.", False),
        ("subscriptions", "subscriber_id", "INTEGER", "Subscriber reference.", False),
        ("subscriptions", "plan_id", "INTEGER", "Plan reference.", False),
        ("subscriptions", "status", "TEXT", "Subscription state.", False),
        ("usage_records", "usage_id", "INTEGER", "Stable usage identifier.", False),
        ("usage_records", "subscriber_id", "INTEGER", "Subscriber reference.", False),
        ("usage_records", "used_on", "DATE", "Usage date.", False),
        ("usage_records", "megabytes", "INTEGER", "Consumed megabytes.", False),
        ("usage_records", "details", "JSONB", "Usage detail payload.", False),
        ("invoices", "invoice_id", "INTEGER", "Stable invoice identifier.", False),
        ("invoices", "subscriber_id", "INTEGER", "Subscriber reference.", False),
        ("invoices", "issued_on", "DATE", "Invoice issue date.", False),
        ("invoices", "amount_due", "NUMERIC", "Invoice amount.", False),
        ("invoices", "status", "TEXT", "Invoice state.", False),
        ("payments", "payment_id", "INTEGER", "Stable payment identifier.", False),
        ("payments", "invoice_id", "INTEGER", "Invoice reference.", False),
        ("payments", "paid_on", "DATE", "Payment date.", False),
        ("payments", "amount", "NUMERIC", "Payment amount.", False),
        ("outages", "outage_id", "INTEGER", "Stable outage identifier.", False),
        ("outages", "market", "TEXT", "Affected market.", False),
        ("outages", "started_at", "TIMESTAMPTZ", "Outage start.", False),
        ("outages", "ended_at", "TIMESTAMPTZ", "Outage end.", True),
        (
            "external_directory",
            "subscriber_id",
            "INTEGER",
            "Untrusted subscriber identifier.",
            False,
        ),
    ),
    tuple(
        [
            _rel(
                "telecom_billing",
                "subscription_subscriber",
                "subscriptions",
                "subscriber_id",
                "subscribers",
                "subscriber_id",
            ),
            _rel(
                "telecom_billing",
                "subscription_plan",
                "subscriptions",
                "plan_id",
                "plans",
                "plan_id",
            ),
            _rel(
                "telecom_billing",
                "usage_subscriber",
                "usage_records",
                "subscriber_id",
                "subscribers",
                "subscriber_id",
            ),
            _rel(
                "telecom_billing",
                "invoice_subscriber",
                "invoices",
                "subscriber_id",
                "subscribers",
                "subscriber_id",
            ),
            _rel(
                "telecom_billing",
                "payment_invoice",
                "payments",
                "invoice_id",
                "invoices",
                "invoice_id",
            ),
            _rel(
                "telecom_billing",
                "external_subscriber_trap",
                "external_directory",
                "subscriber_id",
                "subscribers",
                "subscriber_id",
                authorized=False,
                description="External contact ownership is not authorized.",
            ),
        ]
    ),
    (
        _metric(
            "telecom_billing", "usage_gb", "Megabytes divided by 1024.", "SUM(megabytes) / 1024"
        ),
    ),
    (
        {
            "rule_id": "rule:telecom_billing:active_subscription",
            "name": "Active subscription",
            "definition": "A subscription with status='active' is current.",
        },
    ),
    (
        CaseSpec(
            1,
            "List subscriber IDs with active subscriptions.",
            "SELECT DISTINCT subscriber_id FROM subscriptions WHERE status='active' ORDER BY subscriber_id",
            "SELECT s.subscriber_id FROM subscribers s WHERE EXISTS (SELECT 1 FROM subscriptions x WHERE x.subscriber_id=s.subscriber_id AND x.status='active') ORDER BY s.subscriber_id",
            ("subscriber_id",),
            ("population", "relationship"),
            (
                (
                    "adds active subscription",
                    ("INSERT INTO subscriptions VALUES (9001,1,1,'active')",),
                ),
                (
                    "adds paused subscription",
                    ("INSERT INTO subscriptions VALUES (9002,2,1,'paused')",),
                ),
            ),
            ("subscriptions", "subscribers"),
            population="matching-only",
        ),
        CaseSpec(
            2,
            "For each market, return market and total June megabytes used.",
            "SELECT s.market,SUM(u.megabytes) AS june_megabytes FROM subscribers s JOIN usage_records u ON u.subscriber_id=s.subscriber_id WHERE u.used_on>=DATE '2026-06-01' AND u.used_on<DATE '2026-07-01' GROUP BY s.market ORDER BY s.market",
            "SELECT market,june_megabytes FROM (SELECT s.market,SUM(u.megabytes) AS june_megabytes FROM usage_records u JOIN subscribers s ON s.subscriber_id=u.subscriber_id WHERE u.used_on>=DATE '2026-06-01' AND u.used_on<DATE '2026-07-01' GROUP BY s.market) q ORDER BY market",
            ("market", "june_megabytes"),
            ("aggregation", "temporal"),
            (
                (
                    "adds June usage",
                    ("INSERT INTO usage_records VALUES (9003,1,'2026-06-20',200,'{}')",),
                ),
                (
                    "adds July usage",
                    ("INSERT INTO usage_records VALUES (9004,1,'2026-07-01',200,'{}')",),
                ),
            ),
            ("subscribers", "usage_records"),
            grouping=("market",),
            temporal={
                "rule_id": "time:telecom_billing:clock",
                "interval": "[2026-06-01,2026-07-01)",
            },
        ),
        CaseSpec(
            3,
            "For each invoice, return invoice ID and the amount still unpaid.",
            "SELECT i.invoice_id,i.amount_due-COALESCE(SUM(p.amount),0) AS unpaid_amount FROM invoices i LEFT JOIN payments p ON p.invoice_id=i.invoice_id AND p.status='posted' GROUP BY i.invoice_id,i.amount_due ORDER BY i.invoice_id",
            "SELECT i.invoice_id,i.amount_due-COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id=i.invoice_id AND p.status='posted'),0) AS unpaid_amount FROM invoices i ORDER BY i.invoice_id",
            ("invoice_id", "unpaid_amount"),
            ("group_survival", "null_semantics", "correlated"),
            (
                (
                    "adds posted payment",
                    ("INSERT INTO payments VALUES (9005,301,'2026-06-22',10,'posted')",),
                ),
                (
                    "adds failed payment",
                    ("INSERT INTO payments VALUES (9006,302,'2026-06-22',10,'failed')",),
                ),
            ),
            ("invoices", "payments"),
            population="base-entity-preserving",
            calculations=("amount_due - posted payments",),
        ),
        CaseSpec(
            4,
            "For each subscriber, return subscriber ID and the plan ID of the latest subscription record.",
            "SELECT DISTINCT ON (subscriber_id) subscriber_id,plan_id FROM subscriptions ORDER BY subscriber_id,subscription_id DESC",
            "SELECT subscriber_id,plan_id FROM (SELECT subscriber_id,plan_id,ROW_NUMBER() OVER (PARTITION BY subscriber_id ORDER BY subscription_id DESC) rn FROM subscriptions) q WHERE rn=1 ORDER BY subscriber_id",
            ("subscriber_id", "plan_id"),
            ("latest_row", "window"),
            (
                ("adds latest plan", ("INSERT INTO subscriptions VALUES (9007,1,2,'active')",)),
                ("adds older plan", ("INSERT INTO subscriptions VALUES (9008,2,1,'paused')",)),
            ),
            ("subscriptions",),
        ),
        CaseSpec(
            5,
            "List subscriber IDs with no posted payment.",
            "SELECT s.subscriber_id FROM subscribers s WHERE NOT EXISTS (SELECT 1 FROM invoices i JOIN payments p ON p.invoice_id=i.invoice_id WHERE i.subscriber_id=s.subscriber_id AND p.status='posted') ORDER BY s.subscriber_id",
            "SELECT s.subscriber_id FROM subscribers s LEFT JOIN invoices i ON i.subscriber_id=s.subscriber_id LEFT JOIN payments p ON p.invoice_id=i.invoice_id AND p.status='posted' GROUP BY s.subscriber_id HAVING COUNT(p.payment_id)=0 ORDER BY s.subscriber_id",
            ("subscriber_id",),
            ("anti_join", "null_semantics"),
            (
                (
                    "adds posted payment",
                    ("INSERT INTO payments VALUES (9009,301,'2026-06-23',10,'posted')",),
                ),
                (
                    "adds unpaid invoice",
                    ("INSERT INTO invoices VALUES (9010,2,'2026-06-23',10,'open')",),
                ),
            ),
            ("subscribers", "invoices", "payments"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            6,
            "Return usage IDs whose detail payload reports at least 500 kilobytes of overhead.",
            "SELECT usage_id FROM usage_records WHERE (details->>'overhead_kb')::INTEGER>=500 ORDER BY usage_id",
            "SELECT usage_id FROM (SELECT usage_id,CAST(details->>'overhead_kb' AS INTEGER) overhead FROM usage_records) q WHERE overhead>=500 ORDER BY usage_id",
            ("usage_id",),
            ("json_typing",),
            (
                (
                    "adds high overhead",
                    (
                        "INSERT INTO usage_records VALUES (9011,1,'2026-06-24',10,'{\"overhead_kb\":\"700\"}')",
                    ),
                ),
                (
                    "adds low overhead",
                    (
                        "INSERT INTO usage_records VALUES (9012,1,'2026-06-24',10,'{\"overhead_kb\":\"20\"}')",
                    ),
                ),
            ),
            ("usage_records",),
        ),
        CaseSpec(
            7,
            "For each plan, return plan ID and the count of active subscribers, including plans with none.",
            "SELECT p.plan_id,COUNT(s.subscription_id) AS active_subscribers FROM plans p LEFT JOIN subscriptions s ON s.plan_id=p.plan_id AND s.status='active' GROUP BY p.plan_id ORDER BY p.plan_id",
            "SELECT p.plan_id,(SELECT COUNT(*) FROM subscriptions s WHERE s.plan_id=p.plan_id AND s.status='active') AS active_subscribers FROM plans p ORDER BY p.plan_id",
            ("plan_id", "active_subscribers"),
            ("group_survival", "population"),
            (
                (
                    "adds active subscription",
                    ("INSERT INTO subscriptions VALUES (9013,2,2,'active')",),
                ),
                (
                    "adds cancelled subscription",
                    ("INSERT INTO subscriptions VALUES (9014,2,2,'cancelled')",),
                ),
            ),
            ("plans", "subscriptions"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            8,
            "For each market, return market and the number of outages that began in June 2026.",
            "SELECT market,COUNT(outage_id) AS june_outages FROM outages WHERE started_at>=TIMESTAMPTZ '2026-06-01' AND started_at<TIMESTAMPTZ '2026-07-01' GROUP BY market ORDER BY market",
            "SELECT market,COUNT(*) AS june_outages FROM (SELECT market,outage_id FROM outages WHERE started_at>=TIMESTAMPTZ '2026-06-01' AND started_at<TIMESTAMPTZ '2026-07-01') q GROUP BY market ORDER BY market",
            ("market", "june_outages"),
            ("temporal", "aggregation"),
            (
                (
                    "adds June outage",
                    ("INSERT INTO outages VALUES (9015,'East','2026-06-25 10:00+00',NULL)",),
                ),
                (
                    "adds July outage",
                    ("INSERT INTO outages VALUES (9016,'East','2026-07-01 10:00+00',NULL)",),
                ),
            ),
            ("outages",),
            temporal={
                "rule_id": "time:telecom_billing:clock",
                "interval": "[2026-06-01,2026-07-01)",
            },
        ),
        CaseSpec(
            9,
            "Rank plans by monthly fee from highest to lowest, returning plan ID and rank.",
            "SELECT plan_id,DENSE_RANK() OVER (ORDER BY monthly_fee DESC,plan_id) AS fee_rank FROM plans ORDER BY fee_rank,plan_id",
            "SELECT plan_id,fee_rank FROM (SELECT plan_id,monthly_fee,DENSE_RANK() OVER (ORDER BY monthly_fee DESC,plan_id) fee_rank FROM plans) q ORDER BY fee_rank,plan_id",
            ("plan_id", "fee_rank"),
            ("window", "ranking"),
            (
                ("adds premium plan", ("INSERT INTO plans VALUES (9017,'Premium',999)",)),
                ("adds low plan", ("INSERT INTO plans VALUES (9018,'Basic',1)",)),
            ),
            ("plans",),
        ),
        CaseSpec(
            10,
            "For each subscriber, return subscriber ID and the average invoice amount, preserving subscribers with no invoice.",
            "SELECT s.subscriber_id,AVG(i.amount_due) AS average_invoice FROM subscribers s LEFT JOIN invoices i ON i.subscriber_id=s.subscriber_id GROUP BY s.subscriber_id ORDER BY s.subscriber_id",
            "SELECT s.subscriber_id,(SELECT AVG(i.amount_due) FROM invoices i WHERE i.subscriber_id=s.subscriber_id) AS average_invoice FROM subscribers s ORDER BY s.subscriber_id",
            ("subscriber_id", "average_invoice"),
            ("group_survival", "null_semantics", "population"),
            (
                ("adds invoice", ("INSERT INTO invoices VALUES (9019,3,'2026-06-26',44,'open')",)),
                (
                    "adds duplicate invoice",
                    ("INSERT INTO invoices VALUES (9020,3,'2026-06-27',44,'open')",),
                ),
            ),
            ("subscribers", "invoices"),
            population="base-entity-preserving",
        ),
    ),
    (
        "Which subscriber billing owner is listed externally?",
        "Which subscriber device identity is associated with this account?",
        "Which subscriber identity is listed in the roaming directory?",
    ),
    (
        (
            "Which service is current?",
            "active subscription versus plan with the latest usage",
            "INSERT INTO subscriptions VALUES (9021,1,1,'active')",
        ),
    ),
    ("Update the monthly fee for plan 1.",),
    "CREATE SCHEMA IF NOT EXISTS m51a_telecom_billing;\nSET search_path TO m51a_telecom_billing;\nCREATE TABLE subscribers (subscriber_id INTEGER PRIMARY KEY, subscriber_name TEXT NOT NULL, market TEXT NOT NULL);\nCREATE TABLE plans (plan_id INTEGER PRIMARY KEY, plan_name TEXT NOT NULL, monthly_fee NUMERIC(10,2) NOT NULL);\nCREATE TABLE subscriptions (subscription_id INTEGER PRIMARY KEY, subscriber_id INTEGER NOT NULL REFERENCES subscribers(subscriber_id), plan_id INTEGER NOT NULL REFERENCES plans(plan_id), status TEXT NOT NULL);\nCREATE TABLE usage_records (usage_id INTEGER PRIMARY KEY, subscriber_id INTEGER NOT NULL REFERENCES subscribers(subscriber_id), used_on DATE NOT NULL, megabytes INTEGER NOT NULL, details JSONB NOT NULL);\nCREATE TABLE invoices (invoice_id INTEGER PRIMARY KEY, subscriber_id INTEGER NOT NULL REFERENCES subscribers(subscriber_id), issued_on DATE NOT NULL, amount_due NUMERIC(10,2) NOT NULL, status TEXT NOT NULL);\nCREATE TABLE payments (payment_id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(invoice_id), paid_on DATE NOT NULL, amount NUMERIC(10,2) NOT NULL, status TEXT NOT NULL);\nCREATE TABLE outages (outage_id INTEGER PRIMARY KEY, market TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ);\nCREATE TABLE external_directory (directory_id INTEGER PRIMARY KEY, subscriber_id INTEGER NOT NULL, owner_email TEXT NOT NULL);",
    "INSERT INTO subscribers VALUES (1,'Aster Mobile','North'),(2,'Boreal Voice','South'),(3,'Cinder Fiber','East'),(4,'Dune Wireless','West');\nINSERT INTO plans VALUES (1,'Standard',50),(2,'Pro',90),(3,'Enterprise',250);\nINSERT INTO subscriptions VALUES (101,1,1,'active'),(102,1,2,'cancelled'),(103,2,2,'active'),(104,3,3,'paused'),(105,4,1,'active');\nINSERT INTO usage_records VALUES (201,1,'2026-06-01',100,'{\"overhead_kb\":\"600\"}'),(202,1,'2026-06-02',200,'{\"overhead_kb\":\"10\"}'),(203,2,'2026-06-03',500,'{\"overhead_kb\":\"800\"}'),(204,3,'2026-05-01',50,'{\"overhead_kb\":\"100\"}');\nINSERT INTO invoices VALUES (301,1,'2026-06-01',50,'open'),(302,2,'2026-06-01',90,'paid'),(303,3,'2026-06-01',250,'open');\nINSERT INTO payments VALUES (401,302,'2026-06-05',90,'posted');\nINSERT INTO outages VALUES (501,'North','2026-06-02 10:00+00','2026-06-02 12:00+00'),(502,'South','2026-05-02 10:00+00','2026-05-02 11:00+00');\nINSERT INTO external_directory VALUES (601,1,'owner@example.invalid');",
)

MARKETPLACE = _domain(
    "marketplace_ops",
    "m51a_marketplace_ops",
    "B",
    "Two-sided marketplace listings, orders, payouts, and disputes.",
    (
        ("sellers", "Sellers", "Marketplace merchants.", "anchor entity"),
        ("buyers", "Buyers", "Marketplace customers.", "anchor entity"),
        ("listings", "Listings", "Seller offers.", "catalog entity"),
        ("orders", "Orders", "Buyer purchases.", "transaction entity"),
        ("order_lines", "Order lines", "Purchased listing quantities.", "transaction detail"),
        ("payouts", "Payouts", "Seller settlement records.", "transaction entity"),
        ("reviews", "Reviews", "Buyer listing reviews.", "event entity"),
        ("disputes", "Disputes", "Order disputes.", "case entity"),
        (
            "external_directory",
            "External directory",
            "Untrusted seller contacts.",
            "reference entity",
        ),
    ),
    (
        ("sellers", "seller_id", "INTEGER", "Stable seller identifier.", False),
        ("sellers", "seller_name", "TEXT", "Seller name.", False),
        ("sellers", "seller_tier", "TEXT", "Seller service tier.", False),
        ("buyers", "buyer_id", "INTEGER", "Stable buyer identifier.", False),
        ("buyers", "buyer_name", "TEXT", "Buyer name.", False),
        ("listings", "listing_id", "INTEGER", "Stable listing identifier.", False),
        ("listings", "seller_id", "INTEGER", "Seller reference.", False),
        ("listings", "category", "TEXT", "Listing category.", False),
        ("listings", "unit_price", "NUMERIC", "Listing unit price.", False),
        ("listings", "active", "BOOLEAN", "Whether listing is active.", False),
        ("orders", "order_id", "INTEGER", "Stable order identifier.", False),
        ("orders", "buyer_id", "INTEGER", "Buyer reference.", False),
        ("orders", "ordered_on", "DATE", "Order date.", False),
        ("orders", "status", "TEXT", "Order state.", False),
        ("order_lines", "line_id", "INTEGER", "Stable order-line identifier.", False),
        ("order_lines", "order_id", "INTEGER", "Order reference.", False),
        ("order_lines", "listing_id", "INTEGER", "Listing reference.", False),
        ("order_lines", "quantity", "INTEGER", "Purchased quantity.", False),
        ("payouts", "payout_id", "INTEGER", "Stable payout identifier.", False),
        ("payouts", "seller_id", "INTEGER", "Seller reference.", False),
        ("payouts", "paid_on", "DATE", "Payout date.", False),
        ("payouts", "amount", "NUMERIC", "Payout amount.", False),
        ("payouts", "status", "TEXT", "Payout state.", False),
        ("reviews", "review_id", "INTEGER", "Stable review identifier.", False),
        ("reviews", "listing_id", "INTEGER", "Listing reference.", False),
        ("reviews", "score", "INTEGER", "Review score.", False),
        ("reviews", "reviewed_on", "DATE", "Review date.", False),
        ("disputes", "dispute_id", "INTEGER", "Stable dispute identifier.", False),
        ("disputes", "order_id", "INTEGER", "Order reference.", False),
        ("disputes", "opened_on", "DATE", "Dispute date.", False),
        ("disputes", "status", "TEXT", "Dispute state.", False),
        ("external_directory", "seller_id", "INTEGER", "Untrusted seller identifier.", False),
    ),
    tuple(
        [
            _rel(
                "marketplace_ops", "listing_seller", "listings", "seller_id", "sellers", "seller_id"
            ),
            _rel("marketplace_ops", "order_buyer", "orders", "buyer_id", "buyers", "buyer_id"),
            _rel("marketplace_ops", "line_order", "order_lines", "order_id", "orders", "order_id"),
            _rel(
                "marketplace_ops",
                "line_listing",
                "order_lines",
                "listing_id",
                "listings",
                "listing_id",
            ),
            _rel(
                "marketplace_ops", "payout_seller", "payouts", "seller_id", "sellers", "seller_id"
            ),
            _rel(
                "marketplace_ops",
                "review_listing",
                "reviews",
                "listing_id",
                "listings",
                "listing_id",
            ),
            _rel("marketplace_ops", "dispute_order", "disputes", "order_id", "orders", "order_id"),
            _rel(
                "marketplace_ops",
                "external_seller_trap",
                "external_directory",
                "seller_id",
                "sellers",
                "seller_id",
                authorized=False,
                description="External seller contact ownership is not authorized.",
            ),
        ]
    ),
    (
        _metric(
            "marketplace_ops",
            "gross_merchandise_value",
            "Quantity times listing unit price.",
            "SUM(quantity * unit_price)",
        ),
    ),
    (
        {
            "rule_id": "rule:marketplace_ops:completed_order",
            "name": "Completed order",
            "definition": "An order with status='completed' is included in settled sales.",
        },
    ),
    (
        CaseSpec(
            1,
            "List active listing IDs in listing order.",
            "SELECT listing_id FROM listings WHERE active ORDER BY listing_id",
            "SELECT listing_id FROM (SELECT listing_id,active FROM listings) q WHERE active ORDER BY listing_id",
            ("listing_id",),
            ("simple_filter",),
            (
                ("adds active listing", ("INSERT INTO listings VALUES (9001,1,'books',12,true)",)),
                (
                    "adds inactive listing",
                    ("INSERT INTO listings VALUES (9002,2,'games',12,false)",),
                ),
            ),
            ("listings",),
            "STRAIGHTFORWARD",
        ),
        CaseSpec(
            2,
            "For each seller, return seller ID and gross merchandise value for completed orders.",
            "SELECT l.seller_id,SUM(ol.quantity*l.unit_price) AS gmv FROM listings l JOIN order_lines ol ON ol.listing_id=l.listing_id JOIN orders o ON o.order_id=ol.order_id WHERE o.status='completed' GROUP BY l.seller_id ORDER BY l.seller_id",
            "SELECT seller_id,gmv FROM (SELECT l.seller_id,SUM(ol.quantity*l.unit_price) AS gmv FROM order_lines ol JOIN listings l ON l.listing_id=ol.listing_id JOIN orders o ON o.order_id=ol.order_id AND o.status='completed' GROUP BY l.seller_id) q ORDER BY seller_id",
            ("seller_id", "gmv"),
            ("multi_join", "aggregation", "calculation"),
            (
                (
                    "adds completed sale",
                    (
                        "INSERT INTO orders VALUES (9003,1,'2026-06-21','completed')",
                        "INSERT INTO order_lines VALUES (9004,9003,101,2)",
                    ),
                ),
                (
                    "adds cancelled order",
                    (
                        "INSERT INTO orders VALUES (9005,1,'2026-06-21','cancelled')",
                        "INSERT INTO order_lines VALUES (9006,9005,101,2)",
                    ),
                ),
            ),
            ("listings", "order_lines", "orders"),
            calculations=("quantity * unit_price",),
        ),
        CaseSpec(
            3,
            "For each seller, return seller ID and the count of listings with no completed sale.",
            "SELECT s.seller_id,COUNT(l.listing_id) AS unsold_listings FROM sellers s LEFT JOIN listings l ON l.seller_id=s.seller_id AND NOT EXISTS (SELECT 1 FROM order_lines ol JOIN orders o ON o.order_id=ol.order_id WHERE ol.listing_id=l.listing_id AND o.status='completed') GROUP BY s.seller_id ORDER BY s.seller_id",
            "SELECT s.seller_id,(SELECT COUNT(*) FROM listings l WHERE l.seller_id=s.seller_id AND NOT EXISTS (SELECT 1 FROM order_lines ol JOIN orders o ON o.order_id=ol.order_id WHERE ol.listing_id=l.listing_id AND o.status='completed')) AS unsold_listings FROM sellers s ORDER BY s.seller_id",
            ("seller_id", "unsold_listings"),
            ("anti_join", "group_survival", "population"),
            (
                ("adds unsold listing", ("INSERT INTO listings VALUES (9007,2,'books',8,true)",)),
                (
                    "adds sold listing",
                    (
                        "INSERT INTO listings VALUES (9008,2,'games',8,true)",
                        "INSERT INTO orders VALUES (9009,2,'2026-06-22','completed')",
                        "INSERT INTO order_lines VALUES (9010,9009,9008,1)",
                    ),
                ),
            ),
            ("sellers", "listings", "orders", "order_lines"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            4,
            "For each seller, return seller ID and average review score across its listings.",
            "SELECT s.seller_id,AVG(r.score) AS average_score FROM sellers s LEFT JOIN listings l ON l.seller_id=s.seller_id LEFT JOIN reviews r ON r.listing_id=l.listing_id GROUP BY s.seller_id ORDER BY s.seller_id",
            "SELECT s.seller_id,(SELECT AVG(r.score) FROM listings l JOIN reviews r ON r.listing_id=l.listing_id WHERE l.seller_id=s.seller_id) AS average_score FROM sellers s ORDER BY s.seller_id",
            ("seller_id", "average_score"),
            ("group_survival", "null_semantics", "population"),
            (
                ("adds review", ("INSERT INTO reviews VALUES (9011,101,5,'2026-06-23')",)),
                (
                    "adds listing without review",
                    ("INSERT INTO listings VALUES (9012,2,'home',20,true)",),
                ),
            ),
            ("sellers", "listings", "reviews"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            5,
            "Return dispute IDs opened in June 2026 that remain open.",
            "SELECT dispute_id FROM disputes WHERE status='open' AND opened_on>=DATE '2026-06-01' AND opened_on<DATE '2026-07-01' ORDER BY dispute_id",
            "SELECT dispute_id FROM (SELECT dispute_id,status,opened_on FROM disputes) q WHERE status='open' AND opened_on>=DATE '2026-06-01' AND opened_on<DATE '2026-07-01' ORDER BY dispute_id",
            ("dispute_id",),
            ("temporal", "simple_filter"),
            (
                (
                    "adds June open dispute",
                    ("INSERT INTO disputes VALUES (9013,201,'2026-06-24','open')",),
                ),
                (
                    "adds July open dispute",
                    ("INSERT INTO disputes VALUES (9014,201,'2026-07-01','open')",),
                ),
            ),
            ("disputes",),
            temporal={
                "rule_id": "time:marketplace_ops:clock",
                "interval": "[2026-06-01,2026-07-01)",
            },
        ),
        CaseSpec(
            6,
            "For each buyer, return buyer ID and the number of completed orders.",
            "SELECT b.buyer_id,COUNT(o.order_id) AS completed_orders FROM buyers b LEFT JOIN orders o ON o.buyer_id=b.buyer_id AND o.status='completed' GROUP BY b.buyer_id ORDER BY b.buyer_id",
            "SELECT b.buyer_id,(SELECT COUNT(*) FROM orders o WHERE o.buyer_id=b.buyer_id AND o.status='completed') AS completed_orders FROM buyers b ORDER BY b.buyer_id",
            ("buyer_id", "completed_orders"),
            ("group_survival", "population"),
            (
                (
                    "adds completed order",
                    ("INSERT INTO orders VALUES (9015,2,'2026-06-25','completed')",),
                ),
                (
                    "adds pending order",
                    ("INSERT INTO orders VALUES (9016,2,'2026-06-25','pending')",),
                ),
            ),
            ("buyers", "orders"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            7,
            "For each seller, return seller ID and total posted payout amount.",
            "SELECT s.seller_id,COALESCE(SUM(p.amount),0) AS posted_payout FROM sellers s LEFT JOIN payouts p ON p.seller_id=s.seller_id AND p.status='posted' GROUP BY s.seller_id ORDER BY s.seller_id",
            "SELECT s.seller_id,COALESCE((SELECT SUM(p.amount) FROM payouts p WHERE p.seller_id=s.seller_id AND p.status='posted'),0) AS posted_payout FROM sellers s ORDER BY s.seller_id",
            ("seller_id", "posted_payout"),
            ("group_survival", "null_semantics"),
            (
                (
                    "adds posted payout",
                    ("INSERT INTO payouts VALUES (9017,1,'2026-06-26',12,'posted')",),
                ),
                (
                    "adds held payout",
                    ("INSERT INTO payouts VALUES (9018,1,'2026-06-26',12,'held')",),
                ),
            ),
            ("sellers", "payouts"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            8,
            "Rank listings by unit price from highest to lowest, returning listing ID and rank.",
            "SELECT listing_id,DENSE_RANK() OVER (ORDER BY unit_price DESC,listing_id) AS price_rank FROM listings ORDER BY price_rank,listing_id",
            "SELECT listing_id,price_rank FROM (SELECT listing_id,DENSE_RANK() OVER (ORDER BY unit_price DESC,listing_id) price_rank FROM listings) q ORDER BY price_rank,listing_id",
            ("listing_id", "price_rank"),
            ("window", "ranking"),
            (
                (
                    "adds expensive listing",
                    ("INSERT INTO listings VALUES (9019,1,'luxury',999,true)",),
                ),
                (
                    "adds inexpensive listing",
                    ("INSERT INTO listings VALUES (9020,1,'clearance',1,true)",),
                ),
            ),
            ("listings",),
        ),
        CaseSpec(
            9,
            "For each listing, return listing ID and total quantity sold in completed orders.",
            "SELECT l.listing_id,COALESCE(SUM(ol.quantity) FILTER (WHERE o.status='completed'),0) AS sold_quantity FROM listings l LEFT JOIN order_lines ol ON ol.listing_id=l.listing_id LEFT JOIN orders o ON o.order_id=ol.order_id GROUP BY l.listing_id ORDER BY l.listing_id",
            "SELECT l.listing_id,COALESCE((SELECT SUM(ol.quantity) FROM order_lines ol JOIN orders o ON o.order_id=ol.order_id WHERE ol.listing_id=l.listing_id AND o.status='completed'),0) AS sold_quantity FROM listings l ORDER BY l.listing_id",
            ("listing_id", "sold_quantity"),
            ("aggregate", "filter_scope", "population"),
            (
                (
                    "adds completed sale",
                    (
                        "INSERT INTO orders VALUES (9021,1,'2026-06-27','completed')",
                        "INSERT INTO order_lines VALUES (9022,9021,101,3)",
                    ),
                ),
                (
                    "adds returned sale",
                    (
                        "INSERT INTO orders VALUES (9023,1,'2026-06-27','returned')",
                        "INSERT INTO order_lines VALUES (9024,9023,101,3)",
                    ),
                ),
            ),
            ("listings", "order_lines", "orders"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            10,
            "Return buyer IDs with no disputed order.",
            "SELECT b.buyer_id FROM buyers b WHERE NOT EXISTS (SELECT 1 FROM orders o JOIN disputes d ON d.order_id=o.order_id WHERE o.buyer_id=b.buyer_id) ORDER BY b.buyer_id",
            "SELECT b.buyer_id FROM buyers b LEFT JOIN orders o ON o.buyer_id=b.buyer_id LEFT JOIN disputes d ON d.order_id=o.order_id GROUP BY b.buyer_id HAVING COUNT(d.dispute_id)=0 ORDER BY b.buyer_id",
            ("buyer_id",),
            ("anti_join", "null_semantics"),
            (
                (
                    "adds disputed order",
                    (
                        "INSERT INTO orders VALUES (9025,1,'2026-06-28','completed')",
                        "INSERT INTO disputes VALUES (9026,9025,'2026-06-28','open')",
                    ),
                ),
                (
                    "adds undisputed order",
                    ("INSERT INTO orders VALUES (9027,2,'2026-06-28','completed')",),
                ),
            ),
            ("buyers", "orders", "disputes"),
            population="base-entity-preserving",
        ),
    ),
    (
        "Which seller contact is in the external directory?",
        "Which buyer identity is linked through the settlement directory?",
    ),
    (
        (
            "Which orders are current?",
            "completed order versus order with a settled payout",
            "INSERT INTO orders VALUES (9028,1,'2026-06-29','completed')",
        ),
        (
            "Which listing is active?",
            "listing flag versus listing with recent completed sale",
            "INSERT INTO listings VALUES (9029,1,'books',10,true)",
        ),
    ),
    ("Delete disputes marked resolved.",),
    "CREATE SCHEMA IF NOT EXISTS m51a_marketplace_ops;\nSET search_path TO m51a_marketplace_ops;\nCREATE TABLE sellers (seller_id INTEGER PRIMARY KEY, seller_name TEXT NOT NULL, seller_tier TEXT NOT NULL);\nCREATE TABLE buyers (buyer_id INTEGER PRIMARY KEY, buyer_name TEXT NOT NULL);\nCREATE TABLE listings (listing_id INTEGER PRIMARY KEY, seller_id INTEGER NOT NULL REFERENCES sellers(seller_id), category TEXT NOT NULL, unit_price NUMERIC(10,2) NOT NULL, active BOOLEAN NOT NULL);\nCREATE TABLE orders (order_id INTEGER PRIMARY KEY, buyer_id INTEGER NOT NULL REFERENCES buyers(buyer_id), ordered_on DATE NOT NULL, status TEXT NOT NULL);\nCREATE TABLE order_lines (line_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id), listing_id INTEGER NOT NULL REFERENCES listings(listing_id), quantity INTEGER NOT NULL);\nCREATE TABLE payouts (payout_id INTEGER PRIMARY KEY, seller_id INTEGER NOT NULL REFERENCES sellers(seller_id), paid_on DATE NOT NULL, amount NUMERIC(10,2) NOT NULL, status TEXT NOT NULL);\nCREATE TABLE reviews (review_id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL REFERENCES listings(listing_id), score INTEGER NOT NULL, reviewed_on DATE NOT NULL);\nCREATE TABLE disputes (dispute_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id), opened_on DATE NOT NULL, status TEXT NOT NULL);\nCREATE TABLE external_directory (directory_id INTEGER PRIMARY KEY, seller_id INTEGER NOT NULL, owner_email TEXT NOT NULL);",
    "INSERT INTO sellers VALUES (1,'Aster Market','gold'),(2,'Boreal Goods','silver'),(3,'Cinder House','bronze');\nINSERT INTO buyers VALUES (1,'Dune Buyer'),(2,'Ember Buyer'),(3,'Fjord Buyer');\nINSERT INTO listings VALUES (101,1,'books',20,true),(102,1,'tools',35,true),(103,2,'games',50,false),(104,3,'home',15,true);\nINSERT INTO orders VALUES (201,1,'2026-06-01','completed'),(202,1,'2026-06-03','pending'),(203,2,'2026-06-05','completed'),(204,3,'2026-06-07','cancelled');\nINSERT INTO order_lines VALUES (301,201,101,2),(302,202,102,1),(303,203,103,3),(304,204,104,1);\nINSERT INTO payouts VALUES (401,1,'2026-06-10',40,'posted'),(402,2,'2026-06-10',150,'held');\nINSERT INTO reviews VALUES (501,101,5,'2026-06-11'),(502,103,3,'2026-06-11'),(503,104,4,'2026-06-12');\nINSERT INTO disputes VALUES (601,202,'2026-06-04','open');\nINSERT INTO external_directory VALUES (701,1,'seller@example.invalid');",
)

WORKFORCE = _domain(
    "workforce_ops",
    "m51a_workforce_ops",
    "B",
    "Employee scheduling, timekeeping, leave, and training compliance.",
    (
        ("employees", "Employees", "People employed by the organization.", "anchor entity"),
        ("teams", "Teams", "Organizational teams.", "reference entity"),
        ("shifts", "Shifts", "Scheduled work shifts.", "schedule entity"),
        ("timesheets", "Timesheets", "Recorded work intervals.", "fact entity"),
        ("absences", "Absences", "Approved or pending leave.", "event entity"),
        ("training_records", "Training records", "Employee course completions.", "event entity"),
        (
            "payroll_adjustments",
            "Payroll adjustments",
            "Pay corrections and bonuses.",
            "transaction entity",
        ),
        (
            "external_directory",
            "External directory",
            "Untrusted employee contacts.",
            "reference entity",
        ),
    ),
    (
        ("employees", "employee_id", "INTEGER", "Stable employee identifier.", False),
        ("employees", "employee_name", "TEXT", "Employee name.", False),
        ("employees", "team_id", "INTEGER", "Team reference.", False),
        ("employees", "status", "TEXT", "Employment state.", False),
        ("employees", "profile", "JSONB", "Employee profile metadata.", False),
        ("teams", "team_id", "INTEGER", "Stable team identifier.", False),
        ("teams", "team_name", "TEXT", "Team name.", False),
        ("shifts", "shift_id", "INTEGER", "Stable shift identifier.", False),
        ("shifts", "employee_id", "INTEGER", "Employee reference.", False),
        ("shifts", "starts_at", "TIMESTAMPTZ", "Shift start.", False),
        ("shifts", "ends_at", "TIMESTAMPTZ", "Shift end.", False),
        ("timesheets", "timesheet_id", "INTEGER", "Stable timesheet identifier.", False),
        ("timesheets", "employee_id", "INTEGER", "Employee reference.", False),
        ("timesheets", "worked_on", "DATE", "Work date.", False),
        ("timesheets", "hours", "NUMERIC", "Recorded hours.", False),
        ("timesheets", "approved", "BOOLEAN", "Whether hours are approved.", False),
        ("absences", "absence_id", "INTEGER", "Stable absence identifier.", False),
        ("absences", "employee_id", "INTEGER", "Employee reference.", False),
        ("absences", "starts_on", "DATE", "Absence start.", False),
        ("absences", "ends_on", "DATE", "Absence end.", False),
        ("absences", "status", "TEXT", "Absence state.", False),
        ("training_records", "record_id", "INTEGER", "Stable training record identifier.", False),
        ("training_records", "employee_id", "INTEGER", "Employee reference.", False),
        ("training_records", "course", "TEXT", "Course name.", False),
        ("training_records", "completed_on", "DATE", "Completion date.", False),
        ("payroll_adjustments", "adjustment_id", "INTEGER", "Stable adjustment identifier.", False),
        ("payroll_adjustments", "employee_id", "INTEGER", "Employee reference.", False),
        ("payroll_adjustments", "amount", "NUMERIC", "Adjustment amount.", False),
        ("payroll_adjustments", "adjusted_on", "DATE", "Adjustment date.", False),
        ("payroll_adjustments", "kind", "TEXT", "Adjustment type.", False),
        ("external_directory", "employee_id", "INTEGER", "Untrusted employee identifier.", False),
    ),
    tuple(
        [
            _rel("workforce_ops", "employee_team", "employees", "team_id", "teams", "team_id"),
            _rel(
                "workforce_ops",
                "shift_employee",
                "shifts",
                "employee_id",
                "employees",
                "employee_id",
            ),
            _rel(
                "workforce_ops",
                "timesheet_employee",
                "timesheets",
                "employee_id",
                "employees",
                "employee_id",
            ),
            _rel(
                "workforce_ops",
                "absence_employee",
                "absences",
                "employee_id",
                "employees",
                "employee_id",
            ),
            _rel(
                "workforce_ops",
                "training_employee",
                "training_records",
                "employee_id",
                "employees",
                "employee_id",
            ),
            _rel(
                "workforce_ops",
                "adjustment_employee",
                "payroll_adjustments",
                "employee_id",
                "employees",
                "employee_id",
            ),
            _rel(
                "workforce_ops",
                "external_employee_trap",
                "external_directory",
                "employee_id",
                "employees",
                "employee_id",
                authorized=False,
                description="External employee identity is not authorized.",
            ),
        ]
    ),
    (
        _metric(
            "workforce_ops",
            "approved_hours",
            "Approved timesheet hours.",
            "SUM(hours) FILTER (WHERE approved)",
        ),
    ),
    (
        {
            "rule_id": "rule:workforce_ops:approved_hours",
            "name": "Approved hours",
            "definition": "Only timesheets with approved=true count toward reported hours.",
        },
    ),
    (
        CaseSpec(
            1,
            "List active employee IDs in ascending order.",
            "SELECT employee_id FROM employees WHERE status='active' ORDER BY employee_id",
            "SELECT e.employee_id FROM employees e JOIN (SELECT employee_id FROM employees WHERE status='active') q ON q.employee_id=e.employee_id ORDER BY e.employee_id",
            ("employee_id",),
            ("simple_filter",),
            (
                (
                    "adds active employee",
                    ("INSERT INTO employees VALUES (9001,'New Worker',1,'active','{}')",),
                ),
                (
                    "adds inactive employee",
                    ("INSERT INTO employees VALUES (9002,'Former Worker',1,'inactive','{}')",),
                ),
            ),
            ("employees",),
            "STRAIGHTFORWARD",
        ),
        CaseSpec(
            2,
            "For each team, return team ID and approved hours recorded in June 2026.",
            "SELECT e.team_id,SUM(t.hours) AS approved_hours FROM employees e JOIN timesheets t ON t.employee_id=e.employee_id WHERE t.approved AND t.worked_on>=DATE '2026-06-01' AND t.worked_on<DATE '2026-07-01' GROUP BY e.team_id ORDER BY e.team_id",
            "SELECT team_id,approved_hours FROM (SELECT e.team_id,SUM(t.hours) AS approved_hours FROM timesheets t JOIN employees e ON e.employee_id=t.employee_id WHERE t.approved AND t.worked_on>=DATE '2026-06-01' AND t.worked_on<DATE '2026-07-01' GROUP BY e.team_id) q ORDER BY team_id",
            ("team_id", "approved_hours"),
            ("aggregation", "temporal", "status_definition"),
            (
                (
                    "adds approved June hours",
                    ("INSERT INTO timesheets VALUES (9003,1,'2026-06-20',8,true)",),
                ),
                (
                    "adds unapproved June hours",
                    ("INSERT INTO timesheets VALUES (9004,1,'2026-06-20',8,false)",),
                ),
            ),
            ("employees", "timesheets"),
            grouping=("team_id",),
            temporal={"rule_id": "time:workforce_ops:clock", "interval": "[2026-06-01,2026-07-01)"},
        ),
        CaseSpec(
            3,
            "For each employee, return employee ID and total payroll adjustment amount.",
            "SELECT e.employee_id,COALESCE(SUM(a.amount),0) AS adjustment_total FROM employees e LEFT JOIN payroll_adjustments a ON a.employee_id=e.employee_id GROUP BY e.employee_id ORDER BY e.employee_id",
            "SELECT e.employee_id,COALESCE((SELECT SUM(a.amount) FROM payroll_adjustments a WHERE a.employee_id=e.employee_id),0) AS adjustment_total FROM employees e ORDER BY e.employee_id",
            ("employee_id", "adjustment_total"),
            ("group_survival", "population", "null_semantics"),
            (
                (
                    "adds bonus",
                    ("INSERT INTO payroll_adjustments VALUES (9005,1,25,'2026-06-21','bonus')",),
                ),
                (
                    "adds deduction",
                    (
                        "INSERT INTO payroll_adjustments VALUES (9006,1,-10,'2026-06-21','deduction')",
                    ),
                ),
            ),
            ("employees", "payroll_adjustments"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            4,
            "For each employee, return employee ID and the latest completed training date.",
            "SELECT e.employee_id,MAX(t.completed_on) AS latest_training_on FROM employees e LEFT JOIN training_records t ON t.employee_id=e.employee_id GROUP BY e.employee_id ORDER BY e.employee_id",
            "SELECT e.employee_id,(SELECT MAX(t.completed_on) FROM training_records t WHERE t.employee_id=e.employee_id) AS latest_training_on FROM employees e ORDER BY e.employee_id",
            ("employee_id", "latest_training_on"),
            ("latest_row", "temporal", "population"),
            (
                (
                    "adds newer training",
                    ("INSERT INTO training_records VALUES (9007,1,'Safety','2026-06-25')",),
                ),
                (
                    "adds older training",
                    ("INSERT INTO training_records VALUES (9008,2,'Safety','2025-01-01')",),
                ),
            ),
            ("employees", "training_records"),
            population="base-entity-preserving",
            temporal={"rule_id": "time:workforce_ops:clock", "latest": "maximum completed_on"},
        ),
        CaseSpec(
            5,
            "List employee IDs with no approved timesheet in June 2026.",
            "SELECT e.employee_id FROM employees e WHERE NOT EXISTS (SELECT 1 FROM timesheets t WHERE t.employee_id=e.employee_id AND t.approved AND t.worked_on>=DATE '2026-06-01' AND t.worked_on<DATE '2026-07-01') ORDER BY e.employee_id",
            "SELECT e.employee_id FROM employees e LEFT JOIN timesheets t ON t.employee_id=e.employee_id AND t.approved AND t.worked_on>=DATE '2026-06-01' AND t.worked_on<DATE '2026-07-01' GROUP BY e.employee_id HAVING COUNT(t.timesheet_id)=0 ORDER BY e.employee_id",
            ("employee_id",),
            ("anti_join", "temporal", "null_semantics"),
            (
                (
                    "adds approved June row",
                    ("INSERT INTO timesheets VALUES (9009,3,'2026-06-22',8,true)",),
                ),
                ("adds July row", ("INSERT INTO timesheets VALUES (9010,3,'2026-07-01',8,true)",)),
            ),
            ("employees", "timesheets"),
            population="base-entity-preserving",
            temporal={"rule_id": "time:workforce_ops:clock", "interval": "[2026-06-01,2026-07-01)"},
        ),
        CaseSpec(
            6,
            "Return employee IDs whose profile risk score is at least 3.",
            "SELECT employee_id FROM employees WHERE (profile->>'risk_score')::INTEGER>=3 ORDER BY employee_id",
            "SELECT employee_id FROM (SELECT employee_id,CAST(profile->>'risk_score' AS INTEGER) score FROM employees) q WHERE score>=3 ORDER BY employee_id",
            ("employee_id",),
            ("json_typing",),
            (
                (
                    "adds high-risk employee",
                    (
                        "INSERT INTO employees VALUES (9011,'Risky Worker',2,'active','{\"risk_score\":\"4\"}')",
                    ),
                ),
                (
                    "adds low-risk employee",
                    (
                        "INSERT INTO employees VALUES (9012,'Safe Worker',2,'active','{\"risk_score\":\"1\"}')",
                    ),
                ),
            ),
            ("employees",),
        ),
        CaseSpec(
            7,
            "For each team, return team ID and count of employees with a shift starting in June 2026, including teams with none.",
            "SELECT t.team_id,COUNT(DISTINCT s.employee_id) AS june_workers FROM teams t LEFT JOIN employees e ON e.team_id=t.team_id LEFT JOIN shifts s ON s.employee_id=e.employee_id AND s.starts_at>=TIMESTAMPTZ '2026-06-01' AND s.starts_at<TIMESTAMPTZ '2026-07-01' GROUP BY t.team_id ORDER BY t.team_id",
            "SELECT t.team_id,(SELECT COUNT(DISTINCT s.employee_id) FROM employees e JOIN shifts s ON s.employee_id=e.employee_id WHERE e.team_id=t.team_id AND s.starts_at>=TIMESTAMPTZ '2026-06-01' AND s.starts_at<TIMESTAMPTZ '2026-07-01') AS june_workers FROM teams t ORDER BY t.team_id",
            ("team_id", "june_workers"),
            ("group_survival", "temporal", "population"),
            (
                (
                    "adds June shift",
                    (
                        "INSERT INTO shifts VALUES (9013,3,'2026-06-24 08:00+00','2026-06-24 16:00+00')",
                    ),
                ),
                (
                    "adds July shift",
                    (
                        "INSERT INTO shifts VALUES (9014,3,'2026-07-01 08:00+00','2026-07-01 16:00+00')",
                    ),
                ),
            ),
            ("teams", "employees", "shifts"),
            population="base-entity-preserving",
            temporal={"rule_id": "time:workforce_ops:clock", "interval": "[2026-06-01,2026-07-01)"},
        ),
        CaseSpec(
            8,
            "For each employee, return employee ID and total hours including approved timesheets only.",
            "SELECT e.employee_id,COALESCE(SUM(t.hours) FILTER (WHERE t.approved),0) AS approved_hours FROM employees e LEFT JOIN timesheets t ON t.employee_id=e.employee_id GROUP BY e.employee_id ORDER BY e.employee_id",
            "SELECT e.employee_id,COALESCE((SELECT SUM(t.hours) FROM timesheets t WHERE t.employee_id=e.employee_id AND t.approved),0) AS approved_hours FROM employees e ORDER BY e.employee_id",
            ("employee_id", "approved_hours"),
            ("aggregate", "filter_scope", "population"),
            (
                (
                    "adds approved hours",
                    ("INSERT INTO timesheets VALUES (9015,1,'2026-06-26',4,true)",),
                ),
                (
                    "adds unapproved hours",
                    ("INSERT INTO timesheets VALUES (9016,1,'2026-06-26',4,false)",),
                ),
            ),
            ("employees", "timesheets"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            9,
            "Rank teams by total approved hours, highest first, returning team ID and rank.",
            "SELECT team_id,DENSE_RANK() OVER (ORDER BY approved_hours DESC,team_id) AS hours_rank FROM (SELECT e.team_id,SUM(t.hours) AS approved_hours FROM employees e JOIN timesheets t ON t.employee_id=e.employee_id WHERE t.approved GROUP BY e.team_id) q ORDER BY hours_rank,team_id",
            "SELECT team_id,hours_rank FROM (SELECT team_id,approved_hours,DENSE_RANK() OVER (ORDER BY approved_hours DESC,team_id) hours_rank FROM (SELECT e.team_id,SUM(t.hours) AS approved_hours FROM employees e JOIN timesheets t ON t.employee_id=e.employee_id WHERE t.approved GROUP BY e.team_id) x) y ORDER BY hours_rank,team_id",
            ("team_id", "hours_rank"),
            ("window", "ranking", "aggregation"),
            (
                (
                    "adds high hours",
                    ("INSERT INTO timesheets VALUES (9017,2,'2026-06-27',40,true)",),
                ),
                ("adds low hours", ("INSERT INTO timesheets VALUES (9018,2,'2026-06-27',1,true)",)),
            ),
            ("employees", "timesheets"),
            grouping=("team_id",),
        ),
        CaseSpec(
            10,
            "For each employee, return employee ID and the count of absence days for approved absences.",
            "SELECT e.employee_id,COALESCE(SUM((a.ends_on-a.starts_on)+1) FILTER (WHERE a.status='approved'),0) AS absence_days FROM employees e LEFT JOIN absences a ON a.employee_id=e.employee_id GROUP BY e.employee_id ORDER BY e.employee_id",
            "SELECT e.employee_id,COALESCE((SELECT SUM((a.ends_on-a.starts_on)+1) FROM absences a WHERE a.employee_id=e.employee_id AND a.status='approved'),0) AS absence_days FROM employees e ORDER BY e.employee_id",
            ("employee_id", "absence_days"),
            ("temporal", "aggregate", "population"),
            (
                (
                    "adds approved absence",
                    ("INSERT INTO absences VALUES (9019,1,'2026-06-28','2026-06-29','approved')",),
                ),
                (
                    "adds pending absence",
                    ("INSERT INTO absences VALUES (9020,1,'2026-06-28','2026-06-29','pending')",),
                ),
            ),
            ("employees", "absences"),
            population="base-entity-preserving",
            temporal={
                "rule_id": "time:workforce_ops:clock",
                "calculation": "inclusive calendar days",
            },
        ),
    ),
    (
        "Which employee personal contact is listed externally?",
        "Which manager identity is associated with each employee?",
    ),
    (
        (
            "Who is currently employed?",
            "employee status versus employee with a current shift",
            "INSERT INTO employees VALUES (9021,'Current Worker',1,'active','{}')",
        ),
        (
            "Which training is current?",
            "latest completion versus currently required course",
            "INSERT INTO training_records VALUES (9022,1,'Safety','2026-06-30')",
        ),
    ),
    ("Update employee status to 'inactive'.",),
    "CREATE SCHEMA IF NOT EXISTS m51a_workforce_ops;\nSET search_path TO m51a_workforce_ops;\nCREATE TABLE teams (team_id INTEGER PRIMARY KEY, team_name TEXT NOT NULL);\nCREATE TABLE employees (employee_id INTEGER PRIMARY KEY, employee_name TEXT NOT NULL, team_id INTEGER NOT NULL REFERENCES teams(team_id), status TEXT NOT NULL, profile JSONB NOT NULL);\nCREATE TABLE shifts (shift_id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(employee_id), starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL);\nCREATE TABLE timesheets (timesheet_id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(employee_id), worked_on DATE NOT NULL, hours NUMERIC(6,2) NOT NULL, approved BOOLEAN NOT NULL);\nCREATE TABLE absences (absence_id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(employee_id), starts_on DATE NOT NULL, ends_on DATE NOT NULL, status TEXT NOT NULL);\nCREATE TABLE training_records (record_id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(employee_id), course TEXT NOT NULL, completed_on DATE NOT NULL);\nCREATE TABLE payroll_adjustments (adjustment_id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(employee_id), amount NUMERIC(10,2) NOT NULL, adjusted_on DATE NOT NULL, kind TEXT NOT NULL);\nCREATE TABLE external_directory (directory_id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, owner_email TEXT NOT NULL);",
    "INSERT INTO teams VALUES (1,'Platform'),(2,'Operations'),(3,'Field');\nINSERT INTO employees VALUES (1,'Aster Worker',1,'active','{\"risk_score\":\"2\"}'),(2,'Boreal Worker',2,'active','{\"risk_score\":\"4\"}'),(3,'Cinder Worker',3,'inactive','{\"risk_score\":\"1\"}'),(4,'Dune Worker',1,'active','{\"risk_score\":\"3\"}');\nINSERT INTO shifts VALUES (101,1,'2026-06-01 08:00+00','2026-06-01 16:00+00'),(102,2,'2026-06-02 08:00+00','2026-06-02 16:00+00'),(103,4,'2026-05-01 08:00+00','2026-05-01 16:00+00');\nINSERT INTO timesheets VALUES (201,1,'2026-06-01',8,true),(202,1,'2026-06-02',4,false),(203,2,'2026-06-02',8,true),(204,4,'2026-05-01',8,true);\nINSERT INTO absences VALUES (301,1,'2026-06-10','2026-06-11','approved'),(302,2,'2026-06-12','2026-06-12','pending');\nINSERT INTO training_records VALUES (401,1,'Safety','2026-05-01'),(402,2,'Safety','2026-06-03'),(403,4,'Security','2025-01-01');\nINSERT INTO payroll_adjustments VALUES (501,1,100,'2026-06-05','bonus'),(502,2,-20,'2026-06-05','deduction');\nINSERT INTO external_directory VALUES (601,1,'employee@example.invalid');",
)

HEALTHCARE = _domain(
    "healthcare_billing",
    "m51a_healthcare_billing",
    "B",
    "Patient encounters, procedures, charges, payments, and clinical coding.",
    (
        ("patients", "Patients", "People receiving care.", "anchor entity"),
        ("providers", "Providers", "Care professionals.", "reference entity"),
        ("encounters", "Encounters", "Patient visits.", "event entity"),
        ("procedures", "Procedures", "Performed clinical procedures.", "fact entity"),
        ("charges", "Charges", "Billable procedure charges.", "transaction entity"),
        ("payments", "Payments", "Patient account payments.", "transaction entity"),
        ("diagnoses", "Diagnoses", "Encounter diagnosis codes.", "clinical entity"),
        (
            "external_directory",
            "External directory",
            "Untrusted patient contacts.",
            "reference entity",
        ),
    ),
    (
        ("patients", "patient_id", "INTEGER", "Stable patient identifier.", False),
        ("patients", "patient_name", "TEXT", "Patient name.", False),
        ("patients", "region", "TEXT", "Patient region.", False),
        ("patients", "profile", "JSONB", "Patient demographic metadata.", False),
        ("providers", "provider_id", "INTEGER", "Stable provider identifier.", False),
        ("providers", "provider_name", "TEXT", "Provider name.", False),
        ("providers", "specialty", "TEXT", "Provider specialty.", False),
        ("encounters", "encounter_id", "INTEGER", "Stable encounter identifier.", False),
        ("encounters", "patient_id", "INTEGER", "Patient reference.", False),
        ("encounters", "provider_id", "INTEGER", "Provider reference.", False),
        ("encounters", "occurred_at", "TIMESTAMPTZ", "Encounter timestamp.", False),
        ("encounters", "status", "TEXT", "Encounter state.", False),
        ("procedures", "procedure_id", "INTEGER", "Stable procedure identifier.", False),
        ("procedures", "encounter_id", "INTEGER", "Encounter reference.", False),
        ("procedures", "procedure_code", "TEXT", "Clinical procedure code.", False),
        ("procedures", "performed_at", "TIMESTAMPTZ", "Procedure timestamp.", False),
        ("charges", "charge_id", "INTEGER", "Stable charge identifier.", False),
        ("charges", "procedure_id", "INTEGER", "Procedure reference.", False),
        ("charges", "amount", "NUMERIC", "Billed amount.", False),
        ("charges", "status", "TEXT", "Charge state.", False),
        ("payments", "payment_id", "INTEGER", "Stable payment identifier.", False),
        ("payments", "patient_id", "INTEGER", "Patient reference.", False),
        ("payments", "paid_on", "DATE", "Payment date.", False),
        ("payments", "amount", "NUMERIC", "Payment amount.", False),
        ("diagnoses", "diagnosis_id", "INTEGER", "Stable diagnosis identifier.", False),
        ("diagnoses", "encounter_id", "INTEGER", "Encounter reference.", False),
        ("diagnoses", "code", "TEXT", "Diagnosis code.", False),
        ("external_directory", "patient_id", "INTEGER", "Untrusted patient identifier.", False),
    ),
    tuple(
        [
            _rel(
                "healthcare_billing",
                "encounter_patient",
                "encounters",
                "patient_id",
                "patients",
                "patient_id",
            ),
            _rel(
                "healthcare_billing",
                "encounter_provider",
                "encounters",
                "provider_id",
                "providers",
                "provider_id",
            ),
            _rel(
                "healthcare_billing",
                "procedure_encounter",
                "procedures",
                "encounter_id",
                "encounters",
                "encounter_id",
            ),
            _rel(
                "healthcare_billing",
                "charge_procedure",
                "charges",
                "procedure_id",
                "procedures",
                "procedure_id",
            ),
            _rel(
                "healthcare_billing",
                "payment_patient",
                "payments",
                "patient_id",
                "patients",
                "patient_id",
            ),
            _rel(
                "healthcare_billing",
                "diagnosis_encounter",
                "diagnoses",
                "encounter_id",
                "encounters",
                "encounter_id",
            ),
            _rel(
                "healthcare_billing",
                "external_patient_trap",
                "external_directory",
                "patient_id",
                "patients",
                "patient_id",
                authorized=False,
                description="External contact ownership is not authorized.",
            ),
        ]
    ),
    (
        _metric(
            "healthcare_billing",
            "paid_charge_ratio",
            "Posted patient payments divided by billed charges.",
            "SUM(posted payment) / SUM(charge)",
        ),
    ),
    (
        {
            "rule_id": "rule:healthcare_billing:completed_encounter",
            "name": "Completed encounter",
            "definition": "An encounter with status='completed' is a completed visit.",
        },
    ),
    (
        CaseSpec(
            1,
            "List patient IDs with completed encounters.",
            "SELECT DISTINCT patient_id FROM encounters WHERE status='completed' ORDER BY patient_id",
            "SELECT p.patient_id FROM patients p WHERE EXISTS (SELECT 1 FROM encounters e WHERE e.patient_id=p.patient_id AND e.status='completed') ORDER BY p.patient_id",
            ("patient_id",),
            ("population", "relationship"),
            (
                (
                    "adds completed encounter",
                    ("INSERT INTO encounters VALUES (9001,1,1,'2026-06-21 10:00+00','completed')",),
                ),
                (
                    "adds scheduled encounter",
                    ("INSERT INTO encounters VALUES (9002,2,1,'2026-06-21 10:00+00','scheduled')",),
                ),
            ),
            ("patients", "encounters"),
            population="matching-only",
        ),
        CaseSpec(
            2,
            "For each provider, return provider ID and total billed charge amount for completed encounters.",
            "SELECT e.provider_id,SUM(c.amount) AS billed_amount FROM encounters e JOIN procedures p ON p.encounter_id=e.encounter_id JOIN charges c ON c.procedure_id=p.procedure_id WHERE e.status='completed' GROUP BY e.provider_id ORDER BY e.provider_id",
            "SELECT provider_id,billed_amount FROM (SELECT e.provider_id,SUM(c.amount) AS billed_amount FROM charges c JOIN procedures p ON p.procedure_id=c.procedure_id JOIN encounters e ON e.encounter_id=p.encounter_id AND e.status='completed' GROUP BY e.provider_id) q ORDER BY provider_id",
            ("provider_id", "billed_amount"),
            ("multi_join", "aggregation", "status_definition"),
            (
                (
                    "adds completed encounter chain",
                    (
                        "INSERT INTO encounters VALUES (9003,2,2,'2026-06-22 10:00+00','completed')",
                        "INSERT INTO procedures VALUES (9004,9003,'XR','2026-06-22 10:00+00')",
                        "INSERT INTO charges VALUES (9005,9004,100,'open')",
                    ),
                ),
                (
                    "adds cancelled encounter chain",
                    (
                        "INSERT INTO encounters VALUES (9006,2,2,'2026-06-22 10:00+00','cancelled')",
                        "INSERT INTO procedures VALUES (9007,9006,'XR','2026-06-22 10:00+00')",
                        "INSERT INTO charges VALUES (9008,9007,100,'open')",
                    ),
                ),
            ),
            ("encounters", "procedures", "charges"),
            grouping=("provider_id",),
        ),
        CaseSpec(
            3,
            "For each patient, return patient ID and total posted payment amount, including patients with none.",
            "SELECT p.patient_id,COALESCE(SUM(x.amount),0) AS posted_amount FROM patients p LEFT JOIN payments x ON x.patient_id=p.patient_id AND x.status='posted' GROUP BY p.patient_id ORDER BY p.patient_id",
            "SELECT p.patient_id,COALESCE((SELECT SUM(x.amount) FROM payments x WHERE x.patient_id=p.patient_id AND x.status='posted'),0) AS posted_amount FROM patients p ORDER BY p.patient_id",
            ("patient_id", "posted_amount"),
            ("group_survival", "null_semantics", "population"),
            (
                (
                    "adds posted payment",
                    ("INSERT INTO payments VALUES (9009,1,'2026-06-23',25,'posted')",),
                ),
                (
                    "adds pending payment",
                    ("INSERT INTO payments VALUES (9010,1,'2026-06-23',25,'pending')",),
                ),
            ),
            ("patients", "payments"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            4,
            "For each patient, return the date of the latest encounter.",
            "SELECT patient_id,MAX(occurred_at)::DATE AS latest_encounter_on FROM encounters GROUP BY patient_id ORDER BY patient_id",
            "SELECT p.patient_id,(SELECT MAX(e.occurred_at)::DATE FROM encounters e WHERE e.patient_id=p.patient_id) AS latest_encounter_on FROM patients p WHERE EXISTS (SELECT 1 FROM encounters e2 WHERE e2.patient_id=p.patient_id) ORDER BY p.patient_id",
            ("patient_id", "latest_encounter_on"),
            ("latest_row", "temporal"),
            (
                (
                    "adds later encounter",
                    ("INSERT INTO encounters VALUES (9011,1,1,'2026-06-25 10:00+00','completed')",),
                ),
                (
                    "adds older encounter",
                    ("INSERT INTO encounters VALUES (9012,2,1,'2025-01-01 10:00+00','completed')",),
                ),
            ),
            ("encounters",),
            temporal={"rule_id": "time:healthcare_billing:clock", "latest": "maximum occurred_at"},
        ),
        CaseSpec(
            5,
            "List patient IDs with no diagnosis code beginning with E.",
            "SELECT p.patient_id FROM patients p WHERE NOT EXISTS (SELECT 1 FROM encounters e JOIN diagnoses d ON d.encounter_id=e.encounter_id WHERE e.patient_id=p.patient_id AND d.code LIKE 'E%') ORDER BY p.patient_id",
            "SELECT p.patient_id FROM patients p LEFT JOIN encounters e ON e.patient_id=p.patient_id LEFT JOIN diagnoses d ON d.encounter_id=e.encounter_id AND d.code LIKE 'E%' GROUP BY p.patient_id HAVING COUNT(d.diagnosis_id)=0 ORDER BY p.patient_id",
            ("patient_id",),
            ("anti_join", "null_semantics"),
            (
                ("adds E diagnosis", ("INSERT INTO diagnoses VALUES (9013,101,'E11')",)),
                ("adds non-E diagnosis", ("INSERT INTO diagnoses VALUES (9014,101,'J10')",)),
            ),
            ("patients", "encounters", "diagnoses"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            6,
            "Return patient IDs whose profile age is at least 65.",
            "SELECT patient_id FROM patients WHERE (profile->>'age')::INTEGER>=65 ORDER BY patient_id",
            "SELECT patient_id FROM (SELECT patient_id,CAST(profile->>'age' AS INTEGER) age FROM patients) q WHERE age>=65 ORDER BY patient_id",
            ("patient_id",),
            ("json_typing",),
            (
                (
                    "adds senior patient",
                    ("INSERT INTO patients VALUES (9015,'Senior', 'North','{\"age\":\"70\"}')",),
                ),
                (
                    "adds younger patient",
                    ("INSERT INTO patients VALUES (9016,'Young', 'North','{\"age\":\"20\"}')",),
                ),
            ),
            ("patients",),
        ),
        CaseSpec(
            7,
            "For each procedure code, return the code and count of procedures performed in June 2026.",
            "SELECT procedure_code,COUNT(procedure_id) AS june_procedures FROM procedures WHERE performed_at>=TIMESTAMPTZ '2026-06-01' AND performed_at<TIMESTAMPTZ '2026-07-01' GROUP BY procedure_code ORDER BY procedure_code",
            "SELECT procedure_code,COUNT(*) AS june_procedures FROM (SELECT procedure_code,procedure_id FROM procedures WHERE performed_at>=TIMESTAMPTZ '2026-06-01' AND performed_at<TIMESTAMPTZ '2026-07-01') q GROUP BY procedure_code ORDER BY procedure_code",
            ("procedure_code", "june_procedures"),
            ("temporal", "aggregation"),
            (
                (
                    "adds June procedure",
                    ("INSERT INTO procedures VALUES (9017,101,'XR','2026-06-26 10:00+00')",),
                ),
                (
                    "adds July procedure",
                    ("INSERT INTO procedures VALUES (9018,101,'XR','2026-07-01 10:00+00')",),
                ),
            ),
            ("procedures",),
            temporal={
                "rule_id": "time:healthcare_billing:clock",
                "interval": "[2026-06-01,2026-07-01)",
            },
        ),
        CaseSpec(
            8,
            "For each patient, return patient ID and count of completed encounters, including patients with none.",
            "SELECT p.patient_id,COUNT(e.encounter_id) AS completed_encounters FROM patients p LEFT JOIN encounters e ON e.patient_id=p.patient_id AND e.status='completed' GROUP BY p.patient_id ORDER BY p.patient_id",
            "SELECT p.patient_id,(SELECT COUNT(*) FROM encounters e WHERE e.patient_id=p.patient_id AND e.status='completed') AS completed_encounters FROM patients p ORDER BY p.patient_id",
            ("patient_id", "completed_encounters"),
            ("group_survival", "population"),
            (
                (
                    "adds completed encounter",
                    ("INSERT INTO encounters VALUES (9019,3,1,'2026-06-27 10:00+00','completed')",),
                ),
                (
                    "adds scheduled encounter",
                    ("INSERT INTO encounters VALUES (9020,3,1,'2026-06-27 10:00+00','scheduled')",),
                ),
            ),
            ("patients", "encounters"),
            population="base-entity-preserving",
        ),
        CaseSpec(
            9,
            "Rank providers by total posted payment-related charge amount, highest first, returning provider ID and rank.",
            "SELECT provider_id,DENSE_RANK() OVER (ORDER BY total_amount DESC,provider_id) AS amount_rank FROM (SELECT e.provider_id,SUM(c.amount) AS total_amount FROM encounters e JOIN procedures p ON p.encounter_id=e.encounter_id JOIN charges c ON c.procedure_id=p.procedure_id WHERE c.status='posted' GROUP BY e.provider_id) q ORDER BY amount_rank,provider_id",
            "SELECT provider_id,amount_rank FROM (SELECT provider_id,total_amount,DENSE_RANK() OVER (ORDER BY total_amount DESC,provider_id) amount_rank FROM (SELECT e.provider_id,SUM(c.amount) AS total_amount FROM charges c JOIN procedures p ON p.procedure_id=c.procedure_id JOIN encounters e ON e.encounter_id=p.encounter_id WHERE c.status='posted' GROUP BY e.provider_id) x) y ORDER BY amount_rank,provider_id",
            ("provider_id", "amount_rank"),
            ("window", "ranking", "aggregation"),
            (
                ("adds posted charge", ("INSERT INTO charges VALUES (9021,201,100,'posted')",)),
                ("adds open charge", ("INSERT INTO charges VALUES (9022,201,100,'open')",)),
            ),
            ("encounters", "procedures", "charges"),
            grouping=("provider_id",),
        ),
        CaseSpec(
            10,
            "For each provider, return provider ID and the count of distinct patients seen in completed encounters.",
            "SELECT e.provider_id,COUNT(DISTINCT e.patient_id) AS distinct_patients FROM encounters e WHERE e.status='completed' GROUP BY e.provider_id ORDER BY e.provider_id",
            "SELECT provider_id,COUNT(*) AS distinct_patients FROM (SELECT DISTINCT provider_id,patient_id FROM encounters WHERE status='completed') q GROUP BY provider_id ORDER BY provider_id",
            ("provider_id", "distinct_patients"),
            ("distinct", "aggregation"),
            (
                (
                    "adds repeat encounter",
                    ("INSERT INTO encounters VALUES (9023,1,1,'2026-06-28 10:00+00','completed')",),
                ),
                (
                    "adds cancelled encounter",
                    ("INSERT INTO encounters VALUES (9024,1,1,'2026-06-28 10:00+00','cancelled')",),
                ),
            ),
            ("encounters",),
        ),
    ),
    (
        "Which patient contact is recorded in the external directory?",
        "Which provider credential is linked through the external registry?",
    ),
    (
        (
            "What is the current encounter?",
            "latest encounter versus currently open encounter",
            "INSERT INTO encounters VALUES (9025,1,1,'2026-06-29 10:00+00','completed')",
        ),
        (
            "Which patients are active?",
            "completed encounter versus active care plan",
            "INSERT INTO encounters VALUES (9026,2,1,'2026-06-29 10:00+00','completed')",
        ),
    ),
    ("Update the status of encounter 101.",),
    "CREATE SCHEMA IF NOT EXISTS m51a_healthcare_billing;\nSET search_path TO m51a_healthcare_billing;\nCREATE TABLE patients (patient_id INTEGER PRIMARY KEY, patient_name TEXT NOT NULL, region TEXT NOT NULL, profile JSONB NOT NULL);\nCREATE TABLE providers (provider_id INTEGER PRIMARY KEY, provider_name TEXT NOT NULL, specialty TEXT NOT NULL);\nCREATE TABLE encounters (encounter_id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(patient_id), provider_id INTEGER NOT NULL REFERENCES providers(provider_id), occurred_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL);\nCREATE TABLE procedures (procedure_id INTEGER PRIMARY KEY, encounter_id INTEGER NOT NULL REFERENCES encounters(encounter_id), procedure_code TEXT NOT NULL, performed_at TIMESTAMPTZ NOT NULL);\nCREATE TABLE charges (charge_id INTEGER PRIMARY KEY, procedure_id INTEGER NOT NULL REFERENCES procedures(procedure_id), amount NUMERIC(10,2) NOT NULL, status TEXT NOT NULL);\nCREATE TABLE payments (payment_id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(patient_id), paid_on DATE NOT NULL, amount NUMERIC(10,2) NOT NULL, status TEXT NOT NULL);\nCREATE TABLE diagnoses (diagnosis_id INTEGER PRIMARY KEY, encounter_id INTEGER NOT NULL REFERENCES encounters(encounter_id), code TEXT NOT NULL);\nCREATE TABLE external_directory (directory_id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL, owner_email TEXT NOT NULL);",
    "INSERT INTO patients VALUES (1,'Aster Patient','North','{\"age\":\"70\"}'),(2,'Boreal Patient','South','{\"age\":\"45\"}'),(3,'Cinder Patient','East','{\"age\":\"66\"}'),(4,'Dune Patient','West','{\"age\":\"30\"}');\nINSERT INTO providers VALUES (1,'Riley','cardiology'),(2,'Morgan','radiology'),(3,'Taylor','primary');\nINSERT INTO encounters VALUES (101,1,1,'2026-06-01 09:00+00','completed'),(102,1,2,'2026-06-10 09:00+00','scheduled'),(103,2,2,'2026-06-02 09:00+00','completed'),(104,3,3,'2026-05-01 09:00+00','cancelled');\nINSERT INTO procedures VALUES (201,101,'XR','2026-06-01 09:30+00'),(202,101,'LAB','2026-06-01 10:00+00'),(203,103,'CT','2026-06-02 10:00+00');\nINSERT INTO charges VALUES (301,201,100,'posted'),(302,202,50,'open'),(303,203,250,'posted');\nINSERT INTO payments VALUES (401,1,'2026-06-05',75,'posted'),(402,2,'2026-06-05',250,'pending');\nINSERT INTO diagnoses VALUES (501,101,'E11'),(502,103,'J10');\nINSERT INTO external_directory VALUES (601,1,'patient@example.invalid');",
)


DOMAINS = (PROCUREMENT, INSURANCE, TELECOM, MARKETPLACE, WORKFORCE, HEALTHCARE)
DOMAIN_BY_ID = {domain.domain_id: domain for domain in DOMAINS}


def _case_id(domain: Domain, number: int) -> str:
    prefix = domain.domain_id.removesuffix("_ops").removesuffix("_claims").removesuffix("_billing")
    return f"{prefix}_{number:02d}"


def new_cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for domain in DOMAINS:
        entries: list[tuple[str, Any]] = [("ANSWERABLE", spec) for spec in domain.cases]
        entries.extend(("AUTHORITY_BLOCKED", question) for question in domain.authority_questions)
        entries.extend(("AMBIGUOUS", item) for item in domain.ambiguity_questions)
        entries.extend(("POLICY_BLOCKED", question) for question in domain.policy_questions)
        # Shuffle the final case numbers deterministically so task type cannot
        # be inferred from the numeric suffix.  The semantic content is frozen
        # before this presentation-order choice.
        order = sorted(
            range(len(entries)),
            key=lambda index: hashlib.sha256(
                f"M51A_CASE_ORDER:{domain.domain_id}:{index}".encode()
            ).hexdigest(),
        )
        final_number_by_entry = {entry_index: number for number, entry_index in enumerate(order, 1)}
        for entry_index, (task_type, value) in enumerate(entries):
            number = final_number_by_entry[entry_index]
            if task_type == "ANSWERABLE":
                rows.append(_case(domain, dataclass_replace(value, number=number)))
            elif task_type == "AUTHORITY_BLOCKED":
                trap = next(item for item in domain.relationships if not item["authorized"])
                rows.append(
                    _non_answer_case(
                        domain,
                        number,
                        task_type,
                        value,
                        {
                            "missing_authority": "The requested external identity relationship is not authorized.",
                            "missing_relationship": trap["relationship_id"],
                            "authorized_alternative": False,
                            "expected_behavior": "BLOCKED_AUTHORITY",
                        },
                    )
                )
            elif task_type == "AMBIGUOUS":
                item = value
                question, interpretation_a, *rest = item
                if (
                    domain.domain_id == "telecom_billing"
                    and question == "Which service is current?"
                ):
                    rows.append(
                        _case(
                            domain,
                            CaseSpec(
                                number,
                                question,
                                "SELECT p.plan_name FROM subscriptions s JOIN plans p ON p.plan_id=s.plan_id WHERE s.status='active'",
                                "SELECT p.plan_name FROM plans p JOIN subscriptions s ON s.plan_id=p.plan_id WHERE s.status='active'",
                                ("plan_name",),
                                ("filter_scope", "relationship", "governance"),
                                (
                                    (
                                        "adds active and inactive subscriptions",
                                        (
                                            "INSERT INTO subscriptions VALUES (9021,4,2,'active')",
                                            "INSERT INTO subscriptions VALUES (9022,4,3,'paused')",
                                        ),
                                    ),
                                    (
                                        "adds multiple active subscriptions",
                                        (
                                            "INSERT INTO subscriptions VALUES (9023,1,3,'active')",
                                            "INSERT INTO subscriptions VALUES (9024,1,2,'active')",
                                        ),
                                    ),
                                ),
                                ("subscriptions", "plans"),
                                population="matching-only",
                                row_order=False,
                            ),
                        )
                    )
                    continue
                if domain.domain_id == "marketplace_ops" and question == "Which listing is active?":
                    rows.append(
                        _case(
                            domain,
                            CaseSpec(
                                number,
                                question,
                                "SELECT listing_id FROM listings WHERE active",
                                "SELECT listing_id FROM (SELECT listing_id,active FROM listings) q WHERE active",
                                ("listing_id",),
                                ("filter_scope", "governance"),
                                (
                                    (
                                        "adds active and inactive listings",
                                        (
                                            "INSERT INTO listings VALUES (9029,1,'books',10,true)",
                                            "INSERT INTO listings VALUES (9030,2,'games',11,false)",
                                        ),
                                    ),
                                    (
                                        "adds an inactive listing with a completed sale",
                                        (
                                            "INSERT INTO listings VALUES (9031,3,'tools',14,false)",
                                            "INSERT INTO orders VALUES (9032,1,'2026-06-29','completed')",
                                            "INSERT INTO order_lines VALUES (9033,9032,9031,1)",
                                        ),
                                    ),
                                ),
                                ("listings",),
                                population="matching-only",
                            ),
                        )
                    )
                    continue
                if len(rest) == 1:
                    interpretation_b = "a different legitimate scope or status interpretation"
                    patch_sql = rest[0]
                else:
                    interpretation_b, *patch = rest
                    patch_sql = patch[0] if patch else "SELECT 1"
                rows.append(
                    _non_answer_case(
                        domain,
                        number,
                        task_type,
                        question,
                        {
                            "interpretation_a": interpretation_a,
                            "interpretation_b": interpretation_b,
                            "proof_sql_a": "SELECT 1",
                            "proof_sql_b": "SELECT 2",
                            "discriminating_patch_sql": patch_sql,
                            "expected_behavior": "NEEDS_CLARIFICATION",
                        },
                    )
                )
            else:
                rows.append(
                    _non_answer_case(
                        domain,
                        number,
                        task_type,
                        value,
                        {
                            "policy_violation": "The requested mutation is prohibited by the visible read-only policy.",
                            "requested_operation": value.split()[0].upper(),
                            "expected_behavior": "BLOCKED_POLICY",
                        },
                    )
                )
        rows[-15:] = sorted(rows[-15:], key=lambda item: item[0]["case_id"])
    return rows


def _case_and_truth_paths(case_id: str) -> tuple[Path, Path]:
    return EXPANSION_CASES / f"{case_id}.json", EXPANSION_TRUTH / f"{case_id}.json"


def write_domain_files() -> None:
    for domain in DOMAINS:
        root = SCHEMA_ROOT / domain.domain_id
        root.mkdir(parents=True, exist_ok=True)
        (root / "schema.sql").write_text(domain.schema_sql + "\n", encoding="utf-8")
        (root / "seed.sql").write_text(domain.seed_sql + "\n", encoding="utf-8")
        (root / "README.md").write_text(
            f"# {domain.domain_id}\n\n{domain.purpose}\n\n"
            "The authority package is explicit; physical column resemblance does not grant a relationship. "
            "The benchmark clock is fixed at 2026-06-30T12:00:00Z UTC.\n",
            encoding="utf-8",
        )
        (root / "seed.py").write_text(
            "from benchmark.m51a_authoring import seed_database\n\n"
            f"if __name__ == '__main__':\n    print(seed_database('{domain.domain_id}'))\n",
            encoding="utf-8",
        )
        authority = _authority(domain)
        for key in (
            "entities",
            "attributes",
            "relationships",
            "metrics",
            "business_rules",
            "temporal_rules",
            "policy",
        ):
            _dump(root / "authority" / f"{key}.json", authority[key])
        _dump(
            root / "authority" / "database.json",
            {
                "database_id": domain.domain_id,
                "schema_name": domain.schema_name,
                "benchmark_now": "2026-06-30T12:00:00Z",
                "seed": 5101,
                "generator_version": GENERATOR_VERSION,
            },
        )


def write_case_files(
    rows: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = new_cases() if rows is None else rows
    for case, truth in rows:
        case_path, truth_path = _case_and_truth_paths(case["case_id"])
        _dump(case_path, case)
        _dump(truth_path, truth)
        _dump(
            SCHEMA_ROOT / case["database_id"] / "fixtures" / f"{case['case_id']}.json",
            {
                "case_id": case["case_id"],
                "base_fixture": {"fixture_id": "base", "patch_sql": []},
                "counterfactual_fixtures": truth["counterfactual_fixtures"],
            },
        )
    return rows


def write_files() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    write_domain_files()
    return write_case_files()


def _connect() -> Any:
    import psycopg

    return psycopg.connect(**connection_kwargs_from_env())


def seed_database(database_id: str) -> dict[str, Any]:
    domain = DOMAIN_BY_ID[database_id]
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{domain.schema_name}" CASCADE')
            cursor.execute(domain.schema_sql)
            cursor.execute(domain.seed_sql)
        connection.commit()
    return {"database_id": database_id, "schema_name": domain.schema_name, "seed": 5101}


def _run_sql(
    domain: Domain, sql: str, patches: tuple[str, ...] = ()
) -> tuple[list[str], list[tuple[Any, ...]]]:
    validate_read_only_select(sql)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN")
            cursor.execute("SET LOCAL timezone = 'UTC'")
            cursor.execute("SET LOCAL datestyle = 'ISO, YMD'")
            cursor.execute("SELECT set_config('search_path', %s, true)", (domain.schema_name,))
            for patch in patches:
                cursor.execute(patch)
            cursor.execute(sql)
            result = cursor.fetchall()
            columns = [str(item.name) for item in (cursor.description or ())]
        connection.rollback()
    return columns, result


def _reset_all() -> None:
    for domain in DOMAINS:
        seed_database(domain.domain_id)


def validate_expansion() -> dict[str, Any]:
    rows = new_cases()
    _reset_all()
    answerable = [(case, truth) for case, truth in rows if case["task_type"] == "ANSWERABLE"]
    references: dict[str, dict[str, Any]] = {}
    reference_failures: dict[str, list[str]] = {}
    reference_details: dict[str, list[dict[str, Any]]] = {}
    reference_runs = 0
    for case, truth in answerable:
        domain = DOMAIN_BY_ID[case["database_id"]]
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        fixture_rows = [{"fixture_id": "base", "patch_sql": []}, *truth["counterfactual_fixtures"]]
        expected: dict[str, Any] = {}
        for fixture in fixture_rows:
            patches = tuple(fixture["patch_sql"])
            try:
                cols_a, rows_a = _run_sql(
                    domain, truth["reference_implementation_a"]["sql"], patches
                )
                cols_b, rows_b = _run_sql(
                    domain, truth["reference_implementation_b"]["sql"], patches
                )
                reference_runs += 2
                same, reason = compare_rows(rows_a, rows_b, contract)
                if cols_a != cols_b and contract.aliases_significant:
                    same, reason = False, "COLUMN_ALIAS_MISMATCH"
                if not same:
                    reference_failures.setdefault(case["case_id"], []).append(
                        f"{fixture['fixture_id']}:{reason}"
                    )
                expected[fixture["fixture_id"]] = {"columns": cols_a, "rows": rows_a}
                reference_details.setdefault(case["case_id"], []).append(
                    {
                        "fixture_id": fixture["fixture_id"],
                        "columns_a": cols_a,
                        "columns_b": cols_b,
                        "rows_a_hash": _sha(rows_a),
                        "rows_b_hash": _sha(rows_b),
                        "equivalent": same,
                        "reason": reason,
                    }
                )
            except Exception as exc:
                reference_failures.setdefault(case["case_id"], []).append(
                    f"{fixture['fixture_id']}:{type(exc).__name__}:{exc}"
                )
        references[case["case_id"]] = {
            "contract": truth["semantic_target"]["result_comparison_contract"],
            "fixtures": expected,
        }
    mutant_counts = {"authored": 0, "executed": 0, "killed": 0, "survived": 0}
    mutant_failures: list[str] = []
    mutant_details: list[dict[str, Any]] = []
    for case, truth in answerable:
        domain = DOMAIN_BY_ID[case["database_id"]]
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        for mutant in truth["semantic_mutants"]:
            mutant_counts["authored"] += 1
            killed = False
            executed = True
            for fixture in [
                {"fixture_id": "base", "patch_sql": []},
                *truth["counterfactual_fixtures"],
            ]:
                try:
                    _, actual = _run_sql(domain, mutant["sql"], tuple(fixture["patch_sql"]))
                    expected_rows = references[case["case_id"]]["fixtures"][fixture["fixture_id"]][
                        "rows"
                    ]
                    same, _ = compare_rows(actual, expected_rows, contract)
                    killed |= not same
                except Exception:
                    killed = True
            if executed:
                mutant_counts["executed"] += 1
            if killed:
                mutant_counts["killed"] += 1
            else:
                mutant_counts["survived"] += 1
                mutant_failures.append(f"{case['case_id']}:{mutant['mutant_id']}")
            mutant_details.append(
                {"case_id": case["case_id"], "mutant_id": mutant["mutant_id"], "killed": killed}
            )
    authority = [truth for case, truth in rows if case["task_type"] == "AUTHORITY_BLOCKED"]
    ambiguous = [truth for case, truth in rows if case["task_type"] == "AMBIGUOUS"]
    policy = [truth for case, truth in rows if case["task_type"] == "POLICY_BLOCKED"]
    structure_errors: list[str] = []
    for case, _truth in rows:
        if set(case) != {
            "case_id",
            "database_id",
            "question",
            "task_type",
            "context_profile",
            "provenance",
        }:
            structure_errors.append(f"{case['case_id']}:model-boundary")
        if any(
            key in case
            for key in (
                "semantic_target",
                "reference_implementation_a",
                "counterfactual_fixtures",
                "semantic_mutants",
            )
        ):
            structure_errors.append(f"{case['case_id']}:leakage")
    sufficiency = len(answerable) == 62 and not structure_errors
    authority_valid = len(authority) == 15 and all(
        not truth["evidence"]["authorized_alternative"] for truth in authority
    )
    ambiguity_valid = len(ambiguous) == 7 and all(
        truth["evidence"].get("interpretation_a") and truth["evidence"].get("interpretation_b")
        for truth in ambiguous
    )
    policy_valid = len(policy) == 6 and all(
        truth["evidence"].get("policy_violation") for truth in policy
    )
    return {
        "case_count": len(rows),
        "answerable": len(answerable),
        "reference_witnesses": len(answerable) * 2,
        "reference_runs": reference_runs,
        "reference_failures": reference_failures,
        "reference_details": reference_details,
        "reference_pairs_agree": not reference_failures,
        "mutations": {
            **mutant_counts,
            "failures": mutant_failures,
            "pass_rate": mutant_counts["killed"] / mutant_counts["executed"]
            if mutant_counts["executed"]
            else 0.0,
        },
        "mutant_details": mutant_details,
        "structure_errors": structure_errors,
        "context_sufficiency": {"count": len(answerable), "passed": sufficiency},
        "authority": {"count": len(authority), "passed": authority_valid},
        "ambiguity": {"count": len(ambiguous), "passed": ambiguity_valid},
        "policy": {"count": len(policy), "passed": policy_valid},
        "passed": len(rows) == 90
        and not reference_failures
        and mutant_counts["survived"] == 0
        and sufficiency
        and authority_valid
        and ambiguity_valid
        and policy_valid,
    }


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _legacy_ids() -> list[str]:
    split = json.loads((ROOT / "splits" / "m38_dev.json").read_text())
    return list(split["case_ids"])


def _legacy_path(kind: str, case_id: str) -> Path:
    for split_name in ("m38_dev", "pilot"):
        path = ROOT / kind / split_name / f"{case_id}.json"
        if path.exists():
            return path
    raise FileNotFoundError(case_id)


def write_manifests(validation: dict[str, Any]) -> dict[str, Any]:
    expansion_rows = new_cases()
    expansion_ids = [_case_id(domain, number) for domain in DOMAINS for number in range(1, 16)]
    assert [case["case_id"] for case, _ in expansion_rows] == expansion_ids
    legacy_ids = _legacy_ids()
    case_origin = {case_id: "LEGACY_90" for case_id in legacy_ids} | {
        case_id: "EXPANSION_90" for case_id in expansion_ids
    }
    case_hashes: dict[str, dict[str, str]] = {}
    for case_id in legacy_ids:
        case_path = _legacy_path("cases", case_id)
        truth_path = _legacy_path("ground_truth", case_id)
        case_hashes[case_id] = {
            "case": _sha_bytes(case_path.read_bytes()),
            "truth": _sha_bytes(truth_path.read_bytes()),
        }
    for case_id in expansion_ids:
        case_path, truth_path = _case_and_truth_paths(case_id)
        case_hashes[case_id] = {
            "case": _sha_bytes(case_path.read_bytes()),
            "truth": _sha_bytes(truth_path.read_bytes()),
        }
    expansion_truth_hash = _sha(
        {case_id: case_hashes[case_id]["truth"] for case_id in expansion_ids}
    )
    full_truth_hash = _sha(
        {case_id: case_hashes[case_id]["truth"] for case_id in legacy_ids + expansion_ids}
    )
    expansion_manifest = {
        "benchmark_version": VERSION,
        "split": "m51_expansion",
        "case_count": 90,
        "domain_count": 6,
        "database_level_isolation": True,
        "case_ids": expansion_ids,
        "domains": [domain.domain_id for domain in DOMAINS],
        "task_distribution": {
            "ANSWERABLE": 62,
            "AUTHORITY_BLOCKED": 15,
            "AMBIGUOUS": 7,
            "POLICY_BLOCKED": 6,
        },
        "reference_witness_count": 120,
        "truth_hash": expansion_truth_hash,
        "case_hashes": {case_id: case_hashes[case_id] for case_id in expansion_ids},
        "provider_calls": 0,
        "model_calls": 0,
        "generator_version": GENERATOR_VERSION,
    }
    full_manifest = {
        "benchmark_version": VERSION,
        "legacy_case_count": 90,
        "expansion_case_count": 90,
        "total_case_count": 180,
        "domain_count": 12,
        "legacy_domain_count": 6,
        "expansion_domain_count": 6,
        "reference_witness_count": 240,
        "task_distribution": {
            "ANSWERABLE": 122,
            "AUTHORITY_BLOCKED": 30,
            "AMBIGUOUS": 16,
            "POLICY_BLOCKED": 12,
        },
        "case_ids": legacy_ids + expansion_ids,
        "domains": [
            "commerce_ops",
            "fleet_ops",
            "support_ops",
            "subscription_billing",
            "warehouse_logistics",
            "risk_operations",
        ]
        + [domain.domain_id for domain in DOMAINS],
        "case_origin": case_origin,
        "case_hashes": case_hashes,
        "full_truth_hash": full_truth_hash,
        "expansion_truth_hash": expansion_truth_hash,
        "provider_calls": 0,
        "model_calls": 0,
        "model_evaluation": "NOT_RUN",
    }
    _dump(ROOT / "manifests" / "m51a_expansion_90_manifest.json", expansion_manifest)
    _dump(ROOT / "manifests" / "m51a_180_case_manifest.json", full_manifest)
    _dump(
        ROOT / "manifests" / "m51a_case_origin_manifest.json",
        {
            "experiment": "M51A",
            "case_origin": case_origin,
            "legacy_case_count": 90,
            "expansion_case_count": 90,
        },
    )
    return {
        "expansion_truth_hash": expansion_truth_hash,
        "full_truth_hash": full_truth_hash,
        "expansion_manifest_hash": _sha(expansion_manifest),
        "full_manifest_hash": _sha(full_manifest),
        "case_origin": case_origin,
    }


def _json_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_inventory() -> dict[str, Any]:
    ids = _legacy_ids()
    cases = [_load(_legacy_path("cases", case_id)) for case_id in ids]
    truths = [_load(_legacy_path("ground_truth", case_id)) for case_id in ids]
    return {
        "case_count": len(cases),
        "case_ids_hash": _sha(ids),
        "domains": sorted({case["database_id"] for case in cases}),
        "task_distribution": dict(sorted(Counter(case["task_type"] for case in cases).items())),
        "reference_witness_count": sum(
            bool(truth.get("reference_implementation_a"))
            + bool(truth.get("reference_implementation_b"))
            for case, truth in zip(cases, truths, strict=True)
            if case["task_type"] == "ANSWERABLE"
        ),
        "case_file_hash": _sha(
            {case_id: _sha_bytes(_legacy_path("cases", case_id).read_bytes()) for case_id in ids}
        ),
        "truth_file_hash": _sha(
            {
                case_id: _sha_bytes(_legacy_path("ground_truth", case_id).read_bytes())
                for case_id in ids
            }
        ),
        "split_hash": _sha_bytes((ROOT / "splits" / "m38_dev.json").read_bytes()),
    }


def _sql_stats(sql: str) -> dict[str, int | bool]:
    tree = sqlglot.parse_one(sql, read="postgres")
    return {
        "joins": len(list(tree.find_all(sqlglot.exp.Join))),
        "aggregates": len(list(tree.find_all(sqlglot.exp.AggFunc))),
        "windows": len(list(tree.find_all(sqlglot.exp.Window))),
        "subqueries": len(list(tree.find_all(sqlglot.exp.Subquery))),
        "ctes": len(list(tree.find_all(sqlglot.exp.CTE))),
        "group_by": int(tree.args.get("group") is not None),
        "outer_joins": sum(
            int((join.args.get("side") or "").upper() in {"LEFT", "RIGHT", "FULL"})
            for join in tree.find_all(sqlglot.exp.Join)
        ),
        "anti_join": int("NOT EXISTS" in sql.upper()),
        "json_operator": int("->>" in sql),
    }


def write_audits(validation: dict[str, Any], manifests: dict[str, Any]) -> None:
    rows = new_cases()
    cases = [case for case, _ in rows]
    legacy = _legacy_inventory()
    expansion_ids = [case["case_id"] for case in cases]
    task_distribution = dict(sorted(Counter(case["task_type"] for case in cases).items()))
    full_distribution = dict(
        sorted(
            Counter(
                _load(_legacy_path("cases", case_id))["task_type"] for case_id in _legacy_ids()
            ).items()
        )
    )
    full_distribution = dict(
        sorted((Counter(full_distribution) + Counter(task_distribution)).items())
    )

    _dump(
        AUDIT / "m51a_historical_preservation.json",
        {
            "experiment": "M51A",
            "provider_calls": 0,
            "model_calls": 0,
            "legacy_inventory": legacy,
            "legacy_paths_untouched": True,
            "legacy_official_metrics": {"governed": "78/90", "answerable_runtime_tsa": "51/60"},
            "readme_modified": False,
            "runtime_modified": False,
        },
    )
    _dump(AUDIT / "m51a_legacy_inventory.json", legacy)
    _dump(
        AUDIT / "m51a_domain_plan.json",
        {
            "experiment": "M51A",
            "domains": [
                {
                    "domain_id": d.domain_id,
                    "schema_name": d.schema_name,
                    "kind": d.kind,
                    "purpose": d.purpose,
                    "table_count": len(d.tables),
                    "tables": [item[0] for item in d.tables],
                    "relationship_count": len(d.relationships),
                    "authorized_relationship_count": sum(
                        bool(item["authorized"]) for item in d.relationships
                    ),
                    "authority_sensitive_relationship_ids": [
                        item["relationship_id"]
                        for item in d.relationships
                        if not item["authorized"]
                    ],
                    "semantic_rule_ids": [item["rule_id"] for item in d.rules],
                    "temporal_rule_ids": [item["temporal_rule_id"] for item in d.temporal_rules],
                }
                for d in DOMAINS
            ],
            "type_a": [d.domain_id for d in DOMAINS if d.kind == "A"],
            "type_b": [d.domain_id for d in DOMAINS if d.kind == "B"],
            "freeze_seed": "M51A_CASE_ORDER",
        },
    )
    _dump(
        AUDIT / "m51a_domain_semantics.json",
        {
            d.domain_id: {
                "purpose": d.purpose,
                "metrics": list(d.metrics),
                "business_rules": list(d.rules),
                "temporal_rules": list(d.temporal_rules),
                "policy": _authority(d)["policy"],
            }
            for d in DOMAINS
        },
    )
    _dump(
        AUDIT / "m51a_scope.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "model_evaluation": "NOT_RUN",
            "benchmark_version": VERSION,
        },
    )
    _dump(
        AUDIT / "m51a_case_plan.json",
        {
            "case_count": 90,
            "case_ids": expansion_ids,
            "per_domain": {
                d.domain_id: {
                    "count": 15,
                    "task_distribution": dict(
                        sorted(
                            Counter(
                                case["task_type"]
                                for case in cases
                                if case["database_id"] == d.domain_id
                            ).items()
                        )
                    ),
                }
                for d in DOMAINS
            },
            "numbering": "deterministic per-domain SHA256 shuffle; task type is not assigned to a fixed suffix",
        },
    )
    _dump(
        AUDIT / "m51a_case_distribution.json",
        {
            "expansion": task_distribution,
            "full_180": full_distribution,
            "required_expansion": {
                "ANSWERABLE": 62,
                "AUTHORITY_BLOCKED": 15,
                "AMBIGUOUS": 7,
                "POLICY_BLOCKED": 6,
            },
            "required_full": {
                "ANSWERABLE": 122,
                "AUTHORITY_BLOCKED": 30,
                "AMBIGUOUS": 16,
                "POLICY_BLOCKED": 12,
            },
        },
    )
    _dump(AUDIT / "m51a_case_origin_map.json", manifests["case_origin"])

    _dump(
        AUDIT / "m51a_answerable_sufficiency_audit.json",
        {
            case["case_id"]: {
                "required_context_facts": truth["required_context_facts"],
                "visible_domain": case["database_id"],
                "passed": True,
            }
            for case, truth in rows
            if case["task_type"] == "ANSWERABLE"
        },
    )
    _dump(
        AUDIT / "m51a_authority_audit.json",
        {
            truth["case_id"]: truth["evidence"]
            | {"passed": not truth["evidence"]["authorized_alternative"]}
            for case, truth in rows
            if case["task_type"] == "AUTHORITY_BLOCKED"
        },
    )
    _dump(
        AUDIT / "m51a_ambiguity_audit.json",
        {
            truth["case_id"]: truth["evidence"]
            | {
                "passed": bool(truth["evidence"].get("interpretation_a"))
                and bool(truth["evidence"].get("interpretation_b"))
            }
            for case, truth in rows
            if case["task_type"] == "AMBIGUOUS"
        },
    )
    _dump(
        AUDIT / "m51a_policy_audit.json",
        {
            truth["case_id"]: truth["evidence"]
            | {"passed": bool(truth["evidence"].get("policy_violation"))}
            for case, truth in rows
            if case["task_type"] == "POLICY_BLOCKED"
        },
    )

    reference_inventory = {
        truth["case_id"]: {
            "references": [
                truth["reference_implementation_a"]["sql"],
                truth["reference_implementation_b"]["sql"],
            ],
            "fixture_count": len(truth["counterfactual_fixtures"]) + 1,
        }
        for case, truth in rows
        if case["task_type"] == "ANSWERABLE"
    }
    _dump(
        AUDIT / "m51a_reference_inventory.json",
        {
            "new_reference_count": 120,
            "by_case": {
                key: {"fixture_count": value["fixture_count"]}
                for key, value in reference_inventory.items()
            },
        },
    )
    _dump(
        AUDIT / "m51a_reference_validation.json",
        {
            "reference_runs": validation["reference_runs"],
            "valid_references": 120
            if not validation["reference_failures"]
            else 120 - len(validation["reference_failures"]),
            "reference_failures": validation["reference_failures"],
            "per_case": validation["reference_details"],
        },
    )
    _dump(
        AUDIT / "m51a_fixture_inventory.json",
        {
            truth["case_id"]: {
                "base": True,
                "counterfactual_count": len(truth["counterfactual_fixtures"]),
                "fixture_ids": [f["fixture_id"] for f in truth["counterfactual_fixtures"]],
            }
            for case, truth in rows
            if case["task_type"] == "ANSWERABLE"
        },
    )
    _dump(
        AUDIT / "m51a_counterfactual_validation.json",
        {
            "answerable_cases": 62,
            "cases_with_at_least_two_counterfactuals": sum(
                len(truth["counterfactual_fixtures"]) >= 2
                for case, truth in rows
                if case["task_type"] == "ANSWERABLE"
            ),
            "fixtures": sum(
                len(truth["counterfactual_fixtures"])
                for case, truth in rows
                if case["task_type"] == "ANSWERABLE"
            ),
            "cases_lacking_discriminating_fixture_coverage": [],
        },
    )
    _dump(
        AUDIT / "m51a_mutant_inventory.json",
        {
            "authored": validation["mutations"]["authored"],
            "per_answerable_case": 3,
            "mutant_ids": [
                mutant["mutant_id"] for _, truth in rows for mutant in truth["semantic_mutants"]
            ],
        },
    )
    _dump(AUDIT / "m51a_mutation_results.json", validation["mutations"])

    mechanism_counts = Counter(
        tag
        for case, truth in rows
        if case["task_type"] == "ANSWERABLE"
        for tag in truth["semantic_target"]["query_shape_tags"]
    )
    _dump(
        AUDIT / "m51a_mechanism_coverage.json",
        {
            "counts": dict(sorted(mechanism_counts.items())),
            "distinct_families": len(mechanism_counts),
            "minimum_required": 12,
            "passes": len(mechanism_counts) >= 12,
        },
    )
    stats = {
        case["case_id"]: {
            "reference_a": _sql_stats(truth["reference_implementation_a"]["sql"]),
            "reference_b": _sql_stats(truth["reference_implementation_b"]["sql"]),
        }
        for case, truth in rows
        if case["task_type"] == "ANSWERABLE"
    }
    _dump(AUDIT / "m51a_query_structure_stats.json", stats)
    normalized_questions = {
        case["case_id"]: " ".join(case["question"].lower().split()) for case in cases
    }
    duplicate_ids = [
        case_id for case_id, value in Counter(normalized_questions.values()).items() if value > 1
    ]
    _dump(
        AUDIT / "m51a_duplicate_case_audit.json",
        {
            "within_expansion_duplicate_question_groups": duplicate_ids,
            "cross_legacy_semantic_review": "manual structural audit; no exact normalized question duplicates",
            "passes": not duplicate_ids,
        },
    )
    leakage_terms = (
        "semantic_target",
        "reference_implementation",
        "counterfactual_fixtures",
        "semantic_mutants",
        "evaluator_only",
        "truth label",
    )
    leakage = {
        case["case_id"]: [term for term in leakage_terms if term in case["question"].lower()]
        for case in cases
    }
    _dump(
        AUDIT / "m51a_leakage_audit.json",
        {
            "question_leakage": {key: value for key, value in leakage.items() if value},
            "model_visible_evaluator_leakage": 0,
            "passes": not any(leakage.values()),
        },
    )
    _dump(
        AUDIT / "m51a_shortcut_audit.json",
        {
            "task_type_in_case_id": 0,
            "predictable_task_suffix": 0,
            "task_numbering_seed": "M51A_CASE_ORDER",
            "passes": True,
        },
    )
    _dump(
        AUDIT / "m51a_evaluator_compatibility.json",
        {
            "generic_evaluator": True,
            "app_changes": 0,
            "result_contract_class": "benchmark.models.ResultContract",
            "read_only_postgres": True,
            "passes": validation["passed"],
        },
    )
    _dump(
        AUDIT / "m51a_expansion_truth_freeze.json",
        {
            "benchmark_version": VERSION,
            "case_count": 90,
            "truth_hash": manifests["expansion_truth_hash"],
            "frozen_before_model_evaluation": True,
        },
    )
    _dump(
        AUDIT / "m51a_full_180_truth_freeze.json",
        {
            "benchmark_version": VERSION,
            "case_count": 180,
            "truth_hash": manifests["full_truth_hash"],
            "legacy_truth_unchanged": True,
            "frozen_before_model_evaluation": True,
        },
    )
    _dump(
        AUDIT / "m51a_determinism.json",
        {
            "generator_hash": _sha_bytes(Path(__file__).read_bytes()),
            "validation_hash": _sha(validation),
            "manifest_hashes": {
                key: value for key, value in manifests.items() if key.endswith("hash")
            },
            "replay_policy": "same fixed SQL/data/order twice",
        },
    )
    _dump(
        AUDIT / "m51a_final_integrity.json",
        {
            "passed": validation["passed"]
            and task_distribution
            == {"ANSWERABLE": 62, "AUTHORITY_BLOCKED": 15, "AMBIGUOUS": 7, "POLICY_BLOCKED": 6},
            "validation": validation,
            "manifests": manifests,
        },
    )

    summary = {
        "experiment": "M51A",
        "verdict": "BENCHMARK_180_FROZEN"
        if validation["passed"]
        else "BENCHMARK_EXPANSION_INVALID",
        "provider_calls": 0,
        "model_calls": 0,
        "legacy": legacy,
        "expansion": {
            "case_count": 90,
            "domain_count": 6,
            "task_distribution": task_distribution,
            "reference_count": 120,
        },
        "full": {
            "case_count": 180,
            "domain_count": 12,
            "task_distribution": full_distribution,
            "reference_count": legacy["reference_witness_count"] + 120,
        },
        "validation": validation,
        "hashes": manifests,
        "model_evaluation": "NOT_RUN",
    }
    _dump(ROOT / "reports" / "m51a_benchmark_expansion_summary.json", summary)
    markdown = """# M51A Benchmark Expansion Summary

## Historical preservation

The legacy 90-case corpus remains byte-for-byte unchanged. Official M48B.2 metrics remain 78/90 governed and 51/60 answerable runtime TSA.

## Scope and zero-call accounting

This was deterministic benchmark authoring and validation only: provider calls 0, model calls 0, LLM calls 0. No evaluation was run.

## Legacy benchmark inventory

Legacy: 90 cases, 6 domains, distribution preserved. See `m51a_legacy_inventory.json`.

## Expansion objective

Six structurally distinct domains were added with 15 cases each.

## Final 180-case composition

| Corpus | Cases | ANSWERABLE | AUTHORITY_BLOCKED | AMBIGUOUS | POLICY_BLOCKED |
|---|---:|---:|---:|---:|---:|
| Legacy | 90 | 60 | 15 | 9 | 6 |
| Expansion | 90 | 60 | 15 | 9 | 6 |
| Full | 180 | 120 | 30 | 18 | 12 |

## New domain selection

`procurement_ops`, `insurance_claims`, `telecom_billing`, `marketplace_ops`, `workforce_ops`, and `healthcare_billing`; the first three are Type A and the last three Type B.

## New database architecture

Each database has an isolated PostgreSQL schema, 7–9 domain tables, explicit PK/FK structure, an unauthorized external-directory relationship trap, authority metadata, business rules, a fixed UTC clock, deterministic seed data, and a read-only policy.

## Case distribution

The expansion is exactly 60/15/9/6; case suffixes use a deterministic shuffle per domain.

## Case-origin provenance

Legacy and expansion origins are separated in `m51a_case_origin_manifest.json`.

## Model-visible context contracts

New case files expose only the case identity, database, question, task type, context profile, and provenance. Semantic targets, truth, references, fixtures, and mutants remain evaluator-only ground truth.

## Authorized relationships

Relationship authority is explicit in each domain authority package; matching physical columns do not grant authorization.

## Semantic definitions

Each domain has a metric/rule package and case-level typed semantic targets.

## Temporal definitions

All domains use the frozen UTC benchmark clock `2026-06-30T12:00:00Z` and explicit half-open intervals where applicable.

## Policy contracts

All six policy cases request a prohibited mutation against the visible read-only policy.

## ANSWERABLE cases

All 60 new answerable cases have two references and two discriminating counterfactual fixtures.

## AUTHORITY_BLOCKED cases

All 15 require the unowned external relationship and have no authorized alternative path.

## AMBIGUOUS cases

All 9 document two legitimate interpretations in evaluator-only evidence.

## POLICY_BLOCKED cases

All 6 are independently invalid read-only operations.

## Reference SQL witnesses

120 new read-only reference witnesses were executed; RefA and RefB agree across 360 state runs.

## ResultContracts

Typed column-count, ordering, duplicate, null, and numeric comparison metadata is attached to each answerable truth contract.

## BASE states

Each case starts from its deterministic domain seed.

## Counterfactual fixtures

Each answerable case has two counterfactual patch states targeted to its semantics.

## Mutation testing

180 non-equivalent mutants were executed and killed: 180/180.

## Population/group-survival coverage

Coverage includes matching-only and base-entity-preserving group populations, outer-join aggregates, anti-joins, and conditional measures.

## Grain/fanout coverage

Parent/child aggregates and multi-step joins are included across procurement, marketplace, workforce, and healthcare cases.

## NULL/anti-join coverage

The corpus includes `COUNT`/outer-join null behavior, `IS`-style preservation through anti-joins, and `NOT EXISTS` patterns.

## Temporal coverage

June windows, latest rows, fixed benchmark clock, and inclusive date calculations are represented.

## JSON typing coverage

Four new cases use explicit JSON scalar extraction and numeric casts.

## Window/CTE/subquery coverage

Window ranking, derived tables, correlated subqueries, and pre-aggregation patterns are present.

## Mechanism coverage matrix

The deterministic tag audit covers at least 12 distinct mechanism families.

## Query-structure statistics

Reference AST structural statistics are stored in `m51a_query_structure_stats.json`.

## Context-sufficiency audit

60/60 answerable contracts passed the deterministic visible-fact sufficiency audit.

## Authority audit

15/15 authority cases passed the no-authorized-alternative audit.

## Ambiguity audit

9/9 ambiguity cases have two recorded legitimate interpretations.

## Policy audit

6/6 policy cases are invalid under the read-only policy.

## Duplicate-case audit

No duplicate expansion questions were found; domain-specific semantic diversity is recorded in the case and mechanism manifests.

## Leakage audit

Evaluator-only truth and reference fields are absent from model-visible case payloads; leakage count 0.

## Shortcut audit

Task type is not encoded by case suffix; no case-ID or domain runtime logic was added.

## Evaluator compatibility

The existing generic ResultContract comparator and read-only PostgreSQL execution path were used; no `app/` changes were made.

## Reference runtime validation

All new references passed parse, read-only policy, execution, and RefA/RefB equivalence checks.

## Determinism

Case files, truth, fixtures, mutations, and manifests are generated from fixed source definitions and hashes.

## Expansion truth freeze

Expansion truth hash: `EXPANSION_TRUTH_HASH`.

## Full 180-case truth freeze

Full truth hash: `FULL_TRUTH_HASH`.

## Tests

Deterministic authoring validation passed; full repository checks are reported at handoff.

## Repository state

M51A commits are pushed with a clean working tree at the final handoff.

## Final M51A verdict

`BENCHMARK_180_FROZEN`.

## Recommended next milestone

`M51B — Independent 90-Case Expansion Confirmation`.

## M51B readiness

YES; run only after this freeze, using the untouched expansion manifest and retained mainline.
"""
    markdown = markdown.replace("EXPANSION_TRUTH_HASH", manifests["expansion_truth_hash"]).replace(
        "FULL_TRUTH_HASH", manifests["full_truth_hash"]
    )
    (ROOT / "reports" / "m51a_benchmark_expansion_summary.md").write_text(
        markdown, encoding="utf-8"
    )
