from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app.generation.provider import _generation_messages
from evaluation.external.livesqlbench.semantic_context import (
    KnowledgeEntry,
    SemanticResourceStore,
    render_semantic_context,
)
from evaluation.m25_2_livesqlbench_direct_semantic_context import (
    MAX_PROVIDER_CALLS,
    _exact_mcnemar_p,
    _semantic_addition,
)

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_pilot_and_old_artifact_are_intact() -> None:
    final = json.loads(
        (ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json").read_text()
    )
    old = json.loads(
        (ROOT / "evaluation/fixtures/m25_livesqlbench_direct_pilot_result.json").read_text()
    )
    assert len(final["pilot_case_ids"]) == 18
    assert len(set(final["pilot_case_ids"])) == 18
    assert old["official"] == {"correct": 1, "total": 18, "accuracy": 1 / 18}
    assert old["provider_calls"] == 18
    assert old["classification"] == "M25_LIVESQLBENCH_DIRECT_PILOT_COMPLETED"


def test_budget_and_exact_mcnemar_are_deterministic() -> None:
    assert MAX_PROVIDER_CALLS == 18
    assert _exact_mcnemar_p(0, 0) is None
    assert _exact_mcnemar_p(1, 3) == pytest.approx(0.625)


def test_semantic_addition_preserves_structural_prefix() -> None:
    store = SemanticResourceStore(
        database="demo",
        schema_text="ignored",
        column_meanings=(("demo|items|id", "identifier"),),
        knowledge=(KnowledgeEntry(1, "metric", "description", "definition"),),
        source_hashes={},
    )
    structural = "LIVE STRUCTURAL SCHEMA"
    corrected = render_semantic_context(structural, store)
    assert _semantic_addition(structural, corrected).startswith(
        "BENCHMARK-SUPPLIED COLUMN MEANINGS"
    )


def test_corrected_generation_payload_contains_semantics_not_gold() -> None:
    context = (
        "LIVE STRUCTURAL SCHEMA\n\n"
        "BENCHMARK-SUPPLIED COLUMN MEANINGS:\nidentifier\n\n"
        "BENCHMARK-SUPPLIED EXTERNAL KNOWLEDGE:\nmetric definition"
    )
    payload = json.dumps(_generation_messages("List the metric", context))
    assert "metric definition" in payload
    assert "sol_sql" not in payload
    assert "test_cases" not in payload
    assert "SELECT gold" not in payload


def test_prompt_hash_matches_frozen_m25() -> None:
    prompt_hash = hashlib.sha256(
        inspect.getsource(
            __import__(
                "app.generation.provider", fromlist=["_generation_messages"]
            )._generation_messages
        ).encode()
    ).hexdigest()
    old = json.loads(
        (ROOT / "evaluation/fixtures/m25_livesqlbench_direct_pilot_result.json").read_text()
    )
    assert prompt_hash == old["configuration"]["prompt_hash"]


def test_completed_result_accounts_for_exactly_one_call_per_case() -> None:
    result_path = (
        ROOT / "evaluation/fixtures/m25_2_livesqlbench_direct_semantic_context_result.json"
    )
    if not result_path.exists():
        pytest.skip("M25.2 provider output has not been materialized locally")
    result = json.loads(result_path.read_text())
    assert result["classification"] == (
        "M25_2_LIVESQLBENCH_SEMANTIC_CONTEXT_PAIRED_RERUN_COMPLETED"
    )
    assert result["provider_calls"] == 18
    assert result["experiment"]["calls_per_case_max"] == 1
    assert (
        sum(
            result["paired"][key]
            for key in (
                "old_correct_new_correct",
                "old_correct_new_wrong",
                "old_wrong_new_correct",
                "old_wrong_new_wrong",
            )
        )
        == 18
    )
