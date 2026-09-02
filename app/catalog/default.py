from sqlalchemy import MetaData

from app.catalog.models import (
    ColumnMetadata,
    RelationshipMetadata,
    SchemaCatalog,
    Sensitivity,
    TableMetadata,
)

TABLE_DESCRIPTIONS = {
    "regions": "Sales regions used for geographic analysis.",
    "sales_representatives": "Sales representatives responsible for customers and orders.",
    "customers": "Synthetic customer accounts.",
    "products": "Products sold through the commerce domain.",
    "orders": "Customer orders and governed order-level revenue amounts.",
    "order_items": "Product line items belonging to orders.",
    "payments": "Payment transactions associated with orders.",
    "refunds": "Refund transactions associated with orders.",
}

TABLE_ALIASES = {
    "regions": ("region", "geography"),
    "sales_representatives": ("representative", "rep", "salesperson"),
    "customers": ("customer", "buyer", "account"),
    "products": ("product", "item", "sku"),
    "orders": ("order", "sales", "sale"),
    "order_items": ("line_item", "line", "order line"),
    "payments": ("payment", "transaction"),
    "refunds": ("refund", "return"),
}

COLUMN_DESCRIPTIONS = {
    "customers.external_key": "External customer identifier; not queryable in M1.",
}


def build_default_catalog(metadata: MetaData) -> SchemaCatalog:
    tables = []
    for table in metadata.sorted_tables:
        columns = []
        for column in table.columns:
            key = f"{table.name}.{column.name}"
            columns.append(
                ColumnMetadata(
                    name=column.name,
                    type=str(column.type),
                    description=COLUMN_DESCRIPTIONS.get(key, f"{column.name} from {table.name}."),
                    sensitivity=(
                        Sensitivity.INTERNAL
                        if key == "customers.external_key"
                        else Sensitivity.PUBLIC
                    ),
                    queryable=key != "customers.external_key",
                    primary_key=column.primary_key,
                )
            )
        relationships = [
            RelationshipMetadata(
                column=foreign_key.parent.name,
                referenced_table=foreign_key.column.table.name,
                referenced_column=foreign_key.column.name,
            )
            for column in table.columns
            for foreign_key in column.foreign_keys
        ]
        tables.append(
            TableMetadata(
                name=table.name,
                description=TABLE_DESCRIPTIONS.get(table.name, f"{table.name} application table."),
                aliases=TABLE_ALIASES.get(table.name, ()),
                columns=tuple(columns),
                relationships=tuple(relationships),
            )
        )
    return SchemaCatalog(tables=tuple(tables))
