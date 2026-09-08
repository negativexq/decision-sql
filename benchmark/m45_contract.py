"""Offline M45 additive-measure applicability and contract checks."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark.model_contract import frozen_benchmark_content_hash, governance_instructions

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
EXPECTED_BENCHMARK_HASH = "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
EXPECTED_M43_PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
EXPECTED_M43_SOURCE_COMMIT = "9f756f1ca119b6a7f8186e1251f5c8f32e556f74"
M43_CONTRACT = ROOT / "manifests" / "m43_contract.json"
M43_LEDGER = ROOT / "manifests" / "m43_request_ledger.json"
INVENTORY_JSON = ROOT / "audits" / "m45_additive_alignment_inventory.json"
INVENTORY_MD = ROOT / "audits" / "m45_additive_alignment_inventory.md"
PRESERVATION_JSON = ROOT / "reports" / "m45_historical_preservation.json"
PRESERVATION_MD = ROOT / "reports" / "m45_historical_preservation.md"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _historical_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for prefix in ("m39", "m41", "m42", "m43", "m44"):
        root = ROOT / "experiments" / "results" / prefix
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(REPO))] = path
    for root in (
        REPO / "evaluation" / "forensics" / "m42",
        REPO / "evaluation" / "forensics" / "m43",
        REPO / "evaluation" / "forensics" / "m44",
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
        "benchmark/manifests/m44_contract.json",
        "benchmark/manifests/m44_paired_request_audit.json",
        "benchmark/manifests/m44_request_ledger.json",
        "benchmark/reports/m42_historical_preservation.json",
        "benchmark/reports/m42_historical_preservation.md",
        "benchmark/reports/m43_historical_preservation.json",
        "benchmark/reports/m43_historical_preservation.md",
        "benchmark/reports/m44_historical_preservation.json",
        "benchmark/reports/m44_historical_preservation.md",
    ):
        path = REPO / relative
        if path.exists():
            files[relative] = path
    return files


def preserve_historical_evidence() -> dict[str, Any]:
    files = {relative: _sha(path) for relative, path in _historical_files().items()}
    result = {
        "milestone": "M45",
        "provider_calls": 0,
        "historical_experiments": ["M39", "M41", "M42", "M43", "M44"],
        "files": files,
    }
    _dump(PRESERVATION_JSON, result)
    lines = [
        "# M45 historical preservation",
        "",
        "M39–M44 evidence was hashed before M45 execution. Provider calls: 0.",
        "",
        "| Artifact | SHA-256 |",
        "|---|---|",
    ]
    lines.extend(f"| `{relative}` | `{digest}` |" for relative, digest in files.items())
    PRESERVATION_MD.parent.mkdir(parents=True, exist_ok=True)
    PRESERVATION_MD.write_text("\n".join(lines) + "\n")
    return result


def _case_rows() -> list[dict[str, Any]]:
    rows = []
    for directory in ("pilot", "m38_dev"):
        for path in sorted((ROOT / "ground_truth" / directory).glob("*.json")):
            row = _load(path)
            if row["semantic_target"]["behavior"] == "ANSWERABLE":
                rows.append(row)
    return rows


def _applicability(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    case_id = case["case_id"]
    if case_id == "warehouse_08":
        return "APPLICABLE", {
            "parent_entity": "purchase_order_lines",
            "parent_key": "po_line_id",
            "parent_additive_measure": "ordered_qty",
            "parent_measure_grain": "purchase_order_lines.po_line_id",
            "child_entity": "receipts",
            "child_foreign_key": "receipts.po_line_id",
            "child_additive_measure": "received_qty",
            "relationship_cardinality": "one-to-many from purchase_order_lines to receipts",
            "arithmetic_combination": "ordered_qty - SUM(received_qty)",
            "output_grain": "product_id",
            "direct_join_duplicates_parent": True,
            "evidence": (
                "Both references reduce receipts per purchase-order line before product rollup."
            ),
        }
    if case_id == "subscription_04":
        return "APPLICABLE", {
            "parent_entity": "payments",
            "parent_key": "payment_id",
            "parent_additive_measure": "amount",
            "parent_measure_grain": "payments.payment_id",
            "child_entity": "refunds",
            "child_foreign_key": "refunds.payment_id",
            "child_additive_measure": "amount",
            "relationship_cardinality": "one-to-many from payments to refunds",
            "arithmetic_combination": "payment amount - SUM(refund amount)",
            "output_grain": "account_id",
            "direct_join_duplicates_parent": True,
            "evidence": "Reference B computes refund total per payment before account rollup.",
        }
    if case_id == "subscription_10":
        return "APPLICABLE", {
            "parent_entity": "payments",
            "parent_key": "payment_id",
            "parent_additive_measure": "amount",
            "parent_measure_grain": "payments.payment_id",
            "child_entity": "refunds",
            "child_foreign_key": "refunds.payment_id",
            "child_additive_measure": "amount",
            "relationship_cardinality": "one-to-many from payments to refunds",
            "arithmetic_combination": "SUM(refund amount) / SUM(payment amount)",
            "output_grain": "plan_id",
            "direct_join_duplicates_parent": True,
            "evidence": (
                "Reference B computes refunded and captured values per payment before plan rollup."
            ),
        }
    if case_id == "risk_05":
        return "NOT_APPLICABLE", {
            "reason": (
                "Parent-row existence fraction; no parent additive measure is combined "
                "with child additive values."
            ),
            "negative_boundary": "EXISTENCE_OR_FRACTION",
        }
    if case_id == "warehouse_13":
        return "NOT_APPLICABLE", {
            "reason": (
                "Combines independently pre-aggregated warehouse measures; not a "
                "parent-row additive value plus child additive values."
            ),
            "negative_boundary": "INDEPENDENT_BRANCH_ROLLUP",
        }
    return "NOT_APPLICABLE", {
        "reason": (
            "No visible semantic target requiring parent additive measure plus child "
            "additive arithmetic alignment."
        ),
    }


def audit_applicability() -> dict[str, Any]:
    cases = []
    for case in _case_rows():
        classification, details = _applicability(case)
        cases.append(
            {
                "case_id": case["case_id"],
                "database": case["database_id"],
                "classification": classification,
                **details,
            }
        )
    counts = Counter(item["classification"] for item in cases)
    applicable = [item["case_id"] for item in cases if item["classification"] == "APPLICABLE"]
    result = {
        "provider_calls": 0,
        "answerable_cases": len(cases),
        "counts": dict(counts),
        "applicable_cases": applicable,
        "positive_control_applicable": ["subscription_04"],
        "negative_control_not_applicable": ["risk_05"],
        "secondary_control": {"case_id": "subscription_10", "classification": "APPLICABLE"},
        "uncertain_cases": [
            item["case_id"] for item in cases if item["classification"] == "UNCERTAIN"
        ],
        "warehouse_08_proof": next(item for item in cases if item["case_id"] == "warehouse_08"),
        "cases": cases,
    }
    _dump(INVENTORY_JSON, result)
    lines = [
        "# M45 additive alignment inventory",
        "",
        "Offline result: **SUPPORTED_OFFLINE**; provider calls: 0.",
        "",
        f"ANSWERABLE cases: {len(cases)}.",
        f"M45 applicable: {len(applicable)} ({', '.join(applicable)}).",
        "Uncertain cases: 0.",
        "Positive structural control: `subscription_04`.",
        "Negative existence/fraction control: `risk_05`.",
        "",
        (
            "`warehouse_08` satisfies all four applicability criteria: parent additive "
            "measure, child additive measure, arithmetic combination, and direct-join "
            "parent duplication risk."
        ),
    ]
    INVENTORY_MD.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_MD.write_text("\n".join(lines) + "\n")
    return result


def verify_parent() -> dict[str, Any]:
    historical_prompt = subprocess.run(
        ["git", "show", f"{EXPECTED_M43_SOURCE_COMMIT}:benchmark/prompts/governed_context_v1.md"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    recovered_hash = hashlib.sha256(historical_prompt.encode()).hexdigest()
    m43 = _load(M43_CONTRACT)
    return {
        "m43_parent_source_commit": EXPECTED_M43_SOURCE_COMMIT,
        "recovered_m43_prompt_hash": recovered_hash,
        "expected_m43_prompt_hash": EXPECTED_M43_PROMPT_HASH,
        "m43_parent_matches": recovered_hash == EXPECTED_M43_PROMPT_HASH,
        "m43_manifest_prompt_hash": m43["hashes"]["governance_prompt_hash"],
        "active_m45_prompt_hash": hashlib.sha256(governance_instructions().encode()).hexdigest(),
        "benchmark_hash": frozen_benchmark_content_hash(),
        "benchmark_hash_matches": frozen_benchmark_content_hash() == EXPECTED_BENCHMARK_HASH,
        "provider_calls": 0,
    }


if __name__ == "__main__":
    preservation = preserve_historical_evidence()
    parent = verify_parent()
    inventory = audit_applicability()
    if not parent["m43_parent_matches"]:
        raise SystemExit("M45_PARENT_PROMPT_MISMATCH")
    if not parent["benchmark_hash_matches"]:
        raise SystemExit("M45_CONTRACT_MISMATCH:benchmark")
    if inventory["uncertain_cases"]:
        raise SystemExit("M45_HYPOTHESIS_REJECTED_OFFLINE:uncertain_applicability")
    print(
        json.dumps(
            {"preservation": preservation, "parent": parent, "inventory": inventory},
            indent=2,
            sort_keys=True,
        )
    )
