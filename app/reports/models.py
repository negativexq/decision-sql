"""Reports: a page of cards laid out on a grid.

A query card holds a question and the SQL that answered it.  The SQL is pinned
from an executed run in the audit log, never taken from the client, and every
refresh sends it through the same checks as a new answer.  A text card holds a
heading and notes.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.operator.models import RunRecord
from app.reports.filters import MAX_FILTERS, FilterChoice, FilterKind, ReportFilter

GRID_COLUMNS = 12
MAX_CARDS = 40
MAX_PEOPLE = 50
# The same shape app.metadata.service accepts in the actor header.
ACTOR_PATTERN = r"^[A-Za-z0-9._@+-]{1,120}$"


class Visibility(StrEnum):
    # Only the owner, editors, and named viewers can open it.
    PRIVATE = "private"
    # Everyone in the workspace can open it; editing still needs the owner or an editor.
    WORKSPACE = "workspace"


class ReportRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


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
    # A fingerprint of each table the SQL reads, as the catalog described it when the
    # card was pinned; a refresh compares it with the catalog of the moment.
    basis: dict[str, str] = Field(default_factory=dict)


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    cards: list[CardInput] = Field(default_factory=list, max_length=MAX_CARDS)
    filters: list[ReportFilter] = Field(default_factory=list, max_length=MAX_FILTERS)


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    connection_id: str = Field(min_length=1, max_length=64)


class ReportAccess(BaseModel):
    """Who besides the owner may open or change a report."""

    model_config = ConfigDict(extra="forbid")

    visibility: Visibility = Visibility.PRIVATE
    editors: list[str] = Field(default_factory=list, max_length=MAX_PEOPLE)
    viewers: list[str] = Field(default_factory=list, max_length=MAX_PEOPLE)

    @field_validator("editors", "viewers")
    @classmethod
    def _people(cls, value: list[str]) -> list[str]:
        people = sorted({item.strip().lower() for item in value if item.strip()})
        for person in people:
            if not re.match(ACTOR_PATTERN, person):
                raise ValueError(f"{person!r} is not a valid name.")
        return people


class ReportSummary(BaseModel):
    id: str
    connection_id: str
    title: str
    description: str
    card_count: int
    owner: str
    visibility: Visibility
    role: ReportRole
    updated_by: str
    updated_at: datetime


class Report(BaseModel):
    id: str
    connection_id: str
    title: str
    description: str
    cards: list[ReportCard]
    filters: list[ReportFilter] = Field(default_factory=list)
    visibility: Visibility = Visibility.PRIVATE
    editors: list[str] = Field(default_factory=list)
    viewers: list[str] = Field(default_factory=list)
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime

    def role_of(self, actor: str) -> ReportRole | None:
        if actor == self.created_by:
            return ReportRole.OWNER
        if actor in self.editors:
            return ReportRole.EDITOR
        if actor in self.viewers or self.visibility is Visibility.WORKSPACE:
            return ReportRole.VIEWER
        return None

    def summary(self, role: ReportRole) -> ReportSummary:
        return ReportSummary(
            id=self.id,
            connection_id=self.connection_id,
            title=self.title,
            description=self.description,
            card_count=len(self.cards),
            owner=self.created_by,
            visibility=self.visibility,
            role=role,
            updated_by=self.updated_by,
            updated_at=self.updated_at,
        )


class ReportView(Report):
    """A report as one reader sees it, with what that reader may do."""

    role: ReportRole


class CardRefreshRequest(BaseModel):
    """The reader's filter choices, by filter id; they are not saved with the report."""

    model_config = ConfigDict(extra="forbid")

    filters: dict[str, FilterChoice] = Field(default_factory=dict, max_length=MAX_FILTERS)


class CardRefresh(BaseModel):
    """A card's run, and the tables whose description changed since it was pinned."""

    run: RunRecord
    changed_tables: list[str] = Field(default_factory=list)
    # Filters with a choice that narrowed this card, and those its SQL could not take.
    applied_filters: list[str] = Field(default_factory=list)
    skipped_filters: list[str] = Field(default_factory=list)


class FilterColumn(BaseModel):
    """A column a filter can name, with the kinds of filter it supports."""

    table: str
    column: str
    type: str
    kinds: list[FilterKind]


class FilterOptions(BaseModel):
    values: list[str]
    # True when the column holds more distinct values than were read.
    truncated: bool
    run_id: str


class CardQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
