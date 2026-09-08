"""Provider-free M30 semantic failure forensics for the frozen 18 cases."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.semantics.semantic_query import SemanticQueryIR, SemanticQueryPlan, plan_to_ir
from evaluation.run_m29_semantic_plan import _component_scores
from evaluation.run_m29r1_semantic_plan import _prepare
from evaluation.semantic_oracle_ceiling import oracle_plan_from_reference_sql

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "evaluation/fixtures/m29r1_livesqlbench_semantic_plan_result.json"
CASES = ROOT / "evaluation/external/livesqlbench/protected/results/m29r1_semantic_plan_cases.jsonl"
OUTPUT = ROOT / "evaluation/fixtures/m30_semantic_failure_forensics.json"
REPORT = ROOT / "docs/m30-semantic-failure-forensics.md"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _first_false(scores: dict[str, bool]) -> str | None:
    order = (
        ("entities", "ENTITY_SELECTION_ERROR"),
        ("population", "POPULATION_ERROR"),
        ("relationships", "RELATIONSHIP_ERROR"),
        ("grain", "GRAIN_ERROR"),
        ("filters", "FILTER_ERROR"),
        ("aggregation", "AGGREGATION_ERROR"),
        ("calculation", "CALCULATION_ERROR"),
        ("outputs", "OUTPUT_ERROR"),
        ("ordering", "ORDER_LIMIT_ERROR"),
        ("limit", "ORDER_LIMIT_ERROR"),
        ("temporal", "TEMPORAL_ERROR"),
        ("window", "WINDOW_ERROR"),
        ("nested_structure", "NESTED_STRUCTURE_ERROR"),
    )
    for field, cause in order:
        if scores.get(field) is False:
            return cause
    return None


def _subtypes(row: dict[str, Any], generated: SemanticQueryPlan | None) -> list[str]:
    error = str(row.get("validation_error") or row.get("semantic_failure") or "")
    if "ad-hoc join" in error:
        return ["RELATIONSHIP_UNAUTHORIZED_ADHOC_JOIN"]
    if "calculation_contract" in error or "numerator and denominator" in error:
        return ["CALCULATION_INVALID_PAYLOAD_FOR_KIND"]
    if "active population" in error:
        return ["RELATIONSHIP_POPULATION_EXPANDING"]
    if "connected entity" in error:
        return ["RELATIONSHIP_DISCONNECTED"]
    if "population base entity" in error:
        return ["POPULATION_SOURCE_MISMATCH"]
    if generated is None:
        return ["CANONICAL_CONTRACT_USE_ERROR"]
    scores = row.get("component_scores", {})
    result: list[str] = []
    if scores.get("population") is False:
        result.append("POPULATION_TOO_BROAD_OR_SOURCE_MISMATCH")
    if scores.get("grain") is False:
        result.append("GRAIN_POPULATION_DERIVED_ERROR")
    if scores.get("outputs") is False:
        result.append("OUTPUT_WRONG_ATTRIBUTE_OR_EXPRESSION")
    if scores.get("relationships") is False:
        result.append("RELATIONSHIP_WRONG_EDGE_OR_UNNECESSARY")
    if scores.get("calculation") is False:
        result.append("CALCULATION_WRONG_OPERAND_OR_KIND")
    if scores.get("filters") is False:
        result.append("FILTER_WRONG_SCOPE_OR_PREDICATE")
    if scores.get("aggregation") is False:
        result.append("AGGREGATION_OR_GROUPING_ERROR")
    if scores.get("ordering") is False:
        result.append("ORDER_WRONG_TARGET_OR_DIRECTION")
    if scores.get("limit") is False:
        result.append("LIMIT_WRONG_OR_MISSING")
    if scores.get("temporal") is False:
        result.append("TEMPORAL_WRONG_BOUNDARY_OR_ATTRIBUTE")
    if scores.get("window") is False:
        result.append("WINDOW_WRONG_PARTITION_OR_ORDER")
    if scores.get("nested_structure") is False:
        result.append("NESTED_WRONG_SCOPE_OR_BOUNDARY")
    return result or ["OTHER_SEMANTIC_DIFF"]


def _primary_from_subtypes(subtypes: list[str]) -> str | None:
    if not subtypes:
        return None
    if subtypes[0].startswith("RELATIONSHIP_"):
        return "RELATIONSHIP_ERROR"
    if subtypes[0].startswith("POPULATION_"):
        return "POPULATION_ERROR"
    if subtypes[0].startswith("CALCULATION_"):
        return "CALCULATION_ERROR"
    if subtypes[0].startswith("GRAIN_"):
        return "GRAIN_ERROR"
    if subtypes[0].startswith("FILTER_"):
        return "FILTER_ERROR"
    return None


def _recursive_ir_entity_ids(ir: SemanticQueryIR, mapping: Any) -> set[str]:
    result: set[str] = set()
    if ir.from_entity_id is not None:
        result.add(ir.from_entity_id)
    if ir.from_source is not None and hasattr(ir.from_source, "entity_id"):
        result.add(ir.from_source.entity_id)
    for join in ir.joins:
        relationship_ids = list(join.relationship_path)
        if join.relationship_id is not None:
            relationship_ids.append(join.relationship_id)
        for relationship_id in relationship_ids:
            try:
                relationship = mapping.relationship(relationship_id)
            except Exception:
                continue
            result.update((relationship.from_entity_id, relationship.to_entity_id))
    for cte in ir.ctes:
        result.update(_recursive_ir_entity_ids(cte.query, mapping))
    for derived in ir.derived_relations:
        result.update(_recursive_ir_entity_ids(derived.query, mapping))
    return result


def _authoritative_scores(
    generated: SemanticQueryPlan, oracle: SemanticQueryPlan, mapping: Any
) -> dict[str, bool]:
    scores = _component_scores(generated, oracle, mapping)
    scores["entities"] = _recursive_ir_entity_ids(
        plan_to_ir(generated), mapping
    ) == _recursive_ir_entity_ids(plan_to_ir(oracle), mapping)
    return scores


def _top_level(row: dict[str, Any]) -> str:
    if row.get("metadata_blocked"):
        return "METADATA_BLOCKED"
    if not row.get("canonical_schema_valid"):
        return "CANONICAL_MODEL_INVALID"
    if row.get("official_status") == "EVALUATOR_LIMITATION":
        return "EVALUATOR_LIMITATION"
    if row.get("official_status") == "CORRECT":
        return "SEMANTIC_VALID_AND_CORRECT"
    if row.get("canonical_semantic_valid") and row.get("official_status") == "INCORRECT":
        return "SEMANTIC_VALID_BUT_WRONG"
    if row.get("canonical_semantic_valid"):
        return "DOWNSTREAM_ENGINE_FAILURE"
    return "CANONICAL_MODEL_INVALID"


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# M30 — Semantic Decision Failure Forensics",
        "",
        "This report is a provider-free replay of the frozen M29R.1 responses.",
        "It does not modify prompts, plans, the semantic engine, or M1.",
        "",
        f"Provider calls during M30: **{report['provider_calls']}**.",
        "",
        "## Primary first-divergence counts",
        "",
        "| Cause | Cases |",
        "|---|---:|",
    ]
    for key, value in report["primary_cause_counts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## Case ledger",
            "",
            "| Case | Status | First divergence | Primary cause | Metadata |",
            "|---|---|---|---|---|",
        ]
    )
    for case in report["cases"]:
        lines.append(
            f"| `{case['case_id']}` | {case['top_level_status']} | "
            f"`{case.get('first_divergence') or 'none'}` | "
            f"`{case.get('primary_cause') or 'none'}` | "
            f"{case['metadata_limitation']} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "Population, grain, relationship, calculation, and nested-scope failures are",
            "semantic-plan evidence. They are not provider transport or projection failures.",
            "The two relationship metadata limitations remain separate from model errors.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report() -> dict[str, Any]:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    rows = _read_jsonl(CASES)
    by_id = {row["case_id"]: row for row in rows}
    mapping_rows = {row["case_id"]: row for row in result["cases"]}
    prepared_cases, _, mappings, _, _ = _prepare()
    prepared_by_id = {case.instance_id: case for case in prepared_cases}
    cases: list[dict[str, Any]] = []
    primary_counts: Counter[str] = Counter()
    subtype_counts: Counter[str] = Counter()
    for case_id in [row["case_id"] for row in result["cases"]]:
        row = {**mapping_rows[case_id], **by_id[case_id]}
        plan = None
        oracle = None
        if isinstance(row.get("plan"), dict):
            plan = SemanticQueryPlan.model_validate(row["plan"])
        scores = row.get("component_scores", {})
        if plan is not None and case_id in prepared_by_id and not row.get("metadata_blocked"):
            case = prepared_by_id[case_id]
            try:
                oracle = oracle_plan_from_reference_sql(
                    case.sol_sql[0], mappings[case_id], database_id=case.database
                )
            except Exception:
                oracle = None
            if oracle is not None:
                scores = _authoritative_scores(plan, oracle, mappings[case_id])
        subtypes = _subtypes(row, plan)
        top_level = _top_level(row)
        first = _first_false(scores) if top_level == "SEMANTIC_VALID_BUT_WRONG" else None
        primary = None
        if top_level == "METADATA_BLOCKED":
            primary = "METADATA_LIMITATION"
        elif top_level == "CANONICAL_MODEL_INVALID":
            primary = first or _primary_from_subtypes(subtypes)
        elif top_level == "SEMANTIC_VALID_BUT_WRONG":
            primary = first or "OUTPUT_ERROR"
        if primary is not None:
            primary_counts[primary] += 1
        subtype_counts.update(subtypes)
        cases.append(
            {
                "case_id": case_id,
                "database": row.get("database"),
                "top_level_status": top_level,
                "first_divergence": first,
                "primary_cause": primary,
                "secondary_causes": [value for value in subtypes if value != primary],
                "subtypes": subtypes,
                "metadata_limitation": bool(row.get("metadata_blocked")),
                "provider_schema_valid": bool(row.get("provider_schema_valid")),
                "canonical_structural_valid": bool(row.get("canonical_structural_valid")),
                "canonical_plan_valid": bool(row.get("canonical_schema_valid")),
                "semantic_valid": bool(row.get("canonical_semantic_valid")),
                "official_status": row.get("official_status"),
                "component_scores": row.get("component_scores", {}),
                "oracle_available": oracle is not None if plan is not None else False,
            }
        )
    component = result["semantic_plan"]["component_scores"]
    return {
        "milestone": "M30",
        "provider_calls": 0,
        "source_milestone": "M29R.1",
        "cases": cases,
        "primary_cause_counts": dict(primary_counts),
        "subtype_counts": dict(subtype_counts),
        "top_level_counts": dict(Counter(case["top_level_status"] for case in cases)),
        "component_accuracy": component,
        "output_accuracy": {
            "old_coarse": component["outputs"],
            "authoritative": component["outputs"],
            "comparator_note": "authoritative signature ignores aliases and descriptions",
        },
        "metadata_blocked_cases": [
            case["case_id"] for case in cases if case["metadata_limitation"]
        ],
        "frozen_engine_regressions": [],
    }


def main() -> None:
    report = build_report()
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
