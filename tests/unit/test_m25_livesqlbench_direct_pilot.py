from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.generation.provider import MalformedProviderResponse
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.external.livesqlbench.protected import LiveSqlBenchRuntimeCase
from evaluation.m25_livesqlbench_direct_pilot import (
    JOURNAL,
    MAX_PROVIDER_CALLS,
    JournalProvider,
    _append_journal,
    _read_journal,
    sha256_json,
    sql_shape,
)

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_pilot_ids_and_order_are_unchanged() -> None:
    final = json.loads(
        (ROOT / "evaluation/fixtures/livesqlbench_base_lite_final_manifest.json").read_text()
    )
    public = json.loads(
        (ROOT / "evaluation/fixtures/livesqlbench_base_lite_preflight_manifest.json").read_text()
    )
    final_ids = final["pilot_case_ids"]
    public_ids = public["future_baseline"]["pilot_case_ids"]
    assert final_ids == public_ids
    assert len(final_ids) == 18
    assert len(set(final_ids)) == 18
    assert sha256_json(final_ids) == sha256_json(public_ids)


def test_runtime_payload_has_no_protected_evaluation_fields() -> None:
    case = LiveSqlBenchRuntimeCase(
        instance_id="synthetic",
        database="db",
        question="List names",
        external_knowledge=("a legitimate hint",),
        category="Query",
        difficulty_tier="simple",
        conditions={},
    )
    payload = case.provider_payload("TABLE names (name text)")
    assert payload == {
        "question": "List names",
        "external_knowledge": ["a legitimate hint"],
        "schema_context": "TABLE names (name text)",
    }
    serialized = json.dumps(payload)
    for protected in ("sol_sql", "test_cases", "gold SQL", "reference result"):
        assert protected not in serialized


def test_journal_rejects_duplicate_case_rows(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    row = {"case_id": "case-1", "actual_request_count": 1}
    _append_journal(journal, row)
    _append_journal(journal, row)
    with pytest.raises(RuntimeError, match="invalid or duplicate"):
        _read_journal(journal)


def test_journal_round_trip_and_budget_constant(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    _append_journal(journal, {"case_id": "case-1", "actual_request_count": 1})
    assert _read_journal(journal)["case-1"]["actual_request_count"] == 1
    assert MAX_PROVIDER_CALLS == 18
    assert JOURNAL.name == "m25_direct_pilot_journal.jsonl"


@pytest.mark.asyncio
async def test_journal_provider_classifies_protocol_failure_without_retry(tmp_path: Path) -> None:
    class FailingInner:
        settings = SimpleNamespace()

        async def propose_sql(self, *_args: object, **_kwargs: object) -> object:
            raise MalformedProviderResponse("synthetic malformed response")

        def consume_model_io(self) -> None:
            return None

    journal = tmp_path / "journal.jsonl"
    provider = JournalProvider(FailingInner(), journal)  # type: ignore[arg-type]
    provider.case_id = "case-1"
    provider.expected_context_hash = None
    with pytest.raises(MalformedProviderResponse):
        await provider.propose_sql(None, None, "schema")
    record = _read_journal(journal)["case-1"]
    assert record["provider_status"] == "PROTOCOL_FAILURE"
    assert record["actual_request_count"] == 1


def test_sql_shape_is_ast_based_and_deterministic() -> None:
    sql = "SELECT a.id, COUNT(*) FROM a JOIN b ON b.a_id = a.id GROUP BY a.id"
    assert sql_shape(sql) == sql_shape(sql)
    shape = sql_shape(sql)
    assert shape["joins"] == 1
    assert shape["aggregation"] is True
    assert shape["group_by"] is True


def test_official_empty_result_semantics_remain_fail() -> None:
    empty = LiveSqlBenchResult((), ())
    assert soft_ex_match(empty, empty, ordered=False) is False
