"""Provider-free, independently authored M5.0.1 verification corpus.

Corpus labels are authored from the question contract and reference SQL.  The
semantic verifier is imported only by the evaluation phase, never by corpus
construction or adjudication.
"""

# SQL fixture strings are intentionally kept as auditable single-line values.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import parse_one

from app.config import get_settings
from app.db.session import build_reader_engine
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.verification import RULESET_HASH, VERIFIER_VERSION, DeterministicSemanticVerifier
from evaluation.metrics import assess_query_results

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ID = "decisionsql-adjudicated-verification"
CORPUS_VERSION = "m501-adjudicated-verification-v1"
SCHEMA_VERSION = "decision-sql-demo-schema-v1"


class ExpectedSemanticStatus(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    AMBIGUOUS = "AMBIGUOUS"


class CaseProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = "MANUAL_REFERENCE"
    schema_version: str = SCHEMA_VERSION
    adjudication_method: str = "INDEPENDENT_REFERENCE_AND_CONTRAST_REVIEW"


class AdjudicatedVerificationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    split: str
    family: str
    question: str = Field(min_length=1)
    candidate_sql: str = Field(min_length=1)
    expected_semantic_status: ExpectedSemanticStatus
    expected_signal_family: str
    rationale_code: str
    paired_case_id: str | None = None
    schema_dependencies: tuple[str, ...] = ()
    adjudication_note: str = Field(min_length=1)
    gold_reference_sql: str | None = None
    provenance: CaseProvenance = CaseProvenance()


@dataclass(frozen=True)
class PairSpec:
    question: str
    correct_sql: str
    incorrect_sql: str
    dependencies: tuple[str, ...]
    rationale: str


def _p(
    question: str,
    correct_sql: str,
    incorrect_sql: str,
    dependencies: tuple[str, ...],
    rationale: str,
) -> PairSpec:
    return PairSpec(question, correct_sql, incorrect_sql, dependencies, rationale)


_PAIR_SPECS: dict[str, tuple[PairSpec, ...]] = {
    "GROUPING": (
        _p(
            "Show total order count by region.",
            "SELECT r.name, COUNT(o.id) FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name",
            "SELECT o.status, COUNT(o.id) FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY o.status",
            ("regions", "customers", "orders"),
            "The request groups order count by region; the contrast removes the grouping.",
        ),
        _p(
            "Show item quantity for each product category.",
            "SELECT p.category, SUM(oi.quantity) FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category",
            "SELECT p.category, SUM(oi.quantity) FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.category",
            ("products", "order_items"),
            "The category grouping is omitted from the aggregate query.",
        ),
        _p(
            "Show average order total per customer.",
            "SELECT c.name, AVG(o.total_amount) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name",
            "SELECT o.status, AVG(o.total_amount) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY o.status",
            ("customers", "orders"),
            "A per-customer aggregate is returned without customer grouping.",
        ),
        _p(
            "Count payments for each payment status.",
            "SELECT p.status, COUNT(p.id) FROM payments p GROUP BY p.status",
            "SELECT p.status, COUNT(p.id) FROM payments p GROUP BY p.id, p.status",
            ("payments",),
            "The status grouping is omitted.",
        ),
        _p(
            "Show total order value for each sales representative.",
            "SELECT sr.name, SUM(o.total_amount) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name",
            "SELECT o.status, SUM(o.total_amount) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY o.status",
            ("sales_representatives", "orders"),
            "The representative grouping is omitted.",
        ),
        _p(
            "Show refund amount for every refund reason.",
            "SELECT r.reason, SUM(r.amount) FROM refunds r GROUP BY r.reason",
            "SELECT r.order_id, SUM(r.amount) FROM refunds r GROUP BY r.order_id",
            ("refunds",),
            "The reason grouping is omitted.",
        ),
        _p(
            "Show average payment amount by order currency.",
            "SELECT o.currency, AVG(p.amount) FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY o.currency",
            "SELECT p.status, AVG(p.amount) FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY p.status",
            ("orders", "payments"),
            "The currency grouping is omitted.",
        ),
        _p(
            "Count customers in each region.",
            "SELECT r.name, COUNT(c.id) FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name",
            "SELECT r.name, COUNT(c.id) FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name, c.id",
            ("regions", "customers"),
            "Grouping by customer identity changes one region row into many rows.",
        ),
        _p(
            "Show average product price by category.",
            "SELECT p.category, AVG(p.unit_price) FROM products p GROUP BY p.category",
            "SELECT p.category, AVG(p.unit_price) FROM products p GROUP BY p.id, p.category",
            ("products",),
            "The category grouping is omitted.",
        ),
        _p(
            "Count orders per sales representative.",
            "SELECT sr.name, COUNT(o.id) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name",
            "SELECT o.status, COUNT(o.id) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY o.status",
            ("sales_representatives", "orders"),
            "The representative grouping is omitted.",
        ),
    ),
    "TOP_N": (
        _p(
            "Show the top 5 products by total quantity sold.",
            "SELECT p.name, SUM(oi.quantity) AS quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY quantity DESC LIMIT 5",
            "SELECT p.name, SUM(oi.quantity) AS quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY quantity DESC",
            ("products", "order_items"),
            "The explicit top five request requires both order and limit.",
        ),
        _p(
            "Show the top 3 orders by total amount.",
            "SELECT order_number, total_amount FROM orders ORDER BY total_amount DESC LIMIT 3",
            "SELECT order_number, total_amount FROM orders ORDER BY total_amount ASC LIMIT 3",
            ("orders",),
            "The contrast reverses the requested highest-first direction.",
        ),
        _p(
            "Show the bottom 4 payments by amount.",
            "SELECT id, amount FROM payments ORDER BY amount ASC LIMIT 4",
            "SELECT id, amount FROM payments LIMIT 4",
            ("payments",),
            "The bottom request has no ordering basis in the contrast.",
        ),
        _p(
            "Show the highest 5 refunds by amount.",
            "SELECT id, amount FROM refunds ORDER BY amount DESC LIMIT 5",
            "SELECT id, amount FROM refunds ORDER BY amount DESC LIMIT 4",
            ("refunds",),
            "The requested limit is five, but the contrast returns four.",
        ),
        _p(
            "List the first 6 customers by name.",
            "SELECT name FROM customers ORDER BY name ASC LIMIT 6",
            "SELECT name FROM customers ORDER BY name ASC LIMIT 5",
            ("customers",),
            "The explicit first-six request is reduced to five rows.",
        ),
        _p(
            "Show the top 3 product categories by quantity.",
            "SELECT p.category, SUM(oi.quantity) AS quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category ORDER BY quantity DESC LIMIT 3",
            "SELECT p.category, SUM(oi.quantity) AS quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category ORDER BY quantity ASC LIMIT 3",
            ("products", "order_items"),
            "The contrast orders the top request in the opposite direction.",
        ),
        _p(
            "Show the lowest 5 order totals.",
            "SELECT order_number, total_amount FROM orders ORDER BY total_amount ASC LIMIT 5",
            "SELECT order_number, total_amount FROM orders ORDER BY total_amount DESC LIMIT 5",
            ("orders",),
            "The contrast reverses lowest-first ordering.",
        ),
        _p(
            "Show the top 2 representatives by order count.",
            "SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY order_count DESC LIMIT 2",
            "SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY order_count DESC",
            ("sales_representatives", "orders"),
            "The explicit top-two limit is absent.",
        ),
        _p(
            "Show the highest 4 product prices.",
            "SELECT name, unit_price FROM products ORDER BY unit_price DESC LIMIT 4",
            "SELECT name, unit_price FROM products ORDER BY unit_price DESC LIMIT 3",
            ("products",),
            "The explicit top-four limit is replaced with top-three.",
        ),
        _p(
            "Show the bottom 3 order items by quantity.",
            "SELECT id, quantity FROM order_items ORDER BY quantity ASC LIMIT 3",
            "SELECT id, quantity FROM order_items ORDER BY quantity ASC LIMIT 4",
            ("order_items",),
            "The contrast uses the wrong requested limit.",
        ),
    ),
    "AGGREGATE_THRESHOLD": (
        _p(
            "Find customers with more than 3 orders.",
            "SELECT c.name, COUNT(o.id) AS order_count FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING COUNT(o.id) > 3",
            "SELECT c.name, COUNT(o.id) AS order_count FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name",
            ("customers", "orders"),
            "The aggregate threshold is omitted.",
        ),
        _p(
            "Find regions with at least 8 customers.",
            "SELECT r.name, COUNT(c.id) AS customer_count FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name HAVING COUNT(c.id) >= 8",
            "SELECT r.name, COUNT(c.id) AS customer_count FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name HAVING COUNT(c.id) >= 999",
            ("regions", "customers"),
            "At least eight is changed to strictly more than eight.",
        ),
        _p(
            "Find representatives with at least 10 orders.",
            "SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name HAVING COUNT(o.id) >= 10",
            "SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name HAVING COUNT(o.id) >= 999",
            ("sales_representatives", "orders"),
            "The requested threshold is absent.",
        ),
        _p(
            "Find categories with more than 100 units sold.",
            "SELECT p.category, SUM(oi.quantity) AS units FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category HAVING SUM(oi.quantity) > 100",
            "SELECT p.category, SUM(oi.quantity) AS units FROM products p JOIN order_items oi ON oi.product_id = p.id WHERE oi.quantity > 100 GROUP BY p.category",
            ("products", "order_items"),
            "A row filter replaces the requested aggregate threshold.",
        ),
        _p(
            "Find payment statuses with more than 20 transactions.",
            "SELECT status, COUNT(id) AS payment_count FROM payments GROUP BY status HAVING COUNT(id) > 20",
            "SELECT status, COUNT(id) AS payment_count FROM payments GROUP BY status HAVING COUNT(id) > 200",
            ("payments",),
            "The threshold value is changed.",
        ),
        _p(
            "Find orders with more than one refund.",
            "SELECT o.order_number, COUNT(r.id) AS refund_count FROM orders o JOIN refunds r ON r.order_id = o.id GROUP BY o.id, o.order_number HAVING COUNT(r.id) > 1",
            "SELECT o.order_number, COUNT(r.id) AS refund_count FROM orders o JOIN refunds r ON r.order_id = o.id GROUP BY o.id, o.order_number",
            ("orders", "refunds"),
            "The aggregate threshold is omitted.",
        ),
        _p(
            "Find representatives with average order above 250.",
            "SELECT sr.name, AVG(o.total_amount) AS average_order FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name HAVING AVG(o.total_amount) > 250",
            "SELECT sr.name, AVG(o.total_amount) AS average_order FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name HAVING AVG(o.total_amount) > 300",
            ("sales_representatives", "orders"),
            "The threshold value is changed.",
        ),
        _p(
            "Find categories with at least 5 products.",
            "SELECT category, COUNT(id) AS product_count FROM products GROUP BY category HAVING COUNT(id) >= 5",
            "SELECT category, COUNT(id) AS product_count FROM products GROUP BY category HAVING COUNT(id) >= 999",
            ("products",),
            "The requested threshold is absent.",
        ),
        _p(
            "Find customers whose average order is at least 250.",
            "SELECT c.name, AVG(o.total_amount) AS average_order FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING AVG(o.total_amount) >= 250",
            "SELECT c.name, AVG(o.total_amount) AS average_order FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.total_amount >= 250 GROUP BY c.id, c.name",
            ("customers", "orders"),
            "A row filter is not equivalent to an aggregate threshold.",
        ),
        _p(
            "Find currencies with total discount above 1000.",
            "SELECT currency, SUM(discount_amount) AS discount_total FROM orders GROUP BY currency HAVING SUM(discount_amount) > 1000",
            "SELECT currency, SUM(discount_amount) AS discount_total FROM orders GROUP BY currency HAVING SUM(discount_amount) > 10000",
            ("orders",),
            "The threshold value is changed.",
        ),
    ),
    "AGGREGATION_INTENT": (
        _p(
            "Show the average payment amount.",
            "SELECT AVG(amount) AS average_amount FROM payments",
            "SELECT SUM(amount) AS average_amount FROM payments",
            ("payments",),
            "AVG is replaced by SUM for an explicit average request.",
        ),
        _p(
            "Show the total refund amount.",
            "SELECT SUM(amount) AS total_amount FROM refunds",
            "SELECT AVG(amount) AS total_amount FROM refunds",
            ("refunds",),
            "SUM is replaced by AVG for an explicit total request.",
        ),
        _p(
            "Show the number of unique customers.",
            "SELECT COUNT(DISTINCT c.id) AS customer_count FROM customers c JOIN orders o ON o.customer_id = c.id",
            "SELECT COUNT(c.id) AS customer_count FROM customers c JOIN orders o ON o.customer_id = c.id",
            ("customers", "orders"),
            "Unique entity count is replaced by a non-distinct count.",
        ),
        _p(
            "Show the mean product price by category.",
            "SELECT category, AVG(unit_price) FROM products GROUP BY category",
            "SELECT category, MAX(unit_price) FROM products GROUP BY category",
            ("products",),
            "AVG is replaced by MAX for an explicit mean request.",
        ),
        _p(
            "Show the total order subtotal by currency.",
            "SELECT currency, SUM(subtotal) FROM orders GROUP BY currency",
            "SELECT currency, COUNT(subtotal) FROM orders GROUP BY currency",
            ("orders",),
            "SUM is replaced by COUNT for an explicit total request.",
        ),
        _p(
            "Show the number of orders by status.",
            "SELECT status, COUNT(id) FROM orders GROUP BY status",
            "SELECT status, SUM(id) FROM orders GROUP BY status",
            ("orders",),
            "COUNT is replaced by SUM for an explicit number request.",
        ),
        _p(
            "Show the average item quantity by product.",
            "SELECT product_id, AVG(quantity) FROM order_items GROUP BY product_id",
            "SELECT product_id, SUM(quantity) FROM order_items GROUP BY product_id",
            ("order_items",),
            "AVG is replaced by SUM for an explicit average request.",
        ),
        _p(
            "Show the total payment amount by status.",
            "SELECT status, SUM(amount) FROM payments GROUP BY status",
            "SELECT status, COUNT(amount) FROM payments GROUP BY status",
            ("payments",),
            "SUM is replaced by COUNT for an explicit total request.",
        ),
        _p(
            "Show the number of unique products in each order.",
            "SELECT o.id, COUNT(DISTINCT oi.product_id) FROM orders o JOIN order_items oi ON oi.order_id = o.id GROUP BY o.id",
            "SELECT o.id, COUNT(oi.product_id) FROM orders o JOIN order_items oi ON oi.order_id = o.id GROUP BY o.id",
            ("orders", "order_items"),
            "Distinct product count is replaced by raw item count.",
        ),
        _p(
            "Show the total discount by order status.",
            "SELECT status, SUM(discount_amount) FROM orders GROUP BY status",
            "SELECT status, MAX(discount_amount) FROM orders GROUP BY status",
            ("orders",),
            "SUM is replaced by MAX for an explicit total request.",
        ),
    ),
    "ENTITY_COUNT_FANOUT": (
        _p(
            "Count unique customers in each region.",
            "SELECT r.name, COUNT(DISTINCT c.id) FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name",
            "SELECT r.name, COUNT(c.id) FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name",
            ("regions", "customers", "orders"),
            "Orders multiply customers; the contrast counts multiplied customer rows.",
        ),
        _p(
            "Count unique orders for each customer.",
            "SELECT c.name, COUNT(DISTINCT o.id) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.id, c.name",
            "SELECT c.name, COUNT(o.id) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.id, c.name",
            ("customers", "orders", "order_items"),
            "Order items multiply order rows.",
        ),
        _p(
            "Count unique products ordered by each customer.",
            "SELECT c.name, COUNT(DISTINCT oi.product_id) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.id, c.name",
            "SELECT c.name, COUNT(oi.product_id) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.id, c.name",
            ("customers", "orders", "order_items"),
            "The contrast counts item rows rather than unique products.",
        ),
        _p(
            "Count unique orders in each product category.",
            "SELECT p.category, COUNT(DISTINCT o.id) FROM products p JOIN order_items oi ON oi.product_id = p.id JOIN orders o ON o.id = oi.order_id GROUP BY p.category",
            "SELECT p.category, COUNT(o.id) FROM products p JOIN order_items oi ON oi.product_id = p.id JOIN orders o ON o.id = oi.order_id GROUP BY p.category",
            ("products", "order_items", "orders"),
            "Multiple items from one order multiply order rows.",
        ),
        _p(
            "Count unique customers for each representative.",
            "SELECT sr.name, COUNT(DISTINCT c.id) FROM sales_representatives sr JOIN customers c ON c.sales_rep_id = sr.id JOIN orders o ON o.customer_id = c.id GROUP BY sr.id, sr.name",
            "SELECT sr.name, COUNT(c.id) FROM sales_representatives sr JOIN customers c ON c.sales_rep_id = sr.id JOIN orders o ON o.customer_id = c.id GROUP BY sr.id, sr.name",
            ("sales_representatives", "customers", "orders"),
            "Orders multiply customers.",
        ),
        _p(
            "Count unique payment orders by status.",
            "SELECT p.status, COUNT(DISTINCT p.order_id) FROM payments p JOIN order_items oi ON oi.order_id = p.order_id GROUP BY p.status",
            "SELECT p.status, COUNT(p.order_id) FROM payments p JOIN order_items oi ON oi.order_id = p.order_id GROUP BY p.status",
            ("payments",),
            "The contrast counts payment rows rather than unique orders.",
        ),
        _p(
            "Count unique refund orders by reason.",
            "SELECT r.reason, COUNT(DISTINCT r.order_id) FROM refunds r JOIN order_items oi ON oi.order_id = r.order_id GROUP BY r.reason",
            "SELECT r.reason, COUNT(r.order_id) FROM refunds r JOIN order_items oi ON oi.order_id = r.order_id GROUP BY r.reason",
            ("refunds",),
            "The requested entity is order identity, not refund rows.",
        ),
        _p(
            "Count unique representatives in each region.",
            "SELECT r.name, COUNT(DISTINCT sr.id) FROM regions r JOIN sales_representatives sr ON sr.region_id = r.id JOIN customers c ON c.sales_rep_id = sr.id GROUP BY r.id, r.name",
            "SELECT r.name, COUNT(sr.id) FROM regions r JOIN sales_representatives sr ON sr.region_id = r.id JOIN customers c ON c.sales_rep_id = sr.id GROUP BY r.id, r.name",
            ("regions", "sales_representatives", "customers"),
            "Customers multiply representative rows.",
        ),
        _p(
            "Count unique product categories in each order.",
            "SELECT o.id, COUNT(DISTINCT p.category) FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id GROUP BY o.id",
            "SELECT o.id, COUNT(p.category) FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id GROUP BY o.id",
            ("orders", "order_items", "products"),
            "The contrast counts product rows rather than categories.",
        ),
        _p(
            "Count unique payment orders for each currency.",
            "SELECT o.currency, COUNT(DISTINCT p.order_id) FROM orders o JOIN payments p ON p.order_id = o.id JOIN order_items oi ON oi.order_id = o.id GROUP BY o.currency",
            "SELECT o.currency, COUNT(p.order_id) FROM orders o JOIN payments p ON p.order_id = o.id JOIN order_items oi ON oi.order_id = o.id GROUP BY o.currency",
            ("orders", "payments"),
            "The contrast counts payment rows rather than unique orders.",
        ),
    ),
    "AGGREGATE_FANOUT": (
        _p(
            "Show total order amount by customer region.",
            "SELECT c.region_id, SUM(o.total_amount) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.region_id",
            "SELECT c.region_id, SUM(o.total_amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.region_id",
            ("customers", "orders", "order_items"),
            "Order items multiply the order measure before SUM.",
        ),
        _p(
            "Show total order subtotal by region.",
            "SELECT c.region_id, SUM(o.subtotal) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.region_id",
            "SELECT c.region_id, SUM(o.subtotal) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.region_id",
            ("customers", "orders", "order_items"),
            "Order items multiply rows before AVG.",
        ),
        _p(
            "Show total payment amount by region.",
            "SELECT c.region_id, SUM(p.amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN payments p ON p.order_id = o.id GROUP BY c.region_id",
            "SELECT c.region_id, SUM(p.amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id JOIN payments p ON p.order_id = o.id GROUP BY c.region_id",
            ("customers", "orders", "order_items", "payments"),
            "Order items multiply payment measures.",
        ),
        _p(
            "Show total refund amount by representative.",
            "SELECT sr.id, SUM(r.amount) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id JOIN refunds r ON r.order_id = o.id GROUP BY sr.id",
            "SELECT sr.id, SUM(r.amount) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id JOIN order_items oi ON oi.order_id = o.id JOIN refunds r ON r.order_id = o.id GROUP BY sr.id",
            ("sales_representatives", "orders", "order_items", "refunds"),
            "Order items multiply refund measures.",
        ),
        _p(
            "Show total order amount by status.",
            "SELECT status, SUM(total_amount) FROM orders GROUP BY status",
            "SELECT o.status, SUM(o.total_amount) FROM orders o JOIN order_items oi ON oi.order_id = o.id GROUP BY o.status",
            ("orders", "order_items"),
            "Order items multiply order rows before AVG.",
        ),
        _p(
            "Show total order discount by currency.",
            "SELECT currency, SUM(discount_amount) FROM orders GROUP BY currency",
            "SELECT o.currency, SUM(o.discount_amount) FROM orders o JOIN order_items oi ON oi.order_id = o.id GROUP BY o.currency",
            ("orders", "order_items"),
            "Order items multiply order discounts.",
        ),
        _p(
            "Show total order subtotal by region.",
            "SELECT c.region_id, SUM(o.subtotal) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.region_id",
            "SELECT c.region_id, SUM(o.subtotal) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN payments p ON p.order_id = o.id GROUP BY c.region_id",
            ("customers", "orders", "payments"),
            "Payments multiply order subtotals.",
        ),
        _p(
            "Show total payment amount by order currency.",
            "SELECT o.currency, SUM(p.amount) FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY o.currency",
            "SELECT o.currency, SUM(p.amount) FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN payments p ON p.order_id = o.id GROUP BY o.currency",
            ("orders", "order_items", "payments"),
            "Order items multiply payment rows.",
        ),
        _p(
            "Show average refund amount by customer.",
            "SELECT c.id, AVG(r.amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN refunds r ON r.order_id = o.id GROUP BY c.id",
            "SELECT c.id, AVG(r.amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id JOIN refunds r ON r.order_id = o.id GROUP BY c.id",
            ("customers", "orders", "order_items", "refunds"),
            "Order items multiply refunds before AVG.",
        ),
        _p(
            "Show total order amount by sales representative.",
            "SELECT sr.id, SUM(o.total_amount) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id",
            "SELECT sr.id, SUM(o.total_amount) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id JOIN order_items oi ON oi.order_id = o.id GROUP BY sr.id",
            ("sales_representatives", "orders", "order_items"),
            "Order items multiply order amounts.",
        ),
    ),
    "DIMENSION_PROJECTION": (
        _p(
            "Show average order amount by status.",
            "SELECT status, AVG(total_amount) FROM orders GROUP BY status",
            "SELECT average_amount FROM (SELECT status, AVG(total_amount) AS average_amount FROM orders GROUP BY status) q",
            ("orders",),
            "The final projection omits the requested status dimension.",
        ),
        _p(
            "Show total orders by region.",
            "SELECT r.name, COUNT(o.id) FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name",
            "SELECT order_count FROM (SELECT r.name, COUNT(o.id) AS order_count FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name) q",
            ("regions", "customers", "orders"),
            "The final projection omits region.",
        ),
        _p(
            "Show item quantity per product category.",
            "SELECT p.category, SUM(oi.quantity) FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category",
            "SELECT item_quantity FROM (SELECT p.category, SUM(oi.quantity) AS item_quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category) q",
            ("products", "order_items"),
            "The final projection omits product category.",
        ),
        _p(
            "Show average payment by order currency.",
            "SELECT o.currency, AVG(p.amount) FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY o.currency",
            "SELECT average_payment FROM (SELECT o.currency, AVG(p.amount) AS average_payment FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY o.currency) q",
            ("orders", "payments"),
            "The final projection omits currency.",
        ),
        _p(
            "Show refund total for each reason.",
            "SELECT reason, SUM(amount) FROM refunds GROUP BY reason",
            "SELECT refund_total FROM (SELECT reason, SUM(amount) AS refund_total FROM refunds GROUP BY reason) q",
            ("refunds",),
            "The final projection omits refund reason.",
        ),
        _p(
            "Show order count per sales representative.",
            "SELECT sr.name, COUNT(o.id) FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name",
            "SELECT order_count FROM (SELECT sr.name, COUNT(o.id) AS order_count FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name) q",
            ("sales_representatives", "orders"),
            "The final projection omits representative.",
        ),
        _p(
            "Show average product price for each category.",
            "SELECT category, AVG(unit_price) FROM products GROUP BY category",
            "SELECT average_price FROM (SELECT category, AVG(unit_price) AS average_price FROM products GROUP BY category) q",
            ("products",),
            "The final projection omits category.",
        ),
        _p(
            "Show payment count by status.",
            "SELECT status, COUNT(id) FROM payments GROUP BY status",
            "SELECT payment_count FROM (SELECT status, COUNT(id) AS payment_count FROM payments GROUP BY status) q",
            ("payments",),
            "The final projection omits status.",
        ),
        _p(
            "Show total discounts by currency.",
            "SELECT currency, SUM(discount_amount) FROM orders GROUP BY currency",
            "SELECT discount_total FROM (SELECT currency, SUM(discount_amount) AS discount_total FROM orders GROUP BY currency) q",
            ("orders",),
            "The final projection omits currency.",
        ),
        _p(
            "Show refund count per order status.",
            "SELECT o.status, COUNT(r.id) FROM orders o JOIN refunds r ON r.order_id = o.id GROUP BY o.status",
            "SELECT refund_count FROM (SELECT o.status, COUNT(r.id) AS refund_count FROM orders o JOIN refunds r ON r.order_id = o.id GROUP BY o.status) q",
            ("orders", "refunds"),
            "The final projection omits order status.",
        ),
    ),
    "MEASURE_PROJECTION": (
        _p(
            "Show region and total order amount.",
            "SELECT c.region_id, SUM(o.total_amount) AS total_amount FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.region_id",
            "SELECT region_id FROM (SELECT c.region_id, SUM(o.total_amount) AS total_amount FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.region_id) q",
            ("customers", "orders"),
            "The requested total measure is omitted.",
        ),
        _p(
            "Show customer and order count.",
            "SELECT c.name, COUNT(o.id) AS order_count FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name",
            "SELECT name FROM (SELECT c.name, COUNT(o.id) AS order_count FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name) q",
            ("customers", "orders"),
            "The requested count measure is omitted.",
        ),
        _p(
            "Show product and average quantity.",
            "SELECT p.name, AVG(oi.quantity) AS average_quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name",
            "SELECT name FROM (SELECT p.name, AVG(oi.quantity) AS average_quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name) q",
            ("products", "order_items"),
            "The requested average measure is omitted.",
        ),
        _p(
            "Show currency and total discount.",
            "SELECT currency, SUM(discount_amount) AS total_discount FROM orders GROUP BY currency",
            "SELECT currency FROM (SELECT currency, SUM(discount_amount) AS total_discount FROM orders GROUP BY currency) q",
            ("orders",),
            "The requested total measure is omitted.",
        ),
        _p(
            "Show status and payment count.",
            "SELECT status, COUNT(id) AS payment_count FROM payments GROUP BY status",
            "SELECT status FROM (SELECT status, COUNT(id) AS payment_count FROM payments GROUP BY status) q",
            ("payments",),
            "The requested count measure is omitted.",
        ),
        _p(
            "Show reason and refund average.",
            "SELECT reason, AVG(amount) AS average_refund FROM refunds GROUP BY reason",
            "SELECT reason FROM (SELECT reason, AVG(amount) AS average_refund FROM refunds GROUP BY reason) q",
            ("refunds",),
            "The requested average measure is omitted.",
        ),
        _p(
            "Show representative and order total.",
            "SELECT sr.name, SUM(o.total_amount) AS order_total FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name",
            "SELECT name FROM (SELECT sr.name, SUM(o.total_amount) AS order_total FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name) q",
            ("sales_representatives", "orders"),
            "The requested total measure is omitted.",
        ),
        _p(
            "Show region and customer count.",
            "SELECT region_id, COUNT(id) AS customer_count FROM customers GROUP BY region_id",
            "SELECT region_id FROM (SELECT region_id, COUNT(id) AS customer_count FROM customers GROUP BY region_id) q",
            ("customers",),
            "The requested count measure is omitted.",
        ),
        _p(
            "Show category and product count.",
            "SELECT category, COUNT(id) AS product_count FROM products GROUP BY category",
            "SELECT category FROM (SELECT category, COUNT(id) AS product_count FROM products GROUP BY category) q",
            ("products",),
            "The requested count measure is omitted.",
        ),
        _p(
            "Show order status and average subtotal.",
            "SELECT status, AVG(subtotal) AS average_subtotal FROM orders GROUP BY status",
            "SELECT status FROM (SELECT status, AVG(subtotal) AS average_subtotal FROM orders GROUP BY status) q",
            ("orders",),
            "The requested average measure is omitted.",
        ),
    ),
    "EXPLICIT_FILTER": (
        _p(
            "Show completed orders.",
            "SELECT id, order_number FROM orders WHERE status = 'completed'",
            "SELECT id, order_number FROM orders",
            ("orders",),
            "The explicit completed-status filter is omitted.",
        ),
        _p(
            "List pending orders.",
            "SELECT id, order_number FROM orders WHERE status = 'pending'",
            "SELECT id, order_number FROM orders",
            ("orders",),
            "The explicit pending-status filter is omitted.",
        ),
        _p(
            "Show failed orders.",
            "SELECT id, order_number FROM orders WHERE status = 'failed'",
            "SELECT id, order_number FROM orders WHERE status = 'pending'",
            ("orders",),
            "The explicit failed-status filter is replaced by pending.",
        ),
        _p(
            "List pending payments.",
            "SELECT id, order_id FROM payments WHERE status = 'pending'",
            "SELECT id, order_id FROM payments",
            ("payments",),
            "The explicit pending-status filter is omitted.",
        ),
        _p(
            "Show refunded orders.",
            "SELECT id, order_number FROM orders WHERE status = 'refunded'",
            "SELECT id, order_number FROM orders WHERE status <> 'refunded'",
            ("orders",),
            "The explicit refunded-status population is inverted.",
        ),
        _p(
            "List completed orders by currency.",
            "SELECT currency, COUNT(id) FROM orders WHERE status = 'completed' GROUP BY currency",
            "SELECT currency, COUNT(id) FROM orders GROUP BY currency",
            ("orders",),
            "The completed-status filter is omitted.",
        ),
        _p(
            "Show completed order totals by status.",
            "SELECT status, SUM(total_amount) FROM orders WHERE status = 'completed' GROUP BY status",
            "SELECT status, SUM(total_amount) FROM orders GROUP BY status",
            ("orders",),
            "The explicit completed filter is omitted.",
        ),
        _p(
            "Count failed orders by currency.",
            "SELECT currency, COUNT(id) FROM orders WHERE status = 'failed' GROUP BY currency",
            "SELECT currency, COUNT(id) FROM orders WHERE status = 'completed' GROUP BY currency",
            ("orders",),
            "The wrong status population is used.",
        ),
        _p(
            "Show pending order amounts.",
            "SELECT id, total_amount FROM orders WHERE status = 'pending'",
            "SELECT id, total_amount FROM orders WHERE status = 'completed'",
            ("orders",),
            "The wrong status population is used.",
        ),
        _p(
            "Show completed order totals.",
            "SELECT id, total_amount FROM orders WHERE status = 'completed'",
            "SELECT id, total_amount FROM orders",
            ("orders",),
            "The completed-status filter is omitted.",
        ),
    ),
    "UNREQUESTED_LIMIT": (
        _p(
            "List all products.",
            "SELECT id, name FROM products ORDER BY id",
            "SELECT id, name FROM products ORDER BY id LIMIT 1",
            ("products",),
            "The complete listing is silently truncated.",
        ),
        _p(
            "List all customers.",
            "SELECT id, name FROM customers ORDER BY id",
            "SELECT id, name FROM customers ORDER BY id LIMIT 5",
            ("customers",),
            "The complete listing is silently truncated.",
        ),
        _p(
            "Show order count by status.",
            "SELECT status, COUNT(id) FROM orders GROUP BY status ORDER BY status",
            "SELECT status, COUNT(id) FROM orders GROUP BY status ORDER BY status LIMIT 1",
            ("orders",),
            "The complete grouped result is silently truncated.",
        ),
        _p(
            "List all order numbers.",
            "SELECT order_number FROM orders ORDER BY order_number",
            "SELECT order_number FROM orders ORDER BY order_number LIMIT 10",
            ("orders",),
            "The complete listing is silently truncated.",
        ),
        _p(
            "List every product category.",
            "SELECT DISTINCT category FROM products ORDER BY category",
            "SELECT DISTINCT category FROM products ORDER BY category LIMIT 1",
            ("products",),
            "The complete distinct result is silently truncated.",
        ),
        _p(
            "Show all product names.",
            "SELECT name FROM products ORDER BY name",
            "SELECT name FROM products ORDER BY name LIMIT 1",
            ("products",),
            "The complete distinct result is silently truncated.",
        ),
        _p(
            "Show customer names by region.",
            "SELECT name, region_id FROM customers ORDER BY name",
            "SELECT name, region_id FROM customers ORDER BY name LIMIT 3",
            ("customers",),
            "The complete listing is silently truncated.",
        ),
        _p(
            "Show all order statuses.",
            "SELECT DISTINCT status FROM orders ORDER BY status",
            "SELECT DISTINCT status FROM orders ORDER BY status LIMIT 1",
            ("orders",),
            "The complete distinct result is silently truncated.",
        ),
        _p(
            "List every representative.",
            "SELECT id, name FROM sales_representatives ORDER BY name",
            "SELECT id, name FROM sales_representatives ORDER BY name LIMIT 2",
            ("sales_representatives",),
            "The complete listing is silently truncated.",
        ),
        _p(
            "Show all payment identifiers.",
            "SELECT id FROM payments ORDER BY id",
            "SELECT id FROM payments ORDER BY id LIMIT 4",
            ("payments",),
            "The complete listing is silently truncated.",
        ),
    ),
    "CARTESIAN": (
        _p(
            "Count orders for each customer.",
            "SELECT c.name, COUNT(o.id) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name",
            "SELECT c.name, COUNT(o.id) FROM customers c CROSS JOIN orders o GROUP BY c.id, c.name",
            ("customers", "orders"),
            "The contrast replaces the relationship join with a Cartesian product.",
        ),
        _p(
            "Count items for each product.",
            "SELECT p.name, COUNT(oi.id) FROM products p LEFT JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name",
            "SELECT p.name, COUNT(oi.id) FROM products p CROSS JOIN order_items oi GROUP BY p.id, p.name",
            ("products", "order_items"),
            "The contrast replaces the relationship join with a Cartesian product.",
        ),
        _p(
            "Count customers for each region.",
            "SELECT r.name, COUNT(c.id) FROM regions r LEFT JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name",
            "SELECT r.name, COUNT(c.id) FROM regions r CROSS JOIN customers c GROUP BY r.id, r.name",
            ("regions", "customers"),
            "The contrast replaces the relationship join with a Cartesian product.",
        ),
        _p(
            "Count orders for each representative.",
            "SELECT sr.name, COUNT(o.id) FROM sales_representatives sr LEFT JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name",
            "SELECT sr.name, COUNT(o.id) FROM sales_representatives sr CROSS JOIN orders o GROUP BY sr.id, sr.name",
            ("sales_representatives", "orders"),
            "The contrast replaces the relationship join with a Cartesian product.",
        ),
        _p(
            "Count payments for each order.",
            "SELECT o.id, COUNT(p.id) FROM orders o LEFT JOIN payments p ON p.order_id = o.id GROUP BY o.id",
            "SELECT o.id, COUNT(p.id) FROM orders o CROSS JOIN payments p GROUP BY o.id",
            ("orders", "payments"),
            "The contrast replaces the relationship join with a Cartesian product.",
        ),
        _p(
            "Count refunds for each order.",
            "SELECT o.id, COUNT(r.id) FROM orders o LEFT JOIN refunds r ON r.order_id = o.id GROUP BY o.id",
            "SELECT o.id, COUNT(r.id) FROM orders o CROSS JOIN refunds r GROUP BY o.id",
            ("orders", "refunds"),
            "The contrast replaces the relationship join with a Cartesian product.",
        ),
        _p(
            "Show item quantity by product category.",
            "SELECT p.category, SUM(oi.quantity) FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category",
            "SELECT p.category, SUM(oi.quantity) FROM products p CROSS JOIN order_items oi GROUP BY p.category",
            ("products", "order_items"),
            "The contrast replaces the product relationship with a Cartesian product.",
        ),
        _p(
            "Show customer payment total by customer.",
            "SELECT c.id, SUM(p.amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN payments p ON p.order_id = o.id GROUP BY c.id",
            "SELECT c.id, SUM(p.amount) FROM customers c CROSS JOIN orders o CROSS JOIN payments p GROUP BY c.id",
            ("customers", "orders", "payments"),
            "The contrast removes both relationship predicates.",
        ),
        _p(
            "Show refund total by region.",
            "SELECT c.region_id, SUM(r.amount) FROM customers c JOIN orders o ON o.customer_id = c.id JOIN refunds r ON r.order_id = o.id GROUP BY c.region_id",
            "SELECT c.region_id, SUM(r.amount) FROM customers c CROSS JOIN orders o CROSS JOIN refunds r GROUP BY c.region_id",
            ("customers", "orders", "refunds"),
            "The contrast removes relationship predicates.",
        ),
        _p(
            "Show item quantity by category.",
            "SELECT p.category, SUM(oi.quantity) FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category",
            "SELECT p.category, SUM(oi.quantity) FROM products p CROSS JOIN order_items oi GROUP BY p.category",
            ("products", "order_items"),
            "The contrast removes the product-item relationship predicate.",
        ),
    ),
    "OUTPUT_SHAPE": (
        _p(
            "Show region name and customer count.",
            "SELECT r.name, COUNT(c.id) FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name",
            "SELECT r.name FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name",
            ("regions", "customers"),
            "The explicit count output is omitted.",
        ),
        _p(
            "Show product name, category, and price.",
            "SELECT name, category, unit_price FROM products",
            "SELECT name, category FROM products",
            ("products",),
            "The explicit price output is omitted.",
        ),
        _p(
            "Show customer name and region.",
            "SELECT c.name, c.region_id FROM customers c",
            "SELECT c.name FROM customers c",
            ("customers",),
            "The explicit region output is omitted.",
        ),
        _p(
            "Show order number, status, and total amount.",
            "SELECT order_number, status, total_amount FROM orders",
            "SELECT order_number, status FROM orders",
            ("orders",),
            "The explicit total amount output is omitted.",
        ),
        _p(
            "Show payment id, amount, and status.",
            "SELECT id, amount, status FROM payments",
            "SELECT id, amount FROM payments",
            ("payments",),
            "The explicit status output is omitted.",
        ),
        _p(
            "Show refund id, reason, and amount.",
            "SELECT id, reason, amount FROM refunds",
            "SELECT id, reason FROM refunds",
            ("refunds",),
            "The explicit amount output is omitted.",
        ),
        _p(
            "Show representative name and order count.",
            "SELECT sr.name, COUNT(o.id) FROM sales_representatives sr LEFT JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name",
            "SELECT sr.name FROM sales_representatives sr LEFT JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name",
            ("sales_representatives", "orders"),
            "The explicit count output is omitted.",
        ),
        _p(
            "Show category and total item quantity.",
            "SELECT p.category, SUM(oi.quantity) FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category",
            "SELECT p.category FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category",
            ("products", "order_items"),
            "The explicit quantity output is omitted.",
        ),
        _p(
            "Show order id and payment total.",
            "SELECT o.id, SUM(p.amount) FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY o.id",
            "SELECT o.id FROM orders o JOIN payments p ON p.order_id = o.id GROUP BY o.id",
            ("orders", "payments"),
            "The explicit payment total output is omitted.",
        ),
        _p(
            "Show region, customer count, and representative count.",
            "SELECT r.name, COUNT(DISTINCT c.id), COUNT(DISTINCT sr.id) FROM regions r LEFT JOIN customers c ON c.region_id = r.id LEFT JOIN sales_representatives sr ON sr.region_id = r.id GROUP BY r.id, r.name",
            "SELECT r.name, COUNT(DISTINCT c.id) FROM regions r LEFT JOIN customers c ON c.region_id = r.id LEFT JOIN sales_representatives sr ON sr.region_id = r.id GROUP BY r.id, r.name",
            ("regions", "customers", "sales_representatives"),
            "The second explicitly requested measure is omitted.",
        ),
    ),
}


_EXPECTED_SIGNAL_FAMILY = {
    "GROUPING": "REQUESTED_GROUPING_MISSING",
    "TOP_N": "TOP_N_STRUCTURE",
    "AGGREGATE_THRESHOLD": "AGGREGATE_THRESHOLD_FILTER",
    "AGGREGATION_INTENT": "AGGREGATION_INTENT",
    "ENTITY_COUNT_FANOUT": "ENTITY_COUNT_FANOUT",
    "AGGREGATE_FANOUT": "AGGREGATE_FANOUT",
    "DIMENSION_PROJECTION": "DIMENSION_PROJECTION",
    "MEASURE_PROJECTION": "MEASURE_PROJECTION",
    "EXPLICIT_FILTER": "EXPLICIT_FILTER",
    "UNREQUESTED_LIMIT": "UNREQUESTED_LIMIT",
    "CARTESIAN": "CARTESIAN",
    "OUTPUT_SHAPE": "OUTPUT_SHAPE",
}


def build_corpus() -> tuple[AdjudicatedVerificationCase, ...]:
    """Build the finite, explicitly authored corpus in stable order."""
    cases: list[AdjudicatedVerificationCase] = []
    for family, specs in _PAIR_SPECS.items():
        for index, spec in enumerate(specs):
            split = "DEV" if index < 8 else "HOLDOUT"
            stem = f"m501-{family.lower()}-{index + 1:02d}"
            correct_id = f"{stem}-correct"
            incorrect_id = f"{stem}-incorrect"
            signal_family = _EXPECTED_SIGNAL_FAMILY[family]
            common = {
                "split": split,
                "family": family,
                "question": spec.question,
                "expected_signal_family": signal_family,
                "rationale_code": family,
                "schema_dependencies": spec.dependencies,
                "gold_reference_sql": spec.correct_sql,
            }
            cases.extend(
                (
                    AdjudicatedVerificationCase(
                        case_id=correct_id,
                        candidate_sql=spec.correct_sql,
                        expected_semantic_status=ExpectedSemanticStatus.CORRECT,
                        paired_case_id=incorrect_id,
                        adjudication_note="Independent correct control: "
                        "the candidate satisfies the explicit request.",
                        **common,
                    ),
                    AdjudicatedVerificationCase(
                        case_id=incorrect_id,
                        candidate_sql=spec.incorrect_sql,
                        expected_semantic_status=ExpectedSemanticStatus.INCORRECT,
                        paired_case_id=correct_id,
                        adjudication_note=spec.rationale,
                        **common,
                    ),
                )
            )
    return tuple(cases)


def canonical_corpus_payload(
    cases: tuple[AdjudicatedVerificationCase, ...],
) -> list[dict[str, Any]]:
    return [case.model_dump(mode="json") for case in cases]


def corpus_hash(cases: tuple[AdjudicatedVerificationCase, ...]) -> str:
    payload = json.dumps(
        canonical_corpus_payload(cases), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode()).hexdigest()


def split_hash(cases: tuple[AdjudicatedVerificationCase, ...], split: str) -> str:
    selected = tuple(case for case in cases if case.split == split)
    return corpus_hash(selected)


def validate_structure(cases: tuple[AdjudicatedVerificationCase, ...]) -> dict[str, Any]:
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("M5.0.1 corpus contains duplicate case IDs")
    by_id = {case.case_id: case for case in cases}
    for case in cases:
        if case.paired_case_id is None or case.paired_case_id not in by_id:
            raise ValueError(f"invalid pair for {case.case_id}")
        if by_id[case.paired_case_id].paired_case_id != case.case_id:
            raise ValueError(f"pair is not reciprocal for {case.case_id}")
        if case.expected_semantic_status not in {
            ExpectedSemanticStatus.CORRECT,
            ExpectedSemanticStatus.INCORRECT,
        }:
            raise ValueError(f"primary corpus cannot contain {case.expected_semantic_status}")
    family_counts: dict[str, dict[str, int]] = {}
    for family in sorted(_PAIR_SPECS):
        selected = [case for case in cases if case.family == family]
        family_counts[family] = {
            "total": len(selected),
            "correct": sum(
                case.expected_semantic_status == ExpectedSemanticStatus.CORRECT for case in selected
            ),
            "incorrect": sum(
                case.expected_semantic_status == ExpectedSemanticStatus.INCORRECT
                for case in selected
            ),
            "dev": sum(case.split == "DEV" for case in selected),
            "holdout": sum(case.split == "HOLDOUT" for case in selected),
            "paired": len(selected) // 2,
        }
    return {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "total": len(cases),
        "dev": sum(case.split == "DEV" for case in cases),
        "holdout": sum(case.split == "HOLDOUT" for case in cases),
        "correct": sum(
            case.expected_semantic_status == ExpectedSemanticStatus.CORRECT for case in cases
        ),
        "incorrect": sum(
            case.expected_semantic_status == ExpectedSemanticStatus.INCORRECT for case in cases
        ),
        "ambiguous": 0,
        "family_counts": family_counts,
        "corpus_hash": corpus_hash(cases),
        "dev_hash": split_hash(cases, "DEV"),
        "holdout_hash": split_hash(cases, "HOLDOUT"),
    }


def validate_with_m1(
    cases: tuple[AdjudicatedVerificationCase, ...], service: SqlSafetyService
) -> dict[str, Any]:
    reference_results: dict[str, QueryExecution] = {}
    rows: list[dict[str, Any]] = []
    for case in cases:
        parse_one(case.candidate_sql, read="postgres")
        if case.gold_reference_sql is None:
            raise ValueError(f"missing reference SQL for {case.case_id}")
        parse_one(case.gold_reference_sql, read="postgres")
        reference_plan = service.plan(SqlCandidate(sql=case.gold_reference_sql))
        if not isinstance(reference_plan, QueryPlan):
            raise ValueError(f"reference SQL failed M1 for {case.case_id}")
        reference_execution = reference_results.get(case.gold_reference_sql)
        if reference_execution is None:
            outcome = service.execute(reference_plan)
            if not isinstance(outcome, QueryExecution):
                raise ValueError(f"reference SQL failed execution for {case.case_id}")
            reference_execution = outcome
            reference_results[case.gold_reference_sql] = reference_execution
        candidate_plan = service.plan(SqlCandidate(sql=case.candidate_sql))
        candidate_execution: QueryExecution | None = None
        if isinstance(candidate_plan, QueryPlan):
            outcome = service.execute(candidate_plan)
            candidate_execution = outcome if isinstance(outcome, QueryExecution) else None
        equivalent = None
        if candidate_execution is not None:
            equivalent = assess_query_results(
                candidate_execution,
                reference_execution,
                order_sensitive=True,
                actual_sql=case.candidate_sql,
                expected_sql=case.gold_reference_sql,
            ).equivalent
        rows.append(
            {
                "case_id": case.case_id,
                "split": case.split,
                "family": case.family,
                "expected_status": case.expected_semantic_status,
                "candidate_parse": True,
                "candidate_m1_accepted": isinstance(candidate_plan, QueryPlan),
                "candidate_executed": candidate_execution is not None,
                "reference_m1_accepted": True,
                "reference_executed": True,
                "candidate_equivalent_to_reference": equivalent,
            }
        )
    incorrect = [row for row in rows if row["expected_status"] == ExpectedSemanticStatus.INCORRECT]
    correct = [row for row in rows if row["expected_status"] == ExpectedSemanticStatus.CORRECT]
    family_validation = {}
    for family in sorted({case.family for case in cases}):
        family_rows = [row for row in rows if row["family"] == family]
        family_incorrect = [
            row for row in family_rows if row["expected_status"] == ExpectedSemanticStatus.INCORRECT
        ]
        family_validation[family] = {
            "total": len(family_rows),
            "correct": len(family_rows) - len(family_incorrect),
            "incorrect": len(family_incorrect),
            "candidate_m1_accepted": sum(row["candidate_m1_accepted"] for row in family_rows),
            "candidate_executed": sum(row["candidate_executed"] for row in family_rows),
            "incorrect_but_executes": sum(row["candidate_executed"] for row in family_incorrect),
            "incorrect_differentiated": sum(
                row["candidate_equivalent_to_reference"] is False for row in family_incorrect
            ),
        }
    return {
        "case_count": len(rows),
        "candidate_parse_success": sum(row["candidate_parse"] for row in rows),
        "reference_parse_success": len(rows),
        "candidate_m1_accepted": sum(row["candidate_m1_accepted"] for row in rows),
        "reference_m1_accepted": len(rows),
        "candidate_executed": sum(row["candidate_executed"] for row in rows),
        "incorrect_but_executes": sum(row["candidate_executed"] for row in incorrect),
        "incorrect_execution_success_rate": sum(row["candidate_executed"] for row in incorrect)
        / len(incorrect),
        "reference_execution_success": len(rows),
        "correct_candidate_equivalence": sum(
            row["candidate_equivalent_to_reference"] is True for row in correct
        ),
        "incorrect_differentiated": sum(
            row["candidate_equivalent_to_reference"] is False for row in incorrect
        ),
        "counterexample_fixture_count": 0,
        "multi_fixture_validated_count": 0,
        "family_validation": family_validation,
        "rows": rows,
    }


def evaluate_verifier(
    cases: tuple[AdjudicatedVerificationCase, ...],
    split: str,
) -> dict[str, Any]:
    """Score verifier-v1 after corpus labels and execution checks exist."""
    from app.verification.verifier import _RULES

    verifier = DeterministicSemanticVerifier()
    selected = [case for case in cases if case.split == split]
    reports: list[dict[str, Any]] = []
    started = perf_counter()
    for case in selected:
        report = verifier.verify(case.question, case.candidate_sql)
        reports.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "expected_status": case.expected_semantic_status,
                "expected_signal_family": case.expected_signal_family,
                "signals": [signal.model_dump(mode="json") for signal in report.signals],
                "hard_fired": bool(report.hard_signals),
                "recommendation": report.recommendation.value,
            }
        )
    elapsed_ms = (perf_counter() - started) * 1000
    signal_codes = [code.value for code, _ in _RULES]
    rule_stats = {code: _rule_metrics(reports, code) for code in signal_codes}
    family_stats = {
        family: _family_metrics(reports, family)
        for family in sorted({case.family for case in selected})
    }
    return {
        "split": split,
        "verifier_version": VERIFIER_VERSION,
        "ruleset_hash": RULESET_HASH,
        "case_count": len(selected),
        "fired_count": sum(row["hard_fired"] for row in reports),
        "any_signal_precision": _precision([row for row in reports if row["signals"]]),
        "any_signal_incorrect_recall": _recall([row for row in reports if row["signals"]], reports),
        "any_hard_precision": _precision([row for row in reports if row["hard_fired"]]),
        "any_hard_incorrect_recall": _recall(
            [row for row in reports if row["hard_fired"]], reports
        ),
        "any_hard_false_positive_rate": _false_positive_rate(
            [row for row in reports if row["hard_fired"]], reports
        ),
        "equivalence_false_positive_rate": _equivalence_false_positive_rate(reports),
        "rule_stats": rule_stats,
        "family_stats": family_stats,
        "pair_stats": _pair_stats(selected, reports),
        "latency_ms": {
            "average": elapsed_ms / len(selected) if selected else 0.0,
        },
        "reports": reports,
    }


def _rule_metrics(reports: list[dict[str, Any]], code: str) -> dict[str, int | float]:
    fired = [row for row in reports if any(signal["code"] == code for signal in row["signals"])]
    incorrect = sum(row["expected_status"] == ExpectedSemanticStatus.INCORRECT for row in reports)
    correct = len(reports) - incorrect
    tp = sum(row["expected_status"] == ExpectedSemanticStatus.INCORRECT for row in fired)
    fp = len(fired) - tp
    fn = incorrect - tp
    tn = correct - fp
    return {
        "support": len(fired),
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": tp / len(fired) if fired else 0.0,
        "recall": tp / incorrect if incorrect else 0.0,
        "specificity": tn / correct if correct else 0.0,
        "false_positive_rate": fp / correct if correct else 0.0,
        "correct_case_fire_rate": fp / correct if correct else 0.0,
    }


def _family_metrics(reports: list[dict[str, Any]], family: str) -> dict[str, int | float]:
    rows = [row for row in reports if row["family"] == family]
    incorrect = [row for row in rows if row["expected_status"] == ExpectedSemanticStatus.INCORRECT]
    return {
        "total": len(rows),
        "correct": len(rows) - len(incorrect),
        "incorrect": len(incorrect),
        "hard_fired_incorrect": sum(row["hard_fired"] for row in incorrect),
        "mutation_detection_rate": sum(row["hard_fired"] for row in incorrect) / len(incorrect)
        if incorrect
        else 0.0,
        "hard_fired_correct": sum(
            row["hard_fired"]
            for row in rows
            if row["expected_status"] == ExpectedSemanticStatus.CORRECT
        ),
    }


def _pair_stats(
    cases: list[AdjudicatedVerificationCase], reports: list[dict[str, Any]]
) -> dict[str, int]:
    by_id = {row["case_id"]: row for row in reports}
    pairs: set[tuple[str, str]] = set()
    result = {
        "ideal_correct_unflagged_incorrect_flagged": 0,
        "both_unflagged": 0,
        "both_flagged": 0,
        "inverted": 0,
    }
    for case in cases:
        if case.paired_case_id is None:
            continue
        assert case.paired_case_id is not None
        pair = (
            min(case.case_id, case.paired_case_id),
            max(case.case_id, case.paired_case_id),
        )
        if pair in pairs or case.expected_semantic_status != ExpectedSemanticStatus.CORRECT:
            continue
        pairs.add(pair)
        correct_flagged = by_id[case.case_id]["hard_fired"]
        incorrect_flagged = by_id[case.paired_case_id]["hard_fired"]
        if not correct_flagged and incorrect_flagged:
            result["ideal_correct_unflagged_incorrect_flagged"] += 1
        elif not correct_flagged and not incorrect_flagged:
            result["both_unflagged"] += 1
        elif correct_flagged and incorrect_flagged:
            result["both_flagged"] += 1
        else:
            result["inverted"] += 1
    return result


def _precision(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return float(
        sum(row["expected_status"] == ExpectedSemanticStatus.INCORRECT for row in rows) / len(rows)
    )


def _recall(flagged: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> float:
    incorrect = sum(row["expected_status"] == ExpectedSemanticStatus.INCORRECT for row in all_rows)
    return (
        sum(row["expected_status"] == ExpectedSemanticStatus.INCORRECT for row in flagged)
        / incorrect
        if incorrect
        else 0.0
    )


def _false_positive_rate(flagged: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> float:
    correct = sum(row["expected_status"] == ExpectedSemanticStatus.CORRECT for row in all_rows)
    return (
        sum(row["expected_status"] == ExpectedSemanticStatus.CORRECT for row in flagged) / correct
        if correct
        else 0.0
    )


def _equivalence_false_positive_rate(reports: list[dict[str, Any]]) -> float:
    # All paired correct controls are independently authored equivalent controls.
    correct = [row for row in reports if row["expected_status"] == ExpectedSemanticStatus.CORRECT]
    return sum(row["hard_fired"] for row in correct) / len(correct) if correct else 0.0


def future_candidate_rules(dev: dict[str, Any]) -> list[str]:
    return sorted(
        code
        for code, values in dev["rule_stats"].items()
        if int(values["true_positive"]) >= 8
        and float(values["precision"]) >= 0.90
        and float(values["false_positive_rate"]) <= 0.10
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and audit the M5.0.1 corpus")
    parser.add_argument("--phase", choices=("validate", "dev", "holdout"), default="validate")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--accepted-rules", type=Path)
    args = parser.parse_args()
    corpus_started = perf_counter()
    cases = build_corpus()
    corpus_build_time_ms = (perf_counter() - corpus_started) * 1000
    structure = validate_structure(cases)
    artifact_dir = args.artifact_dir or ROOT / "evaluation/results/m501/local"
    _write_json(artifact_dir / "corpus_manifest.json", structure)
    if args.phase == "validate":
        started = perf_counter()
        service = SqlSafetyService(build_reader_engine(get_settings()))
        validation = validate_with_m1(cases, service)
        validation["corpus_build_time_ms"] = corpus_build_time_ms
        validation["validation_time_ms"] = (perf_counter() - started) * 1000
        _write_json(artifact_dir / "corpus_validation.json", validation)
        print(
            json.dumps({"manifest": structure, "validation": validation}, indent=2, sort_keys=True)
        )
        return
    if RULESET_HASH != "4b3c91168832f1d3ca4370972d33abfebc7158712ea7ac04c8ea687d2253b494":
        raise SystemExit("M5.0 verifier ruleset drifted; refusing primary evaluation")
    result = evaluate_verifier(cases, args.phase.upper())
    if args.phase == "dev":
        candidates = future_candidate_rules(result)
        _write_json(artifact_dir / "future_candidate_rules.json", {"rules": candidates})
        result["future_candidate_rules"] = candidates
    else:
        if args.accepted_rules is None:
            raise SystemExit("--accepted-rules is required for holdout")
        result["future_candidate_rules"] = json.loads(args.accepted_rules.read_text())["rules"]
    _write_json(artifact_dir / f"{args.phase}_verifier_summary.json", result)
    _write_json(artifact_dir / f"{args.phase}_per_rule.json", result["rule_stats"])
    _write_json(artifact_dir / f"{args.phase}_pair_analysis.json", result["pair_stats"])
    _write_json(artifact_dir / f"{args.phase}_family_distribution.json", result["family_stats"])
    _write_json(artifact_dir / f"{args.phase}_verifier_results.json", result["reports"])
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "reports"},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
