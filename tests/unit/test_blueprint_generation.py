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


def test_blueprint_parser_rejects_missing_fields_and_extra_top_level() -> None:
    payload = _payload()
    del payload["sql"]
    with pytest.raises((KeyError, ValueError)):
        parse_blueprint_payload(payload, model="test", provider="test")
    payload = _payload()
    payload["extra"] = "forbidden"
    with pytest.raises(ValueError):
        parse_blueprint_payload(payload, model="test", provider="test")


def test_blueprint_messages_request_one_compact_object_without_reasoning() -> None:
    messages = blueprint_messages("Show completed orders", "[Table] orders")
    assert len(messages) == 2
    assert "chain-of-thought" in messages[0]["content"]
    assert "blueprint" in messages[0]["content"]
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
