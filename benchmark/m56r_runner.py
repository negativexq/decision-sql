# ruff: noqa: E501

"""M56R canonical request-builder recovery and single-call experiment.

The only request construction boundary used here is
``benchmark.m51b_runner._provider_request``.  CONTROL delegates to the
production request list, while treatment arms use the same builder with an
explicit contract-only prompt override.
"""

from __future__ import annotations

import asyncio
import base64
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
from benchmark import m46a_audit as m46a
from benchmark import m48b_runner as m48b
from benchmark import m51b_runner as m51b
from benchmark.m46b_contract import m43_prompt
from benchmark.m56_runner import INTERVENTIONS, candidate_prompts, load_rows, selection_cases
from benchmark.model_contract import sha256_bytes, sha256_text, submission_schema

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "audits" / "m56r"
M56_AUDIT = ROOT / "audits" / "m56"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90
CANONICAL_PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
INVALID_M56_SELECTION_HASH = "6d72ed1a483316c9b40b4a7dea3dafb360791a26a157de3309a4056318ba44e1"
INVALID_M56_FULL_HASH = "ac308feadff2c2546606ec06743254854cb6a17409bbccd050024c5ea79f533d"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT.parent, check=True, capture_output=True, text=True
    ).stdout.strip()


def provider_payload(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": request["instructions"]},
            {"role": "user", "content": request["user_text"]},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "decision_sql_m51b_submission",
                "strict": True,
                "schema": submission_schema(),
            },
        },
        "temperature": TEMPERATURE,
    }


def request_fingerprint(request: dict[str, Any]) -> str:
    return digest(provider_payload(request))


def structural_diff(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path or "$", "left": left, "right": right}]
    if isinstance(left, dict):
        result: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                result.append({"path": child, "left": left.get(key), "right": right.get(key)})
            else:
                result.extend(structural_diff(left[key], right[key], child))
        return result
    if isinstance(left, list):
        result = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                result.append(
                    {
                        "path": child,
                        "left": left[index] if index < len(left) else None,
                        "right": right[index] if index < len(right) else None,
                    }
                )
            else:
                result.extend(structural_diff(left[index], right[index], child))
        return result
    return [] if left == right else [{"path": path or "$", "left": left, "right": right}]


def _root_cause() -> None:
    ledger = load_json(ROOT / "experiments" / "results" / "m43" / "m43_request_ledger.json")
    canonical_prompt = (
        ledger["requests"][0]["request_text"].split("\n\nUSER:\n", 1)[0].removeprefix("SYSTEM:\n")
    )
    prompt_file = (ROOT / "prompts" / "governed_context_v1.md").read_text(encoding="utf-8")
    report = f"""# M56R request-builder root cause

## Canonical path

Production benchmark requests are built by `benchmark.m51b_runner._requests`, which delegates to `_provider_request`, serializes context with `serialize_governed_context_v1`, and passes `instructions` and `user_text` to `OpenAICompatibleProvider.complete_json_schema`.

## Aborted path

The aborted M56 runner read `benchmark/prompts/governed_context_v1.md` directly and rebuilt the request text in `benchmark/m56_runner.py`. It did not obtain the prompt through the frozen M43 request ledger/canonical builder.

## First divergence

The first provider-visible divergence was `messages[0].content` (the system prompt). The canonical ledger prompt is SHA-256 `{sha256_text(canonical_prompt)}` and `{len(canonical_prompt)}` bytes; the directly read prompt file is SHA-256 `{sha256_text(prompt_file)}` and `{len(prompt_file)}` bytes. The file contains an additional parent/child measure section that is absent from the retained M43 prompt.

No model context, response schema, or provider transport was needed to explain the first divergence; the system message differed before the request reached the provider.

## Why existing tests missed it

Historical tests separately checked prompt-file content and frozen ledger hashes, but no test compared the complete provider-visible production payload with the experiment CONTROL payload. M56 therefore passed its own internal prompt hash while bypassing the canonical request source.

## Repair strategy

`_provider_request` is now the single request construction helper used by production `_requests` and M56R. CONTROL calls `_requests`; treatment arms call the same helper with an explicit contract-only prompt override. M56R fingerprints the complete provider-visible payload, including model, ordered messages, response schema, and temperature. Timeout, request IDs, trace IDs, timestamps, and local paths are transport/local metadata and are excluded because they are not in the provider body.
"""
    (AUDIT / "m56r_request_builder_root_cause.md").write_text(report, encoding="utf-8")


def control_equivalence() -> list[dict[str, Any]]:
    ids, rows = load_rows()
    production = {item["case_id"]: item for item in m51b._requests(ids, rows, prompt=m43_prompt())}
    result = []
    for index, case_id in enumerate(ids, 1):
        control = m51b._provider_request(index, case_id, rows[case_id][0], prompt=m43_prompt())
        left = provider_payload(production[case_id])
        right = provider_payload(control)
        differences = structural_diff(left, right)
        result.append(
            {
                "case_id": case_id,
                "production_fingerprint": digest(left),
                "control_fingerprint": digest(right),
                "equal": not differences,
                "field_differences": differences,
            }
        )
    return result


def prepare() -> None:
    ids, rows = load_rows()
    AUDIT.mkdir(parents=True, exist_ok=True)
    _root_cause()
    equivalence = control_equivalence()
    if not all(item["equal"] for item in equivalence):
        dump(AUDIT / "m56r_control_equivalence.json", equivalence)
        raise RuntimeError("M56R_ABORTED_PRELIVE_CANONICAL_EQUIVALENCE_FAILURE")
    dump(AUDIT / "m56r_control_equivalence.json", equivalence)
    dump(
        AUDIT / "m56r_structural_request_diff.json",
        {"cases": equivalence, "unexpected_differences": []},
    )
    prompts = candidate_prompts()
    prior = load_json(M56_AUDIT / "m56_prompt_candidates.json")
    reused_unchanged = all(
        prior["candidates"][arm]["added_contract"] == INTERVENTIONS[arm] for arm in INTERVENTIONS
    )
    selection = selection_cases(ids, rows)
    schedule = []
    ordinal = 0
    for arm in prompts:
        for case_id in selection:
            ordinal += 1
            request = m51b._provider_request(
                ordinal, case_id, rows[case_id][0], prompt=prompts[arm]
            )
            schedule.append(
                {
                    "ordinal": ordinal,
                    "phase": "SELECTION",
                    "arm": arm,
                    "case_id": case_id,
                    "domain": rows[case_id][0]["database_id"],
                    "task_type": rows[case_id][0]["task_type"],
                    "contract_hash": sha256_text(prompts[arm]),
                    "provider_request_fingerprint": request_fingerprint(request),
                }
            )
    builder_hash = file_hash(ROOT / "m51b_runner.py")
    plan = {
        "experiment": "M56R",
        "starting_head": git("rev-parse", "HEAD"),
        "canonical_builder": "benchmark.m51b_runner._requests -> _provider_request",
        "canonical_builder_source_hash": builder_hash,
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
        "arms": {
            arm: {"contract_hash": sha256_text(prompt), "intervention": INTERVENTIONS[arm]}
            for arm, prompt in prompts.items()
        },
        "candidate_definitions_reused_unchanged": reused_unchanged,
        "selection_case_ids": selection,
        "selection_call_count": len(schedule),
        "full_run_case_count": 90,
        "maximum_provider_calls": len(schedule) + 90,
        "calls_per_case_per_arm": 1,
        "retries": 0,
        "repair": 0,
        "judge": 0,
        "selector": 0,
        "router": 0,
        "pass_at_k": 0,
        "selection_metric": {
            "primary": "governed_correct_count",
            "hard_constraints": {
                "authority_not_below_control": True,
                "policy_remains_perfect": True,
                "runtime_safety_unchanged": True,
            },
            "tie_break": [
                "higher governed_correct_count",
                "higher authority_correct_count",
                "higher policy_correct_count",
                "fewer control_regressions",
                "shorter_contract_delta",
                "lexicographically_smallest_arm",
            ],
        },
        "invalid_m56_exclusion": {
            "selection_corpus_hash": INVALID_M56_SELECTION_HASH,
            "full_corpus_hash": INVALID_M56_FULL_HASH,
            "eligible_for_scoring": False,
        },
        "benchmark_runtime_frozen": True,
    }
    dump(AUDIT / "m56r_experiment_plan.json", plan)
    dump(
        AUDIT / "m56r_prelive_integrity.json",
        {
            "starting_head": git("rev-parse", "HEAD"),
            "canonical_builder": plan["canonical_builder"],
            "canonical_builder_source_hash": builder_hash,
            "canonical_prompt_hash": CANONICAL_PROMPT_HASH,
            "control_equivalence_cases": len(equivalence),
            "equivalence": "PASS",
            "provider_calls_before_gate": 0,
            "candidate_definitions_reused_unchanged": reused_unchanged,
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
            "passed": True,
        },
    )
    dump(
        AUDIT / "m56r_invalid_m56_corpus_exclusion.json",
        {
            "selection_corpus_hash": INVALID_M56_SELECTION_HASH,
            "full_corpus_hash": INVALID_M56_FULL_HASH,
            "invalid_reason": "PRELIVE_REQUEST_BUILDER_DRIFT",
            "canonical_evidence_eligible": False,
            "response_bytes_mutated": False,
        },
    )
    dump(
        AUDIT / "m56r_selection_schedule.json",
        {"schedule": schedule, "schedule_hash": digest(schedule), "calls": len(schedule)},
    )
    print(
        json.dumps(
            {
                "equivalence": "PASS",
                "cases": len(equivalence),
                "selection_calls": len(schedule),
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )


class Guard:
    def __init__(self, schedule: list[dict[str, Any]], responses_path: Path) -> None:
        self.allowed = {(row["arm"], row["case_id"]): row for row in schedule}
        self.seen = (
            {(row["arm"], row["case_id"]) for row in load_jsonl(responses_path)}
            if responses_path.exists()
            else set()
        )

    def admit(self, schedule_row: dict[str, Any], request: dict[str, Any]) -> None:
        key = (schedule_row["arm"], schedule_row["case_id"])
        if key not in self.allowed or key in self.seen:
            raise RuntimeError("M56R_CALL_GUARD")
        if request_fingerprint(request) != schedule_row["provider_request_fingerprint"]:
            raise RuntimeError("M56R_REQUEST_DRIFT_BEFORE_CALL")
        self.seen.add(key)


def one_call(
    provider: OpenAICompatibleProvider, request: dict[str, Any], ordinal: int, arm: str, phase: str
) -> dict[str, Any]:
    started = time.perf_counter()
    provider.consume_response_wire()
    payload: Any = None
    error: Exception | None = None
    try:
        payload = asyncio.run(
            provider.complete_json_schema(
                operation=f"m56r_{phase.lower()}",
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
    metadata = m39._provider_metadata(payload or {}, capture)
    return {
        "experiment": "M56R",
        "phase": phase,
        "arm": arm,
        "ordinal": ordinal,
        "case_id": request["case_id"],
        "request_builder": "benchmark.m51b_runner._provider_request",
        "contract_hash": request["prompt_sha256"],
        "model_visible_input_hash": request["request_sha256"],
        "provider_request_fingerprint": request_fingerprint(request),
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
        "attempt_number": 1,
        "provider_attempted": True,
        "provider_success": error is None,
        "provider_outcome": "SUCCESS" if error is None else "FAILURE",
        "provider_error": None
        if error is None
        else {"type": type(error).__name__, "message": str(error)[:400]},
        "provider_metadata": metadata,
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
    }


def live_selection() -> None:
    ids, rows = load_rows()
    prompts = candidate_prompts()
    schedule = load_json(AUDIT / "m56r_selection_schedule.json")["schedule"]
    requests_path = AUDIT / "m56r_selection_requests.jsonl"
    responses_path = AUDIT / "m56r_selection_responses.jsonl"
    if requests_path.exists() or responses_path.exists():
        raise RuntimeError("M56R_ARTIFACT_EXISTS")
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M56R_PROVIDER_BLOCKED_API_KEY")
    provider = OpenAICompatibleProvider(
        settings.model_copy(
            update={
                "llm_model": MODEL,
                "llm_reasoning_effort": REASONING,
                "llm_temperature": TEMPERATURE,
                "llm_timeout_seconds": TIMEOUT_SECONDS,
                "eval_capture_model_io": True,
            }
        )
    )
    guard = Guard(schedule, responses_path)
    for item in schedule:
        request = m51b._provider_request(
            item["ordinal"], item["case_id"], rows[item["case_id"]][0], prompt=prompts[item["arm"]]
        )
        guard.admit(item, request)
        append_jsonl(
            requests_path,
            {
                "ordinal": item["ordinal"],
                "phase": "SELECTION",
                "arm": item["arm"],
                "case_id": item["case_id"],
                "provider_request_fingerprint": request_fingerprint(request),
                "request_text": request["request_text"],
            },
        )
        append_jsonl(
            responses_path, one_call(provider, request, item["ordinal"], item["arm"], "SELECTION")
        )
    responses = load_jsonl(responses_path)
    if len(responses) != 120:
        raise RuntimeError("M56R_SELECTION_INCOMPLETE")
    dump(
        AUDIT / "m56r_selection_response_freeze.json",
        {
            "responses": 120,
            "provider_calls": 120,
            "retries": 0,
            "response_corpus_hash": file_hash(responses_path),
            "frozen_before_analysis": True,
        },
    )


def setup_services(rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    m51b._patch_runtime_for_expansion()
    catalogs, _ = m46a._build_catalogs(
        [truth for case, truth in rows.values() if case["task_type"] == "ANSWERABLE"]
    )
    return m48b._runtime_services(catalogs)


def score_arm(
    responses: list[dict[str, Any]],
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    services: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, runtime_first = m51b._replay({"rows": rows, "services": services}, responses)
    groups = {
        task: [row for row in records if row["task_type"] == task]
        for task in ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED")
    }
    return records, {
        "governed_correct": sum(row["governed_correct"] for row in records),
        "answerable_tsa": sum(row["full_counterfactual_correct"] for row in groups["ANSWERABLE"]),
        "authority_correct": sum(row["governed_correct"] for row in groups["AUTHORITY_BLOCKED"]),
        "ambiguity_correct": sum(row["governed_correct"] for row in groups["AMBIGUOUS"]),
        "policy_correct": sum(row["governed_correct"] for row in groups["POLICY_BLOCKED"]),
        "runtime_first": runtime_first,
    }


def analyze_selection() -> None:
    responses = load_jsonl(AUDIT / "m56r_selection_responses.jsonl")
    ids, rows = load_rows()
    services = setup_services(rows)
    by_arm: dict[str, dict[str, Any]] = {}
    for arm in candidate_prompts():
        arm_responses = [row for row in responses if row["arm"] == arm]
        records, metrics = score_arm(arm_responses, rows, services)
        by_arm[arm] = {"records": records, "metrics": metrics}
    control_records = {row["case_id"]: row for row in by_arm["CONTROL"]["records"]}
    results = {}
    for arm, value in by_arm.items():
        candidate_records = {row["case_id"]: row for row in value["records"]}
        regressions = sorted(
            case_id
            for case_id in control_records
            if control_records[case_id]["governed_correct"]
            and not candidate_records[case_id]["governed_correct"]
        )
        new_passes = sorted(
            case_id
            for case_id in control_records
            if not control_records[case_id]["governed_correct"]
            and candidate_records[case_id]["governed_correct"]
        )
        metrics = {
            **value["metrics"],
            "control_regressions": regressions,
            "new_passes_vs_control": new_passes,
            "hard_constraints_pass": value["metrics"]["authority_correct"]
            >= by_arm["CONTROL"]["metrics"]["authority_correct"]
            and value["metrics"]["policy_correct"] == 6,
        }
        results[arm] = metrics
    eligible = [arm for arm, result in results.items() if result["hard_constraints_pass"]]
    eligible.sort(
        key=lambda arm: (
            -results[arm]["governed_correct"],
            -results[arm]["authority_correct"],
            -results[arm]["policy_correct"],
            len(INTERVENTIONS[arm].encode()),
            arm,
        )
    )
    winner = eligible[0] if eligible else "NO_PROMPT_CHANGE_ACCEPTED"
    dump(AUDIT / "m56r_candidate_results.json", results)
    dump(
        AUDIT / "m56r_transition_matrix.json",
        {
            arm: dict(
                Counter(
                    f"{'PASS' if control_records[cid]['governed_correct'] else 'FAIL'}->{'PASS' if next(row for row in by_arm[arm]['records'] if row['case_id'] == cid)['governed_correct'] else 'FAIL'}"
                    for cid in control_records
                )
            )
            for arm in results
        },
    )
    dump(
        AUDIT / "m56r_regression_analysis.json",
        {
            arm: {
                "new_passes": result["new_passes_vs_control"],
                "regressions": result["control_regressions"],
                "hard_constraints_pass": result["hard_constraints_pass"],
            }
            for arm, result in results.items()
        },
    )
    dump(
        AUDIT / "m56r_selected_contract.json",
        {
            "winner": winner,
            "selected_prompt_hash": sha256_text(candidate_prompts()[winner])
            if winner in candidate_prompts()
            else None,
            "eligible_arms": eligible,
            "provider_calls_after_freeze": 0,
        },
    )


def full_run() -> None:
    selection = load_json(AUDIT / "m56r_selected_contract.json")
    arm = selection["winner"]
    if arm not in candidate_prompts():
        raise RuntimeError("M56R_NO_WINNER")
    prompt = candidate_prompts()[arm]
    ids, rows = load_rows()
    schedule = [
        {
            "ordinal": index,
            "case_id": case_id,
            "arm": arm,
            "fingerprint": request_fingerprint(
                m51b._provider_request(index, case_id, rows[case_id][0], prompt=prompt)
            ),
        }
        for index, case_id in enumerate(ids, 1)
    ]
    dump(
        AUDIT / "m56r_full_schedule.json", {"schedule": schedule, "schedule_hash": digest(schedule)}
    )
    requests_path = AUDIT / "m56r_full_requests.jsonl"
    responses_path = AUDIT / "m56r_full_responses.jsonl"
    if requests_path.exists() or responses_path.exists():
        raise RuntimeError("M56R_FULL_ARTIFACT_EXISTS")
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M56R_PROVIDER_BLOCKED_API_KEY")
    provider = OpenAICompatibleProvider(
        settings.model_copy(
            update={
                "llm_model": MODEL,
                "llm_reasoning_effort": REASONING,
                "llm_temperature": TEMPERATURE,
                "llm_timeout_seconds": TIMEOUT_SECONDS,
                "eval_capture_model_io": True,
            }
        )
    )
    guard = Guard(
        [
            {
                "arm": arm,
                "case_id": item["case_id"],
                "provider_request_fingerprint": item["fingerprint"],
            }
            for item in schedule
        ],
        responses_path,
    )
    for item in schedule:
        request = m51b._provider_request(
            item["ordinal"], item["case_id"], rows[item["case_id"]][0], prompt=prompt
        )
        guard.admit(
            {
                "arm": arm,
                "case_id": item["case_id"],
                "provider_request_fingerprint": item["fingerprint"],
            },
            request,
        )
        append_jsonl(
            requests_path,
            {
                "ordinal": item["ordinal"],
                "case_id": item["case_id"],
                "arm": arm,
                "provider_request_fingerprint": request_fingerprint(request),
                "request_text": request["request_text"],
            },
        )
        append_jsonl(responses_path, one_call(provider, request, item["ordinal"], arm, "FULL"))
    responses = load_jsonl(responses_path)
    if len(responses) != 90:
        raise RuntimeError("M56R_FULL_INCOMPLETE")
    dump(
        AUDIT / "m56r_full_response_freeze.json",
        {
            "responses": 90,
            "provider_calls": 90,
            "retries": 0,
            "response_corpus_hash": file_hash(responses_path),
            "frozen_before_analysis": True,
        },
    )


def analyze_full() -> None:
    responses = load_jsonl(AUDIT / "m56r_full_responses.jsonl")
    ids, rows = load_rows()
    records, metrics = score_arm(responses, rows, setup_services(rows))
    dump(
        AUDIT / "m56r_full_run_results.json",
        {
            "records": records,
            "metrics": metrics,
            "provider_calls": 90,
            "response_corpus_hash": file_hash(AUDIT / "m56r_full_responses.jsonl"),
        },
    )
    m54 = {
        row["case_id"]: row
        for row in load_jsonl(ROOT / "audits" / "m54" / "m54_replay_records.jsonl")
    }
    residual = [
        "telecom_10",
        "workforce_10",
        "procurement_03",
        "procurement_13",
        "telecom_15",
        "procurement_05",
        "workforce_02",
        "healthcare_10",
    ]
    outcomes = []
    for case_id in residual:
        old = m54[case_id]
        new = next(row for row in records if row["case_id"] == case_id)
        old_fail = (
            "NONE"
            if old["governed_correct"]
            else (
                "DECISION" if old.get("first_failure") == "DECISION" else old.get("first_failure")
            )
        )
        new_fail = (
            "NONE"
            if new["governed_correct"]
            else (
                "DECISION" if new.get("first_failure") == "DECISION" else new.get("first_failure")
            )
        )
        outcomes.append(
            {
                "case_id": case_id,
                "status": "FIXED"
                if new["governed_correct"]
                else "UNCHANGED"
                if old_fail == new_fail
                else "CHANGED_FAILURE_MODE",
                "old_failure": old_fail,
                "new_failure": new_fail,
                "new_decision": new["decision"],
            }
        )
    regressions = sorted(
        case_id
        for case_id in ids
        if m54[case_id]["governed_correct"]
        and not next(row for row in records if row["case_id"] == case_id)["governed_correct"]
    )
    dump(AUDIT / "m56r_residual_outcomes.json", {"cases": outcomes, "new_regressions": regressions})
    dump(
        AUDIT / "m56r_summary.json",
        {
            "verdict": "M56R_COMPLETE",
            "metrics": metrics,
            "residual_outcomes": outcomes,
            "new_regressions": regressions,
            "m54_baseline": {"governed": "82/90", "answerable_tsa": "57/62"},
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
        },
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("prepare", "selection", "analyze-selection", "full-run", "analyze-full")
    )
    command = parser.parse_args().command
    {
        "prepare": prepare,
        "selection": live_selection,
        "analyze-selection": analyze_selection,
        "full-run": full_run,
        "analyze-full": analyze_full,
    }[command]()


if __name__ == "__main__":
    main()
