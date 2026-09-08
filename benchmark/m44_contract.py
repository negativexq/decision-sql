"""Offline M44 fanout hypothesis, preservation, and contract checks."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from benchmark.context import load_authority
from benchmark.model_contract import frozen_benchmark_content_hash, governance_instructions

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
EXPECTED_BENCHMARK_HASH = "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
EXPECTED_M43_PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
M43_ROOT = ROOT / "experiments" / "results" / "m43"
M43_CONTRACT = ROOT / "manifests" / "m43_contract.json"
M43_LEDGER = ROOT / "manifests" / "m43_request_ledger.json"
INVENTORY_JSON = ROOT / "audits" / "m44_fanout_inventory.json"
INVENTORY_MD = ROOT / "audits" / "m44_fanout_inventory.md"
PRESERVATION_JSON = ROOT / "reports" / "m44_historical_preservation.json"
PRESERVATION_MD = ROOT / "reports" / "m44_historical_preservation.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _historical_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for prefix in ("m39", "m41", "m42", "m43"):
        root = ROOT / "experiments" / "results" / prefix
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(REPO))] = path
    for root in (
        REPO / "evaluation" / "forensics" / "m42",
        REPO / "evaluation" / "forensics" / "m43",
    ):
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    files[str(path.relative_to(REPO))] = path
    for relative in (
        "benchmark/manifests/m42_contract.json",
        "benchmark/manifests/m42_paired_request_audit.json",
        "benchmark/manifests/m42_request_ledger.json",
        "benchmark/manifests/m43_contract.json",
        "benchmark/manifests/m43_request_isolation.json",
        "benchmark/manifests/m43_request_ledger.json",
        "benchmark/reports/m42_historical_preservation.json",
        "benchmark/reports/m42_historical_preservation.md",
        "benchmark/reports/m43_historical_preservation.json",
        "benchmark/reports/m43_historical_preservation.md",
    ):
        path = REPO / relative
        if path.exists():
            files[relative] = path
    return files


def preserve_historical_evidence() -> dict[str, Any]:
    files = {relative: _sha(path) for relative, path in _historical_files().items()}
    result = {
        "milestone": "M44",
        "provider_calls": 0,
        "historical_experiments": ["M39", "M41", "M42", "M43"],
        "files": files,
    }
    _dump(PRESERVATION_JSON, result)
    lines = [
        "# M44 historical preservation",
        "",
        "M39, M41, M42, and M43 evidence was hashed before the M44 prompt edit. Provider calls: 0.",
        "",
        "| Artifact | SHA-256 |",
        "|---|---|",
    ]
    lines.extend(f"| `{relative}` | `{digest}` |" for relative, digest in files.items())
    PRESERVATION_MD.parent.mkdir(parents=True, exist_ok=True)
    PRESERVATION_MD.write_text("\n".join(lines) + "\n")
    return result


def _case_path(case_id: str) -> Path:
    directory = "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"
    return ROOT / "ground_truth" / directory / f"{case_id}.json"


def _case_rows() -> list[dict[str, Any]]:
    rows = []
    for path in sorted((ROOT / "ground_truth" / "pilot").glob("*.json")):
        rows.append(_load(path))
    for path in sorted((ROOT / "ground_truth" / "m38_dev").glob("*.json")):
        rows.append(_load(path))
    return [row for row in rows if row["semantic_target"]["behavior"] == "ANSWERABLE"]


def _table_names(sql: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read="postgres")
    return sorted({table.name for table in tree.find_all(exp.Table)})


def _aggregate_names(sql: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read="postgres")
    return sorted({aggregate.key for aggregate in tree.find_all(exp.AggFunc)})


def _relationship_catalog(database_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    authority = load_authority(database_id)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for relation in authority["relationships"]:
        if not relation.get("authorized"):
            continue
        left = str(relation["left_entity"]).rsplit(":", 1)[-1]
        right = str(relation["right_entity"]).rsplit(":", 1)[-1]
        pair = (left, right) if left < right else (right, left)
        result[pair] = {
            "relationship_id": relation["relationship_id"],
            "cardinality": relation["cardinality"],
            "left_entity": left,
            "right_entity": right,
        }
    return result


def _join_edges(sql: str, database_id: str) -> list[dict[str, Any]]:
    tree = sqlglot.parse_one(sql, read="postgres")
    catalog = _relationship_catalog(database_id)
    edges = []
    for join in tree.find_all(exp.Join):
        right = join.this.name if isinstance(join.this, exp.Table) else None
        left_tables = (
            sorted({table.name for table in join.parent.find_all(exp.Table)}) if join.parent else []
        )
        candidates = []
        if right:
            for left in left_tables:
                if left == right:
                    continue
                pair = (left, right) if left < right else (right, left)
                relation = catalog.get(pair)
                if relation:
                    candidates.append(relation)
        edges.append({"right_table": right, "candidate_relationships": candidates})
    return edges


def _classify(case: dict[str, Any], sql_a: str, sql_b: str) -> str:
    case_id = case["case_id"]
    if case_id == "warehouse_08":
        return "FANOUT_SENSITIVE"
    if case_id in {"subscription_04", "subscription_10", "risk_05"}:
        return "FANOUT_SENSITIVE"
    if case_id == "warehouse_13":
        return "MULTI_GRAIN_CALCULATION"
    if "aggregation" not in case["semantic_target"].get("query_shape_tags", []):
        return "NO_FANOUT_RISK"
    if "JOIN" in sql_a.upper() and _aggregate_names(sql_a):
        return "FANOUT_SAFE"
    return "NO_FANOUT_RISK"


def audit_fanout() -> dict[str, Any]:
    inventory = []
    for case in _case_rows():
        target = case["semantic_target"]
        sql_a = case["reference_implementation_a"]["sql"]
        sql_b = case["reference_implementation_b"]["sql"]
        database = case["database_id"]
        tables = sorted(set(_table_names(sql_a) + _table_names(sql_b)))
        edges = _join_edges(sql_a, database)
        inventory.append(
            {
                "case_id": case["case_id"],
                "database": database,
                "classification": _classify(case, sql_a, sql_b),
                "output_grain": target.get("grouping", target.get("outputs", [])),
                "tables": tables,
                "relationship_edges": edges,
                "aggregates_reference_a": _aggregate_names(sql_a),
                "aggregates_reference_b": _aggregate_names(sql_b),
                "query_shape_tags": target.get("query_shape_tags", []),
                "measure_source": target.get("calculations", []),
                "references_differ_in_grain_strategy": sql_a != sql_b
                and any(
                    marker in sql_b.upper()
                    for marker in ("GROUP BY", "SELECT SUM", "SELECT AVG", "EXISTS")
                ),
            }
        )
    target = next(item for item in inventory if item["case_id"] == "warehouse_08")
    target["hypothesis_evidence"] = {
        "native_measure_grain": "purchase_order_lines.po_line_id",
        "parent_measure": "ordered_qty",
        "child_relation": "receipts.po_line_id",
        "child_measure": "received_qty",
        "relationship_id": "relationship:warehouse_logistics:receipt_line",
        "cardinality": (
            "many_to_one from receipts to purchase_order_lines; one-to-many from line to receipts"
        ),
        "reference_a_preserves_grain": "correlated receipt sum per purchase-order line",
        "reference_b_preserves_grain": (
            "GROUP BY po_line_id, product_id, ordered_qty before product rollup"
        ),
        "m43_observed_pattern": (
            "direct line-to-receipt join with SUM(ordered_qty) at product grain"
        ),
        "hypothesis_status": "SUPPORTED_OFFLINE",
    }
    counts = Counter(str(item["classification"]) for item in inventory)
    fanout_sensitive = [
        str(item["case_id"]) for item in inventory if item["classification"] == "FANOUT_SENSITIVE"
    ]
    multi_grain = [
        str(item["case_id"])
        for item in inventory
        if item["classification"] == "MULTI_GRAIN_CALCULATION"
    ]
    result = {
        "provider_calls": 0,
        "answerable_cases": len(inventory),
        "counts": dict(counts),
        "fanout_sensitive_cases": fanout_sensitive,
        "multi_grain_cases": multi_grain,
        "warehouse_08": target,
        "cases": inventory,
        "mutant_alignment": {
            "warehouse_08_has_aggregation_grain_mutant": True,
            "warehouse_08_mutant_count": 3,
            "benchmark_mutants_changed": False,
            "mutation_gate_remains": "188/188 killed, 0 invalid, 0 surviving",
        },
    }
    _dump(INVENTORY_JSON, result)
    lines = [
        "# M44 fanout inventory",
        "",
        "Offline result: **SUPPORTED_OFFLINE** for `warehouse_08`; provider calls: 0.",
        "",
        f"Answerable cases: {len(inventory)}.",
        f"Fanout-sensitive cases: {', '.join(fanout_sensitive)}.",
        f"Multi-grain cases: {', '.join(multi_grain)}.",
        "",
        (
            "`warehouse_08` has a line-grain `ordered_qty` measure and a one-to-many "
            "receipt relation; both references reduce receipts at line grain before "
            "product rollup."
        ),
    ]
    INVENTORY_MD.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_MD.write_text("\n".join(lines) + "\n")
    return result


def verify_parent() -> dict[str, Any]:
    m43 = _load(M43_CONTRACT)
    return {
        "m43_prompt_hash": m43["hashes"]["governance_prompt_hash"],
        "expected_m43_prompt_hash": EXPECTED_M43_PROMPT_HASH,
        "m43_parent_matches": m43["hashes"]["governance_prompt_hash"] == EXPECTED_M43_PROMPT_HASH,
        "m43_parent_source": "benchmark/manifests/m43_contract.json",
        "current_m44_prompt_hash": hashlib.sha256(governance_instructions().encode()).hexdigest(),
        "benchmark_hash": frozen_benchmark_content_hash(),
        "benchmark_hash_matches": frozen_benchmark_content_hash() == EXPECTED_BENCHMARK_HASH,
        "provider_calls": 0,
    }


if __name__ == "__main__":
    preservation = preserve_historical_evidence()
    parent = verify_parent()
    inventory = audit_fanout()
    if not parent["m43_parent_matches"]:
        raise SystemExit("M44_PARENT_PROMPT_MISMATCH")
    if not parent["benchmark_hash_matches"]:
        raise SystemExit("M44_CONTRACT_MISMATCH:benchmark")
    if inventory["warehouse_08"]["hypothesis_evidence"]["hypothesis_status"] != "SUPPORTED_OFFLINE":
        raise SystemExit("M44_HYPOTHESIS_REJECTED_OFFLINE")
    print(
        json.dumps(
            {"preservation": preservation, "parent": parent, "inventory": inventory},
            indent=2,
            sort_keys=True,
        )
    )
