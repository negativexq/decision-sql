from __future__ import annotations

import pytest

from app.semantics.context_availability import (
    ContextAvailabilityError,
    PrimitiveInventoryState,
    build_context_availability_snapshot,
)


def _context() -> dict[str, object]:
    return {
        "context_profile": "GOVERNED_CONTEXT_V1",
        "database_id": "synthetic_db",
        "schema_catalog": [
            {"entity_id": "entity:orders", "physical_table": "orders"},
        ],
        "attributes": [
            {
                "attribute_id": "attribute:orders:order_id",
                "entity_id": "entity:orders",
                "data_type": "INTEGER",
                "semantic_description": "Order identifier.",
            },
        ],
        "authorized_relationships": [
            {
                "authorized": True,
                "cardinality": "many_to_one",
                "relationship_id": "relationship:order_customer",
                "left_entity": "entity:orders",
                "right_entity": "entity:customers",
            },
        ],
        "metrics": [
            {
                "metric_id": "metric:order_count",
                "name": "order_count",
                "definition": "Count of orders.",
            },
        ],
        "business_rules": [],
        "temporal_rules": [
            {
                "temporal_rule_id": "time:clock",
                "clock_mode": "fixed",
                "bounds": "inclusive",
            },
        ],
        "policy": {"read_only": True, "allowed_statement": "SELECT"},
    }


def test_snapshot_is_typed_complete_and_negative_capabilities_are_explicit() -> None:
    snapshot = build_context_availability_snapshot(_context())

    assert len(snapshot.primitive_sets) == 5
    assert snapshot.boundary_capabilities.required_fact_identification == "NOT_COMPUTED"
    assert snapshot.boundary_capabilities.uniqueness_of_interpretation == "NOT_COMPUTED"
    assert snapshot.boundary_capabilities.final_answerability == "NOT_COMPUTED"
    payload = snapshot.model_dump(mode="json")
    assert "is_answerable" not in payload
    assert "should_answer" not in payload
    assert "required_facts_complete" not in payload


def test_snapshot_is_order_independent_and_idempotent() -> None:
    context = _context()
    reordered = {
        **context,
        "schema_catalog": list(reversed(context["schema_catalog"])),  # type: ignore[arg-type]
        "attributes": list(reversed(context["attributes"])),  # type: ignore[arg-type]
        "authorized_relationships": list(reversed(context["authorized_relationships"])),  # type: ignore[arg-type]
    }

    first = build_context_availability_snapshot(context)
    second = build_context_availability_snapshot(context)
    permuted = build_context_availability_snapshot(reordered)

    assert first.snapshot_hash == second.snapshot_hash == permuted.snapshot_hash


def test_duplicate_identical_records_are_deduplicated() -> None:
    context = _context()
    context["attributes"] = [context["attributes"][0], context["attributes"][0]]  # type: ignore[index]

    snapshot = build_context_availability_snapshot(context)
    schema = next(item for item in snapshot.primitive_sets if item.family == "SCHEMA")

    assert schema.state == PrimitiveInventoryState.POPULATED
    assert schema.item_count == 2


def test_conflicting_duplicate_records_fail_closed() -> None:
    context = _context()
    context["attributes"] = [
        context["attributes"][0],  # type: ignore[index]
        {
            **context["attributes"][0],  # type: ignore[index]
            "semantic_description": "Conflicting description.",
        },
    ]

    with pytest.raises(ContextAvailabilityError, match="conflicting duplicate primitive"):
        build_context_availability_snapshot(context)


def test_empty_and_missing_catalogs_remain_distinguishable() -> None:
    empty = _context()
    empty["authorized_relationships"] = []
    empty["metrics"] = []
    empty["business_rules"] = []
    empty["temporal_rules"] = []
    empty["policy"] = {}
    empty_snapshot = build_context_availability_snapshot(empty)

    missing = _context()
    del missing["authorized_relationships"]
    del missing["temporal_rules"]
    del missing["policy"]
    missing_snapshot = build_context_availability_snapshot(missing)

    empty_relationships = next(
        item for item in empty_snapshot.primitive_sets if item.family == "AUTHORIZED_RELATIONSHIP"
    )
    missing_relationships = next(
        item for item in missing_snapshot.primitive_sets if item.family == "AUTHORIZED_RELATIONSHIP"
    )
    empty_temporal = next(
        item for item in empty_snapshot.primitive_sets if item.family == "TEMPORAL_DEFINITION"
    )
    missing_temporal = next(
        item for item in missing_snapshot.primitive_sets if item.family == "TEMPORAL_DEFINITION"
    )

    assert empty_relationships.state == PrimitiveInventoryState.ABSENT
    assert missing_relationships.state == PrimitiveInventoryState.UNKNOWN
    assert empty_temporal.state == PrimitiveInventoryState.ABSENT
    assert missing_temporal.state == PrimitiveInventoryState.UNKNOWN
