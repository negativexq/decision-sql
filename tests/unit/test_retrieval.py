from app.catalog.default import build_default_catalog
from app.catalog.models import SchemaContext
from app.db.models import Base
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context


def resolver(**overrides: int) -> SchemaContextResolver:
    values = {
        "top_k": 3,
        "max_tables": 5,
        "max_columns_per_table": 4,
        "relationship_depth": 2,
    }
    values.update(overrides)
    return SchemaContextResolver(build_default_catalog(Base.metadata), **values)


def test_retrieval_matches_table_description_and_expands_bridge_relationships() -> None:
    context = resolver().resolve("Which products generated the most revenue?")
    names = {table.name for table in context.tables}

    assert "products" in names
    assert "order_items" in names
    assert context.context_metadata.selected_table_count <= 5
    assert any(
        relationship.source_table == "order_items"
        and relationship.target_table == "products"
        for relationship in context.relationships
    )


def test_context_pruning_is_bounded_and_excludes_non_queryable_columns() -> None:
    context = resolver(max_columns_per_table=3).resolve("customer names and regions")

    assert all(len(table.columns) <= 3 for table in context.tables)
    assert all(column.name != "external_key" for column in context.selected_columns)
    assert all(column.sensitivity.value != "internal" for column in context.selected_columns)


def test_context_serialization_is_stable_and_contains_metadata_only() -> None:
    first = resolver().resolve("revenue by product")
    second = resolver().resolve("revenue by product")

    assert first == second
    assert isinstance(first, SchemaContext)
    serialized = serialize_schema_context(first)
    assert serialized == serialize_schema_context(second)
    assert "external_key" not in serialized
    assert "Description:" in serialized
    assert "[Relationships]" in serialized
    assert "[Table]" in serialized
    assert "products.id" in serialized
    assert "sample" not in serialized.lower()


def test_full_mode_contains_all_queryable_tables_without_hidden_columns() -> None:
    context = resolver().resolve("anything", mode=SchemaContextMode.FULL_COMPACT)

    assert context.context_metadata.mode == "full"
    assert {table.name for table in context.tables} == {
        "customers",
        "order_items",
        "orders",
        "payments",
        "products",
        "refunds",
        "regions",
        "sales_representatives",
    }
    assert all(column.name != "external_key" for column in context.selected_columns)
    orders = next(table for table in context.tables if table.name == "orders")
    assert len(orders.columns) == 10


def test_full_compact_serialization_exposes_pk_fk_structure() -> None:
    context = resolver().resolve("anything", mode=SchemaContextMode.FULL_COMPACT)
    serialized = serialize_schema_context(context)

    assert "- orders.id " in serialized
    assert "[PK]" in next(line for line in serialized.splitlines() if "orders.id " in line)
    assert "[FK -> customers.id]" in serialized
    assert "- customers.external_key" not in serialized


def test_context_mode_compatibility_aliases_are_canonical() -> None:
    assert SchemaContextMode.FULL is SchemaContextMode.FULL_COMPACT
    assert SchemaContextMode.RETRIEVED is SchemaContextMode.RETRIEVED_BOUNDED
