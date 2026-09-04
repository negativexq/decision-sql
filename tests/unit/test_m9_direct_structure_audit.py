from evaluation.m9_direct_structure_audit import (
    build_reconstruction,
    run_offline_audit,
    stable_hash,
    structure_deltas,
    structure_signature,
    validate_reconstruction,
)


def test_reconstructs_frozen_m7_m8_direct_population() -> None:
    cases = build_reconstruction()
    validation = validate_reconstruction(cases)

    assert validation["passed"] is True
    assert validation["case_count"] == 120
    assert validation["correct"] == 75
    assert validation["incorrect"] == 45
    assert validation["source_counts"] == {
        "M7_DIRECT": 100,
        "M8_COUNTERFACTUAL_DIRECT": 20,
    }


def test_reconstruction_hash_is_deterministic_and_split_disjoint() -> None:
    first = build_reconstruction()
    second = build_reconstruction()

    assert stable_hash(first) == stable_hash(second)
    assert {case.case_id for case in first if case.split == "dev"}.isdisjoint(
        {case.case_id for case in first if case.split == "holdout"}
    )


def test_structure_signature_is_bounded_and_deterministic() -> None:
    sql = """
    WITH totals AS (
      SELECT customer_id, SUM(total_amount) AS total
      FROM orders
      GROUP BY customer_id
    )
    SELECT customer_id, total, RANK() OVER (ORDER BY total DESC) AS position
    FROM totals
    ORDER BY position
    LIMIT 5
    """

    first = structure_signature(sql)
    second = structure_signature(sql)

    assert first == second
    assert first.cte_count == 1
    assert first.window_count == 1
    assert first.has_group_by is True
    assert first.has_limit is True


def test_structure_deltas_compare_against_all_reference_variants() -> None:
    generated = structure_signature("SELECT category, SUM(amount) FROM sales GROUP BY category")
    reference_one = structure_signature("SELECT category, SUM(amount) FROM sales GROUP BY category")
    reference_two = structure_signature(
        "SELECT category, SUM(amount) FROM sales GROUP BY category ORDER BY category"
    )

    deltas = structure_deltas(generated, (reference_one, reference_two))

    assert deltas["group_by_presence_mismatch"] is False
    assert deltas["projection_count_mismatch"] is False
    assert deltas["ranges"]["order_by_count"] == {"min": 0, "max": 1}


def test_failure_adjudication_and_control_sample_are_finite() -> None:
    audit = run_offline_audit()

    assert len(audit["adjudications"]) == 45
    assert len(audit["controls"]) == 20
    assert audit["summary"]["scope_distribution"] == {
        "STRUCTURAL_COMPOSITION": 35,
        "SEMANTIC_GROUNDING": 3,
        "POLICY_OR_EXECUTION": 7,
    }
    assert audit["summary"]["bounded_structural_count"] == 4
