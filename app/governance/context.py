"""Canonical product-owned governed context for generation and authority.

The context is request-bounded: it contains the exact server-selected schema
envelope exposed to a model for one request.  It contains structure and
governance metadata, plus stored spellings of categorical columns when an
administrator shares them; it never contains result rows, SQL, or evaluator
truth.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from app.catalog.models import RelevantRelationship, SchemaContext
from app.sql.functions import model_visible_functions


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
    known_values: tuple[str, ...] = ()
    known_values_complete: bool = False
    value_patterns: tuple[str, ...] = ()

    @model_serializer(mode="wrap")
    def _omit_absent_values(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Most attributes have no known values; leaving the keys out keeps the prompt small.
        data: dict[str, Any] = handler(self)
        if not self.known_values:
            data.pop("known_values", None)
            data.pop("known_values_complete", None)
        if not self.value_patterns:
            data.pop("value_patterns", None)
        return data


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
    # The runtime policy rejects any other function, so the model must see the same list.
    allowed_functions: tuple[str, ...] = Field(default_factory=model_visible_functions)


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
    # Names of tables this bounded context leaves out.  A name grants nothing: the
    # model may only ask for the table, and the runtime decides whether to add it.
    other_tables: tuple[str, ...] = Field(default_factory=tuple)

    @model_serializer(mode="wrap")
    def _omit_empty_other_tables(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if not self.other_tables:
            data.pop("other_tables", None)
        return data


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
            known_values=column.known_values,
            known_values_complete=column.known_values_complete,
            value_patterns=column.value_patterns,
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
        other_tables=context.omitted_tables,
    )


def serialize_governed_context(context: GovernedContext) -> str:
    """Serialize the exact model-visible context with canonical JSON bytes."""
    payload = context.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def render_governed_context(context: GovernedContext) -> str:
    """The same context as compact text: one line per column, names instead of ids.

    The JSON form repeats ids and empty fields on every column; this form keeps every
    fact the model reads (types, keys, descriptions, stored values, relationships,
    rules, policy) under the section names the decision contract refers to. The
    canonical JSON stays the audited and hashed form.
    """
    tables = {entity.entity_id: entity.physical_table for entity in context.schema_catalog}
    columns = {
        attribute.attribute_id: (
            tables.get(attribute.entity_id, ""),
            attribute.physical_column_or_path,
        )
        for attribute in context.attributes
    }

    def column_name(attribute_id: str) -> str:
        if attribute_id in columns:
            table, column = columns[attribute_id]
            return f"{table}.{column}"
        # A key into a table this bounded context leaves out: its id still names it.
        return ".".join(attribute_id.split(":")[-2:])

    def values(items: tuple[str, ...]) -> str:
        return ", ".join(json.dumps(item, ensure_ascii=False) for item in items)

    policy = context.policy
    lines = [
        f"database: {context.database_id}",
        f"policy: {policy.mode}; statements: {', '.join(policy.allowed_statement_types)}",
        f"policy.allowed_functions: {', '.join(policy.allowed_functions)}",
        "",
        "schema_catalog:",
    ]
    for entity in context.schema_catalog:
        flag = "" if entity.queryable else " (not queryable)"
        lines.append(f"## {entity.physical_table}{flag} — {entity.description}")
        for attribute in context.attributes:
            if attribute.entity_id != entity.entity_id:
                continue
            line = f"- {attribute.physical_column_or_path} {attribute.semantic_type}"
            if attribute.primary_key:
                line += " PK"
            if attribute.foreign_key_attribute_id:
                line += f" FK → {column_name(attribute.foreign_key_attribute_id)}"
            if not attribute.queryable:
                line += " (not queryable)"
            line += f" — {attribute.description}"
            if attribute.known_values:
                kind = "complete" if attribute.known_values_complete else "examples"
                line += f" | known_values ({kind}): {values(attribute.known_values)}"
            if attribute.value_patterns:
                line += f" | value_patterns: {', '.join(attribute.value_patterns)}"
            lines.append(line)
    lines += ["", "authorized_relationships:"]
    lines += [
        f"- {column_name(item.source_attribute_id)} = {column_name(item.target_attribute_id)}"
        for item in context.authorized_relationships
    ] or ["- none"]
    for title, items in (
        ("metrics", [f"- {item.name}: {item.description}" for item in context.metrics]),
        ("business_rules", [f"- {item.description}" for item in context.business_rules]),
        ("temporal_rules", [f"- {item.description}" for item in context.temporal_rules]),
    ):
        if items:
            lines += ["", f"{title}:", *items]
    if context.other_tables:
        lines += ["", f"other_tables: {', '.join(context.other_tables)}"]
    return "\n".join(lines) + "\n"


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
