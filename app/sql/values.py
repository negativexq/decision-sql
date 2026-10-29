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
from typing import Protocol

from sqlglot import exp

from app.catalog.models import SchemaCatalog, Sensitivity
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
    """The value without case, spacing, or punctuation; a code of digits without leading zeros.

    ``01611430131177`` and ``1611430131177`` name the same code: a stored code
    keeps its zero padding, and the person asking rarely types it.
    """
    key = re.sub(r"[\W_]+", "", value.casefold())
    return (key.lstrip("0") or "0") if key.isdigit() else key


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
        return _resolve(column, aliases, self._columns)


def _resolve(
    column: exp.Column, aliases: dict[str, str], columns: dict[str, set[str]]
) -> tuple[str, str] | None:
    name = column.name.lower()
    if column.table:
        table = aliases.get(column.table.lower())
        return (table, name) if table is not None else None
    owners = {table for table in aliases.values() if name in columns.get(table, ())}
    return (owners.pop(), name) if len(owners) == 1 else None


_TEXT_TYPES = ("char", "text", "string", "varchar", "citext")


class ValueProbe:
    """Look up, in the database itself, text literals compared with columns of unknown values.

    The statistics behind ``ValueChecker`` hold only short categorical columns, so a
    filter such as ``power_name = 'stealth'`` on a column of a few hundred names is not
    checked before the query runs.  When such a query comes back empty, the probe asks
    the database whether each literal is stored, and if not, whether exactly one stored
    value differs from it only in case, spacing, punctuation, or the zero padding of a
    code of digits.  Confidential columns
    and columns ``ValueChecker`` already covers are left alone.
    """

    def __init__(self, catalog: SchemaCatalog) -> None:
        self._columns: dict[str, set[str]] = {}
        self._probed: set[tuple[str, str]] = set()
        for table in catalog.tables:
            self._columns[table.name.lower()] = {column.name.lower() for column in table.columns}
            for column in table.columns:
                if (
                    column.value_domain
                    or not column.queryable
                    or column.sensitivity is Sensitivity.CONFIDENTIAL
                    or not any(kind in column.type.lower() for kind in _TEXT_TYPES)
                ):
                    continue
                self._probed.add((table.name.lower(), column.name.lower()))

    def targets(self, expression: exp.Expression) -> list[tuple[tuple[str, str], exp.Literal]]:
        """The probed columns this query compares with text literals, with each literal."""
        ctes = cte_names(expression)
        found: list[tuple[tuple[str, str], exp.Literal]] = []
        for column, literals in ValueChecker._comparisons(expression):
            select = column.find_ancestor(exp.Select)
            if select is None:
                continue
            target = _resolve(column, direct_tables(select, ctes), self._columns)
            if target is None or target not in self._probed:
                continue
            found.extend((target, literal) for literal in literals)
        return found

    def probe(self, expression: exp.Expression, lookup: ValueLookup) -> ValueCheck:
        """Rewrite literals with one stored spelling; report those the column does not store."""
        expression = expression.copy()
        changes: list[str] = []
        unmatched: list[UnmatchedValue] = []
        targets = self.targets(expression)
        for target, literal in targets:
            value = literal.this
            if lookup.stored(target, value):
                continue
            key = _key(value)
            # A literal of only punctuation has no key; any punctuation-only value would match it.
            matches = lookup.spellings(target, key) if key else ()
            if len(matches) == 1:
                literal.replace(exp.Literal.string(matches[0]))
                changes.append(
                    f"The filter on {target[0]}.{target[1]} compared it with '{value}'; "
                    f"the column stores that value as '{matches[0]}'."
                )
            else:
                unmatched.append(
                    UnmatchedValue(table=target[0], column=target[1], literal=value, shared=False)
                )
        return ValueCheck(expression, tuple(changes), tuple(unmatched), len(targets))


class ValueLookup(Protocol):
    """Answers the probe's two questions about one column, from the database."""

    def stored(self, target: tuple[str, str], value: str) -> bool: ...

    def spellings(self, target: tuple[str, str], key: str) -> tuple[str, ...]:
        """Up to two stored values whose form under ``_key`` is ``key``."""
        ...
