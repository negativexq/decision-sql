"""Small structural failure labels for the M2.5 holdout report."""

from sqlglot import exp, parse_one

from evaluation.models import BaselineCase


def classify_sql_failure(case: BaselineCase, generated_sql: str | None) -> str | None:
    if generated_sql is None:
        return "SQL_GENERATION_ERROR"
    try:
        generated = parse_one(generated_sql, dialect="postgres")
        gold = parse_one(case.gold_sql, dialect="postgres")
    except Exception:
        return "SQL_PARSE_ERROR"

    generated_tables = _tables(generated)
    gold_tables = _tables(gold)
    if generated_tables != gold_tables:
        return "WRONG_TABLE"
    generated_select = _select_expressions(generated)
    gold_select = _select_expressions(gold)
    if len(generated_select) != len(gold_select) or generated_select != gold_select:
        return "WRONG_COLUMN"
    if _limit(generated) != _limit(gold):
        return "WRONG_LIMIT"
    if case.category == "window_functions" and _windows(generated) != _windows(gold):
        return "WRONG_WINDOW_LOGIC"
    if _joins(generated) != _joins(gold):
        return "WRONG_JOIN"
    if _group_by(generated) != _group_by(gold):
        return "WRONG_GROUPING"
    if case.category == "date_filtering" and _where(generated) != _where(gold):
        return "WRONG_DATE_BOUNDARY"
    if case.category == "ratios" and _contains(generated, exp.Div):
        if _has_percentage_scaling(generated) != _has_percentage_scaling(gold):
            return "WRONG_RATIO_SCALING"
        if _aggregates(generated) != _aggregates(gold):
            return "WRONG_RATIO_DENOMINATOR"
    if _aggregates(generated) != _aggregates(gold):
        return "WRONG_AGGREGATION"
    if case.order_sensitive and _order_by(generated) != _order_by(gold):
        return "WRONG_ORDERING"
    if _where(generated) != _where(gold):
        return "WRONG_FILTER"
    return "OTHER"


def _tables(expression: exp.Expression) -> tuple[str, ...]:
    cte_names = {
        cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name
    }
    return tuple(
        sorted(
            {
                table.name.lower()
                for table in expression.find_all(exp.Table)
                if table.name.lower() not in cte_names
            }
        )
    )


def _select_expressions(expression: exp.Expression) -> tuple[str, ...]:
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if select is None:
        return ()
    values: list[str] = []
    for item in select.expressions:
        if isinstance(item, exp.Alias):
            item = item.this
        values.append(item.sql(dialect="postgres"))
    return tuple(values)


def _limit(expression: exp.Expression) -> str | None:
    limit = expression.find(exp.Limit)
    return limit.expression.sql(dialect="postgres") if limit else None


def _windows(expression: exp.Expression) -> tuple[str, ...]:
    return tuple(
        sorted(window.sql(dialect="postgres") for window in expression.find_all(exp.Window))
    )


def _joins(expression: exp.Expression) -> tuple[str, ...]:
    return tuple(
        sorted(
            join.args["on"].sql(dialect="postgres")
            for join in expression.find_all(exp.Join)
            if join.args.get("on") is not None
        )
    )


def _group_by(expression: exp.Expression) -> tuple[str, ...]:
    select = expression if isinstance(expression, exp.Select) else expression.find(exp.Select)
    if select is None or not select.args.get("group"):
        return ()
    return tuple(
        sorted(item.sql(dialect="postgres") for item in select.args["group"].expressions)
    )


def _where(expression: exp.Expression) -> str | None:
    where = expression.find(exp.Where)
    return where.this.sql(dialect="postgres") if where else None


def _aggregates(expression: exp.Expression) -> tuple[str, ...]:
    return tuple(sorted(item.sql(dialect="postgres") for item in expression.find_all(exp.AggFunc)))


def _order_by(expression: exp.Expression) -> tuple[str, ...]:
    return tuple(
        item.sql(dialect="postgres") for item in expression.find_all(exp.Ordered)
    )


def _contains(expression: exp.Expression, node_type: type[exp.Expression]) -> bool:
    return any(isinstance(node, node_type) for node in expression.walk())


def _has_percentage_scaling(expression: exp.Expression) -> bool:
    return any(
        isinstance(node, exp.Literal) and node.is_number and float(node.this) == 100
        for node in expression.walk()
    )
