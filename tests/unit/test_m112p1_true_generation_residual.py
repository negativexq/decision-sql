from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "evaluation" / "m112p1_true_generation_residual.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("m112p1_residual", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preexposure_contracts_are_frozen_and_complete() -> None:
    module = _module()
    assert module.protocol()["taxonomy_frozen_before_case_exposure"] is True
    assert module.tier_rulebook()["p1_labels_authoritative"] is False
    assert module.pair_contract()["states"] == list(module.PAIR_STATES)
    assert module.sql_identity_contract()["semantic_equivalence_inferred"] is False
    rule = module.repeated_boundary_rule()
    assert rule["either_arm_unique_support_min"] == 10
    assert rule["both_arm_shared_support_min"] == 5
    assert rule["specific_subreason_support_min"] == 5


def test_literal_preserving_features_capture_required_boundaries() -> None:
    module = _module()
    reference = module._feature(
        "SELECT id FROM t WHERE status = 'COMPLETED' "
        "GROUP BY id HAVING COUNT(*) > 1 ORDER BY id LIMIT 10 OFFSET 0"
    )
    candidate = module._feature(
        "SELECT id, sku FROM t WHERE status = 'completed' "
        "GROUP BY id HAVING COUNT(*) > 2 ORDER BY id LIMIT 10 OFFSET 100"
    )
    assert reference and candidate
    assert {"where", "having", "projection", "offset"} <= module._feature_diff(reference, candidate)
    assert reference["where"] != candidate["where"]
    assert reference["offset"] != candidate["offset"]


def test_join_using_kind_and_order_are_not_collapsed() -> None:
    module = _module()
    using_id = module._feature("SELECT t.id FROM t JOIN x USING(id) ORDER BY t.id, t.name")
    using_name = module._feature("SELECT t.id FROM t LEFT JOIN x USING(name) ORDER BY t.name, t.id")
    assert using_id and using_name
    assert using_id["joins"] != using_name["joins"]
    assert using_id["order"] != using_name["order"]
    assert "joins" in module._feature_diff(using_id, using_name)
    assert "order" in module._feature_diff(using_id, using_name)


def test_window_partition_and_order_are_preserved() -> None:
    module = _module()
    first = module._feature(
        "SELECT SUM(q) OVER (PARTITION BY order_id ORDER BY line_id) FROM lines"
    )
    second = module._feature(
        "SELECT SUM(q) OVER (PARTITION BY customer_id ORDER BY created_at) FROM lines"
    )
    assert first and second
    assert first["windows"] != second["windows"]
    assert "windows" in module._feature_diff(first, second)


def test_positive_controls_cover_tier_and_failure_boundaries() -> None:
    module = _module()
    controls = module.design_controls()["controls"]
    expected = {
        "wrong_filter_literal": "ROW_FILTER_VALUE_SEMANTICS",
        "missing_filter": "ROW_FILTER_VALUE_SEMANTICS",
        "wrong_join_key": "RELATIONAL_COMPOSITION_JOIN",
        "left_vs_inner_outer_required": "NULL_ZERO_INCLUSION_SEMANTICS",
        "wrong_group_by": "GROUPING_GRAIN_SEMANTICS",
        "sum_vs_avg": "AGGREGATION_SEMANTICS",
        "wrong_formula_denominator": "FORMULA_BUSINESS_SEMANTICS",
        "wrong_window_partition": "WINDOW_SEMANTICS",
        "wrong_window_order": "WINDOW_SEMANTICS",
        "wrong_rank_direction": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
        "offset_change": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
        "having_threshold": "GROUP_FILTER_HAVING_SEMANTICS",
        "literal_case": "ROW_FILTER_VALUE_SEMANTICS",
        "full_projection_entitlement": "PROJECTION_OUTPUT_SEMANTICS",
        "core_projection_mask": "P0_TIER_MASKED",
        "structural_divergence": "UNRESOLVED",
        "multi_failure": "MULTIPLE_INDEPENDENT_FAILURES",
        "m1_rejection": "M1_REJECTION",
    }
    assert {name: controls[name]["expected"] for name in expected} == expected


def test_positive_filter_and_projection_rules_respect_core_mask() -> None:
    module = _module()
    reference = module._feature("SELECT id, name FROM products WHERE category = 'Garden'")
    extra_projection = module._feature(
        "SELECT id, name, sku FROM products WHERE category = 'Garden'"
    )
    assert reference and extra_projection
    assert (
        module._positive_failure(
            "Which catalog items belong to Garden?", reference, extra_projection, "CORE"
        )
        == []
    )
    failures = module._positive_failure(
        "Return id and name only.", reference, extra_projection, "FULL"
    )
    assert any(item["family"] == "PROJECTION_OUTPUT_SEMANTICS" for item in failures)

    wrong_filter = module._feature("SELECT id, name FROM products WHERE category = 'Books'")
    assert wrong_filter
    assert any(
        item["family"] == "ROW_FILTER_VALUE_SEMANTICS"
        for item in module._positive_failure(
            "Which catalog items belong to Garden?", reference, wrong_filter, "CORE"
        )
    )


def test_pair_states_and_m1_rejection_are_deterministic() -> None:
    module = _module()
    rejected = module._m1_failure({"status": "M1_REJECTED", "m1_rejection_code": "M1_REJECT_PARSE"})
    assert rejected["family"] == "M1_REJECTION"
    assert rejected["reason_code"] == "M1_REJECT_PARSE"
    off = {
        "sql_identity_state": "PARSE_NORMALIZED_IDENTICAL",
        "candidate_identity_hash": "off-and-on-same",
        "proven_failure_families": ["ROW_FILTER_VALUE_SEMANTICS"],
    }
    on = {
        "sql_identity_state": "PARSE_NORMALIZED_IDENTICAL",
        "candidate_identity_hash": "off-and-on-same",
        "proven_failure_families": ["ROW_FILTER_VALUE_SEMANTICS"],
    }
    assert module._pair_state(off, on) == "STABLE_CANDIDATE_SHARED_FAILURE"
    different = {
        "sql_identity_state": "PARSE_NORMALIZED_DIFFERENT",
        "candidate_identity_hash": "on-is-different",
        "proven_failure_families": ["RELATIONAL_COMPOSITION_JOIN"],
    }
    assert module._pair_state(off, different) == "TREATMENT_SENSITIVE_DIFFERENT_FAILURE_BOUNDARIES"


def test_sql_identity_distinguishes_same_and_material_structural_change() -> None:
    module = _module()
    first = module._feature("SELECT id FROM t WHERE status = 'COMPLETED'")
    same = module._feature("SELECT id FROM t WHERE status = 'COMPLETED'")
    changed = module._feature("SELECT id FROM t WHERE status = 'completed'")
    assert first and same and changed
    assert module._identity(first, same) == "PARSE_NORMALIZED_IDENTICAL"
    assert module._identity(first, changed) == "PARSE_NORMALIZED_DIFFERENT"


def test_frozen_population_output_has_two_tiers_and_no_core_projection_failure() -> None:
    rows = [
        json.loads(line)
        for line in (ROOT / "evaluation/fixtures/m112p1_case_attribution.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert len(rows) == 53
    assert len({row["case_id"] for row in rows}) == 53
    assert {row["p0_tier"] for row in rows} == {"FULL", "CORE"}
    assert sum(row["p0_tier"] == "FULL" for row in rows) == 23
    assert sum(row["p0_tier"] == "CORE" for row in rows) == 30
    assert all(
        "PROJECTION_OUTPUT_SEMANTICS" not in row["OFF_proven_failure_families"]
        and "PROJECTION_OUTPUT_SEMANTICS" not in row["ON_proven_failure_families"]
        for row in rows
        if row["p0_tier"] == "CORE"
    )


def test_pair_state_summary_covers_entire_eligible_population() -> None:
    summary = json.loads(
        (ROOT / "evaluation/fixtures/m112p1_pair_state_summary.json").read_text(encoding="utf-8")
    )
    assert set(summary["counts"]) == set(_module().PAIR_STATES)
    assert summary["sum"] == 53
