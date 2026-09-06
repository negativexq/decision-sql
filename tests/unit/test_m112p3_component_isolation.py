import pytest

from evaluation.m112p3_component_isolation import (
    ArmIsolationState,
    ComponentIsolationError,
    ComponentKind,
    SubstitutionDirection,
    SubstitutionValidation,
    aggregate_pair,
    build_hybrid,
    changed_components,
    component_fingerprint,
    parse_postgres,
    validate_non_target_invariance,
)


def test_changed_components_and_order_hybrid_preserve_non_targets() -> None:
    candidate = "SELECT id FROM orders WHERE status = 'completed' ORDER BY id ASC LIMIT 1"
    reference = "SELECT id FROM orders WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
    left = parse_postgres(candidate)
    right = parse_postgres(reference)
    assert changed_components(left, right) == (ComponentKind.TOP_LEVEL_ORDER_BY,)
    hybrid = build_hybrid(
        candidate, reference, ComponentKind.TOP_LEVEL_ORDER_BY, SubstitutionDirection.REPAIR
    )
    assert validate_non_target_invariance(candidate, hybrid.sql, ComponentKind.TOP_LEVEL_ORDER_BY)
    assert "LIMIT 1" in hybrid.sql
    assert "status = 'completed'" in hybrid.sql


def test_repair_and_transfer_hybrids_are_opposite_directions() -> None:
    candidate = "SELECT id FROM orders WHERE status = 'completed'"
    reference = "SELECT id FROM orders WHERE status = 'cancelled'"
    repair = build_hybrid(
        candidate, reference, ComponentKind.TOP_LEVEL_WHERE, SubstitutionDirection.REPAIR
    )
    transfer = build_hybrid(
        reference, candidate, ComponentKind.TOP_LEVEL_WHERE, SubstitutionDirection.TRANSFER
    )
    assert "cancelled" in repair.sql
    assert "completed" in transfer.sql


def test_offset_join_using_literal_case_and_window_frame_are_fingerprinted_exactly() -> None:
    offset_a = parse_postgres("SELECT id FROM orders ORDER BY id LIMIT 4 OFFSET 0")
    offset_b = parse_postgres("SELECT id FROM orders ORDER BY id LIMIT 4 OFFSET 100")
    assert component_fingerprint(offset_a, ComponentKind.TOP_LEVEL_OFFSET) != component_fingerprint(
        offset_b, ComponentKind.TOP_LEVEL_OFFSET
    )
    join_a = parse_postgres("SELECT o.id FROM orders o JOIN customers c USING(id)")
    join_b = parse_postgres("SELECT o.id FROM orders o JOIN customers c USING(name)")
    assert component_fingerprint(
        join_a, ComponentKind.RELATIONAL_FROM_JOIN_BLOCK
    ) != component_fingerprint(join_b, ComponentKind.RELATIONAL_FROM_JOIN_BLOCK)
    assert "COMPLETED" in parse_postgres("SELECT * FROM orders WHERE status = 'COMPLETED'").sql()
    window_a = parse_postgres(
        "SELECT id, SUM(unit_price) OVER (ORDER BY id ROWS BETWEEN "
        "1 PRECEDING AND CURRENT ROW) AS x FROM products"
    )
    window_b = parse_postgres(
        "SELECT id, SUM(unit_price) OVER (ORDER BY id ROWS BETWEEN "
        "2 PRECEDING AND CURRENT ROW) AS x FROM products"
    )
    assert component_fingerprint(
        window_a, ComponentKind.WINDOW_EXPRESSION_SLOT
    ) != component_fingerprint(window_b, ComponentKind.WINDOW_EXPRESSION_SLOT)


def test_non_target_mutation_is_rejected() -> None:
    assert not validate_non_target_invariance(
        "SELECT id FROM orders WHERE status = 'completed' ORDER BY id ASC",
        "SELECT id FROM orders WHERE status = 'cancelled' ORDER BY id DESC",
        ComponentKind.TOP_LEVEL_ORDER_BY,
    )


def test_alias_dependency_and_p0_projection_are_not_safe() -> None:
    with pytest.raises(ComponentIsolationError) as alias_error:
        build_hybrid(
            "SELECT id AS order_id FROM orders ORDER BY order_id LIMIT 1",
            "SELECT id AS id FROM orders ORDER BY id LIMIT 1",
            ComponentKind.TOP_LEVEL_PROJECTION,
            SubstitutionDirection.REPAIR,
        )
    assert alias_error.value.validation is SubstitutionValidation.SUBSTITUTION_NOT_SAFE
    with pytest.raises(ComponentIsolationError) as p0_error:
        build_hybrid(
            "SELECT id FROM products",
            "SELECT name FROM products",
            ComponentKind.TOP_LEVEL_PROJECTION,
            SubstitutionDirection.REPAIR,
            p0_projection_entitled=False,
        )
    assert p0_error.value.validation is SubstitutionValidation.P0_ENTITLEMENT_BLOCKED


def test_pair_aggregation_does_not_choose_disagreeing_arms() -> None:
    # Pair aggregation is exercised with representative frozen result models.
    from evaluation.m112p3_component_isolation import ArmIsolationResult

    def result(name: str, component: ComponentKind) -> ArmIsolationResult:
        return ArmIsolationResult(
            case_id="case",
            arm=name,
            p0_tier="FULL",
            baseline_candidate_hash="c",
            baseline_reference_hash="r",
            baseline_witness_fixture_ids=("f",),
            changed_components=(component,),
            supported_components=(component,),
            trials=(),
            state=ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED,
            isolated_component=component,
        )

    pair = aggregate_pair(
        result("OFF", ComponentKind.TOP_LEVEL_WHERE),
        result("ON", ComponentKind.TOP_LEVEL_LIMIT),
        (),
    )
    assert pair.state.value == "PAIR_COMPONENT_DISAGREEMENT"
