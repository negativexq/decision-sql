"""Replay the persisted M2 run with the corrected result-equivalence contract."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.catalog.default import build_default_catalog
from app.config import get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.sql.models import (
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)
from app.sql.service import SqlSafetyService
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase, BaselineReport
from evaluation.runner import load_baseline

SOURCE_RUN = Path("evaluation/results/m2/m2-real-20260903")
OUTPUT_DIR = SOURCE_RUN / "recomputed"
DATASET = Path("evaluation/datasets/m2_baseline.json")
EVALUATOR_VERSION = "m2-result-equivalence-v2-ordinal-columns"


def main() -> None:
    settings = get_settings()
    catalog = build_default_catalog(Base.metadata)
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings, catalog=catalog)
    cases = {case.id: case for case in load_baseline(DATASET)}
    forensic = json.loads((SOURCE_RUN / "failure_analysis.json").read_text())
    forensic_cases = forensic["cases"]
    original_reports = {
        mode: BaselineReport.model_validate(
            json.loads((SOURCE_RUN / f"{mode}_schema.json").read_text())
        )
        for mode in ("full", "retrieved")
    }

    corrected: dict[str, dict[str, Any]] = {}
    for mode in ("full", "retrieved"):
        corrected[mode] = {
            "evaluator_version": EVALUATOR_VERSION,
            "source_run_id": "m2-real-20260903",
            "mode": mode,
            "cases": [
                _recompute_case(
                    case=cases[case_id],
                    original=next(
                        item for item in original_reports[mode].cases if item.id == case_id
                    ),
                    forensic_case=next(
                        item for item in forensic_cases[mode] if item["id"] == case_id
                    ),
                    safety=safety,
                    mode=mode,
                )
                for case_id in sorted(cases)
            ],
        }
        corrected[mode]["summary"] = _mode_summary(corrected[mode]["cases"])

    evaluator_change = _evaluator_change(original_reports)
    comparison = _comparison_payload(corrected, original_reports, evaluator_change)
    failure_analysis = _failure_analysis_payload(corrected, comparison, evaluator_change)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for mode in ("full", "retrieved"):
        _write_json(OUTPUT_DIR / f"{mode}_schema.json", corrected[mode])
    _write_json(OUTPUT_DIR / "comparison.json", comparison)
    _write_json(OUTPUT_DIR / "evaluator_change.json", evaluator_change)
    _write_json(OUTPUT_DIR / "failure_analysis.json", failure_analysis)
    print(json.dumps({"output_dir": str(OUTPUT_DIR), "comparison": comparison}))


def _recompute_case(
    *,
    case: BaselineCase,
    original: Any,
    forensic_case: dict[str, Any],
    safety: SqlSafetyService,
    mode: str,
) -> dict[str, Any]:
    generated = _replay(safety, original.generated_sql, f"recomputed-{mode}-{case.id}-generated")
    gold = _replay(safety, case.gold_sql, f"recomputed-{mode}-{case.id}-gold")
    comparison = None
    if isinstance(generated, QueryExecution) and isinstance(gold, QueryExecution):
        comparison = assess_query_results(
            generated,
            gold,
            order_sensitive=case.order_sensitive,
            actual_sql=original.generated_sql,
            expected_sql=case.gold_sql,
        )
    equivalent = comparison.equivalent if comparison else False
    old_class = forensic_case["primary_failure_class"]
    if equivalent:
        corrected_class = None
        relevance = None
    else:
        corrected_class = old_class
        if corrected_class == "GOLD_OR_EVAL_ISSUE":
            raise RuntimeError(
                f"Corrected evaluator left an unclassified failure: {mode}/{case.id}"
            )
        relevance = _roadmap_relevance(corrected_class)
    diagnostic = comparison.diagnostic if comparison else None
    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "gold_sql": case.gold_sql,
        "generated_sql": original.generated_sql,
        "original_result_equivalent": original.result_equivalent,
        "original_failure_class": old_class,
        "corrected_result_equivalent": equivalent,
        "equivalence_diagnostic": diagnostic.value if diagnostic else None,
        "original_failure_attribution": original.failure_attribution,
        "corrected_failure_attribution": None if equivalent else "RESULT_INCORRECT",
        "primary_failure_class": corrected_class,
        "roadmap_relevance": relevance,
        "generated": _execution_payload(generated),
        "gold": _execution_payload(gold),
        "context_adequacy": forensic_case.get("context_adequacy"),
    }


def _replay(
    safety: SqlSafetyService, sql: str | None, correlation_id: str
) -> QueryExecution | SqlPlanFailure | SqlExecutionError:
    if not sql:
        return SqlExecutionError(error="Persisted generated SQL is missing.")
    planned = safety.plan(SqlCandidate(sql=sql, correlation_id=correlation_id))
    if not isinstance(planned, QueryPlan):
        return planned
    return safety.execute(planned)


def _execution_payload(
    value: QueryExecution | SqlPlanFailure | SqlExecutionError,
) -> dict[str, Any]:
    if isinstance(value, QueryExecution):
        return {"status": "success", **value.model_dump(mode="json")}
    return {"status": "error", **value.model_dump(mode="json")}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _mode_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    equivalent = sum(row["corrected_result_equivalent"] for row in rows)
    executions = sum(row["generated"]["status"] == "ALLOWED" for row in rows)
    warnings = sum(
        row["equivalence_diagnostic"] == "STRUCTURAL_DIVERGENCE" for row in rows
    )
    failures = Counter(
        row["primary_failure_class"] for row in rows if row["primary_failure_class"]
    )
    relevance = Counter(row["roadmap_relevance"] for row in rows if row["roadmap_relevance"])
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    return {
        "total_questions": total,
        "result_equivalence_count": equivalent,
        "result_equivalence_rate": equivalent / total if total else 0.0,
        "execution_success_count": executions,
        "structural_warning_count": warnings,
        "corrected_false_negative_count": sum(
            row["original_result_equivalent"] is False
            and row["corrected_result_equivalent"] is True
            for row in rows
        ),
        "alias_false_negative_count": sum(
            row["original_failure_class"] == "GOLD_OR_EVAL_ISSUE" for row in rows
        ),
        "failure_taxonomy": dict(sorted(failures.items())),
        "roadmap_relevance": dict(sorted(relevance.items())),
        "category_breakdown": {
            category: {
                "total": len(items),
                "result_equivalence_count": sum(
                    item["corrected_result_equivalent"] for item in items
                ),
                "result_equivalence_rate": sum(
                    item["corrected_result_equivalent"] for item in items
                )
                / len(items),
            }
            for category, items in sorted(categories.items())
        },
    }


def _evaluator_change(reports: dict[str, BaselineReport]) -> dict[str, Any]:
    return {
        "source_run_id": "m2-real-20260903",
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "original_evaluator": {
            "version": "historical-unversioned-m2-result-equivalence-v1",
            "behavior": "Required identical output column-name sets before comparing rows.",
        },
        "corrected_evaluator": {
            "version": EVALUATOR_VERSION,
            "behavior": (
                "Requires equal arity, compares values by ordinal position, preserves "
                "duplicates, supports order-sensitive or unordered multiset comparison, "
                "normalizes NULL/date/numeric values, and applies numeric tolerance."
            ),
        },
        "reason": "Output aliases are not part of ordinary Text-to-SQL result correctness.",
        "no_new_llm_calls": True,
        "original_artifacts_unchanged": True,
        "original_artifact_sha256": {
            name: hashlib.sha256((SOURCE_RUN / name).read_bytes()).hexdigest()
            for name in ("full_schema.json", "retrieved_schema.json", "comparison.json")
        },
        "original_report_counts": {
            mode: reports[mode].result_equivalence_count for mode in reports
        },
    }


def _comparison_payload(
    corrected: dict[str, dict[str, Any]],
    original_reports: dict[str, BaselineReport],
    evaluator_change: dict[str, Any],
) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for mode in ("full", "retrieved"):
        old = original_reports[mode]
        new = corrected[mode]["summary"]
        rows[mode] = {
            "result_equivalence": _metric(
                old.result_equivalence_count, new["result_equivalence_count"], old.total_cases
            ),
            "execution_success": _metric(
                old.execution_success_count, new["execution_success_count"], old.total_cases
            ),
            "structural_fixture_warnings": new["structural_warning_count"],
            "corrected_false_negative_count": new["corrected_false_negative_count"],
            "alias_false_negative_count": new["alias_false_negative_count"],
            "parse_success_count": old.parse_success_count,
            "plan_acceptance_count": old.plan_acceptance_count,
            "failure_taxonomy": new["failure_taxonomy"],
            "category_breakdown": new["category_breakdown"],
            "roadmap_relevance": new["roadmap_relevance"],
        }
    return {
        "source_run_id": "m2-real-20260903",
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_sha256": evaluator_change["dataset_sha256"],
        "no_new_llm_calls": True,
        "modes": rows,
    }


def _failure_analysis_payload(
    corrected: dict[str, dict[str, Any]],
    comparison: dict[str, Any],
    evaluator_change: dict[str, Any],
) -> dict[str, Any]:
    return {
        "audit_metadata": {
            "source_run_id": "m2-real-20260903",
            "evaluator_version": EVALUATOR_VERSION,
            "dataset_sha256": evaluator_change["dataset_sha256"],
            "no_new_llm_calls": True,
            "original_artifacts_unchanged": True,
        },
        "summary": {
            "failure_taxonomy_by_mode": {
                mode: corrected[mode]["summary"]["failure_taxonomy"]
                for mode in ("full", "retrieved")
            },
            "roadmap_relevance_by_mode": {
                mode: corrected[mode]["summary"]["roadmap_relevance"]
                for mode in ("full", "retrieved")
            },
            "structural_warning_count_by_mode": {
                mode: corrected[mode]["summary"]["structural_warning_count"]
                for mode in ("full", "retrieved")
            },
            "corrected_false_negative_count_by_mode": {
                mode: corrected[mode]["summary"]["corrected_false_negative_count"]
                for mode in ("full", "retrieved")
            },
            "alias_false_negative_count_by_mode": {
                mode: corrected[mode]["summary"]["alias_false_negative_count"]
                for mode in ("full", "retrieved")
            },
        },
        "comparison": comparison,
        "cases": {mode: corrected[mode]["cases"] for mode in ("full", "retrieved")},
    }


def _metric(original_count: int, corrected_count: int, total: int) -> dict[str, Any]:
    original_rate = original_count / total if total else 0.0
    corrected_rate = corrected_count / total if total else 0.0
    return {
        "original_count": original_count,
        "original_rate": original_rate,
        "corrected_count": corrected_count,
        "corrected_rate": corrected_rate,
        "delta_count": corrected_count - original_count,
        "delta_rate": corrected_rate - original_rate,
    }


def _roadmap_relevance(class_name: str) -> str:
    if class_name in {"WRONG_RATIO_DENOMINATOR", "WRONG_RATIO_SCALING"}:
        return "B_SEMANTIC_BUSINESS_CONTRACT"
    return "A_BASIC_GENERATION_SCHEMA_REASONING"


if __name__ == "__main__":
    main()
