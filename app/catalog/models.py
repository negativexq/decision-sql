from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class ColumnMetadata(BaseModel):
    name: str
    type: str
    description: str
    sensitivity: Sensitivity = Sensitivity.PUBLIC
    queryable: bool = True
    primary_key: bool = False


class RelationshipMetadata(BaseModel):
    column: str
    referenced_table: str
    referenced_column: str


class TableMetadata(BaseModel):
    name: str
    description: str
    aliases: tuple[str, ...] = ()
    queryable: bool = True
    columns: tuple[ColumnMetadata, ...] = Field(default_factory=tuple)
    relationships: tuple[RelationshipMetadata, ...] = Field(default_factory=tuple)

    def get_column(self, name: str) -> ColumnMetadata | None:
        normalized = name.lower()
        return next((column for column in self.columns if column.name.lower() == normalized), None)


class SchemaCatalog(BaseModel):
    tables: tuple[TableMetadata, ...] = Field(default_factory=tuple)

    def get_table(self, name: str) -> TableMetadata | None:
        normalized = name.lower()
        return next(
            (
                table
                for table in self.tables
                if table.name.lower() == normalized
                or normalized in {alias.lower() for alias in table.aliases}
            ),
            None,
        )

    def relevant_tables(self, text: str, limit: int = 5) -> list[TableMetadata]:
        """Return deterministic metadata matches for later schema retrieval stages."""
        tokens = {token.strip(".,?!:;()[]").lower() for token in text.split()}
        scored = []
        for table in self.tables:
            searchable = {table.name.lower(), *(alias.lower() for alias in table.aliases)}
            searchable.update(column.name.lower() for column in table.columns)
            score = len(tokens & searchable)
            if score:
                scored.append((score, table.name, table))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [table for _, _, table in scored[:limit]]


class RelevanceScore(BaseModel):
    table_name: str
    score: int
    matched_tokens: tuple[str, ...] = ()


class RelevantColumn(BaseModel):
    table_name: str
    name: str
    type: str
    description: str
    sensitivity: Sensitivity
    primary_key: bool = False
    foreign_key_table: str | None = None
    foreign_key_column: str | None = None


class RelevantTable(BaseModel):
    name: str
    description: str
    columns: tuple[RelevantColumn, ...] = Field(default_factory=tuple)


class RelevantRelationship(BaseModel):
    source_table: str
    source_column: str
    target_table: str
    target_column: str


class ContextMetadata(BaseModel):
    mode: str
    seed_table_count: int
    selected_table_count: int
    selected_column_count: int
    relationship_count: int
    relevance_scores: tuple[RelevanceScore, ...] = ()


class SchemaContext(BaseModel):
    """Bounded, server-derived schema context safe to serialize for a provider."""

    model_config = ConfigDict(frozen=True)

    tables: tuple[RelevantTable, ...] = Field(default_factory=tuple)
    relationships: tuple[RelevantRelationship, ...] = Field(default_factory=tuple)
    selected_columns: tuple[RelevantColumn, ...] = Field(default_factory=tuple)
    context_metadata: ContextMetadata
