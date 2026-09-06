import pytest

from evaluation.m112p3_component_isolation import (
    ComponentIsolationError,
    ComponentKind,
    parse_postgres,
)
from evaluation.m112p4_dependency_bundle_isolation import (
    BundleArmState,
    DependencyReason,
    build_bundle_hybrid,
    canonical_bundle_id,
    extract_component_dependencies,
    minimal_dependency_closure,
    validate_bundle_non_target_invariance,
)


def test_alias_dependency_is_explicit_and_cooccurrence_is_not() -> None:
    candidate = parse_postgres("SELECT id AS key FROM products ORDER BY key")
    reference = parse_postgres("SELECT name AS label FROM products ORDER BY label")
    changed = (ComponentKind.TOP_LEVEL_PROJECTION, ComponentKind.TOP_LEVEL_ORDER_BY)
    edges = extract_component_dependencies(candidate, reference, changed)
    assert len(edges) == 2
    assert {edge.reason for edge in edges} == {DependencyReason.ALIAS_REFERENCE_DEPENDENCY}

    cooccurrence = extract_component_dependencies(
        parse_postgres("SELECT id AS key FROM products ORDER BY id"),
        parse_postgres("SELECT name AS key FROM products ORDER BY id DESC"),
        changed,
    )
    assert cooccurrence == ()


def test_minimal_closure_is_two_or_abstains_above_two() -> None:
    candidate = parse_postgres(
        "SELECT id AS key, category AS category_key FROM products "
        "GROUP BY key, category_key ORDER BY key"
    )
    reference = parse_postgres(
        "SELECT name AS label, category AS category_key FROM products "
        "GROUP BY label, category_key ORDER BY label"
    )
    changed = (
        ComponentKind.TOP_LEVEL_PROJECTION,
        ComponentKind.TOP_LEVEL_GROUP_BY,
        ComponentKind.TOP_LEVEL_ORDER_BY,
    )
    edges = extract_component_dependencies(candidate, reference, changed)
    closure = minimal_dependency_closure(ComponentKind.TOP_LEVEL_PROJECTION, changed, edges)
    assert closure.too_large
    assert len(closure.components) == 3


def test_bundle_identity_and_bidirectional_hybrids_are_deterministic() -> None:
    components = (ComponentKind.TOP_LEVEL_PROJECTION, ComponentKind.TOP_LEVEL_ORDER_BY)
    assert canonical_bundle_id(components) == "TOP_LEVEL_ORDER_BY+TOP_LEVEL_PROJECTION"
    candidate = "SELECT id AS key FROM products ORDER BY key"
    reference = "SELECT name AS label FROM products ORDER BY label DESC"
    repair = build_bundle_hybrid(candidate, reference, components, "REPAIR")
    transfer = build_bundle_hybrid(reference, candidate, components, "TRANSFER")
    assert repair.sql_hash != transfer.sql_hash
    assert "name AS label" in repair.sql
    assert "id AS key" in transfer.sql
    assert validate_bundle_non_target_invariance(candidate, repair.sql, components)


def test_p0_blocks_projection_bundle() -> None:
    with pytest.raises(ComponentIsolationError) as error:
        build_bundle_hybrid(
            "SELECT id AS key FROM products ORDER BY key",
            "SELECT name AS label FROM products ORDER BY label",
            (ComponentKind.TOP_LEVEL_PROJECTION, ComponentKind.TOP_LEVEL_ORDER_BY),
            "REPAIR",
            p0_projection_entitled=False,
        )
    assert error.value.validation.value == "P0_ENTITLEMENT_BLOCKED"
    assert BundleArmState.P0_ENTITLEMENT_BLOCKED.value == "P0_ENTITLEMENT_BLOCKED"
