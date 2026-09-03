# ruff: noqa: E501

"""Frozen internal benchmark and verified-query corpus for M4.

The corpus is server-owned, manually authored reference SQL.  This module is
evaluation-only: retrieved SQL is prompt context and never execution authority.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import parse_one

from app.memory.models import (
    StructuralSignature,
    VerificationType,
    VerifiedQueryExample,
    build_verified_query_example,
    canonical_example_payload,
)
from app.semantics.catalog import build_m3_catalog

MEMORY_CORPUS_ID = "decisionsql-demo-verified-query-memory"
MEMORY_CORPUS_VERSION = 1
MEMORY_RETRIEVER_VERSION = "m4-retriever-v1"


class M4BenchmarkQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str = Field(min_length=1)
    split: str
    family: str
    question: str = Field(min_length=1)
    gold_sql: str = Field(min_length=1)
    schema_objects: tuple[str, ...] = Field(min_length=1)
    structural_signature: StructuralSignature
    useful_example_ids: tuple[str, ...] = Field(min_length=1)


_MEMORY_SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    "FILTERED_AGGREGATION": (
        ("Summarize order totals by status.", "SELECT o.status, SUM(o.total_amount) AS total_amount FROM orders AS o GROUP BY o.status ORDER BY o.status"),
        ("Show the average payment amount by payment status.", "SELECT p.status, AVG(p.amount) AS average_amount FROM payments AS p GROUP BY p.status ORDER BY p.status"),
        ("Give the average product price for each category.", "SELECT p.category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category ORDER BY p.category"),
        ("Count refunds by their reason.", "SELECT r.reason, COUNT(r.id) AS refund_count FROM refunds AS r GROUP BY r.reason ORDER BY r.reason"),
        ("Show subtotal totals by order currency.", "SELECT o.currency, SUM(o.subtotal) AS subtotal_total FROM orders AS o GROUP BY o.currency ORDER BY o.currency"),
    ),
    "TWO_TABLE_JOIN": (
        ("List customers with their order totals.", "SELECT c.name, SUM(o.total_amount) AS order_total FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Show units sold for every product.", "SELECT p.name, SUM(oi.quantity) AS units_sold FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY p.name"),
        ("List payment amounts by order status.", "SELECT o.status, SUM(p.amount) AS paid_amount FROM orders AS o JOIN payments AS p ON p.order_id = o.id GROUP BY o.status ORDER BY o.status"),
        ("Show refund totals by order status.", "SELECT o.status, SUM(r.amount) AS refund_total FROM orders AS o JOIN refunds AS r ON r.order_id = o.id GROUP BY o.status ORDER BY o.status"),
        ("List each customer and their region.", "SELECT c.name AS customer, r.name AS region FROM customers AS c JOIN regions AS r ON r.id = c.region_id ORDER BY c.name"),
    ),
    "MULTI_JOIN": (
        ("Show order value by region.", "SELECT r.name AS region, SUM(o.total_amount) AS order_total FROM regions AS r JOIN customers AS c ON c.region_id = r.id JOIN orders AS o ON o.customer_id = c.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show item quantity by product category.", "SELECT p.category, SUM(oi.quantity) AS units_sold FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id JOIN orders AS o ON o.id = oi.order_id GROUP BY p.category ORDER BY p.category"),
        ("Show payment totals by customer region.", "SELECT r.name AS region, SUM(pay.amount) AS payment_total FROM regions AS r JOIN customers AS c ON c.region_id = r.id JOIN orders AS o ON o.customer_id = c.id JOIN payments AS pay ON pay.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show refunds by sales representative.", "SELECT sr.name AS representative, SUM(r.amount) AS refund_total FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id JOIN refunds AS r ON r.order_id = o.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Count orders represented by each product category.", "SELECT p.category, COUNT(DISTINCT o.id) AS order_count FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id JOIN orders AS o ON o.id = oi.order_id GROUP BY p.category ORDER BY p.category"),
    ),
    "GROUPED_TOP_N": (
        ("Which product categories have the highest average prices?", "SELECT p.category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category ORDER BY average_price DESC LIMIT 3"),
        ("Show the five customers with the largest order totals.", "SELECT c.name, SUM(o.total_amount) AS order_total FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY order_total DESC LIMIT 5"),
        ("Find the sales representatives with the most orders.", "SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY order_count DESC LIMIT 4"),
        ("Show the largest refund reasons by amount.", "SELECT r.reason, SUM(r.amount) AS refund_total FROM refunds AS r GROUP BY r.reason ORDER BY refund_total DESC LIMIT 2"),
        ("List the products with the most units sold.", "SELECT p.name, SUM(oi.quantity) AS units_sold FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY units_sold DESC LIMIT 5"),
    ),
    "SUBQUERY": (
        ("List orders whose total exceeds the average order total.", "SELECT o.order_number, o.total_amount FROM orders AS o WHERE o.total_amount > (SELECT AVG(i.total_amount) FROM orders AS i) ORDER BY o.total_amount DESC"),
        ("Show products priced below the average product price.", "SELECT p.name, p.unit_price FROM products AS p WHERE p.unit_price < (SELECT AVG(i.unit_price) FROM products AS i) ORDER BY p.unit_price"),
        ("List payments above the average payment amount.", "SELECT p.id, p.amount FROM payments AS p WHERE p.amount > (SELECT AVG(i.amount) FROM payments AS i) ORDER BY p.amount DESC"),
        ("Find customers whose order count is above the average customer order count.", "SELECT c.name, COUNT(o.id) AS order_count FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING COUNT(o.id) > (SELECT CAST(COUNT(customer_id) AS numeric) / COUNT(DISTINCT customer_id) FROM orders) ORDER BY order_count DESC"),
        ("Show categories whose average price is above the overall average.", "SELECT p.category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category HAVING AVG(p.unit_price) > (SELECT AVG(i.unit_price) FROM products AS i) ORDER BY p.category"),
    ),
    "CASE_SEGMENTATION": (
        ("Classify orders as large or standard by total amount.", "SELECT o.order_number, CASE WHEN o.total_amount >= 300 THEN 'large' ELSE 'standard' END AS order_segment FROM orders AS o ORDER BY o.order_number"),
        ("Bucket payments by amount.", "SELECT p.id, CASE WHEN p.amount >= 250 THEN 'high' WHEN p.amount >= 100 THEN 'medium' ELSE 'low' END AS payment_band FROM payments AS p ORDER BY p.id"),
        ("Classify products by unit price.", "SELECT p.name, CASE WHEN p.unit_price >= 300 THEN 'premium' ELSE 'regular' END AS price_segment FROM products AS p ORDER BY p.name"),
        ("Label customers by their number of orders.", "SELECT c.name, CASE WHEN COUNT(o.id) >= 4 THEN 'frequent' ELSE 'occasional' END AS customer_segment FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Label orders based on their discount amount.", "SELECT o.order_number, CASE WHEN o.discount_amount > 0 THEN 'discounted' ELSE 'full_price' END AS pricing_segment FROM orders AS o ORDER BY o.order_number"),
    ),
    "HAVING": (
        ("Find customers with at least two orders.", "SELECT c.name, COUNT(o.id) AS order_count FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING COUNT(o.id) >= 2 ORDER BY c.name"),
        ("Find product categories with more than 100 units.", "SELECT p.category, SUM(oi.quantity) AS units_sold FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category HAVING SUM(oi.quantity) > 100 ORDER BY p.category"),
        ("Find representatives with at least ten orders.", "SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name HAVING COUNT(o.id) >= 10 ORDER BY sr.name"),
        ("Find payment statuses with more than twenty transactions.", "SELECT p.status, COUNT(p.id) AS payment_count FROM payments AS p GROUP BY p.status HAVING COUNT(p.id) > 20 ORDER BY p.status"),
        ("Find customers whose average order exceeds 250.", "SELECT c.name, AVG(o.total_amount) AS average_order FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING AVG(o.total_amount) > 250 ORDER BY c.name"),
    ),
    "ORDER_LIMIT": (
        ("Show the three largest orders.", "SELECT o.order_number, o.total_amount FROM orders AS o ORDER BY o.total_amount DESC LIMIT 3"),
        ("Show the five largest payments.", "SELECT p.id, p.amount FROM payments AS p ORDER BY p.amount DESC LIMIT 5"),
        ("List the first five products alphabetically.", "SELECT p.name, p.category FROM products AS p ORDER BY p.name LIMIT 5"),
        ("Show the largest refunds.", "SELECT r.id, r.amount FROM refunds AS r ORDER BY r.amount DESC LIMIT 4"),
        ("List the first ten customers by name.", "SELECT c.name, c.region_id FROM customers AS c ORDER BY c.name LIMIT 10"),
    ),
    "DISTINCT_ENTITY_COUNT": (
        ("Count distinct customers in each region.", "SELECT r.name, COUNT(DISTINCT c.id) AS customer_count FROM regions AS r LEFT JOIN customers AS c ON c.region_id = r.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Count distinct orders for every customer.", "SELECT c.name, COUNT(DISTINCT o.id) AS order_count FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Count distinct products in each category.", "SELECT p.category, COUNT(DISTINCT p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category"),
        ("Count distinct customers for every representative.", "SELECT sr.name, COUNT(DISTINCT c.id) AS customer_count FROM sales_representatives AS sr LEFT JOIN customers AS c ON c.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Count distinct orders represented in each payment status.", "SELECT p.status, COUNT(DISTINCT p.order_id) AS order_count FROM payments AS p GROUP BY p.status ORDER BY p.status"),
    ),
    "NON_GOVERNED_ARITHMETIC": (
        ("Show the discount percentage for each order.", "SELECT o.order_number, CASE WHEN o.subtotal = 0 THEN 0 ELSE CAST(o.discount_amount AS numeric) / o.subtotal * 100 END AS discount_percent FROM orders AS o ORDER BY o.order_number"),
        ("Show the difference between subtotal and total for each order.", "SELECT o.order_number, o.subtotal - o.total_amount AS price_difference FROM orders AS o ORDER BY o.order_number"),
        ("Calculate extended value for every order item.", "SELECT oi.id, oi.quantity * oi.unit_price AS extended_value FROM order_items AS oi ORDER BY oi.id"),
        ("Show the average discount per order currency.", "SELECT o.currency, AVG(o.discount_amount) AS average_discount FROM orders AS o GROUP BY o.currency ORDER BY o.currency"),
        ("Show refund amount as a fraction of the order subtotal.", "SELECT o.order_number, CASE WHEN o.subtotal = 0 THEN 0 ELSE CAST(r.amount AS numeric) / o.subtotal END AS refund_fraction FROM orders AS o JOIN refunds AS r ON r.order_id = o.id ORDER BY o.order_number"),
    ),
}


_TARGET_SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    "FILTERED_AGGREGATION": (
        ("What is the average order total for each order status?", "SELECT o.status, AVG(o.total_amount) AS average_total FROM orders AS o GROUP BY o.status ORDER BY o.status"),
        ("Summarize product prices by category using their maximum price.", "SELECT p.category, MAX(p.unit_price) AS maximum_price FROM products AS p GROUP BY p.category ORDER BY p.category"),
        ("How many payments are there in each status?", "SELECT p.status, COUNT(p.id) AS payment_count FROM payments AS p GROUP BY p.status ORDER BY p.status"),
        ("Show total refunded amount for every refund reason.", "SELECT r.reason, SUM(r.amount) AS refund_total FROM refunds AS r GROUP BY r.reason ORDER BY r.reason"),
        ("Give total discount for each order currency.", "SELECT o.currency, SUM(o.discount_amount) AS total_discount FROM orders AS o GROUP BY o.currency ORDER BY o.currency"),
        ("Summarize item quantities by product identifier.", "SELECT oi.product_id, SUM(oi.quantity) AS item_quantity FROM order_items AS oi GROUP BY oi.product_id ORDER BY oi.product_id"),
        ("Count representatives in each region.", "SELECT sr.region_id, COUNT(sr.id) AS representative_count FROM sales_representatives AS sr GROUP BY sr.region_id ORDER BY sr.region_id"),
        ("Show the minimum order subtotal by status.", "SELECT o.status, MIN(o.subtotal) AS minimum_subtotal FROM orders AS o GROUP BY o.status ORDER BY o.status"),
    ),
    "TWO_TABLE_JOIN": (
        ("Show average item quantity for every product category.", "SELECT p.category, AVG(oi.quantity) AS average_quantity FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category"),
        ("Count orders for each customer region.", "SELECT c.region_id, COUNT(o.id) AS order_count FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.region_id ORDER BY c.region_id"),
        ("Show average payment amount by order currency.", "SELECT o.currency, AVG(p.amount) AS average_payment FROM orders AS o JOIN payments AS p ON p.order_id = o.id GROUP BY o.currency ORDER BY o.currency"),
        ("Count refunds for each order status.", "SELECT o.status, COUNT(r.id) AS refund_count FROM orders AS o JOIN refunds AS r ON r.order_id = o.id GROUP BY o.status ORDER BY o.status"),
        ("Show order totals for each sales representative.", "SELECT sr.name, SUM(o.total_amount) AS order_total FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Show item discounts by product category.", "SELECT p.category, SUM(oi.discount_amount) AS discount_total FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category"),
        ("List each customer with their largest order.", "SELECT c.name, MAX(o.total_amount) AS largest_order FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Count representatives in every region.", "SELECT r.name, COUNT(sr.id) AS representative_count FROM regions AS r JOIN sales_representatives AS sr ON sr.region_id = r.id GROUP BY r.id, r.name ORDER BY r.name"),
    ),
    "MULTI_JOIN": (
        ("Count orders for every region through its customers.", "SELECT r.name, COUNT(o.id) AS order_count FROM regions AS r JOIN customers AS c ON c.region_id = r.id JOIN orders AS o ON o.customer_id = c.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show order subtotal by sales representative region.", "SELECT r.name, SUM(o.subtotal) AS subtotal_total FROM regions AS r JOIN sales_representatives AS sr ON sr.region_id = r.id JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show average order total for each product category.", "SELECT p.category, AVG(o.total_amount) AS average_order FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id JOIN orders AS o ON o.id = oi.order_id GROUP BY p.category ORDER BY p.category"),
        ("Count distinct orders containing each product category, highest first.", "SELECT p.category, COUNT(DISTINCT o.id) AS order_count FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id JOIN orders AS o ON o.id = oi.order_id GROUP BY p.category ORDER BY order_count DESC"),
        ("Count payments for every customer region.", "SELECT r.name, COUNT(pay.id) AS payment_count FROM regions AS r JOIN customers AS c ON c.region_id = r.id JOIN orders AS o ON o.customer_id = c.id JOIN payments AS pay ON pay.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show refund totals by customer region.", "SELECT r.name, SUM(ref.amount) AS refund_total FROM regions AS r JOIN customers AS c ON c.region_id = r.id JOIN orders AS o ON o.customer_id = c.id JOIN refunds AS ref ON ref.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show average payment amount for every sales representative.", "SELECT sr.name, AVG(pay.amount) AS average_payment FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id JOIN payments AS pay ON pay.order_id = o.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Show payment totals for each region.", "SELECT r.name, SUM(pay.amount) AS payment_total FROM regions AS r JOIN sales_representatives AS sr ON sr.region_id = r.id JOIN orders AS o ON o.sales_rep_id = sr.id JOIN payments AS pay ON pay.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
    ),
    "GROUPED_TOP_N": (
        ("Which customers have the greatest total subtotal?", "SELECT c.name, SUM(o.subtotal) AS subtotal_total FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY subtotal_total DESC LIMIT 4"),
        ("Show the top three representatives by order value.", "SELECT sr.name, SUM(o.total_amount) AS order_total FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY order_total DESC LIMIT 3"),
        ("List the categories with the most products.", "SELECT p.category, COUNT(p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY product_count DESC LIMIT 2"),
        ("Show the customers with the highest average order.", "SELECT c.name, AVG(o.total_amount) AS average_order FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY average_order DESC LIMIT 5"),
        ("Find the representatives with the lowest average order value.", "SELECT sr.name, AVG(o.total_amount) AS average_order FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY average_order LIMIT 3"),
        ("Show the top product categories by item quantity.", "SELECT p.category, SUM(oi.quantity) AS units_sold FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY units_sold DESC LIMIT 3"),
        ("List the highest-payment statuses by total amount.", "SELECT p.status, SUM(p.amount) AS payment_total FROM payments AS p GROUP BY p.status ORDER BY payment_total DESC LIMIT 2"),
        ("Show regions with the most customers.", "SELECT r.name, COUNT(c.id) AS customer_count FROM regions AS r JOIN customers AS c ON c.region_id = r.id GROUP BY r.id, r.name ORDER BY customer_count DESC LIMIT 3"),
    ),
    "SUBQUERY": (
        ("Which orders have a subtotal above the average subtotal?", "SELECT o.order_number, o.subtotal FROM orders AS o WHERE o.subtotal > (SELECT AVG(i.subtotal) FROM orders AS i) ORDER BY o.subtotal DESC"),
        ("List products priced above the average price.", "SELECT p.name, p.unit_price FROM products AS p WHERE p.unit_price > (SELECT AVG(i.unit_price) FROM products AS i) ORDER BY p.unit_price DESC"),
        ("Show refunds larger than the average refund.", "SELECT r.id, r.amount FROM refunds AS r WHERE r.amount > (SELECT AVG(i.amount) FROM refunds AS i) ORDER BY r.amount DESC"),
        ("Find customers with fewer orders than the average customer.", "SELECT c.name, COUNT(o.id) AS order_count FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING COUNT(o.id) < (SELECT CAST(COUNT(customer_id) AS numeric) / COUNT(DISTINCT customer_id) FROM orders) ORDER BY order_count"),
        ("Show categories whose average price is below the overall average.", "SELECT p.category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category HAVING AVG(p.unit_price) < (SELECT AVG(i.unit_price) FROM products AS i) ORDER BY p.category"),
        ("List payments below the average amount.", "SELECT p.id, p.amount FROM payments AS p WHERE p.amount < (SELECT AVG(i.amount) FROM payments AS i) ORDER BY p.amount"),
        ("Show orders with a discount above the average discount.", "SELECT o.order_number, o.discount_amount FROM orders AS o WHERE o.discount_amount > (SELECT AVG(i.discount_amount) FROM orders AS i) ORDER BY o.discount_amount DESC"),
        ("Find products whose price is below the maximum price.", "SELECT p.name, p.unit_price FROM products AS p WHERE p.unit_price < (SELECT MAX(i.unit_price) FROM products AS i) ORDER BY p.unit_price"),
    ),
    "CASE_SEGMENTATION": (
        ("Classify orders as meaningfully discounted or full price.", "SELECT o.order_number, CASE WHEN o.discount_amount >= 10 THEN 'discounted' ELSE 'full_price' END AS pricing_segment FROM orders AS o ORDER BY o.order_number"),
        ("Bucket products into low, medium, and premium prices.", "SELECT p.name, CASE WHEN p.unit_price >= 300 THEN 'premium' WHEN p.unit_price >= 150 THEN 'medium' ELSE 'low' END AS price_band FROM products AS p ORDER BY p.name"),
        ("Classify refunds as small or large.", "SELECT r.id, CASE WHEN r.amount >= 100 THEN 'large' ELSE 'small' END AS refund_band FROM refunds AS r ORDER BY r.id"),
        ("Segment representatives by their order count.", "SELECT sr.name, CASE WHEN COUNT(o.id) >= 15 THEN 'high_volume' ELSE 'regular' END AS representative_segment FROM sales_representatives AS sr LEFT JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Label payments as settled or other.", "SELECT p.id, CASE WHEN p.status = 'settled' THEN 'settled_payment' ELSE 'other_payment' END AS payment_segment FROM payments AS p ORDER BY p.id"),
        ("Classify customers by whether they have a large order.", "SELECT c.name, CASE WHEN MAX(o.total_amount) >= 400 THEN 'large_order_customer' ELSE 'other_customer' END AS customer_segment FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Bucket order subtotals into small and large.", "SELECT o.order_number, CASE WHEN o.subtotal >= 250 THEN 'large' ELSE 'small' END AS subtotal_band FROM orders AS o ORDER BY o.order_number"),
        ("Classify products by category family.", "SELECT p.name, CASE WHEN p.category IN ('Electronics', 'Office') THEN 'indoor' ELSE 'outdoor_home' END AS category_family FROM products AS p ORDER BY p.name"),
    ),
    "HAVING": (
        ("Show product categories with at least 40 products units.", "SELECT p.category, SUM(oi.quantity) AS units_sold FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category HAVING SUM(oi.quantity) >= 40 ORDER BY p.category"),
        ("Find regions with more than eight customers.", "SELECT r.name, COUNT(c.id) AS customer_count FROM regions AS r JOIN customers AS c ON c.region_id = r.id GROUP BY r.id, r.name HAVING COUNT(c.id) > 8 ORDER BY r.name"),
        ("Find representatives with an average order above 250.", "SELECT sr.name, AVG(o.total_amount) AS average_order FROM sales_representatives AS sr JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name HAVING AVG(o.total_amount) > 250 ORDER BY sr.name"),
        ("Find order statuses with at least twenty orders.", "SELECT o.status, COUNT(o.id) AS order_count FROM orders AS o GROUP BY o.status HAVING COUNT(o.id) >= 20 ORDER BY o.status"),
        ("Find customers with more than three orders.", "SELECT c.name, COUNT(o.id) AS order_count FROM customers AS c JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING COUNT(o.id) > 3 ORDER BY c.name"),
        ("Find categories with an average price above 200.", "SELECT p.category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category HAVING AVG(p.unit_price) > 200 ORDER BY p.category"),
        ("Find sales representatives with at least five customers.", "SELECT sr.name, COUNT(DISTINCT c.id) AS customer_count FROM sales_representatives AS sr JOIN customers AS c ON c.sales_rep_id = sr.id GROUP BY sr.id, sr.name HAVING COUNT(DISTINCT c.id) >= 5 ORDER BY sr.name"),
        ("Find currencies with more than fifty orders.", "SELECT o.currency, COUNT(o.id) AS order_count FROM orders AS o GROUP BY o.currency HAVING COUNT(o.id) > 50 ORDER BY o.currency"),
    ),
    "ORDER_LIMIT": (
        ("Show the four orders with the smallest totals.", "SELECT o.order_number, o.total_amount FROM orders AS o ORDER BY o.total_amount LIMIT 4"),
        ("List the largest product prices.", "SELECT p.name, p.unit_price FROM products AS p ORDER BY p.unit_price DESC LIMIT 6"),
        ("Show the first six payments by identifier.", "SELECT p.id, p.amount FROM payments AS p ORDER BY p.id LIMIT 6"),
        ("List the four customers with the highest customer identifiers.", "SELECT c.name, c.id FROM customers AS c ORDER BY c.id DESC LIMIT 4"),
        ("Show the two largest refund amounts.", "SELECT r.id, r.amount FROM refunds AS r ORDER BY r.amount DESC LIMIT 2"),
        ("List products alphabetically in the first eight positions.", "SELECT p.name, p.sku FROM products AS p ORDER BY p.name LIMIT 8"),
        ("Show the five orders with the greatest discounts.", "SELECT o.order_number, o.discount_amount FROM orders AS o ORDER BY o.discount_amount DESC LIMIT 5"),
        ("List representatives alphabetically, limited to four.", "SELECT sr.name, sr.region_id FROM sales_representatives AS sr ORDER BY sr.name LIMIT 4"),
    ),
    "DISTINCT_ENTITY_COUNT": (
        ("Count distinct customers for each representative region.", "SELECT sr.region_id, COUNT(DISTINCT c.id) AS customer_count FROM sales_representatives AS sr LEFT JOIN customers AS c ON c.sales_rep_id = sr.id GROUP BY sr.region_id ORDER BY sr.region_id"),
        ("Count distinct products ordered by each customer.", "SELECT c.name, COUNT(DISTINCT oi.product_id) AS product_count FROM customers AS c LEFT JOIN orders AS o ON o.customer_id = c.id LEFT JOIN order_items AS oi ON oi.order_id = o.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Count distinct orders represented in each region.", "SELECT r.name, COUNT(DISTINCT o.id) AS order_count FROM regions AS r LEFT JOIN sales_representatives AS sr ON sr.region_id = r.id LEFT JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Count distinct payment orders by status, largest group first.", "SELECT p.status, COUNT(DISTINCT p.order_id) AS order_count FROM payments AS p GROUP BY p.status ORDER BY order_count DESC"),
        ("Count distinct customers who bought each product category.", "SELECT p.category, COUNT(DISTINCT o.customer_id) AS customer_count FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id JOIN orders AS o ON o.id = oi.order_id GROUP BY p.category ORDER BY p.category"),
        ("Count distinct products in every order.", "SELECT o.order_number, COUNT(DISTINCT oi.product_id) AS product_count FROM orders AS o LEFT JOIN order_items AS oi ON oi.order_id = o.id GROUP BY o.id, o.order_number ORDER BY o.order_number"),
        ("Count distinct refund orders by reason.", "SELECT r.reason, COUNT(DISTINCT r.order_id) AS order_count FROM refunds AS r GROUP BY r.reason ORDER BY r.reason"),
        ("Count distinct orders for each sales representative.", "SELECT sr.name, COUNT(DISTINCT o.id) AS order_count FROM sales_representatives AS sr LEFT JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
    ),
    "NON_GOVERNED_ARITHMETIC": (
        ("Show net amount after discount for every order.", "SELECT o.order_number, o.subtotal - o.discount_amount AS net_before_tax FROM orders AS o ORDER BY o.order_number"),
        ("Calculate the line value after line discount.", "SELECT oi.id, oi.quantity * oi.unit_price - oi.discount_amount AS net_line_value FROM order_items AS oi ORDER BY oi.id"),
        ("Show each order's total as a fraction of the overall total.", "SELECT o.order_number, CASE WHEN (SELECT SUM(i.total_amount) FROM orders AS i) = 0 THEN 0 ELSE CAST(o.total_amount AS numeric) / (SELECT SUM(i.total_amount) FROM orders AS i) END AS total_fraction FROM orders AS o ORDER BY o.order_number"),
        ("Show average order discount as a percentage of subtotal by status.", "SELECT o.status, CASE WHEN SUM(o.subtotal) = 0 THEN 0 ELSE CAST(SUM(o.discount_amount) AS numeric) / SUM(o.subtotal) * 100 END AS discount_percent FROM orders AS o GROUP BY o.status ORDER BY o.status"),
        ("Calculate payment amount remaining after a fixed two percent fee.", "SELECT p.id, p.amount - p.amount * 0.02 AS net_payment FROM payments AS p ORDER BY p.id"),
        ("Show the gap between product price and line price.", "SELECT p.name, p.unit_price - oi.unit_price AS price_gap FROM products AS p JOIN order_items AS oi ON oi.product_id = p.id ORDER BY p.name"),
        ("Calculate each refund amount as a percentage of its order total.", "SELECT r.id, CASE WHEN o.total_amount = 0 THEN 0 ELSE CAST(r.amount AS numeric) / o.total_amount * 100 END AS refund_percent FROM refunds AS r JOIN orders AS o ON o.id = r.order_id ORDER BY r.id"),
        ("Show the average order value per order item by currency.", "SELECT o.currency, CASE WHEN COUNT(oi.id) = 0 THEN 0 ELSE CAST(SUM(o.total_amount) AS numeric) / COUNT(oi.id) END AS value_per_item FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.id GROUP BY o.currency ORDER BY o.currency"),
    ),
}


def build_memory_corpus() -> tuple[VerifiedQueryExample, ...]:
    examples: list[VerifiedQueryExample] = []
    for family, sources in _MEMORY_SOURCES.items():
        for index, (question, sql) in enumerate(sources, start=1):
            examples.append(
                build_verified_query_example(
                    example_id=f"vq:{family.lower()}_{index}:v1",
                    question=question,
                    sql=sql,
                    tags=(family,),
                    verification_type=VerificationType.MANUAL_REFERENCE,
                )
            )
    return tuple(examples)


def build_benchmark() -> tuple[M4BenchmarkQuestion, ...]:
    memory = build_memory_corpus()
    by_family: dict[str, tuple[str, ...]] = {}
    for family in _MEMORY_SOURCES:
        by_family[family] = tuple(example.example_id for example in memory if family in example.tags)
    questions: list[M4BenchmarkQuestion] = []
    index = 0
    split_indices = {"dev": 0, "holdout": 0}
    for family, sources in _TARGET_SOURCES.items():
        dev_count = 4 if family in {"ORDER_LIMIT", "DISTINCT_ENTITY_COUNT"} else 5
        for family_index, (question, sql) in enumerate(sources):
            split = "dev" if family_index < dev_count else "holdout"
            question_id = f"m4-{split}-{split_indices[split]:03d}"
            split_indices[split] += 1
            metadata = build_verified_query_example(
                example_id=f"vq:target_{question_id.replace('-', '_')}:v1",
                question=question,
                sql=sql,
                tags=(family,),
            )
            questions.append(
                M4BenchmarkQuestion(
                    question_id=question_id,
                    split=split,
                    family=family,
                    question=question,
                    gold_sql=sql,
                    schema_objects=metadata.schema_objects,
                    structural_signature=metadata.structural_signature,
                    useful_example_ids=by_family[family],
                )
            )
            index += 1
    return tuple(questions)


def corpus_hash(examples: tuple[VerifiedQueryExample, ...] | None = None) -> str:
    examples = examples or build_memory_corpus()
    return _hash_payload([canonical_example_payload(example) for example in examples])


def benchmark_hash(questions: tuple[M4BenchmarkQuestion, ...] | None = None) -> str:
    questions = questions or build_benchmark()
    return _hash_payload([question.model_dump(mode="json") for question in questions])


def validate_benchmark(
    examples: tuple[VerifiedQueryExample, ...] | None = None,
    questions: tuple[M4BenchmarkQuestion, ...] | None = None,
) -> dict[str, Any]:
    examples = examples or build_memory_corpus()
    questions = questions or build_benchmark()
    memory_ids = {example.example_id for example in examples}
    memory_questions = {example.question for example in examples}
    memory_sql = {example.sql.strip() for example in examples}
    normalized_memory_sql = {_normalized_sql(example.sql) for example in examples}
    question_ids = {question.question_id for question in questions}
    duplicate_ids = len(memory_ids) != len(examples) or len(question_ids) != len(questions)
    leakage: list[dict[str, str]] = []
    temporal_words = ("rolling", "year-over-year", "previous month", "timezone", "last month")
    metric_names = {metric.name for metric in build_m3_catalog().metrics}
    for question in questions:
        if question.question in memory_questions:
            leakage.append({"question_id": question.question_id, "type": "EXACT_QUESTION"})
        if question.gold_sql.strip() in memory_sql:
            leakage.append({"question_id": question.question_id, "type": "EXACT_SQL"})
        if _normalized_sql(question.gold_sql) in normalized_memory_sql:
            leakage.append({"question_id": question.question_id, "type": "NORMALIZED_SQL"})
        if not set(question.useful_example_ids) <= memory_ids:
            leakage.append({"question_id": question.question_id, "type": "UNKNOWN_PRECEDENT"})
        if any(word in question.question.lower() for word in temporal_words):
            leakage.append({"question_id": question.question_id, "type": "TEMPORAL_SCOPE"})
        if any(metric_name in question.question.lower() for metric_name in metric_names):
            leakage.append({"question_id": question.question_id, "type": "GOVERNED_METRIC_TERM"})
    family_counts = {family: sum(question.family == family for question in questions) for family in _TARGET_SOURCES}
    return {
        "memory_count": len(examples),
        "dev_count": sum(question.split == "dev" for question in questions),
        "holdout_count": sum(question.split == "holdout" for question in questions),
        "unique_memory_ids": len(memory_ids),
        "unique_question_ids": len(question_ids),
        "duplicate_ids": duplicate_ids,
        "family_counts": family_counts,
        "exact_question_overlap": sum(item["type"] == "EXACT_QUESTION" for item in leakage),
        "exact_sql_overlap": sum(item["type"] == "EXACT_SQL" for item in leakage),
        "normalized_sql_overlap": sum(item["type"] == "NORMALIZED_SQL" for item in leakage),
        "leakage": leakage,
        "passed": not duplicate_ids and not leakage,
    }


def _normalized_sql(sql: str) -> str:
    return parse_one(sql, read="postgres").sql(dialect="postgres", pretty=False).strip().lower()


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode()).hexdigest()
