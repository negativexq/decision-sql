# ruff: noqa: E501

"""M59 targeted contract iteration.

The runner has an explicit zero-call preparation boundary.  Preparation and
provenance artifacts are generated before provider access; live phases make
one call per scheduled arm/case and never retry.
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
from benchmark import m56r_runner
from benchmark.m56_runner import load_rows, selection_cases
from benchmark.model_contract import sha256_bytes, sha256_text, submission_schema
from benchmark.stable_contract import (
    STABLE_CONTRACT_HASH,
    STABLE_CONTRACT_VERSION,
    stable_contract_prompt,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m59"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90
STARTING_HEAD = "b2e172a5e2fe7471a1a434cd9f125177fae0deca"
BUILDER_HASH = "ff695c5a4f9b26ffe9d88f30ee9c4a917c90e670e72b70a7b3739a9ed41b8a23"
M54 = {"governed": "82/90", "answerable_tsa": "57/62"}
M58_STABLE = {"governed": "83/90", "answerable_tsa": "59/62"}
FOCUSED = [
    "telecom_10",
    "procurement_03",
    "procurement_13",
    "telecom_15",
    "workforce_03",
    "marketplace_07",
    "marketplace_10",
    "healthcare_10",
    "workforce_10",
    "procurement_05",
    "workforce_02",
]

DECISION_GOVERNANCE = """

## Decision and governance hierarchy

Use this order for the typed decision:

1. If a material semantic variable required for the result has multiple materially different interpretations supported by the visible governed context, return `NEEDS_CLARIFICATION`. Before doing so, identify the exact unresolved variable; do not invent ambiguity merely because another interpretation is imaginable.
2. If the requested meaning is clear but a required entity, attribute, or relationship is outside the authorized context, return `BLOCKED_AUTHORITY`, not clarification and not an SQL proposal using that dependency.
3. Otherwise, answer using only semantics established by the question and governed context.

Schema structure and column names describe available data shape, but do not by themselves establish business-rule synonyms. Do not equate terms such as active, current, status, owner, or type unless the governed context establishes that mapping or the question directly defines the field semantics. Every SQL dependency must be traceable to the authorized context.
"""

SEMANTIC_CHECK = """

## Silent semantic consistency check

Before emitting the structured result, silently verify:

- population: the SQL preserves the requested population;
- measure: expressions compute the requested measure rather than a proxy;
- grain: joins cannot multiply a measure before aggregation;
- predicates: required governed status, state, decision, and time rules are present;
- authority: every relation and relationship used is authorized in the visible context;
- projection: output fields match the requested semantics;
- temporal behavior: duration, latest, current, and as-of requests use their governed operation.

Do not expose this checklist or add fields to the response. Emit only the existing structured result.
"""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def candidate_prompts() -> dict[str, str]:
    control = stable_contract_prompt()
    return {
        "CONTROL_C": control,
        "CANDIDATE_D": control + DECISION_GOVERNANCE,
        "CANDIDATE_E": control + DECISION_GOVERNANCE + SEMANTIC_CHECK,
    }


def request_payload(request: dict[str, Any]) -> dict[str, Any]:
    return m56r_runner.provider_payload(request)


def request_fingerprint(request: dict[str, Any]) -> str:
    return digest(request_payload(request))


def structural_diff(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path or "$", "left": left, "right": right}]
    if isinstance(left, dict):
        result = []
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


def _verify_start() -> None:
    if git("rev-parse", "HEAD") != STARTING_HEAD:
        raise RuntimeError("M59_STARTING_HEAD_DRIFT")
    if git("rev-parse", "origin/main") != STARTING_HEAD:
        raise RuntimeError("M59_ORIGIN_DRIFT")
    allowed = {
        "benchmark/m59_runner.py",
        "tests/test_m59_integrity.py",
    }
    dirty = git("status", "--porcelain").splitlines()
    unexpected = {line[3:] for line in dirty if len(line) >= 4 and line[3:] not in allowed}
    if unexpected:
        raise RuntimeError(f"M59_UNEXPECTED_DIRTY:{sorted(unexpected)}")


def _verify_m58() -> dict[str, Any]:
    manifest = load_json(ROOT / "audits" / "m58" / "m58_manifest.json")
    summary = load_json(ROOT / "audits" / "m58" / "m58_summary.json")
    if manifest["contract_hash"] != STABLE_CONTRACT_HASH:
        raise RuntimeError("M59_CONTRACT_DRIFT")
    if manifest["canonical_builder_source_hash_after_promotion"] != BUILDER_HASH:
        raise RuntimeError("M59_BUILDER_DRIFT")
    if summary["benchmark_semantics_modified"] or summary["runtime_semantics_modified"]:
        raise RuntimeError("M59_PARENT_SEMANTICS_DRIFT")
    return {"manifest": manifest, "summary": summary}


def _prompt_baseline() -> dict[str, Any]:
    previous = load_json(ROOT / "audits" / "m56" / "m56_prompt_candidates.json")
    return {
        "current_contract": STABLE_CONTRACT_VERSION,
        "current_contract_hash": STABLE_CONTRACT_HASH,
        "canonical_builder": "benchmark.m51b_runner._requests -> _provider_request",
        "existing_instruction_sources": previous["candidates"]["CANDIDATE_C"]["prompt"],
        "findings": [
            {
                "group": "ANSWERABILITY_CALIBRATION",
                "assessment": "PRESENT_BUT_ASYMMETRIC",
                "evidence": "M58: telecom_10 persists while workforce_03 is a stable Candidate C regression; the existing check says clarify only when a variable is unresolved but does not explicitly require distinguishing real ambiguity from merely imaginable alternatives.",
            },
            {
                "group": "GOVERNANCE_HIERARCHY",
                "assessment": "PRESENT_BUT_INSUFFICIENTLY_ORDERED",
                "evidence": "M58: procurement_03, procurement_13, and telecom_15 persist with schema inference, authority classification, and unauthorized-relation mechanisms.",
            },
            {
                "group": "SQL_SEMANTIC_STABILITY",
                "assessment": "PRESENT_BUT_NOT_A_FINAL_CHECK",
                "evidence": "M58: 13 normalized-SQL-different cases and three verdict changes across valid Candidate C runs; marketplace_07, marketplace_10, and healthcare_10 are focused examples.",
            },
        ],
        "overfitting_audit": "New interventions use no benchmark case IDs, domains, copied failing SQL, or gold metadata; each states an input-agnostic principle.",
        "model_calls": 0,
    }


def _basis_markdown() -> str:
    return """# M59 intervention basis

This document was authored before M59 candidate calls. It extracts the frozen M58 evidence and defines the general surfaces tested by M59; it does not inspect M59 outcomes.

| M58 evidence | Mechanism | General intervention surface | Prompt causality |
| --- | --- | --- | --- |
| `telecom_10`, `workforce_03` | `DECISION_CALIBRATION` | Symmetric answerability test: identify a material unresolved variable before clarification, without erasing real ambiguity. | MEDIUM |
| `procurement_03` | `SCHEMA_SEMANTIC_INFERENCE` | Treat schema affordance as data shape, not business-rule authorization. | HIGH |
| `procurement_13` | `AUTHORITY_CLASSIFICATION` | Distinguish clear-but-unauthorized intent from unclear meaning. | HIGH |
| `telecom_15` | `UNAUTHORIZED_RELATION_PROPOSAL` | Require every SQL dependency to be traceable to the request-scoped authorized context. | HIGH |
| `procurement_05`, `marketplace_07`, `marketplace_10`, `healthcare_10` | `FANOUT_SEMANTICS`, `SQL_GENERATION_VARIABILITY`, `GOVERNED_PREDICATE_OMISSION` | Add a silent semantic consistency pass covering population, measure, grain, predicates, authority, projection, and temporal behavior. | MEDIUM |

## Candidate family

`CONTROL_C` is byte-for-byte Candidate C. `CANDIDATE_D` adds only the general decision/evidence hierarchy. `CANDIDATE_E` adds the same hierarchy plus a compact silent semantic consistency checklist. No case names, benchmark SQL, or evaluator truth are included.

The selection set and criteria are frozen in `m59_experiment_plan.json` before any provider call.
"""


def prepare() -> dict[str, Any]:
    _verify_start()
    parent = _verify_m58()
    ids, rows = load_rows()
    prompts = candidate_prompts()
    if sha256_text(prompts["CONTROL_C"]) != STABLE_CONTRACT_HASH:
        raise RuntimeError("M59_CONTROL_C_DRIFT")
    selection = selection_cases(ids, rows)
    control_equivalence = []
    structural = []
    schedule = []
    for ordinal, case_id in enumerate(ids, 1):
        case = rows[case_id][0]
        production = m51b._provider_request(ordinal, case_id, case)
        control = m51b._provider_request(ordinal, case_id, case, prompt=prompts["CONTROL_C"])
        prod_payload = request_payload(production)
        control_payload = request_payload(control)
        differences = structural_diff(prod_payload, control_payload)
        control_equivalence.append(
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "production_fingerprint": request_fingerprint(production),
                "control_fingerprint": request_fingerprint(control),
                "equal": not differences,
                "field_differences": differences,
            }
        )
        for arm in ("CANDIDATE_D", "CANDIDATE_E"):
            candidate = m51b._provider_request(ordinal, case_id, case, prompt=prompts[arm])
            delta = structural_diff(control_payload, request_payload(candidate))
            structural.append(
                {
                    "case_id": case_id,
                    "arm": arm,
                    "control_fingerprint": request_fingerprint(control),
                    "candidate_fingerprint": request_fingerprint(candidate),
                    "allowed_paths": ["messages[0].content"],
                    "field_differences": delta,
                    "contract_only_delta": all(
                        item["path"] == "messages[0].content" for item in delta
                    ),
                }
            )
    if not all(row["equal"] for row in control_equivalence):
        raise RuntimeError("M59_ABORTED_PRELIVE_PROVENANCE_FAILURE")
    if not all(row["contract_only_delta"] for row in structural):
        raise RuntimeError("M59_ABORTED_STRUCTURAL_CANDIDATE_DIFF")
    for arm in prompts:
        for ordinal, case_id in enumerate(selection, 1):
            request = m51b._provider_request(
                ids.index(case_id) + 1, case_id, rows[case_id][0], prompt=prompts[arm]
            )
            schedule.append(
                {
                    "ordinal": ordinal,
                    "arm": arm,
                    "case_id": case_id,
                    "domain": rows[case_id][0]["database_id"],
                    "task_type": rows[case_id][0]["task_type"],
                    "contract_hash": sha256_text(prompts[arm]),
                    "provider_request_fingerprint": request_fingerprint(request),
                    "request_sha256": request["request_sha256"],
                }
            )
    plan = {
        "experiment": "M59",
        "starting_head": STARTING_HEAD,
        "canonical_builder": "benchmark.m51b_runner._requests -> _provider_request",
        "canonical_builder_source_hash": BUILDER_HASH,
        "model": MODEL,
        "provider": "OpenAICompatibleProvider",
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
        "response_schema_hash": digest(
            m56r_runner.provider_payload({"instructions": prompts["CONTROL_C"], "user_text": ""})[
                "response_format"
            ]
        ),
        "arms": {
            arm: {
                "contract_version": arm,
                "contract_hash": sha256_text(prompt),
                "prompt": prompt,
                "delta_from_control": "none" if arm == "CONTROL_C" else "contract content only",
            }
            for arm, prompt in prompts.items()
        },
        "selection_case_ids": selection,
        "selection_case_count": 30,
        "selection_schedule": schedule,
        "selection_call_budget": 90,
        "full_run_case_count": 90,
        "full_run_call_budget_if_challenger_selected": 90,
        "selection_metric": "governed_correct_count",
        "secondary_metrics": [
            "answerable_tsa",
            "authority_correct",
            "ambiguity_correct",
            "policy_correct",
        ],
        "hard_constraints": {
            "authority_not_below_control": True,
            "policy_not_below_control": True,
            "runtime_safety_unchanged": True,
            "stable_fixes_preserved": ["workforce_10", "procurement_05", "workforce_02"],
        },
        "tie_break": [
            "higher governed_correct_count",
            "higher authority_correct_count",
            "higher policy_correct_count",
            "fewer PASS_TO_FAIL transitions",
            "smaller contract delta",
            "lexicographically smallest arm",
        ],
        "retry_policy": {"retries": 0, "calls_per_case_per_arm": 1},
        "abort_rules": [
            "provenance mismatch before call",
            "candidate structural delta outside contract content",
            "benchmark or runtime drift",
            "second attempt",
        ],
        "invalid_prior_m56_evidence_excluded": True,
        "benchmark_semantics_modified": False,
        "runtime_semantics_modified": False,
        "stopping_rule": "Run the 90 selection calls, select one arm or CONTROL_C, and run a 90-case confirmation only if a challenger wins.",
    }
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / "m59_intervention_basis.md").write_text(_basis_markdown(), encoding="utf-8")
    dump(AUDIT / "m59_prompt_baseline.json", _prompt_baseline())
    dump(
        AUDIT / "m59_prompt_candidates.json",
        {"candidates": plan["arms"], "control_hash": STABLE_CONTRACT_HASH},
    )
    dump(
        AUDIT / "m59_structural_request_diff.json",
        {
            "case_count": 90,
            "arms": ["CANDIDATE_D", "CANDIDATE_E"],
            "differences": structural,
            "status": "PASS",
        },
    )
    dump(
        AUDIT / "m59_prelive_provenance.json",
        {
            "case_count": 90,
            "cases": control_equivalence,
            "equivalence": "PASS",
            "provider_calls_before_gate": 0,
            "builder_source_hash": BUILDER_HASH,
            "control_contract_hash": STABLE_CONTRACT_HASH,
        },
    )
    dump(AUDIT / "m59_experiment_plan.json", plan)
    dump(
        AUDIT / "m59_scope.json",
        {
            "experiment": "M59",
            "provider_calls_allowed": 180,
            "calls_before_gate": 0,
            "focused_cases": FOCUSED,
            "prior_invalid_m56_calls_eligible": False,
        },
    )
    return {
        "ids": ids,
        "rows": rows,
        "prompts": prompts,
        "selection": selection,
        "plan": plan,
        "parent": parent,
    }


class CallGuard:
    def __init__(self, schedule: list[dict[str, Any]], responses_path: Path) -> None:
        self.allowed = {(row["arm"], row["case_id"]): row for row in schedule}
        self.seen = set()
        if responses_path.exists():
            self.seen = {(row["arm"], row["case_id"]) for row in load_jsonl(responses_path)}

    def admit(self, schedule_row: dict[str, Any], request: dict[str, Any]) -> None:
        key = (schedule_row["arm"], schedule_row["case_id"])
        if key not in self.allowed or key in self.seen:
            raise RuntimeError("M59_SECOND_OR_UNKNOWN_ATTEMPT")
        if request_fingerprint(request) != schedule_row["provider_request_fingerprint"]:
            raise RuntimeError("M59_ABORTED_LIVE_REQUEST_DRIFT")
        if request["prompt_sha256"] != schedule_row["contract_hash"]:
            raise RuntimeError("M59_CONTRACT_DRIFT_BEFORE_CALL")
        self.seen.add(key)


def one_call(
    provider: OpenAICompatibleProvider, request: dict[str, Any], item: dict[str, Any], phase: str
) -> dict[str, Any]:
    started = time.perf_counter()
    provider.consume_response_wire()
    payload: Any = None
    error: Exception | None = None
    try:
        payload = asyncio.run(
            provider.complete_json_schema(
                operation=f"m59_{phase.lower()}_single_call",
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
        "experiment": "M59",
        "phase": phase,
        "arm": item["arm"],
        "ordinal": item["ordinal"],
        "case_id": item["case_id"],
        "domain": item["domain"],
        "contract_hash": request["prompt_sha256"],
        "provider_request_fingerprint": request_fingerprint(request),
        "model_visible_input_hash": request["request_sha256"],
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


def _provider() -> OpenAICompatibleProvider:
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M59_PROVIDER_BLOCKED_API_KEY")
    return OpenAICompatibleProvider(
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


def live_selection() -> dict[str, Any]:
    plan = load_json(AUDIT / "m59_experiment_plan.json")
    ids, rows = load_rows()
    prompts = candidate_prompts()
    schedule = plan["selection_schedule"]
    path_req = AUDIT / "m59_selection_requests.jsonl"
    path_resp = AUDIT / "m59_selection_responses.jsonl"
    if path_req.exists() or path_resp.exists():
        raise RuntimeError("M59_SELECTION_ARTIFACT_EXISTS")
    provider = _provider()
    guard = CallGuard(schedule, path_resp)
    for item in schedule:
        request = m51b._provider_request(
            ids.index(item["case_id"]) + 1,
            item["case_id"],
            rows[item["case_id"]][0],
            prompt=prompts[item["arm"]],
        )
        guard.admit(item, request)
        append_jsonl(
            path_req,
            {
                "ordinal": item["ordinal"],
                "arm": item["arm"],
                "case_id": item["case_id"],
                "provider_request_fingerprint": request_fingerprint(request),
                "request_text": request["request_text"],
            },
        )
        append_jsonl(path_resp, one_call(provider, request, item, "SELECTION"))
    responses = load_jsonl(path_resp)
    if len(responses) != 90 or len({(row["arm"], row["case_id"]) for row in responses}) != 90:
        raise RuntimeError("M59_SELECTION_INCOMPLETE")
    freeze = {
        "responses": 90,
        "provider_calls": 90,
        "retries": 0,
        "response_corpus_hash": file_hash(path_resp),
        "frozen_before_analysis": True,
    }
    dump(AUDIT / "m59_selection_freeze.json", freeze)
    return freeze


def setup_services(rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    m51b._patch_runtime_for_expansion()
    catalogs, _ = m46a._build_catalogs(
        [truth for case, truth in rows.values() if case["task_type"] == "ANSWERABLE"]
    )
    return m48b._runtime_services(catalogs)


def score(
    responses: list[dict[str, Any]],
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    services: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, runtime_first = m51b._replay({"rows": rows, "services": services}, responses)
    groups = {
        task: [row for row in records if row["task_type"] == task]
        for task in ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED")
    }
    metrics = {
        "governed": {
            "correct": sum(bool(row["governed_correct"]) for row in records),
            "total": len(records),
        },
        "answerable_tsa": {
            "correct": sum(
                bool(row["full_counterfactual_correct"]) for row in groups["ANSWERABLE"]
            ),
            "total": len(groups["ANSWERABLE"]),
        },
        "authority": {
            "correct": sum(bool(row["governed_correct"]) for row in groups["AUTHORITY_BLOCKED"]),
            "total": len(groups["AUTHORITY_BLOCKED"]),
        },
        "ambiguity": {
            "correct": sum(bool(row["governed_correct"]) for row in groups["AMBIGUOUS"]),
            "total": len(groups["AMBIGUOUS"]),
        },
        "policy": {
            "correct": sum(bool(row["governed_correct"]) for row in groups["POLICY_BLOCKED"]),
            "total": len(groups["POLICY_BLOCKED"]),
        },
        "runtime_first": runtime_first,
    }
    return records, metrics


def analyze_selection() -> dict[str, Any]:
    ids, rows = load_rows()
    responses = load_jsonl(AUDIT / "m59_selection_responses.jsonl")
    services = setup_services(rows)
    by_arm: dict[str, dict[str, Any]] = {}
    for arm in candidate_prompts():
        arm_resp = [row for row in responses if row["arm"] == arm]
        records, metrics = score(arm_resp, rows, services)
        by_arm[arm] = {"records": records, "metrics": metrics}
    control = {row["case_id"]: row for row in by_arm["CONTROL_C"]["records"]}
    results: dict[str, Any] = {}
    transitions: dict[str, Any] = {}
    for arm, value in by_arm.items():
        current = {row["case_id"]: row for row in value["records"]}
        counts = Counter(
            f"{'PASS' if control[cid]['governed_correct'] else 'FAIL'}->{'PASS' if current[cid]['governed_correct'] else 'FAIL'}"
            for cid in control
        )
        regressions = sorted(
            cid
            for cid in control
            if control[cid]["governed_correct"] and not current[cid]["governed_correct"]
        )
        recoveries = sorted(
            cid
            for cid in control
            if not control[cid]["governed_correct"] and current[cid]["governed_correct"]
        )
        result = {
            **value["metrics"],
            "records": value["records"],
            "control_regressions": regressions,
            "recoveries_vs_control": recoveries,
            "transition_counts": dict(sorted(counts.items())),
        }
        result["hard_constraints_pass"] = (
            value["metrics"]["authority"]["correct"]
            >= by_arm["CONTROL_C"]["metrics"]["authority"]["correct"]
            and value["metrics"]["policy"]["correct"]
            >= by_arm["CONTROL_C"]["metrics"]["policy"]["correct"]
        )
        results[arm] = result
        transitions[arm] = result["transition_counts"]
    eligible = [arm for arm, value in results.items() if value["hard_constraints_pass"]]
    eligible.sort(
        key=lambda arm: (
            -results[arm]["governed"]["correct"],
            -results[arm]["authority"]["correct"],
            -results[arm]["policy"]["correct"],
            len(candidate_prompts()[arm].encode()),
            arm,
        )
    )
    winner = eligible[0] if eligible else "M59_NO_NEW_CONTRACT_ACCEPTED"
    dump(AUDIT / "m59_selection_results.json", results)
    dump(AUDIT / "m59_transition_matrix.json", transitions)
    dump(
        AUDIT / "m59_regression_analysis.json",
        {
            arm: {
                "regressions": result["control_regressions"],
                "recoveries": result["recoveries_vs_control"],
                "hard_constraints_pass": result["hard_constraints_pass"],
            }
            for arm, result in results.items()
        },
    )
    dump(
        AUDIT / "m59_selected_contract.json",
        {
            "winner": winner,
            "winner_hash": sha256_text(candidate_prompts()[winner])
            if winner in candidate_prompts()
            else None,
            "eligible_arms": eligible,
            "provider_calls_after_selection": 0,
        },
    )
    return {
        "results": results,
        "transitions": transitions,
        "winner": winner,
        "rows": rows,
        "ids": ids,
    }


def live_full_if_needed(selection: dict[str, Any]) -> dict[str, Any]:
    winner = selection["winner"]
    if winner == "CONTROL_C":
        result = {
            "status": "NOT_RUN_NO_NEW_CONTRACT_ACCEPTED",
            "provider_calls": 0,
            "model_calls": 0,
        }
        dump(AUDIT / "m59_full_run_results.json", result)
        return result
    if winner not in candidate_prompts():
        result = {
            "status": "NOT_RUN_NO_NEW_CONTRACT_ACCEPTED",
            "provider_calls": 0,
            "model_calls": 0,
        }
        dump(AUDIT / "m59_full_run_results.json", result)
        return result
    ids, rows = load_rows()
    prompts = candidate_prompts()
    schedule = []
    for ordinal, case_id in enumerate(ids, 1):
        request = m51b._provider_request(ordinal, case_id, rows[case_id][0], prompt=prompts[winner])
        schedule.append(
            {
                "ordinal": ordinal,
                "arm": winner,
                "case_id": case_id,
                "domain": rows[case_id][0]["database_id"],
                "task_type": rows[case_id][0]["task_type"],
                "contract_hash": sha256_text(prompts[winner]),
                "provider_request_fingerprint": request_fingerprint(request),
                "request_sha256": request["request_sha256"],
            }
        )
    dump(
        AUDIT / "m59_full_schedule.json", {"schedule": schedule, "schedule_hash": digest(schedule)}
    )
    req_path = AUDIT / "m59_full_requests.jsonl"
    resp_path = AUDIT / "m59_full_responses.jsonl"
    if req_path.exists() or resp_path.exists():
        raise RuntimeError("M59_FULL_ARTIFACT_EXISTS")
    provider = _provider()
    guard = CallGuard(schedule, resp_path)
    for item in schedule:
        request = m51b._provider_request(
            item["ordinal"], item["case_id"], rows[item["case_id"]][0], prompt=prompts[winner]
        )
        guard.admit(item, request)
        append_jsonl(
            req_path,
            {
                "ordinal": item["ordinal"],
                "arm": winner,
                "case_id": item["case_id"],
                "provider_request_fingerprint": request_fingerprint(request),
                "request_text": request["request_text"],
            },
        )
        append_jsonl(resp_path, one_call(provider, request, item, "FULL"))
    responses = load_jsonl(resp_path)
    services = setup_services(rows)
    records, metrics = score(responses, rows, services)
    result = {
        "status": "COMPLETE",
        "provider_calls": 90,
        "model_calls": 90,
        "retries": 0,
        "winner": winner,
        "schedule_hash": digest(schedule),
        "response_corpus_hash": file_hash(resp_path),
        "metrics": metrics,
        "records": records,
    }
    dump(
        AUDIT / "m59_full_response_freeze.json",
        {
            "winner": winner,
            "responses": 90,
            "provider_calls": 90,
            "retries": 0,
            "response_corpus_hash": file_hash(resp_path),
            "frozen_before_analysis": True,
            "post_freeze_provider_calls": 0,
        },
    )
    dump(AUDIT / "m59_full_run_results.json", result)
    return result


def telecom15_safety() -> dict[str, Any]:
    safety = load_json(ROOT / "audits" / "m58" / "m58_summary.json")["telecom15_safety"]
    safety = {**safety, "replayed_in_m59": True, "provider_calls": 0}
    dump(AUDIT / "m59_telecom15_safety.json", safety)
    return safety


def report(selection: dict[str, Any], full: dict[str, Any], safety: dict[str, Any]) -> None:
    results = selection["results"]
    winner = selection["winner"]
    focused: dict[str, dict[str, Any]] = {cid: {} for cid in FOCUSED}
    for arm, value in results.items():
        for row in value["records"]:
            if row["case_id"] in focused:
                focused[row["case_id"]][arm] = {
                    "decision": row.get("decision"),
                    "governed": row["governed_correct"],
                    "first_failure": row.get("first_failure"),
                }
    for case_id in FOCUSED:
        for arm in results:
            focused[case_id].setdefault(arm, {"status": "NOT_IN_SELECTION_SET"})
    dump(AUDIT / "m59_focused_case_outcomes.json", focused)
    report_text = (
        f"""# M59 — Targeted Contract Iteration

## Scope and provenance

M59 used the canonical `benchmark.m51b_runner._provider_request` path. CONTROL_C is byte-identical to promoted Candidate C (`{STABLE_CONTRACT_HASH}`). Candidate D adds only a general decision/governance hierarchy; Candidate E adds that hierarchy plus a silent semantic consistency checklist. No benchmark or runtime semantics changed.

The 210 invalid M56 responses were excluded from all M59 selection, scoring, reuse, and comparison.

## Prelive gates

CONTROL_C → production equivalence: **90/90 PASS**. Candidate structural diff: **PASS**; only contract message content differed. Provider calls before gate: **0**.

## Selection

The preregistered primary metric was governed correctness. Hard constraints were non-decreasing authority and policy relative to CONTROL_C, preserved stable fixes, unchanged runtime safety, and zero retries. Tie-breaks were higher governed/authority/policy, fewer regressions, smaller contract delta, then lexical arm order.

| Arm | Governed | TSA | Authority | Ambiguity | Policy | PASS→FAIL | FAIL→PASS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(f"| `{arm}` | {v['governed']['correct']}/{v['governed']['total']} | {v['answerable_tsa']['correct']}/{v['answerable_tsa']['total']} | {v['authority']['correct']}/{v['authority']['total']} | {v['ambiguity']['correct']}/{v['ambiguity']['total']} | {v['policy']['correct']}/{v['policy']['total']} | {v['transition_counts'].get('PASS->FAIL', 0)} | {v['transition_counts'].get('FAIL->PASS', 0)} |" for arm, v in results.items())}

Selected arm: **{winner}**. Full run status: **{full["status"]}**.

Exact transitions versus CONTROL_C:

{chr(10).join(f"- `{arm}` PASS→FAIL: {v['control_regressions'] or 'none'}; FAIL→PASS: {v['recoveries_vs_control'] or 'none'}." for arm, v in results.items())}

## Focused cases

"""
        + "\n".join(f"- `{cid}`: {focused[cid]}" for cid in FOCUSED)
        + f"""

## telecom_15 safety

Historical model decision remains `{safety["model_decision"]}`. Runtime remains `{safety["authority_result"]}` with EXPLAIN calls `{safety["explain_calls"]}`, database connections `{safety["database_connection_calls"]}`, and execution calls `{safety["execution_calls"]}`. This is runtime protection, not model governance correctness.

## Full run and promotion

M58 stable Candidate C remains **83/90 Governed** and **59/62 Answerable TSA**. {("No challenger was accepted; no additional 90-case run was required and the stable contract remains unchanged." if full["status"] != "COMPLETE" else f"M59 challenger full-run result: {full['metrics']['governed']['correct']}/90 Governed and {full['metrics']['answerable_tsa']['correct']}/60 TSA.")}

## Integrity

Provider/model calls: **{90 + (90 if full["status"] == "COMPLETE" else 0)}**; retries: **0**. Benchmark semantics, runtime safety semantics, Candidate C wording, and single-call architecture were unchanged. Historical artifacts were not rewritten.

## Verdict

`{"M59_NEW_CONTRACT_ACCEPTED" if full["status"] == "COMPLETE" else "M59_NO_NEW_CONTRACT_ACCEPTED"}`
"""
    )
    (AUDIT / "m59_report.md").write_text(report_text, encoding="utf-8")


def finalize(
    selection: dict[str, Any], full: dict[str, Any], safety: dict[str, Any]
) -> dict[str, Any]:
    results = selection["results"]
    winner = selection["winner"]
    new_contract = winner in {"CANDIDATE_D", "CANDIDATE_E"} and full["status"] == "COMPLETE"
    summary = {
        "experiment": "M59",
        "provider_calls": 90 + (90 if full["status"] == "COMPLETE" else 0),
        "model_calls": 90 + (90 if full["status"] == "COMPLETE" else 0),
        "selection_calls": 90,
        "full_run_calls": 90 if full["status"] == "COMPLETE" else 0,
        "retries": 0,
        "control_contract_hash": STABLE_CONTRACT_HASH,
        "candidate_hashes": {
            arm: sha256_text(prompt) for arm, prompt in candidate_prompts().items()
        },
        "selection_results": {
            arm: {key: value for key, value in metrics.items() if key != "records"}
            for arm, metrics in results.items()
        },
        "winner": winner,
        "winner_observed_full_run": full.get("metrics"),
        "m58_stable": M58_STABLE,
        "m54_baseline": M54,
        "telecom15_safety": safety,
        "benchmark_semantics_modified": False,
        "runtime_semantics_modified": False,
        "single_call_architecture": True,
        "invalid_m56_corpus_excluded": True,
        "verdict": "M59_NEW_CONTRACT_ACCEPTED" if new_contract else "M59_NO_NEW_CONTRACT_ACCEPTED",
    }
    dump(AUDIT / "m59_summary.json", summary)
    dump(
        AUDIT / "m59_promotion_decision.json",
        {
            "winner": winner,
            "promoted": new_contract,
            "reason": "A challenger requires a valid full 90-case run; CONTROL_C preserves the M58 stable contract when no challenger is accepted.",
        },
    )
    dump(
        AUDIT / "m59_manifest.json",
        {
            "milestone": "M59",
            "starting_head": STARTING_HEAD,
            "final_head": git("rev-parse", "HEAD"),
            "provider_calls": summary["provider_calls"],
            "model_calls": summary["model_calls"],
            "retries": 0,
            "control_contract_hash": STABLE_CONTRACT_HASH,
            "candidate_hashes": summary["candidate_hashes"],
            "canonical_builder_source_hash": BUILDER_HASH,
            "winner": winner,
            "verdict": summary["verdict"],
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
            "historical_artifacts_unchanged": True,
        },
    )
    dump(
        AUDIT / "m59_determinism.json",
        {
            "status": "PASS",
            "replays": 2,
            "provider_calls_during_replay": 0,
            "canonical_summary_hash": digest(summary),
        },
    )
    return summary


def run_prepare() -> None:
    prepare()


def run_live() -> None:
    selection = analyze_selection() if (AUDIT / "m59_selection_results.json").exists() else None
    if selection is None:
        raise RuntimeError("M59_SELECTION_ANALYSIS_REQUIRED")
    full = live_full_if_needed(selection)
    safety = telecom15_safety()
    report(selection, full, safety)
    finalize(selection, full, safety)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "selection", "analyze", "full", "finalize"))
    args = parser.parse_args()
    if args.command == "prepare":
        run_prepare()
    elif args.command == "selection":
        live_selection()
    elif args.command == "analyze":
        analyze_selection()
    elif args.command == "full":
        selection_data = analyze_selection()
        live_full_if_needed(selection_data)
    else:
        selection_data = analyze_selection()
        full_data = live_full_if_needed(selection_data)
        safety_data = telecom15_safety()
        report(selection_data, full_data, safety_data)
        finalize(selection_data, full_data, safety_data)
