from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.metadata import marks as marks_module
from app.metadata.marks import InMemoryMarkStore, MarkKind, MarkLimitError, MarkUpdate


def test_a_mark_belongs_to_one_person_and_keeps_when_it_was_pinned() -> None:
    store = InMemoryMarkStore()

    pinned = store.set("ayse", MarkKind.REPORT, "rep_1", MarkUpdate(pinned=True))
    renamed = store.set("ayse", MarkKind.REPORT, "rep_1", MarkUpdate(title=" Q3 ", pinned=True))

    assert pinned is not None and renamed is not None
    assert renamed.title == "Q3" and renamed.pinned_at == pinned.pinned_at
    assert store.list("mehmet") == []
    assert [mark.item_id for mark in store.list("ayse")] == ["rep_1"]


def test_a_mark_with_no_name_and_no_pin_is_removed() -> None:
    store = InMemoryMarkStore()
    store.set("ayse", MarkKind.CONVERSATION, "run_a", MarkUpdate(title="Revenue"))

    assert store.set("ayse", MarkKind.CONVERSATION, "run_a", MarkUpdate(title="  ")) is None
    assert store.list("ayse") == []
    with pytest.raises(ValidationError):
        MarkUpdate.model_validate({"title": "x", "sql": "SELECT 1"})


def test_marks_per_person_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(marks_module, "MAX_MARKS", 2)
    store = InMemoryMarkStore()
    for item in ("a", "b"):
        store.set("ayse", MarkKind.REPORT, item, MarkUpdate(pinned=True))

    with pytest.raises(MarkLimitError):
        store.set("ayse", MarkKind.REPORT, "c", MarkUpdate(pinned=True))
    # Changing a mark already kept is not a new one.
    assert store.set("ayse", MarkKind.REPORT, "a", MarkUpdate(title="A", pinned=True)) is not None
