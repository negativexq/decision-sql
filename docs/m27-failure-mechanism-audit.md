# M2.7.1 Failure Mechanism Audit

Offline forensic analysis of the persisted M2.7 development run.
No provider, database, SQL regeneration, or holdout access occurred.

- Source: `evaluation/results/m27/dev/20260903T023000Z/recomputed-v2`
- Development dataset: `evaluation/datasets/m2_baseline.json`
- Audited questions: 26 (52 model cases)
- Provider calls: 0
- Result rows: not present in the persisted M2.7 artifacts; row counts are reported where available.

## Classifier audit

The first-pass classifier is not trustworthy as a primary root-cause classifier.
It treated any raw ORDER BY difference as an order/limit failure, paired arbitrary missing/extra columns, and treated qualifier-only window differences as semantic window errors.
The corrected audit normalizes aliases and qualifiers, checks output projection shape separately, and applies root-cause precedence.

## Corrected primary root causes

Counts include policy-rejected cases and exclude result-equivalent fixture warnings. Each model denominator is 33 non-equivalent or rejected cases.

| Root cause | Luna | Terra |
|---|---:|---:|
| OBJECT_SELECTION_ERROR | 19 | 20 |
| FILTER_CONSTRUCTION_ERROR | 1 | 3 |
| AGGREGATION_GRAIN_ERROR | 2 | 3 |
| JOIN_COMPOSITION_ERROR | 0 | 0 |
| ORDER_TOPK_ERROR | 3 | 2 |
| WINDOW_COMPOSITION_ERROR | 0 | 0 |
| SQL_COMPOSITION_ERROR | 0 | 0 |
| STRUCTURAL_CONTEXT_INSUFFICIENT | 0 | 0 |
| BUSINESS_SEMANTIC_INFORMATION_MISSING | 2 | 1 |
| EVALUATOR_OR_FIXTURE_ARTIFACT | 0 | 0 |
| POLICY_REJECTION | 6 | 4 |
| PROVIDER_FAILURE | 0 | 0 |
| OTHER | 0 | 0 |

Fixture/evaluator warnings retained separately: Luna 6, Terra 12.

## Why simple filters are 0/8

The zero is not caused by a result-equivalence alias bug. The corrected evaluator compares equal-arity ordinal columns; the generated queries frequently project the whole/expanded table instead of the requested projection.

- Luna: 6 projection/object-selection failures and 2 M1 policy rejections caused by unsupported `LOWER()`.
- Terra: 7 projection/object-selection failures and 1 M1 policy rejection caused by unsupported `LOWER()`.
- The filter predicates themselves are usually structurally correct after qualifier and date-cast normalization.
- The benchmark’s explicit projection contract makes extra columns a genuine result mismatch, not a harmless alias difference.

## Top-k failure matrix

| Model | Case | Measure/agg | Grouping entity | Order | Direction | Limit | Projection | Root |
|---|---|---:|---:|---:|---:|---:|---:|---|
| luna | m2-039 | False | True | False | True | False | False | BUSINESS_SEMANTIC_INFORMATION_MISSING |
| luna | m2-040 | True | True | False | False | False | False | ORDER_TOPK_ERROR |
| luna | m2-041 | True | True | False | False | True | False | ORDER_TOPK_ERROR |
| luna | m2-042 | True | True | True | True | False | True | ORDER_TOPK_ERROR |
| terra | m2-039 | False | True | False | False | False | False | BUSINESS_SEMANTIC_INFORMATION_MISSING |
| terra | m2-040 | False | True | False | False | False | False | AGGREGATION_GRAIN_ERROR |
| terra | m2-041 | True | True | False | False | True | False | ORDER_TOPK_ERROR |
| terra | m2-042 | True | True | False | False | False | True | ORDER_TOPK_ERROR |

## Window failure matrix

| Model | Case | Window recognized | Function | Partition | Window order | Projection | Root |
|---|---|---:|---:|---:|---:|---:|---|
| luna | m2-046 | True | True | True | True | False | OBJECT_SELECTION_ERROR |
| luna | m2-047 | True | True | True | True | False | OBJECT_SELECTION_ERROR |
| luna | m2-048 | True | True | True | True | False | OBJECT_SELECTION_ERROR |
| terra | m2-046 | True | True | True | True | False | OBJECT_SELECTION_ERROR |
| terra | m2-047 | True | True | True | True | False | OBJECT_SELECTION_ERROR |
| terra | m2-048 | True | True | True | True | False | OBJECT_SELECTION_ERROR |

## Join analysis

Primary JOIN_COMPOSITION_ERROR: Luna 0, Terra 0.
Rows with a normalized join-edge difference: Luna 0, Terra 0.
Rows with a join-type difference (for example INNER vs LEFT): Luna 3, Terra 3.
Join differences are secondary in the audited failures when projection, aggregation, or top-k errors occur earlier. The detailed join rows are persisted in the JSON artifact.

## Context adequacy

| Model | Non-equivalent/rejected | Tables visible | Columns visible | Relationships visible |
|---|---:|---:|---:|---:|
| luna | 33 | 33 | 33 | 33 |
| terra | 33 | 33 | 33 | 33 |

No structural-context-insufficient case was found. The gold table, column, and relationship visibility rate remains 100%.

## Column confusion

High-confidence substitutions require ordinal projection alignment with one referenced column on each side. Arbitrary set differences are retained as unmatched columns.

### High-confidence substitutions

- luna: `{}`
- terra: `{}`

### Unmatched missing/extra columns

- luna: `{"EXTRA_GENERATED_COLUMN:customers.created_at": 1, "EXTRA_GENERATED_COLUMN:customers.id": 2, "EXTRA_GENERATED_COLUMN:customers.name": 2, "EXTRA_GENERATED_COLUMN:customers.sales_rep_id": 1, "EXTRA_GENERATED_COLUMN:order_items.discount_amount": 3, "EXTRA_GENERATED_COLUMN:order_items.unit_price": 2, "EXTRA_GENERATED_COLUMN:orders.currency": 2, "EXTRA_GENERATED_COLUMN:orders.customer_id": 1, "EXTRA_GENERATED_COLUMN:orders.discount_amount": 2, "EXTRA_GENERATED_COLUMN:orders.id": 1, "EXTRA_GENERATED_COLUMN:orders.ordered_at": 3, "EXTRA_GENERATED_COLUMN:orders.sales_rep_id": 1, "EXTRA_GENERATED_COLUMN:orders.status": 2, "EXTRA_GENERATED_COLUMN:orders.subtotal": 2, "EXTRA_GENERATED_COLUMN:orders.total_amount": 1, "EXTRA_GENERATED_COLUMN:payments.paid_at": 2, "EXTRA_GENERATED_COLUMN:payments.status": 3, "EXTRA_GENERATED_COLUMN:products.category": 2, "EXTRA_GENERATED_COLUMN:products.id": 1, "EXTRA_GENERATED_COLUMN:products.sku": 6, "EXTRA_GENERATED_COLUMN:products.unit_price": 1, "EXTRA_GENERATED_COLUMN:refunds.reason": 3, "EXTRA_GENERATED_COLUMN:refunds.refunded_at": 4, "MISSING_EXPECTED_COLUMN:customers.name": 1, "MISSING_EXPECTED_COLUMN:order_items.id": 3, "MISSING_EXPECTED_COLUMN:refunds.id": 1}`
- terra: `{"EXTRA_GENERATED_COLUMN:customers.created_at": 2, "EXTRA_GENERATED_COLUMN:customers.id": 3, "EXTRA_GENERATED_COLUMN:customers.name": 3, "EXTRA_GENERATED_COLUMN:customers.sales_rep_id": 1, "EXTRA_GENERATED_COLUMN:order_items.discount_amount": 4, "EXTRA_GENERATED_COLUMN:order_items.quantity": 1, "EXTRA_GENERATED_COLUMN:order_items.unit_price": 3, "EXTRA_GENERATED_COLUMN:orders.currency": 7, "EXTRA_GENERATED_COLUMN:orders.customer_id": 3, "EXTRA_GENERATED_COLUMN:orders.discount_amount": 5, "EXTRA_GENERATED_COLUMN:orders.id": 2, "EXTRA_GENERATED_COLUMN:orders.ordered_at": 7, "EXTRA_GENERATED_COLUMN:orders.sales_rep_id": 3, "EXTRA_GENERATED_COLUMN:orders.status": 4, "EXTRA_GENERATED_COLUMN:orders.subtotal": 5, "EXTRA_GENERATED_COLUMN:orders.total_amount": 5, "EXTRA_GENERATED_COLUMN:payments.paid_at": 2, "EXTRA_GENERATED_COLUMN:payments.status": 4, "EXTRA_GENERATED_COLUMN:products.category": 5, "EXTRA_GENERATED_COLUMN:products.id": 1, "EXTRA_GENERATED_COLUMN:products.sku": 8, "EXTRA_GENERATED_COLUMN:products.unit_price": 1, "EXTRA_GENERATED_COLUMN:refunds.reason": 4, "EXTRA_GENERATED_COLUMN:refunds.refunded_at": 4, "EXTRA_GENERATED_COLUMN:sales_representatives.id": 1, "EXTRA_GENERATED_COLUMN:sales_representatives.name": 1, "MISSING_EXPECTED_COLUMN:customers.name": 1, "MISSING_EXPECTED_COLUMN:order_items.id": 2}`

## Model-capacity comparison

Pairwise outcomes: `{"BOTH_CORRECT": 14, "BOTH_INCORRECT": 32, "LUNA_ONLY_CORRECT": 1, "TERRA_ONLY_CORRECT": 1}`.
Among BOTH_INCORRECT cases, same root cause: 29; different root cause: 3.

The Luna-only correct case is `m2-015` (Luna produced the unfiltered payment total; Terra added a status predicate). The Terra-only correct case is `m2-030` (Terra used the February date range without Luna’s extra cancellation predicate).

## Recommended M2.8 experiment

Recommend **SQL_SHAPE_DECOMPOSITION** as the single next experiment.

The required schema was visible, model capacity was neutral, and the dominant remaining mechanisms are projection selection, filter/aggregation composition, ordering/LIMIT, and query shape. A narrow decomposition experiment is therefore better justified than richer schema context, another model ablation, candidate voting, or a semantic contract. No decomposition is implemented here.

## Detailed stratified audit

The following cases cover all eight simple-filter questions, all four top-k questions, all three window questions, six join/multi-table cases, two ratio cases, aggregation/grouping, and both model-exclusive correct cases. The machine-readable artifact contains the complete case-by-case fields, including model-visible schema excerpts and structural parts.

### m2-001 — simple_filters

Question: Which products are in the Electronics category?

Model-visible schema excerpt:

```text
[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, name, category FROM products WHERE category = 'Electronics' ORDER BY id`
Gold result: unavailable in source artifacts; generated row counts: Luna `6`, Terra `6`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["products"]` | `["products"]` | `["products"]` |
| Columns | `["products.category","products.id","products.name"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `["category = 'electronics'"]` | `["category = 'electronics'"]` | `["category = 'electronics'"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["name"],null]` | `[["name","id"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, name, sku, unit_price FROM products WHERE category = 'Electronics' ORDER BY name;`
Terra SQL: `SELECT p.id, p.name, p.sku, p.category, p.unit_price
FROM products AS p
WHERE p.category = 'Electronics'
ORDER BY p.name, p.id;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-002 — simple_filters

Question: List products with a unit price above 100.

Model-visible schema excerpt:

```text
[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, name, unit_price FROM products WHERE unit_price > 100 ORDER BY unit_price DESC`
Gold result: unavailable in source artifacts; generated row counts: Luna `12`, Terra `12`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["products"]` | `["products"]` | `["products"]` |
| Columns | `["products.id","products.name","products.unit_price"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `["unit_price > 100"]` | `["unit_price > 100"]` | `["unit_price > 100"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["unit_price desc"],null]` | `[["unit_price desc","name asc"],null]` | `[["unit_price desc","name asc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, name, sku, category, unit_price FROM products WHERE unit_price > 100 ORDER BY unit_price DESC, name ASC;`
Terra SQL: `SELECT p.id, p.sku, p.name, p.category, p.unit_price
FROM products AS p
WHERE p.unit_price > 100
ORDER BY p.unit_price DESC, p.name ASC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-003 — simple_filters

Question: Show completed orders.

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, order_number, total_amount FROM orders WHERE status = 'completed' ORDER BY id`
Gold result: unavailable in source artifacts; generated row counts: Luna `72`, Terra `72`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["orders"]` | `["orders"]` |
| Columns | `["orders.id","orders.order_number","orders.status","orders.total_amount"]` | `["orders.currency","orders.customer_id","orders.discount_amount","orders.id","orders.order_number","orders.ordered_at","orders.sales_rep_id","orders.status","orders.subtotal","orders.total_amount"]` | `["orders.currency","orders.customer_id","orders.discount_amount","orders.id","orders.order_number","orders.ordered_at","orders.sales_rep_id","orders.status","orders.subtotal","orders.total_amount"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `["status = 'completed'"]` | `["status = 'completed'"]` | `["status = 'completed'"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["ordered_at desc","id desc"],null]` | `[["ordered_at desc","id desc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, order_number, customer_id, sales_rep_id, ordered_at, currency, subtotal, discount_amount, total_amount, status FROM orders WHERE status = 'completed' ORDER BY ordered_at DESC, id DESC;`
Terra SQL: `SELECT o.id, o.order_number, o.customer_id, o.sales_rep_id, o.ordered_at, o.currency, o.subtotal, o.discount_amount, o.total_amount, o.status
FROM orders AS o
WHERE o.status = 'completed'
ORDER BY o.ordered_at DESC, o.id DESC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-004 — simple_filters

Question: Which customers belong to the North region?

Model-visible schema excerpt:

```text
[Table] customers
Description: Synthetic customer accounts.
[Columns]
- customers.created_at DATETIME — created_at from customers.
- customers.id INTEGER [PK] — id from customers.
- customers.name VARCHAR(150) — name from customers.
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.

[Table] regions
Description: Sales regions used for geographic analysis.
[Columns]
- regions.id INTEGER [PK] — id from regions.
- regions.name VARCHAR(100) — name from regions.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT c.id, c.name FROM customers c JOIN regions r ON r.id = c.region_id WHERE r.name = 'North' ORDER BY c.id`
Gold result: unavailable in source artifacts; generated row counts: Luna `10`, Terra `10`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["customers","regions"]` | `["customers","regions"]` | `["customers","regions"]` |
| Columns | `["customers.id","customers.name","customers.region_id","regions.id","regions.name"]` | `["customers.created_at","customers.id","customers.name","customers.region_id","customers.sales_rep_id","regions.id","regions.name"]` | `["customers.created_at","customers.id","customers.name","customers.region_id","customers.sales_rep_id","regions.id","regions.name"]` |
| Joins | `["customers.region_id<->regions.id"]` | `["customers.region_id<->regions.id"]` | `["customers.region_id<->regions.id"]` |
| Filters | `["name = 'north'"]` | `["name = 'north'"]` | `["name = 'north'"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["name","id"],null]` | `[["name","id"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT c.id, c.name, c.created_at, c.region_id, c.sales_rep_id
FROM customers AS c
JOIN regions AS r ON r.id = c.region_id
WHERE r.name = 'North'
ORDER BY c.name, c.id;`
Terra SQL: `SELECT c.id, c.name, c.created_at, c.sales_rep_id
FROM customers AS c
JOIN regions AS r ON r.id = c.region_id
WHERE r.name = 'North'
ORDER BY c.name, c.id;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-005 — simple_filters

Question: List pending orders.

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, order_number, status FROM orders WHERE status = 'pending' ORDER BY id`
Gold result: unavailable in source artifacts; generated row counts: Luna `None`, Terra `24`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["orders"]` | `["customers","orders","sales_representatives"]` |
| Columns | `["orders.id","orders.order_number","orders.status"]` | `["orders.currency","orders.customer_id","orders.discount_amount","orders.id","orders.order_number","orders.ordered_at","orders.sales_rep_id","orders.status","orders.subtotal","orders.total_amount"]` | `["customers.id","customers.name","orders.currency","orders.customer_id","orders.discount_amount","orders.id","orders.order_number","orders.ordered_at","orders.sales_rep_id","orders.status","orders.subtotal","orders.total_amount","sales_representatives.id","sales_representatives.name"]` |
| Joins | `[]` | `[]` | `["customers.id<->orders.customer_id","orders.sales_rep_id<->sales_representatives.id"]` |
| Filters | `["status = 'pending'"]` | `["lower(status) = 'pending'"]` | `["status = 'pending'"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["ordered_at desc","id desc"],null]` | `[["ordered_at desc","id desc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, order_number, customer_id, sales_rep_id, ordered_at, currency, subtotal, discount_amount, total_amount, status FROM orders WHERE LOWER(status) = 'pending' ORDER BY ordered_at DESC, id DESC;`
Terra SQL: `SELECT o.id, o.order_number, o.customer_id, c.name AS customer_name, o.sales_rep_id, sr.name AS sales_rep_name, o.ordered_at, o.currency, o.subtotal, o.discount_amount, o.total_amount, o.status
FROM orders AS o
LEFT JOIN customers AS c ON c.id = o.customer_id
LEFT JOIN sales_representatives AS sr ON sr.id = o.sales_rep_id
WHERE o.status = 'pending'
ORDER BY o.ordered_at DESC, o.id DESC;`
Result-equivalence: Luna `None`, Terra `False`
Primary root cause: Luna `POLICY_REJECTION`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['FILTER_DIFFERENCE', 'FORBIDDEN_FUNCTION:LOWER', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['JOIN_EDGE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-006 — simple_filters

Question: Show products in the Outdoor category priced below 100.

Model-visible schema excerpt:

```text
[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, name, unit_price FROM products WHERE category = 'Outdoor' AND unit_price < 100 ORDER BY id`
Gold result: unavailable in source artifacts; generated row counts: Luna `3`, Terra `3`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["products"]` | `["products"]` | `["products"]` |
| Columns | `["products.category","products.id","products.name","products.unit_price"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `["category = 'outdoor' and unit_price < 100"]` | `["category = 'outdoor' and unit_price < 100"]` | `["category = 'outdoor' and unit_price < 100"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["unit_price asc","name asc"],null]` | `[["unit_price asc","name asc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, name, sku, category, unit_price
FROM products
WHERE category = 'Outdoor'
  AND unit_price < 100
ORDER BY unit_price ASC, name ASC;`
Terra SQL: `SELECT p.id, p.name, p.sku, p.category, p.unit_price
FROM products AS p
WHERE p.category = 'Outdoor'
  AND p.unit_price < 100
ORDER BY p.unit_price ASC, p.name ASC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-007 — simple_filters

Question: Which payments are settled?

Model-visible schema excerpt:

```text
[Table] payments
Description: Payment transactions associated with orders.
[Columns]
- payments.amount NUMERIC(12, 2) — amount from payments.
- payments.id INTEGER [PK] — id from payments.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- payments.paid_at DATETIME — paid_at from payments.
- payments.status VARCHAR(30) — status from payments.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, order_id, amount FROM payments WHERE status = 'settled' ORDER BY id`
Gold result: unavailable in source artifacts; generated row counts: Luna `None`, Terra `None`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["payments"]` | `["payments"]` | `["orders","payments"]` |
| Columns | `["payments.amount","payments.id","payments.order_id","payments.status"]` | `["payments.amount","payments.id","payments.order_id","payments.paid_at","payments.status"]` | `["orders.id","orders.order_number","payments.amount","payments.id","payments.order_id","payments.paid_at","payments.status"]` |
| Joins | `[]` | `[]` | `["orders.id<->payments.order_id"]` |
| Filters | `["status = 'settled'"]` | `["lower(status) = 'settled'"]` | `["lower(status) = 'settled'"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["paid_at","id"],null]` | `[["paid_at desc","id desc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, order_id, amount, paid_at, status FROM payments WHERE LOWER(status) = 'settled' ORDER BY paid_at, id;`
Terra SQL: `SELECT p.id AS payment_id, p.order_id, o.order_number, p.amount, p.paid_at, p.status FROM payments AS p LEFT JOIN orders AS o ON o.id = p.order_id WHERE LOWER(p.status) = 'settled' ORDER BY p.paid_at DESC, p.id DESC;`
Result-equivalence: Luna `None`, Terra `None`
Primary root cause: Luna `POLICY_REJECTION`, Terra `POLICY_REJECTION`
Secondary tags: Luna `['FILTER_DIFFERENCE', 'FORBIDDEN_FUNCTION:LOWER', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['FILTER_DIFFERENCE', 'FORBIDDEN_FUNCTION:LOWER', 'JOIN_EDGE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-008 — simple_filters

Question: List refunds for more than 50 dollars.

Model-visible schema excerpt:

```text
[Table] refunds
Description: Refund transactions associated with orders.
[Columns]
- refunds.amount NUMERIC(12, 2) — amount from refunds.
- refunds.id INTEGER [PK] — id from refunds.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- refunds.reason VARCHAR(200) — reason from refunds.
- refunds.refunded_at DATETIME — refunded_at from refunds.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, order_id, amount FROM refunds WHERE amount > 50 ORDER BY amount DESC`
Gold result: unavailable in source artifacts; generated row counts: Luna `9`, Terra `9`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["refunds"]` | `["refunds"]` | `["refunds"]` |
| Columns | `["refunds.amount","refunds.id","refunds.order_id"]` | `["refunds.amount","refunds.id","refunds.order_id","refunds.reason","refunds.refunded_at"]` | `["refunds.amount","refunds.id","refunds.order_id","refunds.reason","refunds.refunded_at"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `["amount > 50"]` | `["amount > 50"]` | `["amount > 50"]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["amount desc"],null]` | `[["refunded_at desc","id desc"],null]` | `[["refunded_at desc","id desc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, order_id, amount, reason, refunded_at FROM refunds WHERE amount > 50 ORDER BY refunded_at DESC, id DESC;`
Terra SQL: `SELECT r.id, r.order_id, r.amount, r.reason, r.refunded_at
FROM refunds AS r
WHERE r.amount > 50
ORDER BY r.refunded_at DESC, r.id DESC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-017 — joins

Question: Show each product and its order item quantity.

Model-visible schema excerpt:

```text
[Table] order_items
Description: Product line items belonging to orders.
[Columns]
- order_items.discount_amount NUMERIC(12, 2) — discount_amount from order_items.
- order_items.id INTEGER [PK] — id from order_items.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- order_items.quantity INTEGER — quantity from order_items.
- order_items.unit_price NUMERIC(12, 2) — unit_price from order_items.

[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT p.name, oi.quantity FROM products p JOIN order_items oi ON oi.product_id = p.id ORDER BY p.id, oi.id`
Gold result: unavailable in source artifacts; generated row counts: Luna `24`, Terra `24`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["order_items","products"]` | `["order_items","products"]` | `["order_items","products"]` |
| Columns | `["order_items.id","order_items.product_id","order_items.quantity","products.id","products.name"]` | `["order_items.product_id","order_items.quantity","products.id","products.name"]` | `["order_items.product_id","order_items.quantity","products.category","products.id","products.name","products.sku"]` |
| Joins | `["order_items.product_id<->products.id"]` | `["order_items.product_id<->products.id"]` | `["order_items.product_id<->products.id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[["sum(quantity)"],["id","name"]]` | `[["sum(quantity)"],["category","id","name","sku"]]` |
| Order/limit | `[["id","id"],null]` | `[["name"],null]` | `[["order_item_quantity desc","name asc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT p.id AS product_id, p.name AS product_name, COALESCE(SUM(oi.quantity), 0) AS order_item_quantity
FROM products AS p
LEFT JOIN order_items AS oi ON oi.product_id = p.id
GROUP BY p.id, p.name
ORDER BY p.name;`
Terra SQL: `SELECT p.id AS product_id, p.name AS product_name, p.sku, p.category, COALESCE(SUM(oi.quantity), 0) AS order_item_quantity
FROM products AS p
LEFT JOIN order_items AS oi ON oi.product_id = p.id
GROUP BY p.id, p.name, p.sku, p.category
ORDER BY order_item_quantity DESC, p.name ASC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `AGGREGATION_GRAIN_ERROR`, Terra `AGGREGATION_GRAIN_ERROR`
Secondary tags: Luna `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'JOIN_TYPE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'JOIN_TYPE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-019 — joins

Question: Show payments with their order numbers.

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.

[Table] payments
Description: Payment transactions associated with orders.
[Columns]
- payments.amount NUMERIC(12, 2) — amount from payments.
- payments.id INTEGER [PK] — id from payments.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- payments.paid_at DATETIME — paid_at from payments.
- payments.status VARCHAR(30) — status from payments.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT p.id, o.order_number, p.amount FROM payments p JOIN orders o ON o.id = p.order_id ORDER BY p.id`
Gold result: unavailable in source artifacts; generated row counts: Luna `72`, Terra `72`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders","payments"]` | `["orders","payments"]` | `["orders","payments"]` |
| Columns | `["orders.id","orders.order_number","payments.amount","payments.id","payments.order_id"]` | `["orders.id","orders.order_number","payments.amount","payments.id","payments.order_id","payments.paid_at","payments.status"]` | `["orders.id","orders.order_number","payments.amount","payments.id","payments.order_id","payments.paid_at","payments.status"]` |
| Joins | `["orders.id<->payments.order_id"]` | `["orders.id<->payments.order_id"]` | `["orders.id<->payments.order_id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[[],null]` | `[["paid_at desc","id desc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT p.id AS payment_id, p.order_id, o.order_number, p.amount, p.paid_at, p.status FROM payments AS p JOIN orders AS o ON o.id = p.order_id;`
Terra SQL: `SELECT p.id AS payment_id, p.order_id, o.order_number, p.amount, p.status, p.paid_at
FROM payments AS p
JOIN orders AS o ON o.id = p.order_id
ORDER BY p.paid_at DESC, p.id DESC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-021 — joins

Question: Show customers and their regions.

Model-visible schema excerpt:

```text
[Table] customers
Description: Synthetic customer accounts.
[Columns]
- customers.created_at DATETIME — created_at from customers.
- customers.id INTEGER [PK] — id from customers.
- customers.name VARCHAR(150) — name from customers.
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.

[Table] regions
Description: Sales regions used for geographic analysis.
[Columns]
- regions.id INTEGER [PK] — id from regions.
- regions.name VARCHAR(100) — name from regions.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT c.name AS customer_name, r.name AS region_name FROM customers c JOIN regions r ON r.id = c.region_id ORDER BY c.id`
Gold result: unavailable in source artifacts; generated row counts: Luna `40`, Terra `40`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["customers","regions"]` | `["customers","regions"]` | `["customers","regions"]` |
| Columns | `["customers.id","customers.name","customers.region_id","regions.id","regions.name"]` | `["customers.id","customers.name","customers.region_id","regions.id","regions.name"]` | `["customers.id","customers.name","customers.region_id","regions.id","regions.name"]` |
| Joins | `["customers.region_id<->regions.id"]` | `["customers.region_id<->regions.id"]` | `["customers.region_id<->regions.id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["id"],null]` | `[["name","id"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT c.id AS customer_id, c.name AS customer_name, r.id AS region_id, r.name AS region_name FROM customers AS c LEFT JOIN regions AS r ON r.id = c.region_id ORDER BY c.id;`
Terra SQL: `SELECT c.id AS customer_id, c.name AS customer_name, r.id AS region_id, r.name AS region_name
FROM customers AS c
LEFT JOIN regions AS r ON r.id = c.region_id
ORDER BY c.name, c.id;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['JOIN_TYPE_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['JOIN_TYPE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-024 — multi_table_joins

Question: List orders with customer names and sales representatives.

Model-visible schema excerpt:

```text
[Table] customers
Description: Synthetic customer accounts.
[Columns]
- customers.created_at DATETIME — created_at from customers.
- customers.id INTEGER [PK] — id from customers.
- customers.name VARCHAR(150) — name from customers.
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.

[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.

[Table] sales_representatives
Description: Sales representatives responsible for customers and orders.
[Columns]
- sales_representatives.id INTEGER [PK] — id from sales_representatives.
- sales_representatives.name VARCHAR(150) — name from sales_representatives.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.

[Relationships]
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id

[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT o.order_number, c.name AS customer_name, s.name AS representative_name FROM orders o JOIN customers c ON c.id = o.customer_id JOIN sales_representatives s ON s.id = o.sales_rep_id ORDER BY o.id`
Gold result: unavailable in source artifacts; generated row counts: Luna `120`, Terra `120`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["customers","orders","sales_representatives"]` | `["customers","orders","sales_representatives"]` | `["customers","orders","sales_representatives"]` |
| Columns | `["customers.id","customers.name","orders.customer_id","orders.id","orders.order_number","orders.sales_rep_id","sales_representatives.id","sales_representatives.name"]` | `["customers.id","customers.name","orders.customer_id","orders.id","orders.order_number","orders.sales_rep_id","sales_representatives.id","sales_representatives.name"]` | `["customers.id","customers.name","orders.currency","orders.customer_id","orders.discount_amount","orders.id","orders.order_number","orders.ordered_at","orders.sales_rep_id","orders.status","orders.subtotal","orders.total_amount","sales_representatives.id","sales_representatives.name"]` |
| Joins | `["customers.id<->orders.customer_id","orders.sales_rep_id<->sales_representatives.id"]` | `["customers.id<->orders.customer_id","orders.sales_rep_id<->sales_representatives.id"]` | `["customers.id<->orders.customer_id","orders.sales_rep_id<->sales_representatives.id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id"],null]` | `[["id"],null]` | `[["ordered_at desc","id desc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT o.id AS order_id, o.order_number, c.name AS customer_name, sr.name AS sales_representative_name FROM orders AS o JOIN customers AS c ON c.id = o.customer_id LEFT JOIN sales_representatives AS sr ON sr.id = o.sales_rep_id ORDER BY o.id;`
Terra SQL: `SELECT o.id AS order_id, o.order_number, o.ordered_at, o.status, o.currency, o.subtotal, o.discount_amount, o.total_amount, c.name AS customer_name, sr.name AS sales_representative_name
FROM orders AS o
LEFT JOIN customers AS c ON c.id = o.customer_id
LEFT JOIN sales_representatives AS sr ON sr.id = o.sales_rep_id
ORDER BY o.ordered_at DESC, o.id DESC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['JOIN_TYPE_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['JOIN_TYPE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-027 — multi_table_joins

Question: Show refunded orders with product names.

Model-visible schema excerpt:

```text
[Table] order_items
Description: Product line items belonging to orders.
[Columns]
- order_items.discount_amount NUMERIC(12, 2) — discount_amount from order_items.
- order_items.id INTEGER [PK] — id from order_items.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- order_items.quantity INTEGER — quantity from order_items.
- order_items.unit_price NUMERIC(12, 2) — unit_price from order_items.

[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.

[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.

[Table] refunds
Description: Refund transactions associated with orders.
[Columns]
- refunds.amount NUMERIC(12, 2) — amount from refunds.
- refunds.id INTEGER [PK] — id from refunds.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- refunds.reason VARCHAR(200) — reason from refunds.
- refunds.refunded_at DATETIME — refunded_at from refunds.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT o.order_number, p.name, r.amount FROM refunds r JOIN orders o ON o.id = r.order_id JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id ORDER BY r.id, oi.id`
Gold result: unavailable in source artifacts; generated row counts: Luna `20`, Terra `20`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["order_items","orders","products","refunds"]` | `["order_items","orders","products","refunds"]` | `["order_items","orders","products","refunds"]` |
| Columns | `["order_items.id","order_items.order_id","order_items.product_id","orders.id","orders.order_number","products.id","products.name","refunds.amount","refunds.id","refunds.order_id"]` | `["order_items.order_id","order_items.product_id","orders.id","orders.order_number","products.id","products.name","refunds.amount","refunds.order_id","refunds.refunded_at"]` | `["order_items.discount_amount","order_items.order_id","order_items.product_id","order_items.quantity","order_items.unit_price","orders.currency","orders.id","orders.order_number","orders.ordered_at","orders.status","orders.total_amount","products.id","products.name","products.sku","refunds.amount","refunds.id","refunds.order_id","refunds.reason","refunds.refunded_at"]` |
| Joins | `["order_items.order_id<->orders.id","order_items.product_id<->products.id","orders.id<->refunds.order_id"]` | `["order_items.order_id<->orders.id","order_items.product_id<->products.id","orders.id<->refunds.order_id"]` | `["order_items.order_id<->orders.id","order_items.product_id<->products.id","orders.id<->refunds.order_id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["id","id"],null]` | `[["refunded_at desc","id","name"],null]` | `[["refunded_at desc","id","name"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT DISTINCT o.id AS order_id, o.order_number, p.name AS product_name, r.amount AS refund_amount, r.refunded_at FROM orders AS o JOIN refunds AS r ON r.order_id = o.id JOIN order_items AS oi ON oi.order_id = o.id JOIN products AS p ON p.id = oi.product_id ORDER BY r.refunded_at DESC, o.id, p.name;`
Terra SQL: `SELECT o.id AS order_id, o.order_number, o.ordered_at, o.status AS order_status, o.currency, o.total_amount, r.id AS refund_id, r.refunded_at, r.amount AS refund_amount, r.reason AS refund_reason, p.name AS product_name, p.sku AS product_sku, oi.quantity, oi.unit_price, oi.discount_amount AS item_discount_amount FROM orders o JOIN refunds r ON r.order_id = o.id JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id ORDER BY r.refunded_at DESC, o.id, p.name;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-028 — multi_table_joins

Question: Which sales representatives serve customers in each region?

Model-visible schema excerpt:

```text
[Table] customers
Description: Synthetic customer accounts.
[Columns]
- customers.created_at DATETIME — created_at from customers.
- customers.id INTEGER [PK] — id from customers.
- customers.name VARCHAR(150) — name from customers.
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.

[Table] regions
Description: Sales regions used for geographic analysis.
[Columns]
- regions.id INTEGER [PK] — id from regions.
- regions.name VARCHAR(100) — name from regions.

[Table] sales_representatives
Description: Sales representatives responsible for customers and orders.
[Columns]
- sales_representatives.id INTEGER [PK] — id from sales_representatives.
- sales_representatives.name VARCHAR(150) — name from sales_representatives.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.

[Relationships]
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id

[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT s.name AS representative_name, c.name AS customer_name, r.name AS region_name FROM sales_representatives s JOIN customers c ON c.sales_rep_id = s.id JOIN regions r ON r.id = c.region_id ORDER BY s.id, c.id`
Gold result: unavailable in source artifacts; generated row counts: Luna `8`, Terra `8`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["customers","regions","sales_representatives"]` | `["customers","regions","sales_representatives"]` | `["customers","regions","sales_representatives"]` |
| Columns | `["customers.id","customers.name","customers.region_id","customers.sales_rep_id","regions.id","regions.name","sales_representatives.id","sales_representatives.name"]` | `["customers.id","customers.region_id","customers.sales_rep_id","regions.id","regions.name","sales_representatives.id","sales_representatives.name"]` | `["customers.id","customers.region_id","customers.sales_rep_id","regions.id","regions.name","sales_representatives.id","sales_representatives.name"]` |
| Joins | `["customers.region_id<->regions.id","customers.sales_rep_id<->sales_representatives.id"]` | `["customers.region_id<->regions.id","customers.sales_rep_id<->sales_representatives.id"]` | `["customers.region_id<->regions.id","customers.sales_rep_id<->sales_representatives.id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[["count(distinct id)"],["id","id","name","name"]]` | `[["count(distinct id)"],["id","id","name","name"]]` |
| Order/limit | `[["id","id"],null]` | `[["name","name"],null]` | `[["name","name"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT r.name AS region_name, sr.name AS sales_representative_name, COUNT(DISTINCT c.id) AS customer_count FROM regions AS r JOIN customers AS c ON c.region_id = r.id JOIN sales_representatives AS sr ON sr.id = c.sales_rep_id GROUP BY r.id, r.name, sr.id, sr.name ORDER BY r.name, sr.name;`
Terra SQL: `SELECT r.name AS region_name, sr.id AS sales_representative_id, sr.name AS sales_representative_name, COUNT(DISTINCT c.id) AS customer_count
FROM regions r
JOIN customers c ON c.region_id = r.id
JOIN sales_representatives sr ON sr.id = c.sales_rep_id
GROUP BY r.id, r.name, sr.id, sr.name
ORDER BY r.name, sr.name;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `AGGREGATION_GRAIN_ERROR`, Terra `AGGREGATION_GRAIN_ERROR`
Secondary tags: Luna `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-039 — top_k

Question: Which products generated the most revenue?

Model-visible schema excerpt:

```text
[Table] order_items
Description: Product line items belonging to orders.
[Columns]
- order_items.discount_amount NUMERIC(12, 2) — discount_amount from order_items.
- order_items.id INTEGER [PK] — id from order_items.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- order_items.quantity INTEGER — quantity from order_items.
- order_items.unit_price NUMERIC(12, 2) — unit_price from order_items.

[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.name ORDER BY revenue DESC LIMIT 5`
Gold result: unavailable in source artifacts; generated row counts: Luna `24`, Terra `24`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["order_items","products"]` | `["order_items","products"]` | `["order_items","products"]` |
| Columns | `["order_items.product_id","order_items.quantity","order_items.unit_price","products.id","products.name"]` | `["order_items.discount_amount","order_items.product_id","order_items.quantity","order_items.unit_price","products.id","products.name","products.sku"]` | `["order_items.discount_amount","order_items.product_id","order_items.quantity","order_items.unit_price","products.category","products.id","products.name","products.sku"]` |
| Joins | `["order_items.product_id<->products.id"]` | `["order_items.product_id<->products.id"]` | `["order_items.product_id<->products.id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[["sum(quantity * unit_price)"],["name"]]` | `[["sum(quantity * unit_price - coalesce(discount_amount, 0))"],["id","name","sku"]]` | `[["sum((quantity * unit_price) - discount_amount)"],["category","id","name","sku"]]` |
| Order/limit | `[["revenue desc"],5]` | `[["revenue desc nulls last"],null]` | `[["revenue desc","id asc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT p.id AS product_id, p.name AS product_name, p.sku, SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0)) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.id, p.name, p.sku ORDER BY revenue DESC NULLS LAST;`
Terra SQL: `SELECT p.id AS product_id, p.name AS product_name, p.sku, p.category, SUM((oi.quantity * oi.unit_price) - oi.discount_amount) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.id, p.name, p.sku, p.category ORDER BY revenue DESC, p.id ASC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `BUSINESS_SEMANTIC_INFORMATION_MISSING`, Terra `BUSINESS_SEMANTIC_INFORMATION_MISSING`
Secondary tags: Luna `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'LIMIT_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'LIMIT_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-040 — top_k

Question: Which customers spent the most?

Model-visible schema excerpt:

```text
[Table] customers
Description: Synthetic customer accounts.
[Columns]
- customers.created_at DATETIME — created_at from customers.
- customers.id INTEGER [PK] — id from customers.
- customers.name VARCHAR(150) — name from customers.
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.

[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT customer_id, SUM(total_amount) AS spend FROM orders GROUP BY customer_id ORDER BY spend DESC LIMIT 5`
Gold result: unavailable in source artifacts; generated row counts: Luna `10`, Terra `40`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["customers","orders"]` | `["customers","orders"]` |
| Columns | `["orders.customer_id","orders.total_amount"]` | `["customers.id","customers.name","orders.customer_id","orders.total_amount"]` | `["customers.id","customers.name","orders.customer_id","orders.id","orders.total_amount"]` |
| Joins | `[]` | `["customers.id<->orders.customer_id"]` | `["customers.id<->orders.customer_id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[["sum(total_amount)"],["customer_id"]]` | `[["sum(total_amount)"],["id","name"]]` | `[["count(id)","sum(total_amount)"],["id","name"]]` |
| Order/limit | `[["spend desc"],5]` | `[["total_spent desc","id"],10]` | `[["total_spent desc","order_count desc","name asc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT c.id AS customer_id, c.name AS customer_name, COALESCE(SUM(o.total_amount), 0) AS total_spent FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY total_spent DESC, c.id LIMIT 10;`
Terra SQL: `SELECT c.id AS customer_id, c.name AS customer_name, COALESCE(SUM(o.total_amount), 0) AS total_spent, COUNT(o.id) AS order_count
FROM customers AS c
LEFT JOIN orders AS o
  ON o.customer_id = c.id
GROUP BY c.id, c.name
ORDER BY total_spent DESC, order_count DESC, c.name ASC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `ORDER_TOPK_ERROR`, Terra `AGGREGATION_GRAIN_ERROR`
Secondary tags: Luna `['GROUPING_DIFFERENCE', 'JOIN_EDGE_DIFFERENCE', 'LIMIT_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'JOIN_EDGE_DIFFERENCE', 'LIMIT_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-041 — top_k

Question: What are the five largest refunds?

Model-visible schema excerpt:

```text
[Table] refunds
Description: Refund transactions associated with orders.
[Columns]
- refunds.amount NUMERIC(12, 2) — amount from refunds.
- refunds.id INTEGER [PK] — id from refunds.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- refunds.reason VARCHAR(200) — reason from refunds.
- refunds.refunded_at DATETIME — refunded_at from refunds.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT id, order_id, amount FROM refunds ORDER BY amount DESC LIMIT 5`
Gold result: unavailable in source artifacts; generated row counts: Luna `5`, Terra `5`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["refunds"]` | `["refunds"]` | `["refunds"]` |
| Columns | `["refunds.amount","refunds.id","refunds.order_id"]` | `["refunds.amount","refunds.id","refunds.order_id","refunds.reason","refunds.refunded_at"]` | `["refunds.amount","refunds.id","refunds.order_id","refunds.reason","refunds.refunded_at"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["amount desc"],5]` | `[["amount desc","id asc"],5]` | `[["amount desc","refunded_at desc","id desc"],5]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT id, order_id, amount, reason, refunded_at FROM refunds ORDER BY amount DESC, id ASC LIMIT 5;`
Terra SQL: `SELECT r.id, r.order_id, r.amount, r.reason, r.refunded_at
FROM refunds AS r
ORDER BY r.amount DESC, r.refunded_at DESC, r.id DESC
LIMIT 5;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `ORDER_TOPK_ERROR`, Terra `ORDER_TOPK_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-042 — top_k

Question: Which categories have the highest average price?

Model-visible schema excerpt:

```text
[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT category, AVG(unit_price) AS average_price FROM products GROUP BY category ORDER BY average_price DESC LIMIT 3`
Gold result: unavailable in source artifacts; generated row counts: Luna `4`, Terra `4`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["products"]` | `["products"]` | `["products"]` |
| Columns | `["products.category","products.unit_price"]` | `["products.category","products.unit_price"]` | `["products.category","products.unit_price"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[["avg(unit_price)"],["category"]]` | `[["avg(unit_price)"],["category"]]` | `[["avg(unit_price)"],["category"]]` |
| Order/limit | `[["average_price desc"],3]` | `[["average_price desc"],null]` | `[["average_price desc","category asc"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT p.category, AVG(p.unit_price) AS average_price
FROM products AS p
GROUP BY p.category
ORDER BY average_price DESC;`
Terra SQL: `SELECT p.category, AVG(p.unit_price) AS average_price
FROM products AS p
GROUP BY p.category
ORDER BY average_price DESC, p.category ASC;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `ORDER_TOPK_ERROR`, Terra `ORDER_TOPK_ERROR`
Secondary tags: Luna `['LIMIT_DIFFERENCE']`, Terra `['LIMIT_DIFFERENCE', 'ORDER_DIFFERENCE']`

### m2-043 — ratios

Question: What share of orders are completed?

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)::numeric / COUNT(*) AS completed_share FROM orders`
Gold result: unavailable in source artifacts; generated row counts: Luna `None`, Terra `None`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["orders"]` | `["orders"]` |
| Columns | `["orders.status"]` | `["orders.status"]` | `["orders.status"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `[]` | `["status = 'completed'"]` | `["status = 'completed'"]` |
| Aggregation/grouping | `[["count(*)","sum(case when status = 'completed' then 1 else 0 end)"],[]]` | `[["count(*)","count(*)"],[]]` | `[["count(*)","count(*)"],[]]` |
| Order/limit | `[[],null]` | `[[],null]` | `[[],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT COALESCE(100.0 * COUNT(*) FILTER (WHERE status = 'completed') / NULLIF(COUNT(*), 0), 0) AS completed_order_share_percent FROM orders;`
Terra SQL: `SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'completed') / NULLIF(COUNT(*), 0), 2) AS completed_order_share_percent FROM orders;`
Result-equivalence: Luna `None`, Terra `None`
Primary root cause: Luna `POLICY_REJECTION`, Terra `POLICY_REJECTION`
Secondary tags: Luna `['AGGREGATION_DIFFERENCE', 'FILTER_DIFFERENCE', 'FORBIDDEN_FUNCTION:*', 'FORBIDDEN_FUNCTION:NULLIF', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['AGGREGATION_DIFFERENCE', 'FILTER_DIFFERENCE', 'FORBIDDEN_FUNCTION:*', 'FORBIDDEN_FUNCTION:NULLIF', 'FORBIDDEN_FUNCTION:ROUND', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-044 — ratios

Question: What is the average refund as a share of order revenue?

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.

[Table] refunds
Description: Refund transactions associated with orders.
[Columns]
- refunds.amount NUMERIC(12, 2) — amount from refunds.
- refunds.id INTEGER [PK] — id from refunds.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- refunds.reason VARCHAR(200) — reason from refunds.
- refunds.refunded_at DATETIME — refunded_at from refunds.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT SUM(r.amount) / SUM(o.total_amount) AS refund_share FROM refunds r JOIN orders o ON o.id = r.order_id`
Gold result: unavailable in source artifacts; generated row counts: Luna `None`, Terra `None`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders","refunds"]` | `["orders","refunds"]` | `["orders","refunds"]` |
| Columns | `["orders.id","orders.total_amount","refunds.amount","refunds.order_id"]` | `["orders.id","orders.total_amount","refunds.amount","refunds.order_id"]` | `["orders.id","orders.total_amount","refund_totals.order_id","refund_totals.refund_amount","refunds.amount","refunds.order_id"]` |
| Joins | `["orders.id<->refunds.order_id"]` | `["orders.id<->refunds.order_id"]` | `["orders.id<->refund_totals.order_id"]` |
| Filters | `[]` | `[]` | `["total_amount > 0"]` |
| Aggregation/grouping | `[["sum(amount)","sum(total_amount)"],[]]` | `[["avg(refund_total / nullif(order_revenue, 0))","sum(amount)"],["id","total_amount"]]` | `[["avg(refund_amount / nullif(total_amount, 0))","sum(amount)"],["order_id"]]` |
| Order/limit | `[[],null]` | `[[],null]` | `[[],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT AVG(refund_total / NULLIF(order_revenue, 0)) AS average_refund_share_of_order_revenue
FROM (
  SELECT o.id,
         o.total_amount AS order_revenue,
         COALESCE(SUM(r.amount), 0) AS refund_total
  FROM orders o
  LEFT JOIN refunds r ON r.order_id = o.id
  GROUP BY o.id, o.total_amount
) AS order_refunds;`
Terra SQL: `WITH refund_totals AS (
  SELECT
    r.order_id,
    SUM(r.amount) AS refund_amount
  FROM refunds r
  GROUP BY r.order_id
)
SELECT
  AVG(rt.refund_amount / NULLIF(o.total_amount, 0)) AS average_refund_share_of_order_revenue
FROM refund_totals rt
JOIN orders o
  ON o.id = rt.order_id
WHERE o.total_amount > 0;`
Result-equivalence: Luna `None`, Terra `None`
Primary root cause: Luna `POLICY_REJECTION`, Terra `POLICY_REJECTION`
Secondary tags: Luna `['AGGREGATION_DIFFERENCE', 'FORBIDDEN_FUNCTION:NULLIF', 'GROUPING_DIFFERENCE', 'JOIN_TYPE_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['AGGREGATION_DIFFERENCE', 'FILTER_DIFFERENCE', 'FORBIDDEN_FUNCTION:NULLIF', 'GROUPING_DIFFERENCE', 'JOIN_EDGE_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-046 — window_functions

Question: Rank products by unit price.

Model-visible schema excerpt:

```text
[Table] products
Description: Products sold through the commerce domain.
[Columns]
- products.category VARCHAR(100) — category from products.
- products.id INTEGER [PK] — id from products.
- products.name VARCHAR(150) — name from products.
- products.sku VARCHAR(50) — sku from products.
- products.unit_price NUMERIC(12, 2) — unit_price from products.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT name, unit_price, RANK() OVER (ORDER BY unit_price DESC) AS price_rank FROM products ORDER BY price_rank, name`
Gold result: unavailable in source artifacts; generated row counts: Luna `24`, Terra `24`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["products"]` | `["products"]` | `["products"]` |
| Columns | `["products.name","products.unit_price"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` | `["products.category","products.id","products.name","products.sku","products.unit_price"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[["rank()"],[]]` | `[["rank()"],[]]` | `[["rank()"],[]]` |
| Order/limit | `[["price_rank","name","unit_price desc"],null]` | `[["price_rank","id","unit_price desc"],null]` | `[["unit_price_rank","id","unit_price desc"],null]` |
| Window | `[["rank()",[],["unit_price desc"]]]` | `[["rank()",[],["unit_price desc"]]]` | `[["rank()",[],["unit_price desc"]]]` |

Luna SQL: `SELECT p.id, p.name, p.sku, p.category, p.unit_price, RANK() OVER (ORDER BY p.unit_price DESC) AS price_rank FROM products AS p ORDER BY price_rank, p.id;`
Terra SQL: `SELECT p.id AS product_id, p.name AS product_name, p.sku, p.category, p.unit_price, RANK() OVER (ORDER BY p.unit_price DESC) AS unit_price_rank FROM products AS p ORDER BY unit_price_rank, p.id;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-047 — window_functions

Question: Number each order for every customer by date.

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT customer_id, order_number, ordered_at, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY ordered_at, id) AS order_number_for_customer FROM orders ORDER BY customer_id, ordered_at, id`
Gold result: unavailable in source artifacts; generated row counts: Luna `120`, Terra `120`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["orders"]` | `["orders"]` |
| Columns | `["orders.customer_id","orders.id","orders.order_number","orders.ordered_at"]` | `["orders.customer_id","orders.id","orders.order_number","orders.ordered_at"]` | `["orders.customer_id","orders.id","orders.order_number","orders.ordered_at"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[[],[]]` | `[[],[]]` | `[[],[]]` |
| Order/limit | `[["customer_id","ordered_at","id","ordered_at","id"],null]` | `[["customer_id","order_number_for_customer","ordered_at","id"],null]` | `[["ordered_at","id"],null]` |
| Window | `[["row_number()",["customer_id"],["ordered_at","id"]]]` | `[["row_number()",["customer_id"],["ordered_at","id"]]]` | `[["row_number()",["customer_id"],["ordered_at","id"]]]` |

Luna SQL: `SELECT o.customer_id, o.id AS order_id, o.order_number, o.ordered_at, ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS order_number_for_customer FROM orders AS o ORDER BY o.customer_id, order_number_for_customer;`
Terra SQL: `SELECT o.customer_id, o.id AS order_id, o.order_number, o.ordered_at, ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS order_number_for_customer FROM orders o;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-048 — window_functions

Question: Show each order and the previous order date for that customer.

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT customer_id, order_number, ordered_at, LAG(ordered_at) OVER (PARTITION BY customer_id ORDER BY ordered_at, id) AS previous_order_at FROM orders ORDER BY customer_id, ordered_at, id`
Gold result: unavailable in source artifacts; generated row counts: Luna `120`, Terra `120`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["orders"]` | `["orders"]` |
| Columns | `["orders.customer_id","orders.id","orders.order_number","orders.ordered_at"]` | `["orders.customer_id","orders.id","orders.order_number","orders.ordered_at"]` | `["orders.customer_id","orders.id","orders.order_number","orders.ordered_at"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[["lag(ordered_at)"],[]]` | `[["lag(ordered_at)"],[]]` | `[["lag(ordered_at)"],[]]` |
| Order/limit | `[["customer_id","ordered_at","id","ordered_at","id"],null]` | `[["customer_id","ordered_at","id","ordered_at","id"],null]` | `[["customer_id","ordered_at","id","ordered_at","id"],null]` |
| Window | `[["lag(ordered_at)",["customer_id"],["ordered_at","id"]]]` | `[["lag(ordered_at)",["customer_id"],["ordered_at","id"]]]` | `[["lag(ordered_at)",["customer_id"],["ordered_at","id"]]]` |

Luna SQL: `SELECT o.id AS order_id, o.order_number, o.customer_id, o.ordered_at, LAG(o.ordered_at) OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS previous_order_date FROM orders AS o ORDER BY o.customer_id, o.ordered_at, o.id;`
Terra SQL: `SELECT o.id AS order_id, o.order_number, o.customer_id, o.ordered_at AS order_date, LAG(o.ordered_at) OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS previous_order_date FROM orders o ORDER BY o.customer_id, o.ordered_at, o.id;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-015 — simple_aggregation

Question: What is the total amount paid?

Model-visible schema excerpt:

```text
[Table] payments
Description: Payment transactions associated with orders.
[Columns]
- payments.amount NUMERIC(12, 2) — amount from payments.
- payments.id INTEGER [PK] — id from payments.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- payments.paid_at DATETIME — paid_at from payments.
- payments.status VARCHAR(30) — status from payments.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT SUM(amount) AS paid_total FROM payments`
Gold result: unavailable in source artifacts; generated row counts: Luna `1`, Terra `1`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["payments"]` | `["payments"]` | `["payments"]` |
| Columns | `["payments.amount"]` | `["payments.amount"]` | `["payments.amount","payments.status"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `[]` | `[]` | `["status = 'paid'"]` |
| Aggregation/grouping | `[["sum(amount)"],[]]` | `[["sum(amount)"],[]]` | `[["sum(amount)"],[]]` |
| Order/limit | `[[],null]` | `[[],null]` | `[[],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT COALESCE(SUM(amount), 0) AS total_amount_paid FROM payments;`
Terra SQL: `SELECT COALESCE(SUM(p.amount), 0) AS total_amount_paid
FROM payments AS p
WHERE p.status = 'paid';`
Result-equivalence: Luna `True`, Terra `False`
Primary root cause: Luna `None`, Terra `FILTER_CONSTRUCTION_ERROR`
Secondary tags: Luna `['PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['FILTER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-035 — group_by

Question: How many orders does each customer have?

Model-visible schema excerpt:

```text
[Table] customers
Description: Synthetic customer accounts.
[Columns]
- customers.created_at DATETIME — created_at from customers.
- customers.id INTEGER [PK] — id from customers.
- customers.name VARCHAR(150) — name from customers.
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.

[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT customer_id, COUNT(*) AS order_count FROM orders GROUP BY customer_id ORDER BY customer_id`
Gold result: unavailable in source artifacts; generated row counts: Luna `40`, Terra `40`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["customers","orders"]` | `["customers","orders"]` |
| Columns | `["orders.customer_id"]` | `["customers.id","customers.name","orders.customer_id","orders.id"]` | `["customers.id","customers.name","orders.customer_id","orders.id"]` |
| Joins | `[]` | `["customers.id<->orders.customer_id"]` | `["customers.id<->orders.customer_id"]` |
| Filters | `[]` | `[]` | `[]` |
| Aggregation/grouping | `[["count(*)"],["customer_id"]]` | `[["count(id)"],["id","name"]]` | `[["count(id)"],["id","name"]]` |
| Order/limit | `[["customer_id"],null]` | `[["id"],null]` | `[["order_count desc","id"],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT c.id AS customer_id, c.name AS customer_name, COUNT(o.id) AS order_count FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.id;`
Terra SQL: `SELECT c.id AS customer_id, c.name AS customer_name, COUNT(o.id) AS order_count
FROM customers AS c
LEFT JOIN orders AS o ON o.customer_id = c.id
GROUP BY c.id, c.name
ORDER BY order_count DESC, c.id;`
Result-equivalence: Luna `False`, Terra `False`
Primary root cause: Luna `OBJECT_SELECTION_ERROR`, Terra `OBJECT_SELECTION_ERROR`
Secondary tags: Luna `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'JOIN_EDGE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['AGGREGATION_DIFFERENCE', 'GROUPING_DIFFERENCE', 'JOIN_EDGE_DIFFERENCE', 'ORDER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`

### m2-030 — date_filtering

Question: What was the revenue in February 2025?

Model-visible schema excerpt:

```text
[Table] orders
Description: Customer orders and governed order-level revenue amounts.
[Columns]
- orders.currency VARCHAR(3) — currency from orders.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.discount_amount NUMERIC(12, 2) — discount_amount from orders.
- orders.id INTEGER [PK] — id from orders.
- orders.order_number VARCHAR(50) — order_number from orders.
- orders.ordered_at DATETIME — ordered_at from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- orders.status VARCHAR(30) — status from orders.
- orders.subtotal NUMERIC(12, 2) — subtotal from orders.
- orders.total_amount NUMERIC(12, 2) — total_amount from orders.


[Relationships]
- customers.region_id INTEGER [FK -> regions.id] — region_id from customers.
- customers.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from customers.
- order_items.order_id INTEGER [FK -> orders.id] — order_id from order_items.
- order_items.product_id INTEGER [FK -> products.id] — product_id from order_items.
- orders.customer_id INTEGER [FK -> customers.id] — customer_id from orders.
- orders.sales_rep_id INTEGER [FK -> sales_representatives.id] — sales_rep_id from orders.
- payments.order_id INTEGER [FK -> orders.id] — order_id from payments.
- refunds.order_id INTEGER [FK -> orders.id] — order_id from refunds.
- sales_representatives.region_id INTEGER [FK -> regions.id] — region_id from sales_representatives.
- customers.region_id -> regions.id
- customers.sales_rep_id -> sales_representatives.id
- order_items.order_id -> orders.id
- order_items.product_id -> products.id
- orders.customer_id -> customers.id
- orders.sales_rep_id -> sales_representatives.id
- payments.order_id -> orders.id
- refunds.order_id -> orders.id
- sales_representatives.region_id -> regions.id
```

Gold SQL: `SELECT SUM(total_amount) AS revenue FROM orders WHERE ordered_at >= '2025-02-01' AND ordered_at < '2025-03-01'`
Gold result: unavailable in source artifacts; generated row counts: Luna `1`, Terra `1`.

| Field | Gold | Luna | Terra |
|---|---|---|---|
| Tables | `["orders"]` | `["orders"]` | `["orders"]` |
| Columns | `["orders.ordered_at","orders.total_amount"]` | `["orders.ordered_at","orders.status","orders.total_amount"]` | `["orders.ordered_at","orders.total_amount"]` |
| Joins | `[]` | `[]` | `[]` |
| Filters | `["ordered_at >= '2025-02-01' and ordered_at < '2025-03-01'"]` | `["ordered_at >= '2025-02-01' and ordered_at < '2025-03-01' and not status in ('cancelled', 'canceled')"]` | `["ordered_at >= '2025-02-01' and ordered_at < '2025-03-01'"]` |
| Aggregation/grouping | `[["sum(total_amount)"],[]]` | `[["sum(total_amount)"],[]]` | `[["sum(total_amount)"],[]]` |
| Order/limit | `[[],null]` | `[[],null]` | `[[],null]` |
| Window | `[]` | `[]` | `[]` |

Luna SQL: `SELECT COALESCE(SUM(o.total_amount), 0) AS revenue FROM orders AS o WHERE o.ordered_at >= TIMESTAMP '2025-02-01' AND o.ordered_at < TIMESTAMP '2025-03-01' AND o.status NOT IN ('cancelled', 'canceled');`
Terra SQL: `SELECT COALESCE(SUM(o.total_amount), 0) AS revenue FROM orders AS o WHERE o.ordered_at >= DATE '2025-02-01' AND o.ordered_at < DATE '2025-03-01';`
Result-equivalence: Luna `False`, Terra `True`
Primary root cause: Luna `BUSINESS_SEMANTIC_INFORMATION_MISSING`, Terra `EVALUATOR_OR_FIXTURE_ARTIFACT`
Secondary tags: Luna `['FILTER_DIFFERENCE', 'PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`, Terra `['PROJECTION_ARITY_OR_ORDINAL_DIFFERENCE']`
