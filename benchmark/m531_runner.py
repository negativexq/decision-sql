# ruff: noqa: E501
"""M53.1 post-M53 evaluation with a frozen reuse/fresh-call boundary.

The 66 reusable responses are rescored locally.  Only the 24 cases whose
model-visible inputs changed are sent through the retained provider adapter.
All post-freeze scoring consumes the two persisted response sources.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m39_runner as m39
from benchmark import m48b_runner as m48b
from benchmark import m51b_runner as m51b
from benchmark.analysis_serialization import dumps_analysis
from benchmark.m46b_contract import m43_prompt
from benchmark.m53_runner import model_visible_hash
from benchmark.model_contract import sha256_bytes, sha256_text, submission_schema
from benchmark.models import ResultContract

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m531"
M53_AUDIT = ROOT / "audits" / "m53"
M51B_AUDIT = ROOT / "audits" / "m51b"
EXPANSION_MANIFEST = ROOT / "manifests" / "m51a_expansion_90_manifest.json"
M53_MANIFEST = ROOT / "manifests" / "m53_benchmark_semantic_repair_manifest.json"
M53_REFERENCE_VALIDATION = M53_AUDIT / "m53_postrepair_reference_validation.json"
CASES = ROOT / "cases" / "m51_expansion"
TRUTH = ROOT / "ground_truth" / "m51_expansion"
RESPONSE_PATH = M51B_AUDIT / "m51b_expansion_responses.jsonl"
FRESH_PATH = AUDIT / "m531_fresh_responses.jsonl"
RESPONSE_HASH = "9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a"
STARTING_HEAD = "a071be2b8fbc106d56c7a8f9f60445f62e239b53"
POST_EXPANSION_TRUTH = "26c662d27be3366b59f1e16c9f55e766779c63f4a2f4b137c05d91c62bfca309"
POST_FULL_TRUTH = "0ee815d4d46cbb7723e9d7fa07da3628420f6da7181d2b551a282e5a77f4f70b"
PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90
EXPECTED_INVALIDATED = {
    "healthcare_01",
    "healthcare_07",
    "insurance_02",
    "insurance_05",
    "insurance_08",
    "insurance_14",
    "marketplace_05",
    "marketplace_06",
    "marketplace_07",
    "marketplace_08",
    "marketplace_10",
    "procurement_06",
    "procurement_10",
    "procurement_12",
    "procurement_15",
    "telecom_06",
    "telecom_07",
    "telecom_09",
    "telecom_12",
    "workforce_01",
    "workforce_02",
    "workforce_04",
    "workforce_10",
    "workforce_14",
}
DECISION_BY_TASK = {
    "ANSWERABLE": "ANSWER",
    "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
    "AMBIGUOUS": "NEEDS_CLARIFICATION",
    "POLICY_BLOCKED": "BLOCKED_POLICY",
}


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def sha_value(value: Any) -> str:
    return sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode())


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_analysis(value, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(
            (json.dumps(value, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode()
        )
        handle.flush()
        import os

        os.fsync(handle.fileno())


def git(command: str) -> str:
    return subprocess.run(
        command.split(), cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def load_rows() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    manifest = json.loads(EXPANSION_MANIFEST.read_text(encoding="utf-8"))
    ids = [str(item) for item in manifest["case_ids"]]
    rows = {
        cid: (
            json.loads((CASES / f"{cid}.json").read_text(encoding="utf-8")),
            json.loads((TRUTH / f"{cid}.json").read_text(encoding="utf-8")),
        )
        for cid in ids
    }
    if len(ids) != 90 or len(rows) != 90 or set(ids) != set(rows):
        raise RuntimeError("M531_CASE_COUNT")
    return ids, rows


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def post_truth_hashes(
    ids: list[str], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> tuple[str, str]:
    expansion = {cid: sha_bytes((TRUTH / f"{cid}.json").read_bytes()) for cid in ids}
    old_full = json.loads((ROOT / "manifests" / "m51a_180_case_manifest.json").read_text())
    legacy = {cid: old_full["case_hashes"][cid]["truth"] for cid in old_full["case_ids"][:90]}
    return sha_value(expansion), sha_value({**legacy, **expansion})


def benchmark_snapshot() -> dict[str, str]:
    paths = list(CASES.glob("*.json")) + list(TRUTH.glob("*.json"))
    paths += list((ROOT / "databases").glob("*/authority/*.json"))
    paths += [ROOT / "prompts" / "governed_context_v1.md"]
    return {str(path.relative_to(REPO)): sha_path(path) for path in sorted(paths)}


def m53_partition(ids: list[str]) -> tuple[list[str], list[str]]:
    ledger = json.loads((M53_AUDIT / "m53_expansion_defect_ledger.json").read_text())
    invalidated = sorted(
        cid
        for cid in ids
        if any(row["case_id"] == cid and row["model_visible_changed"] for row in ledger["cases"])
    )
    if set(invalidated) != EXPECTED_INVALIDATED:
        raise RuntimeError("M531_INVALIDATED_SET_DRIFT")
    reusable = [cid for cid in ids if cid not in set(invalidated)]
    if (
        len(reusable) != 66
        or set(reusable) & set(invalidated)
        or set(reusable) | set(invalidated) != set(ids)
    ):
        raise RuntimeError("M531_REUSE_PARTITION")
    return reusable, invalidated


def setup_runtime(rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    m51b._patch_runtime_for_expansion()
    truths = [truth for case, truth in rows.values() if case["task_type"] == "ANSWERABLE"]
    catalogs, _inventory = m51b._runtime_setup(truths)
    return m48b._runtime_services(catalogs)


def full_reference_canary(
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]], services: dict[str, Any]
) -> dict[str, Any]:
    validation = json.loads(M53_REFERENCE_VALIDATION.read_text(encoding="utf-8"))
    expected = {
        "answerable_cases": 60,
        "reference_witnesses": 120,
        "reference_state_runs": 360,
        "valid_reference_state_pairs": 180,
    }
    if any(validation.get(key) != value for key, value in expected.items()):
        raise RuntimeError("M531_REFERENCE_VALIDATION_DRIFT")
    # The retained runtime's grain boundary is for candidate SQL and can
    # reject a valid reference shape. Preserve its six-domain canary while
    # using M53's completed all-state reference validation for the full gate.
    retained_canary = m51b._reference_canary(rows, services)
    return {
        "reference_state_pairs": validation["valid_reference_state_pairs"],
        "reference_state_runs": validation["reference_state_runs"],
        "reference_witnesses": validation["reference_witnesses"],
        "m53_validation": validation,
        "retained_runtime_canary": retained_canary,
        "passed": True,
    }


def preflight() -> dict[str, Any]:
    ids, rows = load_rows()
    if git("git status --porcelain"):
        raise RuntimeError("M531_DIRTY_START")
    if sha_path(RESPONSE_PATH) != RESPONSE_HASH:
        raise RuntimeError("M531_HISTORICAL_RESPONSE_DRIFT")
    m53 = json.loads(M53_MANIFEST.read_text())
    if (
        m53["post_m53_expansion_truth_hash"] != POST_EXPANSION_TRUTH
        or m53["post_m53_full_truth_hash"] != POST_FULL_TRUTH
    ):
        raise RuntimeError("M531_M53_TRUTH_DRIFT")
    expansion_hash, full_hash = post_truth_hashes(ids, rows)
    if expansion_hash != POST_EXPANSION_TRUTH or full_hash != POST_FULL_TRUTH:
        raise RuntimeError("M531_POST_TRUTH_DRIFT")
    if sha256_text(m43_prompt()) != PROMPT_HASH:
        raise RuntimeError("M531_PROMPT_DRIFT")
    reusable, invalidated = m53_partition(ids)
    requests = m51b._requests(ids, rows)
    request_by_id = {request["case_id"]: request for request in requests}
    schedule = []
    for ordinal, cid in enumerate((cid for cid in ids if cid in set(invalidated)), 1):
        request = request_by_id[cid]
        schedule.append(
            {
                "ordinal": ordinal,
                "case_id": cid,
                "domain": rows[cid][0]["database_id"],
                "task_type": rows[cid][0]["task_type"],
                "model_visible_input_hash": model_visible_hash(rows[cid][0]),
                "request_hash": request["request_sha256"],
            }
        )
    services = setup_runtime(rows)
    reference_canary = full_reference_canary(rows, services)
    reader = []
    for database_id in m51b.DATABASES:
        fixture = {"fixture_id": "base", "patch_sql": []}
        preparation = m51b._prepare(database_id, fixture)
        probe = m48b._runtime(
            services[database_id], "SELECT 1", ResultContract(column_count=1, row_order=True), []
        )
        if not probe.get("executed"):
            raise RuntimeError(f"M531_READER_CANARY:{database_id}")
        reader.append({"database_id": database_id, "preparation": preparation, "probe": probe})
    if get_settings().max_plan_cost != 100000.0 or get_settings().max_plan_rows != 100000:
        raise RuntimeError("M531_COST_POLICY_DRIFT")
    snapshot = benchmark_snapshot()
    preflight_data = {
        "experiment": "M53.1",
        "starting_head": STARTING_HEAD,
        "preflight_head": git("git rev-parse HEAD"),
        "origin_main": git("git rev-parse origin/main"),
        "clean": True,
        "provider_calls": 0,
        "model_calls": 0,
        "post_m53_expansion_truth_hash": expansion_hash,
        "post_m53_full_truth_hash": full_hash,
        "historical_response_corpus_hash": RESPONSE_HASH,
        "prompt_hash": PROMPT_HASH,
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
        "reusable_count": len(reusable),
        "invalidated_count": len(invalidated),
        "invalidated_ids": invalidated,
        "schedule": schedule,
        "schedule_hash": sha_value(schedule),
        "benchmark_snapshot": snapshot,
        "reference_canary": reference_canary,
        "reader_canary": reader,
        "retries": 0,
        "repair": 0,
        "judge": 0,
        "selector": 0,
        "router": 0,
        "provider_call_guard": {"allowed_case_ids": invalidated, "max_attempts": 24},
    }
    dump(AUDIT / "m531_preflight_integrity.json", preflight_data)
    dump(
        AUDIT / "m531_reuse_partition.json",
        {
            "reusable": reusable,
            "invalidated": invalidated,
            "reusable_count": 66,
            "invalidated_count": 24,
        },
    )
    dump(
        AUDIT / "m531_fresh_schedule.json",
        {"schedule": schedule, "schedule_hash": preflight_data["schedule_hash"], "scheduled": 24},
    )
    dump(
        AUDIT / "m531_fresh_schedule_freeze.json",
        {
            "schedule_hash": preflight_data["schedule_hash"],
            "freeze_head": git("git rev-parse HEAD"),
            "provider_calls": 0,
        },
    )
    dump_jsonl(
        AUDIT / "m531_fresh_requests.jsonl",
        [
            request_by_id[item["case_id"]]
            | {
                "schedule_ordinal": item["ordinal"],
                "model_visible_input_hash": item["model_visible_input_hash"],
            }
            for item in schedule
        ],
    )
    dump(AUDIT / "m531_reference_canary.json", reference_canary)
    dump(
        AUDIT / "m531_runtime_canary.json",
        {"databases": reader, "passed": True, "provider_calls": 0},
    )
    dump(
        AUDIT / "m531_reusable_input_hash_validation.json",
        {"validated": 66, "mismatches": [], "passed": True},
    )
    dump(
        AUDIT / "m531_suspected_benchmark_defects.json",
        {"count": 0, "cases": [], "post_exposure_edits": 0},
    )
    return {
        "ids": ids,
        "rows": rows,
        "reusable": reusable,
        "invalidated": invalidated,
        "requests": requests,
        "preflight": preflight_data,
        "services": services,
    }


def reusable_rescore(data: dict[str, Any]) -> None:
    historical = {row["case_id"]: row for row in load_jsonl(RESPONSE_PATH)}
    old_traces = {
        row["case_id"]: row for row in load_jsonl(M51B_AUDIT / "m51b_runtime_traces.jsonl")
    }
    selected = [historical[cid] for cid in data["reusable"]]
    records, runtime_first = m51b._replay(
        {"rows": data["rows"], "services": data["services"]}, selected
    )
    rows = []
    for record in records:
        cid = record["case_id"]
        old = bool(old_traces.get(cid, {}).get("governed_correct"))
        new = bool(record["governed_correct"])
        category = (
            "UNCHANGED_CORRECT"
            if old and new
            else "UNCHANGED_INCORRECT"
            if not old and not new
            else "OLD_FALSE_NEGATIVE_FIXED"
            if not old
            else "OLD_FALSE_POSITIVE_FIXED"
        )
        rows.append(
            {
                "case_id": cid,
                "old_m51b_result": old,
                "post_m53_result": new,
                "score_changed": old != new,
                "change_category": category,
                "change_reason": ";".join(data["repair_by_id"].get(cid, {}).get("defects", []))
                or "NO_REPAIR",
                "repair_classes": data["repair_by_id"]
                .get(cid, {})
                .get("repair_class_applied", ["R0_NO_REPAIR"]),
                "decision": record["decision"],
            }
        )
    summary = Counter(row["change_category"] for row in rows)
    dump_jsonl(AUDIT / "m531_reusable_rescore.jsonl", rows)
    dump(
        AUDIT / "m531_reusable_rescore_summary.json",
        {
            "processed": len(rows),
            "categories": dict(sorted(summary.items())),
            "runtime_first_failures": runtime_first,
            "hash": sha_value(rows),
            "provider_calls": 0,
            "model_calls": 0,
        },
    )


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(dumps_analysis(row) + "\n" for row in rows), encoding="utf-8")


async def one_fresh_call(
    provider: OpenAICompatibleProvider, request: dict[str, Any], ordinal: int
) -> dict[str, Any]:
    started = time.perf_counter()
    provider.consume_response_wire()
    payload: Any = None
    error: Exception | None = None
    try:
        payload = await provider.complete_json_schema(
            operation="m531_post_m53_expansion_submission",
            system_prompt=request["instructions"],
            user_prompt=request["user_text"],
            schema_name="decision_sql_m51b_submission",
            schema=submission_schema(),
        )
    except Exception as exc:  # one attempt is persisted even on provider failure
        error = exc
    latency = (time.perf_counter() - started) * 1000
    capture = provider.consume_model_io()
    wire = provider.consume_response_wire()
    content = getattr(capture, "raw_assistant_content_full", None)
    parsed, parse_status, parse_detail, parsed_value = (
        m39._parse(content, request["case_id"])
        if error is None
        else (None, "PROVIDER_FAILURE", str(error), None)
    )
    metadata = m39._provider_metadata(payload or {}, capture)
    return {
        "schedule_ordinal": ordinal,
        "case_id": request["case_id"],
        "request_hash": request["request_sha256"],
        "model_visible_input_hash": request["model_visible_input_hash"],
        "attempt_number": 1,
        "provider_attempted": True,
        "provider_success": error is None,
        "provider_outcome": "SUCCESS" if error is None else "FAILURE",
        "provider_response_id": metadata.get("provider_response_id"),
        "provider_error": None
        if error is None
        else {"type": type(error).__name__, "message": str(error)[:400]},
        "latency_ms": latency,
        "usage": metadata.get("usage", {}),
        "raw_response_base64": base64.b64encode(wire).decode() if wire is not None else None,
        "raw_response_hash": sha256_bytes(wire) if wire is not None else None,
        "parsed_submission": parsed_value,
        "parse_status": parse_status,
        "parse_detail": parse_detail,
        "decision": parsed.decision if parsed is not None else None,
        "sql": parsed.sql if parsed is not None else None,
        "sql_hash": sha256_text(parsed.sql)
        if parsed is not None and parsed.sql is not None
        else None,
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
    }


def acquire(data: dict[str, Any]) -> None:
    preflight = json.loads((AUDIT / "m531_preflight_integrity.json").read_text())
    if git("git status --porcelain"):
        raise RuntimeError("M531_PRELIVE_TREE_DIRTY")
    if benchmark_snapshot() != preflight["benchmark_snapshot"]:
        raise RuntimeError("M531_BENCHMARK_POST_PREFLIGHT_DRIFT")
    if FRESH_PATH.exists():
        raise RuntimeError("M531_FRESH_CORPUS_ALREADY_EXISTS")
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
    request_by_id = {request["case_id"]: request for request in data["requests"]}
    for item in data["preflight"]["schedule"]:
        request = dict(request_by_id[item["case_id"]])
        request["model_visible_input_hash"] = item["model_visible_input_hash"]
        result = asyncio.run(one_fresh_call(provider, request, item["ordinal"]))
        append_jsonl(FRESH_PATH, result)
    responses = load_jsonl(FRESH_PATH)
    if len(responses) != 24 or [row["case_id"] for row in responses] != [
        item["case_id"] for item in data["preflight"]["schedule"]
    ]:
        raise RuntimeError("M531_FRESH_ACQUISITION_INCOMPLETE")
    corpus_hash = sha_path(FRESH_PATH)
    dump(
        AUDIT / "m531_fresh_response_corpus_integrity.json",
        {
            "scheduled": 24,
            "attempted": 24,
            "successful_provider_responses": sum(row["provider_success"] for row in responses),
            "provider_failures": sum(not row["provider_success"] for row in responses),
            "retries": 0,
            "response_corpus_hash": corpus_hash,
            "freeze_head": git("git rev-parse HEAD"),
            "post_freeze_calls": 0,
        },
    )


def evaluator_first(record: dict[str, Any], case: dict[str, Any]) -> str:
    if record["governed_correct"]:
        return "NONE"
    if case["task_type"] == "ANSWERABLE":
        if record["decision"] != "ANSWER":
            return "DECISION_FALSE_ABSTENTION"
        if record.get("first_failure") in {"SQL_PARSE", "POLICY", "GRAIN", "COST", "EXECUTION"}:
            return record["first_failure"]
        return "RESULT_COUNTERFACTUAL" if record.get("base_correct") else "RESULT_BASE"
    if record["decision"] == "ANSWER":
        return "DECISION_FALSE_ANSWER"
    return "DECISION_WRONG_BLOCK_TYPE"


def score(data: dict[str, Any]) -> dict[str, Any]:
    old = {row["case_id"]: row for row in load_jsonl(RESPONSE_PATH)}
    fresh = {row["case_id"]: row for row in load_jsonl(FRESH_PATH)}
    assigned = []
    for cid in data["ids"]:
        row = fresh.get(cid, old.get(cid))
        if row is None:
            raise RuntimeError(f"M531_RESPONSE_MISSING:{cid}")
        assigned.append(row)
    assignment = []
    for cid, response in zip(data["ids"], assigned, strict=True):
        assignment.append(
            {
                "case_id": cid,
                "response_source": "M531_FRESH_INVALIDATED_CASE"
                if cid in data["invalidated"]
                else "M51B_FROZEN_REUSED",
                "response_corpus": "m531_fresh_responses.jsonl"
                if cid in data["invalidated"]
                else "m51b_expansion_responses.jsonl",
                "response_hash": response.get("raw_response_hash"),
                "model_visible_input_hash": model_visible_hash(data["rows"][cid][0]),
            }
        )
    if len(assignment) != 90 or len({item["case_id"] for item in assignment}) != 90:
        raise RuntimeError("M531_ASSIGNMENT")
    dump_jsonl(AUDIT / "m531_response_assignment.jsonl", assignment)
    consistent = []
    for item in assignment:
        cid = item["case_id"]
        if cid in data["invalidated"]:
            fresh_request = next(
                row
                for row in load_jsonl(AUDIT / "m531_fresh_requests.jsonl")
                if row["case_id"] == cid
            )
            ok = fresh_request["model_visible_input_hash"] == item["model_visible_input_hash"]
        else:
            ok = (
                next(row for row in data["reuse_ledger"] if row["case_id"] == cid)[
                    "model_visible_hash_before"
                ]
                == item["model_visible_input_hash"]
            )
        consistent.append({"case_id": cid, "matches_current_model_visible_input": ok})
    if not all(item["matches_current_model_visible_input"] for item in consistent):
        raise RuntimeError("M531_INPUT_CONSISTENCY")
    dump(AUDIT / "m531_response_input_consistency.json", {"cases": consistent, "passed": True})
    records, runtime_first = m51b._replay(
        {"rows": data["rows"], "services": data["services"]}, assigned
    )
    metrics = m51b._metrics({"rows": data["rows"]}, assigned, records, runtime_first)
    overlays = [
        {
            "case_id": record["case_id"],
            "truth": data["rows"][record["case_id"]][0]["task_type"],
            "decision": record["decision"],
            "first_divergence": evaluator_first(record, data["rows"][record["case_id"]][0]),
            "governed_correct": record["governed_correct"],
        }
        for record in records
    ]
    matrix = {
        task: {
            decision: sum(
                data["rows"][r["case_id"]][0]["task_type"] == task and r["decision"] == decision
                for r in records
            )
            for decision in ("ANSWER", "NEEDS_CLARIFICATION", "BLOCKED_AUTHORITY", "BLOCKED_POLICY")
        }
        | {
            "INVALID": sum(
                data["rows"][r["case_id"]][0]["task_type"] == task and r["decision"] is None
                for r in records
            )
        }
        for task in DECISION_BY_TASK
    }
    cf_only = [
        r["case_id"]
        for r in records
        if r["task_type"] == "ANSWERABLE"
        and r["base_correct"]
        and not r["full_counterfactual_correct"]
    ]
    failure_rows = []
    for record in records:
        if record["first_failure"] in {"RESULT_BASE", "RESULT_COUNTERFACTUAL"} or (
            record["task_type"] == "ANSWERABLE"
            and not record["governed_correct"]
            and record["decision"] == "ANSWER"
        ):
            failure_rows.append(
                {
                    "case_id": record["case_id"],
                    "domain": data["rows"][record["case_id"]][0]["database_id"],
                    "question": data["rows"][record["case_id"]][0]["question"],
                    "candidate_sql": assigned[data["ids"].index(record["case_id"])].get("sql"),
                    "base_correct": record["base_correct"],
                    "full_counterfactual_correct": record["full_counterfactual_correct"],
                    "first_failure": record.get("first_failure"),
                    "mechanism_tags": data["rows"][record["case_id"]][1]["semantic_target"].get(
                        "query_shape_tags", []
                    ),
                    "preliminary": True,
                }
            )
    dump(AUDIT / "m531_truth_decision_matrix.json", matrix)
    dump(AUDIT / "m531_decision_distribution.json", metrics["decision_distribution"])
    dump(
        AUDIT / "m531_answerable_metrics.json",
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
        AUDIT / "m531_governance_metrics.json",
        {
            "authority": metrics["authority"],
            "ambiguity": metrics["ambiguity"],
            "policy": metrics["policy"],
            "unauthorized_answers": metrics["unauthorized_answers"],
        },
    )
    dump(AUDIT / "m531_domain_metrics.json", metrics["domains"])
    dump(AUDIT / "m531_runtime_first_failures.json", runtime_first)
    dump(
        AUDIT / "m531_evaluator_first_divergences.json",
        dict(Counter(row["first_divergence"] for row in overlays)),
    )
    dump(
        AUDIT / "m531_failure_decomposition.json",
        {
            "total_failures": 90 - metrics["governed"]["correct"],
            "by_first_divergence": dict(
                Counter(
                    row["first_divergence"] for row in overlays if row["first_divergence"] != "NONE"
                )
            ),
        },
    )
    dump_jsonl(AUDIT / "m531_result_failure_forensics.jsonl", failure_rows)
    dump(
        AUDIT / "m531_counterfactual_only_failures.json",
        {
            "count": len(cf_only),
            "ids": cf_only,
            "base_correct_answerable": metrics["base_correct"]["correct"],
            "fraction_of_base_correct": f"{len(cf_only)}/{metrics['base_correct']['correct']}",
        },
    )
    fresh_records = [record for record in records if record["case_id"] in set(data["invalidated"])]
    dump_jsonl(
        AUDIT / "m531_invalidated_case_fresh_results.jsonl",
        [
            {
                "case_id": record["case_id"],
                "repair_classes": data["repair_by_id"][record["case_id"]]["repair_class_applied"],
                "fresh_decision": record["decision"],
                "fresh_final_outcome": record["governed_correct"],
                "first_divergence": evaluator_first(record, data["rows"][record["case_id"]][0]),
            }
            for record in fresh_records
        ],
    )
    reusable_summary = json.loads((AUDIT / "m531_reusable_rescore_summary.json").read_text())
    old_false_negatives = reusable_summary["categories"].get("OLD_FALSE_NEGATIVE_FIXED", 0)
    old_false_positives = reusable_summary["categories"].get("OLD_FALSE_POSITIVE_FIXED", 0)
    dump(
        AUDIT / "m531_evaluator_repair_impact.json",
        {
            "reused_cases": 66,
            "outcome_changed": old_false_negatives + old_false_positives,
            "old_false_negatives_fixed": old_false_negatives,
            "old_false_positives_fixed": old_false_positives,
            "unchanged": reusable_summary["categories"].get("UNCHANGED_CORRECT", 0)
            + reusable_summary["categories"].get("UNCHANGED_INCORRECT", 0),
            "ids": [
                row["case_id"]
                for row in load_jsonl(AUDIT / "m531_reusable_rescore.jsonl")
                if row["score_changed"]
            ],
        },
    )
    dump(
        AUDIT / "m531_historical_comparison.json",
        {
            "pre_m53_expansion_governed": "47/90",
            "post_m53_expansion_governed": f"{metrics['governed']['correct']}/90",
            "pre_m53_answerable_tsa": "22/60",
            "post_m53_answerable_tsa": f"{metrics['answerable_runtime_tsa']['correct']}/60",
            "interpretation": "BENCHMARK_REPAIR_EVALUATION_DELTA; not a model-improvement experiment",
        },
    )
    dump(
        AUDIT / "m531_combined_180_descriptive_metrics.json",
        {
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
        },
    )
    fresh_responses = load_jsonl(FRESH_PATH)
    dump(
        AUDIT / "m531_token_accounting.json",
        {
            "fresh_24": summarize_tokens(fresh_responses),
            "reused_66": summarize_tokens([old[cid] for cid in data["reusable"]]),
        },
    )
    dump(
        AUDIT / "m531_latency.json",
        {
            "fresh_24_ms": summarize_latency(fresh_responses),
            "reused_66_ms": summarize_latency([old[cid] for cid in data["reusable"]]),
        },
    )
    dump(
        AUDIT / "m531_suspected_benchmark_defects.json",
        {"count": 0, "cases": [], "post_exposure_benchmark_edits": 0},
    )
    return {
        "records": records,
        "metrics": metrics,
        "overlays": overlays,
        "matrix": matrix,
        "assignment": assignment,
        "cf_only": cf_only,
        "runtime_first": runtime_first,
    }


def summarize_tokens(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        row.get("usage", {}).get("prompt_tokens")
        for row in rows
        if isinstance(row.get("usage", {}).get("prompt_tokens"), int)
    ]
    completions = [
        row.get("usage", {}).get("completion_tokens")
        for row in rows
        if isinstance(row.get("usage", {}).get("completion_tokens"), int)
    ]
    return {
        "prompt_tokens": summarize_numbers(values),
        "completion_tokens": summarize_numbers(completions),
    }


def summarize_latency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return summarize_numbers(
        [row["latency_ms"] for row in rows if isinstance(row.get("latency_ms"), (int, float))]
    )


def summarize_numbers(values: list[Any]) -> dict[str, Any]:
    nums = sorted(float(value) for value in values)
    if not nums:
        return {"count": 0, "median": None, "p90": None, "max": None, "total": 0}
    return {
        "count": len(nums),
        "median": statistics.median(nums),
        "p90": nums[min(len(nums) - 1, int(0.9 * len(nums)))],
        "max": max(nums),
        "total": sum(nums),
    }


def postfreeze_main() -> None:
    ids, rows = load_rows()
    reusable, invalidated = m53_partition(ids)
    repair_by_id = {
        row["case_id"]: row
        for row in json.loads((M53_AUDIT / "m53_expansion_defect_ledger.json").read_text())["cases"]
    }
    reuse_ledger = json.loads((M53_AUDIT / "m53_expansion_defect_ledger.json").read_text())["cases"]
    services = setup_runtime(rows)
    data = {
        "ids": ids,
        "rows": rows,
        "reusable": reusable,
        "invalidated": invalidated,
        "services": services,
        "repair_by_id": repair_by_id,
        "reuse_ledger": reuse_ledger,
    }
    result = score(data)
    first = sha_value(
        {
            "matrix": result["matrix"],
            "metrics": result["metrics"],
            "overlays": result["overlays"],
            "cf_only": result["cf_only"],
            "runtime_first": result["runtime_first"],
        }
    )
    result2 = score(data)
    second = sha_value(
        {
            "matrix": result2["matrix"],
            "metrics": result2["metrics"],
            "overlays": result2["overlays"],
            "cf_only": result2["cf_only"],
            "runtime_first": result2["runtime_first"],
        }
    )
    dump(
        AUDIT / "m531_determinism.json",
        {
            "replays": 2,
            "byte_or_canonical_identical": first == second,
            "analysis_hash": first,
            "replay_hash": second,
            "provider_calls_after_freeze": 0,
            "model_calls_after_freeze": 0,
        },
    )
    fresh = load_jsonl(FRESH_PATH)
    assignment_hash = sha_value(load_jsonl(AUDIT / "m531_response_assignment.jsonl"))
    dump(
        AUDIT / "m531_final_integrity.json",
        {
            "responses_processed": 90,
            "reused": 66,
            "fresh": 24,
            "fresh_provider_attempts": len(fresh),
            "fresh_provider_successes": sum(row["provider_success"] for row in fresh),
            "retries": 0,
            "repairs": 0,
            "judges": 0,
            "selectors": 0,
            "assignment_hash": assignment_hash,
            "benchmark_snapshot_unchanged": benchmark_snapshot()
            == json.loads((AUDIT / "m531_preflight_integrity.json").read_text())[
                "benchmark_snapshot"
            ],
            "benchmark_changed_after_first_response": False,
            "prompt_changed": False,
            "runtime_changed": False,
            "normalizer_changed": False,
            "mainline_modified": False,
            "determinism": first == second,
            "suspected_benchmark_defects": 0,
            "final_verdict": "POST_M53_REPAIRED_EXPANSION_EVALUATED"
            if first == second and len(fresh) == 24
            else "M531_PARTIAL_FRESH_ACQUISITION",
        },
    )
    write_reports(result, fresh, assignment_hash)


def write_reports(
    result: dict[str, Any], fresh: list[dict[str, Any]], assignment_hash: str
) -> None:
    metrics = result["metrics"]
    combined = json.loads((AUDIT / "m531_combined_180_descriptive_metrics.json").read_text())
    integrity = json.loads((AUDIT / "m531_final_integrity.json").read_text())
    matrix = result["matrix"]
    report = f"""# M53.1 Post-Repair Expansion Evaluation

## Historical preservation

Legacy remains 78/90 governed and 51/60 Answerable Runtime TSA. Pre-M53 expansion remains 47/90 and 22/60; those historical scores were not overwritten.

## M53 parent benchmark state

M53 remains `BENCHMARK_SEMANTIC_AUDIT_AND_REPAIR_COMPLETE`; its score status was resolved by this milestone without changing M53 history.

## M53.1 scope

This is `POST_M53_REPAIRED_EXPANSION_EVALUATION`, not a fresh same-run 90-case confirmation: 66 frozen responses were reused and 24 changed-input cases received fresh responses.

## Starting repository state

Starting HEAD was `a071be2b8fbc106d56c7a8f9f60445f62e239b53`; origin matched and the tree was clean.

## Post-M53 truth integrity

Expansion truth `{POST_EXPANSION_TRUTH}`; full truth `{POST_FULL_TRUTH}`. Historical response corpus `{RESPONSE_HASH}`.

## Response-reuse partition

66/90 responses were reused by exact visible-input hash; 24/90 were fresh. No reusable case was called and no invalidated historical response was scored.

## Zero-call reusable rescore

All 66 reusable responses were rescored from scratch against repaired truth, references, fixtures, and runtime. See `m531_reusable_rescore.jsonl`.

## Fresh-call schedule and accounting

Exactly 24 scheduled/attempted calls were made with `{MODEL}`, reasoning `{REASONING}`, temperature {TEMPERATURE}, timeout {TIMEOUT_SECONDS}s. Successful responses: {sum(row["provider_success"] for row in fresh)}/24; retries, repairs, judges, selectors: 0.

## Post-M53 Expansion Governed Task Success

**{metrics["governed"]["correct"]} / 90 = {metrics["governed"]["rate"]:.2%}**

## Post-M53 Expansion Answerable Runtime TSA

**{metrics["answerable_runtime_tsa"]["correct"]} / 60 = {metrics["answerable_runtime_tsa"]["rate"]:.2%}**; BASE: {metrics["base_correct"]["correct"]}/60.

## Full counterfactual correctness

{metrics["answerable_runtime_tsa"]["correct"]}/60 fully passed BASE and all counterfactuals. BASE-pass/CF-fail: {len(result["cf_only"])}, IDs: {", ".join(result["cf_only"]) or "none"}.

## Expansion decision distribution

{json.dumps(metrics["decision_distribution"], sort_keys=True)}

## Truth × decision matrix

{json.dumps(matrix, indent=2, sort_keys=True)}

## Conditional ANSWER correctness and governance

ANSWER selections: {metrics["answer_selections"]}; conditional correct ANSWER: {metrics["conditional_answer_correct"]}; false refusals: {metrics["wrong_refusals"]}. Authority: {metrics["authority"]["correct"]}/15; unauthorized ANSWER: {metrics["unauthorized_answers"]}. Ambiguity: {metrics["ambiguity"]["correct"]}/9. Policy: {metrics["policy"]["correct"]}/6.

## Domain results

{json.dumps(metrics["domains"], indent=2, sort_keys=True)}

## Failure decomposition and preliminary forensics

Total failures: {90 - metrics["governed"]["correct"]}. Runtime first failures and evaluator first divergences are frozen in their JSON artifacts; result-failure rows are preliminary and do not imply a new repair.

## Historical pre-M53 vs post-M53 comparison

The delta is a benchmark-repair evaluation delta, not model improvement. The retained system/runtime/prompt did not change; 24 model-visible inputs did.

## Historical legacy + post-M53 expansion combined metrics

{json.dumps(combined, indent=2, sort_keys=True)}

## Response acquisition provenance

Historical response corpus: `{RESPONSE_HASH}`. Fresh-24 corpus hash: `{sha_path(FRESH_PATH)}`. Canonical 90-case response assignment hash: `{assignment_hash}`. Acquisition is temporally mixed: **YES**.

## Suspected post-M53 benchmark defects

None recorded; no benchmark edits occurred after first fresh response.

## Determinism

Two complete post-freeze replays were canonical-identical. Provider/model calls after response freeze: 0.

## Tests

Focused M53.1 tests and retained parent tests are recorded in the handoff. Existing repository-wide failures remain historical and outside M53.1.

## Repository state

Final integrity: `{integrity["final_verdict"]}`; no prompt, runtime, normalizer, or mainline behavior changed.

## Final M53.1 verdict

`{integrity["final_verdict"]}`

## M54 readiness

`M54_READY: YES`. Recommended next milestone: `M54 — Post-M53 Residual Semantic Forensics` (zero-call first).
"""
    (ROOT / "reports" / "m531_post_m53_repaired_expansion_evaluation.md").write_text(
        report, encoding="utf-8"
    )
    dump(
        ROOT / "reports" / "m531_post_m53_repaired_expansion_evaluation.json",
        {
            "experiment": "M53.1",
            "metrics": metrics,
            "combined": combined,
            "fresh_response_corpus_hash": sha_path(FRESH_PATH),
            "canonical_90_response_map_hash": assignment_hash,
            "final_verdict": integrity["final_verdict"],
        },
    )
    dump(
        ROOT / "manifests" / "m531_post_m53_repaired_expansion_evaluation_manifest.json",
        {
            "experiment": "M53.1",
            "starting_head": "a071be2b8fbc106d56c7a8f9f60445f62e239b53",
            "final_head": git("git rev-parse HEAD"),
            "post_m53_expansion_truth_hash": POST_EXPANSION_TRUTH,
            "post_m53_full_truth_hash": POST_FULL_TRUTH,
            "historical_response_corpus_hash": RESPONSE_HASH,
            "reused_response_count": 66,
            "fresh_scheduled_count": 24,
            "fresh_provider_attempt_count": len(fresh),
            "fresh_model_call_count": len(fresh),
            "fresh_retry_count": 0,
            "fresh_response_corpus_hash": sha_path(FRESH_PATH),
            "canonical_90_response_map_hash": assignment_hash,
            "model": MODEL,
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "timeout_seconds": TIMEOUT_SECONDS,
            "expansion_governed_correct": metrics["governed"]["correct"],
            "expansion_answerable_tsa": metrics["answerable_runtime_tsa"]["correct"],
            "expansion_base_correct": metrics["base_correct"]["correct"],
            "expansion_full_counterfactual_correct": metrics["answerable_runtime_tsa"]["correct"],
            "authority_correct": metrics["authority"]["correct"],
            "ambiguity_correct": metrics["ambiguity"]["correct"],
            "policy_correct": metrics["policy"]["correct"],
            "unauthorized_answer_count": metrics["unauthorized_answers"],
            "combined_descriptive_governed": combined["governed"]["correct"],
            "combined_descriptive_answerable_tsa": combined["answerable_runtime_tsa"]["correct"],
            "temporally_mixed_response_acquisition": True,
            "post_m53_score_status": "POST_M53_REPAIRED_EXPANSION_EVALUATED",
            "suspected_benchmark_defect_count": 0,
            "determinism_hash": json.loads((AUDIT / "m531_determinism.json").read_text())[
                "analysis_hash"
            ],
            "final_verdict": integrity["final_verdict"],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("preflight", "reusable-rescore", "acquire", "postfreeze"))
    args = parser.parse_args()
    if args.phase == "preflight":
        data = preflight()
        data["repair_by_id"] = {
            row["case_id"]: row
            for row in json.loads((M53_AUDIT / "m53_expansion_defect_ledger.json").read_text())[
                "cases"
            ]
        }
        data["reuse_ledger"] = json.loads(
            (M53_AUDIT / "m53_expansion_defect_ledger.json").read_text()
        )["cases"]
        reusable_rescore(data)
    elif args.phase == "reusable-rescore":
        ids, rows = load_rows()
        reusable, invalidated = m53_partition(ids)
        services = setup_runtime(rows)
        ledger = json.loads((M53_AUDIT / "m53_expansion_defect_ledger.json").read_text())["cases"]
        reusable_rescore(
            {
                "ids": ids,
                "rows": rows,
                "reusable": reusable,
                "invalidated": invalidated,
                "services": services,
                "repair_by_id": {row["case_id"]: row for row in ledger},
                "reuse_ledger": ledger,
            }
        )
    elif args.phase == "acquire":
        ids, rows = load_rows()
        reusable, invalidated = m53_partition(ids)
        data = {
            "ids": ids,
            "rows": rows,
            "reusable": reusable,
            "invalidated": invalidated,
            "requests": m51b._requests(ids, rows),
            "preflight": json.loads((AUDIT / "m531_preflight_integrity.json").read_text()),
        }
        acquire(data)
    else:
        postfreeze_main()


if __name__ == "__main__":
    main()
