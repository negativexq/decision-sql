# ruff: noqa: E501

"""Provider-blind construction and freshness gating for M10.4S.

The cases in this module are new research fixtures authored after the frozen
M10.4R checkpoint.  This module contains no provider, runtime, evaluator, or
database dependency.  Reference freshness is checked with the frozen
``decision-sql-reference-freshness-v1`` implementation before any reference
validation or architecture execution.
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
from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m104_corpus import _binding_json, _contract_json
from evaluation.reference_freshness import (
    canonical_reference_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
CORPUS_ID = "decisionsql-m104s-provenance-diagnostic"
CORPUS_VERSION = "decisionsql-m104s-provenance-diagnostic-v1"
STARTING_CHECKPOINT = "0326682e96e4a8ac22d73ed6ada7e58af3ccff56"
CONSUMED_MANIFEST_HASH = "17b5fc23ffdbcf1c2e45191c708032fae9b7690632197f4c0aa8b9d9d5de7ebc"
CANONICAL_INDEX_PATH = ROOT / "evaluation" / "results" / "m104r" / "canonical-freshness-v1" / "historical_canonical_index.json"
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
PHENOTYPE_BY_FAMILY = {
    "SCHEMA_SOURCE_GROUNDING": "SCHEMA_SOURCE",
    "JOIN_GRAIN": "JOIN_GRAIN",
    "AGGREGATION_GROUPING": "AGGREGATION_GROUPING",
    "FORMULA_RATIO": "FORMULA_RATIO",
    "FILTER_COLUMN": "FILTER_COLUMN",
    "FILTER_OPERATOR_BOOLEAN": "FILTER_BOOLEAN_LOGIC",
    "FILTER_LITERAL": "FILTER_LITERAL",
    "TEMPORAL": "TEMPORAL",
    "WINDOW": "WINDOW",
    "PROJECTION_RESULT_SHAPE": "PROJECTION_RESULT_SHAPE",
    "ORDER_TOPN": "ORDER_TOPN",
    "DISTINCT_SET": "DISTINCT_SET",
    "GOVERNED_SEMANTIC": "COMPOUND_SEMANTIC",
}


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalized_question(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


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
    if select is None:
        raise ValueError("reference SQL must contain a SELECT")
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


def _alias(sql: str, token: str) -> str:
    """Add a case-unique alias to every physical table in a compiler-free fixture."""
    tables = (
        "sales_representatives",
        "order_items",
        "customers",
        "products",
        "payments",
        "refunds",
        "orders",
        "regions",
    )
    result = sql
    for table in tables:
        alias = f"{token}_{table}"
        result = re.sub(rf"\b{table}\.", f"{alias}.", result)
        result = re.sub(
            rf"\b(FROM|JOIN)\s+{table}\b",
            rf"\1 {table} AS {alias}",
            result,
        )
    return result


def _direct_rows() -> dict[str, tuple[tuple[str, str], ...]]:
    """New benchmark-owned direct cases; no M10.4 fixture is imported."""
    return {
        "SCHEMA_SOURCE_GROUNDING": (
            ("Which account record exposes its stable key beside its display name?", "SELECT {a}.id AS account_key, {a}.name AS account_name FROM customers AS {a} ORDER BY {a}.id"),
            ("Which catalog records pair a stock code with the listed product label?", "SELECT {a}.sku AS stock_code, {a}.name AS product_label FROM products AS {a} ORDER BY {a}.sku"),
            ("Which sales documents show their identifier and settlement currency?", "SELECT {a}.id AS order_key, {a}.currency AS settlement_currency FROM orders AS {a} ORDER BY {a}.id"),
            ("Which transaction rows expose payment time and amount together?", "SELECT {a}.paid_at AS payment_time, {a}.amount AS payment_amount FROM payments AS {a} ORDER BY {a}.paid_at, {a}.id"),
            ("Which return records identify the associated order and stated reason?", "SELECT {a}.order_id AS order_key, {a}.reason AS return_reason FROM refunds AS {a} ORDER BY {a}.order_id, {a}.id"),
            ("Which staff records pair representative names with region keys?", "SELECT {a}.name AS representative_name, {a}.region_id AS region_key FROM sales_representatives AS {a} ORDER BY {a}.name"),
            ("Which line records show quantity beside the product key?", "SELECT {a}.product_id AS product_key, {a}.quantity AS units FROM order_items AS {a} ORDER BY {a}.product_id, {a}.id"),
            ("Which geography rows expose the region key and its label?", "SELECT {a}.id AS region_key, {a}.name AS region_label FROM regions AS {a} ORDER BY {a}.id"),
            ("Which order rows expose subtotal and discount as stored amounts?", "SELECT {a}.subtotal AS subtotal_amount, {a}.discount_amount AS discount_amount FROM orders AS {a} ORDER BY {a}.id"),
            ("Which customer rows identify the assigned representative key?", "SELECT {a}.id AS customer_key, {a}.sales_rep_id AS representative_key FROM customers AS {a} ORDER BY {a}.id"),
        ),
        "JOIN_GRAIN": (
            ("At the account level, what total order value belongs to each account?", "SELECT {c}.id AS account_key, SUM({o}.total_amount) AS account_value FROM customers AS {c} JOIN orders AS {o} ON {o}.customer_id = {c}.id GROUP BY {c}.id ORDER BY {c}.id"),
            ("At product level, how many units are represented by its order lines?", "SELECT {p}.id AS product_key, SUM({i}.quantity) AS unit_total FROM products AS {p} JOIN order_items AS {i} ON {i}.product_id = {p}.id GROUP BY {p}.id ORDER BY {p}.id"),
            ("For each sales representative, how many order rows are assigned?", "SELECT {s}.id AS representative_key, COUNT({o}.id) AS order_total FROM sales_representatives AS {s} JOIN orders AS {o} ON {o}.sales_rep_id = {s}.id GROUP BY {s}.id ORDER BY {s}.id"),
            ("At order level, what payment amount is attached to each order?", "SELECT {o}.id AS order_key, SUM({p}.amount) AS paid_total FROM orders AS {o} JOIN payments AS {p} ON {p}.order_id = {o}.id GROUP BY {o}.id ORDER BY {o}.id"),
            ("At region level, how many customer records connect to each region?", "SELECT {r}.id AS region_key, COUNT({c}.id) AS account_total FROM regions AS {r} JOIN customers AS {c} ON {c}.region_id = {r}.id GROUP BY {r}.id ORDER BY {r}.id"),
            ("At order level, what refund amount is connected to each order?", "SELECT {o}.id AS order_key, SUM({r}.amount) AS return_total FROM orders AS {o} JOIN refunds AS {r} ON {r}.order_id = {o}.id GROUP BY {o}.id ORDER BY {o}.id"),
            ("Which product categories accumulate quantity through their line records?", "SELECT {p}.category AS category_name, SUM({i}.quantity) AS unit_total FROM products AS {p} JOIN order_items AS {i} ON {i}.product_id = {p}.id GROUP BY {p}.category ORDER BY {p}.category"),
            ("Which representative is paired with which region label?", "SELECT {s}.name AS representative_name, {r}.name AS region_name FROM sales_representatives AS {s} JOIN regions AS {r} ON {r}.id = {s}.region_id ORDER BY {s}.name"),
            ("For each account, how many different catalog products appear in its orders?", "SELECT {c}.id AS account_key, COUNT(DISTINCT {i}.product_id) AS product_total FROM customers AS {c} JOIN orders AS {o} ON {o}.customer_id = {c}.id JOIN order_items AS {i} ON {i}.order_id = {o}.id GROUP BY {c}.id ORDER BY {c}.id"),
            ("At region level, what payment value is reached through its accounts and orders?", "SELECT {r}.id AS region_key, SUM({p}.amount) AS payment_total FROM regions AS {r} JOIN customers AS {c} ON {c}.region_id = {r}.id JOIN orders AS {o} ON {o}.customer_id = {c}.id JOIN payments AS {p} ON {p}.order_id = {o}.id GROUP BY {r}.id ORDER BY {r}.id"),
        ),
        "AGGREGATION_GROUPING": (
            ("For every order status, what are the low, high, and mean totals?", "SELECT {a}.status AS lifecycle, MIN({a}.total_amount) AS low_total, MAX({a}.total_amount) AS high_total, AVG({a}.total_amount) AS mean_total FROM orders AS {a} GROUP BY {a}.status ORDER BY {a}.status"),
            ("How do payment counts and sums break down by order currency?", "SELECT {o}.currency AS currency_code, COUNT({p}.id) AS payment_rows, SUM({p}.amount) AS payment_sum FROM orders AS {o} JOIN payments AS {p} ON {p}.order_id = {o}.id GROUP BY {o}.currency ORDER BY {o}.currency"),
            ("For each catalog category, what are the mean and maximum prices?", "SELECT {a}.category AS category_name, AVG({a}.unit_price) AS mean_price, MAX({a}.unit_price) AS peak_price FROM products AS {a} GROUP BY {a}.category ORDER BY {a}.category"),
            ("How many returns and how much value occur for each return reason?", "SELECT {a}.reason AS reason_code, COUNT({a}.id) AS return_rows, SUM({a}.amount) AS return_sum FROM refunds AS {a} GROUP BY {a}.reason ORDER BY {a}.reason"),
            ("For each representative, summarize order count and subtotal value.", "SELECT {s}.id AS representative_key, COUNT({o}.id) AS order_rows, SUM({o}.subtotal) AS subtotal_sum FROM sales_representatives AS {s} JOIN orders AS {o} ON {o}.sales_rep_id = {s}.id GROUP BY {s}.id ORDER BY {s}.id"),
            ("How does line quantity and extended value group by product key?", "SELECT {a}.product_id AS product_key, SUM({a}.quantity) AS unit_sum, SUM({a}.quantity * {a}.unit_price) AS extended_sum FROM order_items AS {a} GROUP BY {a}.product_id ORDER BY {a}.product_id"),
            ("For each currency, what is the mean discount and mean subtotal?", "SELECT {a}.currency AS currency_code, AVG({a}.discount_amount) AS mean_discount, AVG({a}.subtotal) AS mean_subtotal FROM orders AS {a} GROUP BY {a}.currency ORDER BY {a}.currency"),
            ("How many customer rows belong to each region key?", "SELECT {a}.region_id AS region_key, COUNT({a}.id) AS customer_rows FROM customers AS {a} GROUP BY {a}.region_id ORDER BY {a}.region_id"),
            ("For each payment status, what are the smallest and largest amounts?", "SELECT {a}.status AS payment_state, MIN({a}.amount) AS low_amount, MAX({a}.amount) AS high_amount FROM payments AS {a} GROUP BY {a}.status ORDER BY {a}.status"),
            ("How does order value group by representative and currency?", "SELECT {a}.sales_rep_id AS representative_key, {a}.currency AS currency_code, SUM({a}.total_amount) AS order_value FROM orders AS {a} GROUP BY {a}.sales_rep_id, {a}.currency ORDER BY {a}.sales_rep_id, {a}.currency"),
        ),
        "FORMULA_RATIO": (
            ("For every order, what remains after its discount is removed?", "SELECT {a}.id AS order_key, {a}.total_amount - {a}.discount_amount AS net_value FROM orders AS {a} ORDER BY {a}.id"),
            ("What extended line value results from multiplying units by price?", "SELECT {a}.id AS line_key, {a}.quantity * {a}.unit_price AS extended_value FROM order_items AS {a} ORDER BY {a}.id"),
            ("What amount remains from each payment after a three percent fee?", "SELECT {a}.id AS payment_key, {a}.amount * 0.97 AS net_amount FROM payments AS {a} ORDER BY {a}.id"),
            ("What share of each order total is represented by its discount?", "SELECT {a}.id AS order_key, CASE WHEN {a}.total_amount = 0 THEN 0 ELSE {a}.discount_amount / {a}.total_amount END AS discount_ratio FROM orders AS {a} ORDER BY {a}.id"),
            ("What fraction of its order total does each return represent?", "SELECT {r}.id AS return_key, CASE WHEN {o}.total_amount = 0 THEN 0 ELSE {r}.amount / {o}.total_amount END AS return_ratio FROM refunds AS {r} JOIN orders AS {o} ON {o}.id = {r}.order_id ORDER BY {r}.id"),
            ("What is the difference between stored subtotal and total for each order?", "SELECT {a}.id AS order_key, {a}.subtotal - {a}.total_amount AS subtotal_gap FROM orders AS {a} ORDER BY {a}.id"),
            ("What is the average line value per unit within each product?", "SELECT {p}.id AS product_key, CASE WHEN SUM({i}.quantity) = 0 THEN 0 ELSE SUM({i}.quantity * {i}.unit_price) / SUM({i}.quantity) END AS unit_value FROM products AS {p} JOIN order_items AS {i} ON {i}.product_id = {p}.id GROUP BY {p}.id ORDER BY {p}.id"),
            ("What remains from every return after a two percent handling fee?", "SELECT {a}.id AS return_key, {a}.amount - ({a}.amount * 0.02) AS net_return FROM refunds AS {a} ORDER BY {a}.id"),
            ("What percentage of each currency's subtotal is discount value?", "SELECT {a}.currency AS currency_code, CASE WHEN SUM({a}.subtotal) = 0 THEN 0 ELSE SUM({a}.discount_amount) / SUM({a}.subtotal) * 100 END AS discount_percent FROM orders AS {a} GROUP BY {a}.currency ORDER BY {a}.currency"),
            ("What is the mean order total per distinct customer for each representative?", "SELECT {a}.sales_rep_id AS representative_key, CASE WHEN COUNT(DISTINCT {a}.customer_id) = 0 THEN 0 ELSE SUM({a}.total_amount) / COUNT(DISTINCT {a}.customer_id) END AS mean_customer_value FROM orders AS {a} GROUP BY {a}.sales_rep_id ORDER BY {a}.sales_rep_id"),
        ),
        "FILTER_COLUMN": (
            ("Which orders use the GBP currency code?", "SELECT {a}.id AS order_key, {a}.total_amount AS order_value FROM orders AS {a} WHERE {a}.currency = 'GBP' ORDER BY {a}.id"),
            ("Which customer records point to region key 2?", "SELECT {a}.id AS customer_key, {a}.name AS customer_name FROM customers AS {a} WHERE {a}.region_id = 2 ORDER BY {a}.id"),
            ("Which catalog items belong to the Garden category?", "SELECT {a}.id AS product_key, {a}.name AS product_name FROM products AS {a} WHERE {a}.category = 'Garden' ORDER BY {a}.id"),
            ("Which payments exceed 300 in stored amount?", "SELECT {a}.id AS payment_key, {a}.amount AS payment_amount FROM payments AS {a} WHERE {a}.amount > 300 ORDER BY {a}.id"),
            ("Which return rows are attached to order key 4?", "SELECT {a}.id AS return_key, {a}.amount AS return_amount FROM refunds AS {a} WHERE {a}.order_id = 4 ORDER BY {a}.id"),
            ("Which orders belong to representative key 1?", "SELECT {a}.id AS order_key, {a}.total_amount AS order_value FROM orders AS {a} WHERE {a}.sales_rep_id = 1 ORDER BY {a}.id"),
            ("Which line rows contain at least six units?", "SELECT {a}.id AS line_key, {a}.quantity AS units FROM order_items AS {a} WHERE {a}.quantity >= 6 ORDER BY {a}.id"),
            ("Which region row has key 3?", "SELECT {a}.id AS region_key, {a}.name AS region_name FROM regions AS {a} WHERE {a}.id = 3 ORDER BY {a}.id"),
            ("Which orders carry the pending lifecycle state?", "SELECT {a}.id AS order_key, {a}.total_amount AS order_value FROM orders AS {a} WHERE {a}.status = 'pending' ORDER BY {a}.id"),
            ("Which payment rows are not marked failed?", "SELECT {a}.id AS payment_key, {a}.status AS payment_state FROM payments AS {a} WHERE {a}.status <> 'failed' ORDER BY {a}.id"),
        ),
        "FILTER_OPERATOR_BOOLEAN": (
            ("Which CAD orders have totals above 125?", "SELECT {a}.id AS order_key, {a}.total_amount AS order_value FROM orders AS {a} WHERE {a}.currency = 'CAD' AND {a}.total_amount > 125 ORDER BY {a}.id"),
            ("Which catalog rows are in either Garden or Books?", "SELECT {a}.id AS product_key, {a}.category AS category_name FROM products AS {a} WHERE {a}.category IN ('Garden', 'Books') ORDER BY {a}.id"),
            ("Which orders are either pending or cancelled?", "SELECT {a}.id AS order_key, {a}.status AS lifecycle FROM orders AS {a} WHERE {a}.status IN ('pending', 'cancelled') ORDER BY {a}.id"),
            ("Which payments are neither failed nor pending?", "SELECT {a}.id AS payment_key, {a}.status AS payment_state FROM payments AS {a} WHERE {a}.status NOT IN ('failed', 'pending') ORDER BY {a}.id"),
            ("Which orders have a discount and a total over 150?", "SELECT {a}.id AS order_key, {a}.discount_amount AS discount FROM orders AS {a} WHERE {a}.discount_amount > 0 AND {a}.total_amount > 150 ORDER BY {a}.id"),
            ("Which orders are cancelled or have totals below 40?", "SELECT {a}.id AS order_key, {a}.status AS lifecycle, {a}.total_amount AS order_value FROM orders AS {a} WHERE {a}.status = 'cancelled' OR {a}.total_amount < 40 ORDER BY {a}.id"),
            ("Which payments are positive and not settled?", "SELECT {a}.id AS payment_key, {a}.amount AS payment_amount FROM payments AS {a} WHERE {a}.amount > 0 AND {a}.status <> 'settled' ORDER BY {a}.id"),
            ("Which products are outside the Books category?", "SELECT {a}.id AS product_key, {a}.category AS category_name FROM products AS {a} WHERE NOT {a}.category = 'Books' ORDER BY {a}.id"),
            ("Which returns exceed 35 or cite a damaged item?", "SELECT {a}.id AS return_key, {a}.amount AS return_amount FROM refunds AS {a} WHERE {a}.amount > 35 OR {a}.reason = 'damaged_item' ORDER BY {a}.id"),
            ("Which orders have a nonzero discount in GBP?", "SELECT {a}.id AS order_key, {a}.discount_amount AS discount FROM orders AS {a} WHERE {a}.discount_amount <> 0 AND {a}.currency = 'GBP' ORDER BY {a}.id"),
        ),
        "FILTER_LITERAL": (
            ("Which orders carry the refunded lifecycle label?", "SELECT {a}.id AS order_key FROM orders AS {a} WHERE {a}.status = 'refunded' ORDER BY {a}.id"),
            ("Which orders are recorded in JPY?", "SELECT {a}.id AS order_key FROM orders AS {a} WHERE {a}.currency = 'JPY' ORDER BY {a}.id"),
            ("Which products use the Books category label?", "SELECT {a}.id AS product_key FROM products AS {a} WHERE {a}.category = 'Books' ORDER BY {a}.id"),
            ("Which payments are marked pending?", "SELECT {a}.id AS payment_key FROM payments AS {a} WHERE {a}.status = 'pending' ORDER BY {a}.id"),
            ("Which returns cite a late_delivery reason?", "SELECT {a}.id AS return_key FROM refunds AS {a} WHERE {a}.reason = 'late_delivery' ORDER BY {a}.id"),
            ("Which orders were recorded on or after 2024-06-01?", "SELECT {a}.id AS order_key FROM orders AS {a} WHERE {a}.ordered_at >= TIMESTAMPTZ '2024-06-01 00:00:00+00:00' ORDER BY {a}.id"),
            ("Which representatives belong to region key 4?", "SELECT {a}.id AS representative_key FROM sales_representatives AS {a} WHERE {a}.region_id = 4 ORDER BY {a}.id"),
            ("Which line rows have a unit price of 90?", "SELECT {a}.id AS line_key FROM order_items AS {a} WHERE {a}.unit_price = 90 ORDER BY {a}.id"),
            ("Which customer row is named Noor?", "SELECT {a}.id AS customer_key FROM customers AS {a} WHERE {a}.name = 'Noor' ORDER BY {a}.id"),
            ("Which returns use the duplicate_charge reason?", "SELECT {a}.id AS return_key, {a}.amount AS return_amount FROM refunds AS {a} WHERE {a}.reason = 'duplicate_charge' ORDER BY {a}.id"),
        ),
        "TEMPORAL": (
            ("How many orders fall in each ordered month?", "SELECT DATE_TRUNC('month', {a}.ordered_at) AS month_start, COUNT(*) AS order_rows FROM orders AS {a} GROUP BY DATE_TRUNC('month', {a}.ordered_at) ORDER BY month_start"),
            ("What payment value is recorded for each payment month?", "SELECT DATE_TRUNC('month', {a}.paid_at) AS month_start, SUM({a}.amount) AS payment_value FROM payments AS {a} GROUP BY DATE_TRUNC('month', {a}.paid_at) ORDER BY month_start"),
            ("Which orders occurred during the second quarter of 2024?", "SELECT {a}.id AS order_key, {a}.ordered_at AS ordered_time FROM orders AS {a} WHERE {a}.ordered_at >= TIMESTAMPTZ '2024-04-01 00:00:00+00:00' AND {a}.ordered_at < TIMESTAMPTZ '2024-07-01 00:00:00+00:00' ORDER BY {a}.ordered_at"),
            ("Which returns were recorded during 2023?", "SELECT {a}.id AS return_key, {a}.refunded_at AS return_time FROM refunds AS {a} WHERE {a}.refunded_at >= TIMESTAMPTZ '2023-01-01 00:00:00+00:00' AND {a}.refunded_at < TIMESTAMPTZ '2024-01-01 00:00:00+00:00' ORDER BY {a}.refunded_at"),
            ("What is average order value by ordered year?", "SELECT DATE_PART('year', {a}.ordered_at) AS order_year, AVG({a}.total_amount) AS mean_value FROM orders AS {a} GROUP BY DATE_PART('year', {a}.ordered_at) ORDER BY order_year"),
            ("How many payments were recorded in each calendar quarter?", "SELECT DATE_TRUNC('quarter', {a}.paid_at) AS quarter_start, COUNT({a}.id) AS payment_rows FROM payments AS {a} GROUP BY DATE_TRUNC('quarter', {a}.paid_at) ORDER BY quarter_start"),
            ("Which orders were placed during the final month of 2024?", "SELECT {a}.id AS order_key, {a}.ordered_at AS ordered_time FROM orders AS {a} WHERE {a}.ordered_at >= TIMESTAMPTZ '2024-12-01 00:00:00+00:00' AND {a}.ordered_at < TIMESTAMPTZ '2025-01-01 00:00:00+00:00' ORDER BY {a}.id"),
            ("What refund amount is grouped by refund year?", "SELECT DATE_PART('year', {a}.refunded_at) AS refund_year, SUM({a}.amount) AS refund_value FROM refunds AS {a} GROUP BY DATE_PART('year', {a}.refunded_at) ORDER BY refund_year"),
            ("How many orders were recorded on each day?", "SELECT CAST({a}.ordered_at AS date) AS order_day, COUNT({a}.id) AS order_rows FROM orders AS {a} GROUP BY CAST({a}.ordered_at AS date) ORDER BY order_day"),
            ("What is payment value by the date it was paid?", "SELECT CAST({a}.paid_at AS date) AS payment_day, SUM({a}.amount) AS payment_value FROM payments AS {a} GROUP BY CAST({a}.paid_at AS date) ORDER BY payment_day"),
        ),
        "WINDOW": (
            ("Rank orders by total within each currency.", "SELECT {a}.id AS order_key, {a}.currency AS currency_code, {a}.total_amount AS order_value, RANK() OVER (PARTITION BY {a}.currency ORDER BY {a}.total_amount DESC) AS currency_rank FROM orders AS {a}"),
            ("Show each payment alongside a running amount by order key.", "SELECT {a}.id AS payment_key, {a}.order_id AS order_key, {a}.amount AS payment_amount, SUM({a}.amount) OVER (PARTITION BY {a}.order_id ORDER BY {a}.paid_at, {a}.id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_amount FROM payments AS {a}"),
            ("Number each product within its category by ascending price.", "SELECT {a}.id AS product_key, {a}.category AS category_name, ROW_NUMBER() OVER (PARTITION BY {a}.category ORDER BY {a}.unit_price, {a}.id) AS category_position FROM products AS {a}"),
            ("Show each order and the previous order total for its representative.", "SELECT {a}.id AS order_key, {a}.sales_rep_id AS representative_key, {a}.total_amount AS order_value, LAG({a}.total_amount) OVER (PARTITION BY {a}.sales_rep_id ORDER BY {a}.ordered_at, {a}.id) AS prior_value FROM orders AS {a}"),
            ("Show each return and the highest return amount seen for its order.", "SELECT {a}.id AS return_key, {a}.order_id AS order_key, {a}.amount AS return_amount, MAX({a}.amount) OVER (PARTITION BY {a}.order_id) AS order_peak_return FROM refunds AS {a}"),
            ("Rank representatives by order count using a window over the summary.", "SELECT {s}.id AS representative_key, COUNT({o}.id) AS order_rows, DENSE_RANK() OVER (ORDER BY COUNT({o}.id) DESC) AS order_rank FROM sales_representatives AS {s} LEFT JOIN orders AS {o} ON {o}.sales_rep_id = {s}.id GROUP BY {s}.id"),
            ("Show each line and its share of quantity within its order.", "SELECT {a}.id AS line_key, {a}.order_id AS order_key, {a}.quantity AS units, SUM({a}.quantity) OVER (PARTITION BY {a}.order_id) AS order_units FROM order_items AS {a}"),
            ("Number customers within each region by customer key.", "SELECT {a}.id AS customer_key, {a}.region_id AS region_key, ROW_NUMBER() OVER (PARTITION BY {a}.region_id ORDER BY {a}.id) AS region_position FROM customers AS {a}"),
            ("Show payment rows with the next payment time for each order.", "SELECT {a}.id AS payment_key, {a}.order_id AS order_key, {a}.paid_at AS payment_time, LEAD({a}.paid_at) OVER (PARTITION BY {a}.order_id ORDER BY {a}.paid_at, {a}.id) AS next_payment_time FROM payments AS {a}"),
            ("Rank products by price within each category from highest downward.", "SELECT {a}.id AS product_key, {a}.category AS category_name, {a}.unit_price AS listed_price, RANK() OVER (PARTITION BY {a}.category ORDER BY {a}.unit_price DESC) AS price_rank FROM products AS {a}"),
        ),
        "PROJECTION_RESULT_SHAPE": (
            ("Return the order reference with its lifecycle text as two named columns.", "SELECT {a}.order_number AS order_ref, {a}.status AS lifecycle_text FROM orders AS {a} ORDER BY {a}.order_number"),
            ("Show a product label and its price increased by a fixed service amount.", "SELECT {a}.name AS product_label, {a}.unit_price + 7 AS adjusted_price FROM products AS {a} ORDER BY {a}.name"),
            ("Return exactly a region key and region label for each region.", "SELECT {a}.id AS region_key, {a}.name AS region_label FROM regions AS {a} ORDER BY {a}.id"),
            ("Project the customer label with its order representative key.", "SELECT {a}.name AS customer_label, {a}.sales_rep_id AS representative_key FROM customers AS {a} ORDER BY {a}.name"),
            ("Return payment state and amount as the result pair.", "SELECT {a}.status AS payment_state, {a}.amount AS payment_amount FROM payments AS {a} ORDER BY {a}.id"),
            ("Project a return reason and value under readable names.", "SELECT {a}.reason AS return_reason, {a}.amount AS return_value FROM refunds AS {a} ORDER BY {a}.id"),
            ("Return line units and listed unit price without aggregation.", "SELECT {a}.quantity AS units, {a}.unit_price AS listed_price FROM order_items AS {a} ORDER BY {a}.id"),
            ("Show each order reference and whether its discount is positive.", "SELECT {a}.order_number AS order_ref, ({a}.discount_amount > 0) AS has_discount FROM orders AS {a} ORDER BY {a}.order_number"),
            ("Return category with a count of products as a grouped pair.", "SELECT {a}.category AS category_name, COUNT({a}.id) AS product_rows FROM products AS {a} GROUP BY {a}.category ORDER BY {a}.category"),
            ("Return order reference and its recorded ordering timestamp only.", "SELECT {a}.order_number AS order_ref, {a}.ordered_at AS order_time FROM orders AS {a} ORDER BY {a}.order_number"),
        ),
        "ORDER_TOPN": (
            ("Which four orders have the largest stored totals?", "SELECT {a}.id AS order_key, {a}.total_amount AS order_value FROM orders AS {a} ORDER BY {a}.total_amount DESC LIMIT 4"),
            ("Which seven products come first by ascending label?", "SELECT {a}.id AS product_key, {a}.name AS product_label FROM products AS {a} ORDER BY {a}.name ASC LIMIT 7"),
            ("Which six payments have the greatest amounts?", "SELECT {a}.id AS payment_key, {a}.amount AS payment_amount FROM payments AS {a} ORDER BY {a}.amount DESC LIMIT 6"),
            ("Which three returns have the highest values?", "SELECT {a}.id AS return_key, {a}.amount AS return_amount FROM refunds AS {a} ORDER BY {a}.amount DESC LIMIT 3"),
            ("Which eight customers come first in identifier order?", "SELECT {a}.id AS customer_key, {a}.name AS customer_name FROM customers AS {a} ORDER BY {a}.id ASC LIMIT 8"),
            ("Which four categories have the greatest mean product price?", "SELECT {a}.category AS category_name, AVG({a}.unit_price) AS mean_price FROM products AS {a} GROUP BY {a}.category ORDER BY mean_price DESC LIMIT 4"),
            ("Which six representatives have the most orders?", "SELECT {s}.id AS representative_key, COUNT({o}.id) AS order_rows FROM sales_representatives AS {s} JOIN orders AS {o} ON {o}.sales_rep_id = {s}.id GROUP BY {s}.id ORDER BY order_rows DESC LIMIT 6"),
            ("Which five orders are earliest by their stored time?", "SELECT {a}.id AS order_key, {a}.ordered_at AS order_time FROM orders AS {a} ORDER BY {a}.ordered_at ASC LIMIT 5"),
            ("Which five products have the lowest listed prices?", "SELECT {a}.id AS product_key, {a}.unit_price AS listed_price FROM products AS {a} ORDER BY {a}.unit_price ASC LIMIT 5"),
            ("Which three payment states have the largest total value?", "SELECT {a}.status AS payment_state, SUM({a}.amount) AS payment_value FROM payments AS {a} GROUP BY {a}.status ORDER BY payment_value DESC LIMIT 3"),
        ),
        "DISTINCT_SET": (
            ("Which different order currencies occur in the source?", "SELECT DISTINCT {a}.currency AS currency_code FROM orders AS {a} ORDER BY {a}.currency"),
            ("Which unique lifecycle states occur among orders?", "SELECT DISTINCT {a}.status AS lifecycle FROM orders AS {a} ORDER BY {a}.status"),
            ("How many different customer keys appear in each region?", "SELECT {a}.region_id AS region_key, COUNT(DISTINCT {a}.id) AS customer_total FROM customers AS {a} GROUP BY {a}.region_id ORDER BY {a}.region_id"),
            ("How many unique product keys does each order contain?", "SELECT {a}.order_id AS order_key, COUNT(DISTINCT {a}.product_id) AS product_total FROM order_items AS {a} GROUP BY {a}.order_id ORDER BY {a}.order_id"),
            ("Which unique return reasons occur, with their row totals?", "SELECT {a}.reason AS reason_code, COUNT(*) AS return_rows FROM refunds AS {a} GROUP BY {a}.reason ORDER BY {a}.reason"),
            ("How many distinct order keys belong to each representative?", "SELECT {a}.sales_rep_id AS representative_key, COUNT(DISTINCT {a}.id) AS order_total FROM orders AS {a} GROUP BY {a}.sales_rep_id ORDER BY {a}.sales_rep_id"),
            ("Which category keys have distinct product counts?", "SELECT {a}.category AS category_name, COUNT(DISTINCT {a}.id) AS product_total FROM products AS {a} GROUP BY {a}.category ORDER BY {a}.category"),
            ("How many different orders have payments in each state?", "SELECT {a}.status AS payment_state, COUNT(DISTINCT {a}.order_id) AS order_total FROM payments AS {a} GROUP BY {a}.status ORDER BY {a}.status"),
            ("Which customer keys occur in orders without repetition?", "SELECT DISTINCT {a}.customer_id AS customer_key FROM orders AS {a} ORDER BY {a}.customer_id"),
            ("How many unique order keys use each currency?", "SELECT {a}.currency AS currency_code, COUNT(DISTINCT {a}.id) AS order_total FROM orders AS {a} GROUP BY {a}.currency ORDER BY {a}.currency"),
        ),
    }


def _governed_rows() -> tuple[dict[str, Any], ...]:
    catalog = build_m3_catalog()
    compiler = MetricCompiler(catalog)
    rows: list[dict[str, Any]] = []
    dimensions = {metric.name: tuple(metric.valid_dimensions[:2]) for metric in catalog.metrics}
    for metric_name in METRICS:
        choices = dimensions[metric_name]
        cases = ((), (choices[0],), (choices[-1],), choices[:2])
        for ordinal, selected in enumerate(cases):
            compiled = compiler.compile_metric(MetricRequest(metric_name=metric_name, dimensions=selected))
            if not hasattr(compiled, "sql"):
                raise ValueError(f"unable to compile governed reference {metric_name}/{selected}")
            sql = _alias(compiled.sql, f"s{len(rows):03d}")
            label = ", ".join(selected) if selected else "the whole business"
            rows.append(
                {
                    "case_id": f"m104s-governed-{metric_name}-{ordinal:02d}",
                    "question": f"Using the governed catalog, summarize {metric_name.replace('_', ' ')} for diagnostic slice {ordinal + 1} at {label} grain.",
                    "expected_route": "GOVERNED",
                    "primary_diagnostic_family": "GOVERNED_SEMANTIC",
                    "governed_metric": metric_name,
                    "dimensions": list(selected),
                    "reference_sql": sql,
                }
            )
    return tuple(rows)


def build_cases() -> tuple[dict[str, Any], ...]:
    cases: list[dict[str, Any]] = []
    cases.extend(_governed_rows())
    ordinal = 0
    for family in DIRECT_FAMILIES:
        rows = _direct_rows()[family]
        if len(rows) != 10:
            raise ValueError(f"{family} must have exactly ten rows")
        for family_ordinal, (question, template) in enumerate(rows):
            token = f"q{ordinal:03d}"
            sql = template.format(a=token, c=f"{token}_c", o=f"{token}_o", p=f"{token}_p", i=f"{token}_i", s=f"{token}_s", r=f"{token}_r")
            cases.append(
                {
                    "case_id": f"m104s-direct-{family.lower()}-{family_ordinal:02d}",
                    "question": question,
                    "expected_route": "DIRECT",
                    "primary_diagnostic_family": family,
                    "reference_sql": sql,
                }
            )
            ordinal += 1
    if len(cases) != 160 or len({case["case_id"] for case in cases}) != 160:
        raise ValueError("M10.4S quota or case identity failure")
    for case in cases:
        fingerprint = canonical_reference_fingerprint(case["reference_sql"])
        signature = _signature(case["reference_sql"])
        case["reference_sql_hash"] = fingerprint.raw_hash
        case["canonical_reference_fingerprint"] = fingerprint.fingerprint
        case["semantic_signature"] = signature
        case["required_tables"] = signature["table_set"]
        case["secondary_phenotypes"] = [PHENOTYPE_BY_FAMILY[case["primary_diagnostic_family"]]]
        case["expected_grain"] = case["primary_diagnostic_family"]
        if case["expected_route"] == "GOVERNED":
            contract, spec = _governed_contract(case)
            case["contract_id"] = f"m104s-contract-{case['case_id']}"
            case["contract"] = contract
            case["binding_spec"] = spec
        else:
            case["contract_id"] = None
            case["contract"] = None
            case["binding_spec"] = None
    for index, case in enumerate(cases):
        case["partition"] = "DIAG_A" if (index < 40 and index % 4 < 2) or (index >= 40 and index % 10 < 5) else "DIAG_B"
    return tuple(cases)


def _governed_contract(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from evaluation.m10_corpus import _contract_and_spec

    contract, spec = _contract_and_spec(case["reference_sql"], case["case_id"])
    return _contract_json(contract), _binding_json(spec)


def _historical_questions() -> set[str]:
    values: set[str] = set()
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name.startswith("m104s_") or path.name.startswith("m104r_"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stack: list[Any] = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"question", "prompt"} and isinstance(item, str):
                        values.add(_normalized_question(item))
                    if isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(value, list):
                stack.extend(value)
    values.update(_normalized_question(example.question) for example in build_memory_corpus())
    return values


def _historical_index() -> dict[str, tuple[dict[str, Any], ...]]:
    payload = json.loads(CANONICAL_INDEX_PATH.read_text(encoding="utf-8"))
    return {key: tuple(value) for key, value in payload["fingerprints"].items()}


def freshness_report(cases: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    historical_questions = _historical_questions()
    consumed_manifest = json.loads(
        (FIXTURES / "m104r_consumed_reference_manifest.json").read_text(encoding="utf-8")
    )
    historical_raw_hashes = {
        entry["raw_reference_hash"] for entry in consumed_manifest["entries"]
    }
    index = _historical_index()
    m4_questions = {_normalized_question(example.question) for example in build_memory_corpus()}
    m4_raw_hashes = {sha256(example.sql.encode()).hexdigest() for example in build_memory_corpus()}
    question_values = [_normalized_question(case["question"]) for case in cases]
    reference_values = [case["reference_sql"] for case in cases]
    raw_hashes = [sha256(value.encode()).hexdigest() for value in reference_values]
    canonical_values = [case["canonical_reference_fingerprint"] for case in cases]
    canonical_matches = [
        {"case_id": case["case_id"], "fingerprint": fingerprint, "sources": index[fingerprint]}
        for case, fingerprint in zip(cases, canonical_values, strict=True)
        if fingerprint in index
    ]
    duplicate_questions = len(cases) - len(set(question_values))
    duplicate_pairs = len(cases) - len(set(zip(question_values, raw_hashes, strict=True)))
    exact_question = sum(value in historical_questions for value in question_values)
    exact_raw = sum(value in historical_raw_hashes for value in raw_hashes)
    m4_question = sum(value in m4_questions for value in question_values)
    m4_raw_overlap = sum(value in m4_raw_hashes for value in raw_hashes)
    invalid_cases = json.loads((FIXTURES / "m104_corpus.json").read_text())["cases"]
    invalid_questions = {_normalized_question(case["question"]) for case in invalid_cases}
    invalid_raw = {sha256(case["reference_sql"].encode()).hexdigest() for case in invalid_cases}
    invalid_question = sum(value in invalid_questions for value in question_values)
    invalid_canonical_values = {
        canonical_reference_fingerprint(case["reference_sql"]).fingerprint
        for case in invalid_cases
    }
    invalid_canonical = sum(value in invalid_canonical_values for value in canonical_values)
    report = {
        "contract_id": "decision-sql-reference-freshness-v1",
        "historical_manifest_hash": CONSUMED_MANIFEST_HASH,
        "cases": len(cases),
        "duplicate_case_ids": len(cases) - len({case["case_id"] for case in cases}),
        "duplicate_questions": duplicate_questions,
        "duplicate_question_reference_pairs": duplicate_pairs,
        "exact_historical_question_overlap": exact_question,
        "normalized_historical_question_overlap": exact_question,
        "exact_raw_reference_overlap": exact_raw,
        "canonical_reference_overlap": len(canonical_matches),
        "canonical_matches": canonical_matches,
        "m4_exact_question_overlap": m4_question,
        "m4_exact_raw_reference_overlap": m4_raw_overlap,
        "m4_canonical_reference_overlap": sum(
            any(entry.get("source_milestone") == "M4" or entry.get("consumption_role") == "MEMORY" for entry in index.get(value, ()))
            for value in canonical_values
        ),
        "invalid_m104_exact_question_overlap": invalid_question,
        "invalid_m104_normalized_question_overlap": invalid_question,
        "invalid_m104_raw_reference_overlap": sum(value in invalid_raw for value in raw_hashes),
        "invalid_m104_canonical_reference_overlap": invalid_canonical,
        "status": "PASS" if not any((
            duplicate_questions,
            duplicate_pairs,
            exact_question,
            exact_raw,
            canonical_matches,
            m4_question,
            m4_raw_overlap,
            invalid_question,
            invalid_canonical,
        )) else "FAIL",
    }
    return report


def artifact_payloads() -> dict[str, Any]:
    cases = build_cases()
    freshness = freshness_report(cases)
    if freshness["status"] != "PASS":
        raise ValueError(f"M10.4S first freshness gate failed: {freshness}")
    governed = [case for case in cases if case["expected_route"] == "GOVERNED"]
    direct = [case for case in cases if case["expected_route"] == "DIRECT"]
    gold = {
        case["case_id"]: {
            key: case[key]
            for key in (
                "case_id", "question", "expected_route", "primary_diagnostic_family",
                "governed_metric", "dimensions", "reference_sql", "reference_sql_hash",
                "canonical_reference_fingerprint", "semantic_signature", "required_tables",
                "secondary_phenotypes", "expected_grain",
            )
            if key in case
        }
        for case in cases
    }
    return {
        "corpus_recipe": {
            "corpus_id": CORPUS_ID,
            "corpus_version": CORPUS_VERSION,
            "starting_checkpoint": STARTING_CHECKPOINT,
            "total_cases": 160,
            "expected_governed": 40,
            "expected_direct": 120,
            "governed_metric_quota": {metric: 4 for metric in METRICS},
            "direct_family_quota": {family: 10 for family in DIRECT_FAMILIES},
            "new_case_id_prefix": "m104s-",
            "construction_policy": "new benchmark-owned questions and references authored after the M10.4R checkpoint",
        },
        "corpus": {
            "corpus_id": CORPUS_ID,
            "corpus_version": CORPUS_VERSION,
            "starting_checkpoint": STARTING_CHECKPOINT,
            "cases": list(cases),
            "total_cases": 160,
            "quotas": {"expected_governed": len(governed), "expected_direct": len(direct)},
        },
        "semantic_gold": {"corpus_version": CORPUS_VERSION, "cases": gold},
        "family_manifest": {
            "corpus_version": CORPUS_VERSION,
            "governed_metrics": {metric: 4 for metric in METRICS},
            "direct_families": {family: 10 for family in DIRECT_FAMILIES},
        },
        "partition_manifest": {
            "corpus_version": CORPUS_VERSION,
            "recipe": "governed ordinal modulo-four and direct-family ordinal half split",
            "partitions": {
                part: [case["case_id"] for case in cases if case["partition"] == part]
                for part in ("DIAG_A", "DIAG_B")
            },
        },
        "first_freshness": freshness,
    }


def write_artifacts() -> dict[str, str]:
    payloads = artifact_payloads()
    paths = {
        "corpus_recipe": FIXTURES / "m104s_corpus_recipe.json",
        "corpus": FIXTURES / "m104s_corpus.json",
        "semantic_gold": FIXTURES / "m104s_semantic_gold.json",
        "family_manifest": FIXTURES / "m104s_family_manifest.json",
        "partition_manifest": FIXTURES / "m104s_partition_manifest.json",
        "first_freshness": FIXTURES / "m104s_first_freshness.json",
    }
    hashes: dict[str, str] = {}
    for key, path in paths.items():
        path.write_text(json.dumps(payloads[key], ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        hashes[key] = sha256(path.read_bytes()).hexdigest()
    return hashes


if __name__ == "__main__":
    print(json.dumps(write_artifacts(), indent=2, sort_keys=True))
