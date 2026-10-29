import pytest

from app.catalog.models import ColumnMetadata, SchemaCatalog, TableMetadata
from app.metadata.notes import (
    ColumnNoteEdit,
    ColumnNotesRejectedError,
    InMemoryColumnNoteStore,
    apply_notes,
    check_notes,
)


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="district",
                description="district table.",
                columns=(
                    ColumnMetadata(name="a2", type="text", description="a2 from district."),
                    ColumnMetadata(name="a4", type="integer", description="a4 from district."),
                ),
            ),
        )
    )


def test_a_note_replaces_the_description_the_model_sees() -> None:
    store = InMemoryColumnNoteStore()
    edits = check_notes(
        _catalog(),
        [ColumnNoteEdit(table="District", column="A4", description=" number of\ninhabitants ")],
    )
    store.apply("c1", edits, "admin")

    [note] = store.list("c1")
    assert (note.table, note.column, note.description) == (
        "district",
        "a4",
        "number of inhabitants",
    )
    described = apply_notes(_catalog(), store.list("c1"))
    table = described.get_table("district")
    assert table is not None
    assert [column.description for column in table.columns] == [
        "a2 from district.",
        "number of inhabitants",
    ]
    assert store.list("c2") == []


def test_an_empty_note_removes_it_and_an_unknown_column_is_rejected() -> None:
    store = InMemoryColumnNoteStore()
    store.apply(
        "c1", [ColumnNoteEdit(table="district", column="a2", description="district name")], "admin"
    )
    store.apply("c1", [ColumnNoteEdit(table="district", column="a2", description="  ")], "admin")
    assert store.list("c1") == []

    with pytest.raises(ColumnNotesRejectedError, match="district.a9"):
        check_notes(_catalog(), [ColumnNoteEdit(table="district", column="a9", description="x")])
