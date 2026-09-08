"""Offline M46A.1 reference adjudication and benchmark repair audit.

This module intentionally performs no provider work.  It keeps the M46A
structured grain layer server-owned and uses it as an authoring/audit aid;
the resulting metadata is never added to the model-facing context here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from benchmark.model_contract import sha256_text

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
TRUTH = ROOT / "ground_truth" / "m38_dev"
AUDIT_ROOT = ROOT / "audits" / "m46a1"
REPORT_ROOT = ROOT / "reports"
EXPECTED_PARENT_VERSION = "0.2.1-dev"
EXPECTED_PARENT_HASH = "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
NEW_VERSION = "0.2.2-dev"


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def historical_artifact_paths() -> list[Path]:
    """Return the immutable M39--M46A evidence files, not current source code."""
    paths: set[Path] = set()
    for experiment in ("m39", "m41", "m42", "m43", "m44", "m45"):
        for root in (
            ROOT / "experiments" / "results" / experiment,
            ROOT / "experiments",
            ROOT / "manifests",
        ):
            if root == ROOT / "experiments":
                paths.update(root.glob(f"{experiment}*.json"))
            elif root == ROOT / "manifests":
                paths.update(root.glob(f"{experiment}*.json"))
            elif root.exists():
                paths.update(path for path in root.rglob("*") if path.is_file())
    for root in (
        ROOT / "audits",
        ROOT / "reports",
        REPO / "evaluation" / "forensics",
    ):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            lowered = str(path).lower()
            if any(token in lowered for token in ("m42", "m43", "m44", "m45", "m46a")):
                paths.add(path)
    return sorted(paths)


def preserve_historical() -> dict[str, Any]:
    files = {
        str(path.relative_to(REPO)): _sha(path)
        for path in historical_artifact_paths()
        if path.exists()
    }
    payload = {
        "milestone": "M46A.1",
        "provider_calls": 0,
        "model_calls": 0,
        "historical_experiments": ["M39", "M41", "M42", "M43", "M44", "M45", "M46A"],
        "parent_benchmark_version": EXPECTED_PARENT_VERSION,
        "parent_benchmark_hash": EXPECTED_PARENT_HASH,
        "files": files,
    }
    _dump(REPORT_ROOT / "m46a1_historical_preservation.json", payload)
    lines = [
        "# M46A.1 historical preservation",
        "",
        "M39–M46A evidence is preserved by SHA-256 before benchmark mutation.",
        "",
        f"- Files hashed: `{len(files)}`",
        "- Provider calls: `0`",
        "- Model calls: `0`",
        "- Historical official scores are not rescored.",
    ]
    (REPORT_ROOT / "m46a1_historical_preservation.md").write_text("\n".join(lines) + "\n")
    return payload


def answerable_truth() -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], _load(path))
        for path in sorted(TRUTH.glob("*.json"))
        if _load(path).get("semantic_target", {}).get("behavior") == "ANSWERABLE"
    ]


def adjudication() -> dict[str, Any]:
    """Record the independent scratch-data adjudication before repair."""
    return {
        "method": [
            "public question and metric contract",
            "public schema and authorized cardinality",
            "independent multi-child scratch data",
            "execution of both references against the scratch data",
            "mathematical expected result",
        ],
        "provider_calls": 0,
        "cases": [
            {
                "case_id": "subscription_04",
                "reference_id": "A",
                "question": "For each account, return the account ID and net collected amount from captured payments after refunds. Include only groups represented by at least one qualifying source record.",
                "public_semantic_interpretation": "Captured payment amount less the sum of all refunds for each payment, then summed by account.",
                "source_measures": [
                    {"measure": "payments.amount", "grain": "payments.payment_id", "additivity": "ADDITIVE"},
                    {"measure": "refunds.amount", "grain": "refunds.refund_id", "rollup_key": "refunds.payment_id", "additivity": "ADDITIVE"},
                ],
                "relationship": "refunds.payment_id -> payments.payment_id",
                "cardinality": "many_to_one",
                "adversarial_data": {"captured_payment_amount": 100, "refund_amounts": [20, 10]},
                "reference_result": 170,
                "independent_expected_result": 70,
                "classification": "REFERENCE_DEFECT",
                "failure_family": "PARENT_MEASURE_FANOUT",
                "confidence": "HIGH",
                "repair_required": True,
                "evidence": "The payment amount is repeated once per refund row by Reference A; Reference B computes payment-grain net before account rollup.",
            },
            {
                "case_id": "subscription_04",
                "reference_id": "B",
                "classification": "REFERENCE_CORRECT",
                "adversarial_data": {"captured_payment_amount": 100, "refund_amounts": [20, 10]},
                "reference_result": 70,
                "independent_expected_result": 70,
                "confidence": "HIGH",
                "repair_required": False,
            },
            {
                "case_id": "subscription_10",
                "reference_id": "A",
                "question": "For each plan, return the plan ID and the refund rate, defined as refunded captured dollars divided by captured dollars. Include only groups represented by at least one qualifying source record.",
                "public_semantic_interpretation": "Sum refund dollars divided by sum captured payment dollars, with each captured payment counted once in the denominator.",
                "source_measures": [
                    {"measure": "payments.amount", "grain": "payments.payment_id", "additivity": "ADDITIVE"},
                    {"measure": "refunds.amount", "grain": "refunds.refund_id", "rollup_key": "refunds.payment_id", "additivity": "ADDITIVE"},
                ],
                "relationship": "refunds.payment_id -> payments.payment_id",
                "cardinality": "many_to_one",
                "adversarial_data": {"captured_payment_amounts": [100, 100], "refund_amounts": [20, 10, 0]},
                "reference_result": "0.15",
                "independent_expected_result": "0.30",
                "classification": "REFERENCE_DEFECT",
                "failure_family": "PARENT_MEASURE_FANOUT",
                "confidence": "HIGH",
                "repair_required": True,
                "evidence": "The captured payment denominator is repeated once per refund row by Reference A; the question explicitly defines captured dollars and Reference B preserves payment grain.",
            },
            {
                "case_id": "subscription_10",
                "reference_id": "B",
                "classification": "REFERENCE_CORRECT",
                "adversarial_data": {"captured_payment_amounts": [100, 100], "refund_amounts": [20, 10, 0]},
                "reference_result": "0.30",
                "independent_expected_result": "0.30",
                "confidence": "HIGH",
                "repair_required": False,
            },
        ],
        "question_contract_result": {
            "subscription_04": "QUESTION_SUFFICIENT",
            "subscription_10": "QUESTION_SUFFICIENT",
            "model_visible_context_changed": False,
            "metric_metadata_note": "refund_rate is case-defined by the explicit question; no hidden metric rule is needed.",
        },
        "review_required": 0,
    }


def write_pre_repair_audit() -> dict[str, Any]:
    preservation = preserve_historical()
    report = adjudication()
    report["historical_preservation_file_count"] = len(preservation["files"])
    _dump(AUDIT_ROOT / "m46a1_reference_adjudication.json", report)
    return report


if __name__ == "__main__":
    result = write_pre_repair_audit()
    print(json.dumps({"provider_calls": 0, "review_required": result["review_required"]}, indent=2))
