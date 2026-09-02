import pytest
from pydantic import ValidationError

from app.models.domain import FailureStage, QueryRequest, QueryResult, QueryStatus, UserContext


def test_user_context_is_server_owned_and_immutable() -> None:
    context = UserContext(user_id="user-1", tenant_id="tenant-1", roles=("analyst",))

    assert context.tenant_id == "tenant-1"
    with pytest.raises(ValidationError):
        context.tenant_id = "other-tenant"


def test_query_request_is_bounded() -> None:
    assert QueryRequest(question="revenue by region").question == "revenue by region"
    with pytest.raises(ValidationError):
        QueryRequest(question="")
    with pytest.raises(ValidationError):
        QueryRequest(question="x" * 2001)


def test_query_result_has_typed_status_and_failure_stage() -> None:
    result = QueryResult(
        status=QueryStatus.FAILED,
        failure_stage=FailureStage.POLICY_REJECTION,
        failure_detail="not allowed",
    )

    assert result.status is QueryStatus.FAILED
    assert result.failure_stage is FailureStage.POLICY_REJECTION


def test_query_result_collection_defaults_are_independent() -> None:
    first = QueryResult(status=QueryStatus.RECEIVED)
    second = QueryResult(status=QueryStatus.RECEIVED)
    first.columns.append("count")

    assert second.columns == []
