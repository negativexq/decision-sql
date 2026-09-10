"""Audit-only population semantic contract proposal for M52.2.

This module is deliberately outside ``app/``.  It describes a possible
production semantic-layer contract but is not consumed by generation,
validation, or runtime execution.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class PopulationInclusionMode(StrEnum):
    ALL_BASE_ENTITIES = "ALL_BASE_ENTITIES"
    MATCHING_ENTITIES_ONLY = "MATCHING_ENTITIES_ONLY"


class ResultGrainV0(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str


class CarrierV0(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    inclusion: PopulationInclusionMode


class MeasureV0(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_entity_id: str
    contributing_population_ref: str | None = None


class PopulationSemanticContractV0(BaseModel):
    """Minimal typed proposal; audit-only and intentionally no SQL escape hatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["PopulationSemanticContractV0"] = "PopulationSemanticContractV0"
    result_grain: ResultGrainV0
    carrier: CarrierV0
    measure: MeasureV0


def validate_entity_references(
    contract: PopulationSemanticContractV0, allowed_entity_ids: set[str]
) -> None:
    references = {
        contract.result_grain.entity_id,
        contract.carrier.entity_id,
        contract.measure.source_entity_id,
    }
    unknown = sorted(references - allowed_entity_ids)
    if unknown:
        raise ValueError(f"unknown entity references: {unknown}")


def contract_schema() -> dict[str, Any]:
    return PopulationSemanticContractV0.model_json_schema()


__all__ = [
    "CarrierV0",
    "MeasureV0",
    "PopulationInclusionMode",
    "PopulationSemanticContractV0",
    "ResultGrainV0",
    "ValidationError",
    "contract_schema",
    "validate_entity_references",
]
