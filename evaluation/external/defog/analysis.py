"""Offline analysis for a persisted M2.13 Defog calibration run."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from evaluation.external.defog.benchmark import (
    ast_features,
    canonical_gold_variants,
    load_questions,
    structural_profile,
)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def gold_profile(question: Any) -> tuple[dict[str, bool], dict[str, int | bool]]:
    variants = canonical_gold_variants(question.gold_sql)
    feature_values = [ast_features(variant) for variant in variants]
    profile_values = [structural_profile(variant) for variant in variants]
    feature_keys = {key for values in feature_values for key in values}
    features = {
        key: any(values.get(key, False) for values in feature_values) for key in feature_keys
    }
    profile_keys = {key for values in profile_values for key in values}
    profile: dict[str, int | bool] = {
        key: max((int(values.get(key, 0)) for values in profile_values), default=0)
        for key in profile_keys
    }
    return features, profile


def complexity_tags(profile: dict[str, int | bool]) -> list[str]:
    tags: list[str] = []
    if not profile.get("group_by") and not profile.get("joins") and not profile.get("window"):
        tags.append("SIMPLE_SELECT")
    if profile.get("filter"):
        tags.append("FILTER")
    if profile.get("aggregation"):
        tags.append("AGGREGATION")
    if profile.get("group_by"):
        tags.append("GROUP_BY")
    if profile.get("joins"):
        tags.append("JOIN")
    if int(profile.get("joins", 0)) > 1:
        tags.append("MULTI_JOIN")
    if profile.get("subquery"):
        tags.append("SUBQUERY")
    if profile.get("cte"):
        tags.append("CTE")
    if profile.get("window"):
        tags.append("WINDOW")
    if profile.get("arithmetic"):
        tags.append("MULTI_STAGE_ANALYTIC")
    return tags


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_questions_from_env() -> dict[str, list[Any]]:
    root = Path(os.environ["DEFOG_SQL_EVAL_ROOT"])
    return {
        "classic": load_questions(root / "data" / "questions_gen_postgres.csv", "classic"),
        "advanced": load_questions(root / "data" / "instruct_advanced_postgres.csv", "advanced"),
    }


def analyze_dataset(records: list[dict[str, Any]], questions: list[Any]) -> dict[str, Any]:
    by_index = {question.index: question for question in questions}
    enriched: list[dict[str, Any]] = []
    for record in records:
        question = by_index[record["index"]]
        features, profile = gold_profile(question)
        generated_features = ast_features(record.get("generated_sql", ""))
        enriched.append(
            {
                **record,
                "gold_features_recomputed": features,
                "gold_structural_profile_recomputed": profile,
                "complexity_tags": complexity_tags(profile),
                "generated_features": generated_features,
            }
        )

    window_rows = [row for row in enriched if row["gold_features_recomputed"].get("HAS_WINDOW")]
    official_window_rows = [
        row for row in enriched if row["query_category"] == "instructions_cte_window"
    ]
    feature_names = ["LAG", "LEAD", "ROW_NUMBER", "RANK", "DENSE_RANK", "EXPLICIT_WINDOW_FRAME"]
    window_features = {
        name: {
            "questions": sum(row["gold_features_recomputed"].get(name, False) for row in enriched),
            "exact_correct": sum(
                row["gold_features_recomputed"].get(name, False) and row["exact_match"]
                for row in enriched
            ),
        }
        for name in feature_names
    }
    return {
        "questions": len(enriched),
        "provider_success": sum(row["provider_success"] for row in enriched),
        "provider_failures": sum(not row["provider_success"] for row in enriched),
        "sql_parse_success": sum(row["parse_success"] for row in enriched),
        "benchmark_safety_acceptance": sum(row["safety_accepted"] for row in enriched),
        "benchmark_safety_rejections": sum(
            row.get("failure") == "BENCHMARK_SAFETY_REJECTION" for row in enriched
        ),
        "execution_success": sum(row["execution_success"] for row in enriched),
        "execution_errors": sum(row.get("failure") == "EXECUTION_ERROR" for row in enriched),
        "timeouts": sum(row["timeout"] for row in enriched),
        "exact_correct": sum(row["exact_match"] for row in enriched),
        "subset_correct": sum(row["subset_match"] for row in enriched),
        "category": category_summary(enriched),
        "window": window_summary(window_rows),
        "official_window_category": window_summary(official_window_rows),
        "window_features": window_features,
        "generated_window_on_gold_window_questions": sum(
            row["generated_features"].get("HAS_WINDOW", False) for row in window_rows
        ),
        "complexity_distribution": dict(
            Counter(tag for row in enriched for tag in row["complexity_tags"])
        ),
        "structural_averages": {
            key: mean(
                float(row["gold_structural_profile_recomputed"].get(key, 0)) for row in enriched
            )
            for key in (
                "tables",
                "joins",
                "cte",
                "window",
                "subquery",
                "group_by",
                "order_by",
                "limit",
                "arithmetic",
            )
        },
        "latency_ms": latency_summary(enriched),
        "tokens": token_summary(enriched),
    }


def category_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for category in sorted({row["query_category"] for row in records}):
        rows = [row for row in records if row["query_category"] == category]
        result[category] = {
            "questions": len(rows),
            "exact_correct": sum(row["exact_match"] for row in rows),
            "subset_correct": sum(row["subset_match"] for row in rows),
            "parse_success": sum(row["parse_success"] for row in rows),
            "execution_success": sum(row["execution_success"] for row in rows),
        }
    return result


def window_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "questions": len(records),
        "exact_correct": sum(row["exact_match"] for row in records),
        "subset_correct": sum(row["subset_match"] for row in records),
        "generated_window": sum(
            row["generated_features"].get("HAS_WINDOW", False) for row in records
        ),
    }


def latency_summary(records: list[dict[str, Any]]) -> dict[str, float | None]:
    values = [
        float(row["provider_latency_ms"]) for row in records if row.get("provider_latency_ms")
    ]
    return {
        "avg_ms": mean(values) if values else None,
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
    }


def token_summary(records: list[dict[str, Any]]) -> dict[str, int | float | None]:
    inputs = [int(row["input_tokens"]) for row in records if row.get("input_tokens") is not None]
    outputs = [int(row["output_tokens"]) for row in records if row.get("output_tokens") is not None]
    return {
        "input_tokens": sum(inputs),
        "output_tokens": sum(outputs),
        "avg_input_tokens": mean(inputs) if inputs else None,
        "avg_output_tokens": mean(outputs) if outputs else None,
        "p50_input_tokens": percentile([float(value) for value in inputs], 0.50),
        "p95_input_tokens": percentile([float(value) for value in inputs], 0.95),
        "p50_output_tokens": percentile([float(value) for value in outputs], 0.50),
        "p95_output_tokens": percentile([float(value) for value in outputs], 0.95),
    }


def internal_profile() -> dict[str, Any]:
    path = Path("evaluation/datasets/m212_window_dev.json")
    rows = json.loads(path.read_text())
    profiles = [structural_profile(row["gold_sql"]) for row in rows]
    return {
        "questions": len(rows),
        "patterns": dict(Counter(row["pattern"] for row in rows)),
        "structural_averages": {
            key: mean(float(profile.get(key, 0)) for profile in profiles)
            for key in (
                "tables",
                "joins",
                "cte",
                "window",
                "subquery",
                "group_by",
                "order_by",
                "limit",
                "arithmetic",
            )
        },
        "complexity_distribution": dict(
            Counter(tag for profile in profiles for tag in complexity_tags(profile))
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    questions = load_questions_from_env()
    classic = analyze_dataset(
        read_jsonl(args.run_dir / "classic_results.jsonl"), questions["classic"]
    )
    advanced = analyze_dataset(
        read_jsonl(args.run_dir / "advanced_results.jsonl"), questions["advanced"]
    )
    output = {
        "classic": classic,
        "advanced": advanced,
        "m212_internal_window_stress": internal_profile(),
    }
    (args.run_dir / "complexity_analysis.json").write_text(
        json.dumps(
            {
                "classic": classic["complexity_distribution"],
                "advanced": advanced["complexity_distribution"],
                "m212": output["m212_internal_window_stress"]["complexity_distribution"],
            },
            indent=2,
        )
        + "\n"
    )
    (args.run_dir / "difficulty_calibration.json").write_text(json.dumps(output, indent=2) + "\n")
    (args.run_dir / "window_analysis.json").write_text(
        json.dumps(
            {
                "classic": classic["window"],
                "advanced": advanced["window"],
                "advanced_official_category": advanced["official_window_category"],
                "classic_window_features": classic["window_features"],
                "advanced_window_features": advanced["window_features"],
            },
            indent=2,
        )
        + "\n"
    )
    (args.run_dir / "category_analysis.json").write_text(
        json.dumps({"classic": classic["category"], "advanced": advanced["category"]}, indent=2)
        + "\n"
    )
    (args.run_dir / "performance_analysis.json").write_text(
        json.dumps(
            {
                "classic": {"latency_ms": classic["latency_ms"], "tokens": classic["tokens"]},
                "advanced": {"latency_ms": advanced["latency_ms"], "tokens": advanced["tokens"]},
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
