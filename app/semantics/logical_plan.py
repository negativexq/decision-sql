"""Small compositional, model-facing logical query language.

The logical contract intentionally contains semantic choices and dataflow only.
It does not contain database names, physical sources, relationship IDs, join
keys, aliases, output positions, SQL, or canonical compiler bookkeeping.
``LogicalPlanResolver`` is the only bridge to the canonical semantic engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.semantics.relationship_graph import RelationshipPathError, SemanticRelationshipGraph
from app.semantics.semantic_errors import SemanticEngineError, SemanticFailureCode
from app.semantics.semantic_mapping import SemanticMappingError, SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    BinaryExpression,
    BinaryOperator,
    CaseBranch,
    CaseExpression,
    CastExpression,
    CommonTableExpression,
    CTERelationSource,
    DerivedRelation,
    EntityRelationSource,
    Expression,
    FunctionExpression,
    InExpression,
    IntervalExpression,
    IRSelectItem,
    IsNullExpression,
    JoinKey,
    JoinType,
    LiteralExpression,
    NotExpression,
    OrderedAggregateExpression,
    OrderSpec,
    PlannedJoin,
    PlannedOutput,
    PopulationContract,
    PopulationInclusion,
    ScalarSubqueryExpression,
    SemanticFunction,
    SemanticQueryIR,
    SemanticQueryPlan,
    SortDirection,
    StarExpression,
    WindowExpression,
    WindowOrder,
)
from app.semantics.semantic_query import (
    LogicalExpression as SemanticLogicalExpression,
)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LogicalRelationMode(StrEnum):
    """Population meaning, not a physical SQL join type."""

    MATCHING = "MATCHING"
    PRESERVE_LEFT = "PRESERVE_LEFT"
    SEMI = "SEMI"
    ANTI = "ANTI"


class LogicalBinaryOperator(StrEnum):
    ADD = "ADD"
    SUBTRACT = "SUBTRACT"
    MULTIPLY = "MULTIPLY"
    DIVIDE = "DIVIDE"
    MODULO = "MODULO"
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"


class LogicalFunction(StrEnum):
    ABS = "ABS"
    AGE = "AGE"
    ARRAY_REMOVE = "ARRAY_REMOVE"
    CARDINALITY = "CARDINALITY"
    CAST = "CAST"
    COALESCE = "COALESCE"
    CURRENT_DATE = "CURRENT_DATE"
    DATE_TRUNC = "DATE_TRUNC"
    EXTRACT = "EXTRACT"
    GREATEST = "GREATEST"
    JSONB_EXTRACT_PATH_TEXT = "JSONB_EXTRACT_PATH_TEXT"
    JSONB_EXTRACT_SCALAR = "JSONB_EXTRACT_SCALAR"
    JSON_EXTRACT = "JSON_EXTRACT"
    JSON_EXTRACT_SCALAR = "JSON_EXTRACT_SCALAR"
    NULLIF = "NULLIF"
    ROUND = "ROUND"
    SQRT = "SQRT"
    STRING_TO_ARRAY = "STRING_TO_ARRAY"
    TO_CHAR = "TO_CHAR"


class LogicalAggregateFunction(StrEnum):
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    CORR = "CORR"
    REGR_SLOPE = "REGR_SLOPE"
    STDDEV = "STDDEV"
    PERCENTILE_CONT = "PERCENTILE_CONT"


class LogicalWindowFunction(StrEnum):
    ROW_NUMBER = "ROW_NUMBER"
    RANK = "RANK"
    DENSE_RANK = "DENSE_RANK"
    LAG = "LAG"
    LEAD = "LEAD"
    NTH_VALUE = "NTH_VALUE"
    NTILE = "NTILE"
    PERCENT_RANK = "PERCENT_RANK"
    FIRST_VALUE = "FIRST_VALUE"
    LAST_VALUE = "LAST_VALUE"
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class LogicalAttribute(_Strict):
    kind: Literal["attribute"] = "attribute"
    attribute_id: str = Field(min_length=1, max_length=256)


class LogicalOuterAttribute(_Strict):
    """A semantic attribute from an explicitly enclosing logical scope."""

    kind: Literal["outer_attribute"] = "outer_attribute"
    attribute_id: str = Field(min_length=1, max_length=256)
    scope_depth: int = Field(ge=1, le=4)


class LogicalResult(_Strict):
    kind: Literal["result"] = "result"
    step: int = Field(ge=0, le=63)
    slot: int = Field(ge=0, le=63)


class LogicalLiteral(_Strict):
    kind: Literal["literal"] = "literal"
    value: str | int | float | bool | None
    value_type: Literal["null", "string", "integer", "decimal", "boolean", "date", "timestamp"]


class LogicalInterval(_Strict):
    kind: Literal["interval"] = "interval"
    amount: str = Field(min_length=1, max_length=32)
    unit: str = Field(min_length=1, max_length=32)


class LogicalStar(_Strict):
    kind: Literal["star"] = "star"


class LogicalBinary(_Strict):
    kind: Literal["binary"] = "binary"
    operator: LogicalBinaryOperator
    left: LogicalExpression
    right: LogicalExpression


class LogicalFunctionCall(_Strict):
    kind: Literal["function"] = "function"
    function: LogicalFunction
    arguments: tuple[LogicalExpression, ...] = Field(default=(), max_length=8)


class LogicalBoolean(_Strict):
    kind: Literal["boolean"] = "boolean"
    operator: Literal["AND", "OR"]
    terms: tuple[LogicalExpression, ...] = Field(min_length=2, max_length=16)


class LogicalNot(_Strict):
    kind: Literal["not"] = "not"
    expression: LogicalExpression


class LogicalBetween(_Strict):
    kind: Literal["between"] = "between"
    expression: LogicalExpression
    low: LogicalExpression
    high: LogicalExpression


class LogicalIn(_Strict):
    kind: Literal["in"] = "in"
    expression: LogicalExpression
    values: tuple[LogicalExpression, ...] = Field(min_length=1, max_length=32)
    negated: bool = False


class LogicalNullTest(_Strict):
    kind: Literal["null_test"] = "null_test"
    expression: LogicalExpression
    negated: bool = False


class LogicalCaseBranch(_Strict):
    when: LogicalExpression
    then: LogicalExpression


class LogicalCase(_Strict):
    kind: Literal["case"] = "case"
    branches: tuple[LogicalCaseBranch, ...] = Field(min_length=1, max_length=8)
    default: LogicalExpression | None = None


class LogicalScalarResult(_Strict):
    kind: Literal["scalar_result"] = "scalar_result"
    step: int = Field(ge=0, le=63)


LogicalExpression = Annotated[
    LogicalAttribute
    | LogicalOuterAttribute
    | LogicalResult
    | LogicalLiteral
    | LogicalInterval
    | LogicalStar
    | LogicalBinary
    | LogicalFunctionCall
    | LogicalBoolean
    | LogicalNot
    | LogicalBetween
    | LogicalIn
    | LogicalNullTest
    | LogicalCase
    | LogicalScalarResult,
    Field(discriminator="kind"),
]


class LogicalOutput(_Strict):
    expression: LogicalExpression


class LogicalOrder(_Strict):
    expression: LogicalExpression
    direction: SortDirection = SortDirection.ASC


class LogicalMeasure(_Strict):
    function: LogicalAggregateFunction
    expression: LogicalExpression
    distinct: bool = False
    percentile: LogicalExpression | None = None
    filter: LogicalExpression | None = None


class LogicalWindowOutput(_Strict):
    function: LogicalWindowFunction
    arguments: tuple[LogicalExpression, ...] = Field(default=(), max_length=4)
    partition_by: tuple[LogicalExpression, ...] = Field(default=(), max_length=8)
    order_by: tuple[LogicalOrder, ...] = Field(default=(), max_length=8)


class LogicalScan(_Strict):
    kind: Literal["SCAN"] = "SCAN"
    entity_id: str | None = Field(default=None, min_length=1, max_length=128)
    input_step: int | None = Field(default=None, ge=0, le=63)

    @model_validator(mode="after")
    def exactly_one_source(self) -> LogicalScan:
        if (self.entity_id is None) == (self.input_step is None):
            raise ValueError("SCAN requires exactly one entity_id or input_step")
        return self


class LogicalRelate(_Strict):
    kind: Literal["RELATE"] = "RELATE"
    input_step: int = Field(ge=0, le=63)
    entity_id: str | None = Field(default=None, min_length=1, max_length=128)
    result_step: int | None = Field(default=None, ge=0, le=63)
    mode: LogicalRelationMode = LogicalRelationMode.MATCHING

    @model_validator(mode="after")
    def exactly_one_target(self) -> LogicalRelate:
        if (self.entity_id is None) == (self.result_step is None):
            raise ValueError("RELATE requires exactly one entity_id or result_step")
        return self


class LogicalFilter(_Strict):
    kind: Literal["FILTER"] = "FILTER"
    input_step: int = Field(ge=0, le=63)
    predicate: LogicalExpression


class LogicalProject(_Strict):
    kind: Literal["PROJECT"] = "PROJECT"
    input_step: int = Field(ge=0, le=63)
    outputs: tuple[LogicalOutput, ...] = Field(min_length=1, max_length=32)


class LogicalAggregate(_Strict):
    kind: Literal["AGGREGATE"] = "AGGREGATE"
    input_step: int = Field(ge=0, le=63)
    group_by: tuple[LogicalExpression, ...] = Field(default=(), max_length=16)
    measures: tuple[LogicalMeasure, ...] = Field(min_length=1, max_length=32)


class LogicalCompute(_Strict):
    kind: Literal["COMPUTE"] = "COMPUTE"
    input_step: int = Field(ge=0, le=63)
    outputs: tuple[LogicalOutput, ...] = Field(min_length=1, max_length=32)


class LogicalWindow(_Strict):
    kind: Literal["WINDOW"] = "WINDOW"
    input_step: int = Field(ge=0, le=63)
    outputs: tuple[LogicalWindowOutput, ...] = Field(min_length=1, max_length=16)


class LogicalSort(_Strict):
    kind: Literal["SORT"] = "SORT"
    input_step: int = Field(ge=0, le=63)
    order_by: tuple[LogicalOrder, ...] = Field(min_length=1, max_length=16)


class LogicalTop(_Strict):
    kind: Literal["TOP"] = "TOP"
    input_step: int = Field(ge=0, le=63)
    limit: int = Field(ge=1, le=10000)
    offset: int | None = Field(default=None, ge=0, le=1000000)


LogicalStep = Annotated[
    LogicalScan
    | LogicalRelate
    | LogicalFilter
    | LogicalProject
    | LogicalAggregate
    | LogicalCompute
    | LogicalWindow
    | LogicalSort
    | LogicalTop,
    Field(discriminator="kind"),
]


class LogicalQueryPlanV1(_Strict):
    """Bounded ordered dataflow; all references point to earlier steps."""

    version: Literal["LogicalQueryPlanV1"] = "LogicalQueryPlanV1"
    steps: tuple[LogicalStep, ...] = Field(min_length=1, max_length=32)
    final_step: int = Field(ge=0, le=63)

    @model_validator(mode="after")
    def validate_dataflow(self) -> LogicalQueryPlanV1:
        if self.final_step >= len(self.steps):
            raise ValueError("final_step must reference an existing step")
        for index, step in enumerate(self.steps):
            refs: list[int] = []
            if isinstance(step, LogicalScan) and step.input_step is not None:
                refs.append(step.input_step)
            else:
                input_step = getattr(step, "input_step", None)
                if input_step is not None:
                    refs.append(input_step)
            if isinstance(step, LogicalRelate) and step.result_step is not None:
                refs.append(step.result_step)
            for ref in refs:
                if ref >= index:
                    raise ValueError("logical plan contains a forward reference or cycle")
            _validate_expression_refs(step, index)
        return self


def _validate_expression_refs(value: Any, current_step: int) -> None:
    if isinstance(value, LogicalResult | LogicalScalarResult):
        if value.step >= current_step:
            raise ValueError("expression contains a forward result reference")
        return
    if isinstance(value, BaseModel):
        for child in value.__dict__.values():
            _validate_expression_refs(child, current_step)
    elif isinstance(value, (tuple, list)):
        for child in value:
            _validate_expression_refs(child, current_step)


@dataclass(frozen=True)
class _RelationState:
    index: int
    query: SemanticQueryIR
    exports: tuple[tuple[str, str], ...]
    base_entities: tuple[str, ...]
    joined_entities: tuple[str, ...]
    population_mode: PopulationInclusion
    relationships: tuple[str, ...]
    visible_results: dict[tuple[int, int], tuple[str, str | None]] = field(default_factory=dict)
    # Semantic attributes retained solely for later server-owned lowering.
    # They are never exposed as logical outputs or provider fields.
    hidden_attributes: tuple[str, ...] = ()
    carried_results: tuple[tuple[int, int, str], ...] = ()

    @property
    def export_map(self) -> dict[str, str]:
        return dict(self.exports)


@dataclass(frozen=True)
class ResolvedLogicalPlan:
    plan: SemanticQueryPlan
    provenance: tuple[dict[str, Any], ...]


class LogicalPlanResolver:
    """Deterministically lower a logical plan into the canonical plan."""

    def __init__(self, mapping: SemanticMappingSnapshot, database_id: str) -> None:
        self.mapping = mapping
        self.database_id = database_id
        self.graph = SemanticRelationshipGraph(mapping)
        self._steps: tuple[LogicalStep, ...] = ()
        self._cache: dict[int, _RelationState] = {}
        self._provenance: list[dict[str, Any]] = []
        self._needed_attributes: set[str] = set()
        self._server_required_attributes: set[str] = set()
        self._scope_stack: list[_RelationState] = []

    def resolve(self, logical: LogicalQueryPlanV1) -> ResolvedLogicalPlan:
        self._steps = logical.steps
        self._cache = {}
        self._provenance = []
        self._needed_attributes = _logical_attribute_ids(logical)
        self._scope_stack = []
        self._server_required_attributes = {
            attribute_id
            for relationship in self.mapping.relationships
            for attribute_id in (
                relationship.from_attribute_id,
                relationship.to_attribute_id,
            )
        }
        state = self._resolve_step(logical.final_step)
        # Hidden relationship keys are an implementation detail.  Keep them
        # available to nested scopes, but never make them final user outputs.
        final_query = state.query
        if len(final_query.select) > len(state.exports):
            final_query = final_query.model_copy(
                update={"select": final_query.select[: len(state.exports)]}
            )
        outputs = tuple(
            PlannedOutput(
                position=index,
                semantic_role=f"output_{index}",
                expression=final_query.select[index].expression,
                alias=f"output_{index}",
            )
            for index in range(len(state.exports))
        )
        plan = SemanticQueryPlan(
            database_id=self.database_id,
            from_entity_id=final_query.from_entity_id,
            from_source=final_query.from_source,
            population_contract=final_query.population_contract,
            outputs=outputs,
            joins=final_query.joins,
            where=final_query.where,
            group_by=final_query.group_by,
            having=final_query.having,
            order_by=final_query.order_by,
            distinct=final_query.distinct,
            limit=final_query.limit,
            offset=final_query.offset,
            calculation_contract=final_query.calculation_contract,
            ctes=final_query.ctes,
            derived_relations=final_query.derived_relations,
        )
        return ResolvedLogicalPlan(plan=plan, provenance=tuple(self._provenance))

    @staticmethod
    def _visible_results(
        state: _RelationState,
        index: int,
        exports: tuple[tuple[str, str], ...],
    ) -> dict[tuple[int, int], tuple[str, str | None]]:
        visible = dict(state.visible_results)
        source = _relation_ref(state.query.from_source)
        carried = {(step, slot): attribute_id for step, slot, attribute_id in state.carried_results}
        if isinstance(state.query.from_source, CTERelationSource):
            for key, attribute_id in carried.items():
                visible[key] = (attribute_id, source)
        for position, export in enumerate(exports):
            visible[(index, position)] = (export[0], source)
        return visible

    def _resolve_step(self, index: int) -> _RelationState:
        cacheable = not self._scope_stack
        if cacheable and index in self._cache:
            return self._cache[index]
        step = self._steps[index]
        if isinstance(step, LogicalScan):
            state = self._resolve_scan(index, step)
        elif isinstance(step, LogicalRelate):
            state = self._resolve_relate(index, step)
        elif isinstance(step, LogicalFilter):
            state = self._resolve_filter(index, step)
        elif isinstance(step, LogicalProject):
            state = self._resolve_project(index, step)
        elif isinstance(step, LogicalAggregate):
            state = self._resolve_aggregate(index, step)
        elif isinstance(step, LogicalCompute):
            state = self._resolve_compute(index, step)
        elif isinstance(step, LogicalWindow):
            state = self._resolve_window(index, step)
        elif isinstance(step, LogicalSort):
            state = self._resolve_sort(index, step)
        elif isinstance(step, LogicalTop):
            state = self._resolve_top(index, step)
        else:  # pragma: no cover - discriminated validation makes this unreachable
            raise SemanticEngineError(
                SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT, type(step).__name__
            )
        if cacheable:
            self._cache[index] = state
        return state

    def _resolve_scan(self, index: int, step: LogicalScan) -> _RelationState:
        if step.entity_id is not None:
            try:
                entity = self.mapping.entity(step.entity_id)
            except SemanticMappingError as error:
                raise SemanticEngineError(
                    SemanticFailureCode.UNKNOWN_SEMANTIC_ENTITY, str(error)
                ) from error
            source = EntityRelationSource(entity_id=entity.entity_id, source_id=entity.entity_id)
            attrs = tuple(
                item
                for item in self.mapping.attributes
                if item.entity_id == entity.entity_id
                and item.attribute_id in self._needed_attributes
            )
            hidden = tuple(
                item.attribute_id
                for item in self.mapping.attributes
                if item.entity_id == entity.entity_id
                and item.attribute_id in self._server_required_attributes
                and item.attribute_id not in {value.attribute_id for value in attrs}
            )
            select = tuple(
                IRSelectItem(
                    position=position,
                    expression=AttributeRef(
                        attribute_id=item.attribute_id, relation_ref=entity.entity_id
                    ),
                    alias=item.physical_column,
                )
                for position, item in enumerate(attrs)
            ) + tuple(
                IRSelectItem(
                    position=len(attrs) + position,
                    expression=AttributeRef(
                        attribute_id=attribute_id, relation_ref=entity.entity_id
                    ),
                    alias=self._hidden_name(position),
                )
                for position, attribute_id in enumerate(hidden)
            )
            exports = tuple((item.attribute_id, item.attribute_id) for item in attrs)
            base = (entity.entity_id,)
            joined = base
            query = self._query(
                source=source,
                select=select,
                population=PopulationContract(base_entity_ids=base),
            )
            return _RelationState(
                index,
                query,
                exports,
                base,
                joined,
                PopulationInclusion.BASE_ENTITY,
                (),
                {
                    (index, position): (export[0], entity.entity_id)
                    for position, export in enumerate(exports)
                },
                hidden_attributes=hidden,
            )
        assert step.input_step is not None
        return self._from_input(index, self._resolve_step(step.input_step))

    def _from_input(self, index: int, input_state: _RelationState) -> _RelationState:
        relation_id = f"logical_step_{input_state.index}"
        source = CTERelationSource(cte_id=relation_id, source_id=relation_id)
        inherited_ctes = self._flatten_ctes(input_state.query.ctes)
        carried_exports = tuple(
            (attribute_id, self._carried_result_name(step, slot))
            for step, slot, attribute_id in input_state.carried_results
        )
        hidden_exports = tuple(
            (attribute_id, self._hidden_name(position))
            for position, attribute_id in enumerate(input_state.hidden_attributes)
        )
        all_exports = (*input_state.exports, *carried_exports, *hidden_exports)
        cte = CommonTableExpression(
            cte_id=relation_id,
            query=input_state.query.model_copy(update={"ctes": ()}),
            exported_attributes=tuple(
                # The exported ID is the stable semantic slot identity.
                # Physical names never cross the logical boundary.
                self._export_attribute(attribute_id, name, position)
                for position, (attribute_id, name) in enumerate(all_exports)
            ),
        )
        exprs = tuple(
            IRSelectItem(
                position=position,
                expression=AttributeRef(attribute_id=attribute_id, relation_ref=relation_id),
                alias=name,
            )
            for position, (attribute_id, name) in enumerate(all_exports)
        )
        query = self._query(
            source=source,
            select=exprs,
            population=self._population(input_state),
            ctes=(*inherited_ctes, cte),
        )
        self._provenance.append(
            {
                "canonical_field": "ctes",
                "owner": "SERVER_DERIVED",
                "derived_from": f"logical step {input_state.index}",
                "rule": "logical result reference is packaged as a server-owned CTE",
            }
        )
        visible_results: dict[tuple[int, int], tuple[str, str | None]] = {
            key: (
                export[0],
                export[1]
                if export[1] is not None and export[1].startswith("logical_result_")
                else relation_id,
            )
            for key, export in input_state.visible_results.items()
        }
        for position, export in enumerate(input_state.exports):
            visible_results[(input_state.index, position)] = (export[0], relation_id)
        for step, slot, attribute_id in input_state.carried_results:
            visible_results[(step, slot)] = (attribute_id, relation_id)
        for position, export in enumerate(input_state.exports):
            visible_results[(index, position)] = (export[0], relation_id)
        return _RelationState(
            index=index,
            query=query,
            exports=input_state.exports,
            base_entities=input_state.base_entities,
            joined_entities=input_state.joined_entities,
            population_mode=input_state.population_mode,
            relationships=input_state.relationships,
            visible_results=visible_results,
            hidden_attributes=input_state.hidden_attributes,
            carried_results=input_state.carried_results,
        )

    def _operation_input(self, index: int, input_index: int) -> _RelationState:
        """Keep a direct entity query flat; nest only genuine prior results."""

        state = self._resolve_step(input_index)
        # A correlated scalar must retain its predicate in the scalar query's
        # own scope.  Wrapping that predicate in a server CTE would make the
        # CTE illegally depend on an enclosing row and the SQL compiler would
        # correctly reject the lost outer binding.
        if self._scope_stack and isinstance(state.query.from_source, CTERelationSource):
            return state
        if isinstance(state.query.from_source, EntityRelationSource) and not any(
            attribute_id.startswith("output:") for attribute_id, _ in state.exports
        ):
            return state
        return self._from_input(index, state)

    def _resolve_relate(self, index: int, step: LogicalRelate) -> _RelationState:
        input_state = self._resolve_step(step.input_step)
        if step.result_step is not None:
            return self._resolve_result_relate(index, step, input_state)
        assert step.entity_id is not None
        target = self.mapping.entity(step.entity_id)
        roots = input_state.joined_entities or input_state.base_entities
        paths: list[tuple[Any, ...]] = []
        direct_paths = []
        for root in roots:
            for relationship in self.mapping.relationships_for(root):
                if target.entity_id in {
                    relationship.from_entity_id,
                    relationship.to_entity_id,
                }:
                    direct_paths.append((root, relationship))
        if len(direct_paths) == 1:
            root, relationship = direct_paths[0]
            paths.append(
                (
                    type(
                        "Path",
                        (),
                        {
                            "relationship_id": relationship.relationship_id,
                            "source_entity": root,
                            "target_entity": target.entity_id,
                        },
                    )(),
                )
            )
        for root in roots:
            if paths:
                break
            try:
                path = self.graph.resolve_path(
                    root,
                    target.entity_id,
                    allow_fanout=True,
                )
            except RelationshipPathError:
                continue
            if path:
                paths.append(path)
        if not paths:
            raise SemanticEngineError(
                SemanticFailureCode.NO_RELATIONSHIP_PATH,
                f"no authorized path to {target.entity_id}",
            )
        shortest = min(len(item) for item in paths)
        candidates = [item for item in paths if len(item) == shortest]
        if len(candidates) != 1:
            raise SemanticEngineError(
                SemanticFailureCode.AMBIGUOUS_RELATIONSHIP_PATH,
                f"ambiguous path to {target.entity_id}",
            )
        path = candidates[0]
        relationship_ids = tuple(item.relationship_id for item in path)
        current = self._operation_input(index, step.input_step)
        joins = list(current.query.joins)
        join_type = (
            JoinType.LEFT if step.mode == LogicalRelationMode.PRESERVE_LEFT else JoinType.INNER
        )
        if isinstance(current.query.from_source, CTERelationSource) and len(relationship_ids) == 1:
            relationship = self.mapping.relationship(relationship_ids[0])
            connected = set((relationship.from_entity_id, relationship.to_entity_id)) & set(
                input_state.joined_entities
            )
            if len(connected) != 1:
                raise SemanticEngineError(
                    SemanticFailureCode.AMBIGUOUS_RELATIONSHIP_PATH,
                    f"relationship is not connected to the logical result: {relationship_ids[0]}",
                )
            connected_entity = next(iter(connected))
            target_entity = next(
                entity
                for entity in (relationship.from_entity_id, relationship.to_entity_id)
                if entity != connected_entity
            )
            left_attribute = relationship.from_attribute_id
            right_attribute = relationship.to_attribute_id
            if connected_entity == relationship.to_entity_id:
                left_attribute, right_attribute = right_attribute, left_attribute
            joins.append(
                PlannedJoin(
                    target_source=EntityRelationSource(entity_id=target_entity),
                    join_keys=(
                        JoinKey(
                            left=self._source_attribute_ref(current, left_attribute),
                            right=AttributeRef(
                                attribute_id=right_attribute,
                                relation_ref=target_entity,
                            ),
                        ),
                    ),
                    join_type=join_type,
                )
            )
        else:
            joins.extend(
                PlannedJoin(relationship_id=relationship_id, join_type=join_type)
                for relationship_id in relationship_ids
            )
        target_attrs = tuple(
            item
            for item in self.mapping.attributes
            if item.entity_id == target.entity_id and item.attribute_id in self._needed_attributes
        )
        select = list(current.query.select[: len(current.exports)])
        exports = list(current.exports)
        for item in target_attrs:
            select.append(
                IRSelectItem(
                    position=len(select),
                    expression=AttributeRef(
                        attribute_id=item.attribute_id, relation_ref=target.entity_id
                    ),
                    alias=item.physical_column,
                )
            )
            exports.append((item.attribute_id, item.attribute_id))
        select.extend(
            IRSelectItem(
                position=len(select) + offset,
                expression=AttributeRef(
                    attribute_id=attribute_id,
                    relation_ref=_relation_ref(current.query.from_source),
                ),
                alias=self._hidden_name(offset),
            )
            for offset, attribute_id in enumerate(input_state.hidden_attributes)
        )
        population_mode = (
            PopulationInclusion.PRESERVE_BASE
            if step.mode == LogicalRelationMode.PRESERVE_LEFT
            else PopulationInclusion.MATCHING_RELATIONS
        )
        population = self._population(
            input_state,
            mode=population_mode,
            relationships=input_state.relationships + relationship_ids,
            # RELATE is an explicit logical population expansion.  The
            # canonical compiler still validates the resolved path and join
            # contract; the logical language does not expose a physical
            # fanout switch to the model.
            fanout=True,
        )
        query = self._query(
            source=current.query.from_source,
            select=tuple(select),
            joins=tuple(joins),
            where=current.query.where,
            group_by=current.query.group_by,
            order_by=current.query.order_by,
            population=population,
            ctes=current.query.ctes,
            derived_relations=current.query.derived_relations,
        )
        self._provenance.append(
            {
                "canonical_field": "joins",
                "owner": "SERVER_DERIVED",
                "derived_from": f"logical RELATE step {index} + RelationshipGraph",
                "rule": "unique authorized safe path",
            }
        )
        return _RelationState(
            index,
            query,
            tuple(exports),
            input_state.base_entities,
            tuple(dict.fromkeys((*input_state.joined_entities, target.entity_id))),
            population_mode,
            input_state.relationships + relationship_ids,
            self._visible_results(input_state, index, tuple(exports)),
            input_state.hidden_attributes,
            input_state.carried_results,
        )

    def _resolve_result_relate(
        self, index: int, step: LogicalRelate, input_state: _RelationState
    ) -> _RelationState:
        assert step.result_step is not None
        result_step_index = step.result_step
        result_state = self._resolve_step(result_step_index)
        current_entities = set(input_state.joined_entities or input_state.base_entities)
        result_entities = set(result_state.joined_entities or result_state.base_entities)
        # Relationship matching is based on logical result exports.  Hidden
        # server keys retained inside a result must not manufacture additional
        # candidate relationships and turn an otherwise unique relation into
        # an ambiguity.
        result_attributes = self._visible_query_physical_attributes(result_state)
        candidates: list[tuple[Any | None, str, str, str, str, int]] = []
        for common_entity in sorted(current_entities & result_entities):
            entity = self.mapping.entity(common_entity)
            for attribute_id in entity.key_attribute_ids:
                if attribute_id in result_attributes:
                    candidates.append(
                        (
                            None,
                            common_entity,
                            common_entity,
                            attribute_id,
                            attribute_id,
                            result_attributes[attribute_id],
                        )
                    )
        for current_entity in current_entities:
            for relationship in self.mapping.relationships_for(current_entity):
                if (
                    relationship.from_entity_id in result_entities
                    and relationship.to_entity_id == current_entity
                ):
                    result_attribute = relationship.from_attribute_id
                    if result_attribute in result_attributes:
                        candidates.append(
                            (
                                relationship,
                                current_entity,
                                relationship.from_entity_id,
                                relationship.to_attribute_id,
                                result_attribute,
                                result_attributes[result_attribute],
                            )
                        )
                elif (
                    relationship.to_entity_id in result_entities
                    and relationship.from_entity_id == current_entity
                ):
                    result_attribute = relationship.to_attribute_id
                    if result_attribute in result_attributes:
                        candidates.append(
                            (
                                relationship,
                                current_entity,
                                relationship.to_entity_id,
                                relationship.from_attribute_id,
                                result_attribute,
                                result_attributes[result_attribute],
                            )
                        )
        for current_entity in current_entities:
            for left in self.mapping.relationships_for(current_entity):
                shared_entity = (
                    left.to_entity_id
                    if left.from_entity_id == current_entity
                    else left.from_entity_id
                )
                for result_entity in result_entities:
                    if result_entity == current_entity:
                        continue
                    for right in self.mapping.relationships_for(result_entity):
                        right_shared = (
                            right.to_entity_id
                            if right.from_entity_id == result_entity
                            else right.from_entity_id
                        )
                        if right_shared != shared_entity:
                            continue
                        result_attribute = (
                            right.from_attribute_id
                            if right.from_entity_id == result_entity
                            else right.to_attribute_id
                        )
                        if result_attribute not in result_attributes:
                            continue
                        current_attribute = (
                            left.from_attribute_id
                            if left.from_entity_id == current_entity
                            else left.to_attribute_id
                        )
                        candidates.append(
                            (
                                None,
                                current_entity,
                                result_entity,
                                current_attribute,
                                result_attribute,
                                result_attributes[result_attribute],
                            )
                        )
        if not candidates:
            raise SemanticEngineError(
                SemanticFailureCode.NO_RELATIONSHIP_PATH,
                "no authorized relationship between logical results",
            )
        direct_candidates = [candidate for candidate in candidates if candidate[0] is not None]
        if direct_candidates:
            candidates = direct_candidates
        shortest = candidates
        if len(shortest) != 1:
            raise SemanticEngineError(
                SemanticFailureCode.AMBIGUOUS_RELATIONSHIP_PATH,
                "ambiguous relationship between logical results",
            )
        (
            _relationship,
            current_entity,
            _result_entity,
            current_attribute,
            result_attribute,
            result_position,
        ) = shortest[0]
        if result_position >= len(result_state.exports):
            raise SemanticEngineError(
                SemanticFailureCode.INVALID_SCOPE_REFERENCE,
                f"logical result does not export {result_attribute}",
            )
        relation_id = f"logical_result_{result_step_index}"
        target_source = CTERelationSource(cte_id=relation_id, source_id=relation_id)
        result_ctes = self._flatten_ctes(result_state.query.ctes)
        cte = CommonTableExpression(
            cte_id=relation_id,
            query=result_state.query.model_copy(update={"ctes": ()}),
            exported_attributes=tuple(
                self._export_attribute(attribute_id, name, position)
                for position, (attribute_id, name) in enumerate(result_state.exports)
            ),
        )
        target_attribute_id = result_state.exports[result_position][0]
        join = PlannedJoin(
            relationship_id=None,
            target_source=target_source,
            join_keys=(
                JoinKey(
                    left=self._source_attribute_ref(input_state, current_attribute),
                    right=AttributeRef(
                        attribute_id=target_attribute_id,
                        relation_ref=relation_id,
                    ),
                ),
            ),
            join_type=(
                JoinType.LEFT if step.mode == LogicalRelationMode.PRESERVE_LEFT else JoinType.INNER
            ),
        )
        query = input_state.query.model_copy(
            update={
                "joins": (*input_state.query.joins, join),
                "ctes": (*input_state.query.ctes, *result_ctes, cte),
            }
        )
        visible_count = len(input_state.exports)
        carried_count = len(input_state.carried_results)
        visible_select = input_state.query.select[:visible_count]
        carried_select = input_state.query.select[visible_count : visible_count + carried_count]
        hidden_select = input_state.query.select[visible_count + carried_count :]
        result_select = tuple(
            IRSelectItem(
                position=visible_count + carried_count + position,
                expression=AttributeRef(attribute_id=export[0], relation_ref=relation_id),
                alias=self._carried_result_name(result_step_index, position),
            )
            for position, export in enumerate(result_state.exports)
        )
        query = query.model_copy(
            update={
                "select": visible_select
                + carried_select
                + result_select
                + tuple(
                    item.model_copy(
                        update={
                            "position": visible_count + carried_count + len(result_select) + offset
                        }
                    )
                    for offset, item in enumerate(hidden_select)
                )
            }
        )
        visible = self._visible_results(input_state, index, input_state.exports)
        carried_results = list(input_state.carried_results)
        for position, export in enumerate(result_state.exports):
            visible[(result_step_index, position)] = (export[0], relation_id)
            carried_results.append((result_step_index, position, export[0]))
        return _RelationState(
            index=index,
            query=query,
            exports=input_state.exports,
            base_entities=input_state.base_entities,
            joined_entities=tuple(dict.fromkeys((*input_state.joined_entities, *result_entities))),
            population_mode=input_state.population_mode,
            relationships=input_state.relationships,
            visible_results=visible,
            hidden_attributes=input_state.hidden_attributes,
            carried_results=tuple(carried_results),
        )

    def _source_attribute_ref(self, state: _RelationState, attribute_id: str) -> AttributeRef:
        """Return the current-scope export carrying a semantic attribute.

        A relationship may be requested after PROJECT/TOP/another nested
        result.  In that case the physical semantic key is no longer a column
        name in the current scope; it is carried by a server-created output
        slot.  Follow only direct attribute lineage, never expression text or
        aliases, and fail closed when the key was not exported.
        """

        source_ref = _relation_ref(state.query.from_source)
        for position, item in enumerate(state.query.select):
            if self._attribute_lineage_contains(
                state.query, item.expression, attribute_id, set(), state.query.ctes
            ):
                if position < len(state.exports):
                    exported_id = state.exports[position][0]
                else:
                    hidden_position = position - len(state.exports)
                    if hidden_position >= len(state.hidden_attributes):
                        continue
                    exported_id = state.hidden_attributes[hidden_position]
                return AttributeRef(attribute_id=exported_id, relation_ref=source_ref)
        raise SemanticEngineError(
            SemanticFailureCode.INVALID_SCOPE_REFERENCE,
            f"server-required relationship attribute is not exported: {attribute_id}",
        )

    @staticmethod
    def _attribute_lineage_contains(
        query: SemanticQueryIR,
        expression: Expression,
        target_attribute_id: str,
        seen: set[tuple[str, str, int]],
        available_ctes: tuple[CommonTableExpression, ...] = (),
    ) -> bool:
        if not isinstance(expression, AttributeRef):
            return False
        if expression.attribute_id == target_attribute_id:
            return True
        if not expression.attribute_id.startswith("output:"):
            return False
        relation_ref = expression.relation_ref
        if relation_ref is None:
            return False
        definition = next(
            (item for item in (*query.ctes, *available_ctes) if item.cte_id == relation_ref),
            None,
        )
        if definition is None:
            return False
        exported = next(
            (
                item
                for item in definition.exported_attributes
                if item.attribute_id == expression.attribute_id
            ),
            None,
        )
        if exported is None or exported.position >= len(definition.query.select):
            return False
        marker = (relation_ref, expression.attribute_id, exported.position)
        if marker in seen:
            return False
        seen.add(marker)
        return LogicalPlanResolver._attribute_lineage_contains(
            definition.query,
            definition.query.select[exported.position].expression,
            target_attribute_id,
            seen,
            available_ctes,
        )

    @staticmethod
    def _query_physical_attributes(query: SemanticQueryIR) -> dict[str, int]:
        result: dict[str, int] = {}
        definitions: dict[str, CommonTableExpression] = {}

        def collect(current: SemanticQueryIR) -> None:
            for definition in current.ctes:
                definitions.setdefault(definition.cte_id, definition)
                collect(definition.query)

        collect(query)

        def visit(current: SemanticQueryIR, seen: set[tuple[int, int]]) -> None:
            for item in current.select:
                expression = item.expression
                if not isinstance(expression, AttributeRef):
                    continue
                if expression.attribute_id.startswith("attribute:"):
                    result.setdefault(expression.attribute_id, item.position)
                    continue
                if not expression.attribute_id.startswith("output:"):
                    continue
                key = (id(current), item.position)
                if key in seen:
                    continue
                seen.add(key)
                definition = definitions.get(expression.relation_ref or "")
                if definition is not None:
                    export = next(
                        (
                            value
                            for value in definition.exported_attributes
                            if value.attribute_id == expression.attribute_id
                        ),
                        None,
                    )
                    if export is not None:
                        nested = definition.query
                        if export.position < len(nested.select):
                            nested_expression = nested.select[export.position].expression
                            if isinstance(nested_expression, AttributeRef):
                                if nested_expression.attribute_id.startswith("attribute:"):
                                    result.setdefault(nested_expression.attribute_id, item.position)
                                else:
                                    visit(nested, seen)
                            else:
                                visit(nested, seen)

        visit(query, set())
        return result

    @staticmethod
    def _visible_query_physical_attributes(state: _RelationState) -> dict[str, int]:
        """Find physical attributes carried by visible result slots only."""

        definitions: dict[str, CommonTableExpression] = {}

        def collect(query: SemanticQueryIR) -> None:
            for definition in query.ctes:
                definitions.setdefault(definition.cte_id, definition)
                collect(definition.query)

        collect(state.query)
        result: dict[str, int] = {}

        def resolve(
            query: SemanticQueryIR,
            expression: Expression,
            position: int,
            seen: set[tuple[str, int]],
        ) -> None:
            if not isinstance(expression, AttributeRef):
                return
            if expression.attribute_id.startswith("attribute:"):
                result.setdefault(expression.attribute_id, position)
                return
            relation_ref = expression.relation_ref
            if relation_ref is None:
                return
            definition = definitions.get(relation_ref)
            if definition is None:
                return
            exported = next(
                (
                    item
                    for item in definition.exported_attributes
                    if item.attribute_id == expression.attribute_id
                ),
                None,
            )
            if exported is None or exported.position >= len(definition.query.select):
                return
            marker = (relation_ref, exported.position)
            if marker in seen:
                return
            seen.add(marker)
            resolve(
                definition.query,
                definition.query.select[exported.position].expression,
                position,
                seen,
            )

        for position, item in enumerate(state.query.select[: len(state.exports)]):
            resolve(state.query, item.expression, position, set())
        return result

    def _resolve_filter(self, index: int, step: LogicalFilter) -> _RelationState:
        state = self._operation_input(index, step.input_step)
        predicate = self._expression(step.predicate, state, step.input_step)
        query = state.query.model_copy(update={"where": predicate})
        query = self._merge_expression_ctes(query, (predicate,))
        return _RelationState(
            index,
            query,
            state.exports,
            state.base_entities,
            state.joined_entities,
            state.population_mode,
            state.relationships,
            self._visible_results(state, index, state.exports),
            state.hidden_attributes,
            state.carried_results,
        )

    def _resolve_project(self, index: int, step: LogicalProject) -> _RelationState:
        state = self._operation_input(index, step.input_step)
        select = tuple(
            IRSelectItem(
                position=position,
                expression=self._project_expression(output.expression, state, step.input_step),
                alias=f"output_{position}",
            )
            for position, output in enumerate(step.outputs)
        )
        exports = tuple(
            (f"output:logical:{index}:{position}", f"output_{position}")
            for position in range(len(select))
        )
        hidden = state.hidden_attributes
        hidden_select = tuple(
            IRSelectItem(
                position=len(select) + offset,
                expression=AttributeRef(
                    attribute_id=attribute_id,
                    relation_ref=_relation_ref(state.query.from_source),
                ),
                alias=self._hidden_name(offset),
            )
            for offset, attribute_id in enumerate(hidden)
        )
        query = state.query.model_copy(update={"select": select + hidden_select})
        query = self._merge_expression_ctes(query, tuple(item.expression for item in select))
        self._provenance.append(
            {
                "canonical_field": "output.position",
                "owner": "SERVER_DERIVED",
                "derived_from": f"PROJECT step {index}",
                "rule": "ordered output list",
            }
        )
        return _RelationState(
            index,
            query,
            exports,
            state.base_entities,
            state.joined_entities,
            state.population_mode,
            state.relationships,
            self._visible_results(state, index, exports),
            hidden,
            state.carried_results,
        )

    def _project_expression(
        self, value: LogicalExpression, state: _RelationState, input_step: int
    ) -> Expression:
        """Inline direct input slots when a nested scalar stays in one scope."""

        if (
            isinstance(value, LogicalResult)
            and value.step == input_step
            and value.slot < len(state.query.select)
        ):
            return state.query.select[value.slot].expression
        return self._expression(value, state, input_step)

    def _resolve_aggregate(self, index: int, step: LogicalAggregate) -> _RelationState:
        state = self._operation_input(index, step.input_step)
        groups = tuple(self._expression(item, state, step.input_step) for item in step.group_by)
        measures = tuple(
            self._aggregate_expression(measure, state, step.input_step) for measure in step.measures
        )
        select = tuple(
            [
                IRSelectItem(position=i, expression=expr, alias=f"group_{i}")
                for i, expr in enumerate(groups)
            ]
            + [
                IRSelectItem(position=len(groups) + i, expression=expr, alias=f"measure_{i}")
                for i, expr in enumerate(measures)
            ]
        )
        exports = tuple(
            [(f"output:logical:{index}:{i}", f"group_{i}") for i in range(len(groups))]
            + [
                (f"output:logical:{index}:{len(groups) + i}", f"measure_{i}")
                for i in range(len(measures))
            ]
        )
        query = state.query.model_copy(update={"select": select, "group_by": groups})
        query = self._merge_expression_ctes(query, (*groups, *measures))
        self._provenance.append(
            {
                "canonical_field": "grain",
                "owner": "SERVER_DERIVED",
                "derived_from": f"AGGREGATE step {index}",
                "rule": "grouping references define aggregation grain",
            }
        )
        return _RelationState(
            index,
            query,
            exports,
            state.base_entities,
            state.joined_entities,
            state.population_mode,
            state.relationships,
            self._visible_results(state, index, exports),
            (),
        )

    def _resolve_compute(self, index: int, step: LogicalCompute) -> _RelationState:
        state = self._operation_input(index, step.input_step)
        computed = tuple(
            self._expression(output.expression, state, step.input_step) for output in step.outputs
        )
        visible_select = state.query.select[: len(state.exports)]
        select = visible_select + tuple(
            IRSelectItem(position=len(visible_select) + i, expression=expr, alias=f"computed_{i}")
            for i, expr in enumerate(computed)
        )
        exports = state.exports + tuple(
            (f"output:logical:{index}:{len(state.exports) + i}", f"computed_{i}")
            for i in range(len(computed))
        )
        hidden_select = tuple(
            IRSelectItem(
                position=len(select) + offset,
                expression=AttributeRef(
                    attribute_id=attribute_id,
                    relation_ref=_relation_ref(state.query.from_source),
                ),
                alias=self._hidden_name(offset),
            )
            for offset, attribute_id in enumerate(state.hidden_attributes)
        )
        query = state.query.model_copy(update={"select": select + hidden_select})
        query = self._merge_expression_ctes(query, computed)
        return _RelationState(
            index,
            query,
            exports,
            state.base_entities,
            state.joined_entities,
            state.population_mode,
            state.relationships,
            self._visible_results(state, index, exports),
            state.hidden_attributes,
            state.carried_results,
        )

    def _resolve_window(self, index: int, step: LogicalWindow) -> _RelationState:
        state = self._operation_input(index, step.input_step)
        additions: list[IRSelectItem] = []
        for offset, output in enumerate(step.outputs):
            args = tuple(
                self._expression(item, state, step.input_step) for item in output.arguments
            )
            partition = tuple(
                self._expression(item, state, step.input_step) for item in output.partition_by
            )
            order = tuple(
                WindowOrder(
                    expression=self._expression(item.expression, state, step.input_step),
                    direction=item.direction,
                )
                for item in output.order_by
            )
            additions.append(
                IRSelectItem(
                    position=len(state.query.select[: len(state.exports)]) + offset,
                    expression=WindowExpression(
                        function=output.function.value,
                        arguments=args,
                        partition_by=partition,
                        order_by=order,
                    ),
                    alias=f"window_{offset}",
                )
            )
        visible_select = state.query.select[: len(state.exports)]
        hidden_additions = tuple(
            IRSelectItem(
                position=len(visible_select) + len(additions) + offset,
                expression=AttributeRef(
                    attribute_id=attribute_id,
                    relation_ref=_relation_ref(state.query.from_source),
                ),
                alias=self._hidden_name(offset),
            )
            for offset, attribute_id in enumerate(state.hidden_attributes)
        )
        query = state.query.model_copy(
            update={"select": visible_select + tuple(additions) + hidden_additions}
        )
        query = self._merge_expression_ctes(query, tuple(item.expression for item in additions))
        exports = state.exports + tuple(
            (f"output:logical:{index}:{len(state.exports) + i}", f"window_{i}")
            for i in range(len(additions))
        )
        return _RelationState(
            index,
            query,
            exports,
            state.base_entities,
            state.joined_entities,
            state.population_mode,
            state.relationships,
            self._visible_results(state, index, exports),
            state.hidden_attributes,
            state.carried_results,
        )

    def _resolve_sort(self, index: int, step: LogicalSort) -> _RelationState:
        state = self._operation_input(index, step.input_step)
        order = tuple(
            OrderSpec(
                expression=self._expression(item.expression, state, step.input_step),
                direction=item.direction,
            )
            for item in step.order_by
        )
        return _RelationState(
            index,
            state.query.model_copy(update={"order_by": order}),
            state.exports,
            state.base_entities,
            state.joined_entities,
            state.population_mode,
            state.relationships,
            self._visible_results(state, index, state.exports),
            state.hidden_attributes,
            state.carried_results,
        )

    def _resolve_top(self, index: int, step: LogicalTop) -> _RelationState:
        state = self._from_input(index, self._resolve_step(step.input_step))
        return _RelationState(
            index,
            state.query.model_copy(update={"limit": step.limit, "offset": step.offset}),
            state.exports,
            state.base_entities,
            state.joined_entities,
            state.population_mode,
            state.relationships,
            self._visible_results(state, index, state.exports),
            state.hidden_attributes,
            state.carried_results,
        )

    def _expression(
        self, value: LogicalExpression, state: _RelationState, input_step: int
    ) -> Expression:
        if isinstance(value, LogicalOuterAttribute):
            if value.scope_depth > len(self._scope_stack):
                raise SemanticEngineError(
                    SemanticFailureCode.INVALID_SCOPE_REFERENCE,
                    "outer attribute scope depth exceeds enclosing logical scopes",
                )
            outer_state = self._scope_stack[-value.scope_depth]
            try:
                self.mapping.attribute(value.attribute_id)
            except SemanticMappingError as error:
                raise SemanticEngineError(
                    SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, str(error)
                ) from error
            try:
                return self._source_attribute_ref(outer_state, value.attribute_id)
            except SemanticEngineError as error:
                raise SemanticEngineError(
                    SemanticFailureCode.INVALID_SCOPE_REFERENCE,
                    f"outer attribute is not visible in scope depth {value.scope_depth}: "
                    f"{value.attribute_id}",
                ) from error
        if isinstance(value, LogicalAttribute):
            catalog_attribute = None
            try:
                catalog_attribute = self.mapping.attribute(value.attribute_id)
            except SemanticMappingError as error:
                # Output attributes are server-created relation exports, not catalog attributes.
                if value.attribute_id not in state.export_map:
                    raise SemanticEngineError(
                        SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, str(error)
                    ) from error
            relation_ref = (
                catalog_attribute.entity_id
                if catalog_attribute is not None
                and catalog_attribute.entity_id in state.joined_entities
                and isinstance(state.query.from_source, EntityRelationSource)
                else state.query.from_source.source_id
                if isinstance(state.query.from_source, EntityRelationSource)
                else getattr(state.query.from_source, "relation_id", None)
            )
            return AttributeRef(attribute_id=value.attribute_id, relation_ref=relation_ref)
        if isinstance(value, LogicalResult):
            reference = state.visible_results.get((value.step, value.slot))
            if reference is None and value.step == input_step and value.slot < len(state.exports):
                reference = (
                    state.exports[value.slot][0],
                    _relation_ref(state.query.from_source),
                )
            if reference is None:
                raise SemanticEngineError(
                    SemanticFailureCode.INVALID_SCOPE_REFERENCE, "result slot is not exported"
                )
            return AttributeRef(attribute_id=reference[0], relation_ref=reference[1])
        if isinstance(value, LogicalLiteral):
            literal: Any = value.value
            if value.value_type == "decimal" and isinstance(literal, str):
                literal = Decimal(literal)
            return LiteralExpression(value=literal, value_type=value.value_type)
        if isinstance(value, LogicalInterval):
            return IntervalExpression(amount=value.amount, unit=value.unit)
        if isinstance(value, LogicalStar):
            return StarExpression()
        if isinstance(value, LogicalBinary):
            return BinaryExpression(
                operator=BinaryOperator(value.operator.value),
                left=self._expression(value.left, state, input_step),
                right=self._expression(value.right, state, input_step),
            )
        if isinstance(value, LogicalFunctionCall):
            if value.function == LogicalFunction.CAST and len(value.arguments) == 2:
                target = value.arguments[1]
                if isinstance(target, LogicalLiteral) and isinstance(target.value, str):
                    return CastExpression(
                        expression=self._expression(value.arguments[0], state, input_step),
                        target_type=target.value,
                    )
            return FunctionExpression(
                function=SemanticFunction(value.function.value),
                arguments=tuple(
                    self._expression(item, state, input_step) for item in value.arguments
                ),
            )
        if isinstance(value, LogicalBoolean):
            return SemanticLogicalExpression(
                operator=value.operator,
                terms=tuple(self._expression(item, state, input_step) for item in value.terms),
            )
        if isinstance(value, LogicalNot):
            return NotExpression(expression=self._expression(value.expression, state, input_step))
        if isinstance(value, LogicalBetween):
            from app.semantics.semantic_query import BetweenExpression

            return BetweenExpression(
                expression=self._expression(value.expression, state, input_step),
                low=self._expression(value.low, state, input_step),
                high=self._expression(value.high, state, input_step),
            )
        if isinstance(value, LogicalIn):
            return InExpression(
                expression=self._expression(value.expression, state, input_step),
                values=tuple(self._expression(item, state, input_step) for item in value.values),
                negated=value.negated,
            )
        if isinstance(value, LogicalNullTest):
            return IsNullExpression(
                expression=self._expression(value.expression, state, input_step),
                negated=value.negated,
            )
        if isinstance(value, LogicalCase):
            return CaseExpression(
                branches=tuple(
                    CaseBranch(
                        when=self._expression(item.when, state, input_step),
                        then=self._expression(item.then, state, input_step),
                    )
                    for item in value.branches
                ),
                default=self._expression(value.default, state, input_step)
                if value.default is not None
                else None,
            )
        if isinstance(value, LogicalScalarResult):
            self._scope_stack.append(state)
            try:
                nested = self._resolve_step(value.step)
            finally:
                self._scope_stack.pop()
            if len(nested.query.select) != 1:
                raise SemanticEngineError(
                    SemanticFailureCode.INVALID_SCOPE_REFERENCE,
                    "scalar result must have one output",
                )
            return ScalarSubqueryExpression(query=nested.query)
        raise SemanticEngineError(
            SemanticFailureCode.UNSUPPORTED_SEMANTIC_ADDITION, type(value).__name__
        )

    def _aggregate_expression(
        self, measure: LogicalMeasure, state: _RelationState, input_step: int
    ) -> Expression:
        expression = self._expression(measure.expression, state, input_step)
        if measure.function == LogicalAggregateFunction.PERCENTILE_CONT:
            if measure.percentile is None:
                raise SemanticEngineError(
                    SemanticFailureCode.INVALID_AGGREGATION,
                    "PERCENTILE_CONT requires a percentile",
                )
            return OrderedAggregateExpression(
                function="PERCENTILE_CONT",
                percentile=self._expression(measure.percentile, state, input_step),
                expression=expression,
            )
        return AggregateExpression(
            function=measure.function.value,
            expression=expression,
            distinct=measure.distinct,
            filter=(
                self._expression(measure.filter, state, input_step)
                if measure.filter is not None
                else None
            ),
        )

    def _merge_expression_ctes(
        self, query: SemanticQueryIR, expressions: tuple[Expression, ...]
    ) -> SemanticQueryIR:
        """Hoist scalar-result dependencies into the enclosing query scope.

        Logical scalar references are dataflow references, not model-owned
        CTE names.  The compiler nevertheless needs every referenced CTE in
        the enclosing namespace.  Hoisting the already-resolved definitions
        is deterministic and keeps the logical plan free of scope mechanics.
        """

        discovered: list[CommonTableExpression] = []
        for expression in expressions:
            self._collect_expression_ctes(expression, discovered)
        if not discovered:
            return query
        existing = {item.cte_id for item in query.ctes}
        additions = tuple(item for item in discovered if item.cte_id not in existing)
        if not additions:
            return query
        return query.model_copy(update={"ctes": (*query.ctes, *additions)})

    def _collect_expression_ctes(self, value: Any, result: list[CommonTableExpression]) -> None:
        if isinstance(value, ScalarSubqueryExpression):
            for definition in self._flatten_ctes(value.query.ctes):
                if definition.cte_id not in {item.cte_id for item in result}:
                    result.append(definition)
            self._collect_expression_ctes(value.query, result)
            return
        if isinstance(value, BaseModel):
            for field_name in value.__class__.model_fields:
                self._collect_expression_ctes(getattr(value, field_name), result)
        elif isinstance(value, (tuple, list)):
            for item in value:
                self._collect_expression_ctes(item, result)

    def _query(
        self,
        *,
        source: Any,
        select: tuple[IRSelectItem, ...],
        population: PopulationContract,
        joins: tuple[PlannedJoin, ...] = (),
        where: Expression | None = None,
        group_by: tuple[Expression, ...] = (),
        order_by: tuple[OrderSpec, ...] = (),
        limit: int | None = None,
        offset: int | None = None,
        ctes: tuple[CommonTableExpression, ...] = (),
        derived_relations: tuple[DerivedRelation, ...] = (),
    ) -> SemanticQueryIR:
        return SemanticQueryIR(
            database_id=self.database_id,
            from_source=source,
            joins=joins,
            select=select,
            where=where,
            group_by=group_by,
            order_by=order_by,
            limit=limit,
            offset=offset,
            population_contract=population,
            ctes=ctes,
            derived_relations=derived_relations,
        )

    def _population(
        self,
        state: _RelationState,
        *,
        mode: PopulationInclusion | None = None,
        relationships: tuple[str, ...] | None = None,
        fanout: bool | None = None,
    ) -> PopulationContract:
        return PopulationContract(
            base_entity_ids=state.base_entities,
            required_relationship_ids=(),
            inclusion_mode=mode or state.population_mode,
            fanout_allowed=fanout if fanout is not None else bool(state.relationships),
        )

    @staticmethod
    def _export_attribute(attribute_id: str, name: str, position: int) -> Any:
        from app.semantics.semantic_query import ExportedAttribute

        return ExportedAttribute(attribute_id=attribute_id, output_name=name, position=position)

    @staticmethod
    def _hidden_name(position: int) -> str:
        return f"__m31_hidden_{position}"

    @staticmethod
    def _carried_result_name(step: int, slot: int) -> str:
        return f"__m31_result_{step}_{slot}"

    @staticmethod
    def _flatten_ctes(
        ctes: tuple[CommonTableExpression, ...],
    ) -> tuple[CommonTableExpression, ...]:
        """Expose prior logical results as one deterministic CTE namespace."""

        flattened: list[CommonTableExpression] = []
        seen: set[str] = set()

        def visit(items: tuple[CommonTableExpression, ...]) -> None:
            for item in items:
                visit(item.query.ctes)
                if item.cte_id in seen:
                    continue
                seen.add(item.cte_id)
                flattened.append(
                    item.model_copy(update={"query": item.query.model_copy(update={"ctes": ()})})
                )

        visit(ctes)
        return tuple(flattened)


def _relation_ref(source: Any) -> str | None:
    if source is None:
        return None
    return getattr(source, "source_id", None) or getattr(source, "relation_id", None)


def _logical_attribute_ids(logical: LogicalQueryPlanV1) -> set[str]:
    result: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, (LogicalAttribute, LogicalOuterAttribute)):
            result.add(value.attribute_id)
            return
        if isinstance(value, BaseModel):
            for name in value.__class__.model_fields:
                visit(getattr(value, name))
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit(logical)
    return result


for _model in (
    LogicalAttribute,
    LogicalOuterAttribute,
    LogicalResult,
    LogicalLiteral,
    LogicalInterval,
    LogicalStar,
    LogicalBinary,
    LogicalFunctionCall,
    LogicalBoolean,
    LogicalNot,
    LogicalBetween,
    LogicalIn,
    LogicalNullTest,
    LogicalCaseBranch,
    LogicalCase,
    LogicalScalarResult,
    LogicalOutput,
    LogicalOrder,
    LogicalMeasure,
    LogicalWindowOutput,
    LogicalScan,
    LogicalRelate,
    LogicalFilter,
    LogicalProject,
    LogicalAggregate,
    LogicalCompute,
    LogicalWindow,
    LogicalSort,
    LogicalTop,
    LogicalQueryPlanV1,
):
    _model.model_rebuild()
