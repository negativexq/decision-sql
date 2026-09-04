# ruff: noqa: E501

"""Frozen, evaluation-only mixed workload for M7.

The cases are manually authored and deliberately distinct from the existing
M3/M4 question corpora.  This module contains no provider or production code.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import parse_one

from app.semantics.catalog import build_m3_catalog
from app.semantics.compiler import MetricCompiler
from app.semantics.requests import MetricRequest

Split = Literal["dev", "holdout"]
Route = Literal["GOVERNED", "DIRECT"]
Naturalness = Literal["NATURAL", "BORDERLINE"]


class CombinedProductCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    family: str = Field(min_length=1)
    expected_route: Route
    reference_sql_variants: tuple[str, ...] = Field(min_length=1)
    relevant_semantic_ids: tuple[str, ...] = ()
    relevant_schema_objects: tuple[str, ...] = Field(min_length=1)
    literal_values: tuple[str, ...] = ()
    temporal_tag: str | None = None
    naturalness: Naturalness = "NATURAL"
    split: Split
    adjudication_note: str = Field(min_length=1)

    @property
    def reference_sql(self) -> str:
        return self.reference_sql_variants[0]


class BenchmarkConstructionError(ValueError):
    pass


_METRIC_PHRASES = {
    "completed_revenue": "revenue from completed orders",
    "average_completed_order_value": "average completed order value",
    "average_payment_amount": "average payment amount",
    "completed_item_quantity": "quantity on completed orders",
    "payment_success_rate": "payment success rate",
    "refunded_order_rate": "refunded order rate",
    "refund_to_revenue_rate": "refund amount as a share of completed revenue",
    "average_items_per_completed_order": "average items per completed order",
    "completed_order_rate": "completed order rate",
    "customer_order_coverage_rate": "customer order coverage rate",
}


def build_benchmark() -> tuple[CombinedProductCase, ...]:
    """Build exactly 150 finite, deterministic cases."""
    groups: list[tuple[str, Route, list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]]] = []

    governed_specs = [
        ("completed_revenue", ()),
        ("completed_revenue", ("customer",)),
        ("completed_revenue", ("sales_representative",)),
        ("average_completed_order_value", ()),
        ("average_completed_order_value", ("customer",)),
        ("average_payment_amount", ("order_currency",)),
        ("completed_item_quantity", ("product_category",)),
        ("payment_success_rate", ()),
        ("refunded_order_rate", ("sales_representative",)),
        ("refund_to_revenue_rate", ("order_currency",)),
        ("average_items_per_completed_order", ("customer",)),
        ("completed_order_rate", ("order_currency",)),
        ("customer_order_coverage_rate", ("sales_representative",)),
        ("completed_revenue", ("order_currency",)),
        ("average_completed_order_value", ("sales_representative",)),
        ("average_payment_amount", ("customer",)),
        ("completed_item_quantity", ("sales_representative",)),
        ("payment_success_rate", ("customer",)),
        ("refunded_order_rate", ("customer",)),
        ("refund_to_revenue_rate", ("customer",)),
        ("average_items_per_completed_order", ("product_category",)),
        ("completed_order_rate", ("customer",)),
        ("customer_order_coverage_rate", ()),
        ("completed_revenue", ("customer", "order_currency")),
        ("average_completed_order_value", ("customer", "order_currency")),
        ("average_payment_amount", ("sales_representative",)),
        ("completed_item_quantity", ("customer",)),
        ("payment_success_rate", ("sales_representative",)),
        ("refunded_order_rate", ("order_currency",)),
        ("refund_to_revenue_rate", ("sales_representative",)),
    ]
    catalog = build_m3_catalog()
    compiler = MetricCompiler(catalog)
    governed_rows: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
    for _index, (metric, dimensions) in enumerate(governed_specs, start=1):
        request = MetricRequest(metric_name=metric, dimensions=dimensions)
        compiled = compiler.compile_metric(request)
        if not hasattr(compiled, "sql"):
            raise BenchmarkConstructionError(f"governed reference failed: {metric}/{dimensions}")
        dimension_text = " and ".join(dimensions) if dimensions else "the full business"
        question = (
            f"For {dimension_text}, report the governed {_METRIC_PHRASES[metric]}."
            if dimensions
            else f"Report the governed {_METRIC_PHRASES[metric]} for the entire business."
        )
        governed_rows.append((question, compiled.sql, (f"metric:{metric}",), ()))
    groups.append(("GOVERNED_METRIC", "GOVERNED", governed_rows))

    groups.extend(
        [
            ("CLEAR_DIRECT_SIMPLE", "DIRECT", _simple_cases()),
            ("CLEAR_DIRECT_COMPLEX", "DIRECT", _complex_cases()),
            ("RELATIONAL_COMPOSITION", "DIRECT", _relational_cases()),
            ("FILTER_AGGREGATION", "DIRECT", _filter_cases()),
            ("RATIO_DERIVED", "DIRECT", _ratio_cases()),
            ("ORDER_TOP_N", "DIRECT", _order_cases()),
            ("WINDOW_COMPOSITION", "DIRECT", _window_cases()),
            ("EXACT_LITERAL_VALUE_FILTER", "DIRECT", _literal_cases()),
        ]
    )

    expected_counts = {
        "GOVERNED_METRIC": 30,
        "CLEAR_DIRECT_SIMPLE": 20,
        "CLEAR_DIRECT_COMPLEX": 25,
        "RELATIONAL_COMPOSITION": 20,
        "FILTER_AGGREGATION": 15,
        "RATIO_DERIVED": 10,
        "ORDER_TOP_N": 10,
        "WINDOW_COMPOSITION": 10,
        "EXACT_LITERAL_VALUE_FILTER": 10,
    }
    cases: list[CombinedProductCase] = []
    family_index: dict[str, int] = {}
    holdout_counts = {
        "GOVERNED_METRIC": 10,
        "CLEAR_DIRECT_SIMPLE": 7,
        "CLEAR_DIRECT_COMPLEX": 8,
        "RELATIONAL_COMPOSITION": 7,
        "FILTER_AGGREGATION": 5,
        "RATIO_DERIVED": 3,
        "ORDER_TOP_N": 3,
        "WINDOW_COMPOSITION": 3,
        "EXACT_LITERAL_VALUE_FILTER": 4,
    }
    for family, route, specs in groups:
        if len(specs) != expected_counts[family]:
            raise BenchmarkConstructionError(f"{family}: expected {expected_counts[family]}, got {len(specs)}")
        holdout_start = len(specs) - holdout_counts[family]
        for local_index, (question, sql, semantic_ids, literals) in enumerate(specs):
            ordinal = family_index.get(family, 0)
            family_index[family] = ordinal + 1
            split: Split = "holdout" if local_index >= holdout_start else "dev"
            cases.append(
                CombinedProductCase(
                    case_id=f"m7-{family.lower()}-{ordinal:03d}",
                    question=question,
                    family=family,
                    expected_route=route,
                    reference_sql_variants=(sql,),
                    relevant_semantic_ids=semantic_ids,
                    relevant_schema_objects=_schema_objects_for(family, sql),
                    literal_values=literals,
                    split=split,
                    adjudication_note=f"Manually authored {family.lower()} reference for an answerable analytics request.",
                )
            )
    if len(cases) != 150 or sum(case.split == "dev" for case in cases) != 100:
        raise BenchmarkConstructionError("M7 benchmark must contain 100 DEV and 50 HOLDOUT cases")
    return tuple(cases)


def benchmark_hash(cases: tuple[CombinedProductCase, ...] | None = None) -> str:
    cases = cases or build_benchmark()
    payload = [case.model_dump(mode="json") for case in cases]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def split_hash(cases: tuple[CombinedProductCase, ...], split: Split) -> str:
    payload = [case.model_dump(mode="json") for case in cases if case.split == split]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_structure(cases: tuple[CombinedProductCase, ...] | None = None) -> dict[str, object]:
    cases = cases or build_benchmark()
    ids = [case.case_id for case in cases]
    questions = [case.question for case in cases]
    family_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    for case in cases:
        family_counts[case.family] = family_counts.get(case.family, 0) + 1
        route_counts[case.expected_route] = route_counts.get(case.expected_route, 0) + 1
        for sql in case.reference_sql_variants:
            parse_one(sql, read="postgres")
    return {
        "case_count": len(cases),
        "dev_count": sum(case.split == "dev" for case in cases),
        "holdout_count": sum(case.split == "holdout" for case in cases),
        "unique_case_ids": len(set(ids)) == len(ids),
        "unique_questions": len(set(questions)) == len(questions),
        "family_counts": family_counts,
        "route_counts": route_counts,
        "naturalness_counts": {
            status: sum(case.naturalness == status for case in cases)
            for status in ("NATURAL", "BORDERLINE")
        },
        "parse_count": len(cases),
        "passed": len(cases) == 150
        and len(set(ids)) == len(ids)
        and len(set(questions)) == len(questions)
        and all(case.naturalness == "NATURAL" for case in cases),
    }


def _specs(rows: list[tuple[str, str]]) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return [(question, sql, (), ()) for question, sql in rows]


def _simple_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return _specs([
        ("List every customer together with the name of their region.", "SELECT c.name, r.name AS region FROM customers c JOIN regions r ON r.id = c.region_id ORDER BY c.name"),
        ("Show product names, categories, and prices from the catalog.", "SELECT p.name, p.category, p.unit_price FROM products p ORDER BY p.name"),
        ("Count orders by their lifecycle status.", "SELECT o.status, COUNT(*) AS order_count FROM orders o GROUP BY o.status ORDER BY o.status"),
        ("List all payment records with their amount and status.", "SELECT p.id, p.amount, p.status FROM payments p ORDER BY p.id"),
        ("Show each refund record and its stated reason.", "SELECT r.id, r.amount, r.reason FROM refunds r ORDER BY r.id"),
        ("List sales representatives alphabetically with their region identifiers.", "SELECT sr.name, sr.region_id FROM sales_representatives sr ORDER BY sr.name"),
        ("Show order numbers and totals for orders placed in 2025.", "SELECT o.order_number, o.total_amount FROM orders o WHERE o.ordered_at >= DATE '2025-01-01' AND o.ordered_at < DATE '2026-01-01' ORDER BY o.order_number"),
        ("Count the products in each catalog category.", "SELECT p.category, COUNT(*) AS product_count FROM products p GROUP BY p.category ORDER BY p.category"),
        ("List completed order numbers in order-number order.", "SELECT o.order_number FROM orders o WHERE o.status = 'completed' ORDER BY o.order_number"),
        ("Show settled payment IDs and amounts.", "SELECT p.id, p.amount FROM payments p WHERE p.status = 'settled' ORDER BY p.id"),
        ("List customers assigned to each sales representative.", "SELECT sr.name, c.name AS customer FROM sales_representatives sr JOIN customers c ON c.sales_rep_id = sr.id ORDER BY sr.name, c.name"),
        ("Show products priced above 200.", "SELECT p.name, p.unit_price FROM products p WHERE p.unit_price > 200 ORDER BY p.unit_price DESC, p.name"),
        ("List orders whose currency is USD.", "SELECT o.order_number, o.total_amount FROM orders o WHERE o.currency = 'USD' ORDER BY o.order_number"),
        ("Count customers assigned to each region.", "SELECT r.name, COUNT(c.id) AS customer_count FROM regions r LEFT JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show item identifiers, quantities, and unit prices.", "SELECT oi.id, oi.quantity, oi.unit_price FROM order_items oi ORDER BY oi.id"),
        ("List orders with a nonzero discount.", "SELECT o.order_number, o.discount_amount FROM orders o WHERE o.discount_amount > 0 ORDER BY o.order_number"),
        ("Show customer identifiers and names.", "SELECT c.id, c.name FROM customers c ORDER BY c.id"),
        ("List all regions by name.", "SELECT r.name FROM regions r ORDER BY r.name"),
        ("Show payment dates and amounts for settled payments.", "SELECT p.paid_at, p.amount FROM payments p WHERE p.status = 'settled' ORDER BY p.paid_at, p.id"),
        ("List refund amounts in descending order.", "SELECT r.id, r.amount FROM refunds r ORDER BY r.amount DESC, r.id"),
    ])


def _complex_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return _specs([
        ("For every customer, show the count of orders and keep customers with at least four.", "SELECT c.name, COUNT(o.id) AS order_count FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name HAVING COUNT(o.id) >= 4 ORDER BY c.name"),
        ("Return products whose price is higher than the catalog average.", "SELECT p.name, p.unit_price FROM products p WHERE p.unit_price > (SELECT AVG(i.unit_price) FROM products i) ORDER BY p.unit_price DESC, p.name"),
        ("For each category, show average price and retain categories above 200.", "SELECT p.category, AVG(p.unit_price) AS average_price FROM products p GROUP BY p.category HAVING AVG(p.unit_price) > 200 ORDER BY p.category"),
        ("Rank each representative by total order value within the full representative list.", "SELECT sr.name, SUM(o.total_amount) AS order_value, RANK() OVER (ORDER BY SUM(o.total_amount) DESC) AS value_rank FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY value_rank, sr.name"),
        ("Show the latest order number for every customer that has an order.", "SELECT c.name, MAX(o.order_number) AS latest_order FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("For each order, show its item count beside the order total.", "SELECT o.order_number, o.total_amount, COUNT(oi.id) AS item_count FROM orders o JOIN order_items oi ON oi.order_id = o.id GROUP BY o.id, o.order_number, o.total_amount ORDER BY o.order_number"),
        ("Find representatives whose average completed-order total exceeds 250.", "SELECT sr.name, AVG(o.total_amount) AS average_total FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id WHERE o.status = 'completed' GROUP BY sr.id, sr.name HAVING AVG(o.total_amount) > 250 ORDER BY sr.name"),
        ("Show each region's completed revenue using the customer relationship.", "SELECT r.name, SUM(o.total_amount) AS revenue FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY r.id, r.name ORDER BY r.name"),
        ("For each product, calculate the total extended line value.", "SELECT p.name, SUM(oi.quantity * oi.unit_price - oi.discount_amount) AS extended_value FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY p.name"),
        ("List orders whose total is above their own customer's average order total.", "SELECT o.order_number, o.total_amount FROM orders o WHERE o.total_amount > (SELECT AVG(i.total_amount) FROM orders i WHERE i.customer_id = o.customer_id) ORDER BY o.order_number"),
        ("Show each product category's share of all item quantity.", "SELECT p.category, CAST(SUM(oi.quantity) AS numeric) / (SELECT SUM(quantity) FROM order_items) AS quantity_share FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category"),
        ("For each payment, show the order number and the customer's name.", "SELECT pay.id, o.order_number, c.name FROM payments pay JOIN orders o ON o.id = pay.order_id JOIN customers c ON c.id = o.customer_id ORDER BY pay.id"),
        ("Calculate running completed revenue by order date.", "SELECT o.order_number, o.ordered_at, SUM(o.total_amount) OVER (ORDER BY o.ordered_at, o.id) AS running_revenue FROM orders o WHERE o.status = 'completed' ORDER BY o.ordered_at, o.id"),
        ("Show categories with a product count above the average category count.", "SELECT p.category, COUNT(*) AS product_count FROM products p GROUP BY p.category HAVING COUNT(*) > (SELECT AVG(category_count) FROM (SELECT COUNT(*) AS category_count FROM products GROUP BY category) x) ORDER BY p.category"),
        ("For each sales representative, return their smallest and largest order totals.", "SELECT sr.name, MIN(o.total_amount) AS smallest_order, MAX(o.total_amount) AS largest_order FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Show customers with at least one refund-bearing order and their refund total.", "SELECT c.name, SUM(r.amount) AS refund_total FROM customers c JOIN orders o ON o.customer_id = c.id JOIN refunds r ON r.order_id = o.id GROUP BY c.id, c.name HAVING COUNT(DISTINCT r.order_id) >= 1 ORDER BY c.name"),
        ("Compare each order total with the overall average using a derived column.", "SELECT o.order_number, o.total_amount, o.total_amount - (SELECT AVG(total_amount) FROM orders) AS difference_from_average FROM orders o ORDER BY o.order_number"),
        ("Show each region and the date of its earliest order.", "SELECT r.name, MIN(o.ordered_at) AS first_order_date FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("For every customer, show total quantity purchased across their orders.", "SELECT c.name, SUM(oi.quantity) AS quantity_purchased FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Find products that appear in more than ten order lines.", "SELECT p.name, COUNT(oi.id) AS line_count FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name HAVING COUNT(oi.id) > 10 ORDER BY p.name"),
        ("Show each status with its percentage of all orders.", "SELECT o.status, CAST(COUNT(*) AS numeric) / (SELECT COUNT(*) FROM orders) * 100 AS percentage_of_orders FROM orders o GROUP BY o.status ORDER BY o.status"),
        ("Return the second-highest order total for each customer when it exists.", "SELECT name, order_number, total_amount FROM (SELECT c.name, o.order_number, o.total_amount, DENSE_RANK() OVER (PARTITION BY c.id ORDER BY o.total_amount DESC) AS position FROM customers c JOIN orders o ON o.customer_id = c.id) ranked WHERE position = 2 ORDER BY name"),
        ("Show refund amounts alongside the related order subtotal and refund ratio.", "SELECT r.id, o.subtotal, r.amount, CAST(r.amount AS numeric) / o.subtotal AS refund_ratio FROM refunds r JOIN orders o ON o.id = r.order_id ORDER BY r.id"),
        ("For every category, report minimum, average, and maximum product price.", "SELECT p.category, MIN(p.unit_price) AS minimum_price, AVG(p.unit_price) AS average_price, MAX(p.unit_price) AS maximum_price FROM products p GROUP BY p.category ORDER BY p.category"),
        ("Show the total and average completed order value for each currency.", "SELECT o.currency, SUM(o.total_amount) AS total_value, AVG(o.total_amount) AS average_value FROM orders o WHERE o.status = 'completed' GROUP BY o.currency ORDER BY o.currency"),
    ])


def _relational_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return _specs([
        ("Count completed orders for each sales representative region.", "SELECT r.name, COUNT(o.id) AS completed_orders FROM regions r JOIN sales_representatives sr ON sr.region_id = r.id JOIN orders o ON o.sales_rep_id = sr.id WHERE o.status = 'completed' GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show total payment amount by customer region.", "SELECT r.name, SUM(pay.amount) AS payment_total FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id JOIN payments pay ON pay.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("List every product category with its sold unit count.", "SELECT p.category, SUM(oi.quantity) AS units_sold FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category ORDER BY p.category"),
        ("For each product category, count the different customers represented in its sales.", "SELECT p.category, COUNT(DISTINCT o.customer_id) AS customers FROM products p JOIN order_items oi ON oi.product_id = p.id JOIN orders o ON o.id = oi.order_id GROUP BY p.category ORDER BY p.category"),
        ("Show the summed refund amount associated with each sales representative.", "SELECT sr.name, SUM(r.amount) AS refund_total FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id JOIN refunds r ON r.order_id = o.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Show each customer and the number of products they have purchased.", "SELECT c.name, COUNT(DISTINCT oi.product_id) AS product_count FROM customers c LEFT JOIN orders o ON o.customer_id = c.id LEFT JOIN order_items oi ON oi.order_id = o.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Count payments by region through the representative assigned to the order.", "SELECT r.name, COUNT(pay.id) AS payment_count FROM regions r JOIN sales_representatives sr ON sr.region_id = r.id JOIN orders o ON o.sales_rep_id = sr.id JOIN payments pay ON pay.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("List order numbers with their representative and customer.", "SELECT o.order_number, sr.name AS representative, c.name AS customer FROM orders o JOIN sales_representatives sr ON sr.id = o.sales_rep_id JOIN customers c ON c.id = o.customer_id ORDER BY o.order_number"),
        ("Show average order total for each region using customer ownership.", "SELECT r.name, AVG(o.total_amount) AS average_total FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Count refund records by customer region.", "SELECT r.name, COUNT(ref.id) AS refund_count FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id JOIN refunds ref ON ref.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show product names with the count of distinct orders containing them.", "SELECT p.name, COUNT(DISTINCT oi.order_id) AS order_count FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY p.name"),
        ("For each representative, show the number of distinct customers served.", "SELECT sr.name, COUNT(DISTINCT c.id) AS customer_count FROM sales_representatives sr LEFT JOIN customers c ON c.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Show completed item quantity by order currency.", "SELECT o.currency, SUM(oi.quantity) AS item_quantity FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE o.status = 'completed' GROUP BY o.currency ORDER BY o.currency"),
        ("Show total order discount by region through customers.", "SELECT r.name, SUM(o.discount_amount) AS discount_total FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Count orders containing each product category.", "SELECT p.category, COUNT(DISTINCT o.id) AS order_count FROM products p JOIN order_items oi ON oi.product_id = p.id JOIN orders o ON o.id = oi.order_id GROUP BY p.category ORDER BY p.category"),
        ("Show average payment amount by sales representative.", "SELECT sr.name, AVG(pay.amount) AS average_payment FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id JOIN payments pay ON pay.order_id = o.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("List customers with their region and total completed revenue.", "SELECT c.name, r.name AS region, SUM(o.total_amount) AS revenue FROM customers c JOIN regions r ON r.id = c.region_id JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id, c.name, r.id, r.name ORDER BY c.name"),
        ("Break down refund amount by the lifecycle status of its order.", "SELECT o.status, SUM(r.amount) AS refund_total FROM orders o JOIN refunds r ON r.order_id = o.id GROUP BY o.status ORDER BY o.status"),
        ("For every sales representative, count their distinct orders.", "SELECT sr.name, COUNT(DISTINCT o.id) AS order_count FROM sales_representatives sr LEFT JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY sr.name"),
        ("Show item quantity sold by product category for completed orders only.", "SELECT p.category, SUM(oi.quantity) AS quantity_sold FROM products p JOIN order_items oi ON oi.product_id = p.id JOIN orders o ON o.id = oi.order_id WHERE o.status = 'completed' GROUP BY p.category ORDER BY p.category"),
    ])


def _filter_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return _specs([
        ("What is the total value of completed orders?", "SELECT SUM(o.total_amount) AS total_value FROM orders o WHERE o.status = 'completed'"),
        ("How many orders are pending?", "SELECT COUNT(*) AS order_count FROM orders o WHERE o.status = 'pending'"),
        ("Give the average payment amount for settled payments.", "SELECT AVG(p.amount) AS average_amount FROM payments p WHERE p.status = 'settled'"),
        ("Sum refunds attached to completed orders.", "SELECT SUM(r.amount) AS refund_total FROM refunds r JOIN orders o ON o.id = r.order_id WHERE o.status = 'completed'"),
        ("Count products priced above 150 in each category.", "SELECT p.category, COUNT(*) AS product_count FROM products p WHERE p.unit_price > 150 GROUP BY p.category ORDER BY p.category"),
        ("Show average completed order total by status.", "SELECT o.status, AVG(o.total_amount) AS average_total FROM orders o WHERE o.status = 'completed' GROUP BY o.status ORDER BY o.status"),
        ("How much subtotal belongs to USD orders by status?", "SELECT o.status, SUM(o.subtotal) AS subtotal_total FROM orders o WHERE o.currency = 'USD' GROUP BY o.status ORDER BY o.status"),
        ("Count settled payments for each customer region.", "SELECT r.name, COUNT(p.id) AS settled_payments FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id JOIN payments p ON p.order_id = o.id WHERE p.status = 'settled' GROUP BY r.id, r.name ORDER BY r.name"),
        ("Show categories whose completed item quantity exceeds 20.", "SELECT p.category, SUM(oi.quantity) AS quantity FROM products p JOIN order_items oi ON oi.product_id = p.id JOIN orders o ON o.id = oi.order_id WHERE o.status = 'completed' GROUP BY p.category HAVING SUM(oi.quantity) > 20 ORDER BY p.category"),
        ("Find customers with more than two completed orders.", "SELECT c.name, COUNT(o.id) AS completed_orders FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id, c.name HAVING COUNT(o.id) > 2 ORDER BY c.name"),
        ("Show the total paid amount for orders with a discount.", "SELECT SUM(p.amount) AS paid_total FROM payments p JOIN orders o ON o.id = p.order_id WHERE o.discount_amount > 0"),
        ("Count refund records with amounts above 100.", "SELECT COUNT(*) AS refund_count FROM refunds r WHERE r.amount > 100"),
        ("Show average item quantity for Electronics products.", "SELECT AVG(oi.quantity) AS average_quantity FROM products p JOIN order_items oi ON oi.product_id = p.id WHERE p.category = 'Electronics'"),
        ("Find regions with at least ten customers.", "SELECT r.name, COUNT(c.id) AS customer_count FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name HAVING COUNT(c.id) >= 10 ORDER BY r.name"),
        ("Show total completed revenue for orders placed before July 2025.", "SELECT SUM(o.total_amount) AS revenue FROM orders o WHERE o.status = 'completed' AND o.ordered_at < DATE '2025-07-01'"),
    ])


def _ratio_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return _specs([
        ("Calculate each order's discount as a percentage of subtotal.", "SELECT o.order_number, CASE WHEN o.subtotal = 0 THEN 0 ELSE CAST(o.discount_amount AS numeric) / o.subtotal * 100 END AS discount_percent FROM orders o ORDER BY o.order_number"),
        ("Show the fraction of each order total represented by its refund.", "SELECT o.order_number, CASE WHEN o.total_amount = 0 THEN 0 ELSE CAST(r.amount AS numeric) / o.total_amount END AS refund_fraction FROM orders o JOIN refunds r ON r.order_id = o.id ORDER BY o.order_number"),
        ("Report payment amount as a percentage of the related order total.", "SELECT p.id, CASE WHEN o.total_amount = 0 THEN 0 ELSE CAST(p.amount AS numeric) / o.total_amount * 100 END AS payment_percent FROM payments p JOIN orders o ON o.id = p.order_id ORDER BY p.id"),
        ("Show each product's price relative to the maximum catalog price.", "SELECT p.name, CAST(p.unit_price AS numeric) / (SELECT MAX(unit_price) FROM products) AS price_ratio FROM products p ORDER BY p.name"),
        ("Calculate the share of total order value for each customer.", "SELECT c.name, CAST(SUM(o.total_amount) AS numeric) / (SELECT SUM(total_amount) FROM orders) AS order_share FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY c.name"),
        ("Show the percentage of orders represented by each status.", "SELECT o.status, CAST(COUNT(*) AS numeric) / (SELECT COUNT(*) FROM orders) * 100 AS status_percent FROM orders o GROUP BY o.status ORDER BY o.status"),
        ("Report refunded amount divided by completed revenue for each region.", "SELECT r.name, CAST(COALESCE(SUM(ref.amount), 0) AS numeric) / SUM(CASE WHEN o.status = 'completed' THEN o.total_amount ELSE 0 END) AS refund_ratio FROM regions r JOIN customers c ON c.region_id = r.id JOIN orders o ON o.customer_id = c.id LEFT JOIN refunds ref ON ref.order_id = o.id GROUP BY r.id, r.name ORDER BY r.name"),
        ("Calculate average order total per item line for every currency.", "SELECT o.currency, CAST(SUM(o.total_amount) AS numeric) / COUNT(oi.id) AS value_per_line FROM orders o JOIN order_items oi ON oi.order_id = o.id GROUP BY o.currency ORDER BY o.currency"),
        ("Show each refund as a percentage of its order subtotal.", "SELECT r.id, CAST(r.amount AS numeric) / o.subtotal * 100 AS subtotal_percent FROM refunds r JOIN orders o ON o.id = r.order_id ORDER BY r.id"),
        ("Calculate the percentage discount by order status.", "SELECT o.status, CASE WHEN SUM(o.subtotal) = 0 THEN 0 ELSE CAST(SUM(o.discount_amount) AS numeric) / SUM(o.subtotal) * 100 END AS discount_percent FROM orders o GROUP BY o.status ORDER BY o.status"),
    ])


def _order_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return _specs([
        ("Return the five orders with the highest total amounts.", "SELECT o.order_number, o.total_amount FROM orders o ORDER BY o.total_amount DESC, o.order_number LIMIT 5"),
        ("List the three least expensive products.", "SELECT p.name, p.unit_price FROM products p ORDER BY p.unit_price, p.name LIMIT 3"),
        ("Show the top four customers by number of orders.", "SELECT c.name, COUNT(o.id) AS order_count FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name ORDER BY order_count DESC, c.name LIMIT 4"),
        ("Which two categories have the greatest total item quantity?", "SELECT p.category, SUM(oi.quantity) AS quantity FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.category ORDER BY quantity DESC, p.category LIMIT 2"),
        ("Show the four largest refunds.", "SELECT r.id, r.amount FROM refunds r ORDER BY r.amount DESC, r.id LIMIT 4"),
        ("Return the first six customers alphabetically.", "SELECT c.name FROM customers c ORDER BY c.name LIMIT 6"),
        ("Find the three representatives with the smallest average order total.", "SELECT sr.name, AVG(o.total_amount) AS average_total FROM sales_representatives sr JOIN orders o ON o.sales_rep_id = sr.id GROUP BY sr.id, sr.name ORDER BY average_total, sr.name LIMIT 3"),
        ("List the five products with the most units sold.", "SELECT p.name, SUM(oi.quantity) AS units FROM products p JOIN order_items oi ON oi.product_id = p.id GROUP BY p.id, p.name ORDER BY units DESC, p.name LIMIT 5"),
        ("Show the two highest payment amounts.", "SELECT p.id, p.amount FROM payments p ORDER BY p.amount DESC, p.id LIMIT 2"),
        ("Return the three regions with the largest customer populations.", "SELECT r.name, COUNT(c.id) AS customer_count FROM regions r JOIN customers c ON c.region_id = r.id GROUP BY r.id, r.name ORDER BY customer_count DESC, r.name LIMIT 3"),
    ])


def _window_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return _specs([
        ("Show each order with its sequence number within its customer history.", "SELECT o.order_number, o.customer_id, ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS customer_sequence FROM orders o ORDER BY o.customer_id, customer_sequence"),
        ("Rank products from most expensive to least expensive.", "SELECT p.name, p.unit_price, RANK() OVER (ORDER BY p.unit_price DESC) AS price_rank FROM products p ORDER BY price_rank, p.name"),
        ("Show a running total of payments in payment-date order.", "SELECT p.id, p.paid_at, SUM(p.amount) OVER (ORDER BY p.paid_at, p.id) AS running_paid FROM payments p ORDER BY p.paid_at, p.id"),
        ("For each representative, number their orders from newest to oldest.", "SELECT o.order_number, o.sales_rep_id, ROW_NUMBER() OVER (PARTITION BY o.sales_rep_id ORDER BY o.ordered_at DESC, o.id DESC) AS recent_position FROM orders o ORDER BY o.sales_rep_id, recent_position"),
        ("Show each customer's order total and the previous order total.", "SELECT o.customer_id, o.order_number, o.total_amount, LAG(o.total_amount) OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at, o.id) AS previous_total FROM orders o ORDER BY o.customer_id, o.ordered_at, o.id"),
        ("Calculate cumulative completed revenue ordered by order date.", "SELECT o.order_number, SUM(o.total_amount) OVER (ORDER BY o.ordered_at, o.id) AS cumulative_revenue FROM orders o WHERE o.status = 'completed' ORDER BY o.ordered_at, o.id"),
        ("Rank customers by their completed revenue.", "SELECT c.name, SUM(o.total_amount) AS revenue, RANK() OVER (ORDER BY SUM(o.total_amount) DESC) AS revenue_rank FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id, c.name ORDER BY revenue_rank, c.name"),
        ("Show every product and its price difference from the prior product by price.", "SELECT p.name, p.unit_price, p.unit_price - LAG(p.unit_price) OVER (ORDER BY p.unit_price, p.id) AS price_difference FROM products p ORDER BY p.unit_price, p.id"),
        ("For each region, rank representatives by their order count.", "SELECT r.name AS region, sr.name AS representative, COUNT(o.id) AS order_count, RANK() OVER (PARTITION BY r.id ORDER BY COUNT(o.id) DESC) AS region_rank FROM regions r JOIN sales_representatives sr ON sr.region_id = r.id LEFT JOIN orders o ON o.sales_rep_id = sr.id GROUP BY r.id, r.name, sr.id, sr.name ORDER BY r.name, region_rank, sr.name"),
        ("Show each refund and the next refund amount by refund date.", "SELECT r.id, r.refunded_at, r.amount, LEAD(r.amount) OVER (ORDER BY r.refunded_at, r.id) AS next_amount FROM refunds r ORDER BY r.refunded_at, r.id"),
    ])


def _literal_cases() -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    return [
        ("List orders whose status is exactly completed.", "SELECT o.order_number, o.total_amount FROM orders o WHERE o.status = 'completed' ORDER BY o.order_number", (), ("completed",)),
        ("Show payments whose status is exactly settled.", "SELECT p.id, p.amount FROM payments p WHERE p.status = 'settled' ORDER BY p.id", (), ("settled",)),
        ("List products in the Electronics category.", "SELECT p.name, p.sku FROM products p WHERE p.category = 'Electronics' ORDER BY p.name", (), ("Electronics",)),
        ("Show customers assigned to the North region.", "SELECT c.name FROM customers c JOIN regions r ON r.id = c.region_id WHERE r.name = 'North' ORDER BY c.name", (), ("North",)),
        ("List orders using the USD currency.", "SELECT o.order_number FROM orders o WHERE o.currency = 'USD' ORDER BY o.order_number", (), ("USD",)),
        ("Show refunds whose reason is Customer return.", "SELECT r.id, r.amount FROM refunds r WHERE r.reason = 'Customer return' ORDER BY r.id", (), ("Customer return",)),
        ("List products in the Office category.", "SELECT p.name FROM products p WHERE p.category = 'Office' ORDER BY p.name", (), ("Office",)),
        ("Show cancelled order numbers.", "SELECT o.order_number FROM orders o WHERE o.status = 'cancelled' ORDER BY o.order_number", (), ("cancelled",)),
        ("List representatives assigned to the West region.", "SELECT sr.name FROM sales_representatives sr JOIN regions r ON r.id = sr.region_id WHERE r.name = 'West' ORDER BY sr.name", (), ("West",)),
        ("Show products in the Outdoor category with their prices.", "SELECT p.name, p.unit_price FROM products p WHERE p.category = 'Outdoor' ORDER BY p.name", (), ("Outdoor",)),
    ]


def _schema_objects_for(family: str, sql: str) -> tuple[str, ...]:
    from sqlglot import exp

    tree = parse_one(sql, read="postgres")
    tables = {table.name.lower() for table in tree.find_all(exp.Table)}
    columns = {
        f"{column.table.lower()}.{column.name.lower()}" if column.table else column.name.lower()
        for column in tree.find_all(exp.Column)
    }
    return tuple(sorted((*tables, *columns)))
