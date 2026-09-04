"""Write the ignored, deterministic M9.2 regression artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from evaluation.m92_equivalence_regression import (
    CORPUS_ID,
    M3_HASH,
    M4_HASH,
    M7_HASHES,
    M8_HASH,
    M9_HASHES,
    M91_HASH,
    RegressionCase,
    build_cases,
    evaluate,
    final_decision,
    fixture_hash,
    stable_hash,
    validate_corpus,
)
from evaluation.result_equivalence_contract import CANDIDATE_VERSION, CONTRACT_VERSION, SlotKind

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "results" / "m92" / "audit-20260904"


def _write(name: str, value: object) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _write_jsonl(name: str, rows: list[object]) -> None:
    (OUT / name).write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)
    )


def _case_payload(case: RegressionCase) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "source": case.source,
        "split": case.split,
        "family": case.family,
        "expected_label": case.expected_label,
        "contract": asdict(case.contract),
        "generated": asdict(case.generated),
        "references": [asdict(reference) for reference in case.references],
    }


def main() -> None:
    cases = build_cases()
    validation = validate_corpus(cases)
    if not validation["passed"]:
        raise ValueError("M9.2 corpus validation failed")
    evaluated = evaluate(cases)
    summary = evaluated["summary"]
    decision = final_decision(summary)
    OUT.mkdir(parents=True, exist_ok=True)
    historical = [case for case in cases if case.source != "SYNTHETIC"]
    synthetic = [case for case in cases if case.source == "SYNTHETIC"]
    rows = evaluated["rows"]
    by_id = {row["case_id"]: row for row in rows}
    manifest = {
        "corpus_id": CORPUS_ID,
        "version": "m92-equivalence-contract-v1",
        "fixture_hash": fixture_hash(),
        "historical_case_ids": [case.case_id for case in historical],
        "synthetic_case_ids": [case.case_id for case in synthetic],
        "historical_hash": stable_hash([by_id[case.case_id] for case in historical]),
        "artifact_positive_hash": stable_hash(
            [by_id[case.case_id] for case in historical if case.source == "HISTORICAL_ARTIFACT"]
        ),
        "projection_negative_hash": stable_hash(
            [by_id[case.case_id] for case in historical if case.source == "HISTORICAL_PROJECTION"]
        ),
        "semantic_negative_hash": stable_hash(
            [by_id[case.case_id] for case in historical if case.source == "HISTORICAL_SEMANTIC"]
        ),
        "correct_control_hash": stable_hash(
            [by_id[case.case_id] for case in historical if case.source == "CORRECT_CONTROL"]
        ),
        "non_s11_negative_hash": stable_hash(
            [by_id[case.case_id] for case in historical if case.source == "NON_S11_NEGATIVE"]
        ),
        "synthetic_hash": stable_hash([by_id[case.case_id] for case in synthetic]),
        "full_regression_hash": stable_hash(rows),
    }
    _write(
        "metadata.json",
        {
            "milestone": "M9.2",
            "corpus_id": CORPUS_ID,
            "version": "m92-equivalence-contract-v1",
            "contract_version": CONTRACT_VERSION,
            "candidate_version": CANDIDATE_VERSION,
            "fixture_hash": fixture_hash(),
            "provider_calls": 0,
            "sql_generation_calls": 0,
            "repair_calls": 0,
            "llm_judge_calls": 0,
            "embedding_calls": 0,
            "reranker_calls": 0,
            "db_mutations": 0,
            "read_only_db_executions": 0,
            "m7_hashes": M7_HASHES,
            "m8_hash": M8_HASH,
            "m9_hashes": M9_HASHES,
            "m91_hash": M91_HASH,
            "m3_contract_hash": M3_HASH,
            "m4_corpus_hash": M4_HASH,
            "historical_rows": "compact oracle-bound fixtures; no raw persisted M9.1 rows",
        },
    )
    _write("regression_manifest.json", manifest)
    _write("regression_validation.json", validation)
    _write(
        "contract_schema.json",
        {
            "contract_version": CONTRACT_VERSION,
            "slot_kinds": [kind.value for kind in SlotKind],
            "extra_output_policies": ["FORBID_EXTRA_OUTPUT", "ALLOW_DECLARED_OPTIONAL_ONLY"],
            "duplicate_policy": "PRESERVE",
            "row_order_policies": ["ORDER_INSENSITIVE", "ORDER_REQUIRED"],
        },
    )
    _write_jsonl("historical_contract_cases.jsonl", [_case_payload(case) for case in historical])
    _write_jsonl("synthetic_cases.jsonl", [_case_payload(case) for case in synthetic])
    _write_jsonl("v1_vs_candidate.jsonl", rows)
    _write("artifact_recovery.json", summary["artifacts"])
    _write("projection_negative_results.json", summary["projection_negatives"])
    _write("semantic_negative_results.json", summary["semantic_negatives"])
    _write("correct_control_results.json", summary["correct_controls"])
    _write("non_s11_negative_results.json", summary["non_s11_negatives"])
    _write("synthetic_results.json", summary["synthetic"])
    _write("reason_code_summary.json", summary["reasons"])
    _write(
        "split_breakdown.json",
        {
            split: {"N": sum(row["split"] == split for row in rows)}
            for split in ("dev", "holdout", "synthetic")
        },
    )
    _write(
        "family_breakdown.json",
        {
            family: sum(row["family"] == family for row in rows)
            for family in sorted({row["family"] for row in rows})
        },
    )
    _write(
        "optional_field_breakdown.json",
        {
            group: sum(row["optional_group"] == group for row in rows)
            for group in (
                "IDENTITY_COMPANION",
                "DISPLAY_METADATA",
                "AUXILIARY_OUTPUT",
                "OTHER_DECLARED_OPTIONAL",
                "UNDETERMINED",
            )
        },
    )
    _write(
        "determinism_check.json", {"passed": evaluate(cases) == evaluated, "repeated_run_count": 2}
    )
    _write("final_decision.json", decision)
    print(
        json.dumps(
            {"output": str(OUT), "manifest": manifest, "summary": summary, "decision": decision},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
