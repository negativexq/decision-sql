from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.external.livesqlbench.semantic_context import (
    KnowledgeEntry,
    SemanticResourceStore,
    load_semantic_resources,
    render_semantic_context,
)
from evaluation.m25_1_livesqlbench_semantic_context import sha256_json


def _write_resources(root: Path, duplicate_name: bool = False) -> None:
    db = root / "demo"
    db.mkdir()
    (db / "demo_schema.txt").write_text('CREATE TABLE "items" (id integer);\n')
    (db / "demo_column_meaning_base.json").write_text(json.dumps({"demo|items|id": "identifier"}))
    rows = [
        {
            "id": 0,
            "knowledge": "Metric",
            "description": "first",
            "definition": "one",
        }
    ]
    if duplicate_name:
        rows.append(
            {
                "id": 1,
                "knowledge": "Metric",
                "description": "last",
                "definition": "two",
            }
        )
    (db / "demo_kb.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_resource_loader_and_exact_id_resolution(tmp_path: Path) -> None:
    _write_resources(tmp_path)
    store = load_semantic_resources(tmp_path, "demo")
    assert [entry.id for entry in store.resolve_reference_ids([0])] == [0]
    assert store.duplicate_knowledge_names == 0


def test_duplicate_name_matches_official_name_indexing(tmp_path: Path) -> None:
    _write_resources(tmp_path, duplicate_name=True)
    store = load_semantic_resources(tmp_path, "demo")
    assert store.duplicate_knowledge_names == 1
    rendered = store.knowledge_json()
    assert '"id": 1' in rendered
    assert '"id": 0' not in rendered


def test_ambiguous_reference_fails_closed() -> None:
    store = SemanticResourceStore(
        database="demo",
        schema_text="schema",
        column_meanings=(),
        knowledge=(
            KnowledgeEntry(0, "a", "d", "x"),
            KnowledgeEntry(0, "b", "d", "y"),
        ),
        source_hashes={},
    )
    with pytest.raises(ValueError, match="resolved to 2 entries"):
        store.resolve_reference_ids([0])


def test_semantic_renderer_is_deterministic_and_separate() -> None:
    store = SemanticResourceStore(
        database="demo",
        schema_text="official schema is not rendered here",
        column_meanings=(("demo|items|id", "identifier"),),
        knowledge=(KnowledgeEntry(0, "Metric", "description", "definition"),),
        source_hashes={},
    )
    first = render_semantic_context("LIVE STRUCTURAL SCHEMA", store)
    second = render_semantic_context("LIVE STRUCTURAL SCHEMA", store)
    assert first == second
    assert "LIVE STRUCTURAL SCHEMA" in first
    assert "BENCHMARK-SUPPLIED COLUMN MEANINGS" in first
    assert "BENCHMARK-SUPPLIED EXTERNAL KNOWLEDGE" in first
    assert "sol_sql" not in first
    assert sha256_json(first) == sha256_json(second)
