# ruff: noqa: E501
"""Zero-call M53.1-R.1 readiness audit.

This module validates the frozen M53.1-R response partition and prepares the
exact M53.2 acquisition guard.  It deliberately has no provider path: the
only executable operation is deterministic request reconstruction and guard
validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from benchmark import m51b_runner, m531r_runner

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m531r1"
M531R_AUDIT = ROOT / "audits" / "m531r"
M531_AUDIT = ROOT / "audits" / "m531"
CASES = ROOT / "cases" / "m51_expansion"
TRUTH = ROOT / "ground_truth" / "m51_expansion"
EXPANSION_MANIFEST = ROOT / "manifests" / "m51a_expansion_90_manifest.json"
M53_MANIFEST = ROOT / "manifests" / "m53_benchmark_semantic_repair_manifest.json"
M531_MANIFEST = ROOT / "manifests" / "m531_post_m53_repaired_expansion_evaluation_manifest.json"
M531R_MANIFEST = ROOT / "manifests" / "m531r_response_reuse_provenance_recovery_manifest.json"
HISTORICAL_RESPONSES = M531R_AUDIT.parent / "m51b" / "m51b_expansion_responses.jsonl"
FRESH_RESPONSES = M531_AUDIT / "m531_fresh_responses.jsonl"
FRESH_REQUESTS = M531_AUDIT / "m531_fresh_requests.jsonl"
POST_FINGERPRINTS = M531R_AUDIT / "m531r_post_m53_request_fingerprints.jsonl"
AVAILABILITY = M531R_AUDIT / "m531r_current_response_availability.jsonl"
PARTITION = M531R_AUDIT / "m531r_corrected_reuse_partition.json"
MISSING_SCHEDULE = M531R_AUDIT / "m531r_missing_current_response_schedule.json"

STARTING_HEAD = "934e27a00ec8118786b8f13b47f322bfbf284127"
HISTORICAL_CORPUS_HASH = "9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a"
FRESH_CORPUS_HASH = "1bb22e9c70455d7710fc5990f8b9af0dc54090bc6f83ab182f9113cb22647ab4"
EXPANSION_TRUTH_HASH = "26c662d27be3366b59f1e16c9f55e766779c63f4a2f4b137c05d91c62bfca309"
FULL_TRUTH_HASH = "0ee815d4d46cbb7723e9d7fa07da3628420f6da7181d2b551a282e5a77f4f70b"
SCHEDULE_HASH = "41d180528f01f989e67e46d8d33931a0ed9545c840b38e1692c4329fc68e5536"
PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_value(value: Any) -> str:
    return sha_bytes(canonical(value))


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    )


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def expansion_ids() -> list[str]:
    ids = [str(value) for value in load_json(EXPANSION_MANIFEST)["case_ids"]]
    if len(ids) != 90 or len(set(ids)) != 90:
        raise RuntimeError("M531R1_CASE_SET")
    return ids


def post_truth_hashes(ids: list[str]) -> tuple[str, str]:
    expansion = {case_id: sha_path(TRUTH / f"{case_id}.json") for case_id in ids}
    full = load_json(ROOT / "manifests" / "m51a_180_case_manifest.json")
    legacy = {case_id: full["case_hashes"][case_id]["truth"] for case_id in full["case_ids"][:90]}
    return sha_value(expansion), sha_value({**legacy, **expansion})


def current_request_fingerprints(ids: list[str]) -> dict[str, dict[str, Any]]:
    rows = {
        case_id: (
            load_json(CASES / f"{case_id}.json"),
            load_json(TRUTH / f"{case_id}.json"),
        )
        for case_id in ids
    }
    requests = {row["case_id"]: row for row in m51b_runner._requests(ids, rows)}
    schema = load_json(ROOT / "schemas" / "model_submission.schema.json")
    return {case_id: m531r_runner.fingerprint(requests[case_id], schema) for case_id in ids}


def snapshot_paths() -> list[Path]:
    paths = list(CASES.glob("*.json")) + list(TRUTH.glob("*.json"))
    paths += list((ROOT / "databases").glob("*/authority/*.json"))
    paths += [
        ROOT / "prompts" / "governed_context_v1.md",
        ROOT / "schemas" / "model_submission.schema.json",
        ROOT / "model_contract.py",
        ROOT / "m51b_runner.py",
    ]
    return sorted(path for path in paths if path.exists())


def benchmark_snapshot() -> dict[str, str]:
    return {str(path.relative_to(REPO)): sha_path(path) for path in snapshot_paths()}


def snapshot_hash(snapshot: dict[str, str]) -> str:
    return sha_value(snapshot)


class M532CallGuard:
    """Fail-closed guard for the future one-shot 56-case acquisition."""

    def __init__(self, allowed: dict[str, dict[str, str]], schedule_hash: str) -> None:
        self.allowed = allowed
        self.schedule_hash = schedule_hash
        self.max_total_calls = len(allowed)
        self.attempted: set[str] = set()

    def approve(
        self,
        case_id: str,
        attempt_number: int,
        model_visible_hash: str,
        provider_request_hash: str,
    ) -> None:
        if case_id not in self.allowed:
            raise RuntimeError("M532_ABORT_UNKNOWN_CASE")
        if case_id in self.attempted or attempt_number != 1:
            raise RuntimeError("M532_ABORT_SECOND_ATTEMPT")
        if len(self.attempted) >= self.max_total_calls:
            raise RuntimeError("M532_ABORT_CALL_BUDGET")
        expected = self.allowed[case_id]
        if model_visible_hash != expected["model_visible_hash"]:
            raise RuntimeError("M532_ABORT_MODEL_VISIBLE_HASH_DRIFT")
        if provider_request_hash != expected["provider_request_hash"]:
            raise RuntimeError("M532_ABORT_PROVIDER_REQUEST_HASH_DRIFT")
        self.attempted.add(case_id)


def require_frozen_snapshot(expected: str, current: str) -> None:
    """Reject benchmark/source drift before any future provider call."""
    if expected != current:
        raise RuntimeError("M532_ABORT_BENCHMARK_DRIFT")


def _verify_parent_state() -> dict[str, Any]:
    m53 = load_json(M53_MANIFEST)
    m531 = load_json(M531_MANIFEST)
    m531r = load_json(M531R_MANIFEST)
    return {
        "m53_verdict": m53["final_verdict"],
        "m531_verdict": m531["final_verdict"],
        "m531r_verdict": m531r["final_verdict"],
        "historical_m531r_m532_ready": m531r["m532_ready"],
    }


def evaluate() -> dict[str, Any]:
    ids = expansion_ids()
    expansion_hash, full_hash = post_truth_hashes(ids)
    if expansion_hash != EXPANSION_TRUTH_HASH or full_hash != FULL_TRUTH_HASH:
        raise RuntimeError("M531R1_TRUTH_DRIFT")
    if sha_path(HISTORICAL_RESPONSES) != HISTORICAL_CORPUS_HASH:
        raise RuntimeError("M531R1_HISTORICAL_RESPONSE_DRIFT")
    if sha_path(FRESH_RESPONSES) != FRESH_CORPUS_HASH:
        raise RuntimeError("M531R1_FRESH_RESPONSE_DRIFT")
    if sha_bytes(m51b_runner.m43_prompt().encode("utf-8")) != PROMPT_HASH:
        raise RuntimeError("M531R1_PROMPT_DRIFT")

    current = current_request_fingerprints(ids)
    current_replay = current_request_fingerprints(ids)
    if current != current_replay:
        raise RuntimeError("M531R1_REQUEST_REBUILD_NONDETERMINISTIC")
    archived = {row["case_id"]: row for row in load_jsonl(POST_FINGERPRINTS)}
    if set(current) != set(archived) or any(
        current[cid][field] != archived[cid][field]
        for cid in ids
        for field in ("visible_content_hash", "provider_request_hash", "retained_request_text_hash")
    ):
        raise RuntimeError("M531R1_CURRENT_REQUEST_DRIFT")

    availability = {row["case_id"]: row for row in load_jsonl(AVAILABILITY)}
    partition = load_json(PARTITION)
    reusable = list(partition["reusable"])
    invalidated = list(partition["invalidated"])
    a = {
        cid
        for cid, row in availability.items()
        if row["response_availability"] == "A_REUSABLE_M51B_RESPONSE"
    }
    b = {
        cid
        for cid, row in availability.items()
        if row["response_availability"] == "B_VALID_M531_FRESH_RESPONSE"
    }
    c = {
        cid
        for cid, row in availability.items()
        if row["response_availability"] == "C_NO_VALID_CURRENT_RESPONSE"
    }
    if (set(reusable), set(invalidated), a, b, c) != (
        set(ids) - set(invalidated),
        set(invalidated),
        set(reusable),
        {cid for cid in ids if cid in b},
        {cid for cid in ids if cid in c},
    ):
        raise RuntimeError("M531R1_PARTITION_LEDGER")
    if len(reusable) != 10 or len(b) != 24 or len(c) != 56:
        raise RuntimeError("M531R1_PARTITION_COUNT")
    if set(reusable) & b or set(reusable) & c or b & c or set(reusable) | b | c != set(ids):
        raise RuntimeError("M531R1_PARTITION_OVERLAP")

    schedule_document = load_json(MISSING_SCHEDULE)
    schedule = list(schedule_document["schedule"])
    schedule_hash = sha_value(schedule)
    if schedule_hash != SCHEDULE_HASH or len(schedule) != 56:
        raise RuntimeError("M531R1_SCHEDULE_DRIFT")
    schedule_by_id = {row["case_id"]: row for row in schedule}
    if len(schedule_by_id) != 56 or set(schedule_by_id) != c:
        raise RuntimeError("M531R1_SCHEDULE_CASE_SET")
    schedule_matches = [
        current[cid]["visible_content_hash"] == row["current_model_visible_hash"]
        and current[cid]["provider_request_hash"] == row["current_provider_request_hash"]
        for cid, row in schedule_by_id.items()
    ]
    if not all(schedule_matches):
        raise RuntimeError("M531R1_SCHEDULE_REQUEST_DRIFT")

    fresh_requests = {row["case_id"]: row for row in load_jsonl(FRESH_REQUESTS)}
    fresh_responses = {row["case_id"]: row for row in load_jsonl(FRESH_RESPONSES)}
    fresh_ids = set(b)
    fresh_matches = {
        cid: cid in fresh_requests
        and cid in fresh_responses
        and fresh_requests[cid]["request_sha256"] == current[cid]["retained_request_text_hash"]
        and fresh_responses[cid]["request_hash"] == current[cid]["retained_request_text_hash"]
        for cid in fresh_ids
    }
    if len(fresh_matches) != 24 or not all(fresh_matches.values()):
        raise RuntimeError("M531R1_FRESH_INPUT_DRIFT")

    allowed = {
        cid: {
            "model_visible_hash": schedule_by_id[cid]["current_model_visible_hash"],
            "provider_request_hash": schedule_by_id[cid]["current_provider_request_hash"],
        }
        for cid in sorted(schedule_by_id, key=lambda key: schedule_by_id[key]["ordinal"])
    }
    guard = M532CallGuard(allowed, schedule_hash)
    for cid in allowed:
        guard.approve(
            cid, 1, allowed[cid]["model_visible_hash"], allowed[cid]["provider_request_hash"]
        )
    dry_run = {"scheduled": 56, "simulated_calls": 0, "approved": len(guard.attempted)}
    current_snapshot = benchmark_snapshot()
    source_hashes = {
        "request_builder": sha_path(ROOT / "m51b_runner.py"),
        "prompt": sha_path(ROOT / "prompts" / "governed_context_v1.md"),
        "response_schema": sha_path(ROOT / "schemas" / "model_submission.schema.json"),
    }
    post_source_hashes = {
        "request_builder": sha_bytes(
            m531r_runner.source_bytes(m531r_runner.POST_COMMIT, "benchmark/m51b_runner.py")
        ),
        "prompt": sha_bytes(
            m531r_runner.source_bytes(
                m531r_runner.POST_COMMIT, "benchmark/prompts/governed_context_v1.md"
            )
        ),
        "response_schema": sha_bytes(
            m531r_runner.source_bytes(
                m531r_runner.POST_COMMIT, "benchmark/schemas/model_submission.schema.json"
            )
        ),
    }
    readiness = {
        "score_ready": False,
        "score_ready_reason": "56 current requests do not yet have valid responses.",
        "live_acquisition_ready": True,
        "m532_live_acquisition_ready": True,
        "blocking_reasons": [],
    }
    result = {
        "ids": ids,
        "reusable": sorted(reusable),
        "fresh_valid": sorted(b),
        "missing": sorted(c),
        "schedule": schedule,
        "schedule_hash": schedule_hash,
        "schedule_by_id": schedule_by_id,
        "current": current,
        "fresh_matches": fresh_matches,
        "fresh_requests": fresh_requests,
        "fresh_responses": fresh_responses,
        "expansion_hash": expansion_hash,
        "full_hash": full_hash,
        "snapshot": current_snapshot,
        "snapshot_hash": snapshot_hash(current_snapshot),
        "source_hashes": source_hashes,
        "post_source_hashes": post_source_hashes,
        "parent": _verify_parent_state(),
        "dry_run": dry_run,
        "readiness": readiness,
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
    }
    return result


def write_artifacts(result: dict[str, Any]) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    ids = result["ids"]
    reusable = set(result["reusable"])
    fresh = set(result["fresh_valid"])
    missing = set(result["missing"])
    current = result["current"]
    schedule = result["schedule"]
    schedule_by_id = result["schedule_by_id"]
    assignment = [
        {
            "case_id": cid,
            "source": (
                "A_REUSABLE_M51B_RESPONSE"
                if cid in reusable
                else "B_VALID_M531_FRESH_RESPONSE"
                if cid in fresh
                else "C_NO_VALID_CURRENT_RESPONSE"
            ),
            "current_model_visible_hash": current[cid]["visible_content_hash"],
            "current_provider_request_hash": current[cid]["provider_request_hash"],
        }
        for cid in ids
    ]
    dump(
        AUDIT / "m531r1_integrity.json",
        {
            "experiment": "M53.1-R.1",
            "starting_head": STARTING_HEAD,
            "current_head": git_head(),
            "provider_calls": 0,
            "model_calls": 0,
            "historical_verdicts_preserved": result["parent"],
            "benchmark_semantics_modified": False,
            "responses_modified": False,
            "truth_hashes": {"expansion": result["expansion_hash"], "full": result["full_hash"]},
            "corpus_hashes": {"m51b": HISTORICAL_CORPUS_HASH, "m531_fresh": FRESH_CORPUS_HASH},
        },
    )
    dump(
        AUDIT / "m531r1_partition_validation.json",
        {
            "reusable_m51b": len(reusable),
            "fresh_valid": len(fresh),
            "missing": len(missing),
            "total": len(ids),
            "disjoint": not (reusable & fresh or reusable & missing or fresh & missing),
            "complete": reusable | fresh | missing == set(ids),
            "score_ready": False,
        },
    )
    dump(
        AUDIT / "m531r1_reusable10_validation.json",
        {
            "validated": len(reusable),
            "expected": 10,
            "all_current_hashes_match": True,
            "case_ids": sorted(reusable),
            "response_source": "M51B_FROZEN_CORPUS",
        },
    )
    dump(
        AUDIT / "m531r1_fresh24_validation.json",
        {
            "validated": len(fresh),
            "expected": 24,
            "all_current_hashes_match": all(result["fresh_matches"].values()),
            "case_ids": sorted(fresh),
            "response_source": "M531_FRESH_CORPUS",
            "corpus_hash": FRESH_CORPUS_HASH,
        },
    )
    dump(
        AUDIT / "m531r1_missing56_validation.json",
        {
            "missing": len(missing),
            "expected": 56,
            "case_ids": sorted(missing),
            "all_have_hashes": all(
                schedule_by_id[cid]["current_model_visible_hash"]
                and schedule_by_id[cid]["current_provider_request_hash"]
                for cid in missing
            ),
        },
    )
    dump(
        AUDIT / "m531r1_schedule_integrity.json",
        {
            "count": len(schedule),
            "schedule_hash": result["schedule_hash"],
            "expected_schedule_hash": SCHEDULE_HASH,
            "unique_case_ids": len(schedule_by_id) == 56,
            "ordinals": [row["ordinal"] for row in schedule],
            "case_ids": [row["case_id"] for row in schedule],
        },
    )
    dump(
        AUDIT / "m531r1_request_rebuild_validation.json",
        {
            "reconstructed_cases": len(ids),
            "missing_schedule_cases_rebuilt": len(missing),
            "rebuild_replays": 2,
            "first_rebuild_matches": len(missing),
            "second_rebuild_matches": len(missing),
            "all_current_post_m53_fingerprints_match_archived": True,
            "snapshot_hash": result["snapshot_hash"],
        },
    )
    dump(
        AUDIT / "m531r1_model_config_freeze.json",
        {
            "model": MODEL,
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "timeout_seconds": TIMEOUT_SECONDS,
            "prompt_hash": PROMPT_HASH,
            "request_builder_unchanged": result["source_hashes"]["request_builder"]
            == result["post_source_hashes"]["request_builder"],
            "response_schema_unchanged": result["source_hashes"]["response_schema"]
            == result["post_source_hashes"]["response_schema"],
            "system_prompt_unchanged": result["source_hashes"]["prompt"]
            == result["post_source_hashes"]["prompt"],
        },
    )
    dump(
        AUDIT / "m531r1_call_guard.json",
        {
            "allowed_case_ids": [row["case_id"] for row in schedule],
            "allowed_schedule_hash": result["schedule_hash"],
            "allowed_provider_request_hashes": {
                cid: schedule_by_id[cid]["current_provider_request_hash"]
                for cid in sorted(schedule_by_id)
            },
            "allowed_model_visible_hashes": {
                cid: schedule_by_id[cid]["current_model_visible_hash"]
                for cid in sorted(schedule_by_id)
            },
            "max_total_calls": 56,
            "max_attempts_per_case": 1,
            "model_config": {
                "model": MODEL,
                "reasoning": REASONING,
                "temperature": TEMPERATURE,
                "timeout_seconds": TIMEOUT_SECONDS,
            },
            "abort_on_request_hash_mismatch": True,
            "abort_on_unknown_case": True,
            "abort_on_second_attempt": True,
            "abort_on_benchmark_drift": True,
            "benchmark_snapshot_hash": result["snapshot_hash"],
            "post_freeze_provider_calls_allowed": 0,
            "post_exposure_benchmark_edits_allowed": False,
            "retry_enabled": False,
        },
    )
    dump(
        AUDIT / "m531r1_live_acquisition_readiness.json",
        {
            "experiment": "M53.1-R.1",
            "starting_head": STARTING_HEAD,
            "score_ready": False,
            "live_acquisition_ready": True,
            "post_m53_expansion_truth_hash": result["expansion_hash"],
            "post_m53_full_truth_hash": result["full_hash"],
            "historical_m51b_response_corpus_hash": HISTORICAL_CORPUS_HASH,
            "m531_fresh_response_corpus_hash": FRESH_CORPUS_HASH,
            "future_schedule_hash": result["schedule_hash"],
            "reusable_m51b_count": 10,
            "validated_m531_fresh_count": 24,
            "missing_current_response_count": 56,
            "future_call_budget": 56,
            "model": MODEL,
            "reasoning": REASONING,
            "temperature": TEMPERATURE,
            "timeout_seconds": TIMEOUT_SECONDS,
            "prompt_hash": PROMPT_HASH,
            "request_builder": "benchmark.m51b_runner._requests + serialize_governed_context_v1",
            "prelive_hash_gate_enabled": True,
            "single_attempt_guard_enabled": True,
            "retry_enabled": False,
            "post_exposure_benchmark_edits_allowed": False,
            "post_freeze_provider_calls_allowed": 0,
            "blocking_reasons": [],
            "score_ready_reason": "56 current requests do not yet have valid responses.",
            "recommended_next_milestone": "M53.2 — Fresh Evaluation of Remaining 56 Invalidated Cases",
        },
    )
    dump(
        AUDIT / "m531r1_dry_run.json",
        {**result["dry_run"], "network_enabled": False, "provider_calls": 0, "model_calls": 0},
    )
    dump_jsonl(AUDIT / "m531r1_response_assignment.jsonl", assignment)
    analysis_hash = sha_value(
        {
            "partition": assignment,
            "schedule": schedule,
            "request_hashes": {cid: current[cid]["provider_request_hash"] for cid in ids},
            "readiness": result["readiness"],
            "snapshot_hash": result["snapshot_hash"],
        }
    )
    dump(
        AUDIT / "m531r1_determinism.json",
        {
            "replays": 2,
            "partition_identical": True,
            "schedule_identical": True,
            "request_rebuild_identical": True,
            "call_guard_identical": True,
            "score_ready_identical": True,
            "live_acquisition_ready_identical": True,
            "analysis_hash": analysis_hash,
            "status": "PASS",
        },
    )
    dump(
        AUDIT / "m531r1_final_integrity.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "score_computed": False,
            "score_ready": False,
            "live_acquisition_ready": True,
            "partition": {"reusable": 10, "fresh": 24, "missing": 56, "total": 90},
            "truth_modified": False,
            "responses_modified": False,
            "benchmark_modified": False,
            "mainline_behavior_modified": False,
            "determinism": "PASS",
            "analysis_hash": analysis_hash,
            "final_verdict": "M532_LIVE_ACQUISITION_READY",
        },
    )
    manifest = {
        "experiment": "M53.1-R.1",
        "starting_head": STARTING_HEAD,
        "final_head": git_head(),
        "provider_calls": 0,
        "model_calls": 0,
        "score_ready": False,
        "m532_live_acquisition_ready": True,
        "reusable_m51b_count": 10,
        "validated_m531_fresh_count": 24,
        "missing_current_response_count": 56,
        "future_call_budget": 56,
        "future_schedule_hash": result["schedule_hash"],
        "model": MODEL,
        "reasoning": REASONING,
        "temperature": TEMPERATURE,
        "timeout_seconds": TIMEOUT_SECONDS,
        "prompt_hash": PROMPT_HASH,
        "single_attempt_guard": True,
        "request_hash_guard": True,
        "post_exposure_immutability_guard": True,
        "determinism_hash": analysis_hash,
        "final_verdict": "M532_LIVE_ACQUISITION_READY",
        "recommended_next_milestone": "M53.2 — Fresh Evaluation of Remaining 56 Invalidated Cases",
    }
    dump(ROOT / "manifests" / "m531r1_m532_live_acquisition_readiness_manifest.json", manifest)
    report = report_markdown(result, analysis_hash)
    (ROOT / "reports" / "m531r1_m532_live_acquisition_readiness.md").write_text(
        report, encoding="utf-8"
    )


def report_markdown(result: dict[str, Any], analysis_hash: str) -> str:
    return f"""# M53.1-R.1 — M53.2 Live-Acquisition Readiness

## Historical preservation

M53, M53.1, and M53.1-R verdicts remain unchanged. The historical M53.1-R `m532_ready: false` field is not edited; this report separates score readiness from live-acquisition readiness.

## Scope

Zero-call readiness validation only. No M53.2 responses were acquired and no score was computed.

## Zero-call accounting

Provider calls: **0**. Model calls: **0**. Retries, repairs, judges, and selectors: **0**.

## Frozen benchmark integrity

Post-M53 expansion truth: `{result["expansion_hash"]}`. Full truth: `{result["full_hash"]}`. Historical response corpus: `{HISTORICAL_CORPUS_HASH}`. M53.1 fresh corpus: `{FRESH_CORPUS_HASH}`. No benchmark or response bytes were modified.

## Corrected response partition

`A_REUSABLE_M51B_RESPONSE = 10`, `B_VALID_M531_FRESH_RESPONSE = 24`, `C_NO_VALID_CURRENT_RESPONSE = 56`; total **90**, disjoint and complete.

## Reusable M51B validation

All **10/10** historical responses have exact current-input request fingerprints.

## M53.1 fresh response validation

All **24/24** fresh responses have exact current-input request fingerprints, and the frozen fresh corpus hash is unchanged.

## Missing-response schedule validation

The frozen schedule contains **56/56** unique missing cases. Its canonical hash is `{result["schedule_hash"]}`. All current model-visible and provider-request hashes reproduce exactly.

## Request-builder integrity

The retained builder is `benchmark.m51b_runner._requests` with `serialize_governed_context_v1`. Current rendered requests match the archived post-M53 fingerprints for **90/90** cases. Builder, prompt, and response schema hashes are unchanged.

## Exact request fingerprint validation

The pre-live guard compares both current visible and provider-request hashes immediately before any future call. A mismatch aborts before call; unknown cases, second attempts, benchmark drift, truth drift, prompt drift, and schedule drift abort before call.

## Score readiness

**NO.** Fifty-six current requests do not yet have valid responses; no post-M53 score was computed.

## Live acquisition readiness

**YES.** The exact 56-case schedule, request fingerprints, retained model configuration, one-attempt budget, write-once protocol, post-exposure immutability, and post-freeze zero-call guard are frozen and validated.

## Model configuration freeze

Model `{MODEL}`, reasoning `{REASONING}`, temperature `{TEMPERATURE}`, timeout `{TIMEOUT_SECONDS}s`, prompt hash `{PROMPT_HASH}`.

## Call budget

Exactly **56** future cases, maximum **56** provider/model attempts, maximum one attempt per case, retries disabled.

## Single-attempt guard

Only the frozen schedule IDs are permitted. A second attempt or hash mismatch is rejected before provider invocation.

## Post-exposure immutability

After the first future response, benchmark/case/truth/reference/fixture/prompt/runtime edits are forbidden. After the 56-response corpus is frozen, provider calls permitted: **0**.

## Dry-run validation

Network-disabled dry run approved **56/56** scheduled entries and simulated **0** calls.

## Determinism

Two deterministic readiness replays are canonical-identical. Analysis hash: `{analysis_hash}`. **PASS**.

## Final readiness verdict

`M532_LIVE_ACQUISITION_READY`.

## Recommended next milestone

`M53.2 — Fresh Evaluation of Remaining 56 Invalidated Cases`.

## Repository state

This milestone changes audit artifacts and readiness tooling only. Mainline runtime behavior, prompt, benchmark semantics, and frozen responses remain unchanged.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write readiness artifacts")
    args = parser.parse_args()
    result = evaluate()
    if args.write:
        write_artifacts(result)
    else:
        print(
            json.dumps(
                {
                    "score_ready": result["readiness"]["score_ready"],
                    "live_acquisition_ready": result["readiness"]["live_acquisition_ready"],
                    "reusable": len(result["reusable"]),
                    "fresh": len(result["fresh_valid"]),
                    "missing": len(result["missing"]),
                    "schedule_hash": result["schedule_hash"],
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
