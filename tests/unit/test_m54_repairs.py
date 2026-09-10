"""Model-free regression checks for the M54 semantic repairs."""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.semantics.grain import GrainDiagnosticCode, GrainSafetyValidator
from benchmark import m46a_audit, m51a_authoring

ROOT = Path(__file__).resolve().parents[2]
TRUTH = ROOT / "benchmark" / "ground_truth" / "m51_expansion"
CASES = ROOT / "benchmark" / "cases" / "m51_expansion"


def _truth(case_id: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((TRUTH / f"{case_id}.json").read_text(encoding="utf-8")))


def _case(case_id: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((CASES / f"{case_id}.json").read_text(encoding="utf-8")))


def test_population_repairs_preserve_base_entities_and_eventless_counterfactuals() -> None:
    for case_id, base_entity, child_entity in (
        ("procurement_14", "po_lines", "receipts"),
        ("insurance_11", "claims", "claim_events"),
    ):
        truth = _truth(case_id)
        target = truth["semantic_target"]
        assert target["population"] == "base-entity-preserving"
        assert base_entity in truth["required_context_facts"][0]
        assert child_entity in " ".join(truth["required_context_facts"])
        assert any("without" in fixture["purpose"] for fixture in truth["counterfactual_fixtures"])
        assert "LEFT JOIN" in truth["reference_implementation_a"]["sql"]
        assert "FROM " + base_entity in truth["reference_implementation_a"]["sql"]


def test_governed_status_repairs_are_answerable_and_discriminating() -> None:
    assert _case("telecom_14")["task_type"] == "ANSWERABLE"
    assert _case("marketplace_09")["task_type"] == "ANSWERABLE"
    assert len(_truth("telecom_14")["counterfactual_fixtures"]) >= 2
    assert len(_truth("marketplace_09")["counterfactual_fixtures"]) >= 2
    assert "active" in _truth("telecom_14")["reference_implementation_a"]["sql"]
    assert "active" in _truth("marketplace_09")["reference_implementation_a"]["sql"]


def test_procurement_duplicate_approval_mutant_and_source_are_preserved() -> None:
    truth = _truth("procurement_05")
    mutant = next(
        item
        for item in truth["semantic_mutants"]
        if item["mutant_id"] == "m54_procurement_05_duplicate_approval"
    )
    assert "JOIN approvals" in mutant["sql"]
    assert "SUM(r.estimated_amount)" in mutant["sql"]
    generated_truth = next(
        generated_truth
        for generated_case, generated_truth in m51a_authoring.new_cases()
        if generated_case["case_id"] == "procurement_05"
    )
    assert any(
        item["mutant_id"] == "m54_procurement_05_duplicate_approval"
        for item in generated_truth["semantic_mutants"]
    )


def test_marketplace_gmv_uses_governed_metric_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(m46a_audit, "DATABASES", ["marketplace_ops"])
    truth = [_truth("marketplace_04")]
    catalogs, _ = m46a_audit._build_catalogs(truth)
    catalog = catalogs["marketplace_ops"]
    assert any(
        metric.metric_id == "metric:marketplace_ops:gross_merchandise_value"
        and metric.expression_signature == "order_lines.quantity * listings.unit_price"
        for metric in catalog.derived_measures
    )
    validator = GrainSafetyValidator(catalog)
    assert (
        validator.validate(
            "SELECT l.seller_id, SUM(ol.quantity * l.unit_price) "
            "FROM listings l JOIN order_lines ol ON ol.listing_id=l.listing_id "
            "GROUP BY l.seller_id"
        ).code
        is GrainDiagnosticCode.PASS
    )
    assert (
        validator.validate(
            "SELECT l.seller_id, SUM(l.unit_price) "
            "FROM listings l JOIN order_lines ol ON ol.listing_id=l.listing_id "
            "GROUP BY l.seller_id"
        ).code
        is GrainDiagnosticCode.PARENT_MEASURE_FANOUT
    )
    assert (
        validator.validate(
            "SELECT l.seller_id, SUM(l.unit_price * 2) "
            "FROM listings l JOIN order_lines ol ON ol.listing_id=l.listing_id "
            "GROUP BY l.seller_id"
        ).code
        is GrainDiagnosticCode.PARENT_MEASURE_FANOUT
    )
