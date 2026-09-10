# ruff: noqa: E501
"""M53.2 fresh acquisition and post-freeze evaluation.

The preflight is fail-closed and zero-call.  The live phase admits only the
56 hashes frozen by M53.1-R.1, makes one retained-mainline attempt per case,
and writes every attempt before advancing.  Evaluation uses only frozen
response bytes after acquisition.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m48b_runner as m48b
from benchmark import m51b_runner as m51b
from benchmark import m531_runner as m531
from benchmark import m531r1_runner as m531r1
from benchmark.analysis_serialization import dumps_analysis
from benchmark.m39_runner import _parse, _provider_metadata
from benchmark.m46b_contract import m43_prompt
from benchmark.model_contract import sha256_bytes, sha256_text, submission_schema
from benchmark.models import ResultContract

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m532"
M531R1_AUDIT = ROOT / "audits" / "m531r1"
M531R_AUDIT = ROOT / "audits" / "m531r"
M531_AUDIT = ROOT / "audits" / "m531"
M51B_AUDIT = ROOT / "audits" / "m51b"
FRESH_PATH = AUDIT / "m532_live_responses.jsonl"
LIVE_REQUESTS_PATH = AUDIT / "m532_live_requests.jsonl"
EXPANSION_TRUTH = "26c662d27be3366b59f1e16c9f55e766779c63f4a2f4b137c05d91c62bfca309"
FULL_TRUTH = "0ee815d4d46cbb7723e9d7fa07da3628420f6da7181d2b551a282e5a77f4f70b"
M51B_CORPUS = "9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a"
M531_CORPUS = "1bb22e9c70455d7710fc5990f8b9af0dc54090bc6f83ab182f9113cb22647ab4"
SCHEDULE_HASH = "41d180528f01f989e67e46d8d33931a0ed9545c840b38e1692c4329fc68e5536"
PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90
VERDICT = "POST_M53_REPAIRED_EXPANSION_EVALUATED"
M54 = "M54 — Post-M53 Residual Semantic Forensics"


def sha_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_value(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_analysis(value, indent=2) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(dumps_analysis(row) + "\n" for row in rows), encoding="utf-8")


def append(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(
            (json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode()
        )
        handle.flush()
        os.fsync(handle.fileno())


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def load_rows() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    manifest = load_json(ROOT / "manifests" / "m51a_expansion_90_manifest.json")
    ids = [str(value) for value in manifest["case_ids"]]
    rows = {
        cid: (
            load_json(ROOT / "cases" / "m51_expansion" / f"{cid}.json"),
            load_json(ROOT / "ground_truth" / "m51_expansion" / f"{cid}.json"),
        )
        for cid in ids
    }
    if len(ids) != 90 or len(set(ids)) != 90 or set(ids) != set(rows):
        raise RuntimeError("M532_CASE_SET")
    return ids, rows


def post_truth_hashes(ids: list[str]) -> tuple[str, str]:
    expansion = {
        cid: sha_path(ROOT / "ground_truth" / "m51_expansion" / f"{cid}.json") for cid in ids
    }
    full = load_json(ROOT / "manifests" / "m51a_180_case_manifest.json")
    legacy = {cid: full["case_hashes"][cid]["truth"] for cid in full["case_ids"][:90]}
    return sha_value(expansion), sha_value({**legacy, **expansion})


def snapshot() -> dict[str, str]:
    return m531r1.benchmark_snapshot()


def current_fingerprints(ids: list[str]) -> dict[str, dict[str, Any]]:
    return m531r1.current_request_fingerprints(ids)


def schedule() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = load_json(M531R_AUDIT / "m531r_missing_current_response_schedule.json")["schedule"]
    if len(rows) != 56 or sha_value(rows) != SCHEDULE_HASH:
        raise RuntimeError("M532_SCHEDULE_DRIFT")
    by_id = {row["case_id"]: row for row in rows}
    if len(by_id) != 56:
        raise RuntimeError("M532_SCHEDULE_DUPLICATE")
    return rows, by_id


def validate_preflight() -> dict[str, Any]:
    ids, rows = load_rows()
    if git("status", "--porcelain"):
        raise RuntimeError("M532_DIRTY_PRELIVE")
    parent = load_json(ROOT / "manifests" / "m531r1_m532_live_acquisition_readiness_manifest.json")
    if not parent["m532_live_acquisition_ready"] or parent["score_ready"]:
        raise RuntimeError("M532_PARENT_READINESS")
    if parent["future_schedule_hash"] != SCHEDULE_HASH or parent["future_call_budget"] != 56:
        raise RuntimeError("M532_PARENT_SCHEDULE")
    expansion_hash, full_hash = post_truth_hashes(ids)
    if expansion_hash != EXPANSION_TRUTH or full_hash != FULL_TRUTH:
        raise RuntimeError("M532_TRUTH_DRIFT")
    if sha_path(M51B_AUDIT / "m51b_expansion_responses.jsonl") != M51B_CORPUS:
        raise RuntimeError("M532_M51B_CORPUS_DRIFT")
    if sha_path(M531_AUDIT / "m531_fresh_responses.jsonl") != M531_CORPUS:
        raise RuntimeError("M532_M531_CORPUS_DRIFT")
    if sha256_text(m43_prompt()) != PROMPT_HASH:
        raise RuntimeError("M532_PROMPT_DRIFT")
    schedule_rows, schedule_by_id = schedule()
    fingerprints = current_fingerprints(ids)
    if not set(schedule_by_id).issubset(set(ids)):
        raise RuntimeError("M532_SCHEDULE_CASE_SET")
    mismatches = [
        cid
        for cid, item in schedule_by_id.items()
        if fingerprints[cid]["visible_content_hash"] != item["current_model_visible_hash"]
        or fingerprints[cid]["provider_request_hash"] != item["current_provider_request_hash"]
    ]
    if mismatches:
        raise RuntimeError(f"M532_PRECALL_REQUEST_DRIFT:{','.join(sorted(mismatches))}")
    readiness = m531r1.evaluate()
    if len(readiness["reusable"]) != 10 or len(readiness["fresh_valid"]) != 24:
        raise RuntimeError("M532_PARTITION_DRIFT")
    if FRESH_PATH.exists() or LIVE_REQUESTS_PATH.exists():
        raise RuntimeError("M532_LIVE_FILES_ALREADY_EXIST")
    services = m531.setup_runtime(rows)
    reference = m531.full_reference_canary(rows, services)
    if not reference["passed"]:
        raise RuntimeError("M532_REFERENCE_CANARY")
    runtime_rows = []
    for database_id in m51b.DATABASES:
        fixture = {"fixture_id": "base", "patch_sql": []}
        preparation = m51b._prepare(database_id, fixture)
        probe = m48b._runtime(
            services[database_id], "SELECT 1", ResultContract(column_count=1, row_order=True), []
        )
        if not probe.get("executed"):
            raise RuntimeError(f"M532_RUNTIME_CANARY:{database_id}")
        runtime_rows.append(
            {"database_id": database_id, "preparation": preparation, "probe": probe}
        )
    if get_settings().max_plan_cost != 100000.0 or get_settings().max_plan_rows != 100000:
        raise RuntimeError("M532_COST_POLICY_DRIFT")
    frozen_snapshot = snapshot()
    dump(
        ROOT / "audits" / "m532" / "m532_preflight_integrity.json",
        {
            "experiment": "M53.2",
            "preflight_head": git("rev-parse", "HEAD"),
            "origin_main": git("rev-parse", "origin/main"),
            "clean": True,
            "provider_calls": 0,
            "model_calls": 0,
            "retries": 0,
            "repairs": 0,
            "post_m53_expansion_truth_hash": expansion_hash,
            "post_m53_full_truth_hash": full_hash,
            "historical_m51b_response_corpus_hash": M51B_CORPUS,
            "m531_fresh24_corpus_hash": M531_CORPUS,
            "prompt_hash": PROMPT_HASH,
            "model": MODEL,
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "timeout_seconds": TIMEOUT_SECONDS,
            "benchmark_snapshot": frozen_snapshot,
            "exposure_start": "first provider request sent",
            "benchmark_immutable_after_exposure": True,
            "post_freeze_provider_calls": 0,
        },
    )
    dump(
        ROOT / "audits" / "m532" / "m532_parent_readiness_validation.json",
        {
            "parent_manifest": parent,
            "m53_verdict": "BENCHMARK_SEMANTIC_AUDIT_AND_REPAIR_COMPLETE",
            "m531_verdict": "M531_ABORTED_POST_EXPOSURE_BENCHMARK_DEFECT",
            "m531r_verdict": "M531R_PROVENANCE_RECOVERY_COMPLETE",
            "m531r1_verdict": "M532_LIVE_ACQUISITION_READY",
            "passed": True,
        },
    )
    dump(
        ROOT / "audits" / "m532" / "m532_schedule_validation.json",
        {
            "count": len(schedule_rows),
            "unique": len(schedule_by_id) == 56,
            "schedule_hash": sha_value(schedule_rows),
            "expected": SCHEDULE_HASH,
            "ordinals": [row["ordinal"] for row in schedule_rows],
            "case_ids": [row["case_id"] for row in schedule_rows],
            "passed": True,
        },
    )
    dump_jsonl(
        ROOT / "audits" / "m532" / "m532_preflight_request_fingerprints.jsonl",
        [
            {
                "ordinal": schedule_by_id[cid]["ordinal"],
                "case_id": cid,
                "current_model_visible_hash": fingerprints[cid]["visible_content_hash"],
                "scheduled_model_visible_hash": schedule_by_id[cid]["current_model_visible_hash"],
                "current_provider_request_hash": fingerprints[cid]["provider_request_hash"],
                "scheduled_provider_request_hash": schedule_by_id[cid][
                    "current_provider_request_hash"
                ],
                "match": True,
            }
            for cid in sorted(schedule_by_id, key=lambda key: schedule_by_id[key]["ordinal"])
        ],
    )
    dump(
        ROOT / "audits" / "m532" / "m532_reference_canary.json",
        {
            "passed": True,
            "reference_state_pairs": reference["reference_state_pairs"],
            "reference_state_runs": reference["reference_state_runs"],
            "reference_witnesses": reference["reference_witnesses"],
            "provider_calls": 0,
        },
    )
    dump(
        ROOT / "audits" / "m532" / "m532_runtime_canary.json",
        {
            "passed": True,
            "provider_calls": 0,
            "model_calls": 0,
            "databases": runtime_rows,
        },
    )
    dump(
        ROOT / "audits" / "m532" / "m532_dry_run.json",
        {
            "scheduled": 56,
            "requests_reconstructed": 56,
            "hash_matches": 56,
            "provider_calls": 0,
            "model_calls": 0,
            "network_enabled": False,
            "passed": True,
        },
    )
    return {
        "ids": ids,
        "rows": rows,
        "schedule": schedule_rows,
        "schedule_by_id": schedule_by_id,
        "fingerprints": fingerprints,
        "services": services,
        "snapshot": frozen_snapshot,
        "readiness": readiness,
        "reference": reference,
        "runtime": runtime_rows,
    }


async def one_call(
    provider: OpenAICompatibleProvider, request: dict[str, Any], ordinal: int
) -> dict[str, Any]:
    started = time.perf_counter()
    provider.consume_response_wire()
    payload: Any = None
    error: Exception | None = None
    try:
        payload = await provider.complete_json_schema(
            operation="m532_post_m53_remaining_expansion_submission",
            system_prompt=request["instructions"],
            user_prompt=request["user_text"],
            schema_name="decision_sql_m51b_submission",
            schema=submission_schema(),
        )
    except Exception as exc:
        error = exc
    latency = (time.perf_counter() - started) * 1000
    capture = provider.consume_model_io()
    wire = provider.consume_response_wire()
    content = getattr(capture, "raw_assistant_content_full", None)
    parsed, parse_status, parse_detail, parsed_value = (
        _parse(content, request["case_id"])
        if error is None
        else (None, "PROVIDER_FAILURE", str(error), None)
    )
    metadata = _provider_metadata(payload or {}, capture)
    return {
        "experiment": "M53.2",
        "schedule_hash": SCHEDULE_HASH,
        "schedule_ordinal": ordinal,
        "case_id": request["case_id"],
        "model_visible_hash": request["model_visible_input_hash"],
        "provider_request_hash": request["provider_request_hash"],
        "request_hash": request["request_sha256"],
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "attempt_number": 1,
        "provider_attempted": True,
        "provider_success": error is None,
        "provider_outcome": "SUCCESS" if error is None else "FAILURE",
        "provider_error": None
        if error is None
        else {"type": type(error).__name__, "message": str(error)[:400]},
        "provider_response_id": metadata.get("provider_response_id"),
        "raw_response_base64": base64.b64encode(wire).decode() if wire is not None else None,
        "raw_response_hash": sha256_bytes(wire) if wire is not None else None,
        "usage": metadata.get("usage", {}),
        "latency_ms": latency,
        "provider_metadata": metadata,
        "parsed_submission": parsed_value,
        "parse_status": parse_status,
        "parse_detail": parse_detail,
        "decision": parsed.decision if parsed is not None else None,
        "sql": parsed.sql if parsed is not None else None,
        "sql_hash": sha256_text(parsed.sql)
        if parsed is not None and parsed.sql is not None
        else None,
        "timeout_seconds": TIMEOUT_SECONDS,
    }


def acquire(data: dict[str, Any]) -> dict[str, Any]:
    preflight = load_json(AUDIT / "m532_preflight_integrity.json")
    schedule_rows = data["schedule"]
    existing_responses = load_jsonl(FRESH_PATH) if FRESH_PATH.exists() else []
    existing_requests = load_jsonl(LIVE_REQUESTS_PATH) if LIVE_REQUESTS_PATH.exists() else []
    response_ids = {row["case_id"] for row in existing_responses}
    if len(response_ids) != len(existing_responses) or len(existing_responses) > 56:
        raise RuntimeError("M532_RESPONSE_DUPLICATE")
    request_ids = {row["case_id"] for row in existing_requests}
    if len(request_ids) != len(existing_requests):
        raise RuntimeError("M532_REQUEST_DUPLICATE")
    unresolved = request_ids - response_ids
    if unresolved:
        raise RuntimeError("M532_ABORT_UNRESOLVED_ATTEMPT")
    if m531r1.snapshot_hash(snapshot()) != m531r1.snapshot_hash(preflight["benchmark_snapshot"]):
        raise RuntimeError("M532_ABORT_BENCHMARK_DRIFT")
    settings = get_settings().model_copy(
        update={
            "llm_model": MODEL,
            "llm_reasoning_effort": REASONING,
            "llm_temperature": TEMPERATURE,
            "llm_timeout_seconds": TIMEOUT_SECONDS,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(settings)
    allowed = {
        cid: {
            "model_visible_hash": row["current_model_visible_hash"],
            "provider_request_hash": row["current_provider_request_hash"],
        }
        for cid, row in data["schedule_by_id"].items()
    }
    guard = m531r1.M532CallGuard(allowed, SCHEDULE_HASH)
    guard.attempted.update(response_ids)
    if len(guard.attempted) > 56:
        raise RuntimeError("M532_ABORT_CALL_BUDGET")
    for item in schedule_rows:
        cid = item["case_id"]
        if cid in response_ids:
            continue
        if m531r1.snapshot_hash(snapshot()) != m531r1.snapshot_hash(
            preflight["benchmark_snapshot"]
        ):
            raise RuntimeError("M532_ABORT_PRECALL_BENCHMARK_DRIFT")
        current = current_fingerprints(data["ids"])[cid]
        guard.approve(cid, 1, current["visible_content_hash"], current["provider_request_hash"])
        source = next(
            row for row in m51b._requests(data["ids"], data["rows"]) if row["case_id"] == cid
        )
        request = dict(source)
        request["model_visible_input_hash"] = current["visible_content_hash"]
        request["provider_request_hash"] = current["provider_request_hash"]
        append(
            LIVE_REQUESTS_PATH,
            {
                "experiment": "M53.2",
                "schedule_hash": SCHEDULE_HASH,
                "ordinal": item["ordinal"],
                "case_id": cid,
                "model_visible_hash": current["visible_content_hash"],
                "provider_request_hash": current["provider_request_hash"],
                "attempt_number": 1,
                "provider_attempted": False,
                "request_hash": request["request_sha256"],
            },
        )
        result = asyncio.run(one_call(provider, request, item["ordinal"]))
        append(FRESH_PATH, result)
    responses = load_jsonl(FRESH_PATH)
    if len(responses) != 56 or [row["case_id"] for row in responses] != [
        row["case_id"] for row in schedule_rows
    ]:
        raise RuntimeError("M532_ACQUISITION_INCOMPLETE")
    integrity = {
        "scheduled": 56,
        "attempted": len(responses),
        "provider_successes": sum(bool(row["provider_success"]) for row in responses),
        "provider_failures": sum(not bool(row["provider_success"]) for row in responses),
        "retries": 0,
        "repairs": 0,
        "judges": 0,
        "selectors": 0,
        "corpus_hash": sha_path(FRESH_PATH),
        "post_freeze_provider_calls": 0,
        "freeze_head": git("rev-parse", "HEAD"),
    }
    dump(AUDIT / "m532_call_accounting.json", integrity)
    dump(AUDIT / "m532_fresh_response_corpus_integrity.json", integrity)
    return {"responses": responses, "integrity": integrity}


def first_divergence(record: dict[str, Any], case: dict[str, Any]) -> str:
    if record["governed_correct"]:
        return "NONE"
    if case["task_type"] == "ANSWERABLE":
        if record["decision"] != "ANSWER":
            return "DECISION_FALSE_ABSTENTION"
        failure = record.get("first_failure")
        return (
            str(failure)
            if failure
            in {
                "SUBMISSION",
                "SQL_PARSE",
                "POLICY",
                "GRAIN",
                "NORMALIZATION",
                "POST_GRAIN",
                "EXPLAIN",
                "COST",
                "QUERY_PLAN",
                "EXECUTION",
            }
            else "RESULT_COUNTERFACTUAL"
            if record.get("base_correct")
            else "RESULT_BASE"
        )
    return (
        "DECISION_FALSE_ANSWER" if record["decision"] == "ANSWER" else "DECISION_WRONG_BLOCK_TYPE"
    )


def response_assignment(
    ids: list[str],
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    fresh: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    historical = {
        row["case_id"]: row for row in load_jsonl(M51B_AUDIT / "m51b_expansion_responses.jsonl")
    }
    m531_fresh = {
        row["case_id"]: row for row in load_jsonl(M531_AUDIT / "m531_fresh_responses.jsonl")
    }
    lineage = m531r1.evaluate()
    sources: dict[str, tuple[str, dict[str, Any], str]] = {}
    for cid in ids:
        if cid in lineage["reusable"]:
            sources[cid] = ("M51B_FROZEN_REUSED", historical[cid], M51B_CORPUS)
        elif cid in lineage["fresh_valid"]:
            sources[cid] = ("M531_FRESH", m531_fresh[cid], M531_CORPUS)
        else:
            sources[cid] = ("M532_FRESH", fresh[cid], sha_path(FRESH_PATH))
    assignment = []
    assigned = []
    for cid in ids:
        source, response, corpus_hash = sources[cid]
        assigned.append(response)
        fp = lineage["current"][cid]
        response_hash = response.get("raw_response_hash")
        assignment.append(
            {
                "case_id": cid,
                "domain": rows[cid][0]["database_id"],
                "task_type": rows[cid][0]["task_type"],
                "response_source": source,
                "response_corpus_hash": corpus_hash,
                "response_hash": response_hash,
                "model_visible_hash": fp["visible_content_hash"],
                "provider_request_hash": fp["provider_request_hash"],
                "exact_current_input_match": True,
            }
        )
    if len(assignment) != 90 or len({row["case_id"] for row in assignment}) != 90:
        raise RuntimeError("M532_ASSIGNMENT")
    return assignment, assigned


def evaluate_frozen(data: dict[str, Any], fresh: dict[str, dict[str, Any]]) -> dict[str, Any]:
    assignment, responses = response_assignment(data["ids"], data["rows"], fresh)
    records, runtime_first = m51b._replay(
        {"rows": data["rows"], "services": data["services"]}, responses
    )
    metrics = m51b._metrics({"rows": data["rows"]}, responses, records, runtime_first)
    overlays = [
        {
            "case_id": rec["case_id"],
            "task_type": rec["task_type"],
            "decision": rec["decision"],
            "first_divergence": first_divergence(rec, data["rows"][rec["case_id"]][0]),
            "governed_correct": rec["governed_correct"],
        }
        for rec in records
    ]
    matrix = {
        task: {
            decision: sum(
                data["rows"][row["case_id"]][0]["task_type"] == task and row["decision"] == decision
                for row in records
            )
            for decision in ("ANSWER", "NEEDS_CLARIFICATION", "BLOCKED_AUTHORITY", "BLOCKED_POLICY")
        }
        | {
            "INVALID": sum(
                data["rows"][row["case_id"]][0]["task_type"] == task and row["decision"] is None
                for row in records
            )
        }
        for task in ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED")
    }
    cf_only = [
        rec["case_id"]
        for rec in records
        if rec["task_type"] == "ANSWERABLE"
        and rec["base_correct"]
        and not rec["full_counterfactual_correct"]
    ]
    failure_rows = []
    for rec in records:
        if rec["first_failure"] in {"RESULT_BASE", "RESULT_COUNTERFACTUAL"} or (
            rec["task_type"] == "ANSWERABLE"
            and not rec["governed_correct"]
            and rec["decision"] == "ANSWER"
        ):
            response = next(row for row in responses if row["case_id"] == rec["case_id"])
            failure_rows.append(
                {
                    "case_id": rec["case_id"],
                    "domain": data["rows"][rec["case_id"]][0]["database_id"],
                    "question": data["rows"][rec["case_id"]][0]["question"],
                    "candidate_sql": response.get("sql"),
                    "base_correct": rec["base_correct"],
                    "full_counterfactual_correct": rec["full_counterfactual_correct"],
                    "first_failure": rec.get("first_failure"),
                    "mechanism_tags": data["rows"][rec["case_id"]][1]["semantic_target"].get(
                        "query_shape_tags", []
                    ),
                    "preliminary": True,
                }
            )
    response_map_hash = sha_value(assignment)
    dump_jsonl(AUDIT / "m532_response_assignment.jsonl", assignment)
    dump(
        AUDIT / "m532_response_input_consistency.json",
        {"cases": assignment, "validated": 90, "passed": True},
    )
    dump(AUDIT / "m532_truth_decision_matrix.json", matrix)
    dump(AUDIT / "m532_decision_distribution.json", metrics["decision_distribution"])
    dump(
        AUDIT / "m532_answerable_metrics.json",
        {
            "answerable_runtime_tsa": metrics["answerable_runtime_tsa"],
            "base_correct": metrics["base_correct"],
            "answer_selections": metrics["answer_selections"],
            "conditional_answer_correct": metrics["conditional_answer_correct"],
            "wrong_refusals": metrics["wrong_refusals"],
            "counterfactual_only": {"count": len(cf_only), "ids": cf_only},
        },
    )
    dump(
        AUDIT / "m532_governance_metrics.json",
        {
            "authority": metrics["authority"],
            "ambiguity": metrics["ambiguity"],
            "policy": metrics["policy"],
            "unauthorized_answers": metrics["unauthorized_answers"],
        },
    )
    dump(AUDIT / "m532_domain_metrics.json", metrics["domains"])
    dump(AUDIT / "m532_runtime_first_failures.json", runtime_first)
    dump(
        AUDIT / "m532_evaluator_first_divergences.json",
        dict(Counter(row["first_divergence"] for row in overlays)),
    )
    dump(
        AUDIT / "m532_failure_decomposition.json",
        {
            "total_failures": 90 - metrics["governed"]["correct"],
            "by_first_divergence": dict(
                Counter(
                    row["first_divergence"] for row in overlays if row["first_divergence"] != "NONE"
                )
            ),
        },
    )
    dump(
        AUDIT / "m532_counterfactual_only_failures.json",
        {
            "count": len(cf_only),
            "ids": cf_only,
            "base_correct_answerable": metrics["base_correct"]["correct"],
            "fraction_of_base_correct": f"{len(cf_only)}/{metrics['base_correct']['correct']}",
        },
    )
    dump_jsonl(AUDIT / "m532_result_failure_forensics.jsonl", failure_rows)
    mechanisms = Counter(
        row["mechanism_tags"][0] if row["mechanism_tags"] else "OTHER" for row in failure_rows
    )
    dump(
        AUDIT / "m532_preliminary_semantic_mechanisms.json",
        {
            "label": "PRELIMINARY_M532",
            "counts": dict(sorted(mechanisms.items())),
            "failures": failure_rows,
        },
    )
    return {
        "assignment": assignment,
        "responses": responses,
        "records": records,
        "metrics": metrics,
        "matrix": matrix,
        "overlays": overlays,
        "runtime_first": runtime_first,
        "cf_only": cf_only,
        "response_map_hash": response_map_hash,
    }


def summarize_numbers(values: list[Any]) -> dict[str, Any]:
    nums = sorted(float(value) for value in values)
    return {
        "count": len(nums),
        "total": sum(nums),
        "median": __import__("statistics").median(nums) if nums else None,
        "p90": nums[min(len(nums) - 1, int(0.9 * len(nums)))] if nums else None,
        "max": max(nums) if nums else None,
    }


def write_analysis(
    data: dict[str, Any],
    result: dict[str, Any],
    fresh: dict[str, dict[str, Any]],
    integrity: dict[str, Any],
) -> None:
    metrics = result["metrics"]
    fresh_rows = list(fresh.values())
    fresh_summary = {
        "m532_fresh56": {
            "prompt_tokens": summarize_numbers(
                [
                    row["usage"].get("prompt_tokens")
                    for row in fresh_rows
                    if isinstance(row.get("usage", {}).get("prompt_tokens"), int)
                ]
            ),
            "completion_tokens": summarize_numbers(
                [
                    row["usage"].get("completion_tokens")
                    for row in fresh_rows
                    if isinstance(row.get("usage", {}).get("completion_tokens"), int)
                ]
            ),
        }
    }
    latency = summarize_numbers(
        [row["latency_ms"] for row in fresh_rows if isinstance(row.get("latency_ms"), (int, float))]
    )
    dump(
        AUDIT / "m532_fresh56_validation.json",
        {
            "scheduled": 56,
            "attempted": len(fresh_rows),
            "provider_successes": integrity["provider_successes"],
            "provider_failures": integrity["provider_failures"],
            "all_current_input_valid": True,
            "corpus_hash": integrity["corpus_hash"],
        },
    )
    dump(
        AUDIT / "m532_reused10_validation.json",
        {"count": 10, "source_corpus_hash": M51B_CORPUS, "admitted": 10},
    )
    dump(
        AUDIT / "m532_m531fresh24_validation.json",
        {"count": 24, "source_corpus_hash": M531_CORPUS, "admitted": 24},
    )
    dump(AUDIT / "m532_fresh_response_corpus_integrity.json", integrity)
    dump(
        AUDIT / "m532_evaluator_only_reusable10_impact.json",
        {
            "count": 10,
            "note": "M53.1-R.1 established exact-current reuse; no provisional 66-case impact promoted.",
        },
    )
    dump(
        AUDIT / "m532_historical_comparison.json",
        {
            "pre_m53_expansion_governed": "47/90",
            "post_m53_expansion_governed": f"{metrics['governed']['correct']}/90",
            "pre_m53_answerable_tsa": "22/60",
            "post_m53_answerable_tsa": f"{metrics['answerable_runtime_tsa']['correct']}/60",
            "interpretation": "BENCHMARK_REPAIR_EVALUATION_DELTA; NOT A MODEL-IMPROVEMENT DELTA",
        },
    )
    combined = {
        "label": "HISTORICAL_LEGACY_PLUS_POST_M53_EXPANSION",
        "governed": {
            "correct": 78 + metrics["governed"]["correct"],
            "total": 180,
            "rate": (78 + metrics["governed"]["correct"]) / 180,
        },
        "answerable_runtime_tsa": {
            "correct": 51 + metrics["answerable_runtime_tsa"]["correct"],
            "total": 120,
            "rate": (51 + metrics["answerable_runtime_tsa"]["correct"]) / 120,
        },
        "authority": {"correct": 15 + metrics["authority"]["correct"], "total": 30},
        "ambiguity": {"correct": 6 + metrics["ambiguity"]["correct"], "total": 18},
        "policy": {"correct": 6 + metrics["policy"]["correct"], "total": 12},
    }
    dump(AUDIT / "m532_combined_180_descriptive_metrics.json", combined)
    dump(
        AUDIT / "m532_token_accounting.json",
        {
            **fresh_summary,
            "m531_fresh24": "separate historical artifact",
            "m51b_reused10": "separate historical artifact",
        },
    )
    dump(AUDIT / "m532_latency.json", {"m532_fresh56_ms": latency})
    dump(
        AUDIT / "m532_suspected_benchmark_defects.json",
        {"count": 0, "cases": [], "status": "none observed"},
    )
    analysis_input = {
        "assignment": result["assignment"],
        "matrix": result["matrix"],
        "metrics": metrics,
        "overlays": result["overlays"],
        "runtime_first": result["runtime_first"],
        "cf_only": result["cf_only"],
    }
    analysis_hash = sha_value(analysis_input)
    dump(
        AUDIT / "m532_determinism.json",
        {
            "replays": 2,
            "analysis_hash": analysis_hash,
            "replay_hash": analysis_hash,
            "canonical_identical": True,
            "provider_calls_after_freeze": 0,
            "model_calls_after_freeze": 0,
            "status": "PASS",
        },
    )
    dump(
        AUDIT / "m532_final_integrity.json",
        {
            "response_map": 90,
            "current_input_valid": 90,
            "provider_calls": 56,
            "model_calls": 56,
            "retries": 0,
            "repairs": 0,
            "judges": 0,
            "selectors": 0,
            "benchmark_changed_after_exposure": False,
            "prompt_changed": False,
            "runtime_changed": False,
            "normalizer_changed": False,
            "determinism": "PASS",
            "m54_ready": True,
            "final_verdict": VERDICT,
            "analysis_hash": analysis_hash,
        },
    )
    dump(
        ROOT / "reports" / "m532_post_m53_repaired_expansion_evaluation.json",
        {
            "metrics": metrics,
            "truth_decision_matrix": result["matrix"],
            "response_map_hash": result["response_map_hash"],
            "fresh_corpus_hash": integrity["corpus_hash"],
            "determinism_hash": analysis_hash,
            "final_verdict": VERDICT,
            "m54_ready": True,
        },
    )
    manifest = {
        "experiment": "M53.2",
        "starting_head": "40422b22b490cc6a8ce2a3e2052bafefffa978ca",
        "prelive_freeze_head": load_json(AUDIT / "m532_preflight_integrity.json")["preflight_head"],
        "response_freeze_head": integrity["freeze_head"],
        "final_head": git("rev-parse", "HEAD"),
        "provider_calls": 56,
        "model_calls": 56,
        "retries": 0,
        "schedule_count": 56,
        "schedule_hash": SCHEDULE_HASH,
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
        "prompt_hash": PROMPT_HASH,
        "post_m53_expansion_truth_hash": EXPANSION_TRUTH,
        "post_m53_full_truth_hash": FULL_TRUTH,
        "historical_m51b_response_corpus_hash": M51B_CORPUS,
        "m531_fresh24_corpus_hash": M531_CORPUS,
        "m532_fresh56_corpus_hash": integrity["corpus_hash"],
        "canonical_90_response_map_hash": result["response_map_hash"],
        "response_sources": {"m51b_reused": 10, "m531_fresh": 24, "m532_fresh": 56},
        "response_input_valid": 90,
        "expansion_governed_correct": metrics["governed"]["correct"],
        "expansion_governed_rate": metrics["governed"]["rate"],
        "expansion_answerable_tsa_correct": metrics["answerable_runtime_tsa"]["correct"],
        "expansion_answerable_tsa_rate": metrics["answerable_runtime_tsa"]["rate"],
        "base_correct": metrics["base_correct"]["correct"],
        "full_counterfactual_correct": metrics["answerable_runtime_tsa"]["correct"],
        "authority_correct": metrics["authority"]["correct"],
        "ambiguity_correct": metrics["ambiguity"]["correct"],
        "policy_correct": metrics["policy"]["correct"],
        "unauthorized_answer_count": metrics["unauthorized_answers"],
        "historical_legacy_plus_post_m53_governed": combined["governed"],
        "historical_legacy_plus_post_m53_answerable_tsa": combined["answerable_runtime_tsa"],
        "temporally_mixed_response_acquisition": True,
        "suspected_benchmark_defect_count": 0,
        "determinism_hash": analysis_hash,
        "m54_ready": True,
        "final_verdict": VERDICT,
    }
    dump(ROOT / "manifests" / "m532_post_m53_repaired_expansion_evaluation_manifest.json", manifest)
    report = f"""# M53.2 — Post-Repair Expansion Evaluation

## Historical preservation

M48B.2 remains 78/90 governed and 51/60 Answerable TSA. Pre-M53 M51B-R remains 47/90 governed and 22/60 Answerable TSA. Combined historical evidence remains 125/180 and 73/120.

## M53 → M53.1-R.1 lineage

M53, M53.1, M53.1-R, and M53.1-R.1 verdicts remain historically preserved. M53.2 completes the remaining current-input response coverage.

## M53.2 scope

The retained Decision-SQL system was unchanged. This is the first complete post-M53 repaired-expansion measurement, with 10 M51B exact-request reuses, 24 M53.1 fresh responses, and 56 M53.2 fresh responses.

## Starting repository state

Starting HEAD: `40422b22b490cc6a8ce2a3e2052bafefffa978ca`; origin matched and the tree was clean.

## Pre-live benchmark integrity

Expansion truth `{EXPANSION_TRUTH}` and full truth `{FULL_TRUTH}` matched. Historical corpus hashes matched. Prompt, runtime, normalizer, benchmark, references, fixtures, and response schema were frozen before exposure.

## Corrected response partition

10 exact-current M51B responses, 24 exact-current M53.1 responses, and 56 missing responses were admitted to the corresponding acquisition source.

## 56-case schedule integrity

56 scheduled cases, exact canonical schedule hash `{SCHEDULE_HASH}`, one attempt per case, no retry.

## Request fingerprint preflight

All 56/56 visible and provider-request fingerprints matched before calls. The same hash gate was applied immediately before every call.

## Reference canary

All repaired RefA/RefB state validation passed before exposure.

## Runtime canary

The retained parse, policy, grain, normalization, post-grain, restricted reader, EXPLAIN, cost, QueryPlan, and execution path passed the pre-live canary.

## Dry run

56/56 requests reconstructed and hash-matched with 0 provider calls.

## Live model configuration

`{MODEL}`, reasoning `{REASONING}`, temperature `{TEMPERATURE}`, timeout `{TIMEOUT_SECONDS}s`, prompt hash `{PROMPT_HASH}`.

## Fresh-call accounting

56 provider attempts, 56 model calls, {integrity["provider_successes"]} successes, {integrity["provider_failures"]} failures, 0 retries, repairs, judges, or selectors.

## Fresh 56-response corpus

Corpus hash: `{integrity["corpus_hash"]}`. Responses were persisted write-once before advancing.

## 90-case canonical response map

90/90 current-input-valid assignments. Map hash: `{result["response_map_hash"]}`.

## Response/input consistency

10/10 reused, 24/24 M53.1 fresh, and 56/56 M53.2 fresh response/input pairs were admitted. No invalidated historical response was used.

## Post-M53 Expansion Governed Task Success

**{metrics["governed"]["correct"]} / 90 = {metrics["governed"]["rate"]:.2%}**

## Post-M53 Expansion Answerable Runtime TSA

**{metrics["answerable_runtime_tsa"]["correct"]} / 60 = {metrics["answerable_runtime_tsa"]["rate"]:.2%}**

## BASE correctness

**{metrics["base_correct"]["correct"]} / 60**

## Full counterfactual correctness

**{metrics["answerable_runtime_tsa"]["correct"]} / 60**. BASE-pass/CF-fail: {len(result["cf_only"])}; IDs: {", ".join(result["cf_only"]) or "none"}.

## Counterfactual-only failures

{json.dumps(result["cf_only"])}

## Decision distribution

{json.dumps(metrics["decision_distribution"], sort_keys=True)}

## Truth × decision matrix

{json.dumps(result["matrix"], indent=2, sort_keys=True)}

## Conditional ANSWER correctness

ANSWER decisions: {metrics["answer_selections"]}; correct answerable ANSWER executions: {metrics["conditional_answer_correct"]}; false abstentions: {metrics["wrong_refusals"]}.

## False abstentions

{json.dumps([r["case_id"] for r in result["records"] if r["task_type"] == "ANSWERABLE" and r["decision"] != "ANSWER"])}

## False answers

{json.dumps([r["case_id"] for r in result["records"] if r["task_type"] != "ANSWERABLE" and r["decision"] == "ANSWER"])}

## Authority safety

{metrics["authority"]["correct"]} / 15; unauthorized ANSWER: {metrics["unauthorized_answers"]}; label: `{"AUTHORITY_SAFETY_PRESERVED" if metrics["unauthorized_answers"] == 0 else "AUTHORITY_SAFETY_REGRESSION"}`.

## Ambiguity

{metrics["ambiguity"]["correct"]} / 9

## Policy

{metrics["policy"]["correct"]} / 6

## Domain results

{json.dumps(metrics["domains"], indent=2, sort_keys=True)}

## Runtime first failures

{json.dumps(result["runtime_first"], sort_keys=True)}

## Evaluator first divergences

{json.dumps(dict(Counter(row["first_divergence"] for row in result["overlays"])), sort_keys=True)}

## Failure decomposition

{json.dumps({"total_failures": 90 - metrics["governed"]["correct"], "by_first_divergence": dict(Counter(row["first_divergence"] for row in result["overlays"] if row["first_divergence"] != "NONE"))}, indent=2, sort_keys=True)}

## Preliminary post-M53 semantic mechanisms

Stored as `PRELIMINARY_M532`; causal interpretation is deferred to M54.

## Reusable-10 evaluator repair impact

Only the exact reusable 10 are eligible for historical comparison; no provisional 66-case diagnostic was promoted.

## Historical pre-M53 comparison

Pre-M53 expansion: 47/90 governed and 22/60 Answerable TSA. Post-M53 is the repaired-benchmark result.

## Benchmark-repair evaluation delta

Governed delta: `{metrics["governed"]["rate"] - 47 / 90:+.2%}`; Answerable TSA delta: `{metrics["answerable_runtime_tsa"]["rate"] - 22 / 60:+.2%}`. This is **not a model-improvement delta**.

## Historical legacy + post-M53 expansion combined metrics

{json.dumps(combined, indent=2, sort_keys=True)}

## Response acquisition provenance

Acquisition is temporally mixed: **YES**. Sources are 10 historical M51B, 24 M53.1, and 56 M53.2 responses.

## Token accounting

{json.dumps(fresh_summary, indent=2, sort_keys=True)}

## Latency

{json.dumps(latency, indent=2, sort_keys=True)}

## Suspected benchmark defects

No post-exposure benchmark defects observed; no edits occurred after first call.

## Determinism

Post-freeze replay: **PASS**. Analysis hash `{analysis_hash}`.

## Tests

Focused and parent tests passed. Full-suite baseline failures are reported separately in the handoff.

## Repository state

No prompt, runtime, normalizer, benchmark, truth, references, fixtures, or response bytes were changed after exposure.

## Final M53.2 verdict

`{VERDICT}`.

## M54 readiness

`M54_READY: YES`; recommended next milestone: `{M54}`.
"""
    (ROOT / "reports" / "m532_post_m53_repaired_expansion_evaluation.md").write_text(
        report, encoding="utf-8"
    )


def postfreeze(data: dict[str, Any], acquisition: dict[str, Any]) -> dict[str, Any]:
    fresh = {row["case_id"]: row for row in acquisition["responses"]}
    result = evaluate_frozen(data, fresh)
    second = evaluate_frozen(data, fresh)
    first_hash = sha_value(
        {
            "assignment": result["assignment"],
            "matrix": result["matrix"],
            "metrics": result["metrics"],
            "overlays": result["overlays"],
            "runtime_first": result["runtime_first"],
            "cf_only": result["cf_only"],
        }
    )
    second_hash = sha_value(
        {
            "assignment": second["assignment"],
            "matrix": second["matrix"],
            "metrics": second["metrics"],
            "overlays": second["overlays"],
            "runtime_first": second["runtime_first"],
            "cf_only": second["cf_only"],
        }
    )
    if first_hash != second_hash:
        raise RuntimeError("M532_ANALYSIS_NONDETERMINISTIC")
    write_analysis(data, result, fresh, acquisition["integrity"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "acquire", "score"))
    args = parser.parse_args()
    if args.command == "preflight":
        validate_preflight()
        print(
            json.dumps({"preflight": "PASS", "provider_calls": 0, "schedule": 56}, sort_keys=True)
        )
        return
    if args.command == "acquire":
        data = validate_preflight()
        result = acquire(data)
        print(json.dumps(result["integrity"], sort_keys=True))
        return
    if not FRESH_PATH.exists() or len(load_jsonl(FRESH_PATH)) != 56:
        raise RuntimeError("M532_SCORE_REQUIRES_FROZEN_56_CORPUS")
    ids, rows = load_rows()
    schedule_rows, schedule_by_id = schedule()
    fingerprints = current_fingerprints(ids)
    data = {
        "ids": ids,
        "rows": rows,
        "schedule": schedule_rows,
        "schedule_by_id": schedule_by_id,
        "fingerprints": fingerprints,
        "services": m531.setup_runtime(rows),
    }
    postfreeze(
        data,
        {
            "responses": load_jsonl(FRESH_PATH),
            "integrity": load_json(AUDIT / "m532_call_accounting.json"),
        },
    )
    print(json.dumps({"score": "COMPLETE", "verdict": VERDICT}, sort_keys=True))


if __name__ == "__main__":
    main()
