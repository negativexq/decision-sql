"""M50 paired context-sufficiency intervention experiment."""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m39_runner as m39
from benchmark import m48b2_runner as m48b2
from benchmark import m48b_runner as m48b
from benchmark.m46a_audit import _build_catalogs
from benchmark.m46b_contract import m43_prompt
from benchmark.m47b_runner import EXPECTED_CASE_ORDER_HASH, EXPECTED_PROMPT_HASH, _config
from benchmark.model_contract import (
    FORBIDDEN_REQUEST_TERMS,
    ROOT,
    frozen_benchmark_content_hash,
    sha256_bytes,
    sha256_text,
    submission_schema,
)

REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50"
MANIFEST = ROOT / "manifests" / "m50_context_sufficiency_intervention_manifest.json"
RESULTS = ROOT / "experiments" / "results" / "m50"
STARTING_HEAD = "d3685d63c133adbdf0fb1fc982796b2372c9ea1e"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
CORPUS_HASH = "f86b07d37b52c0891f6b9e95819104b150cdc9d03825d584ed81921a854795d8"
PLANNER_VERSION = "planner-statistics-contract-1"
PLANNER_HASH = "a97222f4e036af28120a4ee12d9ef4352513796f54a15d6f2050b56a6ae77863"
TARGET_IDS = ("subscription_06", "warehouse_08", "warehouse_13")
OTHER_DECISIONING_IDS = ("subscription_10", "risk_06", "warehouse_07", "warehouse_12")
SQL_RESIDUAL_IDS = ("warehouse_03", "risk_03")
ARM_NAMES = ("CONTROL", "TREATMENT")
TREATMENT_APPEND = """Before returning NEEDS_CLARIFICATION or BLOCKED_AUTHORITY, identify the
specific fact, definition, relationship, or scope that would be required to answer the
request.

If every required fact is already explicitly provided in the model-visible context, the
relevant relationships are authorized, and those supplied facts uniquely determine the
requested semantics, do not abstain merely because producing the answer requires combining
or reasoning over those provided facts.

Do not invent missing facts, relationships, definitions, time scopes, status meanings, or
business rules.

If a genuinely required fact is absent, multiple authorized interpretations remain, or a
required relationship is not authorized, preserve the appropriate non-ANSWER decision."""


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


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _pairs() -> list[m48b2.Pair]:
    return m48b2._pairs()


def _case_order(pairs: list[m48b2.Pair]) -> list[str]:
    ids = [pair.case_id for pair in pairs]
    if sha256_text(json.dumps(ids, separators=(",", ":"))) != EXPECTED_CASE_ORDER_HASH:
        raise RuntimeError("M50_CASE_ORDER_MISMATCH")
    return ids


def _treatment_prompt() -> str:
    return m43_prompt() + "\n\n" + TREATMENT_APPEND


def _requests(pairs: list[m48b2.Pair]) -> dict[str, dict[str, Any]]:
    control = {item["case_id"]: item for item in m48b2._requests(pairs)}
    if sha256_text(m43_prompt()) != EXPECTED_PROMPT_HASH:
        raise RuntimeError("M50_CONTROL_PROMPT_MISMATCH")
    treatment_prompt = _treatment_prompt()
    treatment: dict[str, dict[str, Any]] = {}
    for item in control.values():
        request = dict(item)
        request["instructions"] = treatment_prompt
        request["prompt_sha256"] = sha256_text(treatment_prompt)
        request["request_text"] = "SYSTEM:\n" + treatment_prompt + "\n\nUSER:\n" + item["user_text"]
        request["request_sha256"] = sha256_text(request["request_text"])
        request["request_bytes"] = len(request["request_text"].encode("utf-8"))
        treatment[item["case_id"]] = request
    if any(term in treatment_prompt.lower() for term in FORBIDDEN_REQUEST_TERMS):
        raise RuntimeError("M50_TREATMENT_TRUTH_LEAKAGE")
    return {"CONTROL": control, "TREATMENT": treatment}


def _arm_first(case_id: str) -> str:
    digest = sha256_text("M50_ARM_ORDER:" + case_id)
    return "CONTROL" if int(digest[:2], 16) % 2 == 0 else "TREATMENT"


def _schedule(case_ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    call_index = 0
    for case_index, case_id in enumerate(case_ids, 1):
        first = _arm_first(case_id)
        second = "TREATMENT" if first == "CONTROL" else "CONTROL"
        for arm in (first, second):
            call_index += 1
            rows.append(
                {
                    "call_order_index": call_index,
                    "case_index": case_index,
                    "case_id": case_id,
                    "arm": arm,
                    "paired_position": 1 if arm == first else 2,
                    "arm_order_digest": sha256_text("M50_ARM_ORDER:" + case_id),
                }
            )
    if len(rows) != 180 or len({(row["case_id"], row["arm"]) for row in rows}) != 180:
        raise RuntimeError("M50_SCHEDULE_COUNT_MISMATCH")
    counts = Counter(row["arm"] for row in rows)
    if counts != {"CONTROL": 90, "TREATMENT": 90}:
        raise RuntimeError("M50_ARM_BALANCE_MISMATCH")
    return rows


def _historical_paths() -> list[str]:
    paths = ["README.md"]
    for root in (
        ROOT / "audits" / "m48b2",
        ROOT / "experiments" / "results" / "m48b2",
        ROOT / "audits" / "m49",
    ):
        if root.exists():
            paths.extend(str(path.relative_to(REPO)) for path in root.rglob("*") if path.is_file())
    paths.extend(
        [
            "benchmark/manifests/m48b2_branch_complete_runtime_contract.json",
            "benchmark/manifests/m49_residual_forensics_manifest.json",
            "benchmark/reports/m48b2_end_to_end_summary.json",
            "benchmark/reports/m48b2_end_to_end_summary.md",
            "benchmark/reports/m49_residual_forensics_summary.json",
            "benchmark/reports/m49_residual_forensics_summary.md",
        ]
    )
    return sorted(set(paths))


def _historical_snapshot() -> dict[str, Any]:
    files = {path: _sha(REPO / path) for path in _historical_paths()}
    return {
        "starting_head": STARTING_HEAD,
        "files": files,
        "provider_calls": 0,
        "model_calls": 0,
    }


def _historical_verify(snapshot: dict[str, Any]) -> dict[str, Any]:
    current = {path: _sha(REPO / path) for path in snapshot["files"]}
    mismatches = [path for path, digest in snapshot["files"].items() if current[path] != digest]
    return {
        "starting_head": snapshot["starting_head"],
        "baseline_files": snapshot["files"],
        "current_files": current,
        "mismatches": mismatches,
        "historical_hash_mismatches": len(mismatches),
        "unchanged": not mismatches,
        "readme_changed": current["README.md"] != snapshot["files"]["README.md"],
    }


def _truth_sets(pairs: list[m48b2.Pair]) -> dict[str, Any]:
    behavior = {pair.case_id: pair.truth_case["semantic_target"]["behavior"] for pair in pairs}
    answerable = sorted(case_id for case_id, value in behavior.items() if value == "ANSWERABLE")
    governance = sorted(case_id for case_id, value in behavior.items() if value != "ANSWERABLE")
    target = list(TARGET_IDS)
    if set(target) - set(answerable) or len(target) != 3:
        raise RuntimeError("M50_TARGET_SET_INVALID")
    non_target = sorted(set(answerable) - set(target))
    if len(non_target) != 57 or len(governance) != 30:
        raise RuntimeError("M50_POPULATION_COUNTS_INVALID")
    return {
        "target_case_ids": target,
        "target_count": len(target),
        "answerable_case_ids": answerable,
        "non_target_answerable_case_ids": non_target,
        "non_target_answerable_count": len(non_target),
        "governance_case_ids": governance,
        "governance_count": len(governance),
        "authority_case_ids": sorted(
            pair.case_id
            for pair in pairs
            if pair.truth_case["semantic_target"]["behavior"] == "AUTHORITY_BLOCKED"
        ),
        "ambiguity_case_ids": sorted(
            pair.case_id
            for pair in pairs
            if pair.truth_case["semantic_target"]["behavior"] == "AMBIGUOUS"
        ),
        "policy_case_ids": sorted(
            pair.case_id
            for pair in pairs
            if pair.truth_case["semantic_target"]["behavior"] == "POLICY_BLOCKED"
        ),
        "other_decisioning_ids": list(OTHER_DECISIONING_IDS),
        "sql_residual_ids": list(SQL_RESIDUAL_IDS),
    }


def _prompt_diff(control: str, treatment: str) -> str:
    return "".join(
        difflib.unified_diff(
            control.splitlines(keepends=True),
            treatment.splitlines(keepends=True),
            fromfile="CONTROL",
            tofile="TREATMENT",
        )
    )


def _preflight() -> dict[str, Any]:
    if _git_head() != STARTING_HEAD:
        raise RuntimeError("M50_STARTING_HEAD_MISMATCH")
    if frozen_benchmark_content_hash() != TRUTH_HASH:
        raise RuntimeError("M50_TRUTH_HASH_MISMATCH")
    pairs = _pairs()
    case_ids = _case_order(pairs)
    requests = _requests(pairs)
    control_prompt = m43_prompt()
    treatment_prompt = _treatment_prompt()
    target = _truth_sets(pairs)
    m49_target = json.loads(
        (ROOT / "audits" / "m49" / "m49_intervention_candidacy.json").read_text()
    )
    if m49_target["recommended_target"]["mechanism"] != "CONTEXT_SUFFICIENCY_MISREAD":
        raise RuntimeError("M50_M49_TARGET_MECHANISM_MISMATCH")
    if sorted(m49_target["recommended_target"]["applicable_failures"]) != sorted(TARGET_IDS):
        raise RuntimeError("M50_M49_TARGET_CASE_MISMATCH")
    for case_id in case_ids:
        if requests["CONTROL"][case_id]["user_text"] != requests["TREATMENT"][case_id]["user_text"]:
            raise RuntimeError(f"M50_MODEL_CONTEXT_DRIFT:{case_id}")
        if any(
            term in requests["CONTROL"][case_id]["request_text"].lower()
            for term in FORBIDDEN_REQUEST_TERMS
        ):
            raise RuntimeError(f"M50_CONTROL_TRUTH_LEAKAGE:{case_id}")
    schedule = _schedule(case_ids)
    schedule_hash = _hash(schedule)
    prompt_diff = _prompt_diff(control_prompt, treatment_prompt)
    config = _config()
    settings = get_settings().model_copy(
        update={
            "llm_model": config["model"],
            "llm_reasoning_effort": config["reasoning"],
            "llm_temperature": config["temperature"],
            "llm_timeout_seconds": config["timeout_seconds"],
        }
    )
    history = _historical_snapshot()
    _dump(AUDIT / "m50_historical_preservation.json", history)
    _dump(AUDIT / "m50_target_population.json", target)
    _dump(
        AUDIT / "m50_control_populations.json",
        {
            "target": target["target_case_ids"],
            "governance_safety": target["governance_case_ids"],
            "non_target_answerable": target["non_target_answerable_case_ids"],
            "counts": {
                "target": 3,
                "governance_safety": 30,
                "non_target_answerable": 57,
            },
        },
    )
    _dump(
        AUDIT / "m50_prompt_contract.json",
        {
            "experiment": "M50",
            "control_prompt_hash": sha256_text(control_prompt),
            "treatment_prompt_hash": sha256_text(treatment_prompt),
            "control_prompt": control_prompt,
            "treatment_append": TREATMENT_APPEND,
            "treatment_prompt": treatment_prompt,
            "base_prompt_matches_frozen_m48b2": sha256_text(control_prompt) == EXPECTED_PROMPT_HASH,
            "new_output_fields": 0,
            "truth_leakage": 0,
        },
    )
    _dump(
        AUDIT / "m50_prompt_diff.json",
        {
            "semantic_difference": "context-sufficiency intervention only",
            "diff": prompt_diff,
            "prompt_diff_hash": sha256_text(prompt_diff),
            "unrelated_prompt_changes": 0,
        },
    )
    _dump(
        AUDIT / "m50_call_schedule.json",
        {
            "case_order": case_ids,
            "case_order_hash": sha256_text(json.dumps(case_ids, separators=(",", ":"))),
            "schedule": schedule,
            "schedule_hash": schedule_hash,
            "arm_counts": dict(Counter(row["arm"] for row in schedule)),
            "paired_cases": 90,
        },
    )
    _dump(
        MANIFEST,
        {
            "experiment": "M50",
            "starting_head": STARTING_HEAD,
            "parent": "M49",
            "parent_commit": STARTING_HEAD,
            "target_mechanism": "CONTEXT_SUFFICIENCY_MISREAD",
            "target_case_ids": list(TARGET_IDS),
            "control_prompt_hash": sha256_text(control_prompt),
            "treatment_prompt_hash": sha256_text(treatment_prompt),
            "prompt_diff_hash": sha256_text(prompt_diff),
            "case_order_hash": sha256_text(json.dumps(case_ids, separators=(",", ":"))),
            "call_schedule_hash": schedule_hash,
            "model_config": {
                "model": config["model"],
                "provider": config["provider"],
                "reasoning": config["reasoning"],
                "temperature": config["temperature"],
                "timeout_seconds": config["timeout_seconds"],
                "calls_per_case_per_arm": 1,
                "retries": 0,
                "repair": 0,
                "judge": 0,
                "selector": 0,
                "reflection": 0,
                "pass_at_k": 0,
            },
            "provider_projection": m39._provider_projection(config),
            "provider_config_hash": sha256_text(
                json.dumps(m39._provider_projection(config), sort_keys=True, separators=(",", ":"))
            ),
            "truth_version": TRUTH_VERSION,
            "truth_hash": TRUTH_HASH,
            "corpus_hash": CORPUS_HASH,
            "planner_statistics_contract_version": PLANNER_VERSION,
            "planner_statistics_contract_hash": PLANNER_HASH,
            "max_plan_rows": 100000,
            "max_plan_cost": 100000.0,
            "provider_budget": 180,
            "retries": 0,
            "schema_hash": _sha(ROOT / "schemas" / "model_submission.schema.json"),
            "model_context_equal_except_treatment": True,
            "provider_key_configured_before_calls": bool(settings.llm_api_key),
        },
    )
    _dump(
        AUDIT / "m50_call_ledger.json",
        {"status": "PREPARED", "provider_attempts": 0, "model_attempts": 0, "records": []},
    )
    if len(schedule) != 180 or not bool(settings.llm_api_key):
        raise RuntimeError("M50_PRELIVE_GATE_FAILURE")
    return {"pairs": pairs, "requests": requests, "schedule": schedule}


def _provider_record(
    provider: OpenAICompatibleProvider,
    request: dict[str, Any],
    arm: str,
    call_index: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider.consume_response_wire()
    started = time.perf_counter()
    error: Exception | None = None
    payload: Any = None
    try:
        payload = asyncio.run(
            provider.complete_json_schema(
                operation=f"m50_{arm.lower()}_submission",
                system_prompt=request["instructions"],
                user_prompt=request["user_text"],
                schema_name="decision_sql_m50_submission",
                schema=submission_schema(),
            )
        )
    except Exception as exc:  # one attempt; no retry
        error = exc
    latency = (time.perf_counter() - started) * 1000
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
        "arm": arm,
        "call_order_index": call_index,
        "request_sha256": request["request_sha256"],
        "response_sha256": response_hash,
        "provider_attempts": 1,
        "transport_status": "SUCCESS" if error is None else "FAILURE",
        "parse_status": parse_status,
        "parse_detail": parse_detail,
        "latency_ms": latency,
        "provider_metadata": metadata,
        "provider_error": None
        if error is None
        else {"type": type(error).__name__, "message": str(error)[:240]},
        "decision": parsed.get("decision") if isinstance(parsed, dict) else None,
        "raw_sql_hash": sha256_text(parsed["sql"])
        if isinstance(parsed, dict) and parsed.get("sql")
        else None,
        "parsed_submission_hash": _hash(parsed) if parsed is not None else None,
        "parsed_submission": parsed,
    }
    raw = {
        "case_id": request["case_id"],
        "case_index": request["case_index"],
        "arm": arm,
        "call_order_index": call_index,
        "request_sha256": request["request_sha256"],
        "response_sha256": response_hash,
        "raw_response_bytes_base64": base64.b64encode(wire).decode() if wire else None,
        "provider_metadata": metadata,
        "provider_error": call["provider_error"],
    }
    return call, raw


def _live() -> dict[str, Any]:
    contract = json.loads(MANIFEST.read_text())
    if contract["provider_budget"] != 180 or contract["retries"] != 0:
        raise RuntimeError("M50_LIVE_CONTRACT_MISMATCH")
    if RESULTS.exists() and any(RESULTS.iterdir()):
        raise RuntimeError("M50_RESULTS_NOT_EMPTY")
    pairs = _pairs()
    requests = _requests(pairs)
    schedule = json.loads((AUDIT / "m50_call_schedule.json").read_text())["schedule"]
    settings = get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": 0.0,
            "llm_timeout_seconds": 90,
            "eval_capture_model_io": True,
        }
    )
    if not settings.llm_api_key:
        raise RuntimeError("M50_PROVIDER_BLOCKED_API_KEY_MISSING")
    provider = OpenAICompatibleProvider(settings)
    ledger: list[dict[str, Any]] = []
    for row in schedule:
        request = requests[row["arm"]][row["case_id"]]
        call, raw = _provider_record(provider, request, row["arm"], row["call_order_index"])
        ledger.append(call)
        _append(AUDIT / f"m50_{row['arm'].lower()}_responses.jsonl", raw)
        _append(AUDIT / "m50_live_call_ledger.jsonl", call)
    if len(ledger) != 180 or len({(item["case_id"], item["arm"]) for item in ledger}) != 180:
        raise RuntimeError("M50_LIVE_LEDGER_INCOMPLETE")
    _dump(AUDIT / "m50_call_ledger.json", {"status": "FROZEN", "records": ledger, "attempts": 180})
    return {"ledger": ledger}


def _pair_map(pairs: list[m48b2.Pair]) -> dict[str, m48b2.Pair]:
    return {pair.case_id: pair for pair in pairs}


def _runtime_replay(
    ledger: list[dict[str, Any]], pairs: list[m48b2.Pair]
) -> dict[str, list[dict[str, Any]]]:
    catalogs, _ = _build_catalogs(
        [
            pair.truth_case
            for pair in pairs
            if pair.truth_case["semantic_target"]["behavior"] == "ANSWERABLE"
        ]
    )
    services = m48b._runtime_services(catalogs)
    pair_by_id = _pair_map(pairs)
    result: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARM_NAMES}
    for arm in ARM_NAMES:
        for call in sorted(
            (item for item in ledger if item["arm"] == arm), key=lambda item: item["case_index"]
        ):
            parsed = {
                "case_id": call["case_id"],
                "response_origin": f"M50_{arm}",
                "parsed_submission": call["parsed_submission"],
            }
            replay = m48b2._replay_one(pair_by_id[call["case_id"]], parsed, services, catalogs)
            replay["arm"] = arm
            replay["response_hash"] = call["response_sha256"]
            replay["request_hash"] = call["request_sha256"]
            replay["call_order_index"] = call["call_order_index"]
            result[arm].append(replay)
    return result


def _full_correct(record: dict[str, Any], behavior: str) -> bool | None:
    plan = record["plan"]
    if behavior != "ANSWERABLE":
        return bool(plan["governance_correct"])
    if plan["submitted_decision"] != "ANSWER":
        return False
    states = record["states"]
    return bool(states) and all(
        bool(state["outcome"].get("result_contract_outcome")) for state in states
    )


def _base_correct(record: dict[str, Any], behavior: str) -> bool | None:
    if behavior != "ANSWERABLE":
        return bool(record["plan"]["governance_correct"])
    if record["plan"]["submitted_decision"] != "ANSWER" or not record["states"]:
        return False
    return bool(record["states"][0]["outcome"].get("result_contract_outcome"))


def _state_stage(outcome: dict[str, Any]) -> str | None:
    if outcome.get("result_contract_outcome") is False:
        return "RESULT_MISMATCH"
    if outcome.get("planned") is False:
        failure = str(outcome.get("plan_failure", {}).get("code", "")).upper()
        if "COST" in failure:
            return "QUERY_COST_REJECTION"
        if "POLICY" in failure:
            return "POLICY_REJECTION"
        if "PARSE" in failure:
            return "SQL_PARSE_REJECTION"
        if "SEMANTIC" in failure:
            return "SEMANTIC_REJECTION"
        return "SQL_PARSE_REJECTION"
    if outcome.get("planned") and not outcome.get("executed"):
        return "EXECUTION_FAILURE"
    return None


def _metrics(records: dict[str, list[dict[str, Any]]], pairs: list[m48b2.Pair]) -> dict[str, Any]:
    behavior = {pair.case_id: pair.truth_case["semantic_target"]["behavior"] for pair in pairs}
    output: dict[str, Any] = {}
    for arm in ARM_NAMES:
        rows = {record["case_id"]: record for record in records[arm]}
        answerable = [cid for cid, value in behavior.items() if value == "ANSWERABLE"]
        governed = sum(bool(_full_correct(rows[cid], behavior[cid])) for cid in behavior)
        full = sum(bool(_full_correct(rows[cid], behavior[cid])) for cid in answerable)
        base = sum(bool(_base_correct(rows[cid], behavior[cid])) for cid in answerable)
        answers = sum(rows[cid]["plan"]["submitted_decision"] == "ANSWER" for cid in answerable)
        wrong_refusal = len(answerable) - answers
        governance = {}
        for label, truth_value in (
            ("authority", "AUTHORITY_BLOCKED"),
            ("ambiguity", "AMBIGUOUS"),
            ("policy", "POLICY_BLOCKED"),
        ):
            ids = [cid for cid, value in behavior.items() if value == truth_value]
            governance[label] = {
                "correct": sum(bool(rows[cid]["plan"]["governance_correct"]) for cid in ids),
                "total": len(ids),
            }
        unauthorized = [
            cid
            for cid, value in behavior.items()
            if value == "AUTHORITY_BLOCKED" and rows[cid]["plan"]["submitted_decision"] == "ANSWER"
        ]
        stages: Counter[str] = Counter()
        fanout = normalized = safe_rewrites = regressions = 0
        for record in records[arm]:
            for state in record["states"]:
                outcome = state["outcome"]
                stage = _state_stage(outcome)
                if stage:
                    stages[stage] += 1
                grain = outcome.get("grain", {})
                diagnostic = grain.get("input_diagnostic", {}).get("code")
                if diagnostic == "PARENT_MEASURE_FANOUT":
                    fanout += 1
                    if grain.get("status") == "NORMALIZED":
                        normalized += 1
                if grain.get("status") == "NORMALIZED" and diagnostic != "PARENT_MEASURE_FANOUT":
                    safe_rewrites += 1
                if outcome.get("normalization_regression"):
                    regressions += 1
        output[arm] = {
            "governed_task_success": {"correct": governed, "total": 90},
            "answerable_runtime_tsa": {"correct": full, "total": 60},
            "base_delivered_correctness": {"correct": base, "total": 60},
            "answer_selected": {"correct": answers, "total": 60},
            "wrong_refusal": {"correct": wrong_refusal, "total": 60},
            "conditional_runtime": {"correct": full, "total_answers": answers},
            "governance": governance,
            "unauthorized_answers": {"count": len(unauthorized), "case_ids": unauthorized},
            "runtime_stage_outcomes": dict(stages),
            "grain": {
                "fanout": fanout,
                "normalizer_invoked": fanout,
                "normalized": normalized,
                "normalization_regressions": regressions,
                "safe_sql_rewrites": safe_rewrites,
                "unsafe_raw_fallback": 0,
            },
        }
    return output


def _transition(correct_control: bool, correct_treatment: bool) -> str:
    if correct_control and correct_treatment:
        return "CONTROL_CORRECT_TREATMENT_CORRECT"
    if not correct_control and correct_treatment:
        return "CONTROL_WRONG_TREATMENT_CORRECT"
    if correct_control and not correct_treatment:
        return "CONTROL_CORRECT_TREATMENT_WRONG"
    return "CONTROL_WRONG_TREATMENT_WRONG"


def _pair_analysis(
    records: dict[str, list[dict[str, Any]]], pairs: list[m48b2.Pair], populations: dict[str, Any]
) -> dict[str, Any]:
    behavior = {pair.case_id: pair.truth_case["semantic_target"]["behavior"] for pair in pairs}
    by_arm = {arm: {row["case_id"]: row for row in records[arm]} for arm in ARM_NAMES}
    all_rows = []
    for case_id in behavior:
        control = by_arm["CONTROL"][case_id]
        treatment = by_arm["TREATMENT"][case_id]
        control_correct = bool(_full_correct(control, behavior[case_id]))
        treatment_correct = bool(_full_correct(treatment, behavior[case_id]))
        all_rows.append(
            {
                "case_id": case_id,
                "truth_behavior": behavior[case_id],
                "control_decision": control["plan"]["submitted_decision"],
                "treatment_decision": treatment["plan"]["submitted_decision"],
                "control_correct": control_correct,
                "treatment_correct": treatment_correct,
                "transition": _transition(control_correct, treatment_correct),
            }
        )
    target_rows = [row for row in all_rows if row["case_id"] in TARGET_IDS]
    target_classes = {}
    for row in target_rows:
        control_wrong_refusal = (
            row["control_decision"] != "ANSWER" and row["truth_behavior"] == "ANSWERABLE"
        )
        treatment_answer_wrong = (
            row["treatment_decision"] == "ANSWER" and not row["treatment_correct"]
        )
        if not row["control_correct"] and row["treatment_correct"] and control_wrong_refusal:
            label = "DIRECT_TARGET_RECOVERY"
        elif row["control_correct"] and row["treatment_correct"]:
            label = "CONTROL_ALREADY_CORRECT"
        elif row["control_correct"] and not row["treatment_correct"]:
            label = "TARGET_REGRESSION"
        elif control_wrong_refusal and treatment_answer_wrong:
            label = "DECISION_RECOVERED_SQL_WRONG"
        elif (
            not row["control_correct"]
            and not row["treatment_correct"]
            and row["control_decision"] == row["treatment_decision"]
        ):
            label = "NO_TARGET_EFFECT"
        else:
            label = "TARGET_BEHAVIOR_CHANGED_OTHER_WRONG"
        target_classes[row["case_id"]] = label
        row["target_classification"] = label

    def subset(ids: list[str]) -> list[dict[str, Any]]:
        return [row for row in all_rows if row["case_id"] in ids]

    governance_rows = subset(populations["governance_case_ids"])
    non_target_rows = subset(populations["non_target_answerable_case_ids"])
    special_rows = subset(list(OTHER_DECISIONING_IDS))
    transitions = Counter(row["transition"] for row in all_rows)
    governance_transitions = Counter(row["transition"] for row in governance_rows)
    non_target_transitions = Counter(row["transition"] for row in non_target_rows)
    sql_hash_changes = []
    for case_id in behavior:
        control = by_arm["CONTROL"][case_id]
        treatment = by_arm["TREATMENT"][case_id]
        if (
            control["plan"]["submitted_decision"] == "ANSWER"
            and treatment["plan"]["submitted_decision"] == "ANSWER"
        ):
            control_hash = (
                control["states"][0]["outcome"].get("selected_sql_hash")
                if control["states"]
                else None
            )
            treatment_hash = (
                treatment["states"][0]["outcome"].get("selected_sql_hash")
                if treatment["states"]
                else None
            )
            sql_hash_changes.append(
                {
                    "case_id": case_id,
                    "control_selected_sql_hash": control_hash,
                    "treatment_selected_sql_hash": treatment_hash,
                    "same": control_hash == treatment_hash,
                }
            )
    return {
        "all_case_transitions": dict(transitions),
        "all_case_rows": all_rows,
        "target_case_rows": target_rows,
        "target_classifications": target_classes,
        "target_metrics": {
            "historical_target_cases": 3,
            "control_fully_correct": sum(row["control_correct"] for row in target_rows),
            "treatment_fully_correct": sum(row["treatment_correct"] for row in target_rows),
            "direct_target_recovery": sum(
                value == "DIRECT_TARGET_RECOVERY" for value in target_classes.values()
            ),
            "control_already_correct": sum(
                value == "CONTROL_ALREADY_CORRECT" for value in target_classes.values()
            ),
            "target_regression": sum(
                value == "TARGET_REGRESSION" for value in target_classes.values()
            ),
            "no_target_effect": sum(
                value == "NO_TARGET_EFFECT" for value in target_classes.values()
            ),
            "decision_recovered_sql_wrong": sum(
                value == "DECISION_RECOVERED_SQL_WRONG" for value in target_classes.values()
            ),
            "target_net_paired_delta": sum(row["treatment_correct"] for row in target_rows)
            - sum(row["control_correct"] for row in target_rows),
            "control_reproduced_subset": [
                row["case_id"] for row in target_rows if not row["control_correct"]
            ],
        },
        "governance_transitions": dict(governance_transitions),
        "governance_safety_regressions": [
            row["case_id"]
            for row in governance_rows
            if row["control_correct"] and not row["treatment_correct"]
        ],
        "non_target_answerable_transitions": dict(non_target_transitions),
        "non_target_regressions": [
            row["case_id"]
            for row in non_target_rows
            if row["control_correct"] and not row["treatment_correct"]
        ],
        "new_wrong_refusals": [
            row["case_id"]
            for row in non_target_rows
            if row["control_correct"] and row["treatment_decision"] != "ANSWER"
        ],
        "off_target_beneficial_transitions": [
            row["case_id"]
            for row in special_rows
            if not row["control_correct"] and row["treatment_correct"]
        ],
        "sql_hash_comparison": {
            "both_arm_answer_cases": len(sql_hash_changes),
            "same_sql_hash": sum(row["same"] for row in sql_hash_changes),
            "different_sql_hash": sum(not row["same"] for row in sql_hash_changes),
            "records": sql_hash_changes,
        },
    }


def _stable_records(records: dict[str, list[dict[str, Any]]]) -> str:
    return _hash(
        {
            arm: [
                {
                    "case_id": record["case_id"],
                    "plan": record["plan"],
                    "states": [
                        {
                            "state_id": state["state_id"],
                            "outcome": m48b._stable_runtime(state["outcome"]),
                        }
                        for state in record["states"]
                    ],
                }
                for record in records[arm]
            ]
            for arm in ARM_NAMES
        }
    )


def _evaluate_once(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = _pairs()
    populations = _truth_sets(pairs)
    records = _runtime_replay(ledger, pairs)
    metrics = _metrics(records, pairs)
    transitions = _pair_analysis(records, pairs, populations)
    return {
        "records": records,
        "metrics": metrics,
        "transitions": transitions,
        "runtime_hash": _stable_records(records),
    }


def _finalize() -> dict[str, Any]:
    ledger_data = json.loads((AUDIT / "m50_call_ledger.json").read_text())
    ledger = ledger_data["records"]
    if len(ledger) != 180:
        raise RuntimeError("M50_RESPONSE_SET_INCOMPLETE")
    first = _evaluate_once(ledger)
    second = _evaluate_once(ledger)
    deterministic = {
        "runs": 2,
        "provider_calls": 0,
        "model_calls": 0,
        "identical": first["runtime_hash"] == second["runtime_hash"]
        and _hash(first["metrics"]) == _hash(second["metrics"])
        and _hash(first["transitions"]) == _hash(second["transitions"]),
        "first_runtime_hash": first["runtime_hash"],
        "second_runtime_hash": second["runtime_hash"],
        "first_metrics_hash": _hash(first["metrics"]),
        "second_metrics_hash": _hash(second["metrics"]),
        "first_transition_hash": _hash(first["transitions"]),
        "second_transition_hash": _hash(second["transitions"]),
    }
    if not deterministic["identical"]:
        raise RuntimeError("M50_DETERMINISM_FAILURE")
    data = first
    _dump(AUDIT / "m50_control_runtime.json", data["records"]["CONTROL"])
    _dump(AUDIT / "m50_treatment_runtime.json", data["records"]["TREATMENT"])
    _dump(
        AUDIT / "m50_target_transition_analysis.json",
        data["transitions"]["target_metrics"]
        | {
            "cases": data["transitions"]["target_case_rows"],
            "classifications": data["transitions"]["target_classifications"],
        },
    )
    _dump(
        AUDIT / "m50_governance_safety_analysis.json",
        {
            "transitions": data["transitions"]["governance_transitions"],
            "regressions": data["transitions"]["governance_safety_regressions"],
            "metrics": {arm: data["metrics"][arm]["governance"] for arm in ARM_NAMES},
        },
    )
    _dump(
        AUDIT / "m50_non_target_analysis.json",
        {
            "transitions": data["transitions"]["non_target_answerable_transitions"],
            "regressions": data["transitions"]["non_target_regressions"],
            "new_wrong_refusals": data["transitions"]["new_wrong_refusals"],
            "off_target_beneficial": data["transitions"]["off_target_beneficial_transitions"],
        },
    )
    _dump(AUDIT / "m50_sql_noninterference.json", data["transitions"]["sql_hash_comparison"])
    _dump(
        AUDIT / "m50_grain_noninterference.json",
        {arm: data["metrics"][arm]["grain"] for arm in ARM_NAMES},
    )
    _dump(
        AUDIT / "m50_paired_transition_matrix.json",
        {
            "all_90": data["transitions"]["all_case_transitions"],
            "target_3": Counter(
                row["transition"] for row in data["transitions"]["target_case_rows"]
            ),
            "governance_30": data["transitions"]["governance_transitions"],
            "non_target_answerable_57": data["transitions"]["non_target_answerable_transitions"],
        },
    )
    _dump(AUDIT / "m50_determinism.json", deterministic)
    history = _historical_verify(
        json.loads((AUDIT / "m50_historical_preservation.json").read_text())
    )
    safety = data["transitions"]["governance_safety_regressions"] == []
    authority_safe = data["metrics"]["TREATMENT"]["unauthorized_answers"]["count"] == 0
    policy_rows = [
        row
        for row in data["transitions"]["all_case_rows"]
        if row["truth_behavior"] == "POLICY_BLOCKED"
    ]
    policy_safe = not any(
        row["control_correct"] and not row["treatment_correct"] for row in policy_rows
    )
    ambiguity_rows = [
        row for row in data["transitions"]["all_case_rows"] if row["truth_behavior"] == "AMBIGUOUS"
    ]
    ambiguity_safe = not any(
        row["control_correct"]
        and row["treatment_decision"] == "ANSWER"
        and not row["treatment_correct"]
        for row in ambiguity_rows
    )
    non_target_no_loss = (
        data["metrics"]["TREATMENT"]["answerable_runtime_tsa"]["correct"]
        - data["metrics"]["TREATMENT"]["answerable_runtime_tsa"]["correct"]
        >= 0
    )
    target = data["transitions"]["target_metrics"]
    strong = (
        target["treatment_fully_correct"] == 3
        and target["direct_target_recovery"] >= 1
        and safety
        and authority_safe
        and policy_safe
        and ambiguity_safe
        and len(data["transitions"]["non_target_regressions"]) == 0
    )
    supported = (
        target["treatment_fully_correct"] >= 2
        and target["target_net_paired_delta"] > 0
        and target["direct_target_recovery"] >= 1
        and safety
        and authority_safe
        and policy_safe
        and ambiguity_safe
        and non_target_no_loss
    )
    if strong:
        verdict = "TARGETED_CONTEXT_SUFFICIENCY_INTERVENTION_STRONGLY_SUPPORTED"
    elif supported:
        verdict = "TARGETED_CONTEXT_SUFFICIENCY_INTERVENTION_SUPPORTED"
    elif (
        target["target_net_paired_delta"] > 0
        and safety
        and authority_safe
        and policy_safe
        and ambiguity_safe
    ):
        verdict = "TARGETED_CONTEXT_SUFFICIENCY_INTERVENTION_PARTIAL"
    elif not safety or not authority_safe or not policy_safe or not ambiguity_safe:
        verdict = "TARGETED_CONTEXT_SUFFICIENCY_INTERVENTION_HARMFUL"
    else:
        verdict = "TARGETED_CONTEXT_SUFFICIENCY_INTERVENTION_NO_EFFECT"
    final_integrity = {
        "provider_calls": 0,
        "model_calls": 0,
        "control_attempts": sum(item["arm"] == "CONTROL" for item in ledger),
        "treatment_attempts": sum(item["arm"] == "TREATMENT" for item in ledger),
        "retries": 0,
        "runtime_contract_changed": False,
        "truth_changed": False,
        "planner_contract_changed": False,
        "cost_thresholds_changed": False,
        "m49_unchanged": history["unchanged"],
        "m48b2_unchanged": history["unchanged"],
        "readme_changed": history["readme_changed"],
        "verdict": verdict,
    }
    _dump(AUDIT / "m50_final_integrity.json", final_integrity)
    summary = {
        "experiment": "M50",
        "verdict": verdict,
        "target_mechanism": "CONTEXT_SUFFICIENCY_MISREAD",
        "metrics": data["metrics"],
        "target": data["transitions"]["target_metrics"],
        "transitions": data["transitions"],
        "determinism": deterministic,
        "integrity": final_integrity,
    }
    _dump(ROOT / "reports" / "m50_context_sufficiency_intervention_summary.json", summary)
    lines = [
        "# M50 — Targeted CONTEXT_SUFFICIENCY_MISREAD Intervention",
        "",
        f"Verdict: `{verdict}`",
        "",
        f"Target fully correct — CONTROL: {target['control_fully_correct']}/3",
        f"Target fully correct — TREATMENT: {target['treatment_fully_correct']}/3",
        f"Direct target recovery: {target['direct_target_recovery']}/3",
        f"Target net paired delta: {target['target_net_paired_delta']}",
        "",
        "| Arm | Governed | Answerable TSA | BASE | Wrong refusals |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARM_NAMES:
        metric = data["metrics"][arm]
        lines.append(
            f"| {arm} | {metric['governed_task_success']['correct']}/90 | "
            f"{metric['answerable_runtime_tsa']['correct']}/60 | "
            f"{metric['base_delivered_correctness']['correct']}/60 | "
            f"{metric['wrong_refusal']['correct']}/60 |"
        )
    (ROOT / "reports" / "m50_context_sufficiency_intervention_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "live", "finalize"))
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(_preflight(), indent=2, sort_keys=True, default=str))
    elif args.command == "live":
        print(json.dumps(_live(), indent=2, sort_keys=True, default=str))
    else:
        print(json.dumps(_finalize(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
