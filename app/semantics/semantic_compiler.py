"""Deterministic lowering from the canonical semantic IR to SQLGlot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlglot import exp

from app.semantics.relationship_graph import RelationshipPathError, SemanticRelationshipGraph
from app.semantics.semantic_errors import (
    SemanticCompilationError,
    SemanticFailureCode,
    SemanticValidationError,
)
from app.semantics.semantic_mapping import SemanticMappingError, SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    BetweenExpression,
    BinaryExpression,
    BinaryOperator,
    CaseExpression,
    CastExpression,
    CommonTableExpression,
    CTERelationSource,
    DerivedRelation,
    DerivedRelationSource,
    EntityRelationSource,
    ExistsExpression,
    ExportedAttribute,
    Expression,
    FunctionExpression,
    InExpression,
    IntervalExpression,
    IsNullExpression,
    LiteralExpression,
    LogicalExpression,
    NotExpression,
    OrderedAggregateExpression,
    PlannedJoin,
    RelationSource,
    ScalarSubqueryExpression,
    SemanticFunction,
    SemanticQueryIR,
    StarExpression,
    WindowExpression,
)


class FunctionCapability(StrEnum):
    SAFE_SCALAR = "SAFE_SCALAR"
    SAFE_AGGREGATE = "SAFE_AGGREGATE"
    SAFE_WINDOW = "SAFE_WINDOW"
    VOLATILE = "VOLATILE"
    SIDE_EFFECT = "SIDE_EFFECT"
    FORBIDDEN = "FORBIDDEN"


SAFE_FUNCTION_CAPABILITIES: dict[SemanticFunction, FunctionCapability] = {
    SemanticFunction.COALESCE: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.NULLIF: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.CAST: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.ROUND: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.ABS: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.GREATEST: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.LEAST: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.POWER: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.SQRT: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.LN: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.LOG: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.EXP: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.TRIM: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.CONCAT_WS: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.TO_CHAR: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.ARRAY_TO_STRING: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.ROW_TO_JSON: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.JSON_BUILD_OBJECT: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.JSONB_BUILD_OBJECT: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.ARRAY: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.ARRAY_REMOVE: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.CARDINALITY: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.UNNEST: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.DATE_TRUNC: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.EXTRACT: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.AGE: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.TIMESTAMP_TRUNC: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.CURRENT_DATE: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.CURRENT_TIME: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.CURRENT_TIMESTAMP: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.LOCALTIME: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.LOCALTIMESTAMP: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.NOW: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.STATEMENT_TIMESTAMP: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.TRANSACTION_TIMESTAMP: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.COUNT: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.COUNT_DISTINCT: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.SUM: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.AVG: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.MIN: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.MAX: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.ARRAY_AGG: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.STRING_AGG: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.JSON_AGG: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.JSONB_AGG: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.JSON_OBJECT_AGG: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.JSONB_OBJECT_AGG: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.CORR: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.REGR_SLOPE: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.STDDEV: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.PERCENTILE_CONT: FunctionCapability.SAFE_AGGREGATE,
    SemanticFunction.ROW_NUMBER: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.RANK: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.DENSE_RANK: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.LAG: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.LEAD: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.NTH_VALUE: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.NTILE: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.PERCENT_RANK: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.FIRST_VALUE: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.LAST_VALUE: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.COUNT: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.SUM: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.AVG: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.MIN: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.MAX: FunctionCapability.SAFE_WINDOW,
    SemanticFunction.JSONB_EXTRACT_PATH_TEXT: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.JSONB_EXTRACT_SCALAR: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.JSON_EXTRACT: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.JSON_EXTRACT_SCALAR: FunctionCapability.SAFE_SCALAR,
    SemanticFunction.STRING_TO_ARRAY: FunctionCapability.SAFE_SCALAR,
}


SAFE_OPERATORS = set(BinaryOperator)


@dataclass(frozen=True)
class CompiledSemanticQuery:
    ast: exp.Select
    sql: str
    compiler_version: str


@dataclass(frozen=True)
class _RelationBinding:
    """Compiler-owned SQL alias and the semantic outputs visible in a scope."""

    source_id: str
    alias: str
    entity_id: str | None = None
    attributes: dict[str, str] | None = None


class ScopedAliasAllocator:
    """One deterministic alias allocator shared by all recursive query scopes."""

    def __init__(self) -> None:
        self._next = 0

    def allocate(self) -> str:
        alias = f"t{self._next}"
        self._next += 1
        return alias


class SemanticPlanValidator:
    """Validate semantic IDs and graph/population invariants before lowering."""

    def __init__(self, mapping: SemanticMappingSnapshot) -> None:
        self.mapping = mapping
        self.graph = SemanticRelationshipGraph(mapping)
        self._local_attributes: dict[str, set[str]] = {}
        self._outer_attributes: dict[str, set[str]] = {}
        self._visible_ctes: tuple[CommonTableExpression, ...] = ()

    def validate(
        self,
        ir: SemanticQueryIR,
        *,
        _visible_ctes: tuple[CommonTableExpression, ...] = (),
        _outer_attributes: dict[str, set[str]] | None = None,
    ) -> None:
        # Only outputs exported by relations declared in this query are in
        # scope.  Nested validators use their own instance so a sibling or
        # child query can never leak an attribute into this scope.
        self._visible_ctes = _visible_ctes
        self._local_attributes = _local_scope_attribute_ids(ir, _visible_ctes)
        self._outer_attributes = dict(_outer_attributes or {})
        self._validate_nested_definitions(ir, _visible_ctes)
        root_entity = ir.from_entity_id
        if isinstance(ir.from_source, EntityRelationSource):
            root_entity = ir.from_source.entity_id
        if root_entity is not None:
            try:
                self.mapping.entity(root_entity)
            except SemanticMappingError as error:
                raise SemanticValidationError(
                    SemanticFailureCode.UNKNOWN_SEMANTIC_ENTITY, str(error)
                ) from error
        if root_entity is not None and ir.population_contract.base_entity_ids != (root_entity,):
            raise SemanticValidationError(
                SemanticFailureCode.INVALID_POPULATION_CONTRACT,
                "population base entity must match the mapped query source",
            )
        for entity_id in ir.population_contract.base_entity_ids:
            self._entity(entity_id)
        calculation_grain = (
            ir.calculation_contract.grain_attribute_ids
            if ir.calculation_contract is not None
            else ()
        )
        for attribute_id in (
            *ir.population_contract.result_grain_attribute_ids,
            *ir.population_contract.aggregation_grain_attribute_ids,
            *calculation_grain,
        ):
            try:
                self.mapping.attribute(attribute_id)
            except SemanticMappingError as error:
                raise SemanticValidationError(
                    SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, str(error)
                ) from error
        if len(set(ir.population_contract.required_relationship_ids)) != len(
            ir.population_contract.required_relationship_ids
        ):
            raise SemanticValidationError(
                SemanticFailureCode.INVALID_POPULATION_CONTRACT,
                "population relationship IDs must be unique",
            )
        for item in ir.select:
            self._expression(item.expression)
        for expression in ir.group_by:
            self._expression(expression)
        if ir.where is not None:
            self._expression(ir.where)
        if ir.having is not None:
            self._expression(ir.having)
        for order_spec in ir.order_by:
            self._expression(order_spec.expression)
        if ir.calculation_contract is not None:
            calculation = ir.calculation_contract
            if calculation.numerator is not None:
                self._expression(calculation.numerator)
            if calculation.denominator is not None:
                self._expression(calculation.denominator)
        # A CTE/derived source has no direct entity ID, but its population
        # still carries the semantic entities represented by that relation.
        # Relationship joins after a logical result must validate against that
        # lineage instead of appearing disconnected merely because the source
        # was lowered into a server-owned scope.
        joined: set[str] = set(
            self._source_base_entities(ir.from_source, ir, self._visible_ctes)
            if ir.from_source is not None
            else ((root_entity,) if root_entity is not None else ())
        )
        for planned_join in ir.joins:
            relationship_ids = planned_join.relationship_path or (
                (planned_join.relationship_id,) if planned_join.relationship_id is not None else ()
            )
            if planned_join.relationship_id is None:
                if planned_join.relationship_path:
                    for relationship_id in relationship_ids:
                        self._validate_relationship_step(ir, joined, relationship_id)
                    continue
                if planned_join.target_source is None or not planned_join.join_keys:
                    raise SemanticValidationError(
                        SemanticFailureCode.UNKNOWN_RELATION_SOURCE,
                        "typed source join is incomplete",
                    )
                for key in planned_join.join_keys:
                    self._expression(key.left)
                    self._expression(key.right)
                joined.update(self._source_base_entities(planned_join.target_source, ir))
                continue
            for relationship_id in relationship_ids:
                self._validate_relationship_step(ir, joined, relationship_id)
        for attribute_id in (
            *ir.population_contract.result_grain_attribute_ids,
            *ir.population_contract.aggregation_grain_attribute_ids,
            *calculation_grain,
        ):
            try:
                attribute = self.mapping.attribute(attribute_id)
            except SemanticMappingError as error:
                raise SemanticValidationError(
                    SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, str(error)
                ) from error
            if attribute.entity_id not in joined:
                raise SemanticValidationError(
                    SemanticFailureCode.INVALID_GRAIN,
                    f"grain attribute is outside the selected relation set: {attribute_id}",
                )
        if ir.population_contract.required_relationship_ids:
            missing = set(ir.population_contract.required_relationship_ids) - {
                relationship_id
                for item in ir.joins
                for relationship_id in (
                    item.relationship_path
                    or ((item.relationship_id,) if item.relationship_id is not None else ())
                )
            }
            if missing:
                raise SemanticValidationError(
                    SemanticFailureCode.INVALID_POPULATION_CONTRACT,
                    f"population requires unplanned relationships: {sorted(missing)}",
                )

    def _validate_relationship_step(
        self, ir: SemanticQueryIR, joined: set[str], relationship_id: str
    ) -> None:
        try:
            relationship = self.mapping.relationship(relationship_id)
        except SemanticMappingError as error:
            raise SemanticValidationError(
                SemanticFailureCode.UNKNOWN_RELATIONSHIP, str(error)
            ) from error
        endpoints = {relationship.from_entity_id, relationship.to_entity_id}
        if len(endpoints & joined) != 1:
            raise SemanticValidationError(
                SemanticFailureCode.AMBIGUOUS_RELATIONSHIP_PATH,
                f"relationship does not introduce one connected entity: {relationship_id}",
            )
        connected_entity = next(iter(endpoints & joined))
        new_entity = next(iter(endpoints - joined))
        if (
            not ir.population_contract.fanout_allowed
            and connected_entity == relationship.to_entity_id
            and relationship.cardinality != "ONE_TO_ONE"
        ):
            raise SemanticValidationError(
                SemanticFailureCode.FANOUT_UNSAFE_PATH,
                f"relationship expands the active population: {relationship_id}",
            )
        try:
            self.graph.resolve_path(
                connected_entity,
                new_entity,
                allow_fanout=ir.population_contract.fanout_allowed,
            )
        except RelationshipPathError as error:
            text = str(error).lower()
            if "ambiguous" in text:
                code = SemanticFailureCode.AMBIGUOUS_RELATIONSHIP_PATH
            elif "no safe" in text:
                code = SemanticFailureCode.NO_RELATIONSHIP_PATH
            else:
                code = SemanticFailureCode.FANOUT_UNSAFE_PATH
            raise SemanticValidationError(code, str(error)) from error
        joined.update(endpoints)

    def _entity(self, entity_id: str) -> None:
        try:
            self.mapping.entity(entity_id)
        except SemanticMappingError as error:
            raise SemanticValidationError(
                SemanticFailureCode.UNKNOWN_SEMANTIC_ENTITY, str(error)
            ) from error

    @staticmethod
    def _source_base_entities(
        source: RelationSource | None,
        ir: SemanticQueryIR,
        visible_ctes: tuple[CommonTableExpression, ...] = (),
    ) -> tuple[str, ...]:
        if isinstance(source, EntityRelationSource):
            return (source.entity_id,)
        if isinstance(source, CTERelationSource):
            for cte in (*ir.ctes, *visible_ctes):
                if cte.cte_id == source.cte_id:
                    return cte.query.population_contract.base_entity_ids
        if isinstance(source, DerivedRelationSource):
            for relation in ir.derived_relations:
                if relation.relation_id == source.relation_id:
                    return relation.query.population_contract.base_entity_ids
        return ()

    def _validate_nested_definitions(
        self, ir: SemanticQueryIR, visible_ctes: tuple[CommonTableExpression, ...]
    ) -> None:
        cte_ids = [item.cte_id for item in ir.ctes]
        if len(cte_ids) != len(set(cte_ids)):
            raise SemanticValidationError(
                SemanticFailureCode.DUPLICATE_CTE_ID, "duplicate CTE identifier"
            )
        derived_ids = [item.relation_id for item in ir.derived_relations]
        if len(derived_ids) != len(set(derived_ids)):
            raise SemanticValidationError(
                SemanticFailureCode.UNKNOWN_RELATION_SOURCE, "duplicate derived relation ID"
            )
        available_ctes = {cte.cte_id: cte for cte in visible_ctes}
        for cte in ir.ctes:
            SemanticPlanValidator(self.mapping).validate(
                cte.query, _visible_ctes=tuple(available_ctes.values())
            )
            available_ctes[cte.cte_id] = cte
        for relation in ir.derived_relations:
            SemanticPlanValidator(self.mapping).validate(relation.query)
        for join in ir.joins:
            if join.target_source is not None:
                if (
                    isinstance(join.target_source, CTERelationSource)
                    and join.target_source.cte_id not in cte_ids
                ):
                    if join.target_source.cte_id in available_ctes:
                        continue
                    raise SemanticValidationError(
                        SemanticFailureCode.INVALID_CTE_REFERENCE,
                        join.target_source.cte_id,
                    )
                if (
                    isinstance(join.target_source, DerivedRelationSource)
                    and join.target_source.relation_id not in derived_ids
                ):
                    raise SemanticValidationError(
                        SemanticFailureCode.UNKNOWN_RELATION_SOURCE,
                        join.target_source.relation_id,
                    )
        if isinstance(ir.from_source, CTERelationSource):
            if ir.from_source.cte_id not in available_ctes:
                raise SemanticValidationError(
                    SemanticFailureCode.INVALID_CTE_REFERENCE, ir.from_source.cte_id
                )
        if (
            isinstance(ir.from_source, DerivedRelationSource)
            and ir.from_source.relation_id not in derived_ids
        ):
            raise SemanticValidationError(
                SemanticFailureCode.UNKNOWN_RELATION_SOURCE, ir.from_source.relation_id
            )

    def _expression(self, expression: Expression) -> None:
        if isinstance(expression, AttributeRef):
            if expression.attribute_id.startswith("output:"):
                if expression.relation_ref is None:
                    raise SemanticValidationError(
                        SemanticFailureCode.INVALID_SCOPE_REFERENCE,
                        expression.attribute_id,
                    )
                visible = self._local_attributes.get(expression.relation_ref)
                if visible is None:
                    visible = self._outer_attributes.get(expression.relation_ref)
                if visible is None or expression.attribute_id not in visible:
                    raise SemanticValidationError(
                        SemanticFailureCode.UNKNOWN_DERIVED_ATTRIBUTE,
                        expression.attribute_id,
                    )
                return
            if expression.relation_ref and not expression.attribute_id.startswith("attribute:"):
                raise SemanticValidationError(
                    SemanticFailureCode.UNKNOWN_DERIVED_ATTRIBUTE, expression.attribute_id
                )
            try:
                self.mapping.attribute(expression.attribute_id)
            except SemanticMappingError as error:
                raise SemanticValidationError(
                    SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, str(error)
                ) from error
            return
        if isinstance(expression, (LiteralExpression, IntervalExpression, StarExpression)):
            return
        if isinstance(expression, AggregateExpression):
            if expression.function not in {
                "COUNT",
                "COUNT_DISTINCT",
                "SUM",
                "AVG",
                "MIN",
                "MAX",
                "CORR",
                "REGR_SLOPE",
                "STDDEV",
                "PERCENTILE_CONT",
                "ARRAY_AGG",
                "STRING_AGG",
                "JSON_AGG",
                "JSONB_AGG",
                "JSON_OBJECT_AGG",
                "JSONB_OBJECT_AGG",
            }:
                raise SemanticValidationError(
                    SemanticFailureCode.INVALID_AGGREGATION,
                    f"unsupported aggregate: {expression.function}",
                )
            self._expression(expression.expression)
            if expression.filter is not None:
                self._expression(expression.filter)
            return
        if isinstance(expression, OrderedAggregateExpression):
            if expression.function != "PERCENTILE_CONT":
                raise SemanticValidationError(
                    SemanticFailureCode.INVALID_AGGREGATION,
                    f"unsupported ordered aggregate: {expression.function}",
                )
            self._expression(expression.percentile)
            self._expression(expression.expression)
            return
        if isinstance(expression, BinaryExpression):
            if expression.operator not in SAFE_OPERATORS:
                raise SemanticValidationError(
                    SemanticFailureCode.UNSUPPORTED_OPERATOR,
                    f"unsupported operator: {expression.operator}",
                )
            self._expression(expression.left)
            self._expression(expression.right)
            return
        if isinstance(expression, LogicalExpression):
            for term in expression.terms:
                self._expression(term)
            return
        if isinstance(expression, NotExpression):
            self._expression(expression.expression)
            return
        if isinstance(expression, BetweenExpression):
            self._expression(expression.expression)
            self._expression(expression.low)
            self._expression(expression.high)
            return
        if isinstance(expression, InExpression):
            self._expression(expression.expression)
            for value in expression.values:
                self._expression(value)
            return
        if isinstance(expression, IsNullExpression):
            self._expression(expression.expression)
            return
        if isinstance(expression, CastExpression):
            self._expression(expression.expression)
            return
        if isinstance(expression, CaseExpression):
            for branch in expression.branches:
                self._expression(branch.when)
                self._expression(branch.then)
            if expression.default is not None:
                self._expression(expression.default)
            return
        if isinstance(expression, FunctionExpression):
            capability = SAFE_FUNCTION_CAPABILITIES.get(expression.function)
            if capability not in {
                FunctionCapability.SAFE_SCALAR,
                FunctionCapability.SAFE_AGGREGATE,
                FunctionCapability.SAFE_WINDOW,
            }:
                raise SemanticValidationError(
                    SemanticFailureCode.UNSUPPORTED_FUNCTION,
                    f"function is not in the reviewed semantic surface: {expression.function}",
                )
            for argument in expression.arguments:
                self._expression(argument)
            return
        if isinstance(expression, WindowExpression):
            if (
                SAFE_FUNCTION_CAPABILITIES.get(SemanticFunction(expression.function))
                is not FunctionCapability.SAFE_WINDOW
            ):
                raise SemanticValidationError(
                    SemanticFailureCode.UNSUPPORTED_FUNCTION,
                    f"unsupported window function: {expression.function}",
                )
            for argument in expression.arguments:
                self._expression(argument)
            for partition in expression.partition_by:
                self._expression(partition)
            for order in expression.order_by:
                self._expression(order.expression)
            return
        if isinstance(expression, ExistsExpression):
            SemanticPlanValidator(self.mapping).validate(
                expression.query,
                _visible_ctes=self._visible_ctes,
                _outer_attributes=self._local_attributes,
            )
            return
        if isinstance(expression, ScalarSubqueryExpression):
            if len(expression.query.select) != 1:
                raise SemanticValidationError(
                    SemanticFailureCode.DERIVED_RELATION_SCHEMA_MISMATCH,
                    "a scalar subquery must export exactly one expression",
                )
            if expression.query.population_contract.expected_cardinality.value == "MANY_ROWS":
                raise SemanticValidationError(
                    SemanticFailureCode.INVALID_POPULATION_CONTRACT,
                    "a scalar subquery cannot declare MANY_ROWS cardinality",
                )
            SemanticPlanValidator(self.mapping).validate(
                expression.query,
                _visible_ctes=self._visible_ctes,
                _outer_attributes=self._local_attributes,
            )
            return
        raise SemanticValidationError(
            SemanticFailureCode.COMPILATION_FAILED,
            f"unsupported semantic expression: {type(expression).__name__}",
        )


class ExpressionCompiler:
    """Shared expression lowering primitive used by semantic and metric code."""

    def __init__(
        self,
        mapping: SemanticMappingSnapshot,
        aliases: dict[str, str],
        bindings: dict[str, _RelationBinding] | None = None,
        default_source: str | None = None,
        nested_query_compiler: Callable[[SemanticQueryIR], exp.Select] | None = None,
    ) -> None:
        self.mapping = mapping
        self.aliases = aliases
        self.bindings = bindings or {}
        self.default_source = default_source
        self.nested_query_compiler = nested_query_compiler

    def compile(self, expression: Expression) -> exp.Expression:
        try:
            return self._compile(expression)
        except SemanticCompilationError:
            raise
        except (KeyError, ValueError, TypeError) as error:
            raise SemanticCompilationError(
                SemanticFailureCode.COMPILATION_FAILED, str(error)
            ) from error

    def _compile(self, expression: Expression) -> exp.Expression:
        if isinstance(expression, AttributeRef):
            if self.bindings:
                source_id = expression.relation_ref or self.default_source
                candidates = (
                    [self.bindings[source_id]]
                    if source_id in self.bindings
                    else list(self.bindings.values())
                    if source_id is None
                    else []
                )
                matches = [
                    binding
                    for binding in candidates
                    if binding.attributes and expression.attribute_id in binding.attributes
                ]
                if not matches and expression.relation_ref is None:
                    matches = [
                        binding
                        for binding in self.bindings.values()
                        if binding.attributes and expression.attribute_id in binding.attributes
                    ]
                if len(matches) != 1:
                    code = (
                        SemanticFailureCode.AMBIGUOUS_SCOPE_REFERENCE
                        if len(matches) > 1
                        else SemanticFailureCode.UNKNOWN_DERIVED_ATTRIBUTE
                        if source_id in self.bindings
                        else SemanticFailureCode.INVALID_SCOPE_REFERENCE
                    )
                    raise SemanticCompilationError(code, expression.attribute_id)
                binding = matches[0]
                if binding.attributes is None:
                    raise SemanticCompilationError(
                        SemanticFailureCode.UNKNOWN_DERIVED_ATTRIBUTE,
                        expression.attribute_id,
                    )
                return exp.column(binding.attributes[expression.attribute_id], table=binding.alias)
            attribute = self.mapping.attribute(expression.attribute_id)
            alias = self.aliases.get(attribute.entity_id)
            if alias is None:
                raise SemanticCompilationError(
                    SemanticFailureCode.UNKNOWN_SEMANTIC_ENTITY,
                    f"attribute entity is not in the compiled relation set: {attribute.entity_id}",
                )
            return exp.column(attribute.physical_column, table=alias)
        if isinstance(expression, LiteralExpression):
            return self._literal(expression)
        if isinstance(expression, IntervalExpression):
            return exp.Interval(  # type: ignore[no-untyped-call]
                this=exp.Literal.string(expression.amount),
                unit=exp.Var(this=expression.unit.upper()),
            )
        if isinstance(expression, StarExpression):
            return exp.Star()
        if isinstance(expression, AggregateExpression):
            value = self._compile(expression.expression)
            distinct = expression.distinct or expression.function == "COUNT_DISTINCT"
            aggregate_value = exp.Distinct(expressions=[value]) if distinct else value
            if expression.function == "COUNT":
                aggregate_result: exp.Expression = exp.Count(this=aggregate_value)
            elif expression.function == "COUNT_DISTINCT":
                aggregate_result = exp.Count(this=aggregate_value)
            else:
                aggregate = {
                    "SUM": exp.Sum,
                    "AVG": exp.Avg,
                    "MIN": exp.Min,
                    "MAX": exp.Max,
                }.get(expression.function)
                if aggregate is None:
                    if expression.function in {
                        "CORR",
                        "REGR_SLOPE",
                        "STDDEV",
                        "PERCENTILE_CONT",
                        "ARRAY_AGG",
                        "STRING_AGG",
                        "JSON_AGG",
                        "JSONB_AGG",
                        "JSON_OBJECT_AGG",
                        "JSONB_OBJECT_AGG",
                    }:
                        aggregate_result = exp.Anonymous(
                            this=expression.function,
                            expressions=[value],
                        )
                    else:
                        raise SemanticCompilationError(
                            SemanticFailureCode.INVALID_AGGREGATION,
                            f"unsupported aggregate: {expression.function}",
                        )
                else:
                    aggregate_result = aggregate(this=aggregate_value)
            if expression.filter is not None:
                return exp.Filter(
                    this=aggregate_result,
                    expression=exp.Where(this=self._compile(expression.filter)),
                )
            return aggregate_result
        if isinstance(expression, OrderedAggregateExpression):
            return exp.WithinGroup(
                this=exp.PercentileCont(this=self._compile(expression.percentile)),
                expression=exp.Order(
                    expressions=[exp.Ordered(this=self._compile(expression.expression))]
                ),
            )
        if isinstance(expression, BinaryExpression):
            left = self._compile(expression.left)
            right = self._compile(expression.right)
            # Preserve nested semantic operators at operand boundaries only;
            # simple predicates retain their existing compact rendering.
            if isinstance(expression.left, (BinaryExpression, LogicalExpression)):
                left = exp.Paren(this=left)
            if isinstance(expression.right, (BinaryExpression, LogicalExpression)):
                right = exp.Paren(this=right)
            operators: dict[BinaryOperator, type[exp.Expression]] = {
                BinaryOperator.ADD: exp.Add,
                BinaryOperator.SUBTRACT: exp.Sub,
                BinaryOperator.MULTIPLY: exp.Mul,
                BinaryOperator.DIVIDE: exp.Div,
                BinaryOperator.MODULO: exp.Mod,
                BinaryOperator.EQ: exp.EQ,
                BinaryOperator.NE: exp.NEQ,
                BinaryOperator.LT: exp.LT,
                BinaryOperator.LTE: exp.LTE,
                BinaryOperator.GT: exp.GT,
                BinaryOperator.GTE: exp.GTE,
            }
            operator = operators.get(expression.operator)
            if operator is None:
                raise SemanticCompilationError(
                    SemanticFailureCode.UNSUPPORTED_OPERATOR, str(expression.operator)
                )
            binary = (
                operator(this=left, expression=right, typed=True)
                if operator is exp.Div
                else operator(this=left, expression=right)
            )
            return binary
        if isinstance(expression, LogicalExpression):
            terms: list[exp.Expression] = []
            for item in expression.terms:
                term = self._compile(item)
                if isinstance(item, LogicalExpression) and item.operator != expression.operator:
                    term = exp.Paren(this=term)
                terms.append(term)
            logical_result = terms[0]
            for term in terms[1:]:
                logical_result = (
                    exp.And(this=logical_result, expression=term)
                    if expression.operator == "AND"
                    else exp.Or(this=logical_result, expression=term)
                )
            return logical_result
        if isinstance(expression, NotExpression):
            return exp.Not(this=self._compile(expression.expression))
        if isinstance(expression, BetweenExpression):
            return exp.Between(
                this=self._compile(expression.expression),
                low=self._compile(expression.low),
                high=self._compile(expression.high),
            )
        if isinstance(expression, InExpression):
            in_result: exp.Expression = exp.In(
                this=self._compile(expression.expression),
                expressions=[self._compile(item) for item in expression.values],
            )
            return exp.Not(this=in_result) if expression.negated else in_result
        if isinstance(expression, IsNullExpression):
            null_result = exp.Is(this=self._compile(expression.expression), expression=exp.Null())
            return exp.Not(this=null_result) if expression.negated else null_result
        if isinstance(expression, CastExpression):
            return exp.Cast(
                this=self._compile(expression.expression),
                to=exp.DataType.build(expression.target_type),
            )
        if isinstance(expression, CaseExpression):
            return exp.Case(
                ifs=[
                    exp.If(this=self._compile(branch.when), true=self._compile(branch.then))
                    for branch in expression.branches
                ],
                default=self._compile(expression.default)
                if expression.default is not None
                else None,
            )
        if isinstance(expression, FunctionExpression):
            return self._function(expression)
        if isinstance(expression, WindowExpression):
            function = self._function_name(expression.function, expression.arguments)
            return exp.Window(
                this=function,
                partition_by=[self._compile(item) for item in expression.partition_by],
                order=exp.Order(
                    expressions=[
                        exp.Ordered(
                            this=self._compile(item.expression), desc=item.direction.value == "DESC"
                        )
                        for item in expression.order_by
                    ]
                )
                if expression.order_by
                else None,
            )
        if isinstance(expression, ExistsExpression):
            nested = (
                self.nested_query_compiler(expression.query)
                if self.nested_query_compiler is not None
                else SemanticQueryCompiler(self.mapping).compile(expression.query).ast
            )
            exists_result = exp.Exists(this=nested)
            return exp.Not(this=exists_result) if expression.negated else exists_result
        if isinstance(expression, ScalarSubqueryExpression):
            nested = (
                self.nested_query_compiler(expression.query)
                if self.nested_query_compiler is not None
                else SemanticQueryCompiler(self.mapping).compile(expression.query).ast
            )
            return exp.Subquery(this=nested)
        raise SemanticCompilationError(
            SemanticFailureCode.COMPILATION_FAILED, type(expression).__name__
        )

    @staticmethod
    def _literal(value: LiteralExpression) -> exp.Expression:
        if value.value_type == "null":
            return exp.Null()
        if value.value_type == "string":
            return exp.Literal.string(str(value.value))
        if value.value_type == "boolean":
            return exp.Boolean(this=bool(value.value))
        if value.value_type == "integer":
            return exp.Literal.number(str(value.value))
        if value.value_type == "decimal":
            return exp.Literal.number(format(Decimal(str(value.value)), "f"))
        if value.value_type in {"date", "timestamp"}:
            sql_type = "DATE" if value.value_type == "date" else "TIMESTAMP"
            return exp.Cast(
                this=exp.Literal.string(str(value.value)), to=exp.DataType.build(sql_type)
            )
        raise SemanticCompilationError(
            SemanticFailureCode.COMPILATION_FAILED, "unknown literal type"
        )

    @staticmethod
    def literal_value(value: str | int | Decimal) -> exp.Expression:
        """Shared literal lowering for legacy governed metric definitions."""
        if isinstance(value, str):
            return exp.Literal.string(value)
        if isinstance(value, Decimal):
            return exp.Literal.number(format(value, "f"))
        return exp.Literal.number(str(value))

    @staticmethod
    def aggregate_value(function: str, value: exp.Expression) -> exp.Expression:
        """Shared aggregate lowering for metric and general semantic compilers."""
        if function == "SUM":
            return exp.Sum(this=value)
        if function == "AVG":
            return exp.Avg(this=value)
        if function == "COUNT":
            return exp.Count(this=value)
        if function == "COUNT_DISTINCT":
            return exp.Count(this=exp.Distinct(expressions=[value]))
        if function == "MIN":
            return exp.Min(this=value)
        if function == "MAX":
            return exp.Max(this=value)
        raise SemanticCompilationError(
            SemanticFailureCode.INVALID_AGGREGATION, f"unsupported aggregate: {function}"
        )

    def _function(self, expression: FunctionExpression) -> exp.Expression:
        function = expression.function
        arguments = [self._compile(item) for item in expression.arguments]
        if function is SemanticFunction.COALESCE:
            return exp.Coalesce(expressions=arguments)
        if function is SemanticFunction.NULLIF:
            if len(arguments) != 2:
                raise SemanticCompilationError(
                    SemanticFailureCode.UNSUPPORTED_FUNCTION, "NULLIF requires two arguments"
                )
            return exp.Nullif(this=arguments[0], expression=arguments[1])
        if function is SemanticFunction.CAST:
            raise SemanticCompilationError(
                SemanticFailureCode.COMPILATION_FAILED, "use CastExpression for CAST"
            )
        if function is SemanticFunction.CURRENT_DATE:
            return exp.CurrentDate()
        if function in {
            SemanticFunction.CURRENT_TIMESTAMP,
            SemanticFunction.NOW,
            SemanticFunction.TRANSACTION_TIMESTAMP,
            SemanticFunction.STATEMENT_TIMESTAMP,
        }:
            return exp.CurrentTimestamp()
        if function is SemanticFunction.CURRENT_TIME:
            return exp.CurrentTime()
        if function is SemanticFunction.LOCALTIME:
            return exp.Localtime()
        if function is SemanticFunction.LOCALTIMESTAMP:
            return exp.Localtimestamp()
        if function is SemanticFunction.EXTRACT:
            if len(arguments) != 2 or not isinstance(arguments[0], exp.Literal):
                raise SemanticCompilationError(
                    SemanticFailureCode.UNSUPPORTED_FUNCTION,
                    "EXTRACT requires a textual field and one expression",
                )
            return exp.Extract(
                this=exp.Var(this=str(arguments[0].this).upper()), expression=arguments[1]
            )
        if function is SemanticFunction.ROUND:
            if not arguments:
                raise SemanticCompilationError(
                    SemanticFailureCode.UNSUPPORTED_FUNCTION,
                    "ROUND requires at least one argument",
                )
            value = arguments[0]
            if len(arguments) == 2:
                # PostgreSQL exposes ROUND(numeric, integer), not the
                # two-argument double-precision overload.  The cast is
                # compiler-owned and keeps semantic ROUND portable across
                # expressions whose source type is only known after lowering.
                value = exp.Cast(this=value, to=exp.DataType.build("DECIMAL"))
            return exp.Round(this=value, decimals=arguments[1] if len(arguments) == 2 else None)
        if function is SemanticFunction.JSON_EXTRACT:
            if len(arguments) < 2:
                raise SemanticCompilationError(
                    SemanticFailureCode.UNSUPPORTED_FUNCTION,
                    f"{function.value} requires a JSON value and path",
                )
            return exp.Anonymous(
                this="JSONB_EXTRACT_PATH",
                expressions=arguments,
            )
        if function in {
            SemanticFunction.JSON_EXTRACT_SCALAR,
            SemanticFunction.JSONB_EXTRACT_SCALAR,
            SemanticFunction.JSONB_EXTRACT_PATH_TEXT,
        }:
            if len(arguments) < 2:
                raise SemanticCompilationError(
                    SemanticFailureCode.UNSUPPORTED_FUNCTION,
                    "JSON_EXTRACT_SCALAR requires a JSON value and path",
                )
            return exp.Anonymous(
                this="JSONB_EXTRACT_PATH_TEXT",
                expressions=arguments,
            )
        return exp.Anonymous(this=function.value, expressions=arguments)

    def _function_name(self, function: str, arguments: tuple[Expression, ...]) -> exp.Expression:
        return self._function(
            FunctionExpression(function=SemanticFunction(function), arguments=arguments)
        )


class AggregationCompiler:
    """Named shared primitive for aggregate lowering."""

    @staticmethod
    def compile(function: str, value: exp.Expression, distinct: bool = False) -> exp.Expression:
        return ExpressionCompiler.aggregate_value(
            function,
            exp.Distinct(expressions=[value])
            if distinct or function == "COUNT_DISTINCT"
            else value,
        )


class CalculationCompiler:
    """Lower typed ratio/percentage calculations with an explicit zero policy."""

    @staticmethod
    def compile(contract: object, expression_compiler: ExpressionCompiler) -> exp.Expression:
        from app.semantics.semantic_query import CalculationContract

        if (
            not isinstance(contract, CalculationContract)
            or contract.numerator is None
            or contract.denominator is None
        ):
            raise SemanticCompilationError(
                SemanticFailureCode.INVALID_CALCULATION,
                "a calculation requires a typed numerator and denominator",
            )
        numerator = expression_compiler.compile(contract.numerator)
        denominator = expression_compiler.compile(contract.denominator)
        zero = exp.Literal.number(0)
        if contract.zero_denominator_policy.value == "NULL":
            denominator = exp.Nullif(this=denominator, expression=zero)
            result: exp.Expression = exp.Div(this=numerator, expression=denominator, typed=True)
        elif contract.zero_denominator_policy.value == "ZERO":
            result = exp.Case(
                ifs=[exp.If(this=exp.EQ(this=denominator.copy(), expression=zero), true=zero)],
                default=exp.Div(this=numerator, expression=denominator, typed=True),
            )
        else:
            result = exp.Div(this=numerator, expression=denominator, typed=True)
        if contract.scale is not None:
            result = exp.Mul(
                this=result,
                expression=ExpressionCompiler.literal_value(Decimal(str(contract.scale))),
            )
        return result


class SemanticQueryCompiler:
    """Compile nested semantic IR through server-owned mapping and SQLGlot only.

    The compiler owns aliases and relation lowering.  Nested relations export
    an explicit schema, so parent expressions never resolve physical columns
    by guessing from SQL aliases.
    """

    version = "semantic-query-compiler-2"

    def __init__(self, mapping: SemanticMappingSnapshot) -> None:
        self.mapping = mapping
        self.validator = SemanticPlanValidator(mapping)
        self.graph = SemanticRelationshipGraph(mapping)
        self.aliases = ScopedAliasAllocator()

    def compile(self, ir: SemanticQueryIR) -> CompiledSemanticQuery:
        self.validator.validate(ir)
        self.aliases = ScopedAliasAllocator()
        ordered_ctes = self._ordered_ctes(ir.ctes)
        query, _ = self._compile_query(ir, available_ctes=ordered_ctes)
        for cte in ordered_ctes:
            # SQLGlot expects a Select/Query AST, not a serialized SQL string.
            cte_query, _ = self._compile_query(
                cte.query, exports=cte.exported_attributes, available_ctes=ordered_ctes
            )
            query = query.with_(cte.cte_id, as_=cte_query)
        return CompiledSemanticQuery(
            ast=query, sql=query.sql(dialect="postgres"), compiler_version=self.version
        )

    def _compile_query(
        self,
        ir: SemanticQueryIR,
        *,
        exports: tuple[ExportedAttribute, ...] = (),
        available_ctes: tuple[CommonTableExpression, ...] = (),
        outer_bindings: dict[str, _RelationBinding] | None = None,
    ) -> tuple[exp.Select, dict[str, _RelationBinding]]:
        source, binding = self._compile_source(
            ir, exports, available_ctes, outer_bindings=outer_bindings
        )
        query = exp.Select().from_(source)
        bindings = {binding.source_id: binding}
        if binding.entity_id is not None:
            bindings[binding.entity_id] = binding
        elif isinstance(ir.from_source, CTERelationSource):
            # A logical-result CTE can retain attributes from one or more
            # semantic entities.  Expose those entity IDs as aliases of the
            # same physical CTE binding so server-owned relationship joins can
            # continue across the scope boundary.
            source_cte = self._find_cte(ir, ir.from_source.cte_id, available_ctes)
            for exported in source_cte.exported_attributes:
                if not exported.attribute_id.startswith("attribute:"):
                    continue
                try:
                    entity_id = self.mapping.attribute(exported.attribute_id).entity_id
                except SemanticMappingError:
                    continue
                bindings.setdefault(entity_id, binding)
        visible_bindings = dict(outer_bindings or {})
        visible_bindings.update(bindings)

        def compile_nested(nested_ir: SemanticQueryIR) -> exp.Select:
            nested, _ = self._compile_query(
                nested_ir,
                available_ctes=available_ctes,
                outer_bindings=visible_bindings,
            )
            return nested

        joined_entities = set(
            self._source_base_entities_for_compile(ir.from_source, ir, available_ctes)
        )
        if binding.entity_id is not None:
            joined_entities.add(binding.entity_id)
        joins: list[tuple[exp.Expression, exp.Expression, str, _RelationBinding]] = []
        for planned in _expanded_relationship_joins(ir.joins):
            if planned.relationship_id is not None:
                relationship = self.mapping.relationship(planned.relationship_id)
                endpoints = {relationship.from_entity_id, relationship.to_entity_id}
                connected = endpoints & joined_entities
                new_entities = endpoints - joined_entities
                if len(connected) != 1 or len(new_entities) != 1:
                    raise SemanticCompilationError(
                        SemanticFailureCode.AMBIGUOUS_RELATIONSHIP_PATH,
                        f"join is disconnected or reuses an entity: {planned.relationship_id}",
                    )
                source_entity = next(iter(connected))
                target_entity = next(iter(new_entities))
                alias = self.aliases.allocate()
                target = self.mapping.entity(target_entity)
                target_binding = _RelationBinding(
                    source_id=target_entity,
                    alias=alias,
                    entity_id=target_entity,
                    attributes={
                        item.attribute_id: item.physical_column
                        for item in self.mapping.attributes
                        if item.entity_id == target_entity
                    },
                )
                bindings[target_entity] = target_binding
                left_attribute = relationship.from_attribute_id
                right_attribute = relationship.to_attribute_id
                if source_entity == relationship.to_entity_id:
                    left_attribute, right_attribute = right_attribute, left_attribute
                left = self._attribute_column(left_attribute, bindings)
                right = self._attribute_column(right_attribute, bindings)
                joins.append(
                    (
                        self._table(target.physical_table).as_(alias),
                        exp.EQ(this=left, expression=right),
                        planned.join_type.value,
                        target_binding,
                    )
                )
                joined_entities.add(target_entity)
            else:
                target_source = planned.target_source
                if target_source is None:
                    raise SemanticCompilationError(
                        SemanticFailureCode.UNKNOWN_RELATION_SOURCE,
                        "join has no target source",
                    )
                target_ast, target_binding = self._compile_source_for_join(
                    target_source,
                    ir,
                    available_ctes,
                    outer_bindings=visible_bindings,
                )
                bindings[target_binding.source_id] = target_binding
                if target_binding.entity_id is not None:
                    bindings[target_binding.entity_id] = target_binding
                joined_entities.update(self._source_base_entities_for_compile(target_source, ir))
                join_bindings = dict(outer_bindings or {})
                join_bindings.update(bindings)
                key_compiler = ExpressionCompiler(
                    self.mapping,
                    {},
                    join_bindings,
                    binding.source_id,
                    compile_nested,
                )
                conditions = [
                    exp.EQ(
                        this=key_compiler.compile(key.left),
                        expression=key_compiler.compile(key.right),
                    )
                    for key in planned.join_keys
                ]
                condition: exp.Expression = conditions[0]
                for extra in conditions[1:]:
                    condition = exp.And(this=condition, expression=extra)
                joins.append((target_ast, condition, planned.join_type.value, target_binding))
        visible_bindings.update(bindings)
        compiler = ExpressionCompiler(
            self.mapping,
            {},
            visible_bindings,
            binding.source_id,
            compile_nested,
        )
        projections = [
            compiler.compile(item.expression).as_(item.alias)
            if item.alias
            else compiler.compile(item.expression)
            for item in ir.select
        ]
        if exports:
            by_position = {item.position: item.output_name for item in exports}
            projections = [
                item.as_(by_position[index]) if index in by_position else item
                for index, item in enumerate(projections)
            ]
        query = query.select(*projections)
        for table, condition, join_type, _ in joins:
            query = query.join(table, on=condition, join_type=join_type)
        if ir.where is not None:
            query = query.where(compiler.compile(ir.where))
        if ir.group_by:
            query = query.group_by(*[compiler.compile(item) for item in ir.group_by])
        if ir.having is not None:
            query = query.having(compiler.compile(ir.having))
        if ir.order_by:
            query = query.order_by(
                *[
                    exp.Ordered(
                        this=compiler.compile(item.expression), desc=item.direction.value == "DESC"
                    )
                    for item in ir.order_by
                ]
            )
        if ir.distinct:
            query = query.distinct()
        if ir.limit is not None:
            query = query.limit(ir.limit)
        if ir.offset is not None:
            query = query.offset(ir.offset)
        return query, bindings

    def _compile_source(
        self,
        ir: SemanticQueryIR,
        exports: tuple[ExportedAttribute, ...],
        available_ctes: tuple[CommonTableExpression, ...] = (),
        *,
        outer_bindings: dict[str, _RelationBinding] | None = None,
    ) -> tuple[exp.Expression, _RelationBinding]:
        source = ir.from_source
        if source is None:
            if ir.from_entity_id is None:
                raise SemanticCompilationError(
                    SemanticFailureCode.UNKNOWN_RELATION_SOURCE, "query has no source"
                )
            source = EntityRelationSource(entity_id=ir.from_entity_id)
        if isinstance(source, EntityRelationSource):
            entity = self.mapping.entity(source.entity_id)
            alias = self.aliases.allocate()
            return self._table(entity.physical_table).as_(alias), _RelationBinding(
                source_id=source.source_id,
                alias=alias,
                entity_id=source.entity_id,
                attributes={
                    item.attribute_id: item.physical_column
                    for item in self.mapping.attributes
                    if item.entity_id == source.entity_id
                },
            )
        if isinstance(source, CTERelationSource):
            cte = self._find_cte(ir, source.cte_id, available_ctes)
            alias = self.aliases.allocate()
            return self._table(cte.cte_id).as_(alias), _RelationBinding(
                source_id=source.source_id or source.cte_id,
                alias=alias,
                attributes={
                    item.attribute_id: item.output_name for item in cte.exported_attributes
                },
            )
        if isinstance(source, DerivedRelationSource):
            derived = self._find_derived(ir, source.relation_id)
            nested, _ = self._compile_query(
                derived.query,
                exports=derived.exported_attributes,
                available_ctes=available_ctes,
                outer_bindings=outer_bindings,
            )
            alias = self.aliases.allocate()
            return exp.Subquery(this=nested).as_(alias), _RelationBinding(
                source_id=source.relation_id,
                alias=alias,
                attributes={
                    item.attribute_id: item.output_name for item in derived.exported_attributes
                },
            )
        raise SemanticCompilationError(
            SemanticFailureCode.UNKNOWN_RELATION_SOURCE, type(source).__name__
        )

    def _compile_source_for_join(
        self,
        source: RelationSource,
        ir: SemanticQueryIR,
        available_ctes: tuple[CommonTableExpression, ...] = (),
        *,
        outer_bindings: dict[str, _RelationBinding] | None = None,
    ) -> tuple[exp.Expression, _RelationBinding]:
        nested_ir = ir.model_copy(update={"from_source": source, "from_entity_id": None})
        return self._compile_source(
            nested_ir,
            (),
            available_ctes,
            outer_bindings=outer_bindings,
        )

    @staticmethod
    def _find_cte(
        ir: SemanticQueryIR,
        cte_id: str,
        available_ctes: tuple[CommonTableExpression, ...] = (),
    ) -> CommonTableExpression:
        for cte in (*ir.ctes, *available_ctes):
            if cte.cte_id == cte_id:
                return cte
        raise SemanticCompilationError(SemanticFailureCode.INVALID_CTE_REFERENCE, cte_id)

    @staticmethod
    def _source_base_entities_for_compile(
        source: RelationSource | None,
        ir: SemanticQueryIR,
        available_ctes: tuple[CommonTableExpression, ...] = (),
    ) -> tuple[str, ...]:
        if isinstance(source, EntityRelationSource):
            return (source.entity_id,)
        if isinstance(source, CTERelationSource):
            for cte in (*ir.ctes, *available_ctes):
                if cte.cte_id == source.cte_id:
                    return cte.query.population_contract.base_entity_ids
        if isinstance(source, DerivedRelationSource):
            for relation in ir.derived_relations:
                if relation.relation_id == source.relation_id:
                    return relation.query.population_contract.base_entity_ids
        return ()

    @staticmethod
    def _find_derived(ir: SemanticQueryIR, relation_id: str) -> DerivedRelation:
        for relation in ir.derived_relations:
            if relation.relation_id == relation_id:
                return relation
        raise SemanticCompilationError(SemanticFailureCode.UNKNOWN_RELATION_SOURCE, relation_id)

    @staticmethod
    def _ordered_ctes(
        ctes: tuple[CommonTableExpression, ...],
    ) -> tuple[CommonTableExpression, ...]:
        ids = [cte.cte_id for cte in ctes]
        if len(ids) != len(set(ids)):
            raise SemanticCompilationError(
                SemanticFailureCode.DUPLICATE_CTE_ID, "duplicate CTE identifier"
            )
        # CTE references are typed sources; topological dependencies are
        # discovered from those sources, never from SQL text.
        by_id = {cte.cte_id: cte for cte in ctes}
        ordered: list[CommonTableExpression] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(cte: CommonTableExpression) -> None:
            if cte.cte_id in visiting:
                raise SemanticCompilationError(SemanticFailureCode.CTE_DEPENDENCY_CYCLE, cte.cte_id)
            if cte.cte_id in visited:
                return
            visiting.add(cte.cte_id)
            for source in _query_sources(cte.query):
                if isinstance(source, CTERelationSource) and source.cte_id in by_id:
                    visit(by_id[source.cte_id])
            visiting.remove(cte.cte_id)
            visited.add(cte.cte_id)
            ordered.append(cte)

        for cte in ctes:
            visit(cte)
        return tuple(ordered)

    def _attribute_column(
        self, attribute_id: str, bindings: dict[str, _RelationBinding]
    ) -> exp.Column:
        attribute = self.mapping.attribute(attribute_id)
        binding = bindings.get(attribute.entity_id)
        if binding is None:
            raise SemanticCompilationError(
                SemanticFailureCode.UNKNOWN_SEMANTIC_ENTITY, attribute.entity_id
            )
        return exp.column(attribute.physical_column, table=binding.alias)

    @staticmethod
    def _table(name: str) -> exp.Table:
        return exp.Table(this=exp.Identifier(this=name, quoted=False))


def _query_sources(ir: SemanticQueryIR) -> tuple[RelationSource, ...]:
    result: list[RelationSource] = []
    if ir.from_source is not None:
        result.append(ir.from_source)
    elif ir.from_entity_id is not None:
        result.append(EntityRelationSource(entity_id=ir.from_entity_id))
    for join in ir.joins:
        if join.target_source is not None:
            result.append(join.target_source)
    return tuple(result)


def _expanded_relationship_joins(joins: tuple[PlannedJoin, ...]) -> tuple[PlannedJoin, ...]:
    expanded: list[PlannedJoin] = []
    for join in joins:
        if getattr(join, "relationship_path", ()):
            expanded.extend(
                join.model_copy(
                    update={"relationship_id": relationship_id, "relationship_path": ()}
                )
                for relationship_id in join.relationship_path
            )
        else:
            expanded.append(join)
    return tuple(expanded)


def _local_scope_attribute_ids(
    ir: SemanticQueryIR, visible_ctes: tuple[CommonTableExpression, ...] = ()
) -> dict[str, set[str]]:
    """Return only relation outputs directly visible in the current scope."""
    result: dict[str, set[str]] = {
        relation.relation_id: {item.attribute_id for item in relation.exported_attributes}
        for relation in ir.derived_relations
    }
    result.update(
        {
            cte.cte_id: {item.attribute_id for item in cte.exported_attributes}
            for cte in visible_ctes
        }
    )
    result.update(
        {cte.cte_id: {item.attribute_id for item in cte.exported_attributes} for cte in ir.ctes}
    )
    for source in _query_sources(ir):
        if isinstance(source, CTERelationSource):
            cte = next(
                (item for item in (*visible_ctes, *ir.ctes) if item.cte_id == source.cte_id),
                None,
            )
            if cte is not None and source.source_id:
                result[source.source_id] = {item.attribute_id for item in cte.exported_attributes}
    return result
