"""A person's own names and pins for their conversations and reports.

A mark is private to the actor who set it: renaming a conversation or pinning a
report here changes only that person's sidebar. A report's shared title is
changed on the report itself.  Marks grant nothing and no gate reads them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Engine, and_, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.metadata.tables import marks

MAX_MARKS = 500


class MarkKind(StrEnum):
    # Named by the first run of the conversation.
    CONVERSATION = "conversation"
    REPORT = "report"


class MarkUpdate(BaseModel):
    """A blank title goes back to the default name; unpinned drops the pin."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    pinned: bool = False

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class Mark(BaseModel):
    kind: MarkKind
    item_id: str
    title: str | None = None
    pinned_at: datetime | None = None
    updated_at: datetime


class MarkStore(Protocol):
    def list(self, actor: str) -> list[Mark]: ...

    def set(self, actor: str, kind: MarkKind, item_id: str, update: MarkUpdate) -> Mark | None:
        """Save the mark, or remove it when it holds nothing; return what is kept."""
        ...


def _mark(kind: MarkKind, item_id: str, update: MarkUpdate, kept: Mark | None) -> Mark | None:
    if update.title is None and not update.pinned:
        return None
    now = datetime.now(UTC)
    pinned_at = (kept.pinned_at if kept and kept.pinned_at else now) if update.pinned else None
    return Mark(kind=kind, item_id=item_id, title=update.title, pinned_at=pinned_at, updated_at=now)


class MarkLimitError(ValueError):
    pass


class InMemoryMarkStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, MarkKind, str], Mark] = {}

    def list(self, actor: str) -> list[Mark]:
        return [mark for (owner, _, _), mark in self._items.items() if owner == actor]

    def set(self, actor: str, kind: MarkKind, item_id: str, update: MarkUpdate) -> Mark | None:
        key = (actor, kind, item_id)
        mark = _mark(kind, item_id, update, self._items.get(key))
        if mark is None:
            self._items.pop(key, None)
            return None
        if key not in self._items and len(self.list(actor)) >= MAX_MARKS:
            raise MarkLimitError(f"At most {MAX_MARKS} names and pins are kept per person.")
        self._items[key] = mark
        return mark


class SqlMarkStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self, actor: str) -> list[Mark]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(marks).where(marks.c.actor == actor)).mappings()
            return [Mark.model_validate(dict(row)) for row in rows]

    def set(self, actor: str, kind: MarkKind, item_id: str, update: MarkUpdate) -> Mark | None:
        key = and_(marks.c.actor == actor, marks.c.kind == kind.value, marks.c.item_id == item_id)
        with self.engine.begin() as connection:
            row = connection.execute(select(marks).where(key)).mappings().first()
            kept = Mark.model_validate(dict(row)) if row is not None else None
            mark = _mark(kind, item_id, update, kept)
            if mark is None:
                connection.execute(delete(marks).where(key))
                return None
            if kept is None:
                count = connection.execute(
                    select(marks.c.item_id).where(marks.c.actor == actor)
                ).all()
                if len(count) >= MAX_MARKS:
                    raise MarkLimitError(f"At most {MAX_MARKS} names and pins are kept per person.")
            values = {
                "actor": actor,
                "kind": kind.value,
                "item_id": item_id,
                "title": mark.title,
                "pinned_at": mark.pinned_at,
                "updated_at": mark.updated_at,
            }
            connection.execute(
                insert(marks)
                .values(**values)
                .on_conflict_do_update(index_elements=["actor", "kind", "item_id"], set_=values)
            )
            return mark
