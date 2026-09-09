from dataclasses import dataclass
from typing import cast

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.catalog.models import SchemaCatalog, TableMetadata
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

SAFE_FUNCTION_FAMILIES = {
    "SAFE_AGGREGATE": frozenset(
        {
            "ARRAY_AGG",
            "AVG",
            "CORR",
            "COUNT",
            "JSON_AGG",
            "JSONB_AGG",
            "JSON_OBJECT_AGG",
            "JSONB_OBJECT_AGG",
            "MAX",
            "MIN",
            "PERCENTILE_CONT",
            "REGR_SLOPE",
            "STDDEV",
            "STRING_AGG",
            "SUM",
        }
    ),
    "SAFE_DATETIME": frozenset({"AGE", "DATE_TRUNC", "EXTRACT", "TO_CHAR", "TIMESTAMP_TRUNC"}),
    "SAFE_NULL_HANDLING": frozenset({"COALESCE", "NULLIF"}),
    "SAFE_NUMERIC": frozenset(
        {"ABS", "EXP", "GREATEST", "LEAST", "LN", "LOG", "POWER", "ROUND", "SQRT"}
    ),
    "SAFE_STRING": frozenset(
        {
            "ARRAY_TO_STRING",
            "CONCAT_WS",
            "JSONB_EXTRACT_PATH_TEXT",
            "JSONB_EXTRACT_PATH",
            # SQLGlot represents PostgreSQL's read-only jsonb #>> operator
            # as JSONB_EXTRACT_SCALAR.  It extracts text and has no side
            # effects or external access.
            "JSONB_EXTRACT_SCALAR",
            "JSON_EXTRACT",
            "JSON_EXTRACT_SCALAR",
            "ROW_TO_JSON",
            "STRING_TO_ARRAY",
            "TRIM",
        }
    ),
    "SAFE_ARRAY": frozenset({"ARRAY", "ARRAY_REMOVE", "CARDINALITY", "UNNEST"}),
    "SAFE_JSON": frozenset({"JSON_BUILD_OBJECT", "JSONB_BUILD_OBJECT"}),
    "SAFE_WINDOW": frozenset(
        {
            "DENSE_RANK",
            "FIRST_VALUE",
            "LAG",
            "LAST_VALUE",
            "LEAD",
            "NTH_VALUE",
            "NTILE",
            "PERCENT_RANK",
            "RANK",
            "ROW_NUMBER",
        }
    ),
    "SAFE_SUBQUERY": frozenset({"EXISTS"}),
    "SAFE_CAST": frozenset({"CAST"}),
    # These expressions are read-only; their execution context is recorded
    # as provenance rather than treated as a safety violation.
    "SAFE_STABLE_TEMPORAL": frozenset(
        {
            "CURRENT_DATE",
            "CURRENT_TIME",
            "CURRENT_TIMESTAMP",
            "LOCALTIME",
            "LOCALTIMESTAMP",
            "NOW",
            "STATEMENT_TIMESTAMP",
            "TRANSACTION_TIMESTAMP",
        }
    ),
}

SAFE_FUNCTIONS = set().union(*SAFE_FUNCTION_FAMILIES.values())

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
        return self._validate_columns(expression)

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
