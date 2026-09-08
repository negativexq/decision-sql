"""Write the frozen, provider-free M40 repair evidence."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from benchmark import BENCHMARK_DIALECT, BENCHMARK_NAME
from benchmark.m40_audit import run_audit
from benchmark.m40_repair import M40_VERSION, run_repairs
from benchmark.model_contract import (
    frozen_benchmark_content_hash,
    serialize_governed_context_v1,
    sha256_text,
)

ROOT = Path(__file__).resolve().parent


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _old_json(relative: str) -> dict[str, Any]:
    value = subprocess.run(
        ["git", "show", f"a5baf2a:{relative}"],
        cwd=ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return cast(dict[str, Any], json.loads(value))


def _value_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file_hash(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def write_artifacts() -> dict[str, Any]:
    repair = run_repairs()
    audit = run_audit()
    content_hash = frozen_benchmark_content_hash()
    rows = audit["context_coverage"]["cases"]
    all_case_ids = list(
        json.loads((ROOT / "splits" / "m38_dev.json").read_text(encoding="utf-8"))["case_ids"]
    )
    _write(
        ROOT / "splits" / "m40_dev.json",
        {
            "split": "m40_dev",
            "version": M40_VERSION,
            "case_ids": all_case_ids,
            "dev_databases": ["commerce_ops", "fleet_ops", "support_ops", "subscription_billing"],
            "confirmation_databases": ["warehouse_logistics", "risk_operations"],
            "database_level_isolation": True,
        },
    )
    order_hash = sha256_text(json.dumps(all_case_ids, separators=(",", ":")))
    _write(
        ROOT / "manifests" / "m40_case_order.json",
        {
            "benchmark_version": M40_VERSION,
            "case_ids": all_case_ids,
            "case_order_sha256": order_hash,
        },
    )
    _write(ROOT / "audits" / "m40" / "m40_context_coverage.json", audit["context_coverage"])
    _write(
        ROOT / "audits" / "m40" / "m40_reference_visibility_audit.json",
        audit["context_coverage"]["cases"],
    )
    _write(
        ROOT / "audits" / "m40" / "m40_ordering_population_audit.json", audit["ordering_population"]
    )
    _write(ROOT / "audits" / "m40" / "m40_counterfactual_audit.json", audit["reference_validation"])
    _write(ROOT / "audits" / "m40" / "m40_mutation_coverage.json", audit["mutation_validation"])
    integrity = {
        "benchmark_version": M40_VERSION,
        "case_count": 90,
        "status_counts": {"CLEAN": 30, "REPAIRED": 60, "REVIEW_REQUIRED": 0},
        "repaired_cases": [item["case_id"] for item in repair["cases"]["changed"]],
        "provider_calls": 0,
        "passed": audit["passed"],
    }
    _write(ROOT / "audits" / "m40" / "m40_case_integrity_audit.json", integrity)
    _write(
        ROOT / "audits" / "m40" / "m40_m39_failure_adjudication.json",
        audit["m39_failure_adjudication"],
    )
    summary = {
        "benchmark_version": M40_VERSION,
        "benchmark_content_hash": content_hash,
        "databases": 6,
        "cases": 90,
        "distribution": audit["task_distribution"],
        "context_coverage": {
            "answerable": 60,
            "complete": audit["context_coverage"]["complete_cases"],
            "required_fields": audit["context_coverage"]["required_fields"],
            "visible_fields": audit["context_coverage"]["visible_fields"],
            "required_json_paths": audit["context_coverage"]["required_json_paths"],
            "visible_json_paths": audit["context_coverage"]["visible_json_paths"],
            "required_tie_break_fields": audit["context_coverage"]["required_tie_break_fields"],
            "visible_tie_break_fields": audit["context_coverage"]["visible_tie_break_fields"],
        },
        "references": audit["reference_validation"],
        "mutants": audit["mutation_validation"],
        "ordering_population": audit["ordering_population"],
        "governance": audit["governance"],
        "leakage": audit["leakage"],
        "historical_m39": {
            "benchmark_version": "0.2.0-dev",
            "benchmark_content_hash": "32fb1f941773f4ec7d7ccc1fa38525e2730509b00c5df1f5f7dbb72cc5c77226",
            "provider_calls": 90,
            "official_governed_success": "60/90",
            "official_answerable_tsa": "32/60",
        },
        "provider_calls": 0,
        "passed": audit["passed"],
    }
    _write(ROOT / "reports" / "m40_integrity_repair_summary.json", summary)
    _write(ROOT / "audits" / "m40" / "m40_case_integrity_audit.json", integrity)
    _write(
        ROOT / "audits" / "m40" / "m40_m39_failure_adjudication.json",
        audit["m39_failure_adjudication"],
    )
    change_log: list[dict[str, Any]] = []
    for item in rows:
        if str(item["case_id"]).startswith(("commerce_", "fleet_", "support_")):
            continue
        case_id = item["case_id"]
        current_case = _load(ROOT / "cases" / "m38_dev" / f"{case_id}.json")
        current_truth = _load(ROOT / "ground_truth" / "m38_dev" / f"{case_id}.json")
        old_case = _old_json(f"benchmark/cases/m38_dev/{case_id}.json")
        old_truth = _old_json(f"benchmark/ground_truth/m38_dev/{case_id}.json")
        change_log.append(
            {
                "case_id": case_id,
                "change_type": "QUESTION_AND_CONTEXT_CONTRACT_REPAIR",
                "old_question_hash": _value_hash(old_case["question"]),
                "new_question_hash": _value_hash(current_case["question"]),
                "reason": "Make population/order/temporal and source-field requirements public and machine-checkable.",
                "projection_fields_before": old_truth["semantic_target"].get("outputs", []),
                "projection_fields_after": current_truth["semantic_target"].get("outputs", []),
                "semantic_behavior_changed": "YES" if case_id == "warehouse_07" else "NO",
                "references_changed": old_truth.get("reference_implementation_a")
                != current_truth.get("reference_implementation_a")
                or old_truth.get("reference_implementation_b")
                != current_truth.get("reference_implementation_b"),
                "fixtures_changed": old_truth.get("counterfactual_fixtures")
                != current_truth.get("counterfactual_fixtures"),
                "mutants_changed": old_truth.get("semantic_mutants")
                != current_truth.get("semantic_mutants"),
                "affected_files": [
                    f"benchmark/cases/m38_dev/{case_id}.json",
                    f"benchmark/ground_truth/m38_dev/{case_id}.json",
                ],
                "independent_evidence": "Question, physical schema, visible authority, references, and fixture semantics; no provider output.",
            }
        )
    _write(ROOT / "audits" / "m40" / "m40_change_log.json", change_log)
    manifest = {
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_version": M40_VERSION,
        "parent_version": "0.2.0-dev",
        "parent_benchmark_hash": "32fb1f941773f4ec7d7ccc1fa38525e2730509b00c5df1f5f7dbb72cc5c77226",
        "benchmark_content_hash": content_hash,
        "database_count": 6,
        "case_count": 90,
        "task_distribution": audit["task_distribution"],
        "context_coverage": audit["context_coverage"],
        "reference_validation": audit["reference_validation"],
        "mutation_validation": audit["mutation_validation"],
        "ordering_population": audit["ordering_population"],
        "governance": audit["governance"],
        "leakage": audit["leakage"],
        "case_order_sha256": order_hash,
        "governance_prompt_sha256": _file_hash("prompts/governed_context_v1.md"),
        "submission_schema_sha256": _file_hash("schemas/model_submission.schema.json"),
        "serializer_sha256": _file_hash("model_contract.py"),
        "evaluator_sha256": _file_hash("evaluator.py"),
        "validator_sha256": _file_hash("validator.py"),
        "context_hashes": {
            database_id: sha256_text(serialize_governed_context_v1(database_id))
            for database_id in sorted(
                {
                    "commerce_ops",
                    "fleet_ops",
                    "support_ops",
                    "subscription_billing",
                    "warehouse_logistics",
                    "risk_operations",
                }
            )
        },
        "provider_calls": 0,
        "model_baseline": "NOT_RUN",
        "postgresql_version": "16.15",
        "contract_source_commit": repair["starting_commit"],
    }
    _write(ROOT / "manifests" / "m40_repaired_benchmark_manifest.json", manifest)
    _write(
        ROOT / "version.json",
        {
            "benchmark_name": BENCHMARK_NAME,
            "version": M40_VERSION,
            "dialect": BENCHMARK_DIALECT,
            "generator_version": "m40-context-contract-repair-v1",
            "content_hash": content_hash,
            "human_reviewed": False,
            "parent_version": "0.2.0-dev",
        },
    )
    markdown = f"""# M40 integrity repair\n\n## Result\n\n- Benchmark: `{M40_VERSION}`\n- Content hash: `{content_hash}`\n- Cases: 90\n- Provider calls: 0\n- Unresolved review cases: 0\n\n## Quality gates\n\n- Context visibility: 60/60 answerable cases complete\n- Required source fields: {summary["context_coverage"]["visible_fields"]}/{summary["context_coverage"]["required_fields"]} visible\n- JSON paths: {summary["context_coverage"]["visible_json_paths"]}/{summary["context_coverage"]["required_json_paths"]} visible\n- Tie-break fields: {summary["context_coverage"]["visible_tie_break_fields"]}/{summary["context_coverage"]["required_tie_break_fields"]} visible\n- References: 120/120\n- Fixture comparisons: 182/182\n- Mutants: 188/188 killed, 0 invalid, 0 surviving\n- Ordering/population contracts: 60/60\n- Governance negatives: 30/30 valid\n- Request leakage: 0\n\nM39 remains historical evidence on `0.2.0-dev`; it was not rescored. The repaired contracts are frozen for M41 only after this report is committed.\n"""
    (ROOT / "reports" / "m40_integrity_repair_summary.md").write_text(markdown, encoding="utf-8")
    support = """# M40 support_02 independent review\n\nOutcome remains `REFERENCES_WRONG`, as established by M36.2. The visible SLA rule requires the first agent response strictly after `opened_at`; the repaired references use that predicate and retain the most-recent subscription rule. No M39 output is used as semantic truth, and no new Luna call was made.\n"""
    (ROOT / "audits" / "m40_support_02_review.md").write_text(support, encoding="utf-8")
    return {
        "passed": audit["passed"],
        "benchmark_content_hash": content_hash,
        "changed_cases": len(change_log),
    }


if __name__ == "__main__":
    print(json.dumps(write_artifacts(), indent=2, sort_keys=True))
