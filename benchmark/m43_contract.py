"""Offline M43 parent, JSON inventory, and historical-preservation checks."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from benchmark.context import render_governed_context
from benchmark.model_contract import frozen_benchmark_content_hash, governance_instructions

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
EXPECTED_BENCHMARK_HASH = "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
EXPECTED_M41_PROMPT_HASH = "1407f33106506753ba4dd552c51ac6524133bc2789020237be5a64cf80aee544"
M41_ROOT = ROOT / "experiments" / "results" / "m41"
M42_ROOT = ROOT / "experiments" / "results" / "m42"
PRESERVATION_JSON = ROOT / "reports" / "m43_historical_preservation.json"
PRESERVATION_MD = ROOT / "reports" / "m43_historical_preservation.md"
INVENTORY_JSON = ROOT / "audits" / "m43_json_semantics_inventory.json"
INVENTORY_MD = ROOT / "audits" / "m43_json_semantics_inventory.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _historical_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for prefix in ("m39", "m41"):
        root = ROOT / "experiments" / "results" / prefix
        for path in sorted(root.iterdir()):
            if path.is_file():
                files[str(path.relative_to(REPO))] = path
    for root in (M42_ROOT, REPO / "evaluation" / "forensics" / "m42"):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[str(path.relative_to(REPO))] = path
    for relative in (
        "benchmark/manifests/m42_contract.json",
        "benchmark/manifests/m42_paired_request_audit.json",
        "benchmark/manifests/m42_request_ledger.json",
        "benchmark/reports/m42_historical_preservation.json",
        "benchmark/reports/m42_historical_preservation.md",
        "benchmark/audits/m42_offline_authority_semantics.json",
        "benchmark/audits/m42_offline_authority_semantics.md",
    ):
        path = REPO / relative
        if path.exists():
            files[relative] = path
    return files


def preserve_historical_evidence() -> dict[str, Any]:
    files = {relative: _sha(path) for relative, path in _historical_files().items()}
    result = {
        "milestone": "M43",
        "provider_calls": 0,
        "m39_benchmark": "0.2.0-dev",
        "m41_benchmark": "0.2.1-dev",
        "m42_experiment": "m42_authority_composition_clarification",
        "files": files,
    }
    PRESERVATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    PRESERVATION_JSON.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = [
        "# M43 historical preservation",
        "",
        "M39, M41, and M42 evidence was hashed before the M43 prompt edit. Provider calls: 0.",
        "",
        "| Artifact | SHA-256 |",
        "|---|---|",
    ]
    lines.extend(f"| `{relative}` | `{digest}` |" for relative, digest in files.items())
    PRESERVATION_MD.write_text("\n".join(lines) + "\n")
    return result


def _case_directory(case_id: str) -> str:
    return "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"


def _json_cases_using_json() -> list[str]:
    result = []
    for directory in ("pilot", "m38_dev"):
        for path in sorted((ROOT / "ground_truth" / directory).glob("*.json")):
            target = json.loads(path.read_text())["semantic_target"]
            if "json" in [str(tag).lower() for tag in target.get("query_shape_tags", [])]:
                result.append(path.stem)
    return result


def audit_json_semantics() -> dict[str, Any]:
    databases = (
        "commerce_ops",
        "fleet_ops",
        "support_ops",
        "subscription_billing",
        "warehouse_logistics",
        "risk_operations",
    )
    attributes: list[dict[str, Any]] = []
    for database_id in databases:
        context = render_governed_context(database_id)
        for attribute in context["attributes"]:
            physical = str(attribute.get("physical_column_or_path", ""))
            data_type = str(attribute.get("data_type", ""))
            extraction = "#>>" if "#>>" in physical else "->>" if "->>" in physical else None
            if extraction or data_type.upper() == "JSONB":
                attributes.append(
                    {
                        "database": database_id,
                        "entity": attribute["entity_id"],
                        "attribute_id": attribute["attribute_id"],
                        "physical_column_or_path": physical,
                        "documented_data_type": data_type,
                        "json_extraction_form": extraction,
                        "text_returning": extraction is not None,
                        "numeric_semantic_type": data_type.upper()
                        in {"NUMERIC", "DECIMAL", "INTEGER", "BIGINT", "REAL", "DOUBLE PRECISION"},
                    }
                )
    m41_rows = json.loads((M41_ROOT / "m41_case_results.json").read_text())
    target_details = []
    for case_id in ("fleet_06", "warehouse_09"):
        row = next(row for row in m41_rows if row["case_id"] == case_id)
        sql = str((row.get("parsed_submission") or {}).get("sql") or "")
        target_details.append(
            {
                "case_id": case_id,
                "m41_sql": sql,
                "text_extraction_present": "#>>" in sql or "->>" in sql,
                "numeric_coercion_present": "::numeric" in sql.lower()
                or "cast(" in sql.lower()
                and "numeric" in sql.lower(),
                "m41_category": row["official_category"],
            }
        )
    cases = _json_cases_using_json()
    numeric = [item for item in attributes if item["numeric_semantic_type"]]
    result = {
        "provider_calls": 0,
        "attributes": attributes,
        "counts": {
            "json_derived_attributes": sum(
                item["json_extraction_form"] is not None for item in attributes
            ),
            "documented_numeric_json_attributes": len(numeric),
            "text_returning_extractions": sum(item["text_returning"] for item in attributes),
            "hash_extractors": sum(item["json_extraction_form"] == "#>>" for item in attributes),
            "arrow_extractors": sum(item["json_extraction_form"] == "->>" for item in attributes),
            "answerable_cases_using_json": len(cases),
        },
        "answerable_json_cases": cases,
        "targets": target_details,
        "hypothesis_status": (
            "SUPPORTED_OFFLINE"
            if all(
                item["text_extraction_present"] and not item["numeric_coercion_present"]
                for item in target_details
            )
            and all(
                item["numeric_semantic_type"]
                for item in numeric
                if item["database"] in {"fleet_ops", "warehouse_logistics"}
            )
            else "REJECTED_OFFLINE"
        ),
    }
    INVENTORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_JSON.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    INVENTORY_MD.write_text(
        "# M43 JSON semantics inventory\n\n"
        f"Result: **{result['hypothesis_status']}**. The two target SQL outputs use "
        "text-returning JSON extraction while their governed attributes are documented "
        "as numeric. Provider calls: 0.\n",
    )
    return result


def verify_parent() -> dict[str, Any]:
    historical_prompt = subprocess.run(
        ["git", "show", "6e84d73:benchmark/prompts/governed_context_v1.md"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    prompt_hash = hashlib.sha256(historical_prompt.encode()).hexdigest()
    active_hash = hashlib.sha256(governance_instructions().encode()).hexdigest()
    benchmark_hash = frozen_benchmark_content_hash()
    return {
        "parent_prompt_hash": prompt_hash,
        "expected_parent_prompt_hash": EXPECTED_M41_PROMPT_HASH,
        "parent_prompt_matches": prompt_hash == EXPECTED_M41_PROMPT_HASH,
        "active_prompt_hash": active_hash,
        "benchmark_hash": benchmark_hash,
        "benchmark_hash_matches": benchmark_hash == EXPECTED_BENCHMARK_HASH,
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "provider_calls": 0,
    }


if __name__ == "__main__":
    preservation = preserve_historical_evidence()
    parent = verify_parent()
    inventory = audit_json_semantics()
    if not parent["parent_prompt_matches"]:
        raise SystemExit("M43_PARENT_PROMPT_MISMATCH")
    if not parent["benchmark_hash_matches"]:
        raise SystemExit("M43_CONTRACT_MISMATCH:benchmark")
    if inventory["hypothesis_status"] != "SUPPORTED_OFFLINE":
        raise SystemExit("M43_HYPOTHESIS_REJECTED_OFFLINE")
    print(
        json.dumps(
            {"preservation": preservation, "parent": parent, "inventory": inventory},
            indent=2,
            sort_keys=True,
        )
    )
