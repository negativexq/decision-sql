from app.catalog.models import (
    ColumnMetadata,
    RelationshipMetadata,
    SchemaCatalog,
    TableMetadata,
)
from app.governance.context import (
    governed_context_from_schema_context,
    render_governed_context,
    serialize_governed_context,
)
from app.metadata.values import (
    ColumnProfile,
    ColumnStats,
    InMemoryValueSharingStore,
    KnownValues,
    ValueSharing,
    apply_known_values,
    apply_profiles,
    column_profile,
    eligible_columns,
    known_values,
    sample_stats,
    value_patterns,
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


def test_distinct_share_of_a_small_table_counts_as_a_domain() -> None:
    # pg_stats stores n_distinct as -share-of-rows when it expects growth: 3 values in 24 rows.
    stats = ColumnStats(
        "products",
        "family",
        0.0,
        ["Hardware", "Software", "Service"],
        [0.34, 0.33, 0.33],
        None,
        -0.125,
        24,
    )
    assert known_values(stats) == KnownValues(("Hardware", "Service", "Software"), complete=True)


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
        _catalog(),
        {("stations", "segment"): KnownValues(("Other", "Premium"), True)},
        ValueSharing(enabled=True),
    )
    context = SchemaContextResolver(catalog).resolve("Which segment has the most premium stations?")
    governed = governed_context_from_schema_context(context)
    dumped = {item["physical_column_or_path"]: item for item in governed.model_dump()["attributes"]}
    assert dumped["segment"]["known_values"] == ("Other", "Premium")
    assert dumped["segment"]["known_values_complete"] is True
    assert "known_values" not in dumped["id"]


def test_values_stay_out_of_the_context_until_sharing_is_turned_on() -> None:
    hints = {("stations", "segment"): KnownValues(("Other", "Premium"), True)}
    for sharing in (None, ValueSharing(enabled=True, excluded=("stations.segment",))):
        catalog = apply_known_values(_catalog(), hints, sharing)
        segment = next(c for c in catalog.tables[0].columns if c.name == "segment")
        assert segment.known_values == ()
        assert segment.value_patterns == ()


def test_unshared_code_columns_show_only_their_shape() -> None:
    hints = {("stations", "segment"): KnownValues(("2012-01-15", "2013-12-31"), False)}
    catalog = apply_known_values(_catalog(), hints)
    segment = next(c for c in catalog.tables[0].columns if c.name == "segment")
    assert segment.known_values == ()
    assert segment.value_patterns == ("9999-99-99",)
    context = SchemaContextResolver(catalog).resolve("Which segment has the most stations?")
    dumped = {
        item["physical_column_or_path"]: item
        for item in governed_context_from_schema_context(context).model_dump()["attributes"]
    }
    assert dumped["segment"]["value_patterns"] == ("9999-99-99",)
    assert "value_patterns" not in dumped["id"]


def test_words_have_no_pattern() -> None:
    assert value_patterns(["Premium", "2012-01-15"]) == ()
    assert value_patterns(["12:30", "9:05"]) == ("99:99", "9:99")


def test_sharing_choice_is_recorded_per_connection() -> None:
    store = InMemoryValueSharingStore()
    assert store.get("conn_a").enabled is False
    saved = store.set("conn_a", True, ("stations.segment",), "admin")
    assert saved.updated_by == "admin"
    assert saved.shares("stations", "name") and not saved.shares("stations", "segment")
    assert store.get("conn_b").enabled is False
    store.delete("conn_a")
    assert store.get("conn_a").enabled is False


def test_null_and_blank_shares_come_from_statistics_and_small_ones_are_left_out() -> None:
    stats = _stats(["", "Premium", " "], [0.04, 0.4, 0.01], null_frac=0.457)
    assert column_profile(stats) == ColumnProfile(null_share=0.46, blank_share=0.05)
    assert column_profile(_stats(["Premium"], [0.99], null_frac=0.004)) is None


def test_a_counted_column_gives_an_exact_blank_share() -> None:
    counts = [("", 3), (None, 2), *((f"code {index}", 1) for index in range(40))]
    stats = sample_stats("stations", "segment", counts)
    assert column_profile(stats) == ColumnProfile(null_share=0.04, blank_share=0.07)


def test_shares_reach_the_context_without_value_sharing() -> None:
    catalog = apply_profiles(
        apply_known_values(_catalog(), {}),
        {("stations", "segment"): ColumnProfile(null_share=0.46, blank_share=0.05)},
    )
    context = governed_context_from_schema_context(
        SchemaContextResolver(catalog).resolve("Which segment has the most stations?")
    )
    dumped = {item["physical_column_or_path"]: item for item in context.model_dump()["attributes"]}
    assert (dumped["segment"]["null_share"], dumped["segment"]["blank_share"]) == (0.46, 0.05)
    assert "null_share" not in dumped["id"] and "blank_share" not in dumped["id"]
    assert serialize_governed_context(context).count("null_share") == 1
    assert "- segment text — Segment. | null_share: 46% | blank_share: 5%" in (
        render_governed_context(context)
    )


def test_a_text_column_of_lists_names_its_separator() -> None:
    lists = _stats(["Creature", "Artifact,Creature", "Instant"], [0.5, 0.3, 0.2])
    assert column_profile(lists, text=True) == ColumnProfile(0.0, 0.0, ",", 0.3)
    # A separator in few values is punctuation; numbers are never profiled for it.
    assert column_profile(_stats(["a", "b,c"], [0.95, 0.05]), text=True) is None
    assert column_profile(lists) is None


def test_the_separator_share_of_a_large_column_counts_its_histogram() -> None:
    stats = ColumnStats(
        "users",
        "location",
        0.5,
        ["India"],
        [0.1],
        ["Bangalore, India", "Pune, India", "Kerala"],
        n_distinct=100,
    )
    # 0.4 of rows are outside the common values; two of three bounds stand for them.
    profile = column_profile(stats, text=True)
    assert profile is not None and profile.separator == ","
    assert profile.separated_share == round((0.4 * 2 / 3) / 0.5, 2)


def test_free_text_commas_are_not_a_list() -> None:
    sentence = "This is a long sentence, with commas, that reads like a comment on a post."
    assert column_profile(_stats([sentence, sentence + "!"], [0.5, 0.5]), text=True) is None


def test_a_counted_column_gives_an_exact_separator_share() -> None:
    stats = sample_stats("cards", "types", [("Creature", 6), ("Artifact,Creature", 4), (None, 2)])
    assert column_profile(stats, text=True) == ColumnProfile(0.17, 0.0, ",", 0.4)
    catalog = apply_profiles(_catalog(), {("stations", "segment"): ColumnProfile(0, 0, ",", 0.4)})
    context = governed_context_from_schema_context(
        SchemaContextResolver(catalog).resolve("Which segment has the most stations?")
    )
    assert '| separated_by ",": 40% of values' in render_governed_context(context)
