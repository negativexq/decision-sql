"""Recompute M32 alignment diagnostics from preserved raw captures.

This does not call a provider or alter frozen funnel outcomes.  It exists so
the post-run forensic comparator can evolve without losing M32's raw evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from app.semantics.m32_alignment import (
    LogicalSynthesisV1,
    QueryAlignmentV1,
    SchemaAlignmentV1,
    combine_schema_and_logic,
)
from evaluation.m32_alignment import alignment_component_match
from evaluation.run_m32 import _load_cases

ROOT = Path(__file__).resolve().parents[1]


def _capture(version: str, case_id: str, stage: str) -> dict[str, Any]:
    path = ROOT / "evaluation/external/livesqlbench/protected/results/m32" / version
    value = json.loads((path / case_id / f"{stage}.json").read_text(encoding="utf-8"))[
        "parsed_operation_plan"
    ]
    return cast(dict[str, Any], value)


def _alignment(version: str, case_id: str) -> QueryAlignmentV1:
    if version == "v2":
        schema = SchemaAlignmentV1.model_validate(_capture(version, case_id, "schema"))
        logic = LogicalSynthesisV1.model_validate(_capture(version, case_id, "logic"))
        return combine_schema_and_logic(schema, logic)
    return QueryAlignmentV1.model_validate(_capture(version, case_id, "alignment"))


def recompute(version: str) -> None:
    result_path = ROOT / "evaluation/fixtures" / f"m32_{version}_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    cases, _contexts, _mappings, _states, expected = _load_cases()
    for row in result["rows"]:
        expected_alignment = expected[row["case_id"]]
        valid = (
            row.get("alignment_semantic_valid")
            if version != "v2"
            else row.get("schema_semantic_valid") and row.get("logic_semantic_valid")
        )
        if expected_alignment is None or not valid:
            row["alignment_components"] = {}
            row["alignment_correct"] = None
            continue
        alignment = _alignment(version, row["case_id"])
        components = alignment_component_match(alignment, expected_alignment)
        row["alignment_components"] = components
        row["alignment_correct"] = all(components.values())

    component_counts: dict[str, dict[str, int]] = {}
    names = (
        "entities",
        "attributes",
        "relationships",
        "population",
        "filters",
        "aggregation",
        "grouping",
        "calculation",
        "ordering",
        "limit",
        "temporal",
        "query_shape",
    )
    for component in names:
        applicable = [
            row for row in result["rows"] if component in row.get("alignment_components", {})
        ]
        component_counts[component] = {
            "applicable": len(applicable),
            "correct": sum(bool(row["alignment_components"].get(component)) for row in applicable),
        }
    aligned = [row for row in result["rows"] if row.get("alignment_correct") is True]
    result["alignment_component_accuracy"] = component_counts
    result["alignment_sql_matrix"] = {
        "alignment_correct_sql_correct": sum(
            row.get("alignment_correct") is True and row.get("official") == "CORRECT"
            for row in result["rows"]
        ),
        "alignment_correct_sql_wrong": sum(
            row.get("alignment_correct") is True and row.get("official") == "INCORRECT"
            for row in result["rows"]
        ),
        "alignment_wrong_sql_correct": sum(
            row.get("alignment_correct") is False and row.get("official") == "CORRECT"
            for row in result["rows"]
        ),
        "alignment_wrong_sql_wrong": sum(
            row.get("alignment_correct") is False and row.get("official") == "INCORRECT"
            for row in result["rows"]
        ),
    }
    result["sql_correct_conditional_on_alignment"] = {
        "aligned_cases": len(aligned),
        "correct": sum(row.get("official") == "CORRECT" for row in aligned),
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    for version in ("v1", "v2", "v3"):
        recompute(version)
