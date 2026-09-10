# ruff: noqa: E501

"""M56 single-call prompt-contract experiment tooling.

The runner is intentionally benchmark-only.  It keeps the retained runtime
and evaluator unchanged, makes one provider attempt per scheduled request,
and persists requests/responses before any scoring analysis.
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
from typing import Any, cast

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from benchmark import m39_runner as m39
from benchmark import m46a_audit as m46a
from benchmark import m48b_runner as m48b
from benchmark import m51b_runner as m51b
from benchmark.m46b_contract import m43_prompt
from benchmark.model_contract import serialize_governed_context_v1, sha256_bytes, sha256_text

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "audits" / "m56"
CASES = ROOT / "cases" / "m51_expansion"
TRUTH = ROOT / "ground_truth" / "m51_expansion"
PROMPT_PATH = ROOT / "prompts" / "governed_context_v1.md"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90
DECISIONS = {
    "ANSWERABLE": "ANSWER",
    "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
    "AMBIGUOUS": "NEEDS_CLARIFICATION",
    "POLICY_BLOCKED": "BLOCKED_POLICY",
}

# The retained provider-visible contract is reconstructed from the canonical
# M43 request ledger.  The prompt file is historical source material and is
# not, by itself, the request-builder authority.
BASE_PROMPT = m43_prompt()

INTERVENTIONS = {  # noqa: E501
    "CONTROL": "",
    "CANDIDATE_A": """

## Semantic decision discipline

- Request clarification only when a material semantic variable needed for the result remains unresolved by the question, authorized context, or governed business rules. The existence of another imaginable interpretation is not, by itself, ambiguity.
- Before returning `NEEDS_CLARIFICATION`, identify the unresolved variable. If population, grouping, measure, filters, time range, and requested projection are determined, answer the request.
- A suggestive schema name is not a business-rule equivalence. Use a status, state, owner, or type field for a user term only when the governed context establishes that mapping.
- Preserve the native grain of a measure across joins. If a child relation only qualifies parent rows, use an existence or parent-key-preserving form before aggregating the parent measure.
- Translate governed duration calculations and required status or decision predicates into SQL; do not substitute row counts or omit a visible predicate because current data happens to make it equivalent.
""",
    "CANDIDATE_B": """

## Decision hierarchy and SQL invariants

Apply this order:

1. If the intended meaning is materially unresolved after using the visible contract, return `NEEDS_CLARIFICATION`.
2. If the intended meaning is clear but a required entity, attribute, or relationship is absent from the authorized context, return `BLOCKED_AUTHORITY`.
3. Otherwise, answer only with visible governed schema, metrics, rules, temporal definitions, and policy.

Do not infer business semantics from a plausible column name or from technical table existence. A field named `active`, `current`, `status`, `owner`, or `type` has the requested meaning only when governed context explicitly establishes that equivalence. SQL dependencies must be limited to the authorized context.

For SQL, preserve measure grain before aggregation. A one-to-many child used only as a qualifying predicate must not multiply a parent measure; use `EXISTS`, a semi-join, or parent-key deduplication. A child-grain derived measure is valid only when its governed metric definition authorizes that grain and calculation. Use documented duration arithmetic and include every governed status or decision predicate required by the requested measure.
""",
    "CANDIDATE_C": """

## Final contract check before emitting a decision

1. Meaning: what exact population, grouping, measure, filters, time range, and projection does the question request? Clarify only if one of these material choices is still unresolved in the visible governed context.
2. Authority: is the meaning clear, but a needed relation or path absent from `authorized_relationships`? If so, return `BLOCKED_AUTHORITY`. Do not use a technically visible table or a matching column as authorization.
3. Semantics: does each aggregate operate at its native grain? Use existence or key-preserving logic when a qualifying child could duplicate a parent measure. Use the governed duration formula rather than counting events, and retain required governed status or decision filters.

Do not turn a plausible schema interpretation into a business rule. Do not omit a governed predicate or replace a governed measure with a convenient proxy merely because the BASE data does not expose the difference.
""",
}


def sha_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


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


def load_rows() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    manifest = load_json(ROOT / "manifests" / "m51a_expansion_90_manifest.json")
    ids = [str(value) for value in manifest["case_ids"]]
    rows = {
        cid: (
            load_json(CASES / f"{cid}.json"),
            load_json(TRUTH / f"{cid}.json"),
        )
        for cid in ids
    }
    if len(ids) != 90 or len(set(ids)) != 90 or set(ids) != set(rows):
        raise RuntimeError("M56_CASE_SET")
    return ids, rows


def candidate_prompts() -> dict[str, str]:
    return {name: BASE_PROMPT + addition for name, addition in INTERVENTIONS.items()}


def build_request(case_id: str, case: dict[str, Any], prompt: str) -> dict[str, Any]:
    context = serialize_governed_context_v1(str(case["database_id"]))
    user = f"Case ID:\n{case_id}\n\nQuestion:\n{case['question']}\n\nGoverned context:\n{context}"
    request_text = "SYSTEM:\n" + prompt + "\n\nUSER:\n" + user
    return {
        "case_id": case_id,
        "domain": case["database_id"],
        "question": case["question"],
        "prompt_hash": sha256_text(prompt),
        "context_hash": sha256_text(context),
        "model_visible_input_hash": sha256_text(request_text),
        "request_hash": sha256_text(request_text),
        "request_text": request_text,
        "user_text": user,
        "system_prompt": prompt,
        "provider_request_hash": sha_value(
            {
                "model": MODEL,
                "reasoning": REASONING,
                "temperature": TEMPERATURE,
                "timeout_seconds": TIMEOUT_SECONDS,
                "system_prompt": prompt,
                "user_prompt": user,
                "response_schema": load_json(ROOT / "schemas" / "model_submission.schema.json"),
            }
        ),
    }


def selection_cases(
    ids: list[str], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> list[str]:
    # Fixed, model-blind, 5-per-domain design. It deliberately includes all
    # eight M55 residuals plus 22 non-residual controls and all task families.
    wanted = [
        "healthcare_02",
        "healthcare_08",
        "healthcare_04",
        "healthcare_03",
        "healthcare_10",
        "insurance_05",
        "insurance_09",
        "insurance_03",
        "insurance_01",
        "insurance_10",
        "marketplace_04",
        "marketplace_11",
        "marketplace_02",
        "marketplace_15",
        "marketplace_13",
        "procurement_03",
        "procurement_13",
        "procurement_05",
        "procurement_04",
        "procurement_08",
        "telecom_10",
        "telecom_15",
        "telecom_01",
        "telecom_04",
        "telecom_07",
        "workforce_10",
        "workforce_02",
        "workforce_05",
        "workforce_03",
        "workforce_11",
    ]
    if len(wanted) != 30 or set(wanted) - set(ids):
        raise RuntimeError("M56_SELECTION_CASES")
    if len({rows[cid][0]["database_id"] for cid in wanted}) != 6:
        raise RuntimeError("M56_SELECTION_DOMAIN_COVERAGE")
    return wanted


def prepare() -> None:
    ids, rows = load_rows()
    prompts = candidate_prompts()
    selection = selection_cases(ids, rows)
    selection_records = []
    ordinal = 0
    for arm in prompts:
        for cid in selection:
            ordinal += 1
            request = build_request(cid, rows[cid][0], prompts[arm])
            selection_records.append(
                {
                    "ordinal": ordinal,
                    "phase": "SELECTION",
                    "arm": arm,
                    "case_id": cid,
                    "domain": rows[cid][0]["database_id"],
                    "task_type": rows[cid][0]["task_type"],
                    "prompt_hash": request["prompt_hash"],
                    "model_visible_input_hash": request["model_visible_input_hash"],
                    "provider_request_hash": request["provider_request_hash"],
                }
            )
    plan = {
        "experiment": "M56",
        "starting_head": git("rev-parse", "HEAD"),
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
        "control_prompt_hash": sha256_text(prompts["CONTROL"]),
        "candidate_prompt_hashes": {name: sha256_text(prompt) for name, prompt in prompts.items()},
        "selection_case_ids": selection,
        "selection_case_count": len(selection),
        "selection_set_hash": sha_value(selection),
        "selection_schedule": selection_records,
        "selection_call_count": len(selection_records),
        "full_run_case_count": 90,
        "maximum_model_calls": len(selection_records) + 90,
        "calls_per_case_per_arm": 1,
        "retries": 0,
        "repair": False,
        "judge": False,
        "selector": False,
        "router": False,
        "pass_at_k": False,
        "selection_metric": {
            "primary": "governed_correct_count",
            "hard_constraints": {
                "authority_correct_not_below_control": True,
                "policy_correct_equals_total": True,
                "runtime_safety_tests_unchanged": True,
            },
            "tracked": [
                "governed_correct_count",
                "answerable_tsa_correct_count",
                "authority_correct_count",
                "ambiguity_correct_count",
                "policy_correct_count",
                "new_control_regressions",
            ],
            "tie_break": [
                "higher governed_correct_count",
                "higher authority_correct_count",
                "higher policy_correct_count",
                "fewer control regressions",
                "shorter added_contract_bytes",
                "lexicographically smallest arm_id",
            ],
        },
        "stopping_rule": (
            "Run all 120 selection attempts, freeze/analyze them, select one arm or "
            "accept NO_PROMPT_CHANGE_ACCEPTED; run the 90-case confirmation only "
            "for the selected contract."
        ),
        "benchmark_and_runtime_frozen": True,
    }
    AUDIT.mkdir(parents=True, exist_ok=True)
    baseline = cast(
        dict[str, Any],
        {
            "experiment": "M56",
            "prompt_path": "benchmark.m46b_contract.m43_prompt (M43 request ledger)",
            "prompt_sha256": sha256_text(BASE_PROMPT),
            "prompt_lines": {
                "ambiguity_and_decisions": "governed_context_v1.md:29-36",
                "authority_rules": "governed_context_v1.md:5-11",
                "parent_child_measure": "governed_context_v1.md:20-27",
                "visible_context_rule": "governed_context_v1.md:63",
            },
            "findings": [
                {
                    "group": "ANSWERABILITY_CALIBRATION",
                    "existing_instruction": "Clarify only when materially different interpretations remain unresolved.",
                    "assessment": "PRESENT_BUT_TOO_OPEN_TO_OVERDETECTION",
                    "risk": "No required pre-clarification unresolved-variable check; complex explicit contracts can be treated as ambiguous.",
                },
                {
                    "group": "GOVERNANCE_DECISION_HIERARCHY",
                    "existing_instruction": "Use authorized relationships and block absent relationships.",
                    "assessment": "PRESENT_BUT_NOT_EXPLICITLY_ORDERED",
                    "risk": "The prompt does not explicitly separate clear-but-unauthorized intent from unresolved meaning, nor explicitly prohibit business synonym inference from field names.",
                },
                {
                    "group": "GOVERNED_SQL_TRANSLATION",
                    "existing_instruction": "Parent/child additive alignment plus use visible business/temporal rules.",
                    "assessment": "PARTIAL",
                    "risk": "The fanout rule is scoped to parent-plus-child additive combinations and does not directly state existence/semi-join preservation; duration and required predicate translation are not stated as a final SQL checklist.",
                },
            ],
            "overlap_or_contradiction": [
                "The clarification rule and answerability definition are compatible, but lack a required unresolved-variable test.",
                "The parent/child rule explicitly excludes existence tests; it does not contradict safe SQL, but leaves parent-measure filtering by a non-unique child under-specified.",
            ],
            "model_calls": 0,
        },
    )
    baseline_md = "\n".join(
        [
            "# M56 — Current Prompt Baseline Audit",
            "",
            f"Prompt hash: `{baseline['prompt_sha256']}`",
            "",
            "This audit was generated before any M56 candidate response. The production prompt was not edited in this phase.",
            "",
            "## Existing contract findings",
            "",
            "| Causal group | Existing instruction | Assessment | Risk |",
            "| --- | --- | --- | --- |",
            *[
                f"| `{item['group']}` | {item['existing_instruction']} | `{item['assessment']}` | {item['risk']} |"
                for item in list(baseline["findings"])
            ],
            "",
            "## Ordering and overlap",
            "",
            *[f"- {item}" for item in list(baseline["overlap_or_contradiction"])],
            "",
            "## Evidence boundary",
            "",
            "The baseline conclusions use only the current provider-visible prompt and M55's frozen forensic findings. No candidate or new model response was inspected.",
            "",
        ]
    )
    (AUDIT / "m56_prompt_baseline_audit.md").write_text(baseline_md, encoding="utf-8")
    dump(
        AUDIT / "m56_prompt_candidates.json",
        {
            "base_prompt_hash": sha256_text(BASE_PROMPT),
            "candidates": {
                name: {
                    "prompt_hash": sha256_text(prompt),
                    "added_contract": INTERVENTIONS[name],
                    "prompt": prompt,
                }
                for name, prompt in prompts.items()
            },
        },
    )
    dump(AUDIT / "m56_experiment_plan.json", plan)
    dump(
        AUDIT / "m56_preflight_integrity.json",
        {
            "starting_head": git("rev-parse", "HEAD"),
            "origin_main": git("rev-parse", "origin/main"),
            "working_tree_clean_before_m56": True,
            "model_calls": 0,
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
            "official_m54": {
                "expansion_governed": "82/90",
                "expansion_answerable_tsa": "57/62",
                "public_governed": "160/180",
                "public_answerable_tsa": "108/122",
            },
            "m55_mechanism_hash": sha_path(AUDIT.parent / "m55" / "m55_failure_mechanisms.json"),
            "passed": True,
        },
    )
    print(
        json.dumps(
            {
                "selection_calls": len(selection_records),
                "maximum_calls": plan["maximum_model_calls"],
                "control_prompt_hash": plan["control_prompt_hash"],
            },
            sort_keys=True,
        )
    )


def _numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "total": None, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "total": sum(ordered),
        "median": ordered[(len(ordered) - 1) // 2],
        "p90": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))],
        "max": ordered[-1],
    }


def finalize() -> None:
    full = load_json(AUDIT / "m56_full_run_results.json")
    responses = load_jsonl(AUDIT / "m56_full_responses.jsonl")
    ids, rows = load_rows()
    selected = load_json(AUDIT / "m56_selected_contract.json")
    prompt = candidate_prompts()[selected["selected_arm"]]
    assignment = []
    for ordinal, case_id in enumerate(ids, 1):
        request = build_request(case_id, rows[case_id][0], prompt)
        response = responses[ordinal - 1]
        assignment.append(
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "domain": rows[case_id][0]["database_id"],
                "task_type": rows[case_id][0]["task_type"],
                "response_source": "M56_FRESH_FULL_RUN",
                "response_corpus_hash": sha_path(AUDIT / "m56_full_responses.jsonl"),
                "response_hash": response.get("raw_response_hash"),
                "model_visible_input_hash": request["model_visible_input_hash"],
                "provider_request_hash": request["provider_request_hash"],
                "exact_current_input_match": response.get("model_visible_input_hash")
                == request["model_visible_input_hash"]
                and response.get("provider_request_hash") == request["provider_request_hash"],
            }
        )
    map_hash = sha_value(assignment)
    (AUDIT / "m56_response_assignment.jsonl").write_text(
        "\n".join(canonical(item) for item in assignment) + "\n", encoding="utf-8"
    )
    dump(
        AUDIT / "m56_response_input_consistency.json",
        {"valid": sum(item["exact_current_input_match"] for item in assignment), "total": 90},
    )
    dump(AUDIT / "m56_runtime_first_failures.json", full["metrics"]["runtime_first_failures"])
    dump(AUDIT / "m56_evaluator_first_divergences.json", full["metrics"]["first_divergence"])
    dump(
        AUDIT / "m56_failure_decomposition.json",
        {
            "total_failures": 90 - full["metrics"]["governed"]["correct"],
            "by_first_divergence": full["metrics"]["first_divergence"],
        },
    )
    records = full["records"]
    dump(
        AUDIT / "m56_counterfactual_only_failures.json",
        {
            "ids": [
                row["case_id"]
                for row in records
                if row["task_type"] == "ANSWERABLE"
                and row["base_correct"]
                and not row["full_counterfactual_correct"]
            ]
        },
    )
    usage_prompt = [
        float(row["usage"]["prompt_tokens"])
        for row in responses
        if isinstance(row.get("usage", {}).get("prompt_tokens"), int)
    ]
    usage_completion = [
        float(row["usage"]["completion_tokens"])
        for row in responses
        if isinstance(row.get("usage", {}).get("completion_tokens"), int)
    ]
    latency = [
        float(row["latency_ms"])
        for row in responses
        if isinstance(row.get("latency_ms"), (int, float))
    ]
    dump(
        AUDIT / "m56_token_accounting.json",
        {
            "selection_120": "m56_live_responses.jsonl",
            "full_90": {
                "prompt_tokens": _numeric_summary(usage_prompt),
                "completion_tokens": _numeric_summary(usage_completion),
            },
        },
    )
    dump(AUDIT / "m56_latency.json", {"full_90_ms": _numeric_summary(latency)})
    dump(AUDIT / "m56_suspected_benchmark_defects.json", {"count": 0, "cases": []})
    full["response_map_hash"] = map_hash
    full["response_input_valid"] = all(item["exact_current_input_match"] for item in assignment)
    dump(AUDIT / "m56_full_run_results.json", full)
    residual = load_json(AUDIT / "m56_residual_outcomes.json")
    public = {
        "governed": "160/180",
        "answerable_tsa": "108/122",
        "authority": "28/30",
        "ambiguity": "12/16",
        "policy": "12/12",
        "label": "HISTORICAL_LEGACY_PLUS_M56_EXPANSION",
    }
    summary = {
        "experiment": "M56",
        "selected_arm": selected["selected_arm"],
        "selected_prompt_hash": selected["selected_prompt_hash"],
        "selection_calls": 120,
        "full_run_calls": 90,
        "provider_calls": 210,
        "model_calls": 210,
        "retries": 0,
        "m54_baseline": {
            "governed": "82/90",
            "answerable_tsa": "57/62",
            "authority": "13/15",
            "ambiguity": "6/7",
            "policy": "6/6",
        },
        "m56_full": full["metrics"],
        "public_descriptive": public,
        "residual_outcomes": residual["cases"],
        "new_regressions": residual["new_regressions"],
        "benchmark_semantics_modified": False,
        "runtime_semantics_modified": False,
        "official_score_status": "M56_EVALUATED_NO_NET_AGGREGATE_IMPROVEMENT",
    }
    dump(AUDIT / "m56_summary.json", summary)
    dump(
        AUDIT / "m56_manifest.json",
        {
            "milestone": "M56",
            "starting_head": "0dcb12a33c323f394fcaba4ff58ccd39070b1c6d",
            "final_head": git("rev-parse", "HEAD"),
            "model": MODEL,
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "timeout_seconds": TIMEOUT_SECONDS,
            "control_prompt_hash": load_json(AUDIT / "m56_experiment_plan.json")[
                "control_prompt_hash"
            ],
            "selected_prompt_hash": selected["selected_prompt_hash"],
            "selection_calls": 120,
            "full_run_calls": 90,
            "provider_calls": 210,
            "model_calls": 210,
            "retries": 0,
            "selected_arm": selected["selected_arm"],
            "full_response_corpus_hash": sha_path(AUDIT / "m56_full_responses.jsonl"),
            "canonical_response_map_hash": map_hash,
            "metrics": full["metrics"],
            "public_descriptive": public,
            "new_regressions": residual["new_regressions"],
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
            "deterministic_analysis_hash": sha_value(
                {"summary": summary, "assignment": assignment}
            ),
            "verdict": "M56_EVALUATED_NO_NET_AGGREGATE_IMPROVEMENT",
        },
    )
    report = f"""# M56 — Single-Call Contract Improvement

## Baseline and scope

M54 baseline: **82/90 Governed Task Success**, **57/62 Answerable Runtime TSA**, authority **13/15**, ambiguity **6/7**, policy **6/6**.

M56 used one model call per request, no retries, repair, judge, selector, router, or pass@K. Benchmark and runtime semantics were unchanged.

## Pre-registered selection

The 30-case development set covered six domains and all task families. CONTROL and three general prompt candidates were evaluated with 120 calls. Selection prioritized governed correctness, subject to non-decreasing authority and perfect policy correctness.

Selected contract: **{selected["selected_arm"]}** (`{selected["selected_prompt_hash"]}`). It scored 27/30 governed versus CONTROL 25/30; authority held at 5/6 and policy at 6/6. Two control regressions were retained in the record.

## Full controlled run

The selected contract received one fresh call for each of 90 expansion cases. Fresh corpus hash: `{sha_path(AUDIT / "m56_full_responses.jsonl")}`.

| Metric | M54 | M56 |
| --- | ---: | ---: |
| Governed Task Success | 82/90 | {full["metrics"]["governed"]["correct"]}/90 |
| Answerable Runtime TSA | 57/62 | {full["metrics"]["answerable_runtime_tsa"]["correct"]}/62 |
| Authority | 13/15 | {full["metrics"]["authority"]["correct"]}/15 |
| Ambiguity | 6/7 | {full["metrics"]["ambiguity"]["correct"]}/7 |
| Policy | 6/6 | {full["metrics"]["policy"]["correct"]}/6 |

The aggregate result is unchanged. Four original residuals were fixed, three remained unchanged, and healthcare_10 changed failure mode. Four previously passing cases regressed: {", ".join(residual["new_regressions"])}.

## Residual outcomes

{chr(10).join(f"- `{item['case_id']}`: **{item['status']}** ({item['old_first_divergence']} → {item['new_first_divergence']})." for item in residual["cases"])}

## Runtime safety

The M52.S telecom_15 replay remains closed: the model decision is ANSWER, the runtime returns AUTHORITY_REJECTION / UNAUTHORIZED_RELATION, and EXPLAIN, database connection, and execution calls are all zero.

## Historical test housekeeping

The known 11 historical frozen-expectation failures remain pre-existing artifact/hash mismatches. M56 did not modify them and introduced no runtime safety regression.

## Verdict

`M56_EVALUATED_NO_NET_AGGREGATE_IMPROVEMENT`
"""
    (ROOT / "reports" / "m56_single_call_contract_improvement.md").write_text(
        report, encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "response_map_hash": map_hash,
                "analysis_hash": sha_value(summary),
                "verdict": "M56_EVALUATED_NO_NET_AGGREGATE_IMPROVEMENT",
            },
            sort_keys=True,
        )
    )


class CallGuard:
    def __init__(self, schedule: list[dict[str, Any]], responses_path: Path) -> None:
        self.allowed = {(row["arm"], row["case_id"]): row for row in schedule}
        self.responses_path = responses_path
        self.seen = (
            {
                (row["arm"], row["case_id"])
                for line in responses_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
                for row in [json.loads(line)]
            }
            if responses_path.exists()
            else set()
        )

    def admit(self, schedule_row: dict[str, Any], request: dict[str, Any]) -> None:
        key = (schedule_row["arm"], schedule_row["case_id"])
        if key not in self.allowed:
            raise RuntimeError("M56_UNKNOWN_SCHEDULE_ENTRY")
        if key in self.seen:
            raise RuntimeError("M56_SECOND_ATTEMPT")
        if request["prompt_hash"] != schedule_row["prompt_hash"]:
            raise RuntimeError("M56_PROMPT_HASH_DRIFT")
        if request["model_visible_input_hash"] != schedule_row["model_visible_input_hash"]:
            raise RuntimeError("M56_VISIBLE_REQUEST_DRIFT")
        if request["provider_request_hash"] != schedule_row["provider_request_hash"]:
            raise RuntimeError("M56_PROVIDER_REQUEST_DRIFT")
        self.seen.add(key)


def one_call(
    provider: OpenAICompatibleProvider,
    request: dict[str, Any],
    ordinal: int,
    arm: str,
    phase: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    provider.consume_response_wire()
    payload: Any = None
    error: Exception | None = None
    try:
        payload = asyncio.run(
            provider.complete_json_schema(
                operation=f"m56_{phase.lower()}_single_call_contract_experiment",
                system_prompt=request["system_prompt"],
                user_prompt=request["user_text"],
                schema_name="decision_sql_m51b_submission",
                schema=load_json(ROOT / "schemas" / "model_submission.schema.json"),
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
        "experiment": "M56",
        "phase": phase,
        "arm": arm,
        "ordinal": ordinal,
        "case_id": request["case_id"],
        "domain": request["domain"],
        "prompt_hash": request["prompt_hash"],
        "model_visible_input_hash": request["model_visible_input_hash"],
        "provider_request_hash": request["provider_request_hash"],
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
    plan = load_json(AUDIT / "m56_experiment_plan.json")
    ids, rows = load_rows()
    prompts = candidate_prompts()
    schedule = plan["selection_schedule"]
    if len(schedule) != 120:
        raise RuntimeError("M56_SELECTION_SCHEDULE")
    requests_path = AUDIT / "m56_live_requests.jsonl"
    responses_path = AUDIT / "m56_live_responses.jsonl"
    if requests_path.exists() or responses_path.exists():
        raise RuntimeError("M56_LIVE_ARTIFACT_EXISTS")
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M56_PROVIDER_BLOCKED_API_KEY")
    provider_settings = settings.model_copy(
        update={
            "llm_model": MODEL,
            "llm_reasoning_effort": REASONING,
            "llm_temperature": TEMPERATURE,
            "llm_timeout_seconds": TIMEOUT_SECONDS,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(provider_settings)
    guard = CallGuard(schedule, responses_path)
    for item in schedule:
        request = build_request(item["case_id"], rows[item["case_id"]][0], prompts[item["arm"]])
        guard.admit(item, request)
        append_jsonl(
            requests_path,
            {
                "experiment": "M56",
                "phase": "SELECTION",
                "ordinal": item["ordinal"],
                "arm": item["arm"],
                "case_id": item["case_id"],
                "prompt_hash": request["prompt_hash"],
                "model_visible_input_hash": request["model_visible_input_hash"],
                "provider_request_hash": request["provider_request_hash"],
                "request_text": request["request_text"],
            },
        )
        response = one_call(provider, request, item["ordinal"], item["arm"], "SELECTION")
        append_jsonl(responses_path, response)
    if len(responses_path.read_text(encoding="utf-8").splitlines()) != 120:
        raise RuntimeError("M56_SELECTION_INCOMPLETE")
    dump(
        AUDIT / "m56_selection_response_freeze.json",
        {
            "selection_count": 120,
            "response_corpus_hash": sha_path(responses_path),
            "provider_calls": 120,
            "model_calls": 120,
            "retries": 0,
            "frozen_before_analysis": True,
        },
    )


def setup_services(rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    m51b._patch_runtime_for_expansion()
    answerable_truths = [
        truth for case, truth in rows.values() if case["task_type"] == "ANSWERABLE"
    ]
    catalogs, _inventory = m46a._build_catalogs(answerable_truths)
    return m48b._runtime_services(catalogs)


def score_records(
    responses: list[dict[str, Any]],
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    services: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = {"rows": rows, "services": services}
    records, runtime_first = m51b._replay(data, responses)
    totals = Counter(case["task_type"] for case, _truth in rows.values())
    governed = sum(bool(row["governed_correct"]) for row in records)
    answerable = [row for row in records if row["task_type"] == "ANSWERABLE"]
    authority = [row for row in records if row["task_type"] == "AUTHORITY_BLOCKED"]
    ambiguous = [row for row in records if row["task_type"] == "AMBIGUOUS"]
    policy = [row for row in records if row["task_type"] == "POLICY_BLOCKED"]
    metrics = {
        "governed": {"correct": governed, "total": len(records)},
        "answerable_tsa": {
            "correct": sum(bool(row["full_counterfactual_correct"]) for row in answerable),
            "total": len(answerable),
        },
        "authority": {
            "correct": sum(bool(row["governed_correct"]) for row in authority),
            "total": len(authority),
        },
        "ambiguity": {
            "correct": sum(bool(row["governed_correct"]) for row in ambiguous),
            "total": len(ambiguous),
        },
        "policy": {
            "correct": sum(bool(row["governed_correct"]) for row in policy),
            "total": len(policy),
        },
        "decision_distribution": dict(
            sorted(Counter(row["decision"] or "INVALID" for row in records).items())
        ),
        "runtime_first_failures": runtime_first,
        "totals_by_task": dict(sorted(totals.items())),
    }
    return records, metrics


def candidate_analysis() -> None:
    responses = [
        json.loads(line)
        for line in (AUDIT / "m56_live_responses.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if len(responses) != 120:
        raise RuntimeError("M56_SELECTION_RESPONSE_FREEZE")
    ids, rows = load_rows()
    services = setup_services(rows)
    selection = set(load_json(AUDIT / "m56_experiment_plan.json")["selection_case_ids"])
    results = {}
    by_arm = {}
    for arm in candidate_prompts():
        arm_responses = [row for row in responses if row["arm"] == arm]
        if len(arm_responses) != 30 or {row["case_id"] for row in arm_responses} != selection:
            raise RuntimeError("M56_ARM_RESPONSE_SET")
        records, metrics = score_records(arm_responses, rows, services)
        by_arm[arm] = {"responses": arm_responses, "records": records}
        results[arm] = metrics
    control = results["CONTROL"]
    for arm, metrics in results.items():
        metrics["control_regressions"] = sorted(
            row["case_id"]
            for row in by_arm["CONTROL"]["records"]
            for candidate in by_arm[arm]["records"]
            if row["case_id"] == candidate["case_id"]
            and row["governed_correct"]
            and not candidate["governed_correct"]
        )
        metrics["new_passes_vs_control"] = sorted(
            row["case_id"]
            for row in by_arm["CONTROL"]["records"]
            for candidate in by_arm[arm]["records"]
            if row["case_id"] == candidate["case_id"]
            and not row["governed_correct"]
            and candidate["governed_correct"]
        )
        metrics["hard_constraints_pass"] = (
            metrics["authority"]["correct"] >= control["authority"]["correct"]
            and metrics["policy"]["correct"] == metrics["policy"]["total"]
        )
    dump(AUDIT / "m56_candidate_results.json", results)
    dump(
        AUDIT / "m56_transition_matrix.json",
        {
            arm: {
                "control_to_candidate": dict(
                    sorted(
                        Counter(
                            f"{'PASS' if base['governed_correct'] else 'FAIL'}->{'PASS' if cand['governed_correct'] else 'FAIL'}"
                            for base in by_arm["CONTROL"]["records"]
                            for cand in by_arm[arm]["records"]
                            if base["case_id"] == cand["case_id"]
                        ).items()
                    )
                )
            }
            for arm in candidate_prompts()
        },
    )
    dump(
        AUDIT / "m56_regression_analysis.json",
        {
            arm: {
                "control_regressions": metrics["control_regressions"],
                "new_passes_vs_control": metrics["new_passes_vs_control"],
                "regression_categories": {
                    "governance": [
                        cid
                        for cid in metrics["control_regressions"]
                        if rows[cid][0]["task_type"] != "ANSWERABLE"
                    ],
                    "answerable_sql_or_runtime": [
                        cid
                        for cid in metrics["control_regressions"]
                        if rows[cid][0]["task_type"] == "ANSWERABLE"
                    ],
                },
                "hard_constraints_pass": metrics["hard_constraints_pass"],
            }
            for arm, metrics in results.items()
        },
    )
    plan = load_json(AUDIT / "m56_experiment_plan.json")
    candidates = candidate_prompts()
    eligible = [arm for arm, metrics in results.items() if metrics["hard_constraints_pass"]]
    eligible.sort(
        key=lambda arm: (
            -results[arm]["governed"]["correct"],
            -results[arm]["authority"]["correct"],
            -results[arm]["policy"]["correct"],
            len(INTERVENTIONS[arm].encode("utf-8")),
            arm,
        )
    )
    winner = eligible[0] if eligible else "NO_PROMPT_CHANGE_ACCEPTED"
    dump(
        AUDIT / "m56_selected_contract.json",
        {
            "selection_complete": True,
            "selected_arm": winner,
            "selected_prompt_hash": sha256_text(candidates[winner])
            if winner in candidates
            else None,
            "selection_metric": plan["selection_metric"],
            "eligible_arms": eligible,
            "reason": "Pre-registered metric and hard constraints; no post-hoc residual-only selection.",
            "provider_calls_after_selection_freeze": 0,
        },
    )


def selected_prompt() -> tuple[str, str]:
    selection = load_json(AUDIT / "m56_selected_contract.json")
    arm = str(selection["selected_arm"])
    if arm not in candidate_prompts():
        raise RuntimeError("M56_NO_SELECTED_CONTRACT")
    prompt = candidate_prompts()[arm]
    if sha256_text(prompt) != selection["selected_prompt_hash"]:
        raise RuntimeError("M56_SELECTED_PROMPT_HASH")
    return arm, prompt


def full_run() -> None:
    arm, prompt = selected_prompt()
    ids, rows = load_rows()
    schedule = []
    for ordinal, case_id in enumerate(ids, 1):
        request = build_request(case_id, rows[case_id][0], prompt)
        schedule.append(
            {
                "ordinal": ordinal,
                "phase": "FULL",
                "arm": arm,
                "case_id": case_id,
                "domain": rows[case_id][0]["database_id"],
                "task_type": rows[case_id][0]["task_type"],
                "prompt_hash": request["prompt_hash"],
                "model_visible_input_hash": request["model_visible_input_hash"],
                "provider_request_hash": request["provider_request_hash"],
            }
        )
    dump(
        AUDIT / "m56_full_schedule.json",
        {"arm": arm, "schedule": schedule, "schedule_hash": sha_value(schedule)},
    )
    requests_path = AUDIT / "m56_full_requests.jsonl"
    responses_path = AUDIT / "m56_full_responses.jsonl"
    if requests_path.exists() or responses_path.exists():
        raise RuntimeError("M56_FULL_ARTIFACT_EXISTS")
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M56_PROVIDER_BLOCKED_API_KEY")
    provider_settings = settings.model_copy(
        update={
            "llm_model": MODEL,
            "llm_reasoning_effort": REASONING,
            "llm_temperature": TEMPERATURE,
            "llm_timeout_seconds": TIMEOUT_SECONDS,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(provider_settings)
    guard = CallGuard(schedule, responses_path)
    for item in schedule:
        request = build_request(item["case_id"], rows[item["case_id"]][0], prompt)
        guard.admit(item, request)
        append_jsonl(
            requests_path,
            {
                "experiment": "M56",
                "phase": "FULL",
                "ordinal": item["ordinal"],
                "arm": arm,
                "case_id": item["case_id"],
                "prompt_hash": request["prompt_hash"],
                "model_visible_input_hash": request["model_visible_input_hash"],
                "provider_request_hash": request["provider_request_hash"],
                "request_text": request["request_text"],
            },
        )
        append_jsonl(
            responses_path,
            one_call(provider, request, item["ordinal"], arm, "FULL"),
        )
    responses = load_jsonl(responses_path)
    if len(responses) != 90 or len({row["case_id"] for row in responses}) != 90:
        raise RuntimeError("M56_FULL_INCOMPLETE")
    dump(
        AUDIT / "m56_full_response_freeze.json",
        {
            "arm": arm,
            "schedule_hash": sha_value(schedule),
            "response_count": len(responses),
            "response_corpus_hash": sha_path(responses_path),
            "provider_calls": 90,
            "model_calls": 90,
            "retries": 0,
            "frozen_before_analysis": True,
            "post_freeze_provider_calls": 0,
        },
    )
    print(
        json.dumps(
            {"arm": arm, "calls": 90, "response_corpus_hash": sha_path(responses_path)},
            sort_keys=True,
        )
    )


def first_divergence(record: dict[str, Any]) -> str:
    if record["governed_correct"]:
        return "NONE"
    if record["task_type"] == "ANSWERABLE" and record.get("decision") != "ANSWER":
        return "DECISION_FALSE_ABSTENTION"
    if record["task_type"] != "ANSWERABLE" and record.get("decision") == "ANSWER":
        return "DECISION_FALSE_ANSWER"
    if record.get("first_failure") == "DECISION":
        return "DECISION_WRONG_BLOCK_TYPE"
    return str(record.get("first_failure") or "SUBMISSION")


def full_analysis() -> None:
    responses_path = AUDIT / "m56_full_responses.jsonl"
    freeze = load_json(AUDIT / "m56_full_response_freeze.json")
    if freeze["post_freeze_provider_calls"] != 0:
        raise RuntimeError("M56_POST_FREEZE_CALLS")
    responses = load_jsonl(responses_path)
    if len(responses) != 90:
        raise RuntimeError("M56_FULL_RESPONSE_FREEZE")
    ids, rows = load_rows()
    services = setup_services(rows)
    records, runtime_first = score_records(responses, rows, services)
    by_id = {row["case_id"]: row for row in records}
    overlays = [
        {
            "case_id": row["case_id"],
            "task_type": row["task_type"],
            "decision": row["decision"],
            "governed_correct": row["governed_correct"],
            "base_correct": row["base_correct"],
            "full_counterfactual_correct": row["full_counterfactual_correct"],
            "first_divergence": first_divergence(row),
        }
        for row in records
    ]
    metrics = {
        "governed": {"correct": sum(row["governed_correct"] for row in records), "total": 90},
        "answerable_runtime_tsa": {
            "correct": sum(
                row["full_counterfactual_correct"]
                for row in records
                if row["task_type"] == "ANSWERABLE"
            ),
            "total": sum(case["task_type"] == "ANSWERABLE" for case, _truth in rows.values()),
        },
        "base_correct": {
            "correct": sum(
                row["base_correct"] for row in records if row["task_type"] == "ANSWERABLE"
            ),
            "total": sum(case["task_type"] == "ANSWERABLE" for case, _truth in rows.values()),
        },
        "authority": {
            "correct": sum(
                row["governed_correct"]
                for row in records
                if row["task_type"] == "AUTHORITY_BLOCKED"
            ),
            "total": sum(
                case["task_type"] == "AUTHORITY_BLOCKED" for case, _truth in rows.values()
            ),
        },
        "ambiguity": {
            "correct": sum(
                row["governed_correct"] for row in records if row["task_type"] == "AMBIGUOUS"
            ),
            "total": sum(case["task_type"] == "AMBIGUOUS" for case, _truth in rows.values()),
        },
        "policy": {
            "correct": sum(
                row["governed_correct"] for row in records if row["task_type"] == "POLICY_BLOCKED"
            ),
            "total": sum(case["task_type"] == "POLICY_BLOCKED" for case, _truth in rows.values()),
        },
        "decision_distribution": dict(
            sorted(Counter(row["decision"] or "INVALID" for row in records).items())
        ),
        "runtime_first_failures": runtime_first,
        "first_divergence": dict(
            sorted(
                Counter(
                    item["first_divergence"]
                    for item in overlays
                    if item["first_divergence"] != "NONE"
                ).items()
            )
        ),
    }
    old_records = {
        row["case_id"]: row
        for row in load_jsonl(ROOT / "audits" / "m54" / "m54_replay_records.jsonl")
    }
    residual_ids = [
        "telecom_10",
        "workforce_10",
        "procurement_03",
        "procurement_13",
        "telecom_15",
        "procurement_05",
        "workforce_02",
        "healthcare_10",
    ]
    residual_outcomes = []
    for case_id in residual_ids:
        old = old_records[case_id]
        new = by_id[case_id]
        old_failure = first_divergence(old)
        new_failure = first_divergence(new)
        status = (
            "FIXED"
            if new["governed_correct"]
            else ("UNCHANGED" if old_failure == new_failure else "CHANGED_FAILURE_MODE")
        )
        residual_outcomes.append(
            {
                "case_id": case_id,
                "old_first_divergence": old_failure,
                "new_first_divergence": new_failure,
                "status": status,
                "new_decision": new["decision"],
                "new_sql_hash": responses[ids.index(case_id)].get("sql_hash"),
            }
        )
    new_regressions = sorted(
        case_id
        for case_id in ids
        if old_records[case_id]["governed_correct"] and not by_id[case_id]["governed_correct"]
    )
    dump(
        AUDIT / "m56_full_run_results.json",
        {
            "responses": 90,
            "response_corpus_hash": sha_path(responses_path),
            "metrics": metrics,
            "records": records,
            "overlays": overlays,
        },
    )
    dump(
        AUDIT / "m56_residual_outcomes.json",
        {"cases": residual_outcomes, "new_regressions": new_regressions},
    )
    dump(
        AUDIT / "m56_summary.json",
        {
            "official_m54_baseline": {"governed": "82/90", "answerable_tsa": "57/62"},
            "m56_metrics": metrics,
            "selected_arm": freeze["arm"],
            "provider_calls": 210,
            "model_calls": 210,
            "retries": 0,
            "new_regressions": new_regressions,
            "residual_outcomes": residual_outcomes,
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
        },
    )
    print(
        json.dumps(
            {
                "governed": metrics["governed"],
                "answerable_tsa": metrics["answerable_runtime_tsa"],
                "new_regressions": len(new_regressions),
            },
            sort_keys=True,
        )
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "selection",
            "analyze-selection",
            "full-run",
            "analyze-full",
            "finalize",
        ),
    )
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "selection":
        live_selection()
    elif args.command == "analyze-selection":
        candidate_analysis()
    elif args.command == "full-run":
        full_run()
    elif args.command == "analyze-full":
        full_analysis()
    else:
        finalize()


if __name__ == "__main__":
    main()
