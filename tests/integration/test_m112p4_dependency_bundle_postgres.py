import os

import pytest

from evaluation import run_m112p2 as d2run
from evaluation.m112p3_component_isolation import ComponentIsolationHarness
from evaluation.m112p4_dependency_bundle_isolation import BundleArmState, DependencyBundleHarness
from evaluation.run_m112p4 import control_arm, load_controls


def test_postgres_dependency_bundle_controls() -> None:
    if not os.getenv("M112P2_DATABASE_URL") or not os.getenv("M112P2_ADMIN_DATABASE_URL"):
        pytest.skip("isolated diagnostic PostgreSQL URLs are required")
    d2run.recreate_fixture_state()
    d2, _ = d2run.build_harness()
    harness = DependencyBundleHarness(ComponentIsolationHarness(d2))
    controls = {control["control_id"]: control for control in load_controls()}
    result = harness.run_arm(control_arm(controls["B01_PRODUCT_ORDER_ALIAS"]))
    assert result.state is BundleArmState.BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED
    assert result.isolated_bundle == "TOP_LEVEL_ORDER_BY+TOP_LEVEL_PROJECTION"
