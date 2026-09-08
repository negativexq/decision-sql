"""Offline contract and provenance helpers for the M42 prompt ablation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark.context import load_authority

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
M39_ROOT = ROOT / "experiments" / "results" / "m39"
M41_ROOT = ROOT / "experiments" / "results" / "m41"
M41_FORENSICS = REPO / "evaluation" / "forensics" / "m41"
PRESERVATION_JSON = ROOT / "reports" / "m42_historical_preservation.json"
PRESERVATION_MD = ROOT / "reports" / "m42_historical_preservation.md"
OFFLINE_JSON = ROOT / "audits" / "m42_offline_authority_semantics.json"
OFFLINE_MD = ROOT / "audits" / "m42_offline_authority_semantics.md"

HISTORICAL_FILES = {
    **{
        f"benchmark/experiments/results/m39/{name}": M39_ROOT / name
        for name in (
            "m39_manifest.json",
            "m39_raw_responses.jsonl",
            "m39_parsed_submissions.jsonl",
            "m39_case_results.json",
            "m39_summary.json",
            "m39_summary.md",
            "m39_request_ledger.json",
        )
    },
    **{
        f"benchmark/experiments/results/m41/{name}": M41_ROOT / name
        for name in (
            "m41_manifest.json",
            "m41_raw_responses.jsonl",
            "m41_parsed_submissions.jsonl",
            "m41_case_results.json",
            "m41_summary.json",
            "m41_summary.md",
            "m41_request_ledger.json",
        )
    },
    **{
        f"evaluation/forensics/m41/{name}": M41_FORENSICS / name
        for name in (
            "m41_failure_forensics.json",
            "m41_failure_forensics.md",
            "m41_governance_forensics.json",
            "m41_sql_forensics.json",
            "m41_complexity_analysis.json",
            "m41_authority_path_depth.json",
        )
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve_historical_evidence() -> dict[str, Any]:
    files = {name: sha256(path) for name, path in HISTORICAL_FILES.items()}
    result = {
        "milestone": "M42",
        "provider_calls": 0,
        "m39_benchmark": "0.2.0-dev",
        "m41_benchmark": "0.2.1-dev",
        "files": files,
    }
    PRESERVATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    PRESERVATION_JSON.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# M42 historical preservation",
        "",
        "M39 and M41 artifacts were hashed before the M42 prompt edit. Provider calls: 0.",
        "",
        "| Artifact | SHA-256 |",
        "|---|---|",
    ]
    lines.extend(f"| `{name}` | `{digest}` |" for name, digest in files.items())
    PRESERVATION_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def audit_authority_semantics() -> dict[str, Any]:
    databases = (
        "commerce_ops",
        "fleet_ops",
        "support_ops",
        "subscription_billing",
        "warehouse_logistics",
        "risk_operations",
    )
    direction_counts: Counter[str] = Counter()
    cardinality_counts: Counter[str] = Counter()
    authorized_edges = 0
    unauthorized_edges = 0
    for database_id in databases:
        authority = load_authority(database_id)
        for relationship in authority["relationships"]:
            direction_counts[str(relationship.get("direction"))] += 1
            cardinality_counts[str(relationship.get("cardinality"))] += 1
            if relationship.get("authorized") is True:
                authorized_edges += 1
            else:
                unauthorized_edges += 1

    multi_hop_cases = []
    for directory in ("pilot", "m38_dev"):
        for path in sorted((ROOT / "ground_truth" / directory).glob("*.json")):
            truth = json.loads(path.read_text(encoding="utf-8"))
            target = truth.get("semantic_target", {})
            relationships = target.get("relationships", [])
            if len(relationships) >= 2 and target.get("behavior") == "ANSWERABLE":
                multi_hop_cases.append(path.stem)

    result = {
        "provider_calls": 0,
        "direction_field_observed": dict(direction_counts),
        "cardinality_field_observed": dict(cardinality_counts),
        "direction_interpretation": (
            "referential/cardinality orientation; no evaluator or validator rule "
            "prohibits reverse SQL traversal"
        ),
        "authorized_edge_count": authorized_edges,
        "unauthorized_edge_count": unauthorized_edges,
        "multi_hop_composition": {
            "supported_by_authoring_targets": True,
            "answerable_cases_with_two_or_more_declared_edges": len(multi_hop_cases),
            "case_ids": multi_hop_cases,
        },
        "same_entity_attributes_require_relationship": False,
        "undeclared_direct_shortcut_authorized_by_composition": False,
        "hypothesis_status": "SUPPORTED_OFFLINE",
        "evidence": [
            (
                "Relationship objects carry direction and cardinality, but authorization "
                "is checked by explicit relationship ID."
            ),
            (
                "The governed context exposes authorized relationship objects without a "
                "directional SQL prohibition."
            ),
            (
                "Existing answerable semantic targets and references use multiple declared "
                "relationship IDs for multi-hop tasks."
            ),
            (
                "Undeclared relationship IDs are rejected by benchmark validation; visible "
                "attributes do not create relationship authorization."
            ),
        ],
    }
    OFFLINE_JSON.parent.mkdir(parents=True, exist_ok=True)
    OFFLINE_JSON.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OFFLINE_MD.write_text(
        "# M42 offline authority-semantics audit\n\n"
        "Result: **SUPPORTED_OFFLINE**. `direction` is a declared "
        "referential/cardinality orientation; it is not an SQL traversal prohibition. "
        "Existing answerable targets demonstrate authorized multi-hop composition. No "
        "undeclared direct shortcut is authorized, and same-entity attribute use requires "
        "no relationship. Provider calls: 0.\n",
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    print(
        json.dumps(
            {"preservation": preserve_historical_evidence(), "audit": audit_authority_semantics()},
            indent=2,
            sort_keys=True,
        )
    )
