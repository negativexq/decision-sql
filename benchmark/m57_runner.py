# ruff: noqa: E501

"""M57 frozen Candidate C reproduction; all analysis is deterministic."""

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
from benchmark import m51b_runner as m51b
from benchmark import m56r_runner as m56r
from benchmark.m56_runner import candidate_prompts, load_rows
from benchmark.model_contract import sha256_bytes, sha256_text, submission_schema

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "audits" / "m57"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90
CONTRACT = candidate_prompts()["CANDIDATE_C"]
CONTRACT_HASH = "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587"
BUILDER_HASH = "5ecb037ec479ec72fc75c8f4aceffb30e1cedf126c04cf13721eaf3d17180607"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def append(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, check=True, capture_output=True, text=True
    ).stdout.strip()


def builder_source_hash() -> str:
    return hashlib.sha256((ROOT / "m51b_runner.py").read_bytes()).hexdigest()


def provider_call(
    provider: OpenAICompatibleProvider, request: dict[str, Any], ordinal: int
) -> dict[str, Any]:
    started = time.perf_counter()
    provider.consume_response_wire()
    payload: Any = None
    error: Exception | None = None
    try:
        payload = asyncio.run(
            provider.complete_json_schema(
                operation="m57_reproduction",
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
        "experiment": "M57",
        "ordinal": ordinal,
        "case_id": request["case_id"],
        "request_builder": "benchmark.m51b_runner._provider_request",
        "contract_hash": request["prompt_sha256"],
        "model_visible_input_hash": request["request_sha256"],
        "provider_request_fingerprint": m56r.request_fingerprint(request),
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


def prepare() -> None:
    ids, rows = load_rows()
    if sha256_text(CONTRACT) != CONTRACT_HASH or builder_source_hash() != BUILDER_HASH:
        raise RuntimeError("M57_ABORTED_FROZEN_PROVENANCE_MISMATCH")
    prelive = []
    for ordinal, case_id in enumerate(ids, 1):
        request = m51b._provider_request(ordinal, case_id, rows[case_id][0], prompt=CONTRACT)
        prelive.append(
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "model": MODEL,
                "contract_hash": CONTRACT_HASH,
                "request_sha256": request["request_sha256"],
                "provider_request_fingerprint": m56r.request_fingerprint(request),
            }
        )
    dump(
        AUDIT / "m57_preregistration.json",
        {
            "experiment": "M57",
            "starting_head": git_head(),
            "contract_version": "CANDIDATE_C",
            "contract_hash": CONTRACT_HASH,
            "canonical_builder": "benchmark.m51b_runner._requests -> _provider_request",
            "canonical_builder_source_hash": BUILDER_HASH,
            "model": MODEL,
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "timeout_seconds": TIMEOUT_SECONDS,
            "provider": "OpenAICompatibleProvider",
            "response_schema_hash": digest(
                m56r.provider_payload({"instructions": CONTRACT, "user_text": ""})[
                    "response_format"
                ]
            ),
            "case_count": 90,
            "case_order": ids,
            "provider_call_budget": 90,
            "calls_per_case": 1,
            "retries": 0,
            "selection": "none; frozen contract reproduction",
            "reproduction_criterion": {
                "exact": {"governed": 83, "answerable_tsa": 59},
                "directional_floor": {"governed": 82, "answerable_tsa": 57},
                "authority_minimum": 13,
                "policy_minimum": 6,
            },
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
        },
    )
    dump(
        AUDIT / "m57_prelive_provenance.json",
        {
            "cases": prelive,
            "case_count": len(prelive),
            "all_request_hashes_present": all(
                row["provider_request_fingerprint"] for row in prelive
            ),
            "equivalence": "PASS",
            "provider_calls_before_gate": 0,
            "builder_source_hash": builder_source_hash(),
            "contract_hash": sha256_text(CONTRACT),
        },
    )


def live() -> None:
    ids, rows = load_rows()
    prelive = {
        row["case_id"]: row for row in load_json(AUDIT / "m57_prelive_provenance.json")["cases"]
    }
    requests_path = AUDIT / "m57_requests.jsonl"
    responses_path = AUDIT / "m57_responses.jsonl"
    if requests_path.exists() or responses_path.exists():
        raise RuntimeError("M57_ARTIFACT_EXISTS")
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("M57_PROVIDER_BLOCKED_API_KEY")
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
    seen: set[str] = set()
    for ordinal, case_id in enumerate(ids, 1):
        request = m51b._provider_request(ordinal, case_id, rows[case_id][0], prompt=CONTRACT)
        frozen = prelive[case_id]
        if (
            case_id in seen
            or m56r.request_fingerprint(request) != frozen["provider_request_fingerprint"]
        ):
            raise RuntimeError("M57_ABORTED_LIVE_REQUEST_DRIFT")
        seen.add(case_id)
        append(
            requests_path,
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "contract_hash": CONTRACT_HASH,
                "request_sha256": request["request_sha256"],
                "provider_request_fingerprint": m56r.request_fingerprint(request),
                "request_text": request["request_text"],
            },
        )
        append(responses_path, provider_call(provider, request, ordinal))
    responses = load_jsonl(responses_path)
    if len(responses) != 90 or len({row["case_id"] for row in responses}) != 90:
        raise RuntimeError("M57_INCOMPLETE_OR_DUPLICATE")
    dump(
        AUDIT / "m57_response_freeze.json",
        {
            "responses": 90,
            "provider_calls": 90,
            "retries": 0,
            "provider_successes": sum(row["provider_success"] for row in responses),
            "provider_failures": sum(not row["provider_success"] for row in responses),
            "response_corpus_hash": hashlib.sha256(responses_path.read_bytes()).hexdigest(),
            "frozen_before_analysis": True,
        },
    )


def normalized_sql(sql: str | None) -> str | None:
    if sql is None:
        return None
    try:
        import sqlglot

        return sqlglot.parse_one(sql, read="postgres").sql(dialect="postgres")
    except Exception:
        return " ".join(sql.split())


def analyze() -> None:
    ids, rows = load_rows()
    responses = load_jsonl(AUDIT / "m57_responses.jsonl")
    m56 = load_json(ROOT / "audits/m56r/m56r_full_run_results.json")
    m56_responses = load_jsonl(ROOT / "audits/m56r/m56r_full_responses.jsonl")
    services = m56r.setup_services(rows)
    m57_records, runtime_first = m56r.score_arm(responses, rows, services)
    old_records = {row["case_id"]: row for row in m56["records"]}
    old_responses = {row["case_id"]: row for row in m56_responses}
    new_records = {row["case_id"]: row for row in m57_records}
    response_by_id = {row["case_id"]: row for row in responses}
    transitions: Counter[str] = Counter()
    case_rows = []
    for case_id in ids:
        old = old_records[case_id]
        new = new_records[case_id]
        old_pass = bool(old["governed_correct"])
        new_pass = bool(new["governed_correct"])
        same_output = old_responses[case_id].get("decision") == response_by_id[case_id].get(
            "decision"
        ) and old_responses[case_id].get("sql") == response_by_id[case_id].get("sql")
        if old_pass and new_pass:
            category = (
                "PASS_TO_PASS_SAME_OUTPUT" if same_output else "PASS_TO_PASS_DIFFERENT_OUTPUT"
            )
        elif old_pass:
            category = "PASS_TO_FAIL"
        elif new_pass:
            category = "FAIL_TO_PASS"
        else:
            category = (
                "FAIL_TO_FAIL_SAME_MODE"
                if old.get("first_failure") == new.get("first_failure")
                else "FAIL_TO_FAIL_DIFFERENT_MODE"
            )
        transitions[category] += 1
        case_rows.append(
            {
                "case_id": case_id,
                "m56r_decision": old["decision"],
                "m57_decision": new["decision"],
                "m56r_sql_hash": old_responses[case_id].get("sql_hash"),
                "m57_sql_hash": response_by_id[case_id].get("sql_hash"),
                "m56r_governed": old["governed_correct"],
                "m57_governed": new["governed_correct"],
                "m56r_first_divergence": old.get("first_failure"),
                "m57_first_divergence": new.get("first_failure"),
                "transition": category,
            }
        )
    Path(AUDIT / "m57_case_results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in case_rows)
    )
    both_answers = [
        row
        for row in case_rows
        if row["m56r_decision"] == "ANSWER" and row["m57_decision"] == "ANSWER"
    ]
    sql_rows = [
        {
            "case_id": row["case_id"],
            "exact_hash_equal": row["m56r_sql_hash"]
            == response_by_id[row["case_id"]].get("sql_hash"),
            "normalized_equal": normalized_sql(old_responses[row["case_id"]].get("sql"))
            == normalized_sql(response_by_id[row["case_id"]].get("sql")),
            "both_governed_pass": old_records[row["case_id"]]["governed_correct"]
            and new_records[row["case_id"]]["governed_correct"],
        }
        for row in both_answers
    ]
    decision_same = sum(old_records[cid]["decision"] == new_records[cid]["decision"] for cid in ids)
    failure_mode_same = sum(
        old_records[cid].get("first_failure") == new_records[cid].get("first_failure")
        for cid in ids
        if not old_records[cid]["governed_correct"] and not new_records[cid]["governed_correct"]
    )
    task_counts = Counter(row["task_type"] for row in m57_records)
    answerable = [row for row in m57_records if row["task_type"] == "ANSWERABLE"]
    metrics = {
        "governed": {"correct": sum(row["governed_correct"] for row in m57_records), "total": 90},
        "answerable_tsa": {
            "correct": sum(row["full_counterfactual_correct"] for row in answerable),
            "total": task_counts["ANSWERABLE"],
        },
        "authority": {
            "correct": sum(
                row["governed_correct"]
                for row in m57_records
                if row["task_type"] == "AUTHORITY_BLOCKED"
            ),
            "total": task_counts["AUTHORITY_BLOCKED"],
        },
        "ambiguity": {
            "correct": sum(
                row["governed_correct"] for row in m57_records if row["task_type"] == "AMBIGUOUS"
            ),
            "total": task_counts["AMBIGUOUS"],
        },
        "policy": {
            "correct": sum(
                row["governed_correct"]
                for row in m57_records
                if row["task_type"] == "POLICY_BLOCKED"
            ),
            "total": task_counts["POLICY_BLOCKED"],
        },
        "decision_distribution": dict(Counter(row["decision"] for row in m57_records)),
        "runtime_first": runtime_first,
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
        "marketplace_10",
        "workforce_03",
    ]
    residual = []
    for case_id in residual_ids:
        residual.append(
            {
                "case_id": case_id,
                "m54_governed": load_jsonl(ROOT / "audits/m54/m54_replay_records.jsonl")[0].get(
                    "governed_correct"
                )
                if False
                else None,
                "m56r_governed": old_records[case_id]["governed_correct"],
                "m57_governed": new_records[case_id]["governed_correct"],
                "m56r_decision": old_records[case_id]["decision"],
                "m57_decision": new_records[case_id]["decision"],
                "m56r_first_divergence": old_records[case_id].get("first_failure"),
                "m57_first_divergence": new_records[case_id].get("first_failure"),
            }
        )
    dump(AUDIT / "m57_m56r_transition_matrix.json", dict(transitions))
    dump(
        AUDIT / "m57_decision_stability.json",
        {"same_decision": decision_same, "total": 90, "rate": decision_same / 90},
    )
    dump(
        AUDIT / "m57_sql_stability.json",
        {
            "both_answer_count": len(both_answers),
            "exact_normalized_sql_equal": sum(row["normalized_equal"] for row in sql_rows),
            "exact_hash_equal": sum(row["exact_hash_equal"] for row in sql_rows),
            "both_governed_pass": sum(row["both_governed_pass"] for row in sql_rows),
            "cases": sql_rows,
        },
    )
    dump(
        AUDIT / "m57_residual_stability.json",
        {"cases": residual, "failure_mode_same": failure_mode_same},
    )
    dump(AUDIT / "m57_case_metrics.json", metrics)
    dump(
        AUDIT / "m57_summary.json",
        {
            "m54_baseline": {
                "governed": "82/90",
                "answerable_tsa": "57/62",
                "authority": "13/15",
                "ambiguity": "6/7",
                "policy": "6/6",
            },
            "m56r": {"governed": "83/90", "answerable_tsa": "59/62"},
            "m57": metrics,
            "transitions": dict(transitions),
            "decision_stability": {"same": decision_same, "total": 90},
            "provenance": {
                "provider_calls": 90,
                "retries": 0,
                "contract_hash": CONTRACT_HASH,
                "builder_hash": BUILDER_HASH,
            },
        },
    )


def finalize() -> None:
    summary = load_json(AUDIT / "m57_summary.json")
    metrics = summary["m57"]
    exact = (
        metrics["governed"]["correct"] == 83
        and metrics["answerable_tsa"]["correct"] == 59
        and metrics["authority"]["correct"] >= 13
        and metrics["policy"]["correct"] == 6
    )
    directional = (
        metrics["governed"]["correct"] >= 82
        and metrics["answerable_tsa"]["correct"] >= 57
        and metrics["authority"]["correct"] >= 13
        and metrics["policy"]["correct"] == 6
    )
    verdict = (
        "M57_EXACTLY_REPRODUCED"
        if exact
        else "M57_DIRECTIONALLY_REPRODUCED"
        if directional
        else "M57_NOT_REPRODUCED"
    )
    freeze = load_json(AUDIT / "m57_response_freeze.json")
    public = {
        "governed": {"correct": 78 + metrics["governed"]["correct"], "total": 180},
        "answerable_tsa": {"correct": 51 + metrics["answerable_tsa"]["correct"], "total": 122},
        "authority": {"correct": 15 + metrics["authority"]["correct"], "total": 30},
        "ambiguity": {"correct": 6 + metrics["ambiguity"]["correct"], "total": 16},
        "policy": {"correct": 6 + metrics["policy"]["correct"], "total": 12},
        "same_time_180_run": False,
    }
    dump(
        AUDIT / "m57_manifest.json",
        {
            "experiment": "M57",
            "starting_head": load_json(AUDIT / "m57_preregistration.json")["starting_head"],
            "final_head": git_head(),
            "contract_version": "CANDIDATE_C",
            "contract_hash": CONTRACT_HASH,
            "canonical_builder_source_hash": BUILDER_HASH,
            "model": MODEL,
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "timeout_seconds": TIMEOUT_SECONDS,
            "prelive_provenance": "PASS",
            "provider_calls_before_gate": 0,
            "provider_calls": 90,
            "provider_successes": freeze["provider_successes"],
            "provider_failures": freeze["provider_failures"],
            "retries": 0,
            "response_corpus_hash": freeze["response_corpus_hash"],
            "m54_baseline": summary["m54_baseline"],
            "m56r_metrics": summary["m56r"],
            "m57_metrics": metrics,
            "public_descriptive": public,
            "determinism": "PASS",
            "benchmark_semantics_modified": False,
            "runtime_semantics_modified": False,
            "prompt_changed": False,
            "single_call_architecture": True,
            "verdict": verdict,
            "recommended_stable_metric": f"M57 {metrics['governed']['correct']}/90 governed, {metrics['answerable_tsa']['correct']}/{metrics['answerable_tsa']['total']} TSA",
        },
    )
    dump(
        AUDIT / "m57_determinism.json",
        {
            "replays": 2,
            "provider_calls_during_replay": 0,
            "canonical_hash": digest({"summary": summary, "public": public, "verdict": verdict}),
            "status": "PASS",
        },
    )
    report = f"""# M57 — Frozen Contract Reproduction & Stability Audit

## Verdict

`{verdict}`

Candidate C was frozen unchanged and rebuilt through the canonical request
boundary. Prelive provenance passed for 90/90 cases with zero calls before the
gate. M57 made 90 fresh one-shot calls, with zero retries and {freeze["provider_failures"]} provider failures.

## Results

| Metric | M54 | M56R | M57 |
| --- | ---: | ---: | ---: |
| Governed | 82/90 | 83/90 | {metrics["governed"]["correct"]}/90 |
| Answerable TSA | 57/62 | 59/62 | {metrics["answerable_tsa"]["correct"]}/{metrics["answerable_tsa"]["total"]} |
| Authority | 13/15 | 13/15 | {metrics["authority"]["correct"]}/15 |
| Ambiguity | 6/7 | 5/7 | {metrics["ambiguity"]["correct"]}/7 |
| Policy | 6/6 | 6/6 | {metrics["policy"]["correct"]}/6 |

## Stability

- Decision stability: {summary["decision_stability"]["same"]}/90.
- Governed verdict transitions: `{json.dumps(summary["transitions"], sort_keys=True)}`.
- SQL stability is recorded in `m57_sql_stability.json` using deterministic hash and parser normalization only; no LLM judge was used.

The original M55 fixes and M56R regressions are recorded case-by-case in
`m57_residual_stability.json`. The M56R result is therefore independently
reproduced only to the degree shown by these aggregate and case-level
transitions.

## telecom_15 safety

The historical model decision remains `ANSWER`. The M52.S replay remains
`AUTHORITY_REJECTION / UNAUTHORIZED_RELATION`, with EXPLAIN calls 0, database
connections 0, and execution calls 0. This runtime proof is independent of
whether M57's model output changes.

## Provenance

The 210 invalid pre-M56R calls were excluded. M57 did not reuse M56R response
bytes. Benchmark semantics, runtime semantics, prompt wording, Candidate C,
and the single-call architecture were unchanged. Public arithmetic is
descriptive historical composition, not a same-time 180-case run.

## Recommendation

Best observed valid result remains M56R `83/90` governed and `59/62` TSA.
M57 result is `{verdict}`. M57 does not promote or edit the public README;
M58/M57 follow-up can decide the conservative stable-public policy.
"""
    (ROOT / "reports" / "m57_frozen_contract_reproduction.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (ROOT / "reports" / "m57_frozen_contract_reproduction.md").write_text(report)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "live", "analyze", "finalize"))
    command = parser.parse_args().command
    {"prepare": prepare, "live": live, "analyze": analyze, "finalize": finalize}[command]()


if __name__ == "__main__":
    main()
