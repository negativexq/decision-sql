"""Offline artifact enrichment for completed M3 runs."""

import argparse
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from typing import Any

from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from evaluation.m3_benchmark import benchmark_hash, build_benchmark_cases, build_targets
from evaluation.run_m3 import write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--split", choices=("dev", "holdout"), required=True)
    args = parser.parse_args()
    targets = build_targets()
    cases = build_benchmark_cases(targets)
    direct = _read_jsonl(args.run_dir / f"{args.split}_direct_results.jsonl")
    governed = _read_jsonl(args.run_dir / f"{args.split}_governed_results.jsonl")
    catalog = build_m3_catalog()
    reference_validation = json.loads((args.run_dir / "compiler_ceiling.json").read_text())
    write_json(
        args.run_dir / "catalog_manifest.json",
        {
            "entity_count": len(catalog.entities),
            "relationship_count": len(catalog.relationships),
            "dimension_count": len(catalog.dimensions),
            "measure_count": len(catalog.measures),
            "metric_count": len(catalog.metrics),
            "catalog_sha256": _json_hash(catalog.model_dump(mode="json")),
            "public_glossary_sha256": sha256(public_metric_glossary(catalog).encode()).hexdigest(),
        },
    )
    write_json(
        args.run_dir / "benchmark_manifest.json",
        {
            "target_count": len(targets),
            "case_count": sum(case.split == args.split for case in cases),
            "benchmark_sha256": benchmark_hash(targets, cases),
            "target_fixture_sha256": _file_hash(Path("evaluation/fixtures/m3_targets.json")),
            "case_fixture_sha256": _file_hash(Path("evaluation/fixtures/m3_cases.json")),
        },
    )
    write_json(args.run_dir / "reference_validation.json", reference_validation)
    write_json(args.run_dir / "slot_analysis.json", _slot_analysis(governed))
    write_json(args.run_dir / "pairwise.json", _pairwise(direct, governed))
    write_json(
        args.run_dir / "failure_analysis.json",
        {
            "direct": dict(Counter(row.get("failure", "NONE") for row in direct)),
            "governed": dict(Counter(row.get("failure", "NONE") for row in governed)),
        },
    )
    write_json(
        args.run_dir / "cost_analysis.json", {"direct": _cost(direct), "governed": _cost(governed)}
    )


def _slot_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    grounded = [row for row in rows if row.get("metric_name_exact") and row.get("dimensions_exact")]
    return {
        "transport": f"{sum('grounding' in row for row in rows)}/{total}",
        "metric_name_exact": f"{sum(bool(row.get('metric_name_exact')) for row in rows)}/{total}",
        "dimensions_exact": f"{sum(bool(row.get('dimensions_exact')) for row in rows)}/{total}",
        "adapter_success": f"{sum(bool(row.get('adapter_success')) for row in rows)}/{total}",
        "compiler_success": f"{sum(bool(row.get('compiler_success')) for row in rows)}/{total}",
        "m1_plan_accepted": f"{sum(bool(row.get('plan_accepted')) for row in rows)}/{total}",
        "execution_success": f"{sum(bool(row.get('execution_success')) for row in rows)}/{total}",
        "result_equivalence": f"{sum(bool(row.get('correct')) for row in rows)}/{total}",
        "correct_grounding_to_correct_result": (
            f"{sum(bool(row.get('correct')) for row in grounded)}/{len(grounded)}"
        ),
    }


def _pairwise(direct: list[dict[str, Any]], governed: list[dict[str, Any]]) -> dict[str, int]:
    governed_by_id = {row["case_id"]: row for row in governed}
    result = {"BOTH_CORRECT": 0, "DIRECT_ONLY": 0, "GOVERNED_ONLY": 0, "BOTH_INCORRECT": 0}
    for row in direct:
        direct_correct = bool(row.get("correct"))
        governed_correct = bool(governed_by_id[row["case_id"]].get("correct"))
        key = (
            "BOTH_CORRECT"
            if direct_correct and governed_correct
            else "DIRECT_ONLY"
            if direct_correct
            else "GOVERNED_ONLY"
            if governed_correct
            else "BOTH_INCORRECT"
        )
        result[key] += 1
    return result


def _cost(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = [row["prompt_tokens"] for row in rows if isinstance(row.get("prompt_tokens"), int)]
    completion = [
        row["completion_tokens"] for row in rows if isinstance(row.get("completion_tokens"), int)
    ]
    latency = [row["latency_ms"] for row in rows if isinstance(row.get("latency_ms"), (int, float))]
    return {
        "prompt_tokens_total": sum(prompt),
        "completion_tokens_total": sum(completion),
        "prompt_tokens_average": mean(prompt) if prompt else None,
        "completion_tokens_average": mean(completion) if completion else None,
        "latency_average_ms": mean(latency) if latency else None,
        "latency_p50_ms": median(latency) if latency else None,
        "latency_p95_ms": _percentile(latency, 0.95),
    }


def _percentile(values: list[float | int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _json_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
