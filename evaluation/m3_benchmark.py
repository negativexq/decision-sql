"""Frozen, small internal benchmark for governed metric grounding.

The reference SQL in this module is evaluation-only.  Production semantics are
typed and live under ``app.semantics``; these queries are an independent,
human-auditable oracle for the compiler ceiling.
"""

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.semantics.catalog import build_m3_catalog
from app.semantics.models import MetricCatalog


class M3Target(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    metric_name: str
    dimensions: tuple[str, ...] = ()
    reference_sql: str
    metric_kind: str
    relationship_distance: int
    slices: tuple[str, ...] = ()


class M3BenchmarkCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    split: Literal["dev", "holdout"]
    target_id: str
    question: str
    metric_name: str
    dimensions: tuple[str, ...] = ()


class BenchmarkConstructionError(ValueError):
    """Raised when the frozen M3 benchmark constraints cannot be constructed."""


@dataclass(frozen=True)
class _MeasureShape:
    from_sql: str
    joins: tuple[str, ...]
    value_sql: str
    filters: tuple[str, ...]


def build_targets(catalog: MetricCatalog | None = None) -> tuple[M3Target, ...]:
    catalog = catalog or build_m3_catalog()
    requested: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("completed_revenue", ()),
        ("completed_revenue", ("customer",)),
        ("completed_revenue", ("sales_representative",)),
        ("completed_revenue", ("order_currency",)),
        ("average_completed_order_value", ()),
        ("average_completed_order_value", ("customer",)),
        ("average_completed_order_value", ("sales_representative",)),
        ("average_completed_order_value", ("order_currency",)),
        ("average_payment_amount", ()),
        ("average_payment_amount", ("customer",)),
        ("average_payment_amount", ("sales_representative",)),
        ("average_payment_amount", ("order_currency",)),
        ("completed_item_quantity", ()),
        ("completed_item_quantity", ("product_category",)),
        ("completed_item_quantity", ("customer",)),
        ("completed_item_quantity", ("order_currency",)),
        ("payment_success_rate", ()),
        ("payment_success_rate", ("customer",)),
        ("payment_success_rate", ("sales_representative",)),
        ("payment_success_rate", ("order_currency",)),
        ("refunded_order_rate", ()),
        ("refunded_order_rate", ("customer",)),
        ("refunded_order_rate", ("sales_representative",)),
        ("refunded_order_rate", ("order_currency",)),
        ("refund_to_revenue_rate", ()),
        ("refund_to_revenue_rate", ("customer",)),
        ("refund_to_revenue_rate", ("sales_representative",)),
        ("refund_to_revenue_rate", ("order_currency",)),
        ("average_items_per_completed_order", ()),
        ("average_items_per_completed_order", ("product_category",)),
        ("average_items_per_completed_order", ("customer",)),
        ("average_items_per_completed_order", ("order_currency",)),
    )
    result = []
    for index, (metric_name, dimensions) in enumerate(requested):
        metric = catalog.metric(metric_name)
        reference_sql = _reference_sql(metric_name, dimensions)
        result.append(
            M3Target(
                target_id=f"m3-target-{index:02d}",
                metric_name=metric_name,
                dimensions=dimensions,
                reference_sql=reference_sql,
                metric_kind=metric.kind.value,
                relationship_distance=_relationship_distance(metric_name, dimensions),
                slices=_slices(metric_name),
            )
        )
    return tuple(result)


def build_benchmark_cases(
    targets: tuple[M3Target, ...] | None = None,
) -> tuple[M3BenchmarkCase, ...]:
    targets = targets or build_targets()
    if not targets:
        raise BenchmarkConstructionError(
            "M3 benchmark construction requires at least one semantic target"
        )
    # Four target compositions are held out from development.  All remaining
    # targets receive deterministic paraphrase coverage in development.
    holdout_only = {"m3-target-27", "m3-target-28", "m3-target-29", "m3-target-30"}
    cases: list[M3BenchmarkCase] = []
    dev_index = 0
    for target in targets:
        if target.target_id in holdout_only:
            continue
        for variant in range(2 if dev_index < 16 else 1):
            cases.append(_case(target, "dev", dev_index, variant))
            dev_index += 1
    # Add a second wording to selected development targets, giving 48 cases
    # without changing the semantic target distribution.
    extra_count = 48 - len(cases)
    if extra_count > 0:
        # The old rejection loop could make no progress forever when every
        # target in its cycle was holdout-only.  Preserve its default target
        # order, but materialize one finite cycle before selecting extras.
        candidate_cycle = tuple(
            targets[(index * 3) % len(targets)]
            for index in range(len(targets))
            if targets[(index * 3) % len(targets)].target_id not in holdout_only
        )
        if not candidate_cycle:
            raise BenchmarkConstructionError(
                "M3 development benchmark constraints are impossible: "
                f"requested=48 constructed={len(cases)} "
                f"candidate_cycle_size={len(candidate_cycle)}"
            )
        for extra_index in range(extra_count):
            target = candidate_cycle[extra_index % len(candidate_cycle)]
            cases.append(_case(target, "dev", len(cases), 1))
    for index in range(32):
        target = targets[(index * 5 + 3) % len(targets)]
        cases.append(_case(target, "holdout", index, index % 3))
    return tuple(cases)


def benchmark_hash(targets: tuple[M3Target, ...], cases: tuple[M3BenchmarkCase, ...]) -> str:
    payload = "\n".join(item.model_dump_json() for item in (*targets, *cases))
    return sha256(payload.encode()).hexdigest()


def _case(
    target: M3Target, split: Literal["dev", "holdout"], index: int, variant: int
) -> M3BenchmarkCase:
    dimension = target.dimensions[0] if target.dimensions else None
    noun = _dimension_label(dimension)
    metric = _metric_phrase(target.metric_name)
    if dimension is None:
        questions = (
            f"What is the {metric}?",
            f"Give me the governed {metric}.",
            f"Calculate {metric} for the whole business.",
        )
    else:
        questions = (
            f"What is the {metric} for each {noun}?",
            f"Show how the {metric} varies by {noun}.",
            f"Break down the {metric} across {noun}. ",
        )
    return M3BenchmarkCase(
        case_id=f"m3-{split}-{index:03d}",
        split=split,
        target_id=target.target_id,
        question=questions[variant % len(questions)].strip(),
        metric_name=target.metric_name,
        dimensions=target.dimensions,
    )


def _metric_phrase(name: str) -> str:
    return {
        "completed_revenue": "revenue from completed orders",
        "average_completed_order_value": "average value of completed orders",
        "average_payment_amount": "average payment amount",
        "completed_item_quantity": "item quantity on completed orders",
        "payment_success_rate": "payment success rate",
        "refunded_order_rate": "refunded order rate",
        "refund_to_revenue_rate": "refund amount as a percentage of completed-order revenue",
        "average_items_per_completed_order": "average items per completed order",
    }[name]


def _dimension_label(name: str | None) -> str:
    return {
        "customer": "customer",
        "sales_representative": "sales representative",
        "order_currency": "order currency",
        "product_category": "product category",
    }.get(name or "", "business")


def _relationship_distance(metric_name: str, dimensions: tuple[str, ...]) -> int:
    if not dimensions:
        return 0
    if dimensions == ("order_currency",):
        return 0 if metric_name in {"completed_revenue", "average_completed_order_value"} else 1
    if dimensions == ("product_category",):
        return 1
    return 1 if metric_name in {"completed_revenue", "average_completed_order_value"} else 2


def _slices(metric_name: str) -> tuple[str, ...]:
    if metric_name in {
        "completed_revenue",
        "average_completed_order_value",
        "average_payment_amount",
        "completed_item_quantity",
    }:
        return ("DIRECT_MEASURE", "AGGREGATION_SENSITIVE")
    slices = ["RATIO", "ARITHMETIC_RATIO"]
    if "payment" in metric_name or "refund" in metric_name:
        slices.append("FILTER_SENSITIVE")
    if metric_name in {"payment_success_rate", "refund_to_revenue_rate", "refunded_order_rate"}:
        slices.append("JOIN_PATH_SENSITIVE")
    return tuple(slices)


def _reference_sql(metric_name: str, dimensions: tuple[str, ...]) -> str:
    if metric_name == "completed_revenue":
        measure = _MeasureShape(
            "orders", (), "SUM(orders.total_amount)", ("orders.status = 'completed'",)
        )
    elif metric_name == "average_completed_order_value":
        measure = _MeasureShape(
            "orders", (), "AVG(orders.total_amount)", ("orders.status = 'completed'",)
        )
    elif metric_name == "average_payment_amount":
        measure = _MeasureShape(
            "payments",
            ("JOIN orders ON payments.order_id = orders.id",),
            "AVG(payments.amount)",
            (),
        )
    elif metric_name == "completed_item_quantity":
        measure = _MeasureShape(
            "order_items",
            ("JOIN orders ON order_items.order_id = orders.id",),
            "SUM(order_items.quantity)",
            ("orders.status = 'completed'",),
        )
    elif metric_name == "payment_success_rate":
        return _reference_ratio_sql(
            dimensions,
            _MeasureShape(
                "payments",
                ("JOIN orders ON payments.order_id = orders.id",),
                "COUNT(DISTINCT payments.id)",
                ("payments.status = 'settled'",),
            ),
            _MeasureShape(
                "payments",
                ("JOIN orders ON payments.order_id = orders.id",),
                "COUNT(DISTINCT payments.id)",
                (),
            ),
            DecimalLiteral("100"),
            "NULL",
        )
    elif metric_name == "refunded_order_rate":
        return _reference_ratio_sql(
            dimensions,
            _MeasureShape(
                "refunds",
                ("JOIN orders ON refunds.order_id = orders.id",),
                "COUNT(DISTINCT refunds.order_id)",
                ("orders.status = 'completed'",),
            ),
            _MeasureShape(
                "orders", (), "COUNT(DISTINCT orders.id)", ("orders.status = 'completed'",)
            ),
            DecimalLiteral("100"),
            "NULL",
        )
    elif metric_name == "refund_to_revenue_rate":
        return _reference_ratio_sql(
            dimensions,
            _MeasureShape(
                "refunds",
                ("JOIN orders ON refunds.order_id = orders.id",),
                "SUM(refunds.amount)",
                ("orders.status = 'completed'",),
            ),
            _MeasureShape(
                "orders", (), "SUM(orders.total_amount)", ("orders.status = 'completed'",)
            ),
            DecimalLiteral("100"),
            "0",
        )
    elif metric_name == "average_items_per_completed_order":
        return _reference_ratio_sql(
            dimensions,
            _MeasureShape(
                "order_items",
                ("JOIN orders ON order_items.order_id = orders.id",),
                "SUM(order_items.quantity)",
                ("orders.status = 'completed'",),
            ),
            _MeasureShape(
                "order_items",
                ("JOIN orders ON order_items.order_id = orders.id",),
                "COUNT(DISTINCT order_items.order_id)",
                ("orders.status = 'completed'",),
            ),
            DecimalLiteral("1"),
            "0",
        )
    else:
        raise ValueError(f"no M3 reference for {metric_name}")
    return _reference_measure_sql(measure, dimensions)


class DecimalLiteral(str):
    pass


def _reference_measure_sql(measure: _MeasureShape, dimensions: tuple[str, ...]) -> str:
    selects = []
    group_by = []
    joins = list(measure.joins)
    if dimensions:
        dimension_sql, dimension_join = _dimension_sql(measure.from_sql, dimensions[0])
        selects.append(f"{dimension_sql} AS dimension_0")
        group_by.append(dimension_sql)
        if dimension_join and dimension_join not in joins:
            joins.append(dimension_join)
    selects.append(f"{measure.value_sql} AS measure_value")
    where = f" WHERE {' AND '.join(measure.filters)}" if measure.filters else ""
    group = f" GROUP BY {', '.join(group_by)}" if group_by else ""
    order = " ORDER BY dimension_0" if dimensions else ""
    return (
        f"SELECT {', '.join(selects)} FROM {measure.from_sql} {' '.join(joins)}"
        f"{where}{group}{order}"
    )


def _reference_ratio_sql(
    dimensions: tuple[str, ...],
    numerator: _MeasureShape,
    denominator: _MeasureShape,
    scale: DecimalLiteral,
    zero_value: str,
) -> str:
    n = _reference_measure_sql(numerator, dimensions)
    d = _reference_measure_sql(denominator, dimensions)
    numerator_value = "COALESCE(n.measure_value, 0)"
    denominator_value = "COALESCE(d.measure_value, 0)"
    result = (
        f"CASE WHEN {denominator_value} = 0 THEN {zero_value} ELSE "
        f"CAST({numerator_value} AS DECIMAL) / CAST({denominator_value} AS DECIMAL) * "
        f"{scale} END AS metric_value"
    )
    if dimensions:
        return (
            "WITH n AS ("
            f"{n}), d AS ({d}) SELECT COALESCE(n.dimension_0, d.dimension_0) AS "
            f"dimension_0, {result} "
            "FROM n FULL OUTER JOIN d ON n.dimension_0 = d.dimension_0 ORDER BY dimension_0"
        )
    return f"WITH n AS ({n}), d AS ({d}) SELECT {result} FROM n CROSS JOIN d"


def _dimension_sql(source: str, dimension: str) -> tuple[str, str | None]:
    if dimension == "customer":
        return "customers.name", {
            "orders": "JOIN customers ON orders.customer_id = customers.id",
            "payments": "JOIN customers ON orders.customer_id = customers.id",
            "refunds": "JOIN customers ON orders.customer_id = customers.id",
            "order_items": "JOIN customers ON orders.customer_id = customers.id",
        }[source]
    if dimension == "sales_representative":
        return "sales_representatives.name", {
            "orders": (
                "JOIN sales_representatives ON orders.sales_rep_id = sales_representatives.id"
            ),
            "payments": (
                "JOIN sales_representatives ON orders.sales_rep_id = sales_representatives.id"
            ),
            "refunds": (
                "JOIN sales_representatives ON orders.sales_rep_id = sales_representatives.id"
            ),
            "order_items": (
                "JOIN sales_representatives ON orders.sales_rep_id = sales_representatives.id"
            ),
        }[source]
    if dimension == "order_currency":
        return "orders.currency", None
    if dimension == "product_category":
        return "products.category", "JOIN products ON order_items.product_id = products.id"
    raise ValueError(f"unsupported reference dimension: {dimension}")
