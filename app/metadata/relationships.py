"""Relationships an administrator declares for a source that lacks foreign keys.

A declared relationship carries the same authority as a foreign key: it is
merged into the connection's catalog, so the model may use the join and grain
safety treats it as many-to-one.  The server checks a declaration before it is
saved; nothing is inferred into authority without a person approving it.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, insert, select

from app.catalog.models import RelationshipMetadata, SchemaCatalog, TableMetadata
from app.metadata.tables import declared_relationships

_NAME = Field(min_length=1, max_length=255)


class RelationshipDeclaration(BaseModel):
    """Many rows of ``from_table`` point at one row of ``to_table``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_table: str = _NAME
    from_column: str = _NAME
    to_table: str = _NAME
    to_column: str = _NAME

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.from_table.lower(),
            self.from_column.lower(),
            self.to_table.lower(),
            self.to_column.lower(),
        )


class DeclaredRelationship(RelationshipDeclaration):
    id: str
    connection_id: str
    created_by: str
    created_at: datetime


class RelationshipSuggestion(RelationshipDeclaration):
    reason: str
    # Share of sampled non-null source values found in the target column.
    match_rate: float | None = None


class ForeignKeyRelationship(RelationshipDeclaration):
    pass


class ConnectionRelationships(BaseModel):
    foreign_keys: list[ForeignKeyRelationship]
    declared: list[DeclaredRelationship]
    suggestions: list[RelationshipSuggestion]


class RelationshipRejectedError(ValueError):
    """A declaration that fails a check; the message says which one."""


class RelationshipStore(Protocol):
    def list(self, connection_id: str) -> list[DeclaredRelationship]: ...

    def create(
        self, connection_id: str, declaration: RelationshipDeclaration, actor: str
    ) -> DeclaredRelationship: ...

    def delete(self, connection_id: str, relationship_id: str) -> bool: ...


def _new(
    connection_id: str, declaration: RelationshipDeclaration, actor: str
) -> DeclaredRelationship:
    return DeclaredRelationship(
        id=f"rel_{uuid4().hex[:12]}",
        connection_id=connection_id,
        created_by=actor,
        created_at=datetime.now(UTC),
        **declaration.model_dump(),
    )


class InMemoryRelationshipStore:
    def __init__(self) -> None:
        self._items: OrderedDict[str, DeclaredRelationship] = OrderedDict()

    def list(self, connection_id: str) -> list[DeclaredRelationship]:
        return [item for item in self._items.values() if item.connection_id == connection_id]

    def create(
        self, connection_id: str, declaration: RelationshipDeclaration, actor: str
    ) -> DeclaredRelationship:
        item = _new(connection_id, declaration, actor)
        self._items[item.id] = item
        return item

    def delete(self, connection_id: str, relationship_id: str) -> bool:
        item = self._items.get(relationship_id)
        if item is None or item.connection_id != connection_id:
            return False
        del self._items[relationship_id]
        return True


class SqlRelationshipStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self, connection_id: str) -> list[DeclaredRelationship]:
        query = (
            select(declared_relationships)
            .where(declared_relationships.c.connection_id == connection_id)
            .order_by(declared_relationships.c.created_at)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [DeclaredRelationship.model_validate(dict(row)) for row in rows]

    def create(
        self, connection_id: str, declaration: RelationshipDeclaration, actor: str
    ) -> DeclaredRelationship:
        item = _new(connection_id, declaration, actor)
        with self.engine.begin() as connection:
            connection.execute(insert(declared_relationships).values(**item.model_dump()))
        return item

    def delete(self, connection_id: str, relationship_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                delete(declared_relationships).where(
                    declared_relationships.c.id == relationship_id,
                    declared_relationships.c.connection_id == connection_id,
                )
            )
        return bool(result.rowcount)


def merge_declared(catalog: SchemaCatalog, declared: list[DeclaredRelationship]) -> SchemaCatalog:
    """Add declared relationships to the catalog as if they were foreign keys.

    A declaration whose tables or columns no longer exist is left out rather than
    trusted: the source schema is the authority on what exists.
    """
    additions: dict[str, list[RelationshipMetadata]] = {}
    for item in declared:
        source = catalog.get_table(item.from_table)
        target = catalog.get_table(item.to_table)
        if source is None or target is None:
            continue
        source_column = source.get_column(item.from_column)
        target_column = target.get_column(item.to_column)
        if source_column is None or target_column is None:
            continue
        relationship = RelationshipMetadata(
            column=source_column.name,
            referenced_table=target.name,
            referenced_column=target_column.name,
        )
        if relationship not in source.relationships:
            additions.setdefault(source.name, []).append(relationship)
    if not additions:
        return catalog
    return SchemaCatalog(
        tables=tuple(
            table.model_copy(
                update={"relationships": (*table.relationships, *additions.get(table.name, ()))}
            )
            for table in catalog.tables
        )
    )


def check_declaration(
    catalog: SchemaCatalog,
    declaration: RelationshipDeclaration,
    existing: list[RelationshipDeclaration],
) -> RelationshipDeclaration:
    """Structural checks; returns the declaration with the catalog's spelling of each name."""
    source = catalog.get_table(declaration.from_table)
    target = catalog.get_table(declaration.to_table)
    if source is None:
        raise RelationshipRejectedError(f"Table {declaration.from_table} is not in this source.")
    if target is None:
        raise RelationshipRejectedError(f"Table {declaration.to_table} is not in this source.")
    source_column = source.get_column(declaration.from_column)
    target_column = target.get_column(declaration.to_column)
    if source_column is None:
        raise RelationshipRejectedError(
            f"Column {declaration.from_column} is not in {source.name}."
        )
    if target_column is None:
        raise RelationshipRejectedError(f"Column {declaration.to_column} is not in {target.name}.")
    if source.name == target.name and source_column.name == target_column.name:
        raise RelationshipRejectedError("A column cannot point at itself.")
    if not compatible_types(source_column.type, target_column.type):
        raise RelationshipRejectedError(
            f"{source.name}.{source_column.name} ({source_column.type}) and "
            f"{target.name}.{target_column.name} ({target_column.type}) have incompatible types."
        )
    resolved = RelationshipDeclaration(
        from_table=source.name,
        from_column=source_column.name,
        to_table=target.name,
        to_column=target_column.name,
    )
    if resolved.key() in {item.key() for item in existing}:
        raise RelationshipRejectedError("This relationship already exists.")
    other = _alternate_key_path(resolved, existing)
    if other is not None:
        raise RelationshipRejectedError(
            f"{other.from_table} already points to {other.to_table} through "
            f"{other.to_table}.{other.to_column}. A second path to another key of the same "
            "row would make every join between these tables ambiguous."
        )
    return resolved


def _alternate_key_path(
    declaration: RelationshipDeclaration, existing: list[RelationshipDeclaration]
) -> RelationshipDeclaration | None:
    """An existing link between the same tables that lands on a different target column.

    Two columns pointing at the same target column are roles (home and away team);
    a link to another unique key of the same table is a second name for one row.
    """
    for item in existing:
        if (
            item.from_table.lower() == declaration.from_table.lower()
            and item.to_table.lower() == declaration.to_table.lower()
            and item.to_column.lower() != declaration.to_column.lower()
        ):
            return item
    return None


def foreign_keys(catalog: SchemaCatalog) -> list[ForeignKeyRelationship]:
    return [
        ForeignKeyRelationship(
            from_table=table.name,
            from_column=relationship.column,
            to_table=relationship.referenced_table,
            to_column=relationship.referenced_column,
        )
        for table in catalog.tables
        for relationship in table.relationships
    ]


def candidate_relationships(
    catalog: SchemaCatalog, unique_keys: set[tuple[str, str]], limit: int = 40
) -> list[RelationshipSuggestion]:
    """Name-and-type candidates pointing at single-column unique keys.

    A candidate is only a suggestion: the person declaring it decides.
    """
    links: list[RelationshipDeclaration] = list(foreign_keys(catalog))
    existing = {item.key() for item in links}
    targets = [
        (table, column)
        for table in catalog.tables
        for column in table.columns
        if (table.name, column.name) in unique_keys
    ]
    found: list[RelationshipSuggestion] = []
    for source in catalog.tables:
        for column in source.columns:
            if (source.name, column.name) in unique_keys:
                continue
            for target, key in targets:
                if target.name == source.name or not compatible_types(column.type, key.type):
                    continue
                reason = _name_reason(column.name, target, key.name)
                if reason is None:
                    continue
                suggestion = RelationshipSuggestion(
                    from_table=source.name,
                    from_column=column.name,
                    to_table=target.name,
                    to_column=key.name,
                    reason=reason,
                )
                if (
                    suggestion.key() not in existing
                    and _alternate_key_path(suggestion, links) is None
                ):
                    found.append(suggestion)
    return found[:limit]


def _name_reason(column: str, target: TableMetadata, key: str) -> str | None:
    column_name = _plain(column)
    key_name = _plain(key)
    if column_name == key_name and key_name not in {"id", "code", "name", "key"}:
        return f"Same name as the key of {target.name}."
    stems = {_plain(target.name), _plain(_singular(target.name))}
    if column_name in {stem + key_name for stem in stems} or column_name in {
        stem + "id" for stem in stems
    }:
        return f"Named after {target.name} and its key."
    return None


def _plain(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _singular(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith("ies"):
        return lowered[:-3] + "y"
    if lowered.endswith("ses") or lowered.endswith("xes"):
        return lowered[:-2]
    if lowered.endswith("s") and not lowered.endswith("ss"):
        return lowered[:-1]
    return lowered


_FAMILIES = (
    ("integer", re.compile(r"^(smallint|integer|int|bigint|serial|bigserial|smallserial)")),
    ("number", re.compile(r"^(numeric|decimal|real|double|float)")),
    ("text", re.compile(r"^(text|character|varchar|char|citext|name)")),
    ("uuid", re.compile(r"^uuid")),
    ("date", re.compile(r"^(date|timestamp)")),
)


def type_family(data_type: str) -> str:
    for family, pattern in _FAMILIES:
        if pattern.match(data_type.lower()):
            return family
    return data_type.lower()


def compatible_types(left: str, right: str) -> bool:
    families = {type_family(left), type_family(right)}
    return len(families) == 1 or families == {"integer", "number"}
