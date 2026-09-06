from typing import Any

from sqlalchemy import Connection

from app.sql.models import ExplainEstimate


class QueryCostGate:
    def explain(self, connection: Connection, sql: str) -> ExplainEstimate:
        """Run EXPLAIN only for SQL already accepted by the AST policy."""
        # psycopg's DB-API paramstyle treats percent signs as placeholder
        # syntax even when SQLAlchemy receives no bind parameters.  The
        # doubled form is the driver's documented literal-percent spelling;
        # it is applied only to the EXPLAIN transport copy.  PostgreSQL still
        # receives the original candidate SQL semantics, after the unchanged
        # parser/policy boundary above this cost gate.
        explain_sql = sql.replace("%", "%%")
        result = connection.exec_driver_sql(f"EXPLAIN (FORMAT JSON) {explain_sql}")
        document: Any = result.scalar_one()
        plan = document[0]["Plan"]
        return ExplainEstimate(
            total_cost=float(plan["Total Cost"]),
            plan_rows=int(plan["Plan Rows"]),
            top_level_node_type=str(plan["Node Type"]),
        )

    @staticmethod
    def exceeds(estimate: ExplainEstimate, max_plan_rows: int, max_plan_cost: float) -> bool:
        return estimate.plan_rows > max_plan_rows or estimate.total_cost > max_plan_cost
