from benchmark.context import load_authority
from benchmark.models import ResultContract, compare_rows
from benchmark.validator import (
    load_pilot,
    mutant_metadata_audit,
    semantic_provenance_audit,
    validate_non_answerable,
    validate_structure,
)


def test_m34_2_semantic_provenance_and_distribution_are_complete() -> None:
    audit = semantic_provenance_audit()
    assert audit["passed"]
    assert audit["ordered_cases"] == 3
    assert audit["unordered_cases"] == 17
    assert validate_structure()["passed"]


def test_m34_2_unordered_and_ordered_comparators_are_distinct() -> None:
    unordered = ResultContract(column_count=1, row_order=False)
    ordered = ResultContract(column_count=1, row_order=True)
    assert compare_rows([(1,), (2,), (2,)], [(2,), (1,), (2,)], unordered)[0]
    assert not compare_rows([(1,), (2,)], [(2,), (1,)], ordered)[0]
    assert not compare_rows([(1,), (2,)], [(1,), (2,), (2,)], unordered)[0]


def test_m34_2_active_mutants_have_valid_metadata() -> None:
    result = mutant_metadata_audit()
    assert result["passed"]
    assert result["authored"] >= 60
    assert result["invalid_count"] == 0
    mutants = [
        mutant for _case, truth in load_pilot() for mutant in truth.get("semantic_mutants", [])
    ]
    assert all(mutant["status"] == "VALID" for mutant in mutants)
    assert all(mutant["target_component"] and mutant["semantic_rationale"] for mutant in mutants)


def test_m34_2_authority_attributes_and_denied_relations_are_coherent() -> None:
    fleet = load_authority("fleet_ops")
    support = load_authority("support_ops")
    fleet_attrs = {item["attribute_id"] for item in fleet["attributes"]}
    support_attrs = {item["attribute_id"] for item in support["attributes"]}
    assert any(item.endswith(":fuel_events:liters") for item in fleet_attrs)
    assert any(item.endswith(":maintenance_events:performed_at") for item in fleet_attrs)
    assert any(item.endswith(":subscriptions:starts_on") for item in support_attrs)
    assert any(item.endswith(":incidents:severity") for item in support_attrs)
    denied = {
        item["relationship_id"]: item for item in support["relationships"] if not item["authorized"]
    }
    replacement = next(
        value for key, value in denied.items() if key.endswith("tempting_ticket_incident_id")
    )
    assert replacement["left_attribute"].endswith(":support_tickets:ticket_id")
    assert replacement["right_attribute"].endswith(":incidents:incident_id")


def test_m34_2_replaced_support_case_and_invalid_ids_are_absent() -> None:
    rows = {case["case_id"]: (case, truth) for case, truth in load_pilot()}
    case, truth = rows["support_08"]
    assert case["task_type"] == "AUTHORITY_BLOCKED"
    assert truth["required_authority"] == ["relationship:support_ops:tempting_ticket_incident_id"]
    active_ids = {
        mutant["mutant_id"]
        for _case, row_truth in rows.values()
        for mutant in row_truth.get("semantic_mutants", [])
    }
    assert "m16_no_tiebreak" not in active_ids
    assert "m26_no_date" not in active_ids
    assert "m25_global_avg" not in active_ids


def test_m34_2_non_answerable_governance_regression_is_clean() -> None:
    result = validate_non_answerable()
    assert result["passed"]
    assert result["results"]["support_08"]["valid"]
