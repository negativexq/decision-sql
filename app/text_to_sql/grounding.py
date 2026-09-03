from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse_one

from app.catalog.models import SchemaContext
from app.generation.intent import IntentJoin, QueryIntent


class IntentVisibilityError(ValueError):
    pass


class IntentSqlDiagnostics(BaseModel):
    """Non-authoritative comparison between intent metadata and generated SQL."""

    model_config = ConfigDict(frozen=True)

    intent_tables_not_used: tuple[str, ...] = ()
    sql_tables_not_in_intent: tuple[str, ...] = ()
    intent_columns_not_used: tuple[str, ...] = ()
    sql_columns_not_in_intent: tuple[str, ...] = ()
    expected_limit: int | None = None
    sql_limit: int | None = None
    limit_agreement: bool | None = None
    joins_missing: tuple[str, ...] = ()
    joins_unexpected: tuple[str, ...] = ()
    join_agreement: bool | None = None
    ordering_agreement: bool | None = None
    intent_aggregations_not_used: tuple[str, ...] = ()
    sql_aggregations_not_in_intent: tuple[str, ...] = ()
    aggregation_agreement: bool | None = None
    intent_group_by_not_used: tuple[str, ...] = ()
    sql_group_by_not_in_intent: tuple[str, ...] = ()
    grouping_agreement: bool | None = None
    window_agreement: bool | None = None


def validate_intent_visibility(intent: QueryIntent, context: SchemaContext) -> None:
    visible_tables = {table.name.lower() for table in context.tables}
    visible_columns = {
        f"{table.name.lower()}.{column.name.lower()}"
        for table in context.tables
        for column in table.columns
    }
    invalid_tables = sorted(
        table for table in intent.selected_tables if table.lower() not in visible_tables
    )
    invalid_columns = sorted(
        column
        for column in intent.selected_columns
        if _normalize_intent_column(column, visible_columns) not in visible_columns
    )
    invalid_joins = sorted(
        _join_key(join)
        for join in intent.joins
        if join.source_table.lower() not in visible_tables
        or join.target_table.lower() not in visible_tables
        or f"{join.source_table.lower()}.{join.source_column.lower()}" not in visible_columns
        or f"{join.target_table.lower()}.{join.target_column.lower()}" not in visible_columns
    )
    if invalid_tables or invalid_columns or invalid_joins:
        details: list[str] = []
        if invalid_tables:
            details.append(f"tables={','.join(invalid_tables)}")
        if invalid_columns:
            details.append(f"columns={','.join(invalid_columns)}")
        if invalid_joins:
            details.append(f"joins={','.join(invalid_joins)}")
        raise IntentVisibilityError(
            "QueryIntent references objects outside SchemaContext: " + "; ".join(details)
        )
    for expression in (
        *intent.filters,
        *intent.aggregations,
        *intent.group_by,
        *intent.order_by,
        *intent.window_operations,
    ):
        _validate_expression_visibility(expression, visible_tables, visible_columns)


def compare_intent_to_sql(intent: QueryIntent, sql: str) -> IntentSqlDiagnostics:
    """Compare bounded structural fields without making an authorization decision."""
    expression = parse_one(sql, dialect="postgres")
    sql_tables = {
        table.name.lower()
        for table in expression.find_all(exp.Table)
        if table.name.lower()
        not in {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
    }
    aliases = {
        table.alias_or_name.lower(): table.name.lower()
        for table in expression.find_all(exp.Table)
    }
    sql_columns = {
        _sql_column_key(column, aliases)
        for column in expression.find_all(exp.Column)
        if column.name != "*"
    }
    intent_tables = {table.lower() for table in intent.selected_tables}
    intent_columns = {column.lower() for column in intent.selected_columns}
    intent_join_keys = {_join_key(join) for join in intent.joins}
    sql_join_keys = _sql_join_keys(expression, aliases)
    sql_limit = _limit_value(expression)
    intent_order = {_normalize_expression(item) for item in intent.order_by}
    sql_order = {
        _normalize_expression(order.sql(dialect="postgres"))
        for order in expression.find_all(exp.Ordered)
    }
    if not intent.order_by:
        ordering_agreement: bool | None = None
    else:
        ordering_agreement = intent_order.issubset(sql_order)
    intent_aggregations = {
        _normalize_expression(item) for item in intent.aggregations
    }
    sql_aggregations = {
        _normalize_expression(item.sql(dialect="postgres"))
        for item in expression.find_all(exp.AggFunc)
    }
    if not intent.aggregations:
        aggregation_agreement: bool | None = None
    else:
        aggregation_agreement = intent_aggregations.issubset(sql_aggregations)
    intent_group_by = {_normalize_expression(item) for item in intent.group_by}
    group = expression.find(exp.Group)
    sql_group_by = (
        {_normalize_expression(item.sql(dialect="postgres")) for item in group.expressions}
        if group is not None
        else set()
    )
    if not intent.group_by:
        grouping_agreement: bool | None = None
    else:
        grouping_agreement = intent_group_by.issubset(sql_group_by)
    sql_windows = {
        _normalize_expression(item.sql(dialect="postgres"))
        for item in expression.find_all(exp.Window)
    }
    intent_windows = {_normalize_expression(item) for item in intent.window_operations}
    if not intent.window_operations:
        window_agreement: bool | None = None
    else:
        window_agreement = intent_windows.issubset(sql_windows)
    return IntentSqlDiagnostics(
        intent_tables_not_used=tuple(sorted(intent_tables - sql_tables)),
        sql_tables_not_in_intent=tuple(sorted(sql_tables - intent_tables)),
        intent_columns_not_used=tuple(sorted(intent_columns - sql_columns)),
        sql_columns_not_in_intent=tuple(sorted(sql_columns - intent_columns)),
        expected_limit=intent.limit,
        sql_limit=sql_limit,
        limit_agreement=(intent.limit == sql_limit) if intent.limit is not None else None,
        joins_missing=tuple(sorted(intent_join_keys - sql_join_keys)),
        joins_unexpected=tuple(sorted(sql_join_keys - intent_join_keys)),
        join_agreement=(intent_join_keys == sql_join_keys) if intent.joins else None,
        ordering_agreement=ordering_agreement,
        intent_aggregations_not_used=tuple(
            sorted(intent_aggregations - sql_aggregations)
        ),
        sql_aggregations_not_in_intent=tuple(
            sorted(sql_aggregations - intent_aggregations)
        ),
        aggregation_agreement=aggregation_agreement,
        intent_group_by_not_used=tuple(sorted(intent_group_by - sql_group_by)),
        sql_group_by_not_in_intent=tuple(sorted(sql_group_by - intent_group_by)),
        grouping_agreement=grouping_agreement,
        window_agreement=window_agreement,
    )


def _normalize_intent_column(value: str, visible_columns: set[str]) -> str:
    normalized = value.lower()
    if "." in normalized:
        return normalized
    matches = [column for column in visible_columns if column.endswith(f".{normalized}")]
    return matches[0] if len(matches) == 1 else normalized


def _validate_expression_visibility(
    value: str, visible_tables: set[str], visible_columns: set[str]
) -> None:
    try:
        expression = parse_one(value, dialect="postgres")
    except Exception as error:
        raise IntentVisibilityError(
            "QueryIntent contains an invalid structural expression."
        ) from error
    for table in expression.find_all(exp.Table):
        if table.name.lower() not in visible_tables:
            raise IntentVisibilityError(
                f"QueryIntent expression references an invisible table: {table.name}"
            )
    for column in expression.find_all(exp.Column):
        if column.table:
            key = f"{column.table.lower()}.{column.name.lower()}"
            if key not in visible_columns:
                raise IntentVisibilityError(
                    f"QueryIntent expression references an invisible column: {key}"
                )


def _join_key(join: IntentJoin) -> str:
    left = f"{join.source_table.lower()}.{join.source_column.lower()}"
    right = f"{join.target_table.lower()}.{join.target_column.lower()}"
    return _unordered_join_key(left, right)


def _sql_column_key(column: exp.Column, aliases: dict[str, str]) -> str:
    table = aliases.get(column.table.lower(), column.table.lower()) if column.table else ""
    return f"{table}.{column.name.lower()}" if table else column.name.lower()


def _sql_join_keys(expression: exp.Expression, aliases: dict[str, str]) -> set[str]:
    keys: set[str] = set()
    for join in expression.find_all(exp.Join):
        on = join.args.get("on")
        if not isinstance(on, exp.EQ):
            continue
        left = on.left
        right = on.right
        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            left_key = _sql_column_key(left, aliases)
            right_key = _sql_column_key(right, aliases)
            keys.add(_unordered_join_key(left_key, right_key))
    return keys


def _unordered_join_key(left: str, right: str) -> str:
    return "<->".join(sorted((left, right)))


def _limit_value(expression: exp.Expression) -> int | None:
    limit = expression.find(exp.Limit)
    if limit is None:
        return None
    value = limit.expression
    if isinstance(value, exp.Literal) and value.is_number:
        return int(value.this)
    return None


def _normalize_expression(value: str) -> str:
    return " ".join(value.lower().replace('"', "").split())


def diagnostic_flags(diagnostics: IntentSqlDiagnostics) -> dict[str, int]:
    """Return bounded counters suitable for traces and evaluation summaries."""
    return {
        "intent_tables_not_used": len(diagnostics.intent_tables_not_used),
        "sql_tables_not_in_intent": len(diagnostics.sql_tables_not_in_intent),
        "intent_columns_not_used": len(diagnostics.intent_columns_not_used),
        "sql_columns_not_in_intent": len(diagnostics.sql_columns_not_in_intent),
        "limit_mismatch": int(diagnostics.limit_agreement is False),
        "join_mismatch": int(diagnostics.join_agreement is False),
        "ordering_mismatch": int(diagnostics.ordering_agreement is False),
        "aggregation_mismatch": int(diagnostics.aggregation_agreement is False),
        "grouping_mismatch": int(diagnostics.grouping_agreement is False),
        "window_mismatch": int(diagnostics.window_agreement is False),
    }


def visible_column_names(context: SchemaContext) -> set[str]:
    return {
        f"{table.name.lower()}.{column.name.lower()}"
        for table in context.tables
        for column in table.columns
    }
