from __future__ import annotations

import json
from pathlib import Path

from benchmark.m521_runner import detect_group_survival


def test_detector_requires_structured_population_metadata() -> None:
    result = detect_group_survival(
        "SELECT p.id FROM parents p JOIN children c ON c.parent_id = p.id",
        {"authorized_relationships": []},
    )
    assert result["result"] == "INDETERMINATE"
    assert result["reason_code"] == "POPULATION_REQUIREMENT_NOT_AVAILABLE"


def test_detector_flags_generic_preservation_contradiction() -> None:
    result = detect_group_survival(
        """SELECT p.id, COUNT(c.id)
        FROM parents p
        LEFT JOIN children c ON c.parent_id = p.id
        WHERE c.status = 'late'
        GROUP BY p.id""",
        {
            "population_requirement": "PRESERVE_BASE_ENTITIES",
            "base_entity": "parents",
            "zero_group_policy": "base-entity-preserving",
        },
    )
    assert result["result"] == "SEMANTIC_CONTRADICTION_DETECTED"
    assert result["reason_code"] == "OUTER_JOIN_CHILD_WHERE_ELIMINATION"


def test_matching_only_inner_join_is_not_flagged() -> None:
    result = detect_group_survival(
        "SELECT p.id FROM parents p JOIN children c ON c.parent_id = p.id",
        {
            "population_requirement": "MATCHING_ENTITIES_ONLY",
            "base_entity": "parents",
        },
    )
    assert result["result"] == "PASS"


def test_nested_anti_join_inner_join_is_not_carrier_join() -> None:
    result = detect_group_survival(
        """SELECT p.id FROM parents p
        WHERE NOT EXISTS (
            SELECT 1 FROM children c JOIN tags t ON t.child_id = c.id
            WHERE c.parent_id = p.id
        )""",
        {
            "population_requirement": "PRESERVE_BASE_ENTITIES",
            "base_entity": "parents",
        },
    )
    assert result["result"] == "PASS"


def test_frozen_artifact_counts_and_confusion_matrix() -> None:
    root = Path(__file__).parents[1]
    targets = json.loads(
        (root / "benchmark/audits/m521/m521_group_survival_targets.json").read_text()
    )
    controls = json.loads(
        (root / "benchmark/audits/m521/m521_primary_negative_controls.json").read_text()
    )
    matrix = json.loads(
        (root / "benchmark/audits/m521/m521_target_control_confusion.json").read_text()
    )
    assert targets["count"] == 9
    assert controls["count"] == 5
    assert matrix["hypothetical_extension"]["target"] == {"TP": 4, "FN": 5}
    assert matrix["hypothetical_extension"]["control"] == {"FP": 0, "TN": 5}
