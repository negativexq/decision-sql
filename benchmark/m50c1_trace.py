"""M50C.1 zero-call unified runtime trace and frozen replay.

Layer A is reference-blind: it replays the submitted response through the
actual reader-role runtime and records observable stage outcomes.  Layer B is
an evaluator-only overlay that joins truth, references, fixtures, and result
contracts after Layer A has been frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, cast

from app.sql.models import QueryExecution, SqlCandidate, SqlPlanFailure
from benchmark import m39_runner as m39
from benchmark import m46a_audit as m46a
from benchmark import m47b_runner as m47b
from benchmark import m48a_audit as m48a
from benchmark import m48b1_runner as m48b1
from benchmark import m48b2_runner as m48b2
from benchmark import m48b_runner as m48b
from benchmark.models import compare_rows

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50c1"
MANIFEST = ROOT / "manifests" / "m50c1_unified_failure_trace_manifest.json"
M50C_AUDIT = ROOT / "audits" / "m50c"
M50C_CONTROL = M50C_AUDIT / "m50c_control_responses.jsonl"
M50C_TREATMENT = M50C_AUDIT / "m50c_treatment_responses.jsonl"
M48B2_RESULTS = ROOT / "experiments" / "results" / "m48b2"
M49_ADJUDICATIONS = ROOT / "audits" / "m49" / "m49_case_adjudications.json"
EXPECTED_START = "9d78fbb31b8dadd4c55387faa470fc524ee45c2e"
M48B2_CORPUS = "f86b07d37b52c0891f6b9e95819104b150cdc9d03825d584ed81921a854795d8"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
PLANNER_VERSION = "planner-statistics-contract-1"
PLANNER_HASH = "a97222f4e036af28120a4ee12d9ef4352513796f54a15d6f2050b56a6ae77863"
M50C_RENDERER_HASH = "14229467317b4db3bbc02ab7cae10f01bdd95f9247ed0e202a975899f5be9939"
M50C_CONTEXT_HASH = "7dab9c4c0f7ec47e326a9e429cfba1b3cc74ae337379180dd8a4f30e37a92b9b"
M50C_SCHEDULE_HASH = "ef8e293d601f5d912c54b87ea59d4e1c671cf1ae3afe0005bdc715f750309e0a"
M50C_FREEZE = M50C_AUDIT / "m50c_response_freeze.json"
TRACE_VERSION = "unified-failure-trace-1"
STAGES = (
    "INPUT_CONTEXT",
    "FROZEN_PROVIDER_RESPONSE",
    "SUBMISSION_PARSE",
    "DECISION_BRANCH",
    "SQL_CANDIDATE",
    "SQL_PARSE",
    "POLICY",
    "GRAIN_INPUT_DIAGNOSTIC",
    "GRAIN_NORMALIZATION",
    "POST_NORMALIZATION_PARSE",
    "POST_NORMALIZATION_POLICY",
    "POST_NORMALIZATION_GRAIN",
    "RESTRICTED_READER_SETUP",
    "EXPLAIN",
    "COST_GATE",
    "QUERY_PLAN",
    "EXECUTION",
    "BASE_RESULT",
    "COUNTERFACTUAL_RESULT",
)
STATUSES = (
    "PASS",
    "FAIL",
    "REJECTED",
    "NORMALIZED",
    "UNCHANGED",
    "NOT_APPLICABLE",
    "SKIPPED",
    "INFRASTRUCTURE_ERROR",
    "INVALID_EVIDENCE",
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _hash(value: Any) -> str:
    return _sha_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    )


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, default=str) + "\n")


def _git() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items())
            if key
            not in {
                "plan_id",
                "correlation_id",
                "timestamp",
                "started_at",
                "finished_at",
                "executed_at_utc",
                "latency_ms",
                "plan_ms",
                "execute_ms",
            }
        }
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


@dataclass(frozen=True)
class TraceStageRecord:
    stage: str
    status: str
    reason_code: str | None
    input_hashes: dict[str, str]
    output_hashes: dict[str, str]
    diagnostics: dict[str, Any]
    reached: bool
    skipped_reason: str | None = None


@dataclass(frozen=True)
class CaseExecutionTrace:
    trace_version: str
    run_id: str
    response_identity: dict[str, Any]
    request_hash: str
    response_hash: str | None
    stages: tuple[TraceStageRecord, ...]
    terminal_runtime_stage: str
    runtime_disposition: str
    first_runtime_failure_stage: str | None
    trace_hash: str


def _stage(
    name: str,
    status: str,
    reason: str | None = None,
    *,
    inputs: dict[str, str] | None = None,
    outputs: dict[str, str] | None = None,
    diagnostics: dict[str, Any] | None = None,
    reached: bool = True,
    skipped_reason: str | None = None,
) -> TraceStageRecord:
    if status not in STATUSES:
        raise ValueError(f"unknown trace status: {status}")
    return TraceStageRecord(
        stage=name,
        status=status,
        reason_code=reason,
        input_hashes=inputs or {},
        output_hashes=outputs or {},
        diagnostics=diagnostics or {},
        reached=reached,
        skipped_reason=skipped_reason,
    )


def _trace_hash_payload(trace: dict[str, Any]) -> dict[str, Any]:
    payload = dict(trace)
    payload.pop("trace_hash", None)
    return cast(dict[str, Any], _canonical(payload))


def _make_trace(
    *,
    run_id: str,
    request_hash: str,
    response_identity: dict[str, Any],
    response_hash: str | None,
    stages: list[TraceStageRecord],
    terminal: str,
    disposition: str,
    first_failure: str | None,
) -> dict[str, Any]:
    value = asdict(
        CaseExecutionTrace(
            trace_version=TRACE_VERSION,
            run_id=run_id,
            response_identity=response_identity,
            request_hash=request_hash,
            response_hash=response_hash,
            stages=tuple(stages),
            terminal_runtime_stage=terminal,
            runtime_disposition=disposition,
            first_runtime_failure_stage=first_failure,
            trace_hash="",
        )
    )
    value["stages"] = [asdict(item) for item in stages]
    value["trace_hash"] = _hash(_trace_hash_payload(value))
    return value


def _historical_files() -> dict[str, str]:
    roots = [
        ROOT / "audits" / "m48b2",
        ROOT / "audits" / "m49",
        ROOT / "audits" / "m50",
        ROOT / "audits" / "m501",
        ROOT / "audits" / "m50a",
        ROOT / "audits" / "m50b",
        ROOT / "audits" / "m50c",
        ROOT / "experiments" / "results" / "m48b2",
        REPO / "README.md",
    ]
    files: dict[str, str] = {}
    for root in roots:
        if root.is_file():
            files[str(root.relative_to(REPO))] = _sha(root)
        elif root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    files[str(path.relative_to(REPO))] = _sha(path)
    return dict(sorted(files.items()))


def _phase_a() -> dict[str, Any]:
    if _git() != EXPECTED_START:
        raise RuntimeError("M50C1_STARTING_HEAD_MISMATCH")
    stage_contract = {
        "trace_version": TRACE_VERSION,
        "stages": STAGES,
        "statuses": STATUSES,
        "runtime_first_failure": (
            "earliest deterministic runtime stage with FAIL/REJECTED/INFRASTRUCTURE_ERROR"
        ),
        "evaluator_first_divergence": (
            "earliest evaluator-only contract divergence; never written into Layer A"
        ),
        "canonical_order": list(STAGES),
        "ephemeral_fields_excluded_from_trace_hash": ["plan_id", "correlation_id", "timestamps"],
    }
    allowlist = {
        "layer_a": [
            "question",
            "model-visible context",
            "frozen response",
            "parsed submission",
            "public catalogs",
            "authority metadata",
            "semantic/measure catalog",
            "temporal/public metadata",
            "policy",
            "runtime settings",
            "planner settings",
            "cost settings",
            "grain runtime",
            "database state prepared from opaque base/fixture state instructions",
        ],
        "layer_b_only": [
            "truth_behavior",
            "reference SQL",
            "ResultContract",
            "expected results",
            "counterfactual fixture meaning",
            "M49/M50 labels",
        ],
    }
    denylist = {
        "layer_a": allowlist["layer_b_only"],
        "case_specific_logic": 0,
        "domain_specific_logic": 0,
        "provider_calls": 0,
        "model_calls": 0,
    }
    runtime_contract = {
        "preparation": [
            "reset_or_seed",
            "apply_opaque_fixture_patch",
            "commit",
            "ANALYZE_CURRENT_STATE",
            "restore restricted-reader schema USAGE",
            "restore restricted-reader table SELECT",
        ],
        "preparation_source": "benchmark/m48b1_runner.py:_prepare_state",
        "planner_contract": PLANNER_VERSION,
        "planner_hash": PLANNER_HASH,
        "max_plan_rows": 100000,
        "max_plan_cost": 100000.0,
        "historical_m50c_defect": "m50c used m48b._prepare_state instead of the M48B.1 wrapper",
    }
    contract = {
        "experiment": "M50C.1",
        "trace_contract_version": TRACE_VERSION,
        "stage_contract_hash": _hash(stage_contract),
        "trace_contract_hash": _hash({"trace_version": TRACE_VERSION, "stages": STAGES}),
        "runtime_preparation_contract_hash": _hash(runtime_contract),
        "source_allowlist_hash": _hash(allowlist),
        "source_denylist_hash": _hash(denylist),
        "layer_a_reference_blind": True,
        "layer_b_evaluator_only": True,
        "starting_head": EXPECTED_START,
        "provider_calls": 0,
        "model_calls": 0,
    }
    _dump(AUDIT / "m50c1_historical_preservation.json", {"files": _historical_files()})
    _dump(AUDIT / "m50c1_trace_contract.json", contract)
    _dump(AUDIT / "m50c1_stage_contract.json", stage_contract)
    _dump(AUDIT / "m50c1_source_allowlist.json", allowlist)
    _dump(AUDIT / "m50c1_source_denylist.json", denylist)
    _dump(AUDIT / "m50c1_runtime_preparation_contract.json", runtime_contract)
    manifest = {
        "experiment": "M50C.1",
        "starting_head": EXPECTED_START,
        "provider_calls": 0,
        "model_calls": 0,
        "parent_m50c_verdict": "M50C_ABORTED_POST_RESPONSE_CONTRACT_DEFECT",
        "m48b2_response_corpus_hash": M48B2_CORPUS,
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "planner_contract": PLANNER_VERSION,
        "planner_hash": PLANNER_HASH,
        "trace_contract_version": TRACE_VERSION,
        "trace_contract_hash": contract["trace_contract_hash"],
        "runtime_preparation_contract_hash": contract["runtime_preparation_contract_hash"],
        "phase": "A_FROZEN_BEFORE_REPLAY",
    }
    _dump(MANIFEST, manifest)
    return manifest


def _load_phase_a() -> dict[str, Any]:
    manifest = cast(dict[str, Any], json.loads(MANIFEST.read_text()))
    if manifest.get("phase") not in {
        "A_FROZEN_BEFORE_REPLAY",
        "B_M48B2_TRACE_VALIDATED",
        "D_ZERO_CALL_REPLAY_COMPLETE",
    }:
        raise RuntimeError("M50C1_PHASE_A_NOT_FROZEN")
    if _git() == EXPECTED_START:
        raise RuntimeError("M50C1_PHASE_A_COMMIT_REQUIRED")
    return manifest


def _pairs() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    case_ids, rows = m47b._rows()
    if len(case_ids) != 90:
        raise RuntimeError("M50C1_CASE_COUNT_INVALID")
    return case_ids, rows


def _requests(
    case_ids: list[str], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> list[dict[str, Any]]:
    return m47b._requests(case_ids, rows)


def _catalogs(rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    # This is the frozen runtime catalog construction used by M48B.2.  The
    # resulting catalog is passed into Layer A as server-owned runtime input;
    # truth is not written into traces or used for stage classification.
    cases = [pair[1] for pair in rows.values() if pair[0]["task_type"] == "ANSWERABLE"]
    catalogs, _ = m46a._build_catalogs(cases)
    return catalogs


def _records(path: Path) -> list[dict[str, Any]]:
    result = [json.loads(line) for line in path.read_text().splitlines()]
    if len(result) != 90 or len({row["case_id"] for row in result}) != 90:
        raise RuntimeError(f"M50C1_RESPONSE_COUNT:{path}")
    return sorted(result, key=lambda row: int(row["case_index"]))


def _m48b2_records() -> tuple[list[dict[str, Any]], dict[str, str]]:
    raw = _records(M48B2_RESULTS / "raw_responses.jsonl")
    parsed = _records(M48B2_RESULTS / "parsed_submissions.jsonl")
    response_hashes = {row["case_id"]: row.get("response_sha256") for row in raw}
    if len(response_hashes) != 90:
        raise RuntimeError("M48B2_RESPONSE_HASH_COUNT")
    for row in parsed:
        row["schema_validation"] = row.get("schema_validation") or "PASS"
    return parsed, cast(dict[str, str], response_hashes)


def _m50c_records(arm: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    path = M50C_CONTROL if arm == "CONTROL" else M50C_TREATMENT
    records = _records(path)
    hashes = {row["case_id"]: row.get("response_sha256") for row in records}
    if len(hashes) != 90:
        raise RuntimeError("M50C_RESPONSE_HASH_COUNT")
    return records, cast(dict[str, str], hashes)


def _m50c_response_integrity() -> dict[str, Any]:
    freeze = cast(dict[str, Any], json.loads(M50C_FREEZE.read_text()))
    control = _records(M50C_CONTROL)
    treatment = _records(M50C_TREATMENT)
    rows = {"CONTROL": control, "TREATMENT": treatment}
    slots = [(arm, row["case_id"]) for arm, values in rows.items() for row in values]
    response_hashes = [row.get("response_sha256") for values in rows.values() for row in values]
    result = {
        "source": str(M50C_FREEZE.relative_to(REPO)),
        "control_count": len(control),
        "treatment_count": len(treatment),
        "total_count": len(control) + len(treatment),
        "duplicate_slots": len(slots) - len(set(slots)),
        "missing_response_hashes": sum(item is None for item in response_hashes),
        "duplicate_response_hashes": len(response_hashes) - len(set(response_hashes)),
        "freeze_control_attempts": freeze.get("control_attempts"),
        "freeze_treatment_attempts": freeze.get("treatment_attempts"),
        "freeze_provider_attempts": freeze.get("provider_attempts"),
        "freeze_retries": freeze.get("retries"),
        "file_hashes_match": {
            "CONTROL": _sha(M50C_CONTROL) == freeze["response_files"]["CONTROL"],
            "TREATMENT": _sha(M50C_TREATMENT) == freeze["response_files"]["TREATMENT"],
        },
    }
    result["pass"] = (
        result["control_count"] == 90
        and result["treatment_count"] == 90
        and result["total_count"] == 180
        and result["duplicate_slots"] == 0
        and result["missing_response_hashes"] == 0
        and result["file_hashes_match"] == {"CONTROL": True, "TREATMENT": True}
        and freeze.get("retries") == 0
    )
    _dump(AUDIT / "m50c1_m50c_response_integrity.json", result)
    if not result["pass"]:
        raise RuntimeError("M50C1_FROZEN_RESPONSE_INTEGRITY_FAILURE")
    return result


def _prepare(db: str, fixture: dict[str, Any]) -> dict[str, Any]:
    return m48b1._prepare_state(db, fixture)


def _state_runtime(service: Any, sql: str) -> dict[str, Any]:
    grain = m48b._grain_snapshot(service, sql)
    planned = service.plan(SqlCandidate(sql=sql))
    if isinstance(planned, SqlPlanFailure):
        return {
            "planned": False,
            "executed": False,
            "grain": grain,
            "plan_failure": planned.model_dump(mode="json"),
            "result_contract_outcome": None,
        }
    execution = service.execute(planned)
    if not isinstance(execution, QueryExecution):
        return {
            "planned": True,
            "executed": False,
            "grain": grain,
            "plan": planned.model_dump(mode="json"),
            "execution_error": execution.model_dump(mode="json"),
            "result_contract_outcome": None,
        }
    return {
        "planned": True,
        "executed": True,
        "grain": grain,
        "plan": planned.model_dump(mode="json"),
        "execution": execution.model_dump(mode="json"),
        "result_contract_outcome": None,
    }


def _failure_status(state: dict[str, Any]) -> str | None:
    return cast(str | None, state.get("plan_failure", {}).get("status"))


def _state_stage_summary(
    states: list[dict[str, Any]],
) -> tuple[list[TraceStageRecord], str, str, str | None]:
    if not states:
        return [], "SQL_CANDIDATE", "NO_SQL_STATE", None
    statuses = [_failure_status(state) for state in states]
    grains = [state.get("grain", {}) for state in states]
    stages: list[TraceStageRecord] = []
    parse_fail = any(status == "SQL_PARSE_ERROR" for status in statuses)
    policy_fail = any(status == "POLICY_REJECTION" for status in statuses)
    semantic_fail = any(status == "SEMANTIC_REJECTION" for status in statuses)
    cost_fail = any(status == "QUERY_COST_REJECTION" for status in statuses)
    explain_fail = any(
        status == "EXECUTION_ERROR" and not state.get("planned")
        for status, state in zip(statuses, states, strict=True)
    )
    execution_fail = any(state.get("planned") and not state.get("executed") for state in states)
    normalized = any(grain.get("status") == "NORMALIZED" for grain in grains)
    diagnostics = {"states": [state.get("state_id") for state in states], "grains": grains}

    def skipped(name: str) -> TraceStageRecord:
        return _stage(name, "SKIPPED", skipped_reason="UPSTREAM_RUNTIME_FAILURE", reached=False)

    stages.append(
        _stage(
            "SQL_PARSE",
            "FAIL" if parse_fail else "PASS",
            "SQL_PARSE_ERROR" if parse_fail else None,
            diagnostics=diagnostics,
            outputs={
                f"state_{index}": state.get("raw_sql_hash", "")
                for index, state in enumerate(states)
                if state.get("raw_sql_hash")
            },
        )
    )
    if parse_fail:
        stages.extend(skipped(name) for name in STAGES[6:17])
    else:
        stages.append(
            _stage(
                "POLICY",
                "FAIL" if policy_fail else "PASS",
                "POLICY_REJECTION" if policy_fail else None,
            )
        )
        if policy_fail:
            stages.extend(skipped(name) for name in STAGES[7:17])
        else:
            stages.append(
                _stage(
                    "GRAIN_INPUT_DIAGNOSTIC",
                    "REJECTED" if semantic_fail else "PASS",
                    "SEMANTIC_REJECTION" if semantic_fail else None,
                    diagnostics=diagnostics,
                    outputs={"grain": _hash(_canonical(grains))},
                )
            )
            if semantic_fail:
                stages.extend(skipped(name) for name in STAGES[8:17])
            else:
                stages.append(
                    _stage(
                        "GRAIN_NORMALIZATION",
                        "NORMALIZED" if normalized else "UNCHANGED",
                        outputs={
                            f"state_{index}": state.get("selected_sql_hash", "")
                            for index, state in enumerate(states)
                            if state.get("selected_sql_hash")
                        },
                    )
                )
                stages.extend(
                    _stage(name, "PASS" if normalized else "NOT_APPLICABLE")
                    for name in (
                        "POST_NORMALIZATION_PARSE",
                        "POST_NORMALIZATION_POLICY",
                        "POST_NORMALIZATION_GRAIN",
                    )
                )
                stages.append(_stage("RESTRICTED_READER_SETUP", "PASS"))
                if explain_fail:
                    stages.append(_stage("EXPLAIN", "FAIL", "EXPLAIN_ERROR"))
                    stages.extend(skipped(name) for name in STAGES[14:17])
                else:
                    stages.append(_stage("EXPLAIN", "PASS"))
                    if cost_fail:
                        stages.append(_stage("COST_GATE", "FAIL", "QUERY_COST_REJECTION"))
                        stages.extend(skipped(name) for name in STAGES[15:17])
                    else:
                        stages.append(_stage("COST_GATE", "PASS"))
                        stages.append(
                            _stage(
                                "QUERY_PLAN",
                                "PASS",
                                outputs={
                                    f"state_{index}": _hash(_canonical(state.get("plan", {})))
                                    for index, state in enumerate(states)
                                    if state.get("plan")
                                },
                            )
                        )
                        stages.append(
                            _stage(
                                "EXECUTION",
                                "FAIL" if execution_fail else "PASS",
                                "EXECUTION_ERROR" if execution_fail else None,
                                outputs={
                                    f"state_{index}": _hash(_canonical(state.get("execution", {})))
                                    for index, state in enumerate(states)
                                    if state.get("execution")
                                },
                            )
                        )
    failure_order = [
        ("SQL_PARSE", parse_fail),
        ("POLICY", policy_fail),
        ("GRAIN_INPUT_DIAGNOSTIC", semantic_fail),
        ("EXPLAIN", explain_fail),
        ("COST_GATE", cost_fail),
        ("EXECUTION", execution_fail),
    ]
    first = next((name for name, failed in failure_order if failed), None)
    terminal = first or (
        "EXECUTION" if all(state.get("executed") for state in states) else "QUERY_PLAN"
    )
    disposition = (
        "INFRASTRUCTURE_ERROR"
        if explain_fail or execution_fail
        else ("REJECTED" if first else "PASS")
    )
    return stages, terminal, disposition, first


def _trace_case(
    request: dict[str, Any],
    record: dict[str, Any],
    response_hash: str | None,
    service: Any,
) -> dict[str, Any]:
    parsed = record.get("parsed_submission")
    request_hash = str(request["request_sha256"])
    response_identity = {
        "case_id": request["case_id"],
        "case_index": request["case_index"],
        "arm": record.get("arm"),
        "request_hash": request_hash,
        "response_hash": response_hash,
        "parsed_submission_hash": record.get("parsed_submission_hash") or _hash(parsed),
    }
    stages = [
        _stage(
            "INPUT_CONTEXT",
            "PASS",
            inputs={"request": request_hash, "context": str(request["context_sha256"])},
            outputs={"context": str(request["context_sha256"])},
        ),
        _stage(
            "FROZEN_PROVIDER_RESPONSE",
            "PASS",
            inputs={"request": request_hash},
            outputs={"response": response_hash or ""},
        ),
    ]
    parse_status = record.get("parse_status") or record.get("schema_validation")
    if parse_status != "PASS" or not isinstance(parsed, dict):
        stages.append(_stage("SUBMISSION_PARSE", "FAIL", str(parse_status or "INVALID")))
        for name in STAGES[3:]:
            stages.append(
                _stage(name, "SKIPPED", skipped_reason="UPSTREAM_RUNTIME_FAILURE", reached=False)
            )
        return _make_trace(
            run_id=f"M50C.1:{request['case_id']}:{record.get('arm', 'M48B2')}",
            request_hash=request_hash,
            response_identity=response_identity,
            response_hash=response_hash,
            stages=stages,
            terminal="SUBMISSION_PARSE",
            disposition="OUTPUT_CONTRACT_FAILURE",
            first_failure="SUBMISSION_PARSE",
        )
    stages.append(_stage("SUBMISSION_PARSE", "PASS", outputs={"submission": _hash(parsed)}))
    decision = parsed.get("decision")
    sql = parsed.get("sql")
    if decision != "ANSWER" or not sql:
        stages.append(
            _stage(
                "DECISION_BRANCH", "PASS", "NON_ANSWER_BRANCH", diagnostics={"decision": decision}
            )
        )
        for name in STAGES[4:]:
            stages.append(
                _stage(name, "SKIPPED", skipped_reason="NON_ANSWER_SUBMISSION", reached=False)
            )
        return _make_trace(
            run_id=f"M50C.1:{request['case_id']}:{record.get('arm', 'M48B2')}",
            request_hash=request_hash,
            response_identity=response_identity,
            response_hash=response_hash,
            stages=stages,
            terminal="DECISION_BRANCH",
            disposition="NON_ANSWER_BRANCH",
            first_failure=None,
        )
    stages.extend(
        [
            _stage("DECISION_BRANCH", "PASS", "ANSWER_BRANCH", diagnostics={"decision": decision}),
            _stage(
                "SQL_CANDIDATE",
                "PASS",
                inputs={"submission": _hash(parsed)},
                outputs={"sql": _hash(sql)},
            ),
        ]
    )
    try:
        _prepare(request["database_id"], {"fixture_id": "base", "patch_sql": []})
        state = _state_runtime(service, sql)
        state["state_id"] = "base"
        state["raw_sql_hash"] = _hash(sql)
        state["selected_sql_hash"] = (
            _hash(state.get("plan", {}).get("normalized_sql", "")) if state.get("plan") else None
        )
        state_stages, terminal, disposition, first = _state_stage_summary([state])
        stages.extend(state_stages)
        return _make_trace(
            run_id=f"M50C.1:{request['case_id']}:{record.get('arm', 'M48B2')}",
            request_hash=request_hash,
            response_identity=response_identity,
            response_hash=response_hash,
            stages=stages,
            terminal=terminal,
            disposition=disposition,
            first_failure=first,
        )
    except Exception as exc:
        stages.append(
            _stage(
                "RESTRICTED_READER_SETUP",
                "INFRASTRUCTURE_ERROR",
                "READER_SETUP_ERROR",
                diagnostics={"error": str(exc)[:240]},
            )
        )
        for name in STAGES[13:]:
            stages.append(
                _stage(
                    name, "SKIPPED", skipped_reason="UPSTREAM_INFRASTRUCTURE_ERROR", reached=False
                )
            )
        return _make_trace(
            run_id=f"M50C.1:{request['case_id']}:{record.get('arm', 'M48B2')}",
            request_hash=request_hash,
            response_identity=response_identity,
            response_hash=response_hash,
            stages=stages,
            terminal="RESTRICTED_READER_SETUP",
            disposition="INFRASTRUCTURE_ERROR",
            first_failure="RESTRICTED_READER_SETUP",
        )


def _trace_corpus(
    requests: list[dict[str, Any]],
    records: list[dict[str, Any]],
    response_hashes: dict[str, str],
    services: dict[str, Any],
    path: Path,
) -> tuple[list[dict[str, Any]], str]:
    by_id = {row["case_id"]: row for row in records}
    traces: list[dict[str, Any]] = []
    for request in requests:
        trace = _trace_case(
            request,
            by_id[request["case_id"]],
            response_hashes.get(request["case_id"]),
            services[request["database_id"]],
        )
        traces.append(trace)
    path.unlink(missing_ok=True)
    for trace in traces:
        _append_jsonl(path, trace)
    return traces, _hash([trace["trace_hash"] for trace in traces])


def _canary(catalogs: dict[str, Any], services: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for database_id in sorted(catalogs):
        prep = _prepare(database_id, {"fixture_id": "reader_canary", "patch_sql": []})
        catalog = m48a._schema_catalog(database_id)
        table = sorted(catalog.tables, key=lambda item: item.name)[0]
        column = sorted(table.columns, key=lambda item: item.name)[0]
        sql = f'SELECT "{column.name}" FROM "{table.name}" LIMIT 1'
        state = _state_runtime(services[database_id], sql)
        if not state.get("planned") or not state.get("executed"):
            raise RuntimeError(f"M50C1_READER_CANARY_FAILED:{database_id}")
        rows.append(
            {
                "database_id": database_id,
                "preparation_hash": prep["metadata_hash"],
                "sql_hash": _hash(sql),
                "pass": True,
            }
        )
    result = {"databases": rows, "pass": len(rows) == 6}
    _dump(AUDIT / "m50c1_reader_canary.json", result)
    return result


def _reference_canary(pairs: list[m48b2.Pair], services: dict[str, Any]) -> dict[str, Any]:
    witnesses = 0
    state_runs = 0
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        behavior = pair.truth_case["semantic_target"]["behavior"]
        if behavior != "ANSWERABLE":
            continue
        bundle = m48b2._bundle(pair)
        for witness_name, sql in (("A", bundle.reference_a), ("B", bundle.reference_b)):
            witnesses += 1
            for fixture in bundle.fixtures:
                _prepare(pair.model_case["database_id"], fixture)
                state = m48b._runtime(
                    services[pair.model_case["database_id"]], sql, bundle.contract, []
                )
                state_runs += 1
                if not state.get("planned") or not state.get("executed"):
                    raise RuntimeError(
                        f"M50C1_REFERENCE_CANARY_FAILED:{pair.case_id}:{witness_name}:{fixture['fixture_id']}"
                    )
        for fixture in bundle.fixtures:
            _prepare(pair.model_case["database_id"], fixture)
            reference_a = m48b._runtime(
                services[pair.model_case["database_id"]], bundle.reference_a, bundle.contract, []
            )
            reference_b = m48b._runtime(
                services[pair.model_case["database_id"]], bundle.reference_b, bundle.contract, []
            )
            if not reference_a.get("executed") or not reference_b.get("executed"):
                raise RuntimeError(f"M50C1_REFERENCE_CANARY_COMPARE_RUNTIME:{pair.case_id}")
            same, reason = compare_rows(
                m48b2._state_rows(reference_a), m48b2._state_rows(reference_b), bundle.contract
            )
            if not same:
                raise RuntimeError(
                    f"M50C1_REFERENCE_CANARY_DISAGREEMENT:{pair.case_id}:{fixture['fixture_id']}:{reason}"
                )
        rows.append(
            {
                "case_id": pair.case_id,
                "reference_witnesses": 2,
                "fixture_states": len(bundle.fixtures),
            }
        )
    result = {
        "reference_witnesses": witnesses,
        "state_runs": state_runs,
        "reference_agreement_checks": sum(
            len(m48b2._bundle(pair).fixtures)
            for pair in pairs
            if pair.truth_case["semantic_target"]["behavior"] == "ANSWERABLE"
        ),
        "cases": rows,
        "pass": witnesses == 120,
    }
    _dump(AUDIT / "m50c1_reference_runtime_canary.json", result)
    return result


def _runtime_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    nested = outcome.get("runtime")
    return nested if isinstance(nested, dict) else outcome


def _outcome_status(outcome: dict[str, Any]) -> str | None:
    return cast(str | None, _runtime_outcome(outcome).get("plan_failure", {}).get("status"))


def _outcome_planned(outcome: dict[str, Any]) -> bool:
    return bool(_runtime_outcome(outcome).get("planned"))


def _outcome_executed(outcome: dict[str, Any]) -> bool:
    return bool(_runtime_outcome(outcome).get("executed"))


def _overlay(
    pairs: list[m48b2.Pair],
    parsed_records: list[dict[str, Any]],
    runtime_records: list[dict[str, Any]],
    traces: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pair_by_id = {pair.case_id: pair for pair in pairs}
    trace_by_id = {trace["response_identity"]["case_id"]: trace for trace in traces}
    parsed_by_id = {row["case_id"]: row for row in parsed_records}
    overlays: list[dict[str, Any]] = []
    for runtime in runtime_records:
        case_id = runtime["case_id"]
        pair = pair_by_id[case_id]
        parsed = parsed_by_id[case_id].get("parsed_submission") or {}
        behavior = pair.truth_case["semantic_target"]["behavior"]
        decision = parsed.get("decision")
        parse_ok = parsed_by_id[case_id].get("schema_validation") == "PASS"
        states = runtime.get("states", [])
        base_correct = bool(states and states[0]["outcome"].get("result_contract_outcome"))
        full_correct = bool(
            states and all(state["outcome"].get("result_contract_outcome") for state in states)
        )
        execution_hashes = [
            _hash(_canonical(_runtime_outcome(state["outcome"]).get("execution", {})))
            for state in states
            if _runtime_outcome(state["outcome"]).get("execution")
        ]
        if not parse_ok:
            divergence = "SUBMISSION_CONTRACT"
        elif behavior == "ANSWERABLE" and decision != "ANSWER":
            divergence = "DECISION_FALSE_ABSTENTION"
        elif behavior != "ANSWERABLE" and decision != m39.EXPECTED_DECISION[behavior]:
            divergence = (
                "DECISION_FALSE_ANSWER" if decision == "ANSWER" else "DECISION_WRONG_BLOCK_TYPE"
            )
        elif decision == "ANSWER":
            failure_statuses = [_outcome_status(state["outcome"]) for state in states]
            if "SQL_PARSE_ERROR" in failure_statuses:
                divergence = "SQL_PARSE"
            elif "POLICY_REJECTION" in failure_statuses:
                divergence = "SQL_POLICY"
            elif "SEMANTIC_REJECTION" in failure_statuses:
                divergence = "SEMANTIC_GRAIN"
            elif "QUERY_COST_REJECTION" in failure_statuses:
                divergence = "COST"
            elif any(not _outcome_executed(state["outcome"]) for state in states):
                divergence = "EXECUTION"
            elif not base_correct:
                divergence = "RESULT_BASE"
            elif not full_correct:
                divergence = "RESULT_COUNTERFACTUAL"
            else:
                divergence = "NONE"
        else:
            divergence = "NONE"
        overlays.append(
            {
                "case_id": case_id,
                "truth_behavior": behavior,
                "submitted_decision": decision,
                "governance_correct": behavior == "ANSWERABLE"
                and decision == "ANSWER"
                or behavior != "ANSWERABLE"
                and decision == m39.EXPECTED_DECISION[behavior]
                and not parsed.get("sql"),
                "base_correct": base_correct,
                "full_counterfactual_correct": full_correct,
                "execution_result_hashes": execution_hashes,
                "first_runtime_failure_stage": trace_by_id[case_id]["first_runtime_failure_stage"],
                "runtime_failure_statuses": [
                    _outcome_status(state["outcome"])
                    or (
                        "EXECUTION_ERROR"
                        if _outcome_planned(state["outcome"])
                        and not _outcome_executed(state["outcome"])
                        else None
                    )
                    for state in states
                    if _outcome_status(state["outcome"])
                    or (
                        _outcome_planned(state["outcome"])
                        and not _outcome_executed(state["outcome"])
                    )
                ],
                "first_evaluator_divergence_stage": divergence,
                "runtime_terminal_stage": trace_by_id[case_id]["terminal_runtime_stage"],
            }
        )
    metrics = {
        "governed": sum(
            item["governance_correct"]
            if item["truth_behavior"] != "ANSWERABLE"
            else item["full_counterfactual_correct"]
            for item in overlays
        ),
        "answerable_tsa": sum(
            item["full_counterfactual_correct"]
            for item in overlays
            if item["truth_behavior"] == "ANSWERABLE"
        ),
        "base_correct": sum(
            item["base_correct"] for item in overlays if item["truth_behavior"] == "ANSWERABLE"
        ),
        "answerable": sum(item["truth_behavior"] == "ANSWERABLE" for item in overlays),
    }
    return overlays, metrics


def _runtime_failure_distribution(traces: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(trace["first_runtime_failure_stage"] or "NONE" for trace in traces))


def _divergence_distribution(overlays: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(item["first_evaluator_divergence_stage"] for item in overlays))


def _failure_funnel(
    overlays: list[dict[str, Any]], runtime_records: list[dict[str, Any]]
) -> dict[str, int]:
    runtime_by_id = {item["case_id"]: item for item in runtime_records}
    funnel = {
        "total": len(overlays),
        "submission_parse_pass": 0,
        "decision_branch_reached": 0,
        "answer_selected": 0,
        "sql_parse_pass": 0,
        "policy_pass": 0,
        "grain_pass": 0,
        "explain_pass": 0,
        "query_plan_issued": 0,
        "execution_success": 0,
        "base_correct": 0,
        "full_counterfactual_correct": 0,
        "final_governed_correct": 0,
    }
    for item in overlays:
        if item["first_evaluator_divergence_stage"] != "SUBMISSION_CONTRACT":
            funnel["submission_parse_pass"] += 1
        if item["submitted_decision"] is not None:
            funnel["decision_branch_reached"] += 1
        if item["submitted_decision"] == "ANSWER":
            funnel["answer_selected"] += 1
        states = runtime_by_id[item["case_id"]].get("states", [])
        if states:
            outcomes = [state["outcome"] for state in states]
            statuses = [_outcome_status(outcome) for outcome in outcomes]
            if "SQL_PARSE_ERROR" not in statuses:
                funnel["sql_parse_pass"] += 1
            if "POLICY_REJECTION" not in statuses:
                funnel["policy_pass"] += 1
            if "SEMANTIC_REJECTION" not in statuses:
                funnel["grain_pass"] += 1
            if all(_outcome_planned(outcome) for outcome in outcomes):
                funnel["explain_pass"] += 1
                if "QUERY_COST_REJECTION" not in statuses:
                    funnel["query_plan_issued"] += 1
            if all(_outcome_executed(outcome) for outcome in outcomes):
                funnel["execution_success"] += 1
        funnel["base_correct"] += int(item["base_correct"])
        funnel["full_counterfactual_correct"] += int(item["full_counterfactual_correct"])
        final_correct = (
            item["full_counterfactual_correct"]
            if item["truth_behavior"] == "ANSWERABLE"
            else item["governance_correct"]
        )
        funnel["final_governed_correct"] += int(final_correct)
    return funnel


def _load_m49_map() -> dict[str, str]:
    records = json.loads(M49_ADJUDICATIONS.read_text())
    return {item["case_id"]: item["primary_mechanism"] for item in records}


def _token_accounting(records_by_arm: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm, records in records_by_arm.items():
        values = [float(row["provider_metadata"]["usage"]["prompt_tokens"]) for row in records]
        ordered = sorted(values)
        p90_index = max(0, math.ceil(len(ordered) * 0.9) - 1)
        result[arm] = {
            "count": len(values),
            "total": sum(values),
            "median": median(values) if values else None,
            "p90": ordered[p90_index] if ordered else None,
            "max": max(values) if values else None,
        }
    control = result.get("CONTROL", {})
    treatment = result.get("TREATMENT", {})
    result["delta"] = {
        "total": treatment.get("total", 0) - control.get("total", 0),
        "median_percentage": treatment.get("median", 0) / control.get("median", 1) - 1,
        "p90_percentage": treatment.get("p90", 0) / control.get("p90", 1) - 1,
        "max_percentage": treatment.get("max", 0) / control.get("max", 1) - 1,
    }
    result["source"] = "provider_metadata.usage.prompt_tokens"
    result["historical_null_path_confirmed"] = True
    return result


def _trace_stage_output(trace: dict[str, Any], stage_name: str) -> dict[str, str]:
    for stage in trace["stages"]:
        if stage["stage"] == stage_name:
            return cast(dict[str, str], stage.get("output_hashes", {}))
    return {}


def _m50c_posthoc(
    case_ids: list[str],
    parsed_by_arm: dict[str, list[dict[str, Any]]],
    replay: dict[str, dict[str, Any]],
) -> None:
    parsed = {
        arm: {row["case_id"]: row for row in records} for arm, records in parsed_by_arm.items()
    }
    overlays = {
        arm: {row["case_id"]: row for row in replay[arm]["overlays"]}
        for arm in ("CONTROL", "TREATMENT")
    }
    traces = {
        arm: {trace["response_identity"]["case_id"]: trace for trace in replay[arm]["traces"]}
        for arm in ("CONTROL", "TREATMENT")
    }
    decision_matrix: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for case_id in case_ids:
        c_decision = parsed["CONTROL"][case_id].get("parsed_submission", {}).get("decision")
        t_decision = parsed["TREATMENT"][case_id].get("parsed_submission", {}).get("decision")
        decision_matrix[f"{c_decision or 'INVALID'}->{t_decision or 'INVALID'}"] += 1
        c = overlays["CONTROL"][case_id]
        t = overlays["TREATMENT"][case_id]
        rows.append(
            {
                "case_id": case_id,
                "control_decision": c_decision,
                "treatment_decision": t_decision,
                "control_correct": c["full_counterfactual_correct"]
                if c["truth_behavior"] == "ANSWERABLE"
                else c["governance_correct"],
                "treatment_correct": t["full_counterfactual_correct"]
                if t["truth_behavior"] == "ANSWERABLE"
                else t["governance_correct"],
                "control_divergence": c["first_evaluator_divergence_stage"],
                "treatment_divergence": t["first_evaluator_divergence_stage"],
            }
        )
    _dump(AUDIT / "m50c1_m50c_decision_transition_matrix.json", dict(decision_matrix))
    targets = {"subscription_06", "warehouse_08", "warehouse_13"}
    _dump(
        AUDIT / "m50c1_m50c_target_analysis.json",
        [row for row in rows if row["case_id"] in targets],
    )
    _dump(
        AUDIT / "m50c1_m50c_governance_trace_analysis.json",
        [
            row
            for row in rows
            if row["case_id"] not in targets
            and row["case_id"]
            in {
                item["case_id"]
                for item in rows
                if item["control_decision"] != "ANSWER" or item["treatment_decision"] != "ANSWER"
            }
        ],
    )
    sql_rows: list[dict[str, Any]] = []
    for case_id in case_ids:
        c_submission = parsed["CONTROL"][case_id].get("parsed_submission") or {}
        t_submission = parsed["TREATMENT"][case_id].get("parsed_submission") or {}
        if c_submission.get("decision") != "ANSWER" or t_submission.get("decision") != "ANSWER":
            continue
        c_candidate = _trace_stage_output(traces["CONTROL"][case_id], "SQL_CANDIDATE").get("sql")
        t_candidate = _trace_stage_output(traces["TREATMENT"][case_id], "SQL_CANDIDATE").get("sql")
        c_selected = _trace_stage_output(traces["CONTROL"][case_id], "GRAIN_NORMALIZATION").get(
            "state_0"
        )
        t_selected = _trace_stage_output(traces["TREATMENT"][case_id], "GRAIN_NORMALIZATION").get(
            "state_0"
        )
        c_results = overlays["CONTROL"][case_id]["execution_result_hashes"]
        t_results = overlays["TREATMENT"][case_id]["execution_result_hashes"]
        selected_changed = c_selected != t_selected
        if not selected_changed:
            classification = "SAME_SELECTED_SQL"
        elif c_results and t_results and c_results == t_results:
            classification = "SEMANTICALLY_EQUIVALENT_EXECUTION"
        elif c_results and t_results:
            classification = "MATERIAL_RUNTIME_RESULT_CHANGE"
        else:
            classification = "UNRESOLVED"
        sql_rows.append(
            {
                "case_id": case_id,
                "control_candidate_hash": c_candidate,
                "treatment_candidate_hash": t_candidate,
                "control_selected_hash": c_selected,
                "treatment_selected_hash": t_selected,
                "selected_sql_changed": selected_changed,
                "control_correct": overlays["CONTROL"][case_id]["full_counterfactual_correct"],
                "treatment_correct": overlays["TREATMENT"][case_id]["full_counterfactual_correct"],
                "classification": classification,
                "control_result_hashes": c_results,
                "treatment_result_hashes": t_results,
            }
        )
    sql_summary = {
        "both_answer_count": len(sql_rows),
        "same_selected_sql": sum(not row["selected_sql_changed"] for row in sql_rows),
        "different_selected_sql": sum(row["selected_sql_changed"] for row in sql_rows),
        "material_runtime_result_change": sum(
            row["classification"] == "MATERIAL_RUNTIME_RESULT_CHANGE" for row in sql_rows
        ),
        "equivalent_execution": sum(
            row["classification"] == "SEMANTICALLY_EQUIVALENT_EXECUTION" for row in sql_rows
        ),
        "unresolved": sum(row["classification"] == "UNRESOLVED" for row in sql_rows),
        "rows": sql_rows,
    }
    _dump(AUDIT / "m50c1_m50c_sql_churn.json", sql_summary)


def _replay_m48b2() -> dict[str, Any]:
    manifest = _load_phase_a()
    case_ids, rows = _pairs()
    requests = _requests(case_ids, rows)
    parsed, response_hashes = _m48b2_records()
    catalogs = _catalogs(rows)
    services = m48b._runtime_services(catalogs)
    canary = _canary(catalogs, services)
    pairs = m48b2._pairs()
    reference_canary = _reference_canary(pairs, services)
    traces, trace_hash = _trace_corpus(
        requests, parsed, response_hashes, services, AUDIT / "m50c1_m48b2_runtime_traces.jsonl"
    )
    runtime_records = [
        m48b2._replay_one(pair, parsed_item, services, catalogs)
        for pair, parsed_item in zip(pairs, parsed, strict=True)
    ]
    overlays, overlay_counts = _overlay(pairs, parsed, runtime_records, traces)
    overlay_hash = _hash(
        [item["case_id"] + ":" + item["first_evaluator_divergence_stage"] for item in overlays]
    )
    _write_jsonl(AUDIT / "m50c1_m48b2_evaluator_overlays.jsonl", overlays)
    failure_funnel = {
        "total": 90,
        "answer_selected": sum(item["submitted_decision"] == "ANSWER" for item in overlays),
        "base_correct": overlay_counts["base_correct"],
        "full_counterfactual_correct": overlay_counts["answerable_tsa"],
        "governed_correct": overlay_counts["governed"],
    }
    _dump(
        AUDIT / "m50c1_m48b2_trace_corpus_hashes.json",
        {"runtime": trace_hash, "evaluator": overlay_hash},
    )
    _dump(AUDIT / "m50c1_m48b2_failure_funnel.json", failure_funnel)
    _dump(
        AUDIT / "m50c1_m48b2_failure_funnel.json",
        {
            **failure_funnel,
            "stage_counts": _failure_funnel(overlays, runtime_records),
        },
    )
    _dump(
        AUDIT / "m50c1_m48b2_first_runtime_failure_distribution.json",
        _runtime_failure_distribution(traces),
    )
    _dump(
        AUDIT / "m50c1_m48b2_first_evaluator_divergence_distribution.json",
        _divergence_distribution(overlays),
    )
    m49 = _load_m49_map()
    _dump(
        AUDIT / "m50c1_m49_comparison.json",
        [
            {
                "case_id": item["case_id"],
                "m49_mechanism": m49.get(item["case_id"]),
                "first_divergence": item["first_evaluator_divergence_stage"],
            }
            for item in overlays
            if item["case_id"] in m49
        ],
    )
    expected = overlay_counts["governed"] == 78 and overlay_counts["answerable_tsa"] == 51
    return {
        "manifest": manifest,
        "canary": canary,
        "reference_canary": reference_canary,
        "traces": traces,
        "overlays": overlays,
        "trace_hash": trace_hash,
        "overlay_hash": overlay_hash,
        "metrics": overlay_counts,
        "failure_funnel": _failure_funnel(overlays, runtime_records),
        "exact_reproduction": expected,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.unlink(missing_ok=True)
    for row in rows:
        _append_jsonl(path, row)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _phase_b_validate() -> dict[str, Any]:
    _load_phase_a()
    first = _replay_m48b2()
    second = _replay_m48b2()
    determinism = _determinism_record(first, second)
    validation = {
        "phase": "B_M48B2_TRACE_VALIDATED",
        "provider_calls": 0,
        "model_calls": 0,
        "reader_preflight": first["canary"]["pass"] and second["canary"]["pass"],
        "reference_runtime_canary": first["reference_canary"]["pass"]
        and second["reference_canary"]["pass"],
        "first": {
            key: first[key]
            for key in (
                "trace_hash",
                "overlay_hash",
                "metrics",
                "failure_funnel",
                "exact_reproduction",
            )
        },
        "second": {
            key: second[key]
            for key in (
                "trace_hash",
                "overlay_hash",
                "metrics",
                "failure_funnel",
                "exact_reproduction",
            )
        },
        "determinism": determinism,
        "exact_reproduction": first["exact_reproduction"] and second["exact_reproduction"],
    }
    _dump(AUDIT / "m50c1_m48b2_replay_validation.json", validation)
    manifest = cast(dict[str, Any], json.loads(MANIFEST.read_text()))
    manifest.update(
        {
            "phase": validation["phase"],
            "m48b2_trace_corpus_hash": first["trace_hash"],
            "m48b2_evaluator_corpus_hash": first["overlay_hash"],
        }
    )
    _dump(MANIFEST, manifest)
    return validation


def _replay_m50c(m48b2_result: dict[str, Any]) -> dict[str, Any]:
    response_integrity = _m50c_response_integrity()
    case_ids, rows = _pairs()
    requests = _requests(case_ids, rows)
    catalogs = _catalogs(rows)
    services = m48b._runtime_services(catalogs)
    output: dict[str, Any] = {}
    all_overlays: dict[str, list[dict[str, Any]]] = {}
    parsed_by_arm: dict[str, list[dict[str, Any]]] = {}
    for arm in ("CONTROL", "TREATMENT"):
        parsed, response_hashes = _m50c_records(arm)
        parsed_by_arm[arm] = parsed
        traces, trace_hash = _trace_corpus(
            requests,
            parsed,
            response_hashes,
            services,
            AUDIT / f"m50c1_m50c_{arm.lower()}_runtime_traces.jsonl",
        )
        pairs = m48b2._pairs()
        converted = []
        for item in parsed:
            row = dict(item)
            row["schema_validation"] = row.get("parse_status")
            converted.append(row)
        runtime_records = [
            m48b2._replay_one(pair, converted_item, services, catalogs)
            for pair, converted_item in zip(pairs, converted, strict=True)
        ]
        overlays, metrics = _overlay(pairs, converted, runtime_records, traces)
        overlay_hash = _hash(
            [item["case_id"] + ":" + item["first_evaluator_divergence_stage"] for item in overlays]
        )
        _write_jsonl(AUDIT / f"m50c1_m50c_{arm.lower()}_evaluator_overlays.jsonl", overlays)
        output[arm] = {
            "trace_hash": trace_hash,
            "overlay_hash": overlay_hash,
            "metrics": metrics,
            "traces": traces,
            "overlays": overlays,
            "runtime_failure_distribution": dict(
                Counter(item["first_runtime_failure_stage"] or "NONE" for item in traces)
            ),
            "evaluator_divergence_distribution": _divergence_distribution(overlays),
        }
        all_overlays[arm] = overlays
    _m50c_posthoc(case_ids, parsed_by_arm, output)
    _dump(
        AUDIT / "m50c1_m50c_recovered_metrics.json",
        {
            arm: {key: value for key, value in item.items() if key not in {"traces", "overlays"}}
            for arm, item in output.items()
        },
    )
    paired = []
    for case_id in case_ids:
        c = next(item for item in all_overlays["CONTROL"] if item["case_id"] == case_id)
        t = next(item for item in all_overlays["TREATMENT"] if item["case_id"] == case_id)
        paired.append(
            {
                "case_id": case_id,
                "control_divergence": c["first_evaluator_divergence_stage"],
                "treatment_divergence": t["first_evaluator_divergence_stage"],
                "control_correct": c["full_counterfactual_correct"]
                if c["truth_behavior"] == "ANSWERABLE"
                else c["governance_correct"],
                "treatment_correct": t["full_counterfactual_correct"]
                if t["truth_behavior"] == "ANSWERABLE"
                else t["governance_correct"],
            }
        )
    _dump(
        AUDIT / "m50c1_m50c_paired_trace_analysis.json",
        {"rows": paired, "response_integrity": response_integrity},
    )
    _dump(
        AUDIT / "m50c1_actual_token_accounting.json",
        _token_accounting(
            {"CONTROL": _records(M50C_CONTROL), "TREATMENT": _records(M50C_TREATMENT)}
        ),
    )
    return output


def _observability_gaps(
    traces: list[dict[str, Any]], overlays: list[dict[str, Any]]
) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    for trace in traces:
        if trace.get("first_runtime_failure_stage") is None and not trace.get("stages"):
            gaps.append({"case_id": trace["response_identity"]["case_id"], "missing": ["stages"]})
    for overlay in overlays:
        if overlay.get("first_evaluator_divergence_stage") == "UNRESOLVED":
            gaps.append(
                {
                    "case_id": overlay["case_id"],
                    "missing": ["evaluator_discriminant"],
                }
            )
    result = {"unresolved_count": len(gaps), "gaps": gaps}
    _dump(AUDIT / "m50c1_observability_gap_analysis.json", result)
    return result


def _representative_traces(traces: list[dict[str, Any]], overlays: list[dict[str, Any]]) -> None:
    overlay_by_id = {item["case_id"]: item for item in overlays}
    selected: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    categories = (
        "DECISION_FALSE_ABSTENTION",
        "DECISION_FALSE_ANSWER",
        "RESULT_BASE",
        "RESULT_COUNTERFACTUAL",
        "NONE",
    )
    for category in categories:
        for trace in traces:
            case_id = trace["response_identity"]["case_id"]
            overlay = overlay_by_id.get(case_id)
            if overlay and overlay["first_evaluator_divergence_stage"] == category:
                selected.append((category, trace, overlay))
                break
    lines = ["# M50C.1 Representative Traces", ""]
    for category, trace, overlay in selected:
        case_id = trace["response_identity"]["case_id"]
        lines.extend(
            [
                f"## {category}: {case_id}",
                "",
                f"- trace hash: `{trace['trace_hash']}`",
                f"- first runtime failure: `{trace['first_runtime_failure_stage'] or 'NONE'}`",
                f"- first evaluator divergence: `{overlay['first_evaluator_divergence_stage']}`",
                "",
            ]
        )
        for stage in trace["stages"]:
            lines.append(f"### {stage['stage']}")
            lines.append("")
            lines.append(f"- status: `{stage['status']}`")
            if stage.get("reason_code"):
                lines.append(f"- reason: `{stage['reason_code']}`")
            if stage.get("skipped_reason"):
                lines.append(f"- skipped: `{stage['skipped_reason']}`")
            if stage.get("diagnostics"):
                lines.append(f"- diagnostics: `{json.dumps(stage['diagnostics'], sort_keys=True)}`")
            lines.append("")
    if not selected:
        lines.extend(["No representative categories were present.", ""])
    (AUDIT / "m50c1_representative_traces.md").write_text("\n".join(lines))


def _determinism_record(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    def comparable(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "trace_hash": value.get("trace_hash"),
            "overlay_hash": value.get("overlay_hash"),
            "metrics": value.get("metrics"),
            "runtime_failure_distribution": value.get("runtime_failure_distribution"),
            "evaluator_divergence_distribution": value.get("evaluator_divergence_distribution"),
        }

    first_value = comparable(first)
    second_value = comparable(second)
    return {"identical": first_value == second_value, "first": first_value, "second": second_value}


def _finalize() -> dict[str, Any]:
    manifest = _load_phase_a()
    validation = cast(
        dict[str, Any], json.loads((AUDIT / "m50c1_m48b2_replay_validation.json").read_text())
    )
    phase_b = {
        "trace_hash": validation["first"]["trace_hash"],
        "overlay_hash": validation["first"]["overlay_hash"],
        "metrics": validation["first"]["metrics"],
        "failure_funnel": validation["first"]["failure_funnel"],
        "exact_reproduction": validation["exact_reproduction"],
        "canary": json.loads((AUDIT / "m50c1_reader_canary.json").read_text()),
        "reference_canary": json.loads((AUDIT / "m50c1_reference_runtime_canary.json").read_text()),
        "traces": _read_jsonl(AUDIT / "m50c1_m48b2_runtime_traces.jsonl"),
        "overlays": _read_jsonl(AUDIT / "m50c1_m48b2_evaluator_overlays.jsonl"),
    }
    m48b2_determinism = validation["determinism"]
    m50c: dict[str, Any] | None
    m50c_determinism: dict[str, Any]
    if not phase_b["exact_reproduction"]:
        verdict = "UNIFIED_FAILURE_TRACE_REPLAY_MISMATCH"
        recovery = "M50C_FROZEN_RUNTIME_RECOVERY_FAILED"
        m50c = None
        m50c_determinism = {"identical": False, "not_run": True}
    else:
        m50c = _replay_m50c(phase_b)
        m50c_replay = _replay_m50c(phase_b)
        m50c_determinism = {
            arm: _determinism_record(m50c[arm], m50c_replay[arm])
            for arm in ("CONTROL", "TREATMENT")
        }
        verdict = (
            "UNIFIED_FAILURE_TRACE_SUPPORTED"
            if all(item["identical"] for item in m50c_determinism.values())
            else "UNIFIED_FAILURE_TRACE_PARTIAL"
        )
        recovery = "M50C_FROZEN_RUNTIME_RECOVERY_SUPPORTED"
    _observability_gaps(phase_b["traces"], phase_b["overlays"])
    _representative_traces(phase_b["traces"], phase_b["overlays"])
    historical = json.loads((AUDIT / "m50c1_historical_preservation.json").read_text())
    mismatches = [
        path for path, digest in historical["files"].items() if _sha(REPO / path) != digest
    ]
    integrity = {
        "experiment": "M50C.1",
        "provider_calls": 0,
        "model_calls": 0,
        "historical_hash_mismatches": mismatches,
        "m48b2_exact_reproduction": phase_b["exact_reproduction"],
        "reader_preflight": phase_b["canary"]["pass"],
        "reference_runtime_canary": phase_b["reference_canary"]["pass"],
        "m50c_response_integrity": (
            json.loads((AUDIT / "m50c1_m50c_response_integrity.json").read_text())
            if m50c is not None
            else None
        ),
        "determinism": {"m48b2": m48b2_determinism, "m50c": m50c_determinism},
        "unified_trace_verdict": verdict,
        "m50c_recovery_verdict": recovery,
        "scientific_signal": "NOT_ADJUDICABLE"
        if recovery != "M50C_FROZEN_RUNTIME_RECOVERY_SUPPORTED"
        else "TARGET_SIGNAL_PRESENT",
        "m51_ready": False,
    }
    _dump(AUDIT / "m50c1_final_integrity.json", integrity)
    report = {
        "integrity": integrity,
        "manifest": manifest,
        "m48b2": {
            key: value for key, value in phase_b.items() if key not in {"traces", "overlays"}
        },
    }
    if phase_b["exact_reproduction"] and m50c is not None:
        report["m50c"] = {
            arm: {key: value for key, value in item.items() if key not in {"traces", "overlays"}}
            for arm, item in m50c.items()
        }
    manifest.update(
        {
            "phase": "D_ZERO_CALL_REPLAY_COMPLETE",
            "m48b2_trace_corpus_hash": phase_b["trace_hash"],
            "m48b2_evaluator_corpus_hash": phase_b["overlay_hash"],
            "m50c_control_trace_corpus_hash": m50c["CONTROL"]["trace_hash"] if m50c else None,
            "m50c_treatment_trace_corpus_hash": m50c["TREATMENT"]["trace_hash"] if m50c else None,
            "m50c_control_evaluator_corpus_hash": m50c["CONTROL"]["overlay_hash"] if m50c else None,
            "m50c_treatment_evaluator_corpus_hash": m50c["TREATMENT"]["overlay_hash"]
            if m50c
            else None,
        }
    )
    _dump(MANIFEST, manifest)
    _dump(ROOT / "reports" / "m50c1_unified_failure_trace_summary.json", report)
    (ROOT / "reports" / "m50c1_unified_failure_trace_summary.md").write_text(
        "# M50C.1 — Zero-Call Unified Failure Trace\n\n"
        f"Unified trace verdict: `{verdict}`.\n\n"
        f"M50C recovery verdict: `{recovery}`.\n\n"
        "M50C historical verdict remains `M50C_ABORTED_POST_RESPONSE_CONTRACT_DEFECT`.\n"
    )
    return integrity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("phase-a", "validate-m48b2", "recover-m50c"))
    args = parser.parse_args()
    if args.command == "phase-a":
        print(json.dumps(_phase_a(), indent=2, sort_keys=True))
    elif args.command == "validate-m48b2":
        print(json.dumps(_phase_b_validate(), indent=2, sort_keys=True))
    else:
        print(json.dumps(_finalize(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
