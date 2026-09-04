# ruff: noqa: E501

"""DB/provider-free tests for the frozen M10R protocol artifacts."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

from evaluation.m10r_reconstruct import transport_preflight

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "evaluation" / "fixtures"


def _cases() -> list[dict[str, Any]]:
    payload = cast(dict[str, Any], json.loads((FIXTURES / "m10r_cases_v2.json").read_text()))
    return cast(list[dict[str, Any]], payload["cases"])


def test_m10r_counts_and_categories_are_frozen() -> None:
    cases = _cases()
    assert len(cases) == 200
    assert Counter(case["split"] for case in cases) == {"dev": 120, "holdout": 80}
    assert Counter(case["expected_route"] for case in cases) == {"GOVERNED": 50, "DIRECT": 150}
    assert len({case["case_id"] for case in cases}) == 200
    assert Counter(case["family"] for case in cases if case["expected_route"] == "DIRECT") == {
        "FILTER_PROJECTION": 24,
        "MULTI_TABLE_JOIN": 24,
        "AGGREGATE_GROUP": 24,
        "ORDER_TOPN": 14,
        "FORMULA_RATIO": 18,
        "TEMPORAL": 14,
        "WINDOW": 14,
        "DISTINCT_OR_SET": 8,
        "MIXED_COMPOSITION": 10,
    }


def test_all_frozen_references_pass_offline_transport_preflight() -> None:
    for case in _cases():
        for sql in case["reference_sql_variants"]:
            result = transport_preflight(sql)
            assert result["parse"] == "PASSED"
            assert result["transport_status"] == "TRANSPORT_SAFE"


def test_transport_preflight_regresses_the_v1_failure_mechanism() -> None:
    assert transport_preflight("SELECT p.sku FROM products AS p WHERE p.sku LIKE 'Z%'")["transport_status"] == "TRANSPORT_INCOMPATIBLE"
    assert transport_preflight("SELECT p.sku FROM products AS p WHERE p.sku LIKE 'Z%%'")["transport_status"] == "TRANSPORT_SAFE"


def test_generation_request_boundary_does_not_include_benchmark_metadata() -> None:
    tree = ast.parse((ROOT / "evaluation/run_m10r.py").read_text())
    requests = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "TextToSqlRequest"]
    assert len(requests) == 1
    keywords = {keyword.arg for keyword in requests[0].keywords}
    assert keywords == {"question", "correlation_id", "execute"}
