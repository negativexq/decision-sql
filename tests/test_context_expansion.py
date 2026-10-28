from __future__ import annotations

import json

import pytest

from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.decision.service import DecisionSqlApplication
from app.generation.decision_contract import (
    PRODUCTION_DECISION_CASE_ID,
    DecisionType,
    ProductionDecision,
)
from app.generation.provider import ProductionDecisionProposal, RuntimeRejectionFeedback
from app.governance.context import governed_context_from_schema_context
from app.observability.run_trace import RunTraceCollector, TraceStageStatus
from app.retrieval.context import SchemaContextResolver


class SequenceProvider:
    def __init__(self, *decisions: ProductionDecision) -> None:
        self.decisions = list(decisions)
        self.contexts: list[str] = []

    async def propose_decision(
        self,
        question: str,
        schema_context: str,
        repair: RuntimeRejectionFeedback | None = None,
    ) -> ProductionDecisionProposal:
        del question, repair
        self.contexts.append(schema_context)
        return ProductionDecisionProposal(
            decision=self.decisions.pop(0), provider="fake", model="fake-model", latency_ms=1.0
        )


class NoSafety:
    def plan(self, candidate: object) -> object:
        raise AssertionError("a non-answer entered the SQL runtime")


def _blocked(*pairs: tuple[str, str]) -> ProductionDecision:
    return ProductionDecision(
        case_id=PRODUCTION_DECISION_CASE_ID,
        decision="BLOCKED_AUTHORITY",
        reason_code="MISSING_AUTHORIZED_RELATIONSHIP",
        explanation="Refund amounts are not in the detailed tables.",
        missing_relationships=[{"from_table": a, "to_table": b} for a, b in pairs],
    )


_CLARIFY = ProductionDecision(
    case_id=PRODUCTION_DECISION_CASE_ID,
    decision="NEEDS_CLARIFICATION",
    reason_code="AMBIGUOUS_SEMANTICS",
    explanation="Refunded could mean partly or fully.",
    clarification_question="Partly or fully refunded?",
    interpretations=["Partly refunded", "Fully refunded"],
)


def _resolver(**values: int) -> SchemaContextResolver:
    return SchemaContextResolver(build_default_catalog(Base.metadata), **values)


def _entities(context: str) -> set[str]:
    return {item["physical_table"] for item in json.loads(context)["schema_catalog"]}


async def _run(provider: SequenceProvider, **settings: object):
    collector = RunTraceCollector("run_expansion")
    collector.record_stage("request", TraceStageStatus.PASS)
    result = await DecisionSqlApplication(
        _resolver(),
        provider,  # type: ignore[arg-type]
        NoSafety(),  # type: ignore[arg-type]
        Settings(_env_file=None, reader_role="reader", **settings),
    ).run("Which product names were refunded?", collector)
    return result, {stage.name: stage for stage in result.trace.stages}


def test_neighbors_join_the_context_and_the_rest_are_named() -> None:
    context = _resolver(top_k=1, neighbor_limit=1).resolve("product names")
    names = {table.name for table in context.tables}

    assert names == {"products", "order_items"}
    assert "refunds" in context.omitted_tables
    assert not set(context.omitted_tables) & names


def test_included_tables_are_selected_even_when_unnamed() -> None:
    context = _resolver(top_k=1).resolve("product names", include=["refunds"])
    assert {"products", "refunds"} <= {table.name for table in context.tables}
    assert "refunds" not in context.omitted_tables


def test_full_context_has_no_other_tables_key() -> None:
    context = _resolver(full_context_max_tables=20).resolve("product names")
    dumped = governed_context_from_schema_context(context).model_dump()
    assert context.omitted_tables == ()
    assert "other_tables" not in dumped


@pytest.mark.asyncio
async def test_a_named_omitted_table_gets_one_expansion_round() -> None:
    provider = SequenceProvider(_blocked(("products", "refunds")), _CLARIFY)
    result, stages = await _run(provider)

    assert len(provider.contexts) == 2
    assert "refunds" not in _entities(provider.contexts[0])
    assert "refunds" in json.loads(provider.contexts[0])["other_tables"]
    assert "refunds" in _entities(provider.contexts[1])
    assert result.model_decision is DecisionType.NEEDS_CLARIFICATION
    assert stages["context_expansion"].status is TraceStageStatus.PASS
    assert stages["context_expansion"].metadata["requested_tables"] == ["refunds"]
    assert result.model_context is not None
    assert "refunds" in {entity.physical_table for entity in result.model_context.schema_catalog}


@pytest.mark.asyncio
async def test_a_second_block_is_final() -> None:
    provider = SequenceProvider(
        _blocked(("products", "refunds")), _blocked(("products", "refunds"))
    )
    result, stages = await _run(provider)

    assert len(provider.contexts) == 2
    assert result.model_decision is DecisionType.BLOCKED_AUTHORITY
    assert stages["context_expansion"].status is TraceStageStatus.PASS


@pytest.mark.asyncio
async def test_tables_outside_the_omitted_list_never_expand() -> None:
    provider = SequenceProvider(_blocked(("products", "invoices")))
    result, stages = await _run(provider)

    assert len(provider.contexts) == 1
    assert result.model_decision is DecisionType.BLOCKED_AUTHORITY
    assert stages["context_expansion"].status is TraceStageStatus.SKIPPED


@pytest.mark.asyncio
async def test_expansion_can_be_turned_off() -> None:
    provider = SequenceProvider(_blocked(("products", "refunds")))
    _, stages = await _run(provider, context_expansion_enabled=False)

    assert len(provider.contexts) == 1
    assert stages["context_expansion"].status is TraceStageStatus.SKIPPED
