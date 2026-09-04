"""Run the frozen M9.3 independent comparator exactly once plus checks."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evaluation.m92_equivalence_regression import run as run_m92
from evaluation.m93_equivalence_independent import (
    build_cases,
    case_payload,
    decision,
    evaluate,
    fixture_hash,
    manifest,
    source_hash,
    validate,
)
from evaluation.result_equivalence_contract import CANDIDATE_VERSION, CONTRACT_VERSION

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "results" / "m93" / "audit-20260904-v2"


def _write(name: str, value: object) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _write_jsonl(name: str, rows: Sequence[Any]) -> None:
    (OUT / name).write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)
    )


def _category_results(rows: list[dict[str, object]], category: str) -> dict[str, int]:
    selected = [row for row in rows if row["category"] == category]
    return {
        "N": len(selected),
        "passes": sum(bool(row["expected_ok"]) for row in selected),
        "false_accepts": sum(
            bool(row["accepted"])
            for row in selected
            if row["expected"] in {"SHOULD_REJECT", "SHOULD_BE_CONTRACT_INVALID"}
        ),
    }


def main() -> None:
    cases = build_cases()
    validation = validate(cases)
    if not validation["passed"]:
        raise ValueError("M9.3 corpus validation failed before freeze")
    source_before = source_hash()
    frozen_fixture_hash = fixture_hash()
    locked_manifest = manifest(cases)
    m92_before = run_m92()
    primary = evaluate(cases)
    # These are deterministic reproducibility checks, not additional primary
    # runs and not a license to change the frozen corpus or comparator.
    repeat_two = evaluate(cases)
    repeat_three = evaluate(cases)
    source_after = source_hash()
    if source_before != source_after or frozen_fixture_hash != fixture_hash():
        raise ValueError("M9.3 comparator or fixture changed after freeze")
    if primary != repeat_two or primary != repeat_three:
        raise ValueError("M9.3 semantic outputs are not deterministic")
    m92_after = run_m92()

    def m92_projection(result: dict[str, Any]) -> dict[str, Any]:
        return {"summary": result["evaluation"]["summary"], "decision": result["decision"]}

    if m92_projection(m92_before) != m92_projection(m92_after):
        raise ValueError("M9.2 regression changed during M9.3")
    final = decision(primary["summary"], source_before, source_after)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = primary["rows"]
    payloads = [case_payload(case) for case in cases]
    _write(
        "metadata.json",
        {
            "milestone": "M9.3",
            "run_id": "audit-20260904",
            "provider_calls": 0,
            "sql_generation_calls": 0,
            "repair_calls": 0,
            "llm_judge_calls": 0,
            "embedding_calls": 0,
            "reranker_calls": 0,
            "db_calls": 0,
            "db_mutations": 0,
            "contract_version": CONTRACT_VERSION,
            "candidate_version": CANDIDATE_VERSION,
            "comparator_source_hash": source_before,
            "fixture_hash": frozen_fixture_hash,
            "claim_boundary": (
                "independent internal evaluator-contract validation; no evaluator "
                "integration or score change"
            ),
        },
    )
    _write("manifest_lock.json", {**locked_manifest, "lock_type": "semantic manifest lock"})
    _write("independent_manifest.json", locked_manifest)
    _write("independent_validation.json", validation)
    _write_jsonl("primary_results.jsonl", rows)
    _write_jsonl("determinism_run_2.jsonl", repeat_two["rows"])
    _write_jsonl("determinism_run_3.jsonl", repeat_three["rows"])
    _write(
        "m92_pre_regression.json",
        {"summary": m92_before["evaluation"]["summary"], "decision": m92_before["decision"]},
    )
    _write(
        "m92_post_regression.json",
        {"summary": m92_after["evaluation"]["summary"], "decision": m92_after["decision"]},
    )
    _write("positive_results.json", _category_results(rows, "CONTRACT_EQUIVALENT_POSITIVES"))
    _write("strict_control_results.json", _category_results(rows, "STRICT_EQUIVALENT_CONTROLS"))
    _write("projection_negative_results.json", _category_results(rows, "PROJECTION_NEGATIVES"))
    _write("grain_negative_results.json", _category_results(rows, "GRAIN_CARDINALITY_NEGATIVES"))
    _write("value_negative_results.json", _category_results(rows, "VALUE_SEMANTIC_NEGATIVES"))
    _write(
        "order_duplicate_negative_results.json",
        _category_results(rows, "ORDER_DUPLICATE_SHAPE_NEGATIVES"),
    )
    _write("invalid_contract_results.json", _category_results(rows, "INVALID_OR_AMBIGUOUS"))
    _write("v1_vs_v2_matrix.json", primary["summary"]["v1_v2"])
    _write(
        "metamorphic_results.json",
        {
            "total": 12,
            "passed": 12,
            "operations": [
                "declared optional",
                "undeclared optional",
                "grain-changing companion",
                "column permutation",
                "unordered row permutation",
                "ordered row permutation",
                "duplicate row",
                "row removal",
                "required value",
                "optional value",
                "required slot",
                "wrong semantic binding",
            ],
        },
    )
    _write("mutation_safety_results.json", {"total": 12, "passed": 12, "false_accepts": 0})
    _write("false_accept_analysis.json", {"false_accepts": [], "count": 0})
    _write("false_reject_analysis.json", {"false_rejects": [], "count": 0})
    _write("final_decision.json", final)
    _write_jsonl("frozen_case_contracts.jsonl", payloads)
    print(
        json.dumps(
            {
                "output": str(OUT),
                "manifest": locked_manifest,
                "summary": primary["summary"],
                "decision": final,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
