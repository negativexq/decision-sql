"""Recompute M2.10 derived diagnostics from persisted candidates only.

This command never contacts a provider and never executes SQL.  It exists to
repair/report derived analysis without mutating the raw candidate artifact.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evaluation.m210_passk import diversity_summary, pass_at_k
from evaluation.run_m210 import _failure_analysis, _policy_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute offline M2.10 diagnostics")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in (args.run_dir / "candidates.jsonl").read_text().splitlines()
    ]
    grouped = _group(rows)
    output = args.output or args.run_dir / "recomputed-v2"
    output.mkdir(parents=True, exist_ok=True)

    diversity = _diversity(grouped)
    policy = _policy_audit(rows)
    execution_filter = _execution_filter(grouped)
    transitions = _transitions(grouped)
    comparison = {
        "source_run": str(args.run_dir),
        "provider_calls": 0,
        "raw_candidates_preserved": True,
        "pass_k_recomputed": True,
        "derived_diagnostics_corrected": [
            "structural_signature_field_name",
            "safe_capability_not_allowed_policy_classification",
        ],
    }
    _write(output / "comparison.json", comparison)
    _write(output / "diversity_analysis.json", diversity)
    _write(output / "failure_mechanism_analysis.json", _failure_analysis(rows))
    _write(output / "policy_rejection_audit.json", policy)
    _write(output / "execution_filter_analysis.json", execution_filter)
    _write(output / "failure_transitions.json", transitions)
    _write(
        output / "evaluator_change.json",
        {
            "source_run": str(args.run_dir),
            "reason": "Derived M2.10 diagnostics used a mismatched persisted signature key.",
            "correction": "Use generated_signature when structural_signature is absent.",
            "provider_calls": 0,
            "raw_candidates_unchanged": True,
        },
    )
    print(json.dumps({"output_dir": str(output), "provider_calls": 0}))


def _group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["question_id"]].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: row["candidate_index"])
    return dict(grouped)


def _diversity(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    per_question = {question_id: diversity_summary(rows) for question_id, rows in grouped.items()}
    return {
        "per_question": per_question,
        "average_unique_raw_sql_at_8": _average(
            item["unique_raw_sql_count"] for item in per_question.values()
        ),
        "average_unique_normalized_sql_at_8": _average(
            item["unique_normalized_sql_count"] for item in per_question.values()
        ),
        "average_unique_structural_signatures_at_8": _average(
            item["unique_structural_signature_count"] for item in per_question.values()
        ),
        "classification_counts": dict(
            Counter(item["diversity_classification"] for item in per_question.values())
        ),
    }


def _execution_filter(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    per_question: dict[str, Any] = {}
    for question_id, rows in grouped.items():
        executable = [row for row in rows if row["plan_accepted"] and row["execution_success"]]
        executable_pass_k: dict[str, bool | None] = {}
        for k in (1, 2, 4, 8):
            if len(executable) < k:
                executable_pass_k[f"pass@{k}"] = None
            else:
                executable_pass_k[f"pass@{k}"] = pass_at_k(executable, k)
        per_question[question_id] = {
            "candidates_generated": len(rows),
            "plan_accepted": sum(row["plan_accepted"] for row in rows),
            "executable": len(executable),
            "unique_executable_normalized_sql": len(
                {row["normalized_sql"] for row in executable if row["normalized_sql"]}
            ),
            "pass_k_among_executable_candidates": executable_pass_k,
        }
    return {
        "average_candidates_generated": _average(
            row["candidates_generated"] for row in per_question.values()
        ),
        "average_plan_accepted": _average(row["plan_accepted"] for row in per_question.values()),
        "average_executable": _average(row["executable"] for row in per_question.values()),
        "average_unique_executable_normalized_sql": _average(
            row["unique_executable_normalized_sql"] for row in per_question.values()
        ),
        "per_question": per_question,
    }


def _transitions(grouped: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_question: dict[str, Any] = {}
    for question_id, rows in grouped.items():
        causes = [row["primary_root_cause"] for row in rows]
        by_question[question_id] = {
            "primary_causes_in_order": causes,
            "distinct_failure_causes": sorted({cause for cause in causes if cause}),
            "all_candidates_same_failure": len(set(causes)) == 1,
            "eventual_correct": any(row["result_equivalent"] is True for row in rows),
        }
    return {
        "same_failure_all_candidates": sum(
            row["all_candidates_same_failure"] for row in by_question.values()
        ),
        "failure_class_switches": sum(
            not row["all_candidates_same_failure"] for row in by_question.values()
        ),
        "eventual_correct_questions": sum(row["eventual_correct"] for row in by_question.values()),
        "per_question": by_question,
    }


def _average(values: Any) -> float:
    numbers = [float(value) for value in values]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
