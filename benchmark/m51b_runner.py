"""M51B independent confirmation over the frozen M51A expansion.

The live phase makes exactly one retained-mainline request per expansion case.
All scoring and runtime replay occur from the persisted response ledger after
the live corpus is frozen.  This module contains no treatment or M50C path.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as psycopg_sql

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m39_runner as m39
from benchmark import m46a_audit as m46a
from benchmark import m48a_audit as m48a
from benchmark import m48b1_runner as m48b1
from benchmark import m48b_runner as m48b
from benchmark import m51a_authoring as m51a
from benchmark.m46b_contract import m43_prompt
from benchmark.model_contract import (
    FORBIDDEN_REQUEST_TERMS,
    serialize_governed_context_v1,
    sha256_bytes,
    sha256_text,
    submission_schema,
)
from benchmark.models import ResultContract, Submission, compare_rows

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m51b"
MANIFEST = ROOT / "manifests" / "m51b_independent_expansion_confirmation_manifest.json"
EXPANSION_MANIFEST = ROOT / "manifests" / "m51a_expansion_90_manifest.json"
FULL_MANIFEST = ROOT / "manifests" / "m51a_180_case_manifest.json"
EXPANSION_CASES = ROOT / "cases" / "m51_expansion"
EXPANSION_TRUTH = ROOT / "ground_truth" / "m51_expansion"
TRUTH_VERSION = "0.3.0-dev"
EXPANSION_TRUTH_HASH = "7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9"
FULL_TRUTH_HASH = "b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173"
PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
PLANNER_HASH = "a97222f4e036af28120a4ee12d9ef4352513796f54a15d6f2050b56a6ae77863"
STARTING_HEAD = "645e77d3b9158e6b2e2d07185da04f001c92176a"
DATABASES = tuple(domain.domain_id for domain in m51a.DOMAINS)
SCHEMAS = {domain.domain_id: domain.schema_name for domain in m51a.DOMAINS}
DECISION_BY_TASK = {
    "ANSWERABLE": "ANSWER",
    "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
    "AMBIGUOUS": "NEEDS_CLARIFICATION",
    "POLICY_BLOCKED": "BLOCKED_POLICY",
}


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
        import os

        os.fsync(handle.fileno())


def _git() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _origin() -> str:
    return subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _clean() -> bool:
    return not subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _rows() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    manifest = json.loads(EXPANSION_MANIFEST.read_text(encoding="utf-8"))
    ids = [str(value) for value in manifest["case_ids"]]
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for case_id in ids:
        case = json.loads((EXPANSION_CASES / f"{case_id}.json").read_text(encoding="utf-8"))
        truth = json.loads((EXPANSION_TRUTH / f"{case_id}.json").read_text(encoding="utf-8"))
        rows[case_id] = (case, truth)
    if len(ids) != 90 or len(set(ids)) != 90 or set(ids) != set(rows):
        raise RuntimeError("M51B_EXPANSION_CASE_COUNT_MISMATCH")
    return ids, rows


def _requests(
    ids: list[str], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> list[dict[str, Any]]:
    prompt = m43_prompt()
    result = []
    for index, case_id in enumerate(ids, 1):
        case = rows[case_id][0]
        context = serialize_governed_context_v1(str(case["database_id"]))
        user = (
            f"Case ID:\n{case_id}\n\nQuestion:\n{case['question']}\n\nGoverned context:\n{context}"
        )
        request_text = "SYSTEM:\n" + prompt + "\n\nUSER:\n" + user
        result.append(
            {
                "case_index": index,
                "case_id": case_id,
                "database_id": case["database_id"],
                "domain": case["database_id"],
                "question": case["question"],
                "question_sha256": sha256_text(str(case["question"])),
                "prompt_sha256": sha256_text(prompt),
                "context_sha256": sha256_text(context),
                "serialized_context": context,
                "instructions": prompt,
                "user_text": user,
                "request_text": request_text,
                "request_sha256": sha256_text(request_text),
                "request_bytes": len(request_text.encode()),
            }
        )
    return result


def _patch_runtime_for_expansion() -> None:
    # The generic retained runtime resolves schema/seed through these hooks.
    # Only the benchmark database loader is redirected; no app behavior changes.
    m48a._schema_name = lambda database_id: SCHEMAS[database_id]

    def seed_expansion(database_id: str) -> None:
        m51a.seed_database(database_id)

    m48a._seed = seed_expansion
    m46a.DATABASES = list(DATABASES)


def _admin_kwargs() -> dict[str, Any]:
    from urllib.parse import urlparse

    raw = get_settings().admin_database_url.replace("postgresql+psycopg://", "postgresql://")
    parsed = urlparse(raw)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/decision_sql").lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
    }


def _restore_reader(database_id: str) -> None:
    schema = SCHEMAS[database_id]
    settings = get_settings()
    with psycopg.connect(**_admin_kwargs()) as connection:
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


def _prepare(database_id: str, fixture: dict[str, Any]) -> dict[str, Any]:
    # m48b1 adds the retained post-ANALYZE reader-grant restoration.
    return m48b1._prepare_state(database_id, fixture)


def _runtime_setup(
    answerable_truths: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _patch_runtime_for_expansion()
    catalogs, inventory = m46a._build_catalogs(answerable_truths)
    return catalogs, inventory


def _reference_canary(
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]], services: dict[str, Any]
) -> dict[str, Any]:
    records = []
    for database_id in DATABASES:
        candidate = next(
            (
                pair
                for pair in rows.values()
                if pair[0]["database_id"] == database_id and pair[0]["task_type"] == "ANSWERABLE"
            ),
            None,
        )
        if candidate is None:
            raise RuntimeError(f"M51B_NO_REFERENCE_CANARY:{database_id}")
        case, truth = candidate
        fixture = {"fixture_id": "base", "patch_sql": []}
        prep = _prepare(database_id, fixture)
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        a = m48b._runtime(
            services[database_id], truth["reference_implementation_a"]["sql"], contract, []
        )
        b = m48b._runtime(
            services[database_id], truth["reference_implementation_b"]["sql"], contract, []
        )
        if not a.get("executed") or not b.get("executed"):
            raise RuntimeError(f"M51B_REFERENCE_CANARY_FAILURE:{case['case_id']}")
        same, reason = compare_rows(m48b._rows(a), m48b._rows(b), contract)
        if not same:
            raise RuntimeError(f"M51B_REFERENCE_CANARY_DISAGREE:{case['case_id']}:{reason}")
        records.append(
            {
                "database_id": database_id,
                "case_id": case["case_id"],
                "prepared": prep,
                "equivalent": same,
                "reason": reason,
            }
        )
    return {"provider_calls": 0, "model_calls": 0, "records": records, "passed": True}


def _preflight() -> dict[str, Any]:
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    allowed_dirty = {
        "benchmark/m51b_runner.py",
        "benchmark/manifests/m51b_independent_expansion_confirmation_manifest.json",
    }
    unrelated_dirty = [
        line
        for line in dirty
        if line[3:] not in allowed_dirty and not line[3:].startswith("benchmark/audits/m51b/")
    ]
    if _git() != STARTING_HEAD or _origin() != STARTING_HEAD or unrelated_dirty:
        raise RuntimeError("M51B_STARTING_REPOSITORY_MISMATCH")
    manifest = json.loads(EXPANSION_MANIFEST.read_text(encoding="utf-8"))
    full = json.loads(FULL_MANIFEST.read_text(encoding="utf-8"))
    ids, rows = _rows()
    distribution = Counter(case["task_type"] for case, _truth in rows.values())
    if (
        manifest.get("truth_hash") != EXPANSION_TRUTH_HASH
        or full.get("full_truth_hash") != FULL_TRUTH_HASH
    ):
        raise RuntimeError("M51B_BENCHMARK_TRUTH_DRIFT")
    if (
        manifest.get("case_count") != 90
        or manifest.get("domain_count") != 6
        or dict(distribution)
        != {"ANSWERABLE": 60, "AUTHORITY_BLOCKED": 15, "AMBIGUOUS": 9, "POLICY_BLOCKED": 6}
    ):
        raise RuntimeError("M51B_EXPANSION_DISTRIBUTION_MISMATCH")
    prompt = m43_prompt()
    if sha256_text(prompt) != PROMPT_HASH:
        raise RuntimeError("M51B_PROMPT_DRIFT")
    leakage = []
    for request in _requests(ids, rows):
        hits = [term for term in FORBIDDEN_REQUEST_TERMS if term in request["request_text"].lower()]
        if hits:
            leakage.append({"case_id": request["case_id"], "hits": hits})
    if leakage:
        raise RuntimeError("M51B_LEAKAGE_PRELIGHT")
    _patch_runtime_for_expansion()
    answerable = [truth for case, truth in rows.values() if case["task_type"] == "ANSWERABLE"]
    catalogs, inventory = _runtime_setup(answerable)
    services = m48b._runtime_services(catalogs)
    reference_validation = json.loads(
        (ROOT / "audits" / "m51a" / "m51a_reference_validation.json").read_text(encoding="utf-8")
    )
    if (
        reference_validation.get("valid_references") != 120
        or reference_validation.get("reference_runs") != 360
        or reference_validation.get("reference_failures")
    ):
        raise RuntimeError("M51B_REFERENCE_INTEGRITY_FAILURE")
    canary = _reference_canary(rows, services)
    reader: dict[str, Any] = {"databases": [], "passed": True}
    for database_id in DATABASES:
        prep = _prepare(database_id, {"fixture_id": "base", "patch_sql": []})
        service = services[database_id]
        probe = m48b._runtime(
            service, "SELECT 1", ResultContract(column_count=1, row_order=True), []
        )
        if not probe.get("executed"):
            raise RuntimeError(f"M51B_READER_PREFLIGHT_FAILURE:{database_id}")
        reader["databases"].append(
            {"database_id": database_id, "preparation": prep, "probe": probe}
        )
    settings = get_settings()
    if settings.max_plan_cost != 100000.0 or settings.max_plan_rows != 100000:
        raise RuntimeError("M51B_COST_POLICY_DRIFT")
    config = {
        "model": "gpt-5.6-luna",
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": 90,
        "calls_per_case": 1,
        "retries": 0,
        "repair": False,
        "judge": False,
        "selector": False,
        "router": False,
    }
    schedule = [
        {"call_index": index, "case_index": index, "case_id": case_id}
        for index, case_id in enumerate(ids, 1)
    ]
    result = {
        "experiment": "M51B",
        "starting_head": _git(),
        "origin_main": _origin(),
        "clean": _clean(),
        "provider_calls": 0,
        "model_calls": 0,
        "expansion_manifest_hash": _sha(EXPANSION_MANIFEST),
        "full_manifest_hash": _sha(FULL_MANIFEST),
        "truth_hash": EXPANSION_TRUTH_HASH,
        "full_truth_hash": FULL_TRUTH_HASH,
        "case_count": 90,
        "domain_count": 6,
        "task_distribution": dict(sorted(distribution.items())),
        "prompt_hash": PROMPT_HASH,
        "model_config": config,
        "planner_contract_hash": PLANNER_HASH,
        "case_order": ids,
        "schedule_hash": _hash(schedule),
        "leakage": leakage,
        "input_factual_facts_added": 0,
        "factual_context_growth": 0,
        "reference_canary": canary,
        "reference_validation": {
            "valid_references": reference_validation["valid_references"],
            "reference_runs": reference_validation["reference_runs"],
        },
        "reader_preflight": reader,
        "inventory": inventory,
    }
    _dump(
        AUDIT / "m51b_historical_preservation.json",
        {
            "experiment": "M51B",
            "starting_head": _git(),
            "provider_calls": 0,
            "model_calls": 0,
            "official_m48b2": {"governed": "78/90", "answerable_runtime_tsa": "51/60"},
        },
    )
    _dump(
        AUDIT / "m51b_expansion_manifest_integrity.json",
        {
            "manifest_hash": result["expansion_manifest_hash"],
            "case_count": 90,
            "domain_count": 6,
            "distribution": result["task_distribution"],
            "passed": True,
        },
    )
    _dump(
        AUDIT / "m51b_truth_integrity.json",
        {
            "expansion_truth_hash": EXPANSION_TRUTH_HASH,
            "full_truth_hash": FULL_TRUTH_HASH,
            "passed": True,
        },
    )
    _dump(AUDIT / "m51b_prompt_integrity.json", {"prompt_hash": PROMPT_HASH, "passed": True})
    _dump(AUDIT / "m51b_model_config.json", config)
    _dump(
        AUDIT / "m51b_call_schedule.json",
        {"schedule": schedule, "schedule_hash": result["schedule_hash"], "provider_calls": 0},
    )
    _dump(AUDIT / "m51b_reader_preflight.json", reader)
    _dump(AUDIT / "m51b_reference_canary.json", canary)
    _dump(AUDIT / "m51b_leakage_preflight.json", {"leakage": leakage, "passed": True})
    _dump(AUDIT / "m51b_preflight.json", result)
    source_paths = {
        "provider_adapter": REPO / "app/generation/provider.py",
        "normalizer": REPO / "app/semantics/grain_normalizer.py",
        "grain_validator": REPO / "app/semantics/grain.py",
        "grain_runtime": REPO / "app/semantics/grain_runtime.py",
        "sql_parser": REPO / "app/sql/parser.py",
        "sql_policy": REPO / "app/sql/policy.py",
        "cost": REPO / "app/execution/cost.py",
        "reader": REPO / "app/execution/reader.py",
        "sql_service": REPO / "app/sql/service.py",
        "evaluator": ROOT / "evaluator.py",
        "planner_contract": ROOT / "manifests" / "m48a2_planner_statistics_contract.json",
        "submission_schema": ROOT / "schemas" / "model_submission.schema.json",
        "m43_ledger": ROOT / "experiments" / "results" / "m43" / "m43_request_ledger.json",
    }
    frozen_manifest = {
        "experiment": "M51B",
        "development_experiment": True,
        "starting_head": _git(),
        "prelive_freeze_head": _git(),
        "expansion_manifest_hash": result["expansion_manifest_hash"],
        "expansion_truth_hash": EXPANSION_TRUTH_HASH,
        "full_180_truth_hash": FULL_TRUTH_HASH,
        "prompt_hash": PROMPT_HASH,
        "model": config["model"],
        "reasoning": config["reasoning"],
        "temperature": config["temperature"],
        "timeout": config["timeout_seconds"],
        "scheduled_calls": 90,
        "provider_attempts": 0,
        "successful_responses": 0,
        "retries": 0,
        "post_freeze_calls": 0,
        "provider_adapter_hash": _sha(source_paths["provider_adapter"]),
        "runtime_source_hashes": {key: _sha(path) for key, path in source_paths.items()},
        "planner_contract_hash": PLANNER_HASH,
        "case_order_hash": result["schedule_hash"],
        "reference_validation": result["reference_validation"],
        "leakage": {"count": 0},
        "freeze_status": "FROZEN_BEFORE_FIRST_PROVIDER_CALL",
    }
    _dump(MANIFEST, frozen_manifest)
    return {
        "manifest": result,
        "requests": _requests(ids, rows),
        "rows": rows,
        "catalogs": catalogs,
        "services": services,
    }


def _generation(data: dict[str, Any]) -> dict[str, Any]:
    path = AUDIT / "m51b_expansion_responses.jsonl"
    if path.exists():
        raise RuntimeError("M51B_RESPONSE_ARTIFACT_EXISTS")
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M51B_PROVIDER_BLOCKED_API_KEY")
    provider_settings = settings.model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": 0.0,
            "llm_timeout_seconds": 90,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(provider_settings)
    rows: list[dict[str, Any]] = []
    for request in data["requests"]:
        started = time.perf_counter()
        provider.consume_response_wire()
        payload: Any = None
        error: Exception | None = None
        try:
            payload = asyncio.run(
                provider.complete_json_schema(
                    operation="m51b_expansion_submission",
                    system_prompt=request["instructions"],
                    user_prompt=request["user_text"],
                    schema_name="decision_sql_m51b_submission",
                    schema=submission_schema(),
                )
            )
        except Exception as exc:
            error = exc
        latency = (time.perf_counter() - started) * 1000
        capture = provider.consume_model_io()
        wire = provider.consume_response_wire()
        content = getattr(capture, "raw_assistant_content_full", None)
        parsed, parse_status, parse_detail, parsed_value = (
            m39._parse(content, request["case_id"])
            if error is None
            else (None, m39._classify_provider_error(error)[0], str(error), None)
        )
        response_hash = sha256_bytes(wire) if wire is not None else None
        metadata = m39._provider_metadata(payload or {}, capture)
        row = {
            "call_index": request["case_index"],
            "case_id": request["case_id"],
            "domain": request["domain"],
            "request_hash": request["request_sha256"],
            "prompt_hash": PROMPT_HASH,
            "context_hash": request["context_sha256"],
            "model": "gpt-5.6-luna",
            "reasoning": "none",
            "temperature": 0.0,
            "timeout_seconds": 90,
            "provider_attempt": 1,
            "provider_outcome": "SUCCESS" if error is None else "FAILURE",
            "provider_response_id": metadata.get("provider_response_id"),
            "provider_error": None
            if error is None
            else {"type": type(error).__name__, "message": str(error)[:400]},
            "latency_ms": latency,
            "usage": metadata.get("usage", {}),
            "raw_response_base64": base64.b64encode(wire).decode() if wire is not None else None,
            "raw_response_hash": response_hash,
            "parsed_submission": parsed_value,
            "parse_status": parse_status,
            "parse_detail": parse_detail,
            "decision": parsed.decision if parsed is not None else None,
            "sql": parsed.sql if parsed is not None else None,
            "sql_hash": sha256_text(parsed.sql)
            if parsed is not None and parsed.sql is not None
            else None,
        }
        _append(path, row)
        rows.append(row)
    if len(rows) != 90:
        raise RuntimeError("M51B_GENERATION_INCOMPLETE")
    return {"rows": rows, "corpus_hash": _sha(path)}


def _load_responses() -> list[dict[str, Any]]:
    path = AUDIT / "m51b_expansion_responses.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows.sort(key=lambda row: int(row["call_index"]))
    if len(rows) != 90 or len({row["case_id"] for row in rows}) != 90:
        raise RuntimeError("M51B_RESPONSE_INTEGRITY")
    return rows


def _replay(
    data: dict[str, Any], responses: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    services = data["services"]
    records: list[dict[str, Any]] = []
    runtime_first: Counter[str] = Counter()
    for response in responses:
        case, truth = data["rows"][response["case_id"]]
        parsed = response.get("parsed_submission")
        rec: dict[str, Any] = {
            "case_id": case["case_id"],
            "task_type": case["task_type"],
            "decision": response.get("decision"),
            "parse_status": response.get("parse_status"),
            "states": [],
            "governed_correct": False,
            "base_correct": False,
            "full_counterfactual_correct": False,
            "first_failure": None,
        }
        if response.get("parse_status") != "PASS" or not isinstance(parsed, dict):
            rec["first_failure"] = "SUBMISSION"
            runtime_first["SUBMISSION"] += 1
            records.append(rec)
            continue
        submission = Submission.from_dict_unchecked(parsed)
        expected_decision = DECISION_BY_TASK[case["task_type"]]
        if submission.decision != expected_decision:
            rec["first_failure"] = "DECISION"
            runtime_first["NONE"] += 1
            records.append(rec)
            continue
        if case["task_type"] != "ANSWERABLE":
            rec["governed_correct"] = submission.sql is None
            if not rec["governed_correct"]:
                rec["first_failure"] = "DECISION"
            runtime_first["NONE"] += 1
            records.append(rec)
            continue
        if submission.sql is None:
            rec["first_failure"] = "SUBMISSION"
            runtime_first["SUBMISSION"] += 1
            records.append(rec)
            continue
        bundle_contract = ResultContract.from_dict(
            truth["semantic_target"]["result_comparison_contract"]
        )
        states = []
        for fixture in [
            {"fixture_id": "base", "patch_sql": []},
            *truth.get("counterfactual_fixtures", []),
        ]:
            prep = _prepare(case["database_id"], fixture)
            oracle = m48b._runtime(
                services[case["database_id"]],
                truth["reference_implementation_a"]["sql"],
                bundle_contract,
                [],
            )
            expected_rows = m48b._rows(oracle)
            outcome = m48b._state_runtime(
                services[case["database_id"]], submission.sql, case, truth, expected_rows
            )
            states.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "preparation_hash": prep["metadata_hash"],
                    "outcome": outcome,
                }
            )
        rec["states"] = states
        rec["base_correct"] = bool(states and states[0]["outcome"]["result_contract_outcome"])
        rec["full_counterfactual_correct"] = bool(
            states and all(item["outcome"]["result_contract_outcome"] for item in states)
        )
        rec["governed_correct"] = rec["full_counterfactual_correct"]
        if not rec["governed_correct"]:
            statuses = [item["outcome"].get("runtime_disposition") for item in states]
            rec["first_failure"] = next(
                (
                    name
                    for name, value in (
                        ("SQL_PARSE", "SQL_PARSE_ERROR"),
                        ("POLICY", "POLICY_REJECTION"),
                        ("GRAIN", "SEMANTIC_REJECTION"),
                        ("COST", "QUERY_COST_REJECTION"),
                        ("EXECUTION", "EXECUTION_ERROR"),
                    )
                    if value in statuses
                ),
                "RESULT_COUNTERFACTUAL",
            )
            runtime_first[rec["first_failure"]] += 1
        else:
            runtime_first["NONE"] += 1
        records.append(rec)
    return records, dict(runtime_first)


def _metrics(
    data: dict[str, Any],
    responses: list[dict[str, Any]],
    records: list[dict[str, Any]],
    runtime_first: dict[str, Any],
) -> dict[str, Any]:
    governed = sum(bool(row["governed_correct"]) for row in records)
    answerable = [row for row in records if row["task_type"] == "ANSWERABLE"]
    answer_correct = sum(bool(row["full_counterfactual_correct"]) for row in answerable)
    base_correct = sum(bool(row["base_correct"]) for row in answerable)
    answers = [row for row in records if row["decision"] == "ANSWER"]
    task_counts = Counter(
        row["decision"] if row["parse_status"] == "PASS" else "INVALID_SUBMISSION"
        for row in records
    )
    domain: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"governed": 0, "total": 0, "answerable_correct": 0}
    )
    for row in records:
        domain[row["case_id"].rsplit("_", 1)[0]]["total"] += 1
        domain[row["case_id"].rsplit("_", 1)[0]]["governed"] += int(row["governed_correct"])
        domain[row["case_id"].rsplit("_", 1)[0]]["answerable_correct"] += int(
            row["task_type"] == "ANSWERABLE" and row["governed_correct"]
        )
    usage_prompt = [
        row["usage"].get("prompt_tokens")
        for row in responses
        if isinstance(row.get("usage", {}).get("prompt_tokens"), int)
    ]
    usage_completion = [
        row["usage"].get("completion_tokens")
        for row in responses
        if isinstance(row.get("usage", {}).get("completion_tokens"), int)
    ]
    latency = [
        row["latency_ms"] for row in responses if isinstance(row.get("latency_ms"), (int, float))
    ]
    return {
        "governed": {"correct": governed, "total": 90, "rate": governed / 90},
        "answerable_runtime_tsa": {
            "correct": answer_correct,
            "total": 60,
            "rate": answer_correct / 60,
        },
        "base_correct": {"correct": base_correct, "total": 60, "rate": base_correct / 60},
        "answer_selections": len(answers),
        "wrong_refusals": sum(
            row["task_type"] == "ANSWERABLE" and row["decision"] != "ANSWER" for row in records
        ),
        "conditional_answer_correct": sum(
            row["task_type"] == "ANSWERABLE"
            and row["decision"] == "ANSWER"
            and row["governed_correct"]
            for row in records
        ),
        "decision_distribution": dict(sorted(task_counts.items())),
        "authority": {
            "correct": sum(
                row["task_type"] == "AUTHORITY_BLOCKED" and row["governed_correct"]
                for row in records
            ),
            "total": 15,
        },
        "ambiguity": {
            "correct": sum(
                row["task_type"] == "AMBIGUOUS" and row["governed_correct"] for row in records
            ),
            "total": 9,
        },
        "policy": {
            "correct": sum(
                row["task_type"] == "POLICY_BLOCKED" and row["governed_correct"] for row in records
            ),
            "total": 6,
        },
        "unauthorized_answers": sum(
            row["task_type"] == "AUTHORITY_BLOCKED" and row["decision"] == "ANSWER"
            for row in records
        ),
        "domains": dict(sorted(domain.items())),
        "runtime_first_failures": runtime_first,
        "usage": {
            "prompt_tokens": _summary_values(usage_prompt),
            "completion_tokens": _summary_values(usage_completion),
        },
        "latency_ms": _summary_values(latency),
    }


def _summary_values(values: list[Any]) -> dict[str, Any]:
    nums = [float(value) for value in values]
    if not nums:
        return {"count": 0, "median": None, "p90": None, "max": None, "total": None}
    ordered = sorted(nums)
    return {
        "count": len(nums),
        "median": statistics.median(nums),
        "p90": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))],
        "max": max(nums),
        "total": sum(nums),
    }


def _analysis(data: dict[str, Any]) -> dict[str, Any]:
    responses = _load_responses()
    records, runtime_first = _replay(data, responses)
    metrics = _metrics(data, responses, records, runtime_first)
    overlays = []
    for rec in records:
        truth = data["rows"][rec["case_id"]][1]
        overlays.append(
            {
                "case_id": rec["case_id"],
                "task_type": truth["semantic_target"]["behavior"],
                "decision": rec["decision"],
                "official_correct": rec["governed_correct"],
                "first_divergence": "NONE"
                if rec["governed_correct"]
                else (
                    "DECISION_FALSE_ABSTENTION"
                    if truth["semantic_target"]["behavior"] == "ANSWERABLE"
                    and rec["decision"] != "ANSWER"
                    else "DECISION_FALSE_ANSWER"
                    if truth["semantic_target"]["behavior"] != "ANSWERABLE"
                    and rec["decision"] == "ANSWER"
                    else rec.get("first_failure")
                ),
            }
        )
    transitions = Counter(
        f"{data['rows'][rec['case_id']][0]['task_type']}->{rec['decision'] or 'INVALID'}"
        for rec in records
    )
    target_ids = [
        "procurement_01",
        "procurement_02",
        "procurement_03",
        "procurement_04",
        "procurement_05",
        "procurement_06",
        "procurement_07",
    ]
    target_analysis = [
        {
            "case_id": case_id,
            "decision": next(row["decision"] for row in records if row["case_id"] == case_id),
            "correct": next(
                row["governed_correct"] for row in records if row["case_id"] == case_id
            ),
        }
        for case_id in target_ids
        if case_id in data["rows"]
    ]
    artifacts = {
        "m51b_runtime_traces.jsonl": records,
        "m51b_evaluator_overlays.jsonl": overlays,
        "m51b_expansion_metrics.json": metrics,
        "m51b_answerable_metrics.json": {
            "answerable": metrics["answerable_runtime_tsa"],
            "base": metrics["base_correct"],
        },
        "m51b_governance_metrics.json": {
            key: metrics[key]
            for key in ("authority", "ambiguity", "policy", "unauthorized_answers")
        },
        "m51b_truth_decision_matrix.json": {
            task: {
                decision: sum(
                    data["rows"][r["case_id"]][0]["task_type"] == task and r["decision"] == decision
                    for r in records
                )
                for decision in (
                    "ANSWER",
                    "NEEDS_CLARIFICATION",
                    "BLOCKED_AUTHORITY",
                    "BLOCKED_POLICY",
                    None,
                )
            }
            for task in ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED")
        },
        "m51b_domain_metrics.json": metrics["domains"],
        "m51b_runtime_first_failures.json": runtime_first,
        "m51b_evaluator_first_divergences.json": dict(
            Counter(row["first_divergence"] for row in overlays)
        ),
        "m51b_failure_decomposition.json": {
            "total_failures": 90 - metrics["governed"]["correct"],
            "by_first_failure": dict(
                Counter(row.get("first_failure") for row in records if not row["governed_correct"])
            ),
        },
        "m51b_token_accounting.json": metrics["usage"],
        "m51b_latency.json": metrics["latency_ms"],
        "m51b_call_accounting.json": {
            "scheduled": 90,
            "attempted": 90,
            "successful_provider_responses": sum(
                row["provider_outcome"] == "SUCCESS" for row in responses
            ),
            "retries": 0,
            "post_freeze_calls": 0,
        },
        "m51b_suspected_benchmark_defects.json": [],
    }
    for filename, value in artifacts.items():
        path = AUDIT / filename
        if filename.endswith(".jsonl"):
            path.write_text(
                "\n".join(json.dumps(item, sort_keys=True, default=str) for item in value) + "\n"
            )
        else:
            _dump(path, value)
    result = {
        "experiment": "M51B",
        "provider_calls": 0,
        "model_calls": 0,
        "responses": len(responses),
        "response_corpus_hash": _sha(AUDIT / "m51b_expansion_responses.jsonl"),
        "runtime_trace_hash": _sha(AUDIT / "m51b_runtime_traces.jsonl"),
        "evaluator_overlay_hash": _sha(AUDIT / "m51b_evaluator_overlays.jsonl"),
        "metrics": metrics,
        "transitions": dict(sorted(transitions.items())),
        "targets": target_analysis,
    }
    _dump(
        AUDIT / "m51b_determinism.json",
        {"analysis_hash": _hash(result), "provider_calls": 0, "model_calls": 0},
    )
    _dump(
        AUDIT / "m51b_generalization_comparison.json",
        {
            "legacy_governed": "78/90",
            "expansion_governed": f"{metrics['governed']['correct']}/90",
            "legacy_answerable_tsa": "51/60",
            "expansion_answerable_tsa": f"{metrics['answerable_runtime_tsa']['correct']}/60",
        },
    )
    _dump(
        AUDIT / "m51b_combined_180_metrics.json",
        {
            "governed": {"correct": 78 + metrics["governed"]["correct"], "total": 180},
            "answerable_runtime_tsa": {
                "correct": 51 + metrics["answerable_runtime_tsa"]["correct"],
                "total": 120,
            },
            "authority": {"correct": 15 + metrics["authority"]["correct"], "total": 30},
            "ambiguity": {"correct": 6 + metrics["ambiguity"]["correct"], "total": 18},
            "policy": {"correct": 6 + metrics["policy"]["correct"], "total": 12},
        },
    )
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "generate", "analyze"))
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(_preflight()["manifest"], indent=2, sort_keys=True))
        return
    if args.command == "generate":
        generation_data = _preflight()
        print(json.dumps(_generation(generation_data), indent=2, sort_keys=True))
        return
    data: dict[str, Any] | None = (
        _preflight() if not (AUDIT / "m51b_preflight.json").exists() else None
    )
    if data is None:
        ids, rows = _rows()
        _patch_runtime_for_expansion()
        catalogs, _inventory = _runtime_setup(
            [truth for case, truth in rows.values() if case["task_type"] == "ANSWERABLE"]
        )
        data = {"rows": rows, "catalogs": catalogs, "services": m48b._runtime_services(catalogs)}
    print(json.dumps(_analysis(data), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
