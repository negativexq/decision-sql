from __future__ import annotations

import json
from pathlib import Path

from benchmark.analysis_serialization import canonicalize_analysis_value, dumps_analysis


def test_m52_typed_key_serializer_preserves_null_and_is_deterministic() -> None:
    value = {None: 6, "RELATIONSHIP": 10, "SCHEMA_OBJECT": 5}
    first = dumps_analysis(value)
    second = dumps_analysis(value)
    assert first == second
    encoded = canonicalize_analysis_value(value)
    assert encoded["__typed_mapping__"][0] == {"key": None, "value": 6}
    assert [row["key"] for row in encoded["__typed_mapping__"]] == [
        None,
        "RELATIONSHIP",
        "SCHEMA_OBJECT",
    ]


def test_m52_frozen_failure_accounting_is_complete() -> None:
    root = Path(__file__).parents[1] / "benchmark" / "audits" / "m52"
    inventory = [
        json.loads(line) for line in (root / "m52_failure_inventory.jsonl").read_text().splitlines()
    ]
    result = [
        json.loads(line)
        for line in (root / "m52_result_semantic_failure_inventory.jsonl").read_text().splitlines()
    ]
    assert len(inventory) == 43
    assert len(result) == 26
    assert len({row["case_id"] for row in result}) == 26
    assert {row["case_id"] for row in result if not row["base_correct"]}.__len__() == 19
    assert {row["case_id"] for row in result if row["base_correct"]} == {
        "insurance_07",
        "telecom_02",
        "telecom_05",
        "marketplace_06",
        "marketplace_08",
        "workforce_06",
        "healthcare_11",
    }


def test_m52_decision_matrix_and_root_partition_sum() -> None:
    root = Path(__file__).parents[1] / "benchmark" / "audits" / "m52"
    matrix = (
        json.loads((root / "m52_truth_decision_matrix.json").read_text())
        if (root / "m52_truth_decision_matrix.json").exists()
        else None
    )
    if matrix is not None:
        assert sum(sum(row.values()) for row in matrix.values()) == 90
    partition = json.loads((root / "m52_primary_root_causes.json").read_text())
    assert sum(partition["counts"].values()) == 26
