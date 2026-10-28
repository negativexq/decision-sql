import pytest

from app.catalog.models import ColumnMetadata, RelationshipMetadata, SchemaCatalog, TableMetadata
from app.metadata.relationships import (
    DeclaredRelationship,
    InMemoryRelationshipStore,
    RelationshipDeclaration,
    RelationshipRejectedError,
    candidate_relationships,
    check_declaration,
    compatible_types,
    foreign_keys,
    merge_declared,
)
from app.semantics.measures import measure_catalog_from_schema


def column(name: str, type_: str = "integer", primary_key: bool = False) -> ColumnMetadata:
    return ColumnMetadata(name=name, type=type_, description=name, primary_key=primary_key)


def catalog() -> SchemaCatalog:
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="customers",
                description="customers",
                columns=(column("customerid", primary_key=True), column("segment", "text")),
            ),
            TableMetadata(
                name="transactions",
                description="transactions",
                columns=(
                    column("id", primary_key=True),
                    column("customerid"),
                    column("amount", "numeric"),
                    column("note", "text"),
                ),
            ),
            TableMetadata(
                name="sets",
                description="sets",
                columns=(column("id", primary_key=True), column("code", "text")),
            ),
            TableMetadata(
                name="cards",
                description="cards",
                columns=(column("id", primary_key=True), column("setcode", "character varying")),
                relationships=(),
            ),
        )
    )


def declared(**fields: str) -> DeclaredRelationship:
    from datetime import UTC, datetime

    return DeclaredRelationship(
        id="rel_1", connection_id="c1", created_by="a", created_at=datetime.now(UTC), **fields
    )


def test_declared_relationship_is_merged_like_a_foreign_key_and_reaches_grain() -> None:
    merged = merge_declared(
        catalog(),
        [
            declared(
                from_table="transactions",
                from_column="customerid",
                to_table="customers",
                to_column="customerid",
            ),
            # A declaration whose column was dropped from the source is ignored.
            declared(
                from_table="transactions", from_column="gone", to_table="customers", to_column="x"
            ),
        ],
    )
    transactions = merged.get_table("transactions")
    assert transactions is not None
    assert transactions.relationships == (
        RelationshipMetadata(
            column="customerid", referenced_table="customers", referenced_column="customerid"
        ),
    )
    grain = measure_catalog_from_schema(merged)
    assert [item.cardinality for item in grain.relationships] == ["MANY_TO_ONE"]


def test_check_declaration_resolves_names_and_rejects_bad_ones() -> None:
    resolved = check_declaration(
        catalog(),
        RelationshipDeclaration(
            from_table="Transactions",
            from_column="CustomerID",
            to_table="customers",
            to_column="customerid",
        ),
        [],
    )
    assert (resolved.from_table, resolved.from_column) == ("transactions", "customerid")

    rejected = {
        ("orders", "customerid", "customers", "customerid"): "not in this source",
        ("transactions", "missing", "customers", "customerid"): "is not in transactions",
        ("transactions", "note", "customers", "customerid"): "incompatible types",
        ("customers", "customerid", "customers", "customerid"): "cannot point at itself",
    }
    for (from_table, from_column, to_table, to_column), message in rejected.items():
        with pytest.raises(RelationshipRejectedError, match=message):
            check_declaration(
                catalog(),
                RelationshipDeclaration(
                    from_table=from_table,
                    from_column=from_column,
                    to_table=to_table,
                    to_column=to_column,
                ),
                [],
            )
    with pytest.raises(RelationshipRejectedError, match="already exists"):
        check_declaration(catalog(), resolved, [resolved])


def test_candidates_come_from_names_types_and_unique_keys() -> None:
    unique = {("customers", "customerid"), ("sets", "code"), ("sets", "id"), ("cards", "id")}
    found = {
        (item.from_table, item.from_column, item.to_table, item.to_column)
        for item in candidate_relationships(catalog(), unique)
    }
    assert ("transactions", "customerid", "customers", "customerid") in found
    assert ("cards", "setcode", "sets", "code") in found
    # Shared generic names such as id are not evidence of a relationship.
    assert not any(item[1] == "id" for item in found)


def test_candidates_skip_relationships_that_already_exist() -> None:
    merged = merge_declared(
        catalog(),
        [
            declared(
                from_table="transactions",
                from_column="customerid",
                to_table="customers",
                to_column="customerid",
            )
        ],
    )
    found = candidate_relationships(merged, {("customers", "customerid")})
    assert found == []
    assert len(foreign_keys(merged)) == 1


def test_type_families() -> None:
    assert compatible_types("integer", "bigint")
    assert compatible_types("integer", "numeric(10,2)")
    assert compatible_types("text", "character varying(20)")
    assert not compatible_types("text", "integer")
    assert not compatible_types("uuid", "text")


def test_in_memory_store_scopes_by_connection() -> None:
    store = InMemoryRelationshipStore()
    item = store.create(
        "c1",
        RelationshipDeclaration(from_table="a", from_column="b", to_table="c", to_column="d"),
        "ayse",
    )
    assert store.list("c2") == []
    assert not store.delete("c2", item.id)
    assert store.delete("c1", item.id)
    assert store.list("c1") == []


def _players() -> SchemaCatalog:
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="player",
                description="player",
                columns=(column("player_api_id", primary_key=True), column("player_fifa_api_id")),
            ),
            TableMetadata(
                name="player_attributes",
                description="player_attributes",
                columns=(
                    column("id", primary_key=True),
                    column("player_api_id"),
                    column("player_fifa_api_id"),
                    column("rival_player_api_id"),
                ),
                relationships=(
                    RelationshipMetadata(
                        column="player_api_id",
                        referenced_table="player",
                        referenced_column="player_api_id",
                    ),
                ),
            ),
        )
    )


def test_second_key_of_an_already_linked_row_is_neither_suggested_nor_accepted() -> None:
    unique = {("player", "player_api_id"), ("player", "player_fifa_api_id")}
    found = {
        (item.from_column, item.to_column) for item in candidate_relationships(_players(), unique)
    }
    assert ("player_fifa_api_id", "player_fifa_api_id") not in found

    alternate = RelationshipDeclaration(
        from_table="player_attributes",
        from_column="player_fifa_api_id",
        to_table="player",
        to_column="player_fifa_api_id",
    )
    with pytest.raises(RelationshipRejectedError, match="already points to player"):
        check_declaration(_players(), alternate, foreign_keys(_players()))


def test_a_second_role_to_the_same_key_is_still_allowed() -> None:
    role = RelationshipDeclaration(
        from_table="player_attributes",
        from_column="rival_player_api_id",
        to_table="player",
        to_column="player_api_id",
    )
    assert check_declaration(_players(), role, foreign_keys(_players())) == role
