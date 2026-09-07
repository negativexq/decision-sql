from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.generation.provider import OpenAICompatibleProvider, SemanticQueryPlanProposal
from app.models.domain import TextToSqlRequest
from app.semantics.compatibility import query_plan_v1_to_semantic_plan
from app.semantics.query_plan_v1 import QueryPlanV1, QueryPlanV1Catalog
from app.semantics.semantic_compiler import SemanticQueryCompiler
from app.semantics.semantic_errors import SemanticFailureCode, SemanticValidationError
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    BinaryExpression,
    BinaryOperator,
    CommonTableExpression,
    CTERelationSource,
    DerivedRelation,
    DerivedRelationSource,
    ExportedAttribute,
    FunctionExpression,
    IntervalExpression,
    IRSelectItem,
    LiteralExpression,
    LogicalExpression,
    NotExpression,
    PlannedJoin,
    PlannedOutput,
    PopulationContract,
    ScalarSubqueryExpression,
    SemanticFunction,
    SemanticQueryIR,
    SemanticQueryPlan,
    SortDirection,
    StarExpression,
    WindowExpression,
    WindowOrder,
    plan_to_ir,
)
from app.semantics.semantic_validation import SemanticConsistencyValidator
from app.semantics.service import SemanticQueryService
from app.sql.models import ExplainEstimate, PolicyCode, QueryExecution, QueryPlan
from app.sql.parser import SQLParser
from app.sql.policy import SQLPolicy
from evaluation.semantic_oracle_ceiling import run_oracle_ceiling


@pytest.fixture
def mapping() -> SemanticMappingSnapshot:
    return SemanticMappingSnapshot.from_schema(build_default_catalog(Base.metadata))


def _plan(
    mapping: SemanticMappingSnapshot, *, expression: object | None = None
) -> SemanticQueryPlan:
    output = PlannedOutput(
        position=0,
        semantic_role="answer",
        attribute_id="attribute:customers.name" if expression is None else None,
        expression=expression,
    )
    return SemanticQueryPlan(
        database_id="test",
        from_entity_id="entity:customers",
        population_contract=PopulationContract(base_entity_ids=("entity:customers",)),
        outputs=(output,),
    )


def test_canonical_models_are_strict_and_sql_free() -> None:
    with pytest.raises(ValidationError):
        SemanticQueryPlan.model_validate(
            {
                "database_id": "test",
                "from_entity_id": "entity:customers",
                "population_contract": {"base_entity_ids": ["entity:customers"]},
                "outputs": [
                    {"position": 0, "semantic_role": "x", "attribute_id": "attribute:customers.id"}
                ],
                "sql": "DROP TABLE customers",
            }
        )


def test_identity_mapping_resolves_entities_attributes_and_relationships(mapping) -> None:
    assert mapping.entity("entity:orders").physical_table == "orders"
    assert mapping.attribute("attribute:orders.total_amount").physical_column == "total_amount"
    relationship = mapping.relationship_for_legacy("orders.customer_id")
    assert relationship.to_entity_id == "entity:customers"
    assert relationship.from_attribute_id == "attribute:orders.customer_id"


def test_compiler_owns_join_predicate_and_sqlglot_ast(mapping) -> None:
    relationship = mapping.relationship_for_legacy("orders.customer_id")
    plan = SemanticQueryPlan(
        database_id="test",
        from_entity_id="entity:orders",
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        outputs=(
            PlannedOutput(
                position=0, semantic_role="customer", attribute_id="attribute:customers.name"
            ),
        ),
        joins=(PlannedJoin(relationship_id=relationship.relationship_id),),
    )
    compiled = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan))
    assert "ON t0.customer_id = t1.id" in compiled.sql
    assert compiled.ast.__class__.__name__ == "Select"


def test_one_to_many_join_requires_explicit_fanout_contract(mapping) -> None:
    relationship = mapping.relationship_for_legacy("orders.customer_id")
    plan = SemanticQueryPlan(
        database_id="test",
        from_entity_id="entity:customers",
        population_contract=PopulationContract(base_entity_ids=("entity:customers",)),
        outputs=(
            PlannedOutput(
                position=0, semantic_role="customer", attribute_id="attribute:customers.name"
            ),
        ),
        joins=(PlannedJoin(relationship_id=relationship.relationship_id),),
    )
    with pytest.raises(SemanticValidationError) as error:
        SemanticQueryCompiler(mapping).compile(plan_to_ir(plan))
    assert error.value.code is SemanticFailureCode.FANOUT_UNSAFE_PATH
    allowed = plan.model_copy(
        update={
            "population_contract": PopulationContract(
                base_entity_ids=("entity:customers",), fanout_allowed=True
            )
        }
    )
    assert "JOIN orders" in SemanticQueryCompiler(mapping).compile(plan_to_ir(allowed)).sql


def test_typed_expression_aggregation_and_nullif_compile(mapping) -> None:
    total = AttributeRef(attribute_id="attribute:orders.total_amount")
    expression = BinaryExpression(
        operator=BinaryOperator.MULTIPLY,
        left=AggregateExpression(function="SUM", expression=total),
        right=LiteralExpression(value=100, value_type="integer"),
    )
    compiled = SemanticQueryCompiler(mapping).compile(
        plan_to_ir(
            _plan(mapping, expression=expression).model_copy(
                update={
                    "from_entity_id": "entity:orders",
                    "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
                }
            )
        )
    )
    assert "SUM(t0.total_amount) * 100" in compiled.sql
    nullif = FunctionExpression(
        function=SemanticFunction.NULLIF,
        arguments=(total, LiteralExpression(value=0, value_type="integer")),
    )
    assert (
        "NULLIF"
        in SemanticQueryCompiler(mapping)
        .compile(
            plan_to_ir(
                _plan(mapping, expression=nullif).model_copy(
                    update={
                        "from_entity_id": "entity:orders",
                        "population_contract": PopulationContract(
                            base_entity_ids=("entity:orders",)
                        ),
                    }
                )
            )
        )
        .sql
    )


def test_compiler_preserves_nested_arithmetic_precedence(mapping) -> None:
    expression = BinaryExpression(
        operator=BinaryOperator.MULTIPLY,
        left=AttributeRef(attribute_id="attribute:orders.total_amount"),
        right=BinaryExpression(
            operator=BinaryOperator.ADD,
            left=AttributeRef(attribute_id="attribute:orders.id"),
            right=LiteralExpression(value=1, value_type="integer"),
        ),
    )
    plan = _plan(mapping, expression=expression).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    sql = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan)).sql
    assert "t0.total_amount * (t0.id + 1)" in sql


def test_compiler_preserves_mixed_boolean_precedence(mapping) -> None:
    predicate = LogicalExpression(
        operator="AND",
        terms=(
            BinaryExpression(
                operator=BinaryOperator.EQ,
                left=AttributeRef(attribute_id="attribute:orders.id"),
                right=LiteralExpression(value=1, value_type="integer"),
            ),
            LogicalExpression(
                operator="OR",
                terms=(
                    BinaryExpression(
                        operator=BinaryOperator.EQ,
                        left=AttributeRef(attribute_id="attribute:orders.status"),
                        right=LiteralExpression(value="open", value_type="string"),
                    ),
                    BinaryExpression(
                        operator=BinaryOperator.EQ,
                        left=AttributeRef(attribute_id="attribute:orders.status"),
                        right=LiteralExpression(value="pending", value_type="string"),
                    ),
                ),
            ),
        ),
    )
    plan = _plan(mapping).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "outputs": (
                PlannedOutput(position=0, semantic_role="id", attribute_id="attribute:orders.id"),
            ),
            "where": predicate,
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    sql = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan)).sql
    assert "t0.id = 1 AND (t0.status = 'open' OR t0.status = 'pending')" in sql


def test_distinct_is_inside_aggregate_not_around_the_result(mapping) -> None:
    plan = _plan(
        mapping,
        expression=AggregateExpression(
            function="SUM",
            expression=AttributeRef(attribute_id="attribute:orders.total_amount"),
            distinct=True,
        ),
    ).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    sql = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan)).sql
    assert "SUM(DISTINCT t0.total_amount)" in sql
    assert "DISTINCT SUM" not in sql


def test_window_expression_is_compiled_by_general_expression_compiler(mapping) -> None:
    expression = WindowExpression(
        function="ROW_NUMBER",
        partition_by=(AttributeRef(attribute_id="attribute:orders.customer_id"),),
        order_by=(
            WindowOrder(
                expression=AttributeRef(attribute_id="attribute:orders.total_amount"),
                direction=SortDirection.DESC,
            ),
        ),
    )
    plan = _plan(mapping, expression=expression).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    sql = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan)).sql
    assert "ROW_NUMBER() OVER" in sql
    assert "PARTITION BY t0.customer_id" in sql


def test_unknown_semantic_attribute_fails_closed(mapping) -> None:
    plan = _plan(mapping).model_copy(
        update={
            "outputs": (
                PlannedOutput(position=0, semantic_role="bad", attribute_id="attribute:missing.id"),
            )
        }
    )
    with pytest.raises(SemanticValidationError) as error:
        SemanticQueryCompiler(mapping).compile(plan_to_ir(plan))
    assert error.value.code is SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE


def test_consistency_validator_detects_unplanned_projection_filter(mapping) -> None:
    plan = _plan(mapping)
    ir = plan_to_ir(plan)
    compiled = SemanticQueryCompiler(mapping).compile(ir)
    tampered = compiled.ast.copy()
    tampered = tampered.select("t0.id")
    result = SemanticConsistencyValidator(mapping).validate(ir, tampered)
    assert not result.accepted
    assert any(code is SemanticFailureCode.PROJECTION_MISMATCH for code, _ in result.failures)


def test_v1_adapter_lowers_into_canonical_engine(mapping) -> None:
    schema = build_default_catalog(Base.metadata)
    v1 = QueryPlanV1Catalog.from_schema(schema)
    plan = QueryPlanV1(
        applicable=True,
        source="orders",
        projection=("orders.id",),
        filters=(
            {
                "column_id": "orders.total_amount",
                "operator": "GTE",
                "value": {"kind": "decimal", "value": "10.00"},
            },
        ),
    )
    canonical = query_plan_v1_to_semantic_plan(plan, v1)
    assert (
        "WHERE t0.total_amount >= 10.00"
        in SemanticQueryCompiler(mapping).compile(plan_to_ir(canonical)).sql
    )


def test_m1_keeps_dangerous_functions_denied_and_nullif_allowed() -> None:
    policy = SQLPolicy(build_default_catalog(Base.metadata))
    assert policy.validate(SQLParser().parse("SELECT NULLIF(total_amount, 0) FROM orders")) is None
    for sql in ("SELECT RANDOM()", "SELECT PG_SLEEP(1)", "SELECT SET_CONFIG('x', 'y', false)"):
        rejection = policy.validate(SQLParser().parse(sql))
        assert rejection is not None
        assert rejection.code is PolicyCode.FORBIDDEN_FUNCTION


@pytest.mark.asyncio
async def test_semantic_provider_contract_contains_no_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatibleProvider(Settings(_env_file=None, llm_api_key="test"))
    mapping = SemanticMappingSnapshot.from_schema(build_default_catalog(Base.metadata))
    plan = _plan(mapping)
    captured: dict[str, object] = {}

    async def response(body: object) -> dict[str, object]:
        captured["body"] = body
        return {
            "model": "test-model",
            "choices": [{"message": {"content": plan.model_dump_json()}}],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post", response)
    result = await provider.propose_semantic_query_plan("show names", "semantic context")
    assert result.plan == plan
    body = captured["body"]
    assert isinstance(body, dict)
    messages = body["messages"]
    assert isinstance(messages, list)
    assert all("sql" not in message["content"].lower() for message in messages[1:])
    assert "sql" not in plan.model_dump(mode="json")


@pytest.mark.asyncio
async def test_semantic_service_requires_m1_before_execution(mapping) -> None:
    plan = _plan(mapping)

    class Provider:
        async def propose_semantic_query_plan(self, question: str, schema_context: str):
            del question, schema_context
            return SemanticQueryPlanProposal(plan=plan, provider="test", model="test")

    class Safety:
        catalog = build_default_catalog(Base.metadata)

        def __init__(self) -> None:
            self.planned = 0
            self.executed = 0

        def plan(self, candidate):
            self.planned += 1
            return QueryPlan(
                plan_id=uuid4(),
                normalized_sql=candidate.sql,
                statement_type="SELECT",
                estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Seq Scan"),
            )

        def execute(self, query_plan):
            self.executed += 1
            return QueryExecution(
                plan_id=query_plan.plan_id,
                columns=["name"],
                rows=[],
                row_count=0,
                latency_ms=1,
            )

    safety = Safety()
    service = SemanticQueryService(
        Provider(), safety, lambda _: "server-owned context", database_id="test"
    )
    result = await service.run(TextToSqlRequest(question="show customer names"))
    assert result.status.value == "SUCCEEDED", result.error
    assert safety.planned == 1
    assert safety.executed == 1
    assert result.candidate is not None
    assert result.candidate.sql == "SELECT t0.name FROM customers AS t0"
    assert result.generation_path.value == "SEMANTIC_QUERY_COMPILER"


def test_provider_free_oracle_ceiling_uses_canonical_compiler(mapping) -> None:
    result = run_oracle_ceiling(
        [
            "SELECT status, COUNT(*) AS count FROM orders GROUP BY status ORDER BY status",
            "SELECT o.order_number, c.name FROM orders o "
            "JOIN customers c ON c.id = o.customer_id ORDER BY o.order_number",
        ],
        mapping,
    )
    assert result.cases == 2
    assert result.ir_validated == 2
    assert result.compiled == 2
    assert result.semantic_validated == 2
    assert result.unsupported == 0


def test_cte_exports_are_compiled_as_nested_canonical_ir(mapping) -> None:
    inner = SemanticQueryIR(
        database_id="test",
        from_entity_id="entity:orders",
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        select=(
            IRSelectItem(
                position=0,
                expression=AttributeRef(attribute_id="attribute:orders.customer_id"),
                alias="customer_id",
            ),
            IRSelectItem(
                position=1,
                expression=AggregateExpression(function="COUNT", expression=StarExpression()),
                alias="order_count",
            ),
        ),
        group_by=(AttributeRef(attribute_id="attribute:orders.customer_id"),),
    )
    cte = CommonTableExpression(
        cte_id="order_counts",
        query=inner,
        exported_attributes=(
            ExportedAttribute(
                attribute_id="output:order_counts:customer_id",
                output_name="customer_id",
                position=0,
            ),
            ExportedAttribute(
                attribute_id="output:order_counts:order_count",
                output_name="order_count",
                position=1,
            ),
        ),
    )
    outer = SemanticQueryIR(
        database_id="test",
        from_source=CTERelationSource(cte_id="order_counts"),
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        select=(
            IRSelectItem(
                position=0,
                expression=AttributeRef(
                    attribute_id="output:order_counts:customer_id",
                    relation_ref="order_counts",
                ),
            ),
            IRSelectItem(
                position=1,
                expression=AttributeRef(
                    attribute_id="output:order_counts:order_count",
                    relation_ref="order_counts",
                ),
            ),
        ),
        ctes=(cte,),
    )
    sql = SemanticQueryCompiler(mapping).compile(outer).sql
    assert "WITH order_counts AS" in sql
    assert "GROUP BY t1.customer_id" in sql
    assert "FROM order_counts AS t0" in sql


def test_derived_relation_hides_unexported_attributes(mapping) -> None:
    inner = SemanticQueryIR(
        database_id="test",
        from_entity_id="entity:orders",
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        select=(
            IRSelectItem(
                position=0,
                expression=AttributeRef(attribute_id="attribute:orders.id"),
                alias="order_id",
            ),
        ),
    )
    relation = DerivedRelation(
        relation_id="orders_projection",
        query=inner,
        exported_attributes=(
            ExportedAttribute(
                attribute_id="output:orders_projection:order_id",
                output_name="order_id",
                position=0,
            ),
        ),
    )
    outer = SemanticQueryIR(
        database_id="test",
        from_source=DerivedRelationSource(relation_id="orders_projection"),
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        select=(
            IRSelectItem(
                position=0,
                expression=AttributeRef(
                    attribute_id="output:orders_projection:order_id",
                    relation_ref="orders_projection",
                ),
            ),
        ),
        derived_relations=(relation,),
    )
    compiled = SemanticQueryCompiler(mapping).compile(outer)
    assert "FROM (SELECT" in compiled.sql
    with pytest.raises(SemanticValidationError) as error:
        bad = outer.model_copy(
            update={
                "select": (
                    IRSelectItem(
                        position=0,
                        expression=AttributeRef(
                            attribute_id="output:orders_projection:hidden",
                            relation_ref="orders_projection",
                        ),
                    ),
                )
            }
        )
        SemanticQueryCompiler(mapping).compile(bad)
    assert error.value.code is SemanticFailureCode.UNKNOWN_DERIVED_ATTRIBUTE


def test_filtered_aggregate_and_not_are_typed_and_compiled(mapping) -> None:
    status = AttributeRef(attribute_id="attribute:orders.status")
    filtered = AggregateExpression(
        function="COUNT",
        expression=AttributeRef(attribute_id="attribute:orders.customer_id"),
        distinct=True,
        filter=NotExpression(
            expression=BinaryExpression(
                operator=BinaryOperator.EQ,
                left=status,
                right=LiteralExpression(value="cancelled", value_type="string"),
            )
        ),
    )
    plan = _plan(mapping, expression=filtered).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    sql = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan)).sql
    assert "COUNT(DISTINCT t0.customer_id) FILTER(WHERE NOT" in sql
    assert "t0.status = 'cancelled'" in sql


def test_semantic_validator_detects_wrong_filtered_aggregate_predicate(mapping) -> None:
    def filtered(value: str) -> AggregateExpression:
        return AggregateExpression(
            function="COUNT",
            expression=AttributeRef(attribute_id="attribute:orders.customer_id"),
            filter=BinaryExpression(
                operator=BinaryOperator.EQ,
                left=AttributeRef(attribute_id="attribute:orders.status"),
                right=LiteralExpression(value=value, value_type="string"),
            ),
        )

    accepted = _plan(mapping, expression=filtered("shipped")).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    wrong = _plan(mapping, expression=filtered("cancelled")).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    original_ir = plan_to_ir(accepted)
    wrong_compiled = SemanticQueryCompiler(mapping).compile(plan_to_ir(wrong))
    result = SemanticConsistencyValidator(mapping).validate(original_ir, wrong_compiled)
    assert not result.accepted
    assert any(code is SemanticFailureCode.INVALID_AGGREGATION for code, _ in result.failures)


def test_interval_and_scalar_subquery_are_canonical_expressions(mapping) -> None:
    nested = SemanticQueryIR(
        database_id="test",
        from_entity_id="entity:orders",
        population_contract=PopulationContract(
            base_entity_ids=("entity:orders",), expected_cardinality="ONE_ROW"
        ),
        select=(
            IRSelectItem(
                position=0,
                expression=AggregateExpression(
                    function="MAX",
                    expression=AttributeRef(attribute_id="attribute:orders.total_amount"),
                ),
            ),
        ),
    )
    expression = BinaryExpression(
        operator=BinaryOperator.GT,
        left=AttributeRef(attribute_id="attribute:orders.total_amount"),
        right=ScalarSubqueryExpression(query=nested),
    )
    plan = _plan(mapping, expression=expression).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
            "where": BinaryExpression(
                operator=BinaryOperator.GT,
                left=FunctionExpression(
                    function=SemanticFunction.CURRENT_DATE,
                    arguments=(),
                ),
                right=IntervalExpression(amount="1", unit="YEAR"),
            ),
        }
    )
    sql = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan)).sql
    assert "(SELECT MAX" in sql
    assert "INTERVAL '1 YEAR'" in sql


def test_server_configured_relationships_are_explicitly_mapped(mapping) -> None:
    from app.semantics.semantic_mapping import SemanticRelationshipMapping

    relationship = SemanticRelationshipMapping(
        relationship_id="relationship:orders.order_number->customers.name",
        from_entity_id="entity:orders",
        from_attribute_id="attribute:orders.order_number",
        to_entity_id="entity:customers",
        to_attribute_id="attribute:customers.name",
        source_kind="SERVER_CONFIGURED",
    )
    configured = mapping.with_server_relationships((relationship,))
    assert configured.relationship(relationship.relationship_id).source_kind == "SERVER_CONFIGURED"
    with pytest.raises(ValueError):
        mapping.with_server_relationships(
            (relationship.model_copy(update={"source_kind": "MODEL_INFERRED"}),)
        )


def test_multi_hop_relationship_path_is_compiled_deterministically(mapping) -> None:
    path = (
        mapping.relationship_for_legacy("order_items.order_id").relationship_id,
        mapping.relationship_for_legacy("orders.customer_id").relationship_id,
    )
    plan = SemanticQueryPlan(
        database_id="test",
        from_entity_id="entity:order_items",
        population_contract=PopulationContract(
            base_entity_ids=("entity:order_items",), fanout_allowed=True
        ),
        outputs=(
            PlannedOutput(
                position=0,
                semantic_role="customer",
                attribute_id="attribute:customers.name",
            ),
        ),
        joins=(PlannedJoin(relationship_path=path),),
    )
    sql = SemanticQueryCompiler(mapping).compile(plan_to_ir(plan)).sql
    assert "JOIN orders" in sql
    assert "JOIN customers" in sql


def test_duplicate_cte_ids_fail_with_typed_error(mapping) -> None:
    inner = SemanticQueryIR(
        database_id="test",
        from_entity_id="entity:orders",
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        select=(
            IRSelectItem(
                position=0,
                expression=AttributeRef(attribute_id="attribute:orders.id"),
            ),
        ),
    )
    cte = CommonTableExpression(
        cte_id="duplicate",
        query=inner,
        exported_attributes=(
            ExportedAttribute(
                attribute_id="output:duplicate:id",
                output_name="id",
                position=0,
            ),
        ),
    )
    outer = SemanticQueryIR(
        database_id="test",
        from_source=CTERelationSource(cte_id="duplicate"),
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        select=(
            IRSelectItem(
                position=0,
                expression=AttributeRef(
                    attribute_id="output:duplicate:id", relation_ref="duplicate"
                ),
            ),
        ),
        ctes=(cte, cte),
    )
    with pytest.raises(SemanticValidationError) as error:
        SemanticQueryCompiler(mapping).compile(outer)
    assert error.value.code is SemanticFailureCode.DUPLICATE_CTE_ID


def test_scalar_subquery_requires_one_exported_value(mapping) -> None:
    nested = SemanticQueryIR(
        database_id="test",
        from_entity_id="entity:orders",
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        select=(
            IRSelectItem(
                position=0,
                expression=AttributeRef(attribute_id="attribute:orders.id"),
            ),
            IRSelectItem(
                position=1,
                expression=AttributeRef(attribute_id="attribute:orders.customer_id"),
            ),
        ),
    )
    plan = _plan(mapping, expression=ScalarSubqueryExpression(query=nested)).model_copy(
        update={
            "from_entity_id": "entity:orders",
            "population_contract": PopulationContract(base_entity_ids=("entity:orders",)),
        }
    )
    with pytest.raises(SemanticValidationError) as error:
        SemanticQueryCompiler(mapping).compile(plan_to_ir(plan))
    assert error.value.code is SemanticFailureCode.DERIVED_RELATION_SCHEMA_MISMATCH
