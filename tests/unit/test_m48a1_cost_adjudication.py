from __future__ import annotations

import json
from pathlib import Path

from app.execution.cost import QueryCostGate
from app.sql.models import ExplainEstimate
from benchmark.m48a1_audit import rejection_inventory

ROOT = Path(__file__).resolve().parents[2]


def test_m48a1_identifies_the_three_frozen_rejections() -> None:
    result = rejection_inventory()
    assert result["identified"] is True
    assert result["all_warehouse_13_reference_b"] is True
    assert [item["fixture_id"] for item in result["rejected_executions"]] == [
        "base",
        "warehouse_13_cf1_c305ad",
        "warehouse_13_cf2_b6ed47",
    ]
    assert {item["total_cost"] for item in result["rejected_executions"]} == {114604.02}


def test_m48a1_dual_contract_artifacts_are_consistent() -> None:
    summary = json.loads(
        (ROOT / "benchmark/reports/m48a1_cost_adjudication_summary.json").read_text()
    )
    semantic = summary["semantic_validity"]
    runtime = summary["runtime_disposition"]
    assert semantic["references"]["references_analyzed"] == 120
    assert semantic["references"]["fixture_comparisons"] == 184
    assert semantic["references"]["agreement_failures"] == []
    assert semantic["mutations"]["killed"] == 190
    assert runtime["counts"] == {"ALLOWED": 365, "QUERY_COST_REJECTION": 3}
    assert runtime["all_allowed_correct"] is True
    assert runtime["cost_rejected_execution_zero"] is True
    assert summary["classification"] == "REFERENCE_RUNTIME_WITNESS_SPLIT_SUPPORTED"


def test_m48a1_does_not_change_cost_gate_threshold_semantics() -> None:
    gate = QueryCostGate()
    assert gate.exceeds(
        ExplainEstimate(plan_rows=810, total_cost=114604.02, top_level_node_type="Index Only Scan"),
        100000,
        100000.0,
    )
    assert not gate.exceeds(
        ExplainEstimate(plan_rows=810, total_cost=280.92, top_level_node_type="Sort"),
        100000,
        100000.0,
    )
