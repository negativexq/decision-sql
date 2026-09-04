# ruff: noqa: E501

"""Provider-free construction and validation for the M8 routing audit slice.

The slice is derived only from persisted M7 artifacts.  It contains no new
questions and does not call the router, provider, M1, or PostgreSQL.  The
mechanism taxonomy and capability annotations are frozen here before the
counterfactual direct-path run.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
M7_ROOT = ROOT / "evaluation" / "results" / "m7"
M7_CORPUS_ID = "decisionsql-combined-mixed-workload"
M7_CORPUS_VERSION = "m7-combined-v1"
M7_FULL_HASH = "f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043"
M7_DEV_HASH = "59bd32b94939b4f1c2d77ab79cc9feaf4622358009073f9c16f7108eee3a014f"
M7_HOLDOUT_HASH = "740ab83e8700877e3bbe7bee6bcd61611a03d92755231dafebb7270d762be0e5"
M3_CONTRACT_HASH = "0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c"
M4_CORPUS_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
M4_RETRIEVER_VERSION = "m4-retriever-v1"
M4_K = 3

M8_CORPUS_ID = "decisionsql-m8-false-governed-audit"
M8_CORPUS_VERSION = "m8-routing-audit-v1"

Coverage = Literal["FULL", "PARTIAL", "NONE"]
Split = Literal["dev", "holdout"]
RouteLabelStatus = Literal[
    "CONFIRMED_DIRECT", "POSSIBLY_GOVERNED", "LABEL_ERROR", "UNDETERMINED"
]
AdjudicationStatus = Literal["CONFIRMED", "STRONGLY_SUPPORTED", "UNDETERMINED"]

ROUTING_MECHANISMS = (
    "R1_METRIC_INTENT_OVERMATCH",
    "R2_PARTIAL_SEMANTIC_COVERAGE",
    "R3_UNSUPPORTED_FILTER_EXTENSION",
    "R4_UNSUPPORTED_ORDER_TOPN_EXTENSION",
    "R5_UNSUPPORTED_WINDOW_EXTENSION",
    "R6_UNSUPPORTED_DERIVED_FORMULA_EXTENSION",
    "R7_DIMENSION_OR_GRAIN_OVERMATCH",
    "R8_RELATIONSHIP_SCOPE_OVERMATCH",
    "R9_LEXICAL_OR_ALIAS_COLLISION",
    "R10_OTHER",
    "R11_UNDETERMINED",
)


class RoutingAuditCase(BaseModel):
    """Evaluation-only case plus immutable M7 route/result evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    split: Split
    family: str = Field(min_length=1)
    expected_route: Literal["DIRECT"]
    actual_route: Literal["GOVERNED"]
    current_p1_correct: bool
    p0_correct: bool
    expected_route_label_status: RouteLabelStatus
    grounded_metric_id: str | None = None
    grounded_dimension_ids: tuple[str, ...] = ()
    required_operations: tuple[str, ...] = Field(min_length=1)
    governed_supported_operations: tuple[str, ...] = ()
    unsupported_required_operations: tuple[str, ...] = ()
    coverage: Coverage
    primary_routing_mechanism: str
    secondary_mechanisms: tuple[str, ...] = ()
    current_sql_source: Literal["GOVERNED_COMPILER"] = "GOVERNED_COMPILER"
    current_result_equivalent: bool
    current_route_status: str = Field(min_length=1)
    current_governed_sql: str | None = None
    semantic_provenance: dict[str, Any] | None = None
    adjudication_note: str = Field(min_length=1)
    adjudication_status: AdjudicationStatus
    counterfactual_memory_ids: tuple[str, ...] = ()
    counterfactual_sql: str | None = None
    counterfactual_m1_outcome: str | None = None
    counterfactual_result_equivalent: bool | None = None
    counterfactual_outcome_class: str | None = None
    counterfactual_failure_cause: str | None = None


class M8AuditError(ValueError):
    """Raised when frozen M7 evidence cannot form the audit slice."""


class _CaseSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    family: str
    label_status: RouteLabelStatus
    required: tuple[str, ...]
    supported: tuple[str, ...]
    unsupported: tuple[str, ...]
    coverage: Coverage
    mechanism: str
    secondary: tuple[str, ...] = ()
    note: str


# This is the precommitted mechanism taxonomy and case-level capability audit.
# It is intentionally finite and is not a production routing rule set.
CASE_SPECS: dict[str, _CaseSpec] = {
    "m7-clear_direct_complex-006": _CaseSpec(
        family="CLEAR_DIRECT_COMPLEX", label_status="CONFIRMED_DIRECT",
        required=("METRIC", "DIMENSION", "AGGREGATION", "HAVING"),
        supported=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"),
        unsupported=("HAVING",), coverage="PARTIAL", mechanism="R3_UNSUPPORTED_FILTER_EXTENSION",
        note="The request adds a representative-level aggregate threshold; M3 has no arbitrary HAVING input.",
    ),
    "m7-clear_direct_complex-007": _CaseSpec(
        family="CLEAR_DIRECT_COMPLEX", label_status="CONFIRMED_DIRECT",
        required=("METRIC", "DIMENSION", "GRAIN", "RELATIONSHIP_TRAVERSAL"),
        supported=("METRIC", "DEFAULT_FILTER"),
        unsupported=("DIMENSION", "GRAIN", "RELATIONSHIP_TRAVERSAL"), coverage="PARTIAL",
        mechanism="R7_DIMENSION_OR_GRAIN_OVERMATCH", secondary=("R8_RELATIONSHIP_SCOPE_OVERMATCH",),
        note="The question asks for region grain through customers; the grounded metric used customer grain instead.",
    ),
    "m7-clear_direct_complex-012": _CaseSpec(
        family="CLEAR_DIRECT_COMPLEX", label_status="CONFIRMED_DIRECT",
        required=("METRIC", "WINDOW", "RUNNING_AGGREGATE", "GRAIN"),
        supported=("METRIC", "DEFAULT_FILTER", "AGGREGATION"),
        unsupported=("WINDOW", "RUNNING_AGGREGATE", "GRAIN"), coverage="PARTIAL",
        mechanism="R5_UNSUPPORTED_WINDOW_EXTENSION",
        note="A running total by order date is outside the metric compiler's typed request contract.",
    ),
    "m7-relational_composition-002": _CaseSpec(
        family="RELATIONAL_COMPOSITION", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "RELATIONSHIP_TRAVERSAL", "DIMENSION"),
        supported=(), unsupported=("MEASURE", "RELATIONSHIP_TRAVERSAL", "DIMENSION"), coverage="NONE",
        mechanism="R1_METRIC_INTENT_OVERMATCH",
        note="'Sold unit count' does not explicitly request the governed completed-item metric, but grounding selected it.",
    ),
    "m7-relational_composition-005": _CaseSpec(
        family="RELATIONAL_COMPOSITION", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "DISTINCT_ENTITY_COUNT", "RELATIONSHIP_TRAVERSAL", "DIMENSION"),
        supported=(), unsupported=("MEASURE", "DISTINCT_ENTITY_COUNT", "RELATIONSHIP_TRAVERSAL", "DIMENSION"), coverage="NONE",
        mechanism="R1_METRIC_INTENT_OVERMATCH",
        note="The request asks how many products each customer purchased, not item quantity; the grounded metric was different.",
    ),
    "m7-relational_composition-012": _CaseSpec(
        family="RELATIONAL_COMPOSITION", label_status="POSSIBLY_GOVERNED",
        required=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"),
        supported=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"), unsupported=(), coverage="FULL",
        mechanism="R10_OTHER",
        note="The selected completed-item metric and order-currency dimension are representable; M7's DIRECT label may be narrow.",
    ),
    "m7-filter_aggregation-000": _CaseSpec(
        family="FILTER_AGGREGATION", label_status="POSSIBLY_GOVERNED",
        required=("METRIC", "DEFAULT_FILTER", "AGGREGATION"),
        supported=("METRIC", "DEFAULT_FILTER", "AGGREGATION"), unsupported=(), coverage="FULL",
        mechanism="R10_OTHER",
        note="The request is a direct expression of the governed completed-revenue metric.",
    ),
    "m7-filter_aggregation-002": _CaseSpec(
        family="FILTER_AGGREGATION", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "EXPLICIT_FILTER", "AGGREGATION"),
        supported=("MEASURE", "AGGREGATION"), unsupported=("EXPLICIT_FILTER",), coverage="PARTIAL",
        mechanism="R3_UNSUPPORTED_FILTER_EXTENSION",
        note="The settled-payment filter is explicit, while the governed average-payment metric has no arbitrary filter slot.",
    ),
    "m7-filter_aggregation-008": _CaseSpec(
        family="FILTER_AGGREGATION", label_status="CONFIRMED_DIRECT",
        required=("METRIC", "DIMENSION", "HAVING", "AGGREGATION"),
        supported=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"), unsupported=("HAVING",), coverage="PARTIAL",
        mechanism="R3_UNSUPPORTED_FILTER_EXTENSION",
        note="The category aggregate threshold requires HAVING, which is not a governed MetricRequest field.",
    ),
    "m7-ratio_derived-006": _CaseSpec(
        family="RATIO_DERIVED", label_status="CONFIRMED_DIRECT",
        required=("RATIO", "DIMENSION", "GRAIN", "RELATIONSHIP_TRAVERSAL"),
        supported=("RATIO", "DEFAULT_FILTER", "AGGREGATION"), unsupported=("DIMENSION", "GRAIN", "RELATIONSHIP_TRAVERSAL"), coverage="PARTIAL",
        mechanism="R7_DIMENSION_OR_GRAIN_OVERMATCH", secondary=("R8_RELATIONSHIP_SCOPE_OVERMATCH",),
        note="The governed ratio exists, but region-by-region refund/revenue output is outside its valid dimensions.",
    ),
    "m7-order_top_n-003": _CaseSpec(
        family="ORDER_TOP_N", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "DIMENSION", "ORDER", "TOP_N", "AGGREGATION"),
        supported=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"), unsupported=("ORDER", "TOP_N"), coverage="PARTIAL",
        mechanism="R4_UNSUPPORTED_ORDER_TOPN_EXTENSION",
        note="The question requires ranking and limiting categories; the governed compiler emits an unranked metric table.",
    ),
    "m7-window_composition-006": _CaseSpec(
        family="WINDOW_COMPOSITION", label_status="CONFIRMED_DIRECT",
        required=("METRIC", "DIMENSION", "WINDOW", "ORDER"),
        supported=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"), unsupported=("WINDOW", "ORDER"), coverage="PARTIAL",
        mechanism="R5_UNSUPPORTED_WINDOW_EXTENSION",
        note="Ranking customers by completed revenue requires a window/ranking operation beyond the governed metric output.",
    ),
    "m7-clear_direct_complex-018": _CaseSpec(
        family="CLEAR_DIRECT_COMPLEX", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "RELATIONSHIP_TRAVERSAL", "DIMENSION", "AGGREGATION"),
        supported=(), unsupported=("MEASURE", "RELATIONSHIP_TRAVERSAL", "DIMENSION", "AGGREGATION"), coverage="NONE",
        mechanism="R1_METRIC_INTENT_OVERMATCH",
        note="Total quantity purchased across orders is not the governed completed-item metric request as phrased.",
    ),
    "m7-clear_direct_complex-022": _CaseSpec(
        family="CLEAR_DIRECT_COMPLEX", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "RELATIONSHIP_TRAVERSAL", "CUSTOM_FORMULA", "DIMENSION"),
        supported=("RELATIONSHIP_TRAVERSAL",), unsupported=("MEASURE", "CUSTOM_FORMULA", "DIMENSION"), coverage="PARTIAL",
        mechanism="R6_UNSUPPORTED_DERIVED_FORMULA_EXTENSION",
        note="Per-refund amount, order subtotal, and a per-row ratio are not the governed aggregate refund-rate metric.",
    ),
    "m7-relational_composition-015": _CaseSpec(
        family="RELATIONAL_COMPOSITION", label_status="POSSIBLY_GOVERNED",
        required=("METRIC", "DIMENSION", "RELATIONSHIP_TRAVERSAL", "AGGREGATION"),
        supported=("METRIC", "DIMENSION", "RELATIONSHIP_TRAVERSAL", "AGGREGATION"), unsupported=(), coverage="FULL",
        mechanism="R10_OTHER",
        note="Average payment amount by sales representative is representable by the existing governed metric and dimension.",
    ),
    "m7-relational_composition-016": _CaseSpec(
        family="RELATIONAL_COMPOSITION", label_status="CONFIRMED_DIRECT",
        required=("METRIC", "DIMENSION", "GRAIN", "RELATIONSHIP_TRAVERSAL"),
        supported=("METRIC", "DIMENSION", "DEFAULT_FILTER"), unsupported=("GRAIN", "RELATIONSHIP_TRAVERSAL"), coverage="PARTIAL",
        mechanism="R7_DIMENSION_OR_GRAIN_OVERMATCH", secondary=("R8_RELATIONSHIP_SCOPE_OVERMATCH",),
        note="Customers plus their region plus completed revenue requires a second requested grain outside the grounded metric output.",
    ),
    "m7-relational_composition-019": _CaseSpec(
        family="RELATIONAL_COMPOSITION", label_status="POSSIBLY_GOVERNED",
        required=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"),
        supported=("METRIC", "DIMENSION", "DEFAULT_FILTER", "AGGREGATION"), unsupported=(), coverage="FULL",
        mechanism="R10_OTHER",
        note="Completed item quantity by product category is explicitly represented by the governed catalog.",
    ),
    "m7-filter_aggregation-012": _CaseSpec(
        family="FILTER_AGGREGATION", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "DIMENSION", "EXPLICIT_FILTER", "AGGREGATION"),
        supported=(), unsupported=("MEASURE", "DIMENSION", "EXPLICIT_FILTER", "AGGREGATION"), coverage="NONE",
        mechanism="R1_METRIC_INTENT_OVERMATCH",
        note="Average line-item quantity filtered to Electronics is not average items per completed order.",
    ),
    "m7-filter_aggregation-014": _CaseSpec(
        family="FILTER_AGGREGATION", label_status="CONFIRMED_DIRECT",
        required=("METRIC", "EXPLICIT_FILTER", "TEMPORAL_BOUNDARY", "AGGREGATION"),
        supported=("METRIC", "DEFAULT_FILTER", "AGGREGATION"), unsupported=("EXPLICIT_FILTER", "TEMPORAL_BOUNDARY"), coverage="PARTIAL",
        mechanism="R2_PARTIAL_SEMANTIC_COVERAGE",
        note="The explicit date cutoff is additional semantics not accepted by the governed metric request.",
    ),
    "m7-order_top_n-007": _CaseSpec(
        family="ORDER_TOP_N", label_status="CONFIRMED_DIRECT",
        required=("MEASURE", "DIMENSION", "GRAIN", "ORDER", "TOP_N"),
        supported=("METRIC", "DEFAULT_FILTER", "AGGREGATION"), unsupported=("MEASURE", "DIMENSION", "GRAIN", "ORDER", "TOP_N"), coverage="PARTIAL",
        mechanism="R7_DIMENSION_OR_GRAIN_OVERMATCH", secondary=("R4_UNSUPPORTED_ORDER_TOPN_EXTENSION",),
        note="The request ranks individual products, while grounding selected category-level completed item quantity.",
    ),
}


def _read_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text()))


def _load_split(root: Path, split: Split) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    directory = root / ("dev-20260904" if split == "dev" else "holdout-20260904")
    prefix = "dev" if split == "dev" else "holdout"
    manifest = _read_json(directory / "benchmark_manifest.json")
    p1_rows = {
        row["case_id"]: row
        for row in (json.loads(line) for line in (directory / f"p1_{prefix}_results.jsonl").read_text().splitlines())
    }
    p0_rows = {
        row["case_id"]: row
        for row in (json.loads(line) for line in (directory / f"p0_{prefix}_results.jsonl").read_text().splitlines())
    }
    return manifest, p1_rows, p0_rows


def build_audit_cases(root: Path = M7_ROOT) -> tuple[RoutingAuditCase, ...]:
    """Extract exactly the persisted M7 false-governed cases."""
    manifests: dict[Split, dict[str, Any]] = {}
    p1_by_id: dict[str, dict[str, Any]] = {}
    p0_by_id: dict[str, dict[str, Any]] = {}
    m7_cases: dict[str, dict[str, Any]] = {}
    for split in ("dev", "holdout"):
        manifest, p1_rows, p0_rows = _load_split(root, split)
        expected_hash = M7_DEV_HASH if split == "dev" else M7_HOLDOUT_HASH
        manifest_split_hash = manifest.get("dev_hash" if split == "dev" else "holdout_hash")
        if manifest.get("full_hash") != M7_FULL_HASH or manifest_split_hash != expected_hash:
            raise M8AuditError(f"M7 {split} manifest hash anchor mismatch")
        manifests[split] = manifest
        p1_by_id.update(p1_rows)
        p0_by_id.update(p0_rows)
        m7_cases.update({case["case_id"]: case for case in manifest["cases"]})

    result: list[RoutingAuditCase] = []
    for case_id, spec in CASE_SPECS.items():
        if case_id not in m7_cases or case_id not in p1_by_id or case_id not in p0_by_id:
            raise M8AuditError(f"Missing persisted M7 evidence for {case_id}")
        source = m7_cases[case_id]
        p1 = p1_by_id[case_id]
        p0 = p0_by_id[case_id]
        split = source["split"]
        if split not in manifests:
            raise M8AuditError(f"Invalid split for {case_id}: {split}")
        if source["expected_route"] != "DIRECT" or p1.get("route_actual") != "GOVERNED":
            raise M8AuditError(f"Case is not a false-governed route: {case_id}")
        metric_name = p1.get("route_metric_name")
        dimensions = tuple(p1.get("route_dimensions") or ())
        result.append(
            RoutingAuditCase(
                case_id=case_id,
                split=split,
                family=source["family"],
                expected_route="DIRECT",
                actual_route="GOVERNED",
                current_p1_correct=bool(p1.get("correct")),
                p0_correct=bool(p0.get("correct")),
                expected_route_label_status=spec.label_status,
                grounded_metric_id=f"metric:{metric_name}" if metric_name else None,
                grounded_dimension_ids=tuple(f"dimension:{item}" for item in dimensions),
                required_operations=spec.required,
                governed_supported_operations=spec.supported,
                unsupported_required_operations=spec.unsupported,
                coverage=spec.coverage,
                primary_routing_mechanism=spec.mechanism,
                secondary_mechanisms=spec.secondary,
                current_result_equivalent=bool(p1.get("correct")),
                current_route_status=str(p1.get("route_status") or "UNKNOWN"),
                current_governed_sql=p1.get("governed_sql"),
                semantic_provenance=p1.get("semantic_provenance"),
                adjudication_note=spec.note,
                adjudication_status="STRONGLY_SUPPORTED",
            )
        )
    return tuple(result)


def _frozen_payload(case: RoutingAuditCase) -> dict[str, Any]:
    return case.model_dump(
        mode="json",
        exclude={
            "counterfactual_memory_ids",
            "counterfactual_sql",
            "counterfactual_m1_outcome",
            "counterfactual_result_equivalent",
            "counterfactual_outcome_class",
            "counterfactual_failure_cause",
        },
    )


def audit_hash(cases: tuple[RoutingAuditCase, ...]) -> str:
    payload = [_frozen_payload(case) for case in cases]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def split_hash(cases: tuple[RoutingAuditCase, ...], split: Split) -> str:
    payload = [_frozen_payload(case) for case in cases if case.split == split]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_audit_cases(cases: tuple[RoutingAuditCase, ...]) -> dict[str, Any]:
    ids = [case.case_id for case in cases]
    route_ok = all(case.expected_route == "DIRECT" and case.actual_route == "GOVERNED" for case in cases)
    primary_incorrect = sum(not case.current_p1_correct for case in cases)
    mechanisms = {case.primary_routing_mechanism for case in cases}
    coverage_counts = {value: sum(case.coverage == value for case in cases) for value in ("FULL", "PARTIAL", "NONE")}
    return {
        "case_count": len(cases),
        "dev_count": sum(case.split == "dev" for case in cases),
        "holdout_count": sum(case.split == "holdout" for case in cases),
        "unique_ids": len(ids) == len(set(ids)),
        "route_invariant": route_ok,
        "harmful_count": primary_incorrect,
        "harmless_count": len(cases) - primary_incorrect,
        "mechanism_count": len(mechanisms),
        "coverage_counts": coverage_counts,
        "passed": len(cases) == 20 and len(ids) == len(set(ids)) and route_ok and primary_incorrect == 13 and len(cases) - primary_incorrect == 7 and mechanisms <= set(ROUTING_MECHANISMS),
    }


def manifest(cases: tuple[RoutingAuditCase, ...]) -> dict[str, Any]:
    validation = validate_audit_cases(cases)
    return {
        "corpus_id": M8_CORPUS_ID,
        "corpus_version": M8_CORPUS_VERSION,
        "full_hash": audit_hash(cases),
        "dev_hash": split_hash(cases, "dev"),
        "holdout_hash": split_hash(cases, "holdout"),
        "m7_corpus_id": M7_CORPUS_ID,
        "m7_corpus_version": M7_CORPUS_VERSION,
        "m7_full_hash": M7_FULL_HASH,
        "m7_dev_hash": M7_DEV_HASH,
        "m7_holdout_hash": M7_HOLDOUT_HASH,
        "m3_contract_hash": M3_CONTRACT_HASH,
        "m4_corpus_hash": M4_CORPUS_HASH,
        "m4_retriever_version": M4_RETRIEVER_VERSION,
        "m4_k": M4_K,
        "total": len(cases),
        "dev": validation["dev_count"],
        "holdout": validation["holdout_count"],
        "harmful": validation["harmful_count"],
        "harmless": validation["harmless_count"],
        "coverage_counts": validation["coverage_counts"],
        "cases": [case.model_dump(mode="json") for case in cases],
    }
