from app.catalog.models import (
    ContextMetadata,
    RelevantColumn,
    RelevantRelationship,
    RelevantTable,
    SchemaContext,
    Sensitivity,
)
from app.retrieval.context import serialize_schema_context, serialize_schema_context_v2


def _context() -> SchemaContext:
    customer_id = RelevantColumn(
        table_name="public.orders",
        name="customer_id",
        type="bigint",
        description="",
        sensitivity=Sensitivity.PUBLIC,
        foreign_key_table="public.customers",
        foreign_key_column="id",
    )
    order_id = RelevantColumn(
        table_name="public.orders",
        name="id",
        type="bigint",
        description="",
        sensitivity=Sensitivity.PUBLIC,
        primary_key=True,
    )
    customer_pk = RelevantColumn(
        table_name="public.customers",
        name="id",
        type="bigint",
        description="",
        sensitivity=Sensitivity.PUBLIC,
        primary_key=True,
    )
    return SchemaContext(
        tables=(
            RelevantTable(name="public.customers", description="", columns=(customer_pk,)),
            RelevantTable(
                name="public.orders",
                description="",
                columns=(customer_id, order_id),
            ),
        ),
        relationships=(
            RelevantRelationship(
                source_table="public.orders",
                source_column="customer_id",
                target_table="public.customers",
                target_column="id",
            ),
        ),
        selected_columns=(customer_id, order_id, customer_pk),
        context_metadata=ContextMetadata(
            mode="full",
            seed_table_count=2,
            selected_table_count=2,
            selected_column_count=3,
            relationship_count=1,
        ),
    )


def test_v2_serialization_is_deterministic_and_explicit() -> None:
    context = _context()
    first = serialize_schema_context_v2(context)
    second = serialize_schema_context_v2(context)

    assert first == second
    assert "SCHEMA_CONTEXT_VERSION: 2" in first
    assert "- COLUMN_ID: public.orders.customer_id" in first
    assert "PRIMARY_KEY: true" in first
    assert "FROM_TABLE: public.orders" in first
    assert "FROM_COLUMN: customer_id" in first
    assert "TO_TABLE: public.customers" in first
    assert "TO_COLUMN: id" in first


def test_v2_is_opt_in_and_v1_output_remains_available() -> None:
    context = _context()

    v1 = serialize_schema_context(context)
    v2 = serialize_schema_context_v2(context)

    assert "[Table] public.orders" in v1
    assert "SCHEMA_CONTEXT_VERSION: 2" not in v1
    assert "SCHEMA_CONTEXT_VERSION: 2" in v2
