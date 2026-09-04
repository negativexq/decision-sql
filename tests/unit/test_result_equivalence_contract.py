"""Safety tests for the provider-free M9.2 candidate comparator."""

from evaluation.result_equivalence_contract import (
    CONTRACT_VERSION,
    BoundResult,
    DuplicatePolicy,
    ExtraOutputPolicy,
    Outcome,
    ReasonCode,
    ResultEquivalenceContract,
    ResultSlot,
    RowOrderPolicy,
    ScalarOrTabular,
    SlotKind,
    compare_contract,
    validate_contract,
)


def contract(
    *, optional: tuple[str, ...] = (), order: RowOrderPolicy = RowOrderPolicy.ORDER_INSENSITIVE
) -> ResultEquivalenceContract:
    slots = (
        ResultSlot("region", SlotKind.DIMENSION, True, "dimension", "region"),
        ResultSlot("revenue", SlotKind.MEASURE, True, "measure", "revenue"),
    )
    slots += tuple(
        ResultSlot(name, SlotKind.AUXILIARY, False, "optional", name) for name in optional
    )
    return ResultEquivalenceContract(
        CONTRACT_VERSION,
        slots,
        ("region",),
        ("revenue",),
        ScalarOrTabular.TABULAR,
        order,
        DuplicatePolicy.PRESERVE,
        ExtraOutputPolicy.ALLOW_DECLARED_OPTIONAL_ONLY
        if optional
        else ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
    )


def result(
    columns: tuple[str, ...],
    bindings: tuple[str | None, ...],
    rows: tuple[tuple[object, ...], ...],
    scalar: ScalarOrTabular = ScalarOrTabular.TABULAR,
) -> BoundResult:
    return BoundResult(columns, bindings, rows, scalar)


REF = result(("region", "revenue"), ("region", "revenue"), (("North", 100), ("South", 80)))


def test_contract_validation_rejects_duplicate_and_unknown_identity() -> None:
    invalid = ResultEquivalenceContract(
        CONTRACT_VERSION,
        (
            ResultSlot("x", SlotKind.DIMENSION, True, "", "x"),
            ResultSlot("x", SlotKind.MEASURE, False, "", "x"),
        ),
        ("unknown",),
        (),
        ScalarOrTabular.TABULAR,
        RowOrderPolicy.ORDER_INSENSITIVE,
        DuplicatePolicy.PRESERVE,
        ExtraOutputPolicy.ALLOW_DECLARED_OPTIONAL_ONLY,
    )
    assert validate_contract(invalid)


def test_declared_optional_extra_is_accepted_without_row_repair() -> None:
    generated = result(
        ("region", "revenue", "region_id"),
        ("region", "revenue", "region_id"),
        (("North", 100, 1), ("South", 80, 2)),
    )
    compared = compare_contract(generated, (REF,), contract(optional=("region_id",)))
    assert compared.outcome is Outcome.CONTRACT_EQUIVALENT
    assert compared.reason is ReasonCode.DECLARED_OPTIONAL_EXTRA_IGNORED


def test_undeclared_extra_and_missing_required_are_rejected() -> None:
    extra = result(
        ("region", "revenue", "manager"),
        ("region", "revenue", "manager"),
        (("North", 100, "A"), ("South", 80, "B")),
    )
    missing = result(("revenue",), ("revenue",), ((100,), (80,)))
    assert compare_contract(extra, (REF,), contract()).reason is ReasonCode.UNDECLARED_EXTRA_OUTPUT
    assert compare_contract(missing, (REF,), contract()).reason is ReasonCode.MISSING_REQUIRED_SLOT


def test_wrong_values_duplicates_and_grain_are_not_normalized() -> None:
    wrong = result(("region", "revenue"), ("region", "revenue"), (("North", 101), ("South", 80)))
    duplicate = result(
        ("region", "revenue"),
        ("region", "revenue"),
        (("North", 100), ("North", 100), ("South", 80)),
    )
    grain = result(
        ("region", "revenue", "rep"),
        ("region", "revenue", "rep"),
        (("North", 60, "A"), ("North", 40, "B"), ("South", 80, "C")),
    )
    assert compare_contract(wrong, (REF,), contract()).reason is ReasonCode.WRONG_REQUIRED_VALUE
    assert (
        compare_contract(duplicate, (REF,), contract()).reason
        is ReasonCode.ROW_CARDINALITY_MISMATCH
    )
    assert compare_contract(grain, (REF,), contract()).reason is ReasonCode.UNDECLARED_EXTRA_OUTPUT


def test_semantic_bindings_allow_alias_and_column_order_but_not_slot_swaps() -> None:
    reordered = result(("total", "area"), ("revenue", "region"), ((100, "North"), (80, "South")))
    swapped = result(("area", "total"), ("revenue", "region"), (("North", 100), ("South", 80)))
    assert compare_contract(reordered, (REF,), contract()).outcome is Outcome.STRICT_EQUIVALENT
    assert compare_contract(swapped, (REF,), contract()).reason is ReasonCode.WRONG_REQUIRED_VALUE


def test_order_policy_and_multiple_reference_variants() -> None:
    reversed_rows = result(
        ("region", "revenue"), ("region", "revenue"), (("South", 80), ("North", 100))
    )
    assert compare_contract(reversed_rows, (REF,), contract()).outcome is Outcome.STRICT_EQUIVALENT
    ordered = compare_contract(reversed_rows, (REF,), contract(order=RowOrderPolicy.ORDER_REQUIRED))
    assert ordered.reason is ReasonCode.ORDER_MISMATCH
    assert (
        compare_contract(
            reversed_rows, (REF, reversed_rows), contract(order=RowOrderPolicy.ORDER_REQUIRED)
        ).outcome
        is Outcome.STRICT_EQUIVALENT
    )


def test_no_reference_and_ambiguous_bindings_are_safe_failures() -> None:
    assert compare_contract(REF, (), contract()).outcome is Outcome.UNDETERMINED
    ambiguous = result(("a", "b"), ("region", "region"), (("North", 100),))
    assert compare_contract(ambiguous, (REF,), contract()).outcome is Outcome.CONTRACT_INVALID
