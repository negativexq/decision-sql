# ruff: noqa: E501

from app.semantics.population import (
    GroupSurvival,
    PopulationBehavior,
    PopulationBehaviorAnalyzer,
    PopulationCheckStatus,
    PopulationIntent,
    PopulationIntentClaim,
    check_population_intent,
)

ANALYZER = PopulationBehaviorAnalyzer()


def test_supported_population_shapes() -> None:
    cases = {
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id": PopulationBehavior.PRESERVES_BASE_GROUPS,
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id WHERE c.status='open' GROUP BY p.id": PopulationBehavior.REQUIRES_QUALIFYING_ROWS,
        "SELECT p.id, COUNT(c.id) FILTER (WHERE c.status='open') FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id": PopulationBehavior.PRESERVES_BASE_GROUPS,
        "SELECT customer_id, COUNT(alert_id) FILTER (WHERE status='open') FROM alerts GROUP BY customer_id": PopulationBehavior.CONDITIONALLY_PRESERVES_BASE_GROUPS,
        "SELECT customer_id, COUNT(*) FROM alerts WHERE status='open' GROUP BY customer_id": PopulationBehavior.REQUIRES_QUALIFYING_ROWS,
    }
    for sql, expected in cases.items():
        assert ANALYZER.inspect(sql).behavior is expected


def test_null_rejection_is_fail_closed() -> None:
    safe = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id WHERE c.status='open' GROUP BY p.id"
    )
    assert safe.null_rejecting_relations == ("c",)
    assert safe.group_survival is GroupSurvival.REQUIRES_QUALIFYING_ROWS

    unknown = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id WHERE c.status='open' OR c.status IS NULL GROUP BY p.id"
    )
    assert unknown.null_rejecting_relations == ()


def test_intent_check_never_computes_answerability() -> None:
    diagnostic = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id"
    )
    check = check_population_intent(
        PopulationIntentClaim(mode=PopulationIntent.MATCHING_ONLY), diagnostic
    )
    assert check.status is PopulationCheckStatus.CONTRADICTED
    assert not hasattr(check, "is_answerable")
    assert not hasattr(diagnostic, "should_answer")


def test_hash_is_stable_and_literal_changes_do_not_change_behavior() -> None:
    first = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FILTER (WHERE c.status='open') FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id"
    )
    second = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FILTER (WHERE c.status='closed') FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id"
    )
    assert (
        first.diagnostic_hash
        == ANALYZER.inspect(
            "SELECT p.id, COUNT(c.id) FILTER (WHERE c.status='open') FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id"
        ).diagnostic_hash
    )
    assert first.behavior is second.behavior


def test_predicate_placement_and_join_topology_change_behavior() -> None:
    filtered = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id WHERE c.status='open' GROUP BY p.id"
    )
    on_filtered = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id AND c.status='open' GROUP BY p.id"
    )
    inner = ANALYZER.inspect(
        "SELECT p.id, COUNT(c.id) FROM parent p JOIN child c ON c.parent_id=p.id GROUP BY p.id"
    )
    assert filtered.behavior is PopulationBehavior.REQUIRES_QUALIFYING_ROWS
    assert on_filtered.behavior is PopulationBehavior.PRESERVES_BASE_GROUPS
    assert inner.behavior is PopulationBehavior.REQUIRES_QUALIFYING_ROWS
