from dataclasses import dataclass
from typing import cast

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.catalog.models import SchemaCatalog, TableMetadata
from app.sql.functions import SAFE_FUNCTION_FAMILIES, SAFE_FUNCTIONS
from app.sql.models import PolicyCode, PolicyRejection
from app.sql.parser import ParsedSQL

FORBIDDEN_CATALOGS = {
    "pg_catalog",
    "pg_shadow",
    "pg_roles",
    "pg_authid",
    "pg_user",
    "information_schema",
}

STABLE_TEMPORAL_FUNCTIONS = frozenset(SAFE_FUNCTION_FAMILIES["SAFE_STABLE_TEMPORAL"])
VOLATILE_NONDETERMINISTIC_FUNCTIONS = frozenset({"CLOCK_TIMESTAMP", "RANDOM"})
NONDETERMINISTIC_FUNCTIONS = frozenset(
    {*STABLE_TEMPORAL_FUNCTIONS, *VOLATILE_NONDETERMINISTIC_FUNCTIONS}
)
STATEFUL_OR_DANGEROUS_FUNCTIONS = frozenset(
    {
        "ADVISORY_LOCK",
        "PG_SLEEP",
        "SET_CONFIG",
    }
)


@dataclass(frozen=True)
class _Relation:
    table: TableMetadata | None = None
    columns: frozenset[str] = frozenset()

    def has_column(self, name: str) -> bool:
        return (self.table is not None and self.table.get_column(name) is not None) or (
            name in self.columns
        )

    def queryable_column(self, name: str) -> bool:
        if self.table is None:
            return name in self.columns
        column = self.table.get_column(name)
        return column is not None and column.queryable


class SQLPolicy:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog

    def validate(self, parsed: ParsedSQL) -> PolicyRejection | None:
        expression = parsed.expression
        if not isinstance(expression, exp.Query):
            return self._reject(
                PolicyCode.NON_READ_ONLY_STATEMENT,
                "Only SELECT statements and SELECT-based CTEs are allowed.",
                type(expression).__name__,
            )
        into = expression.args.get("into")
        locks = list(expression.find_all(exp.Lock))
        if into is not None or locks:
            return self._reject(
                PolicyCode.NON_READ_ONLY_STATEMENT,
                "SELECT INTO and locking clauses are not allowed.",
                type(into).__name__ if into is not None else type(locks[0]).__name__,
            )

        table_rejection = self._validate_tables(expression)
        if table_rejection:
            return table_rejection

        function_rejection = self._validate_functions(expression)
        if function_rejection:
            return function_rejection
        return self._validate_columns(expression) or self._validate_joins(expression)

    def _validate_tables(self, expression: exp.Expression) -> PolicyRejection | None:
        """Validate physical tables while leaving CTE names to scope resolution."""
        cte_names = {
            cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name
        }
        seen: set[tuple[str, str, str]] = set()
        for table in expression.find_all(exp.Table):
            table_name = table.name.lower()
            schema_name = table.db.lower() if table.db else ""
            catalog_name = table.catalog.lower() if table.catalog else ""
            identity = (catalog_name, schema_name, table_name)
            if identity in seen:
                continue
            seen.add(identity)
            if schema_name in FORBIDDEN_CATALOGS or catalog_name in FORBIDDEN_CATALOGS:
                return self._reject(
                    PolicyCode.FORBIDDEN_CATALOG,
                    f"System catalog access is forbidden: {table.sql()}",
                    table.sql(),
                )
            if table_name in FORBIDDEN_CATALOGS:
                return self._reject(
                    PolicyCode.FORBIDDEN_CATALOG,
                    f"System catalog access is forbidden: {table.sql()}",
                    table.sql(),
                )
            if table_name in cte_names:
                continue
            if schema_name and schema_name != "public":
                return self._reject(
                    PolicyCode.FORBIDDEN_TABLE,
                    f"Only the public application schema is queryable: {table.sql()}",
                    table.sql(),
                )
            metadata = self.catalog.get_table(table_name)
            if metadata is None:
                return self._reject(
                    PolicyCode.UNKNOWN_TABLE,
                    f"Table is not in the queryable catalog: {table_name}",
                    table_name,
                )
            if not metadata.queryable:
                return self._reject(
                    PolicyCode.FORBIDDEN_TABLE,
                    f"Table is not queryable: {table_name}",
                    table_name,
                )
        return None

    def _validate_functions(self, expression: exp.Expression) -> PolicyRejection | None:
        for function in expression.find_all(exp.Func):
            # SQLGlot models boolean connectors and CASE expressions as Func
            # subclasses; they are expression syntax, not callable functions.
            if isinstance(function, (exp.Case, exp.If, exp.Connector)):
                continue
            function_name = self.function_name(function)
            if function_name not in SAFE_FUNCTIONS:
                return self._reject(
                    PolicyCode.FORBIDDEN_FUNCTION,
                    f"Function is not in the analytical allowlist: {function_name}",
                    function_name,
                )
        return None

    def _validate_columns(self, expression: exp.Expression) -> PolicyRejection | None:
        scopes = tuple(traverse_scope(expression))
        for scope in scopes:
            rejection = self._validate_scope_columns(scope, scopes)
            if rejection:
                return rejection
        return None

    def _validate_scope_columns(
        self, scope: Scope, scopes: tuple[Scope, ...]
    ) -> PolicyRejection | None:
        relations = self._scope_relations(scope)
        if isinstance(scope.expression, exp.Select):
            output_aliases = {
                item.alias_or_name.lower()
                for item in scope.expression.selects
                if isinstance(item, exp.Alias) and item.alias_or_name
            }
        elif isinstance(scope.expression, exp.SetOperation):
            output_aliases = set(self._output_names_from_select(scope.expression.left, scope))
        else:
            output_aliases = set()
        for column in self._visible_columns(scope, scopes):
            name = self._identifier_name(column.name)
            qualifier = column.table.lower()
            if (
                not qualifier
                and name in output_aliases
                and self._is_projection_alias_reference(scope, column)
            ):
                continue
            if not qualifier and self._is_ambiguous_unqualified_column(scope, relations, name):
                return self._reject(
                    PolicyCode.UNKNOWN_COLUMN,
                    f"Ambiguous unqualified column in the queryable scope: {column.sql()}",
                    column.sql(),
                )
            relation = self._resolve_relation(scope, relations, qualifier, name)
            if relation is None:
                return self._reject(
                    PolicyCode.UNKNOWN_COLUMN,
                    f"Column cannot be resolved in the queryable catalog: {column.sql()}",
                    column.sql(),
                )
            if not qualifier and name in relations and not relation.has_column(name):
                # PostgreSQL permits a relation alias as a composite/row value
                # (for example, row_to_json(article_ars)).
                continue
            if not relation.has_column(name):
                return self._reject(
                    PolicyCode.UNKNOWN_COLUMN,
                    f"Column cannot be resolved in the queryable scope: {column.sql()}",
                    column.sql(),
                )
            if not relation.queryable_column(name):
                table_name = relation.table.name if relation.table is not None else qualifier
                return self._reject(
                    PolicyCode.FORBIDDEN_COLUMN,
                    f"Column is not queryable: {table_name}.{name}",
                    f"{table_name}.{name}",
                )

        for item in scope.expression.selects if isinstance(scope.expression, exp.Select) else ():
            if isinstance(item, exp.Star):
                qualifier = ""
            elif isinstance(item, exp.Column) and self._identifier_name(item.name) == "*":
                qualifier = item.table.lower()
            else:
                continue
            candidates = (
                [relations[qualifier]]
                if qualifier in relations
                else list(relations.values())
                if not qualifier
                else []
            )
            for relation in candidates:
                if relation.table is None:
                    continue
                denied = next(
                    (column for column in relation.table.columns if not column.queryable), None
                )
                if denied:
                    return self._reject(
                        PolicyCode.FORBIDDEN_COLUMN,
                        "Wildcard would expose a non-queryable column: "
                        f"{relation.table.name}.{denied.name}",
                        f"{relation.table.name}.{denied.name}",
                    )
        return None

    def _validate_joins(self, expression: exp.Expression) -> PolicyRejection | None:
        """Equality between columns of two tables must follow an authorized relationship.

        The tables themselves may be authorized while the columns joining them are
        not: ``orders.id = customers.id`` matches unrelated rows that happen to share
        a number.  Columns of CTEs and derived tables are not traced to their source
        tables, and a table compared with itself is left alone.

        Once two tables are joined by authorized relationships ANDed into the same
        SELECT, a further equality between them, such as ``trans.date = loan.date``,
        only narrows rows the authorized join already paired; it is a filter, not a
        new relationship.  A link in an enclosing SELECT does not count: inside the
        subquery the equality alone would pair the rows.
        """
        for scope in traverse_scope(expression):
            if not isinstance(scope.expression, exp.Select):
                continue
            relations = self._scope_relations(scope)
            pairs: list[tuple[exp.EQ, tuple[str, str], tuple[str, str]]] = []
            for equality in scope.expression.find_all(exp.EQ):
                if equality.find_ancestor(exp.Select) is not scope.expression:
                    continue
                left = self._join_column(scope, relations, equality.left)
                right = self._join_column(scope, relations, equality.right)
                if left is None or right is None or left[0] == right[0]:
                    continue
                pairs.append((equality, left, right))
            links = {
                frozenset((left[0], right[0]))
                for equality, left, right in pairs
                if _conjunct(equality) and self._related(left, right)
            }
            for equality, left, right in pairs:
                if self._related(left, right):
                    continue
                if _conjunct(equality) and _connected(left[0], right[0], links):
                    continue
                return self._reject(
                    PolicyCode.UNAUTHORIZED_JOIN,
                    self._join_message(left, right),
                    f"{left[0]}.{left[1]} = {right[0]}.{right[1]}",
                )
        return None

    def _join_column(
        self, scope: Scope, relations: dict[str, _Relation], node: exp.Expression
    ) -> tuple[str, str] | None:
        while isinstance(node, (exp.Cast, exp.Paren)):
            node = node.this
        if not isinstance(node, exp.Column):
            return None
        name = self._identifier_name(node.name)
        relation = self._resolve_relation(scope, relations, node.table.lower(), name)
        if relation is None or relation.table is None or relation.table.get_column(name) is None:
            return None
        return relation.table.name.lower(), name

    def _relationships(self, a: str, b: str) -> list[tuple[str, str, str, str]]:
        found: list[tuple[str, str, str, str]] = []
        for source, target in ((a, b), (b, a)):
            table = self.catalog.get_table(source)
            for item in table.relationships if table is not None else ():
                if item.referenced_table.lower() == target:
                    found.append(
                        (source, item.column.lower(), target, item.referenced_column.lower())
                    )
        return found

    def _related(self, left: tuple[str, str], right: tuple[str, str]) -> bool:
        return any(
            {(source, column), (target, referenced)} == {left, right}
            for source, column, target, referenced in self._relationships(left[0], right[0])
        )

    def _join_message(self, left: tuple[str, str], right: tuple[str, str]) -> str:
        known = self._relationships(left[0], right[0])
        compared = f"{left[0]}.{left[1]} = {right[0]}.{right[1]}"
        if not known:
            return (
                f"The query compares {compared}, but no authorized relationship links "
                f"{left[0]} and {right[0]}. If the question needs them joined, report the "
                "missing relationship instead of joining on matching columns."
            )
        listed = "; ".join(f"{s}.{c} = {t}.{r}" for s, c, t, r in known)
        return (
            f"The query compares {compared}, which is not an authorized relationship. "
            f"{left[0]} and {right[0]} are linked only by {listed}."
        )

    def _scope_relations(self, scope: Scope) -> dict[str, _Relation]:
        relations: dict[str, _Relation] = {}
        source_keys = self._scope_source_keys(scope)
        for name in source_keys:
            source = scope.sources.get(name)
            if source is None:
                continue
            if isinstance(source, exp.Table):
                metadata = self.catalog.get_table(source.name)
                if metadata is not None:
                    relations[name.lower()] = _Relation(table=metadata)
            elif isinstance(source, Scope):
                relations[name.lower()] = _Relation(columns=self._output_columns(source))
        return relations

    @staticmethod
    def _scope_source_keys(scope: Scope) -> tuple[str, ...]:
        """Return relation aliases actually present in this SELECT's FROM list.

        SQLGlot may index a CTE scope by both its CTE name and each alias that
        references it.  Only the aliases in the current FROM/JOIN clauses are
        visible relations; retaining the extra CTE key makes one relation look
        like several and creates false ambiguity.
        """
        expression = scope.expression
        if not isinstance(expression, exp.Select):
            return tuple(scope.sources)
        nodes: list[exp.Expression] = []
        from_clause = expression.args.get("from_")
        if isinstance(from_clause, exp.From) and from_clause.this is not None:
            nodes.append(from_clause.this)
        nodes.extend(
            join.this for join in expression.args.get("joins", ()) if join.this is not None
        )
        keys: list[str] = []
        for node in nodes:
            name = node.alias_or_name
            if not name and isinstance(node, exp.Table):
                name = node.name
            if not name:
                continue
            source_key = next((key for key in scope.sources if key.lower() == name.lower()), None)
            if source_key is not None and source_key not in keys:
                keys.append(source_key)
        return tuple(keys)

    def _visible_columns(self, scope: Scope, scopes: tuple[Scope, ...]) -> tuple[exp.Column, ...]:
        """Return this scope's columns plus correlated references, not nested locals."""
        descendants = [
            candidate for candidate in scopes if SQLPolicy._is_descendant(candidate, scope)
        ]
        nested_local_ids = {
            id(column)
            for descendant in descendants
            for column in descendant.columns
            if self._is_local_column(descendant, column)
        }
        return tuple(column for column in scope.columns if id(column) not in nested_local_ids)

    def _is_local_column(self, scope: Scope, column: exp.Column) -> bool:
        relations = self._scope_relations(scope)
        name = self._identifier_name(column.name)
        qualifier = column.table.lower()
        if qualifier:
            return qualifier in relations
        return any(relation.has_column(name) for relation in relations.values()) or (
            name in relations
        )

    @staticmethod
    def _is_descendant(candidate: Scope, ancestor: Scope) -> bool:
        current = candidate.parent
        while current is not None:
            if current is ancestor:
                return True
            current = current.parent
        return False

    def _resolve_relation(
        self, scope: Scope, relations: dict[str, _Relation], qualifier: str, name: str
    ) -> _Relation | None:
        if qualifier:
            relation = relations.get(qualifier)
            if relation is not None:
                return relation
        else:
            relation = relations.get(name)
            if relation is not None:
                return relation
            local = [relation for relation in relations.values() if relation.has_column(name)]
            if len(local) == 1:
                return local[0]
            if len(local) > 1:
                return None
        parent = scope.parent
        if parent is not None:
            return self._resolve_relation(parent, self._scope_relations(parent), qualifier, name)
        return None

    def _is_ambiguous_unqualified_column(
        self, scope: Scope, relations: dict[str, _Relation], name: str
    ) -> bool:
        """Report ambiguity before resolution can fall through to a parent scope."""
        if name in relations:
            # A relation alias in expression position is a PostgreSQL row value,
            # not an unqualified column competing with visible columns.
            return False
        local = [relation for relation in relations.values() if relation.has_column(name)]
        if len(local) > 1:
            return True
        if local:
            return False
        parent = scope.parent
        return parent is not None and self._is_ambiguous_unqualified_column(
            parent, self._scope_relations(parent), name
        )

    def _output_columns(self, scope: Scope) -> frozenset[str]:
        if isinstance(scope.expression, (exp.Unnest, exp.Explode)):
            alias = scope.expression.args.get("alias")
            if isinstance(alias, exp.TableAlias) and alias.alias_or_name:
                return frozenset({self._identifier_name(alias.alias_or_name)})
            return frozenset({"unnest"})
        if isinstance(scope.expression, (exp.Lateral, exp.Values)):
            # LATERAL (VALUES ...) AS t(a, b) and (VALUES ...) AS t(a, b) name their columns
            # in the alias; without this the policy reports them as unknown.
            alias = scope.expression.args.get("alias")
            explicit = alias.args.get("columns") if isinstance(alias, exp.TableAlias) else None
            if explicit:
                return frozenset(column.name.lower() for column in explicit)
        if isinstance(scope.expression, exp.Lateral):
            nested = scope.sources.get("")
            if isinstance(nested, Scope):
                return self._output_columns(nested)
        if isinstance(scope.expression, exp.SetOperation):
            left = scope.expression.args.get("this")
            return (
                self._output_names_from_select(left, scope)
                if isinstance(left, exp.Select)
                else frozenset()
            )
        parent = scope.expression.parent
        if isinstance(parent, exp.CTE):
            alias = parent.args.get("alias")
        elif isinstance(parent, exp.Subquery):
            alias = parent.args.get("alias")
        else:
            alias = None
        explicit = alias.args.get("columns") if isinstance(alias, exp.TableAlias) else None
        if explicit:
            return frozenset(column.name.lower() for column in explicit)
        return self._output_names_from_select(scope.expression, scope)

    def _output_names_from_select(
        self, expression: exp.Expression | None, scope: Scope | None
    ) -> frozenset[str]:
        names: set[str] = set()
        if not isinstance(expression, exp.Select):
            return frozenset()
        for item in expression.selects:
            if isinstance(item, exp.Star):
                relations = self._scope_relations(scope) if scope is not None else {}
                for source in relations.values():
                    if source.table is not None:
                        names.update(column.name.lower() for column in source.table.columns)
                    else:
                        names.update(source.columns)
            elif isinstance(item, exp.Column):
                if self._identifier_name(item.name) == "*":
                    relations = self._scope_relations(scope) if scope is not None else {}
                    selected = relations.get(item.table.lower()) if item.table else None
                    sources = [selected] if selected is not None else list(relations.values())
                    for source in sources:
                        if source.table is not None:
                            names.update(column.name.lower() for column in source.table.columns)
                        else:
                            names.update(source.columns)
                else:
                    names.add(self._identifier_name(item.name))
            elif isinstance(item, exp.Alias):
                names.add(self._identifier_name(item.alias_or_name))
            else:
                output_name = getattr(item, "output_name", "")
                if output_name:
                    names.add(self._identifier_name(str(output_name)))
        return frozenset(names)

    @staticmethod
    def _is_projection_alias_reference(scope: Scope, column: exp.Column) -> bool:
        for key in ("order", "group"):
            clause = scope.expression.args.get(key)
            if clause is not None and any(
                candidate is column for candidate in clause.find_all(exp.Column)
            ):
                return True
        return False

    @staticmethod
    def _identifier_name(value: str) -> str:
        """Normalize SQLGlot identifier text when comments trail an identifier."""
        return value.split("/*", 1)[0].strip().lower()

    @staticmethod
    def function_name(function: exp.Func) -> str:
        if isinstance(function, exp.Anonymous):
            return function.name.upper()
        if isinstance(function, exp.TimestampTrunc):
            return "DATE_TRUNC"
        if isinstance(function, exp.JSONBExtract):
            # SQLGlot lowers PostgreSQL's jsonb ``#>`` operator to this
            # function class.  Keep the policy identity aligned with the
            # already-reviewed read-only JSON extraction capability.
            return "JSON_EXTRACT"
        if isinstance(function, exp.JSONBExtractScalar):
            # SQLGlot lowers PostgreSQL's jsonb ``#>>`` operator here.
            return "JSON_EXTRACT_SCALAR"
        if isinstance(function, exp.JSONArrayAgg):
            return "JSON_AGG"
        if isinstance(function, exp.JSONObjectAgg):
            return "JSON_OBJECT_AGG"
        if isinstance(function, exp.JSONBObjectAgg):
            # The generic class-name fallback spells this J_S_O_N_B_OBJECT_AGG.
            return "JSONB_OBJECT_AGG"
        if isinstance(function, exp.GroupConcat):
            return "STRING_AGG"
        if isinstance(function, exp.Explode):
            return "UNNEST"
        if isinstance(function, exp.TimeToStr):
            return "TO_CHAR"
        if isinstance(function, exp.Rand):
            return "RANDOM"
        return cast(str, function.sql_name()).upper()  # type: ignore[no-untyped-call]

    @staticmethod
    def _reject(code: PolicyCode, message: str, object_name: str | None = None) -> PolicyRejection:
        return PolicyRejection(code=code, message=message, object=object_name)


def _conjunct(equality: exp.EQ) -> bool:
    """Whether the equality is ANDed into a WHERE or a JOIN ... ON of its SELECT."""
    node: exp.Expression = equality
    while isinstance(node.parent, exp.And | exp.Paren):
        node = node.parent
    parent = node.parent
    return isinstance(parent, exp.Where) or (isinstance(parent, exp.Join) and node.arg_key == "on")


def _connected(a: str, b: str, edges: set[frozenset[str]]) -> bool:
    """Whether a path of authorized equalities links two tables."""
    reached, frontier = {a}, [a]
    while frontier:
        table = frontier.pop()
        for edge in edges:
            if table in edge:
                for other in edge - {table}:
                    if other not in reached:
                        reached.add(other)
                        frontier.append(other)
    return b in reached
