"""Generic mutation vocabulary used by the case-specific semantic mutants."""

# ruff: noqa: E501

MUTATION_CATEGORIES = (
    "inner_left_join",
    "remove_predicate",
    "where_filter_scope",
    "wrong_comparison_operator",
    "wrong_literal",
    "avg_vs_sum",
    "count_vs_count_distinct",
    "wrong_aggregation_grain",
    "wrong_metric_operand",
    "ratio_numerator_denominator",
    "rounding_stage",
    "wrong_temporal_anchor",
    "asc_desc",
    "remove_limit",
    "wrong_grouping_dimension",
    "wrong_join_path",
    "set_operation",
    "window_partition",
    "wrong_nested_field",
    "null_default_policy",
    "wrong_population",
)


def supported_categories() -> tuple[str, ...]:
    return MUTATION_CATEGORIES
