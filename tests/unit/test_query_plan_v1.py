import pytest
from pydantic import ValidationError

from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.generation.provider import MalformedProviderResponse, OpenAICompatibleProvider
from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Catalog,
    QueryPlanV1Compiler,
    QueryPlanV1ValidationError,
    validate_query_plan_v1,
)


@pytest.fixture
def catalog() -> QueryPlanV1Catalog:
    return QueryPlanV1Catalog.from_schema(build_default_catalog(Base.metadata))


def test_catalog_has_server_owned_relationships(catalog: QueryPlanV1Catalog) -> None:
    assert len(catalog.tables) == 8
    assert len(catalog.relationships) == 9
    assert catalog.relationship("orders.customer_id").right_column_id == "customers.id"


def test_compiler_preserves_projection_and_join_ownership(catalog: QueryPlanV1Catalog) -> None:
    plan = QueryPlanV1(
        applicable=True,
        source="orders",
        joins=({"relationship_id": "orders.customer_id", "join_type": "LEFT"},),
        projection=("orders.status", "customers.name"),
    )
    sql = QueryPlanV1Compiler(catalog).compile(plan).sql
    assert sql == (
        "SELECT t0.status, t1.name FROM orders AS t0 LEFT JOIN customers AS t1 "
        "ON t0.customer_id = t1.id"
    )


def test_typed_predicate_compiles_without_raw_sql(catalog: QueryPlanV1Catalog) -> None:
    plan = QueryPlanV1(
        applicable=True,
        source="orders",
        projection=("orders.id",),
        filters=(
            {
                "column_id": "orders.total_amount",
                "operator": "GTE",
                "value": {"kind": "decimal", "value": 10.25},
            },
        ),
    )
    sql = QueryPlanV1Compiler(catalog).compile(plan).sql
    assert "WHERE t0.total_amount >= 10.25" in sql


def test_strict_parser_rejects_sql_smuggling() -> None:
    with pytest.raises(ValidationError):
        QueryPlanV1.model_validate(
            {
                "applicable": True,
                "source": "orders",
                "projection": ("orders.id",),
                "sql": "DROP TABLE orders",
            }
        )


def test_validation_rejects_disconnected_join(catalog: QueryPlanV1Catalog) -> None:
    plan = QueryPlanV1(
        applicable=True,
        source="orders",
        joins=({"relationship_id": "products.id"},),
        projection=("orders.id",),
    )
    with pytest.raises(QueryPlanV1ValidationError):
        validate_query_plan_v1(plan, catalog)


def test_validation_rejects_type_mismatch(catalog: QueryPlanV1Catalog) -> None:
    plan = QueryPlanV1(
        applicable=True,
        source="orders",
        projection=("orders.id",),
        filters=(
            {
                "column_id": "orders.id",
                "operator": "EQ",
                "value": {"kind": "string", "value": "one"},
            },
        ),
    )
    with pytest.raises(QueryPlanV1ValidationError):
        validate_query_plan_v1(plan, catalog)


@pytest.mark.asyncio
async def test_provider_plan_adapter_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAICompatibleProvider(
        Settings(_env_file=None, llm_api_key="test-key", llm_model="gpt-5.6-luna")
    )

    async def response(_: object) -> dict[str, object]:
        return {
            "model": "gpt-5.6-luna",
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"applicable":true,"source":"orders","joins":[],'
                            '"projection":["orders.id"],"filters":[]}'
                        )
                    }
                }
            ],
        }

    monkeypatch.setattr(provider, "_post", response)
    proposal = await provider.propose_query_plan_v1("show order IDs", "schema")
    assert proposal.plan.source == "orders"

    async def smuggled(_: object) -> dict[str, object]:
        return {
            "choices": [{"message": {"content": '{"applicable":true,"sql":"SELECT 1"}'}}]
        }

    monkeypatch.setattr(provider, "_post", smuggled)
    with pytest.raises(MalformedProviderResponse):
        await provider.propose_query_plan_v1("unsafe", "schema")
