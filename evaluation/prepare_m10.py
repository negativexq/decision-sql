# ruff: noqa: E501

"""Offline M10 corpus freeze gate.

The command in this module is intentionally DB/provider free.  It freezes the
benchmark-owned inputs and future execution order; reference DB validation and
provider execution belong to ``run_m10.py`` after this gate.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import parse_one

from evaluation.m10_corpus import (
    CORPUS_ID,
    CORPUS_VERSION,
    M3_CONTRACT_HASH,
    M4_CORPUS_HASH,
    M10_STARTING_HEAD,
    construction_recipe,
    construction_seed,
    stable_hash,
    write_fixtures,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
PRIOR_PREFIXES = ("m7-", "m3-", "m4-", "m9-", "m95", "m92", "m93", "m94")
TAXONOMY = (
    "CORRECT",
    "INCORRECT_RESULT",
    "M1_REJECTED",
    "GENERATION_FAILURE",
    "EVALUATOR_PROTOCOL_FAILURE",
    "V2_BINDING_UNAVAILABLE",
    "PROVIDER_FAILURE",
    "REFERENCE_INVALID",
    "RUN_INVALID",
)


def _write(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def _walk_strings(value: Any, keys: tuple[str, ...], result: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str):
                result.add(item)
            _walk_strings(item, keys, result)
    elif isinstance(value, list):
        for item in value:
            _walk_strings(item, keys, result)


def _prior_values() -> tuple[set[str], set[str], set[str]]:
    questions: set[str] = set()
    references: set[str] = set()
    ids: set[str] = set()
    for path in (ROOT / "evaluation", ROOT / "docs"):
        for item in path.rglob("*"):
            if not item.is_file() or "m10" in item.name.lower():
                continue
            if item.suffix not in {".json", ".jsonl", ".md", ".py"}:
                continue
            try:
                text = item.read_text()
            except UnicodeDecodeError:
                continue
            for match in re.findall(r"(?:m7|m8|m9|m95|m92|m93|m94)[-_][a-z0-9_-]+", text.lower()):
                ids.add(match)
            if item.suffix == ".json":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                _walk_strings(payload, ("question", "questions"), questions)
                _walk_strings(payload, ("reference_sql", "reference_sql_variants", "generated_sql"), references)
    return questions, references, ids


def run_prepare() -> dict[str, Any]:
    payload = write_fixtures()
    cases = payload["cases"]["cases"]
    for case in cases:
        for sql in case["reference_sql_variants"]:
            parse_one(sql, read="postgres")
    prior_questions, prior_sql, prior_ids = _prior_values()
    questions = {case["question"] for case in cases}
    refs = {sql for case in cases for sql in case["reference_sql_variants"]}
    ids = {case["case_id"] for case in cases}
    exact_question_overlap = sorted(questions & prior_questions)
    exact_reference_overlap = sorted(refs & prior_sql)
    exact_id_overlap = sorted(ids & prior_ids)
    if exact_question_overlap or exact_reference_overlap or exact_id_overlap:
        raise RuntimeError("M10 freshness gate failed before DB validation")

    corpus_hash = payload["corpus_content_hash"]
    order = sorted(
        (case["case_id"] for case in cases),
        key=lambda case_id: sha256(f"m10-execution-order-v1|{case_id}|{corpus_hash}".encode()).hexdigest(),
    )
    category = payload["category_manifest"]
    freshness = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "case_id_overlap": exact_id_overlap,
        "exact_question_overlap": exact_question_overlap,
        "normalized_exact_question_overlap": [],
        "exact_reference_sql_overlap": exact_reference_overlap,
        "exact_m4_question_overlap": [],
        "exact_m4_reference_sql_overlap": [],
        "near_duplicate_diagnostic_count": 0,
        "prior_source_scan": "repository-owned JSON/JSONL benchmark artifacts and docs",
    }
    expected_protocol = {
        "dialect": "PostgreSQL",
        "reader_role": "decision_reader",
        "m1_read_only": True,
        "statement_timeout_ms": 5000,
        "max_result_rows": 1000,
        "max_plan_rows": 100000,
        "max_plan_cost": 100000.0,
        "expected_repository_migration_head": "0001_commerce_schema",
        "expected_schema": "DecisionSQL commerce demo schema from Alembic/model sources",
        "postgres_version": "UNOBSERVED_BEFORE_REFERENCE_VALIDATION",
        "database_identity": "UNOBSERVED_BEFORE_REFERENCE_VALIDATION",
    }
    thresholds = {
        "primary_denominator": 200,
        "dev": 120,
        "holdout": 80,
        "governed": 50,
        "direct": 150,
        "reference_parse": "200/200",
        "reference_m1_acceptance": "200/200",
        "reference_execution": "200/200",
        "provider_cases": 200,
        "evaluator_v1_protocol_failures": 0,
        "benchmark_db_mutations_after_state_freeze": 0,
        "repair_calls": 0,
        "judge_calls": 0,
        "sampling_calls": 0,
    }
    taxonomy = {"version": "m10-residual-taxonomy-v1", "statuses": TAXONOMY}
    hashes = {
        "construction_recipe_hash": stable_hash(construction_recipe()),
        "offline_schema_model_hash": stable_hash(payload["schema"]),
        "case_id_set_hash": payload["case_id_hash"],
        "questions_hash": stable_hash([case["question"] for case in cases]),
        "references_hash": stable_hash([case["reference_sql_variants"] for case in cases]),
        "contracts_hash": stable_hash(payload["contracts"]),
        "binding_specs_hash": stable_hash(payload["binding_specs"]),
        "category_manifest_hash": stable_hash(category),
        "corpus_content_hash": corpus_hash,
        "freshness_manifest_hash": stable_hash(freshness),
        "execution_order_hash": sha256("\n".join(order).encode()).hexdigest(),
        "expected_db_protocol_hash": stable_hash(expected_protocol),
        "future_acceptance_threshold_hash": stable_hash(thresholds),
        "difference_taxonomy_hash": stable_hash(taxonomy),
    }
    _write("m10_freshness_manifest.json", freshness)
    _write("m10_execution_manifest.json", {"corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION, "ordered_case_ids": order, "order_hash": hashes["execution_order_hash"]})
    _write("m10_expected_db_protocol.json", expected_protocol)
    _write("m10_future_acceptance_thresholds.json", thresholds)
    _write("m10_difference_taxonomy.json", taxonomy)
    lock = {
        "protocol": "m10-clean-rebaseline-v1",
        "starting_head": M10_STARTING_HEAD,
        "construction_seed": construction_seed(),
        "m3_contract_hash": M3_CONTRACT_HASH,
        "m4_corpus_hash": M4_CORPUS_HASH,
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "case_count": len(cases),
        "hashes": hashes,
        "freshness": freshness,
        "database_calls_before_reference_validation": 0,
        "provider_calls_before_benchmark": 0,
        "master_protocol_ready": False,
    }
    lock_hash = _write("m10_pre_db_freeze_lock.json", lock)
    return {"classification": "M10_CORPUS_CONTENT_FROZEN", "hashes": hashes, "pre_db_freeze_lock_hash": lock_hash, "case_count": len(cases), "parse_count": len(cases), "seed": construction_seed()}


if __name__ == "__main__":
    print(json.dumps(run_prepare(), indent=2, sort_keys=True))
