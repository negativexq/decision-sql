"""Typed, shadow-only context-availability primitives.

This module represents factual metadata owned by the server.  It deliberately
does not inspect questions, benchmark truth, or generated submissions, and it
does not expose a final answerability decision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "context-availability-shadow-contract-1"


class ContextAvailabilityError(ValueError):
    """The public context cannot form a valid shadow snapshot."""


class PrimitiveFamily(StrEnum):
    SCHEMA = "SCHEMA"
    AUTHORIZED_RELATIONSHIP = "AUTHORIZED_RELATIONSHIP"
    SEMANTIC_DEFINITION = "SEMANTIC_DEFINITION"
    TEMPORAL_DEFINITION = "TEMPORAL_DEFINITION"
    POLICY = "POLICY"


PRIMITIVE_FAMILY_ORDER = (
    PrimitiveFamily.SCHEMA,
    PrimitiveFamily.AUTHORIZED_RELATIONSHIP,
    PrimitiveFamily.SEMANTIC_DEFINITION,
    PrimitiveFamily.TEMPORAL_DEFINITION,
    PrimitiveFamily.POLICY,
)


class PrimitiveInventoryState(StrEnum):
    POPULATED = "POPULATED"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CapabilityState(StrEnum):
    NOT_COMPUTED = "NOT_COMPUTED"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PrimitiveProvenance(_Frozen):
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    derivation_rule: str = Field(min_length=1)
    inference: str = "NONE"


class AvailabilityPrimitive(_Frozen):
    primitive_id: str = Field(min_length=1)
    family: PrimitiveFamily
    subject: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    value: Any
    provenance: PrimitiveProvenance


class PrimitiveSet(_Frozen):
    family: PrimitiveFamily
    catalog_version: str = Field(min_length=1)
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: PrimitiveInventoryState
    items: tuple[AvailabilityPrimitive, ...] = ()
    item_count: int = Field(ge=0)
    provenance: PrimitiveProvenance

    @model_validator(mode="after")
    def validate_items(self) -> PrimitiveSet:
        if self.item_count != len(self.items):
            raise ContextAvailabilityError(f"item count mismatch for {self.family}")
        ids = [item.primitive_id for item in self.items]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ContextAvailabilityError(
                f"primitive ordering/uniqueness mismatch for {self.family}"
            )
        if self.state is PrimitiveInventoryState.POPULATED and not self.items:
            raise ContextAvailabilityError(f"populated family is empty: {self.family}")
        if self.state is not PrimitiveInventoryState.POPULATED and self.items:
            raise ContextAvailabilityError(f"non-populated family has items: {self.family}")
        return self


class BoundaryCapabilities(_Frozen):
    required_fact_identification: CapabilityState = CapabilityState.NOT_COMPUTED
    uniqueness_of_interpretation: CapabilityState = CapabilityState.NOT_COMPUTED
    final_answerability: CapabilityState = CapabilityState.NOT_COMPUTED


class ContextAvailabilitySnapshot(_Frozen):
    contract_version: str = CONTRACT_VERSION
    context_identity: str = Field(min_length=1)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primitive_sets: tuple[PrimitiveSet, ...]
    boundary_capabilities: BoundaryCapabilities = BoundaryCapabilities()
    provenance: tuple[PrimitiveProvenance, ...]
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_snapshot(self) -> ContextAvailabilitySnapshot:
        expected = {family for family in PrimitiveFamily}
        actual = {item.family for item in self.primitive_sets}
        if actual != expected:
            raise ContextAvailabilityError(
                "snapshot must contain exactly the five primitive families"
            )
        if tuple(item.family for item in self.primitive_sets) != PRIMITIVE_FAMILY_ORDER:
            raise ContextAvailabilityError("primitive families must be canonically ordered")
        if len(self.provenance) != len(self.primitive_sets):
            raise ContextAvailabilityError("snapshot provenance must cover every primitive family")
        return self

    def semantic_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"snapshot_hash"})
        return payload


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        normalized_by_key = {
            json.dumps(normalized_item, sort_keys=True, separators=(",", ":")): normalized_item
            for normalized_item in (_canonical(item) for item in value)
        }
        normalized = list(normalized_by_key.values())
        return sorted(
            normalized, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
        )
    return value


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source(
    context_identity: str,
    context_version: str,
    field: str,
    value: Any,
    rule: str,
) -> PrimitiveProvenance:
    return PrimitiveProvenance(
        source_id=f"{context_identity}:{field}",
        source_version=context_version,
        source_hash=_hash(value),
        derivation_rule=rule,
    )


def _collection(context: Mapping[str, Any], field: str) -> tuple[Any, PrimitiveInventoryState]:
    if field not in context or context[field] is None:
        return (), PrimitiveInventoryState.UNKNOWN
    value = context[field]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContextAvailabilityError(f"expected a list for {field}")
    return tuple(
        value
    ), PrimitiveInventoryState.POPULATED if value else PrimitiveInventoryState.ABSENT


def _item(
    family: PrimitiveFamily,
    item: Mapping[str, Any],
    primitive_id: str,
    subject: str,
    kind: str,
    provenance: PrimitiveProvenance,
) -> AvailabilityPrimitive:
    if not primitive_id or not subject:
        raise ContextAvailabilityError(f"missing stable primitive identity in {family}")
    return AvailabilityPrimitive(
        primitive_id=primitive_id,
        family=family,
        subject=subject,
        kind=kind,
        value=_canonical(dict(item)),
        provenance=provenance,
    )


def _set(
    family: PrimitiveFamily,
    catalog_version: str,
    source: PrimitiveProvenance,
    items: Sequence[AvailabilityPrimitive],
    state: PrimitiveInventoryState,
) -> PrimitiveSet:
    deduped: dict[str, AvailabilityPrimitive] = {}
    for item in items:
        previous = deduped.get(item.primitive_id)
        if previous is not None and previous.value != item.value:
            raise ContextAvailabilityError(f"conflicting duplicate primitive: {item.primitive_id}")
        deduped[item.primitive_id] = item
    ordered = tuple(deduped[key] for key in sorted(deduped))
    if state is PrimitiveInventoryState.POPULATED and not ordered:
        raise ContextAvailabilityError(
            f"catalog declared populated but has no valid items: {family}"
        )
    return PrimitiveSet(
        family=family,
        catalog_version=catalog_version,
        catalog_hash=source.source_hash,
        state=state
        if ordered or state is not PrimitiveInventoryState.POPULATED
        else PrimitiveInventoryState.ABSENT,
        items=ordered,
        item_count=len(ordered),
        provenance=source,
    )


def build_context_availability_snapshot(
    public_context: Mapping[str, Any],
    *,
    context_identity: str | None = None,
    context_version: str | None = None,
) -> ContextAvailabilitySnapshot:
    """Build a canonical factual snapshot from public/server-owned metadata.

    The builder accepts no question, case identifier, truth record, or model
    instruction.  It performs deterministic record mapping only.
    """
    context = _canonical(dict(public_context))
    if not isinstance(context, dict):
        raise ContextAvailabilityError("public context must be an object")
    identity = str(context_identity or context.get("database_id") or "governed-context")
    version = str(context_version or context.get("context_profile") or "unknown-context-version")
    context_hash = _hash(context)

    entities, entity_state = _collection(context, "schema_catalog")
    entity_source = _source(
        identity, version, "schema_catalog", entities, "map public entity records"
    )
    schema_items = [
        _item(
            PrimitiveFamily.SCHEMA,
            entity,
            str(entity.get("entity_id", "")),
            str(entity.get("entity_id", "")),
            "ENTITY",
            entity_source,
        )
        for entity in entities
        if isinstance(entity, Mapping)
    ]
    attributes, attribute_state = _collection(context, "attributes")
    attribute_source = _source(
        identity, version, "attributes", attributes, "map public attribute records"
    )
    schema_items.extend(
        _item(
            PrimitiveFamily.SCHEMA,
            attribute,
            str(attribute.get("attribute_id", "")),
            str(attribute.get("entity_id", "")),
            "ATTRIBUTE",
            attribute_source,
        )
        for attribute in attributes
        if isinstance(attribute, Mapping)
    )
    schema_state = (
        PrimitiveInventoryState.POPULATED
        if schema_items
        else (
            PrimitiveInventoryState.UNKNOWN
            if entity_state is PrimitiveInventoryState.UNKNOWN
            else attribute_state
        )
    )
    schema_source = _source(
        identity,
        version,
        "schema_catalog+attributes",
        {"schema_catalog": entities, "attributes": attributes},
        "map explicit public schema and attribute records",
    )

    relationships, relationship_state = _collection(context, "authorized_relationships")
    relationship_source = _source(
        identity,
        version,
        "authorized_relationships",
        relationships,
        "map explicitly authorized relationship records only",
    )
    relationship_items = [
        _item(
            PrimitiveFamily.AUTHORIZED_RELATIONSHIP,
            relationship,
            str(relationship.get("relationship_id", "")),
            str(relationship.get("left_entity", relationship.get("from_entity", ""))),
            "AUTHORIZED_RELATIONSHIP",
            relationship_source,
        )
        for relationship in relationships
        if isinstance(relationship, Mapping)
    ]

    metrics, metric_state = _collection(context, "metrics")
    rules, rule_state = _collection(context, "business_rules")
    semantic_source = _source(
        identity,
        version,
        "metrics+business_rules+attributes",
        {"metrics": metrics, "business_rules": rules, "attributes": attributes},
        "map explicit public semantic definitions without request interpretation",
    )
    semantic_items = [
        _item(
            PrimitiveFamily.SEMANTIC_DEFINITION,
            metric,
            str(metric.get("metric_id", "")),
            str(metric.get("name", metric.get("metric_id", ""))),
            "METRIC_DEFINITION",
            semantic_source,
        )
        for metric in metrics
        if isinstance(metric, Mapping)
    ]
    semantic_items.extend(
        _item(
            PrimitiveFamily.SEMANTIC_DEFINITION,
            rule,
            str(rule.get("rule_id", "")),
            str(rule.get("name", rule.get("rule_id", ""))),
            "BUSINESS_RULE_DEFINITION",
            semantic_source,
        )
        for rule in rules
        if isinstance(rule, Mapping)
    )
    semantic_items.extend(
        _item(
            PrimitiveFamily.SEMANTIC_DEFINITION,
            attribute,
            str(attribute.get("attribute_id", "")),
            str(attribute.get("attribute_id", "")),
            "ATTRIBUTE_DEFINITION",
            semantic_source,
        )
        for attribute in attributes
        if isinstance(attribute, Mapping) and attribute.get("semantic_description")
    )
    semantic_state = (
        PrimitiveInventoryState.POPULATED
        if semantic_items
        else (
            PrimitiveInventoryState.UNKNOWN
            if PrimitiveInventoryState.UNKNOWN in {metric_state, rule_state, attribute_state}
            else PrimitiveInventoryState.ABSENT
        )
    )

    temporal, temporal_state = _collection(context, "temporal_rules")
    temporal_source = _source(
        identity, version, "temporal_rules", temporal, "map explicit temporal records"
    )
    temporal_items = [
        _item(
            PrimitiveFamily.TEMPORAL_DEFINITION,
            rule,
            str(rule.get("temporal_rule_id", "")),
            str(rule.get("temporal_rule_id", "")),
            "TEMPORAL_DEFINITION",
            temporal_source,
        )
        for rule in temporal
        if isinstance(rule, Mapping)
    ]

    policy_value = context.get("policy", None)
    policy_state = (
        PrimitiveInventoryState.UNKNOWN
        if policy_value is None
        else PrimitiveInventoryState.POPULATED
        if policy_value
        else PrimitiveInventoryState.ABSENT
    )
    policy_source = _source(
        identity, version, "policy", policy_value, "map explicit policy metadata"
    )
    policy_items = (
        [
            _item(
                PrimitiveFamily.POLICY,
                policy_value if isinstance(policy_value, Mapping) else {"value": policy_value},
                f"policy:{identity}",
                identity,
                "POLICY",
                policy_source,
            )
        ]
        if policy_state is PrimitiveInventoryState.POPULATED
        else []
    )

    primitive_sets = (
        _set(
            PrimitiveFamily.SCHEMA,
            "public-context-schema-1",
            schema_source,
            schema_items,
            schema_state,
        ),
        _set(
            PrimitiveFamily.AUTHORIZED_RELATIONSHIP,
            "public-context-relationships-1",
            relationship_source,
            relationship_items,
            relationship_state,
        ),
        _set(
            PrimitiveFamily.SEMANTIC_DEFINITION,
            "public-context-semantics-1",
            semantic_source,
            semantic_items,
            semantic_state,
        ),
        _set(
            PrimitiveFamily.TEMPORAL_DEFINITION,
            "public-context-temporal-1",
            temporal_source,
            temporal_items,
            temporal_state,
        ),
        _set(
            PrimitiveFamily.POLICY,
            "public-context-policy-1",
            policy_source,
            policy_items,
            policy_state,
        ),
    )
    provenance = tuple(item.provenance for item in primitive_sets)
    payload = {
        "contract_version": CONTRACT_VERSION,
        "context_identity": identity,
        "context_hash": context_hash,
        "primitive_sets": [item.model_dump(mode="json") for item in primitive_sets],
        "boundary_capabilities": BoundaryCapabilities().model_dump(mode="json"),
        "provenance": [item.model_dump(mode="json") for item in provenance],
    }
    return ContextAvailabilitySnapshot(
        contract_version=CONTRACT_VERSION,
        context_identity=identity,
        context_hash=context_hash,
        primitive_sets=primitive_sets,
        provenance=provenance,
        snapshot_hash=_hash(payload),
    )


__all__ = [
    "AvailabilityPrimitive",
    "BoundaryCapabilities",
    "CONTRACT_VERSION",
    "ContextAvailabilityError",
    "ContextAvailabilitySnapshot",
    "PrimitiveFamily",
    "PrimitiveInventoryState",
    "PrimitiveProvenance",
    "PrimitiveSet",
    "build_context_availability_snapshot",
]
