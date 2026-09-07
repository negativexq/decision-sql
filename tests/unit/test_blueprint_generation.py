import pytest

from app.generation.blueprint import (
    BlueprintFilter,
    QueryBlueprint,
    blueprint_messages,
    blueprint_sql_consistency,
    parse_blueprint_payload,
)


def _payload() -> dict[str, object]:
    return {
        "blueprint": {
            "population": "orders",
            "grain": ["one row per customer"],
            "joins": [],
            "filters": [{"target": "status", "operator": "=", "value_or_rule": "completed"}],
            "aggregations": [{"measure": "amount", "function": "SUM"}],
            "temporal": [],
            "projection": ["customer_id", "total amount"],
            "ordering": [{"expression": "total amount", "direction": "DESC"}],
            "limit": 10,
            "distinct_required": False,
            "notes": [],
            "checks": {"identifiers_exist": True},
        },
        "sql": (
            "SELECT customer_id, SUM(amount) AS total_amount FROM orders "
            "WHERE status = 'completed' GROUP BY customer_id "
            "ORDER BY total_amount DESC LIMIT 10"
        ),
    }


def test_blueprint_payload_is_strict_and_bounded() -> None:
    proposal = parse_blueprint_payload(_payload(), model="test", provider="test")
    assert proposal.sql.startswith("SELECT")
    assert proposal.blueprint.limit == 10
    with pytest.raises(ValueError):
        QueryBlueprint(grain=["x"] * 5)
    with pytest.raises(ValueError):
        BlueprintFilter(target="x", operator="=", value_or_rule="y", extra="no")


def test_blueprint_parser_requires_sql_but_tolerates_non_authoritative_metadata() -> None:
    payload = _payload()
    del payload["sql"]
    with pytest.raises((KeyError, ValueError)):
        parse_blueprint_payload(payload, model="test", provider="test")
    payload = _payload()
    payload["extra"] = "forbidden"
    proposal = parse_blueprint_payload(payload, model="test", provider="test")
    assert proposal.sql.startswith("SELECT")
    assert any("top-level" in warning for warning in proposal.parse_warnings)


def test_blueprint_parser_normalizes_common_model_shapes_without_rewriting_sql() -> None:
    payload = {
        "blueprint": {
            "population": "orders",
            "joins": ["orders.customer_id -> customers.id"],
            "filters": ["status = completed"],
            "aggregations": ["revenue = SUM(total_amount)"],
            "temporal": "February 2025",
            "ordering": "revenue DESC",
            "checks": ["identifiers exist"],
        },
        "sql": "SELECT total_amount FROM orders",
    }

    proposal = parse_blueprint_payload(payload, model="test", provider="test")

    assert proposal.sql == payload["sql"]
    assert proposal.blueprint.joins[0].left == "orders.customer_id"
    assert proposal.blueprint.filters[0].value_or_rule == "status = completed"
    assert proposal.blueprint.aggregations[0].function == "DESCRIPTIVE"
    assert proposal.blueprint.temporal == ["February 2025"]
    assert proposal.blueprint.ordering[0].direction == "DESC"
    assert proposal.parse_warnings


def test_blueprint_parser_accepts_fenced_json_and_missing_blueprint() -> None:
    proposal = parse_blueprint_payload(
        '```json\n{"sql":"SELECT 1"}\n```', model="test", provider="test"
    )

    assert proposal.sql == "SELECT 1"
    assert proposal.blueprint.population == ""
    assert any("blueprint missing" in warning for warning in proposal.parse_warnings)


def test_blueprint_messages_request_one_compact_object_without_reasoning() -> None:
    messages = blueprint_messages("Show completed orders", "[Table] orders")
    assert len(messages) == 2
    assert "chain-of-thought" in messages[0]["content"]
    assert "blueprint" in messages[0]["content"]
    assert "QUERY QUALITY PACK" in messages[0]["content"]
    assert "[Table] orders" in messages[0]["content"]


def test_consistency_checker_is_diagnostic_only() -> None:
    proposal = parse_blueprint_payload(_payload(), model="test", provider="test")
    result = blueprint_sql_consistency(proposal.blueprint, proposal.sql)
    assert result["classification"] == "BLUEPRINT_SQL_CONSISTENT"
    wrong = blueprint_sql_consistency(proposal.blueprint, "SELECT customer_id FROM orders")
    assert wrong["classification"] in {
        "BLUEPRINT_SQL_PARTIAL_MISMATCH",
        "BLUEPRINT_SQL_STRONG_MISMATCH",
    }
