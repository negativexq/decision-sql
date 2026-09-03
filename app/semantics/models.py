from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.models import SchemaCatalog, TableMetadata


class SemanticCatalogError(ValueError):
    """The server-owned semantic catalog is internally inconsistent."""


class Aggregation(StrEnum):
    SUM = "SUM"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    AVG = "AVG"


class Cardinality(StrEnum):
    ONE_TO_ONE = "ONE_TO_ONE"
    MANY_TO_ONE = "MANY_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"


class PredicateOperator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    IN = "IN"
    NOT_IN = "NOT_IN"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"


class MetricKind(StrEnum):
    MEASURE = "MEASURE"
    RATIO = "RATIO"


class ZeroDenominatorPolicy(StrEnum):
    NULL = "NULL"
    ZERO = "ZERO"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntityDefinition(_FrozenModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    physical_table: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    key_columns: tuple[str, ...] = Field(min_length=1)
    description: str = ""


class ColumnPair(_FrozenModel):
    from_column: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    to_column: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")


class RelationshipDefinition(_FrozenModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    from_entity: str = Field(min_length=1)
    to_entity: str = Field(min_length=1)
    column_pairs: tuple[ColumnPair, ...] = Field(min_length=1)
    cardinality: Cardinality


class DimensionDefinition(_FrozenModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    entity: str = Field(min_length=1)
    physical_column: str = Field(min_length=1)
    description: str = Field(min_length=1)


class PredicateDefinition(_FrozenModel):
    entity: str = Field(min_length=1)
    column: str = Field(min_length=1)
    operator: PredicateOperator
    value: str | int | Decimal | None = None
    values: tuple[str | int | Decimal, ...] = ()


class MeasureDefinition(_FrozenModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    entity: str = Field(min_length=1)
    aggregation: Aggregation
    physical_column: str = Field(min_length=1)
    filters: tuple[PredicateDefinition, ...] = ()
    description: str = ""


class MeasureMetricDefinition(_FrozenModel):
    kind: Literal[MetricKind.MEASURE] = MetricKind.MEASURE
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    measure: str = Field(min_length=1)
    valid_dimensions: tuple[str, ...] = ()
    eligibility_filters: tuple[PredicateDefinition, ...] = ()


class RatioMetricDefinition(_FrozenModel):
    kind: Literal[MetricKind.RATIO] = MetricKind.RATIO
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    numerator_measure: str = Field(min_length=1)
    denominator_measure: str = Field(min_length=1)
    valid_dimensions: tuple[str, ...] = ()
    eligibility_filters: tuple[PredicateDefinition, ...] = ()
    scale: Decimal = Field(ge=0, max_digits=12, decimal_places=6)
    zero_denominator_policy: ZeroDenominatorPolicy


MetricDefinition = Annotated[
    MeasureMetricDefinition | RatioMetricDefinition,
    Field(discriminator="kind"),
]


class MetricCatalog(_FrozenModel):
    entities: tuple[EntityDefinition, ...] = ()
    relationships: tuple[RelationshipDefinition, ...] = ()
    dimensions: tuple[DimensionDefinition, ...] = ()
    measures: tuple[MeasureDefinition, ...] = ()
    metrics: tuple[MetricDefinition, ...] = ()

    def validate_against_schema(self, schema: SchemaCatalog) -> None:
        entity_by_name = _unique_by_name(self.entities, "entity")
        table_by_name = {table.name.lower(): table for table in schema.tables}
        for entity in self.entities:
            table = table_by_name.get(entity.physical_table.lower())
            if table is None or not table.queryable:
                raise SemanticCatalogError(f"unknown or non-queryable entity table: {entity.name}")
            for column_name in entity.key_columns:
                column = table.get_column(column_name)
                if column is None or not column.queryable:
                    raise SemanticCatalogError(f"invalid key column: {entity.name}.{column_name}")

        _unique_by_name(self.relationships, "relationship")
        pair_signatures: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
        for relationship in self.relationships:
            source = entity_by_name.get(relationship.from_entity)
            target = entity_by_name.get(relationship.to_entity)
            if source is None or target is None:
                raise SemanticCatalogError(f"unknown relationship entity: {relationship.name}")
            source_table = table_by_name[source.physical_table]
            target_table = table_by_name[target.physical_table]
            pairs = tuple((pair.from_column, pair.to_column) for pair in relationship.column_pairs)
            signature = (relationship.from_entity, relationship.to_entity, pairs)
            if signature in pair_signatures:
                raise SemanticCatalogError(f"duplicate relationship: {relationship.name}")
            pair_signatures.add(signature)
            for source_column, target_column in pairs:
                source_meta = source_table.get_column(source_column)
                target_meta = target_table.get_column(target_column)
                if source_meta is None or target_meta is None:
                    raise SemanticCatalogError(f"invalid relationship columns: {relationship.name}")
                if not source_meta.queryable or not target_meta.queryable:
                    raise SemanticCatalogError(f"non-queryable relationship: {relationship.name}")

        dimension_by_name = _unique_by_name(self.dimensions, "dimension")
        for dimension in self.dimensions:
            dimension_entity = entity_by_name.get(dimension.entity)
            if dimension_entity is None:
                raise SemanticCatalogError(f"unknown dimension entity: {dimension.name}")
            table = table_by_name[dimension_entity.physical_table]
            column = table.get_column(dimension.physical_column)
            if column is None or not column.queryable:
                raise SemanticCatalogError(f"invalid dimension column: {dimension.name}")

        measure_by_name = _unique_by_name(self.measures, "measure")
        for measure in self.measures:
            measure_entity = entity_by_name.get(measure.entity)
            if measure_entity is None:
                raise SemanticCatalogError(f"unknown measure entity: {measure.name}")
            table = table_by_name[measure_entity.physical_table]
            column = table.get_column(measure.physical_column)
            if column is None or not column.queryable:
                raise SemanticCatalogError(f"invalid measure column: {measure.name}")
            self._validate_filters(measure.filters, entity_by_name, table_by_name)

        _unique_by_name(self.metrics, "metric")
        for metric in self.metrics:
            if isinstance(metric, MeasureMetricDefinition):
                if metric.measure not in measure_by_name:
                    raise SemanticCatalogError(f"unknown metric measure: {metric.name}")
            else:
                if metric.numerator_measure not in measure_by_name:
                    raise SemanticCatalogError(f"unknown numerator measure: {metric.name}")
                if metric.denominator_measure not in measure_by_name:
                    raise SemanticCatalogError(f"unknown denominator measure: {metric.name}")
                if metric.numerator_measure == metric.denominator_measure:
                    raise SemanticCatalogError(f"ratio components must differ: {metric.name}")
            for dimension_name in metric.valid_dimensions:
                if dimension_name not in dimension_by_name:
                    raise SemanticCatalogError(f"unknown metric dimension: {dimension_name}")
            self._validate_filters(metric.eligibility_filters, entity_by_name, table_by_name)

    @staticmethod
    def _validate_filters(
        filters: tuple[PredicateDefinition, ...],
        entity_by_name: dict[str, EntityDefinition],
        table_by_name: dict[str, TableMetadata],
    ) -> None:
        for predicate in filters:
            entity = entity_by_name.get(predicate.entity)
            if entity is None:
                raise SemanticCatalogError(f"unknown filter entity: {predicate.entity}")
            table = table_by_name[entity.physical_table]
            column = table.get_column(predicate.column)
            if column is None or not column.queryable:
                raise SemanticCatalogError(
                    f"invalid filter column: {predicate.entity}.{predicate.column}"
                )
            if predicate.operator in {PredicateOperator.IN, PredicateOperator.NOT_IN}:
                if not predicate.values or predicate.value is not None:
                    raise SemanticCatalogError("IN predicates require only non-empty values")
            elif predicate.operator in {PredicateOperator.IS_NULL, PredicateOperator.IS_NOT_NULL}:
                if predicate.value is not None or predicate.values:
                    raise SemanticCatalogError("NULL predicates do not take values")
            elif predicate.value is None or predicate.values:
                raise SemanticCatalogError("scalar predicates require exactly one value")

    def entity(self, name: str) -> EntityDefinition:
        return _lookup(self.entities, name, "entity")

    def dimension(self, name: str) -> DimensionDefinition:
        return _lookup(self.dimensions, name, "dimension")

    def measure(self, name: str) -> MeasureDefinition:
        return _lookup(self.measures, name, "measure")

    def metric(self, name: str) -> MeasureMetricDefinition | RatioMetricDefinition:
        return _lookup(self.metrics, name, "metric")


def _unique_by_name[ModelT: BaseModel](items: tuple[ModelT, ...], kind: str) -> dict[str, ModelT]:
    result: dict[str, ModelT] = {}
    for item in items:
        name = str(item.model_dump()["name"])
        if name in result:
            raise SemanticCatalogError(f"duplicate {kind}: {name}")
        result[name] = item
    return result


def _lookup[ModelT: BaseModel](items: tuple[ModelT, ...], name: str, kind: str) -> ModelT:
    normalized = name.strip().lower()
    for item in items:
        if str(item.model_dump()["name"]).lower() == normalized:
            return item
    raise SemanticCatalogError(f"unknown {kind}: {name}")
