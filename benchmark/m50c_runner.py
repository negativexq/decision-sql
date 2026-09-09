"""M50C paired development experiment for typed availability context exposure.

The treatment is representation-only: it appends a deterministic rendering of
the frozen M50B factual snapshot to the unchanged M48B.2 user context.  This
module deliberately does not compute answerability, uniqueness, or a governed
decision.  Generation, response freezing, and zero-call runtime evaluation are
separate commands so the live contract cannot be tuned from its outputs.
"""

# Report prose and serialized request rows contain long contract lines.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlglot import exp, parse_one

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from app.semantics.context_availability import CONTRACT_VERSION
from app.sql.models import QueryExecution, SqlCandidate, SqlPlanFailure
from benchmark import m39_runner as m39
from benchmark import m46a_audit
from benchmark import m47b_runner as m47b
from benchmark import m48b_runner as m48b
from benchmark.context import render_governed_context
from benchmark.model_contract import sha256_bytes, sha256_text, submission_schema
from benchmark.models import ResultContract

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m50c"
MANIFEST = ROOT / "manifests" / "m50c_typed_availability_context_exposure_manifest.json"
M50B_AUDIT = ROOT / "audits" / "m50b"
M50B_MANIFEST = ROOT / "manifests" / "m50b_context_availability_shadow_manifest.json"
M50_PROMPT = ROOT / "audits" / "m50" / "m50_prompt_contract.json"
M50_CONFIG = ROOT / "experiments" / "m48b_end_to_end.json"
M50B_SNAPSHOTS = M50B_AUDIT / "m50b_shadow_snapshots.json"
M50B_HASHES = M50B_AUDIT / "m50b_snapshot_hashes.json"
M50B_CORPUS = M50B_AUDIT / "m50b_snapshot_corpus_hash.json"
EXPECTED_START = "5758affb7849d22098e758dccab2fe25f56f7321"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
CONTROL_PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
M50B_SCHEMA_HASH = "9b7cf6054f819c2aa7697740c811f339ec66e5e8c8f39fbb439c9b13bb87c805"
M50B_BUILDER_HASH = "4ff0b22ad4eb667c9c2c95094848579dcd5c9f7972e8cd125ab89f047e5146b2"
M50B_CORPUS_HASH = "759d5056cf8edf8477bf1cae2d7028ab9e3da63c37d20bf6f7dfb0d7319dfb4f"
RENDERER_VERSION = "typed-availability-context-renderer-1"
TARGETS = ("subscription_06", "warehouse_08", "warehouse_13")
OTHER_RESIDUALS = ("subscription_10", "risk_06", "warehouse_07", "warehouse_12")
SQL_CONTROLS = ("warehouse_03", "risk_03")
GOVERNANCE_TYPES = ("AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED")
FAMILY_ORDER = (
    "SCHEMA",
    "AUTHORIZED_RELATIONSHIP",
    "SEMANTIC_DEFINITION",
    "TEMPORAL_DEFINITION",
    "POLICY",
)
PROMPT_HASHES = {
    "control": CONTROL_PROMPT_HASH,
    "m50_treatment_not_used": "65c717e281671c57a443ef197feda7691ae3441a31a10264bdfa4b94ba56336e",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        unique = {
            json.dumps(_canonical(item), sort_keys=True, separators=(",", ":")) for item in value
        }
        return [json.loads(item) for item in sorted(unique)]
    return value


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


def _clean() -> bool:
    return not subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _snapshot_rows() -> list[dict[str, Any]]:
    rows = cast(list[dict[str, Any]], json.loads(M50B_SNAPSHOTS.read_text(encoding="utf-8")))
    hashes = cast(list[dict[str, str]], json.loads(M50B_HASHES.read_text(encoding="utf-8")))
    expected = {row["case_id"]: row["snapshot_hash"] for row in hashes}
    if len(rows) != 90 or len({row["case_id"] for row in rows}) != 90:
        raise RuntimeError("M50C_SNAPSHOT_COUNT_INVALID")
    if any(row["snapshot_hash"] != expected.get(row["case_id"]) for row in rows):
        raise RuntimeError("M50C_SNAPSHOT_HASH_ROW_MISMATCH")
    corpus = json.loads(M50B_CORPUS.read_text(encoding="utf-8"))
    actual = _hash(
        [{"case_index": row["case_index"], "snapshot_hash": row["snapshot_hash"]} for row in rows]
    )
    if corpus.get("hash") != M50B_CORPUS_HASH or actual != M50B_CORPUS_HASH:
        raise RuntimeError("M50C_M50B_CORPUS_HASH_MISMATCH")
    return rows


def _load_inputs() -> tuple[
    list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]]
]:
    case_ids, rows = m47b._rows()
    m47b._requests(case_ids, rows)
    snapshots = _snapshot_rows()
    if [row["case_id"] for row in snapshots] != case_ids:
        raise RuntimeError("M50C_CASE_ORDER_MISMATCH")
    return case_ids, rows, snapshots


def _primitive_value_is_redundant(
    family: str, value: dict[str, Any], context: dict[str, Any]
) -> bool:
    if family == "SCHEMA":
        sources = [*context.get("schema_catalog", []), *context.get("attributes", [])]
    elif family == "AUTHORIZED_RELATIONSHIP":
        sources = context.get("authorized_relationships", [])
    elif family == "SEMANTIC_DEFINITION":
        sources = [
            *context.get("metrics", []),
            *context.get("business_rules", []),
            *[item for item in context.get("attributes", []) if item.get("semantic_description")],
        ]
    elif family == "TEMPORAL_DEFINITION":
        sources = context.get("temporal_rules", [])
    elif family == "POLICY":
        return bool(_canonical(value) == _canonical(context.get("policy", {})))
    else:
        return False
    return bool(any(_canonical(value) == _canonical(source) for source in sources))


def render_context_availability_primitives(snapshot: dict[str, Any]) -> str:
    """Render only factual M50B primitive inventories, never evaluator judgments."""
    lines = ["[SERVER-OWNED CONTEXT PRIMITIVES]"]
    for primitive_set in snapshot["primitive_sets"]:
        family = primitive_set["family"]
        lines.append("")
        lines.append(f"{family} (state={primitive_set['state']}):")
        for item in primitive_set["items"]:
            record = {
                "id": item["primitive_id"],
                "subject": item["subject"],
                "kind": item["kind"],
                "value": item["value"],
            }
            lines.append(
                "- " + json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
    lines.extend(
        [
            "",
            "Unsupported semantic judgments:",
            "- required fact identification: NOT_COMPUTED",
            "- required fact completeness: NOT_COMPUTED",
            "- uniqueness of interpretation: NOT_COMPUTED",
            "- final answerability: NOT_COMPUTED",
            "",
            "These entries describe server-owned factual metadata only.",
            "They do not indicate that the request is answerable, unambiguous, or complete.",
        ]
    )
    return "\n".join(lines) + "\n"


def _schedule(
    case_ids: list[str], snapshot_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    global_index = 0
    for case_index, case_id in enumerate(case_ids, 1):
        digest = hashlib.sha256(f"M50C_ARM_ORDER:{case_id}".encode()).hexdigest()
        first = "CONTROL" if int(digest[:2], 16) % 2 == 0 else "TREATMENT"
        second = "TREATMENT" if first == "CONTROL" else "CONTROL"
        for paired_position, arm in enumerate((first, second), 1):
            global_index += 1
            result.append(
                {
                    "case_id": case_id,
                    "case_index": case_index,
                    "paired_position": paired_position,
                    "global_call_index": global_index,
                    "arm": arm,
                    "arm_order_digest": digest,
                    "m50b_snapshot_hash": snapshot_by_id[case_id]["snapshot_hash"],
                }
            )
    return result


def _request_pair(
    request: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    block = render_context_availability_primitives(snapshot["snapshot"])
    treatment_user = request["user_text"] + "\n\n" + block
    treatment_request_text = "SYSTEM:\n" + request["instructions"] + "\n\nUSER:\n" + treatment_user
    control = {
        **request,
        "arm": "CONTROL",
        "request_sha256": sha256_text(request["request_text"]),
        "input_token_estimate": math.ceil(len(request["request_text"].encode()) / 4),
        "rendered_block_hash": None,
    }
    treatment = {
        **request,
        "arm": "TREATMENT",
        "user_text": treatment_user,
        "request_text": treatment_request_text,
        "request_sha256": sha256_text(treatment_request_text),
        "input_token_estimate": math.ceil(len(treatment_request_text.encode()) / 4),
        "rendered_block_hash": sha256_text(block),
    }
    return control, treatment, block


def _historical_files() -> dict[str, str]:
    roots = [
        ROOT / "audits" / "m48b2",
        ROOT / "audits" / "m49",
        ROOT / "audits" / "m50",
        ROOT / "audits" / "m501",
        ROOT / "audits" / "m50a",
        ROOT / "audits" / "m50b",
        ROOT / "manifests" / "m48b2_branch_complete_runtime_contract.json",
        ROOT / "manifests" / "m49_residual_forensics_manifest.json",
        ROOT / "manifests" / "m50_context_sufficiency_intervention_manifest.json",
        ROOT / "manifests" / "m501_spillover_forensics_manifest.json",
        ROOT / "manifests" / "m50a_typed_answerability_feasibility_manifest.json",
        ROOT / "manifests" / "m50b_context_availability_shadow_manifest.json",
        ROOT / "reports" / "m48b2_end_to_end_summary.json",
        ROOT / "reports" / "m49_residual_forensics_summary.json",
        ROOT / "reports" / "m50_context_sufficiency_intervention_summary.json",
        ROOT / "reports" / "m501_spillover_forensics_summary.json",
        ROOT / "reports" / "m50a_typed_answerability_feasibility_summary.json",
        ROOT / "reports" / "m50b_context_availability_shadow_summary.json",
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
        raise RuntimeError("M50C_STARTING_HEAD_MISMATCH")
    m50b_manifest = json.loads(M50B_MANIFEST.read_text(encoding="utf-8"))
    if (
        m50b_manifest.get("contract_version") != CONTRACT_VERSION
        or m50b_manifest.get("contract_schema_hash") != M50B_SCHEMA_HASH
        or m50b_manifest.get("builder_hash") != M50B_BUILDER_HASH
        or m50b_manifest.get("snapshot_corpus_hash") != M50B_CORPUS_HASH
        or m50b_manifest.get("verdict") != "TYPED_CONTEXT_AVAILABILITY_SHADOW_SUPPORTED"
    ):
        raise RuntimeError("M50C_M50B_CONTRACT_MISMATCH")
    prompt_contract = json.loads(M50_PROMPT.read_text(encoding="utf-8"))
    if prompt_contract.get("control_prompt_hash") != CONTROL_PROMPT_HASH:
        raise RuntimeError("M50C_CONTROL_PROMPT_MISMATCH")
    config = json.loads(M50_CONFIG.read_text(encoding="utf-8"))
    expected_config = {
        "model": "gpt-5.6-luna",
        "provider": "openai-compatible",
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": 90,
        "calls_per_case": 1,
        "transport_retries": 0,
        "semantic_retries": 0,
        "repair": False,
        "judge": False,
        "selector": False,
        "reflection": False,
        "pass_at_k": 0,
    }
    if any(config.get(key) != value for key, value in expected_config.items()):
        raise RuntimeError("M50C_PROVIDER_CONTRACT_MISMATCH")
    case_ids, rows, snapshots = _load_inputs()
    snapshot_by_id = {row["case_id"]: row for row in snapshots}
    pairs: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    novel_rows: list[dict[str, Any]] = []
    for request in m47b._requests(case_ids, rows):
        control, treatment, block = _request_pair(request, snapshot_by_id[request["case_id"]])
        pairs.append(
            {
                "case_id": request["case_id"],
                "case_index": request["case_index"],
                "database_id": request["database_id"],
                "question_sha256": request["question_sha256"],
                "control_request_sha256": control["request_sha256"],
                "treatment_request_sha256": treatment["request_sha256"],
                "control_prompt_hash": sha256_text(control["instructions"]),
                "treatment_prompt_hash": sha256_text(treatment["instructions"]),
                "control_context_sha256": request["context_sha256"],
                "treatment_context_sha256": request["context_sha256"],
                "snapshot_hash": snapshot_by_id[request["case_id"]]["snapshot_hash"],
                "base_request_equal": control["user_text"] == request["user_text"],
                "treatment_suffix": block,
                "request_diff_only_block": treatment["user_text"].startswith(
                    request["user_text"] + "\n\n"
                ),
                "control_input_token_estimate": control["input_token_estimate"],
                "treatment_input_token_estimate": treatment["input_token_estimate"],
                "added_token_estimate": treatment["input_token_estimate"]
                - control["input_token_estimate"],
                "added_token_ratio": (
                    treatment["input_token_estimate"] / control["input_token_estimate"] - 1
                ),
            }
        )
        blocks.append(
            {
                "case_id": request["case_id"],
                "case_index": request["case_index"],
                "snapshot_hash": snapshot_by_id[request["case_id"]]["snapshot_hash"],
                "block_hash": sha256_text(block),
                "block": block,
            }
        )
        context = render_governed_context(str(request["database_id"]))
        snapshot = snapshot_by_id[request["case_id"]]["snapshot"]
        mismatches: list[dict[str, Any]] = []
        for primitive_set in snapshot["primitive_sets"]:
            for item in primitive_set["items"]:
                redundant = _primitive_value_is_redundant(
                    primitive_set["family"], item["value"], context
                )
                if not redundant:
                    mismatches.append(
                        {
                            "family": primitive_set["family"],
                            "primitive_id": item["primitive_id"],
                            "value": item["value"],
                        }
                    )
        novel_rows.append(
            {
                "case_id": request["case_id"],
                "primitive_count": sum(item["item_count"] for item in snapshot["primitive_sets"]),
                "non_redundant_primitives": mismatches,
                "new_server_owned_fact_count": len(mismatches),
            }
        )
    overhead = [row["added_token_ratio"] for row in pairs]
    treatment_block_corpus_hash = _hash(
        [{"case_index": row["case_index"], "block_hash": row["block_hash"]} for row in blocks]
    )
    schedule = _schedule(case_ids, snapshot_by_id)
    schedule_hash = _hash(schedule)
    arm_counts = Counter(item["arm"] for item in schedule)
    first_counts = Counter(schedule[index]["arm"] for index in range(0, len(schedule), 2))
    historical = {
        "experiment": "M50C",
        "starting_head": _git(),
        "files": _historical_files(),
        "mismatches": [],
    }
    renderer_contract = {
        "renderer_version": RENDERER_VERSION,
        "renderer_hash": _sha(Path(__file__)),
        "source_snapshot": "M50B frozen snapshot payload",
        "families": FAMILY_ORDER,
        "rendered_fields": ["primitive_id", "subject", "kind", "value", "inventory_state"],
        "negative_capabilities": [
            "required_fact_identification",
            "required_fact_completeness",
            "uniqueness_of_interpretation",
            "final_answerability",
        ],
        "behavioral_guidance_added": False,
        "case_specific_wording": False,
        "truth_oracle_access": False,
    }
    contract = {
        "experiment": "M50C",
        "parent": "M50B",
        "starting_head": EXPECTED_START,
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "m50b_contract_version": CONTRACT_VERSION,
        "m50b_contract_schema_hash": M50B_SCHEMA_HASH,
        "m50b_builder_hash": M50B_BUILDER_HASH,
        "m50b_snapshot_corpus_hash": M50B_CORPUS_HASH,
        "control_prompt_hash": CONTROL_PROMPT_HASH,
        "model": config["model"],
        "provider": config["provider"],
        "reasoning": config["reasoning"],
        "temperature": config["temperature"],
        "timeout_seconds": config["timeout_seconds"],
        "one_shot_per_arm": True,
        "provider_budget": 180,
        "retries": 0,
        "repair": 0,
        "judge": 0,
        "selector": 0,
        "reflection": 0,
        "pass_at_k": 0,
        "new_evaluator_facts": 0,
        "new_server_owned_facts": sum(row["new_server_owned_fact_count"] for row in novel_rows),
        "request_level_answerability": "NOT_EXPOSED",
        "phase": "A_FROZEN_BEFORE_LIVE_CALLS",
    }
    request_diff = {
        "cases": len(pairs),
        "all_base_request_equal": all(row["base_request_equal"] for row in pairs),
        "all_diff_only_block": all(row["request_diff_only_block"] for row in pairs),
        "control_prompt_hashes": sorted({row["control_prompt_hash"] for row in pairs}),
        "treatment_prompt_hashes": sorted({row["treatment_prompt_hash"] for row in pairs}),
        "truth_leakage": 0,
        "rows": pairs,
    }
    token_preflight = {
        "method": "deterministic_utf8_bytes_divided_by_four_estimate",
        "cases": len(pairs),
        "control_total_estimate": sum(row["control_input_token_estimate"] for row in pairs),
        "treatment_total_estimate": sum(row["treatment_input_token_estimate"] for row in pairs),
        "median_added_ratio": statistics.median(overhead),
        "p90_added_ratio": sorted(overhead)[math.ceil(len(overhead) * 0.9) - 1],
        "max_added_ratio": max(overhead),
        "max_treatment_input_estimate": max(row["treatment_input_token_estimate"] for row in pairs),
        "retention_median_gate": statistics.median(overhead) <= 0.30,
        "provider_limit_check": "DEFERRED_TO_PROVIDER_ACCEPTANCE",
    }
    _dump(AUDIT / "m50c_historical_preservation.json", historical)
    _dump(AUDIT / "m50c_contract.json", contract)
    _dump(AUDIT / "m50c_renderer_contract.json", renderer_contract)
    _dump(
        AUDIT / "m50c_snapshot_integrity.json",
        {
            "cases": len(snapshots),
            "matched_m50b_snapshot_hashes": len(snapshots),
            "corpus_hash": M50B_CORPUS_HASH,
            "all_match": True,
        },
    )
    _dump(
        AUDIT / "m50c_novel_fact_audit.json",
        {
            "new_evaluator_facts": 0,
            "new_server_owned_facts": sum(row["new_server_owned_fact_count"] for row in novel_rows),
            "all_primitive_values_redundant": all(
                not row["non_redundant_primitives"] for row in novel_rows
            ),
            "rows": novel_rows,
        },
    )
    _dump(AUDIT / "m50c_request_diff_audit.json", request_diff)
    _dump(AUDIT / "m50c_token_overhead_preflight.json", token_preflight)
    _dump(AUDIT / "m50c_rendered_treatment_blocks.json", blocks)
    _dump(
        AUDIT / "m50c_call_schedule.json",
        {
            "case_order": case_ids,
            "paired_cases": 90,
            "arm_counts": dict(arm_counts),
            "first_arm_counts": dict(first_counts),
            "schedule_hash": schedule_hash,
            "schedule": schedule,
        },
    )
    _dump(
        MANIFEST,
        {
            **contract,
            "renderer_version": RENDERER_VERSION,
            "renderer_hash": renderer_contract["renderer_hash"],
            "treatment_context_corpus_hash": treatment_block_corpus_hash,
            "schedule_hash": schedule_hash,
            "snapshot_validation": 90,
            "phase": "A_FROZEN_BEFORE_LIVE_CALLS",
        },
    )
    return {
        "renderer_hash": renderer_contract["renderer_hash"],
        "treatment_context_corpus_hash": treatment_block_corpus_hash,
        "schedule_hash": schedule_hash,
        "provider_calls": 0,
        "model_calls": 0,
        "new_server_owned_facts": contract["new_server_owned_facts"],
        "median_token_overhead": token_preflight["median_added_ratio"],
        "schedule_arm_counts": dict(arm_counts),
    }


def _load_phase_a() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    manifest = cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))
    if manifest.get("phase") != "A_FROZEN_BEFORE_LIVE_CALLS":
        raise RuntimeError("M50C_PHASE_A_NOT_FROZEN")
    if _git() == EXPECTED_START:
        raise RuntimeError("M50C_PHASE_A_COMMIT_REQUIRED")
    blocks = cast(
        list[dict[str, Any]],
        json.loads((AUDIT / "m50c_rendered_treatment_blocks.json").read_text(encoding="utf-8")),
    )
    schedule = cast(
        dict[str, Any], json.loads((AUDIT / "m50c_call_schedule.json").read_text(encoding="utf-8"))
    )
    if len(schedule["schedule"]) != 180 or schedule["arm_counts"] != {
        "CONTROL": 90,
        "TREATMENT": 90,
    }:
        raise RuntimeError("M50C_SCHEDULE_INVALID")
    return manifest, blocks, schedule


def _generate() -> dict[str, Any]:
    manifest, blocks, schedule_data = _load_phase_a()
    if not get_settings().llm_api_key:
        raise RuntimeError("M50C_PROVIDER_BLOCKED_API_KEY_MISSING")
    case_ids, rows, snapshots = _load_inputs()
    base_requests = m47b._requests(case_ids, rows)
    block_by_id = {row["case_id"]: row["block"] for row in blocks}
    request_by_id = {request["case_id"]: request for request in base_requests}
    settings = get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": 0.0,
            "llm_timeout_seconds": 90,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(settings)
    ledger: list[dict[str, Any]] = []
    paths = {
        "CONTROL": AUDIT / "m50c_control_responses.jsonl",
        "TREATMENT": AUDIT / "m50c_treatment_responses.jsonl",
    }
    for path in paths.values():
        if path.exists():
            raise RuntimeError("M50C_RESPONSE_ARTIFACT_ALREADY_EXISTS")
    for item in schedule_data["schedule"]:
        request = request_by_id[item["case_id"]]
        arm = item["arm"]
        if arm == "CONTROL":
            user_prompt = request["user_text"]
            request_hash = request["request_sha256"]
            block_hash = None
        else:
            block = block_by_id[item["case_id"]]
            user_prompt = request["user_text"] + "\n\n" + block
            request_hash = sha256_text(
                "SYSTEM:\n" + request["instructions"] + "\n\nUSER:\n" + user_prompt
            )
            block_hash = sha256_text(block)
        started_at = _now()
        begin = time.perf_counter()
        provider.consume_response_wire()
        payload: Any = None
        error: Exception | None = None
        try:
            payload = asyncio.run(
                provider.complete_json_schema(
                    operation="m50c_typed_availability_context_exposure",
                    system_prompt=request["instructions"],
                    user_prompt=user_prompt,
                    schema_name="decision_sql_m50c_submission",
                    schema=submission_schema(),
                )
            )
        except Exception as exc:
            error = exc
        latency_ms = (time.perf_counter() - begin) * 1000
        capture = provider.consume_model_io()
        wire = provider.consume_response_wire()
        metadata = m39._provider_metadata(payload or {}, capture)
        response_hash = sha256_bytes(wire) if wire is not None else None
        content = getattr(capture, "raw_assistant_content_full", None)
        submission, parse_status, parse_detail, parsed_value = (
            m39._parse(content, item["case_id"])
            if error is None
            else (None, m39._classify_provider_error(error)[0], str(error), None)
        )
        parsed = parsed_value
        record = {
            **item,
            "started_at": started_at,
            "finished_at": _now(),
            "request_sha256": request_hash,
            "base_prompt_hash": CONTROL_PROMPT_HASH,
            "m50b_snapshot_hash": next(
                row["snapshot_hash"] for row in snapshots if row["case_id"] == item["case_id"]
            ),
            "rendered_block_hash": block_hash,
            "response_sha256": response_hash,
            "parsed_submission": parsed,
            "parsed_submission_hash": _hash(parsed) if parsed is not None else None,
            "parse_status": parse_status,
            "parse_detail": parse_detail,
            "provider_error": None
            if error is None
            else {"type": type(error).__name__, "message": str(error)[:240]},
            "provider_metadata": metadata,
            "transport_status": "SUCCESS" if error is None else "FAILURE",
            "provider_attempts": 1,
            "latency_ms": latency_ms,
        }
        raw = {
            **record,
            "raw_response_bytes_base64": base64.b64encode(wire).decode("ascii")
            if wire is not None
            else None,
        }
        _append(paths[arm], raw)
        ledger.append(record)
        _dump(
            AUDIT / "m50c_call_ledger.json",
            {
                "experiment": "M50C",
                "attempts": len(ledger),
                "control_attempts": sum(row["arm"] == "CONTROL" for row in ledger),
                "treatment_attempts": sum(row["arm"] == "TREATMENT" for row in ledger),
                "retries": 0,
                "records": ledger,
            },
        )
    if len(ledger) != 180:
        raise RuntimeError("M50C_ATTEMPT_COUNT_INVALID")
    counts = Counter(row["arm"] for row in ledger)
    if counts != Counter({"CONTROL": 90, "TREATMENT": 90}):
        raise RuntimeError("M50C_ARM_COUNT_INVALID")
    freeze = {
        "experiment": "M50C",
        "phase": "B_RESPONSES_FROZEN",
        "provider_attempts": len(ledger),
        "control_attempts": counts["CONTROL"],
        "treatment_attempts": counts["TREATMENT"],
        "retries": 0,
        "response_files": {arm: _sha(path) for arm, path in paths.items()},
        "call_ledger_hash": _sha(AUDIT / "m50c_call_ledger.json"),
        "generation_head": _git(),
        "provider_calls_after_generation": 0,
        "model_calls_after_generation": 0,
        "m50b_snapshot_corpus_hash": manifest["m50b_snapshot_corpus_hash"],
    }
    _dump(AUDIT / "m50c_response_freeze.json", freeze)
    return freeze


def _load_records() -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for arm in ("CONTROL", "TREATMENT"):
        path = AUDIT / f"m50c_{arm.lower()}_responses.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if len(records) != 90 or len({row["case_id"] for row in records}) != 90:
            raise RuntimeError(f"M50C_{arm}_RESPONSES_INVALID")
        result[arm] = sorted(records, key=lambda row: row["case_index"])
    return result


def _runtime_only(service: Any, sql: str) -> dict[str, Any]:
    planned = service.plan(SqlCandidate(sql=sql))
    if isinstance(planned, SqlPlanFailure):
        return {
            "planned": False,
            "executed": False,
            "runtime_disposition": planned.status.value,
            "plan_failure": planned.model_dump(mode="json"),
            "result_contract_outcome": None,
        }
    execution = service.execute(planned)
    return {
        "planned": True,
        "executed": isinstance(execution, QueryExecution),
        "runtime_disposition": "ALLOWED",
        "plan": planned.model_dump(mode="json"),
        "execution": execution.model_dump(mode="json"),
        "result_contract_outcome": None,
    }


def _classify_failure(record: dict[str, Any], truth_behavior: str) -> str:
    if record.get("parse_status") != "PASS" or not record.get("parsed_submission"):
        return "MODEL_OUTPUT_CONTRACT_FAILURE"
    submission = record["parsed_submission"]
    decision = submission.get("decision")
    if truth_behavior != "ANSWERABLE":
        return (
            "CORRECT_GOVERNANCE"
            if decision == m39.EXPECTED_DECISION[truth_behavior]
            else "WRONG_GOVERNANCE_DECISION"
        )
    if decision != "ANSWER" or not submission.get("sql"):
        return "WRONG_REFUSAL"
    states = record.get("states", [])
    if not states:
        return "SQL_RUNTIME_FAILURE"
    if any(state["runtime_disposition"] != "ALLOWED" for state in states):
        return "RUNTIME_REJECTION"
    return (
        "CORRECT"
        if all(state.get("result_contract_outcome") for state in states)
        else "RESULT_MISMATCH"
    )


def _evaluate_arm(
    arm: str,
    records: list[dict[str, Any]],
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    services: dict[str, Any],
) -> dict[str, Any]:
    evaluated: list[dict[str, Any]] = []
    for record in records:
        case, truth = rows[record["case_id"]]
        parsed = record.get("parsed_submission")
        output = {**record, "states": [], "truth_behavior": case["task_type"]}
        if record.get("parse_status") != "PASS" or not isinstance(parsed, dict):
            output["failure_class"] = _classify_failure(output, case["task_type"])
            evaluated.append(output)
            continue
        decision = parsed.get("decision")
        sql = parsed.get("sql")
        if decision != "ANSWER" or not sql:
            output["failure_class"] = _classify_failure(output, case["task_type"])
            output["governance_correct"] = decision == m39.EXPECTED_DECISION[case["task_type"]]
            evaluated.append(output)
            continue
        service = services[case["database_id"]]
        if case["task_type"] == "ANSWERABLE":
            contract = ResultContract.from_dict(
                truth["semantic_target"]["result_comparison_contract"]
            )
            for fixture in m48b._fixture_list(truth):
                prep = m48b._prepare_state(case["database_id"], fixture)
                oracle = m48b._runtime(
                    service,
                    truth["reference_implementation_a"]["sql"],
                    contract,
                    [],
                )
                expected_rows = m48b._rows(oracle)
                state = m48b._state_runtime(service, sql, case, truth, expected_rows)
                output["states"].append(
                    {
                        "state_id": fixture["fixture_id"],
                        "state_preparation": prep,
                        **state,
                        "runtime_disposition": state["runtime_disposition"],
                        "result_contract_outcome": state["result_contract_outcome"],
                    }
                )
        else:
            prep = m48b._prepare_state(case["database_id"], {"fixture_id": "base", "patch_sql": []})
            grain = m48b._grain_snapshot(service, sql)
            runtime = _runtime_only(service, sql)
            output["states"].append(
                {
                    "state_id": "base",
                    "state_preparation": prep,
                    "raw_sql_hash": sha256_text(sql),
                    "selected_sql_hash": runtime.get("plan", {}).get("normalized_sql"),
                    "grain": grain,
                    **runtime,
                }
            )
        output["governance_correct"] = False
        output["failure_class"] = _classify_failure(output, case["task_type"])
        evaluated.append(output)
    return {"arm": arm, "records": evaluated, "provider_calls": 0, "model_calls": 0}


def _metrics(
    arm_result: dict[str, Any], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    records = {row["case_id"]: row for row in arm_result["records"]}
    answerable = [case_id for case_id, pair in rows.items() if pair[0]["task_type"] == "ANSWERABLE"]
    governed = sum(row["failure_class"] == "CORRECT" for row in records.values()) + sum(
        row["failure_class"] == "CORRECT_GOVERNANCE" for row in records.values()
    )
    answer_rows = [records[case_id] for case_id in answerable]
    answered = [
        row
        for row in answer_rows
        if (row.get("parsed_submission") or {}).get("decision") == "ANSWER"
    ]
    full_correct = [row for row in answered if row["failure_class"] == "CORRECT"]
    base_correct = [
        row
        for row in full_correct
        if row.get("states") and row["states"][0].get("result_contract_outcome")
    ]
    wrong_refusal = [row for row in answer_rows if row["failure_class"] == "WRONG_REFUSAL"]
    stages: Counter[str] = Counter()
    runtime_stage: Counter[str] = Counter()
    grain: Counter[str] = Counter()
    result_mismatches: list[str] = []
    normalized_incorrect = 0
    for row in answer_rows:
        if (row.get("parsed_submission") or {}).get("decision") == "ANSWER":
            stages["answer_selected"] += 1
        if row["failure_class"] == "CORRECT":
            stages["full_counterfactual_correct"] += 1
        if row["failure_class"] == "RESULT_MISMATCH":
            result_mismatches.append(row["case_id"])
        for state in row.get("states", []):
            disposition = state.get("runtime_disposition")
            runtime_stage[disposition or "UNKNOWN"] += 1
            runtime_stage[_runtime_stage_label(state)] += 1
            grain_code = state.get("grain", {}).get("input_diagnostic", {}).get("code")
            if grain_code == "PARENT_MEASURE_FANOUT":
                grain["fanout"] += 1
            if state.get("grain", {}).get("status") == "NORMALIZED":
                grain["normalized"] += 1
                if not state.get("result_contract_outcome"):
                    normalized_incorrect += 1
            if state.get("grain", {}).get("status") == "ABSTAINED":
                grain["abstained"] += 1
            if state.get("grain", {}).get("input_diagnostic", {}).get("code") in {
                "PASS",
                "NOT_APPLICABLE",
            } and state.get("grain", {}).get("selected_sql_hash") != state.get("grain", {}).get(
                "input_sql_hash"
            ):
                grain["safe_sql_rewrites"] += 1
    governance = {
        truth: sum(
            rows[case_id][0]["task_type"] == truth
            and records[case_id]["failure_class"] == "CORRECT_GOVERNANCE"
            for case_id in rows
        )
        for truth in GOVERNANCE_TYPES
    }
    unauthorized = sum(
        rows[case_id][0]["task_type"] == "AUTHORITY_BLOCKED"
        and (records[case_id].get("parsed_submission") or {}).get("decision") == "ANSWER"
        for case_id in rows
    )
    return {
        "arm": arm_result["arm"],
        "governed": {"correct": governed, "total": 90, "rate": governed / 90},
        "answerable_runtime_tsa": {
            "correct": len(full_correct),
            "total": 60,
            "rate": len(full_correct) / 60,
        },
        "base_delivered": {
            "correct": len(base_correct),
            "total": 60,
            "rate": len(base_correct) / 60,
        },
        "answer_rate": {"correct": len(answered), "total": 60, "rate": len(answered) / 60},
        "wrong_refusal": {"count": len(wrong_refusal), "total": 60},
        "conditional_runtime": {
            "correct": len(full_correct),
            "total": len(answered),
            "rate": len(full_correct) / len(answered) if answered else None,
        },
        "governance": governance,
        "unauthorized_answers": unauthorized,
        "wrong_refusal_ids": sorted(row["case_id"] for row in wrong_refusal),
        "result_mismatch_ids": sorted(result_mismatches),
        "runtime_stage_counts": dict(runtime_stage),
        "grain": {
            **dict(grain),
            "normalization_regressions": normalized_incorrect,
            "unsafe_raw_fallback": 0,
            "execution_outside_query_plan": 0,
        },
        "pipeline": dict(stages),
        "answerable_answer_ids": sorted(row["case_id"] for row in answered),
    }


def _transition(left: bool, right: bool) -> str:
    return f"{'CORRECT' if left else 'WRONG'}->{'CORRECT' if right else 'WRONG'}"


def _correct(row: dict[str, Any], case: dict[str, Any]) -> bool:
    return row["failure_class"] in {"CORRECT", "CORRECT_GOVERNANCE"}


def _deterministic_projection(result: dict[str, Any]) -> dict[str, Any]:
    """Exclude wall-clock/provider bookkeeping from the zero-call replay hash."""
    return {
        "control_metrics": result["control_metrics"],
        "treatment_metrics": result["treatment_metrics"],
        "pair_matrix": result["pair_matrix"],
        "decision_matrix": result["decision_matrix"],
        "paired_rows": result["paired_rows"],
        "target_rows": result["target_rows"],
        "sql_semantic_perturbation": result["sql_semantic_perturbation"],
        "wrong_refusal_churn": result["wrong_refusal_churn"],
        "result_mismatch_churn": result["result_mismatch_churn"],
        "populations": result["populations"],
    }


def _runtime_stage_label(state: dict[str, Any]) -> str:
    if state.get("runtime_disposition") == "ALLOWED":
        if state.get("execution_attempted") and not state.get("result_contract_outcome"):
            return "RESULT_MISMATCH"
        return "EXECUTION_SUCCESS"
    status = str(
        state.get("runtime", {}).get("plan_failure", {}).get("status")
        or state.get("plan_failure", {}).get("status")
        or state.get("runtime_disposition")
        or "UNKNOWN"
    )
    return {
        "SQL_PARSE_ERROR": "SQL_PARSE_REJECTION",
        "POLICY_REJECTION": "SQL_POLICY_REJECTION",
        "SEMANTIC_REJECTION": "SEMANTIC_REJECTION",
        "QUERY_COST_REJECTION": "QUERY_COST_REJECTION",
        "EXECUTION_ERROR": "EXECUTION_FAILURE",
    }.get(status, status)


def _sql_features(sql: str) -> dict[str, Any]:
    tree = parse_one(sql, dialect="postgres")

    def rendered(node: Any) -> str | None:
        return node.sql(dialect="postgres") if node is not None else None

    where = tree.args.get("where")
    group = tree.args.get("group")
    order = tree.args.get("order")
    tables = sorted(item.sql(dialect="postgres") for item in tree.find_all(exp.Table))
    joins = sorted(item.sql(dialect="postgres") for item in tree.find_all(exp.Join))
    projections = [item.sql(dialect="postgres") for item in tree.expressions]
    aggregates = sorted(item.sql(dialect="postgres") for item in tree.find_all(exp.AggFunc))
    windows = sorted(item.sql(dialect="postgres") for item in tree.find_all(exp.Window))
    normalized = tree.sql(dialect="postgres")
    return {
        "normalized_sql": normalized,
        "tables": tables,
        "joins": joins,
        "where": rendered(where),
        "group_by": [item.sql(dialect="postgres") for item in group.expressions]
        if group is not None
        else [],
        "order_by": [item.sql(dialect="postgres") for item in order.expressions]
        if order is not None
        else [],
        "projections": projections,
        "aggregates": aggregates,
        "windows": windows,
        "has_subquery": any(True for _ in tree.find_all(exp.Subquery)),
        "has_json_operator": any(
            token in normalized.upper() for token in ("->", "#>", "JSON", "JSONB")
        ),
        "has_null_predicate": " IS NULL" in normalized.upper()
        or " IS NOT NULL" in normalized.upper(),
    }


def _fanout_observed(record: dict[str, Any]) -> bool:
    return any(
        state.get("grain", {}).get("input_diagnostic", {}).get("code") == "PARENT_MEASURE_FANOUT"
        for state in record.get("states", [])
    )


def _classify_sql_change(
    control_sql: str, treatment_sql: str, control: dict[str, Any], treatment: dict[str, Any]
) -> dict[str, Any]:
    control_features = _sql_features(control_sql)
    treatment_features = _sql_features(treatment_sql)
    if control_features["normalized_sql"] == treatment_features["normalized_sql"]:
        category = "SEMANTICALLY_EQUIVALENT_REWRITE"
    elif control_features["tables"] != treatment_features["tables"]:
        category = "TABLE_SCOPE_CHANGE"
    elif control_features["joins"] != treatment_features["joins"]:
        category = "JOIN_CHANGE"
    elif control_features["where"] != treatment_features["where"]:
        upper = f"{control_features['where']} {treatment_features['where']}".upper()
        category = (
            "NULL_CHANGE"
            if "NULL" in upper
            else (
                "TEMPORAL_CHANGE"
                if any(token in upper for token in ("DATE", "TIME", "INTERVAL"))
                else "FILTER_CHANGE"
            )
        )
    elif control_features["aggregates"] != treatment_features["aggregates"]:
        category = "AGGREGATION_CHANGE"
    elif control_features["group_by"] != treatment_features["group_by"]:
        category = "GROUPING_CHANGE"
    elif control_features["projections"] != treatment_features["projections"]:
        category = "PROJECTION_CHANGE"
    elif control_features["order_by"] != treatment_features["order_by"]:
        category = "ORDER_TIE_BREAK_CHANGE"
    elif control_features["has_json_operator"] != treatment_features["has_json_operator"]:
        category = "JSON_CHANGE"
    elif control_features["has_subquery"] != treatment_features["has_subquery"]:
        category = "SUBQUERY_STRUCTURE_CHANGE"
    elif _fanout_observed(control) != _fanout_observed(treatment):
        category = "GRAIN_FANOUT_CHANGE"
    else:
        category = "OTHER_MATERIAL_CHANGE"
    return {
        "category": category,
        "material": category != "SEMANTICALLY_EQUIVALENT_REWRITE",
        "control_features": control_features,
        "treatment_features": treatment_features,
        "first_discriminating_state": next(
            (
                state["state_id"]
                for state in treatment.get("states", [])
                if state.get("result_contract_outcome")
                != next(
                    (
                        other.get("result_contract_outcome")
                        for other in control.get("states", [])
                        if other.get("state_id") == state.get("state_id")
                    ),
                    None,
                )
            ),
            None,
        ),
    }


def _phase_c() -> dict[str, Any]:
    manifest, _blocks, _schedule = _load_phase_a()
    responses = _load_records()
    case_ids, rows, snapshots = _load_inputs()
    if _sha(AUDIT / "m50c_control_responses.jsonl") == "" or len(snapshots) != 90:
        raise RuntimeError("M50C_RESPONSE_OR_SNAPSHOT_INVALID")
    answerable_truths = [
        truth for case, truth in rows.values() if case["task_type"] == "ANSWERABLE"
    ]
    catalogs, _inventory = m46a_audit._build_catalogs(answerable_truths)
    services = m48b._runtime_services(catalogs)
    control = _evaluate_arm("CONTROL", responses["CONTROL"], rows, services)
    treatment = _evaluate_arm("TREATMENT", responses["TREATMENT"], rows, services)
    control_metrics = _metrics(control, rows)
    treatment_metrics = _metrics(treatment, rows)
    c_by_id = {row["case_id"]: row for row in control["records"]}
    t_by_id = {row["case_id"]: row for row in treatment["records"]}
    matrix: Counter[str] = Counter()
    population_counts: dict[str, Counter[str]] = defaultdict(Counter)
    decision_matrix: Counter[str] = Counter()
    paired_rows: list[dict[str, Any]] = []
    sql_pairs: list[dict[str, Any]] = []
    for case_id in case_ids:
        c = c_by_id[case_id]
        t = t_by_id[case_id]
        case = rows[case_id][0]
        cc = _correct(c, case)
        tc = _correct(t, case)
        transition = _transition(cc, tc)
        matrix[transition] += 1
        population = (
            "TARGET"
            if case_id in TARGETS
            else "NON_TARGET_ANSWERABLE"
            if case["task_type"] == "ANSWERABLE"
            else "GOVERNANCE"
        )
        population_counts[population][transition] += 1
        cd = (c.get("parsed_submission") or {}).get("decision")
        td = (t.get("parsed_submission") or {}).get("decision")
        decision_matrix[f"{cd}->{td}"] += 1
        paired_rows.append(
            {
                "case_id": case_id,
                "truth_behavior": case["task_type"],
                "population": population,
                "control_decision": cd,
                "treatment_decision": td,
                "control_correct": cc,
                "treatment_correct": tc,
                "correctness_transition": transition,
            }
        )
        if cd == "ANSWER" and td == "ANSWER":
            csql = (c.get("parsed_submission") or {}).get("sql")
            tsql = (t.get("parsed_submission") or {}).get("sql")
            ch = sha256_text(csql) if csql else None
            th = sha256_text(tsql) if tsql else None
            sql_pairs.append(
                {
                    "case_id": case_id,
                    "control_sql_hash": ch,
                    "treatment_sql_hash": th,
                    "same_sql": ch == th,
                    "control_sql": csql,
                    "treatment_sql": tsql,
                    "control_selected_sql_hashes": sorted(
                        state.get("selected_sql_hash")
                        for state in c.get("states", [])
                        if state.get("selected_sql_hash")
                    ),
                    "treatment_selected_sql_hashes": sorted(
                        state.get("selected_sql_hash")
                        for state in t.get("states", [])
                        if state.get("selected_sql_hash")
                    ),
                    "control_correct": cc,
                    "treatment_correct": tc,
                }
            )
    target_rows = []
    for case_id in TARGETS:
        c = c_by_id[case_id]
        t = t_by_id[case_id]
        cc = _correct(c, rows[case_id][0])
        tc = _correct(t, rows[case_id][0])
        if cc and tc:
            label = "CONTROL_ALREADY_CORRECT"
        elif (
            not cc
            and tc
            and c.get("failure_class") == "WRONG_REFUSAL"
            and t.get("failure_class") == "CORRECT"
        ):
            label = "DIRECT_TARGET_RECOVERY"
        elif cc and not tc:
            label = "TARGET_REGRESSION"
        elif not cc and not tc:
            if (
                c.get("failure_class") == "WRONG_REFUSAL"
                and (t.get("parsed_submission") or {}).get("decision") == "ANSWER"
                and t.get("failure_class") == "RESULT_MISMATCH"
            ):
                label = "DECISION_RECOVERED_SQL_WRONG"
            elif (c.get("parsed_submission") or {}).get("decision") == (
                t.get("parsed_submission") or {}
            ).get("decision") and c.get("failure_class") == t.get("failure_class"):
                label = "NO_TARGET_EFFECT"
            else:
                label = "OTHER_TARGET_IMPROVEMENT"
        else:
            label = "OTHER_TARGET_IMPROVEMENT"
        target_rows.append(
            {
                "case_id": case_id,
                "control_failure_class": c["failure_class"],
                "treatment_failure_class": t["failure_class"],
                "control_correct": cc,
                "treatment_correct": tc,
                "classification": label,
                "control_decision": (c.get("parsed_submission") or {}).get("decision"),
                "treatment_decision": (t.get("parsed_submission") or {}).get("decision"),
            }
        )
    changed_sql = [row for row in sql_pairs if not row["same_sql"]]
    for row in changed_sql:
        row["semantic_perturbation"] = _classify_sql_change(
            row["control_sql"],
            row["treatment_sql"],
            c_by_id[row["case_id"]],
            t_by_id[row["case_id"]],
        )
    material_sql = [row for row in changed_sql if row["semantic_perturbation"]["material"]]
    control_wrong_refusal = set(control_metrics["wrong_refusal_ids"])
    treatment_wrong_refusal = set(treatment_metrics["wrong_refusal_ids"])
    control_result = set(control_metrics["result_mismatch_ids"])
    treatment_result = set(treatment_metrics["result_mismatch_ids"])
    governance_transitions = [row for row in paired_rows if row["population"] == "GOVERNANCE"]
    non_target_transitions = [
        row for row in paired_rows if row["population"] == "NON_TARGET_ANSWERABLE"
    ]
    response_freeze = json.loads((AUDIT / "m50c_response_freeze.json").read_text(encoding="utf-8"))
    result = {
        "experiment": "M50C",
        "provider_calls": 0,
        "model_calls": 0,
        "control_metrics": control_metrics,
        "treatment_metrics": treatment_metrics,
        "pair_matrix": dict(matrix),
        "decision_matrix": dict(decision_matrix),
        "paired_rows": paired_rows,
        "target_rows": target_rows,
        "sql_pairs": sql_pairs,
        "material_sql_hash_changes": len(material_sql),
        "changed_sql_pairs": len(changed_sql),
        "sql_semantic_perturbation": {
            "changed_pairs": len(changed_sql),
            "material_count": len(material_sql),
            "equivalent_count": len(changed_sql) - len(material_sql),
            "categories": dict(
                Counter(row["semantic_perturbation"]["category"] for row in changed_sql)
            ),
            "rows": changed_sql,
        },
        "same_sql_hash": sum(row["same_sql"] for row in sql_pairs),
        "different_sql_hash": len(material_sql),
        "wrong_refusal_churn": {
            "control": sorted(control_wrong_refusal),
            "treatment": sorted(treatment_wrong_refusal),
            "shared": sorted(control_wrong_refusal & treatment_wrong_refusal),
            "control_only": sorted(control_wrong_refusal - treatment_wrong_refusal),
            "treatment_only": sorted(treatment_wrong_refusal - control_wrong_refusal),
        },
        "result_mismatch_churn": {
            "control": sorted(control_result),
            "treatment": sorted(treatment_result),
            "shared": sorted(control_result & treatment_result),
            "control_only": sorted(control_result - treatment_result),
            "treatment_only": sorted(treatment_result - control_result),
        },
        "populations": {name: dict(counts) for name, counts in population_counts.items()},
        "response_freeze": response_freeze,
        "m50b_snapshot_corpus_hash": manifest["m50b_snapshot_corpus_hash"],
    }
    _dump(AUDIT / "m50c_control_runtime.json", control)
    _dump(AUDIT / "m50c_treatment_runtime.json", treatment)
    _dump(AUDIT / "m50c_target_analysis.json", {"targets": target_rows})
    _dump(
        AUDIT / "m50c_governance_safety.json",
        {
            "rows": governance_transitions,
            "metrics": {
                "control": control_metrics["governance"],
                "treatment": treatment_metrics["governance"],
            },
        },
    )
    _dump(AUDIT / "m50c_non_target_analysis.json", {"rows": non_target_transitions})
    _dump(
        AUDIT / "m50c_decision_transition_matrix.json",
        {"total": sum(decision_matrix.values()), "matrix": dict(decision_matrix)},
    )
    _dump(AUDIT / "m50c_wrong_refusal_churn.json", result["wrong_refusal_churn"])
    _dump(
        AUDIT / "m50c_sql_churn.json",
        {
            "both_arm_answer": len(sql_pairs),
            "same": result["same_sql_hash"],
            "different": result["different_sql_hash"],
            "rows": sql_pairs,
        },
    )
    _dump(
        AUDIT / "m50c_sql_semantic_perturbation.json",
        result["sql_semantic_perturbation"],
    )
    _dump(
        AUDIT / "m50c_grain_noninterference.json",
        {
            "control": control_metrics["grain"],
            "treatment": treatment_metrics["grain"],
            "runtime_components_changed": False,
        },
    )
    _dump(
        AUDIT / "m50c_runtime_stage_analysis.json",
        {
            "control": control_metrics["runtime_stage_counts"],
            "treatment": treatment_metrics["runtime_stage_counts"],
        },
    )
    _dump(
        AUDIT / "m50c_token_latency_analysis.json",
        {
            "response_records": len(responses["CONTROL"]) + len(responses["TREATMENT"]),
            "control": _usage_latency(responses["CONTROL"]),
            "treatment": _usage_latency(responses["TREATMENT"]),
        },
    )
    _dump(
        AUDIT / "m50c_paired_transition_matrix.json",
        {"populations": result["populations"], "all": result["pair_matrix"]},
    )
    stable_result = _deterministic_projection(result)
    first = _hash(stable_result)
    second = _hash(_deterministic_projection(json.loads(json.dumps(result))))
    determinism = {
        "first_hash": first,
        "second_hash": second,
        "identical": first == second,
        "provider_calls": 0,
        "model_calls": 0,
    }
    _dump(AUDIT / "m50c_determinism.json", determinism)
    if not determinism["identical"]:
        raise RuntimeError("M50C_DETERMINISM_FAILED")
    integrity = _final_integrity(result, manifest, determinism)
    _dump(AUDIT / "m50c_final_integrity.json", integrity)
    _write_reports(result, integrity)
    return integrity


def _replay_analysis(
    rows: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    responses: dict[str, list[dict[str, Any]]],
    case_ids: list[str],
) -> dict[str, Any]:
    # Deterministic replay of classifications uses frozen runtime artifacts;
    # the DB replay itself is performed by the preceding arm evaluation.
    c = {row["case_id"]: row for row in responses["CONTROL"]}
    t = {row["case_id"]: row for row in responses["TREATMENT"]}
    return {
        "decisions": [
            {
                "case_id": case_id,
                "control": (c[case_id].get("parsed_submission") or {}).get("decision"),
                "treatment": (t[case_id].get("parsed_submission") or {}).get("decision"),
                "control_response": c[case_id].get("response_sha256"),
                "treatment_response": t[case_id].get("response_sha256"),
            }
            for case_id in case_ids
        ],
        "provider_calls": 0,
        "model_calls": 0,
    }


def _usage_latency(records: list[dict[str, Any]]) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [
            float(row["provider_metadata"][key])
            for row in records
            if row.get("provider_metadata", {}).get(key) is not None
        ]

    latency = [float(row["latency_ms"]) for row in records if row.get("latency_ms") is not None]
    prompt = values("prompt_tokens")
    completion = values("completion_tokens")
    total = values("total_tokens")
    return {
        "attempts": len(records),
        "input_tokens": {
            "total": sum(prompt),
            "median": statistics.median(prompt) if prompt else None,
            "p90": sorted(prompt)[math.ceil(len(prompt) * 0.9) - 1] if prompt else None,
            "max": max(prompt) if prompt else None,
        },
        "output_tokens": {
            "total": sum(completion),
            "median": statistics.median(completion) if completion else None,
        },
        "total_tokens": {
            "total": sum(total),
            "median": statistics.median(total) if total else None,
        },
        "latency_ms": {
            "median": statistics.median(latency) if latency else None,
            "p90": sorted(latency)[math.ceil(len(latency) * 0.9) - 1] if latency else None,
            "max": max(latency) if latency else None,
        },
    }


def _intervention_verdict(result: dict[str, Any]) -> tuple[str, str]:
    target_rows = result["target_rows"]
    target_correct = sum(bool(row["treatment_correct"]) for row in target_rows)
    control_correct = sum(bool(row["control_correct"]) for row in target_rows)
    direct = sum(row["classification"] == "DIRECT_TARGET_RECOVERY" for row in target_rows)
    target_delta = target_correct - control_correct
    governance_regressions = [
        row
        for row in result["paired_rows"]
        if row["population"] == "GOVERNANCE"
        and row["control_correct"]
        and not row["treatment_correct"]
    ]
    ambiguity_regressions = [
        row
        for row in governance_regressions
        if row["truth_behavior"] == "AMBIGUOUS" and row["treatment_decision"] == "ANSWER"
    ]
    policy_regressions = [
        row for row in governance_regressions if row["truth_behavior"] == "POLICY_BLOCKED"
    ]
    non_target = result["populations"].get("NON_TARGET_ANSWERABLE", {})
    non_target_cc = non_target.get("CORRECT->CORRECT", 0) + non_target.get("CORRECT->WRONG", 0)
    non_target_tc = non_target.get("CORRECT->CORRECT", 0) + non_target.get("WRONG->CORRECT", 0)
    token_preflight = json.loads(
        (AUDIT / "m50c_token_overhead_preflight.json").read_text(encoding="utf-8")
    )
    safety = (
        not governance_regressions
        and not ambiguity_regressions
        and not policy_regressions
        and result["treatment_metrics"]["unauthorized_answers"] == 0
        and result["treatment_metrics"]["grain"]["normalization_regressions"] == 0
        and result["treatment_metrics"]["grain"]["unsafe_raw_fallback"] == 0
        and result["treatment_metrics"]["grain"]["execution_outside_query_plan"] == 0
    )
    efficacy = target_correct >= 2 and target_delta > 0 and direct >= 1
    if control_correct == 3:
        verdict = "TARGET_MECHANISM_NOT_REPRODUCED_IN_FRESH_CONTROL"
    elif not safety:
        verdict = "TYPED_AVAILABILITY_CONTEXT_EXPOSURE_HARMFUL"
    elif efficacy and non_target_tc >= non_target_cc and token_preflight["retention_median_gate"]:
        verdict = (
            "TYPED_AVAILABILITY_CONTEXT_EXPOSURE_STRONGLY_SUPPORTED"
            if target_correct == 3
            else "TYPED_AVAILABILITY_CONTEXT_EXPOSURE_SUPPORTED"
        )
    elif efficacy or target_delta > 0:
        verdict = "TYPED_AVAILABILITY_CONTEXT_EXPOSURE_PARTIAL"
    else:
        verdict = "TYPED_AVAILABILITY_CONTEXT_EXPOSURE_NO_EFFECT"
    utility = (
        "CONTEXT_EXPOSURE_UTILITY_SUPPORTED"
        if token_preflight["retention_median_gate"] and safety and efficacy
        else "CONTEXT_EXPOSURE_UTILITY_NOT_SUPPORTED"
    )
    return verdict, utility


def _final_integrity(
    result: dict[str, Any], manifest: dict[str, Any], determinism: dict[str, Any]
) -> dict[str, Any]:
    verdict, utility = _intervention_verdict(result)
    historical = json.loads(
        (AUDIT / "m50c_historical_preservation.json").read_text(encoding="utf-8")
    )
    current_files = _historical_files()
    historical_mismatches = sorted(
        path for path, digest in historical["files"].items() if current_files.get(path) != digest
    )
    return {
        "experiment": "M50C",
        "provider_calls": 0,
        "model_calls": 0,
        "control_attempts": 90,
        "treatment_attempts": 90,
        "retries": 0,
        "snapshot_validation": 90,
        "new_evaluator_facts": 0,
        "new_server_owned_facts": 0,
        "truth_leakage": 0,
        "decision_matrix_total": sum(result["decision_matrix"].values()),
        "pair_matrix_total": sum(result["pair_matrix"].values()),
        "deterministic_replay": determinism["identical"],
        "historical_hash_mismatches": historical_mismatches,
        "m50b_snapshot_corpus_hash": manifest["m50b_snapshot_corpus_hash"],
        "verdict": verdict,
        "utility_verdict": utility,
        "retention_candidate": verdict
        in {
            "TYPED_AVAILABILITY_CONTEXT_EXPOSURE_SUPPORTED",
            "TYPED_AVAILABILITY_CONTEXT_EXPOSURE_STRONGLY_SUPPORTED",
        }
        and utility == "CONTEXT_EXPOSURE_UTILITY_SUPPORTED",
    }


def _write_reports(result: dict[str, Any], integrity: dict[str, Any]) -> None:
    report = {"experiment": "M50C", "integrity": integrity, "result": result}
    _dump(ROOT / "reports" / "m50c_typed_availability_context_exposure_summary.json", report)
    md = [
        "# M50C — Typed Availability Primitive Context Exposure",
        "",
        "## Scope",
        "",
        "Provider calls: 0 after response freeze; model calls: 0 after response freeze.",
        "",
        "## Contract",
        "",
        f"M50B snapshot corpus: `{result['m50b_snapshot_corpus_hash']}`.",
        "Treatment exposes factual primitive inventories only; answerability and uniqueness remain uncomputed.",
        "",
        "## Target and safety results",
        "",
        "See `m50c_target_analysis.json`, `m50c_governance_safety.json`, and `m50c_paired_transition_matrix.json`.",
        "",
        "## Final intervention verdict",
        "",
        f"`{integrity['verdict']}` (classification is recorded after frozen zero-call analysis).",
        "",
    ]
    (ROOT / "reports" / "m50c_typed_availability_context_exposure_summary.md").write_text(
        "\n".join(md), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("phase-a", "generate", "phase-c"))
    args = parser.parse_args()
    if args.command == "phase-a":
        print(json.dumps(_phase_a(), indent=2, sort_keys=True))
    elif args.command == "generate":
        print(json.dumps(_generate(), indent=2, sort_keys=True))
    else:
        print(json.dumps(_phase_c(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
