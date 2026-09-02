from dataclasses import dataclass
from typing import Protocol

from app.models.domain import QueryRequest, UserContext


@dataclass(frozen=True)
class SQLProposal:
    """Untrusted model output; deterministic stages must validate it before execution."""

    sql: str
    provider: str
    model: str


class LLMProvider(Protocol):
    async def propose_sql(
        self,
        request: QueryRequest,
        user_context: UserContext,
        schema_context: str,
    ) -> SQLProposal:
        """Return a SQL proposal. This interface has no execution authority."""


class UnconfiguredLLMProvider:
    async def propose_sql(
        self,
        request: QueryRequest,
        user_context: UserContext,
        schema_context: str,
    ) -> SQLProposal:
        raise NotImplementedError("LLM generation is intentionally not enabled in M0")
