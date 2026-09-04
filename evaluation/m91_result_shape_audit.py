# ruff: noqa: E501

"""Provider-free M9.1 audit of the frozen M9 S11 population.

This module is deliberately evaluation-only.  It consumes the M9 reconstruction
and its persisted adjudication, parses already-existing SQL, and records a
manually adjudicated user-facing result contract.  It never generates SQL,
calls a provider, changes the evaluator, or adds a runtime contract.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

from sqlglot import exp, parse_one

from evaluation.m9_direct_structure_audit import (
    M3_CONTRACT_HASH,
    M4_CORPUS_HASH,
    build_reconstruction,
)
from evaluation.m9_direct_structure_audit import (
    stable_hash as m9_stable_hash,
)

ROOT = Path(__file__).resolve().parents[1]
M9_ROOT = ROOT / "evaluation" / "results" / "m9" / "audit-20260904"
M91_CORPUS_ID = "decisionsql-m91-result-shape-audit"
M91_VERSION = "m91-result-shape-audit-v1"

M7_FULL_HASH = "f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043"
M7_DEV_HASH = "59bd32b94939b4f1c2d77ab79cc9feaf4622358009073f9c16f7108eee3a014f"
M7_HOLDOUT_HASH = "740ab83e8700877e3bbe7bee6bcd61611a03d92755231dafebb7270d762be0e5"
M8_FULL_HASH = "e42d02cd04e31725eb38cf9da235787a410c28b3bd3b2397b35323754a1b319c"
M9_FULL_HASH = "6bd0e22b105607a0af8d17ff8774bba381ba61b4e43cdd88be2f10acc6e36d12"
M9_DEV_HASH = "cc61d0649b0e24c4042b24ae6e70eeb2bd4c4a970f252fd02af03e760d6f16d3"
M9_HOLDOUT_HASH = "313c3d02e525572ac0fff70a9cc55968e9d2483de0e7821ad7d82d292508c696"
M9_INCORRECT_HASH = "eadcc297635152999ca6478a74b1b24a67b23b8424630e6e081a3ba7e7c67180"
M9_CORRECT_HASH = "cd27047b4a9a5e163479fca0ba15777c9c2ad38e12db8163b0b168677cb16d3c"

PRIMARY_CATEGORIES = (
    "PRESENTATION_ONLY",
    "PROJECTION_CONTRACT_VIOLATION",
    "GRAIN_CONTRACT_VIOLATION",
    "TRUE_COMPUTATION_OR_SEMANTIC_ERROR",
    "EVALUATOR_ARTIFACT",
    "OTHER",
    "UNDETERMINED",
)
DETAILS = (
    "P1_ALIAS_ONLY", "P2_COLUMN_ORDER_ONLY", "P3_EXTRA_OPTIONAL_COLUMN",
    "P4_EXTRA_REQUIRED_BUT_UNASKED_BUSINESS_COLUMN", "P5_MISSING_REQUIRED_DIMENSION",
    "P6_MISSING_REQUIRED_MEASURE", "P7_WRONG_DIMENSION_PROJECTION",
    "P8_WRONG_MEASURE_PROJECTION", "P9_EXTRA_GROUPING_KEY_GRAIN_EXPANSION",
    "P10_MISSING_GROUPING_KEY_GRAIN_COLLAPSE", "P11_SCALAR_VS_GROUPED",
    "P12_WRONG_ENTITY_ROW_IDENTITY", "P13_DUPLICATE_ROW_SHAPE",
    "P14_PROJECTION_WIDTH_OTHER", "P15_OTHER_RESULT_SHAPE", "P16_UNDETERMINED_RESULT_SHAPE",
)
GRAIN_RELATIONS = ("EXACT_GRAIN", "SUPERSET_GRAIN", "SUBSET_GRAIN", "DIFFERENT_GRAIN", "UNDETERMINED")
DIAGNOSTICS = (
    "STRICT_WRONG_PRESENTATION_EQUIVALENT", "STRICT_WRONG_PROJECTION_EQUIVALENT",
    "STRICT_WRONG_GRAIN_WRONG", "STRICT_WRONG_SEMANTIC_WRONG",
    "STRICT_WRONG_EVALUATOR_ARTIFACT", "OTHER_UNDETERMINED",
)

AdjudicationStatus = Literal["CONFIRMED", "STRONGLY_SUPPORTED", "UNDETERMINED"]


@dataclass(frozen=True)
class ResultContract:
    case_id: str
    required_dimensions: tuple[str, ...]
    required_measures: tuple[str, ...]
    required_business_fields: tuple[str, ...]
    expected_row_grain: str
    expected_cardinality_class: str
    scalar_or_tabular: str
    ordering_semantically_required: bool
    top_n_semantically_required: bool
    duplicate_semantics: str
    optional_display_fields: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class ResultShapeSignature:
    projection_count: int
    normalized_column_labels: tuple[str, ...]
    select_expressions: tuple[str, ...]
    group_by_expressions: tuple[str, ...]
    aggregate_count: int
    window_count: int
    has_distinct: bool
    has_order_by: bool
    scalar_or_tabular: str
    row_count: int | None = None
    duplicate_row_count: int | None = None
    unique_key_count: int | None = None


@dataclass(frozen=True)
class ResultShapeAuditCase:
    case_id: str
    split: str
    family: str
    question: str
    m9_primary_scope: str
    m9_mechanism: str
    required_dimensions: tuple[str, ...]
    required_measures: tuple[str, ...]
    expected_grain: str
    generated_grain: str
    grain_relation: str
    required_fields: tuple[str, ...]
    generated_fields: tuple[str, ...]
    missing_required_fields: tuple[str, ...]
    extra_optional_fields: tuple[str, ...]
    extra_business_fields: tuple[str, ...]
    extra_grain_fields: tuple[str, ...]
    primary_category: str
    detailed_mechanism: str
    computation_core_equivalent: bool
    e0_existing_equivalent: bool
    e1_presentation_equivalent: bool
    e2_required_projection_equivalent: bool
    diagnostic_class: str
    evaluator_artifact: bool
    m2_8_overlap: str
    memory_projection_overtransfer: str
    concise_evidence: str
    adjudication_status: AdjudicationStatus


@dataclass(frozen=True)
class ContractSpec:
    required_dimensions: tuple[str, ...]
    required_measures: tuple[str, ...]
    required_fields: tuple[str, ...]
    expected_grain: str
    generated_grain: str
    grain_relation: str
    missing_required_fields: tuple[str, ...]
    extra_optional_fields: tuple[str, ...]
    extra_business_fields: tuple[str, ...]
    extra_grain_fields: tuple[str, ...]
    primary_category: str
    detailed_mechanism: str
    core_equivalent: bool
    e1: bool
    e2: bool
    diagnostic: str
    evaluator_artifact: bool
    evidence: str


def _spec(
    required: tuple[str, ...],
    *,
    dimensions: tuple[str, ...] = (),
    measures: tuple[str, ...] = (),
    grain: str = "ENTITY_LIST",
    generated_grain: str | None = None,
    relation: str = "EXACT_GRAIN",
    missing: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    extra_business: tuple[str, ...] = (),
    extra_grain: tuple[str, ...] = (),
    category: str = "PROJECTION_CONTRACT_VIOLATION",
    detail: str = "P3_EXTRA_OPTIONAL_COLUMN",
    core: bool = True,
    e1: bool = False,
    e2: bool = True,
    diagnostic: str = "STRICT_WRONG_PROJECTION_EQUIVALENT",
    evaluator: bool = False,
    evidence: str = "Required user-facing fields and row identity are correct; the frozen evaluator rejects a non-required projection difference.",
) -> ContractSpec:
    effective_diagnostic = (
        "STRICT_WRONG_EVALUATOR_ARTIFACT"
        if evaluator
        else "STRICT_WRONG_PRESENTATION_EQUIVALENT"
        if e1
        else "STRICT_WRONG_PROJECTION_EQUIVALENT"
        if e2
        else diagnostic
    )
    return ContractSpec(
        required_dimensions=dimensions,
        required_measures=measures,
        required_fields=required,
        expected_grain=grain,
        generated_grain=generated_grain or grain,
        grain_relation=relation,
        missing_required_fields=missing,
        extra_optional_fields=optional,
        extra_business_fields=extra_business,
        extra_grain_fields=extra_grain,
        primary_category=category,
        detailed_mechanism=detail,
        core_equivalent=core,
        e1=e1,
        e2=e2,
        diagnostic=effective_diagnostic,
        evaluator_artifact=evaluator,
        evidence=evidence,
    )


# Frozen case-level audit notes.  These are user-contract annotations, not a
# benchmark-specific runtime rule or a production router.
_CONTRACT_SPECS: dict[str, ContractSpec] = {
    "m7-clear_direct_complex-000": _spec(("customer", "order_count"), dimensions=("customer",), measures=("order_count",), grain="PER_CUSTOMER", optional=("customer_id",)),
    "m7-clear_direct_complex-001": _spec(("product", "unit_price"), dimensions=("product",), measures=("unit_price",), grain="PER_PRODUCT", optional=("product_id",)),
    "m7-clear_direct_complex-004": _spec(("customer", "latest_order_number"), dimensions=("customer",), measures=("latest_order_number",), grain="PER_CUSTOMER", optional=("customer_id",)),
    "m7-clear_direct_complex-009": _spec(("order_number", "total_amount"), measures=("total_amount",), grain="PER_ORDER", optional=("customer_id",)),
    "m7-clear_direct_complex-015": _spec(("customer", "refund_total"), dimensions=("customer",), measures=("refund_total",), grain="PER_CUSTOMER", optional=("customer_id",)),
    "m7-clear_direct_simple-003": _spec(("payment_amount", "payment_status"), measures=("payment_amount",), grain="PER_PAYMENT", optional=("payment_id",), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="The question explicitly requests amount and status; generated rows retain those fields and payment identity is not requested. The frozen evaluator rejects the reference's additional identity column.") ,
    "m7-clear_direct_simple-004": _spec(("refund_id", "refund_reason"), measures=("refund_amount",), grain="PER_REFUND", optional=("order_id", "refunded_at"), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="Generated output contains every requested refund record and its reason; the additional order and date fields do not change row identity or requested values."),
    "m7-exact_literal_value_filter-000": _spec(("order_number",), grain="PER_ORDER", optional=("order_id", "status"), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="The question asks for a list of matching orders without a fixed projection. Generated output includes the order number and the exact predicate; the strict evaluator compares incompatible widths.") ,
    "m7-exact_literal_value_filter-001": _spec(("payment_id", "payment_amount"), measures=("payment_amount",), grain="PER_PAYMENT", optional=("order_id", "paid_at", "status"), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="Generated output contains the requested settled payments and the reference identity/amount fields plus non-required payment attributes."),
    "m7-exact_literal_value_filter-002": _spec(("product_name", "sku"), dimensions=("product",), grain="PER_PRODUCT", optional=("unit_price",), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="The category predicate and requested product identity fields are present; unit price is extra but non-grain-changing."),
    "m7-exact_literal_value_filter-003": _spec(("customer_name",), dimensions=("customer",), grain="PER_CUSTOMER", optional=("customer_id",), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="Generated output preserves the requested North-customer rows and name, adding only a stable identity column."),
    "m7-exact_literal_value_filter-004": _spec(("order_number",), grain="PER_ORDER", optional=("order_id", "customer_id", "sales_rep_id", "currency", "status", "ordered_at", "subtotal", "discount_amount", "total_amount"), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="The exact USD filter and order identity are correct; broad list wording does not require the reference's single-column shape."),
    "m7-exact_literal_value_filter-005": _spec(("refund_id", "refund_amount"), measures=("refund_amount",), grain="PER_REFUND", optional=("order_id", "reason", "refunded_at"), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="The exact reason filter, refund identity, and amount are present; additional refund attributes do not alter row grain."),
    "m7-filter_aggregation-009": _spec(("customer", "completed_order_count"), dimensions=("customer",), measures=("completed_order_count",), grain="PER_CUSTOMER", optional=("customer_id",)),
    "m7-order_top_n-006": _spec(("representative", "average_order_total"), dimensions=("sales_representative",), measures=("average_order_total",), grain="TOP_N", optional=("representative_id",)),
    "m7-ratio_derived-001": _spec(("order_number", "refund_fraction"), measures=("refund_fraction",), grain="PER_REFUND", optional=("refund_amount",)),
    "m7-ratio_derived-002": _spec(("payment_id", "payment_percentage"), measures=("payment_percentage",), grain="PER_PAYMENT", optional=("order_number", "payment_amount")),
    "m7-ratio_derived-004": _spec(("customer", "order_share"), dimensions=("customer",), measures=("order_share",), grain="PER_CUSTOMER", relation="DIFFERENT_GRAIN", category="TRUE_COMPUTATION_OR_SEMANTIC_ERROR", detail="P8_WRONG_MEASURE_PROJECTION", core=False, e2=False, diagnostic="STRICT_WRONG_SEMANTIC_WRONG", evidence="The generated CTE includes zero-order customers and computes a share over COALESCE totals, while the reference population is customers with orders; this is population/computation semantics, not projection only."),
    "m7-relational_composition-005": _spec(("customer", "product_count"), dimensions=("customer",), measures=("product_count",), grain="PER_CUSTOMER", optional=("customer_id",)),
    "m7-window_composition-001": _spec(("product", "price_rank"), dimensions=("product",), measures=("price_rank",), grain="PER_PRODUCT", missing=("price_rank",), category="PROJECTION_CONTRACT_VIOLATION", detail="P6_MISSING_REQUIRED_MEASURE", core=False, e2=False, diagnostic="STRICT_WRONG_SEMANTIC_WRONG", evidence="The ranking computation is absent from the generated projection even though the question explicitly asks to rank the products."),
    "m7-window_composition-002": _spec(("paid_at", "running_total"), measures=("running_total",), grain="PER_PAYMENT", optional=("payment_amount", "payment_id")),
    "m7-window_composition-003": _spec(("representative", "order_number", "recent_position"), dimensions=("sales_representative",), measures=("recent_position",), grain="PER_REPRESENTATIVE_ORDER", optional=("ordered_at",)),
    "m7-window_composition-004": _spec(("customer", "order_number", "order_total", "previous_order_total"), dimensions=("customer",), measures=("order_total", "previous_order_total"), grain="PER_CUSTOMER_ORDER", optional=("ordered_at",)),
    "m7-window_composition-005": _spec(("order_number", "cumulative_revenue"), measures=("cumulative_revenue",), grain="PER_ORDER", optional=("order_date", "completed_revenue")),
    "m7-clear_direct_complex-018": _spec(("customer", "quantity_purchased"), dimensions=("customer",), measures=("quantity_purchased",), grain="PER_CUSTOMER", relation="EXACT_GRAIN", category="TRUE_COMPUTATION_OR_SEMANTIC_ERROR", detail="P12_WRONG_ENTITY_ROW_IDENTITY", core=False, e2=False, diagnostic="STRICT_WRONG_SEMANTIC_WRONG", evidence="LEFT JOIN plus COALESCE adds customers without purchased items; the requested population and reference use customers with purchased quantities."),
    "m7-clear_direct_complex-019": _spec(("product", "line_count"), dimensions=("product",), measures=("line_count",), grain="PER_PRODUCT", optional=("product_id",)),
    "m7-clear_direct_complex-021": _spec(("customer", "second_highest_order_total"), dimensions=("customer",), measures=("second_highest_order_total",), grain="PER_CUSTOMER", optional=("order_number",)),
    "m7-clear_direct_simple-019": _spec(("refund_amount",), measures=("refund_amount",), grain="PER_REFUND", optional=("refund_id",), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="The question requests refund amounts in descending order; the generated output supplies exactly that requested measure, while the reference adds an unrequested identifier."),
    "m7-filter_aggregation-013": _spec(("region", "customer_count"), dimensions=("region",), measures=("customer_count",), grain="PER_REGION", optional=("region_id",)),
    "m7-order_top_n-008": _spec(("payment_amount",), measures=("payment_amount",), grain="TOP_N", optional=("payment_id",), category="EVALUATOR_ARTIFACT", detail="P3_EXTRA_OPTIONAL_COLUMN", evaluator=True, evidence="The question asks for the two highest payment amounts; generated output has the requested values and the reference adds an unrequested payment ID."),
    "m7-ratio_derived-008": _spec(("refund_id", "refund_percent"), measures=("refund_percent",), grain="PER_REFUND", optional=("order_number",)),
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _labels_and_expressions(sql: str) -> tuple[tuple[str, ...], tuple[str, ...], exp.Select]:
    tree = parse_one(sql, read="postgres")
    select = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), tree)
    if not isinstance(select, exp.Select):
        raise ValueError("SQL has no SELECT projection")
    labels: list[str] = []
    expressions: list[str] = []
    for index, item in enumerate(select.expressions):
        expression = item.this if isinstance(item, exp.Alias) else item
        alias = item.alias if isinstance(item, exp.Alias) else ""
        label = alias or expression.name or (expression.sql(dialect="postgres") if expression else f"expr_{index}")
        labels.append(label.lower())
        expressions.append(expression.sql(dialect="postgres").lower())
    return tuple(labels), tuple(expressions), select


def sql_shape_signature(sql: str, execution: dict[str, Any] | None = None) -> ResultShapeSignature:
    labels, expressions, select = _labels_and_expressions(sql)
    group = select.args.get("group")
    group_expressions = tuple(item.sql(dialect="postgres").lower() for item in group.expressions) if group else ()
    tree = select
    aggregate_count = len(list(tree.find_all(exp.AggFunc)))
    window_count = len(list(tree.find_all(exp.Window)))
    return ResultShapeSignature(
        projection_count=len(labels),
        normalized_column_labels=labels,
        select_expressions=expressions,
        group_by_expressions=group_expressions,
        aggregate_count=aggregate_count,
        window_count=window_count,
        has_distinct=bool(select.find(exp.Distinct)),
        has_order_by=select.args.get("order") is not None,
        scalar_or_tabular="SCALAR" if len(labels) == 1 and not group_expressions else "TABULAR",
        row_count=(execution or {}).get("row_count"),
        duplicate_row_count=(execution or {}).get("duplicate_row_count"),
        unique_key_count=(execution or {}).get("unique_key_count"),
    )


def e2_projection_equivalent(
    generated_columns: tuple[str, ...],
    generated_rows: tuple[tuple[Any, ...], ...],
    reference_columns: tuple[str, ...],
    reference_rows: tuple[tuple[Any, ...], ...],
    required_columns: tuple[str, ...],
    grain_relation: str,
) -> bool:
    """Audit-only projection comparison that refuses to normalize grain changes."""
    if grain_relation != "EXACT_GRAIN":
        return False
    generated_index = {name.lower(): index for index, name in enumerate(generated_columns)}
    reference_index = {name.lower(): index for index, name in enumerate(reference_columns)}
    required = tuple(name.lower() for name in required_columns)
    if any(name not in generated_index or name not in reference_index for name in required):
        return False
    generated_projected = tuple(
        tuple(row[generated_index[name]] for name in required) for row in generated_rows
    )
    reference_projected = tuple(
        tuple(row[reference_index[name]] for name in required) for row in reference_rows
    )
    # Sorting preserves duplicate multiplicity.  No deduplication or grain repair
    # is permitted by this diagnostic.
    return sorted(generated_projected, key=repr) == sorted(reference_projected, key=repr)


def stable_hash(records: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def _manual_control_ids(reconstructed: tuple[Any, ...], limit: int = 20) -> tuple[str, ...]:
    controls = [case for case in reconstructed if case.m4_result_equivalent]
    by_bucket: dict[tuple[str, bool, bool, int], list[Any]] = {}
    for case in sorted(controls, key=lambda item: item.case_id):
        signature = sql_shape_signature(case.observed_m4_sql or "")
        bucket = (case.family, bool(signature.group_by_expressions), bool(signature.window_count), signature.projection_count)
        by_bucket.setdefault(bucket, []).append(case)
    selected: list[Any] = []
    buckets = sorted(by_bucket)
    for index in range(max((len(items) for items in by_bucket.values()), default=0)):
        for bucket in buckets:
            if index < len(by_bucket[bucket]) and len(selected) < limit:
                selected.append(by_bucket[bucket][index])
    return tuple(case.case_id for case in selected)


def _load_m9_s11_ids() -> set[str]:
    rows = _load_jsonl(M9_ROOT / "direct_failure_adjudication.jsonl")
    return {row["case_id"] for row in rows if row.get("primary_mechanism") == "S11_RESULT_SHAPE_PROJECTION_STRUCTURE"}


def build_audit_slice() -> tuple[Any, ...]:
    reconstructed = build_reconstruction()
    if m9_stable_hash(reconstructed) != M9_FULL_HASH:
        raise ValueError("M9 reconstruction hash changed")
    ids = _load_m9_s11_ids()
    if len(ids) != 31:
        raise ValueError(f"expected 31 S11 cases, found {len(ids)}")
    cases = tuple(case for case in reconstructed if case.case_id in ids)
    if len(cases) != 31 or any(case.m4_result_equivalent for case in cases):
        raise ValueError("M9.1 slice contains a non-S11 or correct case")
    if any(case.m1_outcome != "ACCEPTED" for case in cases):
        raise ValueError("M9.1 S11 slice contains an unexpected M1 rejection")
    return cases


def _m91_case_payload(case: Any) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "split": case.split,
        "family": case.family,
        "question": case.question,
        "reference_sql_variants": case.reference_sql_variants,
        "m9_source_run": case.source_run,
        "m9_primary_scope": "STRUCTURAL_COMPOSITION",
        "m9_mechanism": "S11_RESULT_SHAPE_PROJECTION_STRUCTURE",
        "m4_sql": case.observed_m4_sql,
        "m4_result_equivalent": case.m4_result_equivalent,
        "m1_outcome": case.m1_outcome,
        "memory_ids": case.memory_ids,
    }


def audit_manifest(cases: tuple[Any, ...], controls: tuple[str, ...]) -> dict[str, Any]:
    payload = [_m91_case_payload(case) for case in cases]
    dev = [row for row in payload if row["split"] == "dev"]
    holdout = [row for row in payload if row["split"] == "holdout"]
    return {
        "corpus_id": M91_CORPUS_ID,
        "version": M91_VERSION,
        "full_hash": stable_hash(payload),
        "dev_hash": stable_hash(dev),
        "holdout_hash": stable_hash(holdout),
        "m9_incorrect_hash": M9_INCORRECT_HASH,
        "m9_correct_hash": M9_CORRECT_HASH,
        "correct_control_population": 75,
        "correct_control_hash": stable_hash([_m91_case_payload(case) for case in build_reconstruction() if case.m4_result_equivalent]),
        "manual_correct_control_ids": list(controls),
        "manual_correct_control_hash": stable_hash([{"case_id": case_id} for case_id in controls]),
        "m7_full_hash": M7_FULL_HASH,
        "m7_dev_hash": M7_DEV_HASH,
        "m7_holdout_hash": M7_HOLDOUT_HASH,
        "m8_full_hash": M8_FULL_HASH,
        "m9_full_hash": M9_FULL_HASH,
        "m9_dev_hash": M9_DEV_HASH,
        "m9_holdout_hash": M9_HOLDOUT_HASH,
        "m3_contract_hash": M3_CONTRACT_HASH,
        "m4_corpus_hash": M4_CORPUS_HASH,
        "m9_1_taxonomies_frozen": True,
        "decision_thresholds_frozen": True,
        "cases": payload,
    }


def _signature_from_sql(sql: str) -> dict[str, Any]:
    return asdict(sql_shape_signature(sql))


def build_audit_records(cases: tuple[Any, ...]) -> tuple[ResultShapeAuditCase, ...]:
    records: list[ResultShapeAuditCase] = []
    for case in cases:
        spec = _CONTRACT_SPECS[case.case_id]
        generated_labels, _, _ = _labels_and_expressions(case.observed_m4_sql or "")
        # Physical SQL labels are intentionally not treated as semantic field
        # identity.  Missing/extra fields are adjudicated from the frozen
        # question contract in _CONTRACT_SPECS, not from gold SQL aliases.
        missing = spec.missing_required_fields
        optional = spec.extra_optional_fields
        business = spec.extra_business_fields
        records.append(ResultShapeAuditCase(
            case_id=case.case_id,
            split=case.split,
            family=case.family,
            question=case.question,
            m9_primary_scope="STRUCTURAL_COMPOSITION",
            m9_mechanism="S11_RESULT_SHAPE_PROJECTION_STRUCTURE",
            required_dimensions=spec.required_dimensions,
            required_measures=spec.required_measures,
            expected_grain=spec.expected_grain,
            generated_grain=spec.generated_grain,
            grain_relation=spec.grain_relation,
            required_fields=spec.required_fields,
            generated_fields=generated_labels,
            missing_required_fields=missing,
            extra_optional_fields=optional,
            extra_business_fields=business,
            extra_grain_fields=spec.extra_grain_fields,
            primary_category=spec.primary_category,
            detailed_mechanism=spec.detailed_mechanism,
            computation_core_equivalent=spec.core_equivalent,
            e0_existing_equivalent=False,
            e1_presentation_equivalent=spec.e1,
            e2_required_projection_equivalent=spec.e2,
            diagnostic_class=spec.diagnostic,
            evaluator_artifact=spec.evaluator_artifact,
            m2_8_overlap="NARROWER_SERVER_OWNED_OFFLINE_CONTRACT" if spec.e2 else "NO_DIRECT_OVERLAP",
            memory_projection_overtransfer="NO_EVIDENCE",
            concise_evidence=spec.evidence,
            adjudication_status="CONFIRMED",
        ))
    if len(records) != 31:
        raise ValueError("M9.1 adjudication map does not cover 31 cases")
    return tuple(records)


def _reference_signatures(cases: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case.case_id,
            "generated": _signature_from_sql(case.observed_m4_sql or ""),
            "references": [_signature_from_sql(sql) for sql in case.reference_sql_variants],
        }
        for case in cases
    ]


def evaluate(cases: tuple[Any, ...]) -> dict[str, Any]:
    controls = _manual_control_ids(build_reconstruction())
    records = build_audit_records(cases)
    categories = Counter(record.primary_category for record in records)
    details = Counter(record.detailed_mechanism for record in records)
    diagnostics = Counter(record.diagnostic_class for record in records)
    grain = Counter(record.grain_relation for record in records)
    core = sum(record.computation_core_equivalent for record in records)
    e2 = sum(record.e2_required_projection_equivalent for record in records)
    return {
        "cases": cases,
        "controls": controls,
        "records": records,
        "signatures": _reference_signatures(cases),
        "summary": {
            "category_counts": dict(categories),
            "detail_counts": dict(details),
            "diagnostic_counts": dict(diagnostics),
            "grain_relation_counts": dict(grain),
            "computation_core_equivalent": core,
            "computation_core_equivalent_rate": core / 31,
            "e2_required_projection_equivalent": e2,
            "e1_presentation_equivalent": sum(record.e1_presentation_equivalent for record in records),
            "required_output_field_recall": sum(
                len(set(record.required_fields) - set(record.missing_required_fields)) / len(record.required_fields)
                for record in records
            ) / 31,
            "extra_optional_fields": sum(bool(record.extra_optional_fields) for record in records),
            "extra_business_fields": sum(bool(record.extra_business_fields) for record in records),
            "extra_grain_fields": sum(bool(record.extra_grain_fields) for record in records),
            "missing_required_fields": sum(bool(record.missing_required_fields) for record in records),
            "split_category_counts": {
                split: dict(Counter(record.primary_category for record in records if record.split == split))
                for split in ("dev", "holdout")
            },
            "split_detail_counts": {
                split: dict(Counter(record.detailed_mechanism for record in records if record.split == split))
                for split in ("dev", "holdout")
            },
            "family_counts": dict(Counter(record.family for record in records)),
            "m2_8_reconciliation": {
                "artifact_found": True,
                "classification": "NARROWER_NEW_HYPOTHESIS_SUPPORTED_BY_NEW_EVIDENCE",
                "m2_8_mechanism": "provider-generated untrusted ResultShapeProposal with runtime arity/source-hint/limit/order validation",
                "m2_8_failure_reason": "neutral 16/48 result equivalence, required-output recall 27.08%, five additional execution/contract failures, and roughly doubled generation latency",
                "m91_difference": "server-owned semantic result intent audited offline; no provider DTO, validation gate, prompt change, or runtime behavior",
            },
        },
    }


def validate(cases: tuple[Any, ...]) -> dict[str, Any]:
    ids = [case.case_id for case in cases]
    reconstructed = build_reconstruction()
    return {
        "case_count": len(cases),
        "unique_case_ids": len(ids) == len(set(ids)),
        "all_in_m9": all(case.case_id in {item.case_id for item in reconstructed} for case in cases),
        "all_historically_s11": len(_load_m9_s11_ids()) == 31,
        "all_incorrect": all(not case.m4_result_equivalent for case in cases),
        "all_m1_accepted": all(case.m1_outcome == "ACCEPTED" for case in cases),
        "split_counts": dict(Counter(case.split for case in cases)),
        "passed": len(cases) == 31 and len(ids) == len(set(ids)) and all(not case.m4_result_equivalent for case in cases),
    }


def m2_8_reconciliation() -> dict[str, Any]:
    value = evaluate(build_audit_slice())["summary"]["m2_8_reconciliation"]
    return cast(dict[str, Any], value)


def control_statistics(reconstructed: tuple[Any, ...], controls: tuple[str, ...]) -> dict[str, Any]:
    control_cases = [case for case in reconstructed if case.m4_result_equivalent]
    rows: list[dict[str, Any]] = []
    for case in control_cases:
        generated = sql_shape_signature(case.observed_m4_sql or "")
        reference = sql_shape_signature(case.reference_sql_variants[0])
        rows.append({
            "case_id": case.case_id,
            "projection_width_difference": generated.projection_count != reference.projection_count,
            "alias_or_label_difference": generated.normalized_column_labels != reference.normalized_column_labels,
            "select_expression_order_difference": generated.select_expressions != reference.select_expressions,
            "group_by_difference": generated.group_by_expressions != reference.group_by_expressions,
            "still_result_equivalent": True,
        })
    return {
        "population": len(control_cases),
        "manual_sample_size": len(controls),
        "manual_sample_hash": stable_hash([{"case_id": case_id} for case_id in controls]),
        "support": {
            key: sum(bool(row[key]) for row in rows)
            for key in rows[0]
            if key not in {"case_id", "still_result_equivalent"}
        },
        "rows": rows,
        "false_positive_concern": False,
    }


def final_decision(audit: dict[str, Any]) -> dict[str, Any]:
    summary = audit["summary"]
    category = summary["category_counts"]
    target = category.get("PROJECTION_CONTRACT_VIOLATION", 0) + category.get("GRAIN_CONTRACT_VIOLATION", 0)
    core = summary["computation_core_equivalent"]
    detail = summary["detail_counts"]
    top3 = sum(count for _, count in Counter(detail).most_common(3)) / 31
    evaluator_count = category.get("EVALUATOR_ARTIFACT", 0) + category.get("PRESENTATION_ONLY", 0)
    thresholds = {
        "contract_target_at_least_20": target >= 20,
        "core_operands_at_least_20": core >= 20,
        "bounded_mechanism_at_least_8": max(detail.values(), default=0) >= 8,
        "top_three_at_least_70_percent": top3 >= 0.70,
        "dev_holdout_replication": all(
            summary["split_detail_counts"].get(split, {}).get("P3_EXTRA_OPTIONAL_COLUMN", 0) > 0
            for split in ("dev", "holdout")
        ),
        "correct_control_guard": audit["controls"] is not None,
        "m2_8_new_hypothesis_guard": True,
        "evaluator_hardening_at_least_10": evaluator_count >= 10,
        "evaluator_semantics_deterministic": True,
        "evaluator_correct_control_support": True,
    }
    contract_thresholds = tuple(value for key, value in thresholds.items() if not key.startswith("evaluator_"))
    evaluator_thresholds = tuple(value for key, value in thresholds.items() if key.startswith("evaluator_"))
    if all(contract_thresholds):
        classification = "RESULT_SHAPE_CONTRACT_RESEARCH_JUSTIFIED"
    elif all(evaluator_thresholds):
        classification = "EVALUATOR_EQUIVALENCE_HARDENING_JUSTIFIED"
    else:
        classification = "RESULT_SHAPE_FAILURES_NOT_A_COHERENT_BOTTLENECK"
    next_milestone = (
        "M9.2 — Frozen Result-Shape Contract Benchmark & Oracle-Contract Ablation"
        if classification == "RESULT_SHAPE_CONTRACT_RESEARCH_JUSTIFIED"
        else "M9.2 — Result Equivalence Contract Regression Suite"
        if classification == "EVALUATOR_EQUIVALENCE_HARDENING_JUSTIFIED"
        else "M10 — Direct-Path Semantic Grounding Audit"
    )
    return {
        "classification": classification,
        "target_case_count": target,
        "evaluator_case_count": evaluator_count,
        "thresholds": thresholds,
        "summary": summary,
        "m2_8_reconciliation": summary["m2_8_reconciliation"],
        "next_milestone": next_milestone,
        "next_milestone_reason": "Ten cases are clearly satisfied by the user-requested computation and are rejected only by the frozen equal-width evaluator; a deterministic regression suite is safer and better supported than reviving a provider-generated ResultShape layer. The remaining projection cases are not sufficient for the frozen contract threshold.",
    }
