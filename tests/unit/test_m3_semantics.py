import pytest
from sqlglot import parse_one

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.governed_metric_grounding import (
    GovernedMetricGroundingDTO,
    grounding_to_request,
)
from app.semantics.catalog import build_m3_catalog
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.models import SemanticCatalogError
from app.semantics.requests import MetricRequest
from evaluation.m3_benchmark import (
    BenchmarkConstructionError,
    M3Target,
    benchmark_hash,
    build_benchmark_cases,
    build_targets,
)


def test_m3_catalog_validates_against_actual_demo_schema() -> None:
    catalog = build_m3_catalog(build_default_catalog(Base.metadata))
    assert len(catalog.entities) == 8
    assert len(catalog.metrics) == 10
    assert len(catalog.measures) == 13


def test_metric_catalog_rejects_unknown_physical_reference() -> None:
    catalog = build_m3_catalog()
    bad_measure = catalog.measures[0].model_copy(update={"physical_column": "not_a_column"})
    bad = catalog.model_copy(update={"measures": (bad_measure, *catalog.measures[1:])})
    with pytest.raises(SemanticCatalogError, match="invalid measure column"):
        bad.validate_against_schema(build_default_catalog(Base.metadata))


def test_grounding_adapter_is_exact_and_does_not_repair() -> None:
    catalog = build_m3_catalog()
    request = grounding_to_request(
        GovernedMetricGroundingDTO(metric_name=" Completed_Revenue ", dimensions=(" CUSTOMER ",)),
        catalog,
    )
    assert request == MetricRequest(metric_name="completed_revenue", dimensions=("customer",))
    with pytest.raises(SemanticCatalogError):
        grounding_to_request(
            GovernedMetricGroundingDTO(metric_name="revenue", dimensions=()), catalog
        )


def test_public_glossary_contains_no_physical_metric_implementation() -> None:
    from app.semantics.catalog import public_metric_glossary

    glossary = public_metric_glossary(build_m3_catalog())
    assert "orders.total_amount" not in glossary
    assert "JOIN" not in glossary
    assert "COUNT_DISTINCT" not in glossary


def test_compiler_is_deterministic_and_owns_output_aliases() -> None:
    compiler = MetricCompiler(build_m3_catalog())
    request = MetricRequest(metric_name="refund_to_revenue_rate", dimensions=("customer",))
    first = compiler.compile_metric(request)
    second = compiler.compile_metric(request)
    assert first.sql == second.sql
    assert "metric_value" in first.sql
    assert "numerator" in first.sql
    parse_one(first.sql, read="postgres")


def test_ratio_compiler_preserves_multiple_dimension_ords() -> None:
    compiler = MetricCompiler(build_m3_catalog())
    result = compiler.compile_metric(
        MetricRequest(
            metric_name="payment_success_rate",
            dimensions=("customer", "order_currency"),
        )
    )
    assert not isinstance(result, MetricCompilationFailure)
    assert "dimension_1" in result.sql
    assert "numerator.dimension_1 = denominator.dimension_1" in result.sql


def test_ratio_compiler_preaggregates_components_independently() -> None:
    compiler = MetricCompiler(build_m3_catalog())
    result = compiler.compile_metric(
        MetricRequest(metric_name="refund_to_revenue_rate", dimensions=("customer",))
    )
    assert not isinstance(result, MetricCompilationFailure)
    denominator = result.sql.split("denominator AS (", maxsplit=1)[1]
    assert "refunds" not in denominator
    assert "FULL OUTER JOIN denominator" in result.sql


def test_invalid_dimension_and_unsafe_path_are_rejected() -> None:
    compiler = MetricCompiler(build_m3_catalog())
    invalid = compiler.compile_metric(
        MetricRequest(metric_name="completed_revenue", dimensions=("product_category",))
    )
    assert isinstance(invalid, MetricCompilationFailure)
    assert invalid.code == "INVALID_METRIC_DIMENSION"
    safe = compiler.compile_metric(
        MetricRequest(
            metric_name="average_items_per_completed_order", dimensions=("product_category",)
        )
    )
    assert not isinstance(safe, MetricCompilationFailure)


def test_benchmark_is_frozen_shape_without_temporal_or_value_questions() -> None:
    targets = build_targets()
    cases = build_benchmark_cases(targets)
    assert len(targets) == 32
    assert len([case for case in cases if case.split == "dev"]) == 48
    assert len([case for case in cases if case.split == "holdout"]) == 32
    assert (
        len(
            {case.target_id for case in cases if case.split == "holdout"}
            - {case.target_id for case in cases if case.split == "dev"}
        )
        >= 1
    )


def test_benchmark_is_deterministic_and_preserves_frozen_hash() -> None:
    targets = build_targets()
    first = build_benchmark_cases(targets)
    second = build_benchmark_cases(targets)

    assert first == second
    assert len({case.case_id for case in first}) == len(first)
    assert benchmark_hash(targets, first) == (
        "bae37fa9c4850954dbae1a58b6c26c5b9cb1855b7ab6b6acd562845ee4bbb0b8"
    )


def test_benchmark_fails_fast_when_dev_constraints_have_no_candidates() -> None:
    target = build_targets()[27]
    holdout_only_targets = (M3Target.model_validate(target.model_dump()),)

    with pytest.raises(BenchmarkConstructionError, match="candidate_cycle_size=0"):
        build_benchmark_cases(holdout_only_targets)
