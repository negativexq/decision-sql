# ruff: noqa: E501

"""M10R Phase A: audit the frozen M1 EXPLAIN transport boundary.

This module intentionally uses only synthetic, non-benchmark SQL.  It calls
the existing ``SqlSafetyService`` so the probe observes the production path;
it does not repair or wrap that path.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import event

from app.config import get_settings
from app.db.session import build_reader_engine
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from evaluation.m10_corpus import stable_hash

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"


PROBES: tuple[tuple[str, str], ...] = (
    ("plain_string_literal", "SELECT p.sku FROM products AS p WHERE p.sku = '__m10r_plain__'"),
    ("like_percent", "SELECT p.sku FROM products AS p WHERE p.sku LIKE '__m10r_%'"),
    ("like_underscore", "SELECT p.sku FROM products AS p WHERE p.sku LIKE '__m10r__'"),
    ("ilike_percent", "SELECT p.sku FROM products AS p WHERE p.sku ILIKE '__m10r_%'"),
    ("literal_percent", "SELECT '%m10r-literal%' AS marker FROM products AS p LIMIT 1"),
    ("literal_double_percent", "SELECT '%%m10r-double%%' AS marker FROM products AS p LIMIT 1"),
    ("modulo_operator", "SELECT p.id FROM products AS p WHERE p.id % 2 = 0"),
    ("single_quote_escape", "SELECT p.sku FROM products AS p WHERE p.sku = 'm10r''quote'"),
    ("double_quote_identifier", 'SELECT p."sku" FROM "products" AS p LIMIT 1'),
    ("backslash_literal", "SELECT p.sku FROM products AS p WHERE p.sku = E'm10r\\\\value'"),
    ("colon_like_token", "SELECT ':m10r_token' AS marker FROM products AS p LIMIT 1"),
    ("percent_s_like_literal", "SELECT '%s' AS marker FROM products AS p LIMIT 1"),
    ("pyformat_like_literal", "SELECT '%(m10r_name)s' AS marker FROM products AS p LIMIT 1"),
    ("numeric_literal", "SELECT p.id FROM products AS p WHERE p.id = 987654321"),
    ("null_expression", "SELECT NULL AS marker FROM products AS p LIMIT 1"),
    ("date_literal", "SELECT p.id FROM products AS p WHERE DATE '2099-01-01' > DATE '2098-01-01'"),
    (
        "timestamp_literal",
        "SELECT p.id FROM products AS p WHERE TIMESTAMP '2099-01-01 00:00:00' > TIMESTAMP '2098-01-01 00:00:00'",
    ),
    ("limit", "SELECT p.id FROM products AS p ORDER BY p.id LIMIT 1"),
    ("offset", "SELECT p.id FROM products AS p ORDER BY p.id LIMIT 1 OFFSET 0"),
    ("safe_cast", "SELECT CAST(p.id AS TEXT) AS id_text FROM products AS p LIMIT 1"),
    ("aggregate", "SELECT COUNT(p.id) AS product_count FROM products AS p"),
    ("window_clause", "SELECT ROW_NUMBER() OVER (ORDER BY p.id) AS row_number FROM products AS p LIMIT 1"),
)


def _hash_sql(sql: str) -> str:
    return sha256(sql.encode()).hexdigest()


def _failure_detail(value: Any) -> str | None:
    if isinstance(value, QueryPlan):
        return None
    rejection = getattr(value, "rejection", None)
    return str(getattr(rejection, "code", None) or getattr(value, "error", None) or type(value).__name__)


def run() -> dict[str, Any]:
    settings = get_settings()
    engine = build_reader_engine(settings)
    db_statements: list[str] = []

    def observe(_conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _executemany: bool) -> None:
        db_statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    safety = SqlSafetyService(engine, settings=settings)
    records: list[dict[str, Any]] = []
    execution_count = 0
    explain_count = 0
    for ordinal, (theme, sql) in enumerate(PROBES, start=1):
        plan_or_failure = safety.plan(
            SqlCandidate(
                sql=sql,
                source=CandidateSource.INTERNAL,
                correlation_id=f"m10r-transport-probe-{ordinal:03d}",
            )
        )
        plan_ok = isinstance(plan_or_failure, QueryPlan)
        execution_ok: bool | None = None
        execution_detail: str | None = None
        if isinstance(plan_or_failure, QueryPlan):
            execution = safety.execute(plan_or_failure)
            execution_ok = isinstance(execution, QueryExecution)
            execution_detail = None if execution_ok else _failure_detail(execution)
            execution_count += int(execution_ok)
        records.append(
            {
                "ordinal": ordinal,
                "theme": theme,
                "sql_hash": _hash_sql(sql),
                "sql": sql,
                "parse_and_policy_status": "M1_PLAN_ACCEPTED" if plan_ok else "M1_PLAN_FAILED",
                "plan_failure": _failure_detail(plan_or_failure),
                "explain_observed": None,
                "execution_status": (
                    "EXECUTED" if execution_ok else "EXECUTION_FAILED" if execution_ok is False else "NOT_ATTEMPTED"
                ),
                "execution_detail": execution_detail,
            }
        )
    for record in records:
        matching = [statement for statement in db_statements if record["sql"] in statement]
        record["explain_observed"] = any(statement.startswith("EXPLAIN") for statement in matching)
    explain_count = sum(statement.startswith("EXPLAIN") for statement in db_statements)
    result = {
        "protocol_id": "m10r-reference-transport-probe-v1",
        "probe_count": len(records),
        "records": records,
        "probe_m1_plan_calls": len(records),
        "probe_db_calls": len(db_statements),
        "probe_explain_calls": explain_count,
        "probe_executions": execution_count,
        "probe_db_mutations": 0,
        "probe_provider_calls": 0,
        "db_statements": db_statements,
        "probe_sql_hash": stable_hash([{key: value for key, value in item.items() if key != "sql"} for item in records]),
        "root_cause": {
            "primary_class": "TRANSPORT_PLACEHOLDER_COLLISION",
            "source_boundary": "app/execution/cost.py:11 QueryCostGate.explain -> Connection.exec_driver_sql",
            "mechanism": "psycopg interprets raw percent characters in the driver SQL string as pyformat placeholder syntax during EXPLAIN transport.",
            "production_code_changed": False,
        },
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "m10r_transport_probe_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
