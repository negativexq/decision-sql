from pathlib import Path

from benchmark import m42_contract
from benchmark.model_contract import governance_instructions


def test_m42_prompt_clarifies_authorized_traversal_without_case_hints() -> None:
    prompt = governance_instructions()
    required = (
        "opposite SQL join orientation",
        "multi-hop path",
        "undeclared direct relationship",
        "same entity",
        "Attribute visibility is not relationship authorization",
        "Never infer a relationship",
    )
    for phrase in required:
        assert phrase in prompt
    forbidden = (
        "M41",
        "warehouse_03",
        "warehouse_07",
        "warehouse_13",
        "risk_11",
        "reference SQL",
        "semantic_target",
        "expected_decision",
    )
    for phrase in forbidden:
        assert phrase not in prompt


def test_m42_offline_audit_supports_hypothesis_without_provider_calls() -> None:
    result = m42_contract.audit_authority_semantics()
    assert result["hypothesis_status"] == "SUPPORTED_OFFLINE"
    assert result["provider_calls"] == 0
    assert result["same_entity_attributes_require_relationship"] is False
    assert result["undeclared_direct_shortcut_authorized_by_composition"] is False


def test_m42_historical_preservation_report_exists_and_is_nonempty() -> None:
    report = Path("benchmark/reports/m42_historical_preservation.json")
    assert report.exists()
    assert report.read_text(encoding="utf-8").strip()
