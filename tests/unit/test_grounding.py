import pytest

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.intent import IntentJoin, QueryIntent
from app.retrieval.context import SchemaContextResolver
from app.text_to_sql.grounding import (
    IntentVisibilityError,
    compare_intent_to_sql,
    validate_intent_visibility,
)


def product_context():
    catalog = build_default_catalog(Base.metadata)
    return SchemaContextResolver(catalog).resolve("list products")


def test_query_intent_visibility_allows_only_context_objects() -> None:
    context = product_context()
    intent = QueryIntent(
        selected_tables=("products",), selected_columns=("products.name",)
    )

    validate_intent_visibility(intent, context)


def test_query_intent_visibility_rejects_hidden_or_missing_objects() -> None:
    context = product_context()
    intent = QueryIntent(
        selected_tables=("customers",), selected_columns=("customers.external_key",)
    )

    with pytest.raises(IntentVisibilityError):
        validate_intent_visibility(intent, context)


def test_query_intent_visibility_rejects_invalid_join() -> None:
    context = product_context()
    intent = QueryIntent(
        selected_tables=("products",),
        joins=(
            IntentJoin(
                source_table="products",
                source_column="missing_id",
                target_table="products",
                target_column="id",
            ),
        ),
    )

    with pytest.raises(IntentVisibilityError):
        validate_intent_visibility(intent, context)


def test_intent_sql_diagnostics_report_limit_and_table_column_disagreement() -> None:
    intent = QueryIntent(
        selected_tables=("products",),
        selected_columns=("products.name",),
        limit=5,
    )

    diagnostics = compare_intent_to_sql(intent, "SELECT id FROM products")

    assert diagnostics.intent_columns_not_used == ("products.name",)
    assert diagnostics.sql_columns_not_in_intent == ("id",)
    assert diagnostics.limit_agreement is False
