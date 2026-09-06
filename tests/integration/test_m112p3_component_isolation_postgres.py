import os

import pytest

from evaluation import run_m112p2 as d2run
from evaluation.m112p3_component_isolation import (
    ArmInput,
    ArmIsolationState,
    ComponentIsolationHarness,
    ComponentKind,
    IsolationPair,
)


def test_postgres_component_isolation_controls() -> None:
    if not os.getenv("M112P2_DATABASE_URL") or not os.getenv("M112P2_ADMIN_DATABASE_URL"):
        pytest.skip("isolated diagnostic PostgreSQL URLs are required")
    d2run.recreate_fixture_state()
    d2, _ = d2run.build_harness()
    harness = ComponentIsolationHarness(d2)

    def run(control_id: str, candidate: str, reference: str) -> ArmInput:
        return ArmInput(
            case_id=control_id,
            arm="CONTROL",
            pair=IsolationPair(
                pair_id=control_id, candidate_sql=candidate, reference_sql=reference
            ),
        )

    order = harness.run_arm(
        run(
            "ISO_ORDER",
            "SELECT id FROM orders ORDER BY id ASC LIMIT 1",
            "SELECT id FROM orders ORDER BY id DESC LIMIT 1",
        )
    )
    assert order.state is ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
    assert order.isolated_component is ComponentKind.TOP_LEVEL_ORDER_BY

    where = harness.run_arm(
        run(
            "ISO_WHERE",
            "SELECT id FROM orders WHERE status = 'completed'",
            "SELECT id FROM orders WHERE status = 'cancelled'",
        )
    )
    assert where.state is ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
    assert where.isolated_component is ComponentKind.TOP_LEVEL_WHERE

    unsafe = harness.run_arm(
        run(
            "UNSAFE_ALIAS",
            "SELECT id + 1 AS order_id FROM orders ORDER BY order_id LIMIT 1",
            "SELECT id AS id FROM orders ORDER BY id LIMIT 1",
        )
    )
    assert unsafe.state is ArmIsolationState.SUBSTITUTION_NOT_SAFE
