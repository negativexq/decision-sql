"""Frozen, provider-agnostic model request contract for the M35 baseline."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from app.generation.decision_contract import production_decision_schema

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
    # Historical benchmark callers retain this compatibility entry point, but
    # the canonical Candidate C wire schema is product-owned.
    return production_decision_schema()


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
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


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
    def user_text(self) -> str:
        """The exact USER envelope sent to the provider chat message."""
        return (
            "Case ID:\n"
            + self.case_id
            + "\n\nQuestion:\n"
            + self.question
            + "\n\nGoverned context:\n"
            + self.serialized_context
        )

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
        + "\n\nUSER:\nCase ID:\n"
        + case_id
        + "\n\nQuestion:\n"
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
    """Check a required context fact against structured authority.

    M38 introduced colon-delimited fact IDs while the historical pilot uses
    dotted legacy IDs.  Both are accepted, but neither is checked by raw
    substring containment.  Unknown or malformed facts fail closed.
    """
    authority = load_authority(database_id)
    section, separator, raw = fact.partition(":")
    if not separator:
        section, separator, raw = fact.partition(".")
    if not section or not raw:
        return False

    normalized_section = section
    if section in {"relationships", "relationship"}:
        relationship_id = raw if raw.startswith("relationship:") else f"relationship:{raw}"
        return any(
            str(item.get("relationship_id", "")) == relationship_id
            and item.get("authorized") is True
            for item in authority["relationships"]
        )

    legacy_prefixes = {
        "relationships.relationship": "relationships",
        "metrics.metric": "metrics",
        "business_rules.rule": "business_rules",
        "temporal_rules.time": "temporal_rules",
        "policy.policy": "policy",
    }
    if section in legacy_prefixes:
        normalized_section = legacy_prefixes[section]

    if normalized_section == "relationships":
        relationship_id = raw if raw.startswith("relationship:") else f"relationship:{raw}"
        return any(
            str(item.get("relationship_id", "")) == relationship_id
            and item.get("authorized") is True
            for item in authority["relationships"]
        )

    if normalized_section == "attributes":
        pieces = raw.split(":")
        if len(pieces) == 3:
            db, entity, physical = pieces
            if db != database_id or not entity or not physical:
                return False
            return any(
                str(item.get("entity_id", "")).endswith(f":{entity}")
                and str(item.get("physical_column_or_path", "")) == physical
                for item in authority["attributes"]
            )
        # Historical dotted form: attributes.table.column or attributes.table.payload.path
        pieces = raw.split(".")
        if len(pieces) >= 2:
            entity, physical = pieces[0], ".".join(pieces[1:])
            return any(
                str(item.get("entity_id", "")).endswith(f":{entity}")
                and (
                    str(item.get("physical_column_or_path", "")) == physical
                    or str(item.get("attribute_id", "")).endswith(f":{physical}")
                )
                for item in authority["attributes"]
            )
        return False

    if normalized_section == "entities":
        return any(
            str(item.get("entity_id", "")) == raw
            or str(item.get("entity_id", "")).endswith(f":{raw}")
            for item in authority["entities"]
        )

    collections = {
        "metrics": "metric_id",
        "business_rules": "rule_id",
        "temporal_rules": "temporal_rule_id",
    }
    if normalized_section in collections:
        key = collections[normalized_section]
        if raw.startswith(f"{normalized_section[:-1]}:"):
            expected = raw
        else:
            expected = raw
        return any(
            str(item.get(key, "")) == expected
            or str(item.get(key, "")).endswith(f":{expected.split(':')[-1]}")
            for item in authority[normalized_section]
        )

    if normalized_section == "policy":
        policy_id = str(authority["policy"].get("policy_id", ""))
        return raw == policy_id or raw == policy_id.split(":")[-1] or raw.endswith(":readonly")
    return False


def git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent,
            check=True,
            capture_output=True,
            text=True,
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
        if (
            path.is_file()
            and path.name not in FROZEN_CONTENT_EXCLUDED
            and path.suffix != ".pyc"
            and "__pycache__" not in path.parts
        ):
            digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()
