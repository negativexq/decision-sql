"""Check the text literals a query compares with columns whose stored values are known.

A filter on ``segment = 'premium'`` returns no rows when the column stores
``'Premium'``, and nothing in the result says why.  For columns whose complete
set of values the runtime read from statistics, a literal that differs from
exactly one stored value only in case, spacing, or punctuation is rewritten to
that value; one that matches none is reported, so the run can say why its
result may be empty.  The stored values themselves stay on the server: the
check works whether or not they were shared with the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlglot import exp

from app.catalog.models import SchemaCatalog
from app.sql.models import UnmatchedValue


@dataclass(frozen=True)
class ValueCheck:
    expression: exp.Expression
    changes: tuple[str, ...]
    unmatched: tuple[UnmatchedValue, ...]
    checked: int


@dataclass(frozen=True)
class _Domain:
    values: frozenset[str]
    by_key: dict[str, tuple[str, ...]]
    shared: bool


def _key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


def direct_tables(select: exp.Select, ctes: set[str]) -> dict[str, str]:
    """Alias to table name for the physical tables this SELECT reads directly."""
    sources: list[exp.Expression] = []
    from_clause = select.args.get("from") or select.args.get("from_")
    if from_clause is not None:
        sources.append(from_clause.this)
    sources.extend(join.this for join in select.args.get("joins") or ())
    return {
        source.alias_or_name.lower(): source.name.lower()
        for source in sources
        if isinstance(source, exp.Table) and source.name.lower() not in ctes
    }


def cte_names(expression: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}


class ValueChecker:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self._domains: dict[tuple[str, str], _Domain] = {}
        self._columns: dict[str, set[str]] = {}
        for table in catalog.tables:
            self._columns[table.name.lower()] = {column.name.lower() for column in table.columns}
            for column in table.columns:
                if not column.value_domain:
                    continue
                by_key: dict[str, list[str]] = {}
                for value in column.value_domain:
                    by_key.setdefault(_key(value), []).append(value)
                self._domains[(table.name.lower(), column.name.lower())] = _Domain(
                    values=frozenset(column.value_domain),
                    by_key={key: tuple(items) for key, items in by_key.items()},
                    shared=bool(column.known_values),
                )

    @property
    def active(self) -> bool:
        return bool(self._domains)

    def check(self, expression: exp.Expression) -> ValueCheck:
        if not self._domains:
            return ValueCheck(expression, (), (), 0)
        expression = expression.copy()
        ctes = cte_names(expression)
        changes: list[str] = []
        unmatched: list[UnmatchedValue] = []
        checked = 0
        for column, literals in self._comparisons(expression):
            select = column.find_ancestor(exp.Select)
            if select is None:
                continue
            target = self._resolve(column, direct_tables(select, ctes))
            if target is None or target not in self._domains:
                continue
            domain = self._domains[target]
            for literal in literals:
                checked += 1
                value = literal.this
                if value in domain.values:
                    continue
                matches = domain.by_key.get(_key(value), ())
                if len(matches) == 1:
                    literal.replace(exp.Literal.string(matches[0]))
                    changes.append(
                        f"The filter on {target[0]}.{target[1]} compared it with '{value}'; "
                        f"the column stores that value as '{matches[0]}'."
                    )
                else:
                    unmatched.append(
                        UnmatchedValue(
                            table=target[0], column=target[1], literal=value, shared=domain.shared
                        )
                    )
        return ValueCheck(expression, tuple(changes), tuple(unmatched), checked)

    @staticmethod
    def _comparisons(
        expression: exp.Expression,
    ) -> list[tuple[exp.Column, list[exp.Literal]]]:
        found: list[tuple[exp.Column, list[exp.Literal]]] = []
        for node in expression.find_all(exp.EQ, exp.NEQ):
            left, right = node.left, node.right
            if isinstance(left, exp.Literal) and isinstance(right, exp.Column):
                left, right = right, left
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string:
                found.append((left, [right]))
        for membership in expression.find_all(exp.In):
            literals = [item for item in membership.expressions if isinstance(item, exp.Literal)]
            if (
                isinstance(membership.this, exp.Column)
                and literals
                and all(i.is_string for i in literals)
            ):
                found.append((membership.this, literals))
        return found

    def _resolve(self, column: exp.Column, aliases: dict[str, str]) -> tuple[str, str] | None:
        name = column.name.lower()
        if column.table:
            table = aliases.get(column.table.lower())
            return (table, name) if table is not None else None
        owners = {table for table in aliases.values() if name in self._columns.get(table, ())}
        return (owners.pop(), name) if len(owners) == 1 else None
