from app.catalog.models import (
    ColumnMetadata,
    RelationshipMetadata,
    SchemaCatalog,
    TableMetadata,
)
from app.governance.context import governed_context_from_schema_context
from app.metadata.values import (
    ColumnStats,
    KnownValues,
    apply_known_values,
    eligible_columns,
    known_values,
    sample_stats,
)
from app.retrieval.context import SchemaContextResolver


def _stats(common=None, freqs=None, bounds=None, null_frac=0.0, distinct=None):
    distinct = len(common or ()) if distinct is None else distinct
    return ColumnStats("stations", "segment", null_frac, common, freqs, bounds, distinct)


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="stations",
                description="Gas stations.",
                columns=(
                    ColumnMetadata(name="id", type="integer", description="Id.", primary_key=True),
                    ColumnMetadata(name="segment", type="text", description="Segment."),
                    ColumnMetadata(name="email", type="text", description="Contact."),
                    ColumnMetadata(name="price", type="numeric", description="Price."),
                    ColumnMetadata(name="brand_code", type="text", description="Brand."),
                ),
                relationships=(
                    RelationshipMetadata(
                        column="brand_code", referenced_table="brands", referenced_column="code"
                    ),
                ),
            ),
            TableMetadata(
                name="brands",
                description="Brands.",
                columns=(
                    ColumnMetadata(name="code", type="text", description="Code."),
                    ColumnMetadata(name="label", type="text", description="Label."),
                ),
            ),
        )
    )


def test_small_covered_domain_is_complete_and_sorted() -> None:
    hint = known_values(_stats(["Premium", "Other", "Value"], [0.5, 0.3, 0.19], null_frac=0.01))
    assert hint == KnownValues(("Other", "Premium", "Value"), complete=True)


def test_mostly_null_column_with_more_values_than_listed_is_not_a_domain() -> None:
    # Nulls make the listed values look covering; n_distinct says there are 24.
    assert known_values(_stats(["Junun Efreet"], [0.0002], null_frac=0.9989, distinct=24)) is None


def test_partial_coverage_of_words_gives_nothing() -> None:
    # The common values of a high-cardinality name column say nothing reliable.
    assert known_values(_stats(["Smith", "Jones"], [0.02, 0.01], ["Adams", "Zane"])) is None


def test_long_values_are_not_a_domain() -> None:
    assert known_values(_stats(["x" * 80], [1.0])) is None


def test_date_like_text_gives_format_examples() -> None:
    bounds = [f"2012-01-{day:02d}" for day in range(1, 29)]
    hint = known_values(_stats(None, None, bounds))
    assert hint is not None and not hint.complete
    assert hint.values == ("2012-01-01", "2012-01-15", "2012-01-28")


def test_personal_key_and_non_text_columns_are_never_eligible() -> None:
    assert eligible_columns(_catalog()) == {("stations", "segment"), ("brands", "label")}


def test_sample_of_small_table_counts_nulls() -> None:
    stats = sample_stats("stations", "segment", [("Premium", 6), (None, 2), ("Other", 2)])
    assert stats.most_common_vals == ["Premium", "Other"]
    assert stats.null_frac == 0.2
    assert known_values(stats) == KnownValues(("Other", "Premium"), complete=True)


def test_sample_with_many_values_is_only_format_evidence() -> None:
    counts = [(f"code {index}", 1) for index in range(40)]
    assert known_values(sample_stats("stations", "segment", counts)) is None


def test_values_reach_the_governed_context_only_where_known() -> None:
    catalog = apply_known_values(
        _catalog(), {("stations", "segment"): KnownValues(("Other", "Premium"), True)}
    )
    context = SchemaContextResolver(catalog).resolve("Which segment has the most premium stations?")
    governed = governed_context_from_schema_context(context)
    dumped = {item["physical_column_or_path"]: item for item in governed.model_dump()["attributes"]}
    assert dumped["segment"]["known_values"] == ("Other", "Premium")
    assert dumped["segment"]["known_values_complete"] is True
    assert "known_values" not in dumped["id"]
