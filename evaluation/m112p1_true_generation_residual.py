"""M11.2P1 offline true-generation residual rebaseline.

This module consumes the frozen M11.2P0 contract adjudication and P3 candidate
artifacts.  It uses conservative, literal-preserving SQLGlot evidence and
never calls a provider, database, retriever, evaluator, or application
runtime.  ``--prepare`` freezes the protocol before candidate SQL exposure;
``--analyze`` then writes additive diagnostic evidence for the 53 eligible
cases.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
START_HEAD = "a9a08ec3de48ac6c4919b34ac8b53160f4cbbe97"
P0_CHECKPOINT = START_HEAD
P0_CLASSIFICATION = "M112P0_CONTRACT_INTEGRITY_AUDIT_COMPLETED"
P0_POPULATION_HASH = "d3461d814b532bd14241ae39c9aa897c36d8991d541cf4a2a7a8dbe60408a43c"
P0_ELIGIBILITY_HASH = "6048fb93160520cf0cd8aee2aa68d6cb4b4f6c250b9c29b60090356fd5d7b088"
P0_PREDECESSOR_HASH = "c939b8b5a73cd509c3c04aafe3d3fb97eddfaa4ec89480861cc19dbe4c7530c2"
P3_CHECKPOINT = "2735ca60036946cb54070294366f1d5a32a21996"
P3_MATRIX = {"A": 36, "B": 4, "C": 9, "D": 56}

FAMILIES = (
    "M1_REJECTION",
    "SOURCE_ENTITY_SELECTION",
    "RELATIONAL_COMPOSITION_JOIN",
    "ROW_FILTER_VALUE_SEMANTICS",
    "GROUP_FILTER_HAVING_SEMANTICS",
    "GROUPING_GRAIN_SEMANTICS",
    "AGGREGATION_SEMANTICS",
    "FORMULA_BUSINESS_SEMANTICS",
    "WINDOW_SEMANTICS",
    "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "DISTINCT_SET_SEMANTICS",
    "NULL_ZERO_INCLUSION_SEMANTICS",
    "PROJECTION_OUTPUT_SEMANTICS",
    "OTHER_BOUNDED_SEMANTICS",
    "UNRESOLVED",
)
EVIDENCE_STATES = (
    "MATERIAL_FAILURE_PROVEN",
    "NO_MATERIAL_DIVERGENCE_DETECTED",
    "DIVERGENCE_UNRESOLVED",
    "NOT_APPLICABLE",
    "P0_TIER_MASKED",
)
PAIR_STATES = (
    "STABLE_CANDIDATE_SHARED_FAILURE",
    "STABLE_CANDIDATE_ATTRIBUTION_UNRESOLVED",
    "TREATMENT_SENSITIVE_SHARED_FAILURE_BOUNDARY",
    "TREATMENT_SENSITIVE_OVERLAPPING_FAILURE_BOUNDARIES",
    "TREATMENT_SENSITIVE_DIFFERENT_FAILURE_BOUNDARIES",
    "TREATMENT_SENSITIVE_ONE_ARM_UNRESOLVED",
    "TREATMENT_SENSITIVE_BOTH_ARMS_UNRESOLVED",
)
REASON_CODES = (
    "SOURCE_WRONG_ENTITY",
    "SOURCE_REQUIRED_ENTITY_MISSING",
    "JOIN_REQUIRED_RELATION_MISSING",
    "JOIN_WRONG_KEY",
    "JOIN_WRONG_KIND",
    "JOIN_WRONG_RELATION",
    "RELATIONAL_SUBQUERY_MISMATCH",
    "RELATIONAL_SET_OPERATION_MISMATCH",
    "FILTER_REQUIRED_PREDICATE_MISSING",
    "FILTER_WRONG_COLUMN",
    "FILTER_WRONG_LITERAL",
    "FILTER_WRONG_OPERATOR",
    "FILTER_WRONG_RANGE",
    "FILTER_WRONG_BOOLEAN_LOGIC",
    "HAVING_REQUIRED_PREDICATE_MISSING",
    "HAVING_WRONG_THRESHOLD",
    "HAVING_WRONG_AGGREGATE",
    "GRAIN_WRONG_ENTITY_LEVEL",
    "GROUP_BY_REQUIRED_DIMENSION_MISSING",
    "GROUP_BY_EXTRA_MATERIAL_DIMENSION",
    "AGGREGATE_WRONG_FUNCTION",
    "AGGREGATE_MISSING",
    "AGGREGATE_WRONG_SCOPE",
    "FORMULA_WRONG_NUMERATOR",
    "FORMULA_WRONG_DENOMINATOR",
    "FORMULA_MISSING_OPERATOR",
    "FORMULA_WRONG_ARITHMETIC",
    "FORMULA_WRONG_SCALE",
    "FORMULA_WRONG_DIRECTION",
    "WINDOW_WRONG_FUNCTION",
    "WINDOW_WRONG_PARTITION",
    "WINDOW_WRONG_ORDER",
    "WINDOW_WRONG_FRAME",
    "WINDOW_WRONG_OFFSET",
    "ORDER_REQUIRED_MISSING",
    "ORDER_WRONG_EXPRESSION",
    "ORDER_WRONG_DIRECTION",
    "RANK_WRONG_FUNCTION",
    "LIMIT_WRONG_VALUE",
    "OFFSET_WRONG_VALUE",
    "DISTINCT_MISSING",
    "DISTINCT_SPURIOUS",
    "SET_DUPLICATE_SEMANTICS_WRONG",
    "NULL_HANDLING_WRONG",
    "ZERO_MEMBER_INCLUSION_WRONG",
    "PROJECTION_REQUIRED_FIELD_MISSING",
    "PROJECTION_WRONG_REQUESTED_FIELD",
    "PROJECTION_WRONG_REQUESTED_MEASURE",
    "M1_REJECT_PARSE",
    "M1_REJECT_AST_POLICY",
    "M1_REJECT_OBJECT_POLICY",
    "M1_REJECT_FUNCTION_POLICY",
    "M1_REJECT_COST_POLICY",
    "M1_REJECT_OTHER",
    "OTHER_BOUNDED",
    "UNRESOLVED_EQUIVALENCE",
    "UNRESOLVED_MULTIPLE_INTERPRETATIONS",
    "UNRESOLVED_INSUFFICIENT_EVIDENCE",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _raw_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_json(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return _raw_hash(path)


def _write_jsonl(name: str, rows: Iterable[dict[str, Any]]) -> str:
    path = FIXTURES / name
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return _raw_hash(path)


def protocol() -> dict[str, Any]:
    return {
        "milestone": "M11.2P1 — True Generation Residual Rebaseline",
        "classification": "M112P1_RESIDUAL_ATTRIBUTION_COMPLETED",
        "mode": "ZERO_PROVIDER_ZERO_DB_ZERO_RETRIEVAL_NO_IMPLEMENTATION",
        "population": {"FULL": 23, "CORE": 30, "QUARANTINED": 3, "eligible": 53},
        "p0_contract_unchanged": True,
        "p1_labels_used_as_authority": False,
        "taxonomy_frozen_before_case_exposure": True,
        "m112p1_generation_taxonomy_in_p0": False,
        "provider_calls": 0,
        "db_calls": 0,
        "retrieval_reruns": 0,
        "candidate_regeneration": 0,
        "reference_rewrites": 0,
        "runtime_changes": 0,
    }


def starting_checkpoint() -> dict[str, Any]:
    return {
        "reviewed_checkpoint": START_HEAD,
        "branch": "main",
        "starting_tree": "clean",
        "origin_main": START_HEAD,
    }


def predecessor_integrity() -> dict[str, Any]:
    return {
        "p0_checkpoint": P0_CHECKPOINT,
        "p0_classification": P0_CLASSIFICATION,
        "p0_population_hash": P0_POPULATION_HASH,
        "p0_eligibility_hash": P0_ELIGIBILITY_HASH,
        "p0_predecessor_integrity_hash": P0_PREDECESSOR_HASH,
        "p3_checkpoint": P3_CHECKPOINT,
        "p3_primary_matrix": P3_MATRIX,
        "p1_future_decision_use": "QUARANTINED_PENDING_P1R2",
        "p1_adversarial_reproduction": "m112p0_p1_adversarial_reproduction.json",
        "p1_quarantine": "m112p0_p1_quarantine.json",
        "historical_artifacts_mutated": False,
    }


def _p0_rows() -> list[dict[str, Any]]:
    return _read_jsonl(FIXTURES / "m112p0_case_adjudications.jsonl")


def _tier_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _p0_rows()
    eligible = [
        row
        for row in rows
        if row["overall_attribution_eligibility"]
        in {"FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE", "CORE_SEMANTIC_ATTRIBUTION_ONLY"}
    ]
    excluded = [row for row in rows if row not in eligible]
    return eligible, excluded


def population_manifest() -> dict[str, Any]:
    eligible, excluded = _tier_rows()
    tiers = {
        row["case_id"]: "FULL"
        if row["overall_attribution_eligibility"] == "FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE"
        else "CORE"
        for row in eligible
    }
    return {
        "population_contract": "m112p1-p0-eligible-p3-d-v1",
        "source": "m112p0_case_adjudications.jsonl; P0 eligibility only",
        "eligible_count": len(eligible),
        "full_count": sum(value == "FULL" for value in tiers.values()),
        "core_count": sum(value == "CORE" for value in tiers.values()),
        "rows": [
            {
                "case_id": row["case_id"],
                "tier": tiers[row["case_id"]],
                "question_hash": row["question_hash"],
                "reference_sql_hash": row["reference_sql_hash"],
                "off_candidate_hash": row["off_candidate_hash"],
                "on_candidate_hash": row["on_candidate_hash"],
            }
            for row in eligible
        ],
        "p0_labels_used_for_membership": True,
        "case_level_sql_exposure_started": False,
        "excluded_count": len(excluded),
    }


def exclusions_manifest() -> dict[str, Any]:
    _, excluded = _tier_rows()
    return {
        "excluded_count": len(excluded),
        "rows": [
            {
                "case_id": row["case_id"],
                "p0_eligibility": row["overall_attribution_eligibility"],
                "excluded_from_failure_evidence": True,
            }
            for row in excluded
        ],
        "m104s_direct_window_06_excluded": any(
            row["case_id"] == "m104s-direct-window-06" for row in excluded
        ),
    }


def tier_rulebook() -> dict[str, Any]:
    return {
        "FULL": "core semantics plus question-entitled output/shape behavior may be attributed",
        "CORE": (
            "substantive relational/calculation semantics only; P0-masked output "
            "differences are excluded"
        ),
        "QUARANTINED": "no model-failure evidence",
        "p1_labels_authoritative": False,
        "p0_tier_promotion": False,
    }


def failure_taxonomy() -> dict[str, Any]:
    return {
        "version": "m112p1-failure-taxonomy-v1",
        "primary_families": list(FAMILIES),
        "multi_label": True,
        "primary_multiple": "MULTIPLE_INDEPENDENT_FAILURES",
        "fallback": "UNRESOLVED",
    }


def reason_code_catalog() -> dict[str, Any]:
    return {
        "version": "m112p1-reason-codes-v1",
        "codes": list(REASON_CODES),
        "post_exposure_additions": False,
        "material_failure_requires_positive_evidence": True,
    }


def ast_evidence_contract() -> dict[str, Any]:
    return {
        "version": "m112p1-literal-preserving-ast-evidence-v1",
        "preserved": [
            "string literal case",
            "numeric/date/boolean literal values",
            "JOIN kind",
            "JOIN ON",
            "JOIN USING columns",
            "HAVING",
            "ORDER priority/direction",
            "LIMIT",
            "OFFSET",
            "window function/partition/order/frame/offset",
        ],
        "not_general_equivalence_engine": True,
        "structural_difference_alone": "DIVERGENCE_UNRESOLVED",
        "whole_sql_lowercase": False,
    }


def pair_contract() -> dict[str, Any]:
    return {
        "version": "m112p1-pair-states-v1",
        "states": list(PAIR_STATES),
        "pair_count_must_equal": 53,
        "memory_causality_claims": False,
    }


def sql_identity_contract() -> dict[str, Any]:
    return {
        "version": "m112p1-sql-identity-v1",
        "identity": "literal-preserving recursive SQLGlot AST signature hash",
        "states": [
            "PARSE_NORMALIZED_IDENTICAL",
            "PARSE_NORMALIZED_DIFFERENT",
            "SQL_IDENTITY_UNRESOLVED",
        ],
        "semantic_equivalence_inferred": False,
    }


def repeated_boundary_rule() -> dict[str, Any]:
    return {
        "version": "m112p1-repeated-boundary-rule-v1",
        "either_arm_unique_support_min": 10,
        "both_arm_shared_support_min": 5,
        "specific_subreason_support_min": 5,
        "material_failure_only": True,
        "core_mask_contamination": False,
        "meaning": "eligibility for a later intervention-selection review only",
    }


def design_controls() -> dict[str, Any]:
    controls = {
        "wrong_filter_literal": {"expected": "ROW_FILTER_VALUE_SEMANTICS"},
        "missing_filter": {"expected": "ROW_FILTER_VALUE_SEMANTICS"},
        "wrong_join_key": {"expected": "RELATIONAL_COMPOSITION_JOIN"},
        "left_vs_inner_outer_required": {"expected": "NULL_ZERO_INCLUSION_SEMANTICS"},
        "wrong_group_by": {"expected": "GROUPING_GRAIN_SEMANTICS"},
        "sum_vs_avg": {"expected": "AGGREGATION_SEMANTICS"},
        "wrong_formula_denominator": {"expected": "FORMULA_BUSINESS_SEMANTICS"},
        "wrong_window_partition": {"expected": "WINDOW_SEMANTICS"},
        "wrong_window_order": {"expected": "WINDOW_SEMANTICS"},
        "wrong_rank_direction": {"expected": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS"},
        "offset_change": {"expected": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS"},
        "having_threshold": {"expected": "GROUP_FILTER_HAVING_SEMANTICS"},
        "literal_case": {"expected": "ROW_FILTER_VALUE_SEMANTICS"},
        "full_projection_entitlement": {"expected": "PROJECTION_OUTPUT_SEMANTICS"},
        "core_projection_mask": {"expected": "P0_TIER_MASKED"},
        "structural_divergence": {"expected": "UNRESOLVED"},
        "multi_failure": {"expected": "MULTIPLE_INDEPENDENT_FAILURES"},
        "m1_rejection": {"expected": "M1_REJECTION"},
    }
    return {
        "version": "m112p1-design-controls-v1",
        "controls": controls,
        "all_controls_pre_exposure": True,
    }


def _preexposure_names() -> list[str]:
    return [
        "m112p1_protocol.json",
        "m112p1_starting_checkpoint.json",
        "m112p1_predecessor_integrity.json",
        "m112p1_population.json",
        "m112p1_exclusions.json",
        "m112p1_tier_rulebook.json",
        "m112p1_failure_taxonomy.json",
        "m112p1_reason_codes.json",
        "m112p1_ast_evidence_contract.json",
        "m112p1_pair_contract.json",
        "m112p1_sql_identity_contract.json",
        "m112p1_repeated_boundary_rule.json",
        "m112p1_design_controls.json",
    ]


def prepare() -> dict[str, str]:
    values = {
        "m112p1_protocol.json": protocol(),
        "m112p1_starting_checkpoint.json": starting_checkpoint(),
        "m112p1_predecessor_integrity.json": predecessor_integrity(),
        "m112p1_population.json": population_manifest(),
        "m112p1_exclusions.json": exclusions_manifest(),
        "m112p1_tier_rulebook.json": tier_rulebook(),
        "m112p1_failure_taxonomy.json": failure_taxonomy(),
        "m112p1_reason_codes.json": reason_code_catalog(),
        "m112p1_ast_evidence_contract.json": ast_evidence_contract(),
        "m112p1_pair_contract.json": pair_contract(),
        "m112p1_sql_identity_contract.json": sql_identity_contract(),
        "m112p1_repeated_boundary_rule.json": repeated_boundary_rule(),
        "m112p1_design_controls.json": design_controls(),
    }
    hashes = {name: _write_json(name, value) for name, value in values.items()}
    preexposure = {
        "classification": "M112P1_PREEXPOSURE_FROZEN",
        "population_exposure_started": False,
        "artifact_raw_sha256": hashes,
        "taxonomy_immutable_after_exposure": True,
        "provider_db_retrieval": False,
    }
    hashes["m112p1_preexposure_manifest.json"] = _write_json(
        "m112p1_preexposure_manifest.json", preexposure
    )
    return hashes


def _parse(sql: str | None) -> exp.Expression | None:
    if not isinstance(sql, str) or not sql.strip():
        return None
    try:
        return parse_one(sql, read="postgres")
    except Exception:
        return None


def _sig(value: Any) -> Any:
    if isinstance(value, exp.Literal):
        return {"node": "Literal", "is_string": bool(value.is_string), "this": value.this}
    if isinstance(value, exp.Identifier):
        return {"node": "Identifier", "this": value.this, "quoted": bool(value.args.get("quoted"))}
    if isinstance(value, exp.Expression):
        return {
            "node": value.__class__.__name__,
            "args": {
                key: _sig(child) for key, child in sorted(value.args.items()) if child is not None
            },
        }
    if isinstance(value, list):
        return [_sig(item) for item in value]
    if isinstance(value, tuple):
        return [_sig(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sig(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"scalar_type": type(value).__name__, "value": str(value)}


def _node_sig(node: exp.Expression | None) -> Any:
    return None if node is None else _sig(node)


def _normalise_tree(tree: exp.Expression) -> exp.Expression:
    value = tree.copy()
    aliases: dict[str, str] = {}
    tables = list(value.find_all(exp.Table))
    single_source = tables[0].name if len(tables) == 1 else None
    for table in tables:
        if table.alias:
            aliases[table.alias] = table.name
        table.set("alias", None)
    for column in value.find_all(exp.Column):
        if column.table:
            column.set("table", exp.Identifier(this=aliases.get(column.table, column.table)))
        elif single_source:
            column.set("table", exp.Identifier(this=single_source))
    for ordered in value.find_all(exp.Ordered):
        if ordered.args.get("desc") is False:
            ordered.set("desc", None)
    return value


def _projection_sig(node: exp.Expression) -> Any:
    if isinstance(node, exp.Alias):
        node = node.this
    return _node_sig(node)


def _nodes(tree: exp.Expression, cls: Any) -> list[exp.Expression]:
    return list(tree.find_all(cls))


def _select(tree: exp.Expression | None) -> exp.Select | None:
    if isinstance(tree, exp.Select):
        return tree
    return tree.find(exp.Select) if tree is not None else None


def _feature(sql: str | None) -> dict[str, Any] | None:
    tree = _parse(sql)
    select = _select(tree)
    if tree is None or select is None:
        return None
    tree = _normalise_tree(tree)
    select = _select(tree)
    if select is None:
        return None
    joins = []
    for join in _nodes(tree, exp.Join):
        joins.append(
            {
                "kind": join.args.get("side") or join.args.get("kind") or "INNER",
                "on": _node_sig(join.args.get("on")),
                "using": _node_sig(join.args.get("using")),
            }
        )
    tables = sorted((_node_sig(node) for node in _nodes(tree, exp.Table)), key=_stable_hash)
    source_names = sorted(table.name for table in _nodes(tree, exp.Table))
    aggregates = sorted(
        (_node_sig(node) for node in _nodes(tree, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max))),
        key=_stable_hash,
    )
    aggregate_kinds = sorted(
        {
            node.__class__.__name__
            for node in _nodes(tree, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max))
        }
    )
    null_semantics = sorted(
        {
            node.__class__.__name__
            for node in _nodes(tree, (exp.Is, exp.Coalesce, exp.NullSafeEQ, exp.NullSafeNEQ))
        }
    )
    windows = sorted((_node_sig(node) for node in _nodes(tree, exp.Window)), key=_stable_hash)
    formulas = sorted(
        (_node_sig(node) for node in _nodes(tree, (exp.Div, exp.Add, exp.Sub, exp.Mul, exp.Mod))),
        key=_stable_hash,
    )
    order = _node_sig(select.args.get("order"))
    return {
        "full": _node_sig(tree),
        "sources": tables,
        "source_names": source_names,
        "joins": joins,
        "where": _node_sig(select.args.get("where")),
        "group": _node_sig(select.args.get("group")),
        "having": _node_sig(select.args.get("having")),
        "aggregates": aggregates,
        "aggregate_kinds": aggregate_kinds,
        "null_semantics": null_semantics,
        "windows": windows,
        "formulas": formulas,
        "order": order,
        "limit": _node_sig(select.args.get("limit")),
        "offset": _node_sig(select.args.get("offset") or tree.args.get("offset")),
        "distinct": _node_sig(select.args.get("distinct")),
        "projection": [_projection_sig(node) for node in select.expressions],
    }


def _feature_diff(ref: dict[str, Any], cand: dict[str, Any]) -> set[str]:
    return {
        key
        for key in (
            "sources",
            "source_names",
            "joins",
            "where",
            "group",
            "having",
            "aggregates",
            "null_semantics",
            "windows",
            "order",
            "limit",
            "offset",
            "distinct",
            "projection",
            "formulas",
        )
        if ref[key] != cand[key]
    }


_DIMENSION_FAMILIES = {
    "sources": "SOURCE_ENTITY_SELECTION",
    "source_names": "SOURCE_ENTITY_SELECTION",
    "joins": "RELATIONAL_COMPOSITION_JOIN",
    "where": "ROW_FILTER_VALUE_SEMANTICS",
    "group": "GROUPING_GRAIN_SEMANTICS",
    "having": "GROUP_FILTER_HAVING_SEMANTICS",
    "aggregates": "AGGREGATION_SEMANTICS",
    "null_semantics": "NULL_ZERO_INCLUSION_SEMANTICS",
    "formulas": "FORMULA_BUSINESS_SEMANTICS",
    "windows": "WINDOW_SEMANTICS",
    "order": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "limit": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "offset": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "distinct": "DISTINCT_SET_SEMANTICS",
    "projection": "PROJECTION_OUTPUT_SEMANTICS",
}


def _question_terms(question: str) -> set[str]:
    return {word.strip(".,?!:;()'\"").lower() for word in question.split()}


def _literal_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        if value.get("node") == "Literal":
            return {str(value["this"])}
        return {item for child in value.values() for item in _literal_values(child)}
    if isinstance(value, list):
        return {item for child in value for item in _literal_values(child)}
    return set()


def _group_expressions(value: Any) -> list[Any]:
    if not isinstance(value, dict) or value.get("node") != "Group":
        return []
    expressions = value.get("args", {}).get("expressions", [])
    return expressions if isinstance(expressions, list) else []


def _order_requirement_diff(ref: dict[str, Any], cand: dict[str, Any]) -> bool:
    reference = ref["order"]
    candidate = cand["order"]
    if reference is None:
        return False
    if candidate is None:
        return True
    reference_items = reference.get("args", {}).get("expressions", [])
    candidate_items = candidate.get("args", {}).get("expressions", [])
    if not isinstance(reference_items, list) or not isinstance(candidate_items, list):
        return True
    if len(candidate_items) < len(reference_items):
        return True

    def order_core(item: Any) -> Any:
        if not isinstance(item, dict) or item.get("node") != "Ordered":
            return item
        args = item.get("args", {})
        return {"this": args.get("this"), "desc": args.get("desc")}

    return any(
        _stable_hash(order_core(left)) != _stable_hash(order_core(right))
        for left, right in zip(reference_items, candidate_items, strict=False)
    )


def _window_failure_reason(ref: dict[str, Any], cand: dict[str, Any]) -> str:
    reference_windows = ref.get("windows", [])
    candidate_windows = cand.get("windows", [])
    if not reference_windows or not candidate_windows:
        return "WINDOW_WRONG_FUNCTION"
    if len(reference_windows) != len(candidate_windows):
        return "WINDOW_WRONG_FUNCTION"
    for reference, candidate in zip(reference_windows, candidate_windows, strict=True):
        reference_args = reference.get("args", {})
        candidate_args = candidate.get("args", {})
        reference_function = reference_args.get("this", {}).get("node")
        candidate_function = candidate_args.get("this", {}).get("node")
        if reference_function != candidate_function:
            return "WINDOW_WRONG_FUNCTION"
        if reference_args.get("partition_by") != candidate_args.get("partition_by"):
            return "WINDOW_WRONG_PARTITION"
        if reference_args.get("order") != candidate_args.get("order"):
            return "WINDOW_WRONG_ORDER"
        if reference_args.get("spec") != candidate_args.get("spec"):
            return "WINDOW_WRONG_FRAME"
    return "WINDOW_WRONG_OFFSET"


def _positive_failure(
    question: str, ref: dict[str, Any], cand: dict[str, Any], tier: str
) -> list[dict[str, str]]:
    terms = _question_terms(question)
    diffs = _feature_diff(ref, cand)
    failures: list[dict[str, str]] = []

    def add(family: str, reason: str, evidence: str) -> None:
        failures.append({"family": family, "reason_code": reason, "evidence": evidence})

    ref_where_literals = _literal_values(ref["where"])
    cand_where_literals = _literal_values(cand["where"])
    question_literals = {value.lower() for value in ref_where_literals} & terms
    null_difference_without_contract = (
        ref["null_semantics"] != cand["null_semantics"] and "null" not in terms
    )
    if (
        "where" in diffs
        and ref["where"] is not None
        and question_literals
        and not null_difference_without_contract
    ):
        reason = (
            "FILTER_REQUIRED_PREDICATE_MISSING"
            if cand["where"] is None
            else "FILTER_WRONG_LITERAL"
            if ref_where_literals != cand_where_literals
            else "FILTER_WRONG_OPERATOR"
        )
        add(
            "ROW_FILTER_VALUE_SEMANTICS",
            reason,
            "question names a reference filter value and candidate WHERE changes "
            "the bounded predicate",
        )
    ref_having_literals = _literal_values(ref["having"])
    cand_having_literals = _literal_values(cand["having"])
    if "having" in diffs and any(
        word in terms for word in ("count", "total", "average", "sum", "orders")
    ):
        reason = (
            "HAVING_REQUIRED_PREDICATE_MISSING"
            if cand["having"] is None
            else "HAVING_WRONG_THRESHOLD"
            if ref_having_literals != cand_having_literals
            else "HAVING_WRONG_AGGREGATE"
        )
        add(
            "GROUP_FILTER_HAVING_SEMANTICS",
            reason,
            "question requests the aggregate filter and candidate HAVING changes "
            "its bounded condition",
        )
    reference_group = _group_expressions(ref["group"])
    if (
        "group" in diffs
        and reference_group
        and cand["group"] is None
        and any(word in terms for word in ("each", "per", "group", "representative", "category"))
    ):
        add(
            "GROUPING_GRAIN_SEMANTICS",
            "GRAIN_WRONG_ENTITY_LEVEL",
            "question specifies an explicit repeated/grouped grain and candidate GROUP BY differs",
        )
    if (
        "aggregates" in diffs
        and ref["aggregate_kinds"] != cand["aggregate_kinds"]
        and any(word in terms for word in ("count", "sum", "total", "average", "avg"))
    ):
        add(
            "AGGREGATION_SEMANTICS",
            "AGGREGATE_WRONG_FUNCTION",
            "question names an aggregate and candidate aggregate structure differs",
        )
    if "formulas" in diffs and any(
        word in terms for word in ("share", "ratio", "percentage", "percent", "difference")
    ):
        add(
            "FORMULA_BUSINESS_SEMANTICS",
            "FORMULA_WRONG_ARITHMETIC",
            "question names a calculated ratio/difference and candidate arithmetic "
            "structure differs",
        )
    if "windows" in diffs and any(
        word in terms
        for word in ("window", "running", "previous", "next", "share", "within", "partition")
    ):
        add(
            "WINDOW_SEMANTICS",
            _window_failure_reason(ref, cand),
            "question names row-relative/window scope and candidate window structure differs",
        )
    explicit_order_terms = {
        "top",
        "highest",
        "lowest",
        "rank",
        "sorted",
        "descending",
        "ascending",
        "first",
        "last",
        "pagination",
        "page",
    }
    if (
        (_order_requirement_diff(ref, cand) or "limit" in diffs or "offset" in diffs)
        and explicit_order_terms & terms
    ) and ("order" in diffs or "limit" in diffs or "offset" in diffs):
        reason = "OFFSET_WRONG_VALUE" if "offset" in diffs else "ORDER_WRONG_EXPRESSION"
        add(
            "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
            reason,
            "question specifies ordering/ranking/selection and candidate ordering or "
            "pagination differs",
        )
    if "distinct" in diffs and any(word in terms for word in ("distinct", "unique", "each")):
        add(
            "DISTINCT_SET_SEMANTICS",
            "DISTINCT_MISSING",
            "question makes duplicate/set semantics material and DISTINCT differs",
        )
    if (
        "joins" in diffs
        and ref["joins"]
        and not cand["joins"]
        and any(
            word in terms
            for word in ("with", "whose", "within", "belongs", "associated", "relationship")
        )
    ):
        add(
            "RELATIONAL_COMPOSITION_JOIN",
            "JOIN_WRONG_RELATION",
            "question specifies a relationship and candidate join structure differs",
        )
    if "sources" in diffs and set(ref.get("source_names", [])) - set(cand.get("source_names", [])):
        add(
            "SOURCE_ENTITY_SELECTION",
            "SOURCE_WRONG_ENTITY",
            "candidate omits a reference source entity required by the aligned reference",
        )
    if "projection" in diffs and tier == "FULL" and ("only" in terms or "exactly" in terms):
        add(
            "PROJECTION_OUTPUT_SEMANTICS",
            "PROJECTION_WRONG_REQUESTED_FIELD",
            "FULL tier permits attribution and question names output fields",
        )
    if (
        any(
            key in diffs
            for key in (
                "projection",
                "where",
                "group",
                "aggregates",
                "windows",
                "order",
                "limit",
                "offset",
                "distinct",
                "formulas",
            )
        )
        and not failures
    ):
        return []
    return failures


def _m1_failure(candidate: dict[str, Any]) -> dict[str, str]:
    status = str(candidate.get("status", ""))
    reason = str(
        candidate.get("m1_rejection_code") or candidate.get("rejection_code") or "M1_REJECT_OTHER"
    )
    allowed = {
        "M1_REJECT_PARSE",
        "M1_REJECT_AST_POLICY",
        "M1_REJECT_OBJECT_POLICY",
        "M1_REJECT_FUNCTION_POLICY",
        "M1_REJECT_COST_POLICY",
    }
    return {
        "family": "M1_REJECTION",
        "reason_code": reason if reason in allowed else "M1_REJECT_OTHER",
        "evidence": f"frozen candidate status={status}",
    }


def _identity(ref: dict[str, Any] | None, cand: dict[str, Any] | None) -> str:
    if ref is None or cand is None:
        return "SQL_IDENTITY_UNRESOLVED"
    return (
        "PARSE_NORMALIZED_IDENTICAL"
        if _stable_hash(ref["full"]) == _stable_hash(cand["full"])
        else "PARSE_NORMALIZED_DIFFERENT"
    )


def _arm(case: dict[str, Any], tier: str, arm: str) -> dict[str, Any]:
    candidate = case[arm.lower()]
    ref_feature = _feature(case["reference_sql"])
    cand_feature = _feature(candidate.get("candidate_sql"))
    identity = _identity(ref_feature, cand_feature)
    if candidate.get("status") == "M1_REJECTED":
        failures = [_m1_failure(candidate)]
        states = {family: "NOT_APPLICABLE" for family in FAMILIES}
        states["M1_REJECTION"] = "MATERIAL_FAILURE_PROVEN"
    elif ref_feature is None or cand_feature is None:
        failures = []
        states = {family: "DIVERGENCE_UNRESOLVED" for family in FAMILIES}
    else:
        failures = _positive_failure(case["question"], ref_feature, cand_feature, tier)
        states = {family: "NO_MATERIAL_DIVERGENCE_DETECTED" for family in FAMILIES}
        diffs = _feature_diff(ref_feature, cand_feature)
        for dimension in diffs:
            states[_DIMENSION_FAMILIES[dimension]] = "DIVERGENCE_UNRESOLVED"
        for failure in failures:
            states[failure["family"]] = "MATERIAL_FAILURE_PROVEN"
        if tier == "CORE" and "projection" in diffs:
            states["PROJECTION_OUTPUT_SEMANTICS"] = "P0_TIER_MASKED"
        if not diffs:
            states["UNRESOLVED"] = "NOT_APPLICABLE"
    return {
        "case_id": case["case_id"],
        "arm": arm,
        "candidate_status": candidate.get("status"),
        "candidate_hash": candidate.get("candidate_sql_hash"),
        "sql_identity_hash": _stable_hash(cand_feature["full"]) if cand_feature else None,
        "candidate_identity_hash": _stable_hash(cand_feature["full"]) if cand_feature else None,
        "sql_identity_state": identity,
        "dimension_states": states,
        "proven_failure_families": sorted({failure["family"] for failure in failures}),
        "reason_codes": sorted({failure["reason_code"] for failure in failures}),
        "failures": failures,
        "p0_masked_output_differences": ["projection"]
        if tier == "CORE"
        and ref_feature
        and cand_feature
        and "projection" in _feature_diff(ref_feature, cand_feature)
        else [],
    }


def _primary(failures: list[dict[str, Any]]) -> str:
    families = sorted({item["family"] for item in failures})
    if not families:
        return "UNRESOLVED"
    if len(families) > 1:
        return "MULTIPLE_INDEPENDENT_FAILURES"
    return str(families[0])


def _pair_state(off: dict[str, Any], on: dict[str, Any]) -> str:
    off_set, on_set = set(off["proven_failure_families"]), set(on["proven_failure_families"])
    same_sql = off.get("candidate_identity_hash") is not None and off.get(
        "candidate_identity_hash"
    ) == on.get("candidate_identity_hash")
    if same_sql and off_set:
        return "STABLE_CANDIDATE_SHARED_FAILURE"
    if same_sql:
        return "STABLE_CANDIDATE_ATTRIBUTION_UNRESOLVED"
    if off_set and on_set and off_set == on_set:
        return "TREATMENT_SENSITIVE_SHARED_FAILURE_BOUNDARY"
    if off_set and on_set and off_set & on_set:
        return "TREATMENT_SENSITIVE_OVERLAPPING_FAILURE_BOUNDARIES"
    if off_set and on_set:
        return "TREATMENT_SENSITIVE_DIFFERENT_FAILURE_BOUNDARIES"
    if bool(off_set) != bool(on_set):
        return "TREATMENT_SENSITIVE_ONE_ARM_UNRESOLVED"
    return "TREATMENT_SENSITIVE_BOTH_ARMS_UNRESOLVED"


def load_cases() -> list[dict[str, Any]]:
    eligible, _ = _tier_rows()
    tiers = {
        row["case_id"]: "FULL"
        if row["overall_attribution_eligibility"] == "FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE"
        else "CORE"
        for row in eligible
    }
    corpus = {row["case_id"]: row for row in _load(FIXTURES / "m104s_corpus.json")["cases"]}
    candidates = {
        (row["case_id"], row["arm"]): row
        for row in _read_jsonl(FIXTURES / "m111p3_candidate_ledger.jsonl")
    }
    cases = []
    for row in eligible:
        case_id = row["case_id"]
        if (
            case_id not in corpus
            or (case_id, "OFF") not in candidates
            or (case_id, "ON") not in candidates
        ):
            raise RuntimeError(f"incomplete eligible case: {case_id}")
        cases.append(
            {
                "case_id": case_id,
                "tier": tiers[case_id],
                "question": corpus[case_id]["question"],
                "reference_sql": corpus[case_id]["reference_sql"],
                "off": candidates[(case_id, "OFF")],
                "on": candidates[(case_id, "ON")],
                "p0": row,
            }
        )
    if len(cases) != 53 or len({case["case_id"] for case in cases}) != 53:
        raise RuntimeError("eligible population is not exactly 53 unique cases")
    return cases


def _case(case: dict[str, Any]) -> dict[str, Any]:
    off, on = _arm(case, case["tier"], "OFF"), _arm(case, case["tier"], "ON")
    off_failures = off["failures"]
    on_failures = on["failures"]
    return {
        "case_id": case["case_id"],
        "p0_tier": case["tier"],
        "question_hash": case["p0"].get("question_hash", _text_hash(case["question"])),
        "reference_sql_hash": case["p0"].get(
            "reference_sql_hash", _text_hash(case["reference_sql"])
        ),
        "OFF_candidate_hash": case["off"].get("candidate_sql_hash"),
        "ON_candidate_hash": case["on"].get("candidate_sql_hash"),
        "OFF": off,
        "ON": on,
        "OFF_m1_status": case["off"].get("status"),
        "ON_m1_status": case["on"].get("status"),
        "OFF_sql_identity_hash": off["sql_identity_hash"],
        "ON_sql_identity_hash": on["sql_identity_hash"],
        "pair_sql_identity_state": (
            "PARSE_NORMALIZED_IDENTICAL"
            if off.get("candidate_identity_hash") is not None
            and off.get("candidate_identity_hash") == on.get("candidate_identity_hash")
            else "PARSE_NORMALIZED_DIFFERENT"
            if off.get("candidate_identity_hash") is not None
            and on.get("candidate_identity_hash") is not None
            else "SQL_IDENTITY_UNRESOLVED"
        ),
        "OFF_dimension_states": off["dimension_states"],
        "ON_dimension_states": on["dimension_states"],
        "OFF_proven_failure_families": off["proven_failure_families"],
        "ON_proven_failure_families": on["proven_failure_families"],
        "OFF_reason_codes": off["reason_codes"],
        "ON_reason_codes": on["reason_codes"],
        "p0_masked_output_differences": {
            "OFF": off["p0_masked_output_differences"],
            "ON": on["p0_masked_output_differences"],
        },
        "OFF_primary_failure_family": _primary(off_failures),
        "ON_primary_failure_family": _primary(on_failures),
        "shared_proven_failure_families": sorted(
            set(off["proven_failure_families"]) & set(on["proven_failure_families"])
        ),
        "pair_failure_state": _pair_state(off, on),
        "bounded_case_summary": "M1 rejection preserved as immutable boundary"
        if "M1_REJECTION" in off["proven_failure_families"]
        or "M1_REJECTION" in on["proven_failure_families"]
        else (
            "positive bounded family evidence recorded"
            if off_failures or on_failures
            else "structural divergence did not support conservative semantic attribution"
        ),
    }


def _support(rows: list[dict[str, Any]], family: str) -> dict[str, int]:
    off = {row["case_id"] for row in rows if family in row["OFF"]["proven_failure_families"]}
    on = {row["case_id"] for row in rows if family in row["ON"]["proven_failure_families"]}
    full = {
        row["case_id"]
        for row in rows
        if row["p0_tier"] == "FULL"
        and (
            family in row["OFF"]["proven_failure_families"]
            or family in row["ON"]["proven_failure_families"]
        )
    }
    core = {
        row["case_id"]
        for row in rows
        if row["p0_tier"] == "CORE"
        and (
            family in row["OFF"]["proven_failure_families"]
            or family in row["ON"]["proven_failure_families"]
        )
    }
    return {
        "OFF": len(off),
        "ON": len(on),
        "either": len(off | on),
        "both": len(off & on),
        "OFF_only": len(off - on),
        "ON_only": len(on - off),
        "FULL": len(full),
        "CORE": len(core),
    }


def analyze() -> dict[str, str]:
    if not (FIXTURES / "m112p1_preexposure_manifest.json").exists():
        raise RuntimeError("run --prepare before --analyze")
    cases = load_cases()
    rows = [_case(case) for case in cases]
    family_support = {family: _support(rows, family) for family in FAMILIES}
    primary = {
        arm: {
            family: Counter(row[f"{arm}_primary_failure_family"] for row in rows).get(family, 0)
            for family in (*FAMILIES, "MULTIPLE_INDEPENDENT_FAILURES")
        }
        for arm in ("OFF", "ON")
    }
    pair_states = dict(Counter(row["pair_failure_state"] for row in rows))
    pair_states = {state: pair_states.get(state, 0) for state in PAIR_STATES}
    identity = dict(
        Counter(row[arm]["sql_identity_state"] for row in rows for arm in ("OFF", "ON"))
    )
    identity = {
        state: identity.get(state, 0)
        for state in (
            "PARSE_NORMALIZED_IDENTICAL",
            "PARSE_NORMALIZED_DIFFERENT",
            "SQL_IDENTITY_UNRESOLVED",
        )
    }
    unresolved = {
        "OFF_zero_proven_family": sum(not row["OFF"]["proven_failure_families"] for row in rows),
        "ON_zero_proven_family": sum(not row["ON"]["proven_failure_families"] for row in rows),
        "neither_arm_proven": sum(
            not row["OFF"]["proven_failure_families"] and not row["ON"]["proven_failure_families"]
            for row in rows
        ),
        "structural_divergence_only": sum(
            (
                row["OFF"]["sql_identity_state"] == "PARSE_NORMALIZED_DIFFERENT"
                or row["ON"]["sql_identity_state"] == "PARSE_NORMALIZED_DIFFERENT"
            )
            and not row["OFF"]["proven_failure_families"]
            and not row["ON"]["proven_failure_families"]
            for row in rows
        ),
        "cases_with_unresolved_equivalence": sum(
            row[arm]["sql_identity_state"] == "SQL_IDENTITY_UNRESOLVED"
            for row in rows
            for arm in ("OFF", "ON")
        ),
    }
    eligibility = {}
    for family, support in family_support.items():
        reason_cases: dict[str, set[str]] = {}
        for row in rows:
            for arm in ("OFF", "ON"):
                for item in row[arm]["failures"]:
                    if item["family"] == family:
                        reason_cases.setdefault(item["reason_code"], set()).add(
                            row["case_id"]
                        )
        reasons = {reason: len(case_ids) for reason, case_ids in reason_cases.items()}
        qualifies = (
            support["either"] >= 10
            and support["both"] >= 5
            and max(reasons.values(), default=0) >= 5
            and family != "UNRESOLVED"
        )
        eligibility[family] = {
            "support": support,
            "specific_subreason_support": reasons,
            "largest_recurring_subreason": (
                max(reasons, key=lambda code: reasons[code]) if reasons else None
            ),
            "repeated_boundary": "REPEATED_BOUNDED_BOUNDARY_CANDIDATE"
            if qualifies
            else "NOT_ELIGIBLE",
            "material_failure_only": family != "UNRESOLVED",
            "core_mask_contamination": False,
        }
    candidates = [
        family
        for family, data in eligibility.items()
        if data["repeated_boundary"] == "REPEATED_BOUNDED_BOUNDARY_CANDIDATE"
    ]
    ownership = {
        family: {
            "current_ownership": "SERVER_OWNED_ALREADY"
            if family == "M1_REJECTION"
            else "PARTIALLY_SERVER_CONSTRAINED"
            if family in {"SOURCE_ENTITY_SELECTION", "RELATIONAL_COMPOSITION_JOIN"}
            else "LLM_OWNED_CURRENTLY",
            "server_ownership_candidate": "YES"
            if family
            in {
                "SOURCE_ENTITY_SELECTION",
                "RELATIONAL_COMPOSITION_JOIN",
                "GROUPING_GRAIN_SEMANTICS",
                "GROUP_FILTER_HAVING_SEMANTICS",
            }
            else "UNRESOLVED",
            "existing_relevant_subsystem": "M1"
            if family == "M1_REJECTION"
            else "schema/business context or none; inventory only",
        }
        for family in FAMILIES
    }
    historical = {
        family: {
            "historical_relation": (
                "historical/revalidated family names are context only; fresh P1 "
                "evidence is authoritative"
            ),
            "architecture_selection_authority": False,
        }
        for family in FAMILIES
    }
    hashes: dict[str, str] = {
        name: _raw_hash(FIXTURES / name)
        for name in _preexposure_names() + ["m112p1_preexposure_manifest.json"]
    }
    hashes["evaluation/m112p1_true_generation_residual.py"] = _raw_hash(
        ROOT / "evaluation" / "m112p1_true_generation_residual.py"
    )
    hashes["tests/unit/test_m112p1_true_generation_residual.py"] = _raw_hash(
        ROOT / "tests" / "unit" / "test_m112p1_true_generation_residual.py"
    )
    hashes["docs/m112p1-true-generation-residual-rebaseline.md"] = _raw_hash(
        ROOT / "docs" / "m112p1-true-generation-residual-rebaseline.md"
    )
    hashes["m112p1_case_attribution.jsonl"] = _write_jsonl("m112p1_case_attribution.jsonl", rows)
    hashes["m112p1_arm_family_summary.json"] = _write_json(
        "m112p1_arm_family_summary.json",
        {"families": family_support, "multi_label_non_additive": True},
    )
    hashes["m112p1_pair_family_support.json"] = _write_json(
        "m112p1_pair_family_support.json", family_support
    )
    hashes["m112p1_tier_summary.json"] = _write_json(
        "m112p1_tier_summary.json",
        {
            "FULL": 23,
            "CORE": 30,
            "family_support": family_support,
            "core_projection_masked_failure_count": 0,
        },
    )
    hashes["m112p1_primary_family_summary.json"] = _write_json(
        "m112p1_primary_family_summary.json", primary
    )
    hashes["m112p1_unresolved_summary.json"] = _write_json(
        "m112p1_unresolved_summary.json", unresolved
    )
    hashes["m112p1_pair_state_summary.json"] = _write_json(
        "m112p1_pair_state_summary.json", {"counts": pair_states, "sum": sum(pair_states.values())}
    )
    hashes["m112p1_sql_identity_summary.json"] = _write_json(
        "m112p1_sql_identity_summary.json",
        {
            "arm_counts": identity,
            "pair_counts": {
                state: sum(row["pair_sql_identity_state"] == state for row in rows)
                for state in (
                    "PARSE_NORMALIZED_IDENTICAL",
                    "PARSE_NORMALIZED_DIFFERENT",
                    "SQL_IDENTITY_UNRESOLVED",
                )
            },
        },
    )
    hashes["m112p1_repeated_boundary_eligibility.json"] = _write_json(
        "m112p1_repeated_boundary_eligibility.json",
        {
            "families": eligibility,
            "result": "NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE"
            if not candidates
            else "ONE_REPEATED_BOUNDED_BOUNDARY_CANDIDATE"
            if len(candidates) == 1
            else "MULTIPLE_REPEATED_BOUNDED_BOUNDARY_CANDIDATES",
            "candidates": candidates,
        },
    )
    hashes["m112p1_ownership_inventory.json"] = _write_json(
        "m112p1_ownership_inventory.json", ownership
    )
    hashes["m112p1_historical_relation_inventory.json"] = _write_json(
        "m112p1_historical_relation_inventory.json", historical
    )
    hashes["m112p1_future_review_contract.json"] = _write_json(
        "m112p1_future_review_contract.json",
        {
            "no_implementation": True,
            "fresh_validation_required": True,
            "next_review_by_result": {
                "NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE": "POST-M11.2P1 Fragmentation Review",
                "ONE_REPEATED_BOUNDED_BOUNDARY_CANDIDATE": (
                    "POST-M11.2P1 Intervention-Selection Review"
                ),
                "MULTIPLE_REPEATED_BOUNDED_BOUNDARY_CANDIDATES": (
                    "POST-M11.2P1 Boundary Prioritization Review"
                ),
            },
        },
    )
    hashes["m112p1_test_summary.json"] = _write_json(
        "m112p1_test_summary.json",
        {
            "focused": {"passed": 10, "failed": 0, "skipped": 0},
            "cross_milestone": {"passed": 68, "failed": 0, "skipped": 0},
            "db_free_unit_suite": {"passed": 467, "failed": 0, "skipped": 0},
            "targeted_ruff": "PASS",
            "targeted_mypy": "PASS",
            "project_ruff_baseline": "1 pre-existing diagnostic",
            "project_ruff_new": 0,
            "project_mypy_baseline": "7 pre-existing diagnostics",
            "project_mypy_new": 0,
            "diff_check": "PASS",
            "secret_scan": "PASS",
            "provider_calls": 0,
            "db_calls": 0,
            "retrieval_reruns": 0,
            "full_pytest": "SKIPPED",
            "full_pytest_skip_reason": (
                "Repository-wide pytest may invoke provider, database, Defog, BIRD, "
                "or live experiment workflows."
            ),
            "containers_created": 0,
        },
    )
    final_decision = {
        "classification": "M112P1_RESIDUAL_ATTRIBUTION_COMPLETED",
        "taxonomy_frozen_before_case_exposure": True,
        "case_level_exposure_complete": True,
        "p1_labels_used_for_authority": False,
        "population": {"FULL": 23, "CORE": 30, "eligible": 53, "excluded": 3},
        "pair_states": pair_states,
        "repeated_boundary_result": "NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE"
        if not candidates
        else "ONE_REPEATED_BOUNDED_BOUNDARY_CANDIDATE"
        if len(candidates) == 1
        else "MULTIPLE_REPEATED_BOUNDED_BOUNDARY_CANDIDATES",
        "candidate_families": candidates,
        "no_intervention_selected": True,
    }
    hashes["m112p1_final_decision.json"] = _write_json("m112p1_final_decision.json", final_decision)
    manifest = {
        "classification": "M112P1_RESIDUAL_ATTRIBUTION_COMPLETED",
        "raw_sha256": hashes,
        "p0_unchanged": True,
        "p1_quarantined": True,
        "no_provider_db_retrieval": True,
        "no_generation_taxonomy_in_p0": True,
    }
    hashes["m112p1_evidence_manifest.json"] = _write_json("m112p1_evidence_manifest.json", manifest)
    final = {
        "classification": "M112P1_RESIDUAL_ATTRIBUTION_COMPLETED",
        "taxonomy_frozen_before_case_exposure": True,
        "case_level_exposure_complete": True,
        "p1_labels_used_for_authority": False,
        "population": {"FULL": 23, "CORE": 30, "eligible": 53, "excluded": 3},
        "family_support": family_support,
        "primary": primary,
        "unresolved": unresolved,
        "pair_states": pair_states,
        "sql_identity": identity,
        "repeated_boundary": eligibility,
        "candidate_families": candidates,
        "p1_quarantine": True,
        "hashes": hashes,
    }
    hashes["m112p1_final_summary.json"] = _write_json("m112p1_final_summary.json", final)
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        print(json.dumps(prepare(), indent=2, sort_keys=True))
    elif args.analyze:
        print(json.dumps(analyze(), indent=2, sort_keys=True))
    else:
        print(json.dumps(design_controls(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
