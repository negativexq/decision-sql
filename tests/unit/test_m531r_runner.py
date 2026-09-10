# ruff: noqa: E501
from __future__ import annotations

from benchmark.m531r_runner import canonical_bytes, recursive_diff, sha_value


def test_canonical_request_hash_is_deterministic() -> None:
    assert canonical_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert sha_value({"b": 2, "a": 1}) == sha_value({"a": 1, "b": 2})


def test_shared_context_diff_is_visible_without_case_change() -> None:
    before = {"question": "same", "business_rules": [{"rule_id": "r", "definition": "old"}]}
    after = {"question": "same", "business_rules": [{"rule_id": "r", "definition": "new"}]}
    assert recursive_diff(before, after) == ["business_rules[0].definition"]


def test_transport_only_fields_are_not_part_of_canonical_semantic_value() -> None:
    first = {"model": "gpt-5.6-luna", "messages": [], "response_format": {"type": "json_schema"}}
    second = {**first, "request_id": "different", "trace_id": "different"}
    assert sha_value(first) == sha_value(
        {k: v for k, v in second.items() if k not in {"request_id", "trace_id"}}
    )
