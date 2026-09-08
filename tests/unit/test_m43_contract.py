import json
from pathlib import Path

from benchmark import m43_contract
from benchmark.model_contract import governance_instructions


def test_m43_parent_and_typed_json_prompt_contract() -> None:
    parent = m43_contract.verify_parent()
    assert parent["parent_prompt_matches"] is True
    assert parent["parent_prompt_hash"] == m43_contract.EXPECTED_M41_PROMPT_HASH
    prompt = governance_instructions()
    for phrase in (
        "Typed JSON scalar semantics",
        "JSON-derived attribute",
        "text-returning JSON extraction",
        "numeric comparison",
        "arithmetic",
        "aggregation",
        "numeric ordering",
        "Do not infer a JSON path",
    ):
        assert phrase in prompt
    for forbidden in (
        "Authorized relationship semantics",
        "opposite SQL join orientation",
        "multi-hop path",
        "fleet_06",
        "warehouse_09",
        "temperature_c",
        "M42",
        "reference SQL",
    ):
        assert forbidden not in prompt


def test_m43_json_inventory_supports_one_generic_hypothesis() -> None:
    inventory = m43_contract.audit_json_semantics()
    assert inventory["hypothesis_status"] == "SUPPORTED_OFFLINE"
    assert inventory["counts"] == {
        "json_derived_attributes": 5,
        "documented_numeric_json_attributes": 4,
        "text_returning_extractions": 5,
        "hash_extractors": 4,
        "arrow_extractors": 1,
        "answerable_cases_using_json": 7,
    }
    assert inventory["provider_calls"] == 0


def test_m43_preservation_includes_m42_evidence() -> None:
    preservation = m43_contract.preserve_historical_evidence()
    assert preservation["provider_calls"] == 0
    assert any("results/m42/" in path for path in preservation["files"])
    assert any("forensics/m42/" in path for path in preservation["files"])


def test_m43_config_is_single_call_m41_control() -> None:
    config = json.loads(
        Path("benchmark/experiments/m43_typed_json_numeric_semantics.json").read_text()
    )
    assert config["experiment_id"] == "m43_typed_json_numeric_semantics"
    assert config["model"] == "gpt-5.6-luna"
    assert config["reasoning"] == "none"
    assert config["temperature"] == 0.0
    assert config["calls_per_case"] == 1
    assert config["transport_retries"] == 0
    assert config["semantic_retries"] == 0
    assert config["repair"] is False
    assert config["selector"] is False
    assert config["judge"] is False
