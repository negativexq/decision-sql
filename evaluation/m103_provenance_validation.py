"""DB-free validation for the M10.3 runtime provenance ledger."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.provenance.canonical import semantic_hash
from app.provenance.models import ProvenanceEvent, ProvenanceStage
from app.provenance.sink import PROHIBITED_KEYS

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT = json.loads((FIXTURES / "m102_provenance_contract.json").read_text())
PROFILE_FIELDS: dict[str, tuple[str, ...]] = {
    key: tuple(value) for key, value in CONTRACT["profiles"].items()
}
ENVELOPE_FIELDS = frozenset(
    {"run_id", "case_id", "stage", "event_type", "sequence", "schema_version", "payload_hash"}
)
PROFILE_STAGES = {
    "MEMORY_DIAGNOSTIC": frozenset(
        {
            ProvenanceStage.MEMORY_RETRIEVAL_RESULT,
            ProvenanceStage.MEMORY_SELECTION,
            ProvenanceStage.GENERATION_CONTEXT,
        }
    ),
    "GOVERNED_DIAGNOSTIC": frozenset(
        {
            ProvenanceStage.GOVERNED_GROUNDING_REQUEST,
            ProvenanceStage.PROVIDER_REQUEST,
            ProvenanceStage.PROVIDER_RESPONSE,
            ProvenanceStage.GOVERNED_GROUNDING_RESULT,
            ProvenanceStage.GOVERNED_COMPILER_INPUT,
            ProvenanceStage.GOVERNED_COMPILER_OUTPUT,
        }
    ),
    "DIRECT_DIAGNOSTIC": frozenset(
        {
            ProvenanceStage.GENERATION_CONTEXT,
            ProvenanceStage.PROVIDER_REQUEST,
            ProvenanceStage.PROVIDER_RESPONSE,
            ProvenanceStage.CANDIDATE_EXTRACTION,
        }
    ),
}
PROFILE_STAGES["FULL_DIAGNOSTIC"] = frozenset(ProvenanceStage)


@dataclass(frozen=True)
class ProvenanceCompleteness:
    profile: str
    complete: bool
    event_count: int
    missing_fields: tuple[str, ...] = ()
    invalid_events: tuple[str, ...] = ()
    order_valid: bool = True


def validate_provenance(
    events: Iterable[ProvenanceEvent | dict[str, Any]], profile: str
) -> ProvenanceCompleteness:
    """Validate presence, hashes, and causal order without benchmark truth."""
    if profile not in PROFILE_FIELDS:
        raise ValueError(f"unknown provenance profile: {profile}")
    parsed: list[ProvenanceEvent] = []
    invalid: list[str] = []
    for index, value in enumerate(events):
        try:
            event = (
                value
                if isinstance(value, ProvenanceEvent)
                else ProvenanceEvent.model_validate(value)
            )
            if semantic_hash(event.payload) != event.payload_hash:
                raise ValueError("payload hash mismatch")
            prohibited = _prohibited_key(event.payload)
            if prohibited:
                raise ValueError(f"prohibited provenance field: {prohibited}")
            parsed.append(event)
        except (TypeError, ValueError) as error:
            invalid.append(f"event[{index}]: {error}")

    present = set(ENVELOPE_FIELDS)
    observed_stages: set[ProvenanceStage] = set()
    for event in parsed:
        present.update(event.payload)
        observed_stages.add(event.stage)
    missing = tuple(sorted(set(PROFILE_FIELDS[profile]) - present))
    stage_missing = PROFILE_STAGES.get(profile, frozenset()) - observed_stages
    order_valid = _causal_order_valid(parsed)
    if stage_missing:
        missing = tuple(sorted((*missing, *(f"stage:{stage.value}" for stage in stage_missing))))
    return ProvenanceCompleteness(
        profile=profile,
        complete=not missing and not invalid and order_valid,
        event_count=len(parsed),
        missing_fields=missing,
        invalid_events=tuple(invalid),
        order_valid=order_valid,
    )


def validate_jsonl(path: Path, profile: str) -> ProvenanceCompleteness:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            events.append({"invalid_line": line_number, "error": str(error)})
            continue
        events.append(value)
    return validate_provenance(events, profile)


def _prohibited_key(value: object) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in PROHIBITED_KEYS:
                return str(key)
            found = _prohibited_key(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _prohibited_key(item)
            if found:
                return found
    return None


def _causal_order_valid(events: list[ProvenanceEvent]) -> bool:
    by_case: dict[tuple[str, str], list[ProvenanceEvent]] = {}
    for event in events:
        by_case.setdefault((event.run_id, event.case_id), []).append(event)
    edges = (
        (ProvenanceStage.MEMORY_RETRIEVAL_RESULT, ProvenanceStage.MEMORY_SELECTION),
        (ProvenanceStage.MEMORY_SELECTION, ProvenanceStage.GENERATION_CONTEXT),
        (ProvenanceStage.GOVERNED_GROUNDING_REQUEST, ProvenanceStage.PROVIDER_REQUEST),
        (ProvenanceStage.PROVIDER_REQUEST, ProvenanceStage.PROVIDER_RESPONSE),
        (ProvenanceStage.PROVIDER_RESPONSE, ProvenanceStage.GOVERNED_GROUNDING_RESULT),
        (ProvenanceStage.GOVERNED_GROUNDING_RESULT, ProvenanceStage.GOVERNED_COMPILER_INPUT),
        (ProvenanceStage.GOVERNED_COMPILER_INPUT, ProvenanceStage.GOVERNED_COMPILER_OUTPUT),
        (ProvenanceStage.GENERATION_CONTEXT, ProvenanceStage.PROVIDER_REQUEST),
        (ProvenanceStage.PROVIDER_RESPONSE, ProvenanceStage.CANDIDATE_EXTRACTION),
        (ProvenanceStage.CANDIDATE_EXTRACTION, ProvenanceStage.M1_PLAN),
        (ProvenanceStage.M1_PLAN, ProvenanceStage.EXECUTION),
        (ProvenanceStage.EXECUTION, ProvenanceStage.ROUTER),
    )
    for case_events in by_case.values():
        sequences = [event.sequence for event in case_events]
        if sequences != list(range(1, len(sequences) + 1)):
            return False
        positions: dict[ProvenanceStage, list[int]] = {stage: [] for stage in ProvenanceStage}
        for index, event in enumerate(case_events):
            positions[event.stage].append(index)
        for before, after in edges:
            if (
                positions[before]
                and positions[after]
                and max(positions[before]) > min(positions[after])
            ):
                return False
    return True
