from __future__ import annotations

import json

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.governance.context import (
    GovernedContext,
    GovernedContextScope,
    governed_context_from_schema_context,
    governed_context_hash,
    serialize_governed_context,
)
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.authority import ExecutionAuthority


def _context() -> GovernedContext:
    catalog = build_default_catalog(Base.metadata)
    schema_context = SchemaContextResolver(catalog).resolve(
        "Show total order revenue by status", mode=SchemaContextMode.RETRIEVED
    )
    return governed_context_from_schema_context(schema_context)


def test_governed_context_is_deterministic_and_explicit() -> None:
    first = _context()
    second = _context()

    assert first == second
    assert governed_context_hash(first) == governed_context_hash(second)
    assert serialize_governed_context(first) == serialize_governed_context(second)
    payload = json.loads(serialize_governed_context(first))
    assert payload["context_profile"] == "GOVERNED_CONTEXT_V1"
    assert payload["context_scope"] == GovernedContextScope.REQUEST_BOUNDED.value
    assert payload["metrics"] == []
    assert payload["business_rules"] == []
    assert payload["temporal_rules"] == []
    assert payload["policy"] == {
        "policy_id": "READ_ONLY",
        "mode": "READ_ONLY",
        "allowed_statement_types": ["SELECT"],
    }


def test_governed_context_contains_only_structural_metadata() -> None:
    context = _context()

    assert context.schema_catalog
    assert context.attributes
    assert all(attribute.queryable for attribute in context.attributes)
    assert all(entity.queryable for entity in context.schema_catalog)
    assert all(
        relationship.source_entity_id in {entity.entity_id for entity in context.schema_catalog}
        and relationship.target_entity_id in {entity.entity_id for entity in context.schema_catalog}
        for relationship in context.authorized_relationships
    )
    serialized = serialize_governed_context(context).lower()
    assert "rows" not in serialized
    assert "gold" not in serialized
    assert "counterfactual" not in serialized


def test_governed_context_authority_matches_visible_queryable_entities() -> None:
    catalog = build_default_catalog(Base.metadata)
    schema_context = SchemaContextResolver(catalog).resolve(
        "Show total order revenue by status", mode=SchemaContextMode.RETRIEVED
    )
    context = governed_context_from_schema_context(schema_context)
    expected = tuple(
        sorted(
            f"public.{entity.physical_table}"
            for entity in context.schema_catalog
            if entity.queryable
        )
    )

    assert ExecutionAuthority.from_governed_context(context).allowed_relations == expected
    assert ExecutionAuthority.from_governed_context(context).allowed_relations == (
        ExecutionAuthority.from_context(schema_context).allowed_relations
    )
