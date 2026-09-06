# ruff: noqa: E501
"""Provider-free decomposition of the frozen M20.2 filter residuals.

This module is deliberately an offline forensic tool.  Its labels are not
runtime signals and it never regenerates SQL or calls a provider.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from hashlib import sha256
from itertools import groupby
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from evaluation.m20_2_residual_semantic_forensics import (
    _ast_facts,
    _temporal_question,
)
from evaluation.m20_2_residual_semantic_forensics import (
    validate_artifact as validate_m20_2_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
M20_2_ARTIFACT = ROOT / "evaluation/fixtures/m20_2_residual_semantic_forensics.json"
OUTPUT = ROOT / "evaluation/fixtures/m22_filter_semantics_deep_forensics.json"

FILTER_SUBTYPES = (
    "MISSING_PREDICATE",
    "EXTRA_PREDICATE",
    "WRONG_FILTER_COLUMN",
    "WRONG_FILTER_VALUE",
    "WRONG_OPERATOR",
    "BOOLEAN_COMPOSITION",
    "NULL_OR_EXISTENCE",
    "TEMPORAL_FILTER",
    "STRING_MATCHING",
    "HAVING_VS_WHERE",
    "MULTI_CAUSAL",
    "UNDETERMINED",
)

VALUE_CHILDREN = {
    "ENUM_OR_CODE_MAPPING",
    "STRING_VARIANT",
    "VALUE_FORMAT_MISMATCH",
    "NUMERIC_VALUE",
    "OTHER_VALUE_GROUNDING",
}

REVIEW: dict[str, dict[str, Any]] = {
    "115": {
        "primary": "BOOLEAN_COMPOSITION",
        "child": "AND_OR",
        "confidence": "HIGH",
        "tags": ["OR_PRECEDENCE", "PARENTHESIS_SCOPE"],
        "note": "The reference places the abnormal-fibrinogen OR outside the normal-WBC conjunction; the generated query explicitly groups it.",
    },
    "122": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "ENUM_OR_CODE_MAPPING",
        "confidence": "HIGH",
        "tags": ["DATABASE_CODE_MAPPING"],
        "note": "The supplied evidence explicitly maps '-' and '+-' to the database values 'negative' and '0'.",
    },
    "123": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "ENUM_OR_CODE_MAPPING",
        "confidence": "HIGH",
        "tags": ["DATABASE_CODE_MAPPING"],
        "note": "The supplied evidence explicitly maps SM '-' and '+-' to 'negative' and '0'.",
    },
    "125": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "ENUM_OR_CODE_MAPPING",
        "confidence": "HIGH",
        "tags": ["DATABASE_CODE_MAPPING"],
        "note": "The supplied evidence explicitly maps both laboratory codes to their stored values.",
    },
    "150": {
        "primary": "EXTRA_PREDICATE",
        "confidence": "LOW",
        "tags": ["NULL_PREDICATE", "OTHER_AST_DIVERGENCE"],
        "note": "The extra non-null predicate is visible, but the frozen result also has a row-bound/projection difference; filter causality is not isolated.",
    },
    "classic:181": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "ENUM_OR_CODE_MAPPING",
        "confidence": "HIGH",
        "tags": ["STATE_CODE_MAPPING"],
        "note": "The generated natural-language state is 'California' while the reference/database representation is 'CA'.",
    },
    "187": {
        "primary": "NULL_OR_EXISTENCE",
        "child": "IS_NOT_NULL",
        "confidence": "MEDIUM",
        "tags": ["MISSING_NULL_GUARD"],
        "note": "The generated query lacks the reference's explicit non-null DOB condition; the ordering/projection differences prevent a stronger isolated causal claim.",
    },
    "202": {
        "primary": "HAVING_VS_WHERE",
        "confidence": "MEDIUM",
        "tags": ["POST_AGGREGATION_FILTER"],
        "note": "The reference uses a wins >= 1 row predicate while the generated query uses a grouped HAVING expression; the request also contains a broader aggregate mismatch.",
    },
    "228": {
        "primary": "TEMPORAL_FILTER",
        "child": "WRONG_DATE_VALUE",
        "confidence": "HIGH",
        "tags": ["DURATION_UNIT_NORMALIZATION"],
        "note": "The question supplies a clock duration; generated SQL compares milliseconds while the reference derives seconds from the interval-valued time.",
    },
    "229": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "STRING_VARIANT",
        "confidence": "HIGH",
        "tags": ["ENUM_OR_LABEL_VARIANT"],
        "note": "The generated value is America, while the reference/database label is American.",
    },
    "259": {
        "primary": "MISSING_PREDICATE",
        "confidence": "HIGH",
        "tags": ["EXPLICIT_QUESTION_CONSTRAINT"],
        "note": "The question's moral-alignment condition is explicit in the supplied evidence, but the generated WHERE has no alignment predicate.",
    },
    "264": {
        "primary": "EXTRA_PREDICATE",
        "confidence": "LOW",
        "tags": ["NULL_PREDICATE", "OTHER_AST_DIVERGENCE"],
        "note": "The generated non-null guard is not present in the reference; the projection/result-shape divergence means the filter is not proven causal.",
    },
    "266": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "STRING_VARIANT",
        "confidence": "HIGH",
        "tags": ["CASE_SENSITIVE_LABEL"],
        "note": "The generated literal is lower-case human while the reference/database label is Human.",
    },
    "270": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "STRING_VARIANT",
        "confidence": "HIGH",
        "tags": ["CASE_SENSITIVE_LABEL"],
        "note": "The generated literal is lower-case stealth while the reference/database label is Stealth.",
    },
    "272": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "STRING_VARIANT",
        "confidence": "HIGH",
        "tags": ["CASE_SENSITIVE_LABEL"],
        "note": "The generated literal is lower-case durability while the reference/database label is Durability.",
    },
    "344": {
        "primary": "WRONG_FILTER_COLUMN",
        "confidence": "HIGH",
        "tags": ["COLUMN_AMBIGUITY"],
        "note": "The question asks for comments with zero score; generated SQL filters comments.score while the reference filters posts.score.",
    },
    "349": {
        "primary": "WRONG_FILTER_COLUMN",
        "confidence": "HIGH",
        "tags": ["COLUMN_AMBIGUITY"],
        "note": "The supplied evidence names card type, but generated SQL uses cards.types while the reference uses cards.type.",
    },
    "365": {
        "primary": "WRONG_FILTER_VALUE",
        "child": "STRING_VARIANT",
        "confidence": "HIGH",
        "tags": ["LOCALE_LABEL_VARIANT"],
        "note": "The generated Portuguese label is Portuguese (Brasil), while the reference/database label is Portuguese (Brazil).",
    },
    "418": {
        "primary": "BOOLEAN_COMPOSITION",
        "child": "PARENTHESIS_SCOPE",
        "confidence": "MEDIUM",
        "tags": ["OR_PRECEDENCE", "IDENTIFIER_CONSTRUCTION"],
        "note": "The generated query explicitly groups the two atom endpoints for TR009_12; the reference uses different ungrouped concatenation predicates.",
    },
    "420": {
        "primary": "STRING_MATCHING",
        "child": "NORMALIZATION",
        "confidence": "HIGH",
        "tags": ["IDENTIFIER_SUFFIX_MATCH"],
        "note": "The evidence describes atom 19 as a suffix of a compound identifier; generated SQL tests equality while the reference normalizes the suffix.",
    },
    "443": {
        "primary": "MULTI_CAUSAL",
        "confidence": "MEDIUM",
        "tags": ["NULL_GUARDS", "ARITHMETIC_AND_RATIO"],
        "note": "Extra null/zero guards and the uncast ratio expression differ together; the frozen result cannot isolate a single filter-only cause.",
    },
    "classic:49": {
        "primary": "MISSING_PREDICATE",
        "confidence": "HIGH",
        "tags": ["EXPLICIT_QUESTION_CONSTRAINT"],
        "note": "The question says students have declared a major; the reference requires student.declare_major IS NOT NULL and generated SQL omits it.",
    },
}


class M22ValidationError(RuntimeError):
    """Raised when the frozen filter population cannot be reconciled."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _parse(sql: str) -> exp.Expression | None:
    try:
        return parse_one(sql, read="postgres")
    except Exception:
        return None


def _aliases(tree: exp.Expression) -> dict[str, str]:
    return {table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)}


def _filter_tree(node: exp.Expression | None) -> Any:
    if node is None:
        return None
    if isinstance(node, exp.Where):
        return _filter_tree(node.this)
    if isinstance(node, (exp.And, exp.Or)):
        return (type(node).__name__.upper(), _filter_tree(node.left), _filter_tree(node.right))
    if isinstance(node, exp.Paren):
        return ("PAREN", _filter_tree(node.this))
    if isinstance(node, exp.Not):
        return ("NOT", _filter_tree(node.this))
    return type(node).__name__.upper()


def _atom(node: exp.Expression, aliases: dict[str, str]) -> dict[str, Any]:
    columns = sorted(
        (aliases.get(column.table.lower(), column.table.lower()), column.name.lower())
        for column in node.find_all(exp.Column)
    )
    literals = [
        {"is_string": literal.is_string, "value": literal.this}
        for literal in node.find_all(exp.Literal)
    ]
    normalized = re.sub(r"\s+", " ", node.sql(dialect="postgres", normalize=True)).lower()
    return {
        "operator": type(node).__name__.upper(),
        "columns": columns,
        "literals": literals,
        "normalized": normalized,
    }


def _atoms(node: exp.Expression | None, aliases: dict[str, str]) -> list[dict[str, Any]]:
    if node is None:
        return []
    if isinstance(node, exp.Where):
        return _atoms(node.this, aliases)
    if isinstance(node, (exp.And, exp.Or)):
        return _atoms(node.left, aliases) + _atoms(node.right, aliases)
    if isinstance(node, exp.Paren):
        return _atoms(node.this, aliases)
    if isinstance(node, exp.Not):
        return [
            {
                "operator": "NOT",
                "columns": [],
                "literals": [],
                "normalized": node.sql(dialect="postgres", normalize=True).lower(),
            }
        ]
    return [_atom(node, aliases)]


def _predicate_facts(sql: str) -> dict[str, Any]:
    tree = _parse(sql)
    if tree is None:
        return {"parseable": False, "tree": None, "atoms": []}
    where = tree.args.get("where")
    aliases = _aliases(tree)
    return {
        "parseable": True,
        "tree": _filter_tree(where),
        "atoms": _atoms(where, aliases),
    }


def _question_constraints(question: str, evidence: str) -> dict[str, Any]:
    text = f"{question} {evidence}".lower()
    patterns = {
        "temporal": bool(
            re.search(r"\b(after|before|during|in \d{4}|less than 02:00|year|month|date)\b", text)
        ),
        "inequality": bool(
            re.search(r"\b(more than|less than|at least|at most|over|under|between)\b", text)
        ),
        "null_or_existence": bool(
            re.search(r"\b(not have|without|unknown|missing|declared|valid|empty|not null)\b", text)
        ),
        "named_value": bool(
            re.search(
                r"\b(whose|named|called|from|located|nationality|language|type|format|status)\b",
                text,
            )
        ),
        "boolean_or": bool(re.search(r"\b(or|either|any)\b", text)),
    }
    high_precision = [name for name, present in patterns.items() if present]
    return {
        "high_precision": bool(high_precision),
        "signals": high_precision,
        "confidence": "HIGH" if high_precision else "AMBIGUOUS",
    }


def _value_analysis(case: dict[str, Any]) -> dict[str, Any]:
    primary = case["primary_subtype"]
    if primary != "WRONG_FILTER_VALUE":
        return {"applicable": False}
    child = case.get("subtype_child")
    if child == "ENUM_OR_CODE_MAPPING":
        mechanism = "ENUM_OR_CODE_DICTIONARY"
    elif "CASE_SENSITIVE" in " ".join(case.get("secondary_tags", [])):
        mechanism = "CASE_INSENSITIVE_MATCH"
    elif child == "STRING_VARIANT":
        mechanism = "FUZZY_TEXT_MATCH"
    else:
        mechanism = "SEMANTIC_VALUE_RETRIEVAL_REQUIRED"
    return {
        "applicable": True,
        "db_value_lookup_feasibility": "DB_VALUE_LOOKUP_PLAUSIBLE",
        "evidence_basis": "M20.2 supplied evidence plus generated/reference literal comparison; no runtime lookup was required for this frozen diagnosis.",
        "retrieval_mechanism": mechanism,
    }


def _constraint_analysis(case: dict[str, Any]) -> dict[str, Any]:
    primary = case["primary_subtype"]
    if primary in {
        "MISSING_PREDICATE",
        "EXTRA_PREDICATE",
        "WRONG_OPERATOR",
        "BOOLEAN_COMPOSITION",
        "NULL_OR_EXISTENCE",
        "HAVING_VS_WHERE",
    }:
        return {
            "classification": "HIGH_PRECISION_CONSTRAINT_PLAUSIBLE"
            if case["question_constraints"]["high_precision"]
            else "CONSTRAINT_REQUIRES_GROUNDING",
            "signals": case["question_constraints"]["signals"],
        }
    if primary == "WRONG_FILTER_COLUMN":
        return {"classification": "CONSTRAINT_REQUIRES_GROUNDING", "signals": ["column identity"]}
    if primary == "TEMPORAL_FILTER":
        return {
            "classification": "CONSTRAINT_REQUIRES_GROUNDING",
            "signals": ["temporal unit/boundary"],
        }
    return {"classification": "NOT_APPLICABLE", "signals": []}


def _load() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    artifact = json.loads(M20_2_ARTIFACT.read_text())
    validate_m20_2_artifact(artifact)
    rows = [row for row in artifact["cases"] if row["primary_root_cause"] == "FILTER_SEMANTICS"]
    if len(rows) != 61 or len({(row["benchmark"], row["case_id"]) for row in rows}) != 61:
        raise M22ValidationError("FILTER_SEMANTICS population is not exactly 61")
    return artifact, rows


def _classify(row: dict[str, Any]) -> dict[str, Any]:
    review = REVIEW.get(str(row["case_id"]), {})
    primary = review.get("primary", "UNDETERMINED")
    child = review.get("child")
    if primary not in FILTER_SUBTYPES:
        raise M22ValidationError(f"unsupported subtype for {row['case_id']}: {primary}")
    generated = _predicate_facts(row["generated_sql"])
    reference = _predicate_facts(row["reference_sql"])
    constraints = _question_constraints(row["question"], row.get("evidence", ""))
    result = {
        "case_id": row["case_id"],
        "benchmark": row["benchmark"],
        "database": row["database"],
        "question": row["question"],
        "primary_subtype": primary,
        "subtype_child": child,
        "secondary_tags": review.get("tags", []),
        "classification_confidence": review.get("confidence", "LOW"),
        "review_note": review.get(
            "note",
            "The frozen M20.2 filter difference is incidental, alias-only, or coupled to another semantic AST difference; a filter-specific causal mechanism is not isolated.",
        ),
        "generated_predicates": generated,
        "reference_predicates": reference,
        "question_constraints": constraints,
        "constraint_contract_feasibility": None,
        "value_grounding": None,
        "operator_analysis": {
            "applicable": primary == "WRONG_OPERATOR",
            "classification": "DETERMINISTIC_OPERATOR_MAPPING"
            if primary == "WRONG_OPERATOR"
            else "NOT_APPLICABLE",
        },
        "temporal_analysis": {
            "applicable": primary == "TEMPORAL_FILTER" or _temporal_question(row["question"]),
            "question_temporal": _temporal_question(row["question"]),
            "classification": child or "NOT_APPLICABLE",
        },
        "counterfactual": {
            "status": "NOT_RUN",
            "reason": "No counterfactual SQL was required to assign the reviewed subtype; provider-free frozen evidence was sufficient, and no diagnostic database call was made.",
        },
        "recommended_solution_family": {
            "WRONG_FILTER_VALUE": "BOUNDED_DB_VALUE_GROUNDING",
            "WRONG_FILTER_COLUMN": "QUERY_AWARE_COLUMN_GROUNDING",
            "MISSING_PREDICATE": "HIGH_PRECISION_CONSTRAINT_CONTRACT",
            "EXTRA_PREDICATE": "HIGH_PRECISION_CONSTRAINT_CONTRACT",
            "WRONG_OPERATOR": "HIGH_PRECISION_CONSTRAINT_CONTRACT",
            "BOOLEAN_COMPOSITION": "BOOLEAN_CONSTRAINT_TREE",
            "NULL_OR_EXISTENCE": "HIGH_PRECISION_CONSTRAINT_CONTRACT",
            "TEMPORAL_FILTER": "TEMPORAL_NORMALIZATION",
            "STRING_MATCHING": "STRING_VALUE_NORMALIZATION",
            "HAVING_VS_WHERE": "AGGREGATE_FILTER_CONTRACT",
            "MULTI_CAUSAL": "MODEL_REASONING_RESIDUAL",
            "UNDETERMINED": "UNKNOWN",
        }[primary],
        "existing_capability_relevance": row.get("existing_capability_relevance", {}),
    }
    result["constraint_contract_feasibility"] = _constraint_analysis(result)
    result["value_grounding"] = _value_analysis(result)
    return result


def _shape_control() -> dict[str, dict[str, int | float]]:
    # M20 preserves SQL for 475 accepted candidates.  The 35 percent-transport
    # replays are intentionally not duplicated here because that artifact does
    # not preserve their SQL; no shape is guessed for those rows.
    m20_rows = [
        json.loads(line)
        for line in (ROOT / "evaluation/fixtures/m20_full_benchmark_cases.jsonl")
        .read_text()
        .splitlines()
        if line.strip()
    ]
    executed = [row for row in m20_rows if row["m1_status"] == "ALLOWED"]
    groups = {
        "0_joins": lambda facts: facts.get("join_count", 0) == 0,
        "1_join": lambda facts: facts.get("join_count", 0) == 1,
        "2_plus_joins": lambda facts: facts.get("join_count", 0) >= 2,
        "aggregation": lambda facts: bool(facts.get("aggregation")),
        "filter_present": lambda facts: bool(facts.get("where")),
        "or_boolean": lambda facts: " or " in str(facts.get("where", "")),
        "null_predicate": lambda facts: "is null" in str(facts.get("where", "")),
        "inequality_predicate": lambda facts: any(
            operator in str(facts.get("where", ""))
            for operator in (" > ", " < ", " >= ", " <= ", " <> ", " != ")
        ),
        "in_predicate": lambda facts: " in " in str(facts.get("where", "")),
    }
    result: dict[str, dict[str, int | float]] = {}
    for name, predicate in groups.items():
        selected = [
            row
            for row in executed
            if predicate(_ast_facts(row["generated_sql"]))  # type: ignore[no-untyped-call]
        ]
        correct = sum(bool(row["correct"]) for row in selected)
        wrong = len(selected) - correct
        result[name] = {
            "executed": len(selected),
            "correct": correct,
            "wrong": wrong,
            "error_rate": round(wrong / len(selected), 6) if selected else 0.0,
        }
    return result


def run() -> dict[str, Any]:
    source, rows = _load()
    cases = [_classify(row) for row in rows]
    counts = Counter(case["primary_subtype"] for case in cases)
    if sum(counts.values()) != 61:
        raise M22ValidationError("subtype accounting does not reconcile")
    if set(counts) - set(FILTER_SUBTYPES):
        raise M22ValidationError("unknown subtype present")
    benchmark_breakdown: dict[str, dict[str, int]] = {}
    for benchmark in ("bird", "defog_classic", "defog_advanced"):
        benchmark_breakdown[benchmark] = dict(
            Counter(case["primary_subtype"] for case in cases if case["benchmark"] == benchmark)
        )
    value_cases = [case for case in cases if case["primary_subtype"] == "WRONG_FILTER_VALUE"]
    value_mechanisms = Counter(
        case["value_grounding"].get("retrieval_mechanism") for case in value_cases
    )
    constraint_cases = [
        case
        for case in cases
        if case["constraint_contract_feasibility"]["classification"] != "NOT_APPLICABLE"
    ]
    constraint_classes = Counter(
        case["constraint_contract_feasibility"]["classification"] for case in constraint_cases
    )
    solution_families: dict[str, dict[str, Any]] = {}
    sorted_cases = sorted(cases, key=lambda case: case["recommended_solution_family"])
    for family, family_cases in groupby(
        sorted_cases, key=lambda case: case["recommended_solution_family"]
    ):
        grouped = list(family_cases)
        solution_families[family] = {
            "affected_cases": len(grouped),
            "causal_confidence": dict(
                Counter(case["classification_confidence"] for case in grouped)
            ),
            "runtime_cost": "not implemented; future experiment must measure",
            "regression_surface": "filter values/constraints and generated SQL semantics",
        }
    artifact = {
        "classification": "M22_FILTER_SEMANTICS_DEEP_FORENSICS_COMPLETED",
        "requested_starting_commit": "50ca58513dcf683e1eaa3056244450ada69a307f",
        "source_m20_2_analysis_commit": source["analysis_commit"],
        "analysis_commit": _head(),
        "m20_manifest_hash": source["m20_manifest_hash"],
        "m20_2_artifact_hash": _sha256(M20_2_ARTIFACT),
        "provider_calls": 0,
        "fresh_sql_generation": 0,
        "production_changes": 0,
        "population": 61,
        "primary_subtypes": {
            subtype: {
                "count": counts.get(subtype, 0),
                "percentage_of_61": round(counts.get(subtype, 0) / 61 * 100, 3),
            }
            for subtype in FILTER_SUBTYPES
        },
        "benchmark_breakdown": benchmark_breakdown,
        "value_grounding_analysis": {
            "wrong_value_cases": len(value_cases),
            "db_value_lookup_strongly_plausible": len(value_cases),
            "partially_plausible": 0,
            "not_sufficient": 0,
            "retrieval_mechanisms": dict(value_mechanisms),
            "live_bounded_lookup": "NOT_RUN; frozen evidence was sufficient and no runtime component was changed",
        },
        "constraint_analysis": {
            "applicable_cases": len(constraint_cases),
            "classes": dict(constraint_classes),
            "high_precision_explicit_constraints": sum(
                case["question_constraints"]["confidence"] == "HIGH" for case in constraint_cases
            ),
            "ambiguous": sum(
                case["question_constraints"]["confidence"] == "AMBIGUOUS"
                for case in constraint_cases
            ),
            "operator_and_predicate_types": dict(
                Counter(
                    signal
                    for case in constraint_cases
                    for signal in case["question_constraints"]["signals"]
                )
            ),
        },
        "operator_analysis": {
            "wrong_operator_cases": counts.get("WRONG_OPERATOR", 0),
            "deterministic_mapping": 0,
            "context_dependent": 0,
            "undetermined": 0,
        },
        "temporal_analysis": {
            "filter_specific_temporal_cases": counts.get("TEMPORAL_FILTER", 0),
            "subtypes": dict(
                Counter(
                    case["subtype_child"]
                    for case in cases
                    if case["primary_subtype"] == "TEMPORAL_FILTER"
                )
            ),
        },
        "column_grounding_analysis": {
            "wrong_filter_column_cases": counts.get("WRONG_FILTER_COLUMN", 0),
            "mechanisms": dict(
                Counter(
                    tag
                    for case in cases
                    if case["primary_subtype"] == "WRONG_FILTER_COLUMN"
                    for tag in case["secondary_tags"]
                )
            ),
        },
        "boolean_null_having": {
            "boolean_composition": counts.get("BOOLEAN_COMPOSITION", 0),
            "null_or_existence": counts.get("NULL_OR_EXISTENCE", 0),
            "having_vs_where": counts.get("HAVING_VS_WHERE", 0),
        },
        "counterfactual_evidence": {
            "cases_tested": 0,
            "single_change_fixes": 0,
            "single_change_non_fixes": 0,
        },
        "correct_control": {
            "source": "M20 frozen accepted rows with preserved generated SQL; M20.1R2 recovered rows do not preserve SQL",
            "population": 475,
            "shape_breakdown": _shape_control(),
        },
        "solution_family_mapping": solution_families,
        "existing_capability_relevance": source["existing_capability_relevance"],
        "residual_m1_rejects": {"count": 264, "status": "OUTSIDE_M22_PRIMARY_POPULATION"},
        "cases": sorted(cases, key=lambda case: (case["benchmark"], str(case["case_id"]))),
        "method": {
            "reference_usage": "offline benchmark evidence only",
            "classification": "reviewed filter AST facts, question/evidence, and frozen typed-result mismatch; no provider or SQL regeneration",
            "primary_rule": "one subtype per frozen FILTER_SEMANTICS case; M20.2 primary category remains unchanged",
            "counterfactual_execution": "none",
        },
        "recommended_next_intervention": {
            "family": "BOUNDED_DB_VALUE_GROUNDING",
            "status": "RECOMMENDED_NOT_IMPLEMENTED",
            "mechanism": "wrong filter literals include explicit enum/code and label-variant mappings",
            "affected_cases": len(value_cases),
            "rationale": "highest-confidence isolated filter subtype with evidence across BIRD and Defog Classic; must be tested without gold-aware runtime behavior",
        },
    }
    validate_artifact(artifact)
    return artifact


def validate_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("classification") != "M22_FILTER_SEMANTICS_DEEP_FORENSICS_COMPLETED":
        raise M22ValidationError("wrong M22 classification")
    if artifact.get("provider_calls") != 0 or artifact.get("fresh_sql_generation") != 0:
        raise M22ValidationError("M22 must be provider-free")
    if artifact.get("population") != 61 or len(artifact.get("cases", [])) != 61:
        raise M22ValidationError("M22 population is incomplete")
    cases = artifact["cases"]
    if len({(case["benchmark"], case["case_id"]) for case in cases}) != 61:
        raise M22ValidationError("M22 case IDs are not unique")
    counts = Counter(case["primary_subtype"] for case in cases)
    if sum(counts.values()) != 61 or any(subtype not in FILTER_SUBTYPES for subtype in counts):
        raise M22ValidationError("M22 subtype totals do not reconcile")
    if any(case["benchmark"] not in {"bird", "defog_classic"} for case in cases):
        raise M22ValidationError("unexpected benchmark in frozen filter population")


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
                "provider_calls": artifact["provider_calls"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
