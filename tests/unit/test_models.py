import pytest
from pydantic import ValidationError

from app.models.domain import QueryRequest
from app.sql.models import (
    ExplainEstimate,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlSafetyStatus,
)


def test_query_request_is_bounded() -> None:
    assert QueryRequest(question="revenue by region").question == "revenue by region"
    with pytest.raises(ValidationError):
        QueryRequest(question="")
    with pytest.raises(ValidationError):
        QueryRequest(question="x" * 2001)


def test_sql_trust_lifecycle_models_keep_proposal_plan_and_outcome_distinct() -> None:
    candidate = SqlCandidate(sql="SELECT id FROM products", correlation_id="request-1")
    plan = QueryPlan(
        plan_id="11111111-1111-4111-8111-111111111111",
        correlation_id=candidate.correlation_id,
        normalized_sql=candidate.sql,
        statement_type="Select",
        referenced_tables=("products",),
        referenced_columns=("id",),
        estimate=ExplainEstimate(total_cost=1.0, plan_rows=1, top_level_node_type="Limit"),
    )
    execution = QueryExecution(
        plan_id=plan.plan_id,
        correlation_id=plan.correlation_id,
        columns=["id"],
        rows=[{"id": 1}],
        row_count=1,
        latency_ms=2.5,
    )

    assert candidate.sql == plan.normalized_sql
    assert plan.policy_decision is SqlSafetyStatus.ALLOWED
    assert execution.plan_id == plan.plan_id
    assert not hasattr(candidate, "executable")
