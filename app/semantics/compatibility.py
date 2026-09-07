"""Compatibility adapters from the former semantic experiments.

Adapters are deliberately one-way: legacy DTOs enter the canonical plan and
then use the canonical IR/compiler.  The legacy DTOs do not retain execution
authority or a second compilation path.
"""

from decimal import Decimal

from app.generation.hard_query_plans import RatioPlan, TopKPlan
from app.generation.result_shape import ResultOutputKind, ResultShapeProposal
from app.generation.window_ir import (
    LagSpec,
    LeadSpec,
    RankingSpec,
    WindowQueryIR,
)
from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Catalog,
    QueryPlanV1Operator,
    QueryPlanV1Value,
)
from app.semantics.query_plan_wire_v2 import QueryPlanWireV2, wire_to_query_plan_v1
from app.semantics.semantic_errors import SemanticFailureCode, SemanticValidationError
from app.semantics.semantic_mapping import SemanticAttributeMapping, SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    BinaryExpression,
    BinaryOperator,
    CalculationContract,
    CalculationKind,
    ExpectedCardinality,
    Expression,
    FunctionExpression,
    JoinType,
    LiteralExpression,
    OrderSpec,
    PlannedJoin,
    PlannedOutput,
    PopulationContract,
    SemanticFunction,
    SemanticQueryPlan,
    SortDirection,
)


def query_plan_v1_to_semantic_plan(
    plan: QueryPlanV1, catalog: QueryPlanV1Catalog, *, database_id: str = "schema"
) -> SemanticQueryPlan:
    """Lower the bounded V1 physical-ID contract into canonical semantic IDs."""
    if not plan.applicable or plan.source is None:
        raise SemanticValidationError(
            SemanticFailureCode.INVALID_POPULATION_CONTRACT,
            "only applicable QueryPlan V1 plans can enter the semantic engine",
        )
    mapping = _mapping_from_v1_catalog(catalog)
    source = mapping.entity_for_physical(plan.source).entity_id
    outputs = tuple(
        PlannedOutput(
            position=index,
            semantic_role=column_id,
            attribute_id=mapping.attribute_for_physical(*column_id.split(".", 1)).attribute_id,
        )
        for index, column_id in enumerate(plan.projection)
    )
    joins = tuple(
        PlannedJoin(
            relationship_id=mapping.relationship_for_legacy(item.relationship_id).relationship_id,
            join_type=JoinType(item.join_type.value),
        )
        for item in plan.joins
    )
    expressions = tuple(
        _v1_predicate(item.column_id, item.operator, item.value, mapping) for item in plan.filters
    )
    where = _and(expressions) if expressions else None
    return SemanticQueryPlan(
        database_id=database_id,
        from_entity_id=source,
        population_contract=PopulationContract(
            base_entity_ids=(source,), expected_cardinality=ExpectedCardinality.MANY_ROWS
        ),
        outputs=outputs,
        joins=joins,
        where=where,
    )


def query_plan_wire_v2_to_semantic_plan(
    wire: QueryPlanWireV2, catalog: QueryPlanV1Catalog, *, database_id: str = "schema"
) -> SemanticQueryPlan:
    """Lower the versioned wire DTO through V1 into the canonical contract."""
    return query_plan_v1_to_semantic_plan(
        wire_to_query_plan_v1(wire), catalog, database_id=database_id
    )


def top_k_to_semantic_plan(
    plan: TopKPlan, mapping: SemanticMappingSnapshot, *, database_id: str = "schema"
) -> SemanticQueryPlan:
    group_attrs = [_attribute_from_reference(item, mapping) for item in plan.group_by]
    if not group_attrs:
        raise SemanticValidationError(SemanticFailureCode.INVALID_GRAIN, "TopK requires a group")
    source = mapping.entity(group_attrs[0].entity_id).entity_id
    measure_attr = _attribute_from_components(plan.measure.components, group_attrs[0], mapping)
    aggregate = _aggregate(plan.measure.aggregation, measure_attr)
    outputs = tuple(
        [
            PlannedOutput(
                position=index, semantic_role=reference, attribute_id=attribute.attribute_id
            )
            for index, (reference, attribute) in enumerate(
                zip(plan.entity_outputs, group_attrs, strict=True)
            )
        ]
        + [PlannedOutput(position=len(group_attrs), semantic_role="measure", expression=aggregate)]
    )
    return SemanticQueryPlan(
        database_id=database_id,
        from_entity_id=source,
        population_contract=PopulationContract(
            base_entity_ids=(source,),
            aggregation_grain_attribute_ids=tuple(item.attribute_id for item in group_attrs),
        ),
        outputs=outputs,
        group_by=tuple(AttributeRef(attribute_id=item.attribute_id) for item in group_attrs),
        order_by=(OrderSpec(expression=aggregate, direction=SortDirection(plan.order_direction)),),
        limit=plan.limit,
    )


def ratio_to_semantic_plan(
    plan: RatioPlan, mapping: SemanticMappingSnapshot, *, database_id: str = "schema"
) -> SemanticQueryPlan:
    numerator = _ratio_component(plan.numerator.source_columns, plan.numerator.aggregation, mapping)
    denominator = _ratio_component(
        plan.denominator.source_columns, plan.denominator.aggregation, mapping
    )
    source = mapping.attribute(_first_attribute(numerator, denominator).attribute_id).entity_id
    scale = Decimal(str(plan.scale)) if plan.scale is not None else Decimal("1")
    ratio = BinaryExpression(
        operator=BinaryOperator.MULTIPLY,
        left=BinaryExpression(operator=BinaryOperator.DIVIDE, left=numerator, right=denominator),
        right=LiteralExpression(value=scale, value_type="decimal"),
    )
    return SemanticQueryPlan(
        database_id=database_id,
        from_entity_id=source,
        population_contract=PopulationContract(base_entity_ids=(source,)),
        outputs=(PlannedOutput(position=0, semantic_role="ratio", expression=ratio),),
        calculation_contract=CalculationContract(
            kind=CalculationKind.PERCENTAGE if plan.scale is not None else CalculationKind.RATIO,
            numerator=numerator,
            denominator=denominator,
            scale=scale,
        ),
    )


def window_ir_to_semantic_plan(
    ir: WindowQueryIR, mapping: SemanticMappingSnapshot, *, database_id: str = "schema"
) -> SemanticQueryPlan:
    source = mapping.entity_for_physical(ir.source_relation).entity_id
    outputs = [
        PlannedOutput(
            position=index,
            semantic_role=reference,
            attribute_id=_attribute_from_reference(reference, mapping).attribute_id,
        )
        for index, reference in enumerate(ir.physical_outputs)
    ]
    for computation in ir.computations:
        if isinstance(computation, (RankingSpec, LagSpec, LeadSpec)):
            function = SemanticFunction(
                computation.function.value
                if isinstance(computation, RankingSpec)
                else type(computation).__name__.replace("Spec", "").upper()
            )
            target = getattr(computation, "target", None)
            args = (
                ()
                if target is None
                else (
                    AttributeRef(
                        attribute_id=_attribute_from_reference(target, mapping).attribute_id
                    ),
                )
            )
            outputs.append(
                PlannedOutput(
                    position=len(outputs),
                    semantic_role=computation.alias,
                    expression=FunctionExpression(function=function, arguments=args),
                )
            )
        else:
            raise SemanticValidationError(
                SemanticFailureCode.COMPILATION_FAILED,
                "window adapter supports ranking/lag/lead only",
            )
    return SemanticQueryPlan(
        database_id=database_id,
        from_entity_id=source,
        population_contract=PopulationContract(base_entity_ids=(source,)),
        outputs=tuple(outputs),
    )


def result_shape_to_plan(
    proposal: ResultShapeProposal, mapping: SemanticMappingSnapshot, *, database_id: str = "schema"
) -> SemanticQueryPlan:
    if any(item.kind is ResultOutputKind.DERIVED_VALUE for item in proposal.outputs):
        raise SemanticValidationError(
            SemanticFailureCode.COMPILATION_FAILED,
            "derived ResultShape output needs an explicit expression",
        )
    attrs = [
        _attribute_from_reference(item.source_hint or "", mapping) for item in proposal.outputs
    ]
    source = attrs[0].entity_id
    return SemanticQueryPlan(
        database_id=database_id,
        from_entity_id=source,
        population_contract=PopulationContract(base_entity_ids=(source,)),
        outputs=tuple(
            PlannedOutput(
                position=index, semantic_role=item.semantic_label, attribute_id=attr.attribute_id
            )
            for index, (item, attr) in enumerate(zip(proposal.outputs, attrs, strict=True))
        ),
        limit=proposal.explicit_limit,
        order_by=(
            OrderSpec(
                expression=AttributeRef(attribute_id=attrs[0].attribute_id),
                direction=SortDirection(proposal.explicit_order_direction),
            ),
        )
        if proposal.explicit_order_direction
        else (),
    )


def _mapping_from_v1_catalog(catalog: QueryPlanV1Catalog) -> SemanticMappingSnapshot:
    from app.catalog.models import (
        ColumnMetadata,
        RelationshipMetadata,
        SchemaCatalog,
        TableMetadata,
    )

    tables = tuple(
        TableMetadata(
            name=table.table_id,
            description=table.description,
            columns=tuple(
                ColumnMetadata(
                    name=column.name, type=column.data_type, description=column.description
                )
                for column in table.columns
            ),
            relationships=tuple(
                RelationshipMetadata(
                    column=relationship.left_column_id.split(".", 1)[1],
                    referenced_table=relationship.right_table_id,
                    referenced_column=relationship.right_column_id.split(".", 1)[1],
                )
                for relationship in catalog.relationships
                if relationship.left_table_id == table.table_id
            ),
        )
        for table in catalog.tables
    )
    return SemanticMappingSnapshot.from_schema(SchemaCatalog(tables=tables))


def _attribute_from_reference(
    reference: str, mapping: SemanticMappingSnapshot
) -> SemanticAttributeMapping:
    if "." not in reference:
        raise SemanticValidationError(SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, reference)
    table, column = reference.split(".", 1)
    try:
        return mapping.attribute_for_physical(table, column)
    except ValueError as error:
        raise SemanticValidationError(
            SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, str(error)
        ) from error


def _attribute_from_components(
    components: tuple[str, ...],
    fallback: SemanticAttributeMapping,
    mapping: SemanticMappingSnapshot,
) -> SemanticAttributeMapping:
    return _attribute_from_reference(components[0], mapping) if components else fallback


def _aggregate(name: str, attribute: SemanticAttributeMapping) -> AggregateExpression:
    normalized = name.upper()
    function = "COUNT_DISTINCT" if "DISTINCT" in normalized else normalized
    if function not in {"COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX"}:
        raise SemanticValidationError(SemanticFailureCode.INVALID_AGGREGATION, name)
    return AggregateExpression(
        function=function, expression=AttributeRef(attribute_id=attribute.attribute_id)
    )


def _ratio_component(
    columns: tuple[str, ...], aggregation: str, mapping: SemanticMappingSnapshot
) -> Expression:
    if not columns:
        raise SemanticValidationError(
            SemanticFailureCode.INVALID_CALCULATION, "ratio component has no source column"
        )
    return _aggregate(aggregation, _attribute_from_reference(columns[0], mapping))


def _first_attribute(*expressions: Expression) -> AttributeRef:
    for expression in expressions:
        if isinstance(expression, AggregateExpression) and isinstance(
            expression.expression, AttributeRef
        ):
            return expression.expression
    raise SemanticValidationError(
        SemanticFailureCode.INVALID_CALCULATION, "ratio has no attribute source"
    )


def _v1_predicate(
    column_id: str,
    operator: QueryPlanV1Operator,
    value: QueryPlanV1Value | None,
    mapping: SemanticMappingSnapshot,
) -> Expression:
    attribute = _attribute_from_reference(column_id, mapping)
    left = AttributeRef(attribute_id=attribute.attribute_id)
    if operator is QueryPlanV1Operator.IS_NULL:
        from app.semantics.semantic_query import IsNullExpression

        return IsNullExpression(expression=left)
    if operator is QueryPlanV1Operator.IS_NOT_NULL:
        from app.semantics.semantic_query import IsNullExpression

        return IsNullExpression(expression=left, negated=True)
    assert value is not None
    kind_map = {
        "string": "string",
        "integer": "integer",
        "decimal": "decimal",
        "boolean": "boolean",
        "date": "date",
        "timestamp": "timestamp",
    }
    right = LiteralExpression(value=value.value, value_type=kind_map[value.kind])
    operator_map = {
        QueryPlanV1Operator.EQ: BinaryOperator.EQ,
        QueryPlanV1Operator.NE: BinaryOperator.NE,
        QueryPlanV1Operator.LT: BinaryOperator.LT,
        QueryPlanV1Operator.LTE: BinaryOperator.LTE,
        QueryPlanV1Operator.GT: BinaryOperator.GT,
        QueryPlanV1Operator.GTE: BinaryOperator.GTE,
    }
    return BinaryExpression(operator=operator_map[operator], left=left, right=right)


def _and(expressions: tuple[Expression, ...]) -> Expression:
    if len(expressions) == 1:
        return expressions[0]
    from app.semantics.semantic_query import LogicalExpression

    return LogicalExpression(operator="AND", terms=expressions)
