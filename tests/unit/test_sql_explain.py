from __future__ import annotations

import pytest

from app.sql.explain import ConditionEdit, ConditionEditError, edit_conditions, explain

SQL = (
    "WITH t AS (SELECT o.region, SUM(o.amount) AS revenue FROM orders o "
    "JOIN customers c ON c.id = o.customer_id AND c.tier = 'gold' "
    "WHERE o.status IN ('shipped', 'paid') AND o.created_at >= DATE '2024-01-01' "
    "AND (o.amount > 5 OR o.flag = 'x') GROUP BY o.region) "
    "SELECT region, revenue FROM t ORDER BY revenue DESC LIMIT 10"
)


def test_the_explanation_reads_tables_measures_conditions_and_shape() -> None:
    explanation = explain(SQL)

    assert explanation is not None
    assert explanation.tables == ["customers", "orders"]
    assert [(m.function, m.column) for m in explanation.measures] == [("sum", "amount")]
    assert [(c.column, c.operator, c.values, c.kind) for c in explanation.conditions] == [
        ("orders.status", "in", ["shipped", "paid"], "text"),
        ("orders.created_at", "≥", ["2024-01-01"], "date"),
        ("orders.amount", ">", ["5"], "number"),
        ("orders.flag", "=", ["x"], "text"),
        ("customers.tier", "=", ["gold"], "text"),
    ]
    # A condition under OR cannot be dropped alone; ANDed ones can, in WHERE or ON.
    assert [c.removable for c in explanation.conditions] == [True, True, False, False, True]
    assert explanation.groups == ["region"]
    assert [(o.column, o.descending) for o in explanation.order] == [("revenue", True)]
    assert explanation.limit == 10


def test_counts_distinct_and_star_are_told_apart() -> None:
    explanation = explain(
        "SELECT COUNT(*), COUNT(DISTINCT customer_id) FROM orders WHERE 2024 <= year"
    )

    assert explanation is not None
    assert [(m.function, m.column, m.distinct) for m in explanation.measures] == [
        ("count", None, False),
        ("count", "customer_id", True),
    ]
    # A literal on the left reads the other way round.
    condition = explanation.conditions[0]
    assert (condition.column, condition.operator, condition.values) == (
        "orders.year",
        "≥",
        ["2024"],
    )


def test_an_edit_replaces_literals_in_the_form_they_had() -> None:
    edited = edit_conditions(
        SQL,
        [
            ConditionEdit(index=0, values=["paid"]),
            ConditionEdit(index=1, values=["2023-06-01"]),
            ConditionEdit(index=4, remove=True),
        ],
    )

    assert "o.status IN ('paid')" in edited
    assert "o.created_at >= CAST('2023-06-01' AS DATE)" in edited
    assert "c.tier" not in edited
    # A date written as a quoted string stays one, so a text column still compares as text.
    quoted = edit_conditions(
        "SELECT * FROM event WHERE event_date >= '2019-09-01T00:00:00'",
        [ConditionEdit(index=0, values=["2019-10-01"])],
    )
    assert "event_date >= '2019-10-01'" in quoted


def test_a_value_only_ever_becomes_a_literal() -> None:
    edited = edit_conditions(
        "SELECT * FROM orders WHERE status = 'paid'",
        [ConditionEdit(index=0, values=["x' OR '1'='1"])],
    )
    assert edited.endswith("WHERE status = 'x'' OR ''1''=''1'")

    for bad in ("1; DROP TABLE orders", "nan", "1e9"):
        with pytest.raises(ConditionEditError, match="not a number"):
            edit_conditions(
                "SELECT * FROM orders WHERE amount > 5", [ConditionEdit(index=0, values=[bad])]
            )
    with pytest.raises(ConditionEditError, match="not a date"):
        edit_conditions(
            "SELECT * FROM orders WHERE day >= DATE '2024-01-01'",
            [ConditionEdit(index=0, values=["yesterday"])],
        )


def test_an_edit_that_does_not_fit_is_refused() -> None:
    with pytest.raises(ConditionEditError, match="cannot be removed alone"):
        edit_conditions(SQL, [ConditionEdit(index=2, remove=True)])
    with pytest.raises(ConditionEditError, match="no condition 9"):
        edit_conditions(SQL, [ConditionEdit(index=9, values=["a"])])
    with pytest.raises(ConditionEditError, match="takes one value"):
        edit_conditions(SQL, [ConditionEdit(index=3, values=["a", "b"])])
    with pytest.raises(ValueError):
        ConditionEdit(index=0, values=["a"], remove=True)
    with pytest.raises(ValueError):
        ConditionEdit(index=0)
