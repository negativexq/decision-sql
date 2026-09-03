"""Recompute M2.7 diagnostics from persisted model outputs without provider calls."""

import argparse
import json
from pathlib import Path
from typing import Any

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from evaluation.m27_forensics import (
    classify_forensic_cause,
    column_confusions,
    context_visibility,
    sql_signature,
    structural_diff,
)
from evaluation.models import BaselineCase
from evaluation.run_m27 import (
    MODELS,
    _assert_paired_inputs,
    _comparison,
    _failure_analysis,
    _render_report,
    _summarize,
    _write_json,
    _write_model_io,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay persisted M2.7 diagnostics")
    parser.add_argument("source", type=Path)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    cases = {
        case.id: case
        for case in (
            BaselineCase.model_validate(item)
            for item in json.loads(args.dataset.read_text())
        )
    }
    catalog = build_default_catalog(Base.metadata)
    resolver = SchemaContextResolver(catalog)
    reports: dict[str, dict[str, Any]] = {}
    for model in MODELS:
        source_name = "luna.json" if model.endswith("luna") else "terra.json"
        original = json.loads((args.source / source_name).read_text())
        rows = []
        for row in original["cases"]:
            case = cases[row["id"]]
            context = resolver.resolve(case.question, mode=SchemaContextMode.FULL_COMPACT)
            gold_signature = sql_signature(case.gold_sql)
            generated_signature = (
                sql_signature(row["generated_sql"]) if row["generated_sql"] else None
            )
            diff = (
                structural_diff(gold_signature, generated_signature)
                if generated_signature is not None
                else {}
            )
            visibility = context_visibility(gold_signature, context)
            row = {
                **row,
                "gold_sql": case.gold_sql,
                "gold_signature": gold_signature,
                "generated_signature": generated_signature,
                "structural_diff": diff,
                "context_visibility": visibility,
                "column_confusions": (
                    column_confusions(gold_signature, generated_signature, context)
                    if generated_signature is not None
                    else []
                ),
                "forensic_cause": classify_forensic_cause(
                    generated_sql=row["generated_sql"],
                    failure_stage=row["failure_stage"],
                    gold_signature=gold_signature,
                    generated_signature=generated_signature,
                    diff=diff,
                    visibility=visibility,
                    equivalent=row["result_equivalent"],
                ),
            }
            rows.append(row)
        reports[model] = _summarize(model, rows)

    _assert_paired_inputs(reports)
    metadata = json.loads((args.source / "metadata.json").read_text())
    metadata["diagnostic_replay"] = {
        "source_run": str(args.source),
        "reason": "Corrected unqualified gold-column normalization in offline forensic analysis.",
        "provider_calls": 0,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    _write_json(args.output / "metadata.json", metadata)
    for model, report in reports.items():
        filename = "luna.json" if model.endswith("luna") else "terra.json"
        _write_json(args.output / filename, report)
        _write_model_io(args.output / f"{filename.removesuffix('.json')}_model_io.jsonl", report)
    comparison = _comparison(reports)
    _write_json(args.output / "comparison.json", comparison)
    _write_json(args.output / "failure_analysis.json", _failure_analysis(reports, metadata))
    args.report.write_text(_render_report(metadata, reports, comparison), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output), "report": str(args.report)}))


if __name__ == "__main__":
    main()
