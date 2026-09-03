from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from sqlglot import exp

from app.semantics.models import (
    Aggregation,
    DimensionDefinition,
    MeasureDefinition,
    MeasureMetricDefinition,
    MetricCatalog,
    PredicateDefinition,
    PredicateOperator,
    RatioMetricDefinition,
)
from app.semantics.relationship_graph import PathStep, RelationshipGraph, RelationshipPathError
from app.semantics.requests import MetricRequest
from app.sql.models import CandidateSource, SqlCandidate


@dataclass(frozen=True)
class MetricCompilationFailure:
    code: str
    message: str


MetricCompilationResult = SqlCandidate | MetricCompilationFailure


class MetricCompiler:
    """Compile a governed metric request into policy-compatible PostgreSQL SQL."""

    def __init__(self, catalog: MetricCatalog) -> None:
        self.catalog = catalog
        self.graph = RelationshipGraph(catalog)

    def compile_metric(self, request: MetricRequest) -> MetricCompilationResult:
        try:
            normalized = request.normalized()
            metric = self.catalog.metric(normalized.metric_name)
            dimensions = tuple(self.catalog.dimension(name) for name in normalized.dimensions)
            if len(dimensions) > 2:
                return MetricCompilationFailure(
                    "INVALID_METRIC_DIMENSION", "M3 V1 supports at most two dimensions."
                )
            invalid = set(normalized.dimensions) - set(metric.valid_dimensions)
            if invalid:
                return MetricCompilationFailure(
                    "INVALID_METRIC_DIMENSION",
                    f"Dimensions are not valid for {metric.name}: {sorted(invalid)}",
                )
            if isinstance(metric, MeasureMetricDefinition):
                sql = self._compile_measure_metric(metric, dimensions)
            else:
                sql = self._compile_ratio_metric(metric, dimensions)
            return SqlCandidate(source=CandidateSource.SEMANTIC_METRIC_COMPILER, sql=sql)
        except RelationshipPathError as error:
            code = (
                "AMBIGUOUS_RELATIONSHIP_PATH"
                if "ambiguous" in str(error)
                else "FANOUT_UNSAFE_DIMENSION_PATH"
            )
            return MetricCompilationFailure(code, str(error))
        except (ValueError, KeyError) as error:
            return MetricCompilationFailure("COMPILER_FAILED", str(error))

    def _compile_measure_metric(
        self, metric: MeasureMetricDefinition, dimensions: tuple[DimensionDefinition, ...]
    ) -> str:
        measure = self.catalog.measure(metric.measure)
        return self._measure_select(measure, dimensions, metric.eligibility_filters).sql(
            dialect="postgres"
        )

    def _compile_ratio_metric(
        self, metric: RatioMetricDefinition, dimensions: tuple[DimensionDefinition, ...]
    ) -> str:
        numerator = self.catalog.measure(metric.numerator_measure)
        denominator = self.catalog.measure(metric.denominator_measure)
        numerator_select = self._measure_select(numerator, dimensions, metric.eligibility_filters)
        denominator_select = self._measure_select(
            denominator, dimensions, metric.eligibility_filters
        )
        numerator_ref = exp.column("measure_value", table="numerator")
        denominator_ref = exp.column("measure_value", table="denominator")
        numerator_value = exp.Coalesce(expressions=[numerator_ref, exp.Literal.number(0)])
        denominator_value = exp.Coalesce(expressions=[denominator_ref, exp.Literal.number(0)])
        scaled = exp.Mul(
            this=exp.Div(
                this=exp.Cast(this=numerator_value, to=exp.DataType.build("DECIMAL")),
                expression=exp.Cast(this=denominator_value, to=exp.DataType.build("DECIMAL")),
            ),
            expression=_decimal_literal(metric.scale),
        )
        zero_value: exp.Expression = (
            exp.Null() if metric.zero_denominator_policy.value == "NULL" else exp.Literal.number(0)
        )
        safe_value = exp.Case(
            ifs=[
                exp.If(
                    this=exp.EQ(this=denominator_value.copy(), expression=exp.Literal.number(0)),
                    true=zero_value,
                )
            ],
            default=scaled,
        ).as_("metric_value")
        query = exp.Select()
        if dimensions:
            for index in range(len(dimensions)):
                dimension_refs = [
                    exp.column(f"dimension_{index}", table="numerator"),
                    exp.column(f"dimension_{index}", table="denominator"),
                ]
                query = query.select(
                    exp.Coalesce(expressions=dimension_refs).as_(f"dimension_{index}")
                )
            query = query.select(safe_value)
            join_condition = _and(
                [
                    exp.EQ(
                        this=exp.column(f"dimension_{index}", table="numerator"),
                        expression=exp.column(f"dimension_{index}", table="denominator"),
                    )
                    for index in range(len(dimensions))
                ]
            )
            query = query.from_("numerator").join(
                "denominator",
                on=join_condition,
                join_type="FULL OUTER",
            )
            query = query.order_by(exp.column("dimension_0"))
        else:
            query = (
                query.select(safe_value).from_("numerator").join("denominator", join_type="CROSS")
            )
        query = query.with_("numerator", as_=numerator_select)
        query = query.with_("denominator", as_=denominator_select)
        return query.sql(dialect="postgres")

    def _measure_select(
        self,
        measure: MeasureDefinition,
        dimensions: tuple[DimensionDefinition, ...],
        extra_filters: Iterable[PredicateDefinition],
    ) -> exp.Select:
        measure_entity = self.catalog.entity(measure.entity)
        dimension_entities = [dimension.entity for dimension in dimensions]
        filter_definitions = (*measure.filters, *extra_filters)
        filter_entities = [predicate.entity for predicate in filter_definitions]
        paths = [
            self.graph.shortest_safe_path(measure.entity, entity)
            for entity in (*dimension_entities, *filter_entities)
        ]
        steps = _unique_steps(paths)
        query = exp.Select()
        for index, dimension in enumerate(dimensions):
            dimension_entity = self.catalog.entity(dimension.entity)
            column = exp.column(dimension.physical_column, table=dimension_entity.physical_table)
            query = query.select(column.as_(f"dimension_{index}"))
        measure_column = exp.column(measure.physical_column, table=measure_entity.physical_table)
        query = query.select(_aggregate(measure.aggregation, measure_column).as_("measure_value"))
        query = query.from_(measure_entity.physical_table)
        joined = {measure.entity}
        for step in steps:
            if step.target_entity in joined:
                continue
            if step.source_entity not in joined:
                raise RelationshipPathError(
                    f"non-contiguous relationship path: {step.source_entity}"
                )
            source = self.catalog.entity(step.source_entity)
            target = self.catalog.entity(step.target_entity)
            pair = step.relationship.column_pairs[0]
            if step.forward:
                source_column, target_column = pair.from_column, pair.to_column
            else:
                source_column, target_column = pair.to_column, pair.from_column
            on = exp.EQ(
                this=exp.column(source_column, table=source.physical_table),
                expression=exp.column(target_column, table=target.physical_table),
            )
            query = query.join(target.physical_table, on=on, join_type="INNER")
            joined.add(step.target_entity)
        predicates = [self._predicate_sql(predicate) for predicate in filter_definitions]
        if predicates:
            query = query.where(_and(predicates))
        if dimensions:
            query = query.group_by(
                *[
                    exp.column(
                        dimension.physical_column,
                        table=self.catalog.entity(dimension.entity).physical_table,
                    )
                    for dimension in dimensions
                ]
            )
            query = query.order_by(exp.column("dimension_0"))
        return query

    def _predicate_sql(self, predicate: PredicateDefinition) -> exp.Expression:
        column = exp.column(
            predicate.column, table=self.catalog.entity(predicate.entity).physical_table
        )
        operator = predicate.operator
        if operator is PredicateOperator.IS_NULL:
            return exp.Is(this=column, expression=exp.Null())
        if operator is PredicateOperator.IS_NOT_NULL:
            return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
        if operator in {PredicateOperator.IN, PredicateOperator.NOT_IN}:
            values = [self._literal(value) for value in predicate.values]
            expression: exp.Expression = exp.In(this=column, expressions=values)
            return exp.Not(this=expression) if operator is PredicateOperator.NOT_IN else expression
        if predicate.value is None:
            raise ValueError("predicate value is missing")
        right = self._literal(predicate.value)
        operators: dict[PredicateOperator, type[exp.Binary]] = {
            PredicateOperator.EQ: exp.EQ,
            PredicateOperator.NE: exp.NEQ,
            PredicateOperator.GT: exp.GT,
            PredicateOperator.GTE: exp.GTE,
            PredicateOperator.LT: exp.LT,
            PredicateOperator.LTE: exp.LTE,
        }
        return operators[operator](this=column, expression=right)

    @staticmethod
    def _literal(value: str | int | Decimal) -> exp.Expression:
        if isinstance(value, str):
            return exp.Literal.string(value)
        return _decimal_literal(value) if isinstance(value, Decimal) else exp.Literal.number(value)


def _unique_steps(paths: Iterable[tuple[PathStep, ...]]) -> tuple[PathStep, ...]:
    result: list[PathStep] = []
    seen: set[tuple[str, str, str, bool]] = set()
    for path in paths:
        for step in path:
            key = (step.source_entity, step.target_entity, step.relationship.name, step.forward)
            if key not in seen:
                result.append(step)
                seen.add(key)
    return tuple(result)


def _aggregate(aggregation: Aggregation, column: exp.Expression) -> exp.Expression:
    if aggregation is Aggregation.SUM:
        return exp.Sum(this=column)
    if aggregation is Aggregation.AVG:
        return exp.Avg(this=column)
    if aggregation is Aggregation.COUNT:
        return exp.Count(this=column)
    return exp.Count(this=exp.Distinct(expressions=[column]))


def _and(predicates: list[exp.Expression]) -> exp.Expression:
    result = predicates[0]
    for predicate in predicates[1:]:
        result = exp.And(this=result, expression=predicate)
    return result


def _decimal_literal(value: Decimal | int | str) -> exp.Literal:
    return cast(
        exp.Literal,
        exp.Literal.number(format(value, "f") if isinstance(value, Decimal) else value),
    )
