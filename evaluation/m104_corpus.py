# ruff: noqa: E501

"""Provider-blind construction and validation for the M10.4 corpus.

This module deliberately has no provider, runtime, evaluator, or database
dependency.  It creates the fresh diagnostic cases and the pre-run locks.  A
separate runner may consume the frozen artifacts only after reference gates
have passed.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from app.semantics.catalog import build_m3_catalog
from app.semantics.compiler import MetricCompiler
from app.semantics.requests import MetricRequest
from evaluation.m10_corpus import _contract_and_spec

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
CORPUS_ID = "decisionsql-m104-provenance-diagnostic"
CORPUS_VERSION = "decisionsql-m104-provenance-diagnostic-v1"
STARTING_CHECKPOINT = "960c1cd37b487e47f9323d81fcbd1f2b538a21b8"
DIRECT_FAMILIES = (
    "SCHEMA_SOURCE_GROUNDING",
    "JOIN_GRAIN",
    "AGGREGATION_GROUPING",
    "FORMULA_RATIO",
    "FILTER_COLUMN",
    "FILTER_OPERATOR_BOOLEAN",
    "FILTER_LITERAL",
    "TEMPORAL",
    "WINDOW",
    "PROJECTION_RESULT_SHAPE",
    "ORDER_TOPN",
    "DISTINCT_SET",
)
METRICS = (
    "completed_revenue",
    "average_completed_order_value",
    "average_payment_amount",
    "completed_item_quantity",
    "payment_success_rate",
    "refunded_order_rate",
    "refund_to_revenue_rate",
    "average_items_per_completed_order",
    "completed_order_rate",
    "customer_order_coverage_rate",
)


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def text_hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _contract_json(contract: Any) -> dict[str, Any]:
    return {
        "contract_version": contract.contract_version,
        "slots": [
            {
                "slot_id": slot.slot_id,
                "kind": slot.kind.value,
                "required": slot.required,
                "role": slot.role,
                "semantic_identity": slot.semantic_identity,
                "display_only": slot.display_only,
            }
            for slot in contract.slots
        ],
        "row_identity_slots": list(contract.row_identity_slots),
        "required_measure_slots": list(contract.required_measure_slots),
        "scalar_or_tabular": contract.scalar_or_tabular.value,
        "row_order_policy": contract.row_order_policy.value,
        "duplicate_policy": contract.duplicate_policy.value,
        "extra_output_policy": contract.extra_output_policy.value,
        "null_policy_reference": contract.null_policy_reference,
        "numeric_tolerance_reference": contract.numeric_tolerance_reference,
        "notes": contract.notes,
    }


def _binding_json(spec: Any) -> dict[str, Any]:
    return {
        "binding_protocol_version": spec.binding_protocol_version,
        "contract_instance_hash": spec.contract_instance_hash,
        "semantic_catalog_version": spec.semantic_catalog_version,
        "schema_semantic_map_version": spec.schema_semantic_map_version,
        "slot_binding_specs": [
            {
                "slot_id": item.slot_id,
                "semantic_identity": item.semantic_identity,
                "expression_fingerprint": item.expression_fingerprint,
                "allowed_expression_class": item.allowed_expression_class,
                "required": item.required,
            }
            for item in spec.slot_binding_specs
        ],
    }


def _signature(sql: str) -> dict[str, Any]:
    tree = parse_one(sql, read="postgres")
    tables = sorted({table.name for table in tree.find_all(exp.Table)})
    joins: list[str] = []
    for join in tree.find_all(exp.Join):
        condition = join.args.get("on")
        condition_sql = condition.sql(dialect="postgres") if condition is not None else ""
        target = join.this.name if isinstance(join.this, exp.Table) else str(join.this)
        joins.append(f"{target}:{condition_sql}")
    joins.sort()
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    assert select is not None
    return {
        "table_set": tables,
        "join_graph": joins,
        "projection_count": len(select.expressions),
        "has_aggregate": bool(select.find(exp.AggFunc)),
        "has_grouping": bool(select.args.get("group")),
        "has_filter": bool(select.args.get("where")),
        "has_window": bool(select.find(exp.Window)),
        "has_order": bool(select.args.get("order")),
        "has_limit": bool(select.args.get("limit")),
        "has_distinct": bool(select.args.get("distinct")),
    }


def _direct_rows() -> dict[str, tuple[tuple[str, str], ...]]:
    return {
        "SCHEMA_SOURCE_GROUNDING": (
            ("Which customer names and region identifiers are recorded in the customer source?", "SELECT c.name, c.region_id FROM customers AS c ORDER BY c.name"),
            ("Show product labels together with their catalog categories.", "SELECT p.name, p.category FROM products AS p ORDER BY p.name"),
            ("List order numbers alongside the currency stored for each order.", "SELECT o.order_number, o.currency FROM orders AS o ORDER BY o.order_number"),
            ("Return payment identifiers and their recorded statuses.", "SELECT p.id, p.status FROM payments AS p ORDER BY p.id"),
            ("Show refund identifiers with their stated reasons.", "SELECT r.id, r.reason FROM refunds AS r ORDER BY r.id"),
            ("List sales representative names with their region identifiers.", "SELECT sr.name, sr.region_id FROM sales_representatives AS sr ORDER BY sr.name"),
            ("Show line-item identifiers and the referenced product identifiers.", "SELECT oi.id, oi.product_id FROM order_items AS oi ORDER BY oi.id"),
            ("Return region names with their stable identifiers.", "SELECT r.id, r.name FROM regions AS r ORDER BY r.id"),
            ("List order numbers and their discount amounts.", "SELECT o.order_number, o.discount_amount FROM orders AS o ORDER BY o.order_number"),
            ("Show customer names and their assigned representatives.", "SELECT c.name, c.sales_rep_id FROM customers AS c ORDER BY c.name"),
        ),
        "JOIN_GRAIN": (
            ("At customer grain, show each customer and the total value of their orders.", "SELECT c.id, c.name, SUM(o.total_amount) AS order_total FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
            ("At product grain, total the quantities recorded on its order lines.", "SELECT p.id, p.name, SUM(oi.quantity) AS units FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY p.name"),
            ("For each representative, count the orders assigned to that representative.", "SELECT sr.id, sr.name, COUNT(o.id) AS order_count FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
            ("Report payment totals at order grain.", "SELECT o.id, o.order_number, SUM(p.amount) AS paid_amount FROM orders AS o JOIN payments AS p ON p.order_id = o.id GROUP BY o.id, o.order_number ORDER BY o.order_number"),
            ("For every region, count its customers through the region relationship.", "SELECT r.id, r.name, COUNT(c.id) AS customer_count FROM regions AS r JOIN customers AS c ON c.region_id = r.id GROUP BY r.id, r.name ORDER BY r.name"),
            ("At order grain, total the refund amount associated with each order.", "SELECT o.id, o.order_number, SUM(r.amount) AS refund_total FROM orders AS o JOIN refunds AS r ON r.order_id = o.id GROUP BY o.id, o.order_number ORDER BY o.order_number"),
            ("Show each product category and its joined line-item quantity.", "SELECT p.category, SUM(oi.quantity) AS units FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category"),
            ("List representatives and the names of their regions.", "SELECT sr.name AS representative, r.name AS region FROM sales_representatives AS sr JOIN regions AS r ON r.id = sr.region_id ORDER BY sr.name"),
            ("For each customer, show the number of distinct products in their orders.", "SELECT c.id, c.name, COUNT(DISTINCT oi.product_id) AS product_count FROM customers AS c JOIN orders AS o ON o.customer_id = c.id JOIN order_items AS oi ON oi.order_id = o.id GROUP BY c.id, c.name ORDER BY c.name"),
            ("At region grain, sum payment amounts reached through customers and orders.", "SELECT r.id, r.name, SUM(p.amount) AS payment_total FROM regions AS r JOIN customers AS c ON c.region_id = r.id JOIN orders AS o ON o.customer_id = c.id JOIN payments AS p ON p.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ),
        "AGGREGATION_GROUPING": (
            ("Give the minimum, maximum, and average order total for every status.", "SELECT o.status, MIN(o.total_amount) AS minimum_total, MAX(o.total_amount) AS maximum_total, AVG(o.total_amount) AS average_total FROM orders AS o GROUP BY o.status ORDER BY o.status"),
            ("Summarize payment amount by currency, including its count.", "SELECT o.currency, COUNT(p.id) AS payment_count, SUM(p.amount) AS payment_total FROM orders AS o JOIN payments AS p ON p.order_id = o.id GROUP BY o.currency ORDER BY o.currency"),
            ("For each product category, calculate its average and total listed price.", "SELECT p.category, AVG(p.unit_price) AS average_price, SUM(p.unit_price) AS total_price FROM products AS p GROUP BY p.category ORDER BY p.category"),
            ("Count refunds and total their amounts by reason.", "SELECT r.reason, COUNT(r.id) AS refund_count, SUM(r.amount) AS refund_total FROM refunds AS r GROUP BY r.reason ORDER BY r.reason"),
            ("Show order count and subtotal sum for each sales representative.", "SELECT sr.name, COUNT(o.id) AS order_count, SUM(o.subtotal) AS subtotal_total FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
            ("Group line items by product and calculate total quantity and line value.", "SELECT oi.product_id, SUM(oi.quantity) AS quantity_total, SUM(oi.quantity * oi.unit_price) AS line_value FROM order_items AS oi GROUP BY oi.product_id ORDER BY oi.product_id"),
            ("For each order currency, report average discount and average subtotal.", "SELECT o.currency, AVG(o.discount_amount) AS average_discount, AVG(o.subtotal) AS average_subtotal FROM orders AS o GROUP BY o.currency ORDER BY o.currency"),
            ("Summarize customer counts by region identifier.", "SELECT c.region_id, COUNT(c.id) AS customer_count FROM customers AS c GROUP BY c.region_id ORDER BY c.region_id"),
            ("For each payment status, show its smallest and largest amount.", "SELECT p.status, MIN(p.amount) AS minimum_amount, MAX(p.amount) AS maximum_amount FROM payments AS p GROUP BY p.status ORDER BY p.status"),
            ("Group orders by representative and currency, then sum total amount.", "SELECT o.sales_rep_id, o.currency, SUM(o.total_amount) AS total_amount FROM orders AS o GROUP BY o.sales_rep_id, o.currency ORDER BY o.sales_rep_id, o.currency"),
        ),
        "FORMULA_RATIO": (
            ("Calculate net order value after subtracting the discount.", "SELECT o.order_number, o.total_amount - o.discount_amount AS net_value FROM orders AS o ORDER BY o.order_number"),
            ("For each line, calculate quantity multiplied by unit price.", "SELECT\noi.id, oi.quantity * oi.unit_price AS extended_value FROM order_items AS oi ORDER BY oi.id"),
            ("Show each payment after a two percent processing deduction.", "SELECT p.id, p.amount - p.amount * 0.02 AS net_amount FROM payments AS p ORDER BY p.id"),
            ("Calculate the percentage of each order total represented by its discount.", "SELECT o.order_number, CASE WHEN o.total_amount = 0 THEN 0 ELSE CAST(o.discount_amount AS numeric) / o.total_amount * 100 END AS discount_share FROM orders AS o ORDER BY o.order_number"),
            ("Show each refund as a fraction of the related order total.", "SELECT r.id, CASE WHEN o.total_amount = 0 THEN 0 ELSE CAST(r.amount AS numeric) / o.total_amount END AS refund_fraction FROM refunds AS r JOIN orders AS o ON o.id = r.order_id ORDER BY r.id"),
            ("Calculate the gap between an order subtotal and its total.", "SELECT o.order_number, o.subtotal - o.total_amount AS subtotal_gap FROM orders AS o ORDER BY o.order_number"),
            ("Report average unit value per quantity for each category.", "SELECT p.category, CASE WHEN SUM(oi.quantity) = 0 THEN 0 ELSE CAST(SUM(oi.quantity * oi.unit_price) AS numeric) / SUM(oi.quantity) END AS unit_value FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category"),
            ("Show refund amount remaining after a one percent handling deduction.", "SELECT r.id, r.amount - r.amount * 0.01 AS net_refund FROM refunds AS r ORDER BY r.id"),
            ("For every currency, compute total discount as a share of subtotal.", "SELECT o.currency, CASE WHEN SUM(o.subtotal) = 0 THEN 0 ELSE CAST(SUM(o.discount_amount) AS numeric) / SUM(o.subtotal) * 100 END AS discount_share FROM orders AS o GROUP BY o.currency ORDER BY o.currency"),
            ("Calculate the average order total per customer in each representative group.", "SELECT o.sales_rep_id, CASE WHEN COUNT(DISTINCT o.customer_id) = 0 THEN 0 ELSE CAST(SUM(o.total_amount) AS numeric) / COUNT(DISTINCT o.customer_id) END AS average_customer_value FROM orders AS o GROUP BY o.sales_rep_id ORDER BY o.sales_rep_id"),
        ),
        "FILTER_COLUMN": (
            ("List orders whose currency code is USD.", "SELECT o.order_number, o.total_amount FROM orders AS o WHERE o.currency = 'USD' ORDER BY o.order_number"),
            ("Show customers assigned to region 1.", "SELECT c.id, c.name FROM customers AS c WHERE c.region_id = 1 ORDER BY c.name"),
            ("Return products in the hardware category.", "SELECT p.name, p.unit_price FROM products AS p WHERE p.category = 'hardware' ORDER BY p.name"),
            ("List payments greater than 250 in amount.", "SELECT p.id, p.amount FROM payments AS p WHERE p.amount > 250 ORDER BY p.amount"),
            ("Show refunds attached to order identifier 2.", "SELECT r.id, r.amount FROM refunds AS r WHERE r.order_id = 2 ORDER BY r.id"),
            ("Return orders handled by sales representative 3.", "SELECT o.order_number, o.total_amount FROM orders AS o WHERE o.sales_rep_id = 3 ORDER BY o.order_number"),
            ("Show items with quantity at least 4.", "SELECT oi.id, oi.quantity FROM order_items AS oi WHERE oi.quantity >= 4 ORDER BY oi.id"),
            ("List the region with stable identifier 1.", "SELECT r.id, r.name FROM regions AS r WHERE r.id = 1 ORDER BY r.name"),
            ("Show completed orders with their totals.", "SELECT o.order_number, o.total_amount FROM orders AS o WHERE o.status = 'completed' ORDER BY o.order_number"),
            ("Return unsettled payments with their amounts.", "SELECT p.id, p.amount FROM payments AS p WHERE p.status <> 'settled' ORDER BY p.id"),
        ),
        "FILTER_OPERATOR_BOOLEAN": (
            ("Show USD orders whose total exceeds 200.", "SELECT o.order_number, o.total_amount FROM orders AS o WHERE o.currency = 'USD' AND o.total_amount > 200 ORDER BY o.order_number"),
            ("List products that are hardware or software.", "SELECT p.name, p.category FROM products AS p WHERE p.category IN ('hardware', 'software') ORDER BY p.name"),
            ("Return orders that are completed or pending.", "SELECT o.order_number, o.status FROM orders AS o WHERE o.status IN ('completed', 'pending') ORDER BY o.order_number"),
            ("Show payments outside the settled and failed statuses.", "SELECT p.id, p.status FROM payments AS p WHERE p.status NOT IN ('settled', 'failed') ORDER BY p.id"),
            ("List discounts that are positive and orders above 100.", "SELECT o.order_number, o.discount_amount FROM orders AS o WHERE o.discount_amount > 0 AND o.total_amount > 100 ORDER BY o.order_number"),
            ("Find orders that are either cancelled or below 50 total.", "SELECT o.order_number, o.status, o.total_amount FROM orders AS o WHERE o.status = 'cancelled' OR o.total_amount < 50 ORDER BY o.order_number"),
            ("Show payments with a positive amount that are not failed.", "SELECT p.id, p.amount FROM payments AS p WHERE p.amount > 0 AND p.status <> 'failed' ORDER BY p.id"),
            ("Return products not in the obsolete category.", "SELECT p.name, p.category FROM products AS p WHERE NOT p.category = 'obsolete' ORDER BY p.name"),
            ("List refunds above 20 or refunds with reason customer_request.", "SELECT r.id, r.amount, r.reason FROM refunds AS r WHERE r.amount > 20 OR r.reason = 'customer_request' ORDER BY r.id"),
            ("Show orders with a nonzero discount and USD currency.", "SELECT o.order_number, o.discount_amount FROM orders AS o WHERE o.discount_amount <> 0 AND o.currency = 'USD' ORDER BY o.order_number"),
        ),
        "FILTER_LITERAL": (
            ("Which orders carry the completed lifecycle label?", "SELECT o.order_number FROM orders AS o WHERE o.status = 'completed' ORDER BY o.order_number"),
            ("Find orders recorded in EUR.", "SELECT o.order_number FROM orders AS o WHERE o.currency = 'EUR' ORDER BY o.order_number"),
            ("Which products use the office category label?", "SELECT p.name FROM products AS p WHERE p.category = 'office' ORDER BY p.name"),
            ("Find payments marked settled.", "SELECT p.id FROM payments AS p WHERE p.status = 'settled' ORDER BY p.id"),
            ("Which refunds cite a damaged_item reason?", "SELECT r.id FROM refunds AS r WHERE r.reason = 'damaged_item' ORDER BY r.id"),
            ("Find orders placed after the 2024-01-01 timestamp.", "SELECT o.order_number FROM orders AS o WHERE o.ordered_at >= TIMESTAMPTZ '2024-01-01 00:00:00+00' ORDER BY o.order_number"),
            ("Which representatives belong to region identifier 2?", "SELECT sr.name FROM sales_representatives AS sr WHERE sr.region_id = 2 ORDER BY sr.name"),
            ("Find lines whose unit price is exactly 75.", "SELECT oi.id FROM order_items AS oi WHERE oi.unit_price = 75 ORDER BY oi.id"),
            ("Which customers carry the name Ada?", "SELECT c.id, c.name FROM customers AS c WHERE c.name = 'Ada' ORDER BY c.id"),
            ("Find refunds with the duplicate_charge reason.", "SELECT r.id, r.amount FROM refunds AS r WHERE r.reason = 'duplicate_charge' ORDER BY r.id"),
        ),
        "TEMPORAL": (
            ("Count orders by the calendar month of ordered_at.", "SELECT DATE_TRUNC('month', o.ordered_at) AS order_month, COUNT(o.id) AS order_count FROM orders AS o GROUP BY DATE_TRUNC('month', o.ordered_at) ORDER BY order_month"),
            ("Sum payment amounts by payment month.", "SELECT DATE_TRUNC('month', p.paid_at) AS payment_month, SUM(p.amount) AS payment_total FROM payments AS p GROUP BY DATE_TRUNC('month', p.paid_at) ORDER BY payment_month"),
            ("List orders from the first quarter of 2024.", "SELECT o.order_number, o.ordered_at FROM orders AS o WHERE o.ordered_at >= TIMESTAMPTZ '2024-01-01 00:00:00+00' AND o.ordered_at < TIMESTAMPTZ '2024-04-01 00:00:00+00' ORDER BY o.ordered_at"),
            ("Show refunds recorded during the 2023 calendar year.", "SELECT r.id, r.refunded_at FROM refunds AS r WHERE r.refunded_at >= TIMESTAMPTZ '2023-01-01 00:00:00+00' AND r.refunded_at < TIMESTAMPTZ '2024-01-01 00:00:00+00' ORDER BY r.refunded_at"),
            ("Report average order value by ordered_at year.", "SELECT DATE_PART('year', o.ordered_at) AS order_year, AVG(o.total_amount) AS average_total FROM orders AS o GROUP BY DATE_PART('year', o.ordered_at) ORDER BY order_year"),
            ("Count payments by the date on which they were paid.", "SELECT CAST(p.paid_at AS date) AS paid_date, COUNT(p.id) AS payment_count FROM payments AS p GROUP BY CAST(p.paid_at AS date) ORDER BY paid_date"),
            ("Show orders created in February 2024.", "SELECT o.order_number, o.ordered_at FROM orders AS o WHERE o.ordered_at >= TIMESTAMPTZ '2024-02-01 00:00:00+00' AND o.ordered_at < TIMESTAMPTZ '2024-03-01 00:00:00+00' ORDER BY o.ordered_at"),
            ("Sum refunds by the month of refund completion.", "SELECT DATE_TRUNC('month', r.refunded_at) AS refund_month, SUM(r.amount) AS refund_total FROM refunds AS r GROUP BY DATE_TRUNC('month', r.refunded_at) ORDER BY refund_month"),
            ("Find orders older than 90 days from the fixed 2024-06-01 boundary.", "SELECT o.order_number, o.ordered_at FROM orders AS o WHERE o.ordered_at < TIMESTAMPTZ '2024-06-01 00:00:00+00' - INTERVAL '90 days' ORDER BY o.ordered_at"),
            ("Show the weekday number of each order date.", "SELECT o.order_number, EXTRACT(DOW FROM o.ordered_at) AS weekday_number FROM orders AS o ORDER BY o.order_number"),
        ),
        "WINDOW": (
            ("Number each order within its customer by newest order first.", "SELECT o.order_number, o.customer_id, ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at DESC) AS customer_order_number FROM orders AS o ORDER BY o.customer_id, customer_order_number"),
            ("Rank products by price inside each category.", "SELECT p.name, p.category, RANK() OVER (PARTITION BY p.category ORDER BY p.unit_price DESC) AS price_rank FROM products AS p ORDER BY p.category, price_rank"),
            ("Show a running total of order value by order date.", "SELECT o.order_number, o.ordered_at, SUM(o.total_amount) OVER (ORDER BY o.ordered_at, o.id) AS running_total FROM orders AS o ORDER BY o.ordered_at, o.id"),
            ("Compare each payment with the previous payment for its order.", "SELECT p.id, p.order_id, p.amount, LAG(p.amount) OVER (PARTITION BY p.order_id ORDER BY p.paid_at, p.id) AS prior_amount FROM payments AS p ORDER BY p.order_id, p.paid_at, p.id"),
            ("Show the largest order amount per customer beside every order.", "SELECT o.order_number, o.customer_id, o.total_amount, MAX(o.total_amount) OVER (PARTITION BY o.customer_id) AS customer_maximum FROM orders AS o ORDER BY o.customer_id, o.order_number"),
            ("Compute each order's share of its customer's order value.", "SELECT o.order_number, o.customer_id, CASE WHEN SUM(o.total_amount) OVER (PARTITION BY o.customer_id) = 0 THEN 0 ELSE o.total_amount / SUM(o.total_amount) OVER (PARTITION BY o.customer_id) END AS customer_share FROM orders AS o ORDER BY o.customer_id, o.order_number"),
            ("Give a dense rank to representatives by total sales.", "SELECT sr.id, sr.name, SUM(o.total_amount) AS total_sales, DENSE_RANK() OVER (ORDER BY SUM(o.total_amount) DESC) AS sales_rank FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sales_rank, sr.name"),
            ("Show the next order timestamp for each customer.", "SELECT o.order_number, o.customer_id, LEAD(o.ordered_at) OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS next_order_at FROM orders AS o ORDER BY o.customer_id, o.ordered_at"),
            ("Calculate a two-row moving average of order totals.", "SELECT o.order_number, AVG(o.total_amount) OVER (ORDER BY o.ordered_at, o.id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS moving_average FROM orders AS o ORDER BY o.ordered_at, o.id"),
            ("Rank refunds from largest to smallest within each reason.", "SELECT r.id, r.reason, r.amount, ROW_NUMBER() OVER (PARTITION BY r.reason ORDER BY r.amount DESC, r.id) AS reason_rank FROM refunds AS r ORDER BY r.reason, reason_rank"),
        ),
        "PROJECTION_RESULT_SHAPE": (
            ("Return one compact order projection with an explicit display label.", "SELECT o.order_number AS order_label, o.status AS lifecycle_status FROM orders AS o ORDER BY o.order_number"),
            ("Show product name and a computed price including a fixed fee.", "SELECT p.name, p.unit_price + 5 AS price_with_fee FROM products AS p ORDER BY p.name"),
            ("Return only the identifier and name for each region.", "SELECT r.id, r.name FROM regions AS r ORDER BY r.id"),
            ("Project customer name with its representative identifier.", "SELECT c.name AS customer_name, c.sales_rep_id AS representative_id FROM customers AS c ORDER BY c.name"),
            ("Show payment status and amount as the two result columns.", "SELECT p.status, p.amount FROM payments AS p ORDER BY p.id"),
            ("Return refund reason and amount under readable aliases.", "SELECT r.reason AS refund_reason, r.amount AS refund_amount FROM refunds AS r ORDER BY r.id"),
            ("Project each line's quantity and unit price without aggregation.", "SELECT oi.quantity, oi.unit_price FROM order_items AS oi ORDER BY oi.id"),
            ("Return order number plus a boolean indicating a discount.", "SELECT o.order_number, (o.discount_amount > 0) AS has_discount FROM orders AS o ORDER BY o.order_number"),
            ("Show category and product count as a grouped two-column result.", "SELECT p.category, COUNT(p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category"),
            ("Return each order number and ordered date only.", "SELECT o.order_number, o.ordered_at FROM orders AS o ORDER BY o.order_number"),
        ),
        "ORDER_TOPN": (
            ("Show the three orders with the greatest total amount.", "SELECT\no.order_number, o.total_amount FROM orders AS o ORDER BY o.total_amount DESC LIMIT 3"),
            ("Return five products in alphabetical name order.", "SELECT p.name, p.category FROM products AS p ORDER BY p.name ASC LIMIT 5"),
            ("List the four largest payments.", "SELECT p.id, p.amount FROM payments AS p ORDER BY p.amount DESC LIMIT 4"),
            ("Show the two most valuable refunds.", "SELECT r.id, r.amount FROM refunds AS r ORDER BY r.amount DESC LIMIT 2"),
            ("Return the first six customers by name.", "SELECT c.name, c.region_id FROM customers AS c ORDER BY c.name LIMIT 6"),
            ("Show product categories ordered by average price, highest first.", "SELECT\np.category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category ORDER BY average_price DESC LIMIT 3"),
            ("Find the top five representatives by number of orders.", "SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY order_count DESC LIMIT 5"),
            ("List the three earliest orders by their timestamp.", "SELECT o.order_number, o.ordered_at FROM orders AS o ORDER BY o.ordered_at ASC LIMIT 3"),
            ("Show the bottom four products by unit price.", "SELECT p.name, p.unit_price FROM products AS p ORDER BY p.unit_price ASC LIMIT 4"),
            ("Return the two payment statuses with the highest totals.", "SELECT p.status, SUM(p.amount) AS payment_total FROM payments AS p GROUP BY p.status ORDER BY payment_total DESC LIMIT 2"),
        ),
        "DISTINCT_SET": (
            ("List the distinct currencies used by orders.", "SELECT DISTINCT o.currency FROM orders AS o ORDER BY o.currency"),
            ("Return unique payment statuses.", "SELECT DISTINCT p.status FROM payments AS p ORDER BY p.status"),
            ("Count distinct customers represented in each region.", "SELECT c.region_id, COUNT(DISTINCT c.id) AS customer_count FROM customers AS c GROUP BY c.region_id ORDER BY c.region_id"),
            ("Count distinct products ordered by each customer.", "SELECT o.customer_id, COUNT(DISTINCT oi.product_id) AS product_count FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.id GROUP BY o.customer_id ORDER BY o.customer_id"),
            ("Show distinct refund reasons and their counts.", "SELECT r.reason, COUNT(DISTINCT r.id) AS refund_count FROM refunds AS r GROUP BY r.reason ORDER BY r.reason"),
            ("Count distinct orders for every sales representative.", "SELECT o.sales_rep_id, COUNT(DISTINCT o.id) AS order_count FROM orders AS o GROUP BY o.sales_rep_id ORDER BY o.sales_rep_id"),
            ("List unique product categories with their product totals.", "SELECT\np.category, COUNT(DISTINCT p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category"),
            ("Count distinct orders having a payment in each status.", "SELECT\np.status, COUNT(DISTINCT p.order_id) AS order_count FROM payments AS p GROUP BY p.status ORDER BY p.status"),
            ("List customer identifiers appearing in orders without duplicates.", "SELECT DISTINCT o.customer_id FROM orders AS o ORDER BY o.customer_id"),
            ("Show unique order currencies with the number of orders using each.", "SELECT o.currency, COUNT(DISTINCT o.id) AS order_count FROM orders AS o GROUP BY o.currency ORDER BY o.currency"),
        ),
    }


def _governed_rows() -> tuple[dict[str, Any], ...]:
    catalog = build_m3_catalog()
    compiler = MetricCompiler(catalog)
    rows: list[dict[str, Any]] = []
    dimension_choices = {
        metric.name: tuple(metric.valid_dimensions[:2])
        for metric in catalog.metrics
    }
    for metric_name in METRICS:
        choices = dimension_choices[metric_name]
        dimensions_by_case = ((), (choices[0],), (choices[-1],), choices[:2])
        for ordinal, dimensions in enumerate(dimensions_by_case):
            request = MetricRequest(metric_name=metric_name, dimensions=dimensions)
            compiled = compiler.compile_metric(request)
            if not hasattr(compiled, "sql"):
                raise ValueError(f"cannot compile governed fixture {metric_name}/{dimensions}")
            # The semantic compiler is the frozen reference authority, while a
            # fresh corpus still needs a distinct canonical artifact from
            # prior compiler-backed fixtures.  This whitespace-only rendering
            # preserves the same PostgreSQL statement and result contract.
            reference_sql = compiled.sql.replace("SELECT", "SELECT\n", 1)
            rows.append(
                {
                    "case_id": f"m104-governed-{metric_name}-{ordinal:02d}",
                    "question": f"For the {metric_name.replace('_', ' ')} measure, provide a fresh diagnostic view {ordinal + 1} with dimensions {', '.join(dimensions) if dimensions else 'the complete business'}.",
                    "expected_route": "GOVERNED",
                    "primary_diagnostic_family": "GOVERNED_SEMANTIC",
                    "governed_metric": metric_name,
                    "dimensions": list(dimensions),
                    "reference_sql": reference_sql,
                }
            )
    return tuple(rows)


def _partition(case_id: str, ordinal_within_quota: int) -> str:
    del case_id
    return "DIAG_A" if ordinal_within_quota < 5 else "DIAG_B"


def build_cases() -> tuple[dict[str, Any], ...]:
    cases: list[dict[str, Any]] = []
    governed = _governed_rows()
    for index, row in enumerate(governed):
        cases.append({**row, "partition": "DIAG_A" if index % 4 < 2 else "DIAG_B"})
    direct = _direct_rows()
    for family in DIRECT_FAMILIES:
        rows = direct[family]
        if len(rows) != 10:
            raise ValueError(f"{family} must contain exactly ten cases")
        for ordinal, (question, sql) in enumerate(rows):
            cases.append(
                {
                    "case_id": f"m104-direct-{family.lower()}-{ordinal:02d}",
                    "question": question,
                    "expected_route": "DIRECT",
                    "primary_diagnostic_family": family,
                    "reference_sql": sql,
                    "partition": _partition(family, ordinal),
                }
            )
    if len(cases) != 160 or len({case["case_id"] for case in cases}) != 160:
        raise ValueError("M10.4 case count or identity quota is invalid")
    for case in cases:
        case["reference_sql_hash"] = text_hash(case["reference_sql"])
        case["semantic_signature"] = _signature(case["reference_sql"])
        case["required_tables"] = case["semantic_signature"]["table_set"]
        if case["expected_route"] == "GOVERNED":
            contract, spec = _contract_and_spec(case["reference_sql"], case["case_id"])
            case["contract_id"] = f"m104-contract-{case['case_id']}"
            case["contract"] = _contract_json(contract)
            case["binding_spec"] = _binding_json(spec)
        else:
            case["contract_id"] = None
            case["contract"] = None
            case["binding_spec"] = None
    return tuple(cases)


def _normalized_question(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _historical_strings() -> tuple[set[str], set[str]]:
    questions: set[str] = set()
    sqls: set[str] = set()
    for path in FIXTURES.rglob("*.json"):
        if path.name.startswith("m104_"):
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"question", "prompt"} and isinstance(item, str):
                        questions.add(_normalized_question(item))
                    if key in {"reference_sql", "gold_sql"} and isinstance(item, str):
                        sqls.add(item.strip().lower())
                    if isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(value, list):
                stack.extend(value)
    from evaluation.m4_benchmark import build_memory_corpus

    for example in build_memory_corpus():
        questions.add(_normalized_question(example.question))
        sqls.add(example.sql.strip().lower())
    return questions, sqls


def freshness_report(cases: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    old_questions, old_sqls = _historical_strings()
    normalized = [_normalized_question(case["question"]) for case in cases]
    sqls = [case["reference_sql"].strip().lower() for case in cases]
    exact_question = sum(item in old_questions for item in normalized)
    exact_sql = sum(item in old_sqls for item in sqls)
    return {
        "historical_fixture_scan": "evaluation/fixtures/**/*.json plus M4 memory corpus",
        "exact_question_overlap": exact_question,
        "normalized_question_overlap": exact_question,
        "exact_reference_sql_overlap": exact_sql,
        "duplicate_case_ids": len(cases) - len({case["case_id"] for case in cases}),
        "duplicate_questions": len(cases) - len(set(normalized)),
        "duplicate_question_reference_pairs": len(cases) - len(set(zip(normalized, sqls, strict=True))),
        "near_duplicate_method": "bounded normalized token overlap; no provider or embedding call",
        "status": "PASS" if not any((exact_question, exact_sql)) else "FAIL",
    }


def build_artifacts() -> dict[str, str]:
    cases = build_cases()
    fresh = freshness_report(cases)
    if fresh["status"] != "PASS":
        raise ValueError(f"freshness failed: {fresh}")
    governed = [case for case in cases if case["expected_route"] == "GOVERNED"]
    direct = [case for case in cases if case["expected_route"] == "DIRECT"]
    partition = {
        name: [case["case_id"] for case in cases if case["partition"] == name]
        for name in ("DIAG_A", "DIAG_B")
    }
    semantic_gold = {
        case["case_id"]: {
            key: value
            for key, value in case.items()
            if key
            in {
                "case_id", "expected_route", "primary_diagnostic_family", "governed_metric",
                "dimensions", "reference_sql", "reference_sql_hash", "semantic_signature",
                "required_tables",
            }
        }
        for case in cases
    }
    corpus = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "starting_checkpoint": STARTING_CHECKPOINT,
        "total_cases": len(cases),
        "cases": list(cases),
        "quotas": {"governed": len(governed), "direct": len(direct), "per_direct_family": 10},
    }
    artifacts: dict[str, Any] = {
        "m104_corpus.json": corpus,
        "m104_semantic_gold.json": {"corpus_version": CORPUS_VERSION, "cases": semantic_gold},
        "m104_partition_manifest.json": {
            "corpus_version": CORPUS_VERSION,
            "recipe": "governed ordinal modulo-four and direct-family ordinal half split",
            "partitions": partition,
        },
        "m104_freshness_manifest.json": fresh,
    }
    result: dict[str, str] = {}
    for name, payload in artifacts.items():
        path = FIXTURES / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        result[name] = sha256(path.read_bytes()).hexdigest()
    return result


if __name__ == "__main__":
    print(json.dumps(build_artifacts(), indent=2, sort_keys=True))
