from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


@dataclass(frozen=True)
class ParsedSQL:
    sql: str
    expression: exp.Expression


class SQLParseFailure(ValueError):
    pass


class SQLParser:
    dialect = "postgres"

    def parse(self, sql: str) -> ParsedSQL:
        if not sql.strip():
            raise SQLParseFailure("SQL is empty")
        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except ParseError as error:
            raise SQLParseFailure("SQL could not be parsed as PostgreSQL") from error
        if not statements:
            raise SQLParseFailure("SQL is empty")
        if len(statements) != 1:
            raise SQLParseFailure("Multiple SQL statements are not allowed")
        return ParsedSQL(sql=sql, expression=cast(exp.Expression, statements[0]))
