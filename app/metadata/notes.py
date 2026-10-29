"""Column descriptions an administrator writes for a source.

A source's own comments become the catalog's descriptions, but many sources have
none, and an administrator who can only read the source cannot add them there. A
note replaces the description of one column in the context the model sees. It
explains a column; it grants nothing, and the gates never read it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.catalog.models import SchemaCatalog
from app.metadata.tables import column_notes

_NAME = Field(min_length=1, max_length=255)
_MAX_NOTES_PER_UPDATE = 500


class ColumnNoteEdit(BaseModel):
    """One column's description; an empty description removes the note."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str = _NAME
    column: str = _NAME
    description: str = Field(default="", max_length=500)


class ColumnNotesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: list[ColumnNoteEdit] = Field(max_length=_MAX_NOTES_PER_UPDATE)


class ColumnNote(BaseModel):
    table: str
    column: str
    description: str
    updated_by: str
    updated_at: datetime


class ConnectionNotes(BaseModel):
    notes: list[ColumnNote]


class ColumnNotesRejectedError(ValueError):
    """An update that names a column the source does not have."""


class ColumnNoteStore(Protocol):
    def list(self, connection_id: str) -> list[ColumnNote]: ...

    def apply(self, connection_id: str, edits: Sequence[ColumnNoteEdit], actor: str) -> None: ...

    def delete(self, connection_id: str) -> None: ...


class InMemoryColumnNoteStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[tuple[str, str], ColumnNote]] = {}

    def list(self, connection_id: str) -> list[ColumnNote]:
        return sorted(
            self._items.get(connection_id, {}).values(), key=lambda n: (n.table, n.column)
        )

    def apply(self, connection_id: str, edits: Sequence[ColumnNoteEdit], actor: str) -> None:
        notes = self._items.setdefault(connection_id, {})
        now = datetime.now(UTC)
        for edit in edits:
            key = (edit.table, edit.column)
            text = " ".join(edit.description.split())
            if text:
                notes[key] = ColumnNote(
                    table=edit.table,
                    column=edit.column,
                    description=text,
                    updated_by=actor,
                    updated_at=now,
                )
            else:
                notes.pop(key, None)

    def delete(self, connection_id: str) -> None:
        self._items.pop(connection_id, None)


class SqlColumnNoteStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self, connection_id: str) -> list[ColumnNote]:
        query = (
            select(column_notes)
            .where(column_notes.c.connection_id == connection_id)
            .order_by(column_notes.c.table_name, column_notes.c.column_name)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [
            ColumnNote(
                table=row["table_name"],
                column=row["column_name"],
                description=row["description"],
                updated_by=row["updated_by"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def apply(self, connection_id: str, edits: Sequence[ColumnNoteEdit], actor: str) -> None:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            for edit in edits:
                text = " ".join(edit.description.split())
                if not text:
                    connection.execute(
                        delete(column_notes).where(
                            column_notes.c.connection_id == connection_id,
                            column_notes.c.table_name == edit.table,
                            column_notes.c.column_name == edit.column,
                        )
                    )
                    continue
                values = {"description": text, "updated_by": actor, "updated_at": now}
                statement = insert(column_notes).values(
                    connection_id=connection_id,
                    table_name=edit.table,
                    column_name=edit.column,
                    **values,
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["connection_id", "table_name", "column_name"], set_=values
                    )
                )

    def delete(self, connection_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(column_notes).where(column_notes.c.connection_id == connection_id)
            )


def check_notes(catalog: SchemaCatalog, edits: list[ColumnNoteEdit]) -> list[ColumnNoteEdit]:
    """Edits under the source's own spelling of each name; unknown columns are rejected."""
    checked: list[ColumnNoteEdit] = []
    unknown: list[str] = []
    for edit in edits:
        table = catalog.get_table(edit.table)
        column = table.get_column(edit.column) if table is not None else None
        if table is None or column is None:
            unknown.append(f"{edit.table}.{edit.column}")
            continue
        checked.append(edit.model_copy(update={"table": table.name, "column": column.name}))
    if unknown:
        raise ColumnNotesRejectedError(
            f"These columns are not in the connection's schema: {', '.join(unknown[:5])}."
        )
    return checked


def apply_notes(catalog: SchemaCatalog, notes: list[ColumnNote]) -> SchemaCatalog:
    """The catalog with each noted column described by its note."""
    if not notes:
        return catalog
    by_column = {(note.table, note.column): note.description for note in notes}
    return catalog.model_copy(
        update={
            "tables": tuple(
                table.model_copy(
                    update={
                        "columns": tuple(
                            column.model_copy(
                                update={"description": by_column[(table.name, column.name)]}
                            )
                            if (table.name, column.name) in by_column
                            else column
                            for column in table.columns
                        )
                    }
                )
                for table in catalog.tables
            )
        }
    )
