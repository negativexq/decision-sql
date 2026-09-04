"""Small AST helpers for runtime-legitimate SQL evidence."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse_one


@dataclass(frozen=True)
class SqlFacts:
    tree: exp.Expression
    tables: frozenset[str]
    aliases: dict[str, str]
    projected_columns: frozenset[str]
    projection_count: int
    grouped_columns: frozenset[str]
    aggregate_functions: frozenset[str]
    aggregate_columns: frozenset[str]
    has_group_by: bool
    has_having: bool
    has_order_by: bool
    has_limit: bool
    limit_value: int | None
    has_join: bool
    has_cross_join: bool
    has_unqualified_join: bool
    has_where: bool
    predicate_columns: frozenset[str]
    has_distinct_count: bool
    has_non_distinct_count: bool
    has_subquery: bool


def extract_sql_facts(sql: str) -> SqlFacts:
    tree = parse_one(sql, read="postgres")
    aliases: dict[str, str] = {}
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        tables.add(name)
        aliases[name] = name
        if table.alias:
            aliases[table.alias.lower()] = name

    selects = list(tree.find_all(exp.Select))
    final_select = tree if isinstance(tree, exp.Select) else (selects[-1] if selects else tree)
    projected = {_column_key(column, aliases) for column in final_select.find_all(exp.Column)}
    grouped = {
        _column_key(column, aliases)
        for select in selects
        for group in _groups(select)
        for column in group.find_all(exp.Column)
    }
    aggregate_nodes = list(tree.find_all(exp.AggFunc))
    aggregate_columns = {
        _column_key(column, aliases)
        for node in aggregate_nodes
        for column in node.find_all(exp.Column)
    }
    functions = {node.key.upper() for node in aggregate_nodes}
    joins = list(tree.find_all(exp.Join))
    predicates = list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Having))
    predicate_columns = {
        _column_key(column, aliases)
        for predicate in predicates
        for column in predicate.find_all(exp.Column)
    }
    limits = list(tree.find_all(exp.Limit))
    limit_value = _limit_value(limits[-1]) if limits else None
    count_nodes = list(tree.find_all(exp.Count))
    return SqlFacts(
        tree=tree,
        tables=frozenset(tables),
        aliases=aliases,
        projected_columns=frozenset(projected),
        projection_count=(
            len(final_select.expressions) if isinstance(final_select, exp.Select) else 0
        ),
        grouped_columns=frozenset(grouped),
        aggregate_functions=frozenset(functions),
        aggregate_columns=frozenset(aggregate_columns),
        has_group_by=any(_groups(select) for select in selects),
        has_having=bool(list(tree.find_all(exp.Having))),
        has_order_by=bool(list(tree.find_all(exp.Order))),
        has_limit=bool(limits),
        limit_value=limit_value,
        has_join=bool(joins),
        has_cross_join=any(join.args.get("side") == "CROSS" for join in joins),
        has_unqualified_join=any(join.args.get("on") is None for join in joins),
        has_where=bool(list(tree.find_all(exp.Where))),
        predicate_columns=frozenset(predicate_columns),
        has_distinct_count=any(_count_is_distinct(node) for node in count_nodes),
        has_non_distinct_count=any(not _count_is_distinct(node) for node in count_nodes),
        has_subquery=bool(list(tree.find_all(exp.Subquery))),
    )


def _groups(select: exp.Expression) -> tuple[exp.Expression, ...]:
    group = select.args.get("group")
    return tuple(group.expressions) if isinstance(group, exp.Group) else ()


def _column_key(column: exp.Column, aliases: dict[str, str]) -> str:
    table = column.table.lower()
    return f"{aliases.get(table, table)}.{column.name.lower()}" if table else column.name.lower()


def _limit_value(limit: exp.Limit) -> int | None:
    expression = limit.expression
    if isinstance(expression, exp.Literal) and expression.is_int:
        return int(expression.this)
    return None


def _count_is_distinct(node: exp.Count) -> bool:
    expression = node.this
    return isinstance(expression, exp.Distinct) or bool(
        expression and expression.args.get("distinct")
    )
