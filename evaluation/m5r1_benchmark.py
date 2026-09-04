"""Frozen, SQL-free M5R.1 selective-answering benchmark.

This module is evaluation-only.  Cases are manually authored from the
server-owned demo catalog and schema contract; no provider output participates
in corpus construction, labeling, or validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CORPUS_ID = "decisionsql-selective-answering"
CORPUS_VERSION = "m5r1-selective-answering-v1"
SCHEMA_VERSION = "decision-sql-demo-schema-v1"
_DEV_GROUPS = frozenset(
    (*range(1, 8), *range(11, 18), *range(21, 28), *range(31, 38), *range(41, 44), *range(46, 48))
)


class SelectiveAction(StrEnum):
    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    ABSTAIN = "ABSTAIN"


class NaturalnessStatus(StrEnum):
    NATURAL = "NATURAL"
    BORDERLINE = "BORDERLINE"


class SelectiveAnsweringCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    gold_action: SelectiveAction
    reason_family: str = Field(min_length=1)
    supported_interpretations: tuple[str, ...] = ()
    canonical_interpretation: str | None = None
    required_clarification_slot: str | None = None
    missing_concept: str | None = None
    relevant_schema_objects: tuple[str, ...] = ()
    relevant_semantic_ids: tuple[str, ...] = ()
    adjudication_note: str = Field(min_length=1)
    split: str
    subtype: str
    naturalness_status: NaturalnessStatus = NaturalnessStatus.NATURAL
    contrast_group_id: str


@dataclass(frozen=True)
class _AnswerSpec:
    question: str
    subtype: str
    interpretation: str
    schema_objects: tuple[str, ...]
    semantic_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ClarifySpec:
    question: str
    family: str
    interpretations: tuple[str, ...]
    slot: str
    schema_objects: tuple[str, ...]
    semantic_ids: tuple[str, ...]


@dataclass(frozen=True)
class _AbstainSpec:
    question: str
    family: str
    missing_concept: str
    schema_objects: tuple[str, ...]
    semantic_ids: tuple[str, ...]


def _answer(
    question: str,
    subtype: str,
    interpretation: str,
    schema_objects: tuple[str, ...],
    semantic_ids: tuple[str, ...],
) -> _AnswerSpec:
    return _AnswerSpec(question, subtype, interpretation, schema_objects, semantic_ids)


def _clarify(
    question: str,
    family: str,
    interpretations: tuple[str, ...],
    slot: str,
    schema_objects: tuple[str, ...],
    semantic_ids: tuple[str, ...],
) -> _ClarifySpec:
    return _ClarifySpec(question, family, interpretations, slot, schema_objects, semantic_ids)


def _abstain(
    question: str,
    family: str,
    missing_concept: str,
    schema_objects: tuple[str, ...],
    semantic_ids: tuple[str, ...],
) -> _AbstainSpec:
    return _AbstainSpec(question, family, missing_concept, schema_objects, semantic_ids)


_ANSWERS: tuple[_AnswerSpec, ...] = (
    # A1 — explicit governed metric
    _answer(
        "Show completed revenue by region.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:completed_revenue|dimension:region",
        ("regions", "orders"),
        ("metric:completed_revenue", "dimension:region"),
    ),
    _answer(
        "Show average completed order value by customer.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:average_completed_order_value|dimension:customer",
        ("customers", "orders"),
        ("metric:average_completed_order_value", "dimension:customer"),
    ),
    _answer(
        "Show average payment amount by sales representative.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:average_payment_amount|dimension:sales_representative",
        ("sales_representatives", "orders", "payments"),
        ("metric:average_payment_amount", "dimension:sales_representative"),
    ),
    _answer(
        "Show completed item quantity by product category.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:completed_item_quantity|dimension:product_category",
        ("products", "order_items", "orders"),
        ("metric:completed_item_quantity", "dimension:product_category"),
    ),
    _answer(
        "Show payment success rate by region.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:payment_success_rate|dimension:region",
        ("regions", "customers", "orders", "payments"),
        ("metric:payment_success_rate", "dimension:region"),
    ),
    _answer(
        "Show refunded order rate by sales representative.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:refunded_order_rate|dimension:sales_representative",
        ("sales_representatives", "orders", "refunds"),
        ("metric:refunded_order_rate", "dimension:sales_representative"),
    ),
    _answer(
        "Show refund to revenue rate by order currency.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:refund_to_revenue_rate|dimension:order_currency",
        ("orders", "refunds"),
        ("metric:refund_to_revenue_rate", "dimension:order_currency"),
    ),
    _answer(
        "Show average items per completed order by product category.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:average_items_per_completed_order|dimension:product_category",
        ("products", "order_items", "orders"),
        ("metric:average_items_per_completed_order", "dimension:product_category"),
    ),
    _answer(
        "Show completed order rate by customer.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:completed_order_rate|dimension:customer",
        ("customers", "orders"),
        ("metric:completed_order_rate", "dimension:customer"),
    ),
    _answer(
        "Show customer order coverage rate by sales representative.",
        "A1_EXPLICIT_GOVERNED_METRIC",
        "metric:customer_order_coverage_rate|dimension:sales_representative",
        ("customers", "orders", "sales_representatives"),
        ("metric:customer_order_coverage_rate", "dimension:sales_representative"),
    ),
    # A2 — governed default resolves surface ambiguity
    _answer(
        "Show refund rate by region.",
        "A2_GOVERNED_DEFAULT",
        "metric:refunded_order_rate|dimension:region",
        ("regions", "orders", "refunds"),
        ("metric:refunded_order_rate", "dimension:region"),
    ),
    _answer(
        "What is our completed revenue by currency?",
        "A2_GOVERNED_DEFAULT",
        "metric:completed_revenue|dimension:order_currency",
        ("orders",),
        ("metric:completed_revenue", "dimension:order_currency"),
    ),
    _answer(
        "Give me the average order value by region.",
        "A2_GOVERNED_DEFAULT",
        "metric:average_completed_order_value|dimension:region",
        ("regions", "customers", "orders"),
        ("metric:average_completed_order_value", "dimension:region"),
    ),
    _answer(
        "How successful are payments by region?",
        "A2_GOVERNED_DEFAULT",
        "metric:payment_success_rate|dimension:region",
        ("regions", "customers", "orders", "payments"),
        ("metric:payment_success_rate", "dimension:region"),
    ),
    _answer(
        "What share of orders were completed?",
        "A2_GOVERNED_DEFAULT",
        "metric:completed_order_rate",
        ("orders",),
        ("metric:completed_order_rate",),
    ),
    _answer(
        "How much of completed revenue was refunded?",
        "A2_GOVERNED_DEFAULT",
        "metric:refund_to_revenue_rate",
        ("orders", "refunds"),
        ("metric:refund_to_revenue_rate",),
    ),
    _answer(
        "How many items are in a completed order on average?",
        "A2_GOVERNED_DEFAULT",
        "metric:average_items_per_completed_order",
        ("orders", "order_items"),
        ("metric:average_items_per_completed_order",),
    ),
    _answer(
        "What percentage of customers have placed an order?",
        "A2_GOVERNED_DEFAULT",
        "metric:customer_order_coverage_rate",
        ("customers", "orders"),
        ("metric:customer_order_coverage_rate",),
    ),
    _answer(
        "What is the typical payment amount by customer?",
        "A2_GOVERNED_DEFAULT",
        "metric:average_payment_amount|dimension:customer",
        ("customers", "orders", "payments"),
        ("metric:average_payment_amount", "dimension:customer"),
    ),
    _answer(
        "Show the order completion percentage by sales representative.",
        "A2_GOVERNED_DEFAULT",
        "metric:completed_order_rate|dimension:sales_representative",
        ("sales_representatives", "orders"),
        ("metric:completed_order_rate", "dimension:sales_representative"),
    ),
    # A3 — clear direct simple
    _answer(
        "List all customers with their region names.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:customer|entity:region|relationship:customer_to_region",
        ("customers", "regions"),
        ("entity:customer", "entity:region", "relationship:customer_to_region"),
    ),
    _answer(
        "List every product and its category.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:product|dimension:product_category",
        ("products",),
        ("entity:product", "dimension:product_category"),
    ),
    _answer(
        "Show each order number and order status.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:order|dimension:order_status",
        ("orders",),
        ("entity:order", "dimension:order_status"),
    ),
    _answer(
        "List payment identifiers and amounts.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:payment|measure:average_payment_amount",
        ("payments",),
        ("entity:payment", "measure:average_payment_amount"),
    ),
    _answer(
        "Show every refund reason and amount.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:refund|dimension:refund_reason",
        ("refunds",),
        ("entity:refund", "dimension:refund_reason"),
    ),
    _answer(
        "List representatives responsible for each customer.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:customer|entity:sales_representative|relationship:customer_to_sales_representative",
        ("customers", "sales_representatives"),
        (
            "entity:customer",
            "entity:sales_representative",
            "relationship:customer_to_sales_representative",
        ),
    ),
    _answer(
        "Show all order currencies.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:order|dimension:order_currency",
        ("orders",),
        ("entity:order", "dimension:order_currency"),
    ),
    _answer(
        "List products priced above 100.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:product|filter:unit_price",
        ("products",),
        ("entity:product",),
    ),
    _answer(
        "Show completed orders.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:order|filter:order_status",
        ("orders",),
        ("entity:order", "dimension:order_status"),
    ),
    _answer(
        "List orders with their payment statuses.",
        "A3_CLEAR_DIRECT_SIMPLE",
        "entity:order|entity:payment|relationship:payment_to_order",
        ("orders", "payments"),
        ("entity:order", "entity:payment", "relationship:payment_to_order"),
    ),
    # A4 — clear direct complex / M4-style
    _answer(
        "Show the top five product categories by total item quantity.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:product|measure:completed_item_quantity|dimension:product_category|ranking:quantity",
        ("products", "order_items", "orders"),
        ("entity:product", "measure:completed_item_quantity", "dimension:product_category"),
    ),
    _answer(
        "Find customers with more than four orders.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:customer|measure:all_order_count|filter:count_threshold",
        ("customers", "orders"),
        ("entity:customer", "measure:all_order_count"),
    ),
    _answer(
        "Show order totals by region for completed orders.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:order|dimension:region|filter:order_status|measure:completed_revenue",
        ("regions", "customers", "orders"),
        ("entity:order", "dimension:region", "metric:completed_revenue"),
    ),
    _answer(
        "List products whose price is above the average product price.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:product|measure:unit_price|comparison:average",
        ("products",),
        ("entity:product",),
    ),
    _answer(
        "Show the representatives with the most orders.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:sales_representative|measure:all_order_count|ranking:order_count",
        ("sales_representatives", "orders"),
        ("entity:sales_representative", "measure:all_order_count"),
    ),
    _answer(
        "Compare average payment amounts across order currencies.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:payment|dimension:order_currency|measure:average_payment_amount",
        ("orders", "payments"),
        ("entity:payment", "dimension:order_currency", "metric:average_payment_amount"),
    ),
    _answer(
        "Show refund totals for each order status.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:refund|dimension:order_status|measure:completed_refund_amount",
        ("orders", "refunds"),
        ("entity:refund", "dimension:order_status"),
    ),
    _answer(
        "Count distinct orders represented in each product category.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:order|dimension:product_category|aggregation:count_distinct",
        ("products", "order_items", "orders"),
        ("entity:order", "dimension:product_category"),
    ),
    _answer(
        "Show customers whose order count is above the customer average.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:customer|aggregation:count|comparison:average",
        ("customers", "orders"),
        ("entity:customer",),
    ),
    _answer(
        "Classify orders as large or standard using total amount.",
        "A4_CLEAR_DIRECT_COMPLEX",
        "entity:order|case:total_amount",
        ("orders",),
        ("entity:order",),
    ),
    # A5 — clear filtered analytics
    _answer(
        "Sum completed order totals for each currency.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "metric:completed_revenue|dimension:order_currency",
        ("orders",),
        ("metric:completed_revenue", "dimension:order_currency"),
    ),
    _answer(
        "Count completed orders in each region.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "metric:completed_order_rate|dimension:region|filter:completed",
        ("regions", "customers", "orders"),
        ("measure:completed_order_count", "dimension:region"),
    ),
    _answer(
        "Average settled payment amount for each region.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "metric:average_payment_amount|dimension:region|filter:settled",
        ("regions", "customers", "orders", "payments"),
        ("metric:average_payment_amount", "dimension:region"),
    ),
    _answer(
        "Total quantity sold for products in each category.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "measure:completed_item_quantity|dimension:product_category",
        ("products", "order_items"),
        ("metric:completed_item_quantity", "dimension:product_category"),
    ),
    _answer(
        "Show refunds attached to completed orders.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "entity:refund|filter:completed_order",
        ("orders", "refunds"),
        ("entity:refund",),
    ),
    _answer(
        "Count orders with status cancelled.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "entity:order|filter:order_status",
        ("orders",),
        ("entity:order", "dimension:order_status"),
    ),
    _answer(
        "Show payment totals for orders in each region.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "entity:payment|dimension:region|relationship:payment_to_order",
        ("regions", "customers", "orders", "payments"),
        ("entity:payment", "dimension:region"),
    ),
    _answer(
        "Find products with unit price below 200.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "entity:product|filter:unit_price",
        ("products",),
        ("entity:product",),
    ),
    _answer(
        "Show customers with at least two orders.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "entity:customer|filter:count_threshold",
        ("customers", "orders"),
        ("entity:customer",),
    ),
    _answer(
        "Show orders that have a refund.",
        "A5_CLEAR_FILTERED_ANALYTICS",
        "entity:order|relationship:refund_to_order",
        ("orders", "refunds"),
        ("entity:order", "relationship:refund_to_order"),
    ),
)


_CLARIFICATIONS: tuple[_ClarifySpec, ...] = (
    # C1 — ambiguous metric
    _clarify(
        "Show the best products.",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_revenue", "metric:completed_item_quantity"),
        "ranking_metric",
        ("products", "orders", "order_items"),
        ("metric:completed_revenue", "metric:completed_item_quantity"),
    ),
    _clarify(
        "Which products performed best?",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_revenue", "metric:completed_item_quantity"),
        "ranking_metric",
        ("products", "orders", "order_items"),
        ("metric:completed_revenue", "metric:completed_item_quantity"),
    ),
    _clarify(
        "Show customer performance.",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_revenue", "metric:completed_order_rate"),
        "performance_metric",
        ("customers", "orders"),
        ("metric:completed_revenue", "metric:completed_order_rate"),
    ),
    _clarify(
        "What are the strongest regions?",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_revenue", "metric:completed_item_quantity"),
        "ranking_metric",
        ("regions", "customers", "orders", "order_items"),
        ("metric:completed_revenue", "metric:completed_item_quantity"),
    ),
    _clarify(
        "Show sales by region.",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_revenue", "metric:completed_item_quantity"),
        "sales_measure",
        ("regions", "orders", "order_items"),
        ("metric:completed_revenue", "metric:completed_item_quantity"),
    ),
    _clarify(
        "Give me the most valuable customers.",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_revenue", "metric:average_completed_order_value"),
        "customer_value_metric",
        ("customers", "orders"),
        ("metric:completed_revenue", "metric:average_completed_order_value"),
    ),
    _clarify(
        "Show the leading representatives.",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_revenue", "metric:completed_order_rate"),
        "representative_metric",
        ("sales_representatives", "orders"),
        ("metric:completed_revenue", "metric:completed_order_rate"),
    ),
    _clarify(
        "Which categories are strongest?",
        "C1_AMBIGUOUS_METRIC",
        ("metric:completed_item_quantity", "metric:completed_revenue"),
        "category_metric",
        ("products", "order_items", "orders"),
        ("metric:completed_item_quantity", "metric:completed_revenue"),
    ),
    _clarify(
        "Show our refund performance.",
        "C1_AMBIGUOUS_METRIC",
        ("metric:refunded_order_rate", "metric:refund_to_revenue_rate"),
        "refund_metric",
        ("orders", "refunds"),
        ("metric:refunded_order_rate", "metric:refund_to_revenue_rate"),
    ),
    _clarify(
        "Show the most successful payment groups.",
        "C1_AMBIGUOUS_METRIC",
        ("metric:payment_success_rate", "metric:average_payment_amount"),
        "payment_metric",
        ("payments", "orders"),
        ("metric:payment_success_rate", "metric:average_payment_amount"),
    ),
    # C2 — ambiguous filter target
    _clarify(
        "Show large orders.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("filter:orders.total_amount", "filter:orders.item_quantity"),
        "large_measure",
        ("orders", "order_items"),
        ("entity:order",),
    ),
    _clarify(
        "Find high-value customers.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("metric:completed_revenue", "metric:average_completed_order_value"),
        "customer_value_definition",
        ("customers", "orders"),
        ("metric:completed_revenue", "metric:average_completed_order_value"),
    ),
    _clarify(
        "Show important products.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("filter:products.unit_price", "metric:completed_item_quantity"),
        "importance_measure",
        ("products", "order_items"),
        ("entity:product",),
    ),
    _clarify(
        "List expensive orders.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("filter:orders.total_amount", "filter:orders.subtotal"),
        "order_value_column",
        ("orders",),
        ("entity:order",),
    ),
    _clarify(
        "Find active customers.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("filter:orders.status", "filter:customers.order_presence"),
        "active_definition",
        ("customers", "orders"),
        ("entity:customer",),
    ),
    _clarify(
        "Show costly refunds.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("filter:refunds.amount", "filter:orders.total_amount"),
        "cost_basis",
        ("refunds", "orders"),
        ("entity:refund",),
    ),
    _clarify(
        "List valuable payments.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("filter:payments.amount", "filter:orders.total_amount"),
        "payment_value_basis",
        ("payments", "orders"),
        ("entity:payment",),
    ),
    _clarify(
        "Show successful customers.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("metric:completed_order_rate", "metric:customer_order_coverage_rate"),
        "success_definition",
        ("customers", "orders"),
        ("metric:completed_order_rate", "metric:customer_order_coverage_rate"),
    ),
    _clarify(
        "Find large product categories.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("metric:completed_item_quantity", "metric:completed_revenue"),
        "category_size_metric",
        ("products", "orders", "order_items"),
        ("metric:completed_item_quantity", "metric:completed_revenue"),
    ),
    _clarify(
        "Show preferred regions.",
        "C2_AMBIGUOUS_FILTER_TARGET",
        ("metric:completed_revenue", "metric:customer_order_coverage_rate"),
        "region_preference_metric",
        ("regions", "customers", "orders"),
        ("metric:completed_revenue", "metric:customer_order_coverage_rate"),
    ),
    # C3 — ambiguous filter criteria
    _clarify(
        "Show high-value customers.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:orders.total_amount>threshold", "filter:count(orders)>threshold"),
        "threshold",
        ("customers", "orders"),
        ("entity:customer",),
    ),
    _clarify(
        "Find frequent buyers.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        (
            "filter:orders.count>=threshold",
            "filter:orders.recency",
        ),
        "frequency_criterion",
        ("customers", "orders"),
        ("entity:customer",),
    ),
    _clarify(
        "Show large payments.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:payments.amount>threshold", "filter:payments.count>=threshold"),
        "threshold",
        ("payments",),
        ("entity:payment",),
    ),
    _clarify(
        "List important refunds.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:refunds.amount>threshold", "filter:refunds.status"),
        "importance_criterion",
        ("refunds",),
        ("entity:refund",),
    ),
    _clarify(
        "Find strong representatives.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:orders.count>=threshold", "filter:orders.total_amount>threshold"),
        "strength_criterion",
        ("sales_representatives", "orders"),
        ("entity:sales_representative",),
    ),
    _clarify(
        "Show healthy order activity.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:orders.status", "filter:orders.count>=threshold"),
        "activity_criterion",
        ("orders",),
        ("entity:order",),
    ),
    _clarify(
        "Which products are doing well?",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:item_quantity>threshold", "filter:revenue>threshold"),
        "success_criterion",
        ("products", "orders", "order_items"),
        ("entity:product",),
    ),
    _clarify(
        "Show customers with substantial activity.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:order_count>=threshold", "filter:completed_revenue>threshold"),
        "activity_threshold",
        ("customers", "orders"),
        ("entity:customer",),
    ),
    _clarify(
        "Find regions with strong sales.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("filter:revenue>threshold", "filter:order_count>=threshold"),
        "region_threshold",
        ("regions", "orders"),
        ("entity:region",),
    ),
    _clarify(
        "Show customers above average.",
        "C3_AMBIGUOUS_FILTER_CRITERIA",
        ("comparison:average_order_value", "comparison:average_order_count"),
        "comparison_measure",
        ("customers", "orders"),
        ("entity:customer",),
    ),
    # C4 — ambiguous population
    _clarify(
        "Show refund rate.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:refunded_order_rate", "metric:refund_to_revenue_rate"),
        "refund_population",
        ("orders", "refunds"),
        ("metric:refunded_order_rate", "metric:refund_to_revenue_rate"),
    ),
    _clarify(
        "Show order completion.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:completed_order_rate", "filter:orders.status=completed"),
        "completion_output",
        ("orders",),
        ("metric:completed_order_rate",),
    ),
    _clarify(
        "Show payment success.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:payment_success_rate", "filter:payments.status=settled"),
        "success_output",
        ("payments",),
        ("metric:payment_success_rate",),
    ),
    _clarify(
        "Show customer coverage.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:customer_order_coverage_rate", "entity:customer_with_orders"),
        "coverage_definition",
        ("customers", "orders"),
        ("metric:customer_order_coverage_rate",),
    ),
    _clarify(
        "Show completed sales.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:completed_revenue", "metric:completed_item_quantity"),
        "sales_population",
        ("orders", "order_items"),
        ("metric:completed_revenue", "metric:completed_item_quantity"),
    ),
    _clarify(
        "Show average order value.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:average_completed_order_value", "measure:average_order_total_all_statuses"),
        "order_population",
        ("orders",),
        ("metric:average_completed_order_value",),
    ),
    _clarify(
        "Show refund amount.",
        "C4_AMBIGUOUS_POPULATION",
        ("measure:completed_refund_amount", "measure:refund_amount_all_orders"),
        "refund_population",
        ("refunds", "orders"),
        ("measure:completed_refund_amount",),
    ),
    _clarify(
        "Show order count.",
        "C4_AMBIGUOUS_POPULATION",
        ("measure:all_order_count", "measure:completed_order_count"),
        "order_population",
        ("orders",),
        ("measure:completed_order_count",),
    ),
    _clarify(
        "Show item quantity.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:completed_item_quantity", "measure:item_quantity_all_orders"),
        "item_population",
        ("order_items", "orders"),
        ("metric:completed_item_quantity",),
    ),
    _clarify(
        "Show average payment.",
        "C4_AMBIGUOUS_POPULATION",
        ("metric:average_payment_amount", "filter:payments.status=settled"),
        "payment_population",
        ("payments",),
        ("metric:average_payment_amount",),
    ),
    # C5 — ambiguous top-N basis
    _clarify(
        "Show the top five products.",
        "C5_AMBIGUOUS_TOP_N_BASIS",
        ("ranking:completed_revenue", "ranking:completed_item_quantity"),
        "ranking_basis",
        ("products", "orders", "order_items"),
        ("metric:completed_revenue", "metric:completed_item_quantity"),
    ),
    _clarify(
        "Show the top three customers.",
        "C5_AMBIGUOUS_TOP_N_BASIS",
        ("ranking:completed_revenue", "ranking:all_order_count"),
        "ranking_basis",
        ("customers", "orders"),
        ("metric:completed_revenue", "measure:all_order_count"),
    ),
    _clarify(
        "Show the highest regions.",
        "C5_AMBIGUOUS_TOP_N_BASIS",
        ("ranking:completed_revenue", "ranking:order_count"),
        "ranking_basis",
        ("regions", "customers", "orders"),
        ("metric:completed_revenue",),
    ),
    _clarify(
        "List the best representatives.",
        "C5_AMBIGUOUS_TOP_N_BASIS",
        ("ranking:completed_revenue", "ranking:all_order_count"),
        "ranking_basis",
        ("sales_representatives", "orders"),
        ("metric:completed_revenue", "measure:all_order_count"),
    ),
    _clarify(
        "Which refunds are the largest?",
        "C5_AMBIGUOUS_TOP_N_BASIS",
        ("ranking:refund_amount", "ranking:refunded_order_count"),
        "ranking_basis",
        ("refunds", "orders"),
        ("measure:completed_refund_amount",),
    ),
    # C6 — natural ambiguous dimension
    _clarify(
        "Show order performance by sales.",
        "C6_AMBIGUOUS_DIMENSION",
        ("dimension:sales_representative", "dimension:order_status"),
        "sales_dimension",
        ("sales_representatives", "orders"),
        ("dimension:sales_representative", "dimension:order_status"),
    ),
    _clarify(
        "Break down payments by status or region.",
        "C6_AMBIGUOUS_DIMENSION",
        ("dimension:payment_status", "dimension:region"),
        "payment_dimension",
        ("payments", "regions", "orders"),
        ("dimension:payment_status", "dimension:region"),
    ),
    _clarify(
        "Show customer results by area.",
        "C6_AMBIGUOUS_DIMENSION",
        ("dimension:region", "dimension:sales_representative"),
        "customer_dimension",
        ("customers", "regions", "sales_representatives"),
        ("dimension:region", "dimension:sales_representative"),
    ),
    _clarify(
        "Compare refunds across groups.",
        "C6_AMBIGUOUS_DIMENSION",
        ("dimension:refund_reason", "dimension:order_status"),
        "refund_dimension",
        ("refunds", "orders"),
        ("dimension:refund_reason", "dimension:order_status"),
    ),
    _clarify(
        "Show product results by type.",
        "C6_AMBIGUOUS_DIMENSION",
        ("dimension:product_category", "entity:product"),
        "product_dimension",
        ("products",),
        ("dimension:product_category", "entity:product"),
    ),
)


_ABSTAINS: tuple[_AbstainSpec, ...] = (
    # U1 — missing select concept
    _abstain(
        "Show customer marital status.",
        "U1_MISSING_SELECT_CONCEPT",
        "customer marital status",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "Show product supplier ratings.",
        "U1_MISSING_SELECT_CONCEPT",
        "supplier ratings",
        ("products",),
        ("entity:product",),
    ),
    _abstain(
        "List employee department names.",
        "U1_MISSING_SELECT_CONCEPT",
        "employees and departments",
        (),
        (),
    ),
    _abstain(
        "Show customer lifetime satisfaction.",
        "U1_MISSING_SELECT_CONCEPT",
        "customer satisfaction",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "Show warehouse capacity by region.",
        "U1_MISSING_SELECT_CONCEPT",
        "warehouse capacity",
        ("regions",),
        ("entity:region",),
    ),
    _abstain(
        "List product brand owners.",
        "U1_MISSING_SELECT_CONCEPT",
        "product brand owner",
        ("products",),
        ("entity:product",),
    ),
    _abstain(
        "Show delivery carrier for each order.",
        "U1_MISSING_SELECT_CONCEPT",
        "delivery carrier",
        ("orders",),
        ("entity:order",),
    ),
    _abstain(
        "Show customer credit scores.",
        "U1_MISSING_SELECT_CONCEPT",
        "credit score",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "List salesperson commission amounts.",
        "U1_MISSING_SELECT_CONCEPT",
        "commission amount",
        ("sales_representatives",),
        ("entity:sales_representative",),
    ),
    _abstain(
        "Show product warranty periods.",
        "U1_MISSING_SELECT_CONCEPT",
        "warranty period",
        ("products",),
        ("entity:product",),
    ),
    # U2 — missing filter concept
    _abstain(
        "Find orders from customers in the executive segment.",
        "U2_MISSING_FILTER_CONCEPT",
        "customer occupation/segment",
        ("orders", "customers"),
        ("entity:order", "entity:customer"),
    ),
    _abstain(
        "Show products manufactured in Germany.",
        "U2_MISSING_FILTER_CONCEPT",
        "manufacturer country",
        ("products",),
        ("entity:product",),
    ),
    _abstain(
        "Find customers whose industry is healthcare.",
        "U2_MISSING_FILTER_CONCEPT",
        "customer industry",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "Show refunds for VIP customers.",
        "U2_MISSING_FILTER_CONCEPT",
        "VIP customer status",
        ("refunds", "customers"),
        ("entity:refund", "entity:customer"),
    ),
    _abstain(
        "List payments made with corporate cards.",
        "U2_MISSING_FILTER_CONCEPT",
        "card type",
        ("payments",),
        ("entity:payment",),
    ),
    _abstain(
        "Show orders for wholesale accounts.",
        "U2_MISSING_FILTER_CONCEPT",
        "account type",
        ("orders", "customers"),
        ("entity:order", "entity:customer"),
    ),
    _abstain(
        "Find products with a seasonal flag.",
        "U2_MISSING_FILTER_CONCEPT",
        "seasonal flag",
        ("products",),
        ("entity:product",),
    ),
    _abstain(
        "Show customers in the loyalty tier gold.",
        "U2_MISSING_FILTER_CONCEPT",
        "loyalty tier",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "List orders with expedited shipping.",
        "U2_MISSING_FILTER_CONCEPT",
        "shipping speed",
        ("orders",),
        ("entity:order",),
    ),
    _abstain(
        "Show refunds linked to warranty claims.",
        "U2_MISSING_FILTER_CONCEPT",
        "warranty claim",
        ("refunds",),
        ("entity:refund",),
    ),
    # U3 — unsupported relationship
    _abstain(
        "Show refunds by supplier rating.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "refund-to-supplier relationship",
        ("refunds", "products"),
        ("entity:refund", "entity:product"),
    ),
    _abstain(
        "Show payments by product supplier.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "payment-to-supplier relationship",
        ("payments", "products"),
        ("entity:payment", "entity:product"),
    ),
    _abstain(
        "Show customer orders by warehouse.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "customer-to-warehouse relationship",
        ("customers", "orders"),
        ("entity:customer", "entity:order"),
    ),
    _abstain(
        "Show regional revenue by delivery carrier.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "order-to-carrier relationship",
        ("regions", "orders"),
        ("entity:region", "entity:order"),
    ),
    _abstain(
        "Compare product quantity by employee team.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "product-to-employee-team relationship",
        ("products", "order_items"),
        ("entity:product",),
    ),
    _abstain(
        "Show refunds by customer industry.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "customer-industry relationship",
        ("refunds", "customers"),
        ("entity:refund", "entity:customer"),
    ),
    _abstain(
        "List representatives by store location.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "representative-to-store relationship",
        ("sales_representatives",),
        ("entity:sales_representative",),
    ),
    _abstain(
        "Show payment success by card network.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "payment-to-card-network relationship",
        ("payments",),
        ("entity:payment",),
    ),
    _abstain(
        "Show order totals by shipping destination.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "order-to-shipping-destination relationship",
        ("orders",),
        ("entity:order",),
    ),
    _abstain(
        "Show product sales by supplier.",
        "U3_UNSUPPORTED_RELATIONSHIP",
        "product-to-supplier relationship",
        ("products", "orders", "order_items"),
        ("entity:product",),
    ),
    # U4 — missing aggregation source
    _abstain(
        "Calculate average customer satisfaction.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "customer satisfaction measure",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "Calculate employee churn rate.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "employee churn measure",
        (),
        (),
    ),
    _abstain(
        "Calculate supplier defect rate.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "supplier defect measure",
        ("products",),
        ("entity:product",),
    ),
    _abstain(
        "Calculate delivery delay average.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "delivery delay measure",
        ("orders",),
        ("entity:order",),
    ),
    _abstain(
        "Calculate customer acquisition cost.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "acquisition cost measure",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "Calculate net promoter score.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "promoter score measure",
        (),
        (),
    ),
    _abstain(
        "Calculate inventory turnover.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "inventory measure",
        ("products",),
        ("entity:product",),
    ),
    _abstain(
        "Calculate employee utilization.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "employee utilization measure",
        (),
        (),
    ),
    _abstain(
        "Calculate shipping cost per order.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "shipping cost measure",
        ("orders",),
        ("entity:order",),
    ),
    _abstain(
        "Calculate supplier payment terms average.",
        "U4_MISSING_AGGREGATION_SOURCE",
        "supplier payment terms measure",
        ("payments",),
        ("entity:payment",),
    ),
    # U5 — out of domain
    _abstain(
        "Show employee attrition by department.", "U5_OUT_OF_DOMAIN", "employee domain", (), ()
    ),
    _abstain(
        "Forecast next quarter revenue.",
        "U5_OUT_OF_DOMAIN",
        "forecasting capability",
        ("orders",),
        ("entity:order",),
    ),
    _abstain(
        "Show website conversion rate.",
        "U5_OUT_OF_DOMAIN",
        "website session/conversion domain",
        (),
        (),
    ),
    _abstain(
        "Compare support ticket resolution times.",
        "U5_OUT_OF_DOMAIN",
        "support ticket domain",
        (),
        (),
    ),
    _abstain(
        "Show marketing campaign attribution.",
        "U5_OUT_OF_DOMAIN",
        "campaign attribution domain",
        (),
        (),
    ),
    _abstain(
        "Predict which customers will churn.",
        "U5_OUT_OF_DOMAIN",
        "prediction/churn domain",
        ("customers",),
        ("entity:customer",),
    ),
    _abstain(
        "Show supplier contract renewals.", "U5_OUT_OF_DOMAIN", "supplier contract domain", (), ()
    ),
    _abstain(
        "Analyze inventory stockouts.",
        "U5_OUT_OF_DOMAIN",
        "inventory domain",
        ("products",),
        ("entity:product",),
    ),
    _abstain("Show employee payroll costs.", "U5_OUT_OF_DOMAIN", "employee payroll domain", (), ()),
    _abstain(
        "Compare website visitors by campaign.",
        "U5_OUT_OF_DOMAIN",
        "website analytics domain",
        (),
        (),
    ),
)


def build_benchmark() -> tuple[SelectiveAnsweringCase, ...]:
    if not (len(_ANSWERS) == len(_CLARIFICATIONS) == len(_ABSTAINS) == 50):
        raise ValueError("M5R.1 requires exactly 50 cases per action")
    cases: list[SelectiveAnsweringCase] = []
    for index, (answer, clarify, abstain) in enumerate(
        zip(_ANSWERS, _CLARIFICATIONS, _ABSTAINS, strict=True), start=1
    ):
        group_id = f"m5r1-group-{index:03d}"
        split_for = {
            "answer": "DEV" if index in _DEV_GROUPS or index == 49 else "HOLDOUT",
            "clarify": "DEV" if index in _DEV_GROUPS else "HOLDOUT",
            "abstain": "DEV" if index in _DEV_GROUPS else "HOLDOUT",
        }
        cases.extend(
            (
                SelectiveAnsweringCase(
                    case_id=f"{group_id}-answer",
                    question=answer.question,
                    gold_action=SelectiveAction.ANSWER,
                    reason_family=answer.subtype,
                    supported_interpretations=(answer.interpretation,),
                    canonical_interpretation=(
                        answer.interpretation if answer.subtype == "A2_GOVERNED_DEFAULT" else None
                    ),
                    relevant_schema_objects=answer.schema_objects,
                    relevant_semantic_ids=answer.semantic_ids,
                    adjudication_note=(
                        "Exactly one supported interpretation remains after applying "
                        "governed defaults."
                    ),
                    split=split_for["answer"],
                    subtype=answer.subtype,
                    contrast_group_id=group_id,
                ),
                SelectiveAnsweringCase(
                    case_id=f"{group_id}-clarify",
                    question=clarify.question,
                    gold_action=SelectiveAction.CLARIFY,
                    reason_family=clarify.family,
                    supported_interpretations=clarify.interpretations,
                    required_clarification_slot=clarify.slot,
                    relevant_schema_objects=clarify.schema_objects,
                    relevant_semantic_ids=clarify.semantic_ids,
                    adjudication_note=(
                        "Two supported, result-changing interpretations remain unresolved "
                        "by a canonical default."
                    ),
                    split=split_for["clarify"],
                    subtype=clarify.family,
                    contrast_group_id=group_id,
                ),
                SelectiveAnsweringCase(
                    case_id=f"{group_id}-abstain",
                    question=abstain.question,
                    gold_action=SelectiveAction.ABSTAIN,
                    reason_family=abstain.family,
                    missing_concept=abstain.missing_concept,
                    relevant_schema_objects=abstain.schema_objects,
                    relevant_semantic_ids=abstain.semantic_ids,
                    adjudication_note=(
                        "The requested concept or relationship is not supported by the "
                        "current context."
                    ),
                    split=split_for["abstain"],
                    subtype=abstain.family,
                    contrast_group_id=group_id,
                ),
            )
        )
    return tuple(cases)


def canonical_payload(cases: tuple[SelectiveAnsweringCase, ...]) -> list[dict[str, Any]]:
    return [case.model_dump(mode="json") for case in cases]


def benchmark_hash(cases: tuple[SelectiveAnsweringCase, ...]) -> str:
    encoded = json.dumps(
        canonical_payload(cases), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode()).hexdigest()


def split_hash(cases: tuple[SelectiveAnsweringCase, ...], split: str) -> str:
    return benchmark_hash(tuple(case for case in cases if case.split == split))


def validate_benchmark(cases: tuple[SelectiveAnsweringCase, ...]) -> dict[str, Any]:
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("M5R.1 contains duplicate case IDs")
    if {case.split for case in cases} != {"DEV", "HOLDOUT"}:
        raise ValueError("M5R.1 must contain DEV and HOLDOUT")
    groups: dict[str, list[SelectiveAnsweringCase]] = {}
    for case in cases:
        groups.setdefault(case.contrast_group_id, []).append(case)
        if case.gold_action is SelectiveAction.ANSWER and len(case.supported_interpretations) != 1:
            raise ValueError(f"answer case must have one interpretation: {case.case_id}")
        if case.gold_action is SelectiveAction.CLARIFY and len(case.supported_interpretations) < 2:
            raise ValueError(f"clarify case must have two interpretations: {case.case_id}")
        if case.gold_action is SelectiveAction.ABSTAIN and case.supported_interpretations:
            raise ValueError(f"abstain case cannot have supported interpretations: {case.case_id}")
    if len(groups) != 50 or any(len(group) != 3 for group in groups.values()):
        raise ValueError("M5R.1 requires 50 three-case contrast groups")
    action_counts = {
        action.value: sum(case.gold_action is action for case in cases)
        for action in SelectiveAction
    }
    subtype_counts: dict[str, int] = {}
    for case in cases:
        subtype_counts[case.subtype] = subtype_counts.get(case.subtype, 0) + 1
    return {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "schema_version": SCHEMA_VERSION,
        "total": len(cases),
        "dev": sum(case.split == "DEV" for case in cases),
        "holdout": sum(case.split == "HOLDOUT" for case in cases),
        "action_counts": action_counts,
        "subtype_counts": subtype_counts,
        "contrast_group_count": len(groups),
        "corpus_hash": benchmark_hash(cases),
        "dev_hash": split_hash(cases, "DEV"),
        "holdout_hash": split_hash(cases, "HOLDOUT"),
        "case_ids": ids,
    }
