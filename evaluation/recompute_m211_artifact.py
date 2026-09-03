"""Offline correction for an M2.11 run persisted before accounting fixes."""

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.m27_failure_mechanism import analyze_row
from evaluation.run_m211 import (
    _category_analysis,
    _comparison,
    _failure_analysis,
    _model_io_rows,
    _plan_quality,
    _policy_shadow,
    _summary,
)


def _correct_row(row: dict[str, Any]) -> dict[str, Any]:
    corrected = dict(row)
    has_plan = bool(row.get("operation_plan") or row.get("plan_provider_error"))
    plan_succeeded = bool(row.get("operation_plan"))
    sql_attempted = bool(row.get("sql_model_io") or row.get("generated_sql"))
    corrected.update(
        {
            "failure_stage": (
                None
                if row.get("generated_sql") and row.get("status") == "SUCCEEDED"
                else (
                    "PLAN_PROVIDER_FAILURE"
                    if row.get("plan_provider_error")
                    else row.get("failure_stage")
                )
            ),
            "plan_calls_attempted": int(has_plan),
            "plan_calls_succeeded": int(plan_succeeded),
            "plan_calls_failed": int(has_plan and not plan_succeeded),
            "sql_calls_attempted": int(sql_attempted),
            "sql_calls_succeeded": int(sql_attempted and row.get("generated_sql") is not None),
            "sql_calls_failed": int(sql_attempted and row.get("generated_sql") is None),
            "plan_input_tokens": (
                (row.get("plan_provider_metadata") or {}).get("prompt_tokens")
            ),
            "plan_output_tokens": (
                (row.get("plan_provider_metadata") or {}).get("completion_tokens")
            ),
            "plan_latency_ms": (row.get("plan_provider_metadata") or {}).get("latency_ms"),
        }
    )
    corrected["provider_calls_attempted"] = (
        corrected["plan_calls_attempted"] + corrected["sql_calls_attempted"]
    )
    corrected["provider_calls_succeeded"] = (
        corrected["plan_calls_succeeded"] + corrected["sql_calls_succeeded"]
    )
    corrected["provider_calls_failed"] = (
        corrected["plan_calls_failed"] + corrected["sql_calls_failed"]
    )
    analysis = analyze_row(corrected)
    corrected["primary_root_cause"] = analysis["primary_root_cause"]
    corrected["secondary_tags"] = analysis["secondary_tags"]
    return corrected


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct M2.11 accounting offline")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    source = args.run_dir
    output = source / "corrected-v2"
    output.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((source / "baseline.json").read_text())
    decomposed = json.loads((source / "decomposed.json").read_text())
    baseline["cases"] = [_correct_row(row) for row in baseline["cases"]]
    decomposed["cases"] = [_correct_row(row) for row in decomposed["cases"]]
    baseline = _summary("BASELINE", baseline["cases"])
    decomposed = _summary("DECOMPOSED", decomposed["cases"])
    metadata = json.loads((source / "metadata.json").read_text())
    metadata.update(
        {
            "artifact_status": "CORRECTED_OFFLINE_ACCOUNTING_AND_STAGE_LABELS",
            "source_artifact": str(source),
            "provider_calls_attempted": (
                baseline["provider_calls_attempted"] + decomposed["provider_calls_attempted"]
            ),
            "provider_calls_succeeded": (
                baseline["provider_calls_succeeded"] + decomposed["provider_calls_succeeded"]
            ),
            "provider_calls_failed": (
                baseline["provider_calls_failed"] + decomposed["provider_calls_failed"]
            ),
        }
    )
    payloads = {
        "metadata.json": metadata,
        "baseline.json": baseline,
        "decomposed.json": decomposed,
        "comparison.json": _comparison(baseline, decomposed),
        "plan_quality.json": _plan_quality(decomposed["cases"]),
        "failure_analysis.json": _failure_analysis(baseline, decomposed),
        "policy_shadow_analysis.json": _policy_shadow(decomposed["cases"]),
        "category_analysis.json": _category_analysis(baseline, decomposed),
    }
    for name, payload in payloads.items():
        (output / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    (output / "model_io.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n"
            for row in _model_io_rows(baseline, decomposed)
        )
    )
    print(output)


if __name__ == "__main__":
    main()
