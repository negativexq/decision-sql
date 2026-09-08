"""Evaluation-only conversion from canonical oracle plans to M31 fixtures.

This module is never imported by runtime/provider code.  It turns the existing
provider-free oracle plans into logical dataflow fixtures for the M31.0 ceiling
test.  It is intentionally kept under ``evaluation`` so oracle information
cannot reach a model request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.semantics.logical_plan import (
    LogicalAggregate,
    LogicalAggregateFunction,
    LogicalAttribute,
    LogicalBetween,
    LogicalBinary,
    LogicalBinaryOperator,
    LogicalBoolean,
    LogicalCase,
    LogicalCaseBranch,
    LogicalExpression,
    LogicalFilter,
    LogicalFunction,
    LogicalFunctionCall,
    LogicalIn,
    LogicalInterval,
    LogicalLiteral,
    LogicalMeasure,
    LogicalNot,
    LogicalNullTest,
    LogicalOrder,
    LogicalOuterAttribute,
    LogicalOutput,
    LogicalProject,
    LogicalQueryPlanV1,
    LogicalRelate,
    LogicalRelationMode,
    LogicalResult,
    LogicalScalarResult,
    LogicalScan,
    LogicalSort,
    LogicalStar,
    LogicalTop,
    LogicalWindow,
    LogicalWindowFunction,
    LogicalWindowOutput,
    _logical_attribute_ids,
)
from app.semantics.relationship_graph import RelationshipPathError, SemanticRelationshipGraph
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    BetweenExpression,
    BinaryExpression,
    CaseExpression,
    CastExpression,
    Expression,
    FunctionExpression,
    InExpression,
    IntervalExpression,
    IsNullExpression,
    LiteralExpression,
    NotExpression,
    OrderedAggregateExpression,
    ScalarSubqueryExpression,
    StarExpression,
    WindowExpression,
)
from app.semantics.semantic_query import (
    LogicalExpression as SemanticLogicalExpression,
)


@dataclass(frozen=True)
class _QueryBuild:
    terminal: int
    output_slots: dict[str, int]
    output_expressions: tuple[Expression, ...]


class OracleLogicalFixtureBuilder:
    """Build a logical plan without changing or importing runtime behavior."""

    def __init__(self, mapping: SemanticMappingSnapshot, database_id: str) -> None:
        self.mapping = mapping
        self.database_id = database_id
        self.graph = SemanticRelationshipGraph(mapping)
        self.steps: list[Any] = []
        self._queries: dict[int, _QueryBuild] = {}
        self._replacement_stack: list[dict[str, LogicalExpression]] = []
        self._definition_stack: list[tuple[Any, ...]] = []
        self._needed_attributes: set[str] = set()
        self._active_queries: set[int] = set()
        # Canonical oracle scopes are retained only while building an offline
        # fixture.  They let the fixture preserve explicit correlation without
        # exposing canonical aliases to the logical contract.
        self._logical_local_refs: list[set[str]] = []
        self._logical_scope_attributes: list[dict[tuple[str, str], str]] = []

    def build(self, plan: Any) -> LogicalQueryPlanV1:
        self.steps = []
        self._queries = {}
        self._replacement_stack = []
        self._definition_stack = []
        root = plan_to_ir_like(plan)
        # Width/slot planning follows attributes that are part of logical
        # expressions.  Relationship join keys are server-owned mechanics and
        # must not become phantom logical output slots.
        self._needed_attributes = _canonical_attribute_ids(root)
        self._active_queries = set()
        self._logical_local_refs = []
        self._logical_scope_attributes = []
        result = self._query(root, tuple(root.ctes) + tuple(root.derived_relations))
        first = LogicalQueryPlanV1(steps=tuple(self.steps), final_step=result.terminal)
        # Rebuild once with the actual logical attribute surface.  The first
        # pass discovers expression slots; the second prevents hidden
        # relationship keys used by the canonical oracle from affecting
        # logical window/result positions.
        self._needed_attributes = _logical_attribute_ids(first)
        self.steps = []
        self._queries = {}
        self._replacement_stack = []
        self._definition_stack = []
        self._active_queries = set()
        self._logical_local_refs = []
        self._logical_scope_attributes = []
        result = self._query(root, tuple(root.ctes) + tuple(root.derived_relations))
        return LogicalQueryPlanV1(steps=tuple(self.steps), final_step=result.terminal)

    def _query(self, query: Any, available_definitions: tuple[Any, ...] = ()) -> _QueryBuild:
        key = id(query)
        if key in self._queries:
            return self._queries[key]
        self._active_queries.add(key)
        self._definition_stack.append(available_definitions)
        source_step, relation_slots = self._source(query, available_definitions)
        local_refs = _local_relation_refs(query)
        local_scope_attributes = self._scope_attributes(query, available_definitions)
        self._logical_local_refs.append(local_refs)
        self._logical_scope_attributes.append(local_scope_attributes)
        definitions = tuple(query.ctes) + tuple(query.derived_relations) + available_definitions
        # A query may project values from sibling CTEs (the canonical oracle
        # represents these as ordinary AttributeRef nodes).  The logical
        # fixture must turn those references into dataflow references rather
        # than leaking their canonical output IDs as semantic attributes.
        for definition in definitions:
            if id(definition.query) in self._active_queries:
                continue
            nested = self._query(definition.query, available_definitions)
            for exported in definition.exported_attributes:
                relation_slots.setdefault(
                    exported.attribute_id, (nested.terminal, exported.position)
                )
        current = source_step
        joined = set(self._base_entities(query))
        for planned in query.joins:
            if getattr(planned.target_source, "kind", None) == "cte":
                definition = next(
                    item for item in definitions if item.cte_id == planned.target_source.cte_id
                )
                nested = self._query(definition.query, available_definitions)
                self.steps.append(
                    LogicalRelate(
                        input_step=current,
                        result_step=nested.terminal,
                        mode=(
                            LogicalRelationMode.PRESERVE_LEFT
                            if planned.join_type.value == "LEFT"
                            else LogicalRelationMode.MATCHING
                        ),
                    )
                )
                current = len(self.steps) - 1
                continue
            relationship_ids = planned.relationship_path or (
                (planned.relationship_id,) if planned.relationship_id else ()
            )
            if not relationship_ids and getattr(planned.target_source, "kind", None) == "entity":
                target_entity = planned.target_source.entity_id
                candidates: list[tuple[Any, ...]] = []
                for root_entity in joined:
                    try:
                        path = self.graph.resolve_path(
                            root_entity, target_entity, allow_fanout=True
                        )
                    except RelationshipPathError:
                        continue
                    if path:
                        candidates.append(path)
                if candidates:
                    shortest = min(len(path) for path in candidates)
                    shortest_paths = [path for path in candidates if len(path) == shortest]
                    if len(shortest_paths) == 1:
                        relationship_ids = tuple(item.relationship_id for item in shortest_paths[0])
            for relationship_id in relationship_ids:
                relationship = self.mapping.relationship(relationship_id)
                endpoints = {relationship.from_entity_id, relationship.to_entity_id}
                target = next(iter(endpoints - joined), None)
                if target is None:
                    continue
                self.steps.append(
                    LogicalRelate(
                        input_step=current,
                        entity_id=target,
                        mode=(
                            LogicalRelationMode.PRESERVE_LEFT
                            if planned.join_type.value == "LEFT"
                            else LogicalRelationMode.MATCHING
                        ),
                    )
                )
                current = len(self.steps) - 1
                joined.add(target)
        if query.where is not None:
            self.steps.append(
                LogicalFilter(
                    input_step=current,
                    predicate=self._expr(query.where, relation_slots, current),
                )
            )
            current = len(self.steps) - 1

        output_exprs = tuple(item.expression for item in query.select)
        aggregate_items = _find_expressions(
            output_exprs, (AggregateExpression, OrderedAggregateExpression)
        )
        expression_slots: dict[str, LogicalExpression] = {}
        if aggregate_items:
            group_exprs = tuple(
                self._expr(item, relation_slots, current) for item in query.group_by
            )
            measures = tuple(
                self._measure(item, relation_slots, current) for item in aggregate_items
            )
            self.steps.append(
                LogicalAggregate(input_step=current, group_by=group_exprs, measures=measures)
            )
            aggregate_step = len(self.steps) - 1
            for position, item in enumerate(query.group_by):
                expression_slots[_signature(item)] = LogicalResult(
                    step=aggregate_step, slot=position
                )
            for offset, item in enumerate(aggregate_items):
                expression_slots[_signature(item)] = LogicalResult(
                    step=aggregate_step, slot=len(query.group_by) + offset
                )
            current = aggregate_step

        window_items = _find_expressions(output_exprs, (WindowExpression,))
        if window_items:
            outputs = tuple(
                LogicalWindowOutput(
                    function=LogicalWindowFunction(item.function),
                    arguments=tuple(
                        self._expr(arg, relation_slots, current) for arg in item.arguments
                    ),
                    partition_by=tuple(
                        self._expr(arg, relation_slots, current) for arg in item.partition_by
                    ),
                    order_by=tuple(
                        LogicalOrder(
                            expression=self._expr(order.expression, relation_slots, current),
                            direction=order.direction,
                        )
                        for order in item.order_by
                    ),
                )
                for item in window_items
            )
            window_input_width = self._step_width(current)
            self.steps.append(LogicalWindow(input_step=current, outputs=outputs))
            window_step = len(self.steps) - 1
            for offset, item in enumerate(window_items):
                expression_slots[_signature(item)] = LogicalResult(
                    step=window_step, slot=window_input_width + offset
                )
            current = window_step

        # ORDER/TOP operate on the query's logical input.  Keeping them before
        # PROJECT preserves hidden order keys and avoids making a later sort
        # depend on compiler aliases or on an output slot that PROJECT did not
        # expose.
        self._replacement_stack.append(expression_slots)
        if query.order_by:
            order_items = tuple(
                LogicalOrder(
                    expression=expression_slots.get(_signature(spec.expression))
                    or self._expr(spec.expression, relation_slots, current),
                    direction=spec.direction,
                )
                for spec in query.order_by
            )
            self.steps.append(LogicalSort(input_step=current, order_by=order_items))
            current = len(self.steps) - 1
        if query.limit is not None:
            self.steps.append(
                LogicalTop(input_step=current, limit=query.limit, offset=query.offset)
            )
            current = len(self.steps) - 1
        logical_outputs = tuple(
            LogicalOutput(
                expression=(
                    expression_slots[_signature(item)]
                    if _signature(item) in expression_slots
                    else self._expr(item, relation_slots, current)
                )
            )
            for item in output_exprs
        )
        self.steps.append(LogicalProject(input_step=current, outputs=logical_outputs))
        current = len(self.steps) - 1
        output_slots = {f"slot:{position}": position for position in range(len(output_exprs))}
        self._replacement_stack.pop()
        result = _QueryBuild(
            terminal=current,
            output_slots=output_slots,
            output_expressions=output_exprs,
        )
        self._queries[key] = result
        self._definition_stack.pop()
        self._active_queries.discard(key)
        self._logical_scope_attributes.pop()
        self._logical_local_refs.pop()
        return result

    def _source(
        self, query: Any, available_definitions: tuple[Any, ...]
    ) -> tuple[int, dict[str, tuple[int, int]]]:
        source = query.from_source
        if getattr(source, "kind", None) == "entity":
            self.steps.append(LogicalScan(entity_id=source.entity_id))
            return len(self.steps) - 1, {}
        relation_id = source.cte_id if source.kind == "cte" else source.relation_id
        definitions = tuple(query.ctes) + tuple(query.derived_relations) + available_definitions
        definition = next(
            item
            for item in definitions
            if (item.cte_id if source.kind == "cte" else item.relation_id) == relation_id
        )
        nested = self._query(definition.query, available_definitions)
        self.steps.append(LogicalScan(input_step=nested.terminal))
        slots: dict[str, tuple[int, int]] = {
            item.attribute_id: (len(self.steps) - 1, item.position)
            for item in definition.exported_attributes
        }
        # Preserve the logical slot identity for canonical plans whose outer
        # query refers to a CTE's selected physical attribute directly.
        for position, item in enumerate(definition.query.select):
            for attribute_id in _attribute_ids(item.expression):
                slots.setdefault(attribute_id, (len(self.steps) - 1, position))
        return len(self.steps) - 1, slots

    def _scope_attributes(
        self,
        query: Any,
        available_definitions: tuple[Any, ...],
    ) -> dict[tuple[str, str], str]:
        """Return semantic values exported by this query's local source.

        This is fixture construction metadata only.  It deliberately resolves
        output IDs back to semantic attributes and never carries SQL aliases
        into the LogicalQueryPlan contract.
        """

        source = query.from_source
        if source is None:
            return {}
        relation_ref = getattr(source, "source_id", None) or getattr(source, "relation_id", None)
        if relation_ref is None:
            return {}
        if source.kind == "entity":
            return {
                (relation_ref, item.attribute_id): item.attribute_id
                for item in self.mapping.attributes
                if item.entity_id == source.entity_id
            }
        relation_id = source.cte_id if source.kind == "cte" else source.relation_id
        definitions = tuple(query.ctes) + tuple(query.derived_relations) + available_definitions
        definition = next(
            (
                item
                for item in definitions
                if (item.cte_id if source.kind == "cte" else item.relation_id) == relation_id
            ),
            None,
        )
        if definition is None:
            return {}
        result: dict[tuple[str, str], str] = {}
        for exported in definition.exported_attributes:
            semantic = self._semantic_attribute(
                definition.query,
                exported.attribute_id,
                exported.position,
                definitions,
            )
            if semantic is not None:
                result[(relation_ref, exported.attribute_id)] = semantic
        return result

    def _semantic_attribute(
        self,
        query: Any,
        attribute_id: str,
        position: int,
        definitions: tuple[Any, ...],
        seen: set[tuple[int, str, int]] | None = None,
    ) -> str | None:
        """Resolve a direct output lineage to one semantic catalog attribute."""

        seen = seen or set()
        marker = (id(query), attribute_id, position)
        if marker in seen or position >= len(query.select):
            return None
        seen.add(marker)
        expression = query.select[position].expression
        if isinstance(expression, AttributeRef):
            if expression.attribute_id.startswith("attribute:"):
                return expression.attribute_id
            if expression.attribute_id.startswith("output:") and expression.relation_ref:
                nested = next(
                    (
                        item
                        for item in (*query.ctes, *query.derived_relations, *definitions)
                        if getattr(item, "cte_id", None) == expression.relation_ref
                        or getattr(item, "relation_id", None) == expression.relation_ref
                    ),
                    None,
                )
                if nested is not None:
                    nested_export = next(
                        (
                            item
                            for item in nested.exported_attributes
                            if item.attribute_id == expression.attribute_id
                        ),
                        None,
                    )
                    if nested_export is not None:
                        return self._semantic_attribute(
                            nested.query,
                            nested_export.attribute_id,
                            nested_export.position,
                            definitions,
                            seen,
                        )
        return None

    def _expr(
        self, expression: Expression, relation_slots: dict[str, tuple[int, int]], current: int
    ) -> LogicalExpression:
        if self._replacement_stack:
            replacement = self._replacement_stack[-1].get(_signature(expression))
            if replacement is not None:
                return replacement
        if isinstance(expression, AttributeRef):
            relation_ref = expression.relation_ref
            if relation_ref is not None and relation_ref not in self._logical_local_refs[-1]:
                for depth, scope in enumerate(
                    reversed(self._logical_scope_attributes[:-1]), start=1
                ):
                    attribute_id = scope.get((relation_ref, expression.attribute_id))
                    if attribute_id is not None:
                        return LogicalOuterAttribute(
                            attribute_id=attribute_id,
                            scope_depth=depth,
                        )
            slot = relation_slots.get(expression.attribute_id)
            if slot is not None:
                return LogicalResult(step=slot[0], slot=slot[1])
            return LogicalAttribute(attribute_id=expression.attribute_id)
        if isinstance(expression, LiteralExpression):
            return LogicalLiteral(value=expression.value, value_type=expression.value_type)
        if isinstance(expression, IntervalExpression):
            return LogicalInterval(amount=expression.amount, unit=expression.unit)
        if isinstance(expression, StarExpression):
            return LogicalStar()
        if isinstance(expression, BinaryExpression):
            return LogicalBinary(
                operator=LogicalBinaryOperator(expression.operator.value),
                left=self._expr(expression.left, relation_slots, current),
                right=self._expr(expression.right, relation_slots, current),
            )
        if isinstance(expression, FunctionExpression):
            return LogicalFunctionCall(
                function=LogicalFunction(expression.function.value),
                arguments=tuple(
                    self._expr(item, relation_slots, current) for item in expression.arguments
                ),
            )
        if isinstance(expression, SemanticLogicalExpression):
            return LogicalBoolean(
                operator=expression.operator,
                terms=tuple(self._expr(item, relation_slots, current) for item in expression.terms),
            )
        if isinstance(expression, NotExpression):
            return LogicalNot(expression=self._expr(expression.expression, relation_slots, current))
        if isinstance(expression, BetweenExpression):
            return LogicalBetween(
                expression=self._expr(expression.expression, relation_slots, current),
                low=self._expr(expression.low, relation_slots, current),
                high=self._expr(expression.high, relation_slots, current),
            )
        if isinstance(expression, InExpression):
            return LogicalIn(
                expression=self._expr(expression.expression, relation_slots, current),
                values=tuple(
                    self._expr(item, relation_slots, current) for item in expression.values
                ),
                negated=expression.negated,
            )
        if isinstance(expression, IsNullExpression):
            return LogicalNullTest(
                expression=self._expr(expression.expression, relation_slots, current),
                negated=expression.negated,
            )
        if isinstance(expression, CaseExpression):
            return LogicalCase(
                branches=tuple(
                    LogicalCaseBranch(
                        when=self._expr(item.when, relation_slots, current),
                        then=self._expr(item.then, relation_slots, current),
                    )
                    for item in expression.branches
                ),
                default=self._expr(expression.default, relation_slots, current)
                if expression.default is not None
                else None,
            )
        if isinstance(expression, CastExpression):
            return LogicalFunctionCall(
                function=LogicalFunction.CAST,
                arguments=(
                    self._expr(expression.expression, relation_slots, current),
                    LogicalLiteral(value=expression.target_type, value_type="string"),
                ),
            )
        if isinstance(expression, ScalarSubqueryExpression):
            nested = self._query(
                expression.query,
                self._definition_stack[-1] if self._definition_stack else (),
            )
            return LogicalScalarResult(step=nested.terminal)
        raise TypeError(
            f"oracle expression is not in LogicalQueryPlanV1: {type(expression).__name__}"
        )

    def _measure(
        self, expression: Expression, relation_slots: dict[str, tuple[int, int]], current: int
    ) -> LogicalMeasure:
        if isinstance(expression, AggregateExpression):
            return LogicalMeasure(
                function=LogicalAggregateFunction(expression.function),
                expression=self._expr(expression.expression, relation_slots, current),
                distinct=expression.distinct,
                filter=(
                    self._expr(expression.filter, relation_slots, current)
                    if expression.filter is not None
                    else None
                ),
            )
        if isinstance(expression, OrderedAggregateExpression):
            return LogicalMeasure(
                function=LogicalAggregateFunction.PERCENTILE_CONT,
                expression=self._expr(expression.expression, relation_slots, current),
                percentile=self._expr(expression.percentile, relation_slots, current),
            )
        raise TypeError(f"not an aggregate expression: {type(expression).__name__}")

    def _step_width(self, index: int) -> int:
        step = self.steps[index]
        if isinstance(step, LogicalScan):
            if step.entity_id is not None:
                return sum(
                    item.entity_id == step.entity_id
                    and item.attribute_id in self._needed_attributes
                    for item in self.mapping.attributes
                )
            return self._step_width(step.input_step)  # type: ignore[arg-type]
        if isinstance(step, LogicalRelate):
            width = self._step_width(step.input_step)
            if step.entity_id is not None:
                width += sum(
                    item.entity_id == step.entity_id
                    and item.attribute_id in self._needed_attributes
                    for item in self.mapping.attributes
                )
            return width
        if isinstance(step, (LogicalFilter, LogicalSort, LogicalTop)):
            return self._step_width(step.input_step)
        if isinstance(step, LogicalProject):
            return len(step.outputs)
        if isinstance(step, LogicalAggregate):
            return len(step.group_by) + len(step.measures)
        if isinstance(step, LogicalWindow):
            return self._step_width(step.input_step) + len(step.outputs)
        raise TypeError(type(step).__name__)

    def _base_entities(self, query: Any) -> tuple[str, ...]:
        return tuple(query.population_contract.base_entity_ids)


def _export_id(item: Any, position: int) -> str:
    return f"output:query:{position}:{item.alias or position}"


def _output_name(expression: Expression) -> str:
    return getattr(expression, "attribute_id", "")


def _signature(value: Any) -> str:
    return repr(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)


def _find_expressions(values: Any, types: tuple[type[Any], ...]) -> list[Any]:
    found: list[Any] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, ScalarSubqueryExpression):
            return
        if isinstance(value, types):
            signature = _signature(value)
            if signature not in seen:
                seen.add(signature)
                found.append(value)
            return
        if isinstance(value, BaseModel):
            for name in value.__class__.model_fields:
                child = getattr(value, name)
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit(values)
    return found


def _canonical_attribute_ids(value: Any) -> set[str]:
    result: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, AttributeRef):
            result.add(item.attribute_id)
            return
        if hasattr(item, "from_source") and hasattr(item, "select"):
            for name in (
                "select",
                "where",
                "group_by",
                "having",
                "order_by",
                "ctes",
                "derived_relations",
            ):
                visit(getattr(item, name, None))
            return
        if hasattr(item, "query") and hasattr(item, "exported_attributes"):
            visit(item.query)
            return
        if isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                visit(getattr(item, name))
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)

    visit(value)
    return result


def _expression_attribute_ids(value: Any) -> set[str]:
    result: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, AttributeRef):
            result.add(item.attribute_id)
            return
        if isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                visit(getattr(item, name))
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)

    for name in (
        "select",
        "where",
        "group_by",
        "having",
        "order_by",
        "ctes",
        "derived_relations",
    ):
        visit(getattr(value, name, None))
    return result


def _attribute_ids(value: Any) -> set[str]:
    result: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, AttributeRef):
            result.add(item.attribute_id)
            return
        if isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                visit(getattr(item, name))
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)

    visit(value)
    return result


def _local_relation_refs(query: Any) -> set[str]:
    """Collect relation identities declared by this query, not expressions."""
    result: set[str] = set()
    source = query.from_source
    if source is not None:
        relation_ref = getattr(source, "source_id", None) or getattr(source, "relation_id", None)
        if relation_ref is not None:
            result.add(relation_ref)
        if getattr(source, "kind", None) == "entity":
            result.add(source.entity_id)
    for planned in query.joins:
        target = planned.target_source
        if target is not None:
            relation_ref = getattr(target, "source_id", None) or getattr(
                target, "relation_id", None
            )
            if relation_ref is not None:
                result.add(relation_ref)
            if getattr(target, "kind", None) == "entity":
                result.add(target.entity_id)
    return result


def plan_to_ir_like(plan: Any) -> Any:
    """Accept either a plan or IR; oracle fixtures use both shapes."""

    from app.semantics.semantic_query import plan_to_ir

    return plan_to_ir(plan) if hasattr(plan, "outputs") else plan
