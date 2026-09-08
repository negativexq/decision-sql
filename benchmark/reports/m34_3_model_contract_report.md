# M34.3 Model Contract and Evaluation Harness Report

Dry-run only. No provider was called and no model output was generated.

## Frozen hashes

- Benchmark content: `f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8`
- Governance instructions: `cfa5ae7fe35c93a1ecc42301c2195f47938eac892a051fa17bcd6e1d6c47722c`
- Submission schema: `6ae863d93d6f0d8e927b0234c6a737ec0eed7726da7152769a81b9cd80cc9818`
- Case order: `6d31587c9726983492e022c1b731cfdc4e5f5e214dd240f289d417358621ed83`
- Context hashes: {"commerce_ops": "f94bc724e404723969a74a5cfee0b02dd447112559ba9f00d39a54a90615417e", "fleet_ops": "1fde312b7bc723ba877b26720519b9c123e347ec4bd1069f8ebb142d4cfc4ea6", "support_ops": "85894e185978949774810b2fdcfbf92fdf325bdebbff36897d2efde09c5bf375"}

## Dry-run gates

- Requests built: 30/30
- Request schema validation: 30/30
- Instructions/question/context present: {'instructions': 30, 'question': 30, 'serialized_context': 30}
- Answerable actual-request context sufficiency: 20/20
- Unique governed contexts: 3
- Context bytes: {"commerce_ops": 16118, "fleet_ops": 18698, "support_ops": 18548}
- Same database byte-identical context: True
- Policy visibility: 2/2
- Evaluator-only leakage: 0
- Denied authority leakage: 0
- Ambiguity interpretation leakage: 0
- Provider calls: 0

## ResultContract feature audit

- answerable_base_and_counterfactual_execution: SUPPORTED_AND_USED
- non_answerable_sql_absent: SUPPORTED_AND_USED
- governed_decision_mapping: SUPPORTED_AND_USED
- row_order: SUPPORTED_AND_USED
- numeric_tolerance: IMPLEMENTED_BUT_UNUSED_IN_PILOT
- aliases_significant: DECLARED_BUT_UNSUPPORTED_FOR_CANDIDATE_SCORING; UNUSED_IN_PILOT
- duplicates_significant_false: DECLARED_BUT_UNSUPPORTED; UNUSED_IN_PILOT
- gold_sql_string_comparison: NOT_USED
- llm_judge_or_semantic_repair: NOT_USED

## Three representative model requests

The following are the first case in each database's frozen order; selection is not based on gold behavior.

### commerce_01 (commerce_ops)

```text
SYSTEM:
# Decision-SQL governed context instructions v1

You are answering one independent Decision-SQL benchmark question. Use only the question and the complete `GOVERNED_CONTEXT_V1` supplied with it.

## Governance rules

- Use only relationships listed in `authorized_relationships`.
- Do not infer additional relationships from matching column names, matching data types, overlapping values, numeric identifiers, email addresses, codes, or apparent foreign-key structure.
- If answering the request requires a relationship that is not authorized, return `BLOCKED_AUTHORITY` and no SQL.
- If more than one materially different interpretation remains consistent with the question and visible context, and visible business rules do not resolve it, return `NEEDS_CLARIFICATION` and no SQL. Do not guess.
- If the requested operation violates the visible read-only policy, return `BLOCKED_POLICY` and no SQL. Do not rewrite a write request into a different SELECT.

## Decisions and output

Allowed decisions are exactly:

- `ANSWER`: the question is sufficiently specified and can be answered using only the visible schema, attributes, authorized relationships, metrics, business rules, temporal rules, and policy.
- `BLOCKED_AUTHORITY`: the intent is clear, but an authority or relationship required by the request is absent from the authorized context.
- `NEEDS_CLARIFICATION`: materially different interpretations remain unresolved.
- `BLOCKED_POLICY`: the requested operation violates the visible read-only policy.

Return one JSON object with exactly these four fields:

```json
{
  "case_id": "the supplied case identifier",
  "decision": "ANSWER | BLOCKED_AUTHORITY | NEEDS_CLARIFICATION | BLOCKED_POLICY",
  "sql": "one PostgreSQL SELECT statement or null",
  "reason_code": null
}
```

For `ANSWER`, `sql` must be one non-empty read-only PostgreSQL 16 `SELECT` statement and `reason_code` must be `null`. Do not include commentary, multiple candidates, analysis, a logical plan, a confidence value, or a second statement in `sql`.

For `BLOCKED_AUTHORITY`, use `sql: null` and `reason_code: "MISSING_AUTHORIZED_RELATIONSHIP"`.

For `NEEDS_CLARIFICATION`, use `sql: null` and `reason_code: "AMBIGUOUS_SEMANTICS"`.

For `BLOCKED_POLICY`, use `sql: null` and `reason_code: "READ_ONLY_POLICY"`.

The only other permitted reason code is `NO_REASON`; it is not needed for the four canonical decisions above. Do not return unknown decisions or reason codes.

The database context is complete at the database level. Use the physical table names, physical columns or JSON paths, data types, semantic descriptions, and authorized joins exactly as supplied. Each case is independent: do not use memory from another case and do not expect benchmark feedback.


USER:
Question:
List active customers in the North region with their customer identifiers and names.

Governed context:
{
  "attributes": [
    {
      "attribute_id": "attribute:commerce_ops:customers:customer_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:customers",
      "nullable": false,
      "physical_column_or_path": "customer_id",
      "semantic_description": "Stable customer identifier."
    },
    {
      "attribute_id": "attribute:commerce_ops:customers:customer_name",
      "data_type": "TEXT",
      "entity_id": "entity:commerce_ops:customers",
      "nullable": false,
      "physical_column_or_path": "customer_name",
      "semantic_description": "Display name."
    },
    {
      "attribute_id": "attribute:commerce_ops:customers:is_active",
      "data_type": "BOOLEAN",
      "entity_id": "entity:commerce_ops:customers",
      "nullable": false,
      "physical_column_or_path": "is_active",
      "semantic_description": "Whether the account is currently active."
    },
    {
      "attribute_id": "attribute:commerce_ops:customers:legacy_external_code",
      "data_type": "TEXT",
      "entity_id": "entity:commerce_ops:customers",
      "nullable": true,
      "physical_column_or_path": "legacy_external_code",
      "semantic_description": "Legacy code with no authorized cross-entity relationship."
    },
    {
      "attribute_id": "attribute:commerce_ops:customers:region",
      "data_type": "TEXT",
      "entity_id": "entity:commerce_ops:customers",
      "nullable": false,
      "physical_column_or_path": "region",
      "semantic_description": "Assigned service region."
    },
    {
      "attribute_id": "attribute:commerce_ops:customers:signup_date",
      "data_type": "DATE",
      "entity_id": "entity:commerce_ops:customers",
      "nullable": false,
      "physical_column_or_path": "signup_date",
      "semantic_description": "Date the customer joined."
    },
    {
      "attribute_id": "attribute:commerce_ops:order_items:item_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:order_items",
      "nullable": false,
      "physical_column_or_path": "item_id",
      "semantic_description": "Stable line identifier."
    },
    {
      "attribute_id": "attribute:commerce_ops:order_items:order_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:order_items",
      "nullable": false,
      "physical_column_or_path": "order_id",
      "semantic_description": "Authorized order reference."
    },
    {
      "attribute_id": "attribute:commerce_ops:order_items:product_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:order_items",
      "nullable": false,
      "physical_column_or_path": "product_id",
      "semantic_description": "Authorized product reference."
    },
    {
      "attribute_id": "attribute:commerce_ops:order_items:quantity",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:order_items",
      "nullable": false,
      "physical_column_or_path": "quantity",
      "semantic_description": "Units in the line."
    },
    {
      "attribute_id": "attribute:commerce_ops:order_items:unit_price",
      "data_type": "NUMERIC",
      "entity_id": "entity:commerce_ops:order_items",
      "nullable": false,
      "physical_column_or_path": "unit_price",
      "semantic_description": "Captured unit price."
    },
    {
      "attribute_id": "attribute:commerce_ops:orders:customer_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:orders",
      "nullable": false,
      "physical_column_or_path": "customer_id",
      "semantic_description": "Authorized customer reference."
    },
    {
      "attribute_id": "attribute:commerce_ops:orders:discount_pct",
      "data_type": "NUMERIC",
      "entity_id": "entity:commerce_ops:orders",
      "nullable": false,
      "physical_column_or_path": "discount_pct",
      "semantic_description": "Percentage discount applied to line value."
    },
    {
      "attribute_id": "attribute:commerce_ops:orders:external_customer_code",
      "data_type": "TEXT",
      "entity_id": "entity:commerce_ops:orders",
      "nullable": true,
      "physical_column_or_path": "external_customer_code",
      "semantic_description": "External code; not an authorized customer join key."
    },
    {
      "attribute_id": "attribute:commerce_ops:orders:order_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:orders",
      "nullable": false,
      "physical_column_or_path": "order_id",
      "semantic_description": "Stable order identifier."
    },
    {
      "attribute_id": "attribute:commerce_ops:orders:ordered_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:commerce_ops:orders",
      "nullable": false,
      "physical_column_or_path": "ordered_at",
      "semantic_description": "UTC order creation time."
    },
    {
      "attribute_id": "attribute:commerce_ops:orders:status",
      "data_type": "TEXT",
      "entity_id": "entity:commerce_ops:orders",
      "nullable": false,
      "physical_column_or_path": "status",
      "semantic_description": "Order lifecycle status."
    },
    {
      "attribute_id": "attribute:commerce_ops:orders:store_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:orders",
      "nullable": false,
      "physical_column_or_path": "store_id",
      "semantic_description": "Authorized store reference."
    },
    {
      "attribute_id": "attribute:commerce_ops:products:category",
      "data_type": "TEXT",
      "entity_id": "entity:commerce_ops:products",
      "nullable": false,
      "physical_column_or_path": "category",
      "semantic_description": "Catalog category."
    },
    {
      "attribute_id": "attribute:commerce_ops:products:product_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:products",
      "nullable": false,
      "physical_column_or_path": "product_id",
      "semantic_description": "Stable product identifier."
    },
    {
      "attribute_id": "attribute:commerce_ops:products:unit_price",
      "data_type": "NUMERIC",
      "entity_id": "entity:commerce_ops:products",
      "nullable": false,
      "physical_column_or_path": "unit_price",
      "semantic_description": "Current catalog unit price."
    },
    {
      "attribute_id": "attribute:commerce_ops:shipments:order_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:shipments",
      "nullable": false,
      "physical_column_or_path": "order_id",
      "semantic_description": "Authorized order reference."
    },
    {
      "attribute_id": "attribute:commerce_ops:shipments:shipment_id",
      "data_type": "INTEGER",
      "entity_id": "entity:commerce_ops:shipments",
      "nullable": false,
      "physical_column_or_path": "shipment_id",
      "semantic_description": "Stable shipment identifier."
    },
    {
      "attribute_id": "attribute:commerce_ops:shipments:shipped_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:commerce_ops:shipments",
      "nullable": true,
      "physical_column_or_path": "shipped_at",
      "semantic_description": "UTC shipment time."
    },
    {
      "attribute_id": "attribute:commerce_ops:shipments:status",
      "data_type": "TEXT",
      "entity_id": "entity:commerce_ops:shipments",
      "nullable": false,
      "physical_column_or_path": "status",
      "semantic_description": "Shipment lifecycle status."
    }
  ],
  "authorized_relationships": [
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Inventory observes products.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:inventory_snapshots:product_id",
      "left_entity": "entity:commerce_ops:inventory_snapshots",
      "relationship_id": "relationship:commerce_ops:inventory_product",
      "right_attribute": "attribute:commerce_ops:products:product_id",
      "right_entity": "entity:commerce_ops:products"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Inventory is observed at warehouses.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:inventory_snapshots:warehouse_id",
      "left_entity": "entity:commerce_ops:inventory_snapshots",
      "relationship_id": "relationship:commerce_ops:inventory_warehouse",
      "right_attribute": "attribute:commerce_ops:warehouses:warehouse_id",
      "right_entity": "entity:commerce_ops:warehouses"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Lines belong to orders.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:order_items:order_id",
      "left_entity": "entity:commerce_ops:order_items",
      "relationship_id": "relationship:commerce_ops:item_order",
      "right_attribute": "attribute:commerce_ops:orders:order_id",
      "right_entity": "entity:commerce_ops:orders"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Lines identify products.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:order_items:product_id",
      "left_entity": "entity:commerce_ops:order_items",
      "relationship_id": "relationship:commerce_ops:item_product",
      "right_attribute": "attribute:commerce_ops:products:product_id",
      "right_entity": "entity:commerce_ops:products"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Orders belong to their customer.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:orders:customer_id",
      "left_entity": "entity:commerce_ops:orders",
      "relationship_id": "relationship:commerce_ops:order_customer",
      "right_attribute": "attribute:commerce_ops:customers:customer_id",
      "right_entity": "entity:commerce_ops:customers"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Orders are fulfilled by a store.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:orders:store_id",
      "left_entity": "entity:commerce_ops:orders",
      "relationship_id": "relationship:commerce_ops:order_store",
      "right_attribute": "attribute:commerce_ops:stores:store_id",
      "right_entity": "entity:commerce_ops:stores"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Payments describe orders.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:payment_events:order_id",
      "left_entity": "entity:commerce_ops:payment_events",
      "relationship_id": "relationship:commerce_ops:payment_order",
      "right_attribute": "attribute:commerce_ops:orders:order_id",
      "right_entity": "entity:commerce_ops:orders"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Returns belong to orders.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:returns:order_id",
      "left_entity": "entity:commerce_ops:returns",
      "relationship_id": "relationship:commerce_ops:return_order",
      "right_attribute": "attribute:commerce_ops:orders:order_id",
      "right_entity": "entity:commerce_ops:orders"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Shipments belong to orders.",
      "direction": "left_to_right",
      "left_attribute": "attribute:commerce_ops:shipments:order_id",
      "left_entity": "entity:commerce_ops:shipments",
      "relationship_id": "relationship:commerce_ops:shipment_order",
      "right_attribute": "attribute:commerce_ops:orders:order_id",
      "right_entity": "entity:commerce_ops:orders"
    }
  ],
  "business_rules": [
    {
      "definition": "Only orders.status = 'completed' are completed.",
      "name": "Completed order",
      "rule_id": "rule:completed_orders"
    },
    {
      "definition": "Discount is applied per line before summing; the result is rounded to two decimal places before ordering.",
      "name": "Net value",
      "rule_id": "rule:net_value"
    }
  ],
  "context_profile": "GOVERNED_CONTEXT_V1",
  "database_id": "commerce_ops",
  "metrics": [
    {
      "default_policy": "no default",
      "description": "Completed-order captured line value after the order discount.",
      "formula": "SUM(order_items.quantity * order_items.unit_price * (1 - orders.discount_pct / 100))",
      "grain": "order_item",
      "metric_id": "metric:commerce_net_order_value",
      "name": "Net order value",
      "null_policy": "null operands are excluded by ordinary arithmetic",
      "operands": [
        "quantity",
        "unit_price",
        "discount_pct"
      ],
      "precision": 2,
      "rounding_stage": "before_ordering_and_display",
      "temporal_basis": "ordered_at"
    }
  ],
  "policy": {
    "allowed_statement": "one read-only SELECT statement",
    "blocked_decisions": [
      "BLOCKED_POLICY"
    ],
    "forbidden_objects": [
      "pg_authid",
      "secret_tokens"
    ],
    "forbidden_operations": [
      "INSERT",
      "UPDATE",
      "DELETE",
      "MERGE",
      "DDL",
      "COPY",
      "locking reads"
    ],
    "policy_id": "policy:commerce_ops:readonly"
  },
  "schema_catalog": [
    {
      "description": "A buyer account.",
      "entity_id": "entity:commerce_ops:customers",
      "human_name": "Customers",
      "physical_table": "customers",
      "primary_semantic_role": "anchor entity"
    },
    {
      "description": "A dated inventory observation.",
      "entity_id": "entity:commerce_ops:inventory_snapshots",
      "human_name": "Inventory snapshots",
      "physical_table": "inventory_snapshots",
      "primary_semantic_role": "snapshot entity"
    },
    {
      "description": "A product line in an order.",
      "entity_id": "entity:commerce_ops:order_items",
      "human_name": "Order items",
      "physical_table": "order_items",
      "primary_semantic_role": "transaction detail"
    },
    {
      "description": "A customer purchase order.",
      "entity_id": "entity:commerce_ops:orders",
      "human_name": "Orders",
      "physical_table": "orders",
      "primary_semantic_role": "transaction entity"
    },
    {
      "description": "A payment lifecycle event.",
      "entity_id": "entity:commerce_ops:payment_events",
      "human_name": "Payment events",
      "physical_table": "payment_events",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A sellable catalog item.",
      "entity_id": "entity:commerce_ops:products",
      "human_name": "Products",
      "physical_table": "products",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "A returned portion of an order.",
      "entity_id": "entity:commerce_ops:returns",
      "human_name": "Returns",
      "physical_table": "returns",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A fulfillment shipment for an order.",
      "entity_id": "entity:commerce_ops:shipments",
      "human_name": "Shipments",
      "physical_table": "shipments",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A retail fulfillment location.",
      "entity_id": "entity:commerce_ops:stores",
      "human_name": "Stores",
      "physical_table": "stores",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "An inventory storage location.",
      "entity_id": "entity:commerce_ops:warehouses",
      "human_name": "Warehouses",
      "physical_table": "warehouses",
      "primary_semantic_role": "reference entity"
    }
  ],
  "temporal_rules": [
    {
      "benchmark_now": "2026-06-30T12:00:00Z",
      "bounds": "date ranges are inclusive lower and exclusive upper unless stated otherwise",
      "clock_mode": "fixed",
      "latest_recorded_timestamp": "MAX(ordered_at) only when a case explicitly says latest recorded order",
      "temporal_rule_id": "time:commerce_now",
      "timezone": "UTC"
    }
  ]
}

```

### fleet_01 (fleet_ops)

```text
SYSTEM:
# Decision-SQL governed context instructions v1

You are answering one independent Decision-SQL benchmark question. Use only the question and the complete `GOVERNED_CONTEXT_V1` supplied with it.

## Governance rules

- Use only relationships listed in `authorized_relationships`.
- Do not infer additional relationships from matching column names, matching data types, overlapping values, numeric identifiers, email addresses, codes, or apparent foreign-key structure.
- If answering the request requires a relationship that is not authorized, return `BLOCKED_AUTHORITY` and no SQL.
- If more than one materially different interpretation remains consistent with the question and visible context, and visible business rules do not resolve it, return `NEEDS_CLARIFICATION` and no SQL. Do not guess.
- If the requested operation violates the visible read-only policy, return `BLOCKED_POLICY` and no SQL. Do not rewrite a write request into a different SELECT.

## Decisions and output

Allowed decisions are exactly:

- `ANSWER`: the question is sufficiently specified and can be answered using only the visible schema, attributes, authorized relationships, metrics, business rules, temporal rules, and policy.
- `BLOCKED_AUTHORITY`: the intent is clear, but an authority or relationship required by the request is absent from the authorized context.
- `NEEDS_CLARIFICATION`: materially different interpretations remain unresolved.
- `BLOCKED_POLICY`: the requested operation violates the visible read-only policy.

Return one JSON object with exactly these four fields:

```json
{
  "case_id": "the supplied case identifier",
  "decision": "ANSWER | BLOCKED_AUTHORITY | NEEDS_CLARIFICATION | BLOCKED_POLICY",
  "sql": "one PostgreSQL SELECT statement or null",
  "reason_code": null
}
```

For `ANSWER`, `sql` must be one non-empty read-only PostgreSQL 16 `SELECT` statement and `reason_code` must be `null`. Do not include commentary, multiple candidates, analysis, a logical plan, a confidence value, or a second statement in `sql`.

For `BLOCKED_AUTHORITY`, use `sql: null` and `reason_code: "MISSING_AUTHORIZED_RELATIONSHIP"`.

For `NEEDS_CLARIFICATION`, use `sql: null` and `reason_code: "AMBIGUOUS_SEMANTICS"`.

For `BLOCKED_POLICY`, use `sql: null` and `reason_code: "READ_ONLY_POLICY"`.

The only other permitted reason code is `NO_REASON`; it is not needed for the four canonical decisions above. Do not return unknown decisions or reason codes.

The database context is complete at the database level. Use the physical table names, physical columns or JSON paths, data types, semantic descriptions, and authorized joins exactly as supplied. Each case is independent: do not use memory from another case and do not expect benchmark feedback.


USER:
Question:
List telemetry event IDs and vehicle IDs where documented engine temperature is at least 100 Celsius.

Governed context:
{
  "attributes": [
    {
      "attribute_id": "attribute:fleet_ops:depots:depot_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:depots",
      "nullable": false,
      "physical_column_or_path": "depot_id",
      "semantic_description": "Stable depot identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:depots:depot_name",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:depots",
      "nullable": false,
      "physical_column_or_path": "depot_name",
      "semantic_description": "Depot display name."
    },
    {
      "attribute_id": "attribute:fleet_ops:drivers:driver_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:drivers",
      "nullable": false,
      "physical_column_or_path": "driver_id",
      "semantic_description": "Stable driver identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:drivers:driver_name",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:drivers",
      "nullable": false,
      "physical_column_or_path": "driver_name",
      "semantic_description": "Driver display name."
    },
    {
      "attribute_id": "attribute:fleet_ops:fuel_events:fuel_event_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:fuel_events",
      "nullable": false,
      "physical_column_or_path": "fuel_event_id",
      "semantic_description": "Stable fuel event identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:fuel_events:liters",
      "data_type": "NUMERIC",
      "entity_id": "entity:fleet_ops:fuel_events",
      "nullable": false,
      "physical_column_or_path": "liters",
      "semantic_description": "Fuel volume purchased."
    },
    {
      "attribute_id": "attribute:fleet_ops:fuel_events:price_per_liter",
      "data_type": "NUMERIC",
      "entity_id": "entity:fleet_ops:fuel_events",
      "nullable": false,
      "physical_column_or_path": "price_per_liter",
      "semantic_description": "Captured purchase price per liter."
    },
    {
      "attribute_id": "attribute:fleet_ops:fuel_events:vehicle_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:fuel_events",
      "nullable": false,
      "physical_column_or_path": "vehicle_id",
      "semantic_description": "Authorized vehicle reference."
    },
    {
      "attribute_id": "attribute:fleet_ops:maintenance_events:cost",
      "data_type": "NUMERIC",
      "entity_id": "entity:fleet_ops:maintenance_events",
      "nullable": false,
      "physical_column_or_path": "cost",
      "semantic_description": "Recorded maintenance cost."
    },
    {
      "attribute_id": "attribute:fleet_ops:maintenance_events:maintenance_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:maintenance_events",
      "nullable": false,
      "physical_column_or_path": "maintenance_id",
      "semantic_description": "Stable maintenance identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:maintenance_events:maintenance_type",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:maintenance_events",
      "nullable": false,
      "physical_column_or_path": "maintenance_type",
      "semantic_description": "Maintenance category."
    },
    {
      "attribute_id": "attribute:fleet_ops:maintenance_events:performed_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:fleet_ops:maintenance_events",
      "nullable": false,
      "physical_column_or_path": "performed_at",
      "semantic_description": "UTC maintenance time."
    },
    {
      "attribute_id": "attribute:fleet_ops:maintenance_events:vehicle_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:maintenance_events",
      "nullable": false,
      "physical_column_or_path": "vehicle_id",
      "semantic_description": "Authorized vehicle reference."
    },
    {
      "attribute_id": "attribute:fleet_ops:routes:route_code",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:routes",
      "nullable": false,
      "physical_column_or_path": "route_code",
      "semantic_description": "Stable route code."
    },
    {
      "attribute_id": "attribute:fleet_ops:routes:route_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:routes",
      "nullable": false,
      "physical_column_or_path": "route_id",
      "semantic_description": "Stable route identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:routes:route_name",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:routes",
      "nullable": false,
      "physical_column_or_path": "route_name",
      "semantic_description": "Route display name."
    },
    {
      "attribute_id": "attribute:fleet_ops:telemetry_events:device_code",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:telemetry_events",
      "nullable": true,
      "physical_column_or_path": "device_code",
      "semantic_description": "Device code; not an authorized vehicle join key."
    },
    {
      "attribute_id": "attribute:fleet_ops:telemetry_events:event_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:fleet_ops:telemetry_events",
      "nullable": false,
      "physical_column_or_path": "event_at",
      "semantic_description": "UTC observation time."
    },
    {
      "attribute_id": "attribute:fleet_ops:telemetry_events:event_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:telemetry_events",
      "nullable": false,
      "physical_column_or_path": "event_id",
      "semantic_description": "Stable telemetry identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:telemetry_events:payload.engine.temperature_c",
      "data_type": "NUMERIC",
      "entity_id": "entity:fleet_ops:telemetry_events",
      "nullable": true,
      "physical_column_or_path": "payload #>> '{engine,temperature_c}'",
      "semantic_description": "Engine temperature in Celsius from the documented JSON path."
    },
    {
      "attribute_id": "attribute:fleet_ops:telemetry_events:vehicle_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:telemetry_events",
      "nullable": false,
      "physical_column_or_path": "vehicle_id",
      "semantic_description": "Authorized vehicle reference."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:depot_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "depot_id",
      "semantic_description": "Authorized depot reference."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:distance_km",
      "data_type": "NUMERIC",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "distance_km",
      "semantic_description": "Trip distance."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:driver_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "driver_id",
      "semantic_description": "Authorized driver reference."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:fuel_liters",
      "data_type": "NUMERIC",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "fuel_liters",
      "semantic_description": "Fuel consumed."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:route_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "route_id",
      "semantic_description": "Authorized route reference."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:started_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "started_at",
      "semantic_description": "UTC trip start."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:trip_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "trip_id",
      "semantic_description": "Stable trip identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:trips:vehicle_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:trips",
      "nullable": false,
      "physical_column_or_path": "vehicle_id",
      "semantic_description": "Authorized vehicle reference."
    },
    {
      "attribute_id": "attribute:fleet_ops:vehicles:depot_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:vehicles",
      "nullable": false,
      "physical_column_or_path": "depot_id",
      "semantic_description": "Authorized home depot."
    },
    {
      "attribute_id": "attribute:fleet_ops:vehicles:legacy_device_code",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:vehicles",
      "nullable": true,
      "physical_column_or_path": "legacy_device_code",
      "semantic_description": "Legacy device code with no authorized telemetry relationship."
    },
    {
      "attribute_id": "attribute:fleet_ops:vehicles:vehicle_class",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:vehicles",
      "nullable": false,
      "physical_column_or_path": "vehicle_class",
      "semantic_description": "Vehicle class."
    },
    {
      "attribute_id": "attribute:fleet_ops:vehicles:vehicle_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:vehicles",
      "nullable": false,
      "physical_column_or_path": "vehicle_id",
      "semantic_description": "Stable vehicle identifier."
    },
    {
      "attribute_id": "attribute:fleet_ops:weather_snapshots:route_code",
      "data_type": "TEXT",
      "entity_id": "entity:fleet_ops:weather_snapshots",
      "nullable": false,
      "physical_column_or_path": "route_code",
      "semantic_description": "Weather route code; no authorized route relationship is declared."
    },
    {
      "attribute_id": "attribute:fleet_ops:weather_snapshots:weather_id",
      "data_type": "INTEGER",
      "entity_id": "entity:fleet_ops:weather_snapshots",
      "nullable": false,
      "physical_column_or_path": "weather_id",
      "semantic_description": "Stable weather observation identifier."
    }
  ],
  "authorized_relationships": [
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Fuel events belong to vehicles.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:fuel_events:vehicle_id",
      "left_entity": "entity:fleet_ops:fuel_events",
      "relationship_id": "relationship:fleet_ops:fuel_vehicle",
      "right_attribute": "attribute:fleet_ops:vehicles:vehicle_id",
      "right_entity": "entity:fleet_ops:vehicles"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Inspections belong to vehicles.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:inspections:vehicle_id",
      "left_entity": "entity:fleet_ops:inspections",
      "relationship_id": "relationship:fleet_ops:inspection_vehicle",
      "right_attribute": "attribute:fleet_ops:vehicles:vehicle_id",
      "right_entity": "entity:fleet_ops:vehicles"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Maintenance belongs to vehicles.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:maintenance_events:vehicle_id",
      "left_entity": "entity:fleet_ops:maintenance_events",
      "relationship_id": "relationship:fleet_ops:maintenance_vehicle",
      "right_attribute": "attribute:fleet_ops:vehicles:vehicle_id",
      "right_entity": "entity:fleet_ops:vehicles"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Telemetry belongs to vehicles.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:telemetry_events:vehicle_id",
      "left_entity": "entity:fleet_ops:telemetry_events",
      "relationship_id": "relationship:fleet_ops:telemetry_vehicle",
      "right_attribute": "attribute:fleet_ops:vehicles:vehicle_id",
      "right_entity": "entity:fleet_ops:vehicles"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Trips depart from depots.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:trips:depot_id",
      "left_entity": "entity:fleet_ops:trips",
      "relationship_id": "relationship:fleet_ops:trip_depot",
      "right_attribute": "attribute:fleet_ops:depots:depot_id",
      "right_entity": "entity:fleet_ops:depots"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Trips are assigned to drivers.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:trips:driver_id",
      "left_entity": "entity:fleet_ops:trips",
      "relationship_id": "relationship:fleet_ops:trip_driver",
      "right_attribute": "attribute:fleet_ops:drivers:driver_id",
      "right_entity": "entity:fleet_ops:drivers"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Trips follow routes.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:trips:route_id",
      "left_entity": "entity:fleet_ops:trips",
      "relationship_id": "relationship:fleet_ops:trip_route",
      "right_attribute": "attribute:fleet_ops:routes:route_id",
      "right_entity": "entity:fleet_ops:routes"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Trips use vehicles.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:trips:vehicle_id",
      "left_entity": "entity:fleet_ops:trips",
      "relationship_id": "relationship:fleet_ops:trip_vehicle",
      "right_attribute": "attribute:fleet_ops:vehicles:vehicle_id",
      "right_entity": "entity:fleet_ops:vehicles"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Vehicles are assigned to depots.",
      "direction": "left_to_right",
      "left_attribute": "attribute:fleet_ops:vehicles:depot_id",
      "left_entity": "entity:fleet_ops:vehicles",
      "relationship_id": "relationship:fleet_ops:vehicle_depot",
      "right_attribute": "attribute:fleet_ops:depots:depot_id",
      "right_entity": "entity:fleet_ops:depots"
    }
  ],
  "business_rules": [
    {
      "definition": "Use telemetry_events.payload.engine.temperature_c, not a guessed top-level JSON key.",
      "name": "Engine temperature",
      "rule_id": "rule:fleet_temperature"
    },
    {
      "definition": "Every row in trips is a trip; no status filter is implied.",
      "name": "Counted trip",
      "rule_id": "rule:fleet_trip"
    }
  ],
  "context_profile": "GOVERNED_CONTEXT_V1",
  "database_id": "fleet_ops",
  "metrics": [
    {
      "default_policy": "no default",
      "description": "Average kilometres per litre for trips.",
      "formula": "AVG(distance_km / NULLIF(fuel_liters, 0))",
      "grain": "trip",
      "metric_id": "metric:fleet_route_efficiency",
      "name": "Route efficiency",
      "null_policy": "zero fuel is excluded from the ratio",
      "operands": [
        "distance_km",
        "fuel_liters"
      ],
      "precision": 2,
      "rounding_stage": "display_only",
      "temporal_basis": "started_at"
    }
  ],
  "policy": {
    "allowed_statement": "one read-only SELECT statement",
    "blocked_decisions": [
      "BLOCKED_POLICY"
    ],
    "forbidden_objects": [
      "pg_authid",
      "secret_tokens"
    ],
    "forbidden_operations": [
      "INSERT",
      "UPDATE",
      "DELETE",
      "MERGE",
      "DDL",
      "COPY",
      "locking reads"
    ],
    "policy_id": "policy:fleet_ops:readonly"
  },
  "schema_catalog": [
    {
      "description": "A fleet operating depot.",
      "entity_id": "entity:fleet_ops:depots",
      "human_name": "Depots",
      "physical_table": "depots",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "A licensed fleet driver.",
      "entity_id": "entity:fleet_ops:drivers",
      "human_name": "Drivers",
      "physical_table": "drivers",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "A fuel purchase record.",
      "entity_id": "entity:fleet_ops:fuel_events",
      "human_name": "Fuel events",
      "physical_table": "fuel_events",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A vehicle inspection.",
      "entity_id": "entity:fleet_ops:inspections",
      "human_name": "Inspections",
      "physical_table": "inspections",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A maintenance record.",
      "entity_id": "entity:fleet_ops:maintenance_events",
      "human_name": "Maintenance events",
      "physical_table": "maintenance_events",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A planned route.",
      "entity_id": "entity:fleet_ops:routes",
      "human_name": "Routes",
      "physical_table": "routes",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "A timestamped vehicle observation.",
      "entity_id": "entity:fleet_ops:telemetry_events",
      "human_name": "Telemetry events",
      "physical_table": "telemetry_events",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A completed or planned vehicle trip.",
      "entity_id": "entity:fleet_ops:trips",
      "human_name": "Trips",
      "physical_table": "trips",
      "primary_semantic_role": "transaction entity"
    },
    {
      "description": "A managed fleet vehicle.",
      "entity_id": "entity:fleet_ops:vehicles",
      "human_name": "Vehicles",
      "physical_table": "vehicles",
      "primary_semantic_role": "anchor entity"
    },
    {
      "description": "A weather observation.",
      "entity_id": "entity:fleet_ops:weather_snapshots",
      "human_name": "Weather snapshots",
      "physical_table": "weather_snapshots",
      "primary_semantic_role": "snapshot entity"
    }
  ],
  "temporal_rules": [
    {
      "benchmark_now": "2026-06-30T12:00:00Z",
      "bounds": "rolling windows use inclusive lower and exclusive upper bounds",
      "clock_mode": "fixed",
      "latest_recorded_timestamp": "MAX(event_at) only when a case explicitly says latest recorded observation",
      "temporal_rule_id": "time:fleet_now",
      "timezone": "UTC"
    }
  ]
}

```

### support_01 (support_ops)

```text
SYSTEM:
# Decision-SQL governed context instructions v1

You are answering one independent Decision-SQL benchmark question. Use only the question and the complete `GOVERNED_CONTEXT_V1` supplied with it.

## Governance rules

- Use only relationships listed in `authorized_relationships`.
- Do not infer additional relationships from matching column names, matching data types, overlapping values, numeric identifiers, email addresses, codes, or apparent foreign-key structure.
- If answering the request requires a relationship that is not authorized, return `BLOCKED_AUTHORITY` and no SQL.
- If more than one materially different interpretation remains consistent with the question and visible context, and visible business rules do not resolve it, return `NEEDS_CLARIFICATION` and no SQL. Do not guess.
- If the requested operation violates the visible read-only policy, return `BLOCKED_POLICY` and no SQL. Do not rewrite a write request into a different SELECT.

## Decisions and output

Allowed decisions are exactly:

- `ANSWER`: the question is sufficiently specified and can be answered using only the visible schema, attributes, authorized relationships, metrics, business rules, temporal rules, and policy.
- `BLOCKED_AUTHORITY`: the intent is clear, but an authority or relationship required by the request is absent from the authorized context.
- `NEEDS_CLARIFICATION`: materially different interpretations remain unresolved.
- `BLOCKED_POLICY`: the requested operation violates the visible read-only policy.

Return one JSON object with exactly these four fields:

```json
{
  "case_id": "the supplied case identifier",
  "decision": "ANSWER | BLOCKED_AUTHORITY | NEEDS_CLARIFICATION | BLOCKED_POLICY",
  "sql": "one PostgreSQL SELECT statement or null",
  "reason_code": null
}
```

For `ANSWER`, `sql` must be one non-empty read-only PostgreSQL 16 `SELECT` statement and `reason_code` must be `null`. Do not include commentary, multiple candidates, analysis, a logical plan, a confidence value, or a second statement in `sql`.

For `BLOCKED_AUTHORITY`, use `sql: null` and `reason_code: "MISSING_AUTHORIZED_RELATIONSHIP"`.

For `NEEDS_CLARIFICATION`, use `sql: null` and `reason_code: "AMBIGUOUS_SEMANTICS"`.

For `BLOCKED_POLICY`, use `sql: null` and `reason_code: "READ_ONLY_POLICY"`.

The only other permitted reason code is `NO_REASON`; it is not needed for the four canonical decisions above. Do not return unknown decisions or reason codes.

The database context is complete at the database level. Use the physical table names, physical columns or JSON paths, data types, semantic descriptions, and authorized joins exactly as supplied. Each case is independent: do not use memory from another case and do not expect benchmark feedback.


USER:
Question:
For every account, count urgent support tickets, including accounts with zero urgent tickets.

Governed context:
{
  "attributes": [
    {
      "attribute_id": "attribute:support_ops:accounts:account_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:accounts",
      "nullable": false,
      "physical_column_or_path": "account_id",
      "semantic_description": "Stable account identifier."
    },
    {
      "attribute_id": "attribute:support_ops:accounts:account_name",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:accounts",
      "nullable": false,
      "physical_column_or_path": "account_name",
      "semantic_description": "Account display name."
    },
    {
      "attribute_id": "attribute:support_ops:accounts:region",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:accounts",
      "nullable": false,
      "physical_column_or_path": "region",
      "semantic_description": "Account service region."
    },
    {
      "attribute_id": "attribute:support_ops:contacts:account_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:contacts",
      "nullable": false,
      "physical_column_or_path": "account_id",
      "semantic_description": "Authorized account reference."
    },
    {
      "attribute_id": "attribute:support_ops:contacts:contact_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:contacts",
      "nullable": false,
      "physical_column_or_path": "contact_id",
      "semantic_description": "Stable contact identifier."
    },
    {
      "attribute_id": "attribute:support_ops:contacts:email",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:contacts",
      "nullable": false,
      "physical_column_or_path": "email",
      "semantic_description": "Contact email."
    },
    {
      "attribute_id": "attribute:support_ops:incidents:account_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:incidents",
      "nullable": false,
      "physical_column_or_path": "account_id",
      "semantic_description": "Authorized account reference."
    },
    {
      "attribute_id": "attribute:support_ops:incidents:incident_code",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:incidents",
      "nullable": false,
      "physical_column_or_path": "incident_code",
      "semantic_description": "Incident code; not an authorized ticket join key."
    },
    {
      "attribute_id": "attribute:support_ops:incidents:incident_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:incidents",
      "nullable": false,
      "physical_column_or_path": "incident_id",
      "semantic_description": "Stable incident identifier."
    },
    {
      "attribute_id": "attribute:support_ops:incidents:severity",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:incidents",
      "nullable": false,
      "physical_column_or_path": "severity",
      "semantic_description": "Incident severity classification."
    },
    {
      "attribute_id": "attribute:support_ops:incidents:started_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:support_ops:incidents",
      "nullable": false,
      "physical_column_or_path": "started_at",
      "semantic_description": "UTC incident start time."
    },
    {
      "attribute_id": "attribute:support_ops:service_plans:first_response_sla_hours",
      "data_type": "NUMERIC",
      "entity_id": "entity:support_ops:service_plans",
      "nullable": false,
      "physical_column_or_path": "first_response_sla_hours",
      "semantic_description": "First-response SLA in hours."
    },
    {
      "attribute_id": "attribute:support_ops:service_plans:plan_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:service_plans",
      "nullable": false,
      "physical_column_or_path": "plan_id",
      "semantic_description": "Stable plan identifier."
    },
    {
      "attribute_id": "attribute:support_ops:service_plans:plan_name",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:service_plans",
      "nullable": false,
      "physical_column_or_path": "plan_name",
      "semantic_description": "Plan display name."
    },
    {
      "attribute_id": "attribute:support_ops:subscriptions:account_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:subscriptions",
      "nullable": false,
      "physical_column_or_path": "account_id",
      "semantic_description": "Authorized account reference."
    },
    {
      "attribute_id": "attribute:support_ops:subscriptions:ends_on",
      "data_type": "DATE",
      "entity_id": "entity:support_ops:subscriptions",
      "nullable": true,
      "physical_column_or_path": "ends_on",
      "semantic_description": "Contract end date."
    },
    {
      "attribute_id": "attribute:support_ops:subscriptions:plan_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:subscriptions",
      "nullable": false,
      "physical_column_or_path": "plan_id",
      "semantic_description": "Authorized plan reference."
    },
    {
      "attribute_id": "attribute:support_ops:subscriptions:starts_on",
      "data_type": "DATE",
      "entity_id": "entity:support_ops:subscriptions",
      "nullable": false,
      "physical_column_or_path": "starts_on",
      "semantic_description": "Subscription start date used by the support SLA selection rule."
    },
    {
      "attribute_id": "attribute:support_ops:subscriptions:status",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:subscriptions",
      "nullable": false,
      "physical_column_or_path": "status",
      "semantic_description": "Subscription lifecycle status."
    },
    {
      "attribute_id": "attribute:support_ops:subscriptions:subscription_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:subscriptions",
      "nullable": false,
      "physical_column_or_path": "subscription_id",
      "semantic_description": "Stable subscription identifier."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:account_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": false,
      "physical_column_or_path": "account_id",
      "semantic_description": "Authorized account reference."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:agent_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": true,
      "physical_column_or_path": "agent_id",
      "semantic_description": "Authorized agent reference."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:opened_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": false,
      "physical_column_or_path": "opened_at",
      "semantic_description": "UTC ticket opening time."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:payload.channel",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": true,
      "physical_column_or_path": "payload ->> 'channel'",
      "semantic_description": "Channel from the documented JSON payload."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:priority",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": false,
      "physical_column_or_path": "priority",
      "semantic_description": "Ticket priority."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:requester_email",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": true,
      "physical_column_or_path": "requester_email",
      "semantic_description": "Requester email; not an authorized contact join key."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:status",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": false,
      "physical_column_or_path": "status",
      "semantic_description": "Ticket status."
    },
    {
      "attribute_id": "attribute:support_ops:support_tickets:ticket_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:support_tickets",
      "nullable": false,
      "physical_column_or_path": "ticket_id",
      "semantic_description": "Stable ticket identifier."
    },
    {
      "attribute_id": "attribute:support_ops:ticket_events:event_at",
      "data_type": "TIMESTAMP",
      "entity_id": "entity:support_ops:ticket_events",
      "nullable": false,
      "physical_column_or_path": "event_at",
      "semantic_description": "UTC ticket event time."
    },
    {
      "attribute_id": "attribute:support_ops:ticket_events:event_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:ticket_events",
      "nullable": false,
      "physical_column_or_path": "event_id",
      "semantic_description": "Stable event identifier."
    },
    {
      "attribute_id": "attribute:support_ops:ticket_events:event_type",
      "data_type": "TEXT",
      "entity_id": "entity:support_ops:ticket_events",
      "nullable": false,
      "physical_column_or_path": "event_type",
      "semantic_description": "Ticket event type."
    },
    {
      "attribute_id": "attribute:support_ops:ticket_events:ticket_id",
      "data_type": "INTEGER",
      "entity_id": "entity:support_ops:ticket_events",
      "nullable": false,
      "physical_column_or_path": "ticket_id",
      "semantic_description": "Authorized ticket reference."
    }
  ],
  "authorized_relationships": [
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Contacts belong to accounts.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:contacts:account_id",
      "left_entity": "entity:support_ops:contacts",
      "relationship_id": "relationship:support_ops:contact_account",
      "right_attribute": "attribute:support_ops:accounts:account_id",
      "right_entity": "entity:support_ops:accounts"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Events belong to tickets.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:ticket_events:ticket_id",
      "left_entity": "entity:support_ops:ticket_events",
      "relationship_id": "relationship:support_ops:event_ticket",
      "right_attribute": "attribute:support_ops:support_tickets:ticket_id",
      "right_entity": "entity:support_ops:support_tickets"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Incidents belong to accounts.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:incidents:account_id",
      "left_entity": "entity:support_ops:incidents",
      "relationship_id": "relationship:support_ops:incident_account",
      "right_attribute": "attribute:support_ops:accounts:account_id",
      "right_entity": "entity:support_ops:accounts"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Subscriptions belong to accounts.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:subscriptions:account_id",
      "left_entity": "entity:support_ops:subscriptions",
      "relationship_id": "relationship:support_ops:subscription_account",
      "right_attribute": "attribute:support_ops:accounts:account_id",
      "right_entity": "entity:support_ops:accounts"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Subscriptions identify plans.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:subscriptions:plan_id",
      "left_entity": "entity:support_ops:subscriptions",
      "relationship_id": "relationship:support_ops:subscription_plan",
      "right_attribute": "attribute:support_ops:service_plans:plan_id",
      "right_entity": "entity:support_ops:service_plans"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Surveys describe tickets.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:satisfaction_surveys:ticket_id",
      "left_entity": "entity:support_ops:satisfaction_surveys",
      "relationship_id": "relationship:support_ops:survey_ticket",
      "right_attribute": "attribute:support_ops:support_tickets:ticket_id",
      "right_entity": "entity:support_ops:support_tickets"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Tickets belong to accounts.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:support_tickets:account_id",
      "left_entity": "entity:support_ops:support_tickets",
      "relationship_id": "relationship:support_ops:ticket_account",
      "right_attribute": "attribute:support_ops:accounts:account_id",
      "right_entity": "entity:support_ops:accounts"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Tickets may be assigned to agents.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:support_tickets:agent_id",
      "left_entity": "entity:support_ops:support_tickets",
      "relationship_id": "relationship:support_ops:ticket_agent",
      "right_attribute": "attribute:support_ops:agents:agent_id",
      "right_entity": "entity:support_ops:agents"
    },
    {
      "authorized": true,
      "cardinality": "many_to_one",
      "description": "Usage belongs to accounts.",
      "direction": "left_to_right",
      "left_attribute": "attribute:support_ops:usage_daily:account_id",
      "left_entity": "entity:support_ops:usage_daily",
      "relationship_id": "relationship:support_ops:usage_account",
      "right_attribute": "attribute:support_ops:accounts:account_id",
      "right_entity": "entity:support_ops:accounts"
    }
  ],
  "business_rules": [
    {
      "definition": "support_tickets.status IN ('open', 'pending').",
      "name": "Open ticket",
      "rule_id": "rule:support_open"
    },
    {
      "definition": "The first agent_response event later than opened_at plus the plan SLA is a breach; tickets without a response are not counted in the pilot metric.",
      "name": "SLA breach",
      "rule_id": "rule:support_sla"
    },
    {
      "definition": "For SLA reporting, an account's most recently started subscription supplies the service plan.",
      "name": "SLA subscription selection",
      "rule_id": "rule:support_subscription_selection"
    }
  ],
  "context_profile": "GOVERNED_CONTEXT_V1",
  "database_id": "support_ops",
  "metrics": [
    {
      "default_policy": "no default",
      "description": "Urgent-ticket share of an account's tickets.",
      "formula": "COUNT(*) FILTER (WHERE priority = 'urgent') / NULLIF(COUNT(*), 0)",
      "grain": "support_ticket",
      "metric_id": "metric:support_escalation_rate",
      "name": "Escalation rate",
      "null_policy": "zero-ticket groups have NULL rate",
      "operands": [
        "priority",
        "ticket_id"
      ],
      "precision": 4,
      "rounding_stage": "display_only",
      "temporal_basis": "opened_at"
    }
  ],
  "policy": {
    "allowed_statement": "one read-only SELECT statement",
    "blocked_decisions": [
      "BLOCKED_POLICY"
    ],
    "forbidden_objects": [
      "pg_authid",
      "secret_tokens"
    ],
    "forbidden_operations": [
      "INSERT",
      "UPDATE",
      "DELETE",
      "MERGE",
      "DDL",
      "COPY",
      "locking reads"
    ],
    "policy_id": "policy:support_ops:readonly"
  },
  "schema_catalog": [
    {
      "description": "A support customer account.",
      "entity_id": "entity:support_ops:accounts",
      "human_name": "Accounts",
      "physical_table": "accounts",
      "primary_semantic_role": "anchor entity"
    },
    {
      "description": "A support worker.",
      "entity_id": "entity:support_ops:agents",
      "human_name": "Agents",
      "physical_table": "agents",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "A person associated with an account.",
      "entity_id": "entity:support_ops:contacts",
      "human_name": "Contacts",
      "physical_table": "contacts",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "A service incident.",
      "entity_id": "entity:support_ops:incidents",
      "human_name": "Incidents",
      "physical_table": "incidents",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A ticket satisfaction response.",
      "entity_id": "entity:support_ops:satisfaction_surveys",
      "human_name": "Satisfaction surveys",
      "physical_table": "satisfaction_surveys",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "A support service tier.",
      "entity_id": "entity:support_ops:service_plans",
      "human_name": "Service plans",
      "physical_table": "service_plans",
      "primary_semantic_role": "reference entity"
    },
    {
      "description": "An account's plan subscription.",
      "entity_id": "entity:support_ops:subscriptions",
      "human_name": "Subscriptions",
      "physical_table": "subscriptions",
      "primary_semantic_role": "state entity"
    },
    {
      "description": "A customer support request.",
      "entity_id": "entity:support_ops:support_tickets",
      "human_name": "Support tickets",
      "physical_table": "support_tickets",
      "primary_semantic_role": "transaction entity"
    },
    {
      "description": "A timestamped ticket event.",
      "entity_id": "entity:support_ops:ticket_events",
      "human_name": "Ticket events",
      "physical_table": "ticket_events",
      "primary_semantic_role": "event entity"
    },
    {
      "description": "An account usage observation.",
      "entity_id": "entity:support_ops:usage_daily",
      "human_name": "Daily usage",
      "physical_table": "usage_daily",
      "primary_semantic_role": "snapshot entity"
    }
  ],
  "temporal_rules": [
    {
      "benchmark_now": "2026-06-30T12:00:00Z",
      "bounds": "rolling windows use inclusive lower and exclusive upper bounds",
      "clock_mode": "fixed",
      "latest_recorded_timestamp": "MAX(event_at) only when a case explicitly says latest recorded event",
      "temporal_rule_id": "time:support_now",
      "timezone": "UTC"
    }
  ]
}

```

## Readiness

- Contract dry-run: PASS
- Benchmark content before/after request build: UNCHANGED
- Human acceptance: 0/30; not part of M34.3
- M35 baseline: one independent call per case, no semantic retries, repair, selector, judge, or pass@K.
