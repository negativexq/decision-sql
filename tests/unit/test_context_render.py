from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.governance.context import (
    governed_context_from_schema_context,
    render_governed_context,
    serialize_governed_context,
)
from app.retrieval.context import SchemaContextResolver


def test_the_compact_form_keeps_every_fact_in_far_less_text() -> None:
    context = governed_context_from_schema_context(
        SchemaContextResolver(build_default_catalog(Base.metadata)).resolve("refunds by product")
    )
    text = render_governed_context(context)

    assert len(text) < len(serialize_governed_context(context)) / 2
    assert "attribute:" not in text and "entity:" not in text
    for entity in context.schema_catalog:
        assert f"## {entity.physical_table}" in text
    for attribute in context.attributes:
        assert f"- {attribute.physical_column_or_path} {attribute.semantic_type}" in text
    assert text.count("\n- ") >= len(context.attributes) + len(context.authorized_relationships)
    assert "policy.allowed_functions: " in text and "COUNT" in text
