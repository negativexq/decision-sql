from app.generation.hard_query_plans import (
    RatioPlan,
    TopKPlan,
    WindowPlan,
    operation_plan_references,
    validate_operation_plan_visibility,
)


def test_operation_plans_are_narrow_and_do_not_contain_sql_or_join_graph() -> None:
    top_k = TopKPlan(
        entity_outputs=("products.name",),
        measure={
            "semantic_label": "sales",
            "aggregation": "SUM",
            "components": ("order_items.quantity", "order_items.unit_price"),
        },
        group_by=("products.name",),
        order_direction="DESC",
        limit=5,
    )
    RatioPlan(
        numerator={
            "semantic_label": "refunds",
            "aggregation": "SUM",
            "source_columns": ("refunds.amount",),
        },
        denominator={
            "semantic_label": "orders",
            "aggregation": "SUM",
            "source_columns": ("orders.total_amount",),
        },
        grain="order",
        scale=100,
    )
    WindowPlan(
        requested_outputs=("orders.customer_id", "orders.ordered_at"),
        window_function="ROW_NUMBER",
        partition_by=("orders.customer_id",),
        order_by=("orders.ordered_at",),
        order_direction="DESC",
    )

    assert not any(name in TopKPlan.model_fields for name in ("sql", "joins", "from_clause"))
    assert not any(name in RatioPlan.model_fields for name in ("sql", "joins", "where"))
    assert not any(name in WindowPlan.model_fields for name in ("sql", "joins", "where"))
    assert operation_plan_references(top_k) == (
        "products.name",
        "order_items.quantity",
        "order_items.unit_price",
    )


def test_only_impossible_physical_references_fail_visibility_validation() -> None:
    plan = RatioPlan(
        numerator={
            "semantic_label": "refunds",
            "aggregation": "SUM",
            "source_columns": ("refunds.amount",),
        },
        denominator={
            "semantic_label": "orders",
            "aggregation": "SUM",
            "source_columns": ("orders.total_amount",),
        },
        grain="order",
    )
    validate_operation_plan_visibility(plan, {"refunds.amount", "orders.total_amount"})

    invalid = plan.model_copy(
        update={
            "numerator": plan.numerator.model_copy(
                update={"source_columns": ("payments.secret_amount",)}
            )
        }
    )
    try:
        validate_operation_plan_visibility(invalid, {"refunds.amount", "orders.total_amount"})
    except ValueError as error:
        assert "payments.secret_amount" in str(error)
    else:
        raise AssertionError("invisible plan reference was not rejected")
