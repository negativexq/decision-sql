"""Deterministic query-aware schema, knowledge, and value grounding.

The component has no SQL-generation or evaluation authority.  It selects
server-owned schema metadata, benchmark/business annotations supplied by the
caller, and bounded scalar value evidence for a single provider context.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Engine, text

from app.catalog.models import (
    ColumnMetadata,
    ContextMetadata,
    RelationshipMetadata,
    RelevanceScore,
    RelevantColumn,
    RelevantRelationship,
    RelevantTable,
    SchemaCatalog,
    SchemaContext,
    TableMetadata,
)

DEFAULT_TABLE_LIMIT = 10
DEFAULT_COLUMN_LIMIT = 24
DEFAULT_MAX_TABLES = 10
DEFAULT_KB_LIMIT = 12
DEFAULT_VALUE_COLUMN_LIMIT = 16
DEFAULT_VALUE_PHRASE_LIMIT = 12
DEFAULT_VALUES_PER_COLUMN = 8
DEFAULT_FK_DEPTH = 3

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "by",
        "for",
        "from",
        "how",
        "in",
        "into",
        "is",
        "list",
        "of",
        "on",
        "show",
        "that",
        "the",
        "to",
        "what",
        "which",
        "with",
    }
)


@dataclass(frozen=True)
class GroundingKnowledge:
    identifier: str
    name: str
    description: str | None = None
    definition: str | None = None

    def searchable_text(self) -> str:
        return " ".join(filter(None, (self.name, self.description, self.definition)))


@dataclass(frozen=True)
class ValueEvidence:
    table: str
    column: str
    value: str
    phrase: str
    match_kind: str


@dataclass(frozen=True)
class RankedItem:
    identifier: str
    score: int
    matched_tokens: tuple[str, ...]


@dataclass(frozen=True)
class GroundingContext:
    selected_tables: tuple[TableMetadata, ...]
    selected_columns: tuple[tuple[str, ColumnMetadata], ...]
    relationships: tuple[RelationshipMetadata, ...]
    column_meanings: tuple[tuple[str, str], ...]
    knowledge: tuple[GroundingKnowledge, ...]
    values: tuple[ValueEvidence, ...]
    table_ranking: tuple[RankedItem, ...]
    column_ranking: tuple[RankedItem, ...]
    knowledge_ranking: tuple[RankedItem, ...]
    fk_paths: tuple[tuple[str, ...], ...]
    value_probe_columns: tuple[str, ...]
    value_phrases: tuple[str, ...]


class GroundingError(RuntimeError):
    """Raised when deterministic grounding cannot safely construct context."""


def to_schema_context(context: GroundingContext) -> SchemaContext:
    """Convert grounding facts to the existing provider schema model."""
    selected_names = {table.name for table in context.selected_tables}
    relevant_tables: list[RelevantTable] = []
    selected_columns: list[RelevantColumn] = []
    for table in context.selected_tables:
        columns: list[RelevantColumn] = []
        for column in sorted(
            (item for item in table.columns if item.queryable), key=lambda item: item.name
        ):
            relation = next(
                (item for item in table.relationships if item.column == column.name), None
            )
            if relation is not None and relation.referenced_table not in selected_names:
                relation = None
            rendered = RelevantColumn(
                table_name=table.name,
                name=column.name,
                type=column.type,
                description=column.description,
                sensitivity=column.sensitivity,
                primary_key=column.primary_key,
                foreign_key_table=relation.referenced_table if relation else None,
                foreign_key_column=relation.referenced_column if relation else None,
            )
            columns.append(rendered)
            selected_columns.append(rendered)
        relevant_tables.append(
            RelevantTable(
                name=table.name,
                description=table.description,
                columns=tuple(columns),
            )
        )
    relationships = tuple(
        RelevantRelationship(
            source_table=table.name,
            source_column=relation.column,
            target_table=relation.referenced_table,
            target_column=relation.referenced_column,
        )
        for table in context.selected_tables
        for relation in table.relationships
        if relation.referenced_table in selected_names
    )
    scores = tuple(
        RelevanceScore(
            table_name=item.identifier,
            score=item.score,
            matched_tokens=item.matched_tokens,
        )
        for item in context.table_ranking
    )
    return SchemaContext(
        tables=tuple(relevant_tables),
        relationships=tuple(
            sorted(
                relationships,
                key=lambda item: (
                    item.source_table,
                    item.source_column,
                    item.target_table,
                    item.target_column,
                ),
            )
        ),
        selected_columns=tuple(selected_columns),
        context_metadata=ContextMetadata(
            mode="query_aware_grounding",
            seed_table_count=len(context.table_ranking[:DEFAULT_TABLE_LIMIT]),
            selected_table_count=len(relevant_tables),
            selected_column_count=len(selected_columns),
            relationship_count=len(relationships),
            relevance_scores=scores,
        ),
    )


def normalize_tokens(value: str) -> tuple[str, ...]:
    """Normalize text while retaining numeric/date tokens and abbreviations."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = normalized.casefold().replace("_", " ")
    tokens = re.findall(r"[a-z0-9]+(?:[./:-][a-z0-9]+)*", normalized)
    return tuple(token for token in tokens if token not in _STOPWORDS)


def meaningful_phrases(value: str, *, max_n: int = 4) -> tuple[str, ...]:
    tokens = normalize_tokens(value)
    phrases: list[str] = []
    for size in range(1, min(max_n, len(tokens)) + 1):
        phrases.extend(
            " ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)
        )
    return tuple(dict.fromkeys(phrases))


def extract_value_phrases(
    value: str, *, limit: int = DEFAULT_VALUE_PHRASE_LIMIT
) -> tuple[str, ...]:
    """Extract bounded literal-like phrases without treating them as SQL."""
    quoted = [item for item in re.findall(r"['\"]([^'\"]+)['\"]", value) if item.strip()]
    explicit = re.findall(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d+(?:\.\d+)?)\b", value)
    candidates: list[str] = quoted + explicit
    tokens = normalize_tokens(value)
    candidates.extend(tokens)
    for size in range(2, 5):
        candidates.extend(
            " ".join(tokens[index : index + size])
            for index in range(max(0, len(tokens) - size + 1))
        )
    return tuple(dict.fromkeys(item.strip() for item in candidates if item.strip()))[:limit]


def _token_set(value: str) -> set[str]:
    return set(normalize_tokens(value))


def _overlap(question: set[str], text_value: str) -> tuple[int, set[str]]:
    matched = question & _token_set(text_value)
    return len(matched), matched


def _quote_identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise GroundingError("invalid SQL identifier")
    return '"' + value.replace('"', '""') + '"'


class QueryAwareGrounder:
    """Build bounded deterministic context from a catalog and semantic store."""

    def __init__(
        self,
        catalog: SchemaCatalog,
        *,
        column_meanings: Mapping[str, str] | None = None,
        knowledge: tuple[GroundingKnowledge, ...] = (),
        table_limit: int = DEFAULT_TABLE_LIMIT,
        column_limit: int = DEFAULT_COLUMN_LIMIT,
        max_tables: int = DEFAULT_MAX_TABLES,
        knowledge_limit: int = DEFAULT_KB_LIMIT,
        fk_depth: int = DEFAULT_FK_DEPTH,
        value_column_limit: int = DEFAULT_VALUE_COLUMN_LIMIT,
        value_phrase_limit: int = DEFAULT_VALUE_PHRASE_LIMIT,
        values_per_column: int = DEFAULT_VALUES_PER_COLUMN,
    ) -> None:
        self.catalog = catalog
        self.column_meanings = dict(column_meanings or {})
        self.knowledge = tuple(knowledge)
        self.table_limit = table_limit
        self.column_limit = column_limit
        self.max_tables = max_tables
        self.knowledge_limit = knowledge_limit
        self.fk_depth = fk_depth
        self.value_column_limit = value_column_limit
        self.value_phrase_limit = value_phrase_limit
        self.values_per_column = values_per_column

    def ground(self, question: str, engine: Engine | None = None) -> GroundingContext:
        if not question.strip():
            raise GroundingError("question is empty")
        table_ranking = self._rank_tables(question)
        column_ranking = self._rank_columns(question)
        table_candidates = table_ranking[: self.table_limit]
        owner_scores: dict[str, int] = {}
        for item in column_ranking[: self.column_limit]:
            owner, _, _ = item.identifier.partition(".")
            owner_scores[owner] = max(owner_scores.get(owner, 0), item.score)
        weakest_table_score = table_candidates[-1].score if table_candidates else 0
        owner_candidates = [
            owner
            for owner, score in sorted(owner_scores.items(), key=lambda item: (-item[1], item[0]))
            if score >= weakest_table_score
        ]
        candidate_names = list(dict.fromkeys(item.identifier for item in table_candidates))
        candidate_names.extend(name for name in owner_candidates if name not in candidate_names)
        table_names: set[str] = set()
        # Accept candidates in deterministic relevance order only when their
        # complete minimal FK closure still fits.  This retains high-ranked
        # table/column evidence without dropping arbitrary bridge tables after
        # closure or allowing a long low-score tail to dominate the context.
        for name in candidate_names:
            candidate_set = table_names | {name}
            seed = tuple(
                table
                for table in self.catalog.tables
                if table.queryable and table.name in candidate_set
            )
            try:
                self._close_tables(seed)
            except GroundingError:
                continue
            table_names.add(name)
            if len(table_names) >= self.max_tables:
                break
        seed_tables = tuple(
            table for table in self.catalog.tables if table.queryable and table.name in table_names
        )
        if not seed_tables:
            raise GroundingError("no queryable table matched the question")
        selected_names, paths = self._close_tables(seed_tables)
        selected_tables = tuple(
            sorted(
                (
                    table
                    for name in selected_names
                    if (table := self.catalog.get_table(name)) is not None
                ),
                key=lambda item: item.name,
            )
        )
        relationships = tuple(
            sorted(
                (
                    relationship
                    for table in selected_tables
                    for relationship in table.relationships
                    if relationship.referenced_table in selected_names
                ),
                key=lambda item: (item.referenced_table, item.column, item.referenced_column),
            )
        )
        selected_columns = tuple(
            (table.name, column)
            for table in selected_tables
            for column in sorted(table.columns, key=lambda item: item.name)
            if column.queryable
        )
        meanings = self._selected_meanings(selected_columns)
        knowledge_ranking = self._rank_knowledge(question)
        selected_knowledge = tuple(
            self.knowledge_by_identifier(item.identifier)
            for item in knowledge_ranking[: self.knowledge_limit]
        )
        value_phrases = extract_value_phrases(question, limit=self.value_phrase_limit)
        value_columns = self._value_columns(question, selected_columns)
        values = self.probe_values(engine, question, selected_columns) if engine is not None else ()
        return GroundingContext(
            selected_tables=selected_tables,
            selected_columns=selected_columns,
            relationships=relationships,
            column_meanings=meanings,
            knowledge=selected_knowledge,
            values=tuple(values),
            table_ranking=tuple(table_ranking),
            column_ranking=tuple(column_ranking),
            knowledge_ranking=tuple(knowledge_ranking),
            fk_paths=paths,
            value_probe_columns=tuple(value_columns),
            value_phrases=value_phrases,
        )

    def _rank_tables(self, question: str) -> list[RankedItem]:
        question_tokens = _token_set(question)
        result: list[RankedItem] = []
        for table in self.catalog.tables:
            if not table.queryable:
                continue
            name_count, name_matches = _overlap(question_tokens, table.name)
            alias_count, alias_matches = _overlap(question_tokens, " ".join(table.aliases))
            desc_count, desc_matches = _overlap(question_tokens, table.description)
            column_text = " ".join(
                f"{column.name} {self._meaning_for(table.name, column.name)}"
                for column in table.columns
            )
            column_count, column_matches = _overlap(question_tokens, column_text)
            score = name_count * 12 + alias_count * 6 + desc_count * 3 + column_count * 2
            if score:
                result.append(
                    RankedItem(
                        table.name,
                        score,
                        tuple(sorted(name_matches | alias_matches | desc_matches | column_matches)),
                    )
                )
        return sorted(result, key=lambda item: (-item.score, item.identifier))

    def _rank_columns(self, question: str) -> list[RankedItem]:
        question_tokens = _token_set(question)
        result: list[RankedItem] = []
        for table in self.catalog.tables:
            if not table.queryable:
                continue
            for column in table.columns:
                if not column.queryable:
                    continue
                identifier = f"{table.name}.{column.name}"
                count, matches = _overlap(
                    question_tokens,
                    f"{identifier} {column.description} "
                    f"{self._meaning_for(table.name, column.name)}",
                )
                if count:
                    result.append(RankedItem(identifier, count, tuple(sorted(matches))))
        return sorted(result, key=lambda item: (-item.score, item.identifier))

    def _rank_knowledge(self, question: str) -> list[RankedItem]:
        question_tokens = _token_set(question)
        ranked: list[RankedItem] = []
        for entry in self.knowledge:
            score, matches = _overlap(question_tokens, entry.searchable_text())
            ranked.append(RankedItem(entry.identifier, score, tuple(sorted(matches))))
        return sorted(ranked, key=lambda item: (-item.score, item.identifier))

    def knowledge_by_identifier(self, identifier: str) -> GroundingKnowledge:
        matches = [entry for entry in self.knowledge if entry.identifier == identifier]
        if len(matches) != 1:
            raise GroundingError(f"knowledge identifier is missing or ambiguous: {identifier}")
        return matches[0]

    def _meaning_for(self, table: str, column: str) -> str:
        target = f"{table}.{column}".casefold()
        for key, value in self.column_meanings.items():
            parts = key.split("|")
            if len(parts) == 3 and f"{parts[1]}.{parts[2]}".casefold() == target:
                return str(value)
        return ""

    def _selected_meanings(
        self, columns: tuple[tuple[str, ColumnMetadata], ...]
    ) -> tuple[tuple[str, str], ...]:
        selected = {f"{table}.{column.name}".casefold() for table, column in columns}
        return tuple(
            sorted(
                (
                    (key, str(value))
                    for key, value in self.column_meanings.items()
                    if self._meaning_key_matches(key, selected)
                ),
                key=lambda item: item[0].casefold(),
            )
        )

    @staticmethod
    def _meaning_key_matches(key: str, selected: set[str]) -> bool:
        parts = key.split("|")
        return len(parts) == 3 and f"{parts[1]}.{parts[2]}".casefold() in selected

    def _close_tables(
        self, seed_tables: tuple[TableMetadata, ...]
    ) -> tuple[set[str], tuple[tuple[str, ...], ...]]:
        seed_names = {table.name for table in seed_tables}
        if len(seed_names) > self.max_tables:
            raise GroundingError("grounding seed exceeds table bound")
        adjacency: dict[str, set[str]] = {table.name: set() for table in self.catalog.tables}
        for table in self.catalog.tables:
            if not table.queryable:
                continue
            for relationship in table.relationships:
                target_table = self.catalog.get_table(relationship.referenced_table)
                if target_table is None or not target_table.queryable:
                    continue
                adjacency[table.name].add(target_table.name)
                adjacency[target_table.name].add(table.name)
        paths: list[tuple[str, ...]] = []
        starts = sorted(seed_names)
        selected = {starts[0]} if starts else set()
        remaining = set(starts[1:])
        while remaining:
            candidates: list[tuple[int, tuple[str, ...], str]] = []
            for target in sorted(remaining):
                for start in sorted(selected):
                    path = self._shortest_path(adjacency, start, target)
                    if path:
                        candidates.append((len(path), path, target))
            if not candidates:
                break
            _, path, target = min(candidates, key=lambda item: (item[0], item[1], item[2]))
            paths.append(path)
            selected.update(path)
            remaining.remove(target)
        if len(selected) > self.max_tables:
            raise GroundingError("FK closure exceeds final table bound")
        return selected, tuple(sorted(set(paths)))

    def _shortest_path(
        self, adjacency: Mapping[str, set[str]], start: str, target: str
    ) -> tuple[str, ...]:
        queue: deque[tuple[str, tuple[str, ...]]] = deque([(start, (start,))])
        visited = {start}
        while queue:
            current, path = queue.popleft()
            if current == target:
                return path
            if len(path) - 1 >= self.fk_depth:
                continue
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, (*path, neighbor)))
        return ()

    def _value_columns(
        self, question: str, columns: tuple[tuple[str, ColumnMetadata], ...]
    ) -> list[str]:
        question_tokens = _token_set(question)
        candidates: list[tuple[int, str]] = []
        for table, column in columns:
            data_type = column.type.casefold()
            if not any(
                marker in data_type
                for marker in (
                    "char",
                    "text",
                    "enum",
                    "json",
                    "date",
                    "time",
                    "numeric",
                    "decimal",
                    "integer",
                )
            ):
                continue
            count, _ = _overlap(
                question_tokens,
                f"{table} {column.name} {column.description} "
                f"{self._meaning_for(table, column.name)}",
            )
            candidates.append((count, f"{table}.{column.name}"))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [identifier for _, identifier in candidates[: self.value_column_limit]]

    def probe_values(
        self,
        engine: Engine | None,
        question: str,
        columns: tuple[tuple[str, ColumnMetadata], ...],
    ) -> tuple[ValueEvidence, ...]:
        if engine is None:
            return ()
        phrases = extract_value_phrases(question, limit=self.value_phrase_limit)
        identifiers = self._value_columns(question, columns)
        by_identifier = {f"{table}.{column.name}": column for table, column in columns}
        evidence: dict[tuple[str, str], ValueEvidence] = {}
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = 3000"))
            for identifier in identifiers:
                table_name, column_name = identifier.split(".", 1)
                if identifier not in by_identifier:
                    raise GroundingError("value probe identifier is outside grounded catalog")
                table_sql = _quote_identifier(table_name)
                column_sql = _quote_identifier(column_name)
                query = text(
                    f"SELECT DISTINCT CAST({column_sql} AS TEXT) AS value "
                    f"FROM {table_sql} "
                    "WHERE CAST(" + column_sql + " AS TEXT) = :exact "
                    "OR CAST(" + column_sql + " AS TEXT) ILIKE :pattern "
                    "LIMIT " + str(self.values_per_column)
                )
                for phrase in phrases:
                    rows = connection.execute(
                        query, {"exact": phrase, "pattern": f"%{phrase}%"}
                    ).fetchall()
                    for row in rows:
                        raw_value = row[0]
                        if raw_value is None:
                            continue
                        value = str(raw_value)
                        lowered_value = value.casefold()
                        lowered_phrase = phrase.casefold()
                        kind = (
                            "EXACT"
                            if value == phrase
                            else "CASE_INSENSITIVE_EXACT"
                            if lowered_value == lowered_phrase
                            else "PREFIX"
                            if lowered_value.startswith(lowered_phrase)
                            else "SUBSTRING"
                        )
                        evidence.setdefault(
                            (identifier, lowered_value),
                            ValueEvidence(table_name, column_name, value, phrase, kind),
                        )
        return tuple(
            sorted(
                evidence.values(),
                key=lambda item: (item.table, item.column, item.match_kind, item.value.casefold()),
            )
        )


def render_grounding_context(context: GroundingContext) -> str:
    """Render only grounded structural facts and bounded semantic evidence."""
    lines = ["QUERY-AWARE STRUCTURAL SCHEMA", ""]
    for table in context.selected_tables:
        lines.extend((f"[Table] {table.name}", f"Description: {table.description}", "[Columns]"))
        for column in sorted(table.columns, key=lambda item: item.name):
            if not column.queryable:
                continue
            annotations = []
            if column.primary_key:
                annotations.append("PK")
            for relationship in table.relationships:
                if relationship.column == column.name:
                    annotations.append(
                        f"FK -> {relationship.referenced_table}.{relationship.referenced_column}"
                    )
            suffix = f" [{', '.join(annotations)}]" if annotations else ""
            lines.append(
                f"- {table.name}.{column.name} {column.type}{suffix} — {column.description}"
            )
        lines.append("")
    lines.append("[Relationships]")
    for relationship in context.relationships:
        lines.append(
            f"- {relationship.column} -> "
            f"{relationship.referenced_table}.{relationship.referenced_column}"
        )
    lines.extend(("", "RELEVANT COLUMN MEANINGS", ""))
    for key, meaning in context.column_meanings:
        lines.append(f"- {key}: {meaning}")
    lines.extend(("", "RELEVANT BUSINESS KNOWLEDGE", ""))
    for entry in context.knowledge:
        lines.append(f"- [{entry.identifier}] {entry.name}")
        if entry.description:
            lines.append(f"  Description: {entry.description}")
        if entry.definition:
            lines.append(f"  Definition: {entry.definition}")
    lines.extend(("", "RELEVANT DATABASE VALUES", ""))
    for evidence in context.values:
        lines.append(f"- {evidence.table}.{evidence.column}: {evidence.value}")
    return "\n".join(lines).strip()


def render_grounding_semantic_addition(context: GroundingContext) -> str:
    """Render only semantic/value sections for the existing service boundary."""
    lines = ["RELEVANT COLUMN MEANINGS", ""]
    for key, meaning in context.column_meanings:
        lines.append(f"- {key}: {meaning}")
    lines.extend(("", "RELEVANT BUSINESS KNOWLEDGE", ""))
    for entry in context.knowledge:
        lines.append(f"- [{entry.identifier}] {entry.name}")
        if entry.description:
            lines.append(f"  Description: {entry.description}")
        if entry.definition:
            lines.append(f"  Definition: {entry.definition}")
    lines.extend(("", "RELEVANT DATABASE VALUES", ""))
    for evidence in context.values:
        lines.append(f"- {evidence.table}.{evidence.column}: {evidence.value}")
    return "\n".join(lines).strip()
