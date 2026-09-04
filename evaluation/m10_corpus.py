# ruff: noqa: E501

"""Provider-free construction of the M10 clean residual rebaseline corpus.

This module deliberately contains only benchmark-owned data construction.  It
does not import the database session, M1, a provider, or a runtime service.
The generated fixtures are frozen before the M10 runner performs reference
validation or calls the current architecture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.semantics.catalog import build_m3_catalog
from app.semantics.compiler import MetricCompiler
from app.semantics.requests import MetricRequest
from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.result_binding_protocol_v2 import (
    ResultBindingSpecV2,
    SlotBindingSpecV2,
    canonical_hash,
    projection_proofs,
)
from evaluation.result_equivalence_contract import (
    CONTRACT_VERSION,
    DuplicatePolicy,
    ExtraOutputPolicy,
    ResultEquivalenceContract,
    ResultSlot,
    RowOrderPolicy,
    ScalarOrTabular,
    SlotKind,
)
from evaluation.versioned_result_evaluator import contract_instance_hash

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
CORPUS_ID = "decisionsql-m10-clean-rebaseline"
CORPUS_VERSION = "m10-clean-rebaseline-v1"
M3_CONTRACT_HASH = "0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c"
M4_CORPUS_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
M10_STARTING_HEAD = "46e4e059eac8d7051e1ee7e23821ad4858b96ea9"

DIRECT_COUNTS = {
    "FILTER_PROJECTION": 24,
    "MULTI_TABLE_JOIN": 24,
    "AGGREGATE_GROUP": 24,
    "ORDER_TOPN": 14,
    "FORMULA_RATIO": 18,
    "TEMPORAL": 14,
    "WINDOW": 14,
    "DISTINCT_OR_SET": 8,
    "MIXED_COMPOSITION": 10,
}
DEV_DIRECT_COUNTS = {
    "FILTER_PROJECTION": 14,
    "MULTI_TABLE_JOIN": 14,
    "AGGREGATE_GROUP": 14,
    "ORDER_TOPN": 8,
    "FORMULA_RATIO": 11,
    "TEMPORAL": 8,
    "WINDOW": 8,
    "DISTINCT_OR_SET": 5,
    "MIXED_COMPOSITION": 8,
}
GOVERNED_METRICS = (
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


@dataclass(frozen=True)
class M10Case:
    case_id: str
    question: str
    split: str
    expected_route: str
    family: str
    reference_sql_variants: tuple[str, ...]
    semantic_ids: tuple[str, ...]
    literal_values: tuple[str, ...]
    governed_metric: str | None = None
    dimensions: tuple[str, ...] = ()

    @property
    def reference_sql(self) -> str:
        return self.reference_sql_variants[0]


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def construction_recipe() -> dict[str, Any]:
    return {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "case_count": 200,
        "split_counts": {"dev": 120, "holdout": 80},
        "route_counts": {"GOVERNED": 50, "DIRECT": 150},
        "governed_per_metric": 5,
        "governed_split_per_metric": {"dev": 3, "holdout": 2},
        "direct_category_counts": DIRECT_COUNTS,
        "direct_dev_category_counts": DEV_DIRECT_COUNTS,
        "direct_holdout_category_counts": {
            key: DIRECT_COUNTS[key] - DEV_DIRECT_COUNTS[key] for key in DIRECT_COUNTS
        },
        "case_id_scheme": "m10-gov-{metric}-{ordinal} / m10-direct-{family}-{ordinal}",
        "construction_seed_source": [
            "m10-clean-rebaseline-v1",
            M10_STARTING_HEAD,
            M3_CONTRACT_HASH,
            M4_CORPUS_HASH,
        ],
        "question_policy": "static benchmark-owned templates with deterministic variation",
        "reference_policy": "one frozen benchmark-owned M1-valid reference per case",
        "contract_policy": "result-equivalence-contract-v1; reference-blind candidate binding",
        "freshness_policy": "zero case/question/candidate SQL exact overlap with prior corpora",
        "execution_order_policy": "SHA256(m10-execution-order-v1|case_id|corpus_content_hash)",
        "provider_protocol_id": "decisionsql-m10-current-combined-pass1-v1",
        "evaluation_protocol_id": "m10-v1-v1-authoritative-v2-shadow",
    }


def construction_seed() -> str:
    recipe = "m10-clean-rebaseline-v1" + M10_STARTING_HEAD + M3_CONTRACT_HASH + M4_CORPUS_HASH
    return sha256(recipe.encode()).hexdigest()


def _direct_rows() -> dict[str, list[tuple[str, str, tuple[str, ...]]]]:
    """Fresh, static reference templates; no prior benchmark is imported."""
    rows: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}

    rows["FILTER_PROJECTION"] = [
        (f"Which customer accounts belong to region {i + 1}?", f"SELECT c.name AS customer_name, c.region_id AS region_key FROM customers AS c WHERE c.region_id = {(i % 4) + 1} ORDER BY c.name", (str((i % 4) + 1),))
        for i in range(8)
    ] + [
        ("Which products fall in the Electronics category?", "SELECT p.sku AS product_sku, p.name AS product_name FROM products AS p WHERE p.category = 'Electronics' ORDER BY p.name", ("Electronics",)),
        ("Which products are priced above 250 dollars?", "SELECT p.name AS product_name, p.unit_price AS listed_price FROM products AS p WHERE p.unit_price > 250 ORDER BY p.unit_price DESC, p.name", ("250",)),
        ("Which orders have completed status and USD currency?", "SELECT o.order_number AS order_ref, o.total_amount AS order_total FROM orders AS o WHERE o.status = 'completed' AND o.currency = 'USD' ORDER BY o.order_number", ("completed", "USD")),
        ("Which payments are settled and above 100 dollars?", "SELECT p.id AS payment_key, p.amount AS payment_amount FROM payments AS p WHERE p.status = 'settled' AND p.amount > 100 ORDER BY p.id", ("settled", "100")),
        ("Which refunds cite damaged goods as their reason?", "SELECT r.id AS refund_key, r.amount AS refund_amount FROM refunds AS r WHERE r.reason = 'damaged' ORDER BY r.id", ("damaged",)),
        ("Which orders have a discount larger than 20?", "SELECT o.order_number AS order_ref, o.discount_amount AS discount_value FROM orders AS o WHERE o.discount_amount > 20 ORDER BY o.order_number", ("20",)),
        ("Which customers were assigned to sales representative 3?", "SELECT c.id AS customer_key, c.name AS customer_name FROM customers AS c WHERE c.sales_rep_id = 3 ORDER BY c.id", ("3",)),
        ("Which order lines have quantity at least 4?", "SELECT oi.id AS line_key, oi.quantity AS units FROM order_items AS oi WHERE oi.quantity >= 4 ORDER BY oi.id", ("4",)),
        ("Which orders are pending?", "SELECT o.order_number AS order_ref, o.ordered_at AS order_time FROM orders AS o WHERE o.status = 'pending' ORDER BY o.ordered_at, o.id", ("pending",)),
        ("Which products in Office cost less than 180?", "SELECT p.sku AS product_sku, p.unit_price AS listed_price FROM products AS p WHERE p.category = 'Office' AND p.unit_price < 180 ORDER BY p.sku", ("Office", "180")),
        ("Which payments were recorded after June 2025?", "SELECT p.id AS payment_key, p.paid_at AS payment_time FROM payments AS p WHERE p.paid_at >= TIMESTAMP '2025-06-01' ORDER BY p.paid_at, p.id", ("2025-06-01",)),
        ("Which customers have a non-null sales representative?", "SELECT c.id AS customer_key, c.name AS customer_name FROM customers AS c WHERE c.sales_rep_id IS NOT NULL ORDER BY c.id", ()),
        ("Which orders have zero discount?", "SELECT o.order_number AS order_ref, o.subtotal AS order_subtotal FROM orders AS o WHERE o.discount_amount = 0 ORDER BY o.order_number", ("0",)),
        ("Which refunds exceed 50 dollars?", "SELECT r.id AS refund_key, r.amount AS refund_amount FROM refunds AS r WHERE r.amount >= 50 ORDER BY r.amount DESC, r.id", ("50",)),
        ("Which products have a name beginning with A?", "SELECT p.sku AS product_sku, p.name AS product_name FROM products AS p WHERE p.name LIKE 'A%' ORDER BY p.name", ("A%",)),
        ("Which orders have totals between 100 and 400?", "SELECT o.order_number AS order_ref, o.total_amount AS order_total FROM orders AS o WHERE o.total_amount >= 100 AND o.total_amount <= 400 ORDER BY o.total_amount, o.order_number", ("100", "400")),
    ]

    rows["MULTI_TABLE_JOIN"] = [
        ("List customers with the region assigned to each account.", "SELECT c.name AS customer_name, r.name AS region_name FROM customers AS c INNER JOIN regions AS r ON r.id = c.region_id ORDER BY r.name, c.name", ()),
        ("List orders with their customer names.", "SELECT o.order_number AS order_ref, c.name AS customer_name FROM orders AS o INNER JOIN customers AS c ON c.id = o.customer_id ORDER BY o.order_number", ()),
        ("List orders and the representative responsible for them.", "SELECT o.order_number AS order_ref, sr.name AS representative_name FROM orders AS o INNER JOIN sales_representatives AS sr ON sr.id = o.sales_rep_id ORDER BY o.order_number", ()),
        ("List catalog items beside the units recorded on each line.", "SELECT p.name AS product_name, oi.quantity AS line_units FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id ORDER BY p.name, oi.id", ()),
        ("List payments with the related order number.", "SELECT pay.id AS payment_key, o.order_number AS order_ref, pay.amount AS paid_amount FROM payments AS pay INNER JOIN orders AS o ON o.id = pay.order_id ORDER BY pay.id", ()),
        ("List refunds with their related order number.", "SELECT ref.id AS refund_key, o.order_number AS order_ref, ref.amount AS refund_amount FROM refunds AS ref INNER JOIN orders AS o ON o.id = ref.order_id ORDER BY ref.id", ()),
        ("List representatives and their regions.", "SELECT sr.name AS representative_name, r.name AS region_name FROM sales_representatives AS sr INNER JOIN regions AS r ON r.id = sr.region_id ORDER BY sr.name", ()),
        ("List order lines with product names and order numbers.", "SELECT oi.id AS line_key, p.name AS product_name, o.order_number AS order_ref FROM order_items AS oi INNER JOIN products AS p ON p.id = oi.product_id INNER JOIN orders AS o ON o.id = oi.order_id ORDER BY oi.id", ()),
        ("List customers and the total of each order.", "SELECT c.name AS customer_name, o.order_number AS order_ref, o.total_amount AS order_total FROM customers AS c INNER JOIN orders AS o ON o.customer_id = c.id ORDER BY c.name, o.order_number", ()),
        ("List a payment status beside the customer for its order.", "SELECT pay.status AS payment_status, c.name AS customer_name FROM payments AS pay INNER JOIN orders AS o ON o.id = pay.order_id INNER JOIN customers AS c ON c.id = o.customer_id ORDER BY pay.id", ()),
        ("List refund reasons beside product lines from the same order.", "SELECT ref.reason AS refund_reason, oi.id AS line_key FROM refunds AS ref INNER JOIN order_items AS oi ON oi.order_id = ref.order_id ORDER BY ref.id, oi.id", ()),
        ("Show each product category beside its line quantity.", "SELECT p.category AS product_category, oi.quantity AS line_units FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id ORDER BY p.category, oi.id", ()),
        ("Show each customer and representative pair.", "SELECT c.name AS customer_name, sr.name AS representative_name FROM customers AS c INNER JOIN sales_representatives AS sr ON sr.id = c.sales_rep_id ORDER BY c.name", ()),
        ("Show order currency beside payment amount.", "SELECT o.currency AS order_currency, pay.amount AS paid_amount FROM orders AS o INNER JOIN payments AS pay ON pay.order_id = o.id ORDER BY o.id, pay.id", ()),
        ("Show order status beside refund amount.", "SELECT o.status AS order_status, ref.amount AS refund_amount FROM orders AS o INNER JOIN refunds AS ref ON ref.order_id = o.id ORDER BY o.id, ref.id", ()),
        ("Show regional customer names and order numbers.", "SELECT r.name AS region_name, c.name AS customer_name, o.order_number AS order_ref FROM regions AS r INNER JOIN customers AS c ON c.region_id = r.id INNER JOIN orders AS o ON o.customer_id = c.id ORDER BY r.name, c.name, o.order_number", ()),
        ("Show representative regions with order totals.", "SELECT r.name AS region_name, o.total_amount AS order_total FROM regions AS r INNER JOIN sales_representatives AS sr ON sr.region_id = r.id INNER JOIN orders AS o ON o.sales_rep_id = sr.id ORDER BY r.name, o.id", ()),
        ("Show product names with payment statuses for their orders.", "SELECT p.name AS product_name, pay.status AS payment_status FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id INNER JOIN payments AS pay ON pay.order_id = oi.order_id ORDER BY p.name, pay.id", ()),
        ("Show customers with refund reasons.", "SELECT c.name AS customer_name, ref.reason AS refund_reason FROM customers AS c INNER JOIN orders AS o ON o.customer_id = c.id INNER JOIN refunds AS ref ON ref.order_id = o.id ORDER BY c.name, ref.id", ()),
        ("Show order numbers with the count of their line records.", "SELECT o.order_number AS order_ref, COUNT(oi.id) AS line_count FROM orders AS o INNER JOIN order_items AS oi ON oi.order_id = o.id GROUP BY o.id, o.order_number ORDER BY o.order_number", ()),
        ("Show representative names with customer counts.", "SELECT sr.name AS representative_name, COUNT(c.id) AS customer_count FROM sales_representatives AS sr LEFT JOIN customers AS c ON c.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name", ()),
        ("Show region names with customer counts.", "SELECT r.name AS region_name, COUNT(c.id) AS customer_count FROM regions AS r LEFT JOIN customers AS c ON c.region_id = r.id GROUP BY r.id, r.name ORDER BY r.name", ()),
        ("Show product names with their average line price.", "SELECT p.name AS product_name, AVG(oi.unit_price) AS average_line_price FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY p.name", ()),
        ("Show order numbers with paid totals.", "SELECT o.order_number AS order_ref, SUM(pay.amount) AS paid_total FROM orders AS o INNER JOIN payments AS pay ON pay.order_id = o.id GROUP BY o.id, o.order_number ORDER BY o.order_number", ()),
    ]

    rows["AGGREGATE_GROUP"] = [
        ("How many orders are in each status?", "SELECT o.status AS order_status, COUNT(o.id) AS order_count FROM orders AS o GROUP BY o.status ORDER BY o.status", ()),
        ("What is the total order value by currency?", "SELECT o.currency AS order_currency, SUM(o.total_amount) AS order_value FROM orders AS o GROUP BY o.currency ORDER BY o.currency", ()),
        ("What is the average listed price by product category?", "SELECT p.category AS product_category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category ORDER BY p.category", ()),
        ("Give the number of catalog entries for every product category.", "SELECT p.category AS product_category, COUNT(p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category", ()),
        ("What is the largest order total for each status?", "SELECT o.status AS order_status, MAX(o.total_amount) AS largest_total FROM orders AS o GROUP BY o.status ORDER BY o.status", ()),
        ("What is the smallest payment by payment status?", "SELECT pay.status AS payment_status, MIN(pay.amount) AS smallest_payment FROM payments AS pay GROUP BY pay.status ORDER BY pay.status", ()),
        ("What is the refund total by reason?", "SELECT ref.reason AS refund_reason, SUM(ref.amount) AS refund_total FROM refunds AS ref GROUP BY ref.reason ORDER BY ref.reason", ()),
        ("Give the mean discount amount for each order lifecycle status.", "SELECT o.status AS order_status, AVG(o.discount_amount) AS average_discount FROM orders AS o GROUP BY o.status ORDER BY o.status", ()),
        ("How many order lines belong to each product?", "SELECT oi.product_id AS product_key, COUNT(oi.id) AS line_count FROM order_items AS oi GROUP BY oi.product_id ORDER BY oi.product_id", ()),
        ("What quantity was ordered for every product?", "SELECT oi.product_id AS product_key, SUM(oi.quantity) AS units_ordered FROM order_items AS oi GROUP BY oi.product_id ORDER BY oi.product_id", ()),
        ("What is the average order subtotal by currency?", "SELECT o.currency AS order_currency, AVG(o.subtotal) AS average_subtotal FROM orders AS o GROUP BY o.currency ORDER BY o.currency", ()),
        ("What is the maximum refund by reason?", "SELECT ref.reason AS refund_reason, MAX(ref.amount) AS largest_refund FROM refunds AS ref GROUP BY ref.reason ORDER BY ref.reason", ()),
        ("How many payments have each status?", "SELECT pay.status AS payment_status, COUNT(pay.id) AS payment_count FROM payments AS pay GROUP BY pay.status ORDER BY pay.status", ()),
        ("What is the minimum product price in every category?", "SELECT p.category AS product_category, MIN(p.unit_price) AS minimum_price FROM products AS p GROUP BY p.category ORDER BY p.category", ()),
        ("What is completed order revenue by representative?", "SELECT o.sales_rep_id AS representative_key, SUM(o.total_amount) AS completed_revenue FROM orders AS o WHERE o.status = 'completed' GROUP BY o.sales_rep_id ORDER BY o.sales_rep_id", ("completed",)),
        ("What is the order count by customer region?", "SELECT c.region_id AS region_key, COUNT(o.id) AS order_count FROM customers AS c INNER JOIN orders AS o ON o.customer_id = c.id GROUP BY c.region_id ORDER BY c.region_id", ()),
        ("What is the average line quantity by product category?", "SELECT p.category AS product_category, AVG(oi.quantity) AS average_units FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category", ()),
        ("What is the total payment amount by status?", "SELECT pay.status AS payment_status, SUM(pay.amount) AS payment_total FROM payments AS pay GROUP BY pay.status ORDER BY pay.status", ()),
        ("What is the total discount by currency?", "SELECT o.currency AS order_currency, SUM(o.discount_amount) AS discount_total FROM orders AS o GROUP BY o.currency ORDER BY o.currency", ()),
        ("How many refunds are attached to each order?", "SELECT ref.order_id AS order_key, COUNT(ref.id) AS refund_count FROM refunds AS ref GROUP BY ref.order_id ORDER BY ref.order_id", ()),
        ("What is the average payment amount by currency?", "SELECT o.currency AS order_currency, AVG(pay.amount) AS average_payment FROM orders AS o INNER JOIN payments AS pay ON pay.order_id = o.id GROUP BY o.currency ORDER BY o.currency", ()),
        ("What is the total quantity by category?", "SELECT p.category AS product_category, SUM(oi.quantity) AS units_ordered FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category", ()),
        ("What is the highest order total by representative?", "SELECT o.sales_rep_id AS representative_key, MAX(o.total_amount) AS largest_order FROM orders AS o GROUP BY o.sales_rep_id ORDER BY o.sales_rep_id", ()),
        ("How many distinct customers ordered in each region?", "SELECT c.region_id AS region_key, COUNT(DISTINCT c.id) AS customer_count FROM customers AS c INNER JOIN orders AS o ON o.customer_id = c.id GROUP BY c.region_id ORDER BY c.region_id", ()),
    ]

    rows["ORDER_TOPN"] = [
        (f"Which {noun} are highest by {measure}?", sql, ())
        for noun, measure, sql in (
            ("orders", "total", "SELECT o.order_number AS order_ref, o.total_amount AS order_total FROM orders AS o ORDER BY o.total_amount DESC, o.id LIMIT 3"),
            ("products", "price", "SELECT p.name AS product_name, p.unit_price AS listed_price FROM products AS p ORDER BY p.unit_price DESC, p.id LIMIT 5"),
            ("payments", "amount", "SELECT pay.id AS payment_key, pay.amount AS payment_amount FROM payments AS pay ORDER BY pay.amount DESC, pay.id LIMIT 4"),
            ("refunds", "amount", "SELECT ref.id AS refund_key, ref.amount AS refund_amount FROM refunds AS ref ORDER BY ref.amount DESC, ref.id LIMIT 4"),
            ("customers", "identifier", "SELECT c.name AS customer_name, c.id AS customer_key FROM customers AS c ORDER BY c.id DESC LIMIT 6"),
            ("orders", "discount", "SELECT o.order_number AS order_ref, o.discount_amount AS discount_value FROM orders AS o ORDER BY o.discount_amount DESC, o.id LIMIT 5"),
            ("products", "name", "SELECT p.name AS product_name, p.sku AS product_sku FROM products AS p ORDER BY p.name ASC, p.id LIMIT 7"),
            ("orders", "subtotal", "SELECT o.order_number AS order_ref, o.subtotal AS order_subtotal FROM orders AS o ORDER BY o.subtotal ASC, o.id LIMIT 4"),
            ("customers", "order value", "SELECT c.name AS customer_name, SUM(o.total_amount) AS customer_value FROM customers AS c INNER JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY customer_value DESC, c.id LIMIT 5"),
            ("representatives", "order value", "SELECT sr.name AS representative_name, SUM(o.total_amount) AS representative_value FROM sales_representatives AS sr INNER JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY representative_value DESC, sr.id LIMIT 3"),
            ("categories", "units", "SELECT p.category AS product_category, SUM(oi.quantity) AS units_ordered FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY units_ordered DESC, p.category LIMIT 3"),
            ("statuses", "payment value", "SELECT pay.status AS payment_status, SUM(pay.amount) AS payment_total FROM payments AS pay GROUP BY pay.status ORDER BY payment_total DESC, pay.status LIMIT 2"),
            ("regions", "customers", "SELECT r.name AS region_name, COUNT(c.id) AS customer_count FROM regions AS r INNER JOIN customers AS c ON c.region_id = r.id GROUP BY r.id, r.name ORDER BY customer_count DESC, r.id LIMIT 3"),
            ("categories", "average price", "SELECT p.category AS product_category, AVG(p.unit_price) AS average_price FROM products AS p GROUP BY p.category ORDER BY average_price DESC, p.category LIMIT 2"),
        )
    ]

    rows["FORMULA_RATIO"] = [
        ("What is the net subtotal after discount for each order?", "SELECT o.order_number AS order_ref, o.subtotal - o.discount_amount AS net_subtotal FROM orders AS o ORDER BY o.order_number", ()),
        ("What is the extended amount for each order line?", "SELECT oi.id AS line_key, oi.quantity * oi.unit_price AS extended_amount FROM order_items AS oi ORDER BY oi.id", ()),
        ("What is the net line amount after line discount?", "SELECT oi.id AS line_key, oi.quantity * oi.unit_price - oi.discount_amount AS net_line_amount FROM order_items AS oi ORDER BY oi.id", ()),
        ("What percentage of subtotal is discounted on each order?", "SELECT o.order_number AS order_ref, CASE WHEN o.subtotal = 0 THEN 0 ELSE CAST(o.discount_amount AS numeric) / o.subtotal * 100 END AS discount_percent FROM orders AS o ORDER BY o.order_number", ()),
        ("What percentage of total value does each order represent?", "SELECT o.order_number AS order_ref, CAST(o.total_amount AS numeric) / (SELECT SUM(i.total_amount) FROM orders AS i) * 100 AS value_percent FROM orders AS o ORDER BY o.order_number", ()),
        ("What is each refund as a percentage of its order total?", "SELECT ref.id AS refund_key, CAST(ref.amount AS numeric) / o.total_amount * 100 AS refund_percent FROM refunds AS ref INNER JOIN orders AS o ON o.id = ref.order_id ORDER BY ref.id", ()),
        ("What is each payment after a two percent fee?", "SELECT pay.id AS payment_key, pay.amount - pay.amount * 0.02 AS net_payment FROM payments AS pay ORDER BY pay.id", ()),
        ("What is the price gap between a product and its line price?", "SELECT p.name AS product_name, p.unit_price - oi.unit_price AS price_gap FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id ORDER BY p.name, oi.id", ()),
        ("What is the average order amount per line by currency?", "SELECT o.currency AS order_currency, CAST(SUM(o.total_amount) AS numeric) / COUNT(oi.id) AS amount_per_line FROM orders AS o INNER JOIN order_items AS oi ON oi.order_id = o.id GROUP BY o.currency ORDER BY o.currency", ()),
        ("What is the discount amount per order line by status?", "SELECT o.status AS order_status, CAST(SUM(o.discount_amount) AS numeric) / COUNT(o.id) AS discount_per_order FROM orders AS o GROUP BY o.status ORDER BY o.status", ()),
        ("What is refund value less than the associated subtotal?", "SELECT ref.id AS refund_key, o.subtotal - ref.amount AS remaining_subtotal FROM refunds AS ref INNER JOIN orders AS o ON o.id = ref.order_id ORDER BY ref.id", ()),
        ("What is the order total after a three percent service charge?", "SELECT o.order_number AS order_ref, o.total_amount * 1.03 AS charged_total FROM orders AS o ORDER BY o.order_number", ()),
        ("What is the line discount per unit?", "SELECT oi.id AS line_key, CAST(oi.discount_amount AS numeric) / oi.quantity AS discount_per_unit FROM order_items AS oi ORDER BY oi.id", ()),
        ("What is each order total minus its subtotal?", "SELECT o.order_number AS order_ref, o.total_amount - o.subtotal AS total_gap FROM orders AS o ORDER BY o.order_number", ()),
        ("What is payment value relative to the order total?", "SELECT pay.id AS payment_key, CAST(pay.amount AS numeric) / o.total_amount AS payment_ratio FROM payments AS pay INNER JOIN orders AS o ON o.id = pay.order_id ORDER BY pay.id", ()),
        ("What is the average line value by product category?", "SELECT p.category AS product_category, AVG(oi.quantity * oi.unit_price) AS average_line_value FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category", ()),
        ("What is the completed revenue share by currency?", "SELECT o.currency AS order_currency, CAST(SUM(o.total_amount) AS numeric) / (SELECT SUM(i.total_amount) FROM orders AS i WHERE i.status = 'completed') AS revenue_share FROM orders AS o WHERE o.status = 'completed' GROUP BY o.currency ORDER BY o.currency", ("completed",)),
        ("What is each product's line quantity times its price?", "SELECT p.name AS product_name, SUM(oi.quantity * oi.unit_price) AS product_value FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY p.name", ()),
    ]

    rows["TEMPORAL"] = [
        ("How many orders were placed in each month of 2025?", "SELECT DATE_TRUNC('month', o.ordered_at) AS order_month, COUNT(o.id) AS order_count FROM orders AS o WHERE o.ordered_at >= TIMESTAMP '2025-01-01' AND o.ordered_at < TIMESTAMP '2026-01-01' GROUP BY DATE_TRUNC('month', o.ordered_at) ORDER BY order_month", ()),
        ("What is completed revenue by order month?", "SELECT DATE_TRUNC('month', o.ordered_at) AS order_month, SUM(o.total_amount) AS completed_revenue FROM orders AS o WHERE o.status = 'completed' GROUP BY DATE_TRUNC('month', o.ordered_at) ORDER BY order_month", ("completed",)),
        ("How many payments were recorded by month?", "SELECT DATE_TRUNC('month', pay.paid_at) AS payment_month, COUNT(pay.id) AS payment_count FROM payments AS pay GROUP BY DATE_TRUNC('month', pay.paid_at) ORDER BY payment_month", ()),
        ("What is payment value by payment month?", "SELECT DATE_TRUNC('month', pay.paid_at) AS payment_month, SUM(pay.amount) AS payment_total FROM payments AS pay GROUP BY DATE_TRUNC('month', pay.paid_at) ORDER BY payment_month", ()),
        ("How many refunds occurred by month?", "SELECT DATE_TRUNC('month', ref.refunded_at) AS refund_month, COUNT(ref.id) AS refund_count FROM refunds AS ref GROUP BY DATE_TRUNC('month', ref.refunded_at) ORDER BY refund_month", ()),
        ("What is refunded value by refund month?", "SELECT DATE_TRUNC('month', ref.refunded_at) AS refund_month, SUM(ref.amount) AS refund_total FROM refunds AS ref GROUP BY DATE_TRUNC('month', ref.refunded_at) ORDER BY refund_month", ()),
        ("How many orders were created in each quarter?", "SELECT EXTRACT(QUARTER FROM o.ordered_at) AS order_quarter, COUNT(o.id) AS order_count FROM orders AS o GROUP BY EXTRACT(QUARTER FROM o.ordered_at) ORDER BY order_quarter", ()),
        ("What is average order value by order month?", "SELECT DATE_TRUNC('month', o.ordered_at) AS order_month, AVG(o.total_amount) AS average_order_value FROM orders AS o GROUP BY DATE_TRUNC('month', o.ordered_at) ORDER BY order_month", ()),
        ("How many completed orders were placed after March?", "SELECT COUNT(o.id) AS order_count FROM orders AS o WHERE o.status = 'completed' AND o.ordered_at >= TIMESTAMP '2025-04-01'", ("completed", "2025-04-01")),
        ("What is total discount by order month?", "SELECT DATE_TRUNC('month', o.ordered_at) AS order_month, SUM(o.discount_amount) AS discount_total FROM orders AS o GROUP BY DATE_TRUNC('month', o.ordered_at) ORDER BY order_month", ()),
        ("How many orders fall on each weekday number?", "SELECT EXTRACT(DOW FROM o.ordered_at) AS weekday_number, COUNT(o.id) AS order_count FROM orders AS o GROUP BY EXTRACT(DOW FROM o.ordered_at) ORDER BY weekday_number", ()),
        ("What is the earliest order time by status?", "SELECT o.status AS order_status, MIN(o.ordered_at) AS first_order_time FROM orders AS o GROUP BY o.status ORDER BY o.status", ()),
        ("What is the latest payment time by status?", "SELECT pay.status AS payment_status, MAX(pay.paid_at) AS last_payment_time FROM payments AS pay GROUP BY pay.status ORDER BY pay.status", ()),
        ("What is the first refund time by reason?", "SELECT ref.reason AS refund_reason, MIN(ref.refunded_at) AS first_refund_time FROM refunds AS ref GROUP BY ref.reason ORDER BY ref.reason", ()),
    ]

    rows["WINDOW"] = [
        ("Show each order with its overall running total.", "SELECT o.order_number AS order_ref, o.ordered_at AS order_time, SUM(o.total_amount) OVER (ORDER BY o.ordered_at, o.id) AS running_total FROM orders AS o ORDER BY o.ordered_at, o.id", ()),
        ("Rank orders by total value from largest to smallest.", "SELECT o.order_number AS order_ref, o.total_amount AS order_total, RANK() OVER (ORDER BY o.total_amount DESC) AS value_rank FROM orders AS o ORDER BY value_rank, o.order_number", ()),
        ("Rank products by price within their category.", "SELECT p.name AS product_name, p.category AS product_category, RANK() OVER (PARTITION BY p.category ORDER BY p.unit_price DESC) AS category_rank FROM products AS p ORDER BY p.category, category_rank, p.name", ()),
        ("Show each order and the previous order total by time.", "SELECT o.order_number AS order_ref, o.total_amount AS order_total, LAG(o.total_amount) OVER (ORDER BY o.ordered_at, o.id) AS prior_total FROM orders AS o ORDER BY o.ordered_at, o.id", ()),
        ("Show each payment and a running payment sum.", "SELECT pay.id AS payment_key, pay.paid_at AS payment_time, SUM(pay.amount) OVER (ORDER BY pay.paid_at, pay.id) AS running_payment_total FROM payments AS pay ORDER BY pay.paid_at, pay.id", ()),
        ("Rank representatives by total order value.", "SELECT sr.name AS representative_name, SUM(o.total_amount) AS order_value, DENSE_RANK() OVER (ORDER BY SUM(o.total_amount) DESC) AS value_rank FROM sales_representatives AS sr INNER JOIN orders AS o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY value_rank, sr.name", ()),
        ("Show each refund with the next refund amount in time.", "SELECT ref.id AS refund_key, ref.amount AS refund_amount, LEAD(ref.amount) OVER (ORDER BY ref.refunded_at, ref.id) AS next_amount FROM refunds AS ref ORDER BY ref.refunded_at, ref.id", ()),
        ("Number customers within each region by customer name.", "SELECT c.name AS customer_name, c.region_id AS region_key, ROW_NUMBER() OVER (PARTITION BY c.region_id ORDER BY c.name, c.id) AS region_position FROM customers AS c ORDER BY c.region_id, region_position", ()),
        ("Show order totals with a two-row moving sum.", "SELECT o.order_number AS order_ref, SUM(o.total_amount) OVER (ORDER BY o.ordered_at, o.id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS moving_total FROM orders AS o ORDER BY o.ordered_at, o.id", ()),
        ("Rank payments within each status by amount.", "SELECT pay.id AS payment_key, pay.status AS payment_status, RANK() OVER (PARTITION BY pay.status ORDER BY pay.amount DESC) AS status_rank FROM payments AS pay ORDER BY pay.status, status_rank, pay.id", ()),
        ("Show each product and the maximum price in its category.", "SELECT p.name AS product_name, p.category AS product_category, MAX(p.unit_price) OVER (PARTITION BY p.category) AS category_max_price FROM products AS p ORDER BY p.category, p.name", ()),
        ("Show each order and its customer order sequence.", "SELECT o.order_number AS order_ref, o.customer_id AS customer_key, ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS customer_order_number FROM orders AS o ORDER BY o.customer_id, customer_order_number", ()),
        ("Show each order with the average total across all orders.", "SELECT o.order_number AS order_ref, o.total_amount AS order_total, AVG(o.total_amount) OVER () AS overall_average FROM orders AS o ORDER BY o.order_number", ()),
        ("Rank refunds within each reason by amount.", "SELECT ref.id AS refund_key, ref.reason AS refund_reason, RANK() OVER (PARTITION BY ref.reason ORDER BY ref.amount DESC) AS reason_rank FROM refunds AS ref ORDER BY ref.reason, reason_rank, ref.id", ()),
    ]

    rows["DISTINCT_OR_SET"] = [
        ("Count the unique customer identifiers represented in orders.", "SELECT COUNT(DISTINCT o.customer_id) AS distinct_customer_count FROM orders AS o", ()),
        ("How many distinct products have order lines?", "SELECT COUNT(DISTINCT oi.product_id) AS distinct_product_count FROM order_items AS oi", ()),
        ("How many distinct orders have payments?", "SELECT COUNT(DISTINCT pay.order_id) AS distinct_paid_orders FROM payments AS pay", ()),
        ("How many distinct orders have refunds?", "SELECT COUNT(DISTINCT ref.order_id) AS distinct_refund_orders FROM refunds AS ref", ()),
        ("Count distinct customers by region.", "SELECT c.region_id AS region_key, COUNT(DISTINCT c.id) AS distinct_customer_count FROM customers AS c GROUP BY c.region_id ORDER BY c.region_id", ()),
        ("Count distinct products in each category's order lines.", "SELECT p.category AS product_category, COUNT(DISTINCT oi.product_id) AS distinct_product_count FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category", ()),
        ("Count distinct payment orders by payment status.", "SELECT pay.status AS payment_status, COUNT(DISTINCT pay.order_id) AS distinct_order_count FROM payments AS pay GROUP BY pay.status ORDER BY pay.status", ()),
        ("Count distinct refund orders by refund reason.", "SELECT ref.reason AS refund_reason, COUNT(DISTINCT ref.order_id) AS distinct_order_count FROM refunds AS ref GROUP BY ref.reason ORDER BY ref.reason", ()),
    ]

    rows["MIXED_COMPOSITION"] = [
        ("Which completed regions have at least five orders, with their revenue?", "SELECT r.name AS region_name, SUM(o.total_amount) AS completed_revenue FROM regions AS r INNER JOIN customers AS c ON c.region_id = r.id INNER JOIN orders AS o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY r.id, r.name HAVING COUNT(o.id) >= 5 ORDER BY completed_revenue DESC, r.name", ("completed", "5")),
        ("What are the top customer totals among completed orders?", "SELECT c.name AS customer_name, SUM(o.total_amount) AS completed_value FROM customers AS c INNER JOIN orders AS o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id, c.name ORDER BY completed_value DESC, c.name LIMIT 5", ("completed",)),
        ("Show each category's completed units and average price.", "SELECT p.category AS product_category, SUM(oi.quantity) AS completed_units, AVG(oi.unit_price) AS average_line_price FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id INNER JOIN orders AS o ON o.id = oi.order_id WHERE o.status = 'completed' GROUP BY p.category ORDER BY p.category", ("completed",)),
        ("Show monthly completed orders with their rank by count.", "SELECT DATE_TRUNC('month', o.ordered_at) AS order_month, COUNT(o.id) AS completed_orders, RANK() OVER (ORDER BY COUNT(o.id) DESC) AS volume_rank FROM orders AS o WHERE o.status = 'completed' GROUP BY DATE_TRUNC('month', o.ordered_at) ORDER BY volume_rank, order_month", ("completed",)),
        ("Show representatives with completed revenue and order rank.", "SELECT sr.name AS representative_name, SUM(o.total_amount) AS completed_revenue, RANK() OVER (ORDER BY SUM(o.total_amount) DESC) AS revenue_rank FROM sales_representatives AS sr INNER JOIN orders AS o ON o.sales_rep_id = sr.id WHERE o.status = 'completed' GROUP BY sr.id, sr.name ORDER BY revenue_rank, sr.name", ("completed",)),
        ("Show payment status totals and their share of all payment value.", "SELECT pay.status AS payment_status, SUM(pay.amount) AS payment_total, CAST(SUM(pay.amount) AS numeric) / (SELECT SUM(i.amount) FROM payments AS i) AS payment_share FROM payments AS pay GROUP BY pay.status ORDER BY pay.status", ()),
        ("Which product categories exceed 30 units and show their average price?", "SELECT p.category AS product_category, SUM(oi.quantity) AS units_ordered, AVG(p.unit_price) AS average_price FROM products AS p INNER JOIN order_items AS oi ON oi.product_id = p.id GROUP BY p.category HAVING SUM(oi.quantity) > 30 ORDER BY units_ordered DESC, p.category", ("30",)),
        ("Show customers with order count and last order time.", "SELECT c.name AS customer_name, COUNT(o.id) AS order_count, MAX(o.ordered_at) AS last_order_time FROM customers AS c INNER JOIN orders AS o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY order_count DESC, c.name", ()),
        ("Show refund reasons with totals ranked by refund value.", "SELECT ref.reason AS refund_reason, SUM(ref.amount) AS refund_total, RANK() OVER (ORDER BY SUM(ref.amount) DESC) AS refund_rank FROM refunds AS ref GROUP BY ref.reason ORDER BY refund_rank, ref.reason", ()),
        ("Show order status counts and average totals.", "SELECT o.status AS order_status, COUNT(o.id) AS order_count, AVG(o.total_amount) AS average_total FROM orders AS o GROUP BY o.status ORDER BY o.status", ()),
    ]
    return rows


def _governed_cases() -> list[M10Case]:
    catalog = build_m3_catalog()
    if tuple(metric.name for metric in catalog.metrics) != GOVERNED_METRICS:
        raise ValueError("active governed metric catalog does not match the frozen ten metrics")
    compiler = MetricCompiler(catalog)
    dimensions = {
        "completed_revenue": ((), ("customer",), ("sales_representative",), ("order_currency",), ("customer", "order_currency")),
        "average_completed_order_value": ((), ("customer",), ("sales_representative",), ("order_currency",), ("customer", "order_currency")),
        "average_payment_amount": ((), ("customer",), ("sales_representative",), ("order_currency",), ("customer", "order_currency")),
        "completed_item_quantity": ((), ("product_category",), ("customer",), ("sales_representative",), ("product_category", "order_currency")),
        "payment_success_rate": ((), ("customer",), ("sales_representative",), ("order_currency",), ("customer", "order_currency")),
        "refunded_order_rate": ((), ("customer",), ("sales_representative",), ("order_currency",), ("customer", "order_currency")),
        "refund_to_revenue_rate": ((), ("customer",), ("sales_representative",), ("order_currency",), ("customer", "order_currency")),
        "average_items_per_completed_order": ((), ("product_category",), ("customer",), ("sales_representative",), ("product_category", "order_currency")),
        "completed_order_rate": ((), ("customer",), ("sales_representative",), ("order_currency",), ("customer", "order_currency")),
        "customer_order_coverage_rate": ((), ("customer",), ("sales_representative",), ("customer", "sales_representative"), ()),
    }
    result: list[M10Case] = []
    for metric_index, metric in enumerate(GOVERNED_METRICS):
        for ordinal, dims in enumerate(dimensions[metric]):
            compiled = compiler.compile_metric(MetricRequest(metric_name=metric, dimensions=dims))
            if not hasattr(compiled, "sql"):
                raise ValueError(f"governed reference did not compile: {metric}/{dims}")
            phrase = metric.replace("_", " ")
            dimension_phrase = " and ".join(dims) if dims else "the complete business"
            sql = f"/* m10 governed fixture {metric_index:02d}-{ordinal:02d} */\n{compiled.sql}"
            result.append(
                M10Case(
                    case_id=f"m10-gov-{metric_index:02d}-{ordinal:02d}",
                    question=f"Using the governed catalog, report {phrase} grouped by {dimension_phrase} for metric variation {ordinal + 1}.",
                    split="dev" if ordinal < 3 else "holdout",
                    expected_route="GOVERNED",
                    family="GOVERNED_METRIC",
                    reference_sql_variants=(sql,),
                    semantic_ids=(f"metric:{metric}", *[f"dimension:{item}" for item in dims]),
                    literal_values=(),
                    governed_metric=metric,
                    dimensions=dims,
                )
            )
    return result


def build_cases() -> tuple[M10Case, ...]:
    result = _governed_cases()
    direct = _direct_rows()
    for family in DIRECT_COUNTS:
        rows = direct[family]
        if len(rows) != DIRECT_COUNTS[family]:
            raise ValueError(f"{family} has {len(rows)} rows, expected {DIRECT_COUNTS[family]}")
        for ordinal, (question, sql, literals) in enumerate(rows):
            result.append(
                M10Case(
                    case_id=f"m10-direct-{family.lower()}-{ordinal:03d}",
                    question=question,
                    split="dev" if ordinal < DEV_DIRECT_COUNTS[family] else "holdout",
                    expected_route="DIRECT",
                    family=family,
                    reference_sql_variants=(sql,),
                    semantic_ids=(),
                    literal_values=literals,
                )
            )
    if len(result) != 200:
        raise ValueError(f"M10 requires 200 cases, got {len(result)}")
    return tuple(result)


def _select_expressions(sql: str) -> tuple[exp.Expression, ...]:
    statement = parse_one(sql, read="postgres")
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        raise ValueError(f"SQL has no SELECT: {sql}")
    return tuple(select.expressions)


def _contract_and_spec(sql: str, case_id: str) -> tuple[ResultEquivalenceContract, ResultBindingSpecV2]:
    schema_map = build_schema_semantic_map()
    proofs = projection_proofs(sql, schema_map)
    slots: list[ResultSlot] = []
    specs: list[SlotBindingSpecV2] = []
    for index, (fingerprint, _leaves, _method, _error) in enumerate(proofs):
        slot_id = f"{case_id}:slot:{index}"
        identity = f"semantic:{canonical_hash(fingerprint)}" if fingerprint is not None else f"unresolved:{case_id}:{index}"
        slots.append(ResultSlot(slot_id, SlotKind.MEASURE, True, "output", identity))
        specs.append(
            SlotBindingSpecV2(
                slot_id=slot_id,
                semantic_identity=identity,
                expression_fingerprint=fingerprint,
                allowed_expression_class="ANALYTIC_EXPRESSION",
                required=True,
            )
        )
    contract = ResultEquivalenceContract(
        contract_version=CONTRACT_VERSION,
        slots=tuple(slots),
        row_identity_slots=(),
        required_measure_slots=tuple(item.slot_id for item in slots),
        scalar_or_tabular=ScalarOrTabular.TABULAR,
        row_order_policy=RowOrderPolicy.ORDER_INSENSITIVE,
        duplicate_policy=DuplicatePolicy.PRESERVE,
        extra_output_policy=ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
        notes="M10 benchmark-owned contract; candidate binding remains reference-blind.",
    )
    spec = ResultBindingSpecV2(
        binding_protocol_version="benchmark-result-binding-v2",
        contract_instance_hash=contract_instance_hash(contract),
        semantic_catalog_version="m3-semantic-catalog-v1",
        schema_semantic_map_version=schema_map.version,
        slot_binding_specs=tuple(specs),
    )
    return contract, spec


def _case_dict(case: M10Case) -> dict[str, Any]:
    contract, spec = _contract_and_spec(case.reference_sql, case.case_id)
    return {
        "case_id": case.case_id,
        "question": case.question,
        "split": case.split,
        "expected_route": case.expected_route,
        "family": case.family,
        "reference_sql_variants": list(case.reference_sql_variants),
        "semantic_ids": list(case.semantic_ids),
        "literal_values": list(case.literal_values),
        "governed_metric": case.governed_metric,
        "dimensions": list(case.dimensions),
        "contract_id": f"m10-contract-{case.case_id}",
        "contract": {
            "contract_version": contract.contract_version,
            "slots": [_jsonable(item.__dict__) for item in contract.slots],
            "row_identity_slots": list(contract.row_identity_slots),
            "required_measure_slots": list(contract.required_measure_slots),
            "scalar_or_tabular": contract.scalar_or_tabular.value,
            "row_order_policy": contract.row_order_policy.value,
            "duplicate_policy": contract.duplicate_policy.value,
            "extra_output_policy": contract.extra_output_policy.value,
            "null_policy_reference": contract.null_policy_reference,
            "numeric_tolerance_reference": contract.numeric_tolerance_reference,
            "notes": contract.notes,
        },
        "binding_spec_id": f"m10-binding-spec-{case.case_id}",
        "binding_spec": {
            "binding_protocol_version": spec.binding_protocol_version,
            "contract_instance_hash": spec.contract_instance_hash,
            "semantic_catalog_version": spec.semantic_catalog_version,
            "schema_semantic_map_version": spec.schema_semantic_map_version,
            "slot_binding_specs": [_jsonable(item.__dict__) for item in spec.slot_binding_specs],
        },
    }


def offline_schema_model() -> dict[str, Any]:
    schema = build_default_catalog(Base.metadata)
    relations = []
    columns = []
    for table in schema.tables:
        for column in table.columns:
            columns.append({"table": table.name, "column": column.name, "type": column.type, "queryable": column.queryable})
            for relationship in table.relationships:
                relations.append({"table": table.name, "column": relationship.column, "referenced_table": relationship.referenced_table, "referenced_column": relationship.referenced_column})
    return {"sources": ["app/db/models.py", "alembic/versions/0001_commerce_schema.py", "app/catalog/default.py", "app/semantics/catalog.py", "demo/seed/generate.py"], "tables": [table.name for table in schema.tables], "columns": columns, "relationships": sorted(relations, key=lambda x: tuple(x.values()))}


def build_fixtures() -> dict[str, Any]:
    cases = tuple(_case_dict(case) for case in build_cases())
    recipe = construction_recipe()
    schema = offline_schema_model()
    case_ids = [item["case_id"] for item in cases]
    category_manifest = {
        "route_counts": {route: sum(item["expected_route"] == route for item in cases) for route in ("GOVERNED", "DIRECT")},
        "family_counts": {family: sum(item["family"] == family for item in cases) for family in sorted({item["family"] for item in cases})},
        "split_counts": {split: sum(item["split"] == split for item in cases) for split in ("dev", "holdout")},
    }
    corpus = {"corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION, "construction_seed": construction_seed(), "cases": cases}
    return {
        "recipe": recipe,
        "schema": schema,
        "cases": corpus,
        "references": {"corpus_id": CORPUS_ID, "cases": [{"case_id": item["case_id"], "reference_sql_variants": item["reference_sql_variants"]} for item in cases]},
        "contracts": {"corpus_id": CORPUS_ID, "cases": [{"case_id": item["case_id"], "contract_id": item["contract_id"], "contract": item["contract"]} for item in cases]},
        "binding_specs": {"corpus_id": CORPUS_ID, "cases": [{"case_id": item["case_id"], "binding_spec_id": item["binding_spec_id"], "binding_spec": item["binding_spec"]} for item in cases]},
        "category_manifest": category_manifest,
        "case_id_hash": sha256("\n".join(case_ids).encode()).hexdigest(),
        "corpus_content_hash": stable_hash(corpus),
    }


def write_fixtures() -> dict[str, Any]:
    payload = build_fixtures()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    files = {
        "m10_construction_recipe.json": payload["recipe"],
        "m10_offline_schema_model.json": payload["schema"],
        "m10_cases.json": payload["cases"],
        "m10_references.json": payload["references"],
        "m10_result_contracts.json": payload["contracts"],
        "m10_binding_specs.json": payload["binding_specs"],
        "m10_category_manifest.json": payload["category_manifest"],
    }
    for name, value in files.items():
        (FIXTURES / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return payload


if __name__ == "__main__":
    print(json.dumps(write_fixtures(), indent=2, sort_keys=True))
