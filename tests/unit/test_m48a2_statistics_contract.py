from __future__ import annotations

import json
from pathlib import Path

from benchmark.planner_statistics import PlannerStatisticsLifecycle, lifecycle_sequence

ROOT = Path(__file__).resolve().parents[2]


def test_m48a2_selects_current_state_lifecycle_without_app_dependency() -> None:
    contract = json.loads(
        (ROOT / "benchmark/manifests/m48a2_planner_statistics_contract.json").read_text()
    )
    assert contract["selected_lifecycle"] == PlannerStatisticsLifecycle.ANALYZE_CURRENT_STATE
    assert contract["role_boundary"] == {
        "setup": "admin",
        "planning": "reader",
        "execution": "reader",
    }
    assert contract["reader_analyze_forbidden"] is True


def test_current_state_sequence_analyzes_after_fixture() -> None:
    assert lifecycle_sequence(
        PlannerStatisticsLifecycle.ANALYZE_CURRENT_STATE, has_fixture=True
    ) == [
        "reset",
        "seed",
        "no_analyze_after_seed",
        "apply_fixture",
        "ANALYZE_CURRENT_STATE",
        "commit_state_preparation",
        "reader/runtime_planning",
    ]


def test_statistics_maintenance_is_not_in_request_application() -> None:
    app_files = [path for path in (ROOT / "app").rglob("*.py") if path.is_file()]
    assert all("ANALYZE" not in path.read_text(encoding="utf-8") for path in app_files)


def test_m48a2_truth_and_policy_are_unchanged() -> None:
    contract = json.loads(
        (ROOT / "benchmark/manifests/m48a2_planner_statistics_contract.json").read_text()
    )
    assert contract["evaluation_truth_version"] == "0.2.2-dev"
    assert (
        contract["evaluation_truth_hash"]
        == "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
    )
    assert contract["effective_cost_policy"] == {
        "max_plan_rows": 100000,
        "max_plan_cost": 100000.0,
    }
