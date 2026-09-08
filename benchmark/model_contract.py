"""Frozen, provider-agnostic model request contract for the M35 baseline."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The contract intentionally keeps the generated request readable.
# ruff: noqa: E501
from benchmark.context import load_authority, render_governed_context

ROOT = Path(__file__).resolve().parent
PROMPT_PATH = ROOT / "prompts" / "governed_context_v1.md"
SUBMISSION_SCHEMA_PATH = ROOT / "schemas" / "model_submission.schema.json"
SPLIT_PATH = ROOT / "splits" / "pilot.json"
FROZEN_CONTENT_EXCLUDED = {"model_submission.schema.json", "m35_model_result.schema.json"}
FORBIDDEN_REQUEST_TERMS = (
    "semantic_target",
    "reference_implementation",
    "counterfactual_fixtures",
    "semantic_mutants",
    "expected_results",
    "expected behavior",
    "missing_authority",
    "interpretation_a",
    "interpretation_b",
    "difficulty",
    "mechanism_tags",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def governance_instructions() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def submission_schema() -> dict[str, Any]:
    return json.loads(SUBMISSION_SCHEMA_PATH.read_text(encoding="utf-8"))


def _ordered_context(value: dict[str, Any]) -> dict[str, Any]:
    list_keys = {
        "schema_catalog": "entity_id",
        "attributes": "attribute_id",
        "authorized_relationships": "relationship_id",
        "metrics": "metric_id",
        "business_rules": "rule_id",
        "temporal_rules": "temporal_rule_id",
    }
    result: dict[str, Any] = {}
    for key in sorted(value):
        item = value[key]
        if key in list_keys and isinstance(item, list):
            item = sorted(item, key=lambda entry: str(entry.get(list_keys[key], "")))
        result[key] = item
    return result


def serialize_governed_context_v1(database_id: str) -> str:
    """Serialize the complete database-level context with stable bytes."""
    context = _ordered_context(render_governed_context(database_id))
    return json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def context_hash(database_id: str) -> str:
    return sha256_text(serialize_governed_context_v1(database_id))


def pilot_case_ids() -> list[str]:
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    return [str(case_id) for case_id in split["case_ids"]]


def case_order_hash(case_ids: list[str] | None = None) -> str:
    ids = case_ids if case_ids is not None else pilot_case_ids()
    return sha256_text(json.dumps(ids, separators=(",", ":"), ensure_ascii=False))


def _load_model_case(case_id: str) -> dict[str, Any]:
    path = ROOT / "cases" / "pilot" / f"{case_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class BenchmarkRequest:
    case_id: str
    database_id: str
    instructions: str
    question: str
    serialized_context: str
    submission_schema: dict[str, Any]
    request_text: str

    @property
    def request_bytes(self) -> int:
        return len(self.request_text.encode("utf-8"))

    @property
    def request_sha256(self) -> str:
        return sha256_text(self.request_text)


def build_benchmark_request(case_id: str) -> BenchmarkRequest:
    case = _load_model_case(case_id)
    instructions = governance_instructions()
    question = str(case["question"])
    context = serialize_governed_context_v1(str(case["database_id"]))
    request_text = (
        "SYSTEM:\n"
        + instructions
        + "\n\nUSER:\nQuestion:\n"
        + question
        + "\n\nGoverned context:\n"
        + context
    )
    return BenchmarkRequest(
        case_id=case_id,
        database_id=str(case["database_id"]),
        instructions=instructions,
        question=question,
        serialized_context=context,
        submission_schema=submission_schema(),
        request_text=request_text,
    )


def build_all_requests() -> list[BenchmarkRequest]:
    return [build_benchmark_request(case_id) for case_id in pilot_case_ids()]


def request_leakage(request: BenchmarkRequest, case: dict[str, Any] | None = None) -> list[str]:
    text = request.request_text.lower()
    hits = [term for term in FORBIDDEN_REQUEST_TERMS if term in text]
    if case is not None and case.get("task_type"):
        task_type = str(case["task_type"]).lower()
        if re.search(rf"(?<![a-z0-9_]){re.escape(task_type)}(?![a-z0-9_])", text):
            hits.append("case_task_type")
    return sorted(set(hits))


def context_contains_fact(database_id: str, fact: str) -> bool:
    context = serialize_governed_context_v1(database_id)
    section, _, raw_id = fact.partition(".")
    suffix = raw_id.replace(".", ":")
    if section == "relationships":
        return any(
            str(item.get("relationship_id", "")).endswith(suffix)
            for item in load_authority(database_id)["relationships"]
            if item.get("authorized") is True
        )
    if section == "policy":
        return "readonly" in context.lower()
    return (
        suffix in context
        or raw_id in context
        or raw_id.replace(".", ":", 1) in context
        or raw_id.replace(".", ":") in context
        or raw_id.replace(":", ".") in context
    )


def git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, check=True, capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        return "UNAVAILABLE"


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def frozen_benchmark_content_hash() -> str:
    """Hash M34.2 truth inputs while excluding M34.3 harness-only schemas."""
    digest = hashlib.sha256()
    paths = (
        [ROOT / "authoring.py"]
        + sorted((ROOT / "schemas").rglob("*"))
        + sorted((ROOT / "databases").rglob("*"))
        + sorted((ROOT / "cases").rglob("*"))
        + sorted((ROOT / "ground_truth").rglob("*"))
    )
    for path in paths:
        if path.is_file() and path.name not in FROZEN_CONTENT_EXCLUDED:
            digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()
