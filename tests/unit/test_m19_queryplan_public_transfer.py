from app.semantics.query_plan_v1 import (
    QueryPlanV1Catalog,
    QueryPlanV1Column,
    QueryPlanV1Relationship,
    QueryPlanV1Table,
)
from evaluation.m19_queryplan_public_transfer import lower_gold_sql


def catalog() -> QueryPlanV1Catalog:
    return QueryPlanV1Catalog(
        tables=(
            QueryPlanV1Table(
                table_id="customers",
                description="",
                columns=(
                    QueryPlanV1Column(
                        column_id="customers.id",
                        table_id="customers",
                        name="id",
                        data_type="integer",
                        description="",
                    ),
                    QueryPlanV1Column(
                        column_id="customers.name",
                        table_id="customers",
                        name="name",
                        data_type="text",
                        description="",
                    ),
                ),
            ),
            QueryPlanV1Table(
                table_id="orders",
                description="",
                columns=(
                    QueryPlanV1Column(
                        column_id="orders.id",
                        table_id="orders",
                        name="id",
                        data_type="integer",
                        description="",
                    ),
                    QueryPlanV1Column(
                        column_id="orders.customer_id",
                        table_id="orders",
                        name="customer_id",
                        data_type="integer",
                        description="",
                    ),
                ),
            ),
        ),
        relationships=(
            QueryPlanV1Relationship(
                relationship_id="orders.customer_id__to__customers.id",
                left_table_id="orders",
                left_column_id="orders.customer_id",
                right_table_id="customers",
                right_column_id="customers.id",
            ),
        ),
    )


def test_m19_lowers_supported_gold_select() -> None:
    result = lower_gold_sql(
        "SELECT c.name FROM customers AS c WHERE c.id = 7",
        catalog(),
    )
    assert result.status == "REPRESENTABLE"
    assert result.plan is not None
    assert result.plan.projection == ("customers.name",)


def test_m19_requires_declared_relationship() -> None:
    result = lower_gold_sql(
        "SELECT c.name FROM customers AS c JOIN orders AS o ON c.id = o.id",
        catalog(),
    )
    assert result.status == "UNSUPPORTED"
    assert "UNDECLARED_RELATIONSHIP" in result.reasons


def test_m19_rejects_unsupported_boolean_shape() -> None:
    result = lower_gold_sql(
        "SELECT c.name FROM customers AS c WHERE c.id = 7 OR c.id = 8",
        catalog(),
    )
    assert result.status == "UNSUPPORTED"
    assert "OR_BOOLEAN" in result.reasons
