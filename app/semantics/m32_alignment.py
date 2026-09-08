"""Small model-facing semantic alignment contract for M32.

The contract records semantic selections and intent needed to ground a later
SQL-generation prompt.  It is deliberately not an execution plan and carries
no SQL, physical aliases, join predicates, or compiler bookkeeping.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.semantics.relationship_graph import RelationshipPathError, SemanticRelationshipGraph
from app.semantics.semantic_mapping import (
    SemanticMappingError,
    SemanticMappingSnapshot,
    SemanticRelationshipMapping,
)


class AlignmentPopulationMode(StrEnum):
    BASE_ENTITY = "BASE_ENTITY"
    MATCHED_ONLY = "MATCHED_ONLY"
    PRESERVE_ANCHOR = "PRESERVE_ANCHOR"
    SEMI = "SEMI"
    ANTI = "ANTI"


class AlignmentQueryShape(StrEnum):
    SIMPLE_SELECT = "SIMPLE_SELECT"
    FILTERED_SELECT = "FILTERED_SELECT"
    AGGREGATE = "AGGREGATE"
    GROUPED_AGGREGATE = "GROUPED_AGGREGATE"
    TOP_K = "TOP_K"
    WINDOW = "WINDOW"
    SCALAR_SUBQUERY = "SCALAR_SUBQUERY"
    CORRELATED_SUBQUERY = "CORRELATED_SUBQUERY"
    SET_OPERATION = "SET_OPERATION"
    MULTI_STAGE = "MULTI_STAGE"


class AlignmentFilterOperator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"
    BETWEEN = "BETWEEN"
    IN = "IN"
    NOT_IN = "NOT_IN"
    LIKE = "LIKE"
    ILIKE = "ILIKE"


class AlignmentAggregateFunction(StrEnum):
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class AlignmentCalculationKind(StrEnum):
    NONE = "NONE"
    RATIO = "RATIO"
    DIFFERENCE = "DIFFERENCE"
    PERCENTAGE = "PERCENTAGE"
    PERCENT_CHANGE = "PERCENT_CHANGE"


class AlignmentDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class AlignmentTemporalKind(StrEnum):
    FILTER = "FILTER"
    GROUP = "GROUP"
    ORDER = "ORDER"
    RANGE = "RANGE"
    EXTRACTION = "EXTRACTION"


AlignmentValue = str | int | float | bool | None


class _AlignmentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AlignmentFilter(_AlignmentModel):
    field_id: str | None = Field(default=None, max_length=256)
    target_kind: Literal["ATTRIBUTE", "DERIVED_RESULT"] = "ATTRIBUTE"
    result_label: str | None = Field(default=None, max_length=128)
    operator: AlignmentFilterOperator
    value: AlignmentValue = None

    @model_validator(mode="after")
    def validate_target(self) -> AlignmentFilter:
        if self.target_kind == "ATTRIBUTE" and self.field_id is None:
            raise ValueError("attribute filters require field_id")
        if self.target_kind == "DERIVED_RESULT" and not self.result_label:
            raise ValueError("derived-result filters require result_label")
        return self


class AlignmentAggregate(_AlignmentModel):
    function: AlignmentAggregateFunction
    attribute_id: str | None = Field(default=None, max_length=256)
    distinct: bool = False


class AlignmentCalculation(_AlignmentModel):
    kind: AlignmentCalculationKind
    operand_attribute_ids: tuple[str, ...] = Field(default=(), max_length=8)


class AlignmentOrder(_AlignmentModel):
    attribute_id: str | None = Field(default=None, max_length=256)
    target_kind: Literal["ATTRIBUTE", "AGGREGATE", "CALCULATION", "WINDOW", "EXPRESSION"] = (
        "ATTRIBUTE"
    )
    result_label: str | None = Field(default=None, max_length=128)
    direction: AlignmentDirection = AlignmentDirection.ASC


class AlignmentTemporal(_AlignmentModel):
    attribute_id: str = Field(min_length=1, max_length=256)
    kind: AlignmentTemporalKind


class QueryAlignmentV1(_AlignmentModel):
    """Compact semantic alignment; never an executable query representation."""

    anchor_entity_id: str = Field(min_length=1, max_length=128)
    relevant_entity_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    relevant_attribute_ids: tuple[str, ...] = Field(default=(), max_length=32)
    relationship_ids: tuple[str, ...] = Field(default=(), max_length=16)
    population_mode: AlignmentPopulationMode = AlignmentPopulationMode.BASE_ENTITY
    filters: tuple[AlignmentFilter, ...] = Field(default=(), max_length=16)
    aggregations: tuple[AlignmentAggregate, ...] = Field(default=(), max_length=8)
    grouping_attribute_ids: tuple[str, ...] = Field(default=(), max_length=16)
    grouping_result_labels: tuple[str, ...] = Field(default=(), max_length=8)
    calculation: AlignmentCalculation | None = None
    ordering: tuple[AlignmentOrder, ...] = Field(default=(), max_length=8)
    limit: int | None = Field(default=None, ge=1, le=10000)
    temporal: tuple[AlignmentTemporal, ...] = Field(default=(), max_length=8)
    query_shape: AlignmentQueryShape = AlignmentQueryShape.SIMPLE_SELECT
    logic_summary: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def anchor_is_relevant(self) -> QueryAlignmentV1:
        if self.anchor_entity_id not in self.relevant_entity_ids:
            raise ValueError("anchor_entity_id must be included in relevant_entity_ids")
        return self


class SchemaAlignmentV1(_AlignmentModel):
    """Small schema-linking artifact for the evidence-driven M32 v2A path."""

    anchor_entity_id: str = Field(min_length=1, max_length=128)
    relevant_entity_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    relevant_attribute_ids: tuple[str, ...] = Field(default=(), max_length=32)
    logic_summary: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def anchor_is_relevant(self) -> SchemaAlignmentV1:
        if self.anchor_entity_id not in self.relevant_entity_ids:
            raise ValueError("anchor_entity_id must be included in relevant_entity_ids")
        return self


class LogicalSynthesisV1(_AlignmentModel):
    """Semantic computation after schema selection, still not an execution plan."""

    population_mode: AlignmentPopulationMode = AlignmentPopulationMode.BASE_ENTITY
    filters: tuple[AlignmentFilter, ...] = Field(default=(), max_length=16)
    aggregations: tuple[AlignmentAggregate, ...] = Field(default=(), max_length=8)
    grouping_attribute_ids: tuple[str, ...] = Field(default=(), max_length=16)
    grouping_result_labels: tuple[str, ...] = Field(default=(), max_length=8)
    calculation: AlignmentCalculation | None = None
    ordering: tuple[AlignmentOrder, ...] = Field(default=(), max_length=8)
    limit: int | None = Field(default=None, ge=1, le=10000)
    temporal: tuple[AlignmentTemporal, ...] = Field(default=(), max_length=8)
    query_shape: AlignmentQueryShape = AlignmentQueryShape.SIMPLE_SELECT
    logic_summary: str = Field(min_length=1, max_length=600)


def combine_schema_and_logic(
    schema: SchemaAlignmentV1, logic: LogicalSynthesisV1
) -> QueryAlignmentV1:
    """Combine v2A semantic stages into the existing M32 grounding contract."""

    return QueryAlignmentV1(
        anchor_entity_id=schema.anchor_entity_id,
        relevant_entity_ids=schema.relevant_entity_ids,
        relevant_attribute_ids=schema.relevant_attribute_ids,
        relationship_ids=(),
        population_mode=logic.population_mode,
        filters=logic.filters,
        aggregations=logic.aggregations,
        grouping_attribute_ids=logic.grouping_attribute_ids,
        grouping_result_labels=logic.grouping_result_labels,
        calculation=logic.calculation,
        ordering=logic.ordering,
        limit=logic.limit,
        temporal=logic.temporal,
        query_shape=logic.query_shape,
        logic_summary=logic.logic_summary,
    )


class AlignmentValidationError(ValueError):
    """A model alignment references unavailable or unauthorized semantics."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GroundedRelationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    cardinality: str


class GroundedSQLContext(BaseModel):
    """Server-owned, concise context supplied to the SQL-generation call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_id: str
    question: str
    alignment: QueryAlignmentV1
    entities: tuple[dict[str, str], ...]
    attributes: tuple[dict[str, str], ...]
    relationships: tuple[GroundedRelationship, ...]
    sql_dialect: Literal["postgres"] = "postgres"
    safety: str = (
        "Generate one read-only SELECT; the server will apply M1, EXPLAIN, and execution limits."
    )

    def serialize(self) -> str:
        lines = [
            "VALIDATED SEMANTIC ALIGNMENT:",
            self.alignment.model_dump_json(exclude_none=True),
            "\nSERVER-GROUNDED ENTITIES:",
        ]
        lines.extend(
            f"- {item['entity_id']} table={item['physical_table']} "
            f"description={item['description']}"
            for item in self.entities
        )
        lines.append("\nSERVER-GROUNDED ATTRIBUTES:")
        lines.extend(
            f"- {item['attribute_id']} entity={item['entity_id']} column={item['physical_column']} "
            f"type={item['data_type']} description={item['description']}"
            for item in self.attributes
        )
        lines.append("\nAUTHORIZED RELATIONSHIPS:")
        lines.extend(
            f"- {item.relationship_id}: {item.source_entity_id}.{item.source_table}."
            f"{item.source_column} = {item.target_entity_id}.{item.target_table}."
            f"{item.target_column} cardinality={item.cardinality}"
            for item in self.relationships
        )
        lines.append(f"\nSQL DIALECT: {self.sql_dialect}\nSAFETY: {self.safety}")
        return "\n".join(lines)


def validate_alignment(
    alignment: QueryAlignmentV1, mapping: SemanticMappingSnapshot
) -> QueryAlignmentV1:
    """Validate only supplied semantic IDs and bounded semantic choices."""

    try:
        entity_ids = {item.entity_id for item in mapping.entities}
        attribute_ids = {item.attribute_id for item in mapping.attributes}
        relationship_ids = {item.relationship_id for item in mapping.relationships}
        if alignment.anchor_entity_id not in entity_ids:
            raise AlignmentValidationError("UNKNOWN_ENTITY", alignment.anchor_entity_id)
        for entity_id in alignment.relevant_entity_ids:
            if entity_id not in entity_ids:
                raise AlignmentValidationError("UNKNOWN_ENTITY", entity_id)
        for attribute_id in alignment.relevant_attribute_ids:
            if attribute_id not in attribute_ids:
                raise AlignmentValidationError("UNKNOWN_ATTRIBUTE", attribute_id)
        for relationship_id in alignment.relationship_ids:
            if relationship_id not in relationship_ids:
                raise AlignmentValidationError("UNKNOWN_RELATIONSHIP", relationship_id)
        for filter_item in alignment.filters:
            if filter_item.field_id is not None:
                _require_attribute(filter_item.field_id, attribute_ids, "FILTER_ATTRIBUTE")
        for aggregate_item in alignment.aggregations:
            if aggregate_item.attribute_id is not None:
                _require_attribute(
                    aggregate_item.attribute_id, attribute_ids, "AGGREGATE_ATTRIBUTE"
                )
        for attribute_id in alignment.grouping_attribute_ids:
            _require_attribute(attribute_id, attribute_ids, "GROUPING_ATTRIBUTE")
        for order_item in alignment.ordering:
            if order_item.attribute_id is not None:
                _require_attribute(order_item.attribute_id, attribute_ids, "ORDER_ATTRIBUTE")
        for temporal_item in alignment.temporal:
            _require_attribute(temporal_item.attribute_id, attribute_ids, "TEMPORAL_ATTRIBUTE")
        if alignment.calculation is not None:
            for attribute_id in alignment.calculation.operand_attribute_ids:
                _require_attribute(attribute_id, attribute_ids, "CALCULATION_ATTRIBUTE")
    except SemanticMappingError as error:
        raise AlignmentValidationError("UNKNOWN_SEMANTIC_ID", str(error)) from error
    return alignment


def validate_schema_alignment(
    alignment: SchemaAlignmentV1, mapping: SemanticMappingSnapshot
) -> SchemaAlignmentV1:
    """Validate the v2A schema-selection artifact against server catalog IDs."""

    entity_ids = {item.entity_id for item in mapping.entities}
    attribute_ids = {item.attribute_id for item in mapping.attributes}
    if alignment.anchor_entity_id not in entity_ids:
        raise AlignmentValidationError("UNKNOWN_ENTITY", alignment.anchor_entity_id)
    for entity_id in alignment.relevant_entity_ids:
        if entity_id not in entity_ids:
            raise AlignmentValidationError("UNKNOWN_ENTITY", entity_id)
    for attribute_id in alignment.relevant_attribute_ids:
        if attribute_id not in attribute_ids:
            raise AlignmentValidationError("UNKNOWN_ATTRIBUTE", attribute_id)
    return alignment


def validate_logical_synthesis(
    synthesis: LogicalSynthesisV1,
    mapping: SemanticMappingSnapshot,
    allowed_attribute_ids: set[str],
) -> LogicalSynthesisV1:
    """Validate logic IDs and forbid reintroducing schema selection in stage two."""

    attribute_ids = {item.attribute_id for item in mapping.attributes}
    refs: set[str] = set(synthesis.grouping_attribute_ids)
    refs.update(
        item.attribute_id for item in synthesis.aggregations if item.attribute_id is not None
    )
    refs.update(item.attribute_id for item in synthesis.ordering if item.attribute_id is not None)
    refs.update(item.attribute_id for item in synthesis.temporal)
    refs.update(synthesis.calculation.operand_attribute_ids if synthesis.calculation else ())
    refs.update(item.field_id for item in synthesis.filters if item.field_id is not None)
    for attribute_id in refs:
        if attribute_id not in attribute_ids:
            raise AlignmentValidationError("UNKNOWN_ATTRIBUTE", attribute_id)
        if attribute_id not in allowed_attribute_ids:
            raise AlignmentValidationError("LOGIC_ATTRIBUTE_NOT_SELECTED", attribute_id)
    return synthesis


def _require_attribute(attribute_id: str, available: set[str], code: str) -> None:
    if attribute_id not in available:
        raise AlignmentValidationError(code, attribute_id)


class AlignmentGrounder:
    """Deterministically lower aligned semantic IDs into SQL-generation context."""

    def __init__(self, mapping: SemanticMappingSnapshot, database_id: str) -> None:
        self.mapping = mapping
        self.database_id = database_id

    def ground(self, question: str, alignment: QueryAlignmentV1) -> GroundedSQLContext:
        validate_alignment(alignment, self.mapping)
        entity_ids = set(alignment.relevant_entity_ids)
        relationship_items = self._relationships(alignment, entity_ids)
        for item in relationship_items:
            entity_ids.update((item.from_entity_id, item.to_entity_id))
        entities = tuple(
            {
                "entity_id": item.entity_id,
                "physical_table": item.physical_table,
                "description": item.description,
            }
            for item in sorted(self.mapping.entities, key=lambda value: value.entity_id)
            if item.entity_id in entity_ids
        )
        attribute_ids = set(alignment.relevant_attribute_ids)
        attribute_ids.update(
            item.field_id for item in alignment.filters if item.field_id is not None
        )
        attribute_ids.update(
            item.attribute_id for item in alignment.aggregations if item.attribute_id is not None
        )
        attribute_ids.update(alignment.grouping_attribute_ids)
        attribute_ids.update(
            item.attribute_id for item in alignment.ordering if item.attribute_id is not None
        )
        attribute_ids.update(item.attribute_id for item in alignment.temporal)
        if alignment.calculation is not None:
            attribute_ids.update(alignment.calculation.operand_attribute_ids)
        attributes = tuple(
            {
                "attribute_id": item.attribute_id,
                "entity_id": item.entity_id,
                "physical_column": item.physical_column,
                "data_type": item.data_type,
                "description": item.description,
            }
            for item in sorted(self.mapping.attributes, key=lambda value: value.attribute_id)
            if item.attribute_id in attribute_ids
        )
        return GroundedSQLContext(
            database_id=self.database_id,
            question=question,
            alignment=alignment,
            entities=entities,
            attributes=attributes,
            relationships=tuple(self._grounded_relationship(item) for item in relationship_items),
        )

    def _relationships(
        self,
        alignment: QueryAlignmentV1,
        entity_ids: set[str],
    ) -> tuple[SemanticRelationshipMapping, ...]:
        if alignment.relationship_ids:
            return tuple(self.mapping.relationship(item) for item in alignment.relationship_ids)
        if len(entity_ids) <= 1:
            return ()
        graph = SemanticRelationshipGraph(self.mapping)
        selected: dict[str, SemanticRelationshipMapping] = {}
        for entity_id in sorted(entity_ids - {alignment.anchor_entity_id}):
            try:
                path = graph.resolve_path(alignment.anchor_entity_id, entity_id, allow_fanout=True)
            except RelationshipPathError as error:
                common = self._unique_common_target_relationships(
                    alignment.anchor_entity_id, entity_id
                )
                if common is not None:
                    selected.update({item.relationship_id: item for item in common})
                    continue
                message = str(error)
                code = (
                    "AMBIGUOUS_SERVER_RELATIONSHIP"
                    if "ambiguous" in message
                    else "MISSING_SERVER_RELATIONSHIP"
                )
                raise AlignmentValidationError(code, message) from error
            for step in path:
                selected[step.relationship_id] = self.mapping.relationship(step.relationship_id)
        return tuple(selected[key] for key in sorted(selected))

    def _unique_common_target_relationships(
        self, source_entity_id: str, target_entity_id: str
    ) -> tuple[SemanticRelationshipMapping, ...] | None:
        source_edges = self.mapping.relationships_for(source_entity_id)
        target_edges = self.mapping.relationships_for(target_entity_id)
        source_targets = {
            item.to_entity_id
            if item.from_entity_id == source_entity_id
            else item.from_entity_id: item
            for item in source_edges
        }
        target_targets = {
            item.to_entity_id
            if item.from_entity_id == target_entity_id
            else item.from_entity_id: item
            for item in target_edges
        }
        common_targets = sorted(set(source_targets) & set(target_targets))
        if len(common_targets) != 1:
            return None
        return (source_targets[common_targets[0]], target_targets[common_targets[0]])

    def _grounded_relationship(
        self, relationship: SemanticRelationshipMapping
    ) -> GroundedRelationship:
        source_entity = self.mapping.entity(relationship.from_entity_id)
        target_entity = self.mapping.entity(relationship.to_entity_id)
        source_attribute = self.mapping.attribute(relationship.from_attribute_id)
        target_attribute = self.mapping.attribute(relationship.to_attribute_id)
        return GroundedRelationship(
            relationship_id=relationship.relationship_id,
            source_entity_id=relationship.from_entity_id,
            target_entity_id=relationship.to_entity_id,
            source_table=source_entity.physical_table,
            source_column=source_attribute.physical_column,
            target_table=target_entity.physical_table,
            target_column=target_attribute.physical_column,
            cardinality=relationship.cardinality,
        )


def alignment_schema_safety_errors(value: object) -> tuple[str, ...]:
    """Return stable validation diagnostics for provider-boundary tests."""

    try:
        QueryAlignmentV1.model_validate(value)
    except ValidationError as error:
        return tuple(item.get("msg", "validation error") for item in error.errors())
    return ()
