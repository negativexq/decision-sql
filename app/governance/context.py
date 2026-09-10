"""Canonical product-owned governed context for generation and authority.

The context is request-bounded: it contains the exact server-selected schema
envelope exposed to a model for one request.  It contains structure and
governance metadata only; it never contains rows, SQL, or evaluator truth.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.models import RelevantRelationship, SchemaContext


class GovernedContextScope(StrEnum):
    REQUEST_BOUNDED = "REQUEST_BOUNDED"


class GovernedEntity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    physical_table: str
    description: str
    queryable: bool = True


class GovernedAttribute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attribute_id: str
    entity_id: str
    physical_column_or_path: str
    semantic_type: str
    description: str
    queryable: bool = True
    primary_key: bool = False
    foreign_key_entity_id: str | None = None
    foreign_key_attribute_id: str | None = None


class GovernedRelationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str
    source_entity_id: str
    source_attribute_id: str
    target_entity_id: str
    target_attribute_id: str
    authorized: Literal[True] = True


class GovernedMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    name: str
    description: str


class GovernedBusinessRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    description: str


class GovernedTemporalRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    temporal_rule_id: str
    description: str


class GovernedPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = "READ_ONLY"
    mode: Literal["READ_ONLY"] = "READ_ONLY"
    allowed_statement_types: tuple[str, ...] = ("SELECT",)


class GovernedContext(BaseModel):
    """Typed, sanitized model-visible context for one production request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_profile: Literal["GOVERNED_CONTEXT_V1"] = "GOVERNED_CONTEXT_V1"
    database_id: str
    context_scope: GovernedContextScope = GovernedContextScope.REQUEST_BOUNDED
    schema_catalog: tuple[GovernedEntity, ...] = Field(default_factory=tuple)
    attributes: tuple[GovernedAttribute, ...] = Field(default_factory=tuple)
    authorized_relationships: tuple[GovernedRelationship, ...] = Field(default_factory=tuple)
    metrics: tuple[GovernedMetric, ...] = Field(default_factory=tuple)
    business_rules: tuple[GovernedBusinessRule, ...] = Field(default_factory=tuple)
    temporal_rules: tuple[GovernedTemporalRule, ...] = Field(default_factory=tuple)
    policy: GovernedPolicy = Field(default_factory=GovernedPolicy)


def governed_context_from_schema_context(
    context: SchemaContext, *, database_id: str = "operator"
) -> GovernedContext:
    """Convert bounded server metadata without adding or inferring semantics."""
    entities = tuple(
        GovernedEntity(
            entity_id=_entity_id(database_id, table.name),
            physical_table=table.name,
            description=table.description,
        )
        for table in sorted(context.tables, key=lambda item: item.name)
    )
    entity_ids = {entity.physical_table: entity.entity_id for entity in entities}

    attributes = tuple(
        GovernedAttribute(
            attribute_id=_attribute_id(database_id, column.table_name, column.name),
            entity_id=entity_ids[column.table_name],
            physical_column_or_path=column.name,
            semantic_type=column.type,
            description=column.description,
            queryable=True,
            primary_key=column.primary_key,
            foreign_key_entity_id=(
                entity_ids.get(column.foreign_key_table)
                if column.foreign_key_table is not None
                else None
            ),
            foreign_key_attribute_id=(
                _attribute_id(database_id, column.foreign_key_table, column.foreign_key_column)
                if column.foreign_key_table and column.foreign_key_column
                else None
            ),
        )
        for column in sorted(
            context.selected_columns,
            key=lambda item: (item.table_name, item.name),
        )
        if column.table_name in entity_ids
    )
    attribute_ids = {attribute.attribute_id for attribute in attributes}
    relationships = tuple(
        GovernedRelationship(
            relationship_id=_relationship_id(database_id, relationship),
            source_entity_id=entity_ids[relationship.source_table],
            source_attribute_id=_attribute_id(
                database_id, relationship.source_table, relationship.source_column
            ),
            target_entity_id=entity_ids[relationship.target_table],
            target_attribute_id=_attribute_id(
                database_id, relationship.target_table, relationship.target_column
            ),
        )
        for relationship in context.relationships
        if (
            relationship.source_table in entity_ids
            and relationship.target_table in entity_ids
            and _attribute_id(database_id, relationship.source_table, relationship.source_column)
            in attribute_ids
            and _attribute_id(database_id, relationship.target_table, relationship.target_column)
            in attribute_ids
        )
    )
    return GovernedContext(
        database_id=database_id,
        schema_catalog=entities,
        attributes=attributes,
        authorized_relationships=tuple(
            sorted(relationships, key=lambda item: item.relationship_id)
        ),
    )


def serialize_governed_context(context: GovernedContext) -> str:
    """Serialize the exact model-visible context with canonical JSON bytes."""
    payload = context.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def governed_context_hash(context: GovernedContext) -> str:
    return hashlib.sha256(serialize_governed_context(context).encode("utf-8")).hexdigest()


def _entity_id(database_id: str, table: str) -> str:
    return f"entity:{database_id}:{table}"


def _attribute_id(database_id: str, table: str | None, column: str | None) -> str:
    if table is None or column is None:
        raise ValueError("Governed attributes require table and column names")
    return f"attribute:{database_id}:{table}:{column}"


def _relationship_id(database_id: str, relationship: RelevantRelationship) -> str:
    source_table = relationship.source_table
    source_column = relationship.source_column
    target_table = relationship.target_table
    target_column = relationship.target_column
    return (
        f"relationship:{database_id}:{source_table}.{source_column}->{target_table}.{target_column}"
    )
