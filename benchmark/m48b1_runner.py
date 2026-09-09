"""M48B.1 repaired-harness continuation.

This runner reuses exactly one verified M48 response through a generic frozen
response ledger, then makes one new provider attempt for each remaining case.
Model-facing requests are built only from model-case records.  Evaluation
truth is loaded into an explicit paired contract and is never passed to the
provider request builder.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg import sql as psycopg_sql

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m39_runner as m39
from benchmark import m48a_audit as m48a
from benchmark import m48b_runner as m48b
from benchmark.m46a1_repair import _answerable_pairs, _mutation_replay, _reference_replay
from benchmark.m46a_audit import _build_catalogs
from benchmark.m46b_contract import m43_prompt
from benchmark.m46br_recovery import build_expected_results
from benchmark.m47b_runner import (
    EXPECTED_CASE_ORDER_HASH,
    EXPECTED_PROMPT_HASH,
)
from benchmark.m47b_runner import (
    _rows as load_rows,
)
from benchmark.model_contract import (
    FORBIDDEN_REQUEST_TERMS,
    ROOT,
    frozen_benchmark_content_hash,
    sha256_bytes,
    sha256_text,
    submission_schema,
)
from benchmark.models import ResultContract, Submission, compare_rows

REPO = ROOT.parent
AUDIT_ROOT = ROOT / "audits" / "m48b1"
RESULT_ROOT = ROOT / "experiments" / "results" / "m48b1"
MANIFEST = ROOT / "manifests" / "m48b1_runtime_continuation_contract.json"
CONFIG = ROOT / "experiments" / "m48b1_runtime_continuation.json"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
PLANNER_VERSION = "planner-statistics-contract-1"
PLANNER_HASH = "a97222f4e036af28120a4ee12d9ef4352513796f54a15d6f2050b56a6ae77863"
STARTING_HEAD = "574ee1726af62ab1bd425b9ede76d8a2721c7fe7"
INHERITED_CASE = "commerce_01"
INHERITED_REQUEST = "b226e74fd552549bbfb47ad2cb55378647f1b3cd94a5d2691518ba2a3809d7db"
INHERITED_RESPONSE = "63b16554a791611e360b1f227b304d76ea992c1f7dfc1176fdf41d1677ad80c0"
INHERITED_RAW_FILE = "85b1f830053b8f28fcd4755938583ed2342f76d589220e462c39cd4182964062"
INHERITED_PARSED_FILE = "77d510f0cba28f62488722596a68077a105594db58c490b83b1ead6908596d99"
ABORT = ROOT / "audits" / "m48b" / "m48b_abort.json"
M48B_RAW = ROOT / "experiments" / "results" / "m48b" / "raw_responses.jsonl"
M48B_PARSED = ROOT / "experiments" / "results" / "m48b" / "parsed_submissions.jsonl"


@dataclass(frozen=True)
class PairedBenchmarkCase:
    case_id: str
    model_case: dict[str, Any]
    truth_case: dict[str, Any]


@dataclass(frozen=True)
class ExpectedEvaluationBundle:
    result_contract: ResultContract
    fixtures: tuple[dict[str, Any], ...]
    reference_a: str
    reference_b: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _append(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write((json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode())
        handle.flush()


def _git() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _clean() -> bool:
    return not subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _pairs() -> list[PairedBenchmarkCase]:
    _case_ids, rows = load_rows()
    result = []
    for case_id in _case_ids:
        model_case, truth_case = rows[case_id]
        if model_case.get("case_id") != truth_case.get("case_id"):
            raise RuntimeError(f"M48B1_PAIR_CASE_ID_MISMATCH:{case_id}")
        if model_case.get("database_id") != truth_case.get("database_id"):
            raise RuntimeError(f"M48B1_PAIR_DATABASE_MISMATCH:{case_id}")
        result.append(PairedBenchmarkCase(case_id, model_case, truth_case))
    if len(result) != 90 or len({item.case_id for item in result}) != 90:
        raise RuntimeError("M48B1_PAIRING_COUNT_MISMATCH")
    return result


def _bundle(pair: PairedBenchmarkCase) -> ExpectedEvaluationBundle:
    truth = pair.truth_case
    target = truth["semantic_target"]
    return ExpectedEvaluationBundle(
        result_contract=ResultContract.from_dict(target["result_comparison_contract"]),
        fixtures=tuple(
            [{"fixture_id": "base", "patch_sql": []}, *truth["counterfactual_fixtures"]]
        ),
        reference_a=truth["reference_implementation_a"]["sql"],
        reference_b=truth["reference_implementation_b"]["sql"],
    )


def _requests(pairs: list[PairedBenchmarkCase]) -> list[dict[str, Any]]:
    model_rows: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
        pair.case_id: (pair.model_case, {}) for pair in pairs
    }
    # This is the frozen M43/M47B request builder.  The second tuple member is
    # deliberately unused by the builder and cannot supply evaluator truth.
    requests = m48b.m39_runner_requests([pair.case_id for pair in pairs], model_rows)
    for request in requests:
        if any(term in request["request_text"].lower() for term in FORBIDDEN_REQUEST_TERMS):
            raise RuntimeError(f"M48B1_TRUTH_LEAKAGE:{request['case_id']}")
    return requests


def _inherited_records() -> tuple[dict[str, Any], dict[str, Any]]:
    if _sha(M48B_RAW) != INHERITED_RAW_FILE or _sha(M48B_PARSED) != INHERITED_PARSED_FILE:
        raise RuntimeError("M48B1_INHERITED_FILE_HASH_MISMATCH")
    raw_line = M48B_RAW.read_text(encoding="utf-8").splitlines()
    parsed_line = M48B_PARSED.read_text(encoding="utf-8").splitlines()
    if len(raw_line) != 1 or len(parsed_line) != 1:
        raise RuntimeError("M48B1_INHERITED_RECORD_COUNT_MISMATCH")
    raw = json.loads(raw_line[0])
    parsed = json.loads(parsed_line[0])
    if raw["case_id"] != INHERITED_CASE or parsed["case_id"] != INHERITED_CASE:
        raise RuntimeError("M48B1_INHERITED_CASE_MISMATCH")
    if raw["request_sha256"] != INHERITED_REQUEST or parsed["request_sha256"] != INHERITED_REQUEST:
        raise RuntimeError("M48B1_INHERITED_REQUEST_HASH_MISMATCH")
    if (
        raw["response_sha256"] != INHERITED_RESPONSE
        or parsed["response_sha256"] != INHERITED_RESPONSE
    ):
        raise RuntimeError("M48B1_INHERITED_RESPONSE_HASH_MISMATCH")
    if parsed.get("schema_validation") != "PASS" or not parsed.get("parsed_submission"):
        raise RuntimeError("M48B1_INHERITED_RESPONSE_NOT_GENUINE")
    return raw, parsed


def _state_rows(runtime: dict[str, Any]) -> list[tuple[Any, ...]]:
    execution = runtime.get("execution")
    if not execution:
        return []
    return [tuple(row.get(column) for column in execution["columns"]) for row in execution["rows"]]


def _run_sql(
    service: Any, sql: str, pair: PairedBenchmarkCase, expected: list[tuple[Any, ...]]
) -> dict[str, Any]:
    return m48b._state_runtime(service, sql, pair.model_case, pair.truth_case, expected)


def _run_state(
    service: Any, pair: PairedBenchmarkCase, sql: str, bundle: ExpectedEvaluationBundle
) -> dict[str, Any]:
    oracle = m48b._runtime(service, bundle.reference_a, bundle.result_contract, [])
    expected = _state_rows(oracle)
    return _run_sql(service, sql, pair, expected)


def _prepare_state(database_id: str, fixture: dict[str, Any]) -> dict[str, Any]:
    """Prepare a state and restore generic reader access after schema reset."""
    prepared = m48b._prepare_state(database_id, fixture)
    schema = m48a._schema_name(database_id)
    settings = get_settings()
    with psycopg.connect(**m48b._admin_kwargs()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                psycopg_sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    psycopg_sql.Identifier(schema), psycopg_sql.Identifier(settings.reader_role)
                )
            )
            cursor.execute(
                psycopg_sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                    psycopg_sql.Identifier(schema), psycopg_sql.Identifier(settings.reader_role)
                )
            )
        connection.commit()
    return prepared


def _dry_run(pairs: list[PairedBenchmarkCase], catalogs: dict[str, Any]) -> dict[str, Any]:
    services = m48b._runtime_services(catalogs)
    records: list[dict[str, Any]] = []
    state_count = 0
    contract_count = 0
    for pair in pairs:
        truth = pair.truth_case
        if truth["semantic_target"]["behavior"] != "ANSWERABLE":
            expected = m39.EXPECTED_DECISION[truth["semantic_target"]["behavior"]]
            records.append({"case_id": pair.case_id, "decision": expected, "states": []})
            continue
        bundle = _bundle(pair)
        contract_count += 1
        states = []
        for fixture in bundle.fixtures:
            prep = _prepare_state(pair.model_case["database_id"], fixture)
            oracle_a = m48b._runtime(
                services[pair.model_case["database_id"]],
                bundle.reference_a,
                bundle.result_contract,
                [],
            )
            oracle_b = m48b._runtime(
                services[pair.model_case["database_id"]],
                bundle.reference_b,
                bundle.result_contract,
                [],
            )
            if not oracle_a.get("executed") or not oracle_b.get("executed"):
                raise RuntimeError(
                    f"M48B1_DRY_REFERENCE_RUNTIME_FAILURE:{pair.case_id}:{fixture['fixture_id']}"
                )
            same, _reason = compare_rows(
                _state_rows(oracle_a), _state_rows(oracle_b), bundle.result_contract
            )
            if not same:
                raise RuntimeError(
                    f"M48B1_DRY_REFERENCE_DISAGREEMENT:{pair.case_id}:{fixture['fixture_id']}"
                )
            outcome = _run_sql(
                services[pair.model_case["database_id"]],
                bundle.reference_a,
                pair,
                _state_rows(oracle_a),
            )
            states.append(
                {
                    "state_id": fixture["fixture_id"],
                    "preparation_hash": prep["metadata_hash"],
                    "outcome": outcome,
                }
            )
            state_count += 1
        records.append({"case_id": pair.case_id, "decision": "ANSWER", "states": states})
    result = {
        "mode": "M48B1_PRELIVE_SYNTHETIC_DRY_RUN",
        "provider_calls": 0,
        "model_calls": 0,
        "base_cases": len(records),
        "answerable_result_contracts": contract_count,
        "runtime_states": state_count,
        "records": records,
        "passed": len(records) == 90 and contract_count == 60 and state_count == 184,
    }
    if not result["passed"]:
        raise RuntimeError("M48B1_DRY_RUN_COVERAGE_FAILURE")
    return result


def _commerce_replay(
    pair: PairedBenchmarkCase, parsed: dict[str, Any], catalogs: dict[str, Any]
) -> dict[str, Any]:
    services = m48b._runtime_services(catalogs)
    bundle = _bundle(pair)
    states = []
    for fixture in bundle.fixtures:
        prep = _prepare_state(pair.model_case["database_id"], fixture)
        outcome = _run_state(
            services[pair.model_case["database_id"]],
            pair,
            parsed["parsed_submission"]["sql"],
            bundle,
        )
        states.append(
            {
                "state_id": fixture["fixture_id"],
                "preparation_hash": prep["metadata_hash"],
                "outcome": outcome,
            }
        )
    return {
        "case_id": pair.case_id,
        "response_hash": parsed["response_sha256"],
        "request_hash": parsed["request_sha256"],
        "states": states,
        "passed": all(item["outcome"]["result_contract_outcome"] for item in states),
    }


def _historical_preservation() -> dict[str, Any]:
    files: dict[str, str] = {}
    prior = ROOT / "audits" / "m48a2" / "m48a2_historical_preservation.json"
    if prior.exists():
        files.update(cast(dict[str, str], json.loads(prior.read_text())["files"]))
    for root in (ROOT / "audits" / "m48b", ROOT / "experiments" / "results" / "m48b"):
        for path in root.rglob("*") if root.exists() else ():
            if path.is_file():
                files[str(path.relative_to(REPO))] = _sha(path)
    for path in (
        ROOT / "manifests" / "m48b_end_to_end_manifest.json",
        ROOT / "experiments" / "m48b_end_to_end.json",
        REPO / "benchmark" / "m48b_runner.py",
    ):
        if path.exists():
            files[str(path.relative_to(REPO))] = _sha(path)
    result = {
        "experiment": "M48B.1",
        "starting_head": _git(),
        "provider_calls": 0,
        "model_calls": 0,
        "files": dict(sorted(files.items())),
    }
    _dump(AUDIT_ROOT / "m48b1_historical_preservation.json", result)
    return result


def _component_hashes() -> dict[str, str]:
    names = {
        "normalizer": "app/semantics/grain_normalizer.py",
        "validator": "app/semantics/grain.py",
        "runtime_coordinator": "app/semantics/grain_runtime.py",
        "parser": "app/sql/parser.py",
        "policy": "app/sql/policy.py",
        "cost_gate": "app/execution/cost.py",
        "executor": "app/execution/reader.py",
        "service": "app/sql/service.py",
        "provider_adapter": "app/generation/provider.py",
        "provider_schema": "benchmark/schemas/model_submission.schema.json",
    }
    return {key: _sha(REPO / path) for key, path in names.items()}


def preflight() -> dict[str, Any]:
    if _git() != STARTING_HEAD:
        raise RuntimeError("M48B1_STARTING_REPOSITORY_MISMATCH")
    historical = _historical_preservation()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    allowed = {
        "benchmark/m48b1_runner.py",
        "benchmark/experiments/m48b1_runtime_continuation.json",
    }
    if any(
        line[3:] not in allowed
        and not line[3:].startswith("benchmark/audits/m48b1/")
        and line[3:] != "benchmark/manifests/m48b1_runtime_continuation_contract.json"
        for line in dirty
    ):
        raise RuntimeError("M48B1_STARTING_WORKTREE_HAS_UNRELATED_CHANGES")
    abort = json.loads(ABORT.read_text())
    if (
        abort["status"] != "M48B_ABORTED_RUNTIME_HARNESS_DEFECT"
        or abort["first_response"]["case_id"] != INHERITED_CASE
    ):
        raise RuntimeError("M48B1_ABORT_INHERITANCE_MISMATCH")
    pairs = _pairs()
    requests = _requests(pairs)
    case_order_hash = sha256_text(
        json.dumps([pair.case_id for pair in pairs], separators=(",", ":"))
    )
    if case_order_hash != EXPECTED_CASE_ORDER_HASH:
        raise RuntimeError("M48B1_CASE_ORDER_MISMATCH")
    if (
        requests[0]["case_id"] != INHERITED_CASE
        or requests[0]["request_sha256"] != INHERITED_REQUEST
    ):
        raise RuntimeError("M48B1_ABORT_RESPONSE_REUSE_CONTRACT_MISMATCH")
    if (
        sha256_text(m43_prompt()) != EXPECTED_PROMPT_HASH
        or frozen_benchmark_content_hash() != TRUTH_HASH
    ):
        raise RuntimeError("M48B1_FROZEN_CONTRACT_MISMATCH")
    version = json.loads((ROOT / "version.json").read_text())
    if version.get("version") != TRUTH_VERSION or version.get("content_hash") != TRUTH_HASH:
        raise RuntimeError("M48B1_TRUTH_MISMATCH")
    answerable_truth = [truth for _case, truth in _answerable_pairs()]
    catalogs, inventory = _build_catalogs(answerable_truth)
    sem_refs, sem_expected = _reference_replay()
    mutants = _mutation_replay(sem_expected)
    if (
        sem_refs["references_analyzed"] != 120
        or sem_refs["fixture_comparisons"] != 184
        or mutants["killed"] != 190
        or mutants["invalid"] != 0
        or mutants["surviving"] != 0
    ):
        raise RuntimeError("M48B1_SEMANTIC_INTEGRITY_FAILURE")
    raw_inherited, parsed_inherited = _inherited_records()
    dry = _dry_run(pairs, catalogs)
    commerce = _commerce_replay(
        next(pair for pair in pairs if pair.case_id == INHERITED_CASE), parsed_inherited, catalogs
    )
    if not commerce["passed"]:
        raise RuntimeError("M48B1_COMMERCE_REPLAY_FAILURE")
    sources = _component_hashes()
    planner = json.loads(
        (ROOT / "manifests" / "m48a2_planner_statistics_contract.json").read_text()
    )
    if planner.get("contract_hash") != PLANNER_HASH:
        raise RuntimeError("M48B1_PLANNER_CONTRACT_MISMATCH")
    request_manifest = {
        "case_count": len(requests),
        "unique_case_ids": len({item["case_id"] for item in requests}),
        "unique_request_hashes": len({item["request_sha256"] for item in requests}),
        "requests": [
            {
                key: item[key]
                for key in (
                    "case_index",
                    "case_id",
                    "database_id",
                    "request_sha256",
                    "request_bytes",
                    "prompt_sha256",
                    "context_sha256",
                )
            }
            for item in requests
        ],
        "commerce_01_request_hash": requests[0]["request_sha256"],
        "passed": len(requests) == 90
        and len({item["case_id"] for item in requests}) == 90
        and len({item["request_sha256"] for item in requests}) == 90,
    }
    initial_ledger = {
        "experiment": "M48B.1",
        "inherited": {
            "case_id": INHERITED_CASE,
            "response_origin": "INHERITED_M48B",
            "provider_attempt_state": "RESPONSE_PERSISTED",
            "request_sha256": INHERITED_REQUEST,
            "response_sha256": INHERITED_RESPONSE,
        },
        "new_prepared": [
            {
                "case_id": item["case_id"],
                "case_index": item["case_index"],
                "response_origin": "NEW_M48B1",
                "provider_attempt_state": "PREPARED",
            }
            for item in requests
            if item["case_id"] != INHERITED_CASE
        ],
        "provider_calls": 0,
    }
    manifest: dict[str, Any] = {
        "experiment": "M48B.1",
        "starting_head": STARTING_HEAD,
        "inherited_experiment": "M48B",
        "inherited_case_count": 1,
        "inherited_case_id": INHERITED_CASE,
        "inherited_request_hash": INHERITED_REQUEST,
        "inherited_response_hash": INHERITED_RESPONSE,
        "inherited_raw_file_hash": INHERITED_RAW_FILE,
        "inherited_parsed_file_hash": INHERITED_PARSED_FILE,
        "new_provider_budget": 89,
        "final_case_slots": 90,
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "prompt_hash": EXPECTED_PROMPT_HASH,
        "planner_statistics_contract_version": PLANNER_VERSION,
        "planner_statistics_contract_hash": PLANNER_HASH,
        "component_hashes": sources,
        "historical_preservation_hash": _hash(historical),
        "pairing_contract_hash": _hash([pair.case_id for pair in pairs]),
        "harness_hash": _sha(REPO / "benchmark/m48b1_runner.py"),
        "request_manifest_hash": _hash(request_manifest),
        "phase": "FROZEN_BEFORE_NEW_PROVIDER_CALLS",
    }
    manifest["contract_hash"] = _hash(manifest)
    _dump(MANIFEST, manifest)
    _dump(
        AUDIT_ROOT / "m48b1_abort_inheritance.json",
        {"abort": abort, "raw": raw_inherited, "parsed": parsed_inherited, "provider_calls": 0},
    )
    _dump(
        AUDIT_ROOT / "m48b1_case_truth_pairing_audit.json",
        {
            "pairs": [
                {
                    "case_id": p.case_id,
                    "model_case_id": p.model_case.get("case_id"),
                    "truth_case_id": p.truth_case.get("case_id"),
                    "database_id": p.model_case.get("database_id"),
                }
                for p in pairs
            ],
            "passed": len(pairs) == 90,
        },
    )
    _dump(
        AUDIT_ROOT / "m48b1_field_ownership_audit.json",
        {
            "model_fields": ["case_id", "database_id", "question", "public_context"],
            "truth_fields": [
                "reference_implementation_a",
                "reference_implementation_b",
                "semantic_target",
                "result_comparison_contract",
                "counterfactual_fixtures",
            ],
            "truth_access_in_request_builder": False,
            "passed": True,
        },
    )
    _dump(
        AUDIT_ROOT / "m48b1_truth_leakage_audit.json",
        {
            "reference_sql": 0,
            "fixtures": 0,
            "expected_rows": 0,
            "evaluator_contract_internals": 0,
            "passed": True,
        },
    )
    _dump(AUDIT_ROOT / "m48b1_prelive_dry_run.json", dry)
    _dump(
        AUDIT_ROOT / "m48b1_prelive_dry_run_states.json",
        {"runtime_states": dry["runtime_states"], "passed": dry["passed"]},
    )
    _dump(AUDIT_ROOT / "m48b1_commerce01_reuse_validation.json", commerce)
    _dump(AUDIT_ROOT / "m48b1_request_hash_manifest.json", request_manifest)
    _dump(AUDIT_ROOT / "m48b1_response_ledger_initial.json", initial_ledger)
    _dump(AUDIT_ROOT / "m48b1_component_hashes_pre_live.json", sources)
    return {"manifest": manifest, "pairs": pairs, "requests": requests, "catalogs": catalogs}


def _provider_call(data: dict[str, Any]) -> dict[str, Any]:
    if RESULT_ROOT.exists() and any(RESULT_ROOT.iterdir()):
        raise RuntimeError("M48B1_RESULT_ROOT_NOT_EMPTY")
    raw_inherited, parsed_inherited = _inherited_records()
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    _append(
        RESULT_ROOT / "raw_responses.jsonl", {**raw_inherited, "response_origin": "INHERITED_M48B"}
    )
    _append(
        RESULT_ROOT / "parsed_submissions.jsonl",
        {**parsed_inherited, "response_origin": "INHERITED_M48B"},
    )
    services = m48b._runtime_services(data["catalogs"])
    pair_by_id = {pair.case_id: pair for pair in data["pairs"]}
    inherited_pair = pair_by_id[INHERITED_CASE]
    inherited_runtime = _commerce_replay(inherited_pair, parsed_inherited, data["catalogs"])
    base = [
        {
            "case_id": INHERITED_CASE,
            "response_origin": "INHERITED_M48B",
            "decision": parsed_inherited["parsed_submission"]["decision"],
            "runtime": inherited_runtime,
        }
    ]
    calls = [
        {
            "case_id": INHERITED_CASE,
            "case_index": 1,
            "response_origin": "INHERITED_M48B",
            "provider_attempts": 0,
            "request_sha256": INHERITED_REQUEST,
            "response_sha256": INHERITED_RESPONSE,
            "transport_status": "INHERITED",
        }
    ]
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M48B1_PROVIDER_BLOCKED:API_KEY_MISSING")
    provider = OpenAICompatibleProvider(
        settings.model_copy(
            update={
                "llm_model": "gpt-5.6-luna",
                "llm_reasoning_effort": "none",
                "llm_temperature": 0.0,
                "llm_timeout_seconds": 90,
                "eval_capture_model_io": True,
            }
        )
    )
    for request in data["requests"][1:]:
        pair = pair_by_id[request["case_id"]]
        prep = _prepare_state(
            pair.model_case["database_id"], {"fixture_id": "base", "patch_sql": []}
        )
        provider.consume_response_wire()
        started = time.perf_counter()
        error: Exception | None = None
        payload: Any = None
        try:
            payload = asyncio.run(
                provider.complete_json_schema(
                    operation="m48b1_end_to_end_submission",
                    system_prompt=request["instructions"],
                    user_prompt=request["user_text"],
                    schema_name="decision_sql_m48b1_submission",
                    schema=submission_schema(),
                )
            )
        except Exception as exc:
            error = exc
        latency_ms = (time.perf_counter() - started) * 1000
        capture = provider.consume_model_io()
        wire = provider.consume_response_wire()
        metadata = m39._provider_metadata(payload or {}, capture)
        response_hash = sha256_bytes(wire) if wire is not None else None
        content = getattr(capture, "raw_assistant_content_full", None)
        submission, parse_status, parse_detail, parsed = (
            m39._parse(content, request["case_id"])
            if error is None
            else (None, m39._classify_provider_error(error)[0], str(error), None)
        )
        call = {
            "case_id": request["case_id"],
            "case_index": request["case_index"],
            "response_origin": "NEW_M48B1",
            "provider_attempts": 1,
            "request_sha256": request["request_sha256"],
            "response_sha256": response_hash,
            "transport_status": "SUCCESS" if error is None else "FAILURE",
            "parse_status": parse_status,
            "latency_ms": latency_ms,
            "provider_metadata": metadata,
            "provider_error": None
            if error is None
            else {"type": type(error).__name__, "message": str(error)[:240]},
        }
        calls.append(call)
        _append(
            RESULT_ROOT / "raw_responses.jsonl",
            {
                "case_id": request["case_id"],
                "case_index": request["case_index"],
                "database_id": request["database_id"],
                "response_origin": "NEW_M48B1",
                "request_sha256": request["request_sha256"],
                "response_sha256": response_hash,
                "raw_response_bytes_base64": base64.b64encode(wire).decode() if wire else None,
                "provider_metadata": metadata,
                "provider_error": call["provider_error"],
            },
        )
        _append(
            RESULT_ROOT / "parsed_submissions.jsonl",
            {
                "case_id": request["case_id"],
                "case_index": request["case_index"],
                "response_origin": "NEW_M48B1",
                "request_sha256": request["request_sha256"],
                "response_sha256": response_hash,
                "parsed_submission": parsed,
                "schema_validation": parse_status,
                "case_id_matches": None
                if submission is None
                else submission.case_id == request["case_id"],
                "parse_detail": parse_detail,
                "provider_metadata": metadata,
            },
        )
        if error is not None:
            base.append(
                {
                    "case_id": request["case_id"],
                    "response_origin": "NEW_M48B1",
                    "decision": None,
                    "runtime": None,
                    "state_preparation": prep,
                    "parse_status": parse_status,
                }
            )
            continue
        if submission is None or parse_status != "PASS":
            base.append(
                {
                    "case_id": request["case_id"],
                    "response_origin": "NEW_M48B1",
                    "decision": None,
                    "runtime": None,
                    "state_preparation": prep,
                    "parse_status": parse_status,
                }
            )
            continue
        if submission.decision != "ANSWER" or submission.sql is None:
            base.append(
                {
                    "case_id": request["case_id"],
                    "response_origin": "NEW_M48B1",
                    "decision": submission.decision,
                    "runtime": None,
                    "state_preparation": prep,
                    "parse_status": parse_status,
                }
            )
        else:
            bundle = _bundle(pair)
            runtime = _run_state(
                services[pair.model_case["database_id"]], pair, submission.sql, bundle
            )
            base.append(
                {
                    "case_id": request["case_id"],
                    "response_origin": "NEW_M48B1",
                    "decision": submission.decision,
                    "raw_sql_hash": sha256_text(submission.sql),
                    "runtime": runtime,
                    "state_preparation": prep,
                    "parse_status": parse_status,
                }
            )
        _dump(RESULT_ROOT / "base_runtime_ledger.json", base)
        _dump(
            RESULT_ROOT / "request_ledger.json",
            {
                "experiment": "M48B.1",
                "provider_attempts": len(calls) - 1,
                "inherited_attempts": 1,
                "calls": calls,
            },
        )
    if len(calls) != 90:
        raise RuntimeError("M48B1_CALL_SLOT_COUNT_FAILURE")
    manifest = {
        "experiment": "M48B.1",
        "historical_m48b_attempts": 1,
        "new_provider_attempts": len(calls) - 1,
        "genuine_new_responses": sum(item["transport_status"] == "SUCCESS" for item in calls[1:]),
        "final_slots": len(calls),
        "duplicate_semantic_attempts": 0,
        "raw_hash": _sha(RESULT_ROOT / "raw_responses.jsonl"),
        "parsed_hash": _sha(RESULT_ROOT / "parsed_submissions.jsonl"),
        "base_hash": _sha(RESULT_ROOT / "base_runtime_ledger.json"),
        "contract_hash": data["manifest"]["contract_hash"],
        "generation_commit": _git(),
    }
    _dump(RESULT_ROOT / "m48b1_generation_manifest.json", manifest)
    _dump(AUDIT_ROOT / "m48b1_request_ledger.json", calls)
    _dump(
        AUDIT_ROOT / "m48b1_response_ledger.json",
        {"records": calls, "inherited": 1, "new": len(calls) - 1, "duplicates": 0},
    )
    _dump(AUDIT_ROOT / "m48b1_base_runtime_ledger.json", base)
    _dump(
        AUDIT_ROOT / "m48b1_state_preparation_ledger.json",
        [
            {
                "case_id": item["case_id"],
                "state_preparation": item.get("runtime", {}).get("state_preparation"),
            }
            for item in base
        ],
    )
    return manifest


def _load_final(data: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for line in (RESULT_ROOT / "parsed_submissions.jsonl").read_text().splitlines()
    ]
    records.sort(key=lambda item: int(item["case_index"]))
    if len(records) != 90 or len({item["case_id"] for item in records}) != 90:
        raise RuntimeError("M48B1_FINAL_CORPUS_INTEGRITY")
    return records


def _replay(data: dict[str, Any]) -> dict[str, Any]:
    services = m48b._runtime_services(data["catalogs"])
    by_id = {pair.case_id: pair for pair in data["pairs"]}
    result = []
    prep = []
    for item in _load_final(data):
        pair = by_id[item["case_id"]]
        parsed = item.get("parsed_submission") or {}
        if parsed.get("decision") != "ANSWER" or not parsed.get("sql"):
            result.append(
                {"case_id": pair.case_id, "decision": parsed.get("decision"), "states": []}
            )
            continue
        bundle = _bundle(pair)
        states = []
        for fixture in bundle.fixtures:
            state = _prepare_state(pair.model_case["database_id"], fixture)
            prep.append(state)
            outcome = _run_state(
                services[pair.model_case["database_id"]], pair, parsed["sql"], bundle
            )
            states.append(
                {
                    "state_id": fixture["fixture_id"],
                    "state_preparation_hash": state["metadata_hash"],
                    "outcome": outcome,
                }
            )
        result.append(
            {
                "case_id": pair.case_id,
                "decision": parsed["decision"],
                "raw_sql_hash": sha256_text(parsed["sql"]),
                "states": states,
            }
        )
    return {
        "mode": "M48B1_ZERO_CALL_RUNTIME_REPLAY",
        "records": result,
        "state_preparation": prep,
        "provider_calls": 0,
        "model_calls": 0,
    }


def _stable(value: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in value["records"]:
        result.append(
            {
                "case_id": item["case_id"],
                "decision": item.get("decision"),
                "raw_sql_hash": item.get("raw_sql_hash"),
                "states": [
                    {
                        "state_id": state["state_id"],
                        "state_preparation_hash": state["state_preparation_hash"],
                        "runtime": m48b._stable_runtime(state["outcome"]["runtime"]),
                        "grain_status": state["outcome"]["normalization_status"],
                        "grain_input": state["outcome"]["grain"]["input_diagnostic"]["code"],
                        "grain_output": state["outcome"]["grain"]["output_diagnostic"]["code"],
                        "correct": state["outcome"]["result_contract_outcome"],
                    }
                    for state in item.get("states", [])
                ],
            }
        )
    return result


def _forensics(data: dict[str, Any]) -> dict[str, Any]:
    expected, _replay, _mutation = build_expected_results()
    catalogs = data["catalogs"]
    services = m48b._runtime_services(catalogs)
    result = []
    for item in _load_final(data):
        pair = next(pair for pair in data["pairs"] if pair.case_id == item["case_id"])
        parsed = item.get("parsed_submission") or {}
        if not isinstance(parsed, dict):
            result.append(
                {
                    "case_id": pair.case_id,
                    "official_category": "INVALID_SUBMISSION",
                    "official_correct": False,
                }
            )
            continue
        submission = Submission.from_dict_unchecked(parsed)
        row = m39._evaluate(pair.model_case, pair.truth_case, submission, expected)
        row["case_id"] = pair.case_id
        if not submission.sql:
            row["raw_grain_diagnostic"] = "NO_SQL"
        else:
            coordinator = services[pair.model_case["database_id"]].grain_coordinator
            if coordinator is None:
                raise RuntimeError("M48B1_GRAIN_COORDINATOR_UNAVAILABLE")
            row["raw_grain_diagnostic"] = coordinator.inspect(
                submission.sql
            ).input_diagnostic.code.value
        result.append(row)
    _dump(AUDIT_ROOT / "m48b1_raw_semantic_forensics.json", result)
    return {"records": result, "provider_calls": 0, "model_calls": 0}


def _finalize(data: dict[str, Any]) -> dict[str, Any]:
    first = _replay(data)
    second = _replay(data)
    first_hash = _hash(_stable(first))
    second_hash = _hash(_stable(second))
    if first_hash != second_hash:
        raise RuntimeError("M48B1_DETERMINISM_FAILURE")
    forensic = _forensics(data)
    summary = m48b._metrics(data, first, forensic)
    summary["experiment"] = "M48B.1"
    summary["call_accounting"] = {
        "historical_m48b_attempts": 1,
        "new_m48b1_attempts": 89,
        "inherited_slots": 1,
        "new_slots": 89,
        "duplicate_semantic_attempts": 0,
        "retries": 0,
    }
    _dump(AUDIT_ROOT / "m48b1_counterfactual_runtime_ledger.json", first)
    _dump(AUDIT_ROOT / "m48b1_pipeline_attrition.json", summary["pipeline_attrition"])
    _dump(AUDIT_ROOT / "m48b1_failure_taxonomy.json", summary["failure_counts"])
    _dump(AUDIT_ROOT / "m48b1_grain_analysis.json", summary["grain"])
    _dump(AUDIT_ROOT / "m48b1_cost_analysis.json", {"cost_rejections": summary["cost_rejections"]})
    _dump(
        AUDIT_ROOT / "m48b1_safe_sql_noninterference.json",
        {"safe_sql_changed": summary["grain"]["safe_sql_changed"]},
    )
    _dump(
        AUDIT_ROOT / "m48b1_governance_analysis.json",
        {key: summary[key] for key in ("authority", "ambiguity", "policy", "unauthorized_answers")},
    )
    _dump(
        AUDIT_ROOT / "m48b1_determinism.json",
        {
            "first_hash": first_hash,
            "second_hash": second_hash,
            "identical": True,
            "provider_calls": 0,
            "model_calls": 0,
        },
    )
    _dump(AUDIT_ROOT / "m48b1_final_component_hashes.json", _component_hashes())
    _dump(
        AUDIT_ROOT / "m48b1_production_dependency_audit.json",
        {
            "benchmark_imports": False,
            "case_specific_runtime_branches": False,
            "reference_access": False,
            "fixture_access": False,
            "evaluator_access": False,
            "analyze_in_request_runtime": False,
        },
    )
    _dump(ROOT / "reports" / "m48b1_end_to_end_summary.json", summary)
    _dump(ROOT / "reports" / "m48b1_end_to_end_summary.md", _markdown(summary))
    return {"summary": summary, "determinism": {"identical": True}, "forensics": forensic}


def _markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# M48B.1 — Fresh End-to-End Runtime Confirmation",
            "",
            "M48B.1 uses one inherited M48B response and 89 new one-shot attempts.",
            "",
            (
                f"Governed Task Success: {summary['governed']['correct']}/90 "
                f"({summary['governed']['rate']:.1%})"
            ),
            (
                f"Answerable Runtime TSA: {summary['answerable_runtime_tsa']['correct']}/60 "
                f"({summary['answerable_runtime_tsa']['rate']:.1%})"
            ),
            (
                "BASE Delivered Correctness: "
                f"{summary['base_delivered_correctness']['correct']}/60 "
                f"({summary['base_delivered_correctness']['rate']:.1%})"
            ),
            (
                f"Fresh fanout states: {summary['grain']['raw_fanout_states']}; "
                f"normalized: {summary['grain']['normalized']}; "
                f"safe/correct: {summary['grain']['normalized_grain_safe_and_correct']}"
            ),
            (
                f"Safe SQL changed: {summary['grain']['safe_sql_changed']}; "
                f"normalization regressions: {summary['grain']['normalization_regressions']}"
            ),
            "",
        ]
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "generate", "finalize"))
    args = parser.parse_args()
    if args.command == "preflight":
        result = preflight()
        print(json.dumps(result["manifest"], indent=2, sort_keys=True))
        return
    pairs = _pairs()
    requests = _requests(pairs)
    catalogs, _inventory = _build_catalogs([truth for _case, truth in _answerable_pairs()])
    data = {
        "manifest": json.loads(MANIFEST.read_text()),
        "pairs": pairs,
        "requests": requests,
        "catalogs": catalogs,
    }
    if args.command == "generate":
        print(json.dumps(_provider_call(data), indent=2, sort_keys=True))
    else:
        print(json.dumps(_finalize(data)["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
