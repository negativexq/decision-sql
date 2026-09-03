"""Frozen M2.12 single-relation window capability datasets.

Gold SQL is generated from the gold semantic IR by the deterministic compiler;
the gold IR is never included in a provider prompt by the evaluation runner.
"""

# Fixture declarations keep each question and gold IR together.
# ruff: noqa: E501

import hashlib
import json
from pathlib import Path
from typing import Any

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "evaluation" / "datasets"


def _ir(
    source: str,
    pattern: str,
    outputs: tuple[str, ...],
    computations: tuple[dict[str, Any], ...],
) -> WindowQueryIR:
    return WindowQueryIR(
        source_relation=source,
        pattern=pattern,
        physical_outputs=outputs,
        computations=computations,
    )


def _order(column: str, direction: str = "ASC") -> dict[str, str]:
    return {"column": column, "direction": direction}


def _bound(kind: str, value: int | None = None) -> dict[str, Any]:
    return {"kind": kind, **({"value": value} if value is not None else {})}


def _case(case_id: str, category: str, question: str, ir: WindowQueryIR) -> dict[str, Any]:
    compiler = WindowSqlCompiler(build_default_catalog(Base.metadata))
    return {
        "id": case_id,
        "question": question,
        "category": category,
        "pattern": ir.pattern.value,
        "gold_sql": compiler.compile(ir),
        "gold_window_ir": ir.model_dump(mode="json"),
        "expected_tables": [ir.source_relation],
        "order_sensitive": False,
    }


def _family_cases(
    prefix: str, family: str, entries: list[tuple[str, WindowQueryIR]]
) -> list[dict[str, Any]]:
    return [
        _case(f"{prefix}-{index:03d}", family, question, ir)
        for index, (question, ir) in enumerate(entries, 1)
    ]


def _dev_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    cases += _family_cases(
        "m212-dev-latest",
        "latest_per_group",
        [
            (
                "For each customer, return the most recently placed order with its id, status, and timestamp.",
                _ir(
                    "orders",
                    "LATEST_PER_GROUP",
                    ("orders.customer_id", "orders.id", "orders.status", "orders.ordered_at"),
                    (
                        {
                            "pattern": "LATEST_PER_GROUP",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (_order("orders.ordered_at", "DESC"),),
                            "tie_breakers": (_order("orders.id", "DESC"),),
                        },
                    ),
                ),
            ),
            (
                "Show the newest product record in every category, including the product id, name, and price.",
                _ir(
                    "products",
                    "LATEST_PER_GROUP",
                    ("products.category", "products.id", "products.name", "products.unit_price"),
                    (
                        {
                            "pattern": "LATEST_PER_GROUP",
                            "partition_by": ("products.category",),
                            "order_by": (_order("products.id", "DESC"),),
                            "tie_breakers": (_order("products.sku", "DESC"),),
                        },
                    ),
                ),
            ),
            (
                "Find the last payment recorded for each order and show order id, payment id, and paid time.",
                _ir(
                    "payments",
                    "LATEST_PER_GROUP",
                    ("payments.order_id", "payments.id", "payments.paid_at", "payments.amount"),
                    (
                        {
                            "pattern": "LATEST_PER_GROUP",
                            "partition_by": ("payments.order_id",),
                            "order_by": (_order("payments.paid_at", "DESC"),),
                            "tie_breakers": (_order("payments.id", "DESC"),),
                        },
                    ),
                ),
            ),
            (
                "For every order, keep only its latest refund and return the order id, refund id, and reason.",
                _ir(
                    "refunds",
                    "LATEST_PER_GROUP",
                    ("refunds.order_id", "refunds.id", "refunds.reason", "refunds.refunded_at"),
                    (
                        {
                            "pattern": "LATEST_PER_GROUP",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (_order("refunds.refunded_at", "DESC"),),
                            "tie_breakers": (_order("refunds.id", "DESC"),),
                        },
                    ),
                ),
            ),
            (
                "Return the most recent line item for each order, with the order, item, product, and quantity columns.",
                _ir(
                    "order_items",
                    "LATEST_PER_GROUP",
                    (
                        "order_items.order_id",
                        "order_items.id",
                        "order_items.product_id",
                        "order_items.quantity",
                    ),
                    (
                        {
                            "pattern": "LATEST_PER_GROUP",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (_order("order_items.id", "DESC"),),
                            "tie_breakers": (),
                        },
                    ),
                ),
            ),
            (
                "For each sales region, list the newest customer id, name, and creation time.",
                _ir(
                    "customers",
                    "LATEST_PER_GROUP",
                    (
                        "customers.region_id",
                        "customers.id",
                        "customers.name",
                        "customers.created_at",
                    ),
                    (
                        {
                            "pattern": "LATEST_PER_GROUP",
                            "partition_by": ("customers.region_id",),
                            "order_by": (_order("customers.created_at", "DESC"),),
                            "tie_breakers": (_order("customers.id", "DESC"),),
                        },
                    ),
                ),
            ),
        ],
    )
    cases += _family_cases(
        "m212-dev-topn",
        "top_n_per_group",
        [
            (
                "Show the two highest-value orders for each customer.",
                _ir(
                    "orders",
                    "TOP_N_PER_GROUP",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "TOP_N_PER_GROUP",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (
                                _order("orders.total_amount", "DESC"),
                                _order("orders.id", "DESC"),
                            ),
                            "n": 2,
                            "tie_policy": "EXACT_N",
                        },
                    ),
                ),
            ),
            (
                "For every product category, return the three most expensive products by id and price.",
                _ir(
                    "products",
                    "TOP_N_PER_GROUP",
                    ("products.category", "products.id", "products.unit_price"),
                    (
                        {
                            "pattern": "TOP_N_PER_GROUP",
                            "partition_by": ("products.category",),
                            "order_by": (
                                _order("products.unit_price", "DESC"),
                                _order("products.id", "ASC"),
                            ),
                            "n": 3,
                            "tie_policy": "EXACT_N",
                        },
                    ),
                ),
            ),
            (
                "List the top item quantities within each order, keeping at most one line per tied rank.",
                _ir(
                    "order_items",
                    "TOP_N_PER_GROUP",
                    ("order_items.order_id", "order_items.id", "order_items.quantity"),
                    (
                        {
                            "pattern": "TOP_N_PER_GROUP",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (
                                _order("order_items.quantity", "DESC"),
                                _order("order_items.id", "ASC"),
                            ),
                            "n": 1,
                            "tie_policy": "INCLUDE_TIES",
                        },
                    ),
                ),
            ),
            (
                "Return the four largest payments for each order, showing payment id and amount.",
                _ir(
                    "payments",
                    "TOP_N_PER_GROUP",
                    ("payments.order_id", "payments.id", "payments.amount"),
                    (
                        {
                            "pattern": "TOP_N_PER_GROUP",
                            "partition_by": ("payments.order_id",),
                            "order_by": (
                                _order("payments.amount", "DESC"),
                                _order("payments.id", "ASC"),
                            ),
                            "n": 4,
                            "tie_policy": "EXACT_N",
                        },
                    ),
                ),
            ),
            (
                "Within each customer, keep the three largest refunds and their dates.",
                _ir(
                    "refunds",
                    "TOP_N_PER_GROUP",
                    ("refunds.order_id", "refunds.id", "refunds.amount", "refunds.refunded_at"),
                    (
                        {
                            "pattern": "TOP_N_PER_GROUP",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (
                                _order("refunds.amount", "DESC"),
                                _order("refunds.id", "ASC"),
                            ),
                            "n": 3,
                            "tie_policy": "EXACT_N",
                        },
                    ),
                ),
            ),
            (
                "For each region, return the two latest-created customers by id and creation date.",
                _ir(
                    "customers",
                    "TOP_N_PER_GROUP",
                    ("customers.region_id", "customers.id", "customers.created_at"),
                    (
                        {
                            "pattern": "TOP_N_PER_GROUP",
                            "partition_by": ("customers.region_id",),
                            "order_by": (
                                _order("customers.created_at", "DESC"),
                                _order("customers.id", "ASC"),
                            ),
                            "n": 2,
                            "tie_policy": "EXACT_N",
                        },
                    ),
                ),
            ),
        ],
    )
    cases += _family_cases(
        "m212-dev-laglead",
        "lag_lead",
        [
            (
                "For each customer order, show the amount from the preceding order chronologically.",
                _ir(
                    "orders",
                    "LAG",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "LAG",
                            "target": "orders.total_amount",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (
                                _order("orders.ordered_at", "ASC"),
                                _order("orders.id", "ASC"),
                            ),
                            "offset": 1,
                            "alias": "previous_amount",
                        },
                    ),
                ),
            ),
            (
                "Within each order, show the next payment amount in payment-time order.",
                _ir(
                    "payments",
                    "LEAD",
                    ("payments.order_id", "payments.id", "payments.amount"),
                    (
                        {
                            "pattern": "LEAD",
                            "target": "payments.amount",
                            "partition_by": ("payments.order_id",),
                            "order_by": (
                                _order("payments.paid_at", "ASC"),
                                _order("payments.id", "ASC"),
                            ),
                            "offset": 1,
                            "alias": "next_amount",
                        },
                    ),
                ),
            ),
            (
                "Return each refund and the amount of the previous refund for its order, two rows back.",
                _ir(
                    "refunds",
                    "LAG",
                    ("refunds.order_id", "refunds.id", "refunds.amount"),
                    (
                        {
                            "pattern": "LAG",
                            "target": "refunds.amount",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (
                                _order("refunds.refunded_at", "ASC"),
                                _order("refunds.id", "ASC"),
                            ),
                            "offset": 2,
                            "alias": "amount_two_back",
                        },
                    ),
                ),
            ),
            (
                "For each order, show every line and the quantity on the following line.",
                _ir(
                    "order_items",
                    "LEAD",
                    ("order_items.order_id", "order_items.id", "order_items.quantity"),
                    (
                        {
                            "pattern": "LEAD",
                            "target": "order_items.quantity",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (_order("order_items.id", "ASC"),),
                            "offset": 1,
                            "alias": "next_quantity",
                        },
                    ),
                ),
            ),
            (
                "Within a category, show each product's price and the prior product price when sorted low to high.",
                _ir(
                    "products",
                    "LAG",
                    ("products.category", "products.id", "products.unit_price"),
                    (
                        {
                            "pattern": "LAG",
                            "target": "products.unit_price",
                            "partition_by": ("products.category",),
                            "order_by": (
                                _order("products.unit_price", "ASC"),
                                _order("products.id", "ASC"),
                            ),
                            "offset": 1,
                            "alias": "prior_price",
                        },
                    ),
                ),
            ),
            (
                "For customers in each region, include the creation date of the next customer.",
                _ir(
                    "customers",
                    "LEAD",
                    ("customers.region_id", "customers.id", "customers.created_at"),
                    (
                        {
                            "pattern": "LEAD",
                            "target": "customers.created_at",
                            "partition_by": ("customers.region_id",),
                            "order_by": (
                                _order("customers.created_at", "ASC"),
                                _order("customers.id", "ASC"),
                            ),
                            "offset": 1,
                            "alias": "next_created_at",
                        },
                    ),
                ),
            ),
        ],
    )
    cases += _family_cases(
        "m212-dev-running",
        "running_aggregate",
        [
            (
                "Show each customer's orders with their cumulative total amount over order time.",
                _ir(
                    "orders",
                    "RUNNING_AGGREGATE",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "orders.total_amount",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (
                                _order("orders.ordered_at", "ASC"),
                                _order("orders.id", "ASC"),
                            ),
                            "alias": "running_amount",
                        },
                    ),
                ),
            ),
            (
                "For each order, calculate the cumulative payment amount in payment sequence.",
                _ir(
                    "payments",
                    "RUNNING_AGGREGATE",
                    ("payments.order_id", "payments.id", "payments.amount"),
                    (
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "payments.amount",
                            "partition_by": ("payments.order_id",),
                            "order_by": (
                                _order("payments.paid_at", "ASC"),
                                _order("payments.id", "ASC"),
                            ),
                            "alias": "running_payment",
                        },
                    ),
                ),
            ),
            (
                "Return line items with the running quantity total within each order.",
                _ir(
                    "order_items",
                    "RUNNING_AGGREGATE",
                    ("order_items.order_id", "order_items.id", "order_items.quantity"),
                    (
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "order_items.quantity",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (_order("order_items.id", "ASC"),),
                            "alias": "running_quantity",
                        },
                    ),
                ),
            ),
            (
                "Within every category, show the cumulative product price from cheapest to most expensive.",
                _ir(
                    "products",
                    "RUNNING_AGGREGATE",
                    ("products.category", "products.id", "products.unit_price"),
                    (
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "products.unit_price",
                            "partition_by": ("products.category",),
                            "order_by": (
                                _order("products.unit_price", "ASC"),
                                _order("products.id", "ASC"),
                            ),
                            "alias": "cumulative_price",
                        },
                    ),
                ),
            ),
            (
                "For each refund order, show a running sum of refund amounts by refund date.",
                _ir(
                    "refunds",
                    "RUNNING_AGGREGATE",
                    ("refunds.order_id", "refunds.id", "refunds.amount"),
                    (
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "refunds.amount",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (
                                _order("refunds.refunded_at", "ASC"),
                                _order("refunds.id", "ASC"),
                            ),
                            "alias": "cumulative_refund",
                        },
                    ),
                ),
            ),
            (
                "Across each sales region, show the cumulative customer id in creation order.",
                _ir(
                    "customers",
                    "RUNNING_AGGREGATE",
                    ("customers.region_id", "customers.id", "customers.created_at"),
                    (
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "customers.id",
                            "partition_by": ("customers.region_id",),
                            "order_by": (
                                _order("customers.created_at", "ASC"),
                                _order("customers.id", "ASC"),
                            ),
                            "alias": "running_customer_id",
                        },
                    ),
                ),
            ),
        ],
    )
    cases += _family_cases(
        "m212-dev-moving",
        "moving_window",
        [
            (
                "For each customer order, show a three-row moving average of total amount including the current order.",
                _ir(
                    "orders",
                    "MOVING_AGGREGATE",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "AVG",
                            "target": "orders.total_amount",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (
                                _order("orders.ordered_at", "ASC"),
                                _order("orders.id", "ASC"),
                            ),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 2),
                                "end": _bound("CURRENT_ROW"),
                            },
                            "alias": "moving_average",
                        },
                    ),
                ),
            ),
            (
                "Within each order, compute the average of the prior two payment amounts, excluding the current payment.",
                _ir(
                    "payments",
                    "MOVING_AGGREGATE",
                    ("payments.order_id", "payments.id", "payments.amount"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "AVG",
                            "target": "payments.amount",
                            "partition_by": ("payments.order_id",),
                            "order_by": (
                                _order("payments.paid_at", "ASC"),
                                _order("payments.id", "ASC"),
                            ),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 2),
                                "end": _bound("N_PRECEDING", 1),
                            },
                            "alias": "prior_two_average",
                        },
                    ),
                ),
            ),
            (
                "Show a five-row moving sum of quantities for each order's lines, including the current line.",
                _ir(
                    "order_items",
                    "MOVING_AGGREGATE",
                    ("order_items.order_id", "order_items.id", "order_items.quantity"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "order_items.quantity",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (_order("order_items.id", "ASC"),),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 4),
                                "end": _bound("CURRENT_ROW"),
                            },
                            "alias": "moving_quantity",
                        },
                    ),
                ),
            ),
            (
                "For products in a category, return a two-row moving average of unit price in descending price order.",
                _ir(
                    "products",
                    "MOVING_AGGREGATE",
                    ("products.category", "products.id", "products.unit_price"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "AVG",
                            "target": "products.unit_price",
                            "partition_by": ("products.category",),
                            "order_by": (
                                _order("products.unit_price", "DESC"),
                                _order("products.id", "DESC"),
                            ),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 1),
                                "end": _bound("CURRENT_ROW"),
                            },
                            "alias": "price_moving_average",
                        },
                    ),
                ),
            ),
            (
                "For each order, show a three-row moving average of refunds ordered by refund time.",
                _ir(
                    "refunds",
                    "MOVING_AGGREGATE",
                    ("refunds.order_id", "refunds.id", "refunds.amount"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "AVG",
                            "target": "refunds.amount",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (
                                _order("refunds.refunded_at", "ASC"),
                                _order("refunds.id", "ASC"),
                            ),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 2),
                                "end": _bound("CURRENT_ROW"),
                            },
                            "alias": "refund_moving_average",
                        },
                    ),
                ),
            ),
            (
                "Within every region, calculate the moving maximum of customer ids over the current and preceding row.",
                _ir(
                    "customers",
                    "MOVING_AGGREGATE",
                    ("customers.region_id", "customers.id", "customers.created_at"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "MAX",
                            "target": "customers.id",
                            "partition_by": ("customers.region_id",),
                            "order_by": (
                                _order("customers.created_at", "ASC"),
                                _order("customers.id", "ASC"),
                            ),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 1),
                                "end": _bound("CURRENT_ROW"),
                            },
                            "alias": "recent_max_customer",
                        },
                    ),
                ),
            ),
        ],
    )
    cases += _family_cases(
        "m212-dev-ranking",
        "ranking",
        [
            (
                "Rank orders from largest to smallest total within each customer.",
                _ir(
                    "orders",
                    "RANKING",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "RANKING",
                            "function": "RANK",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (_order("orders.total_amount", "DESC"),),
                            "alias": "amount_rank",
                        },
                    ),
                ),
            ),
            (
                "Assign a dense price rank to products within each category.",
                _ir(
                    "products",
                    "RANKING",
                    ("products.category", "products.id", "products.unit_price"),
                    (
                        {
                            "pattern": "RANKING",
                            "function": "DENSE_RANK",
                            "partition_by": ("products.category",),
                            "order_by": (_order("products.unit_price", "DESC"),),
                            "alias": "price_rank",
                        },
                    ),
                ),
            ),
            (
                "Number payments in ascending amount order separately for every order.",
                _ir(
                    "payments",
                    "RANKING",
                    ("payments.order_id", "payments.id", "payments.amount"),
                    (
                        {
                            "pattern": "RANKING",
                            "function": "ROW_NUMBER",
                            "partition_by": ("payments.order_id",),
                            "order_by": (
                                _order("payments.amount", "ASC"),
                                _order("payments.id", "ASC"),
                            ),
                            "alias": "payment_number",
                        },
                    ),
                ),
            ),
            (
                "Give refunds a dense rank by amount inside their order.",
                _ir(
                    "refunds",
                    "RANKING",
                    ("refunds.order_id", "refunds.id", "refunds.amount"),
                    (
                        {
                            "pattern": "RANKING",
                            "function": "DENSE_RANK",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (_order("refunds.amount", "DESC"),),
                            "alias": "refund_rank",
                        },
                    ),
                ),
            ),
            (
                "Number each order line from the largest quantity to the smallest within its order.",
                _ir(
                    "order_items",
                    "RANKING",
                    ("order_items.order_id", "order_items.id", "order_items.quantity"),
                    (
                        {
                            "pattern": "RANKING",
                            "function": "ROW_NUMBER",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (
                                _order("order_items.quantity", "DESC"),
                                _order("order_items.id", "ASC"),
                            ),
                            "alias": "quantity_position",
                        },
                    ),
                ),
            ),
            (
                "Rank customers by creation time within each region, newest first.",
                _ir(
                    "customers",
                    "RANKING",
                    ("customers.region_id", "customers.id", "customers.created_at"),
                    (
                        {
                            "pattern": "RANKING",
                            "function": "RANK",
                            "partition_by": ("customers.region_id",),
                            "order_by": (_order("customers.created_at", "DESC"),),
                            "alias": "customer_rank",
                        },
                    ),
                ),
            ),
        ],
    )
    cases += _family_cases(
        "m212-dev-share",
        "share_of_total",
        [
            (
                "For every order line, show its quantity as a share of that order's total quantity.",
                _ir(
                    "order_items",
                    "SHARE_OF_TOTAL",
                    ("order_items.order_id", "order_items.id", "order_items.quantity"),
                    (
                        {
                            "pattern": "SHARE_OF_TOTAL",
                            "target": "order_items.quantity",
                            "partition_by": ("order_items.order_id",),
                            "scale": 100,
                            "alias": "quantity_share_pct",
                        },
                    ),
                ),
            ),
            (
                "Show each payment amount as a percentage of the payments recorded for its order.",
                _ir(
                    "payments",
                    "SHARE_OF_TOTAL",
                    ("payments.order_id", "payments.id", "payments.amount"),
                    (
                        {
                            "pattern": "SHARE_OF_TOTAL",
                            "target": "payments.amount",
                            "partition_by": ("payments.order_id",),
                            "scale": 100,
                            "alias": "payment_share_pct",
                        },
                    ),
                ),
            ),
            (
                "For products in each category, calculate each unit price's percentage of category price total.",
                _ir(
                    "products",
                    "SHARE_OF_TOTAL",
                    ("products.category", "products.id", "products.unit_price"),
                    (
                        {
                            "pattern": "SHARE_OF_TOTAL",
                            "target": "products.unit_price",
                            "partition_by": ("products.category",),
                            "scale": 100,
                            "alias": "category_price_share",
                        },
                    ),
                ),
            ),
            (
                "Return the amount share of each refund within its order's refunds.",
                _ir(
                    "refunds",
                    "SHARE_OF_TOTAL",
                    ("refunds.order_id", "refunds.id", "refunds.amount"),
                    (
                        {
                            "pattern": "SHARE_OF_TOTAL",
                            "target": "refunds.amount",
                            "partition_by": ("refunds.order_id",),
                            "scale": 100,
                            "alias": "refund_share_pct",
                        },
                    ),
                ),
            ),
            (
                "Within each customer, express every order total as a percentage of that customer's order total.",
                _ir(
                    "orders",
                    "SHARE_OF_TOTAL",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "SHARE_OF_TOTAL",
                            "target": "orders.total_amount",
                            "partition_by": ("orders.customer_id",),
                            "scale": 100,
                            "alias": "customer_order_share",
                        },
                    ),
                ),
            ),
            (
                "Show each customer's id as its share of the id total for the region.",
                _ir(
                    "customers",
                    "SHARE_OF_TOTAL",
                    ("customers.region_id", "customers.id"),
                    (
                        {
                            "pattern": "SHARE_OF_TOTAL",
                            "target": "customers.id",
                            "partition_by": ("customers.region_id",),
                            "scale": 100,
                            "alias": "regional_id_share",
                        },
                    ),
                ),
            ),
        ],
    )
    cases += _family_cases(
        "m212-dev-mixed",
        "mixed_multi_window",
        [
            (
                "For every order, show the prior-order amount and its rank by amount within the customer.",
                _ir(
                    "orders",
                    "MIXED_MULTI_WINDOW",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "LAG",
                            "target": "orders.total_amount",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (
                                _order("orders.ordered_at", "ASC"),
                                _order("orders.id", "ASC"),
                            ),
                            "offset": 1,
                            "alias": "previous_amount",
                        },
                        {
                            "pattern": "RANKING",
                            "function": "RANK",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (_order("orders.total_amount", "DESC"),),
                            "alias": "amount_rank",
                        },
                    ),
                ),
            ),
            (
                "For each payment, return its running amount and its row number within the order.",
                _ir(
                    "payments",
                    "MIXED_MULTI_WINDOW",
                    ("payments.order_id", "payments.id", "payments.amount"),
                    (
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "payments.amount",
                            "partition_by": ("payments.order_id",),
                            "order_by": (
                                _order("payments.paid_at", "ASC"),
                                _order("payments.id", "ASC"),
                            ),
                            "alias": "running_payment",
                        },
                        {
                            "pattern": "RANKING",
                            "function": "ROW_NUMBER",
                            "partition_by": ("payments.order_id",),
                            "order_by": (
                                _order("payments.paid_at", "ASC"),
                                _order("payments.id", "ASC"),
                            ),
                            "alias": "payment_number",
                        },
                    ),
                ),
            ),
            (
                "For each line item, include the moving quantity average and a dense quantity rank within its order.",
                _ir(
                    "order_items",
                    "MIXED_MULTI_WINDOW",
                    ("order_items.order_id", "order_items.id", "order_items.quantity"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "AVG",
                            "target": "order_items.quantity",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (_order("order_items.id", "ASC"),),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 1),
                                "end": _bound("CURRENT_ROW"),
                            },
                            "alias": "moving_quantity",
                        },
                        {
                            "pattern": "RANKING",
                            "function": "DENSE_RANK",
                            "partition_by": ("order_items.order_id",),
                            "order_by": (_order("order_items.quantity", "DESC"),),
                            "alias": "quantity_rank",
                        },
                    ),
                ),
            ),
            (
                "Within each product category, show the previous price and its category price share.",
                _ir(
                    "products",
                    "MIXED_MULTI_WINDOW",
                    ("products.category", "products.id", "products.unit_price"),
                    (
                        {
                            "pattern": "LAG",
                            "target": "products.unit_price",
                            "partition_by": ("products.category",),
                            "order_by": (
                                _order("products.unit_price", "ASC"),
                                _order("products.id", "ASC"),
                            ),
                            "offset": 1,
                            "alias": "previous_price",
                        },
                        {
                            "pattern": "SHARE_OF_TOTAL",
                            "target": "products.unit_price",
                            "partition_by": ("products.category",),
                            "scale": 100,
                            "alias": "price_share_pct",
                        },
                    ),
                ),
            ),
            (
                "For each customer, show a moving average of order totals and the latest-order ranking.",
                _ir(
                    "orders",
                    "MIXED_MULTI_WINDOW",
                    ("orders.customer_id", "orders.id", "orders.total_amount"),
                    (
                        {
                            "pattern": "MOVING_AGGREGATE",
                            "aggregate": "AVG",
                            "target": "orders.total_amount",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (
                                _order("orders.ordered_at", "ASC"),
                                _order("orders.id", "ASC"),
                            ),
                            "frame": {
                                "mode": "ROWS",
                                "start": _bound("N_PRECEDING", 2),
                                "end": _bound("CURRENT_ROW"),
                            },
                            "alias": "moving_order_average",
                        },
                        {
                            "pattern": "RANKING",
                            "function": "ROW_NUMBER",
                            "partition_by": ("orders.customer_id",),
                            "order_by": (
                                _order("orders.ordered_at", "DESC"),
                                _order("orders.id", "DESC"),
                            ),
                            "alias": "latest_position",
                        },
                    ),
                ),
            ),
            (
                "For each refund, show the next refund amount and a running refund total for its order.",
                _ir(
                    "refunds",
                    "MIXED_MULTI_WINDOW",
                    ("refunds.order_id", "refunds.id", "refunds.amount"),
                    (
                        {
                            "pattern": "LEAD",
                            "target": "refunds.amount",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (
                                _order("refunds.refunded_at", "ASC"),
                                _order("refunds.id", "ASC"),
                            ),
                            "offset": 1,
                            "alias": "next_refund",
                        },
                        {
                            "pattern": "RUNNING_AGGREGATE",
                            "aggregate": "SUM",
                            "target": "refunds.amount",
                            "partition_by": ("refunds.order_id",),
                            "order_by": (
                                _order("refunds.refunded_at", "ASC"),
                                _order("refunds.id", "ASC"),
                            ),
                            "alias": "running_refund",
                        },
                    ),
                ),
            ),
        ],
    )
    return cases


def _holdout_cases() -> list[dict[str, Any]]:
    # The holdout is a separately authored set with different wording and slots.
    return (
        _family_cases(
            "m212-holdout-latest",
            "latest_per_group",
            [
                (
                    "Keep the last order row for each sales representative.",
                    _ir(
                        "orders",
                        "LATEST_PER_GROUP",
                        ("orders.sales_rep_id", "orders.id", "orders.ordered_at"),
                        (
                            {
                                "pattern": "LATEST_PER_GROUP",
                                "partition_by": ("orders.sales_rep_id",),
                                "order_by": (_order("orders.ordered_at", "DESC"),),
                                "tie_breakers": (_order("orders.id", "DESC"),),
                            },
                        ),
                    ),
                ),
                (
                    "Find the latest catalog item in each category by product id.",
                    _ir(
                        "products",
                        "LATEST_PER_GROUP",
                        ("products.category", "products.id", "products.name"),
                        (
                            {
                                "pattern": "LATEST_PER_GROUP",
                                "partition_by": ("products.category",),
                                "order_by": (_order("products.id", "DESC"),),
                                "tie_breakers": (),
                            },
                        ),
                    ),
                ),
                (
                    "For each order, retain the most recent payment event.",
                    _ir(
                        "payments",
                        "LATEST_PER_GROUP",
                        ("payments.order_id", "payments.id", "payments.paid_at"),
                        (
                            {
                                "pattern": "LATEST_PER_GROUP",
                                "partition_by": ("payments.order_id",),
                                "order_by": (_order("payments.paid_at", "DESC"),),
                                "tie_breakers": (_order("payments.id", "DESC"),),
                            },
                        ),
                    ),
                ),
                (
                    "Select the newest customer created in each region.",
                    _ir(
                        "customers",
                        "LATEST_PER_GROUP",
                        ("customers.region_id", "customers.id", "customers.created_at"),
                        (
                            {
                                "pattern": "LATEST_PER_GROUP",
                                "partition_by": ("customers.region_id",),
                                "order_by": (_order("customers.created_at", "DESC"),),
                                "tie_breakers": (_order("customers.id", "DESC"),),
                            },
                        ),
                    ),
                ),
            ],
        )
        + _family_cases(
            "m212-holdout-topn",
            "top_n_per_group",
            [
                (
                    "Return the three most costly orders per sales representative.",
                    _ir(
                        "orders",
                        "TOP_N_PER_GROUP",
                        ("orders.sales_rep_id", "orders.id", "orders.total_amount"),
                        (
                            {
                                "pattern": "TOP_N_PER_GROUP",
                                "partition_by": ("orders.sales_rep_id",),
                                "order_by": (
                                    _order("orders.total_amount", "DESC"),
                                    _order("orders.id", "ASC"),
                                ),
                                "n": 3,
                                "tie_policy": "EXACT_N",
                            },
                        ),
                    ),
                ),
                (
                    "Within a category, retain the two cheapest products.",
                    _ir(
                        "products",
                        "TOP_N_PER_GROUP",
                        ("products.category", "products.id", "products.unit_price"),
                        (
                            {
                                "pattern": "TOP_N_PER_GROUP",
                                "partition_by": ("products.category",),
                                "order_by": (
                                    _order("products.unit_price", "ASC"),
                                    _order("products.id", "ASC"),
                                ),
                                "n": 2,
                                "tie_policy": "EXACT_N",
                            },
                        ),
                    ),
                ),
                (
                    "Show up to two largest line quantities for every product.",
                    _ir(
                        "order_items",
                        "TOP_N_PER_GROUP",
                        ("order_items.product_id", "order_items.id", "order_items.quantity"),
                        (
                            {
                                "pattern": "TOP_N_PER_GROUP",
                                "partition_by": ("order_items.product_id",),
                                "order_by": (
                                    _order("order_items.quantity", "DESC"),
                                    _order("order_items.id", "ASC"),
                                ),
                                "n": 2,
                                "tie_policy": "INCLUDE_TIES",
                            },
                        ),
                    ),
                ),
                (
                    "Keep the single largest refund for every order, including amount and reason.",
                    _ir(
                        "refunds",
                        "TOP_N_PER_GROUP",
                        ("refunds.order_id", "refunds.id", "refunds.amount", "refunds.reason"),
                        (
                            {
                                "pattern": "TOP_N_PER_GROUP",
                                "partition_by": ("refunds.order_id",),
                                "order_by": (
                                    _order("refunds.amount", "DESC"),
                                    _order("refunds.id", "ASC"),
                                ),
                                "n": 1,
                                "tie_policy": "EXACT_N",
                            },
                        ),
                    ),
                ),
            ],
        )
        + _family_cases(
            "m212-holdout-laglead",
            "lag_lead",
            [
                (
                    "Show the next order date for each customer ordered by time.",
                    _ir(
                        "orders",
                        "LEAD",
                        ("orders.customer_id", "orders.id", "orders.ordered_at"),
                        (
                            {
                                "pattern": "LEAD",
                                "target": "orders.ordered_at",
                                "partition_by": ("orders.customer_id",),
                                "order_by": (
                                    _order("orders.ordered_at", "ASC"),
                                    _order("orders.id", "ASC"),
                                ),
                                "offset": 1,
                                "alias": "next_order_at",
                            },
                        ),
                    ),
                ),
                (
                    "Show the amount from two payments earlier within each order.",
                    _ir(
                        "payments",
                        "LAG",
                        ("payments.order_id", "payments.id", "payments.amount"),
                        (
                            {
                                "pattern": "LAG",
                                "target": "payments.amount",
                                "partition_by": ("payments.order_id",),
                                "order_by": (
                                    _order("payments.paid_at", "ASC"),
                                    _order("payments.id", "ASC"),
                                ),
                                "offset": 2,
                                "alias": "amount_two_prior",
                            },
                        ),
                    ),
                ),
                (
                    "For each product category, show the following unit price in ascending order.",
                    _ir(
                        "products",
                        "LEAD",
                        ("products.category", "products.id", "products.unit_price"),
                        (
                            {
                                "pattern": "LEAD",
                                "target": "products.unit_price",
                                "partition_by": ("products.category",),
                                "order_by": (
                                    _order("products.unit_price", "ASC"),
                                    _order("products.id", "ASC"),
                                ),
                                "offset": 1,
                                "alias": "next_price",
                            },
                        ),
                    ),
                ),
                (
                    "For every order, show the prior line quantity by line id.",
                    _ir(
                        "order_items",
                        "LAG",
                        ("order_items.order_id", "order_items.id", "order_items.quantity"),
                        (
                            {
                                "pattern": "LAG",
                                "target": "order_items.quantity",
                                "partition_by": ("order_items.order_id",),
                                "order_by": (_order("order_items.id", "ASC"),),
                                "offset": 1,
                                "alias": "prior_quantity",
                            },
                        ),
                    ),
                ),
            ],
        )
        + _family_cases(
            "m212-holdout-running",
            "running_aggregate",
            [
                (
                    "Calculate cumulative refund amount within each order.",
                    _ir(
                        "refunds",
                        "RUNNING_AGGREGATE",
                        ("refunds.order_id", "refunds.id", "refunds.amount"),
                        (
                            {
                                "pattern": "RUNNING_AGGREGATE",
                                "aggregate": "SUM",
                                "target": "refunds.amount",
                                "partition_by": ("refunds.order_id",),
                                "order_by": (
                                    _order("refunds.refunded_at", "ASC"),
                                    _order("refunds.id", "ASC"),
                                ),
                                "alias": "refund_total_to_date",
                            },
                        ),
                    ),
                ),
                (
                    "Show cumulative product price by category from high to low.",
                    _ir(
                        "products",
                        "RUNNING_AGGREGATE",
                        ("products.category", "products.id", "products.unit_price"),
                        (
                            {
                                "pattern": "RUNNING_AGGREGATE",
                                "aggregate": "SUM",
                                "target": "products.unit_price",
                                "partition_by": ("products.category",),
                                "order_by": (
                                    _order("products.unit_price", "DESC"),
                                    _order("products.id", "DESC"),
                                ),
                                "alias": "running_category_price",
                            },
                        ),
                    ),
                ),
                (
                    "For each representative, accumulate customer ids by creation time.",
                    _ir(
                        "customers",
                        "RUNNING_AGGREGATE",
                        ("customers.sales_rep_id", "customers.id", "customers.created_at"),
                        (
                            {
                                "pattern": "RUNNING_AGGREGATE",
                                "aggregate": "SUM",
                                "target": "customers.id",
                                "partition_by": ("customers.sales_rep_id",),
                                "order_by": (
                                    _order("customers.created_at", "ASC"),
                                    _order("customers.id", "ASC"),
                                ),
                                "alias": "running_customer_id",
                            },
                        ),
                    ),
                ),
                (
                    "Within each customer, accumulate order totals in reverse order time.",
                    _ir(
                        "orders",
                        "RUNNING_AGGREGATE",
                        ("orders.customer_id", "orders.id", "orders.total_amount"),
                        (
                            {
                                "pattern": "RUNNING_AGGREGATE",
                                "aggregate": "SUM",
                                "target": "orders.total_amount",
                                "partition_by": ("orders.customer_id",),
                                "order_by": (
                                    _order("orders.ordered_at", "DESC"),
                                    _order("orders.id", "DESC"),
                                ),
                                "alias": "reverse_running_total",
                            },
                        ),
                    ),
                ),
            ],
        )
        + _family_cases(
            "m212-holdout-moving",
            "moving_window",
            [
                (
                    "Find a four-row moving average of order totals per representative.",
                    _ir(
                        "orders",
                        "MOVING_AGGREGATE",
                        ("orders.sales_rep_id", "orders.id", "orders.total_amount"),
                        (
                            {
                                "pattern": "MOVING_AGGREGATE",
                                "aggregate": "AVG",
                                "target": "orders.total_amount",
                                "partition_by": ("orders.sales_rep_id",),
                                "order_by": (
                                    _order("orders.ordered_at", "ASC"),
                                    _order("orders.id", "ASC"),
                                ),
                                "frame": {
                                    "mode": "ROWS",
                                    "start": _bound("N_PRECEDING", 3),
                                    "end": _bound("CURRENT_ROW"),
                                },
                                "alias": "four_order_average",
                            },
                        ),
                    ),
                ),
                (
                    "For each category, compute the average price over the previous three products, excluding this product.",
                    _ir(
                        "products",
                        "MOVING_AGGREGATE",
                        ("products.category", "products.id", "products.unit_price"),
                        (
                            {
                                "pattern": "MOVING_AGGREGATE",
                                "aggregate": "AVG",
                                "target": "products.unit_price",
                                "partition_by": ("products.category",),
                                "order_by": (_order("products.id", "ASC"),),
                                "frame": {
                                    "mode": "ROWS",
                                    "start": _bound("N_PRECEDING", 3),
                                    "end": _bound("N_PRECEDING", 1),
                                },
                                "alias": "previous_price_average",
                            },
                        ),
                    ),
                ),
                (
                    "For each order, show a two-row moving sum of line quantities.",
                    _ir(
                        "order_items",
                        "MOVING_AGGREGATE",
                        ("order_items.order_id", "order_items.id", "order_items.quantity"),
                        (
                            {
                                "pattern": "MOVING_AGGREGATE",
                                "aggregate": "SUM",
                                "target": "order_items.quantity",
                                "partition_by": ("order_items.order_id",),
                                "order_by": (_order("order_items.id", "ASC"),),
                                "frame": {
                                    "mode": "ROWS",
                                    "start": _bound("N_PRECEDING", 1),
                                    "end": _bound("CURRENT_ROW"),
                                },
                                "alias": "two_row_quantity",
                            },
                        ),
                    ),
                ),
                (
                    "Within each order, show a moving maximum refund amount over the current and prior refund.",
                    _ir(
                        "refunds",
                        "MOVING_AGGREGATE",
                        ("refunds.order_id", "refunds.id", "refunds.amount"),
                        (
                            {
                                "pattern": "MOVING_AGGREGATE",
                                "aggregate": "MAX",
                                "target": "refunds.amount",
                                "partition_by": ("refunds.order_id",),
                                "order_by": (
                                    _order("refunds.refunded_at", "ASC"),
                                    _order("refunds.id", "ASC"),
                                ),
                                "frame": {
                                    "mode": "ROWS",
                                    "start": _bound("N_PRECEDING", 1),
                                    "end": _bound("CURRENT_ROW"),
                                },
                                "alias": "recent_refund_max",
                            },
                        ),
                    ),
                ),
            ],
        )
        + _family_cases(
            "m212-holdout-ranking",
            "ranking",
            [
                (
                    "Give each order a dense rank by total inside its sales representative.",
                    _ir(
                        "orders",
                        "RANKING",
                        ("orders.sales_rep_id", "orders.id", "orders.total_amount"),
                        (
                            {
                                "pattern": "RANKING",
                                "function": "DENSE_RANK",
                                "partition_by": ("orders.sales_rep_id",),
                                "order_by": (_order("orders.total_amount", "DESC"),),
                                "alias": "rep_amount_rank",
                            },
                        ),
                    ),
                ),
                (
                    "Rank products from cheapest to most expensive within their category.",
                    _ir(
                        "products",
                        "RANKING",
                        ("products.category", "products.id", "products.unit_price"),
                        (
                            {
                                "pattern": "RANKING",
                                "function": "RANK",
                                "partition_by": ("products.category",),
                                "order_by": (_order("products.unit_price", "ASC"),),
                                "alias": "cheapness_rank",
                            },
                        ),
                    ),
                ),
                (
                    "Number each customer chronologically within its representative.",
                    _ir(
                        "customers",
                        "RANKING",
                        ("customers.sales_rep_id", "customers.id", "customers.created_at"),
                        (
                            {
                                "pattern": "RANKING",
                                "function": "ROW_NUMBER",
                                "partition_by": ("customers.sales_rep_id",),
                                "order_by": (
                                    _order("customers.created_at", "ASC"),
                                    _order("customers.id", "ASC"),
                                ),
                                "alias": "customer_number",
                            },
                        ),
                    ),
                ),
                (
                    "Assign a refund rank by newest refund timestamp within each order.",
                    _ir(
                        "refunds",
                        "RANKING",
                        ("refunds.order_id", "refunds.id", "refunds.refunded_at"),
                        (
                            {
                                "pattern": "RANKING",
                                "function": "RANK",
                                "partition_by": ("refunds.order_id",),
                                "order_by": (_order("refunds.refunded_at", "DESC"),),
                                "alias": "refund_recency_rank",
                            },
                        ),
                    ),
                ),
            ],
        )
        + _family_cases(
            "m212-holdout-share",
            "share_of_total",
            [
                (
                    "Express each order total as its percentage of the representative's order total.",
                    _ir(
                        "orders",
                        "SHARE_OF_TOTAL",
                        ("orders.sales_rep_id", "orders.id", "orders.total_amount"),
                        (
                            {
                                "pattern": "SHARE_OF_TOTAL",
                                "target": "orders.total_amount",
                                "partition_by": ("orders.sales_rep_id",),
                                "scale": 100,
                                "alias": "rep_order_share",
                            },
                        ),
                    ),
                ),
                (
                    "For each order, show a line quantity's share of all quantities in that order.",
                    _ir(
                        "order_items",
                        "SHARE_OF_TOTAL",
                        ("order_items.order_id", "order_items.id", "order_items.quantity"),
                        (
                            {
                                "pattern": "SHARE_OF_TOTAL",
                                "target": "order_items.quantity",
                                "partition_by": ("order_items.order_id",),
                                "scale": 100,
                                "alias": "line_quantity_share",
                            },
                        ),
                    ),
                ),
                (
                    "Calculate each customer's id percentage within the representative's customers.",
                    _ir(
                        "customers",
                        "SHARE_OF_TOTAL",
                        ("customers.sales_rep_id", "customers.id"),
                        (
                            {
                                "pattern": "SHARE_OF_TOTAL",
                                "target": "customers.id",
                                "partition_by": ("customers.sales_rep_id",),
                                "scale": 100,
                                "alias": "rep_customer_share",
                            },
                        ),
                    ),
                ),
                (
                    "Show each product price as a fraction of its category's total price, scaled to percent.",
                    _ir(
                        "products",
                        "SHARE_OF_TOTAL",
                        ("products.category", "products.id", "products.unit_price"),
                        (
                            {
                                "pattern": "SHARE_OF_TOTAL",
                                "target": "products.unit_price",
                                "partition_by": ("products.category",),
                                "scale": 100,
                                "alias": "category_share_percent",
                            },
                        ),
                    ),
                ),
            ],
        )
        + _family_cases(
            "m212-holdout-mixed",
            "mixed_multi_window",
            [
                (
                    "For orders by representative, show both the prior total and a running total.",
                    _ir(
                        "orders",
                        "MIXED_MULTI_WINDOW",
                        ("orders.sales_rep_id", "orders.id", "orders.total_amount"),
                        (
                            {
                                "pattern": "LAG",
                                "target": "orders.total_amount",
                                "partition_by": ("orders.sales_rep_id",),
                                "order_by": (
                                    _order("orders.ordered_at", "ASC"),
                                    _order("orders.id", "ASC"),
                                ),
                                "offset": 1,
                                "alias": "prior_rep_order",
                            },
                            {
                                "pattern": "RUNNING_AGGREGATE",
                                "aggregate": "SUM",
                                "target": "orders.total_amount",
                                "partition_by": ("orders.sales_rep_id",),
                                "order_by": (
                                    _order("orders.ordered_at", "ASC"),
                                    _order("orders.id", "ASC"),
                                ),
                                "alias": "rep_running_total",
                            },
                        ),
                    ),
                ),
                (
                    "For each product, include its category rank and a one-row moving average of price.",
                    _ir(
                        "products",
                        "MIXED_MULTI_WINDOW",
                        ("products.category", "products.id", "products.unit_price"),
                        (
                            {
                                "pattern": "RANKING",
                                "function": "DENSE_RANK",
                                "partition_by": ("products.category",),
                                "order_by": (_order("products.unit_price", "DESC"),),
                                "alias": "category_rank",
                            },
                            {
                                "pattern": "MOVING_AGGREGATE",
                                "aggregate": "AVG",
                                "target": "products.unit_price",
                                "partition_by": ("products.category",),
                                "order_by": (_order("products.id", "ASC"),),
                                "frame": {
                                    "mode": "ROWS",
                                    "start": _bound("N_PRECEDING", 1),
                                    "end": _bound("CURRENT_ROW"),
                                },
                                "alias": "price_average",
                            },
                        ),
                    ),
                ),
                (
                    "For each customer, show the next creation time and its position within the region.",
                    _ir(
                        "customers",
                        "MIXED_MULTI_WINDOW",
                        ("customers.region_id", "customers.id", "customers.created_at"),
                        (
                            {
                                "pattern": "LEAD",
                                "target": "customers.created_at",
                                "partition_by": ("customers.region_id",),
                                "order_by": (
                                    _order("customers.created_at", "ASC"),
                                    _order("customers.id", "ASC"),
                                ),
                                "offset": 1,
                                "alias": "next_customer_at",
                            },
                            {
                                "pattern": "RANKING",
                                "function": "ROW_NUMBER",
                                "partition_by": ("customers.region_id",),
                                "order_by": (
                                    _order("customers.created_at", "ASC"),
                                    _order("customers.id", "ASC"),
                                ),
                                "alias": "region_position",
                            },
                        ),
                    ),
                ),
                (
                    "For each refund, calculate its amount share and the next refund amount in the order.",
                    _ir(
                        "refunds",
                        "MIXED_MULTI_WINDOW",
                        ("refunds.order_id", "refunds.id", "refunds.amount"),
                        (
                            {
                                "pattern": "SHARE_OF_TOTAL",
                                "target": "refunds.amount",
                                "partition_by": ("refunds.order_id",),
                                "scale": 100,
                                "alias": "refund_percent",
                            },
                            {
                                "pattern": "LEAD",
                                "target": "refunds.amount",
                                "partition_by": ("refunds.order_id",),
                                "order_by": (
                                    _order("refunds.refunded_at", "ASC"),
                                    _order("refunds.id", "ASC"),
                                ),
                                "offset": 1,
                                "alias": "next_amount",
                            },
                        ),
                    ),
                ),
            ],
        )
    )


def build_datasets() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dev = _dev_cases()
    holdout = _holdout_cases()
    assert len(dev) == 48 and len(holdout) == 32
    assert {case["pattern"] for case in dev} >= {"MIXED_MULTI_WINDOW"}
    return dev, holdout


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_datasets() -> None:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    dev, holdout = build_datasets()
    for name, payload in (("m212_window_dev", dev), ("m212_window_holdout", holdout)):
        path = DATASET_DIR / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n")
        manifest = {
            "milestone": "M2.12",
            "dataset": name,
            "size": len(payload),
            "sha256": _sha(path),
            "patterns": {
                pattern: sum(item["pattern"] == pattern for item in payload)
                for pattern in sorted({item["pattern"] for item in payload})
            },
            "gold_ir_compiler": "pending-validation",
            "frozen_before_provider_evaluation": True,
        }
        (DATASET_DIR / f"{name}_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    write_datasets()
