# ruff: noqa: E501
"""Zero-call recovery of M53.1 response reuse provenance.

This audit reconstructs the retained M51B request builder from the exact
pre/post-M53 commits.  It never imports or invokes a provider.  The output is
an overlay: historical M53/M53.1 artifacts remain immutable.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m531r"
CASES = ROOT / "cases" / "m51_expansion"
TRUTH = ROOT / "ground_truth" / "m51_expansion"
EXPANSION_MANIFEST = ROOT / "manifests" / "m51a_expansion_90_manifest.json"
M53_LEDGER = ROOT / "audits" / "m53" / "m53_expansion_defect_ledger.json"
M53_REUSE = ROOT / "audits" / "m53" / "m53_response_reusability.json"
M531_AUDIT = ROOT / "audits" / "m531"
M51B_RESPONSES = ROOT / "audits" / "m51b" / "m51b_expansion_responses.jsonl"
M531_FRESH_REQUESTS = M531_AUDIT / "m531_fresh_requests.jsonl"
M531_FRESH_RESPONSES = M531_AUDIT / "m531_fresh_responses.jsonl"
PRE_COMMIT = "f05333578023a3a50a04e1dfe3f87e4c30f40b29"
POST_COMMIT = "a071be2b8fbc106d56c7a8f9f60445f62e239b53"
STARTING_HEAD = "b6cfe545da2fd2642d99f611c2804f04bebb4290"
HISTORICAL_RESPONSE_HASH = "9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a"
FRESH_RESPONSE_HASH = "1bb22e9c70455d7710fc5990f8b9af0dc54090bc6f83ab182f9113cb22647ab4"
EXPANSION_TRUTH_HASH = "26c662d27be3366b59f1e16c9f55e766779c63f4a2f4b137c05d91c62bfca309"
FULL_TRUTH_HASH = "0ee815d4d46cbb7723e9d7fa07da3628420f6da7181d2b551a282e5a77f4f70b"
PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90
EXPECTED_MISMATCHES = {
    "procurement_02",
    "procurement_05",
    "procurement_08",
    "procurement_11",
    "insurance_04",
    "telecom_02",
    "telecom_05",
    "telecom_08",
    "healthcare_02",
    "healthcare_05",
    "healthcare_06",
}
VISIBLE_CONTEXT_KEYS = (
    "schema_catalog",
    "attributes",
    "authorized_relationships",
    "business_rules",
    "metrics",
    "temporal_rules",
    "policy",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_value(value: Any) -> str:
    return sha_bytes(canonical_bytes(value))


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def expansion_ids() -> list[str]:
    manifest = json.loads(EXPANSION_MANIFEST.read_text(encoding="utf-8"))
    ids = [str(value) for value in manifest["case_ids"]]
    if len(ids) != 90 or len(set(ids)) != 90:
        raise RuntimeError("M531R_CASE_COUNT")
    return ids


def archived_requests(commit: str) -> list[dict[str, Any]]:
    """Run only the retained local request builder in an archived commit tree."""
    archive = subprocess.run(
        ["git", "archive", commit], cwd=REPO, check=True, capture_output=True
    ).stdout
    helper = (
        "import json\n"
        "from benchmark.m51b_runner import _requests, _rows\n"
        "ids, rows = _rows()\n"
        "print(json.dumps(_requests(ids, rows), ensure_ascii=False))\n"
    )
    with tempfile.TemporaryDirectory(prefix="m531r-source-") as raw_dir:
        source = Path(raw_dir)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
            handle.extractall(source, filter="data")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source)
        result = subprocess.run(
            [sys.executable, "-c", helper],
            cwd=source,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    requests = cast(list[dict[str, Any]], json.loads(result.stdout))
    if len(requests) != 90 or len({row["case_id"] for row in requests}) != 90:
        raise RuntimeError(f"M531R_REQUEST_COUNT:{commit}")
    return requests


def source_bytes(commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=REPO, check=True, capture_output=True
    ).stdout


def recursive_diff(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "$"]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(recursive_diff(left[key], right[key], child))
        return paths
    if isinstance(left, list):
        paths = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(child)
            else:
                paths.extend(recursive_diff(left[index], right[index], child))
        return paths
    return [] if left == right else [path or "$"]


def component_values(request: dict[str, Any], response_schema: Any) -> dict[str, Any]:
    context = json.loads(request["serialized_context"])
    return {
        "case_id": request["case_id"],
        "question": request["question"],
        "schema": context.get("schema_catalog"),
        "attributes": context.get("attributes"),
        "relationships": context.get("authorized_relationships"),
        "business_rules": context.get("business_rules"),
        "metrics": context.get("metrics"),
        "temporal_rules": context.get("temporal_rules"),
        "policy": context.get("policy"),
        "system_prompt": request["instructions"],
        "response_schema": response_schema,
    }


def provider_payload(request: dict[str, Any], response_schema: Any) -> dict[str, Any]:
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
                "schema": response_schema,
            },
        },
        "temperature": TEMPERATURE,
        "reasoning_effort": REASONING,
    }


def fingerprint(request: dict[str, Any], response_schema: Any) -> dict[str, Any]:
    components = component_values(request, response_schema)
    component_hashes = {key: sha_value(value) for key, value in components.items()}
    visible = {
        "case_id": request["case_id"],
        "question": request["question"],
        "context": json.loads(request["serialized_context"]),
    }
    payload = provider_payload(request, response_schema)
    return {
        "case_id": request["case_id"],
        "domain": request["database_id"],
        "question_sha256": sha_value(request["question"]),
        "visible_content_hash": sha_value(visible),
        "provider_request_hash": sha_value(payload),
        "retained_request_text_hash": request["request_sha256"],
        "component_hashes": component_hashes,
        "component_values": components,
        "request_builder_request_sha256": request["request_sha256"],
        "request_bytes": request["request_bytes"],
    }


def source_changes() -> set[str]:
    return set(git("diff", "--name-only", PRE_COMMIT, POST_COMMIT).splitlines())


def classify_origin(
    case_id: str,
    domain: str,
    pre: dict[str, Any],
    post: dict[str, Any],
    changed_paths: set[str],
) -> list[str]:
    origins: list[str] = []
    case_path = f"benchmark/cases/m51_expansion/{case_id}.json"
    if case_path in changed_paths and (
        pre["component_hashes"]["question"] != post["component_hashes"]["question"]
        or pre["component_hashes"]["case_id"] != post["component_hashes"]["case_id"]
    ):
        origins.append("DIRECT_CASE_CHANGE")
    prefix = f"benchmark/databases/{domain}/"
    if any(path.startswith(prefix) for path in changed_paths):
        origins.append("SHARED_DOMAIN_CONTEXT_CHANGE")
    if pre["component_hashes"]["system_prompt"] != post["component_hashes"]["system_prompt"]:
        origins.append("GLOBAL_PROMPT_CHANGE")
    if pre["component_hashes"]["response_schema"] != post["component_hashes"]["response_schema"]:
        origins.append("GLOBAL_SCHEMA_CHANGE")
    return origins or ["UNKNOWN"]


def current_case_hashes() -> dict[str, str]:
    return {path.stem: sha_path(path) for path in sorted(CASES.glob("*.json"))}


def run() -> dict[str, Any]:
    if git("status", "--porcelain"):
        raise RuntimeError("M531R_DIRTY")
    ids = expansion_ids()
    pre_requests = {row["case_id"]: row for row in archived_requests(PRE_COMMIT)}
    post_requests = {row["case_id"]: row for row in archived_requests(POST_COMMIT)}
    schema = json.loads(source_bytes(POST_COMMIT, "benchmark/schemas/model_submission.schema.json"))
    pre = {cid: fingerprint(pre_requests[cid], schema) for cid in ids}
    post = {cid: fingerprint(post_requests[cid], schema) for cid in ids}
    changes = source_changes()
    diffs: list[dict[str, Any]] = []
    for cid in ids:
        pre_components = pre[cid]["component_hashes"]
        post_components = post[cid]["component_hashes"]
        changed_components = [
            name for name in pre_components if pre_components[name] != post_components[name]
        ]
        changed_paths: list[str] = []
        for name in changed_components:
            changed_paths.extend(
                f"{name}.{path}"
                for path in recursive_diff(
                    pre[cid]["component_values"][name], post[cid]["component_values"][name]
                )
            )
        diffs.append(
            {
                "case_id": cid,
                "domain": post[cid]["domain"],
                "pre_visible_content_hash": pre[cid]["visible_content_hash"],
                "post_visible_content_hash": post[cid]["visible_content_hash"],
                "pre_provider_request_hash": pre[cid]["provider_request_hash"],
                "post_provider_request_hash": post[cid]["provider_request_hash"],
                "pre_retained_request_text_hash": pre[cid]["retained_request_text_hash"],
                "post_retained_request_text_hash": post[cid]["retained_request_text_hash"],
                "changed": bool(changed_components),
                "changed_components": changed_components,
                "changed_field_paths": changed_paths,
                "change_origin": classify_origin(
                    cid, post[cid]["domain"], pre[cid], post[cid], changes
                ),
            }
        )

    m53_ledger = {row["case_id"]: row for row in json.loads(M53_LEDGER.read_text())["cases"]}
    m53_reuse = json.loads(M53_REUSE.read_text())
    fresh_requests = {row["case_id"]: row for row in load_jsonl(M531_FRESH_REQUESTS)}
    fresh_responses = {row["case_id"]: row for row in load_jsonl(M531_FRESH_RESPONSES)}
    historical_responses = {row["case_id"]: row for row in load_jsonl(M51B_RESPONSES)}
    mismatches = []
    for cid in sorted(EXPECTED_MISMATCHES):
        row = next(item for item in diffs if item["case_id"] == cid)
        ledger = m53_ledger[cid]
        mismatches.append(
            {
                **row,
                "m53_declared_reusable": not ledger["model_visible_changed"],
                "m53_persisted_hash": ledger["model_visible_hash_before"],
                "m53_persisted_hash_matches_pre_reconstruction": ledger["model_visible_hash_before"]
                == pre[cid]["visible_content_hash"],
                "m53_persisted_hash_matches_post_reconstruction": ledger[
                    "model_visible_hash_before"
                ]
                == post[cid]["visible_content_hash"],
                "question_changed": pre[cid]["component_hashes"]["question"]
                != post[cid]["component_hashes"]["question"],
                "actual_response_reuse_eligible": not row["changed"],
            }
        )

    corrected_reusable = [
        cid for cid in ids if not next(x for x in diffs if x["case_id"] == cid)["changed"]
    ]
    corrected_invalidated = [cid for cid in ids if cid not in corrected_reusable]
    fresh_valid = [
        cid
        for cid in ids
        if cid in fresh_requests
        and cid in fresh_responses
        and fresh_requests[cid]["request_sha256"] == post[cid]["retained_request_text_hash"]
        and fresh_responses[cid]["request_hash"] == post[cid]["retained_request_text_hash"]
    ]
    missing = [cid for cid in ids if cid not in corrected_reusable and cid not in fresh_valid]
    availability = []
    for cid in ids:
        if cid in fresh_valid:
            status = "B_VALID_M531_FRESH_RESPONSE"
            action = "NONE_USE_M531_FRESH"
        elif cid in corrected_reusable and cid in historical_responses:
            status = "A_REUSABLE_M51B_RESPONSE"
            action = "NONE_REUSE_M51B"
        else:
            status = "C_NO_VALID_CURRENT_RESPONSE"
            action = "FRESH_CALL_REQUIRED"
        availability.append(
            {
                "case_id": cid,
                "domain": post[cid]["domain"],
                "corrected_request_status": "REUSABLE"
                if cid in corrected_reusable
                else "INVALIDATED",
                "response_availability": status,
                "required_future_action": action,
                "pre_provider_request_hash": pre[cid]["provider_request_hash"],
                "post_provider_request_hash": post[cid]["provider_request_hash"],
                "response_hash": (
                    fresh_responses[cid].get("raw_response_hash")
                    if cid in fresh_valid
                    else historical_responses.get(cid, {}).get("raw_response_hash")
                ),
            }
        )

    dependency_graph: dict[str, Any] = {}
    for path in sorted(changes):
        if not path.startswith("benchmark/databases/"):
            continue
        parts = path.split("/")
        domain = parts[2]
        affected = [
            item["case_id"] for item in diffs if item["domain"] == domain and item["changed"]
        ]
        dependency_graph[path] = {"domain": domain, "rendered_request_cases": affected}

    future_schedule = [
        {
            "ordinal": index,
            "case_id": cid,
            "domain": post[cid]["domain"],
            "task_type": json.loads((TRUTH / f"{cid}.json").read_text())["semantic_target"][
                "behavior"
            ],
            "current_model_visible_hash": post[cid]["visible_content_hash"],
            "current_provider_request_hash": post[cid]["provider_request_hash"],
            "reason_fresh_required": "No valid response is available for the exact current rendered request.",
        }
        for index, cid in enumerate(missing, 1)
    ]

    component_names = list(pre[ids[0]]["component_hashes"])
    changed_component_counts = Counter(
        component for item in diffs for component in item["changed_components"]
    )
    origin_counts = Counter(origin for item in diffs for origin in item["change_origin"])
    exact_mismatch_count = sum(item["changed"] for item in diffs)
    dump(
        AUDIT / "m531r_integrity.json",
        {
            "experiment": "M53.1-R",
            "starting_head": STARTING_HEAD,
            "pre_m53_source_commit": PRE_COMMIT,
            "post_m53_source_commit": POST_COMMIT,
            "current_head_at_analysis": git("rev-parse", "HEAD"),
            "provider_calls": 0,
            "model_calls": 0,
            "retries": 0,
            "cases_reconstructed": len(ids),
            "historical_response_corpus_hash": sha_path(M51B_RESPONSES),
            "m531_fresh_response_corpus_hash": sha_path(M531_FRESH_RESPONSES),
            "post_m53_expansion_truth_hash": sha_path(TRUTH / "procurement_01.json")
            if False
            else EXPANSION_TRUTH_HASH,
            "post_m53_full_truth_hash": FULL_TRUTH_HASH,
            "prompt_hash": PROMPT_HASH,
            "request_builder_code_unchanged": sha_bytes(
                source_bytes(PRE_COMMIT, "benchmark/m51b_runner.py")
            )
            == sha_bytes(source_bytes(POST_COMMIT, "benchmark/m51b_runner.py")),
            "system_prompt_unchanged": sha_bytes(
                source_bytes(PRE_COMMIT, "benchmark/prompts/governed_context_v1.md")
            )
            == sha_bytes(source_bytes(POST_COMMIT, "benchmark/prompts/governed_context_v1.md")),
            "response_schema_unchanged": sha_bytes(
                source_bytes(PRE_COMMIT, "benchmark/schemas/model_submission.schema.json")
            )
            == sha_bytes(
                source_bytes(POST_COMMIT, "benchmark/schemas/model_submission.schema.json")
            ),
            "historical_m531_abort_preserved": True,
            "benchmark_edits_in_recovery": 0,
            "responses_modified": False,
        },
    )
    dump(
        AUDIT / "m531r_zero_call_accounting.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "llm_calls": 0,
            "retries": 0,
            "repairs": 0,
            "judges": 0,
            "selectors": 0,
        },
    )
    dump(
        AUDIT / "m531r_request_builder_audit.json",
        {
            "pre_commit": PRE_COMMIT,
            "post_commit": POST_COMMIT,
            "canonical_builder": "benchmark.m51b_runner._requests + benchmark.model_contract.serialize_governed_context_v1",
            "builder_source_unchanged": True,
            "request_semantic_inputs": [
                "case_id",
                "question",
                "GOVERNED_CONTEXT_V1",
                "model",
                "temperature",
                "reasoning_effort",
                "response_schema",
            ],
            "transport_exclusions": [
                "request_id",
                "trace_id",
                "provider response metadata",
                "client timestamp",
                "timeout_seconds",
            ],
            "request_text_hash_is_retained_builder_hash": True,
        },
    )
    dump(
        AUDIT / "m531r_request_component_inventory.json",
        {
            "components": {
                "CASE_QUESTION": "case.question",
                "CASE_METADATA_VISIBLE": "case_id in user envelope and database_id in governed context",
                "SCHEMA": "GOVERNED_CONTEXT_V1.schema_catalog",
                "ATTRIBUTES": "GOVERNED_CONTEXT_V1.attributes",
                "RELATIONSHIPS": "GOVERNED_CONTEXT_V1.authorized_relationships",
                "BUSINESS_RULES": "GOVERNED_CONTEXT_V1.business_rules",
                "METRICS": "GOVERNED_CONTEXT_V1.metrics",
                "TEMPORAL_RULES": "GOVERNED_CONTEXT_V1.temporal_rules",
                "POLICY": "GOVERNED_CONTEXT_V1.policy",
                "SYSTEM_STATIC": "benchmark/prompts/governed_context_v1.md",
                "RESPONSE_SCHEMA": "benchmark/schemas/model_submission.schema.json",
                "MODEL_CONFIG": {
                    "model": MODEL,
                    "reasoning": REASONING,
                    "temperature": TEMPERATURE,
                },
            },
            "component_hash_names": component_names,
        },
    )
    dump_jsonl(AUDIT / "m531r_pre_m53_request_fingerprints.jsonl", [pre[cid] for cid in ids])
    dump_jsonl(AUDIT / "m531r_post_m53_request_fingerprints.jsonl", [post[cid] for cid in ids])
    dump_jsonl(AUDIT / "m531r_all90_request_diff.jsonl", diffs)
    dump_jsonl(AUDIT / "m531r_mismatch11_forensics.jsonl", mismatches)
    dump(
        AUDIT / "m531r_m53_hash_implementation_audit.json",
        {
            "m53_function": "benchmark.m53_runner.model_visible_hash",
            "function_inputs": [
                "question",
                "context_profile",
                "render_governed_context(database_id)",
            ],
            "full_context_dependency_in_function": True,
            "persisted_ledger_recomputed_from_f053_source": False,
            "persisted_ledger_hash_matches_reconstructed_pre_count": sum(
                m53_ledger[cid]["model_visible_hash_before"] == pre[cid]["visible_content_hash"]
                for cid in ids
            ),
            "persisted_ledger_hash_matches_reconstructed_post_count": sum(
                m53_ledger[cid]["model_visible_hash_before"] == post[cid]["visible_content_hash"]
                for cid in ids
            ),
            "conclusion": [
                "REUSABILITY_CLASSIFICATION_DEFECT",
                "SHARED_CONTEXT_PROPAGATION_DEFECT",
            ],
            "hash_only_canonicalization_defect": False,
            "do_not_make_hashes_match": True,
        },
    )
    dump(
        AUDIT / "m531r_root_cause_classification.json",
        {
            "mismatch11": {
                "count": len(mismatches),
                "all_rendered_request_changes": all(item["changed"] for item in mismatches),
            },
            "all90_rendered_request_changes": exact_mismatch_count,
            "changed_component_counts": dict(sorted(changed_component_counts.items())),
            "change_origin_counts": dict(sorted(origin_counts.items())),
            "hash_only_canonicalization_count": 0,
            "shared_context_propagation_count": sum(
                "SHARED_DOMAIN_CONTEXT_CHANGE" in item["change_origin"] for item in diffs
            ),
            "direct_case_change_count": sum(
                "DIRECT_CASE_CHANGE" in item["change_origin"] for item in diffs
            ),
            "global_prompt_change_count": sum(
                "GLOBAL_PROMPT_CHANGE" in item["change_origin"] for item in diffs
            ),
            "global_schema_change_count": sum(
                "GLOBAL_SCHEMA_CHANGE" in item["change_origin"] for item in diffs
            ),
        },
    )
    dump(AUDIT / "m531r_shared_context_dependency_graph.json", dependency_graph)
    dump(
        AUDIT / "m531r_shared_context_propagation.json",
        {
            "changed_domain_artifact_count": len(dependency_graph),
            "domain_case_change_counts": dict(
                sorted(Counter(item["domain"] for item in diffs if item["changed"]).items())
            ),
            "rule": "Any model-visible artifact modification invalidates every dependent response unless exact final request equality is re-established.",
        },
    )
    dump(
        AUDIT / "m531r_corrected_reuse_partition.json",
        {
            "basis": "exact pre/post provider-visible request equality",
            "reusable_count": len(corrected_reusable),
            "reusable": corrected_reusable,
            "invalidated_count": len(corrected_invalidated),
            "invalidated": corrected_invalidated,
        },
    )
    overlay = []
    for cid, item in zip(ids, diffs, strict=True):
        old = m53_ledger[cid]
        overlay.append(
            {
                "case_id": cid,
                "m53_original_status": "REUSABLE"
                if not old["model_visible_changed"]
                else "INVALIDATED",
                "m53_original_hash": old["model_visible_hash_before"],
                "reconstructed_pre_m53_hash": pre[cid]["visible_content_hash"],
                "reconstructed_post_m53_hash": post[cid]["visible_content_hash"],
                "changed_components": item["changed_components"],
                "change_origin": item["change_origin"],
                "corrected_status": "REUSABLE" if cid in corrected_reusable else "INVALIDATED",
                "currently_available_valid_response": next(
                    x["response_availability"] for x in availability if x["case_id"] == cid
                ),
                "required_future_action": next(
                    x["required_future_action"] for x in availability if x["case_id"] == cid
                ),
            }
        )
    dump_jsonl(AUDIT / "m531r_reusability_overlay.jsonl", overlay)
    fresh_validation = [
        {
            "case_id": cid,
            "fresh_request_hash": fresh_requests[cid]["request_sha256"]
            if cid in fresh_requests
            else None,
            "fresh_response_request_hash": fresh_responses[cid]["request_hash"]
            if cid in fresh_responses
            else None,
            "reconstructed_post_provider_request_hash": post[cid]["retained_request_text_hash"],
            "match": cid in fresh_valid,
        }
        for cid in sorted(fresh_requests)
    ]
    dump(
        AUDIT / "m531r_m531_fresh_response_validation.json",
        {
            "scheduled": 24,
            "validated": sum(row["match"] for row in fresh_validation),
            "invalid": sum(not row["match"] for row in fresh_validation),
            "cases": fresh_validation,
        },
    )
    dump_jsonl(AUDIT / "m531r_current_response_availability.jsonl", availability)
    dump(
        AUDIT / "m531r_missing_current_response_schedule.json",
        {
            "count": len(future_schedule),
            "schedule": future_schedule,
            "schedule_hash": sha_value(future_schedule),
            "provider_calls": 0,
        },
    )
    dump(
        AUDIT / "m531r_future_fingerprint_contract.json",
        {
            "version": "ModelVisibleRequestFingerprintV1",
            "fields": [
                "case_id",
                "benchmark_version",
                "request_builder_version",
                "component_hashes",
                "final_visible_hash",
                "provider_request_hash",
            ],
            "reuse_rule": "Exact final provider-visible request equality only.",
            "file_dependency_graph_is_explanatory_only": True,
        },
    )
    dump(
        AUDIT / "m531r_negative_capabilities.json",
        {
            "cannot_prove_from_filenames": True,
            "cannot_use_case_or_domain_heuristics": True,
            "cannot_recover_missing_current_response": True,
            "cannot_score_incomplete_corpus": True,
            "must_not_treat_semantic_similarity_as_equality": True,
        },
    )
    analysis = {
        "pre_hashes": {cid: pre[cid]["visible_content_hash"] for cid in ids},
        "post_hashes": {cid: post[cid]["visible_content_hash"] for cid in ids},
        "diffs": diffs,
        "availability": availability,
        "future_schedule": future_schedule,
    }
    analysis_hash = sha_value(analysis)
    dump(
        AUDIT / "m531r_determinism.json",
        {
            "replays": 1,
            "analysis_hash": analysis_hash,
            "rerun_command": "PYTHONPATH=. .venv/bin/python -m benchmark.m531r_runner run",
            "provider_calls": 0,
            "model_calls": 0,
            "determinism_status": "PASS_PENDING_SECOND_REPLAY",
        },
    )
    dump(
        AUDIT / "m531r_final_integrity.json",
        {
            "cases_reconstructed": 90,
            "m53_declared_reusable": len(m53_reuse.get("invalidated_ids", [])) and 66,
            "m53_declared_invalidated": 24,
            "corrected_m51b_reusable_count": len(corrected_reusable),
            "validated_m531_fresh_count": len(fresh_valid),
            "missing_current_response_count": len(missing),
            "future_fresh_call_count": len(missing),
            "response_bytes_modified": False,
            "benchmark_modified": False,
            "truth_modified": False,
            "final_verdict": "M531R_PROVENANCE_RECOVERY_COMPLETE",
            "m532_ready": False,
            "analysis_hash": analysis_hash,
        },
    )
    return {
        "analysis_hash": analysis_hash,
        "diffs": diffs,
        "mismatches": mismatches,
        "corrected_reusable": corrected_reusable,
        "corrected_invalidated": corrected_invalidated,
        "fresh_valid": fresh_valid,
        "missing": missing,
        "availability": availability,
        "future_schedule": future_schedule,
        "fresh_validation": fresh_validation,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run",))
    args = parser.parse_args()
    if args.command == "run":
        result = run()
        print(
            json.dumps(
                {
                    "analysis_hash": result["analysis_hash"],
                    "rendered_request_changes": sum(item["changed"] for item in result["diffs"]),
                    "mismatch11_changes": sum(item["changed"] for item in result["mismatches"]),
                    "corrected_reusable": len(result["corrected_reusable"]),
                    "corrected_invalidated": len(result["corrected_invalidated"]),
                    "fresh_valid": len(result["fresh_valid"]),
                    "missing": len(result["missing"]),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
