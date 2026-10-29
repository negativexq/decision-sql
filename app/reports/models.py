"""Reports: a page of cards laid out on a grid.

A query card holds a question and the SQL that answered it.  The SQL is pinned
from an executed run in the audit log, never taken from the client, and every
refresh sends it through the same checks as a new answer.  A text card holds a
heading and notes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GRID_COLUMNS = 12
MAX_CARDS = 40


class ChartType(StrEnum):
    NUMBER = "number"
    BAR = "bar"
    COLUMN = "column"
    LINE = "line"
    AREA = "area"
    PIE = "pie"
    TABLE = "table"


class CardLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, lt=GRID_COLUMNS)
    y: int = Field(ge=0, le=1000)
    w: int = Field(ge=1, le=GRID_COLUMNS)
    h: int = Field(ge=1, le=40)


class CardInput(BaseModel):
    """A card as the client sends it; the server fills in the pinned SQL."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    kind: Literal["query", "text"]
    title: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=4000)
    question: str = Field(default="", max_length=1000)
    # The executed run whose SQL this card pins.
    run_id: str | None = Field(default=None, max_length=80)
    chart: ChartType = ChartType.TABLE
    # Columns the viewer chose for the category axis and the value; None picks one.
    dimension: str | None = Field(default=None, max_length=200)
    measure: str | None = Field(default=None, max_length=200)
    layout: CardLayout


class ReportCard(CardInput):
    sql: str | None = None


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    cards: list[CardInput] = Field(default_factory=list, max_length=MAX_CARDS)


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    connection_id: str = Field(min_length=1, max_length=64)


class ReportSummary(BaseModel):
    id: str
    connection_id: str
    title: str
    description: str
    card_count: int
    updated_by: str
    updated_at: datetime


class Report(BaseModel):
    id: str
    connection_id: str
    title: str
    description: str
    cards: list[ReportCard]
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime

    def summary(self) -> ReportSummary:
        return ReportSummary(
            id=self.id,
            connection_id=self.connection_id,
            title=self.title,
            description=self.description,
            card_count=len(self.cards),
            updated_by=self.updated_by,
            updated_at=self.updated_at,
        )


class CardQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
