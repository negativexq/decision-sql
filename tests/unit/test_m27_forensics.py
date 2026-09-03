from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.retrieval.context import (
    SchemaContextMode,
    SchemaContextResolver,
    serialize_schema_context,
)
from evaluation.m27_forensics import (
    context_visibility,
    sha256_json,
    sql_signature,
    structural_diff,
)


def test_structural_signature_and_diff_are_deterministic() -> None:
    gold = sql_signature(
        "SELECT category, COUNT(*) FROM products GROUP BY category ORDER BY category"
    )
    generated = sql_signature(
        "SELECT category, COUNT(*) FROM products GROUP BY category ORDER BY category DESC"
    )

    assert gold["tables"] == ("products",)
    assert gold["group_by"] == ("category",)
    difference = structural_diff(gold, generated)
    assert "ORDER_DIRECTION_DIFFERENT" in difference
    assert sha256_json(gold) == sha256_json(
        sql_signature(
            "SELECT category, COUNT(*) FROM products GROUP BY category ORDER BY category"
        )
    )


def test_full_context_exposes_gold_schema_objects_and_relationships() -> None:
    catalog = build_default_catalog(Base.metadata)
    context = SchemaContextResolver(catalog).resolve(
        "show orders by customers and products", mode=SchemaContextMode.FULL_COMPACT
    )
    signature = sql_signature(
        "SELECT c.name, SUM(oi.quantity) FROM customers c "
        "JOIN orders o ON o.customer_id = c.id "
        "JOIN order_items oi ON oi.order_id = o.id GROUP BY c.name"
    )

    visibility = context_visibility(signature, context)
    assert visibility["gold_tables_visible"] is True
    assert visibility["gold_columns_visible"] is True
    assert visibility["gold_relationships_visible"] is True
    serialized = serialize_schema_context(context)
    assert "customers.external_key" not in serialized
