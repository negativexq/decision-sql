from app.catalog.default import build_default_catalog
from app.catalog.models import SchemaContext
from app.catalog.role_metadata import (
    ROLE_AWARE_METADATA,
    ColumnRole,
    serialize_role_aware_schema_context,
    validate_role_metadata,
)
from app.db.models import Base
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context


def _full_context() -> SchemaContext:
    catalog = build_default_catalog(Base.metadata)
    return SchemaContextResolver(catalog).resolve(
        "schema audit", mode=SchemaContextMode.FULL_COMPACT
    )


def test_role_metadata_covers_all_queryable_catalog_columns() -> None:
    validate_role_metadata(build_default_catalog(Base.metadata))


def test_role_aware_schema_is_deterministic_and_preserves_queryable_structure() -> None:
    context = _full_context()
    first = serialize_role_aware_schema_context(context)
    second = serialize_role_aware_schema_context(context)

    assert first == second
    assert {table.name for table in context.tables} == set(ROLE_AWARE_METADATA.tables)
    assert "customers.external_key" not in first
    assert "[Relationships]" in first
    assert "orders.customer_id INTEGER [FK -> customers.id]" in first
    assert "products.id INTEGER [PK]" in first
    assert "products.unit_price" in first
    assert "role=ENTITY_MEASURE" in first
    assert "order_items.unit_price" in first
    assert "role=TRANSACTION_MEASURE" in first
    assert "event=order_created" in first


def test_role_aware_schema_adds_roles_without_business_formulas_or_values() -> None:
    role_schema = serialize_role_aware_schema_context(_full_context()).lower()

    assert "net_revenue" not in role_schema
    assert "refund_rate" not in role_schema
    assert "sum(" not in role_schema
    assert "select " not in role_schema
    assert "california" not in role_schema
    assert "cancelled" not in role_schema


def test_baseline_serializer_remains_separate_from_role_aware_serializer() -> None:
    context = _full_context()

    assert serialize_schema_context(context) != serialize_role_aware_schema_context(context)
    assert ROLE_AWARE_METADATA.columns["products.id"].role is ColumnRole.PRIMARY_KEY
