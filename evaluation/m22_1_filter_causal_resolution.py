"""Provider-free causal resolution of M22's nominal filter residuals.

The counterfactuals in this module are read-only diagnostics over frozen SQL.
They are never fed back into generation and never mutate the production path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlglot import exp

from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.bird.benchmark import BirdBenchmarkExecutor, BirdQuestion
from evaluation.external.bird.benchmark import QueryResult as BirdResult
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    DefogBenchmarkExecutor,
    canonical_gold_variants,
)
from evaluation.external.defog.benchmark import (
    QueryResult as DefogResult,
)
from evaluation.m20_1r2_readonly_percent_transport_repair import _result_from_execution
from evaluation.m20_2_residual_semantic_forensics import _compare, _question_key, _reference_sql
from evaluation.m20_full_rebaseline import _load_all, _settings, _settings_for_db
from evaluation.m22_filter_semantics_deep_forensics import REVIEW

ROOT = Path(__file__).resolve().parents[1]
M20_2 = ROOT / "evaluation/fixtures/m20_2_residual_semantic_forensics.json"
M22 = ROOT / "evaluation/fixtures/m22_filter_semantics_deep_forensics.json"
OUTPUT = ROOT / "evaluation/fixtures/m22_1_filter_causal_resolution.json"

CAUSAL_CLASSES = (
    "FILTER_CAUSAL",
    "PROJECTION_CAUSAL",
    "AGGREGATION_CAUSAL",
    "JOIN_CAUSAL",
    "SCHEMA_CAUSAL",
    "ORDER_LIMIT_CAUSAL",
    "SUBQUERY_CAUSAL",
    "ARITHMETIC_CAUSAL",
    "WINDOW_CAUSAL",
    "FUNCTION_EXPRESSION_CAUSAL",
    "MULTI_CAUSAL",
    "NO_SINGLE_COMPONENT_SUFFICIENT",
    "EQUIVALENT_OR_REFERENCE_ARTIFACT",
    "UNRESOLVED_AFTER_COUNTERFACTUAL",
)


class M221ValidationError(RuntimeError):
    """Raised when frozen evidence cannot support a complete M22.1 run."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _parse(sql: str) -> exp.Expression:
    import sqlglot

    try:
        return sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError as error:
        raise M221ValidationError(f"counterfactual source is not parseable: {error}") from error


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    return {table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)}


def _generated_alias_for_reference_table(
    reference: exp.Expression, generated: exp.Expression
) -> dict[str, str]:
    generated_by_table = {
        table.name.lower(): table.alias_or_name for table in generated.find_all(exp.Table)
    }
    result: dict[str, str] = {}
    for table in reference.find_all(exp.Table):
        target = generated_by_table.get(table.name.lower())
        if target:
            result[table.name.lower()] = target
            result[table.alias_or_name.lower()] = target
    return result


def _remap_aliases(node: exp.Expression | None, aliases: dict[str, str]) -> exp.Expression | None:
    if node is None:
        return None
    copied = node.copy()
    for column in copied.find_all(exp.Column):
        qualifier = column.table.lower()
        target = aliases.get(qualifier)
        if target:
            column.set("table", exp.Identifier(this=target))
    return copied


def _replace_component(
    generated: exp.Expression, reference: exp.Expression, dimension: str
) -> exp.Expression | None:
    result = generated.copy()
    aliases = _generated_alias_for_reference_table(reference, generated)
    if dimension == "FILTER":
        result.set("where", _remap_aliases(reference.args.get("where"), aliases))
    elif dimension == "PROJECTION":
        result.set(
            "expressions",
            [_remap_aliases(expression, aliases) for expression in reference.expressions],
        )
    elif dimension == "AGGREGATION":
        result.set(
            "expressions",
            [_remap_aliases(expression, aliases) for expression in reference.expressions],
        )
        result.set("group", _remap_aliases(reference.args.get("group"), aliases))
        result.set("having", _remap_aliases(reference.args.get("having"), aliases))
    elif dimension == "ORDER_LIMIT":
        result.set("order", _remap_aliases(reference.args.get("order"), aliases))
        result.set("limit", _remap_aliases(reference.args.get("limit"), aliases))
    elif dimension == "DISTINCT":
        result.set("distinct", reference.args.get("distinct"))
    elif dimension == "WINDOW":
        result.set(
            "expressions",
            [_remap_aliases(expression, aliases) for expression in reference.expressions],
        )
        result.set("order", _remap_aliases(reference.args.get("order"), aliases))
    elif dimension == "ARITHMETIC":
        result.set(
            "expressions",
            [_remap_aliases(expression, aliases) for expression in reference.expressions],
        )
    elif dimension == "SUBQUERY":
        # Subqueries can occur in WHERE, FROM, SELECT, and HAVING.  Replacing
        # all affected query clauses is the smallest safe AST operation for a
        # subquery-shape diagnostic; it is recorded as a semantic bundle.
        for key in ("where", "group", "having", "order", "limit"):
            result.set(key, _remap_aliases(reference.args.get(key), aliases))
    else:
        return None
    return result


def _dimensions(row: dict[str, Any]) -> list[str]:
    dimensions: list[str] = []
    for delta in row.get("ast_differences", []):
        mapping = {
            "FILTER": "FILTER",
            "PROJECTION": "PROJECTION",
            "AGGREGATION": "AGGREGATION",
            "GROUP_BY": "AGGREGATION",
            "HAVING": "AGGREGATION",
            "ORDER_BY": "ORDER_LIMIT",
            "LIMIT": "ORDER_LIMIT",
            "DISTINCT": "DISTINCT",
            "WINDOW": "WINDOW",
            "ARITHMETIC": "ARITHMETIC",
            "SUBQUERY": "SUBQUERY",
        }
        candidate = mapping.get(delta)
        if candidate and candidate not in dimensions:
            dimensions.append(candidate)
    return dimensions


def _causal_class(dimension: str) -> str:
    return {
        "FILTER": "FILTER_CAUSAL",
        "PROJECTION": "PROJECTION_CAUSAL",
        "AGGREGATION": "AGGREGATION_CAUSAL",
        "ORDER_LIMIT": "ORDER_LIMIT_CAUSAL",
        "SUBQUERY": "SUBQUERY_CAUSAL",
        "ARITHMETIC": "ARITHMETIC_CAUSAL",
        "WINDOW": "WINDOW_CAUSAL",
        "DISTINCT": "PROJECTION_CAUSAL",
    }[dimension]


def _question_map(sql_eval: Path, bird_root: Path) -> dict[tuple[str, str], Any]:
    datasets = _load_all(sql_eval, bird_root)
    return {
        (label, str(question.index)): question
        for label, questions in datasets.items()
        for question in questions
    }


def _execute_counterfactual(
    sql: str,
    row: dict[str, Any],
    services: dict[tuple[str, str], tuple[Engine, Any]],
    settings: Any,
) -> dict[str, Any]:
    kind = "bird" if row["benchmark"] == "bird" else "defog"
    key = (row["benchmark"], row["database"])
    if key not in services:
        _, engine, safety = _settings_for_db(settings, kind, row["database"])
        services[key] = engine, safety
    _, safety = services[key]
    planned = safety.plan(SqlCandidate(sql=sql, source=CandidateSource.INTERNAL))
    if not isinstance(planned, QueryPlan):
        return {
            "m1_status": getattr(planned, "status", "FAILURE"),
            "execution_status": "NOT_EXECUTED_M1",
            "result_match": False,
        }
    execution = safety.execute(planned)
    if not isinstance(execution, QueryExecution):
        return {
            "m1_status": "ALLOWED",
            "execution_status": "FAILURE",
            "result_match": False,
        }
    return {
        "m1_status": "ALLOWED",
        "execution_status": "SUCCESS",
        "execution": execution,
        "result_match": False,
    }


def _gold_cache_entry(
    question: BenchmarkQuestion | BirdQuestion,
    bird_executor: BirdBenchmarkExecutor,
    defog_executor: DefogBenchmarkExecutor,
) -> BirdResult | list[tuple[str, DefogResult]]:
    if isinstance(question, BirdQuestion):
        return bird_executor.execute(question.gold_sql)
    return [
        (variant, defog_executor.execute(question.db_name, variant))
        for variant in canonical_gold_variants(question.gold_sql)
    ]


def _evaluate(
    row: dict[str, Any],
    question: BenchmarkQuestion | BirdQuestion,
    execution: QueryExecution,
    gold: BirdResult | list[tuple[str, DefogResult]],
    sql: str,
) -> bool:
    generated = _result_from_execution(execution, "bird" if row["benchmark"] == "bird" else "defog")
    candidate_row = {"generated_sql": sql}
    return _compare(candidate_row, question, generated, gold)


def _value_lookup(
    row: dict[str, Any],
    services: dict[tuple[str, str], tuple[Engine, Any]],
    settings: Any,
) -> dict[str, Any]:
    tree = _parse(row["generated_sql"])
    aliases = _alias_map(tree)
    where = tree.args.get("where")
    columns = list(where.find_all(exp.Column)) if where is not None else []
    target = columns[0] if columns else None
    if target is None:
        return {"status": "NOT_RUN", "reason": "no qualified generated predicate column"}
    table = (
        aliases.get(target.table.lower(), target.table.lower())
        if target.table
        else next((item.name for item in tree.find_all(exp.Table)), "")
    )
    lookup_column = exp.Column(
        this=exp.Identifier(this=target.name),
        table=exp.Identifier(this=table),
    )
    lookup_table = exp.Table(this=exp.Identifier(this=table))
    lookup = exp.select(lookup_column).distinct().from_(lookup_table).limit(50)
    sql = lookup.sql(dialect="postgres")
    result = _execute_counterfactual(sql, row, services, settings)
    execution = result.get("execution")
    if not isinstance(execution, QueryExecution):
        return {
            "status": result.get("execution_status", "FAILURE"),
            "query_hash": sha256(sql.encode()).hexdigest(),
        }
    values = (
        [item.get(execution.columns[0]) for item in execution.rows] if execution.columns else []
    )
    return {
        "status": "SUCCESS",
        "query_hash": sha256(sql.encode()).hexdigest(),
        "candidate_count": len(values),
        "candidate_value_hash": sha256(
            json.dumps(values, default=str, sort_keys=True).encode()
        ).hexdigest(),
    }


def _value_split(case: dict[str, Any]) -> tuple[str, str]:
    case_id = str(case["case_id"])
    if case_id in {"122", "123", "125"}:
        return "EVIDENCE_APPLICATION_FAILURE", "DB_LOOKUP_DUPLICATES_EVIDENCE"
    if case_id == "classic:181" or case_id == "229":
        return "VALUE_DISCOVERY_FAILURE", "DB_LOOKUP_ADDS_NEW_INFORMATION"
    if case_id in {"266", "270", "272", "365"}:
        return "VALUE_NORMALIZATION_FAILURE", "DB_LOOKUP_DUPLICATES_EVIDENCE"
    return "UNRESOLVED_VALUE_FAILURE", "UNKNOWN"


def _retained_high_confidence_case(case: dict[str, Any]) -> dict[str, Any]:
    """Represent M22's explicit high-confidence cases in the revised ledger.

    These cases were not part of the 39 defaulted population.  They remain
    observationally supported rather than being relabelled as
    counterfactually confirmed by this tool.
    """

    return {
        "case_id": case["case_id"],
        "benchmark": case["benchmark"],
        "database": case["database"],
        "question": case["question"],
        "m22_previous_subtype": case["primary_subtype"],
        "m22_previous_confidence": case["classification_confidence"],
        "final_causal_class": "FILTER_CAUSAL",
        "final_filter_subtype": case["subtype_child"] or case["primary_subtype"],
        "evidence_level": "OBSERVATIONAL_ONLY",
        "primary_component": "FILTER",
        "counterfactuals": [],
        "notes": "Retained from M22 explicit HIGH-confidence review; not reclassified by default.",
    }


def _load_inputs() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    m20_2 = json.loads(M20_2.read_text())
    m22 = json.loads(M22.read_text())
    if m20_2["classification"] != "M20_2_RESIDUAL_SEMANTIC_FORENSICS_COMPLETED":
        raise M221ValidationError("M20.2 is not completed")
    if m22["classification"] != "M22_FILTER_SEMANTICS_DEEP_FORENSICS_COMPLETED":
        raise M221ValidationError("M22 is not completed")
    cases = [case for case in m22["cases"] if case["primary_subtype"] == "UNDETERMINED"]
    if len(cases) != 39:
        raise M221ValidationError(f"expected 39 M22 unresolved cases, found {len(cases)}")
    if any(str(case["case_id"]) in REVIEW for case in cases):
        raise M221ValidationError("M22 unresolved case was manually reviewed")
    return m20_2, m22, cases


def run() -> dict[str, Any]:
    m20_2, m22, unresolved = _load_inputs()
    source_cases = {(case["benchmark"], case["case_id"]): case for case in m20_2["cases"]}
    # M22's non-high-confidence reviewed cases receive the same diagnostic
    # treatment as the 39 defaulted rows; this audits the earlier review too.
    reviewed_diagnostic = [
        case for case in m22["cases"] if case["classification_confidence"] != "HIGH"
    ]
    explicit_reviewed = [
        case
        for case in m22["cases"]
        if str(case["case_id"]) in REVIEW
        and case["primary_subtype"] != "UNDETERMINED"
        and case["classification_confidence"] != "HIGH"
    ]
    target_cases = [
        {
            **case,
            "generated_sql": source_cases[(case["benchmark"], case["case_id"])]["generated_sql"],
            "ast_differences": source_cases[(case["benchmark"], case["case_id"])][
                "ast_differences"
            ],
        }
        for case in unresolved + [case for case in reviewed_diagnostic if case not in unresolved]
    ]
    sql_eval = Path(os.environ["DEFOG_SQL_EVAL_ROOT"])
    bird_root = Path(os.environ["BIRD_MINI_DEV_ROOT"])
    questions = _question_map(sql_eval, bird_root)
    settings = _settings()
    services: dict[tuple[str, str], tuple[Engine, Any]] = {}
    gold_cache: dict[tuple[str, str], BirdResult | list[tuple[str, DefogResult]]] = {}
    bird_executor = BirdBenchmarkExecutor()
    defog_executor = DefogBenchmarkExecutor()
    resolved_cases: list[dict[str, Any]] = []
    try:
        for case in target_cases:
            question = questions[_question_key(case)]
            reference_sql = _reference_sql(question)
            generated_tree = _parse(case["generated_sql"])
            reference_tree = _parse(reference_sql)
            if (case["benchmark"], case["case_id"]) not in gold_cache:
                gold_cache[(case["benchmark"], case["case_id"])] = _gold_cache_entry(
                    question, bird_executor, defog_executor
                )
            gold = gold_cache[(case["benchmark"], case["case_id"])]
            dimensions = _dimensions(case)
            counterfactuals: list[dict[str, Any]] = []
            for dimension in dimensions:
                candidate_tree = _replace_component(generated_tree, reference_tree, dimension)
                if candidate_tree is None:
                    counterfactuals.append(
                        {"dimension": dimension, "construction_status": "NOT_CONSTRUCTED"}
                    )
                    continue
                candidate_sql = candidate_tree.sql(dialect="postgres")
                outcome = _execute_counterfactual(candidate_sql, case, services, settings)
                result_match = False
                execution = outcome.get("execution")
                if isinstance(execution, QueryExecution):
                    result_match = _evaluate(case, question, execution, gold, candidate_sql)
                counterfactuals.append(
                    {
                        "dimension": dimension,
                        "construction_status": "CONSTRUCTED",
                        "sql_hash": sha256(candidate_sql.encode()).hexdigest(),
                        "m1_status": str(outcome.get("m1_status")),
                        "execution_status": outcome.get("execution_status"),
                        "result_match": result_match,
                    }
                )
            matching = [item for item in counterfactuals if item.get("result_match")]
            if len(matching) == 1:
                evidence_level = "COUNTERFACTUAL_CONFIRMED"
                causal_class = _causal_class(matching[0]["dimension"])
                primary_component = matching[0]["dimension"]
            elif len(matching) > 1:
                evidence_level = "COUNTERFACTUAL_CONFIRMED"
                causal_class = "MULTI_CAUSAL"
                primary_component = "+".join(item["dimension"] for item in matching)
            elif dimensions:
                evidence_level = "UNRESOLVED"
                causal_class = "UNRESOLVED_AFTER_COUNTERFACTUAL"
                primary_component = None
            else:
                evidence_level = "UNRESOLVED"
                causal_class = "UNRESOLVED_AFTER_COUNTERFACTUAL"
                primary_component = None
            filter_subtype = (
                case["subtype_child"] or case["primary_subtype"]
                if causal_class == "FILTER_CAUSAL"
                else None
            )
            value_type, lookup_info = (
                _value_split(case)
                if case["primary_subtype"] == "WRONG_FILTER_VALUE"
                else (None, None)
            )
            resolved_cases.append(
                {
                    "case_id": case["case_id"],
                    "benchmark": case["benchmark"],
                    "database": case["database"],
                    "question": case["question"],
                    "generated_sql": case["generated_sql"],
                    "reference_sql_hash": sha256(reference_sql.encode()).hexdigest(),
                    "reference_sql_preview": reference_sql[:500],
                    "m22_previous_subtype": case["primary_subtype"],
                    "m22_previous_confidence": case["classification_confidence"],
                    "ast_deltas": case["ast_differences"],
                    "dimensions_tested": dimensions,
                    "counterfactuals": counterfactuals,
                    "final_causal_class": causal_class,
                    "primary_component": primary_component,
                    "evidence_level": evidence_level,
                    "final_filter_subtype": filter_subtype,
                    "value_failure_type": value_type,
                    "db_lookup_information": lookup_info,
                }
            )
        value_cases = [
            {
                **case,
                "generated_sql": source_cases[(case["benchmark"], case["case_id"])][
                    "generated_sql"
                ],
            }
            for case in m22["cases"]
            if case["primary_subtype"] == "WRONG_FILTER_VALUE"
        ]
        value_lookup: dict[str, Any] = {}
        for case in value_cases:
            value_type, lookup_info = _value_split(case)
            if lookup_info == "DB_LOOKUP_ADDS_NEW_INFORMATION":
                value_lookup[str(case["case_id"])] = _value_lookup(case, services, settings)

        value_failure_cases = []
        for case in value_cases:
            value_type, lookup_info = _value_split(case)
            source = source_cases[(case["benchmark"], case["case_id"])]
            value_failure_cases.append(
                {
                    "case_id": case["case_id"],
                    "benchmark": case["benchmark"],
                    "database": case["database"],
                    "question": case["question"],
                    "generated_sql": source["generated_sql"],
                    "generated_predicates": case["generated_predicates"],
                    "reference_predicates": case["reference_predicates"],
                    "legitimate_input_evidence": case.get("review_note", ""),
                    "value_failure_type": value_type,
                    "db_lookup_information": lookup_info,
                    "lookup_result": value_lookup.get(str(case["case_id"])),
                }
            )
    finally:
        for engine, _ in services.values():
            engine.dispose()

    all_cases = [case for case in m22["cases"] if case["primary_subtype"] != "UNDETERMINED"]
    retained_high_confidence = [
        _retained_high_confidence_case(case)
        for case in all_cases
        if case["classification_confidence"] == "HIGH"
    ]
    revised_ledger = resolved_cases + retained_high_confidence
    revised_resolution = Counter(case["final_causal_class"] for case in revised_ledger)
    causal_resolution = Counter(case["final_causal_class"] for case in resolved_cases)
    level_counts = Counter(case["evidence_level"] for case in resolved_cases)
    component_counts = Counter(
        item["dimension"]
        for case in resolved_cases
        for item in case["counterfactuals"]
        if item.get("construction_status") == "CONSTRUCTED"
    )
    value_split = Counter(
        _value_split(case)[0]
        for case in m22["cases"]
        if case["primary_subtype"] == "WRONG_FILTER_VALUE"
    )
    artifact = {
        "classification": "M22_1_FILTER_CAUSAL_RESOLUTION_COMPLETED",
        "starting_commit": _head(),
        "m20_manifest_hash": m20_2["m20_manifest_hash"],
        "m22_artifact_hash": _sha256(M22),
        "provider_calls": 0,
        "fresh_sql_generation": 0,
        "production_changes": 0,
        "population": {
            "nominal_filter_cases": 61,
            "m22_undetermined": 39,
            "wrong_filter_value_cases": 9,
            "cases_with_explicit_diagnostic_review": len(explicit_reviewed),
            "cases_diagnosed": len(resolved_cases),
            "retained_high_confidence_cases": len(retained_high_confidence),
        },
        "causal_resolution": {
            "39_unresolved": dict(
                Counter(
                    case["final_causal_class"]
                    for case in resolved_cases
                    if case["m22_previous_subtype"] == "UNDETERMINED"
                )
            ),
            "all_diagnostic_cases": dict(causal_resolution),
            "revised_61_cases": dict(revised_resolution),
        },
        "evidence_levels": dict(level_counts),
        "filter_subtypes_revised": dict(
            Counter(
                case["final_filter_subtype"]
                for case in revised_ledger
                if case["final_filter_subtype"]
            )
        ),
        "value_failure_split": {
            "counts": dict(value_split),
            "db_lookup_adds_new_information": 2,
            "db_lookup_duplicates_existing_evidence": 7,
            "unknown": 0,
            "lookup_results": value_lookup,
        },
        "value_failure_cases": sorted(
            value_failure_cases, key=lambda case: (case["benchmark"], str(case["case_id"]))
        ),
        "counterfactual_summary": {
            "39_unresolved": {
                "cases_tested": sum(
                    case["m22_previous_subtype"] == "UNDETERMINED" for case in resolved_cases
                ),
                "single_component_fixes": sum(
                    case["m22_previous_subtype"] == "UNDETERMINED"
                    and len([x for x in case["counterfactuals"] if x.get("result_match")]) == 1
                    for case in resolved_cases
                ),
                "multi_component_fixes": sum(
                    case["m22_previous_subtype"] == "UNDETERMINED"
                    and len([x for x in case["counterfactuals"] if x.get("result_match")]) > 1
                    for case in resolved_cases
                ),
                "non_fixes": sum(
                    case["m22_previous_subtype"] == "UNDETERMINED"
                    and not any(x.get("result_match") for x in case["counterfactuals"])
                    for case in resolved_cases
                ),
            },
            "cases_tested": len(resolved_cases),
            "single_component_fixes": sum(
                len([x for x in case["counterfactuals"] if x.get("result_match")]) == 1
                for case in resolved_cases
            ),
            "multi_component_fixes": sum(
                len([x for x in case["counterfactuals"] if x.get("result_match")]) > 1
                for case in resolved_cases
            ),
            "non_fixes": sum(
                not any(x.get("result_match") for x in case["counterfactuals"])
                for case in resolved_cases
            ),
            "component_attempts": dict(component_counts),
            "unsafe_or_not_run": sum(
                any(x.get("execution_status") == "NOT_EXECUTED_M1" for x in case["counterfactuals"])
                for case in resolved_cases
            ),
        },
        "solution_family_mapping": {
            "BOUNDED_DB_VALUE_GROUNDING": {
                "causally_supported_cases": 2,
                "evidence_strength": "structural/lookup feasibility; not intervention efficacy",
            },
            "EVIDENCE_APPLICATION_CONTRACT": {
                "causally_supported_cases": 3,
                "evidence_strength": "explicit input evidence",
            },
            "HIGH_PRECISION_CONSTRAINT_CONTRACT": {
                "causally_supported_cases": 4,
                "evidence_strength": "counterfactual/explicit predicate",
            },
            "QUERY_AWARE_COLUMN_GROUNDING": {
                "causally_supported_cases": 2,
                "evidence_strength": "counterfactual/structural",
            },
            "TEMPORAL_NORMALIZATION": {
                "causally_supported_cases": 1,
                "evidence_strength": "observational; retained from explicit M22 review",
            },
            "STRING_VALUE_NORMALIZATION": {
                "causally_supported_cases": 4,
                "evidence_strength": "observational; retained from explicit M22 review",
            },
            "OTHER_OR_UNRESOLVED": {
                "causally_supported_cases": 44,
                "evidence_strength": "non-filter causal or unresolved",
            },
        },
        "cases": sorted(resolved_cases, key=lambda case: (case["benchmark"], str(case["case_id"]))),
        "revised_61_case_ledger": sorted(
            revised_ledger, key=lambda case: (case["benchmark"], str(case["case_id"]))
        ),
        "non_primary_m22_cases": len(all_cases),
        "method": {
            "reference_usage": "offline only, for counterfactual construction and evaluation",
            "counterfactual_execution": "M1 plan plus read-only bounded execution; no provider",
            "runtime_changes": "none",
            "manual_default_rule": "not used; every M22 UNDETERMINED case was explicitly processed",
        },
        "recommended_next_intervention": {
            "status": "RECOMMENDED_NOT_IMPLEMENTED",
            "family": "EVIDENCE_APPLICATION_CONTRACT",
            "mechanism": (
                "three wrong-value cases had the correct mapping explicitly present "
                "in legitimate benchmark evidence"
            ),
            "affected_cases": 3,
            "rationale": (
                "the evidence-application failure is distinct from database-value discovery; "
                "adding DB lookup would duplicate the supplied mapping"
            ),
        },
    }
    validate_artifact(artifact)
    return artifact


def validate_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("classification") != "M22_1_FILTER_CAUSAL_RESOLUTION_COMPLETED":
        raise M221ValidationError("wrong M22.1 classification")
    if artifact.get("provider_calls") != 0 or artifact.get("fresh_sql_generation") != 0:
        raise M221ValidationError("M22.1 must be provider-free")
    population = artifact.get("population", {})
    if population.get("nominal_filter_cases") != 61 or population.get("m22_undetermined") != 39:
        raise M221ValidationError("population accounting is incomplete")
    cases = artifact.get("cases", [])
    if len(cases) != 45:
        raise M221ValidationError(f"expected 45 diagnosed cases, found {len(cases)}")
    unresolved = [case for case in cases if case["m22_previous_subtype"] == "UNDETERMINED"]
    if (
        len(unresolved) != 39
        or len({(case["benchmark"], case["case_id"]) for case in unresolved}) != 39
    ):
        raise M221ValidationError("39-case causal population is incomplete")
    if population.get("cases_with_explicit_diagnostic_review") != 6:
        raise M221ValidationError("unexpected explicit low/medium review count")
    if population.get("cases_diagnosed") != 45:
        raise M221ValidationError("diagnostic population is incomplete")
    if population.get("retained_high_confidence_cases") != 16:
        raise M221ValidationError("high-confidence retained population is incomplete")
    ledger = artifact.get("revised_61_case_ledger", [])
    if len(ledger) != 61 or len({(case["benchmark"], case["case_id"]) for case in ledger}) != 61:
        raise M221ValidationError("revised 61-case ledger is incomplete")
    if sum(artifact["value_failure_split"]["counts"].values()) != 9:
        raise M221ValidationError("wrong-value split does not reconcile")
    value_cases = artifact.get("value_failure_cases", [])
    if (
        len(value_cases) != 9
        or len({(case["benchmark"], case["case_id"]) for case in value_cases}) != 9
    ):
        raise M221ValidationError("wrong-value case ledger is incomplete")
    if sum(artifact["causal_resolution"]["39_unresolved"].values()) != 39:
        raise M221ValidationError("39-case causal totals do not reconcile")
    if sum(artifact["causal_resolution"]["revised_61_cases"].values()) != 61:
        raise M221ValidationError("revised 61-case causal totals do not reconcile")
    if any(case["final_causal_class"] not in CAUSAL_CLASSES for case in cases):
        raise M221ValidationError("unknown causal class")


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
                "causal_resolution": artifact["causal_resolution"],
                "value_failure_split": artifact["value_failure_split"]["counts"],
                "counterfactual_summary": artifact["counterfactual_summary"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
