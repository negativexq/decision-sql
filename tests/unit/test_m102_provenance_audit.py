from __future__ import annotations

import json

import pytest

from evaluation.m102_provenance_audit import (
    FIXTURES,
    PROFILE_FIELDS,
    build_summary,
    validate_contract,
)


def test_m102_contract_is_complete_and_historical_inputs_are_frozen() -> None:
    summary = build_summary()

    assert summary["classification"] == "M102_PROVENANCE_CAPTURE_CONTRACT_READY"
    assert summary["m10r_primary"] == "28/200"
    assert summary["m10r_consumed"] == 200
    assert summary["m101_classification"] == "M101_AUDIT_BLOCKED"
    assert summary["validation"]["runtime_activity"] == {
        "provider_calls": 0,
        "database_calls": 0,
        "retrieval_calls": 0,
        "generation_calls": 0,
        "grounding_calls": 0,
        "embedding_calls": 0,
        "reranker_calls": 0,
    }
    assert all(PROFILE_FIELDS.values())


def test_m102_secret_fixture_is_rejected_without_treating_it_as_contract_data() -> None:
    fixtures = json.loads((FIXTURES / "m102_provenance_contract_fixtures.json").read_text())
    assert fixtures["secret_payload"]["authorization_header"].startswith("Bearer ")
    assert validate_contract()["prohibited_field_count"] == 7


def test_m102_hypothesis_coverage_and_seams_are_declared() -> None:
    matrix = json.loads((FIXTURES / "m102_hypothesis_field_matrix.json").read_text())
    seams = json.loads((FIXTURES / "m102_capture_seam_matrix.json").read_text())

    hypothesis_ids = {item["id"] for item in matrix["hypotheses"]}
    assert hypothesis_ids == {
        "MEMORY_SELECTIVITY",
        "MEMORY_OVERTRANSFER",
        "DOWNSTREAM_GENERATION",
        "GOVERNED_SLOT",
        "GOVERNED_COMPILER",
        "ROUTING_OVERREACH",
        "M1_STRUCTURE",
    }
    stages = {item["stage"] for item in seams["seams"]}
    assert {"MEMORY_RETRIEVAL_RESULT", "MEMORY_SELECTION", "GOVERNED_GROUNDING_RESULT"} <= stages


def test_m102_does_not_modify_application_runtime() -> None:
    source = (FIXTURES / "m102_evidence_manifest.json").read_text()

    assert "app/" in source
    assert "retrieval" in source
    assert build_summary()["m10r_artifacts_modified"] is False


def test_m102_contract_validation_rejects_an_unknown_profile_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path = FIXTURES / "m102_provenance_contract.json"
    original = contract_path.read_text()
    try:
        data = json.loads(original)
        data["profiles"]["DIRECT_DIAGNOSTIC"].append("unknown_field")
        monkeypatch.setattr(
            "evaluation.m102_provenance_audit.load",
            lambda name: (
                data
                if name == "m102_provenance_contract.json"
                else json.loads((FIXTURES / name).read_text())
            ),
        )
        with pytest.raises(ValueError, match="unknown fields"):
            validate_contract()
    finally:
        assert contract_path.read_text() == original
