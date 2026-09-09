"""Typed benchmark-state planner statistics preparation.

This module is intentionally outside ``app/``.  It is an administrator-side
environment preparation primitive; request planning and execution never call
it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from psycopg import sql


class PlannerStatisticsLifecycle(StrEnum):
    NO_ANALYZE = "NO_ANALYZE"
    ANALYZE_AFTER_SEED = "ANALYZE_AFTER_SEED"
    ANALYZE_CURRENT_STATE = "ANALYZE_CURRENT_STATE"


def analyze_schema(connection: Any, schema: str) -> list[str]:
    """Analyze every ordinary table in one benchmark schema, sorted by name."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT tablename FROM pg_catalog.pg_tables
               WHERE schemaname = %s ORDER BY tablename""",
            (schema,),
        )
        tables = [str(row[0]) for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(
                sql.SQL("ANALYZE {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
            )
    return tables


def lifecycle_sequence(lifecycle: PlannerStatisticsLifecycle, has_fixture: bool) -> list[str]:
    """Return the auditable state-preparation ordering for a lifecycle."""
    return [
        "reset",
        "seed",
        "ANALYZE_AFTER_SEED"
        if lifecycle == PlannerStatisticsLifecycle.ANALYZE_AFTER_SEED
        else "no_analyze_after_seed",
        "apply_fixture" if has_fixture else "no_fixture",
        "ANALYZE_CURRENT_STATE"
        if lifecycle == PlannerStatisticsLifecycle.ANALYZE_CURRENT_STATE
        else "no_current_state_analyze",
        "commit_state_preparation",
        "reader/runtime_planning",
    ]
