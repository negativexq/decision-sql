import pytest

from app.sql.parser import SQLParseFailure, SQLParser


def test_parser_uses_postgres_and_requires_one_statement() -> None:
    parser = SQLParser()

    result = parser.parse("SELECT DATE_TRUNC('month', ordered_at) FROM orders")

    assert result.expression.__class__.__name__ == "Select"

    with pytest.raises(SQLParseFailure, match="empty"):
        parser.parse("   ")
    with pytest.raises(SQLParseFailure, match="Multiple"):
        parser.parse("SELECT 1; SELECT 2")
    with pytest.raises(SQLParseFailure):
        parser.parse("SELECT FROM")
