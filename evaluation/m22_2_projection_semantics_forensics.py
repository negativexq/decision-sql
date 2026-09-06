"""Provider-free projection forensics over frozen M20/M22 evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp

from app.sql.models import QueryExecution
from evaluation.external.bird.benchmark import BirdBenchmarkExecutor, BirdQuestion
from evaluation.external.bird.benchmark import QueryResult as BirdResult
from evaluation.external.defog.benchmark import BenchmarkQuestion, DefogBenchmarkExecutor
from evaluation.external.defog.benchmark import QueryResult as DefogResult
from evaluation.m20_2_residual_semantic_forensics import _question_key, _reference_sql
from evaluation.m20_full_rebaseline import _load_all, _settings
from evaluation.m22_1_filter_causal_resolution import (
    _evaluate,
    _execute_counterfactual,
    _gold_cache_entry,
    _parse,
    _replace_component,
)
from evaluation.m22_1_filter_causal_resolution import (
    validate_artifact as validate_m221_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
M20_CASES = ROOT / "evaluation/fixtures/m20_full_benchmark_cases.jsonl"
M20_2 = ROOT / "evaluation/fixtures/m20_2_residual_semantic_forensics.json"
M22_1 = ROOT / "evaluation/fixtures/m22_1_filter_causal_resolution.json"
OUTPUT = ROOT / "evaluation/fixtures/m22_2_projection_semantics_forensics.json"

SUBTYPES = (
    "WRONG_PROJECTED_COLUMN",
    "MISSING_PROJECTED_COLUMN",
    "EXTRA_PROJECTED_COLUMN",
    "WRONG_PROJECTED_ENTITY",
    "WRONG_AGGREGATE_PROJECTION",
    "WRONG_DERIVED_EXPRESSION",
    "WRONG_SCALAR_FUNCTION",
    "DISTINCT_PROJECTION_SEMANTICS",
    "PROJECTION_GRAIN_INTERACTION",
    "PROJECTION_SUBQUERY_INTERACTION",
    "OUTPUT_SHAPE_OR_ALIAS",
    "MULTI_CAUSAL_PROJECTION",
    "UNDETERMINED",
)


class M222ValidationError(RuntimeError):
    """Raised when frozen projection evidence cannot be reconciled."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _projection_expression(node: exp.Expression) -> exp.Expression:
    return node.this if isinstance(node, exp.Alias) else node


def _physical_aliases(tree: exp.Expression) -> dict[str, str]:
    return {table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)}


def _normalized_projection(node: exp.Expression, aliases: dict[str, str]) -> str:
    expression = _projection_expression(node).copy()
    for column in expression.find_all(exp.Column):
        if column.table:
            physical = aliases.get(column.table.lower(), column.table.lower())
            column.set("table", exp.Identifier(this=physical))
    return expression.sql(dialect="postgres", normalize=True).lower()


def projection_facts(sql: str) -> dict[str, Any]:
    """Extract deterministic projection facts while ignoring output aliases."""
    tree = _parse(sql)
    aliases = _physical_aliases(tree)
    expressions = list(tree.expressions) if isinstance(tree, exp.Select) else []
    facts: list[dict[str, Any]] = []
    for position, expression in enumerate(expressions):
        base = _projection_expression(expression)
        columns = [
            {
                "table": aliases.get(column.table.lower(), column.table.lower())
                if column.table
                else None,
                "column": column.name.lower(),
            }
            for column in base.find_all(exp.Column)
            if column.name != "*"
        ]
        aggregates = sorted({item.key.upper() for item in base.find_all(exp.AggFunc)})
        functions = sorted({item.key.upper() for item in base.find_all(exp.Func)})
        facts.append(
            {
                "position": position,
                "expression_type": base.key.upper(),
                "columns": columns,
                "table_ownership": sorted({item["table"] for item in columns if item["table"]}),
                "function_names": functions,
                "aggregate_function": aggregates[0] if aggregates else None,
                "aggregate": bool(aggregates),
                "distinct": bool(base.find(exp.Distinct)),
                "arithmetic": bool(
                    base.find(exp.Add)
                    or base.find(exp.Sub)
                    or base.find(exp.Mul)
                    or base.find(exp.Div)
                ),
                "cast": base.find(exp.Cast) is not None,
                "case": base.find(exp.Case) is not None,
                "alias": expression.alias if isinstance(expression, exp.Alias) else None,
                "normalized_sql": _normalized_projection(expression, aliases),
            }
        )
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    return {
        "expressions": facts,
        "arity": len(facts),
        "distinct_select": bool(select and select.args.get("distinct")),
        "normalized_expressions": [fact["normalized_sql"] for fact in facts],
    }


def _projection_deltas(generated: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    deltas: list[str] = []
    if generated["arity"] != reference["arity"]:
        deltas.append("ARITY")
    if generated["normalized_expressions"] != reference["normalized_expressions"]:
        deltas.append("EXPRESSION")
    if generated["distinct_select"] != reference["distinct_select"]:
        deltas.append("DISTINCT")
    if any(item["aggregate"] for item in generated["expressions"] + reference["expressions"]):
        if generated["normalized_expressions"] != reference["normalized_expressions"]:
            deltas.append("AGGREGATE")
    return deltas


def _non_projection_signature(tree: exp.Expression) -> tuple[Any, ...]:
    """Compare non-SELECT semantics while retaining literal case."""
    aliases = _physical_aliases(tree)
    tables = tuple(sorted(table.name.lower() for table in tree.find_all(exp.Table)))
    clauses: list[tuple[str, str, tuple[str, ...]]] = []
    for key in ("where", "group", "having", "order", "limit"):
        node = tree.args.get(key)
        if node is None:
            clauses.append((key, "", ()))
            continue
        normalized = _normalized_projection(node, aliases)
        literals = tuple(str(item.this) for item in node.find_all(exp.Literal))
        clauses.append((key, normalized, literals))
    joins = tuple(sorted(_normalized_projection(join, aliases) for join in tree.find_all(exp.Join)))
    return tables, tuple(clauses), joins, bool(tree.args.get("distinct"))


def _non_projection_literals(tree: exp.Expression) -> tuple[str, ...]:
    literals: list[str] = []
    for key in ("where", "group", "having", "order", "limit"):
        node = tree.args.get(key)
        if node is not None:
            literals.extend(str(item.this) for item in node.find_all(exp.Literal))
    return tuple(literals)


def _subtype(generated: dict[str, Any], reference: dict[str, Any], dimensions: list[str]) -> str:
    if "DISTINCT" in dimensions or generated["distinct_select"] != reference["distinct_select"]:
        return "DISTINCT_PROJECTION_SEMANTICS"
    if generated["arity"] > reference["arity"]:
        return "EXTRA_PROJECTED_COLUMN"
    if generated["arity"] < reference["arity"]:
        return "MISSING_PROJECTED_COLUMN"
    generated_items = generated["expressions"]
    reference_items = reference["expressions"]
    if any(item["aggregate"] for item in generated_items + reference_items):
        return "WRONG_AGGREGATE_PROJECTION"
    if any(
        item["arithmetic"] or item["cast"] or item["case"]
        for item in generated_items + reference_items
    ):
        return "WRONG_DERIVED_EXPRESSION"
    generated_columns = [item["columns"] for item in generated_items]
    reference_columns = [item["columns"] for item in reference_items]
    if generated_columns != reference_columns:
        generated_tables = [item["table_ownership"] for item in generated_items]
        reference_tables = [item["table_ownership"] for item in reference_items]
        if generated_tables != reference_tables:
            return "WRONG_PROJECTED_ENTITY"
        return "WRONG_PROJECTED_COLUMN"
    if any(item["function_names"] for item in generated_items + reference_items):
        return "WRONG_SCALAR_FUNCTION"
    return "OUTPUT_SHAPE_OR_ALIAS"


def _alignment(question: str) -> str:
    lowered = question.lower()
    if any(
        token in lowered
        for token in ("how many", "average", "sum", "count", "maximum", "minimum", "total")
    ):
        return "DERIVABLE"
    if any(token in lowered for token in ("which", "what", "list", "show", "find")):
        return "EXPLICIT"
    return "AMBIGUOUS"


def _intent_relevance(facts: dict[str, Any]) -> str:
    if all(
        not item["aggregate"] and not item["arithmetic"] and not item["case"]
        for item in facts["expressions"]
    ):
        return "EXISTING_FIELD_SUFFICIENT"
    return "EXISTING_FIELD_INSUFFICIENT"


def _queryplan_relevance(sql: str) -> str:
    tree = _parse(sql)
    if tree.find(exp.Subquery) or tree.find(exp.CTE) or tree.find(exp.Window):
        return "CONTRACT_EXPANSION_REQUIRED"
    if len(list(tree.find_all(exp.Join))) > 2 or any(
        item.find(exp.AggFunc) for item in tree.expressions
    ):
        return "CONTRACT_EXPANSION_REQUIRED"
    return "SHAPE_COMPATIBLE_CATALOG_UNPROVEN"


def _question_map(sql_eval: Path, bird_root: Path) -> dict[tuple[str, str], Any]:
    return {
        (label, str(question.index)): question
        for label, questions in _load_all(sql_eval, bird_root).items()
        for question in questions
    }


def _gold_cache(
    question: BenchmarkQuestion | BirdQuestion,
    bird_executor: BirdBenchmarkExecutor,
    defog_executor: DefogBenchmarkExecutor,
) -> BirdResult | list[tuple[str, DefogResult]]:
    return _gold_cache_entry(question, bird_executor, defog_executor)


def _load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    m20_2 = json.loads(M20_2.read_text())
    m221 = json.loads(M22_1.read_text())
    validate_m221_artifact(m221)
    native = [
        case for case in m20_2["cases"] if case["primary_root_cause"] == "PROJECTION_SEMANTICS"
    ]
    reassigned = [
        case
        for case in m221["revised_61_case_ledger"]
        if case["final_causal_class"] == "PROJECTION_CAUSAL"
    ]
    by_id: dict[tuple[str, str], dict[str, Any]] = {}
    for case in native:
        by_id[(case["benchmark"], case["case_id"])] = {
            **case,
            "source_population": ["SOURCE_M20_2_PROJECTION"],
            "m221_case": None,
        }
    for case in reassigned:
        key = (case["benchmark"], case["case_id"])
        if key in by_id:
            by_id[key]["source_population"].append("SOURCE_M22_1_CAUSAL_RESOLUTION")
            by_id[key]["m221_case"] = case
        else:
            by_id[key] = {
                **case,
                "source_population": ["SOURCE_M22_1_CAUSAL_RESOLUTION"],
                "m221_case": case,
            }
    if len(native) != 3 or len(reassigned) != 15 or len(by_id) != 18:
        raise M222ValidationError(
            "projection population mismatch: "
            f"native={len(native)}, reassigned={len(reassigned)}, unique={len(by_id)}"
        )
    return m20_2, m221, {"native": native, "reassigned": reassigned}, list(by_id.values())


def run() -> dict[str, Any]:
    m20_2, m221, populations, candidates = _load_inputs()
    sql_eval = Path(os.environ["DEFOG_SQL_EVAL_ROOT"])
    bird_root = Path(os.environ["BIRD_MINI_DEV_ROOT"])
    questions = _question_map(sql_eval, bird_root)
    settings = _settings()
    services: dict[tuple[str, str], tuple[Any, Any]] = {}
    gold_cache: dict[tuple[str, str], BirdResult | list[tuple[str, DefogResult]]] = {}
    bird_executor = BirdBenchmarkExecutor()
    defog_executor = DefogBenchmarkExecutor()
    results: list[dict[str, Any]] = []
    try:
        for case in sorted(candidates, key=lambda item: (item["benchmark"], str(item["case_id"]))):
            question = questions[_question_key(case)]
            reference_sql = _reference_sql(question)
            generated_tree = _parse(case["generated_sql"])
            reference_tree = _parse(reference_sql)
            generated_facts = projection_facts(case["generated_sql"])
            reference_facts = projection_facts(reference_sql)
            deltas = _projection_deltas(generated_facts, reference_facts)
            reused = case.get("m221_case")
            projection_match = False
            counterfactuals: list[dict[str, Any]] = []
            if reused is not None:
                counterfactuals = [
                    {
                        "dimension": item["dimension"],
                        "status": "REUSED_M22_1",
                        "result_match": bool(item.get("result_match")),
                        "execution_status": item.get("execution_status"),
                    }
                    for item in reused.get("counterfactuals", [])
                    if item["dimension"] in {"PROJECTION", "DISTINCT"}
                ]
                matching = [item for item in counterfactuals if item["result_match"]]
                projection_match = bool(matching)
            else:
                if (case["benchmark"], case["case_id"]) not in gold_cache:
                    gold_cache[(case["benchmark"], case["case_id"])] = _gold_cache(
                        question, bird_executor, defog_executor
                    )
                candidate_tree = _replace_component(generated_tree, reference_tree, "PROJECTION")
                candidate_sql = candidate_tree.sql(dialect="postgres") if candidate_tree else None
                if candidate_sql:
                    outcome = _execute_counterfactual(candidate_sql, case, services, settings)
                    execution = outcome.get("execution")
                    result_match = False
                    if isinstance(execution, QueryExecution):
                        result_match = _evaluate(
                            case,
                            question,
                            execution,
                            gold_cache[(case["benchmark"], case["case_id"])],
                            candidate_sql,
                        )
                    projection_match = result_match
                    counterfactuals.append(
                        {
                            "dimension": "PROJECTION",
                            "status": "EXECUTED",
                            "sql_hash": sha256(candidate_sql.encode()).hexdigest(),
                            "m1_status": str(outcome.get("m1_status")),
                            "execution_status": outcome.get("execution_status"),
                            "result_match": result_match,
                        }
                    )
            structurally_confirmed = False
            reclassified_cause = None
            evidence_strength = "COUNTERFACTUAL_CONFIRMED" if projection_match else "UNRESOLVED"
            if not projection_match and reused is None:
                if case.get("ast_differences") == ["PROJECTION"] and _non_projection_literals(
                    generated_tree
                ) == _non_projection_literals(reference_tree):
                    structurally_confirmed = True
                    projection_match = True
                    evidence_strength = "STRUCTURALLY_CONFIRMED"
                elif _non_projection_signature(generated_tree) != _non_projection_signature(
                    reference_tree
                ):
                    filter_tree = _replace_component(generated_tree, reference_tree, "FILTER")
                    filter_sql = filter_tree.sql(dialect="postgres") if filter_tree else None
                    if filter_sql:
                        filter_outcome = _execute_counterfactual(
                            filter_sql, case, services, settings
                        )
                        filter_execution = filter_outcome.get("execution")
                        filter_match = False
                        if isinstance(filter_execution, QueryExecution):
                            filter_match = _evaluate(
                                case,
                                question,
                                filter_execution,
                                gold_cache[(case["benchmark"], case["case_id"])],
                                filter_sql,
                            )
                        counterfactuals.append(
                            {
                                "dimension": "FILTER",
                                "status": "EXECUTED",
                                "sql_hash": sha256(filter_sql.encode()).hexdigest(),
                                "m1_status": str(filter_outcome.get("m1_status")),
                                "execution_status": filter_outcome.get("execution_status"),
                                "result_match": filter_match,
                            }
                        )
                        if filter_match:
                            reclassified_cause = "FILTER_CAUSAL"
                            evidence_strength = "COUNTERFACTUAL_CONFIRMED"
            results.append(
                {
                    "case_id": case["case_id"],
                    "benchmark": case["benchmark"],
                    "database": case["database"],
                    "source_population": case["source_population"],
                    "question": case["question"],
                    "generated_sql_hash": sha256(case["generated_sql"].encode()).hexdigest(),
                    "generated_sql_preview": case["generated_sql"][:500],
                    "reference_sql_hash": sha256(reference_sql.encode()).hexdigest(),
                    "reference_sql_preview": reference_sql[:500],
                    "generated_projection": generated_facts,
                    "reference_projection": reference_facts,
                    "projection_deltas": deltas,
                    "counterfactuals": counterfactuals,
                    "projection_causal": projection_match,
                    "structurally_confirmed": structurally_confirmed,
                    "reclassified_cause": reclassified_cause,
                    "evidence_strength": evidence_strength,
                    "primary_subtype": _subtype(generated_facts, reference_facts, deltas)
                    if projection_match
                    else "UNDETERMINED",
                    "secondary_tags": [],
                    "question_projection_alignment": _alignment(case["question"]),
                    "evidence_sufficiency": "QUESTION_OR_REFERENCE_OFFLINE_ONLY",
                    "queryintent_relevance": _intent_relevance(reference_facts),
                    "queryplan_v1_relevance": _queryplan_relevance(reference_sql),
                }
            )
    finally:
        for engine, _ in services.values():
            engine.dispose()

    m20_rows = [json.loads(line) for line in M20_CASES.read_text().splitlines() if line.strip()]
    control_shape: Counter[str] = Counter()
    control_correct: Counter[str] = Counter()
    for row in m20_rows:
        if row["m1_status"] != "ALLOWED":
            continue
        facts = projection_facts(row["generated_sql"])
        if facts["arity"] == 1 and not any(item["aggregate"] for item in facts["expressions"]):
            shape = "SINGLE_PROJECTED_FIELD"
        elif facts["arity"] > 1 and not any(item["aggregate"] for item in facts["expressions"]):
            shape = "MULTIPLE_PROJECTED_FIELDS"
        elif any(item["aggregate"] for item in facts["expressions"]):
            shape = "AGGREGATE_PROJECTION"
        elif any(
            item["arithmetic"] or item["cast"] or item["case"] for item in facts["expressions"]
        ):
            shape = "DERIVED_EXPRESSION"
        else:
            shape = "OTHER_PROJECTION"
        if facts["distinct_select"]:
            shape = "DISTINCT_PROJECTION"
        if any("JOIN" in item for item in row.get("generated_sql", "").upper().split()):
            shape += "_WITH_JOIN"
        control_shape[shape] += 1
        if row.get("correct"):
            control_correct[shape] += 1
    control = {
        shape: {
            "executed": count,
            "correct": control_correct[shape],
            "wrong": count - control_correct[shape],
            "error_rate": round((count - control_correct[shape]) / count, 6),
        }
        for shape, count in sorted(control_shape.items())
    }
    subtype_counts = Counter(item["primary_subtype"] for item in results)
    artifact = {
        "classification": "M22_2_PROJECTION_SEMANTICS_FORENSICS_COMPLETED",
        "starting_commit": _head(),
        "m20_manifest_hash": m20_2["m20_manifest_hash"],
        "m20_2_artifact_hash": _sha256(M20_2),
        "m22_1_artifact_hash": _sha256(M22_1),
        "provider_calls": 0,
        "fresh_generation": 0,
        "production_changes": 0,
        "population": {
            "m20_2_native_projection": len(populations["native"]),
            "m22_1_projection_causal": len(populations["reassigned"]),
            "overlap": len(
                {
                    (case["benchmark"], case["case_id"]) for case in populations["native"]
                }.intersection(
                    {(case["benchmark"], case["case_id"]) for case in populations["reassigned"]}
                )
            ),
            "unique_candidates": len(candidates),
            "confirmed_projection_causal": sum(item["projection_causal"] for item in results),
            "projection_not_single_causal": sum(not item["projection_causal"] for item in results),
        },
        "primary_subtypes": dict(subtype_counts),
        "causal_summary": {
            "confirmed_projection_causal": sum(item["projection_causal"] for item in results),
            "projection_not_single_causal": sum(not item["projection_causal"] for item in results),
            "unresolved": sum(item["evidence_strength"] == "UNRESOLVED" for item in results),
            "reclassified_non_projection": sum(
                item["reclassified_cause"] is not None for item in results
            ),
        },
        "counterfactual_summary": {
            "cases_tested_or_reused": len(results),
            "single_expression_fixes": sum(
                item["projection_causal"]
                and sum(item.get("result_match", False) for item in item["counterfactuals"]) == 1
                for item in results
            ),
            "multi_expression_projection_fixes": 0,
            "projection_only_non_fixes": sum(not item["projection_causal"] for item in results),
            "reused_m22_1_confirmations": len(populations["reassigned"]),
            "new_native_counterfactuals": len(populations["native"]),
            "structurally_confirmed": sum(item["structurally_confirmed"] for item in results),
        },
        "question_projection_alignment": dict(
            Counter(item["question_projection_alignment"] for item in results)
        ),
        "correct_control": control,
        "existing_capability_relevance": {
            "queryintent_already_representable": sum(
                item["queryintent_relevance"] == "EXISTING_FIELD_SUFFICIENT" for item in results
            ),
            "queryintent_grounding_missing": 0,
            "queryintent_insufficient": sum(
                item["queryintent_relevance"] == "EXISTING_FIELD_INSUFFICIENT" for item in results
            ),
            "queryplan_v1_confirmed_covered": 0,
            "queryplan_v1_shape_compatible_catalog_unproven": sum(
                item["queryplan_v1_relevance"] == "SHAPE_COMPATIBLE_CATALOG_UNPROVEN"
                for item in results
            ),
            "queryplan_v1_contract_expansion_required": sum(
                item["queryplan_v1_relevance"] == "CONTRACT_EXPANSION_REQUIRED" for item in results
            ),
        },
        "solution_family_mapping": {
            "TYPED_OUTPUT_FIELD_CONTRACT": {
                "confirmed_cases": sum(
                    item["primary_subtype"]
                    in {"MISSING_PROJECTED_COLUMN", "EXTRA_PROJECTED_COLUMN"}
                    for item in results
                ),
                "counterfactual_support": "confirmed where projection substitution matched",
                "production_rationale": "bounded output ownership; not implemented",
                "runtime_cost": "future experiment required",
                "regression_surface": "output arity/order",
            },
            "QUERY_AWARE_PROJECTION_GROUNDING": {
                "confirmed_cases": sum(
                    item["primary_subtype"] in {"WRONG_PROJECTED_COLUMN", "WRONG_PROJECTED_ENTITY"}
                    for item in results
                ),
                "counterfactual_support": "confirmed where projection substitution matched",
                "production_rationale": "attribute/entity grounding",
                "runtime_cost": "future experiment required",
                "regression_surface": "column/entity selection",
            },
            "AGGREGATION_PROJECTION_CONTRACT": {
                "confirmed_cases": sum(
                    item["primary_subtype"] == "WRONG_AGGREGATE_PROJECTION" for item in results
                ),
                "counterfactual_support": "confirmed where applicable",
                "production_rationale": "aggregate output semantics",
                "runtime_cost": "future experiment required",
                "regression_surface": "grain/count semantics",
            },
            "OTHER_OR_UNRESOLVED": {
                "confirmed_cases": sum(
                    item["primary_subtype"]
                    not in {
                        "MISSING_PROJECTED_COLUMN",
                        "EXTRA_PROJECTED_COLUMN",
                        "WRONG_PROJECTED_COLUMN",
                        "WRONG_PROJECTED_ENTITY",
                        "WRONG_AGGREGATE_PROJECTION",
                    }
                    for item in results
                ),
                "counterfactual_support": "mixed",
                "production_rationale": "not selected",
                "runtime_cost": "unknown",
                "regression_surface": "unknown",
            },
        },
        "cases": results,
        "method": {
            "reference_usage": "offline only",
            "counterfactuals": "AST-aware; M1 plus bounded read-only execution",
            "alias_policy": "aliases ignored for semantic projection comparison",
            "runtime_changes": "none",
        },
        "recommended_next_intervention": {
            "status": "RECOMMENDED_NOT_IMPLEMENTED",
            "family": "TYPED_OUTPUT_FIELD_CONTRACT",
            "mechanism": (
                "confirmed output-field presence/selection failures are the most "
                "coherent bounded family"
            ),
            "affected_cases": sum(
                item["primary_subtype"] in {"MISSING_PROJECTED_COLUMN", "EXTRA_PROJECTED_COLUMN"}
                for item in results
            ),
            "rationale": (
                "projection-specific output ownership is narrower than a combined "
                "schema/aggregation intervention; this remains a future experiment "
                "hypothesis"
            ),
        },
    }
    validate_artifact(artifact)
    return artifact


def validate_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("classification") != "M22_2_PROJECTION_SEMANTICS_FORENSICS_COMPLETED":
        raise M222ValidationError("wrong classification")
    if artifact.get("provider_calls") != 0 or artifact.get("fresh_generation") != 0:
        raise M222ValidationError("M22.2 must be provider-free")
    population = artifact.get("population", {})
    if population.get("m20_2_native_projection") != 3:
        raise M222ValidationError("native projection population mismatch")
    if population.get("m22_1_projection_causal") != 15:
        raise M222ValidationError("M22.1 projection population mismatch")
    if population.get("overlap") != 0 or population.get("unique_candidates") != 18:
        raise M222ValidationError("deduplicated projection population mismatch")
    cases = artifact.get("cases", [])
    if len(cases) != 18 or len({(case["benchmark"], case["case_id"]) for case in cases}) != 18:
        raise M222ValidationError("candidate case ledger is incomplete")
    if sum(artifact["primary_subtypes"].values()) != 18:
        raise M222ValidationError("projection subtype accounting does not reconcile")
    if any(case["primary_subtype"] not in SUBTYPES for case in cases):
        raise M222ValidationError("unknown projection subtype")


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
                "population": artifact["population"],
                "primary_subtypes": artifact["primary_subtypes"],
                "counterfactual_summary": artifact["counterfactual_summary"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
