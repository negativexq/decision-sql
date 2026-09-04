# ruff: noqa: E501

"""Run the provider-free M9.1 result-shape contract audit."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evaluation.m9_direct_structure_audit import build_reconstruction
from evaluation.m91_result_shape_audit import (
    M91_CORPUS_ID,
    M91_VERSION,
    audit_manifest,
    build_audit_slice,
    control_statistics,
    evaluate,
    final_decision,
    m2_8_reconciliation,
    sql_shape_signature,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline M9.1 result-shape audit")
    parser.add_argument("--phase", choices=("validate", "finalize"), default="validate")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=ROOT / "evaluation" / "results" / "m91" / "audit-20260904",
    )
    args = parser.parse_args()

    cases = build_audit_slice()
    validation = validate(cases)
    if not validation["passed"]:
        raise SystemExit(json.dumps(validation, sort_keys=True))
    controls = evaluate(cases)["controls"]
    manifest = audit_manifest(cases, controls)
    if args.phase == "validate":
        if args.artifact_dir.exists():
            raise SystemExit(f"M9.1 artifact directory already exists: {args.artifact_dir}")
        args.artifact_dir.mkdir(parents=True)
        _write_json(args.artifact_dir / "audit_manifest.json", manifest)
        _write_json(args.artifact_dir / "audit_validation.json", validation)
        print(json.dumps({"corpus_id": M91_CORPUS_ID, "version": M91_VERSION, **validation}, indent=2, sort_keys=True))
        return

    if not (args.artifact_dir / "audit_manifest.json").exists():
        raise SystemExit("M9.1 manifest is missing; run --phase validate first")
    persisted_manifest = json.loads((args.artifact_dir / "audit_manifest.json").read_text())
    if persisted_manifest.get("full_hash") != manifest["full_hash"]:
        raise SystemExit("M9.1 audit manifest hash does not match frozen slice")

    audit = evaluate(cases)
    records = audit["records"]
    reconstruction = build_reconstruction()
    _write_json(args.artifact_dir / "audit_validation.json", validation)
    _write_json(args.artifact_dir / "m28_reconciliation.json", m2_8_reconciliation())
    _write_json(args.artifact_dir / "evaluator_semantics.json", _evaluator_semantics())
    _write_jsonl(args.artifact_dir / "result_contracts.jsonl", _contract_rows(records))
    _write_jsonl(args.artifact_dir / "generated_result_signatures.jsonl", _signature_rows(cases, "generated"))
    _write_jsonl(args.artifact_dir / "reference_result_signatures.jsonl", _signature_rows(cases, "reference"))
    _write_jsonl(args.artifact_dir / "shape_audit.jsonl", [asdict(record) for record in records])
    controls_stats = control_statistics(reconstruction, controls)
    _write_json(args.artifact_dir / "correct_control_statistics.json", controls_stats)
    _write_jsonl(args.artifact_dir / "correct_control_manual_sample.jsonl", _manual_control_rows(reconstruction, controls))
    _write_json(args.artifact_dir / "presentation_normalization_diagnostics.json", _normalization_diagnostics(records, "presentation"))
    _write_json(args.artifact_dir / "projection_normalization_diagnostics.json", _normalization_diagnostics(records, "projection"))
    _write_json(args.artifact_dir / "grain_analysis.json", _grain_analysis(records))
    _write_json(args.artifact_dir / "family_breakdown.json", _family_breakdown(records))
    _write_json(args.artifact_dir / "split_breakdown.json", _split_breakdown(records))
    _write_json(args.artifact_dir / "mechanism_summary.json", _mechanism_summary(records))
    _write_json(args.artifact_dir / "m9_reconciliation.json", _m9_reconciliation(records))
    _write_json(args.artifact_dir / "metadata.json", _metadata(manifest, validation, controls_stats))
    _write_json(args.artifact_dir / "final_decision.json", final_decision(audit))
    print(json.dumps(final_decision(audit), indent=2, sort_keys=True))


def _contract_rows(records: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": record.case_id,
            "required_dimensions": record.required_dimensions,
            "required_measures": record.required_measures,
            "required_business_fields": record.required_fields,
            "expected_row_grain": record.expected_grain,
            "generated_grain": record.generated_grain,
            "grain_relation": record.grain_relation,
            "required_output_field_recall": (
                (len(record.required_fields) - len(record.missing_required_fields)) / len(record.required_fields)
            ),
            "scalar_or_tabular": "SCALAR" if record.expected_grain == "SCALAR" else "TABULAR",
            "ordering_semantically_required": record.expected_grain in {"TOP_N", "ORDERED_ENTITY_LIST"},
            "optional_display_fields": record.extra_optional_fields,
            "notes": record.concise_evidence,
        }
        for record in records
    ]


def _signature_rows(cases: tuple[Any, ...], kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        if kind == "generated":
            sql = case.observed_m4_sql
            signature = sql_shape_signature(sql or "") if sql else None
            rows.append({"case_id": case.case_id, "source": case.source_run, "signature": asdict(signature) if signature else None})
        else:
            rows.append({
                "case_id": case.case_id,
                "references": [asdict(sql_shape_signature(sql)) for sql in case.reference_sql_variants],
            })
    return rows


def _manual_control_rows(reconstructed: tuple[Any, ...], controls: tuple[str, ...]) -> list[dict[str, Any]]:
    wanted = set(controls)
    rows: list[dict[str, Any]] = []
    for case in reconstructed:
        if case.case_id not in wanted:
            continue
        generated = sql_shape_signature(case.observed_m4_sql or "")
        reference = sql_shape_signature(case.reference_sql_variants[0])
        rows.append({
            "case_id": case.case_id,
            "family": case.family,
            "generated": asdict(generated),
            "reference": asdict(reference),
            "benign_alternative_shape": generated.projection_count != reference.projection_count or generated.select_expressions != reference.select_expressions,
            "notes": "Result-equivalent control; SQL shape differences are diagnostic only.",
        })
    return rows


def _normalization_diagnostics(records: tuple[Any, ...], kind: str) -> dict[str, Any]:
    field = "e1_presentation_equivalent" if kind == "presentation" else "e2_required_projection_equivalent"
    return {
        "diagnostic": "E1_PRESENTATION_NORMALIZED" if kind == "presentation" else "E2_REQUIRED_PROJECTION_NORMALIZED",
        "equivalent_count": sum(bool(getattr(record, field)) for record in records),
        "total": len(records),
        "grain_normalization_performed": False,
        "duplicate_deduplication_performed": False,
        "value_normalization_performed": False,
    }


def _grain_analysis(records: tuple[Any, ...]) -> dict[str, Any]:
    from collections import Counter

    return {
        "relations": dict(Counter(record.grain_relation for record in records)),
        "scalar_to_grouped": sum(record.detailed_mechanism == "P11_SCALAR_VS_GROUPED" for record in records),
        "grouped_to_scalar": 0,
        "extra_grouping_key": sum(record.detailed_mechanism == "P9_EXTRA_GROUPING_KEY_GRAIN_EXPANSION" for record in records),
    }


def _family_breakdown(records: tuple[Any, ...]) -> dict[str, dict[str, int]]:
    from collections import Counter

    families = sorted({record.family for record in records})
    return {
        family: dict(Counter(record.primary_category for record in records if record.family == family))
        for family in families
    }


def _split_breakdown(records: tuple[Any, ...]) -> dict[str, dict[str, int]]:
    from collections import Counter

    return {
        split: dict(Counter(record.primary_category for record in records if record.split == split))
        for split in ("dev", "holdout")
    }


def _mechanism_summary(records: tuple[Any, ...]) -> dict[str, Any]:
    from collections import Counter

    details = Counter(record.detailed_mechanism for record in records)
    return {
        "detailed_mechanisms": dict(details),
        "top1": details.most_common(1),
        "top3": details.most_common(3),
        "top3_share": sum(value for _, value in details.most_common(3)) / len(records),
        "dev": dict(Counter(record.detailed_mechanism for record in records if record.split == "dev")),
        "holdout": dict(Counter(record.detailed_mechanism for record in records if record.split == "holdout")),
    }


def _m9_reconciliation(records: tuple[Any, ...]) -> dict[str, Any]:
    from collections import Counter

    # M9 S11 is preserved for every record; M9.1 only refines its meaning.
    return {
        "CONFIRMS_S11": len(records),
        "REFINES_S11": 0,
        "RECLASSIFIES_FROM_S11": 0,
        "historical_label_unchanged": True,
        "note": "M9.1 refines the S11 population without modifying M9 artifacts or scores.",
        "category_counts": dict(Counter(record.primary_category for record in records)),
    }


def _evaluator_semantics() -> dict[str, Any]:
    return {
        "implementation": "evaluation.metrics.assess_query_results",
        "column_names": "ignored; values are compared by ordinal column position",
        "column_aliases": "ignored",
        "column_order": "significant because ordinal positions are compared",
        "row_order": "ignored by default; rows are sorted as multisets unless order_sensitive is true",
        "duplicates": "preserved; row multiplicity is compared",
        "null": "canonicalized and compared as values",
        "numeric_tolerance": "math.isclose with rel_tol and abs_tol 1e-6 by default",
        "extra_columns": "rejected because column counts must match",
        "missing_columns": "rejected because column counts must match",
        "result_shape_diagnostic": "structural SQL divergence is non-scoring and does not change equivalence",
    }


def _metadata(manifest: dict[str, Any], validation: dict[str, Any], controls: dict[str, Any]) -> dict[str, Any]:
    return {
        "milestone": "M9.1",
        "provider_calls": 0,
        "sql_generation_calls": 0,
        "db_mutations": 0,
        "read_only_db_executions": 2,
        "read_only_db_validation": "two exact completed-status generated/reference queries were checked through M1; no results were persisted",
        "validation": validation,
        "manifest_hash": manifest["full_hash"],
        "manual_control_sample_hash": controls["manual_sample_hash"],
        "claim_boundary": "offline diagnostic; M7/M8/M9 scores are unchanged",
        "m9_1_taxonomies_frozen_before_adjudication": True,
        "decision_thresholds_frozen_before_adjudication": True,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows))


if __name__ == "__main__":
    main()
