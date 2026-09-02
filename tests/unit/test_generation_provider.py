import pytest

from app.generation.provider import UnconfiguredLLMProvider
from app.models.domain import QueryRequest, UserContext


@pytest.mark.asyncio
async def test_m0_provider_is_explicitly_unconfigured() -> None:
    provider = UnconfiguredLLMProvider()

    with pytest.raises(NotImplementedError, match="not enabled in M0"):
        await provider.propose_sql(
            QueryRequest(question="show revenue"),
            UserContext(user_id="user-1", tenant_id="tenant-1"),
            "",
        )
