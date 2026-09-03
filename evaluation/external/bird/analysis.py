"""Offline BIRD result, slice, and complexity analysis."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from evaluation.external.bird.features import complexity_tags

FEATURE_NAMES = (
    "LAG",
    "LEAD",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "EXPLICIT_FRAME",
    "WINDOW_AGGREGATE",
)


def read_rows(run_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (run_dir / "results.jsonl").read_text().splitlines()
        if line.strip()
    ]


def slice_summary(rows: list[dict[str, Any]], predicate: Any) -> dict[str, int | float]:
    selected = [row for row in rows if predicate(row)]
    correct = sum(bool(row["execution_accuracy"]) for row in selected)
    return {
        "questions": len(selected),
        "exact_correct": correct,
        "exact_percentage": correct / len(selected) * 100 if selected else 0.0,
    }


def analyze(run_dir: Path) -> dict[str, Any]:
    rows = read_rows(run_dir)
    feature_summary = {
        name: slice_summary(rows, lambda row, key=name: bool(row["gold_features"].get(key)))
        for name in FEATURE_NAMES
    }
    windows = [row for row in rows if row["gold_features"].get("HAS_WINDOW")]
    database: dict[str, Any] = {}
    for db_id in sorted({row["db_id"] for row in rows}):
        selected = [row for row in rows if row["db_id"] == db_id]
        value = slice_summary(selected, lambda row: True)
        value.update(
            {
                "parse_success": sum(bool(row["parse_success"]) for row in selected),
                "execution_success": sum(bool(row["execution_success"]) for row in selected),
            }
        )
        database[db_id] = value
    difficulty = {
        level: slice_summary(rows, lambda row, key=level: row["difficulty"] == key)
        for level in sorted({row["difficulty"] for row in rows})
    }
    structural = [row["gold_structural_profile"] for row in rows]
    structural_sums = {
        key: sum(int(profile.get(key, 0)) for profile in structural)
        for key in sorted({key for profile in structural for key in profile})
    }
    generated_window = sum(
        bool(row.get("generated_features", {}).get("HAS_WINDOW")) for row in windows
    )
    output = {
        "questions": len(rows),
        "database": database,
        "difficulty": difficulty,
        "window": {
            "gold_questions": len(windows),
            "generated_window_on_gold_window": generated_window,
            "recognition_percentage": generated_window / len(windows) * 100 if windows else 0.0,
            "exact_correct": sum(bool(row["execution_accuracy"]) for row in windows),
            "features": feature_summary,
        },
        "ratio": slice_summary(
            rows, lambda row: bool(row["gold_structural_profile"].get("division"))
        ),
        "temporal": slice_summary(
            rows, lambda row: bool(row["gold_structural_profile"].get("date_time"))
        ),
        "complexity": {
            "feature_question_counts": structural_sums,
            "tag_counts": dict(
                Counter(
                    tag for row in rows for tag in complexity_tags(row["gold_structural_profile"])
                )
            ),
        },
        "failure_taxonomy": dict(Counter(row.get("failure") for row in rows if row.get("failure"))),
        "provider_accounting": {
            "attempted": len(rows),
            "succeeded": sum(bool(row["provider_success"]) for row in rows),
            "failed": sum(not row["provider_success"] for row in rows),
        },
    }
    artifacts: dict[str, Any] = {
        "database_analysis.json": database,
        "difficulty_analysis.json": difficulty,
        "window_analysis.json": output["window"],
        "ratio_analysis.json": output["ratio"],
        "temporal_analysis.json": output["temporal"],
        "complexity_analysis.json": output["complexity"],
        "failure_analysis.json": output["failure_taxonomy"],
        "analysis.json": output,
    }
    for name, value in artifacts.items():
        (run_dir / name).write_text(json.dumps(value, indent=2) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir), indent=2))


if __name__ == "__main__":
    main()
