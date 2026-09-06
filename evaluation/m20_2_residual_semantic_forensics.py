"""Provider-free forensic analysis of the frozen M20 semantic residuals."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import sqlglot
from sqlalchemy.engine import Engine
from sqlglot import exp

from app.provenance.canonical import semantic_hash
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.bird.benchmark import (
    BirdBenchmarkExecutor,
    BirdQuestion,
    bird_execution_match,
)
from evaluation.external.bird.benchmark import QueryResult as BirdResult
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
    canonical_compare,
    canonical_gold_variants,
)
from evaluation.external.defog.benchmark import QueryResult as DefogResult
from evaluation.m20_1r2_readonly_percent_transport_repair import (
    _result_from_execution,
)
from evaluation.m20_full_rebaseline import _load_all, _settings, _settings_for_db

ROOT = Path(__file__).resolve().parents[1]
M20_CASES = ROOT / "evaluation/fixtures/m20_full_benchmark_cases.jsonl"
M20_RESULT = ROOT / "evaluation/fixtures/m20_full_benchmark_result.json"
M20_MANIFEST = ROOT / "evaluation/fixtures/m20_prebenchmark_manifest.json"
M20_1R2 = ROOT / "evaluation/fixtures/m20_1r2_readonly_percent_transport_repair.json"
OUTPUT = ROOT / "evaluation/fixtures/m20_2_residual_semantic_forensics.json"

EXPECTED_COUNTS = {"bird": 500, "defog_classic": 210, "defog_advanced": 64}
PRIMARY_CAUSES = (
    "SCHEMA_GROUNDING",
    "JOIN_SEMANTICS",
    "FILTER_SEMANTICS",
    "TEMPORAL_SEMANTICS",
    "AGGREGATION_AND_GRAIN",
    "ARITHMETIC_AND_RATIO",
    "WINDOW_SEMANTICS",
    "SUBQUERY_OR_SET_SEMANTICS",
    "ORDER_AND_LIMIT",
    "DISTINCT_OR_DEDUPLICATION",
    "STRING_MATCHING",
    "PROJECTION_SEMANTICS",
    "FUNCTION_OR_EXPRESSION",
    "UNDETERMINED",
)

ROOT_INTERVENTION_NOTES = {
    "SCHEMA_GROUNDING": {
        "likely_intervention_class": "server-owned schema/relationship grounding context",
        "regression_risk": "context drift or incorrect catalog exposure",
        "recoverability": "PARTIALLY_RECOVERABLE",
    },
    "FILTER_SEMANTICS": {
        "likely_intervention_class": "general filter/value grounding",
        "regression_risk": "overfitting natural-language values or boundaries",
        "recoverability": "PARTIALLY_RECOVERABLE",
    },
    "TEMPORAL_SEMANTICS": {
        "likely_intervention_class": "deterministic temporal interpretation contract",
        "regression_risk": "boundary and timezone/date-grain regressions",
        "recoverability": "CONTRACT_EXPANSION_REQUIRED",
    },
    "JOIN_SEMANTICS": {
        "likely_intervention_class": "server-owned relationship selection",
        "regression_risk": "wrong path or fanout introduced by broader joins",
        "recoverability": "PARTIALLY_RECOVERABLE",
    },
    "AGGREGATION_AND_GRAIN": {
        "likely_intervention_class": "explicit aggregation/grain semantics",
        "regression_risk": "counting grain and NULL behavior changes",
        "recoverability": "CONTRACT_EXPANSION_REQUIRED",
    },
    "ARITHMETIC_AND_RATIO": {
        "likely_intervention_class": "typed derived-formula semantics",
        "regression_risk": "numeric precision and divide-by-zero changes",
        "recoverability": "CONTRACT_EXPANSION_REQUIRED",
    },
    "WINDOW_SEMANTICS": {
        "likely_intervention_class": "existing Window IR where whole request fits",
        "regression_risk": "partition/order/tie semantics",
        "recoverability": "PARTIALLY_RECOVERABLE",
    },
    "SUBQUERY_OR_SET_SEMANTICS": {
        "likely_intervention_class": "bounded subquery/set semantics",
        "regression_risk": "scope and multiplicity changes",
        "recoverability": "CONTRACT_EXPANSION_REQUIRED",
    },
    "ORDER_AND_LIMIT": {
        "likely_intervention_class": "explicit ordering/top-k semantics",
        "regression_risk": "NULL ordering and tie behavior",
        "recoverability": "CONTRACT_EXPANSION_REQUIRED",
    },
    "DISTINCT_OR_DEDUPLICATION": {
        "likely_intervention_class": "explicit deduplication semantics",
        "regression_risk": "row multiplicity changes",
        "recoverability": "CONTRACT_EXPANSION_REQUIRED",
    },
    "STRING_MATCHING": {
        "likely_intervention_class": "typed string matching semantics",
        "regression_risk": "case sensitivity and wildcard interpretation",
        "recoverability": "PARTIALLY_RECOVERABLE",
    },
    "PROJECTION_SEMANTICS": {
        "likely_intervention_class": "projection/output-shape grounding",
        "regression_risk": "column order and output contract changes",
        "recoverability": "DIRECTLY_RECOVERABLE",
    },
    "FUNCTION_OR_EXPRESSION": {
        "likely_intervention_class": "bounded function/expression contract",
        "regression_risk": "type and NULL semantics",
        "recoverability": "CONTRACT_EXPANSION_REQUIRED",
    },
    "UNDETERMINED": {
        "likely_intervention_class": "additional offline evidence",
        "regression_risk": "unknown",
        "recoverability": "UNKNOWN",
    },
}


class ForensicValidationError(RuntimeError):
    """Raised when frozen evidence cannot support a complete forensic run."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _load_frozen() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    rows = [json.loads(line) for line in M20_CASES.read_text().splitlines() if line.strip()]
    result = json.loads(M20_RESULT.read_text())
    manifest = json.loads(M20_MANIFEST.read_text())
    repair = json.loads(M20_1R2.read_text())
    if result.get("classification") != "M20_FULL_REBASELINE_COMPLETED":
        raise ForensicValidationError("M20 baseline is not completed")
    if len(rows) != sum(EXPECTED_COUNTS.values()):
        raise ForensicValidationError(f"expected 774 rows, found {len(rows)}")
    if result.get("manifest_hash") != manifest.get("manifest_hash"):
        raise ForensicValidationError("M20 result/manifest hash mismatch")
    if repair.get("classification") != "M20_1R2_READONLY_PERCENT_TRANSPORT_REPAIR_VALIDATED":
        raise ForensicValidationError("M20.1R2 replay is not validated")
    ids = [(row["benchmark"], row["case_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise ForensicValidationError("frozen case IDs are not unique")
    m1 = Counter(row["m1_status"] for row in rows)
    if m1 != Counter({"ALLOWED": 475, "REJECTED": 264, "ERROR": 35}):
        raise ForensicValidationError(f"M20 M1 accounting mismatch: {m1}")
    return rows, result, manifest, repair


def _target_correctness(repair: dict[str, Any]) -> dict[tuple[str, str], bool]:
    return {
        (row["benchmark"], row["case_id"]): bool(row["correct"])
        for row in repair["target_cases"]
        if row["execution_status"] == "SUCCESS" and row["correct"] is not None
    }


def _question_key(row: dict[str, Any]) -> tuple[str, str]:
    return row["benchmark"], str(row["case_id"]).split(":")[-1]


def _parse(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return None


def _normalized(node: exp.Expression | None) -> str | None:
    if node is None:
        return None
    return re.sub(r"\s+", " ", node.sql(dialect="postgres", normalize=True)).lower()


def _join_edges(tree: exp.Expression) -> tuple[tuple[tuple[str, str], ...], ...]:
    aliases = {
        table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)
    }
    edges: list[tuple[tuple[str, str], ...]] = []
    for join in tree.find_all(exp.Join):
        condition = join.args.get("on")
        if condition is None:
            continue
        for equality in condition.find_all(exp.EQ):
            columns = list(equality.find_all(exp.Column))
            if len(columns) != 2:
                continue
            endpoints = []
            for column in columns:
                endpoints.append(
                    (aliases.get(column.table.lower(), column.table.lower()), column.name.lower())
                )
            edges.append(tuple(sorted(endpoints)))
    return tuple(sorted(edges))


def _ast_facts(sql: str) -> dict[str, Any]:
    tree = _parse(sql)
    if tree is None:
        return {"parseable": False}
    return {
        "parseable": True,
        "tables": sorted({table.name.lower() for table in tree.find_all(exp.Table)}),
        "join_count": len(list(tree.find_all(exp.Join))),
        "join_edges": [list(edge) for edge in _join_edges(tree)],
        "projection": [_normalized(item) for item in tree.expressions],
        "where": _normalized(tree.args.get("where")),
        "group_by": _normalized(tree.args.get("group")),
        "having": _normalized(tree.args.get("having")),
        "order_by": _normalized(tree.args.get("order")),
        "limit": _normalized(tree.args.get("limit")),
        "aggregation": sorted({type(node).__name__.upper() for node in tree.find_all(exp.AggFunc)}),
        "subquery": bool(list(tree.find_all(exp.Subquery))),
        "cte": bool(list(tree.find_all(exp.CTE))),
        "set_operation": any(
            isinstance(node, (exp.Union, exp.Intersect, exp.Except)) for node in tree.walk()
        ),
        "window": bool(list(tree.find_all(exp.Window))),
        "distinct": bool(tree.args.get("distinct")),
        "arithmetic": sorted(
            {
                type(node).__name__.upper()
                for node in tree.walk()
                if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod))
            }
        ),
        "functions": sorted({type(node).__name__.upper() for node in tree.find_all(exp.Func)}),
    }


def _feature_delta(generated: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    labels = {
        "tables": "TABLE_SET",
        "join_count": "JOIN_COUNT",
        "join_edges": "JOIN_EDGE_SET",
        "projection": "PROJECTION",
        "where": "FILTER",
        "group_by": "GROUP_BY",
        "having": "HAVING",
        "order_by": "ORDER_BY",
        "limit": "LIMIT",
        "aggregation": "AGGREGATION",
        "subquery": "SUBQUERY",
        "cte": "CTE",
        "set_operation": "SET_OPERATION",
        "window": "WINDOW",
        "distinct": "DISTINCT",
        "arithmetic": "ARITHMETIC",
        "functions": "FUNCTIONS",
    }
    return [labels[key] for key in labels if generated.get(key) != reference.get(key)]


def _reference_sql(question: BenchmarkQuestion | BirdQuestion) -> str:
    if isinstance(question, BirdQuestion):
        return question.gold_sql
    return canonical_gold_variants(question.gold_sql)[0]


def _temporal_question(question: str) -> bool:
    return bool(
        re.search(
            r"\b(date|dates|day|month|year|season|quarter|period|time|born|birth|201\d|19\d\d|20\d\d)\b",
            question.lower(),
        )
    )


def _primary_cause(
    generated_sql: str, reference_sql: str, question: str
) -> tuple[str, list[str], list[str]]:
    generated = _ast_facts(generated_sql)
    reference = _ast_facts(reference_sql)
    if not generated.get("parseable") or not reference.get("parseable"):
        return "UNDETERMINED", [], ["one or both SQL texts were not parseable by SQLGlot"]
    delta = _feature_delta(generated, reference)
    secondary: list[str] = []
    if generated["tables"] != reference["tables"]:
        secondary.append("WRONG_ENTITY_OR_RELATION")
        return "SCHEMA_GROUNDING", secondary, ["generated and reference relation sets differ"]
    if (
        generated["join_edges"] != reference["join_edges"]
        or generated["join_count"] != reference["join_count"]
    ):
        secondary.append("WRONG_JOIN_PATH")
        return "JOIN_SEMANTICS", secondary, ["generated and reference join graph facts differ"]
    if generated["where"] != reference["where"]:
        if _temporal_question(question):
            secondary.append("WRONG_DATE_RANGE_OR_COLUMN")
            return "TEMPORAL_SEMANTICS", secondary, ["filter AST differs on a temporal question"]
        if "like" in f"{generated['where']} {reference['where']}".lower():
            secondary.append("STRING_MATCHING")
            return "STRING_MATCHING", secondary, ["LIKE/pattern filter AST differs"]
        return "FILTER_SEMANTICS", secondary, ["filter AST differs while relation graph is aligned"]
    if (
        generated["aggregation"] != reference["aggregation"]
        or generated["group_by"] != reference["group_by"]
        or generated["having"] != reference["having"]
    ):
        secondary.append("WRONG_GRAIN")
        if generated["arithmetic"] != reference["arithmetic"] and any(
            word in question.lower()
            for word in ("ratio", "percent", "percentage", "rate", "difference")
        ):
            return (
                "ARITHMETIC_AND_RATIO",
                secondary,
                ["aggregate/formula AST differs for a ratio-like question"],
            )
        return "AGGREGATION_AND_GRAIN", secondary, ["aggregate/group/having AST differs"]
    if generated["arithmetic"] != reference["arithmetic"]:
        return "ARITHMETIC_AND_RATIO", ["DERIVED_FORMULA"], ["arithmetic AST differs"]
    if generated["window"] != reference["window"]:
        return "WINDOW_SEMANTICS", [], ["window AST presence differs"]
    if (
        generated["subquery"] != reference["subquery"]
        or generated["set_operation"] != reference["set_operation"]
    ):
        return "SUBQUERY_OR_SET_SEMANTICS", [], ["subquery/set-operation shape differs"]
    if generated["order_by"] != reference["order_by"] or generated["limit"] != reference["limit"]:
        return "ORDER_AND_LIMIT", [], ["ordering/limit AST differs"]
    if generated["distinct"] != reference["distinct"]:
        return "DISTINCT_OR_DEDUPLICATION", [], ["DISTINCT presence differs"]
    if generated["projection"] != reference["projection"]:
        return "PROJECTION_SEMANTICS", [], ["projected expressions differ"]
    if generated["functions"] != reference["functions"]:
        return "FUNCTION_OR_EXPRESSION", [], ["function AST differs"]
    return "UNDETERMINED", [], [f"unresolved AST delta: {', '.join(delta) or 'none'}"]


def _shape_profile(facts: dict[str, Any], question: str) -> dict[str, bool | int]:
    return {
        "join_count": int(facts.get("join_count", 0)),
        "joins_2_plus": int(facts.get("join_count", 0) >= 2),
        "aggregation": bool(facts.get("aggregation")),
        "group_by": bool(facts.get("group_by")),
        "having": bool(facts.get("having")),
        "subquery": bool(facts.get("subquery")),
        "cte": bool(facts.get("cte")),
        "window": bool(facts.get("window")),
        "distinct": bool(facts.get("distinct")),
        "order_by": bool(facts.get("order_by")),
        "limit": bool(facts.get("limit")),
        "arithmetic": bool(facts.get("arithmetic")),
        "temporal_question": _temporal_question(question),
    }


def _typed(value: Any) -> Any:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, (int, float, str)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "value": [_typed(item) for item in value]}
    if isinstance(value, dict):
        return {
            "type": "dict",
            "value": {str(key): _typed(item) for key, item in sorted(value.items())},
        }
    return {"type": type(value).__name__, "value": str(value)}


def _result_record(result: BirdResult | DefogResult) -> dict[str, Any]:
    rows = [[_typed(value) for value in row] for row in result.rows]
    payload = {"columns": list(result.columns), "rows": rows}
    return {
        "row_count": len(result.rows),
        "columns": list(result.columns),
        "typed_hash": semantic_hash(payload),
    }


def _compare(
    row: dict[str, Any],
    question: BenchmarkQuestion | BirdQuestion,
    generated: BirdResult | DefogResult,
    gold: BirdResult | list[tuple[str, DefogResult]],
) -> bool:
    if isinstance(question, BirdQuestion):
        assert isinstance(gold, BirdResult)
        return bird_execution_match(cast(BirdResult, generated), gold)
    assert isinstance(generated, DefogResult)
    assert isinstance(gold, list)
    return any(
        canonical_compare(gold_result, generated, question, gold_sql, row["generated_sql"])[0]
        for gold_sql, gold_result in gold
    )


def _capability_relevance(generated: dict[str, Any], reference: dict[str, Any]) -> dict[str, str]:
    unsupported = (
        reference.get("aggregation")
        or reference.get("group_by")
        or reference.get("having")
        or reference.get("order_by")
        or reference.get("limit")
        or reference.get("subquery")
        or reference.get("cte")
        or reference.get("set_operation")
        or reference.get("window")
        or reference.get("distinct")
        or reference.get("arithmetic")
        or len(reference.get("tables", ())) != 1
        or reference.get("join_count", 0) > 2
        or len(reference.get("projection", ())) not in range(1, 4)
    )
    return {
        "queryplan_v1": "CONTRACT_EXPANSION_REQUIRED"
        if unsupported
        else "SHAPE_COMPATIBLE_CATALOG_UNPROVEN",
        "window_ir": "WINDOW_SHAPE_PRESENT_REQUIRES_WHOLE_REQUEST_CHECK"
        if reference.get("window")
        else "NOT_RELEVANT",
        "governed_metric": "NOT_CONFIRMED_WHOLE_REQUEST_CONTRACT",
        "verified_memory": "OFF_NO_RUNTIME_COVERAGE",
        "direct_sql": "CURRENT_PATH_MISMATCH",
    }


def validate_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("classification") != "M20_2_RESIDUAL_SEMANTIC_FORENSICS_COMPLETED":
        raise ForensicValidationError("wrong forensic classification")
    if artifact.get("provider_calls") != 0:
        raise ForensicValidationError("provider calls must be zero")
    accounting = artifact.get("accounting", {})
    if accounting.get("total") != 774 or accounting.get("semantic_mismatch") != 189:
        raise ForensicValidationError("frozen accounting is incomplete")
    cases = artifact.get("cases", [])
    if len(cases) != 189 or len({(case["benchmark"], case["case_id"]) for case in cases}) != 189:
        raise ForensicValidationError("primary mismatch population is not exactly 189")
    causes = Counter(case["primary_root_cause"] for case in cases)
    if sum(causes.values()) != 189:
        raise ForensicValidationError("primary cause totals do not reconcile")
    if any(case.get("current_m1_status") != "ALLOWED" for case in cases):
        raise ForensicValidationError("M1-rejected case entered semantic mismatch population")


def run() -> dict[str, Any]:
    rows, m20_result, manifest, repair = _load_frozen()
    target_correct = _target_correctness(repair)
    m20_mismatch = {
        (row["benchmark"], row["case_id"])
        for row in rows
        if row["failure_stage"] == "RESULT_EVALUATION"
    }
    target_mismatch = {key for key, correct in target_correct.items() if not correct}
    mismatch_keys = m20_mismatch | target_mismatch
    if len(mismatch_keys) != 189:
        raise ForensicValidationError(f"expected 189 mismatches, found {len(mismatch_keys)}")

    sql_eval = Path(__import__("os").environ["DEFOG_SQL_EVAL_ROOT"])
    bird_root = Path(__import__("os").environ["BIRD_MINI_DEV_ROOT"])
    datasets = _load_all(sql_eval, bird_root)
    questions = {
        (label, str(question.index)): question
        for label, values in datasets.items()
        for question in values
    }
    settings = _settings()
    services: dict[tuple[str, str], tuple[Engine, Any]] = {}
    generated_results: dict[tuple[str, str], BirdResult | DefogResult] = {}
    primary_rows: list[dict[str, Any]] = []
    try:
        for row in rows:
            if row["m1_status"] == "REJECTED":
                continue
            kind = "bird" if row["benchmark"] == "bird" else "defog"
            service_key = (row["benchmark"], row["database"])
            if service_key not in services:
                _, engine, safety = _settings_for_db(settings, kind, row["database"])
                services[service_key] = engine, safety
            _, safety = services[service_key]
            planned = safety.plan(
                SqlCandidate(sql=row["generated_sql"], source=CandidateSource.LLM)
            )
            if not isinstance(planned, QueryPlan):
                raise ForensicValidationError(
                    f"accepted frozen SQL did not replan: {row['case_id']}"
                )
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution):
                raise ForensicValidationError(
                    f"accepted frozen SQL did not execute: {row['case_id']}"
                )
            generated_results[(row["benchmark"], row["case_id"])] = _result_from_execution(
                execution, kind
            )

        bird_executor = BirdBenchmarkExecutor()
        defog_executor = DefogBenchmarkExecutor()
        gold_cache: dict[tuple[str, str], BirdResult | list[tuple[str, DefogResult]]] = {}
        for row in rows:
            key = (row["benchmark"], row["case_id"])
            if key not in mismatch_keys:
                continue
            question = questions[_question_key(row)]
            reference_sql = _reference_sql(question)
            generated_facts = _ast_facts(row["generated_sql"])
            reference_facts = _ast_facts(reference_sql)
            kind = "bird" if row["benchmark"] == "bird" else "defog"
            if key not in gold_cache:
                if isinstance(question, BirdQuestion):
                    gold_cache[key] = bird_executor.execute(question.gold_sql)
                else:
                    gold_cache[key] = [
                        (variant, defog_executor.execute(question.db_name, variant))
                        for variant in canonical_gold_variants(question.gold_sql)
                    ]
            generated = generated_results[key]
            gold = gold_cache[key]
            primary, secondary, evidence = _primary_cause(
                row["generated_sql"], reference_sql, question.question
            )
            generated_result = _result_record(generated)
            if isinstance(gold, BirdResult):
                reference_result = _result_record(gold)
                matched = _compare(row, question, cast(BirdResult, generated), gold)
            else:
                reference_result = {
                    "variants": [_result_record(result) for _, result in gold],
                }
                matched = _compare(row, question, cast(DefogResult, generated), gold)
            if matched:
                raise ForensicValidationError(
                    f"frozen mismatch replay became correct: {row['case_id']}"
                )
            primary_rows.append(
                {
                    "case_id": row["case_id"],
                    "benchmark": row["benchmark"],
                    "database": row["database"],
                    "question": question.question,
                    "evidence": getattr(question, "evidence", "")
                    or getattr(question, "instructions", ""),
                    "baseline_m1_status": row["m1_status"],
                    "current_m1_status": "ALLOWED",
                    "generated_sql": row["generated_sql"],
                    "reference_sql": reference_sql,
                    "primary_root_cause": primary,
                    "secondary_tags": secondary,
                    "generated_ast_facts": generated_facts,
                    "reference_ast_facts": reference_facts,
                    "ast_differences": _feature_delta(generated_facts, reference_facts),
                    "causal_evidence": evidence + ["offline reference/result comparison"],
                    "generated_result": generated_result,
                    "reference_result": reference_result,
                    "result_match": False,
                    "counterfactual": {
                        "status": "NOT_RUN",
                        "reason": "not required for this deterministic divergence",
                    },
                    "existing_capability_relevance": _capability_relevance(
                        generated_facts, reference_facts
                    ),
                }
            )
    finally:
        for engine, _ in services.values():
            engine.dispose()

    if len(primary_rows) != 189:
        raise ForensicValidationError(f"forensic rows produced: {len(primary_rows)}")
    primary_rows.sort(key=lambda row: (row["benchmark"], row["case_id"]))
    benchmark_causes: dict[str, dict[str, int]] = {}
    for benchmark in EXPECTED_COUNTS:
        benchmark_causes[benchmark] = dict(
            Counter(
                row["primary_root_cause"] for row in primary_rows if row["benchmark"] == benchmark
            )
        )

    all_current_correct = {
        (row["benchmark"], row["case_id"])
        for row in rows
        if row["m1_status"] == "ALLOWED" and row["correct"]
    } | {key for key, correct in target_correct.items() if correct}
    current_accepted = {
        (row["benchmark"], row["case_id"]) for row in rows if row["m1_status"] == "ALLOWED"
    } | set(target_correct)
    all_executed = [row for row in rows if (row["benchmark"], row["case_id"]) in current_accepted]
    shape_groups = (
        ("0_joins", lambda f: f.get("join_count", 0) == 0),
        ("1_join", lambda f: f.get("join_count", 0) == 1),
        ("2_plus_joins", lambda f: f.get("join_count", 0) >= 2),
        ("aggregation", lambda f: bool(f.get("aggregation"))),
        ("window", lambda f: bool(f.get("window"))),
        ("subquery", lambda f: bool(f.get("subquery"))),
        ("ratio_or_arithmetic", lambda f: bool(f.get("arithmetic"))),
        ("temporal_question", lambda f: _temporal_question(f["question"])),
    )
    shape_breakdown: dict[str, dict[str, int | float]] = {}
    for name, predicate in shape_groups:
        shape_selected: list[dict[str, Any]] = []
        for row in all_executed:
            question = questions[_question_key(row)]
            facts = {**_ast_facts(row["generated_sql"]), "question": question.question}
            if predicate(facts):
                shape_selected.append(row)
        correct = sum(
            (row["benchmark"], row["case_id"]) in all_current_correct for row in shape_selected
        )
        wrong = len(shape_selected) - correct
        shape_breakdown[name] = {
            "executed": len(shape_selected),
            "correct": correct,
            "wrong": wrong,
            "error_rate": round(wrong / len(shape_selected), 6) if shape_selected else 0.0,
        }

    root_mechanisms = {}
    for cause in PRIMARY_CAUSES:
        cause_rows = [row for row in primary_rows if row["primary_root_cause"] == cause]
        if not cause_rows:
            continue
        root_mechanisms[cause] = {
            "count": len(cause_rows),
            "percentage_of_189": round(len(cause_rows) / 189 * 100, 3),
            "benchmarks": dict(Counter(row["benchmark"] for row in cause_rows)),
            "representative_case_ids": [row["case_id"] for row in cause_rows[:5]],
            "earliest_causal_decision": cause_rows[0]["causal_evidence"][0],
            "counterfactual_cases": 0,
            "existing_capability_relevance": dict(
                Counter(row["existing_capability_relevance"]["queryplan_v1"] for row in cause_rows)
            ),
            **ROOT_INTERVENTION_NOTES[cause],
        }

    accounting = {
        "total": 774,
        "correct": len(all_current_correct),
        "semantic_mismatch": len(primary_rows),
        "legitimate_m1_reject": sum(row["m1_status"] == "REJECTED" for row in rows),
        "planning_error": 0,
        "execution_failure": 0,
        "unaccounted": (
            774
            - len(all_current_correct)
            - len(primary_rows)
            - sum(row["m1_status"] == "REJECTED" for row in rows)
        ),
    }
    if accounting != {
        "total": 774,
        "correct": 321,
        "semantic_mismatch": 189,
        "legitimate_m1_reject": 264,
        "planning_error": 0,
        "execution_failure": 0,
        "unaccounted": 0,
    }:
        raise ForensicValidationError(f"current accounting mismatch: {accounting}")

    artifact = {
        "classification": "M20_2_RESIDUAL_SEMANTIC_FORENSICS_COMPLETED",
        "starting_commit": "2b08bc3c81a3aafa71e4668624055603b0bf54a8",
        "analysis_commit": _head(),
        "m20_manifest_hash": manifest["manifest_hash"],
        "baseline_artifact_hashes": {
            "m20_cases": _sha256(M20_CASES),
            "m20_result": _sha256(M20_RESULT),
            "m20_manifest": _sha256(M20_MANIFEST),
            "m20_1r2": _sha256(M20_1R2),
        },
        "provider_calls": 0,
        "fresh_sql_generation": 0,
        "production_changes": 0,
        "accounting": accounting,
        "primary_root_causes": root_mechanisms,
        "benchmark_breakdown": {
            benchmark: {
                "total": EXPECTED_COUNTS[benchmark],
                "executed": sum(key[0] == benchmark for key in current_accepted),
                "correct": sum(key[0] == benchmark for key in all_current_correct),
                "semantic_mismatch": sum(row["benchmark"] == benchmark for row in primary_rows),
                "legitimate_m1_reject": sum(
                    row["benchmark"] == benchmark and row["m1_status"] == "REJECTED" for row in rows
                ),
                "primary_root_causes": benchmark_causes[benchmark],
            }
            for benchmark in EXPECTED_COUNTS
        },
        "shape_breakdown": shape_breakdown,
        "existing_capability_relevance": {
            "QueryPlan V1": (
                "No production integration; shape relevance recorded "
                "conservatively per residual case."
            ),
            "Window IR": (
                "No production integration; only existing-contract window shape is marked."
            ),
            "Governed Metric": (
                "Whole-request contract not confirmed; metric recognition "
                "is not treated as coverage."
            ),
            "Verified Memory": "OFF; no runtime coverage in this analysis.",
            "Direct SQL": "All 189 residuals were generated by and accepted on the direct path.",
        },
        "residual_policy_rejects": {
            "count": 264,
            "status": "OUTSIDE_M20_2_PRIMARY_POPULATION",
        },
        "cases": primary_rows,
        "method": {
            "reference_usage": "offline benchmark evidence only",
            "counterfactual_execution": "none in this run",
            "classification_basis": (
                "question/reference review plus deterministic AST and typed-result evidence"
            ),
        },
        "recommended_next_intervention": {
            "mechanism": "SCHEMA_GROUNDING",
            "status": "RECOMMENDED_NOT_IMPLEMENTED",
            "rationale": (
                "largest cross-benchmark primary mechanism with a "
                "server-owned catalog/context intervention surface"
            ),
        },
    }
    validate_artifact(artifact)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    artifact = run()
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "classification": artifact["classification"],
                "accounting": artifact["accounting"],
                "primary_root_causes": artifact["primary_root_causes"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
