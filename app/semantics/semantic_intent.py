"""Small model-facing semantic intent and its deterministic server resolver.

This module is deliberately narrower than ``SemanticQueryPlan``.  It carries
semantic choices and logical result composition, while physical sources,
relationship IDs, join keys, output positions, aliases, and population
bookkeeping are produced by :class:`SemanticIntentResolver`.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.semantics.semantic_errors import SemanticEngineError, SemanticFailureCode
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    BetweenExpression,
    BinaryExpression,
    BinaryOperator,
    CaseBranch,
    CaseExpression,
    CastExpression,
    EntityRelationSource,
    Expression,
    FunctionExpression,
    InExpression,
    IsNullExpression,
    JoinType,
    LiteralExpression,
    LogicalExpression,
    NotExpression,
    OrderedAggregateExpression,
    OrderSpec,
    PlannedJoin,
    PlannedOutput,
    PopulationContract,
    PopulationInclusion,
    ScalarSubqueryExpression,
    SemanticFunction,
    SemanticQueryPlan,
    SortDirection,
    StarExpression,
    WindowExpression,
    WindowOrder,
)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IntentPopulationMode(StrEnum):
    BASE_ENTITY = "BASE_ENTITY"
    MATCHING_RELATIONS = "MATCHING_RELATIONS"
    PRESERVE_BASE = "PRESERVE_BASE"


class IntentSource(_Strict):
    kind: Literal["entity", "result"]
    entity_id: str | None = Field(default=None, min_length=1, max_length=128)
    result_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_source(self) -> IntentSource:
        if self.kind == "entity" and (self.entity_id is None or self.result_id is not None):
            return self
        if self.kind == "result" and (self.result_id is None or self.entity_id is not None):
            return self
        raise ValueError("intent source must contain exactly its selected source ID")


class IntentAttributeRef(_Strict):
    kind: Literal["attribute"] = "attribute"
    attribute_id: str = Field(min_length=1, max_length=256)
    scope_id: str | None = Field(default=None, min_length=1, max_length=128)


class IntentLiteral(_Strict):
    kind: Literal["literal"] = "literal"
    value: str | int | float | bool | None
    value_type: Literal["null", "string", "integer", "decimal", "boolean", "date", "timestamp"]


class IntentStar(_Strict):
    kind: Literal["star"] = "star"


class IntentAggregate(_Strict):
    kind: Literal["aggregate"] = "aggregate"
    function: Literal[
        "COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX", "CORR",
        "REGR_SLOPE", "STDDEV", "PERCENTILE_CONT", "ARRAY_AGG", "STRING_AGG",
        "JSON_AGG", "JSONB_AGG", "JSON_OBJECT_AGG", "JSONB_OBJECT_AGG",
    ]
    expression: IntentExpression
    distinct: bool = False
    filter: IntentExpression | None = None


class IntentOrderedAggregate(_Strict):
    kind: Literal["ordered_aggregate"] = "ordered_aggregate"
    function: Literal["PERCENTILE_CONT"]
    percentile: IntentExpression
    expression: IntentExpression


class IntentBinary(_Strict):
    kind: Literal["binary"] = "binary"
    operator: BinaryOperator
    left: IntentExpression
    right: IntentExpression


class IntentLogical(_Strict):
    kind: Literal["logical"] = "logical"
    operator: Literal["AND", "OR"]
    terms: tuple[IntentExpression, ...] = Field(min_length=2, max_length=16)


class IntentNot(_Strict):
    kind: Literal["not"] = "not"
    expression: IntentExpression


class IntentBetween(_Strict):
    kind: Literal["between"] = "between"
    expression: IntentExpression
    low: IntentExpression
    high: IntentExpression


class IntentIn(_Strict):
    kind: Literal["in"] = "in"
    expression: IntentExpression
    values: tuple[IntentExpression, ...] = Field(min_length=1, max_length=32)
    negated: bool = False


class IntentIsNull(_Strict):
    kind: Literal["is_null"] = "is_null"
    expression: IntentExpression
    negated: bool = False


class IntentCast(_Strict):
    kind: Literal["cast"] = "cast"
    expression: IntentExpression
    target_type: Literal[
        "INTEGER", "BIGINT", "NUMERIC", "DECIMAL", "REAL", "DOUBLE PRECISION",
        "TEXT", "DATE", "TIMESTAMP", "BOOLEAN",
    ]


class IntentCaseBranch(_Strict):
    when: IntentExpression
    then: IntentExpression


class IntentCase(_Strict):
    kind: Literal["case"] = "case"
    branches: tuple[IntentCaseBranch, ...] = Field(min_length=1, max_length=8)
    default: IntentExpression | None = None


class IntentFunction(_Strict):
    kind: Literal["function"] = "function"
    function: SemanticFunction
    arguments: tuple[IntentExpression, ...] = Field(max_length=8)


class IntentWindowOrder(_Strict):
    expression: IntentExpression
    direction: SortDirection = SortDirection.ASC


class IntentWindow(_Strict):
    kind: Literal["window"] = "window"
    function: Literal[
        "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "NTH_VALUE", "NTILE",
        "PERCENT_RANK", "FIRST_VALUE", "LAST_VALUE", "COUNT", "SUM", "AVG", "MIN", "MAX",
    ]
    arguments: tuple[IntentExpression, ...] = Field(default=(), max_length=4)
    partition_by: tuple[IntentExpression, ...] = Field(default=(), max_length=8)
    order_by: tuple[IntentWindowOrder, ...] = Field(default=(), max_length=8)


class IntentScalarSubquery(_Strict):
    kind: Literal["scalar_subquery"] = "scalar_subquery"
    query: IntentQuery


IntentExpression = Annotated[
    IntentAttributeRef
    | IntentLiteral
    | IntentStar
    | IntentAggregate
    | IntentOrderedAggregate
    | IntentBinary
    | IntentLogical
    | IntentNot
    | IntentBetween
    | IntentIn
    | IntentIsNull
    | IntentCast
    | IntentCase
    | IntentFunction
    | IntentWindow
    | IntentScalarSubquery,
    Field(discriminator="kind"),
]


class IntentOutput(_Strict):
    expression: IntentExpression
    label: str | None = Field(default=None, min_length=1, max_length=63)


class IntentJoin(_Strict):
    target: IntentSource
    join_type: JoinType = JoinType.INNER
    left: IntentExpression | None = None
    right: IntentExpression | None = None

    @model_validator(mode="after")
    def validate_join(self) -> IntentJoin:
        if self.target.kind == "result" and (self.left is None or self.right is None):
            raise ValueError("result joins require semantic exported expressions")
        if self.target.kind == "entity" and (self.left is not None or self.right is not None):
            raise ValueError("entity joins use server-owned relationship resolution")
        return self


class IntentOrder(_Strict):
    expression: IntentExpression
    direction: SortDirection = SortDirection.ASC


class IntentCalculationNone(_Strict):
    kind: Literal["NONE"] = "NONE"


class IntentCalculationRatio(_Strict):
    kind: Literal["RATIO", "PERCENTAGE"]
    numerator: IntentExpression
    denominator: IntentExpression
    scale: Decimal | int | float | None = None
    zero_denominator_policy: Literal["NULL", "ZERO", "REJECT"] = "NULL"


class IntentCalculationDifference(_Strict):
    kind: Literal["DIFFERENCE"]
    left: IntentExpression
    right: IntentExpression


IntentCalculation = Annotated[
    IntentCalculationNone | IntentCalculationRatio | IntentCalculationDifference,
    Field(discriminator="kind"),
]


class IntentQuery(_Strict):
    result_id: str = Field(min_length=1, max_length=128)
    source: IntentSource
    outputs: tuple[IntentOutput, ...] = Field(min_length=1, max_length=32)
    joins: tuple[IntentJoin, ...] = Field(default=(), max_length=16)
    filters: tuple[IntentExpression, ...] = Field(default=(), max_length=16)
    group_by: tuple[IntentExpression, ...] = Field(default=(), max_length=16)
    having: IntentExpression | None = None
    order_by: tuple[IntentOrder, ...] = Field(default=(), max_length=16)
    distinct: bool = False
    limit: int | None = Field(default=None, ge=1, le=10000)
    offset: int | None = Field(default=None, ge=0, le=1000000)
    calculation: IntentCalculation | None = None
    children: tuple[IntentQuery, ...] = Field(default=(), max_length=16)


class SmallCompositionalPlanV1(_Strict):
    """The only model-owned contract in M31.

    ``database_id`` is bound by the request context.  All remaining fields
    describe semantic intent or logical result composition; none is physical
    SQL authority.
    """

    anchor_entity_id: str = Field(min_length=1, max_length=128)
    population_mode: IntentPopulationMode = IntentPopulationMode.BASE_ENTITY
    query: IntentQuery


SemanticIntentV1 = SmallCompositionalPlanV1


def _to_expression(value: IntentExpression, mapping: SemanticMappingSnapshot) -> Expression:
    if isinstance(value, IntentAttributeRef):
        if value.scope_id is None:
            mapping.attribute(value.attribute_id)
        return AttributeRef(attribute_id=value.attribute_id, relation_ref=value.scope_id)
    if isinstance(value, IntentLiteral):
        return LiteralExpression(value=value.value, value_type=value.value_type)
    if isinstance(value, IntentStar):
        return StarExpression()
    if isinstance(value, IntentAggregate):
        return AggregateExpression(
            function=value.function,
            expression=_to_expression(value.expression, mapping),
            distinct=value.distinct,
            filter=_to_expression(value.filter, mapping) if value.filter is not None else None,
        )
    if isinstance(value, IntentOrderedAggregate):
        return OrderedAggregateExpression(
            function=value.function,
            percentile=_to_expression(value.percentile, mapping),
            expression=_to_expression(value.expression, mapping),
        )
    if isinstance(value, IntentBinary):
        return BinaryExpression(
            operator=value.operator,
            left=_to_expression(value.left, mapping),
            right=_to_expression(value.right, mapping),
        )
    if isinstance(value, IntentLogical):
        return LogicalExpression(
            operator=value.operator,
            terms=tuple(_to_expression(item, mapping) for item in value.terms),
        )
    if isinstance(value, IntentNot):
        return NotExpression(expression=_to_expression(value.expression, mapping))
    if isinstance(value, IntentBetween):
        return BetweenExpression(
            expression=_to_expression(value.expression, mapping),
            low=_to_expression(value.low, mapping),
            high=_to_expression(value.high, mapping),
        )
    if isinstance(value, IntentIn):
        return InExpression(
            expression=_to_expression(value.expression, mapping),
            values=tuple(_to_expression(item, mapping) for item in value.values),
            negated=value.negated,
        )
    if isinstance(value, IntentIsNull):
        return IsNullExpression(
            expression=_to_expression(value.expression, mapping), negated=value.negated
        )
    if isinstance(value, IntentCast):
        return CastExpression(
            expression=_to_expression(value.expression, mapping), target_type=value.target_type
        )
    if isinstance(value, IntentCase):
        return CaseExpression(
            branches=tuple(
                CaseBranch(
                    when=_to_expression(item.when, mapping),
                    then=_to_expression(item.then, mapping),
                )
                for item in value.branches
            ),
            default=_to_expression(value.default, mapping) if value.default is not None else None,
        )
    if isinstance(value, IntentFunction):
        return FunctionExpression(
            function=value.function,
            arguments=tuple(_to_expression(item, mapping) for item in value.arguments),
        )
    if isinstance(value, IntentWindow):
        return WindowExpression(
            function=value.function,
            arguments=tuple(_to_expression(item, mapping) for item in value.arguments),
            partition_by=tuple(_to_expression(item, mapping) for item in value.partition_by),
            order_by=tuple(
                WindowOrder(
                    expression=_to_expression(item.expression, mapping),
                    direction=item.direction,
                )
                for item in value.order_by
            ),
        )
    if isinstance(value, IntentScalarSubquery):
        raise SemanticEngineError(
            SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
            "scalar subqueries require query-scope resolution",
        )
    raise TypeError(f"unsupported intent expression: {type(value).__name__}")


class SemanticIntentResolver:
    """Resolve logical intent into the existing canonical plan deterministically."""

    def __init__(self, mapping: SemanticMappingSnapshot, *, database_id: str) -> None:
        self.mapping = mapping
        self.database_id = database_id

    def resolve(self, intent: SmallCompositionalPlanV1) -> SemanticQueryPlan:
        self.mapping.entity(intent.anchor_entity_id)
        child_plans = {child.result_id: self._resolve_query(child) for child in intent.query.children}
        plan = self._resolve_query(intent.query, child_plans=child_plans)
        population = plan.population_contract.model_copy(
            update={
                "base_entity_ids": (intent.anchor_entity_id,),
                "inclusion_mode": PopulationInclusion(intent.population_mode.value),
                "fanout_allowed": bool(plan.joins),
            }
        )
        return plan.model_copy(update={"database_id": self.database_id, "population_contract": population})

    def _resolve_query(
        self,
        query: IntentQuery,
        *,
        child_plans: dict[str, SemanticQueryPlan] | None = None,
    ) -> SemanticQueryPlan:
        child_plans = child_plans or {
            child.result_id: self._resolve_query(child) for child in query.children
        }
        if query.source.kind == "entity":
            entity_id = query.source.entity_id
            if entity_id is None:
                raise SemanticEngineError(SemanticFailureCode.UNKNOWN_ENTITY, "missing intent entity")
            self.mapping.entity(entity_id)
            source = EntityRelationSource(entity_id=entity_id, source_id="base")
        else:
            result_id = query.source.result_id
            if result_id not in child_plans:
                raise SemanticEngineError(
                    SemanticFailureCode.UNKNOWN_RELATION_SOURCE,
                    f"unknown logical result: {result_id}",
                )
            # Logical result sources are lowered to a deterministic CTE by the
            # canonical compiler layer.  The intent never chooses SQL aliases.
            raise SemanticEngineError(
                SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                "logical result sources require nested resolver lowering",
            )
        outputs = tuple(
            PlannedOutput(
                position=index,
                semantic_role=item.label or f"output_{index}",
                expression=_to_expression(item.expression, self.mapping),
                alias=item.label,
            )
            for index, item in enumerate(query.outputs)
        )
        joins: list[PlannedJoin] = []
        for join in query.joins:
            if join.target.kind != "entity" or join.target.entity_id is None:
                raise SemanticEngineError(
                    SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                    "nested logical result joins are resolved in the compositional lowering pass",
                )
            target = join.target.entity_id
            self.mapping.entity(target)
            relationship_ids = [
                relation.relationship_id
                for relation in self.mapping.relationships
                if {relation.from_entity_id, relation.to_entity_id}
                == {query.source.entity_id or "", target}
            ]
            if len(relationship_ids) != 1:
                raise SemanticEngineError(
                    SemanticFailureCode.UNKNOWN_RELATIONSHIP,
                    f"intent relationship is not uniquely server-mapped: {target}",
                )
            joins.append(PlannedJoin(relationship_id=relationship_ids[0], join_type=join.join_type))
        calculation = None
        if query.calculation is not None and query.calculation.kind != "NONE":
            if query.calculation.kind in {"RATIO", "PERCENTAGE"}:
                calculation = query.calculation
                # Conversion is intentionally explicit in the model-facing
                # tagged union; canonical calculation normalization is added
                # by the caller when a ratio is requested.
        population = PopulationContract(
            base_entity_ids=(query.source.entity_id or "",),
            inclusion_mode=PopulationInclusion.BASE_ENTITY,
            fanout_allowed=bool(joins),
        )
        return SemanticQueryPlan(
            database_id=self.database_id,
            from_entity_id=query.source.entity_id,
            population_contract=population,
            outputs=outputs,
            joins=tuple(joins),
            where=_combine_filters(query.filters, self.mapping),
            group_by=tuple(_to_expression(item, self.mapping) for item in query.group_by),
            having=_to_expression(query.having, self.mapping) if query.having is not None else None,
            order_by=tuple(
                OrderSpec(
                    expression=_to_expression(item.expression, self.mapping),
                    direction=item.direction,
                )
                for item in query.order_by
            ),
            distinct=query.distinct,
            limit=query.limit,
            offset=query.offset,
        )


def _combine_filters(
    filters: tuple[IntentExpression, ...], mapping: SemanticMappingSnapshot
) -> Expression | None:
    expressions = tuple(_to_expression(item, mapping) for item in filters)
    if not expressions:
        return None
    if len(expressions) == 1:
        return expressions[0]
    return LogicalExpression(operator="AND", terms=expressions)


for _model in (
    IntentSource,
    IntentAttributeRef,
    IntentLiteral,
    IntentStar,
    IntentAggregate,
    IntentOrderedAggregate,
    IntentBinary,
    IntentLogical,
    IntentNot,
    IntentBetween,
    IntentIn,
    IntentIsNull,
    IntentCast,
    IntentCaseBranch,
    IntentCase,
    IntentFunction,
    IntentWindowOrder,
    IntentWindow,
    IntentScalarSubquery,
    IntentOutput,
    IntentJoin,
    IntentOrder,
    IntentCalculationNone,
    IntentCalculationRatio,
    IntentCalculationDifference,
    IntentQuery,
    SmallCompositionalPlanV1,
):
    _model.model_rebuild()

