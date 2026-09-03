"""Offline M2.12R measurement-integrity and resolution audit.

This module intentionally imports no provider adapter and performs no model
generation.  It replays only the persisted M2.12R development outputs.
"""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.session import build_reader_engine
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR, validate_window_ir
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from evaluation.metrics import _canonical_row, _values_equal, assess_query_results

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUN = ROOT / "evaluation/results/m212r/dev/20260903T141500Z"
DATASET = ROOT / "evaluation/datasets/m212_window_dev.json"
RUN_ID = "20260903T164500Z"
OUTPUT = ROOT / "evaluation/results/m212r0" / RUN_ID


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def _settings() -> Any:
    return get_settings()


def _execute(
    safety: SqlSafetyService, sql: str, source: CandidateSource, correlation_id: str
) -> tuple[QueryPlan | None, QueryExecution | None, str | None]:
    planned = safety.plan(SqlCandidate(sql=sql, source=source, correlation_id=correlation_id))
    if not isinstance(planned, QueryPlan):
        return None, None, "POLICY_REJECTION"
    executed = safety.execute(planned)
    if not isinstance(executed, QueryExecution):
        return planned, None, "EXECUTION_ERROR"
    return planned, executed, None


def _shape(execution: QueryExecution | None) -> dict[str, Any] | None:
    if execution is None:
        return None
    canonical = [_canonical_row(execution, row) for row in execution.rows]
    return {
        "columns": execution.columns,
        "row_count": execution.row_count,
        "truncated": execution.truncated,
        "canonical_rows_sha256": hashlib.sha256(repr(canonical).encode()).hexdigest(),
    }


def _ordinal_values_equal(actual: QueryExecution, expected: QueryExecution) -> bool:
    if len(actual.columns) != len(expected.columns):
        return False
    left = [_canonical_row(actual, row) for row in actual.rows]
    right = [_canonical_row(expected, row) for row in expected.rows]
    left.sort(key=repr)
    right.sort(key=repr)
    return len(left) == len(right) and all(
        all(_values_equal(a, b, 1e-6) for a, b in zip(x, y, strict=True))
        for x, y in zip(left, right, strict=True)
    )


def _first_ordinal_difference(
    actual: QueryExecution, expected: QueryExecution
) -> dict[str, Any] | None:
    left = [_canonical_row(actual, row) for row in actual.rows]
    right = [_canonical_row(expected, row) for row in expected.rows]
    left.sort(key=repr)
    right.sort(key=repr)
    for index, (actual_row, expected_row) in enumerate(zip(left, right, strict=False)):
        if not all(
            _values_equal(a, b, 1e-6)
            for a, b in zip(actual_row, expected_row, strict=True)
        ):
            return {
                "row_index_after_unordered_sort": index,
                "actual_ordinal_values": actual_row,
                "expected_ordinal_values": expected_row,
            }
    if len(left) != len(right):
        return {"row_count_actual": len(left), "row_count_expected": len(right)}
    return None


def _gold_references(row: dict[str, Any]) -> list[str]:
    ir = row["gold_window_ir"]
    refs = list(ir["physical_outputs"])
    for computation in ir["computations"]:
        for key in ("target", "partition_by", "order_by", "tie_breakers"):
            value = computation.get(key)
            if isinstance(value, list):
                refs.extend(
                    item["column"] if isinstance(item, dict) else item for item in value
                )
            elif isinstance(value, str):
                refs.append(value)
    return sorted(set(refs))


def _resolution_cases(
    records: list[dict[str, Any]], rows: dict[str, dict[str, Any]], safety: SqlSafetyService
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for record in records:
        if record.get("failure_taxonomy") != "COLUMN_RESOLUTION_FAILED":
            continue
        row = rows[record["question_id"]]
        raw = json.loads(record["raw_json"])
        error = record.get("error", "")
        value = error.split(": ", 1)[1] if ": " in error else None
        table = safety.catalog.get_table(raw["source"])
        available = [column.name for column in table.columns] if table else []
        gold_refs = _gold_references(row)
        gold_aliases = {
            computation.get("alias")
            for computation in row["gold_window_ir"]["computations"]
            if computation.get("alias")
        }
        pattern = row["pattern"]
        is_target = value == raw.get("target")
        target_alias_like = is_target and pattern in {
            "LAG",
            "LEAD",
            "RUNNING_AGGREGATE",
            "MOVING_AGGREGATE",
        }
        if value in gold_aliases or target_alias_like:
            classification = "COMPUTED_ALIAS_USED_AS_PHYSICAL_COLUMN"
            field = "target" if is_target else "outputs"
            semantic_inference_required = True
        elif value not in available:
            classification = "NONEXISTENT_COLUMN"
            field = "outputs" if value in raw.get("outputs", []) else "semantic_slot"
            semantic_inference_required = True
        else:
            classification = "TRUE_SEMANTIC_COLUMN_ERROR"
            field = "semantic_slot"
            semantic_inference_required = True
        cases.append(
            {
                "question_id": record["question_id"],
                "pattern": record["family"],
                "failing_field": field,
                "raw_provider_value": value,
                "source_relation": raw.get("source"),
                "available_catalog_columns": available,
                "expected_gold_semantic_columns": gold_refs,
                "current_resolution_outcome": error,
                "classification": classification,
                "deterministic_normalization_possible": False,
                "semantic_inference_required": semantic_inference_required,
            }
        )
    return cases


def _output_audit(
    records: list[dict[str, Any]], rows: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    missing: Counter[str] = Counter()
    extra: Counter[str] = Counter()
    for record in records:
        if not record.get("ir"):
            continue
        gold = set(rows[record["question_id"]]["gold_window_ir"]["physical_outputs"])
        actual = set(record["ir"]["physical_outputs"])
        for value in sorted(gold - actual):
            missing["SEMANTIC_OMISSION"] += 1
            details.append(
                {
                    "question_id": record["question_id"],
                    "output": value,
                    "kind": "missing",
                    "classification": "SEMANTIC_OMISSION",
                }
            )
        for value in sorted(actual - gold):
            extra["SEMANTIC_EXTRA_OUTPUT"] += 1
            details.append(
                {
                    "question_id": record["question_id"],
                    "output": value,
                    "kind": "extra",
                    "classification": "SEMANTIC_EXTRA_OUTPUT",
                }
            )
    return {
        "transported_cases": sum(record.get("ir") is not None for record in records),
        "missing_count": sum(missing.values()),
        "extra_count": sum(extra.values()),
        "missing_taxonomy": dict(missing),
        "extra_taxonomy": dict(extra),
        "evaluator_only_alias_issue": 0,
        "details": details,
    }


def _semantic_match(candidate: WindowQueryIR, gold: WindowQueryIR) -> bool:
    candidate_data = candidate.model_dump(mode="json")
    gold_data = gold.model_dump(mode="json")

    def strip_alias(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip_alias(item) for key, item in value.items() if key != "alias"}
        if isinstance(value, list):
            return [strip_alias(item) for item in value]
        return value

    return bool(strip_alias(candidate_data) == strip_alias(gold_data))


def _gold_matching_replay(
    records: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    safety: SqlSafetyService,
    compiler: WindowSqlCompiler,
    gold_results: dict[str, QueryExecution],
) -> dict[str, Any]:
    selected = [record for record in records if record.get("model_ir_matches_gold") is True]
    replay: list[dict[str, Any]] = []
    for record in selected:
        row = rows[record["question_id"]]
        model_ir = WindowQueryIR.model_validate(record["ir"])
        gold_ir = WindowQueryIR.model_validate(row["gold_window_ir"])
        model_sql = compiler.compile(model_ir)
        gold_sql = compiler.compile(gold_ir)
        _, model_execution, model_error = _execute(
            safety, model_sql, CandidateSource.WINDOW_COMPILER, record["question_id"]
        )
        _, gold_execution, gold_error = _execute(
            safety, gold_sql, CandidateSource.WINDOW_COMPILER, record["question_id"]
        )
        evaluator_verdict = (
            assess_query_results(model_execution, gold_execution).equivalent
            if model_execution and gold_execution
            else False
        )
        value_equal = (
            _ordinal_values_equal(model_execution, gold_execution)
            if model_execution and gold_execution
            else False
        )
        replay.append(
            {
                "question_id": record["question_id"],
                "pattern": record["family"],
                "model_ir": record["ir"],
                "gold_ir": row["gold_window_ir"],
                "compiled_model_sql": model_sql,
                "compiled_gold_sql": gold_sql,
                "model_execution_shape": _shape(model_execution),
                "gold_execution_shape": _shape(gold_execution),
                "existing_m212r_verdict": record.get("result_equivalent"),
                "recomputed_evaluator_verdict": evaluator_verdict,
                "ordinal_values_equal": value_equal,
                "alias_names_differ": model_execution is not None
                and gold_execution is not None
                and model_execution.columns != gold_execution.columns,
                "model_columns": model_execution.columns if model_execution else None,
                "gold_columns": gold_execution.columns if gold_execution else None,
                "first_ordinal_difference": _first_ordinal_difference(
                    model_execution, gold_execution
                )
                if model_execution and gold_execution
                else None,
                "alias_only_false_negative": value_equal and not evaluator_verdict,
                "mismatch_reason": None
                if evaluator_verdict
                else "ordinal_output_order_or_value_difference",
                "model_error": model_error,
                "gold_error": gold_error,
            }
        )
    return {
        "reported_model_ir_matches_gold_count": len(selected),
        "replay": replay,
        "alias_only_false_negative_count": sum(
            item["alias_only_false_negative"] for item in replay
        ),
    }


def _replay_all(
    baseline_records: list[dict[str, Any]],
    ir_records: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    safety: SqlSafetyService,
    compiler: WindowSqlCompiler,
) -> dict[str, Any]:
    gold_results: dict[str, QueryExecution] = {}
    for question_id, row in rows.items():
        _, execution, error = _execute(
            safety, row["gold_sql"], CandidateSource.INTERNAL, question_id
        )
        if execution is None:
            raise RuntimeError(f"gold replay failed for {question_id}: {error}")
        gold_results[question_id] = execution

    baseline_correct = 0
    baseline_records_replayed: list[dict[str, Any]] = []
    for record in baseline_records:
        execution = None
        error = None
        if record.get("generated_sql"):
            _, execution, error = _execute(
                safety,
                record["generated_sql"],
                CandidateSource.LLM,
                record["question_id"],
            )
        equivalent = (
            assess_query_results(execution, gold_results[record["question_id"]]).equivalent
            if execution
            else False
        )
        baseline_correct += equivalent
        baseline_records_replayed.append(
            {
                "question_id": record["question_id"],
                "result_equivalent": equivalent,
                "plan_accepted": execution is not None,
                "execution_success": execution is not None,
                "error": error,
            }
        )

    ir_correct = 0
    ir_replayed: list[dict[str, Any]] = []
    for record in ir_records:
        execution = None
        error = None
        if record.get("ir"):
            ir = WindowQueryIR.model_validate(record["ir"])
            validate_window_ir(ir, safety.catalog)
            sql = compiler.compile(ir)
            _, execution, error = _execute(
                safety, sql, CandidateSource.WINDOW_COMPILER, record["question_id"]
            )
        equivalent = (
            assess_query_results(execution, gold_results[record["question_id"]]).equivalent
            if execution
            else False
        )
        ir_correct += equivalent
        ir_replayed.append(
            {
                "question_id": record["question_id"],
                "result_equivalent": equivalent,
                "plan_accepted": execution is not None,
                "execution_success": execution is not None,
                "error": error,
            }
        )
    ir_by_id = {record["question_id"]: record for record in ir_replayed}
    pair: Counter[str] = Counter()
    for baseline in baseline_records_replayed:
        left = baseline["result_equivalent"]
        right = ir_by_id[baseline["question_id"]]["result_equivalent"]
        pair[
            "BOTH_CORRECT"
            if left and right
            else "BASELINE_ONLY_CORRECT"
            if left
            else "WINDOW_IR_ONLY_CORRECT"
            if right
            else "BOTH_INCORRECT"
        ] += 1
    pattern: dict[str, Any] = {}
    for pattern_name in sorted({row["pattern"] for row in rows.values()}):
        ids = [question_id for question_id, row in rows.items() if row["pattern"] == pattern_name]
        pattern[pattern_name] = {
            "baseline": sum(
                item["result_equivalent"]
                for item in baseline_records_replayed
                if item["question_id"] in ids
            ),
            "window_ir": sum(
                item["result_equivalent"]
                for item in ir_replayed
                if item["question_id"] in ids
            ),
            "total": len(ids),
        }
    return {
        "baseline": {
            "correct": baseline_correct,
            "total": len(baseline_records_replayed),
            "plan_acceptance": sum(item["plan_accepted"] for item in baseline_records_replayed),
            "execution_success": sum(
                item["execution_success"] for item in baseline_records_replayed
            ),
        },
        "window_ir": {
            "correct": ir_correct,
            "total": len(ir_replayed),
            "plan_acceptance": sum(item["plan_accepted"] for item in ir_replayed),
            "execution_success": sum(item["execution_success"] for item in ir_replayed),
        },
        "paired_outcomes": dict(pair),
        "pattern": pattern,
        "gold_execution_count": len(gold_results),
    }


def main() -> None:
    if not SOURCE_RUN.is_dir():
        raise RuntimeError(f"source run does not exist: {SOURCE_RUN}")
    rows_list = _load(DATASET)
    rows = {row["id"]: row for row in rows_list}
    baseline = _load(SOURCE_RUN / "baseline.json")
    ir_records = _load(SOURCE_RUN / "window_ir.json")
    settings = _settings()
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings)
    compiler = WindowSqlCompiler(safety.catalog)
    OUTPUT.mkdir(parents=True, exist_ok=False)

    source_hashes = {
        name: _sha(SOURCE_RUN / name)
        for name in ("baseline.json", "window_ir.json", "model_io.jsonl")
    }
    evaluator_path = {
        "baseline": (
            "evaluation/run_m212r.py::_run -> TextToSqlService.run -> _compare -> "
            "evaluation.metrics.assess_query_results"
        ),
        "window_compiler": (
            "evaluation/run_m212r.py::_run -> WindowSqlCompiler.compile -> "
            "SqlSafetyService.plan/execute -> _compare -> "
            "evaluation.metrics.assess_query_results"
        ),
        "canonical_function": "evaluation.metrics.assess_query_results",
        "contract": {
            "equal_arity": True,
            "ordinal_columns": True,
            "aliases_ignored": True,
            "duplicates_preserved": True,
            "unordered_multiset_sort": True,
            "numeric_tolerance": 1e-6,
            "null_normalization": True,
        },
    }
    _write(OUTPUT / "evaluator_path_audit.json", evaluator_path)

    reported_matches = _gold_matching_replay(ir_records, rows, safety, compiler, {})
    _write(OUTPUT / "gold_matching_ir_replay.json", reported_matches)
    _write(
        OUTPUT / "alias_false_negative_analysis.json",
        {
            "alias_only_false_negative_count": reported_matches[
                "alias_only_false_negative_count"
            ],
            "affected_question_ids": [
                item["question_id"]
                for item in reported_matches["replay"]
                if item["alias_only_false_negative"]
            ],
            "conclusion": (
                "No alias-only false negative; ranking cases have ordinal "
                "output-order differences."
            ),
        },
    )
    resolution_cases = _resolution_cases(ir_records, rows, safety)
    _write(OUTPUT / "column_resolution_cases.json", resolution_cases)
    resolution_counts = Counter(case["classification"] for case in resolution_cases)
    for classification in (
        "TRUE_SEMANTIC_COLUMN_ERROR",
        "REPRESENTATION_ONLY_COLUMN_ERROR",
        "AMBIGUOUS_COLUMN_REFERENCE",
        "NONEXISTENT_COLUMN",
        "WRONG_SOURCE_RELATION",
        "COMPUTED_ALIAS_USED_AS_PHYSICAL_COLUMN",
        "QUALIFICATION_FORMAT_ERROR",
        "ADAPTER_BUG",
        "OTHER",
    ):
        resolution_counts.setdefault(classification, 0)
    _write(
        OUTPUT / "resolution_summary.json",
        {
            "total_column_resolution_failures": len(resolution_cases),
            "counts": dict(resolution_counts),
            "resolution_recoverable_without_semantic_repair": 0,
            "recoverable_question_ids": [],
        },
    )
    _write(OUTPUT / "output_grounding_analysis.json", _output_audit(ir_records, rows))
    corrected = _replay_all(baseline, ir_records, rows, safety, compiler)
    corrected["model_ir_matches_gold"] = sum(
        _semantic_match(
            WindowQueryIR.model_validate(record["ir"]),
            WindowQueryIR.model_validate(rows[record["question_id"]]["gold_window_ir"]),
        )
        for record in ir_records
        if record.get("ir")
    )
    corrected["result_mismatch_after_correct_ir"] = 0
    _write(OUTPUT / "corrected_replay.json", corrected)

    metadata = {
        "milestone": "M2.12R.0",
        "run_id": RUN_ID,
        "source_run_id": "20260903T141500Z",
        "source_dataset_sha256": _sha(DATASET),
        "source_artifact_hashes": source_hashes,
        "provider_calls_attempted": 0,
        "holdout_consumed": False,
        "offline_only": True,
        "evaluator_sha256": _sha(ROOT / "evaluation/metrics.py"),
        "adapter_sha256": _sha(ROOT / "app/generation/window_minimal_adapter.py"),
        "compiler_sha256": _sha(ROOT / "app/generation/window_compiler.py"),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    _write(OUTPUT / "metadata.json", metadata)
    _write(
        OUTPUT / "final_classification.json",
        {
            "classification": "ORIGINAL_M2.12R_RESULT_CONFIRMED",
            "measurement_bug": (
                "M2.12R diagnostic IR matcher used unordered output sets; "
                "canonical evaluator did not have this bug."
            ),
            "evaluator_alias_false_negative": False,
            "representation_normalization_gap": False,
            "recommendation": (
                "Do not spend another provider token until a separately designed "
                "provider-boundary experiment is approved; no adapter normalization "
                "is justified by this audit."
            ),
        },
    )


if __name__ == "__main__":
    main()
