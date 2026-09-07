"""Server-owned semantic-to-physical mapping for the canonical engine."""

from __future__ import annotations

import json
from collections.abc import Iterable
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.models import SchemaCatalog
from app.semantics.semantic_query import (
    SemanticAttributeId,
    SemanticEntityId,
    SemanticRelationshipId,
)


class SemanticMappingError(ValueError):
    """A semantic identifier cannot be resolved in the server mapping."""


class SemanticEntityMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: SemanticEntityId
    physical_table: str = Field(min_length=1, max_length=63)
    description: str = ""
    key_attribute_ids: tuple[SemanticAttributeId, ...] = ()
    queryable: bool = True


class SemanticAttributeMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attribute_id: SemanticAttributeId
    entity_id: SemanticEntityId
    physical_column: str = Field(min_length=1, max_length=63)
    data_type: str
    description: str = ""
    queryable: bool = True
    primary_key: bool = False


class SemanticRelationshipMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: SemanticRelationshipId
    from_entity_id: SemanticEntityId
    from_attribute_id: SemanticAttributeId
    to_entity_id: SemanticEntityId
    to_attribute_id: SemanticAttributeId
    cardinality: str = "MANY_TO_ONE"
    source_kind: str = "FOREIGN_KEY"
    legacy_relationship_id: str | None = None


class SemanticMappingSnapshot(BaseModel):
    """Immutable identity adapter over live, server-owned schema metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "semantic-mapping-1"
    entities: tuple[SemanticEntityMapping, ...] = ()
    attributes: tuple[SemanticAttributeMapping, ...] = ()
    relationships: tuple[SemanticRelationshipMapping, ...] = ()

    @classmethod
    def from_schema(
        cls, schema: SchemaCatalog, *, database_id: str = "schema"
    ) -> SemanticMappingSnapshot:
        """Build a deterministic identity-style mapping from the live catalog.

        This adapter is deliberately mechanical: it does not infer business
        meaning and never consults generated or reference SQL.
        """
        del database_id  # the snapshot is schema-scoped; the query carries DB identity.
        queryable_tables = {table.name: table for table in schema.tables if table.queryable}
        entities: list[SemanticEntityMapping] = []
        attributes: list[SemanticAttributeMapping] = []
        for table in sorted(queryable_tables.values(), key=lambda item: item.name):
            entity_id = _entity_id(table.name)
            entities.append(
                SemanticEntityMapping(
                    entity_id=entity_id,
                    physical_table=table.name,
                    description=table.description,
                    key_attribute_ids=tuple(
                        _attribute_id(table.name, column.name)
                        for column in table.columns
                        if column.queryable and column.primary_key
                    ),
                )
            )
            attributes.extend(
                SemanticAttributeMapping(
                    attribute_id=_attribute_id(table.name, column.name),
                    entity_id=entity_id,
                    physical_column=column.name,
                    data_type=column.type,
                    description=column.description,
                    queryable=column.queryable,
                    primary_key=column.primary_key,
                )
                for column in table.columns
                if column.queryable
            )
        relationships: list[SemanticRelationshipMapping] = []
        for table in sorted(queryable_tables.values(), key=lambda item: item.name):
            for relationship in sorted(
                table.relationships,
                key=lambda item: (item.column, item.referenced_table, item.referenced_column),
            ):
                if relationship.referenced_table not in queryable_tables:
                    continue
                legacy = f"{table.name}.{relationship.column}"
                relationships.append(
                    SemanticRelationshipMapping(
                        relationship_id=_relationship_id(
                            table.name,
                            relationship.column,
                            relationship.referenced_table,
                            relationship.referenced_column,
                        ),
                        legacy_relationship_id=legacy,
                        from_entity_id=_entity_id(table.name),
                        from_attribute_id=_attribute_id(table.name, relationship.column),
                        to_entity_id=_entity_id(relationship.referenced_table),
                        to_attribute_id=_attribute_id(
                            relationship.referenced_table, relationship.referenced_column
                        ),
                    )
                )
        return cls(
            version="semantic-mapping-1",
            entities=tuple(entities),
            attributes=tuple(attributes),
            relationships=tuple(relationships),
        )

    @property
    def content_hash(self) -> str:
        payload = self.model_dump(mode="json")
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def entity(self, entity_id: str) -> SemanticEntityMapping:
        for item in self.entities:
            if item.entity_id == entity_id:
                return item
        raise SemanticMappingError(f"unknown semantic entity: {entity_id}")

    def attribute(self, attribute_id: str) -> SemanticAttributeMapping:
        for item in self.attributes:
            if item.attribute_id == attribute_id:
                return item
        raise SemanticMappingError(f"unknown semantic attribute: {attribute_id}")

    def relationship(self, relationship_id: str) -> SemanticRelationshipMapping:
        for item in self.relationships:
            if item.relationship_id == relationship_id:
                return item
        raise SemanticMappingError(f"unknown semantic relationship: {relationship_id}")

    def attribute_for_physical(self, table: str, column: str) -> SemanticAttributeMapping:
        normalized = (table.lower(), column.lower())
        for item in self.attributes:
            entity = self.entity(item.entity_id)
            if (entity.physical_table.lower(), item.physical_column.lower()) == normalized:
                return item
        raise SemanticMappingError(f"unknown physical attribute: {table}.{column}")

    def entity_for_physical(self, table: str) -> SemanticEntityMapping:
        normalized = table.lower()
        for item in self.entities:
            if item.physical_table.lower() == normalized:
                return item
        raise SemanticMappingError(f"unknown physical entity: {table}")

    def relationship_for_legacy(self, legacy_id: str) -> SemanticRelationshipMapping:
        for item in self.relationships:
            if item.legacy_relationship_id == legacy_id:
                return item
        raise SemanticMappingError(f"unknown legacy relationship: {legacy_id}")

    def relationships_for(self, entity_id: str) -> tuple[SemanticRelationshipMapping, ...]:
        return tuple(
            item
            for item in self.relationships
            if item.from_entity_id == entity_id or item.to_entity_id == entity_id
        )

    def with_server_relationships(
        self, relationships: Iterable[SemanticRelationshipMapping]
    ) -> SemanticMappingSnapshot:
        """Return a snapshot extended by explicitly server-owned relations.

        This is intentionally an explicit configuration seam.  It accepts no
        SQL or benchmark artifacts, and callers must mark non-FK definitions
        with a provenance such as ``SERVER_CONFIGURED``.
        """
        combined = {item.relationship_id: item for item in self.relationships}
        for relationship in relationships:
            if relationship.source_kind not in {"SCHEMA_DECLARED", "SERVER_CONFIGURED"}:
                raise SemanticMappingError(
                    "non-FK relationships require explicit server-owned provenance"
                )
            combined[relationship.relationship_id] = relationship
        return self.model_copy(
            update={"relationships": tuple(combined[key] for key in sorted(combined))}
        )

    def reachable_entities(self, relationship_ids: Iterable[str]) -> set[str]:
        result: set[str] = set()
        for relationship_id in relationship_ids:
            relationship = self.relationship(relationship_id)
            result.update((relationship.from_entity_id, relationship.to_entity_id))
        return result


def render_semantic_mapping_context(mapping: SemanticMappingSnapshot) -> str:
    """Deterministically expose semantic IDs while retaining server ownership."""
    lines = ["SERVER-OWNED SEMANTIC MAPPING (choose IDs; never emit SQL)"]
    for entity in mapping.entities:
        lines.append(f"ENTITY {entity.entity_id}: {entity.description}")
        for attribute in (
            item for item in mapping.attributes if item.entity_id == entity.entity_id
        ):
            lines.append(
                f"  ATTRIBUTE {attribute.attribute_id} PHYSICAL {attribute.physical_column} "
                f"TYPE {attribute.data_type}: {attribute.description}"
            )
    lines.append("RELATIONSHIPS (the server supplies join predicates):")
    for relationship in mapping.relationships:
        lines.append(
            f"  RELATIONSHIP {relationship.relationship_id}: "
            f"{relationship.from_entity_id} -> {relationship.to_entity_id}"
        )
    return "\n".join(lines)


def _entity_id(table: str) -> str:
    return f"entity:{table}"


def _attribute_id(table: str, column: str) -> str:
    return f"attribute:{table}.{column}"


def _relationship_id(table: str, column: str, target: str, target_column: str) -> str:
    return f"relationship:{table}.{column}->{target}.{target_column}"
