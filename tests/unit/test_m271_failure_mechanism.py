import json
from pathlib import Path

from sqlglot import parse_one

from evaluation.m27_failure_mechanism import (
    RootCause,
    _column_alignment,
    _primary_cause,
    _query_facts,
    build_audit,
    load_persisted_models,
)


def _row(gold_sql: str, generated_sql: str, category: str = "top_k") -> dict[str, object]:
    return {
        "id": "test",
        "category": category,
        "question": "Which products by quantity?",
        "gold_sql": gold_sql,
        "generated_sql": generated_sql,
        "result_equivalent": False,
        "failure_stage": None,
        "equivalence_diagnostic": None,
        "forensic_cause": None,
        "context_visibility": {
            "gold_tables_visible": True,
            "gold_columns_visible": True,
            "gold_relationships_visible": True,
        },
    }


def test_order_topk_does_not_override_wrong_measure() -> None:
    row = _row(
        "SELECT product_id, SUM(quantity) FROM order_items "
        "GROUP BY product_id ORDER BY SUM(quantity) DESC LIMIT 5",
        "SELECT product_id, SUM(unit_price) FROM order_items "
        "GROUP BY product_id ORDER BY SUM(unit_price) DESC",
    )
    cause = _primary_cause(
        row,
        _query_facts(parse_one(row["gold_sql"], dialect="postgres")),  # type: ignore[arg-type]
        _query_facts(parse_one(row["generated_sql"], dialect="postgres")),  # type: ignore[arg-type]
        row["context_visibility"],  # type: ignore[arg-type]
        None,
    )
    assert cause == RootCause.AGGREGATION_GRAIN_ERROR.value


def test_column_alignment_does_not_pair_unrelated_direct_columns() -> None:
    gold = _query_facts(parse_one("SELECT id, name FROM products", dialect="postgres"))
    generated = _query_facts(parse_one("SELECT sku, unit_price FROM products", dialect="postgres"))
    high_confidence, unmatched = _column_alignment(gold, generated)

    assert high_confidence == []
    assert unmatched["missing"] == ["products.id", "products.name"]
    assert unmatched["extra"] == ["products.sku", "products.unit_price"]


def test_primary_cause_counts_cover_non_equivalent_cases() -> None:
    source = Path("evaluation/results/m27/dev/20260903T023000Z/recomputed-v2")
    audit = build_audit(load_persisted_models(source))

    for model in ("luna", "terra"):
        counts = audit["root_cause_counts"][model]
        assert sum(counts.values()) == audit["primary_cause_denominators"][model]
    assert audit["provider_calls"] == 0
    assert audit["holdout_used"] is False


def test_audit_artifact_is_json_when_present() -> None:
    artifact = Path("evaluation/results/m27/failure_mechanism_audit.json")
    if artifact.exists():
        payload = json.loads(artifact.read_text())
        assert payload["provider_calls"] == 0
