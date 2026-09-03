import pytest

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.provider import SqlProposal
from app.generation.result_shape import (
    ProjectionStatus,
    ResultOutputKind,
    ResultShapeKind,
    ResultShapeProposal,
    validate_result_shape,
)
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextResolver
from app.sql.models import (
    ExplainEstimate,
    PolicyCode,
    PolicyRejection,
    QueryPlan,
    SqlCandidate,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.text_to_sql.service import TextToSqlService
from evaluation.m28_projection import projection_diagnostics


def _shape(*outputs: dict[str, str], limit: int | None = None) -> ResultShapeProposal:
    return ResultShapeProposal(
        outputs=tuple(outputs),
        shape=ResultShapeKind.ROW_FILTER,
        explicit_limit=limit,
    )


def test_result_shape_is_narrow_and_supports_physical_and_derived_outputs() -> None:
    shape = _shape(
        {"semantic_label": "product_id", "kind": "PHYSICAL_COLUMN", "source_hint": "products.id"},
        {"semantic_label": "revenue", "kind": "DERIVED_VALUE"},
    )

    assert shape.outputs[0].kind is ResultOutputKind.PHYSICAL_COLUMN
    assert shape.outputs[1].kind is ResultOutputKind.DERIVED_VALUE
    assert not hasattr(shape, "tables")
    assert not hasattr(shape, "joins")
    assert not hasattr(shape, "filters")


def test_result_shape_validator_detects_extra_and_missing_projection() -> None:
    extra = validate_result_shape(
        _shape({"semantic_label": "id", "kind": "PHYSICAL_COLUMN", "source_hint": "products.id"}),
        "SELECT id, name FROM products",
    )
    missing = validate_result_shape(
        _shape(
            {"semantic_label": "id", "kind": "PHYSICAL_COLUMN", "source_hint": "products.id"},
            {"semantic_label": "name", "kind": "PHYSICAL_COLUMN", "source_hint": "products.name"},
        ),
        "SELECT id FROM products",
    )

    assert extra.projection_status is ProjectionStatus.EXTRA
    assert extra.accepted is False
    assert missing.projection_status is ProjectionStatus.MISSING
    assert missing.accepted is False


def test_result_shape_validator_accepts_derived_expression() -> None:
    validation = validate_result_shape(
        _shape({"semantic_label": "total", "kind": "DERIVED_VALUE"}),
        "SELECT SUM(total_amount) AS total FROM orders",
    )

    assert validation.accepted is True
    assert validation.projection_status is ProjectionStatus.EXACT


def test_result_shape_validator_resolves_sql_table_aliases() -> None:
    validation = validate_result_shape(
        _shape(
            {
                "semantic_label": "customer",
                "kind": "PHYSICAL_COLUMN",
                "source_hint": "customers.name",
            }
        ),
        "SELECT c.name AS customer FROM customers AS c",
    )

    assert validation.accepted is True


def test_projection_diagnostics_does_not_pair_unrelated_columns() -> None:
    diagnostic = projection_diagnostics(
        "SELECT order_items.discount_amount FROM order_items",
        "SELECT order_items.id FROM order_items",
    )

    assert diagnostic["status"] == "PROJECTION_MISSING"
    assert diagnostic["missing_expressions"] == ["order_items.discount_amount"]
    assert diagnostic["extra_expressions"] == ["order_items.id"]


class RecordingShapeProvider:
    def __init__(self, sql: str = "SELECT id FROM products") -> None:
        self.sql = sql
        self.shape_calls = 0
        self.sql_calls = 0
        self.received_shape: ResultShapeProposal | None = None

    async def propose_result_shape(self, question: str, schema_context: str) -> ResultShapeProposal:
        del question
        self.shape_calls += 1
        assert "[Table] products" in schema_context
        return _shape(
            {"semantic_label": "id", "kind": "PHYSICAL_COLUMN", "source_hint": "products.id"}
        )

    async def propose_intent(self, question: str, schema_context: str) -> object:
        del question, schema_context
        raise AssertionError("M2.8 must not call QueryIntent")

    async def propose_sql(
        self,
        request: object,
        user_context: object,
        schema_context: str,
        query_intent: object = None,
        result_shape: ResultShapeProposal | None = None,
    ) -> SqlProposal:
        del request, user_context, schema_context, query_intent
        self.sql_calls += 1
        self.received_shape = result_shape
        return SqlProposal(sql=self.sql, provider="fake", model="fake")


def _plan_only_safety() -> object:
    planned = QueryPlan(
        plan_id="11111111-1111-4111-8111-111111111111",
        normalized_sql="SELECT id FROM products",
        statement_type="Select",
        referenced_tables=("products",),
        estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Limit"),
    )

    class PlanOnlySafety:
        def plan(self, candidate: SqlCandidate) -> QueryPlan:
            assert candidate.source.value == "llm"
            return planned

        def execute(self, plan: QueryPlan) -> object:
            raise AssertionError("test uses execute=False")

    return PlanOnlySafety()


@pytest.mark.asyncio
async def test_baseline_skips_shape_and_contract_passes_shape_to_sql() -> None:
    catalog = build_default_catalog(Base.metadata)
    baseline_provider = RecordingShapeProvider()
    contract_provider = RecordingShapeProvider()
    baseline = TextToSqlService(
        SchemaContextResolver(catalog),
        baseline_provider,  # type: ignore[arg-type]
        _plan_only_safety(),  # type: ignore[arg-type]
    )
    contract = TextToSqlService(
        SchemaContextResolver(catalog),
        contract_provider,  # type: ignore[arg-type]
        _plan_only_safety(),  # type: ignore[arg-type]
    )

    await baseline.run(TextToSqlRequest(question="list products", execute=False))
    contract_result = await contract.run_result_shape_contract(
        TextToSqlRequest(question="list products", execute=False)
    )

    assert baseline_provider.shape_calls == 0
    assert baseline_provider.sql_calls == 1
    assert contract_provider.shape_calls == 1
    assert contract_provider.sql_calls == 1
    assert contract_provider.received_shape is not None
    assert contract_result.result_shape_validation is not None
    assert contract_result.result_shape_validation.accepted is True


@pytest.mark.asyncio
async def test_malicious_contract_sql_still_reaches_only_m1() -> None:
    catalog = build_default_catalog(Base.metadata)
    provider = RecordingShapeProvider("DELETE FROM orders")

    class RejectingSafety:
        def plan(self, candidate: SqlCandidate) -> SqlPlanFailure:
            assert candidate.sql == "DELETE FROM orders"
            return SqlPlanFailure(
                status=SqlSafetyStatus.POLICY_REJECTION,
                rejection=PolicyRejection(
                    code=PolicyCode.NON_READ_ONLY_STATEMENT,
                    message="write rejected",
                ),
            )

        def execute(self, plan: QueryPlan) -> object:
            raise AssertionError("M1 execute must not run after rejection")

    service = TextToSqlService(
        SchemaContextResolver(catalog),
        provider,  # type: ignore[arg-type]
        RejectingSafety(),  # type: ignore[arg-type]
    )

    result = await service.run_result_shape_contract(
        TextToSqlRequest(question="list products", execute=True)
    )

    assert result.plan is None
    assert result.execution is None
