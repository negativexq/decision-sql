import re
from collections import deque
from enum import StrEnum

from app.catalog.models import (
    ColumnMetadata,
    ContextMetadata,
    RelevanceScore,
    RelevantColumn,
    RelevantRelationship,
    RelevantTable,
    SchemaCatalog,
    SchemaContext,
    TableMetadata,
)


class SchemaContextMode(StrEnum):
    """Orthogonal schema-context cells used by M2 and M2.5 experiments."""

    # Values remain compatible with M2 artifacts and CLI. The member names
    # carry the explicit M2.5 experiment terminology.
    FULL_COMPACT = "full"
    RETRIEVED_BOUNDED = "retrieved"

    # Compatibility aliases for the M2 API. They intentionally resolve to the
    # same enum members and do not create alternate retrieval behavior.
    FULL = "full"
    RETRIEVED = "retrieved"


class SchemaResolutionError(RuntimeError):
    pass


class SchemaContextResolver:
    """Deterministically retrieves and bounds queryable schema metadata."""

    def __init__(
        self,
        catalog: SchemaCatalog,
        top_k: int = 3,
        max_tables: int = 5,
        max_columns_per_table: int = 8,
        relationship_depth: int = 2,
    ) -> None:
        self.catalog = catalog
        self.top_k = top_k
        self.max_tables = max_tables
        self.max_columns_per_table = max_columns_per_table
        self.relationship_depth = relationship_depth

    def resolve(
        self,
        question: str,
        mode: SchemaContextMode = SchemaContextMode.RETRIEVED_BOUNDED,
    ) -> SchemaContext:
        if not question.strip():
            raise SchemaResolutionError("Question is empty")

        scores = self._score_tables(question)
        seed_tables: list[TableMetadata]
        if mode is SchemaContextMode.FULL_COMPACT:
            selected = [table for table in self.catalog.tables if table.queryable]
            seed_tables = selected
        else:
            seed_tables = []
            for match in scores[: self.top_k]:
                table = self.catalog.get_table(match.table_name)
                if table is not None:
                    seed_tables.append(table)
            selected = [table for table in seed_tables if table is not None and table.queryable]
            if not selected:
                raise SchemaResolutionError("No queryable schema matched the question")
            selected = self._expand_relationships(selected)

        if mode is SchemaContextMode.RETRIEVED_BOUNDED:
            seed_names = {table.name for table in seed_tables}
            seed_selection = [table for table in seed_tables if table.name in seed_names]
            expanded_selection = sorted(
                (table for table in selected if table.name not in seed_names),
                key=lambda item: item.name,
            )
            selected = (seed_selection + expanded_selection)[: self.max_tables]
        else:
            selected = sorted(selected, key=lambda item: item.name)
        relationships = self._relationships_for(selected)
        relevant_tables: list[RelevantTable] = []
        selected_columns: list[RelevantColumn] = []
        for table in selected:
            columns = self._select_columns(
                table,
                question,
                limit=(
                    self.max_columns_per_table
                    if mode is SchemaContextMode.RETRIEVED_BOUNDED
                    else None
                ),
            )
            relevant_table = RelevantTable(
                name=table.name,
                description=table.description,
                columns=tuple(columns),
            )
            relevant_tables.append(relevant_table)
            selected_columns.extend(columns)

        metadata = ContextMetadata(
            mode=mode.value,
            seed_table_count=len(seed_tables),
            selected_table_count=len(relevant_tables),
            selected_column_count=len(selected_columns),
            relationship_count=len(relationships),
            relevance_scores=tuple(scores),
        )
        return SchemaContext(
            tables=tuple(relevant_tables),
            relationships=tuple(relationships),
            selected_columns=tuple(selected_columns),
            context_metadata=metadata,
        )

    def _score_tables(self, question: str) -> list[RelevanceScore]:
        question_tokens = _tokenize(question)
        scored: list[RelevanceScore] = []
        for table in self.catalog.tables:
            if not table.queryable:
                continue
            table_tokens = _tokenize(table.name)
            alias_tokens = set().union(*(_tokenize(alias) for alias in table.aliases))
            description_tokens = _tokenize(table.description)
            column_tokens = set().union(
                *(_tokenize(f"{column.name} {column.description}") for column in table.columns)
            )
            name_matches = question_tokens & table_tokens
            alias_matches = question_tokens & alias_tokens
            description_matches = question_tokens & description_tokens
            column_matches = question_tokens & column_tokens
            score = (
                len(name_matches) * 10
                + len(alias_matches) * 6
                + len(description_matches) * 3
                + len(column_matches)
            )
            if score:
                scored.append(
                    RelevanceScore(
                        table_name=table.name,
                        score=score,
                        matched_tokens=tuple(
                            sorted(
                                name_matches
                                | alias_matches
                                | description_matches
                                | column_matches
                            )
                        ),
                    )
                )
        scored.sort(key=lambda item: (-item.score, item.table_name))
        return scored

    def _expand_relationships(self, seed_tables: list[TableMetadata]) -> list[TableMetadata]:
        if self.relationship_depth <= 0 or len(seed_tables) < 2:
            return seed_tables
        seed_names = {table.name for table in seed_tables}
        adjacency = self._adjacency()
        selected: dict[str, TableMetadata] = {table.name: table for table in seed_tables}
        for start_name in sorted(seed_names):
            for target_name in sorted(seed_names):
                if start_name >= target_name:
                    continue
                path = _shortest_path(
                    adjacency, start_name, target_name, self.relationship_depth
                )
                for table_name in path:
                    table = self.catalog.get_table(table_name)
                    if table is not None and table.queryable:
                        selected.setdefault(table.name, table)
        return list(selected.values())

    def _adjacency(self) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = {table.name: set() for table in self.catalog.tables}
        for table in self.catalog.tables:
            if not table.queryable:
                continue
            for relationship in table.relationships:
                target = self.catalog.get_table(relationship.referenced_table)
                if target is None or not target.queryable:
                    continue
                adjacency[table.name].add(target.name)
                adjacency[target.name].add(table.name)
        return adjacency

    @staticmethod
    def _relationships_for(
        tables: list[TableMetadata],
    ) -> list[RelevantRelationship]:
        selected_names = {table.name for table in tables}
        relationships: list[RelevantRelationship] = []
        for table in tables:
            for relationship in table.relationships:
                if relationship.referenced_table in selected_names:
                    relationships.append(
                        RelevantRelationship(
                            source_table=table.name,
                            source_column=relationship.column,
                            target_table=relationship.referenced_table,
                            target_column=relationship.referenced_column,
                        )
                    )
        relationships.sort(
            key=lambda item: (
                item.source_table,
                item.source_column,
                item.target_table,
                item.target_column,
            )
        )
        return relationships

    def _select_columns(
        self, table: TableMetadata, question: str, limit: int | None
    ) -> list[RelevantColumn]:
        queryable = [column for column in table.columns if column.queryable]
        relationship_columns = {relationship.column for relationship in table.relationships}
        mandatory = [
            column
            for column in queryable
            if column.primary_key or column.name in relationship_columns
        ]
        mandatory_names = {column.name for column in mandatory}
        question_tokens = _tokenize(question)

        def rank(column: ColumnMetadata) -> tuple[int, str]:
            matches = question_tokens & _tokenize(
                f"{column.name} {column.description}"
            )
            return (-len(matches), column.name)

        additional = sorted(
            [column for column in queryable if column.name not in mandatory_names],
            key=rank,
        )
        if limit is None:
            selected = queryable
        else:
            selected = mandatory + additional[: max(0, limit - len(mandatory))]
        selected.sort(key=lambda column: column.name)
        return [
            RelevantColumn(
                table_name=table.name,
                name=column.name,
                type=column.type,
                description=column.description,
                sensitivity=column.sensitivity,
                primary_key=column.primary_key,
                foreign_key_table=next(
                    (
                        relationship.referenced_table
                        for relationship in table.relationships
                        if relationship.column == column.name
                    ),
                    None,
                ),
                foreign_key_column=next(
                    (
                        relationship.referenced_column
                        for relationship in table.relationships
                        if relationship.column == column.name
                    ),
                    None,
                ),
            )
            for column in selected
        ]


def serialize_schema_context(context: SchemaContext) -> str:
    """Serialize bounded queryable metadata in a stable structural format.

    This is deliberately a small DecisionSQL-owned schema representation. It
    carries structure needed for generation, but never values, examples, or
    non-queryable metadata.
    """
    lines: list[str] = []
    for table in sorted(context.tables, key=lambda item: item.name):
        lines.extend(
            (f"[Table] {table.name}", f"Description: {table.description}", "[Columns]")
        )
        for column in sorted(table.columns, key=lambda item: item.name):
            annotations: list[str] = []
            if column.primary_key:
                annotations.append("PK")
            if column.foreign_key_table and column.foreign_key_column:
                annotations.append(
                    f"FK -> {column.foreign_key_table}.{column.foreign_key_column}"
                )
            annotation_text = f" [{', '.join(annotations)}]" if annotations else ""
            lines.append(
                f"- {table.name}.{column.name} {column.type}{annotation_text} — "
                f"{column.description}"
            )
        lines.append("")
    lines.append("[Relationships]")
    for relationship in sorted(
        context.relationships,
        key=lambda item: (item.source_table, item.source_column, item.target_table),
    ):
        lines.append(
            f"- {relationship.source_table}.{relationship.source_column} -> "
            f"{relationship.target_table}.{relationship.target_column}"
        )
    return "\n".join(lines).strip()


def _tokenize(value: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")))
    variants = set(tokens)
    for token in tokens:
        if len(token) > 3 and token.endswith("s"):
            variants.add(token[:-1])
        elif len(token) > 3:
            variants.add(f"{token}s")
    return variants


def _shortest_path(
    adjacency: dict[str, set[str]], start: str, target: str, max_depth: int
) -> tuple[str, ...]:
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(start, (start,))])
    visited = {start}
    while queue:
        current, path = queue.popleft()
        if current == target:
            return path
        if len(path) - 1 >= max_depth:
            continue
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, (*path, neighbor)))
    return ()
