"""M61R.2 raw-schema external arm for the frozen dbt ACME corpus.

This module intentionally keeps the raw-schema contract outside production.  Its
only live path is one provider call per observation; all execution and scoring
after the call are deterministic and read-only.
"""

# Canonical audit strings are kept intact for reproducible contract hashes.
# ruff: noqa: E501
# mypy: ignore-errors

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import sqlglot
from sqlglot import exp

from app.config import Settings
from app.sql.authority import ExecutionAuthority
from app.sql.models import CandidateSource, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from benchmark import m39_runner as m39
from benchmark import m51b_runner as m51b
from benchmark import m56r_runner
from benchmark import m61r_runner as m61r
from benchmark.model_contract import sha256_text, submission_schema
from benchmark.stable_contract import (
    STABLE_CONTRACT_HASH,
    STABLE_CONTRACT_VERSION,
    stable_contract_prompt,
)

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "audits" / "m61r2"
M61R = ROOT / "audits" / "m61r"
M61R1 = ROOT / "audits" / "m61r1"
MODEL = "gpt-5.6-luna"
TEMPERATURE = 0.0
REASONING = "none"
TIMEOUT_SECONDS = 90.0
BUILDER_HASH = "ff695c5a4f9b26ffe9d88f30ee9c4a917c90e670e72b70a7b3739a9ed41b8a23"
DBT_LLM_COMMIT = "a29f2429b1bb38cee9d591892299e96f1e197ff9"
ACME_COMMIT = "ac20c9292fa88ec44d30aeeae61ee41a51e31748"
RAW_A_VERSION = "CONTROL_EXTERNAL_A"
RAW_B_VERSION = "EXTERNAL_B"

RAW_A_DELTA = """

## External raw-schema proposal mode

For this external raw-schema evaluation, the supplied schema is the complete
proposal scope and is the allowed universe of visible tables and columns. Do
not require a separate authorized_relationships declaration before proposing
SQL in this external arm. This proposal rule applies only to this external
evaluation contract.
""".strip()

RAW_B_DELTA = """

## Raw relational inference

Prefer declared foreign keys. When no declared foreign key exists, propose an
ordinary equi-join when visible key-compatible columns provide the clearest
necessary path for the question; matching names alone are suggestive, not
absolute proof. When business terminology is not separately modeled, infer the
most plausible ordinary meaning from visible table names, column names, key or
value structures, and relationships rather than abstaining solely because a
separate governed mapping is absent. Never invent a table or column absent from
the supplied schema.
""".strip()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(name: str, value: Any) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / name).write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def append(name: str, value: Any) -> None:
    with (AUDIT / name).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, check=True, capture_output=True, text=True
    ).stdout.strip()


def questions() -> list[dict[str, Any]]:
    return read_json(M61R / "m61r_question_set.json")["questions"]


def source_contexts() -> dict[str, str]:
    contexts: dict[str, str] = {}
    for row in read_jsonl(M61R / "m61r_requests.jsonl"):
        qid = row["question_id"]
        if qid in contexts:
            continue
        marker = "\n\nGoverned context:\n"
        if marker not in row["request_text"]:
            raise RuntimeError(f"M61R_CONTEXT_NOT_FOUND:{qid}")
        contexts[qid] = row["request_text"].split(marker, 1)[1]
    return contexts


def contract_texts() -> dict[str, str]:
    base = stable_contract_prompt()
    return {
        RAW_A_VERSION: base + "\n\n" + RAW_A_DELTA,
        RAW_B_VERSION: base + "\n\n" + RAW_A_DELTA + "\n\n" + RAW_B_DELTA,
    }


def provider_payload(request: dict[str, Any]) -> dict[str, Any]:
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
                "schema": submission_schema(),
            },
        },
        "temperature": TEMPERATURE,
    }


def structural_diff(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if type(left) is not type(right):
        return [{"path": path or "$", "left": left, "right": right}]
    if isinstance(left, dict):
        result: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                result.append({"path": child, "left": left.get(key), "right": right.get(key)})
            else:
                result.extend(structural_diff(left[key], right[key], child))
        return result
    if isinstance(left, list):
        result: list[dict[str, Any]] = []
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


def build_request(q: dict[str, Any], prompt: str, context: str, ordinal: int) -> dict[str, Any]:
    original = m51b.serialize_governed_context_v1
    m51b.serialize_governed_context_v1 = lambda _database_id: context
    try:
        return m51b._provider_request(
            ordinal,
            q["external_question_id"],
            {"database_id": "acme_small", "question": q["question"]},
            prompt=prompt,
        )
    finally:
        m51b.serialize_governed_context_v1 = original


def forbidden_contract_terms(texts: dict[str, str]) -> list[str]:
    forbidden = {
        "telecom",
        "workforce",
        "procurement",
        "marketplace",
        "healthcare",
        "policy holder",
        "agent",
        "loss payment",
        "claim reserve",
        "IQ_",
    }
    return sorted(term for text in texts.values() for term in forbidden if term in text.lower())


def verify_m61r1_digest() -> str:
    files = sorted(path for path in M61R1.iterdir() if path.is_file())
    combined = "".join(f"{path.name}:{file_hash(path)}\n" for path in files)
    return hashlib.sha256(combined.encode()).hexdigest()


def m61r_provenance() -> dict[str, Any]:
    return read_json(M61R / "m61r_source_manifest.json")


def build_runtime() -> tuple[SqlSafetyService, ExecutionAuthority, dict[str, list[str]]]:
    data_root = Path(os.environ["M61R_ACME_REPO"])
    ddl_path = data_root / "ACME_Insurance/DDL/ACME_small.ddl"
    ddl_tables, _relationships = m61r.parse_ddl(ddl_path.read_text(encoding="utf-8"))
    headers, _paths = m61r.csv_headers(data_root)
    catalog = m61r.runtime_catalog(headers, ddl_tables)
    settings = Settings(
        database_url=m61r.DB_URL,
        admin_database_url=m61r.ADMIN_URL,
        reader_role="decision_reader",
        max_result_rows=10000,
        max_plan_rows=100000,
        max_plan_cost=100000,
        statement_timeout_ms=5000,
    )
    service = SqlSafetyService(m61r.create_engine(m61r.DB_URL), settings=settings, catalog=catalog)
    return service, ExecutionAuthority.from_catalog(catalog), headers


def safe_select_sql(sql: str, visible_schema: dict[str, list[str]]) -> tuple[bool, str, list[str]]:
    try:
        statements = sqlglot.parse(sql, read="postgres")
        if len(statements) != 1:
            return False, "MULTIPLE_STATEMENTS", []
        tree = statements[0]
    except Exception as exc:
        return False, f"SQL_PARSE:{type(exc).__name__}", []
    if not isinstance(tree, (exp.Select, exp.Union)):
        return False, "NON_SELECT", []
    rendered = tree.sql(dialect="postgres").lower()
    if re.search(
        r"\b(for\s+(update|share|no\s+key\s+update)|copy|insert|update|delete|create|alter|drop|truncate|grant|revoke)\b",
        rendered,
    ):
        return False, "READ_ONLY_POLICY", []
    tables = sorted({table.name.lower() for table in tree.find_all(exp.Table)})
    unknown = sorted(set(tables) - set(visible_schema))
    if unknown:
        return False, "INVISIBLE_RELATION", unknown
    aliases = {
        table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)
    }
    all_columns = {column for columns in visible_schema.values() for column in columns}
    for column in tree.find_all(exp.Column):
        if column.name == "*":
            continue
        physical_table = aliases.get(column.table.lower()) if column.table else None
        if physical_table:
            if column.name.lower() not in visible_schema[physical_table]:
                return False, "INVISIBLE_COLUMN", [f"{physical_table}.{column.name.lower()}"]
        elif column.name.lower() not in all_columns:
            return False, "INVISIBLE_COLUMN", [column.name.lower()]
    return True, "PASS", tables


def load_external_questions() -> list[dict[str, Any]]:
    dbt_root = Path(os.environ["M61R_DBT_LLM_REPO"])
    external = m61r.load_questions(dbt_root)
    frozen = questions()
    if [q["external_question_id"] for q in external] != [q["external_question_id"] for q in frozen]:
        raise RuntimeError("M61R2_QUESTION_ORDER_DRIFT")
    for current, expected in zip(external, frozen, strict=True):
        if current["gold_sql_sha256"] != expected["gold_sql_sha256"]:
            raise RuntimeError(f"M61R2_GOLD_HASH_DRIFT:{current['external_question_id']}")
    return external


def prelive() -> dict[str, Any]:
    if STABLE_CONTRACT_HASH != "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587":
        raise RuntimeError("M61R2_CANDIDATE_C_HASH_DRIFT")
    qset = questions()
    if len(qset) != 11:
        raise RuntimeError("M61R2_QUESTION_COUNT_NOT_11")
    source = m61r_provenance()
    contexts = source_contexts()
    texts = contract_texts()
    if forbidden_contract_terms(texts):
        raise RuntimeError("M61R2_CASE_SPECIFIC_CONTRACT_TERM")
    external = load_external_questions()
    m61r1_manifest = read_json(M61R1 / "m61r1_manifest.json")
    m61r_scope = read_json(M61R1 / "m61r1_scope.json")
    if m61r1_manifest["candidate_c_hash"] != STABLE_CONTRACT_HASH:
        raise RuntimeError("M61R2_M61R1_CANDIDATE_HASH_DRIFT")
    headers, _paths = m61r.csv_headers(Path(os.environ["M61R_ACME_REPO"]))
    before_db = read_json(M61R / "m61r_database_fingerprint.json")
    current_db = m61r.database_fingerprint(headers)
    if current_db != before_db["fingerprint"]:
        raise RuntimeError("M61R2_DATABASE_FINGERPRINT_DRIFT")

    production_prompt = stable_contract_prompt()
    diffs: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for q in qset:
        qid = q["external_question_id"]
        if qid not in contexts:
            raise RuntimeError(f"M61R2_CONTEXT_MISSING:{qid}")
        production = provider_payload(
            build_request(q, production_prompt, contexts[qid], q["ordinal"])
        )
        for version, text in texts.items():
            candidate = provider_payload(build_request(q, text, contexts[qid], q["ordinal"]))
            differences = structural_diff(production, candidate)
            unexpected = [item for item in differences if item["path"] != "messages[0].content"]
            serialized = canonical(candidate).lower()
            leakage = [
                value["gold_sql"] for value in external if value["gold_sql"].lower() in serialized
            ]
            provenance.append(
                {
                    "question_id": qid,
                    "arm": version,
                    "contract_hash": sha256_text(text),
                    "provider_request_fingerprint": digest(candidate),
                    "gold_leakage": leakage,
                    "equal_to_production_except_contract": not unexpected,
                    "unexpected_differences": unexpected,
                    "deterministic_copy_fingerprint": digest(
                        provider_payload(build_request(q, text, contexts[qid], q["ordinal"]))
                    ),
                }
            )
            diffs.append(
                {
                    "question_id": qid,
                    "arm": version,
                    "differences": differences,
                    "unexpected_differences": unexpected,
                }
            )

    comparator_artifact = read_json(M61R / "m61r_evaluator_integrity.json")
    if comparator_artifact["status"] != "PASS":
        raise RuntimeError("M61R2_HISTORICAL_EVALUATOR_NOT_PASS")
    service, authority, _headers = build_runtime()
    comparator_path = Path(os.environ["M61R_DBT_LLM_REPO"]) / "src/llm_bench/services/comparison.py"
    comparator = m61r.load_comparator(comparator_path)
    gold_frames: dict[str, pd.DataFrame] = {}
    gold_checks = []
    for q in external:
        try:
            first = m61r.execute_df(q["gold_sql"])
            second = m61r.execute_df(q["gold_sql"])
            reflexive = comparator.compare_query_results(first, first).is_equivalent
            gold_frames[q["external_question_id"]] = first
            gold_checks.append(
                {
                    "question_id": q["external_question_id"],
                    "execution": True,
                    "reflexivity": bool(reflexive),
                    "repeat_equal": first.equals(second),
                }
            )
        except Exception as exc:
            gold_checks.append(
                {"question_id": q["external_question_id"], "execution": False, "error": str(exc)}
            )

    canary = []
    for q in external:
        try:
            plan = service.plan(
                SqlCandidate(
                    sql=m61r.compat_sql(q["gold_sql"]),
                    source=CandidateSource.LLM,
                    execution_authority=authority,
                )
            )
            execution = service.execute(plan) if isinstance(plan, QueryPlan) else None
            canary.append(
                {
                    "question_id": q["external_question_id"],
                    "accepted": isinstance(plan, QueryPlan),
                    "executed": execution is not None and hasattr(execution, "rows"),
                }
            )
        except Exception as exc:
            canary.append(
                {"question_id": q["external_question_id"], "accepted": False, "error": str(exc)}
            )
    evaluator = {
        "gold_execution": sum(x.get("execution", False) for x in gold_checks),
        "gold_reflexivity": sum(x.get("reflexivity", False) for x in gold_checks),
        "gold_repeat_determinism": sum(x.get("repeat_equal", False) for x in gold_checks),
        "candidate_path_canary": sum(
            x.get("accepted", False) and x.get("executed", False) for x in canary
        ),
        "questions": 11,
        "comparator_sha256": file_hash(comparator_path),
        "gold_checks": gold_checks,
        "canary": canary,
        "database_fingerprint": current_db,
    }
    evaluator["status"] = (
        "PASS"
        if all(
            evaluator[key] == 11
            for key in (
                "gold_execution",
                "gold_reflexivity",
                "gold_repeat_determinism",
                "candidate_path_canary",
            )
        )
        else "FAIL"
    )
    all_provenance = all(
        item["equal_to_production_except_contract"]
        and not item["gold_leakage"]
        and item["provider_request_fingerprint"] == item["deterministic_copy_fingerprint"]
        for item in provenance
    )
    if evaluator["status"] != "PASS":
        raise RuntimeError("M61R2_ABORTED_EVALUATOR_INTEGRITY_FAILURE")
    if not all_provenance:
        raise RuntimeError("M61R2_ABORTED_REQUEST_STRUCTURE_DRIFT")

    frozen_plan = AUDIT / "m61r2_experiment_plan.json"
    frozen_repo_head = (
        read_json(frozen_plan).get("repo_head", git_head()) if frozen_plan.exists() else git_head()
    )
    scope = {
        "milestone": "M61R.2",
        "status": "PRELIVE_READY",
        "repo_head": frozen_repo_head,
        "provider_calls_before_gate": 0,
        "retry": 0,
        "repair": 0,
        "historical_candidate_c_hash": STABLE_CONTRACT_HASH,
        "historical_m61r_case_results_sha256": file_hash(M61R / "m61r_case_results.jsonl"),
        "m61r1_digest": verify_m61r1_digest(),
        "m61r1_root_cause_sha256": file_hash(M61R1 / "m61r1_root_cause_accounting.json"),
        "question_set_sha256": file_hash(M61R / "m61r_question_set.json"),
        "source_data_manifest_sha256": source["source_data_manifest_sha256"],
        "ddl_sha256": source["raw_ddl"]["sha256"],
        "schema_adapter_context_hash": read_json(M61R / "m61r_schema_adapter.json")["context_hash"],
        "ddl_adapter_hash": read_json(M61R / "m61r_ddl_adapter.json")["adapter_hash"],
        "database_fingerprint": current_db,
        "comparator_sha256": file_hash(comparator_path),
        "canonical_builder_hash": BUILDER_HASH,
        "provider": "OpenAICompatibleProvider",
        "model": MODEL,
        "temperature": TEMPERATURE,
        "reasoning": REASONING,
        "timeout_seconds": TIMEOUT_SECONDS,
        "questions": 11,
        "selection_calls": 22,
        "full_run_calls": 220,
        "total_calls": 242,
        "m61r1_scope_benchmark_score_changed": m61r_scope.get("benchmark_score_changed", False),
    }
    dump("m61r2_scope.json", scope)
    dump(
        "m61r2_external_contracts.json",
        {
            "production_contract_hash": STABLE_CONTRACT_HASH,
            "contracts": {
                version: {
                    "version": version,
                    "hash": sha256_text(text),
                    "text": text,
                    "base_contract": STABLE_CONTRACT_VERSION,
                    "delta": RAW_A_DELTA
                    if version == RAW_A_VERSION
                    else RAW_A_DELTA + "\n\n" + RAW_B_DELTA,
                    "benchmark_specific_terms": [],
                }
                for version, text in texts.items()
            },
            "selection_arms": list(texts),
            "production_default_unchanged": True,
        },
    )
    dump("m61r2_contract_diff.json", {"cases": diffs, "all_unexpected_differences": []})
    dump(
        "m61r2_information_boundary.json",
        {
            "provider_visible": [
                "natural-language question",
                "raw schema context from frozen M61R adapter",
                "Candidate C plus external raw-schema contract delta",
            ],
            "evaluator_only_forbidden": [
                "gold SQL",
                "gold result",
                "gold join path",
                "expected output",
                "M61R responses",
                "correctness labels",
            ],
            "gold_blind": True,
            "contract_specific_benchmark_terms": forbidden_contract_terms(texts),
            "leakage_tests": {"all_requests": all(not item["gold_leakage"] for item in provenance)},
        },
    )
    dump("m61r2_evaluator_integrity.json", evaluator)
    dump(
        "m61r2_prelive_provenance.json",
        {
            "status": "PASS",
            "request_count": len(provenance),
            "question_count": 11,
            "arms": list(texts),
            "provider_calls_before_gate": 0,
            "requests": provenance,
        },
    )
    dump(
        "m61r2_experiment_plan.json",
        {
            "milestone": "M61R.2",
            "status": "PREREGISTERED",
            "repo_head": frozen_repo_head,
            "candidate_contracts": {version: sha256_text(text) for version, text in texts.items()},
            "control_reference": "historical M61R Candidate C; not rerun",
            "question_ids": [q["external_question_id"] for q in qset],
            "selection_order": [q["external_question_id"] for q in qset],
            "selection_calls": 22,
            "full_run_calls": 220,
            "total_calls": 242,
            "model": MODEL,
            "provider": "OpenAICompatibleProvider",
            "temperature": TEMPERATURE,
            "reasoning": REASONING,
            "timeout_seconds": TIMEOUT_SECONDS,
            "retry": 0,
            "repair": 0,
            "judge": 0,
            "selector": 0,
            "router": 0,
            "pass_at_k": 0,
            "primary_metric": "DBT_COMPARABLE_RAW_SCHEMA_ACCURACY",
            "secondary_metric": "ANSWER_RATE",
            "selection_criteria": [
                "higher dbt comparator pass count",
                "fewer invisible relation/column proposals",
                "fewer execution errors and incorrect ANSWERs",
                "smaller contract delta",
                "simpler wording",
            ],
            "hard_constraints": [
                "read-only SELECT proposals only",
                "visible schema relations/columns only",
                "structured response required",
                "no production contract or runtime change",
            ],
            "evaluator_integrity": evaluator["status"],
            "database_fingerprint": current_db,
            "comparator_sha256": file_hash(comparator_path),
            "canonical_builder_hash": BUILDER_HASH,
            "source_revisions": {"dbt_llm_sl_bench": DBT_LLM_COMMIT, "semantic_layer": ACME_COMMIT},
            "prelive_gate": "PASS",
        },
    )
    return {
        "questions": external,
        "contexts": contexts,
        "contracts": texts,
        "gold_frames": gold_frames,
        "comparator": comparator,
        "service": service,
        "authority": authority,
        "headers": headers,
        "source": source,
    }


def parse_response(wire: bytes | None, qid: str) -> tuple[Any, str, str | None]:
    if not wire:
        return None, "NO_RESPONSE", None
    try:
        payload = json.loads(wire)
        content = payload["choices"][0]["message"]["content"]
        parsed, status, detail, _ = m39._parse(content, qid)
        return parsed, status, detail
    except Exception as exc:
        return None, "RESPONSE_PARSE_FAILURE", str(exc)


def invoke(provider: Any, request: dict[str, Any], qid: str, operation: str) -> dict[str, Any]:
    started = time.perf_counter()
    error: Exception | None = None
    response_payload = None
    try:
        response_payload = asyncio.run(
            provider.complete_json_schema(
                operation=operation,
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
    parsed, parse_status, parse_detail = (
        parse_response(wire, qid) if error is None else (None, "TRANSPORT_FAILURE", str(error))
    )
    return {
        "latency_ms": latency,
        "response_payload": response_payload,
        "capture": capture,
        "wire": wire,
        "parsed": parsed,
        "parse_status": parse_status,
        "parse_detail": parse_detail,
        "provider_success": error is None,
        "provider_error": None
        if error is None
        else {"type": type(error).__name__, "message": str(error)[:400]},
    }


def evaluate_record(
    raw: dict[str, Any],
    q: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    parsed = raw["parsed"]
    sql = parsed.sql if parsed is not None else None
    raw_safe, raw_safety_status, raw_tables = (
        safe_select_sql(sql, state["headers"])
        if sql and parsed.decision == "ANSWER"
        else (False, "NO_ANSWER", [])
    )
    raw_pass = False
    raw_error = None
    if raw_safe:
        try:
            frame = m61r.execute_df(sql)
            raw_pass = bool(
                state["comparator"]
                .compare_query_results(state["gold_frames"][q["external_question_id"]], frame)
                .is_equivalent
            )
        except Exception as exc:
            raw_error = {"type": type(exc).__name__, "message": str(exc)[:400]}

    production_accepted = False
    production_stage = "NOT_RUN"
    production_detail: Any = None
    production_pass = False
    if sql and parsed is not None and parsed.decision == "ANSWER":
        try:
            plan = state["service"].plan(
                SqlCandidate(
                    sql=m61r.compat_sql(sql),
                    source=CandidateSource.LLM,
                    execution_authority=state["authority"],
                )
            )
            production_accepted = isinstance(plan, QueryPlan)
            production_stage = "EXECUTED" if production_accepted else "RUNTIME_REJECTION"
            if not production_accepted:
                production_detail = plan.model_dump(mode="json")
            else:
                execution = state["service"].execute(plan)
                if hasattr(execution, "rows"):
                    got = pd.DataFrame(execution.rows, columns=execution.columns)
                    production_pass = bool(
                        state["comparator"]
                        .compare_query_results(state["gold_frames"][q["external_question_id"]], got)
                        .is_equivalent
                    )
        except Exception as exc:
            production_stage = "EXECUTION_ERROR"
            production_detail = {"type": type(exc).__name__, "message": str(exc)[:400]}

    return {
        "question_id": q["external_question_id"],
        "decision": parsed.decision if parsed is not None else None,
        "sql": sql,
        "sql_hash": sha256_text(sql) if sql else None,
        "normalized_sql_hash": digest(
            sqlglot.parse_one(sql, read="postgres").sql(dialect="postgres")
        )
        if raw_safe
        else None,
        "parse_status": raw["parse_status"],
        "parse_detail": raw["parse_detail"],
        "raw_schema_safety": raw_safety_status,
        "raw_tables": raw_tables,
        "raw_execution_error": raw_error,
        "raw_proposal_dbt_pass": raw_pass,
        "production_runtime_stage": production_stage,
        "production_runtime_detail": production_detail,
        "production_authority_accepted": production_accepted,
        "production_dbt_pass": production_pass,
    }


def call_arm(
    state: dict[str, Any], version: str, questions_to_run: list[dict[str, Any]], phase: str
) -> list[dict[str, Any]]:
    provider = m56r_runner.OpenAICompatibleProvider(
        Settings(
            llm_model=MODEL,
            llm_temperature=TEMPERATURE,
            llm_reasoning_effort=REASONING,
            llm_timeout_seconds=TIMEOUT_SECONDS,
            llm_api_key=os.environ.get("OPENAI_API_KEY"),
            eval_capture_model_io=True,
        )
    )
    rows = []
    for q in questions_to_run:
        request = build_request(
            q,
            state["contracts"][version],
            state["contexts"][q["external_question_id"]],
            q["ordinal"],
        )
        payload = provider_payload(request)
        call = invoke(provider, request, q["external_question_id"], f"m61r2_{phase.lower()}")
        wire = call["wire"]
        evaluated = evaluate_record(call, q, state)
        record = {
            "phase": phase,
            "arm": version,
            "question_id": q["external_question_id"],
            "iteration": 1 if phase == "SELECTION" else q["iteration"],
            "provider_request_fingerprint": digest(payload),
            "contract_hash": sha256_text(state["contracts"][version]),
            "raw_response_base64": base64.b64encode(wire).decode() if wire else None,
            "raw_response_hash": hashlib.sha256(wire).hexdigest() if wire else None,
            "provider_success": call["provider_success"],
            "provider_error": call["provider_error"],
            "latency_ms": call["latency_ms"],
            "usage": m39._provider_metadata(call["response_payload"] or {}, call["capture"])
            if call["capture"]
            else {},
            **evaluated,
        }
        record["provider_request_payload"] = payload
        rows.append(record)
    return rows


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "observations": len(rows),
        "dbt_correct": sum(row["raw_proposal_dbt_pass"] for row in rows),
        "answer_count": sum(row["decision"] == "ANSWER" for row in rows),
        "blocked_authority_count": sum(row["decision"] == "BLOCKED_AUTHORITY" for row in rows),
        "clarification_count": sum(row["decision"] == "NEEDS_CLARIFICATION" for row in rows),
        "provider_failures": sum(not row["provider_success"] for row in rows),
        "invalid_schema_proposals": sum(
            row["decision"] == "ANSWER" and row["raw_schema_safety"] != "PASS" for row in rows
        ),
        "execution_errors": sum(
            row["raw_schema_safety"] == "PASS" and row["raw_execution_error"] is not None
            for row in rows
        ),
    }


def selection_results(rows: list[dict[str, Any]], contracts: dict[str, str]) -> dict[str, Any]:
    by_arm: dict[str, list[dict[str, Any]]] = {arm: [] for arm in contracts}
    for row in rows:
        by_arm[row["arm"]].append(row)
    results = {}
    for arm, arm_rows in by_arm.items():
        results[arm] = {
            **score(arm_rows),
            "contract_hash": sha256_text(contracts[arm]),
            "incorrect_answers": sum(
                row["decision"] == "ANSWER" and not row["raw_proposal_dbt_pass"] for row in arm_rows
            ),
            "invisible_relation_or_column": sum(
                row["raw_schema_safety"] == "INVISIBLE_RELATION" for row in arm_rows
            ),
        }
    return results


def choose(selection: dict[str, Any]) -> str:
    arms = sorted(selection)
    return max(
        arms,
        key=lambda arm: (
            selection[arm]["dbt_correct"],
            -selection[arm]["invisible_relation_or_column"],
            -selection[arm]["execution_errors"],
            -selection[arm]["incorrect_answers"],
            -len(selection[arm]["contract_hash"]),
            -arms.index(arm),
        ),
    )


def failure_decomposition(
    rows: list[dict[str, Any]], questions_by_id: dict[str, dict[str, Any]]
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row["raw_proposal_dbt_pass"]:
            continue
        if row["decision"] == "NEEDS_CLARIFICATION":
            counts["MODEL_ABSTENTION"] += 1
        elif row["decision"] == "BLOCKED_AUTHORITY":
            counts["FALSE_AUTHORITY_BLOCK"] += 1
        elif row["raw_schema_safety"] == "INVISIBLE_RELATION":
            counts["SCHEMA_HALLUCINATION"] += 1
        elif row["raw_schema_safety"] == "INVISIBLE_COLUMN":
            counts["COLUMN_HALLUCINATION"] += 1
        elif row["raw_schema_safety"] != "PASS":
            counts["SQL_PARSE"] += 1
        elif row["raw_execution_error"] is not None:
            counts["EXECUTION_ERROR"] += 1
        else:
            question = questions_by_id[row["question_id"]]
            sql = (row["sql"] or "").lower()
            gold = question["gold_sql"].lower()
            if "party_role_code" in gold and "party_role_code" not in sql:
                counts["BUSINESS_SEMANTICS"] += 1
            else:
                counts["JOIN_PATH"] += 1
    return dict(sorted(counts.items()))


def full_run(state: dict[str, Any], winner: str) -> list[dict[str, Any]]:
    provider = m56r_runner.OpenAICompatibleProvider(
        Settings(
            llm_model=MODEL,
            llm_temperature=TEMPERATURE,
            llm_reasoning_effort=REASONING,
            llm_timeout_seconds=TIMEOUT_SECONDS,
            llm_api_key=os.environ.get("OPENAI_API_KEY"),
            eval_capture_model_io=True,
        )
    )
    rows = []
    for q in state["questions"]:
        for iteration in range(1, 21):
            request = build_request(
                q,
                state["contracts"][winner],
                state["contexts"][q["external_question_id"]],
                q["ordinal"],
            )
            payload = provider_payload(request)
            call = invoke(provider, request, q["external_question_id"], "m61r2_full")
            wire = call["wire"]
            evaluated = evaluate_record(call, q, state)
            row = {
                "phase": "FULL",
                "arm": winner,
                "question_id": q["external_question_id"],
                "iteration": iteration,
                "provider_request_fingerprint": digest(payload),
                "contract_hash": sha256_text(state["contracts"][winner]),
                "raw_response_base64": base64.b64encode(wire).decode() if wire else None,
                "raw_response_hash": hashlib.sha256(wire).hexdigest() if wire else None,
                "provider_success": call["provider_success"],
                "provider_error": call["provider_error"],
                "latency_ms": call["latency_ms"],
                "usage": m39._provider_metadata(call["response_payload"] or {}, call["capture"])
                if call["capture"]
                else {},
                **evaluated,
            }
            rows.append(row)
            append(
                "m61r2_requests.jsonl",
                {
                    "question_id": q["external_question_id"],
                    "iteration": iteration,
                    "provider_request_fingerprint": row["provider_request_fingerprint"],
                    "contract_hash": row["contract_hash"],
                    "provider_request_payload": payload,
                },
            )
            append(
                "m61r2_responses.jsonl",
                {
                    k: row[k]
                    for k in (
                        "question_id",
                        "iteration",
                        "raw_response_base64",
                        "raw_response_hash",
                        "provider_success",
                        "provider_error",
                        "latency_ms",
                        "usage",
                    )
                },
            )
            append("m61r2_case_results.jsonl", row)
    return rows


def postprocess(rows: list[dict[str, Any]], state: dict[str, Any], winner: str) -> None:
    by_q = {q["external_question_id"]: [] for q in state["questions"]}
    for row in rows:
        by_q[row["question_id"]].append(row)
    accuracy = [
        {
            "question_id": qid,
            "correct": sum(row["raw_proposal_dbt_pass"] for row in qrows),
            "total": len(qrows),
            "answer_count": sum(row["decision"] == "ANSWER" for row in qrows),
        }
        for qid, qrows in by_q.items()
    ]
    distribution = Counter(row["decision"] or "FORMAT_FAILURE" for row in rows)
    stable = [
        {
            "question_id": qid,
            "unique_decisions": sorted({row["decision"] for row in qrows}),
            "unique_sql_hashes": len({row["sql_hash"] for row in qrows if row["sql_hash"]}),
            "unique_normalized_sql_hashes": len(
                {row["normalized_sql_hash"] for row in qrows if row["normalized_sql_hash"]}
            ),
            "pass_count": sum(row["raw_proposal_dbt_pass"] for row in qrows),
        }
        for qid, qrows in by_q.items()
    ]
    production = {
        "raw_correct_production_accepted": sum(
            row["raw_proposal_dbt_pass"] and row["production_authority_accepted"] for row in rows
        ),
        "raw_correct_production_rejected": sum(
            row["raw_proposal_dbt_pass"] and not row["production_authority_accepted"]
            for row in rows
        ),
        "raw_wrong_production_accepted": sum(
            not row["raw_proposal_dbt_pass"] and row["production_authority_accepted"]
            for row in rows
        ),
        "raw_wrong_production_rejected": sum(
            not row["raw_proposal_dbt_pass"] and not row["production_authority_accepted"]
            for row in rows
        ),
    }
    latencies = sorted(row["latency_ms"] for row in rows)
    usage = [row["usage"] for row in rows if row["usage"]]
    dump(
        "m61r2_question_accuracy.json",
        {
            "questions": accuracy,
            "correct": sum(x["correct"] for x in accuracy),
            "total": len(rows),
            "fully_stable_questions": sum(x["correct"] == 20 for x in accuracy),
        },
    )
    dump("m61r2_decision_distribution.json", dict(sorted(distribution.items())))
    dump(
        "m61r2_failure_decomposition.json",
        {
            "total_failures": len(rows) - sum(row["raw_proposal_dbt_pass"] for row in rows),
            "counts": failure_decomposition(
                rows, {q["external_question_id"]: q for q in state["questions"]}
            ),
        },
    )
    dump(
        "m61r2_historical_category_comparison.json",
        {
            "m61r1": {"authority_blocks": 118, "clarifications": 13, "incorrect_answers": 7},
            "m61r2": {
                "authority_blocks": distribution.get("BLOCKED_AUTHORITY", 0),
                "clarifications": distribution.get("NEEDS_CLARIFICATION", 0),
                "incorrect_answers": sum(
                    row["decision"] == "ANSWER" and not row["raw_proposal_dbt_pass"] for row in rows
                ),
            },
            "interpretation": "Descriptive ablation across epistemic contracts; not a production upgrade.",
        },
    )
    dump("m61r2_production_policy_diagnostic.json", production)
    dump(
        "m61r2_output_stability.json",
        {
            "questions": stable,
            "unique_decision_count": len({row["decision"] for row in rows}),
            "unique_sql_hash_count": len({row["sql_hash"] for row in rows if row["sql_hash"]}),
        },
    )
    dump(
        "m61r2_cost_latency.json",
        {
            "observations": len(rows),
            "latency_ms": {
                "median": latencies[len(latencies) // 2] if latencies else None,
                "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
                if latencies
                else None,
            },
            "usage_records": len(usage),
            "cost": "not reported by provider",
        },
    )
    dump(
        "m61r2_selected_contract.json",
        {
            "selected": winner,
            "hash": sha256_text(state["contracts"][winner]),
            "selection_basis": "pre-registered dbt pass, schema-validity, execution-error, incorrect-answer, and simplicity ordering",
        },
    )
    summary = {
        "milestone": "M61R.2",
        "verdict": "M61R2_DBT_COMPARABLE_RAW_SCHEMA_COMPLETE",
        "selected_contract": winner,
        "contract_hash": sha256_text(state["contracts"][winner]),
        "provider_calls": 242,
        "selection_calls": 22,
        "full_run_calls": 220,
        "retries": 0,
        "dbt_comparable_correct": sum(row["raw_proposal_dbt_pass"] for row in rows),
        "total": len(rows),
        "fully_stable_questions": sum(x["correct"] == 20 for x in accuracy),
        "provider_failures": sum(not row["provider_success"] for row in rows),
        "failed_case_reruns": 0,
        "production_candidate_c_unchanged": True,
    }
    dump("m61r2_summary.json", summary)
    report = f"""# M61R.2 — dbt-Comparable Raw-Schema Arm

## Verdict

`M61R2_DBT_COMPARABLE_RAW_SCHEMA_COMPLETE`

This is a separate external ablation, not a production contract change. The
production Candidate C contract remains `{STABLE_CONTRACT_HASH}` and the
production runtime remains unchanged.

## Result

- Selected contract: `{winner}` (`{summary["contract_hash"]}`)
- Fresh full-run dbt-comparable result: **{summary["dbt_comparable_correct"]}/{summary["total"]}**
- Fully stable questions: **{summary["fully_stable_questions"]}/11**
- Provider calls: **242** (22 selection + 220 full run); retries: **0**.

The historical production-faithful M61R result was **82/220**. These numbers
measure different epistemic contracts: the raw arm permits ordinary inference
from the visible schema, while production Candidate C requires governed
authority before production execution.

No gold SQL, expected result, or correctness label was provider-visible. The
raw proposals were evaluated only in the isolated read-only ACME database with
the retained dbt comparator. This is a dbt-comparable raw-schema arm, not a
production upgrade or an official dbt leaderboard score.
"""
    (AUDIT / "m61r2_report.md").write_text(report, encoding="utf-8")


def live(state: dict[str, Any]) -> None:
    if any(
        (AUDIT / name).exists()
        for name in ("m61r2_selection_results.json", "m61r2_case_results.jsonl")
    ):
        raise RuntimeError("M61R2_LIVE_ARTIFACT_ALREADY_EXISTS")
    selection_rows = []
    for arm in state["contracts"]:
        selection_rows.extend(call_arm(state, arm, state["questions"], "SELECTION"))
    selection = selection_results(selection_rows, state["contracts"])
    winner = choose(selection)
    for row in selection_rows:
        append(
            "m61r2_selection_requests.jsonl",
            {
                "question_id": row["question_id"],
                "arm": row["arm"],
                "provider_request_fingerprint": row["provider_request_fingerprint"],
                "contract_hash": row["contract_hash"],
                "provider_request_payload": row["provider_request_payload"],
            },
        )
        append(
            "m61r2_selection_responses.jsonl",
            {
                "question_id": row["question_id"],
                "arm": row["arm"],
                "raw_response_base64": row["raw_response_base64"],
                "raw_response_hash": row["raw_response_hash"],
                "provider_success": row["provider_success"],
                "provider_error": row["provider_error"],
            },
        )
    dump(
        "m61r2_selection_results.json",
        {"arms": selection, "winner": winner, "calls": len(selection_rows)},
    )
    dump(
        "m61r2_selected_contract.json",
        {
            "selected": winner,
            "hash": sha256_text(state["contracts"][winner]),
            "selection_basis": "pre-registered criteria",
        },
    )
    rows = full_run(state, winner)
    postprocess(rows, state, winner)
    dump(
        "m61r2_manifest.json",
        {
            "milestone": "M61R.2",
            "verdict": "M61R2_DBT_COMPARABLE_RAW_SCHEMA_COMPLETE",
            "provider_calls_before_gate": 0,
            "selection_calls": len(selection_rows),
            "full_run_calls": len(rows),
            "total_provider_calls": len(selection_rows) + len(rows),
            "retries": 0,
            "failed_case_reruns": 0,
            "historical_m61r_unchanged": True,
            "candidate_c_hash": STABLE_CONTRACT_HASH,
            "production_default_unchanged": True,
            "adapter_unchanged": True,
            "runtime_unchanged": True,
            "benchmark_semantics_unchanged": True,
            "artifact_names": sorted(path.name for path in AUDIT.iterdir() if path.is_file()),
        },
    )


def reconcile() -> None:
    """Recompute deterministic post-processing without touching the corpus."""
    state = prelive()
    selected = read_json(AUDIT / "m61r2_selected_contract.json")["selected"]
    rows = read_jsonl(AUDIT / "m61r2_case_results.jsonl")
    if len(rows) != 220:
        raise RuntimeError("M61R2_FULL_CORPUS_COUNT_MISMATCH")
    postprocess(rows, state, selected)
    dump(
        "m61r2_manifest.json",
        {
            "milestone": "M61R.2",
            "verdict": "M61R2_DBT_COMPARABLE_RAW_SCHEMA_COMPLETE",
            "provider_calls_before_gate": 0,
            "selection_calls": 22,
            "full_run_calls": 220,
            "total_provider_calls": 242,
            "retries": 0,
            "failed_case_reruns": 0,
            "historical_m61r_unchanged": True,
            "candidate_c_hash": STABLE_CONTRACT_HASH,
            "production_default_unchanged": True,
            "adapter_unchanged": True,
            "runtime_unchanged": True,
            "benchmark_semantics_unchanged": True,
            "artifact_names": sorted(path.name for path in AUDIT.iterdir() if path.is_file()),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "live", "reconcile"))
    args = parser.parse_args()
    state = prelive()
    if args.command == "live":
        live(state)
    elif args.command == "reconcile":
        reconcile()


if __name__ == "__main__":
    main()
