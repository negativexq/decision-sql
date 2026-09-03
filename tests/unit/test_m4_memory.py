import json
from pathlib import Path

from sqlglot import parse_one

from app.memory.models import MemoryLifecycle
from app.memory.retrieval import RetrieverConfig, RetrieverVariant, VerifiedQueryRetriever
from evaluation.m4_benchmark import (
    benchmark_hash,
    build_benchmark,
    build_memory_corpus,
    corpus_hash,
    validate_benchmark,
)
from evaluation.run_m4 import run_phase_a

ROOT = Path(__file__).resolve().parents[2]


def test_m4_frozen_manifest_and_counts() -> None:
    memory = build_memory_corpus()
    benchmark = build_benchmark()
    manifest = json.loads((ROOT / "evaluation/fixtures/m4_manifest.json").read_text())

    assert len(memory) == manifest["memory_example_count"] == 50
    assert len([item for item in benchmark if item.split == "dev"]) == manifest["dev_count"] == 48
    assert (
        len([item for item in benchmark if item.split == "holdout"])
        == manifest["holdout_count"]
        == 32
    )
    assert corpus_hash(memory) == manifest["corpus_hash"]
    assert benchmark_hash(benchmark) == manifest["benchmark_hash"]
    assert validate_benchmark(memory, benchmark)["passed"] is True


def test_m4_memory_entries_are_unique_parsable_and_active() -> None:
    memory = build_memory_corpus()

    assert len({item.example_id for item in memory}) == len(memory)
    assert all(item.lifecycle is MemoryLifecycle.ACTIVE for item in memory)
    assert all(item.content_hash for item in memory)
    for item in memory:
        parse_one(item.sql, read="postgres")


def test_m4_retrieval_is_finite_deterministic_and_tie_stable() -> None:
    memory = build_memory_corpus()
    config = RetrieverConfig(
        variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA,
        k=3,
        lexical_weight=0.75,
        schema_weight=0.25,
        structural_weight=0.0,
    )
    retriever = VerifiedQueryRetriever(memory)
    first = retriever.retrieve("show order totals", ("orders",), config)
    second = retriever.retrieve("show order totals", ("orders",), config)

    assert first == second
    assert len(first) == 3
    assert [item.example.example_id for item in first] == sorted(
        item.example.example_id for item in first
    ) or len({item.final_score for item in first}) > 1
    assert VerifiedQueryRetriever(()).retrieve("anything", (), config) == ()


def test_m4_phase_a_passes_without_provider_calls() -> None:
    result = run_phase_a(build_memory_corpus(), build_benchmark())

    assert result["provider_calls"] == 0
    assert result["passed"] is True
    assert result["chosen_config"]["variant"] == RetrieverVariant.QUESTION_LEXICAL_SCHEMA
    assert result["chosen_config"]["k"] == 3
    assert result["chosen_config"]["lexical_weight"] == 0.75
    assert result["chosen_config"]["schema_weight"] == 0.25
    assert result["chosen_config"]["structural_weight"] == 0.0
    assert result["chosen_config_hash"] == (
        "2e3f754517abe743fe66688dddc2f45d1a48e19a79e2836083a873cd3155a294"
    )
    assert result["variants"]
