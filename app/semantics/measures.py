"""Derive grain facts from a source catalog so fan-out checks work on any schema."""

from __future__ import annotations

import re

from app.catalog.models import SchemaCatalog, TableMetadata
from app.semantics.grain import (
    AggregationBehavior,
    GrainEntity,
    GrainKey,
    GrainRelationship,
    MeasureCatalog,
    MeasureSemantics,
)

_NUMERIC = re.compile(
    r"^(numeric|decimal|real|double|float|money|smallint|integer|int|bigint)", re.IGNORECASE
)
_PROVENANCE = ("source-catalog",)


def measure_catalog_from_schema(
    catalog: SchemaCatalog, database_id: str = "operator"
) -> MeasureCatalog:
    """Tables with a primary key become entities, foreign keys become many-to-one edges,
    and numeric non-key columns become measures at their table's grain.

    Treating numeric columns as additive is deliberately conservative: summing or averaging
    a parent's column across a child join double-counts whatever the column means.
    """
    keyed = {table.name: table for table in catalog.tables if _key_columns(table)}

    def attribute(table: str, column: str) -> str:
        return f"attribute:{database_id}:{table}:{column}"

    def entity(table: str) -> str:
        return f"entity:{database_id}:{table}"

    entities = tuple(
        GrainEntity(
            entity_id=entity(name),
            physical_table=name,
            key_attribute_ids=tuple(attribute(name, column) for column in _key_columns(table)),
        )
        for name, table in keyed.items()
    )
    relationships = tuple(
        GrainRelationship(
            relationship_id=(
                f"relationship:{database_id}:{name}.{relationship.column}"
                f"->{relationship.referenced_table}.{relationship.referenced_column}"
            ),
            from_entity_id=entity(name),
            from_attribute_ids=(attribute(name, relationship.column),),
            to_entity_id=entity(relationship.referenced_table),
            to_attribute_ids=(
                attribute(relationship.referenced_table, relationship.referenced_column),
            ),
            cardinality="MANY_TO_ONE",
            provenance=_PROVENANCE,
        )
        for name, table in keyed.items()
        for relationship in table.relationships
        if relationship.referenced_table in keyed
    )
    measures = []
    for name, table in keyed.items():
        foreign = {relationship.column for relationship in table.relationships}
        grain = GrainKey(
            entity_id=entity(name),
            key_attribute_ids=tuple(attribute(name, column) for column in _key_columns(table)),
        )
        for column in table.columns:
            if column.primary_key or column.name in foreign or not _NUMERIC.match(column.type):
                continue
            measures.append(
                MeasureSemantics(
                    measure_id=f"measure:{database_id}:{name}:{column.name}",
                    source_attribute_id=attribute(name, column.name),
                    entity_id=entity(name),
                    physical_table=name,
                    physical_column_or_path=column.name,
                    native_grain=grain,
                    aggregation_behavior=AggregationBehavior.ADDITIVE,
                    provenance=_PROVENANCE,
                )
            )
    return MeasureCatalog(entities=entities, relationships=relationships, measures=tuple(measures))


def _key_columns(table: TableMetadata) -> tuple[str, ...]:
    return tuple(column.name for column in table.columns if column.primary_key)
