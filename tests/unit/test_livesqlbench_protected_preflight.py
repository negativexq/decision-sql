import json
from pathlib import Path

import pytest

from evaluation.external.livesqlbench.loader import load_dataset
from evaluation.external.livesqlbench.protected import (
    ProtectedArtifact,
    ProtectedRow,
    load_protected_artifact,
    merge_public_and_protected,
)


def _public_row(instance_id: str) -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "selected_database": "demo",
        "query": "List values",
        "preprocess_sql": [],
        "clean_up_sqls": [],
        "sol_sql": [],
        "external_knowledge": [],
        "test_cases": [],
        "category": "Query",
        "high_level": False,
        "conditions": {"order": False},
        "difficulty_tier": "Simple",
    }


def _write_public(tmp_path: Path, ids: tuple[str, ...]) -> Path:
    path = tmp_path / "livesqlbench_data.jsonl"
    path.write_text("\n".join(json.dumps(_public_row(i)) for i in ids) + "\n", encoding="utf-8")
    return path


def _write_protected(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "protected.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _protected_row(instance_id: str) -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "sol_sql": ["SELECT 'GOLD_SENTINEL'"],
        "external_knowledge": [7],
        "test_cases": ["TEST_SENTINEL"],
    }


def test_protected_merge_is_exact_instance_id_join_and_runtime_isolated(tmp_path: Path) -> None:
    public = load_dataset(_write_public(tmp_path, ("q1", "q2")))
    protected = load_protected_artifact(
        _write_protected(tmp_path, [_protected_row("q1"), _protected_row("q2")])
    )

    merged, report = merge_public_and_protected(public, protected)
    assert report["merge_key"] == "instance_id"
    assert report["exact_matches"] == 2
    assert [case.instance_id for case in merged] == ["q1", "q2"]
    payload = merged[0].runtime.provider_payload("SCHEMA_SENTINEL")
    assert payload == {
        "question": "List values",
        "external_knowledge": [7],
        "schema_context": "SCHEMA_SENTINEL",
    }
    assert "GOLD_SENTINEL" not in json.dumps(payload)
    assert "TEST_SENTINEL" not in json.dumps(payload)


def test_protected_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate protected instance_id"):
        load_protected_artifact(
            _write_protected(tmp_path, [_protected_row("q1"), _protected_row("q1")])
        )


def test_protected_loader_rejects_missing_evaluation_field(tmp_path: Path) -> None:
    row = _protected_row("q1")
    del row["test_cases"]
    with pytest.raises(ValueError, match="missing fields"):
        load_protected_artifact(_write_protected(tmp_path, [row]))


def test_protected_merge_reports_unmatched_ids_without_fuzzy_matching(tmp_path: Path) -> None:
    public = load_dataset(_write_public(tmp_path, ("q1", "q2")))
    protected = ProtectedArtifact(
        path=tmp_path / "protected.jsonl",
        sha256="synthetic",
        rows=(
            ProtectedRow("q1", ("SELECT 1",), (), ()),
            ProtectedRow("q3", ("SELECT 1",), (), ()),
        ),
        field_names=("external_knowledge", "instance_id", "sol_sql", "test_cases"),
    )
    _, report = merge_public_and_protected(public, protected)
    assert report["exact_matches"] == 1
    assert report["unmatched_public"] == 1
    assert report["unmatched_protected"] == 1
