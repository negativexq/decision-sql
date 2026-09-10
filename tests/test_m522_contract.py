from __future__ import annotations

import pytest
from pydantic import ValidationError

from benchmark.m521_runner import detect_group_survival
from benchmark.m522_contract import (
    CarrierV0,
    MeasureV0,
    PopulationInclusionMode,
    PopulationSemanticContractV0,
    ResultGrainV0,
    contract_schema,
    validate_entity_references,
)


def _contract() -> PopulationSemanticContractV0:
    return PopulationSemanticContractV0(
        result_grain=ResultGrainV0(entity_id="entity:demo:customers"),
        carrier=CarrierV0(
            entity_id="entity:demo:customers",
            inclusion=PopulationInclusionMode.ALL_BASE_ENTITIES,
        ),
        measure=MeasureV0(
            source_entity_id="entity:demo:payments",
            contributing_population_ref="semantic:demo:late_payment",
        ),
    )


def test_v0_is_strict_and_canonical() -> None:
    first = _contract().model_dump_json()
    second = _contract().model_dump_json()
    assert first == second
    assert contract_schema()["additionalProperties"] is False
    assert contract_schema()["$defs"]["CarrierV0"]["additionalProperties"] is False


def test_v0_rejects_oracle_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PopulationSemanticContractV0(
            **_contract().model_dump(),
            expected_group_survival=True,  # type: ignore[call-arg]
        )


def test_v0_rejects_invalid_population_enum() -> None:
    data = _contract().model_dump()
    data["carrier"]["inclusion"] = "SHOULD_USE_LEFT_JOIN"
    with pytest.raises(ValidationError):
        PopulationSemanticContractV0.model_validate(data)


def test_entity_reference_validation_is_separate_from_semantics() -> None:
    validate_entity_references(
        _contract(),
        {"entity:demo:customers", "entity:demo:payments"},
    )
    with pytest.raises(ValueError):
        validate_entity_references(_contract(), {"entity:demo:customers"})


def test_v0_supports_matching_only_and_different_measure_source() -> None:
    contract = PopulationSemanticContractV0(
        result_grain=ResultGrainV0(entity_id="entity:demo:customers"),
        carrier=CarrierV0(
            entity_id="entity:demo:customers",
            inclusion=PopulationInclusionMode.MATCHING_ENTITIES_ONLY,
        ),
        measure=MeasureV0(source_entity_id="entity:demo:orders"),
    )
    assert contract.carrier.inclusion is PopulationInclusionMode.MATCHING_ENTITIES_ONLY
    assert contract.measure.source_entity_id != contract.carrier.entity_id


def test_runtime_signal_is_unchanged_by_forensic_fields() -> None:
    sql = "SELECT p.id FROM parents p JOIN children c ON c.parent_id = p.id"
    permitted = {
        "population_requirement": "PRESERVE_BASE_ENTITIES",
        "base_entity": "parents",
    }
    with_forensic_fields = {
        **permitted,
        "truth": "ANSWERABLE",
        "reference_sql": "SELECT 1",
        "expected_result": [{"id": 1}],
        "case_id": "not-a-runtime-input",
    }
    assert detect_group_survival(sql, permitted) == detect_group_survival(sql, with_forensic_fields)
