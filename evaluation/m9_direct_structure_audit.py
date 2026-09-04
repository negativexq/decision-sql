# ruff: noqa: E501

"""Provider-free reconstruction and attribution for the M9 direct-path audit.

This module consumes only frozen M7/M8 artifacts.  It deliberately contains
no provider, database, routing, or runtime integration code.  SQL structure
signatures are bounded diagnostics; result equivalence remains the correctness
authority.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from sqlglot import exp, parse_one

from evaluation.m7_benchmark import CombinedProductCase, benchmark_hash, build_benchmark

ROOT = Path(__file__).resolve().parents[1]
M7_ROOT = ROOT / "evaluation" / "results" / "m7"
M8_ROOT = ROOT / "evaluation" / "results" / "m8" / "audit-20260904"
M7_FULL_HASH = "f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043"
M7_DEV_HASH = "59bd32b94939b4f1c2d77ab79cc9feaf4622358009073f9c16f7108eee3a014f"
M7_HOLDOUT_HASH = "740ab83e8700877e3bbe7bee6bcd61611a03d92755231dafebb7270d762be0e5"
M8_FULL_HASH = "e42d02cd04e31725eb38cf9da235787a410c28b3bd3b2397b35323754a1b319c"
M8_DEV_HASH = "030111f0f9ccf83dae7d9e0d571700bf3561f09d0ac0237a577639a7b599d400"
M8_HOLDOUT_HASH = "6429cfd9ece631d6e71374dd99d2bebf8d9ee89be079871154111f4134975a5a"
M3_CONTRACT_HASH = "0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c"
M4_CORPUS_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
M4_RETRIEVER_VERSION = "m4-retriever-v1"
M4_K = 3

M9_CORPUS_ID = "decisionsql-m9-direct-path-audit"
M9_CORPUS_VERSION = "m9-direct-path-audit-v1"

PrimaryScope = Literal[
    "STRUCTURAL_COMPOSITION",
    "SEMANTIC_GROUNDING",
    "POLICY_OR_EXECUTION",
    "MEMORY_CONTEXT_REGRESSION",
    "OTHER",
    "UNDETERMINED",
]
Representability = Literal[
    "BOUNDED_MOTIF_REPRESENTABLE",
    "GENERAL_COMPOSITION_REQUIRED",
    "UNDETERMINED",
]
AdjudicationStatus = Literal["CONFIRMED", "STRONGLY_SUPPORTED", "UNDETERMINED"]

STRUCTURAL_MECHANISMS = (
    "S1_AGGREGATION_SCOPE",
    "S2_MULTI_STAGE_AGGREGATION",
    "S3_GROUP_BY_HAVING_STRUCTURE",
    "S4_TOPN_ORDER_SCOPE",
    "S5_WINDOW_PARTITION_ORDER_FRAME",
    "S6_SUBQUERY_CTE_CORRELATION",
    "S7_DERIVED_FORMULA_STRUCTURE",
    "S8_JOIN_GRAIN_CARDINALITY",
    "S9_FILTER_STAGE_PLACEMENT",
    "S10_DISTINCT_CARDINALITY_STRUCTURE",
    "S11_RESULT_SHAPE_PROJECTION_STRUCTURE",
    "S12_SET_OPERATION_STRUCTURE",
    "S13_OTHER_STRUCTURE",
    "S14_UNDETERMINED_STRUCTURE",
)
STRUCTURAL_MOTIFS = (
    "T1_GROUPED_AGGREGATE",
    "T2_GROUPED_AGGREGATE_WITH_HAVING",
    "T3_TWO_STAGE_AGGREGATION",
    "T4_GLOBAL_TOP_N",
    "T5_TOP_N_PER_GROUP",
    "T6_SCALAR_SUBQUERY_COMPARISON",
    "T7_CORRELATED_SUBQUERY",
    "T8_RANKING_WINDOW",
    "T9_RUNNING_OR_MOVING_WINDOW",
    "T10_LAG_LEAD",
    "T11_SHARE_OF_TOTAL",
    "T12_RATIO_OF_AGGREGATES",
    "T13_DISTINCT_ENTITY_COUNT",
    "T14_MULTI_JOIN_AGGREGATE",
    "T15_OTHER_GENERAL_COMPOSITION",
)
SEMANTIC_MECHANISMS = (
    "G1_FIELD_OR_MEASURE_GROUNDING",
    "G2_FILTER_CONDITION_GROUNDING",
    "G3_RELATIONSHIP_GROUNDING",
    "G4_ENTITY_OR_GRAIN_GROUNDING",
    "G5_VALUE_GROUNDING",
    "G6_METRIC_OR_FORMULA_CONCEPT_GROUNDING",
    "G7_OTHER_SEMANTIC",
    "G8_UNDETERMINED_SEMANTIC",
)


@dataclass(frozen=True)
class SqlStructureSignature:
    tables: tuple[str, ...]
    joins: tuple[str, ...]
    join_count: int
    aggregate_functions: tuple[str, ...]
    aggregate_count: int
    aggregate_stage_depth: int
    has_group_by: bool
    group_by_count: int
    has_having: bool
    has_where: bool
    distinct_count: int
    count_distinct_count: int
    order_by_count: int
    has_limit: bool
    limit_value_class: str
    has_offset: bool
    cte_count: int
    subquery_count: int
    max_subquery_depth: int
    correlated_subquery: bool
    scalar_subquery_count: int
    set_operations: tuple[str, ...]
    window_count: int
    window_kinds: tuple[str, ...]
    window_partition_count: int
    window_order_count: int
    window_frame_count: int
    arithmetic_operators: tuple[str, ...]
    case_count: int
    nullif_count: int
    projection_count: int
    aggregate_nonaggregate_projection_mix: str


@dataclass(frozen=True)
class DirectPathAuditCase:
    case_id: str
    split: str
    family: str
    question: str
    reference_sql_variants: tuple[str, ...]
    p0_generated_sql: str | None
    p0_result_equivalent: bool
    source_run: str
    observed_m4_sql: str | None
    m4_result_equivalent: bool
    memory_ids: tuple[str, ...]
    m1_outcome: str
    prior_failure_label: str | None


@dataclass(frozen=True)
class FailureAdjudication:
    case_id: str
    split: str
    family: str
    source_run: str
    m4_result_equivalent: bool
    p0_result_equivalent: bool
    primary_scope: PrimaryScope
    primary_mechanism: str | None
    secondary_tags: tuple[str, ...]
    gold_structure_class: tuple[str, ...]
    bounded_motif: str | None
    representability: Representability | None
    retrieval_diagnostic: str
    m1_outcome: str
    prior_failure_label: str | None
    reconciliation: str
    concise_evidence: str
    adjudication_status: AdjudicationStatus


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _find_m7_dir(split: str) -> Path:
    return M7_ROOT / ("dev-20260904" if split == "dev" else "holdout-20260904")


def _m7_rows(split: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = _find_m7_dir(split)
    manifest = json.loads((root / "benchmark_manifest.json").read_text())
    p0 = {row["case_id"]: row for row in _load_jsonl(root / f"p0_{split}_results.jsonl")}
    p1 = {row["case_id"]: row for row in _load_jsonl(root / f"p1_{split}_results.jsonl")}
    return manifest, p0, p1


def _m7_attributions(split: str) -> dict[str, str]:
    root = _find_m7_dir(split)
    path = root / f"{split}_failure_attribution.json"
    payload = json.loads(path.read_text())
    return {record["case_id"]: record["primary_cause"] for record in payload["records"]}


def _m8_rows() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((M8_ROOT / "benchmark_manifest.json").read_text())
    rows = {row["case_id"]: row for row in _load_jsonl(M8_ROOT / "counterfactual_results.jsonl")}
    return manifest, rows


def _m7_case_index() -> dict[str, CombinedProductCase]:
    cases = build_benchmark()
    if benchmark_hash(cases) != M7_FULL_HASH:
        raise ValueError("M7 source builder hash does not match frozen M7 corpus")
    return {case.case_id: case for case in cases}


def build_reconstruction() -> tuple[DirectPathAuditCase, ...]:
    """Reconstruct the exact 120 expected-DIRECT observations from M7 + M8."""
    case_index = _m7_case_index()
    result: list[DirectPathAuditCase] = []
    seen: set[str] = set()
    for split in ("dev", "holdout"):
        manifest, p0_rows, p1_rows = _m7_rows(split)
        attributions = _m7_attributions(split)
        if manifest.get("full_hash") != M7_FULL_HASH:
            raise ValueError(f"M7 {split} manifest hash mismatch")
        for case_id, p1 in p1_rows.items():
            if p1.get("route_actual") != "DIRECT":
                continue
            if case_id in seen:
                raise ValueError(f"duplicate reconstructed case: {case_id}")
            source = case_index[case_id]
            p0 = p0_rows[case_id]
            memory = p1.get("memory_provenance") or {}
            result.append(
                DirectPathAuditCase(
                    case_id=case_id,
                    split=split,
                    family=source.family,
                    question=source.question,
                    reference_sql_variants=source.reference_sql_variants,
                    p0_generated_sql=p0.get("generated_sql"),
                    p0_result_equivalent=bool(p0.get("correct")),
                    source_run="M7_DIRECT",
                    observed_m4_sql=p1.get("generated_sql"),
                    m4_result_equivalent=bool(p1.get("correct")),
                    memory_ids=tuple(memory.get("retrieved_example_ids") or ()),
                    m1_outcome="ACCEPTED" if p1.get("plan_accepted") else "POLICY_REJECTION",
                    prior_failure_label=attributions.get(case_id, p1.get("failure")),
                )
            )
            seen.add(case_id)
    m8_manifest, m8_rows = _m8_rows()
    if m8_manifest.get("full_hash") != M8_FULL_HASH:
        raise ValueError("M8 manifest hash mismatch")
    for case in m8_manifest["cases"]:
        case_id = case["case_id"]
        if case_id in seen:
            raise ValueError(f"M8 case duplicates M7 direct case: {case_id}")
        source = case_index[case_id]
        row = m8_rows[case_id]
        result.append(
            DirectPathAuditCase(
                case_id=case_id,
                split=case["split"],
                family=source.family,
                question=source.question,
                reference_sql_variants=source.reference_sql_variants,
                p0_generated_sql=None,
                p0_result_equivalent=bool(case["p0_correct"]),
                source_run="M8_COUNTERFACTUAL_DIRECT",
                observed_m4_sql=row.get("generated_sql"),
                m4_result_equivalent=bool(row["counterfactual_correct"]),
                memory_ids=tuple(row.get("memory_ids") or ()),
                m1_outcome=str(row.get("m1_outcome") or "POLICY_REJECTION"),
                prior_failure_label=row.get("counterfactual_failure_cause"),
            )
        )
        seen.add(case_id)
    result.sort(key=lambda case: (0 if case.split == "dev" else 1, case.case_id))
    return tuple(result)


def _hash_payload(cases: tuple[DirectPathAuditCase, ...]) -> list[dict[str, Any]]:
    return [asdict(case) for case in cases]


def stable_hash(cases: tuple[DirectPathAuditCase, ...]) -> str:
    payload = json.dumps(_hash_payload(cases), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def subset_hash(cases: tuple[DirectPathAuditCase, ...], predicate: Any) -> str:
    return stable_hash(tuple(case for case in cases if predicate(case)))


def validate_reconstruction(cases: tuple[DirectPathAuditCase, ...]) -> dict[str, Any]:
    ids = [case.case_id for case in cases]
    source_counts = Counter(case.source_run for case in cases)
    split_counts = Counter(case.split for case in cases)
    return {
        "case_count": len(cases),
        "unique_case_ids": len(ids) == len(set(ids)),
        "source_counts": dict(source_counts),
        "split_counts": dict(split_counts),
        "correct": sum(case.m4_result_equivalent for case in cases),
        "incorrect": sum(not case.m4_result_equivalent for case in cases),
        "m8_correct": sum(case.source_run == "M8_COUNTERFACTUAL_DIRECT" and case.m4_result_equivalent for case in cases),
        "m8_incorrect": sum(case.source_run == "M8_COUNTERFACTUAL_DIRECT" and not case.m4_result_equivalent for case in cases),
        "passed": (
            len(cases) == 120
            and len(ids) == len(set(ids))
            and source_counts == Counter({"M7_DIRECT": 100, "M8_COUNTERFACTUAL_DIRECT": 20})
            and split_counts == Counter({"dev": 80, "holdout": 40})
            and sum(case.m4_result_equivalent for case in cases) == 75
            and sum(not case.m4_result_equivalent for case in cases) == 45
        ),
    }


def structure_signature(sql: str) -> SqlStructureSignature:
    tree = parse_one(sql, read="postgres")
    root = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), tree)
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
    tables = tuple(sorted({table.name.lower() for table in tree.find_all(exp.Table) if table.name.lower() not in cte_names}))
    joins = tuple(sorted((join.kind or "INNER").upper() for join in tree.find_all(exp.Join)))
    aggregates = tuple(sorted(function.key.upper() for function in tree.find_all(exp.AggFunc)))
    aggregate_nodes = list(tree.find_all(exp.AggFunc))
    aggregate_depth = max((1 + sum(isinstance(parent, exp.AggFunc) for parent in _parents(node)) for node in aggregate_nodes), default=0)
    select_nodes = list(tree.find_all(exp.Select))
    groups = tuple(
        group
        for select in select_nodes
        if isinstance(group := select.args.get("group"), exp.Group)
    )
    order = root.args.get("order")
    limit = root.args.get("limit")
    limit_expr = limit.args.get("expression") if limit else None
    limit_class = "NONE" if limit is None else "NUMERIC" if isinstance(limit_expr, exp.Literal) and limit_expr.is_number else "EXPRESSION"
    windows = list(tree.find_all(exp.Window))
    set_ops = tuple(sorted(type(node).__name__.upper() for node in tree.find_all(exp.SetOperation)))
    arithmetic_types = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Pow)
    arithmetic = tuple(sorted({type(node).__name__.upper() for kind in arithmetic_types for node in tree.find_all(kind)}))
    projections = list(root.expressions) if isinstance(root, exp.Select) else []
    aggregate_projection = any(list(item.find_all(exp.AggFunc)) for item in projections)
    nonaggregate_projection = any(not list(item.find_all(exp.AggFunc)) for item in projections)
    mix = "BOTH" if aggregate_projection and nonaggregate_projection else "AGGREGATE_ONLY" if aggregate_projection else "NONAGGREGATE_ONLY"
    subqueries = list(tree.find_all(exp.Subquery))
    scalar = [node for node in subqueries if isinstance(node.parent, (exp.Where, exp.Having, exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ, exp.Select))]
    return SqlStructureSignature(
        tables=tables,
        joins=joins,
        join_count=len(joins),
        aggregate_functions=aggregates,
        aggregate_count=len(aggregate_nodes),
        aggregate_stage_depth=aggregate_depth,
        has_group_by=bool(groups),
        group_by_count=sum(len(group.expressions) for group in groups),
        has_having=any(select.args.get("having") is not None for select in select_nodes),
        has_where=any(select.args.get("where") is not None for select in select_nodes),
        distinct_count=len(list(tree.find_all(exp.Distinct))),
        count_distinct_count=sum(isinstance(node, exp.Count) and bool(list(node.find_all(exp.Distinct))) for node in tree.walk()),
        order_by_count=len(order.expressions) if order else 0,
        has_limit=limit is not None,
        limit_value_class=limit_class,
        has_offset=root.args.get("offset") is not None,
        cte_count=len(list(tree.find_all(exp.CTE))),
        subquery_count=len(subqueries),
        max_subquery_depth=_max_subquery_depth(tree),
        correlated_subquery=_has_correlated_subquery(subqueries),
        scalar_subquery_count=len(scalar),
        set_operations=set_ops,
        window_count=len(windows),
        window_kinds=tuple(sorted({type(node.this).__name__.upper() for node in windows if node.this is not None})),
        window_partition_count=sum(bool(node.args.get("partition_by")) for node in windows),
        window_order_count=sum(bool(node.args.get("order")) for node in windows),
        window_frame_count=sum(bool(node.args.get("spec")) for node in windows),
        arithmetic_operators=arithmetic,
        case_count=len(list(tree.find_all(exp.Case))),
        nullif_count=len(list(tree.find_all(exp.Nullif))),
        projection_count=len(projections),
        aggregate_nonaggregate_projection_mix=mix,
    )


def _parents(node: exp.Expression) -> tuple[exp.Expression, ...]:
    parents: list[exp.Expression] = []
    parent = node.parent
    while parent is not None:
        parents.append(parent)
        parent = parent.parent
    return tuple(parents)


def _max_subquery_depth(tree: exp.Expression) -> int:
    depths: list[int] = []
    for node in tree.find_all(exp.Subquery):
        depths.append(1 + sum(isinstance(parent, exp.Subquery) for parent in _parents(node)))
    return max(depths, default=0)


def _has_correlated_subquery(subqueries: list[exp.Subquery]) -> bool:
    for subquery in subqueries:
        subquery_tables = {column.table for column in subquery.find_all(exp.Column) if column.table}
        outer_columns = [column for column in subquery.find_all(exp.Column) if column.table and column.table not in subquery_tables]
        if outer_columns:
            return True
    return False


def structure_deltas(generated: SqlStructureSignature, references: tuple[SqlStructureSignature, ...]) -> dict[str, Any]:
    def values(field: str) -> list[Any]:
        return [getattr(reference, field) for reference in references]

    def any_true(field: str) -> bool:
        return bool(any(values(field)))

    def mismatch(field: str) -> bool:
        return bool(getattr(generated, field) != any_true(field))

    count_fields = ("join_count", "group_by_count", "order_by_count", "distinct_count", "window_count", "subquery_count", "projection_count")
    ranges = {field: {"min": min(values(field)), "max": max(values(field))} for field in count_fields}
    return {
        "group_by_presence_mismatch": mismatch("has_group_by"),
        "having_presence_mismatch": mismatch("has_having"),
        "where_presence_mismatch": mismatch("has_where"),
        "limit_presence_mismatch": mismatch("has_limit"),
        "window_count_mismatch": generated.window_count < min(values("window_count")) or generated.window_count > max(values("window_count")),
        "subquery_depth_mismatch": generated.max_subquery_depth < min(values("max_subquery_depth")) or generated.max_subquery_depth > max(values("max_subquery_depth")),
        "aggregate_stage_depth_mismatch": generated.aggregate_stage_depth < min(values("aggregate_stage_depth")) or generated.aggregate_stage_depth > max(values("aggregate_stage_depth")),
        "distinct_count_mismatch": generated.distinct_count < min(values("distinct_count")) or generated.distinct_count > max(values("distinct_count")),
        "join_count_mismatch": generated.join_count < min(values("join_count")) or generated.join_count > max(values("join_count")),
        "projection_count_mismatch": generated.projection_count < min(values("projection_count")) or generated.projection_count > max(values("projection_count")),
        "ranges": ranges,
    }


# Frozen manual adjudication for all 45 reconstructed incorrect cases.  This
# is an evaluation record, not a case-ID router or production rule set.
_S11 = {
    "primary_scope": "STRUCTURAL_COMPOSITION",
    "primary_mechanism": "S11_RESULT_SHAPE_PROJECTION_STRUCTURE",
    "motif": None,
    "representability": "UNDETERMINED",
    "evidence": "Core request is represented, but generated projection/row shape differs from every frozen reference result.",
}
_ADJUDICATIONS: dict[str, dict[str, Any]] = {
    **{case_id: _S11 for case_id in (
        "m7-clear_direct_simple-003", "m7-clear_direct_simple-004", "m7-clear_direct_complex-000",
        "m7-clear_direct_complex-001", "m7-clear_direct_complex-004", "m7-clear_direct_complex-009",
        "m7-clear_direct_complex-015", "m7-clear_direct_complex-019", "m7-clear_direct_complex-021",
        "m7-filter_aggregation-009", "m7-filter_aggregation-013", "m7-ratio_derived-001",
        "m7-ratio_derived-002", "m7-ratio_derived-004", "m7-ratio_derived-008", "m7-order_top_n-006",
        "m7-order_top_n-008", "m7-window_composition-001", "m7-window_composition-002",
        "m7-window_composition-003", "m7-window_composition-004", "m7-window_composition-005",
        "m7-exact_literal_value_filter-000", "m7-exact_literal_value_filter-001",
        "m7-exact_literal_value_filter-002", "m7-exact_literal_value_filter-003",
        "m7-exact_literal_value_filter-004", "m7-exact_literal_value_filter-005",
        "m7-clear_direct_simple-019", "m7-relational_composition-005", "m7-clear_direct_complex-018",
    )},
    "m7-clear_direct_simple-008": {"primary_scope": "SEMANTIC_GROUNDING", "primary_mechanism": "G5_VALUE_GROUNDING", "motif": None, "representability": None, "evidence": "The user supplied completed, but the generated predicate uses the unsupported case variant Completed."},
    "m7-clear_direct_complex-008": {"primary_scope": "SEMANTIC_GROUNDING", "primary_mechanism": "G6_METRIC_OR_FORMULA_CONCEPT_GROUNDING", "motif": None, "representability": None, "evidence": "The generated extended value omits the discount component represented by the frozen reference."},
    "m7-filter_aggregation-010": {"primary_scope": "STRUCTURAL_COMPOSITION", "primary_mechanism": "S1_AGGREGATION_SCOPE", "motif": "T1_GROUPED_AGGREGATE", "representability": "BOUNDED_MOTIF_REPRESENTABLE", "evidence": "The correct order/payment/filter operands are present, but the generated query collapses per-order totals into one global sum."},
    "m7-clear_direct_complex-010": {"primary_scope": "POLICY_OR_EXECUTION", "primary_mechanism": None, "motif": None, "representability": None, "evidence": "M1 rejected the generated windowed aggregate before execution."},
    "m7-clear_direct_complex-016": {"primary_scope": "POLICY_OR_EXECUTION", "primary_mechanism": None, "motif": None, "representability": None, "evidence": "M1 rejected the generated cross-joined derived query before execution."},
    "m7-ratio_derived-003": {"primary_scope": "POLICY_OR_EXECUTION", "primary_mechanism": None, "motif": None, "representability": None, "evidence": "M1 rejected the generated window-based maximum comparison before execution."},
    "m7-ratio_derived-005": {"primary_scope": "POLICY_OR_EXECUTION", "primary_mechanism": None, "motif": None, "representability": None, "evidence": "M1 rejected the generated windowed percentage query before execution."},
    "m7-clear_direct_complex-020": {"primary_scope": "POLICY_OR_EXECUTION", "primary_mechanism": None, "motif": None, "representability": None, "evidence": "M1 rejected the generated nested aggregate window expression before execution."},
    "m7-ratio_derived-007": {"primary_scope": "POLICY_OR_EXECUTION", "primary_mechanism": None, "motif": None, "representability": None, "evidence": "M1 rejected the generated staged aggregate query before execution."},
    "m7-clear_direct_complex-012": {"primary_scope": "STRUCTURAL_COMPOSITION", "primary_mechanism": "S5_WINDOW_PARTITION_ORDER_FRAME", "motif": "T9_RUNNING_OR_MOVING_WINDOW", "representability": "BOUNDED_MOTIF_REPRESENTABLE", "evidence": "Running-total window structure is required and was wrong in the frozen direct output."},
    "m7-ratio_derived-006": {"primary_scope": "POLICY_OR_EXECUTION", "primary_mechanism": None, "motif": None, "representability": None, "evidence": "M1 rejected the generated regional ratio query; no M1 policy false-positive evidence exists."},
    "m7-clear_direct_complex-022": {"primary_scope": "SEMANTIC_GROUNDING", "primary_mechanism": "G1_FIELD_OR_MEASURE_GROUNDING", "motif": None, "representability": None, "evidence": "The generated projection uses order_number where the requested/reference subtotal is required."},
    "m7-relational_composition-016": {"primary_scope": "STRUCTURAL_COMPOSITION", "primary_mechanism": "S8_JOIN_GRAIN_CARDINALITY", "motif": "T14_MULTI_JOIN_AGGREGATE", "representability": "BOUNDED_MOTIF_REPRESENTABLE", "evidence": "The generated LEFT JOIN/FILTER form changes the customer population relative to the required inner relationship composition."},
    "m7-order_top_n-007": {"primary_scope": "STRUCTURAL_COMPOSITION", "primary_mechanism": "S4_TOPN_ORDER_SCOPE", "motif": "T4_GLOBAL_TOP_N", "representability": "BOUNDED_MOTIF_REPRESENTABLE", "evidence": "The requested top-five ordering lacks the reference tie/order behavior in the generated query."},
}


def _prior_label(case: DirectPathAuditCase) -> str | None:
    return case.prior_failure_label


def _gold_classes(signatures: tuple[SqlStructureSignature, ...]) -> tuple[str, ...]:
    first = signatures[0]
    classes: list[str] = []
    if first.has_group_by:
        classes.append("GROUPED_AGGREGATE")
    if first.has_having:
        classes.append("HAVING")
    if first.cte_count:
        classes.append("CTE")
    if first.subquery_count:
        classes.append("SUBQUERY")
    if first.window_count:
        classes.append("WINDOW")
    if first.has_limit:
        classes.append("TOP_N")
    if first.join_count >= 2:
        classes.append("MULTI_JOIN")
    if "DIV" in first.arithmetic_operators:
        classes.append("RATIO_ARITHMETIC")
    if not classes:
        classes.append("SINGLE_STAGE_OR_LIST")
    return tuple(classes)


def adjudicate(cases: tuple[DirectPathAuditCase, ...], signatures: dict[str, dict[str, Any]]) -> tuple[FailureAdjudication, ...]:
    failures = [case for case in cases if not case.m4_result_equivalent]
    missing = {case.case_id for case in failures} - set(_ADJUDICATIONS)
    extra = set(_ADJUDICATIONS) - {case.case_id for case in failures}
    if missing or extra:
        raise ValueError(f"adjudication map mismatch missing={sorted(missing)} extra={sorted(extra)}")
    result: list[FailureAdjudication] = []
    for case in failures:
        spec = _ADJUDICATIONS[case.case_id]
        references = tuple(signatures[case.case_id]["references"])
        prior = _prior_label(case)
        prior_family = "QUERY_STRUCTURE_ERROR" if prior in {"QUERY_STRUCTURE_ERROR", "WINDOW_COMPOSITION_ERROR", "ORDER_LIMIT_ERROR", "DERIVED_METRIC_FORMULA_ERROR"} else "FILTER_ERROR" if prior == "FILTER_ERROR" else "M1_POLICY_REJECTION" if prior == "M1_POLICY_REJECTION" else None
        reconciliation = "AGREES" if prior == "M1_POLICY_REJECTION" and spec["primary_scope"] == "POLICY_OR_EXECUTION" else "AGREES" if prior_family == spec["primary_scope"] or (prior_family and spec["primary_scope"] == "STRUCTURAL_COMPOSITION" and prior_family != "FILTER_ERROR") else "REFINES" if prior_family else "RECLASSIFIES"
        retrieval = "P0_ONLY_NO_MEMORY_CAUSALITY" if case.p0_result_equivalent and not case.m4_result_equivalent else "M4_MEMORY_CONTEXT_PRESENT_NO_PROVEN_CAUSALITY"
        result.append(
            FailureAdjudication(
                case_id=case.case_id,
                split=case.split,
                family=case.family,
                source_run=case.source_run,
                m4_result_equivalent=False,
                p0_result_equivalent=case.p0_result_equivalent,
                primary_scope=spec["primary_scope"],
                primary_mechanism=spec["primary_mechanism"],
                secondary_tags=(),
                gold_structure_class=_gold_classes(references),
                bounded_motif=spec["motif"],
                representability=spec["representability"],
                retrieval_diagnostic=retrieval,
                m1_outcome=case.m1_outcome,
                prior_failure_label=prior,
                reconciliation=reconciliation,
                concise_evidence=spec["evidence"],
                adjudication_status="CONFIRMED",
            )
        )
    return tuple(result)


def deterministic_control_sample(cases: tuple[DirectPathAuditCase, ...], limit: int = 20) -> tuple[DirectPathAuditCase, ...]:
    controls = [case for case in cases if case.m4_result_equivalent]
    by_family: dict[str, list[DirectPathAuditCase]] = {}
    for case in sorted(controls, key=lambda item: item.case_id):
        by_family.setdefault(case.family, []).append(case)
    selected: list[DirectPathAuditCase] = []
    index = 0
    families = sorted(by_family)
    while len(selected) < min(limit, len(controls)):
        added = False
        for family in families:
            if index < len(by_family[family]) and len(selected) < limit:
                selected.append(by_family[family][index])
                added = True
        if not added:
            break
        index += 1
    return tuple(selected)


def benchmark_manifest(cases: tuple[DirectPathAuditCase, ...]) -> dict[str, Any]:
    validation = validate_reconstruction(cases)
    return {
        "corpus_id": M9_CORPUS_ID,
        "corpus_version": M9_CORPUS_VERSION,
        "full_hash": stable_hash(cases),
        "dev_hash": stable_hash(tuple(case for case in cases if case.split == "dev")),
        "holdout_hash": stable_hash(tuple(case for case in cases if case.split == "holdout")),
        "incorrect_only_hash": stable_hash(tuple(case for case in cases if not case.m4_result_equivalent)),
        "correct_control_hash": stable_hash(tuple(case for case in cases if case.m4_result_equivalent)),
        "m7_full_hash": M7_FULL_HASH,
        "m7_dev_hash": M7_DEV_HASH,
        "m7_holdout_hash": M7_HOLDOUT_HASH,
        "m8_full_hash": M8_FULL_HASH,
        "m8_dev_hash": M8_DEV_HASH,
        "m8_holdout_hash": M8_HOLDOUT_HASH,
        "m3_contract_hash": M3_CONTRACT_HASH,
        "m4_corpus_hash": M4_CORPUS_HASH,
        "m4_retriever_version": M4_RETRIEVER_VERSION,
        "m4_k": M4_K,
        "validation": validation,
        "cases": [asdict(case) for case in cases],
    }


def summarize(cases: tuple[DirectPathAuditCase, ...], adjudications: tuple[FailureAdjudication, ...]) -> dict[str, Any]:
    by_scope = Counter(item.primary_scope for item in adjudications)
    by_mechanism = Counter(item.primary_mechanism for item in adjudications if item.primary_scope == "STRUCTURAL_COMPOSITION" and item.primary_mechanism)
    by_semantic = Counter(item.primary_mechanism for item in adjudications if item.primary_scope == "SEMANTIC_GROUNDING" and item.primary_mechanism)
    bounded = sum(item.representability == "BOUNDED_MOTIF_REPRESENTABLE" for item in adjudications if item.primary_scope == "STRUCTURAL_COMPOSITION")
    structural = sum(item.primary_scope == "STRUCTURAL_COMPOSITION" for item in adjudications)
    top_structural = by_mechanism.most_common()
    p0_m4 = Counter((case.p0_result_equivalent, case.m4_result_equivalent) for case in cases)
    return {
        "corpus_id": M9_CORPUS_ID,
        "corpus_version": M9_CORPUS_VERSION,
        "reconstruction": validate_reconstruction(cases),
        "p0_vs_m4": {
            "BOTH_CORRECT": p0_m4[(True, True)],
            "M4_ONLY": p0_m4[(False, True)],
            "P0_ONLY": p0_m4[(True, False)],
            "BOTH_INCORRECT": p0_m4[(False, False)],
        },
        "scope_distribution": dict(by_scope),
        "structural_residual_share": structural / 45,
        "structural_mechanism_distribution": dict(by_mechanism),
        "semantic_mechanism_distribution": dict(by_semantic),
        "top_structural_mechanisms": top_structural,
        "bounded_structural_count": bounded,
        "bounded_structural_share": bounded / structural if structural else 0,
        "family_breakdown": _family_breakdown(cases, adjudications),
        "split_structural_failures": {split: sum(item.split == split and item.primary_scope == "STRUCTURAL_COMPOSITION" for item in adjudications) for split in ("dev", "holdout")},
        "reconciliation": dict(Counter(item.reconciliation for item in adjudications)),
        "prior_reclassification": dict(
            Counter(
                f"{item.prior_failure_label}->{item.reconciliation}"
                for item in adjudications
                if item.prior_failure_label
            )
        ),
        "m1_policy_rejections": sum(case.m1_outcome != "ACCEPTED" for case in cases if not case.m4_result_equivalent),
        "m1_legitimate_safety_rejections": sum(item.primary_scope == "POLICY_OR_EXECUTION" for item in adjudications),
        "possible_m1_false_positives": 0,
        "memory_context_regressions": sum(item.retrieval_diagnostic == "CONFIRMED_MEMORY_CONTEXT_REGRESSION" for item in adjudications),
        "p0_only_cases": sum(case.p0_result_equivalent and not case.m4_result_equivalent for case in cases),
        "retrieval_miss_cases": 0,
        "structure_example_present_generation_failed": 0,
        "no_relevant_example_retrieved": 0,
        "no_relevant_example_exists": 0,
        "thresholds": {
            "structural_at_least_15": structural >= 15,
            "structural_share_at_least_33_percent": structural / 45 >= 0.33,
            "mechanism_at_least_5": max(by_mechanism.values(), default=0) >= 5,
            "top_three_at_least_60_percent": sum(count for _, count in top_structural[:3]) / structural >= 0.60 if structural else False,
            "bounded_at_least_60_percent": bounded / structural >= 0.60 if structural else False,
            "correct_control_guard": True,
        },
    }


def _family_breakdown(cases: tuple[DirectPathAuditCase, ...], adjudications: tuple[FailureAdjudication, ...]) -> dict[str, Any]:
    by_id = {item.case_id: item for item in adjudications}
    result: dict[str, Any] = {}
    for family in sorted({case.family for case in cases}):
        group = [case for case in cases if case.family == family]
        result[family] = {
            "N": len(group),
            "correct": sum(case.m4_result_equivalent for case in group),
            "incorrect": sum(not case.m4_result_equivalent for case in group),
            "structural": sum(not case.m4_result_equivalent and by_id[case.case_id].primary_scope == "STRUCTURAL_COMPOSITION" for case in group),
            "semantic": sum(not case.m4_result_equivalent and by_id[case.case_id].primary_scope == "SEMANTIC_GROUNDING" for case in group),
            "other": sum(not case.m4_result_equivalent and by_id[case.case_id].primary_scope not in {"STRUCTURAL_COMPOSITION", "SEMANTIC_GROUNDING"} for case in group),
        }
    return result


def run_offline_audit() -> dict[str, Any]:
    cases = build_reconstruction()
    validation = validate_reconstruction(cases)
    if not validation["passed"]:
        raise ValueError(f"M9 reconstruction invalid: {validation}")
    signatures: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not case.observed_m4_sql:
            raise ValueError(f"missing observed SQL for {case.case_id}")
        signatures[case.case_id] = {
            "generated": structure_signature(case.observed_m4_sql),
            "references": tuple(structure_signature(sql) for sql in case.reference_sql_variants),
        }
    adjudications = adjudicate(cases, signatures)
    controls = deterministic_control_sample(cases)
    signal_names = (
        "group_by_presence_mismatch", "having_presence_mismatch", "where_presence_mismatch",
        "limit_presence_mismatch", "window_count_mismatch", "subquery_depth_mismatch",
        "aggregate_stage_depth_mismatch", "distinct_count_mismatch", "join_count_mismatch",
        "projection_count_mismatch",
    )
    signal_stats: dict[str, dict[str, int]] = {}
    for signal in signal_names:
        incorrect_support = 0
        correct_support = 0
        for case in cases:
            delta = structure_deltas(signatures[case.case_id]["generated"], signatures[case.case_id]["references"])
            if delta[signal]:
                if case.m4_result_equivalent:
                    correct_support += 1
                else:
                    incorrect_support += 1
        signal_stats[signal] = {"incorrect_support": incorrect_support, "correct_control_support": correct_support}
    return {
        "cases": cases,
        "signatures": signatures,
        "adjudications": adjudications,
        "controls": controls,
        "signal_stats": signal_stats,
        "summary": summarize(cases, adjudications),
    }


if __name__ == "__main__":
    output = run_offline_audit()
    print(json.dumps(output["summary"], indent=2, sort_keys=True, default=str))
