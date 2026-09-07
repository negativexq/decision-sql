from app.catalog.models import ColumnMetadata, RelationshipMetadata, SchemaCatalog, TableMetadata
from app.grounding.query_aware import (
    GroundingKnowledge,
    QueryAwareGrounder,
    extract_value_phrases,
    normalize_tokens,
    render_grounding_context,
)


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="customers",
                description="Customers and their regions.",
                columns=(
                    ColumnMetadata(
                        name="id",
                        type="integer",
                        description="Customer identifier",
                        primary_key=True,
                    ),
                    ColumnMetadata(name="region", type="text", description="Customer region"),
                ),
                relationships=(),
            ),
            TableMetadata(
                name="orders",
                description="Orders placed by customers.",
                columns=(
                    ColumnMetadata(
                        name="id", type="integer", description="Order identifier", primary_key=True
                    ),
                    ColumnMetadata(
                        name="customer_id", type="integer", description="Customer reference"
                    ),
                    ColumnMetadata(name="status", type="text", description="Order status"),
                ),
                relationships=(
                    RelationshipMetadata(
                        column="customer_id",
                        referenced_table="customers",
                        referenced_column="id",
                    ),
                ),
            ),
        )
    )


def test_normalization_preserves_dates_numbers_and_phrases() -> None:
    assert normalize_tokens("VIP_customer 2025-01-02") == ("vip", "customer", "2025-01-02")
    assert "2025-01-02" in extract_value_phrases("created on 2025-01-02")
    assert "vip customer" in extract_value_phrases('the "VIP customer" segment')


def test_grounding_is_deterministic_and_closes_fk_bridge() -> None:
    grounder = QueryAwareGrounder(
        _catalog(),
        column_meanings={"db|customers|region": "Commercial region"},
        knowledge=(GroundingKnowledge("1", "customer region", "Meaning", "Definition"),),
    )
    first = grounder.ground("Show customer region and order status")
    second = grounder.ground("Show customer region and order status")
    assert render_grounding_context(first) == render_grounding_context(second)
    assert {table.name for table in first.selected_tables} == {"customers", "orders"}
    assert first.knowledge == (GroundingKnowledge("1", "customer region", "Meaning", "Definition"),)


def test_duplicate_knowledge_records_are_preserved() -> None:
    grounder = QueryAwareGrounder(
        _catalog(),
        knowledge=(
            GroundingKnowledge("1", "same name", "first", None),
            GroundingKnowledge("2", "same name", "second", None),
        ),
        knowledge_limit=2,
    )
    context = grounder.ground("Show customers")
    assert [entry.identifier for entry in context.knowledge] == ["1", "2"]


def test_unknown_identifiers_are_not_created_by_value_probe_builder() -> None:
    grounder = QueryAwareGrounder(_catalog())
    context = grounder.ground("Show customer region")
    assert all("." in identifier for identifier in context.value_probe_columns)
    assert all(
        identifier.split(".", 1)[0] in {"customers", "orders"}
        for identifier in context.value_probe_columns
    )
