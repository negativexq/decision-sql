from typing import cast

from sqlglot import exp

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

SAFE_FUNCTIONS = {
    "AVG",
    "COALESCE",
    "COUNT",
    "DATE_TRUNC",
    "EXTRACT",
    "MAX",
    "MIN",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "LAG",
    "LEAD",
    "FIRST_VALUE",
    "LAST_VALUE",
    "NTH_VALUE",
    "NTILE",
    "SUM",
    "CAST",
    "TIMESTAMP_TRUNC",
}


class SQLPolicy:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog

    def validate(self, parsed: ParsedSQL) -> PolicyRejection | None:
        expression = parsed.expression
        if not isinstance(expression, exp.Select):
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

        cte_names = {
            cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name
        }
        tables = list(expression.find_all(exp.Table))
        source_tables: list[TableMetadata] = []
        aliases: dict[str, TableMetadata] = {}
        for table in tables:
            table_name = table.name.lower()
            schema_name = table.db.lower() if table.db else ""
            catalog_name = table.catalog.lower() if table.catalog else ""
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
            if metadata not in source_tables:
                source_tables.append(metadata)
            aliases[table_name] = metadata
            aliases[table.alias_or_name.lower()] = metadata

        function_rejection = self._validate_functions(expression)
        if function_rejection:
            return function_rejection
        return self._validate_columns(expression, source_tables, aliases, cte_names)

    def _validate_functions(self, expression: exp.Expression) -> PolicyRejection | None:
        for function in expression.find_all(exp.Func):
            function_name = self.function_name(function)
            if function_name not in SAFE_FUNCTIONS:
                return self._reject(
                    PolicyCode.FORBIDDEN_FUNCTION,
                    f"Function is not in the analytical allowlist: {function_name}",
                    function_name,
                )
        return None

    def _validate_columns(
        self,
        expression: exp.Expression,
        source_tables: list[TableMetadata],
        aliases: dict[str, TableMetadata],
        cte_names: set[str],
    ) -> PolicyRejection | None:
        output_aliases = {
            alias.alias_or_name.lower()
            for alias in expression.find_all(exp.Alias)
            if alias.alias_or_name
        }
        for column in expression.find_all(exp.Column):
            column_name = column.name.lower()
            qualifier = column.table.lower()
            if qualifier in cte_names or (not qualifier and column_name in output_aliases):
                continue
            if qualifier:
                candidates = [aliases[qualifier]] if qualifier in aliases else []
            else:
                candidates = [table for table in source_tables if table.get_column(column_name)]
            if not candidates:
                return self._reject(
                    PolicyCode.UNKNOWN_COLUMN,
                    f"Column cannot be resolved in the queryable catalog: {column.sql()}",
                    column.sql(),
                )
            for table in candidates:
                metadata = table.get_column(column_name)
                if metadata is None:
                    return self._reject(
                        PolicyCode.UNKNOWN_COLUMN,
                        f"Column cannot be resolved in table {table.name}: {column_name}",
                        column.sql(),
                    )
                if not metadata.queryable:
                    return self._reject(
                        PolicyCode.FORBIDDEN_COLUMN,
                        f"Column is not queryable: {table.name}.{column_name}",
                        f"{table.name}.{column_name}",
                    )

        for star in expression.find_all(exp.Star):
            if isinstance(star.parent, exp.Count):
                continue
            if isinstance(star.parent, exp.Column):
                qualifier = star.parent.table.lower()
                candidate_tables = [aliases[qualifier]] if qualifier in aliases else []
            else:
                candidate_tables = source_tables
            for table in candidate_tables:
                denied = next((column for column in table.columns if not column.queryable), None)
                if denied:
                    return self._reject(
                        PolicyCode.FORBIDDEN_COLUMN,
                        f"Wildcard would expose a non-queryable column: {table.name}.{denied.name}",
                        f"{table.name}.{denied.name}",
                    )
        return None

    @staticmethod
    def function_name(function: exp.Func) -> str:
        if isinstance(function, exp.Anonymous):
            return function.name.upper()
        if isinstance(function, exp.TimestampTrunc):
            return "DATE_TRUNC"
        return cast(str, function.sql_name()).upper()  # type: ignore[no-untyped-call]

    @staticmethod
    def _reject(code: PolicyCode, message: str, object_name: str | None = None) -> PolicyRejection:
        return PolicyRejection(code=code, message=message, object=object_name)
