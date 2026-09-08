from __future__ import annotations

import json
from pathlib import Path

from benchmark.m45_contract import EXPECTED_BENCHMARK_HASH, EXPECTED_M43_PROMPT_HASH


def test_m45_parent_and_applicability_inventory() -> None:
    contract = json.loads(Path("benchmark/manifests/m45_contract.json").read_text())
    inventory = json.loads(
        Path("benchmark/audits/m45_additive_alignment_inventory.json").read_text()
    )
    assert contract["parent_contract"] == "benchmark/manifests/m43_contract.json"
    assert contract["old_m43_prompt_hash"] == EXPECTED_M43_PROMPT_HASH
    assert contract["hashes"]["benchmark_content_hash"] == EXPECTED_BENCHMARK_HASH
    assert inventory["counts"] == {"APPLICABLE": 3, "NOT_APPLICABLE": 57}
    assert inventory["uncertain_cases"] == []
    assert inventory["applicable_cases"] == ["subscription_04", "subscription_10", "warehouse_08"]


def test_m45_prompt_has_narrow_boundaries_and_no_m44_or_case_hints() -> None:
    prompt = Path("benchmark/prompts/governed_context_v1.md").read_text()
    for phrase in (
        "Typed JSON scalar semantics",
        "Parent/child additive measure alignment",
        "aggregate the child additive values by the declared parent key",
        "exactly once for each parent row",
        "SUM(DISTINCT parent_measure)",
    ):
        assert phrase in prompt
    for forbidden in (
        "Native measure grain and fanout",
        "Authorized relationship semantics",
        "warehouse_08",
        "risk_05",
        "subscription_04",
        "purchase_order_lines",
        "receipts",
    ):
        assert forbidden not in prompt


def test_m45_request_isolation_is_complete() -> None:
    audit = json.loads(Path("benchmark/manifests/m45_paired_request_audit.json").read_text())
    assert audit["counts"] == {
        "question_identical": 90,
        "context_identical": 90,
        "case_id_identical": 90,
        "provider_schema_identical": 90,
        "model_config_identical": 90,
        "prompt_changed": 90,
        "full_request_changed": 90,
    }
