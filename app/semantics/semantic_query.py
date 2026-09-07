"""Canonical, SQL-free semantic query contracts.

The objects in this module are untrusted semantic intent.  They deliberately
carry identifiers and typed expressions, never SQL text or join predicates.
Physical resolution is performed by :mod:`app.semantics.semantic_mapping`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any
from typing import Literal as TypingLiteral

from pydantic import BaseModel, ConfigDict, Field, model_validator

SemanticEntityId = Annotated[str, Field(min_length=1, max_length=128)]
SemanticAttributeId = Annotated[str, Field(min_length=1, max_length=256)]
SemanticRelationshipId = Annotated[str, Field(min_length=1, max_length=256)]
SemanticFunctionId = Annotated[str, Field(min_length=1, max_length=64)]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExpectedCardinality(StrEnum):
    ONE_ROW = "ONE_ROW"
    MANY_ROWS = "MANY_ROWS"
    UNKNOWN = "UNKNOWN"


class PopulationInclusion(StrEnum):
    BASE_ENTITY = "BASE_ENTITY"
    MATCHING_RELATIONS = "MATCHING_RELATIONS"
    PRESERVE_BASE = "PRESERVE_BASE"


class JoinType(StrEnum):
    INNER = "INNER"
    LEFT = "LEFT"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class PredicateOperator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    AND = "AND"
    OR = "OR"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


class BinaryOperator(StrEnum):
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


class SemanticFunction(StrEnum):
    COALESCE = "COALESCE"
    NULLIF = "NULLIF"
    CAST = "CAST"
    ROUND = "ROUND"
    ABS = "ABS"
    GREATEST = "GREATEST"
    LEAST = "LEAST"
    POWER = "POWER"
    SQRT = "SQRT"
    LN = "LN"
    LOG = "LOG"
    EXP = "EXP"
    TRIM = "TRIM"
    CONCAT_WS = "CONCAT_WS"
    TO_CHAR = "TO_CHAR"
    ARRAY_TO_STRING = "ARRAY_TO_STRING"
    ROW_TO_JSON = "ROW_TO_JSON"
    JSON_BUILD_OBJECT = "JSON_BUILD_OBJECT"
    JSONB_BUILD_OBJECT = "JSONB_BUILD_OBJECT"
    ARRAY = "ARRAY"
    ARRAY_REMOVE = "ARRAY_REMOVE"
    CARDINALITY = "CARDINALITY"
    UNNEST = "UNNEST"
    DATE_TRUNC = "DATE_TRUNC"
    EXTRACT = "EXTRACT"
    AGE = "AGE"
    TIMESTAMP_TRUNC = "TIMESTAMP_TRUNC"
    CURRENT_DATE = "CURRENT_DATE"
    CURRENT_TIME = "CURRENT_TIME"
    CURRENT_TIMESTAMP = "CURRENT_TIMESTAMP"
    LOCALTIME = "LOCALTIME"
    LOCALTIMESTAMP = "LOCALTIMESTAMP"
    NOW = "NOW"
    STATEMENT_TIMESTAMP = "STATEMENT_TIMESTAMP"
    TRANSACTION_TIMESTAMP = "TRANSACTION_TIMESTAMP"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    ARRAY_AGG = "ARRAY_AGG"
    STRING_AGG = "STRING_AGG"
    JSON_AGG = "JSON_AGG"
    JSONB_AGG = "JSONB_AGG"
    JSON_OBJECT_AGG = "JSON_OBJECT_AGG"
    JSONB_OBJECT_AGG = "JSONB_OBJECT_AGG"
    CORR = "CORR"
    REGR_SLOPE = "REGR_SLOPE"
    STDDEV = "STDDEV"
    PERCENTILE_CONT = "PERCENTILE_CONT"
    JSONB_EXTRACT_PATH_TEXT = "JSONB_EXTRACT_PATH_TEXT"
    JSONB_EXTRACT_SCALAR = "JSONB_EXTRACT_SCALAR"
    JSON_EXTRACT = "JSON_EXTRACT"
    JSON_EXTRACT_SCALAR = "JSON_EXTRACT_SCALAR"
    STRING_TO_ARRAY = "STRING_TO_ARRAY"
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


class CalculationKind(StrEnum):
    NONE = "NONE"
    COUNT = "COUNT"
    SUM = "SUM"
    AVERAGE = "AVERAGE"
    DIFFERENCE = "DIFFERENCE"
    RATIO = "RATIO"
    PERCENTAGE = "PERCENTAGE"
    RANK = "RANK"
    DERIVED = "DERIVED"


class ZeroDenominatorPolicy(StrEnum):
    NULL = "NULL"
    ZERO = "ZERO"
    REJECT = "REJECT"


ScalarValue = str | int | float | bool | date | datetime | Decimal | None


class AttributeRef(_Strict):
    kind: TypingLiteral["attribute"] = "attribute"
    attribute_id: SemanticAttributeId
    # None means the current/base entity in a top-level query.  Nested query
    # references must name a query-local relation source explicitly.
    relation_ref: str | None = Field(default=None, min_length=1, max_length=128)


class LiteralExpression(_Strict):
    kind: TypingLiteral["literal"] = "literal"
    value: ScalarValue
    value_type: TypingLiteral[
        "null", "string", "integer", "decimal", "boolean", "date", "timestamp"
    ]


class IntervalExpression(_Strict):
    """A bounded PostgreSQL interval literal used by temporal predicates."""

    kind: TypingLiteral["interval"] = "interval"
    amount: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    unit: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z_]+$")


class StarExpression(_Strict):
    kind: TypingLiteral["star"] = "star"


class AggregateExpression(_Strict):
    kind: TypingLiteral["aggregate"] = "aggregate"
    function: TypingLiteral[
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
    ]
    expression: Expression
    distinct: bool = False
    filter: Expression | None = None


class OrderedAggregateExpression(_Strict):
    kind: TypingLiteral["ordered_aggregate"] = "ordered_aggregate"
    function: TypingLiteral["PERCENTILE_CONT"]
    percentile: Expression
    expression: Expression


class BinaryExpression(_Strict):
    kind: TypingLiteral["binary"] = "binary"
    operator: BinaryOperator
    left: Expression
    right: Expression


class LogicalExpression(_Strict):
    kind: TypingLiteral["logical"] = "logical"
    operator: TypingLiteral["AND", "OR"]
    terms: tuple[Expression, ...] = Field(min_length=2, max_length=16)


class NotExpression(_Strict):
    kind: TypingLiteral["not"] = "not"
    expression: Expression


class BetweenExpression(_Strict):
    kind: TypingLiteral["between"] = "between"
    expression: Expression
    low: Expression
    high: Expression


class InExpression(_Strict):
    kind: TypingLiteral["in"] = "in"
    expression: Expression
    values: tuple[Expression, ...] = Field(min_length=1, max_length=32)
    negated: bool = False


class IsNullExpression(_Strict):
    kind: TypingLiteral["is_null"] = "is_null"
    expression: Expression
    negated: bool = False


class CastExpression(_Strict):
    kind: TypingLiteral["cast"] = "cast"
    expression: Expression
    target_type: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_ (),]*$",
    )


class CaseBranch(_Strict):
    when: Expression
    then: Expression


class CaseExpression(_Strict):
    kind: TypingLiteral["case"] = "case"
    branches: tuple[CaseBranch, ...] = Field(min_length=1, max_length=8)
    default: Expression | None = None


class FunctionExpression(_Strict):
    kind: TypingLiteral["function"] = "function"
    function: SemanticFunction
    arguments: tuple[Expression, ...] = Field(max_length=8)


class WindowOrder(_Strict):
    expression: Expression
    direction: SortDirection = SortDirection.ASC


class WindowExpression(_Strict):
    kind: TypingLiteral["window"] = "window"
    function: TypingLiteral[
        "ROW_NUMBER",
        "RANK",
        "DENSE_RANK",
        "LAG",
        "LEAD",
        "NTH_VALUE",
        "NTILE",
        "PERCENT_RANK",
        "FIRST_VALUE",
        "LAST_VALUE",
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
    ]
    arguments: tuple[Expression, ...] = Field(default=(), max_length=4)
    partition_by: tuple[Expression, ...] = Field(default=(), max_length=8)
    order_by: tuple[WindowOrder, ...] = Field(default=(), max_length=8)


class ExistsExpression(_Strict):
    kind: TypingLiteral["exists"] = "exists"
    query: SemanticQueryIR
    negated: bool = False


class ScalarSubqueryExpression(_Strict):
    """A scalar value produced by a nested canonical query."""

    kind: TypingLiteral["scalar_subquery"] = "scalar_subquery"
    query: SemanticQueryIR


class EntityRelationSource(_Strict):
    kind: TypingLiteral["entity"] = "entity"
    entity_id: SemanticEntityId
    source_id: str = Field(default="base", min_length=1, max_length=128)


class DerivedRelationSource(_Strict):
    kind: TypingLiteral["derived"] = "derived"
    relation_id: str = Field(min_length=1, max_length=128)


class CTERelationSource(_Strict):
    kind: TypingLiteral["cte"] = "cte"
    cte_id: str = Field(min_length=1, max_length=128)
    # A CTE definition may be referenced by multiple query-local aliases.
    # The definition ID remains server-owned; this instance ID keeps nested
    # scopes (including correlated subqueries) distinct.
    source_id: str | None = Field(default=None, min_length=1, max_length=128)


RelationSource = Annotated[
    EntityRelationSource | DerivedRelationSource | CTERelationSource,
    Field(discriminator="kind"),
]


class JoinKey(_Strict):
    """Typed equality keys for a server-governed derived-relation join."""

    left: AttributeRef
    right: AttributeRef


class ExportedAttribute(_Strict):
    """An explicit output exposed by a nested relation to its parent scope."""

    attribute_id: SemanticAttributeId
    output_name: str = Field(min_length=1, max_length=63)
    position: int = Field(ge=0, le=63)
    description: str = Field(default="", max_length=240)


Expression = Annotated[
    AttributeRef
    | LiteralExpression
    | IntervalExpression
    | StarExpression
    | AggregateExpression
    | OrderedAggregateExpression
    | BinaryExpression
    | LogicalExpression
    | NotExpression
    | BetweenExpression
    | InExpression
    | IsNullExpression
    | CastExpression
    | CaseExpression
    | FunctionExpression
    | WindowExpression
    | ExistsExpression
    | ScalarSubqueryExpression,
    Field(discriminator="kind"),
]


class PlannedOutput(_Strict):
    position: int = Field(ge=0, le=63)
    semantic_role: str = Field(min_length=1, max_length=160)
    attribute_id: SemanticAttributeId | None = None
    expression: Expression | None = None
    alias: str | None = Field(default=None, min_length=1, max_length=63)

    @model_validator(mode="after")
    def exactly_one_value(self) -> PlannedOutput:
        if (self.attribute_id is None) == (self.expression is None):
            raise ValueError("an output must contain exactly one attribute_id or expression")
        return self


class PlannedJoin(_Strict):
    relationship_id: SemanticRelationshipId | None = None
    relationship_path: tuple[SemanticRelationshipId, ...] = Field(default=(), max_length=8)
    join_type: JoinType = JoinType.INNER
    target_source: RelationSource | None = None
    join_keys: tuple[JoinKey, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_join_source(self) -> PlannedJoin:
        has_relationship = self.relationship_id is not None or bool(self.relationship_path)
        if not has_relationship and (self.target_source is None or not self.join_keys):
            raise ValueError("a join requires a server relationship or typed join keys")
        if has_relationship and (self.target_source is not None or self.join_keys):
            raise ValueError("relationship joins cannot carry ad-hoc join sources or keys")
        if self.relationship_id is not None and self.relationship_path:
            raise ValueError("use relationship_id or relationship_path, not both")
        return self


class OrderSpec(_Strict):
    expression: Expression
    direction: SortDirection = SortDirection.ASC


class PopulationContract(_Strict):
    base_entity_ids: tuple[SemanticEntityId, ...] = Field(min_length=1, max_length=8)
    required_relationship_ids: tuple[SemanticRelationshipId, ...] = Field(default=(), max_length=16)
    inclusion_mode: PopulationInclusion = PopulationInclusion.BASE_ENTITY
    result_grain_attribute_ids: tuple[SemanticAttributeId, ...] = Field(default=(), max_length=16)
    aggregation_grain_attribute_ids: tuple[SemanticAttributeId, ...] = Field(
        default=(), max_length=16
    )
    expected_cardinality: ExpectedCardinality = ExpectedCardinality.UNKNOWN
    fanout_allowed: bool = False


class CalculationContract(_Strict):
    kind: CalculationKind = CalculationKind.NONE
    numerator: Expression | None = None
    denominator: Expression | None = None
    scale: Decimal | int | float | None = None
    zero_denominator_policy: ZeroDenominatorPolicy = ZeroDenominatorPolicy.NULL
    population_aligned: bool = True
    grain_attribute_ids: tuple[SemanticAttributeId, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_ratio_slots(self) -> CalculationContract:
        ratio = self.kind in {CalculationKind.RATIO, CalculationKind.PERCENTAGE}
        if ratio and (self.numerator is None or self.denominator is None):
            raise ValueError("ratio calculations require numerator and denominator")
        if not ratio and (self.numerator is not None or self.denominator is not None):
            raise ValueError("numerator and denominator are only valid for ratio calculations")
        return self


class SemanticQueryPlan(_Strict):
    """Provider-facing semantic intent; never contains SQL or physical ON text."""

    database_id: str = Field(min_length=1, max_length=128)
    from_entity_id: SemanticEntityId | None = None
    from_source: RelationSource | None = None
    population_contract: PopulationContract
    outputs: tuple[PlannedOutput, ...] = Field(min_length=1, max_length=32)
    joins: tuple[PlannedJoin, ...] = Field(default=(), max_length=16)
    where: Expression | None = None
    group_by: tuple[Expression, ...] = Field(default=(), max_length=16)
    having: Expression | None = None
    order_by: tuple[OrderSpec, ...] = Field(default=(), max_length=16)
    distinct: bool = False
    limit: int | None = Field(default=None, ge=1, le=10000)
    offset: int | None = Field(default=None, ge=0, le=1000000)
    calculation_contract: CalculationContract | None = None
    description: str | None = Field(default=None, max_length=240)
    ctes: tuple[CommonTableExpression, ...] = Field(default=(), max_length=16)
    derived_relations: tuple[DerivedRelation, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_positions(self) -> SemanticQueryPlan:
        positions = tuple(item.position for item in self.outputs)
        if positions != tuple(range(len(positions))):
            raise ValueError("outputs must have contiguous positions starting at zero")
        if (self.from_entity_id is None) == (self.from_source is None):
            raise ValueError("a plan requires exactly one from_entity_id or from_source")
        return self


class IRSelectItem(_Strict):
    position: int = Field(ge=0, le=63)
    expression: Expression
    alias: str | None = Field(default=None, min_length=1, max_length=63)


class SemanticQueryIR(_Strict):
    """Canonical semantic IR after plan validation, still SQL-free."""

    database_id: str = Field(min_length=1, max_length=128)
    from_entity_id: SemanticEntityId | None = None
    from_source: RelationSource | None = None
    joins: tuple[PlannedJoin, ...] = Field(default=(), max_length=16)
    select: tuple[IRSelectItem, ...] = Field(min_length=1, max_length=32)
    where: Expression | None = None
    group_by: tuple[Expression, ...] = Field(default=(), max_length=16)
    having: Expression | None = None
    order_by: tuple[OrderSpec, ...] = Field(default=(), max_length=16)
    distinct: bool = False
    limit: int | None = Field(default=None, ge=1, le=10000)
    offset: int | None = Field(default=None, ge=0, le=1000000)
    population_contract: PopulationContract
    calculation_contract: CalculationContract | None = None
    ctes: tuple[CommonTableExpression, ...] = Field(default=(), max_length=16)
    derived_relations: tuple[DerivedRelation, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_source(self) -> SemanticQueryIR:
        if (self.from_entity_id is None) == (self.from_source is None):
            raise ValueError("a query requires exactly one from_entity_id or from_source")
        return self


class DerivedRelation(_Strict):
    """Query-local relation whose body is another canonical semantic query."""

    relation_id: str = Field(min_length=1, max_length=128)
    query: SemanticQueryIR
    exported_attributes: tuple[ExportedAttribute, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_exports(self) -> DerivedRelation:
        positions = tuple(item.position for item in self.exported_attributes)
        if positions != tuple(range(len(positions))):
            raise ValueError("derived outputs must have contiguous positions")
        names = [item.output_name.lower() for item in self.exported_attributes]
        if len(names) != len(set(names)):
            raise ValueError("derived outputs must have unique names")
        return self


class CommonTableExpression(_Strict):
    """Non-recursive named nested semantic query."""

    cte_id: str = Field(min_length=1, max_length=128)
    query: SemanticQueryIR
    exported_attributes: tuple[ExportedAttribute, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_exports(self) -> CommonTableExpression:
        positions = tuple(item.position for item in self.exported_attributes)
        if positions != tuple(range(len(positions))):
            raise ValueError("CTE outputs must have contiguous positions")
        names = [item.output_name.lower() for item in self.exported_attributes]
        if len(names) != len(set(names)):
            raise ValueError("CTE outputs must have unique names")
        return self


class SemanticQueryProvenance(_Strict):
    """Bounded hashes and decisions emitted for a semantic execution."""

    mapping_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ir_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_version: str = Field(min_length=1, max_length=64)
    compiled_sql_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    relationship_ids: tuple[SemanticRelationshipId, ...] = ()
    semantic_validation: TypingLiteral["PASS", "FAIL"]


for _model in (
    AggregateExpression,
    IntervalExpression,
    OrderedAggregateExpression,
    BinaryExpression,
    LogicalExpression,
    NotExpression,
    BetweenExpression,
    InExpression,
    IsNullExpression,
    CastExpression,
    CaseBranch,
    CaseExpression,
    FunctionExpression,
    WindowOrder,
    WindowExpression,
    ExistsExpression,
    ScalarSubqueryExpression,
    EntityRelationSource,
    DerivedRelationSource,
    CTERelationSource,
    JoinKey,
    ExportedAttribute,
    DerivedRelation,
    CommonTableExpression,
    PlannedOutput,
    OrderSpec,
    CalculationContract,
    SemanticQueryPlan,
    IRSelectItem,
    SemanticQueryIR,
    SemanticQueryProvenance,
):
    _model.model_rebuild()


def plan_to_ir(plan: SemanticQueryPlan) -> SemanticQueryIR:
    """Lower a validated semantic plan to the canonical IR without resolving IDs."""
    return SemanticQueryIR(
        database_id=plan.database_id,
        from_entity_id=plan.from_entity_id,
        from_source=plan.from_source,
        joins=plan.joins,
        select=tuple(
            IRSelectItem(
                position=output.position,
                expression=(
                    output.expression
                    if output.expression is not None
                    else AttributeRef(attribute_id=output.attribute_id or "")
                ),
                alias=output.alias,
            )
            for output in plan.outputs
        ),
        where=plan.where,
        group_by=plan.group_by,
        having=plan.having,
        order_by=plan.order_by,
        distinct=plan.distinct,
        limit=plan.limit,
        offset=plan.offset,
        population_contract=plan.population_contract,
        calculation_contract=plan.calculation_contract,
        ctes=plan.ctes,
        derived_relations=plan.derived_relations,
    )


def semantic_content_hash(value: Any) -> str:
    """Stable hash used for plan/IR provenance without persisting free-form SQL."""
    import hashlib
    import json

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
