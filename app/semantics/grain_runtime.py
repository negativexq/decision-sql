"""Runtime boundary for the frozen deterministic grain normalizer."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.semantics.grain import (
    GrainDiagnostic,
    GrainDiagnosticCode,
    GrainSafetyValidator,
    MeasureCatalog,
)
from app.semantics.grain_normalizer import (
    GrainNormalizationResult,
    GrainSafeNormalizer,
    NormalizationReason,
    NormalizationStatus,
)


class GrainRuntimeStatus(StrEnum):
    UNCHANGED = "UNCHANGED"
    NORMALIZED = "NORMALIZED"
    REJECTED = "REJECTED"


class GrainRuntimeReason(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ALREADY_SAFE = "ALREADY_SAFE"
    NORMALIZED_CHILD_PREAGGREGATION = "NORMALIZED_CHILD_PREAGGREGATION"
    ABSTAIN_UNSUPPORTED = "ABSTAIN_UNSUPPORTED"
    NORMALIZER_ERROR = "NORMALIZER_ERROR"
    POST_NORMALIZATION_UNSAFE = "POST_NORMALIZATION_UNSAFE"


class GrainNormalizer(Protocol):
    def normalize(self, sql: str) -> GrainNormalizationResult:
        """Return one deterministic normalization result."""


class GrainRuntimeDecision(BaseModel):
    """Immutable, machine-auditable decision at the SQL planning boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GrainRuntimeStatus
    selected_sql: str
    input_sql_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_sql_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_diagnostic: GrainDiagnostic
    output_diagnostic: GrainDiagnostic
    normalization_status: NormalizationStatus | None = None
    normalization_reason: NormalizationReason | None = None
    runtime_reason: GrainRuntimeReason
    fanout_relationship_ids: tuple[str, ...] = ()
    parent_measure_ids: tuple[str, ...] = ()
    child_measure_ids: tuple[str, ...] = ()


def _hash_sql(sql: str) -> str:
    return sha256(sql.encode("utf-8")).hexdigest()


class RuntimeGrainSafetyCoordinator:
    """Apply the frozen normalizer or fail closed at planning time."""

    def __init__(
        self,
        catalog: MeasureCatalog,
        *,
        validator: GrainSafetyValidator | None = None,
        normalizer: GrainNormalizer | None = None,
    ) -> None:
        catalog.validate_contract()
        self.catalog = catalog
        self.validator = validator or GrainSafetyValidator(catalog)
        self.normalizer = normalizer or GrainSafeNormalizer(catalog)

    def inspect(self, sql: str) -> GrainRuntimeDecision:
        input_diagnostic = self.validator.validate(sql)
        if input_diagnostic.code is not GrainDiagnosticCode.PARENT_MEASURE_FANOUT:
            reason = (
                GrainRuntimeReason.ALREADY_SAFE
                if input_diagnostic.code
                in {GrainDiagnosticCode.PASS, GrainDiagnosticCode.NOT_APPLICABLE}
                else GrainRuntimeReason.NOT_APPLICABLE
            )
            return self._decision(
                input_sql=sql,
                status=GrainRuntimeStatus.UNCHANGED,
                selected_sql=sql,
                input_diagnostic=input_diagnostic,
                output_diagnostic=input_diagnostic,
                runtime_reason=reason,
            )

        try:
            result = self.normalizer.normalize(sql)
        except Exception:
            return self._decision(
                input_sql=sql,
                status=GrainRuntimeStatus.REJECTED,
                selected_sql=sql,
                input_diagnostic=input_diagnostic,
                output_diagnostic=input_diagnostic,
                runtime_reason=GrainRuntimeReason.NORMALIZER_ERROR,
            )

        if result.status is not NormalizationStatus.NORMALIZED:
            return self._decision(
                input_sql=sql,
                status=GrainRuntimeStatus.REJECTED,
                selected_sql=sql,
                input_diagnostic=input_diagnostic,
                output_diagnostic=result.output_diagnostic,
                normalization_status=result.status,
                normalization_reason=result.reason_code,
                runtime_reason=GrainRuntimeReason.ABSTAIN_UNSUPPORTED,
                fanout_relationship_ids=result.fanout_relationship_ids,
                parent_measure_ids=result.parent_measure_ids,
                child_measure_ids=result.child_measure_ids,
            )

        if result.output_diagnostic.code not in {
            GrainDiagnosticCode.PASS,
            GrainDiagnosticCode.NOT_APPLICABLE,
        }:
            return self._decision(
                input_sql=sql,
                status=GrainRuntimeStatus.REJECTED,
                selected_sql=sql,
                input_diagnostic=input_diagnostic,
                output_diagnostic=result.output_diagnostic,
                normalization_status=result.status,
                normalization_reason=result.reason_code,
                runtime_reason=GrainRuntimeReason.POST_NORMALIZATION_UNSAFE,
                fanout_relationship_ids=result.fanout_relationship_ids,
                parent_measure_ids=result.parent_measure_ids,
                child_measure_ids=result.child_measure_ids,
            )

        return self._decision(
            input_sql=sql,
            status=GrainRuntimeStatus.NORMALIZED,
            selected_sql=result.output_sql,
            input_diagnostic=input_diagnostic,
            output_diagnostic=result.output_diagnostic,
            normalization_status=result.status,
            normalization_reason=result.reason_code,
            runtime_reason=GrainRuntimeReason.NORMALIZED_CHILD_PREAGGREGATION,
            fanout_relationship_ids=result.fanout_relationship_ids,
            parent_measure_ids=result.parent_measure_ids,
            child_measure_ids=result.child_measure_ids,
        )

    @staticmethod
    def _decision(
        *,
        input_sql: str,
        status: GrainRuntimeStatus,
        selected_sql: str,
        input_diagnostic: GrainDiagnostic,
        output_diagnostic: GrainDiagnostic,
        runtime_reason: GrainRuntimeReason,
        normalization_status: NormalizationStatus | None = None,
        normalization_reason: NormalizationReason | None = None,
        fanout_relationship_ids: tuple[str, ...] = (),
        parent_measure_ids: tuple[str, ...] = (),
        child_measure_ids: tuple[str, ...] = (),
    ) -> GrainRuntimeDecision:
        return GrainRuntimeDecision(
            status=status,
            selected_sql=selected_sql,
            input_sql_hash=_hash_sql(input_sql),
            selected_sql_hash=_hash_sql(selected_sql),
            input_diagnostic=input_diagnostic,
            output_diagnostic=output_diagnostic,
            normalization_status=normalization_status,
            normalization_reason=normalization_reason,
            runtime_reason=runtime_reason,
            fanout_relationship_ids=fanout_relationship_ids,
            parent_measure_ids=parent_measure_ids,
            child_measure_ids=child_measure_ids,
        )
