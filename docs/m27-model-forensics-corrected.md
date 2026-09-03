# M2.7 Model Capacity and I/O Forensics

- Stage: `dev`
- Dataset SHA-256: `5cf5a80366debff4efd6e33e5ea6ee1f668aa870f770d2982a1f0396d014cf87`
- Models: `gpt-5.6-luna` vs `gpt-5.6-terra`
- Architecture: `FULL_COMPACT + ONE_SHOT` for both arms
- Reasoning: `none`; temperature omitted/provider default
- QueryIntent/retrieval/repair: disabled

| Metric | Luna | Terra | Delta |
|---|---:|---:|---:|
| Result equivalence | 15/48 (31.25%) | 15/48 (31.25%) | 0 (0.00%) |
| Input tokens | 43968 | 43968 | 0.0 avg/question |
| Output tokens | 2628 | 3207 | 12.0625 avg/question |
| Reasoning tokens | 0 | 0 | — |
| Average latency | 1479.3040370623203 ms | 1579.17594516645 ms | 99.87190810412972 ms |
| P95 latency | 1993.0703509013254 ms | 2063.6955849990045 ms | 70.62523409767914 ms |

Pairwise outcomes: `{"BOTH_CORRECT": 14, "BOTH_INCORRECT": 32, "LUNA_ONLY_CORRECT": 1, "TERRA_ONLY_CORRECT": 1}`

Model I/O capture is evaluation-only and excludes credentials, headers, result rows, and hidden reasoning.
No pricing configuration is available; API cost was not computed.

## Stratified model-I/O cases

The following cases are selected offline after both arms completed. No hidden reasoning is included.

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
Luna SQL: `SELECT COALESCE(SUM(o.total_amount), 0) AS revenue FROM orders AS o WHERE o.ordered_at >= TIMESTAMP '2025-02-01' AND o.ordered_at < TIMESTAMP '2025-03-01' AND o.status NOT IN ('cancelled', 'canceled');`
Terra SQL: `SELECT COALESCE(SUM(o.total_amount), 0) AS revenue FROM orders AS o WHERE o.ordered_at >= DATE '2025-02-01' AND o.ordered_at < DATE '2025-03-01';`

Luna structural diff: `{"AGGREGATION_EXTRA": ["SUM(o.total_amount)"], "AGGREGATION_MISSING": ["SUM(total_amount)"], "COLUMN_EXTRA": ["orders.status"], "FILTER_EXTRA": ["o.ordered_at >= CAST('2025-02-01' AS TIMESTAMP) AND o.ordered_at < CAST('2025-03-01' AS TIMESTAMP) AND NOT o.status IN ('cancelled', 'canceled')"], "FILTER_MISSING": ["ordered_at >= '2025-02-01' AND ordered_at < '2025-03-01'"]}`
Terra structural diff: `{"AGGREGATION_EXTRA": ["SUM(o.total_amount)"], "AGGREGATION_MISSING": ["SUM(total_amount)"], "FILTER_EXTRA": ["o.ordered_at >= CAST('2025-02-01' AS DATE) AND o.ordered_at < CAST('2025-03-01' AS DATE)"], "FILTER_MISSING": ["ordered_at >= '2025-02-01' AND ordered_at < '2025-03-01'"]}`
Outcome: Luna=False, Terra=True
Forensic diagnosis: Luna=AGGREGATION_GRAIN_ERROR, Terra=EVALUATOR_OR_FIXTURE_WARNING

### m2-035 — group_by

Question: How many orders does each customer have?

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
Luna SQL: `SELECT c.id AS customer_id, c.name AS customer_name, COUNT(o.id) AS order_count FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.id;`
Terra SQL: `SELECT c.id AS customer_id, c.name AS customer_name, COUNT(o.id) AS order_count
FROM customers AS c
LEFT JOIN orders AS o ON o.customer_id = c.id
GROUP BY c.id, c.name
ORDER BY order_count DESC, c.id;`

Luna structural diff: `{"AGGREGATION_EXTRA": ["COUNT(o.id)"], "AGGREGATION_MISSING": ["COUNT(*)"], "COLUMN_EXTRA": ["customers.id", "customers.name", "orders.id"], "GROUP_BY_EXTRA": ["c.id", "c.name"], "GROUP_BY_MISSING": ["customer_id"], "JOIN_EXTRA": ["customers.id<->orders.customer_id"], "ORDER_BY_EXTRA": ["c.id"], "ORDER_BY_MISSING": ["customer_id"], "TABLE_EXTRA": ["customers"]}`
Terra structural diff: `{"AGGREGATION_EXTRA": ["COUNT(o.id)"], "AGGREGATION_MISSING": ["COUNT(*)"], "COLUMN_EXTRA": ["customers.id", "customers.name", "orders.id"], "GROUP_BY_EXTRA": ["c.id", "c.name"], "GROUP_BY_MISSING": ["customer_id"], "JOIN_EXTRA": ["customers.id<->orders.customer_id"], "ORDER_BY_EXTRA": ["c.id", "order_count DESC"], "ORDER_BY_MISSING": ["customer_id"], "ORDER_DIRECTION_DIFFERENT": {"generated": ["DESC", "ASC"], "gold": ["ASC"]}, "TABLE_EXTRA": ["customers"]}`
Outcome: Luna=False, Terra=False
Forensic diagnosis: Luna=SCHEMA_SELECTION_ERROR, Terra=SCHEMA_SELECTION_ERROR

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

Luna structural diff: `{"AGGREGATION_EXTRA": ["SUM(oi.quantity)"], "COLUMN_MISSING": ["order_items.id"], "GROUP_BY_EXTRA": ["p.id", "p.name"], "ORDER_BY_EXTRA": ["p.name"], "ORDER_BY_MISSING": ["oi.id", "p.id"], "ORDER_DIRECTION_DIFFERENT": {"generated": ["ASC"], "gold": ["ASC", "ASC"]}}`
Terra structural diff: `{"AGGREGATION_EXTRA": ["SUM(oi.quantity)"], "COLUMN_EXTRA": ["products.category", "products.sku"], "COLUMN_MISSING": ["order_items.id"], "COLUMN_SUBSTITUTED": true, "GROUP_BY_EXTRA": ["p.category", "p.id", "p.name", "p.sku"], "ORDER_BY_EXTRA": ["order_item_quantity DESC", "p.name ASC"], "ORDER_BY_MISSING": ["oi.id", "p.id"], "ORDER_DIRECTION_DIFFERENT": {"generated": ["DESC", "ASC"], "gold": ["ASC", "ASC"]}}`
Outcome: Luna=False, Terra=False
Forensic diagnosis: Luna=ORDER_LIMIT_REASONING_ERROR, Terra=ORDER_LIMIT_REASONING_ERROR

### m2-023 — multi_table_joins

Question: Show order line items with product names and order numbers.

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
```

Gold SQL: `SELECT oi.id, p.name, o.order_number, oi.quantity FROM order_items oi JOIN products p ON p.id = oi.product_id JOIN orders o ON o.id = oi.order_id ORDER BY oi.id`
Luna SQL: `SELECT oi.id AS order_item_id, oi.order_id, o.order_number, oi.product_id, p.name AS product_name, oi.quantity, oi.unit_price, oi.discount_amount FROM order_items AS oi JOIN orders AS o ON o.id = oi.order_id JOIN products AS p ON p.id = oi.product_id ORDER BY o.ordered_at, o.id, oi.id;`
Terra SQL: `SELECT oi.id AS order_item_id, o.order_number, p.name AS product_name, oi.quantity, oi.unit_price, oi.discount_amount FROM order_items AS oi JOIN orders AS o ON o.id = oi.order_id JOIN products AS p ON p.id = oi.product_id ORDER BY o.order_number, oi.id`

Luna structural diff: `{"COLUMN_EXTRA": ["order_items.discount_amount", "order_items.unit_price", "orders.ordered_at"], "ORDER_BY_EXTRA": ["o.id", "o.ordered_at"], "ORDER_BY_MISSING": [], "ORDER_DIRECTION_DIFFERENT": {"generated": ["ASC", "ASC", "ASC"], "gold": ["ASC"]}}`
Terra structural diff: `{"COLUMN_EXTRA": ["order_items.discount_amount", "order_items.unit_price"], "ORDER_BY_EXTRA": ["o.order_number"], "ORDER_BY_MISSING": [], "ORDER_DIRECTION_DIFFERENT": {"generated": ["ASC", "ASC"], "gold": ["ASC"]}}`
Outcome: Luna=False, Terra=False
Forensic diagnosis: Luna=ORDER_LIMIT_REASONING_ERROR, Terra=ORDER_LIMIT_REASONING_ERROR

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
Luna SQL: `SELECT COALESCE(100.0 * COUNT(*) FILTER (WHERE status = 'completed') / NULLIF(COUNT(*), 0), 0) AS completed_order_share_percent FROM orders;`
Terra SQL: `SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'completed') / NULLIF(COUNT(*), 0), 2) AS completed_order_share_percent FROM orders;`

Luna structural diff: `{"AGGREGATION_MISSING": ["SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)"], "FILTER_EXTRA": ["status = 'completed'"]}`
Terra structural diff: `{"AGGREGATION_MISSING": ["SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)"], "FILTER_EXTRA": ["status = 'completed'"]}`
Outcome: Luna=None, Terra=None
Forensic diagnosis: Luna=POLICY_REJECTION, Terra=POLICY_REJECTION

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
Luna SQL: `SELECT COALESCE(SUM(amount), 0) AS total_amount_paid FROM payments;`
Terra SQL: `SELECT COALESCE(SUM(p.amount), 0) AS total_amount_paid
FROM payments AS p
WHERE p.status = 'paid';`

Luna structural diff: `{}`
Terra structural diff: `{"AGGREGATION_EXTRA": ["SUM(p.amount)"], "AGGREGATION_MISSING": ["SUM(amount)"], "COLUMN_EXTRA": ["payments.status"], "FILTER_EXTRA": ["p.status = 'paid'"]}`
Outcome: Luna=True, Terra=False
Forensic diagnosis: Luna=None, Terra=AGGREGATION_GRAIN_ERROR

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
Luna SQL: `SELECT id, order_number, customer_id, sales_rep_id, ordered_at, currency, subtotal, discount_amount, total_amount, status FROM orders WHERE LOWER(status) = 'pending' ORDER BY ordered_at DESC, id DESC;`
Terra SQL: `SELECT o.id, o.order_number, o.customer_id, c.name AS customer_name, o.sales_rep_id, sr.name AS sales_rep_name, o.ordered_at, o.currency, o.subtotal, o.discount_amount, o.total_amount, o.status
FROM orders AS o
LEFT JOIN customers AS c ON c.id = o.customer_id
LEFT JOIN sales_representatives AS sr ON sr.id = o.sales_rep_id
WHERE o.status = 'pending'
ORDER BY o.ordered_at DESC, o.id DESC;`

Luna structural diff: `{"COLUMN_EXTRA": ["orders.currency", "orders.customer_id", "orders.discount_amount", "orders.ordered_at", "orders.sales_rep_id", "orders.subtotal", "orders.total_amount"], "FILTER_EXTRA": ["LOWER(status) = 'pending'"], "FILTER_MISSING": ["status = 'pending'"], "ORDER_BY_EXTRA": ["id DESC", "ordered_at DESC"], "ORDER_BY_MISSING": ["id"], "ORDER_DIRECTION_DIFFERENT": {"generated": ["DESC", "DESC"], "gold": ["ASC"]}}`
Terra structural diff: `{"COLUMN_EXTRA": ["customers.id", "customers.name", "orders.currency", "orders.customer_id", "orders.discount_amount", "orders.ordered_at", "orders.sales_rep_id", "orders.subtotal", "orders.total_amount", "sales_representatives.id", "sales_representatives.name"], "FILTER_EXTRA": ["o.status = 'pending'"], "FILTER_MISSING": ["status = 'pending'"], "JOIN_EXTRA": ["customers.id<->orders.customer_id", "orders.sales_rep_id<->sales_representatives.id"], "ORDER_BY_EXTRA": ["o.id DESC", "o.ordered_at DESC"], "ORDER_BY_MISSING": ["id"], "ORDER_DIRECTION_DIFFERENT": {"generated": ["DESC", "DESC"], "gold": ["ASC"]}, "TABLE_EXTRA": ["customers", "sales_representatives"]}`
Outcome: Luna=None, Terra=False
Forensic diagnosis: Luna=POLICY_REJECTION, Terra=SCHEMA_SELECTION_ERROR

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
Luna SQL: `SELECT p.id AS product_id, p.name AS product_name, p.sku, SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0)) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.id, p.name, p.sku ORDER BY revenue DESC NULLS LAST;`
Terra SQL: `SELECT p.id AS product_id, p.name AS product_name, p.sku, p.category, SUM((oi.quantity * oi.unit_price) - oi.discount_amount) AS revenue FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.id, p.name, p.sku, p.category ORDER BY revenue DESC, p.id ASC;`

Luna structural diff: `{"AGGREGATION_EXTRA": ["SUM(oi.quantity * oi.unit_price - COALESCE(oi.discount_amount, 0))"], "AGGREGATION_MISSING": ["SUM(oi.quantity * oi.unit_price)"], "COLUMN_EXTRA": ["order_items.discount_amount", "products.sku"], "GROUP_BY_EXTRA": ["p.id", "p.sku"], "LIMIT_DIFFERENT": {"generated": null, "gold": 5}, "LIMIT_MISSING": 5, "ORDER_BY_EXTRA": ["revenue DESC NULLS LAST"], "ORDER_BY_MISSING": ["revenue DESC"]}`
Terra structural diff: `{"AGGREGATION_EXTRA": ["SUM((oi.quantity * oi.unit_price) - oi.discount_amount)"], "AGGREGATION_MISSING": ["SUM(oi.quantity * oi.unit_price)"], "COLUMN_EXTRA": ["order_items.discount_amount", "products.category", "products.sku"], "GROUP_BY_EXTRA": ["p.category", "p.id", "p.sku"], "LIMIT_DIFFERENT": {"generated": null, "gold": 5}, "LIMIT_MISSING": 5, "ORDER_BY_EXTRA": ["p.id ASC"], "ORDER_BY_MISSING": [], "ORDER_DIRECTION_DIFFERENT": {"generated": ["DESC", "ASC"], "gold": ["DESC"]}}`
Outcome: Luna=False, Terra=False
Forensic diagnosis: Luna=ORDER_LIMIT_REASONING_ERROR, Terra=ORDER_LIMIT_REASONING_ERROR

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
Luna SQL: `SELECT p.id, p.name, p.sku, p.category, p.unit_price, RANK() OVER (ORDER BY p.unit_price DESC) AS price_rank FROM products AS p ORDER BY price_rank, p.id;`
Terra SQL: `SELECT p.id AS product_id, p.name AS product_name, p.sku, p.category, p.unit_price, RANK() OVER (ORDER BY p.unit_price DESC) AS unit_price_rank FROM products AS p ORDER BY unit_price_rank, p.id;`

Luna structural diff: `{"COLUMN_EXTRA": ["products.category", "products.id", "products.sku"], "ORDER_BY_EXTRA": ["p.id", "p.unit_price DESC"], "ORDER_BY_MISSING": ["name", "unit_price DESC"], "WINDOW_EXTRA": ["RANK() OVER (ORDER BY p.unit_price DESC)"], "WINDOW_MISSING": ["RANK() OVER (ORDER BY unit_price DESC)"], "WINDOW_ORDER_DIFFERENT": {"generated": ["ORDER BY p.unit_price DESC"], "gold": ["ORDER BY unit_price DESC"]}}`
Terra structural diff: `{"COLUMN_EXTRA": ["products.category", "products.id", "products.sku"], "ORDER_BY_EXTRA": ["p.id", "p.unit_price DESC", "unit_price_rank"], "ORDER_BY_MISSING": ["name", "price_rank", "unit_price DESC"], "WINDOW_EXTRA": ["RANK() OVER (ORDER BY p.unit_price DESC)"], "WINDOW_MISSING": ["RANK() OVER (ORDER BY unit_price DESC)"], "WINDOW_ORDER_DIFFERENT": {"generated": ["ORDER BY p.unit_price DESC"], "gold": ["ORDER BY unit_price DESC"]}}`
Outcome: Luna=False, Terra=False
Forensic diagnosis: Luna=WINDOW_REASONING_ERROR, Terra=WINDOW_REASONING_ERROR

### m2-033 — date_filtering

Question: Show orders from the first quarter of 2025.

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

Gold SQL: `SELECT id, order_number, ordered_at FROM orders WHERE ordered_at >= '2025-01-01' AND ordered_at < '2025-04-01' ORDER BY ordered_at, id`
Luna SQL: `SELECT o.*
FROM orders AS o
WHERE o.ordered_at >= TIMESTAMP '2025-01-01 00:00:00'
  AND o.ordered_at < TIMESTAMP '2025-04-01 00:00:00';`
Terra SQL: `SELECT o.id, o.order_number, o.customer_id, o.sales_rep_id, o.ordered_at, o.status, o.currency, o.subtotal, o.discount_amount, o.total_amount
FROM orders AS o
WHERE o.ordered_at >= TIMESTAMP '2025-01-01 00:00:00'
  AND o.ordered_at < TIMESTAMP '2025-04-01 00:00:00'
ORDER BY o.ordered_at, o.id;`

Luna structural diff: `{"COLUMN_MISSING": ["orders.id", "orders.order_number"], "FILTER_EXTRA": ["o.ordered_at >= CAST('2025-01-01 00:00:00' AS TIMESTAMP) AND o.ordered_at < CAST('2025-04-01 00:00:00' AS TIMESTAMP)"], "FILTER_MISSING": ["ordered_at >= '2025-01-01' AND ordered_at < '2025-04-01'"], "ORDER_BY_EXTRA": [], "ORDER_BY_MISSING": ["id", "ordered_at"], "ORDER_DIRECTION_DIFFERENT": {"generated": [], "gold": ["ASC", "ASC"]}}`
Terra structural diff: `{"COLUMN_EXTRA": ["orders.currency", "orders.customer_id", "orders.discount_amount", "orders.sales_rep_id", "orders.status", "orders.subtotal", "orders.total_amount"], "FILTER_EXTRA": ["o.ordered_at >= CAST('2025-01-01 00:00:00' AS TIMESTAMP) AND o.ordered_at < CAST('2025-04-01 00:00:00' AS TIMESTAMP)"], "FILTER_MISSING": ["ordered_at >= '2025-01-01' AND ordered_at < '2025-04-01'"], "ORDER_BY_EXTRA": ["o.id", "o.ordered_at"], "ORDER_BY_MISSING": ["id", "ordered_at"]}`
Outcome: Luna=None, Terra=False
Forensic diagnosis: Luna=POLICY_REJECTION, Terra=ORDER_LIMIT_REASONING_ERROR

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
Luna SQL: `SELECT id, name, sku, unit_price FROM products WHERE category = 'Electronics' ORDER BY name;`
Terra SQL: `SELECT p.id, p.name, p.sku, p.category, p.unit_price
FROM products AS p
WHERE p.category = 'Electronics'
ORDER BY p.name, p.id;`

Luna structural diff: `{"COLUMN_EXTRA": ["products.sku", "products.unit_price"], "ORDER_BY_EXTRA": ["name"], "ORDER_BY_MISSING": ["id"]}`
Terra structural diff: `{"COLUMN_EXTRA": ["products.sku", "products.unit_price"], "FILTER_EXTRA": ["p.category = 'Electronics'"], "FILTER_MISSING": ["category = 'Electronics'"], "ORDER_BY_EXTRA": ["p.id", "p.name"], "ORDER_BY_MISSING": ["id"], "ORDER_DIRECTION_DIFFERENT": {"generated": ["ASC", "ASC"], "gold": ["ASC"]}}`
Outcome: Luna=False, Terra=False
Forensic diagnosis: Luna=ORDER_LIMIT_REASONING_ERROR, Terra=ORDER_LIMIT_REASONING_ERROR

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
Luna SQL: `SELECT id, name, sku, category, unit_price FROM products WHERE unit_price > 100 ORDER BY unit_price DESC, name ASC;`
Terra SQL: `SELECT p.id, p.sku, p.name, p.category, p.unit_price
FROM products AS p
WHERE p.unit_price > 100
ORDER BY p.unit_price DESC, p.name ASC;`

Luna structural diff: `{"COLUMN_EXTRA": ["products.category", "products.sku"], "ORDER_BY_EXTRA": ["name ASC"], "ORDER_BY_MISSING": [], "ORDER_DIRECTION_DIFFERENT": {"generated": ["DESC", "ASC"], "gold": ["DESC"]}}`
Terra structural diff: `{"COLUMN_EXTRA": ["products.category", "products.sku"], "FILTER_EXTRA": ["p.unit_price > 100"], "FILTER_MISSING": ["unit_price > 100"], "ORDER_BY_EXTRA": ["p.name ASC", "p.unit_price DESC"], "ORDER_BY_MISSING": ["unit_price DESC"], "ORDER_DIRECTION_DIFFERENT": {"generated": ["DESC", "ASC"], "gold": ["DESC"]}}`
Outcome: Luna=False, Terra=False
Forensic diagnosis: Luna=ORDER_LIMIT_REASONING_ERROR, Terra=ORDER_LIMIT_REASONING_ERROR
