"""The small, server-owned semantic catalog for the M3 demo domain."""

from decimal import Decimal
from typing import TYPE_CHECKING

from app.catalog.default import build_default_catalog
from app.catalog.models import SchemaCatalog
from app.db.models import Base
from app.semantics.models import (
    Aggregation,
    Cardinality,
    ColumnPair,
    DimensionDefinition,
    EntityDefinition,
    MeasureDefinition,
    MeasureMetricDefinition,
    MetricCatalog,
    PredicateDefinition,
    PredicateOperator,
    RatioMetricDefinition,
    RelationshipDefinition,
    ZeroDenominatorPolicy,
)

if TYPE_CHECKING:
    from app.semantics.contract import SemanticContractSnapshot


def build_m3_catalog(schema: SchemaCatalog | None = None) -> MetricCatalog:
    """Build only definitions whose physical implementation exists in the demo schema."""
    schema = schema or build_default_catalog(Base.metadata)
    entities = (
        EntityDefinition(
            name="region",
            physical_table="regions",
            key_columns=("id",),
            description="A sales region.",
        ),
        EntityDefinition(
            name="sales_representative", physical_table="sales_representatives", key_columns=("id",)
        ),
        EntityDefinition(name="customer", physical_table="customers", key_columns=("id",)),
        EntityDefinition(name="product", physical_table="products", key_columns=("id",)),
        EntityDefinition(name="order", physical_table="orders", key_columns=("id",)),
        EntityDefinition(name="order_item", physical_table="order_items", key_columns=("id",)),
        EntityDefinition(name="payment", physical_table="payments", key_columns=("id",)),
        EntityDefinition(name="refund", physical_table="refunds", key_columns=("id",)),
    )
    relationships = (
        _relationship(
            "sales_representative_to_region", "sales_representative", "region", "region_id"
        ),
        _relationship("customer_to_region", "customer", "region", "region_id"),
        _relationship(
            "customer_to_sales_representative", "customer", "sales_representative", "sales_rep_id"
        ),
        _relationship("order_to_customer", "order", "customer", "customer_id"),
        _relationship(
            "order_to_sales_representative", "order", "sales_representative", "sales_rep_id"
        ),
        _relationship("order_item_to_order", "order_item", "order", "order_id"),
        _relationship("order_item_to_product", "order_item", "product", "product_id"),
        _relationship("payment_to_order", "payment", "order", "order_id"),
        _relationship("refund_to_order", "refund", "order", "order_id"),
    )
    dimensions = (
        DimensionDefinition(
            name="region", entity="region", physical_column="name", description="Sales region name."
        ),
        DimensionDefinition(
            name="sales_representative",
            entity="sales_representative",
            physical_column="name",
            description="Sales representative name.",
        ),
        DimensionDefinition(
            name="customer", entity="customer", physical_column="name", description="Customer name."
        ),
        DimensionDefinition(
            name="product_category",
            entity="product",
            physical_column="category",
            description="Product category.",
        ),
        DimensionDefinition(
            name="order_status",
            entity="order",
            physical_column="status",
            description="Order lifecycle status.",
        ),
        DimensionDefinition(
            name="order_currency",
            entity="order",
            physical_column="currency",
            description="Order currency.",
        ),
        DimensionDefinition(
            name="payment_status",
            entity="payment",
            physical_column="status",
            description="Payment status.",
        ),
        DimensionDefinition(
            name="refund_reason",
            entity="refund",
            physical_column="reason",
            description="Refund reason.",
        ),
    )
    completed = _predicate("order", "status", PredicateOperator.EQ, value="completed")
    settled = _predicate("payment", "status", PredicateOperator.EQ, value="settled")
    measures = (
        MeasureDefinition(
            name="completed_revenue",
            entity="order",
            aggregation=Aggregation.SUM,
            physical_column="total_amount",
            filters=(completed,),
            description="Revenue from completed orders.",
        ),
        MeasureDefinition(
            name="average_completed_order_value",
            entity="order",
            aggregation=Aggregation.AVG,
            physical_column="total_amount",
            filters=(completed,),
            description="Average completed order total.",
        ),
        MeasureDefinition(
            name="completed_order_count",
            entity="order",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="id",
            filters=(completed,),
            description="Distinct completed orders.",
        ),
        MeasureDefinition(
            name="all_order_count",
            entity="order",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="id",
            description="Distinct orders.",
        ),
        MeasureDefinition(
            name="settled_payment_count",
            entity="payment",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="id",
            filters=(settled,),
            description="Distinct settled payments.",
        ),
        MeasureDefinition(
            name="payment_count",
            entity="payment",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="id",
            description="Distinct payments.",
        ),
        MeasureDefinition(
            name="refunded_completed_order_count",
            entity="refund",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="order_id",
            filters=(completed,),
            description="Distinct completed orders with a refund.",
        ),
        MeasureDefinition(
            name="completed_refund_amount",
            entity="refund",
            aggregation=Aggregation.SUM,
            physical_column="amount",
            filters=(completed,),
            description="Refund amount attached to completed orders.",
        ),
        MeasureDefinition(
            name="completed_item_quantity",
            entity="order_item",
            aggregation=Aggregation.SUM,
            physical_column="quantity",
            filters=(completed,),
            description="Item quantity on completed orders.",
        ),
        MeasureDefinition(
            name="completed_order_item_order_count",
            entity="order_item",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="order_id",
            filters=(completed,),
            description="Distinct completed orders represented by line items.",
        ),
        MeasureDefinition(
            name="ordering_customer_count",
            entity="order",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="customer_id",
            description="Distinct customers with an order.",
        ),
        MeasureDefinition(
            name="customer_count",
            entity="customer",
            aggregation=Aggregation.COUNT_DISTINCT,
            physical_column="id",
            description="Distinct customers.",
        ),
        MeasureDefinition(
            name="average_payment_amount",
            entity="payment",
            aggregation=Aggregation.AVG,
            physical_column="amount",
            description="Average payment amount.",
        ),
    )
    order_dims = ("customer", "sales_representative", "order_currency")
    customer_dims = ("customer", "sales_representative")
    metrics = (
        MeasureMetricDefinition(
            name="completed_revenue",
            description="Revenue from eligible completed orders.",
            measure="completed_revenue",
            valid_dimensions=order_dims,
        ),
        MeasureMetricDefinition(
            name="average_completed_order_value",
            description="Average value of an eligible completed order.",
            measure="average_completed_order_value",
            valid_dimensions=order_dims,
        ),
        MeasureMetricDefinition(
            name="average_payment_amount",
            description="Average payment amount across all payments.",
            measure="average_payment_amount",
            valid_dimensions=order_dims,
        ),
        MeasureMetricDefinition(
            name="completed_item_quantity",
            description="Total item quantity on eligible completed orders.",
            measure="completed_item_quantity",
            valid_dimensions=("product_category", *order_dims),
        ),
        RatioMetricDefinition(
            name="payment_success_rate",
            description="Percentage of payments whose status is settled.",
            numerator_measure="settled_payment_count",
            denominator_measure="payment_count",
            valid_dimensions=order_dims,
            scale=Decimal("100"),
            zero_denominator_policy=ZeroDenominatorPolicy.NULL,
        ),
        RatioMetricDefinition(
            name="refunded_order_rate",
            description="Percentage of eligible completed orders that have a refund.",
            numerator_measure="refunded_completed_order_count",
            denominator_measure="completed_order_count",
            valid_dimensions=order_dims,
            scale=Decimal("100"),
            zero_denominator_policy=ZeroDenominatorPolicy.NULL,
        ),
        RatioMetricDefinition(
            name="refund_to_revenue_rate",
            description="Refund amount as a percentage of eligible completed-order revenue.",
            numerator_measure="completed_refund_amount",
            denominator_measure="completed_revenue",
            valid_dimensions=order_dims,
            scale=Decimal("100"),
            zero_denominator_policy=ZeroDenominatorPolicy.ZERO,
        ),
        RatioMetricDefinition(
            name="average_items_per_completed_order",
            description=(
                "Average item quantity per eligible completed order represented in the "
                "selected product or order group."
            ),
            numerator_measure="completed_item_quantity",
            denominator_measure="completed_order_item_order_count",
            valid_dimensions=("product_category", *order_dims),
            scale=Decimal("1"),
            zero_denominator_policy=ZeroDenominatorPolicy.ZERO,
        ),
        RatioMetricDefinition(
            name="completed_order_rate",
            description="Percentage of all orders that are eligible completed orders.",
            numerator_measure="completed_order_count",
            denominator_measure="all_order_count",
            valid_dimensions=order_dims,
            scale=Decimal("100"),
            zero_denominator_policy=ZeroDenominatorPolicy.NULL,
        ),
        RatioMetricDefinition(
            name="customer_order_coverage_rate",
            description="Percentage of customers represented by at least one order.",
            numerator_measure="ordering_customer_count",
            denominator_measure="customer_count",
            valid_dimensions=customer_dims,
            scale=Decimal("100"),
            zero_denominator_policy=ZeroDenominatorPolicy.NULL,
        ),
    )
    catalog = MetricCatalog(
        entities=entities,
        relationships=relationships,
        dimensions=dimensions,
        measures=measures,
        metrics=metrics,
    )
    catalog.validate_against_schema(schema)
    return catalog


def public_metric_glossary(
    catalog: MetricCatalog, semantic_contract: "SemanticContractSnapshot | None" = None
) -> str:
    from app.semantics.contract import (
        SemanticObjectKind,
        active_metric_names,
        build_semantic_contract,
    )

    contract = semantic_contract or build_semantic_contract(catalog)
    active_metrics = active_metric_names(contract)
    active_dimensions = {
        item.display_name
        for item in contract.objects
        if item.kind is SemanticObjectKind.DIMENSION
        and item.lifecycle_status.value == "ACTIVE"
    }
    lines = ["AVAILABLE GOVERNED METRICS:"]
    for metric in catalog.metrics:
        if metric.name not in active_metrics:
            continue
        lines.append(f"- {metric.name}: {metric.description}")
        lines.append(f"  valid dimensions: {', '.join(metric.valid_dimensions) or '(none)'}")
    lines.append("\nAVAILABLE SEMANTIC DIMENSIONS:")
    for dimension in catalog.dimensions:
        if dimension.name not in active_dimensions:
            continue
        lines.append(f"- {dimension.name}: {dimension.description}")
    return "\n".join(lines)


def _relationship(
    name: str, from_entity: str, to_entity: str, from_column: str
) -> RelationshipDefinition:
    target_column = "id"
    return RelationshipDefinition(
        name=name,
        from_entity=from_entity,
        to_entity=to_entity,
        column_pairs=(ColumnPair(from_column=from_column, to_column=target_column),),
        cardinality=Cardinality.MANY_TO_ONE,
    )


def _predicate(
    entity: str,
    column: str,
    operator: PredicateOperator,
    *,
    value: str | int | Decimal | None = None,
) -> PredicateDefinition:
    return PredicateDefinition(entity=entity, column=column, operator=operator, value=value)
