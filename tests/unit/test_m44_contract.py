from __future__ import annotations

import json
from pathlib import Path

from benchmark.m44_contract import EXPECTED_BENCHMARK_HASH, EXPECTED_M43_PROMPT_HASH


def test_m44_parent_and_benchmark_are_frozen() -> None:
    contract = json.loads(Path("benchmark/manifests/m44_contract.json").read_text())
    assert contract["parent_contract"] == "benchmark/manifests/m43_contract.json"
    assert contract["old_m43_prompt_hash"] == EXPECTED_M43_PROMPT_HASH
    assert contract["hashes"]["benchmark_content_hash"] == EXPECTED_BENCHMARK_HASH


def test_m44_prompt_has_only_generic_fanout_language() -> None:
    prompt = Path("benchmark/prompts/governed_context_v1.md").read_text()
    for phrase in (
        "Typed JSON scalar semantics",
        "Native measure grain and fanout",
        "one-to-many relationship",
        "native semantic grain",
        "generic substitute for correct grain handling",
    ):
        assert phrase in prompt
    for forbidden in (
        "warehouse_08",
        "purchase_order_lines",
        "receipts",
        "ordered_qty",
        "received_qty",
    ):
        assert forbidden not in prompt
    assert "Authorized relationship semantics" not in prompt


def test_m44_request_isolation_is_complete() -> None:
    audit = json.loads(Path("benchmark/manifests/m44_paired_request_audit.json").read_text())
    assert audit["counts"] == {
        "question_identical": 90,
        "context_identical": 90,
        "case_id_identical": 90,
        "provider_schema_identical": 90,
        "model_config_identical": 90,
        "prompt_changed": 90,
        "full_request_changed": 90,
    }
