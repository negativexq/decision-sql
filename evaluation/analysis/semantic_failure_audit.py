"""Offline cross-benchmark semantic failure mechanism audit.

This module deliberately imports no provider or generation code. It consumes
persisted benchmark outputs and pinned local benchmark source data only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.analysis.sql_ast_diff import compare_summaries, contains_any, summarize_sql
from evaluation.external.bird.benchmark import (
    BirdBenchmarkExecutor,
    BirdQuestion,
    sha256_file,
)
from evaluation.external.bird.benchmark import (
    load_questions as load_bird_questions,
)
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
)
from evaluation.external.defog.benchmark import (
    load_questions as load_defog_questions,
)

ROOT = Path(__file__).resolve().parents[2]
BIRD_RUN = ROOT / "evaluation/results/m214/dev/20260903T130000Z"
DEFOG_RUN = ROOT / "evaluation/results/m213/dev/20260903T120700Z"
BIRD_DATA_FILE = "mini_dev_postgresql.json"
RUN_VERSION = "m2141-semantic-failure-audit-v1"

# The BIRD temporal slice contains several failures whose gold SQL is temporal
# but whose persisted mismatch is caused by selection/projection or unrelated
# relational structure.  These are conservative manual adjudications, not
# model-generated labels.
TEMPORAL_MANUAL_ADJUDICATION: dict[int, tuple[str, str]] = {
    88: (
        "UNDETERMINED",
        "Temporal columns match; the mismatch is oldest-row selection logic.",
    ),
    90: (
        "WRONG_TIME_GRAIN",
        "Age is computed from patient birth year instead of examination year.",
    ),
    91: (
        "WRONG_TIME_COLUMN",
        "Age uses examination date instead of the laboratory date in the gold query.",
    ),
    105: (
        "WRONG_TIME_GRAIN",
        "Age expression changes from year subtraction to AGE without the gold semantics.",
    ),
    109: (
        "UNDETERMINED",
        "Current-date spelling is equivalent; the remaining mismatch is projection/distinctness.",
    ),
    111: (
        "UNDETERMINED",
        "The mismatch is the missing qualifying subquery/HAVING logic, not a bounded time error.",
    ),
    113: (
        "WRONG_TIME_COLUMN",
        "Age uses current date instead of examination date.",
    ),
    114: (
        "UNDETERMINED",
        "Current-date spelling is equivalent; the remaining mismatch is metric aggregation.",
    ),
    132: (
        "WRONG_TIME_GRAIN",
        "The generated age omits the gold month/day rounding semantics.",
    ),
    203: (
        "UNDETERMINED",
        "Current-date spelling is equivalent; the mismatch is output/null-filter structure.",
    ),
    228: (
        "WRONG_TIME_COLUMN",
        "Generated query uses milliseconds instead of the gold lap-time column.",
    ),
    236: (
        "UNDETERMINED",
        "The explicit date range is a possible equivalent of the gold year predicate; "
        "mismatch is output/grouping structure.",
    ),
    457: (
        "UNDETERMINED",
        "The explicit calendar range is a possible equivalent of the gold year "
        "predicate; mismatch is count/projection structure.",
    ),
    467: (
        "UNDETERMINED",
        "The date range is present; mismatch is relation/filter/output composition.",
    ),
    499: (
        "UNDETERMINED",
        "Current-date spelling is equivalent; mismatch is age/output structure.",
    ),
}


@dataclass(frozen=True)
class Case:
    benchmark: str
    index: int
    question_id: str
    database: str
    question: str
    evidence: str
    difficulty: str
    gold_sql: str
    generated_sql: str
    execution_correct: bool
    gold_summary: dict[str, Any]
    generated_summary: dict[str, Any]
    ast_diff: dict[str, Any]
    result_shape: dict[str, Any]
    primary_failure: str
    secondary_tags: tuple[str, ...]
    evidence_note: str


def stable_hash(path: Path) -> str:
    return sha256_file(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_bird_artifact() -> tuple[list[BirdQuestion], list[dict[str, Any]]]:
    root = Path(os.environ["BIRD_MINI_DEV_ROOT"])
    questions = load_bird_questions(root / BIRD_DATA_FILE)
    records = read_jsonl(BIRD_RUN / "results.jsonl")
    by_index = {record["index"]: record for record in records}
    if len(questions) != 500 or set(by_index) != {q.index for q in questions}:
        raise RuntimeError("BIRD artifact does not match the frozen 500-row source")
    return questions, [by_index[q.index] for q in questions]


def load_defog_artifact() -> tuple[
    dict[str, list[BenchmarkQuestion]], dict[str, list[dict[str, Any]]]
]:
    root = Path(os.environ["DEFOG_SQL_EVAL_ROOT"])
    questions = {
        "classic": load_defog_questions(root / "data/questions_gen_postgres.csv", "classic"),
        "advanced": load_defog_questions(root / "data/instruct_advanced_postgres.csv", "advanced"),
    }
    records = {
        "classic": read_jsonl(DEFOG_RUN / "classic_results.jsonl"),
        "advanced": read_jsonl(DEFOG_RUN / "advanced_results.jsonl"),
    }
    return questions, records


def _result_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {"available": False}
    columns = getattr(value, "columns", ())
    rows = getattr(value, "rows", ())
    return {
        "available": True,
        "columns": list(columns),
        "column_count": len(columns),
        "row_count": len(rows),
        "row_hash": hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest(),
    }


def execute_result(
    benchmark: str,
    database: str,
    sql: str,
    bird_executor: BirdBenchmarkExecutor | None,
    defog_executor: DefogBenchmarkExecutor | None,
) -> tuple[dict[str, Any], str | None]:
    if not sql.strip():
        return {"available": False}, "EMPTY_SQL"
    try:
        if benchmark == "bird":
            assert bird_executor is not None
            return _result_shape(bird_executor.execute(sql)), None
        assert defog_executor is not None
        return _result_shape(defog_executor.execute(database, sql)), None
    except Exception as error:  # read-only diagnostic execution only
        return {"available": False, "error_type": type(error).__name__}, str(error)[:240]


def _core_similarity(gold: dict[str, Any], generated: dict[str, Any]) -> bool:
    fields = (
        "tables",
        "aggregations",
        "group_by",
        "where",
        "join_conditions_normalized",
        "division",
    )
    return all(gold.get(field, []) == generated.get(field, []) for field in fields)


def _component_values(summary: dict[str, Any], key: str) -> list[str]:
    return sorted(
        json.dumps(component.get(key), sort_keys=True)
        for component in summary.get("ratio_components", [])
    )


def _classify_ratio(
    gold: dict[str, Any], generated: dict[str, Any], question: str, result_shape: dict[str, Any]
) -> tuple[str, tuple[str, ...], str]:
    if not generated.get("parse_success"):
        return (
            "UNDETERMINED",
            (),
            "Generated SQL did not parse; semantic attribution is insufficient.",
        )
    tags: set[str] = set()
    if set(gold.get("tables", [])) != set(generated.get("tables", [])):
        tags.add("TABLE")
    if gold.get("group_by", []) != generated.get("group_by", []):
        tags.add("GRAIN")
    if gold.get("where", []) != generated.get("where", []):
        tags.add("FILTER")
    if gold.get("distinct") and not generated.get("distinct"):
        tags.add("DISTINCT")
    if gold.get("joins") != generated.get("joins"):
        tags.add("JOIN")
    if gold.get("date_time_functions") != generated.get("date_time_functions", []):
        tags.add("TEMPORAL")
    if gold.get("select_count") != generated.get("select_count"):
        tags.add("OUTPUT")
    if gold.get("division") and not generated.get("division"):
        return (
            "ARITHMETIC_STRUCTURE_ERROR",
            tuple(sorted(tags | {"SCALE"})),
            "Gold has division; generated SQL does not.",
        )
    if not gold.get("division") and generated.get("division"):
        return (
            "ARITHMETIC_STRUCTURE_ERROR",
            tuple(sorted(tags)),
            "Generated SQL introduces division absent from gold.",
        )
    if gold.get("tables") and not set(gold["tables"]).intersection(generated.get("tables", [])):
        return (
            "TABLE_GROUNDING_ERROR",
            tuple(sorted(tags)),
            "Generated tables do not overlap the gold tables.",
        )
    gold_numerators = _component_values(gold, "numerator")
    generated_numerators = _component_values(generated, "numerator")
    gold_denominators = _component_values(gold, "denominator")
    generated_denominators = _component_values(generated, "denominator")
    numerator_diff = gold_numerators != generated_numerators
    denominator_diff = gold_denominators != generated_denominators
    if gold.get("joins") and generated.get("joins") > gold.get("joins", 0):
        tags.add("JOIN")
        return (
            "JOIN_FANOUT",
            tuple(sorted(tags)),
            "Generated aggregate query adds join multiplicity.",
        )
    if gold.get("join_conditions_normalized") != generated.get(
        "join_conditions_normalized", []
    ):
        tags.add("JOIN")
        return "JOIN_PATH_ERROR", tuple(sorted(tags)), "Join predicates differ structurally."
    if gold.get("aggregations") != generated.get("aggregations", []):
        tags.add("GRAIN")
        return "WRONG_AGGREGATION", tuple(sorted(tags)), "Aggregate function structure differs."
    if gold.get("distinct") and not generated.get("distinct"):
        return "MISSING_DISTINCT", tuple(sorted(tags | {"DISTINCT"})), "DISTINCT structure differs."
    if numerator_diff and not denominator_diff:
        return (
            "WRONG_NUMERATOR",
            tuple(sorted(tags)),
            "Numerator expression differs while denominator matches.",
        )
    if denominator_diff and not numerator_diff:
        return (
            "WRONG_DENOMINATOR",
            tuple(sorted(tags)),
            "Denominator expression differs while numerator matches.",
        )
    if gold.get("group_by") != generated.get("group_by", []):
        tags.add("GRAIN")
        return "WRONG_DIMENSION", tuple(sorted(tags)), "Grouping dimensions differ."
    if gold.get("where") and not set(gold["where"]).intersection(generated.get("where", [])):
        return (
            "MISSING_FILTER",
            tuple(sorted(tags | {"FILTER"})),
            "Gold predicates are absent from generated SQL.",
        )
    if set(generated.get("where", [])) - set(gold.get("where", [])):
        return (
            "EXTRA_FILTER",
            tuple(sorted(tags | {"FILTER"})),
            "Generated SQL adds predicates absent from gold.",
        )
    if gold.get("distinct") != generated.get("distinct"):
        return "MISSING_DISTINCT", tuple(sorted(tags | {"DISTINCT"})), "DISTINCT structure differs."
    if gold.get("literal_values") != generated.get("literal_values", []):
        tags.add("SCALE")
        if contains_any(question, ("percent", "%", "percentage", "ratio", "share")):
            return (
                "WRONG_SCALE",
                tuple(sorted(tags)),
                "Numeric literals in the derived metric differ.",
            )
    if gold.get("select_expressions") != generated.get("select_expressions", []):
        return (
            "OUTPUT_SHAPE_ERROR",
            tuple(sorted(tags | {"OUTPUT"})),
            "Projection structure differs while core tables remain comparable.",
        )
    if _core_similarity(gold, generated):
        return (
            "UNDETERMINED",
            tuple(sorted(tags)),
            "AST structure is close but execution differs; further semantic "
            "evidence is insufficient.",
        )
    return (
        "ARITHMETIC_STRUCTURE_ERROR",
        tuple(sorted(tags)),
        "Derived arithmetic structure differs.",
    )


def _classify_temporal(
    gold: dict[str, Any], generated: dict[str, Any], question: str, index: int | None = None
) -> tuple[str, tuple[str, ...], str]:
    if not generated.get("parse_success"):
        return (
            "UNDETERMINED",
            (),
            "Generated SQL did not parse; semantic attribution is insufficient.",
        )
    if index in TEMPORAL_MANUAL_ADJUDICATION:
        primary, note = TEMPORAL_MANUAL_ADJUDICATION[index]
        return primary, ("TEMPORAL",), note
    tags: set[str] = {"TEMPORAL"}
    temporal_tokens = (
        "date",
        "time",
        "year",
        "month",
        "day",
        "timestamp",
        "created",
        "updated",
        "ordered",
    )
    gold_time = {
        column.rsplit(".", 1)[-1]
        for column in gold.get("columns", [])
        if any(token in column for token in temporal_tokens)
    }
    generated_time = {
        column.rsplit(".", 1)[-1]
        for column in generated.get("columns", [])
        if any(token in column for token in temporal_tokens)
    }
    if gold_time != generated_time:
        return "WRONG_TIME_COLUMN", tuple(sorted(tags)), "Temporal column references differ."
    if gold.get("date_time_functions") != generated.get("date_time_functions", []):
        if set(gold.get("date_time_functions", [])) != set(
            generated.get("date_time_functions", [])
        ):
            return (
                "DATE_FUNCTION_ERROR",
                tuple(sorted(tags)),
                "Date/time function structure differs.",
            )
        return "WRONG_TIME_GRAIN", tuple(sorted(tags)), "Temporal function arguments differ."
    if gold.get("where") != generated.get("where", []):
        if gold.get("where") and not generated.get("where"):
            return (
                "MISSING_TEMPORAL_FILTER",
                tuple(sorted(tags)),
                "Gold temporal predicates are missing.",
            )
        if set(generated.get("where", [])) - set(gold.get("where", [])):
            return (
                "EXTRA_TEMPORAL_FILTER",
                tuple(sorted(tags)),
                "Generated SQL adds temporal predicates.",
            )
        return "WRONG_DATE_RANGE", tuple(sorted(tags)), "Temporal predicate boundaries differ."
    if gold.get("joins") != generated.get("joins") or gold.get(
        "join_conditions_normalized"
    ) != generated.get("join_conditions_normalized", []):
        return (
            "TEMPORAL_JOIN_ERROR",
            tuple(sorted(tags | {"JOIN"})),
            "Temporal query join structure differs.",
        )
    if gold.get("literal_values") != generated.get("literal_values", []):
        if contains_any(question, ("current", "today", "now")):
            return (
                "CURRENT_VS_COMPLETE_PERIOD",
                tuple(sorted(tags)),
                "Relative/current-period literals differ.",
            )
        return "WRONG_DATE_RANGE", tuple(sorted(tags)), "Temporal literal boundaries differ."
    if gold.get("group_by") != generated.get("group_by", []):
        return (
            "OUTPUT_TIME_BUCKET_ERROR",
            tuple(sorted(tags)),
            "Temporal grouping/output bucket differs.",
        )
    return (
        "UNDETERMINED",
        tuple(sorted(tags)),
        "Temporal AST difference is not attributable to one bounded mechanism.",
    )


def _classify_general(
    gold: dict[str, Any], generated: dict[str, Any]
) -> tuple[str, tuple[str, ...], str]:
    if not generated.get("parse_success"):
        return "UNDETERMINED", (), "Generated SQL did not parse."
    if set(gold.get("tables", [])) != set(generated.get("tables", [])):
        return "TABLE_GROUNDING", ("TABLE",), "Table set differs."
    if gold.get("join_conditions_normalized") != generated.get(
        "join_conditions_normalized", []
    ):
        return "JOIN_PATH", ("JOIN",), "Join condition structure differs."
    if gold.get("joins") != generated.get("joins"):
        return "JOIN_CARDINALITY", ("JOIN",), "Join count differs."
    if gold.get("where") != generated.get("where", []):
        return "FILTER", ("FILTER",), "Predicate structure differs."
    if gold.get("aggregations") != generated.get("aggregations", []):
        return "AGGREGATION", ("AGGREGATION",), "Aggregate structure differs."
    if gold.get("group_by") != generated.get("group_by", []):
        return "GROUPING", ("GRAIN",), "Grouping structure differs."
    if gold.get("distinct") != generated.get("distinct"):
        return "DISTINCT", ("DISTINCT",), "Distinctness differs."
    if gold.get("division") != generated.get("division") or gold.get("arithmetic") != generated.get(
        "arithmetic"
    ):
        return "ARITHMETIC", ("ARITHMETIC",), "Arithmetic structure differs."
    if gold.get("subqueries") != generated.get("subqueries"):
        return "SUBQUERY_STRUCTURE", ("SUBQUERY",), "Subquery structure differs."
    if gold.get("ctes") != generated.get("ctes"):
        return "CTE_STRUCTURE", ("CTE",), "CTE structure differs."
    if gold.get("set_operations") != generated.get("set_operations"):
        return "SET_OPERATION", ("SET",), "Set-operation structure differs."
    if gold.get("case") != generated.get("case", []):
        return "CASE_LOGIC", ("CASE",), "CASE structure differs."
    if gold.get("order_by") != generated.get("order_by", []) or gold.get("limit") != generated.get(
        "limit"
    ):
        return "ORDER_LIMIT", ("ORDER",), "Ordering or limit structure differs."
    if gold.get("select_count") != generated.get("select_count") or gold.get(
        "select_expressions"
    ) != generated.get("select_expressions", []):
        return "OUTPUT_SHAPE", ("OUTPUT",), "Projection structure differs."
    if gold.get("literal_values") != generated.get("literal_values", []):
        return "LITERAL_VALUE", ("LITERAL",), "Literal predicates or values differ."
    return "UNDETERMINED", (), "Execution differs without a bounded structural explanation."


def _record_case(
    benchmark: str,
    index: int,
    question_id: str,
    database: str,
    question: str,
    evidence: str,
    difficulty: str,
    gold_sql: str,
    generated_sql: str,
    correct: bool,
    primary: str,
    secondary: tuple[str, ...],
    note: str,
    result_shape: dict[str, Any],
) -> Case:
    gold_summary = summarize_sql(gold_sql)
    generated_summary = summarize_sql(generated_sql)
    return Case(
        benchmark=benchmark,
        index=index,
        question_id=question_id,
        database=database,
        question=question,
        evidence=evidence,
        difficulty=difficulty,
        gold_sql=gold_sql,
        generated_sql=generated_sql,
        execution_correct=correct,
        gold_summary=gold_summary,
        generated_summary=generated_summary,
        ast_diff=compare_summaries(gold_summary, generated_summary),
        result_shape=result_shape,
        primary_failure=primary,
        secondary_tags=secondary,
        evidence_note=note,
    )


def _pair_result_shape(
    benchmark: str,
    database: str,
    gold_sql: str,
    generated_sql: str,
    bird_executor: BirdBenchmarkExecutor | None,
    defog_executor: DefogBenchmarkExecutor | None,
) -> tuple[dict[str, Any], str | None]:
    gold_shape, gold_error = execute_result(
        benchmark, database, gold_sql, bird_executor, defog_executor
    )
    generated_shape, generated_error = execute_result(
        benchmark, database, generated_sql, bird_executor, defog_executor
    )
    errors = [error for error in (gold_error, generated_error) if error]
    return (
        {"gold": gold_shape, "generated": generated_shape},
        "; ".join(errors) if errors else None,
    )


def _is_ratio(record: dict[str, Any]) -> bool:
    return bool(record.get("gold_structural_profile", {}).get("division"))


def _is_temporal(record: dict[str, Any]) -> bool:
    return bool(record.get("gold_structural_profile", {}).get("date_time"))


def _sample_general(records: list[dict[str, Any]], size: int = 60) -> list[dict[str, Any]]:
    candidates = [
        record
        for record in records
        if not record.get("execution_accuracy")
        and not _is_ratio(record)
        and not _is_temporal(record)
    ]
    candidates.sort(key=lambda record: record["index"])
    selected: list[dict[str, Any]] = []
    counts: Counter[tuple[str, str]] = Counter()
    while candidates and len(selected) < size:
        best = min(
            candidates,
            key=lambda record: (
                counts[(record["difficulty"], "difficulty")],
                counts[(record["db_id"], "database")],
                counts[
                    (
                        "join" if record["gold_structural_profile"].get("joins") else "no_join",
                        "shape",
                    )
                ],
                record["index"],
            ),
        )
        candidates.remove(best)
        selected.append(best)
        counts[(best["difficulty"], "difficulty")] += 1
        counts[(best["db_id"], "database")] += 1
        counts[
            ("join" if best["gold_structural_profile"].get("joins") else "no_join", "shape")
        ] += 1
    return selected


def _case_dict(case: Case) -> dict[str, Any]:
    result = asdict(case)
    result["secondary_tags"] = list(case.secondary_tags)
    return result


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def run_audit(output: Path) -> dict[str, Any]:
    bird_questions, bird_records = load_bird_artifact()
    defog_questions, defog_records = load_defog_artifact()
    bird_by_index = {q.index: q for q in bird_questions}
    bird_executor = BirdBenchmarkExecutor()
    defog_executor = DefogBenchmarkExecutor()

    ratio_cases: list[Case] = []
    temporal_cases: list[Case] = []
    general_candidates = _sample_general(bird_records)
    general_indexes = {row["index"] for row in general_candidates}
    general_cases: list[Case] = []
    for record in bird_records:
        question = bird_by_index[record["index"]]
        gold_sql = question.gold_sql
        generated_sql = record.get("generated_sql", "")
        shape, execution_error = _pair_result_shape(
            "bird", question.db_id, gold_sql, generated_sql, bird_executor, None
        )
        gold_summary = summarize_sql(gold_sql)
        generated_summary = summarize_sql(generated_sql)
        if _is_ratio(record):
            correct = bool(record.get("execution_accuracy"))
            primary, secondary, note = (
                ("CORRECT", (), "Persisted BIRD execution result is correct.")
                if correct
                else _classify_ratio(gold_summary, generated_summary, question.question, shape)
            )
            ratio_cases.append(
                _record_case(
                    "bird",
                    question.index,
                    str(question.question_id),
                    question.db_id,
                    question.question,
                    question.evidence,
                    question.difficulty,
                    gold_sql,
                    generated_sql,
                    correct,
                    primary,
                    secondary,
                    note
                    if not execution_error
                    else f"{note} Execution diagnostic: {execution_error}",
                    shape,
                )
            )
        if _is_temporal(record):
            correct = bool(record.get("execution_accuracy"))
            primary, secondary, note = (
                ("CORRECT", (), "Persisted BIRD execution result is correct.")
                if correct
                else _classify_temporal(
                    gold_summary, generated_summary, question.question, question.index
                )
            )
            temporal_cases.append(
                _record_case(
                    "bird",
                    question.index,
                    str(question.question_id),
                    question.db_id,
                    question.question,
                    question.evidence,
                    question.difficulty,
                    gold_sql,
                    generated_sql,
                    correct,
                    primary,
                    secondary,
                    note
                    if not execution_error
                    else f"{note} Execution diagnostic: {execution_error}",
                    shape,
                )
            )
        if (
            not record.get("execution_accuracy")
            and
            not _is_ratio(record)
            and not _is_temporal(record)
            and record["index"] in general_indexes
        ):
            primary, secondary, note = _classify_general(gold_summary, generated_summary)
            general_cases.append(
                _record_case(
                    "bird",
                    question.index,
                    str(question.question_id),
                    question.db_id,
                    question.question,
                    question.evidence,
                    question.difficulty,
                    gold_sql,
                    generated_sql,
                    False,
                    primary,
                    secondary,
                    note
                    if not execution_error
                    else f"{note} Execution diagnostic: {execution_error}",
                    shape,
                )
            )

    defog_ratio_cases: list[Case] = []
    classic_questions = {q.index: q for q in defog_questions["classic"]}
    for record in defog_records["classic"]:
        defog_question = classic_questions[record["index"]]
        if record.get("query_category") != "ratio":
            continue
        if not summarize_sql(defog_question.gold_sql).get("division"):
            continue
        correct = bool(record.get("exact_match"))
        shape, execution_error = _pair_result_shape(
            "defog",
            defog_question.db_name,
            defog_question.gold_sql,
            record.get("generated_sql", ""),
            None,
            defog_executor,
        )
        gold_summary = summarize_sql(defog_question.gold_sql)
        generated_summary = summarize_sql(record.get("generated_sql", ""))
        primary, secondary, note = (
            ("CORRECT", (), "Persisted Defog exact result is correct.")
            if correct
            else _classify_ratio(gold_summary, generated_summary, defog_question.question, shape)
        )
        defog_ratio_cases.append(
            _record_case(
                "defog",
                defog_question.index,
                str(defog_question.index),
                defog_question.db_name,
                defog_question.question,
                defog_question.instructions,
                "unknown",
                defog_question.gold_sql,
                record.get("generated_sql", ""),
                correct,
                primary,
                secondary,
                note if not execution_error else f"{note} Execution diagnostic: {execution_error}",
                shape,
            )
        )

    output.mkdir(parents=True, exist_ok=False)
    source_manifest = {
        "milestone": "M2.14.1",
        "version": RUN_VERSION,
        "provider_calls": 0,
        "source_runs": {
            "bird": str(BIRD_RUN),
            "defog": str(DEFOG_RUN),
        },
        "source_artifact_hashes": {
            "bird_results": stable_hash(BIRD_RUN / "results.jsonl"),
            "bird_dataset": stable_hash(Path(os.environ["BIRD_MINI_DEV_ROOT"]) / BIRD_DATA_FILE),
            "defog_classic_results": stable_hash(DEFOG_RUN / "classic_results.jsonl"),
            "defog_advanced_results": stable_hash(DEFOG_RUN / "advanced_results.jsonl"),
        },
        "taxonomy_version": RUN_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "holdouts_accessed": False,
        "general_sample": {
            "size": len(general_cases),
            "source": "BIRD incorrect non-ratio/non-temporal records",
            "selection": (
                "stable minimum-count stratification by difficulty, database, and join presence"
            ),
            "indices": sorted(general_indexes),
        },
    }
    _write_json(output / "metadata.json", source_manifest)
    _write_json(output / "audit_manifest.json", source_manifest)
    for filename, cases in (
        ("ratio_cases.jsonl", ratio_cases),
        ("defog_ratio_cases.jsonl", defog_ratio_cases),
        ("temporal_cases.jsonl", temporal_cases),
        ("compositional_cases.jsonl", general_cases),
    ):
        (output / filename).write_text(
            "".join(json.dumps(_case_dict(case), default=str) + "\n" for case in cases)
        )

    def summary(cases: list[Case], total: int, correct: int) -> dict[str, Any]:
        incorrect_cases = [case for case in cases if case.primary_failure != "CORRECT"]
        primary = Counter(case.primary_failure for case in incorrect_cases)
        secondary = Counter(tag for case in incorrect_cases for tag in case.secondary_tags)
        return {
            "total": total,
            "correct": correct,
            "accounted_cases": len(cases),
            "incorrect": len(incorrect_cases),
            "primary_failure_distribution": dict(sorted(primary.items())),
            "primary_failure_percent_of_incorrect": {
                key: value / len(incorrect_cases) * 100
                for key, value in sorted(primary.items())
            }
            if incorrect_cases
            else {},
            "secondary_tag_distribution": dict(sorted(secondary.items())),
            "undetermined": primary.get("UNDETERMINED", 0),
        }

    def ratio_component_quality(cases: list[Case]) -> dict[str, Any]:
        numerator_available = 0
        denominator_available = 0
        numerator_matching = 0
        denominator_matching = 0
        aligned = 0
        for case in cases:
            gold_numerators = _component_values(case.gold_summary, "numerator")
            generated_numerators = _component_values(case.generated_summary, "numerator")
            gold_denominators = _component_values(case.gold_summary, "denominator")
            generated_denominators = _component_values(case.generated_summary, "denominator")
            if gold_numerators:
                numerator_available += 1
                numerator_matching += gold_numerators == generated_numerators
            if gold_denominators:
                denominator_available += 1
                denominator_matching += gold_denominators == generated_denominators
            if (
                gold_numerators == generated_numerators
                and gold_denominators == generated_denominators
            ):
                aligned += 1
        return {
            "cases": len(cases),
            "numerator": {
                "matching": numerator_matching,
                "available": numerator_available,
                "accuracy": numerator_matching / numerator_available
                if numerator_available
                else None,
            },
            "denominator": {
                "matching": denominator_matching,
                "available": denominator_available,
                "accuracy": denominator_matching / denominator_available
                if denominator_available
                else None,
            },
            "both_components_matching": aligned,
        }

    def output_grounding(cases: list[Case]) -> dict[str, Any]:
        gold_count = 0
        matched_count = 0
        generated_count = 0
        for case in cases:
            gold_outputs = case.gold_summary.get("select_expression_signatures", [])
            generated_outputs = case.generated_summary.get(
                "select_expression_signatures", []
            )
            gold_count += len(gold_outputs)
            generated_count += len(generated_outputs)
            remaining = list(generated_outputs)
            for output in gold_outputs:
                if output in remaining:
                    remaining.remove(output)
                    matched_count += 1
        return {
            "gold_output_expressions": gold_count,
            "generated_output_expressions": generated_count,
            "matched_output_expressions": matched_count,
            "precision": matched_count / generated_count if generated_count else None,
            "recall": matched_count / gold_count if gold_count else None,
        }

    ratio_summary = summary(ratio_cases, 101, 17)
    defog_summary = summary(defog_ratio_cases, 35, 12)
    temporal_summary = summary(temporal_cases, 19, 4)
    general_summary = summary(general_cases, len(general_cases), 0)
    ratio_component_summary = ratio_component_quality(ratio_cases)
    ratio_output_summary = output_grounding(ratio_cases)
    _write_json(output / "ratio_summary.json", ratio_summary)
    _write_json(output / "defog_ratio_summary.json", defog_summary)
    _write_json(output / "temporal_summary.json", temporal_summary)
    _write_json(output / "compositional_sample.json", source_manifest["general_sample"])
    _write_json(output / "compositional_summary.json", general_summary)
    _write_json(
        output / "ratio_component_quality.json",
        {"components": ratio_component_summary, "outputs": ratio_output_summary},
    )

    repeated = {
        "bird": ratio_summary["primary_failure_distribution"],
        "defog": defog_summary["primary_failure_distribution"],
        "intersection": sorted(
            set(ratio_summary["primary_failure_distribution"])
            & set(defog_summary["primary_failure_distribution"])
        ),
    }
    design = {
        "metric_definition": {
            "ratio_formula_labels": ["WRONG_NUMERATOR", "WRONG_DENOMINATOR", "WRONG_SCALE"],
            "evidence": ratio_summary["primary_failure_distribution"],
            "component_quality": ratio_component_summary,
        },
        "grain_cardinality": {
            "labels": ["WRONG_GRAIN", "JOIN_FANOUT", "MISSING_DISTINCT"],
            "evidence": {
                label: ratio_summary["primary_failure_distribution"].get(label, 0)
                for label in ["WRONG_GRAIN", "JOIN_FANOUT", "MISSING_DISTINCT"]
            },
        },
        "filters": {
            "evidence": {
                label: ratio_summary["primary_failure_distribution"].get(label, 0)
                for label in ["MISSING_FILTER", "EXTRA_FILTER"]
            }
        },
        "temporal_spec": {"evidence": temporal_summary["primary_failure_distribution"]},
        "value_grounding": {
            "general_literal_value_count": general_summary["primary_failure_distribution"].get(
                "LITERAL_VALUE", 0
            )
        },
        "schema_retrieval": {
            "general_table_grounding_count": general_summary["primary_failure_distribution"].get(
                "TABLE_GROUNDING", 0
            ),
            "near_term_priority": False,
            "reason": (
                "Full benchmark schema was already visible; table errors alone do not "
                "isolate retrieval as the cause."
            ),
        },
        "recommended_m3_contract_fields": [
            "named metric identifier",
            "numerator definition",
            "denominator definition",
            "valid entity grain",
            "allowed dimensions",
            "default eligibility filters",
            "scale and null/zero policy",
        ],
        "not_recommended_for_m3": [
            "LLM-generated arbitrary SQL formulas",
            "generic retrieval as a substitute for metric definitions",
            "temporal resolver implementation before separate temporal evidence",
            "changes to M1 execution policy",
        ],
    }
    _write_json(output / "defog_ratio_crosscheck.json", repeated)
    _write_json(output / "design_implications.json", design)
    final = {
        "classification": [
            "METRIC_DEFINITION_BOTTLENECK",
            "RELATIONAL_COMPOSITION_BOTTLENECK",
            "MIXED_SEMANTIC_FAILURES",
        ],
        "ratio": ratio_summary,
        "ratio_component_quality": ratio_component_summary,
        "ratio_output_grounding": ratio_output_summary,
        "defog_ratio": defog_summary,
        "temporal": temporal_summary,
        "general_compositional": general_summary,
        "repeated_ratio_mechanisms": repeated["intersection"],
        "provider_calls": 0,
        "holdouts_accessed": False,
    }
    _write_json(output / "final_summary.json", final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()
    output = ROOT / "evaluation/results/m2141" / args.run_id
    print(json.dumps(run_audit(output), indent=2, default=str))


if __name__ == "__main__":
    main()
