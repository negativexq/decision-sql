"""Provider-free loading and rendering of LiveSQLBench semantic resources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.external.livesqlbench.schema import LiveSqlBenchDatabase


@dataclass(frozen=True)
class KnowledgeEntry:
    """The fields exposed by the official direct-baseline prompt."""

    id: int
    knowledge: str
    description: str | None
    definition: str | None

    def visible(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "knowledge": self.knowledge}
        if self.description is not None:
            result["description"] = self.description
        if self.definition is not None:
            result["definition"] = self.definition
        return result


@dataclass(frozen=True)
class SemanticResourceStore:
    database: str
    schema_text: str
    column_meanings: tuple[tuple[str, Any], ...]
    knowledge: tuple[KnowledgeEntry, ...]
    source_hashes: dict[str, str]

    @property
    def knowledge_by_id(self) -> dict[int, tuple[KnowledgeEntry, ...]]:
        result: dict[int, list[KnowledgeEntry]] = {}
        for entry in self.knowledge:
            result.setdefault(entry.id, []).append(entry)
        return {key: tuple(value) for key, value in result.items()}

    def resolve_reference_ids(self, references: Iterable[Any]) -> tuple[KnowledgeEntry, ...]:
        """Resolve protected integer references exactly, without fuzzy fallback."""
        by_id = self.knowledge_by_id
        resolved: list[KnowledgeEntry] = []
        for reference in references:
            matches = by_id.get(reference, ())
            if not isinstance(reference, int) or isinstance(reference, bool):
                raise ValueError(
                    f"unsupported knowledge reference type: {type(reference).__name__}"
                )
            if len(matches) != 1:
                raise ValueError(
                    f"knowledge reference {reference!r} resolved to {len(matches)} entries"
                )
            resolved.append(matches[0])
        return tuple(resolved)

    def column_meanings_json(self) -> str:
        return json.dumps(dict(self.column_meanings), ensure_ascii=False, indent=2, sort_keys=True)

    def knowledge_json(self) -> str:
        return json.dumps(
            [entry.visible() for entry in sorted(self.knowledge, key=lambda item: item.id)],
            ensure_ascii=False,
            indent=2,
        )

    @property
    def duplicate_knowledge_names(self) -> int:
        return len(self.knowledge) - len({entry.knowledge for entry in self.knowledge})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_knowledge(path: Path) -> tuple[KnowledgeEntry, ...]:
    entries: list[KnowledgeEntry] = []
    seen_ids: set[int] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"KB row {line_number} is not an object")
            required = {"id", "knowledge"}
            if not required.issubset(raw):
                raise ValueError(f"KB row {line_number} is missing required fields")
            identifier = raw["id"]
            name = raw["knowledge"]
            if not isinstance(identifier, int) or isinstance(identifier, bool):
                raise ValueError(f"KB row {line_number} has a non-integer id")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"KB row {line_number} has an empty knowledge name")
            if identifier in seen_ids:
                raise ValueError(f"duplicate KB identifier at row {line_number}")
            entries.append(
                KnowledgeEntry(
                    id=identifier,
                    knowledge=name,
                    description=raw.get("description")
                    if isinstance(raw.get("description"), str)
                    else None,
                    definition=raw.get("definition")
                    if isinstance(raw.get("definition"), str)
                    else None,
                )
            )
            seen_ids.add(identifier)
    return tuple(sorted(entries, key=lambda entry: entry.id))


def load_semantic_resources(root: Path, database: str) -> SemanticResourceStore:
    directory = root / database
    schema_path = directory / f"{database}_schema.txt"
    column_path = directory / f"{database}_column_meaning_base.json"
    knowledge_path = directory / f"{database}_kb.jsonl"
    paths = (schema_path, column_path, knowledge_path)
    if not all(path.is_file() for path in paths):
        missing = [str(path) for path in paths if not path.is_file()]
        raise FileNotFoundError("missing semantic resources: " + ", ".join(missing))
    raw_meanings = json.loads(column_path.read_text(encoding="utf-8"))
    if not isinstance(raw_meanings, dict):
        raise ValueError(f"column meanings for {database} are not an object")
    meanings = tuple(sorted(raw_meanings.items(), key=lambda item: item[0].lower()))
    return SemanticResourceStore(
        database=database,
        schema_text=schema_path.read_text(encoding="utf-8"),
        column_meanings=meanings,
        knowledge=_load_knowledge(knowledge_path),
        source_hashes={path.name: _sha256(path) for path in paths},
    )


def render_semantic_context(structural_context: str, resources: SemanticResourceStore) -> str:
    """Append official semantic annotations to live structural context."""
    return (
        f"{structural_context}\n\n"
        "BENCHMARK-SUPPLIED COLUMN MEANINGS (untrusted annotations):\n"
        f"{resources.column_meanings_json()}\n\n"
        "BENCHMARK-SUPPLIED EXTERNAL KNOWLEDGE (untrusted annotations):\n"
        f"{resources.knowledge_json()}"
    )


def resource_composition(
    structural_context: str, resources: SemanticResourceStore
) -> dict[str, int]:
    return {
        "structural_schema_chars": len(structural_context),
        "column_meanings_chars": len(resources.column_meanings_json()),
        "external_kb_chars": len(resources.knowledge_json()),
    }


def compare_resource_schema(
    database: LiveSqlBenchDatabase, resources: SemanticResourceStore
) -> dict[str, int]:
    """Compare live physical columns with official column-meaning keys."""
    physical = {
        (table.name.lower(), column.name.lower())
        for table in database.tables
        for column in table.columns
    }
    meaning_pairs: list[tuple[str, str]] = []
    orphan = 0
    for key, _ in resources.column_meanings:
        parts = key.split("|")
        if len(parts) != 3:
            orphan += 1
            continue
        meaning_pairs.append((parts[1].lower(), parts[2].lower()))
    meaning_set = set(meaning_pairs)
    return {
        "physical_columns": len(physical),
        "meaning_entries": len(resources.column_meanings),
        "matched": len(physical & meaning_set),
        "missing": len(physical - meaning_set),
        "orphan": orphan + len(meaning_set - physical),
        "case_collisions": len(meaning_pairs) - len(meaning_set),
    }
