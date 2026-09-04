"""Typed, provider-free models for semantic-verifier diagnostics."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VerificationSeverity(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"


class VerificationRecommendation(StrEnum):
    CONTINUE = "CONTINUE"
    SUSPICIOUS = "SUSPICIOUS"
    CLARIFICATION_CANDIDATE = "CLARIFICATION_CANDIDATE"


class VerificationSignalCode(StrEnum):
    REQUESTED_GROUPING_MISSING = "REQUESTED_GROUPING_MISSING"
    TOP_N_STRUCTURE_MISSING = "TOP_N_STRUCTURE_MISSING"
    AGGREGATE_THRESHOLD_FILTER_MISSING = "AGGREGATE_THRESHOLD_FILTER_MISSING"
    POSSIBLE_AGGREGATION_MISMATCH = "POSSIBLE_AGGREGATION_MISMATCH"
    POSSIBLE_ENTITY_COUNT_FANOUT = "POSSIBLE_ENTITY_COUNT_FANOUT"
    POSSIBLE_AGGREGATE_FANOUT = "POSSIBLE_AGGREGATE_FANOUT"
    REQUESTED_DIMENSION_NOT_PROJECTED = "REQUESTED_DIMENSION_NOT_PROJECTED"
    REQUESTED_MEASURE_NOT_PROJECTED = "REQUESTED_MEASURE_NOT_PROJECTED"
    TOP_N_WITHOUT_ORDER = "TOP_N_WITHOUT_ORDER"
    TOP_N_LIMIT_MISSING = "TOP_N_LIMIT_MISSING"
    GOVERNED_METRIC_SHOULD_USE_SEMANTIC_PATH = "GOVERNED_METRIC_SHOULD_USE_SEMANTIC_PATH"
    RETIRED_OR_INVALID_SEMANTIC_OBJECT = "RETIRED_OR_INVALID_SEMANTIC_OBJECT"
    POSSIBLE_EXAMPLE_OVERTRANSFER = "POSSIBLE_EXAMPLE_OVERTRANSFER"
    EXPLICIT_ENTITY_NOT_GROUNDED = "EXPLICIT_ENTITY_NOT_GROUNDED"
    OUTPUT_SHAPE_SUSPICIOUS = "OUTPUT_SHAPE_SUSPICIOUS"
    UNEXPECTED_CARTESIAN_JOIN = "UNEXPECTED_CARTESIAN_JOIN"
    EXPLICIT_FILTER_MISSING = "EXPLICIT_FILTER_MISSING"
    UNREQUESTED_LIMIT = "UNREQUESTED_LIMIT"
    UNREQUESTED_FILTER = "UNREQUESTED_FILTER"
    DIRECT_PATH_SEMANTIC_CONTRADICTION = "DIRECT_PATH_SEMANTIC_CONTRADICTION"


class VerificationSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: VerificationSignalCode
    severity: VerificationSeverity
    evidence: tuple[tuple[str, str], ...] = ()


class SemanticVerificationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verifier_version: str
    ruleset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    signals: tuple[VerificationSignal, ...] = ()
    recommendation: VerificationRecommendation

    @property
    def hard_signals(self) -> tuple[VerificationSignal, ...]:
        return tuple(
            signal for signal in self.signals if signal.severity is VerificationSeverity.HARD
        )
