"""Static, structural role metadata for the M2.9 schema ablation."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.models import SchemaCatalog, SchemaContext


class ColumnRole(StrEnum):
    PRIMARY_KEY = "PRIMARY_KEY"
    FOREIGN_KEY = "FOREIGN_KEY"
    IDENTIFIER = "IDENTIFIER"
    DISPLAY_DIMENSION = "DISPLAY_DIMENSION"
    CATEGORICAL_DIMENSION = "CATEGORICAL_DIMENSION"
    TRANSACTION_MEASURE = "TRANSACTION_MEASURE"
    ENTITY_MEASURE = "ENTITY_MEASURE"
    MONETARY_MEASURE = "MONETARY_MEASURE"
    EVENT_TIMESTAMP = "EVENT_TIMESTAMP"
    STATUS = "STATUS"
    QUANTITY = "QUANTITY"


class Additivity(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class RoleAwareTableMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity: str = Field(min_length=1)
    grain: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class RoleAwareColumnMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: ColumnRole
    meaning: str = Field(min_length=1)
    grain_scope: str = Field(min_length=1)
    additive: Additivity = Additivity.UNKNOWN
    event: str | None = None


class RoleAwareMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    tables: dict[str, RoleAwareTableMetadata]
    columns: dict[str, RoleAwareColumnMetadata]


ROLE_AWARE_METADATA = RoleAwareMetadata(
    tables={
        "regions": RoleAwareTableMetadata(
            entity="region",
            grain="one row per sales region",
            purpose="geographic sales grouping",
        ),
        "sales_representatives": RoleAwareTableMetadata(
            entity="sales representative",
            grain="one row per sales representative",
            purpose="representative ownership attributes",
        ),
        "customers": RoleAwareTableMetadata(
            entity="customer",
            grain="one row per customer account",
            purpose="customer account attributes",
        ),
        "products": RoleAwareTableMetadata(
            entity="product",
            grain="one row per product",
            purpose="product catalog attributes",
        ),
        "orders": RoleAwareTableMetadata(
            entity="order",
            grain="one row per customer order",
            purpose="order-level transaction records",
        ),
        "order_items": RoleAwareTableMetadata(
            entity="order item",
            grain="one row per purchased product line within an order",
            purpose="line-level purchase records",
        ),
        "payments": RoleAwareTableMetadata(
            entity="payment",
            grain="one row per payment transaction",
            purpose="payment transaction records",
        ),
        "refunds": RoleAwareTableMetadata(
            entity="refund",
            grain="one row per refund transaction",
            purpose="refund transaction records",
        ),
    },
    columns={
        "regions.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY, meaning="region row identifier", grain_scope="region"
        ),
        "regions.name": RoleAwareColumnMetadata(
            role=ColumnRole.DISPLAY_DIMENSION,
            meaning="human-readable region name",
            grain_scope="region",
        ),
        "sales_representatives.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY,
            meaning="sales representative row identifier",
            grain_scope="sales representative",
        ),
        "sales_representatives.name": RoleAwareColumnMetadata(
            role=ColumnRole.DISPLAY_DIMENSION,
            meaning="human-readable representative name",
            grain_scope="sales representative",
        ),
        "sales_representatives.region_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="region referenced by the representative",
            grain_scope="sales representative",
        ),
        "customers.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY, meaning="customer row identifier", grain_scope="customer"
        ),
        "customers.external_key": RoleAwareColumnMetadata(
            role=ColumnRole.IDENTIFIER,
            meaning="external customer identifier",
            grain_scope="customer",
        ),
        "customers.name": RoleAwareColumnMetadata(
            role=ColumnRole.DISPLAY_DIMENSION,
            meaning="human-readable customer name",
            grain_scope="customer",
        ),
        "customers.region_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="region referenced by the customer",
            grain_scope="customer",
        ),
        "customers.sales_rep_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="representative referenced by the customer",
            grain_scope="customer",
        ),
        "customers.created_at": RoleAwareColumnMetadata(
            role=ColumnRole.EVENT_TIMESTAMP,
            meaning="time the customer account was created",
            grain_scope="customer",
            event="customer_created",
        ),
        "products.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY, meaning="product row identifier", grain_scope="product"
        ),
        "products.sku": RoleAwareColumnMetadata(
            role=ColumnRole.IDENTIFIER,
            meaning="external product stock-keeping identifier",
            grain_scope="product",
        ),
        "products.name": RoleAwareColumnMetadata(
            role=ColumnRole.DISPLAY_DIMENSION,
            meaning="human-readable product name",
            grain_scope="product",
        ),
        "products.category": RoleAwareColumnMetadata(
            role=ColumnRole.CATEGORICAL_DIMENSION,
            meaning="product category label",
            grain_scope="product",
        ),
        "products.unit_price": RoleAwareColumnMetadata(
            role=ColumnRole.ENTITY_MEASURE,
            meaning="catalog unit price stored on the product record",
            grain_scope="product",
        ),
        "orders.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY, meaning="order row identifier", grain_scope="order"
        ),
        "orders.order_number": RoleAwareColumnMetadata(
            role=ColumnRole.IDENTIFIER,
            meaning="external order identifier",
            grain_scope="order",
        ),
        "orders.customer_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="customer referenced by the order",
            grain_scope="order",
        ),
        "orders.sales_rep_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="representative referenced by the order",
            grain_scope="order",
        ),
        "orders.ordered_at": RoleAwareColumnMetadata(
            role=ColumnRole.EVENT_TIMESTAMP,
            meaning="time the order was created",
            grain_scope="order",
            event="order_created",
        ),
        "orders.status": RoleAwareColumnMetadata(
            role=ColumnRole.STATUS,
            meaning="lifecycle status stored on the order",
            grain_scope="order",
        ),
        "orders.currency": RoleAwareColumnMetadata(
            role=ColumnRole.CATEGORICAL_DIMENSION,
            meaning="currency code stored on the order",
            grain_scope="order",
        ),
        "orders.subtotal": RoleAwareColumnMetadata(
            role=ColumnRole.MONETARY_MEASURE,
            meaning="pre-discount monetary amount stored on the order",
            grain_scope="order",
        ),
        "orders.discount_amount": RoleAwareColumnMetadata(
            role=ColumnRole.MONETARY_MEASURE,
            meaning="discount monetary amount stored on the order",
            grain_scope="order",
        ),
        "orders.total_amount": RoleAwareColumnMetadata(
            role=ColumnRole.MONETARY_MEASURE,
            meaning="order-level monetary total stored on the order",
            grain_scope="order",
        ),
        "order_items.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY,
            meaning="order item row identifier",
            grain_scope="order item",
        ),
        "order_items.order_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="order referenced by the line item",
            grain_scope="order item",
        ),
        "order_items.product_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="product referenced by the line item",
            grain_scope="order item",
        ),
        "order_items.quantity": RoleAwareColumnMetadata(
            role=ColumnRole.QUANTITY,
            meaning="number of units on the purchased line",
            grain_scope="order item",
            additive=Additivity.TRUE,
        ),
        "order_items.unit_price": RoleAwareColumnMetadata(
            role=ColumnRole.TRANSACTION_MEASURE,
            meaning="unit price charged on this purchased line",
            grain_scope="order item",
        ),
        "order_items.discount_amount": RoleAwareColumnMetadata(
            role=ColumnRole.MONETARY_MEASURE,
            meaning="discount monetary amount on this purchased line",
            grain_scope="order item",
        ),
        "payments.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY,
            meaning="payment row identifier",
            grain_scope="payment",
        ),
        "payments.order_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="order referenced by the payment",
            grain_scope="payment",
        ),
        "payments.paid_at": RoleAwareColumnMetadata(
            role=ColumnRole.EVENT_TIMESTAMP,
            meaning="time the payment was recorded",
            grain_scope="payment",
            event="payment_recorded",
        ),
        "payments.amount": RoleAwareColumnMetadata(
            role=ColumnRole.MONETARY_MEASURE,
            meaning="monetary amount recorded by the payment",
            grain_scope="payment",
        ),
        "payments.status": RoleAwareColumnMetadata(
            role=ColumnRole.STATUS,
            meaning="lifecycle status stored on the payment",
            grain_scope="payment",
        ),
        "refunds.id": RoleAwareColumnMetadata(
            role=ColumnRole.PRIMARY_KEY, meaning="refund row identifier", grain_scope="refund"
        ),
        "refunds.order_id": RoleAwareColumnMetadata(
            role=ColumnRole.FOREIGN_KEY,
            meaning="order referenced by the refund",
            grain_scope="refund",
        ),
        "refunds.refunded_at": RoleAwareColumnMetadata(
            role=ColumnRole.EVENT_TIMESTAMP,
            meaning="time the refund was recorded",
            grain_scope="refund",
            event="refund_recorded",
        ),
        "refunds.amount": RoleAwareColumnMetadata(
            role=ColumnRole.MONETARY_MEASURE,
            meaning="monetary amount recorded by the refund",
            grain_scope="refund",
        ),
        "refunds.reason": RoleAwareColumnMetadata(
            role=ColumnRole.CATEGORICAL_DIMENSION,
            meaning="refund reason label",
            grain_scope="refund",
        ),
    },
)


def validate_role_metadata(catalog: SchemaCatalog) -> None:
    """Ensure every catalog table/queryable column has static role metadata."""
    for table in catalog.tables:
        if table.name not in ROLE_AWARE_METADATA.tables:
            raise ValueError(f"Missing role metadata for table {table.name}")
        for column in table.columns:
            key = f"{table.name}.{column.name}"
            if column.queryable and key not in ROLE_AWARE_METADATA.columns:
                raise ValueError(f"Missing role metadata for queryable column {key}")


def serialize_role_aware_schema_context(context: SchemaContext) -> str:
    """Serialize only the server-approved context with concise structural roles."""
    lines: list[str] = []
    for table in sorted(context.tables, key=lambda item: item.name):
        table_meta = ROLE_AWARE_METADATA.tables[table.name]
        lines.extend(
            (
                f"[Table] {table.name}",
                f"Description: {table.description}",
                f"Entity: {table_meta.entity}",
                f"Grain: {table_meta.grain}",
                f"Purpose: {table_meta.purpose}",
                "[Columns]",
            )
        )
        for column in sorted(table.columns, key=lambda item: item.name):
            key = f"{table.name}.{column.name}"
            metadata = ROLE_AWARE_METADATA.columns[key]
            annotations: list[str] = []
            if column.primary_key:
                annotations.append("PK")
            if column.foreign_key_table and column.foreign_key_column:
                annotations.append(f"FK -> {column.foreign_key_table}.{column.foreign_key_column}")
            annotation_text = f" [{', '.join(annotations)}]" if annotations else ""
            optional = [f"additive={metadata.additive.value}"]
            if metadata.event:
                optional.append(f"event={metadata.event}")
            lines.append(
                f"- {key} {column.type}{annotation_text} role={metadata.role.value} "
                f"grain={metadata.grain_scope} meaning={metadata.meaning} "
                f"({', '.join(optional)})"
            )
        lines.append("")
    lines.append("[Relationships]")
    for relationship in sorted(
        context.relationships,
        key=lambda item: (item.source_table, item.source_column, item.target_table),
    ):
        lines.append(
            f"- {relationship.source_table}.{relationship.source_column} -> "
            f"{relationship.target_table}.{relationship.target_column}"
        )
    return "\n".join(lines).strip()
