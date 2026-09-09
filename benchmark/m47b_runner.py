"""M47B prospective confirmation of the frozen deterministic grain normalizer.

This runner deliberately separates the one-call generation phase from the
offline RAW/NORMALIZED evaluation phase.  The normalizer is imported from the
M47A implementation and is never changed or selected using benchmark results.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from app.semantics.grain import GrainDiagnosticCode, GrainGraph, GrainSafetyValidator
from app.semantics.grain_normalizer import GrainSafeNormalizer, NormalizationStatus
from benchmark import m39_runner as m39
from benchmark.m46a1_repair import _answerable_pairs, _mutation_replay, _reference_replay
from benchmark.m46a_audit import _answerable_rows, _build_catalogs, _reference_rows
from benchmark.m46b_contract import EXPECTED_PROMPT_HASH, TRUTH_HASH, TRUTH_VERSION, m43_prompt
from benchmark.model_contract import (
    ROOT,
    frozen_benchmark_content_hash,
    sha256_bytes,
    sha256_text,
    submission_schema,
)
from benchmark.models import Submission, validate_submission_invariants

REPO = ROOT.parent
RESULT_ROOT = ROOT / "experiments" / "results" / "m47b"
AUDIT_ROOT = ROOT / "audits" / "m47b"
MANIFEST_PATH = ROOT / "manifests" / "m47b_contract.json"
CONFIG_PATH = ROOT / "experiments" / "m47b_prospective_grain_normalization.json"
M47A_MANIFEST = ROOT / "manifests" / "m47a_architecture_manifest.json"
M47A_NORMALIZER_HASH = "55ff3b32a698c8b8a151984dc8b9070531cfde926fb02148f6fc59f6c97b5800"
M47A_VALIDATOR_HASH = "5e36ff6171050d01a244619cae8902d7e9da7918a461699e426912503a2ad7d5"
M47A_SQL_ADMISSION_HASH = "652206cf95e16bbe6277813d75b1328efe1c5002662dac1104b8d251cb78c652"
EXPECTED_CASE_ORDER_HASH = "3299ecb9046619cd7b2e2aed66ed2e4146e8286b0497202b3b3ebc151947c2c2"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _git() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _historical_paths() -> list[Path]:
    paths: set[Path] = set()
    for token in ("m39", "m41", "m42", "m43", "m44", "m45", "m46a", "m46b", "m46br", "m47a"):
        for root in (ROOT / "experiments", ROOT / "manifests", ROOT / "audits", ROOT / "reports"):
            if not root.exists():
                continue
            paths.update(path for path in root.rglob(f"*{token}*") if path.is_file())
    for root in (ROOT / "experiments" / "results",):
        for path in root.rglob("*") if root.exists() else ():
            if path.is_file() and any(
                token in str(path).lower() for token in ("m46b", "m46br", "m47a")
            ):
                paths.add(path)
    return sorted(paths)


def historical_preservation() -> dict[str, Any]:
    payload = {
        "milestone": "M47B",
        "starting_commit": _git(),
        "provider_calls": 0,
        "model_calls": 0,
        "files": {
            str(path.relative_to(REPO)): _sha(path) for path in _historical_paths() if path.exists()
        },
    }
    _dump(AUDIT_ROOT / "m47b_historical_preservation.json", payload)
    return payload


def verify_historical(preservation: dict[str, Any]) -> dict[str, Any]:
    mismatches = [
        name
        for name, expected in preservation["files"].items()
        if not (REPO / name).exists() or _sha(REPO / name) != expected
    ]
    result = {
        "files_checked": len(preservation["files"]),
        "mismatches": mismatches,
        "unchanged": not mismatches,
    }
    _dump(AUDIT_ROOT / "m47b_historical_preservation_check.json", result)
    return result


def _config() -> dict[str, Any]:
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    expected = {
        "model": "gpt-5.6-luna",
        "provider": "openai-compatible",
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": 90,
        "calls_per_case": 1,
        "transport_retries": 0,
        "semantic_retries": 0,
        "repair": False,
        "selector": False,
        "judge": False,
        "reflection": False,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RuntimeError(f"M47B_CONFIG_MISMATCH:{key}")
    return value


def _rows() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    split = json.loads((ROOT / "splits" / "m40_dev.json").read_text(encoding="utf-8"))
    case_ids = [str(item) for item in split["case_ids"]]
    if len(case_ids) != 90 or len(set(case_ids)) != 90:
        raise RuntimeError("M47B_CONTRACT_MISMATCH:case_order")
    rows = m39._load_rows({"requests": [{"case_id": item} for item in case_ids]})
    return case_ids, rows


def _requests(
    case_ids: list[str], rows: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> list[dict[str, Any]]:
    prompt = m43_prompt()
    result = []
    for index, case_id in enumerate(case_ids, 1):
        case = rows[case_id][0]
        _text, context, _size = m39._request_text(case)
        user = (
            "Case ID:\n"
            + case_id
            + "\n\nQuestion:\n"
            + str(case["question"])
            + "\n\nGoverned context:\n"
            + context
        )
        request_text = "SYSTEM:\n" + prompt + "\n\nUSER:\n" + user
        result.append(
            {
                "case_index": index,
                "case_id": case_id,
                "database_id": case["database_id"],
                "split": "DEV" if case["database_id"] in m39.DEV_DATABASES else "CONFIRMATION",
                "question": case["question"],
                "question_sha256": sha256_text(case["question"]),
                "prompt_sha256": sha256_text(prompt),
                "context_sha256": sha256_text(context),
                "serialized_context": context,
                "instructions": prompt,
                "user_text": user,
                "request_text": request_text,
                "request_bytes": len(request_text.encode("utf-8")),
                "request_sha256": sha256_text(request_text),
            }
        )
    return result


def _source_hashes(catalogs: dict[str, Any]) -> dict[str, Any]:
    return {
        "normalizer_source": _sha(REPO / "app/semantics/grain_normalizer.py"),
        "validator_source": _sha(REPO / "app/semantics/grain.py"),
        "sql_admission_source": _sha(REPO / "benchmark/safety.py"),
        "catalog_hash": {db: catalog.content_hash for db, catalog in catalogs.items()},
        "graph_hash": {
            db: GrainGraph.from_catalog(catalog).content_hash for db, catalog in catalogs.items()
        },
    }


def _reference_noop(catalogs: dict[str, Any]) -> dict[str, Any]:
    references = _reference_rows(_answerable_rows())
    records = []
    for row in references:
        result = GrainSafeNormalizer(catalogs[row["database_id"]]).normalize(row["sql"])
        records.append(
            {
                **row,
                "input_sql_hash": result.input_sql_hash,
                "output_sql_hash": result.output_sql_hash,
                "status": result.status.value,
                "diagnostic": result.input_diagnostic.code.value,
                "byte_identical": result.input_sql == result.output_sql,
            }
        )
    modified = [record for record in records if not record["byte_identical"]]
    result = {
        "references": 120,
        "modified": len(modified),
        "records": records,
        "passed": len(records) == 120 and not modified,
    }
    _dump(AUDIT_ROOT / "m47b_reference_noop.json", result)
    if not result["passed"]:
        raise RuntimeError("M47B_NORMALIZER_CONTRACT_MISMATCH")
    return result


def preflight() -> dict[str, Any]:
    preservation = historical_preservation()
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    if version.get("version") != TRUTH_VERSION or version.get("content_hash") != TRUTH_HASH:
        raise RuntimeError("M47B_CONTRACT_MISMATCH:truth")
    if frozen_benchmark_content_hash() != TRUTH_HASH:
        raise RuntimeError("M47B_CONTRACT_MISMATCH:truth_hash")
    prompt_hash = sha256_text(m43_prompt())
    if prompt_hash != EXPECTED_PROMPT_HASH:
        raise RuntimeError("M47B_PARENT_PROMPT_MISMATCH")
    case_ids, rows = _rows()
    requests = _requests(case_ids, rows)
    case_order_hash = sha256_text(json.dumps(case_ids, separators=(",", ":")))
    if case_order_hash != EXPECTED_CASE_ORDER_HASH:
        raise RuntimeError("M47B_CONTRACT_MISMATCH:case_order_hash")
    answerable = [truth for _case, truth in _answerable_pairs()]
    catalogs, inventory = _build_catalogs(answerable)
    source_hashes = _source_hashes(catalogs)
    m47a = json.loads(M47A_MANIFEST.read_text(encoding="utf-8"))
    if (
        source_hashes["normalizer_source"] != M47A_NORMALIZER_HASH
        or source_hashes["validator_source"] != M47A_VALIDATOR_HASH
        or source_hashes["sql_admission_source"] != M47A_SQL_ADMISSION_HASH
    ):
        raise RuntimeError("M47B_NORMALIZER_CONTRACT_MISMATCH:source")
    if m47a.get("normalizer_source_hash") != M47A_NORMALIZER_HASH:
        raise RuntimeError("M47B_NORMALIZER_CONTRACT_MISMATCH:m47a_manifest")
    noop = _reference_noop(catalogs)
    replay, expected = _reference_replay()
    mutation = _mutation_replay(expected)
    if (
        not replay["passed"]
        or replay["references_analyzed"] != 120
        or replay["fixture_comparisons"] != 184
        or mutation["valid"] != 190
        or mutation["killed"] != 190
        or mutation["invalid"] != 0
        or mutation["surviving"] != 0
    ):
        raise RuntimeError("M47B_REFERENCE_PRECHECK_FAILED")
    contract = {
        "experiment": "M47B",
        "experiment_id": "m47b_prospective_grain_normalization",
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "prompt_hash": prompt_hash,
        "legacy_repaired_context": True,
        "model_context_changed": False,
        "model_config": _config(),
        "case_order_hash": case_order_hash,
        "request_count": 90,
        "provider_calls": 0,
        "normalizer_contract_commit": "186a6eaa206f20a297d19214f51ace5caf900c20",
        "normalizer_source_hash": M47A_NORMALIZER_HASH,
        "m47a_evidence_commit": "e37ac7ff94fae7691298896f1e47cbf2a6aea273",
        "source_hashes": source_hashes,
        "evaluator_hash": _sha(ROOT / "evaluator.py"),
        "provider_schema_hash": _sha(ROOT / "schemas" / "model_submission.schema.json"),
        "provider_adapter_hash": _sha(REPO / "app/generation/provider.py"),
        "experiment_config_hash": _sha(CONFIG_PATH),
        "truth_quality": {
            "references": 120,
            "fixture_comparisons": 184,
            "mutants": 190,
            "survivors": 0,
            "invalid": 0,
        },
        "historical_preservation_hash": sha256_text(
            json.dumps(preservation, sort_keys=True, separators=(",", ":"))
        ),
        "phase": "CONTRACT_FROZEN_BEFORE_GENERATION",
        "contract_source_commit": _git(),
    }
    ledger = {
        "experiment": "M47B",
        "provider_calls": 0,
        "case_order": case_ids,
        "case_order_hash": case_order_hash,
        "requests": [
            {
                key: request[key]
                for key in (
                    "case_index",
                    "case_id",
                    "database_id",
                    "split",
                    "question_sha256",
                    "context_sha256",
                    "prompt_sha256",
                    "request_sha256",
                    "request_bytes",
                )
            }
            for request in requests
        ],
    }
    _dump(MANIFEST_PATH, contract)
    _dump(ROOT / "manifests" / "m47b_request_ledger.json", ledger)
    _dump(
        AUDIT_ROOT / "m47b_preflight_integrity.json",
        {
            "contract": contract,
            "reference_noop": noop,
            "inventory": inventory["summary"],
            "provider_calls": 0,
            "model_calls": 0,
        },
    )
    _dump(
        AUDIT_ROOT / "m47b_normalizer_freeze.json",
        {"m47a_manifest": m47a, "source_hashes": source_hashes, "normalizer_frozen": True},
    )
    _dump(
        AUDIT_ROOT / "m47b_safe_sql_noninterference.json",
        {
            "reference_modified": noop["modified"],
            "normalizer_contract_commit": contract["normalizer_contract_commit"],
        },
    )
    return {
        "contract": contract,
        "ledger": ledger,
        "requests": requests,
        "rows": rows,
        "catalogs": catalogs,
        "preservation": preservation,
    }


def _call_generation(contract_data: dict[str, Any]) -> dict[str, Any]:
    config = _config()
    if not get_settings().llm_api_key:
        raise RuntimeError("M47B_PROVIDER_BLOCKED:DECISION_SQL_LLM_API_KEY is not configured")
    result_root = RESULT_ROOT
    for filename in ("raw_responses.jsonl", "parsed_submissions.jsonl", "request_ledger.json"):
        path = result_root / filename
        if path.exists():
            raise RuntimeError(f"M47B_ARTIFACT_EXISTS:{filename}")
    _dump(result_root / "request_ledger.json", contract_data["ledger"])
    settings = get_settings().model_copy(
        update={
            "llm_model": config["model"],
            "llm_reasoning_effort": "none",
            "llm_temperature": 0.0,
            "llm_timeout_seconds": 90,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(settings)
    calls: list[dict[str, Any]] = []
    first_response = False
    for request in contract_data["requests"]:
        started = _now()
        begin = time.perf_counter()
        provider.consume_response_wire()
        payload: Any = None
        error: Exception | None = None
        try:
            payload = asyncio.run(
                provider.complete_json_schema(
                    operation="m47b_prospective_submission",
                    system_prompt=request["instructions"],
                    user_prompt=request["user_text"],
                    schema_name="decision_sql_m47b_submission",
                    schema=submission_schema(),
                )
            )
        except Exception as exc:
            error = exc
        finished = _now()
        latency_ms = (time.perf_counter() - begin) * 1000
        capture = provider.consume_model_io()
        wire = provider.consume_response_wire()
        metadata = m39._provider_metadata(payload or {}, capture)
        response_hash = sha256_bytes(wire) if wire is not None else None
        call = {
            "case_id": request["case_id"],
            "case_index": request["case_index"],
            "database_id": request["database_id"],
            "split": request["split"],
            "request_sha256": request["request_sha256"],
            "request_bytes": request["request_bytes"],
            "started_at": started,
            "finished_at": finished,
            "latency_ms": latency_ms,
            "transport_status": "SUCCESS" if error is None else "FAILURE",
            "response_sha256": response_hash,
            **metadata,
            "provider_error": None
            if error is None
            else {
                "type": type(error).__name__,
                "message": str(error)[:240],
                **m39._error_detail(error),
            },
        }
        calls.append(call)
        _append(
            result_root / "raw_responses.jsonl",
            {
                "case_id": request["case_id"],
                "case_index": request["case_index"],
                "database_id": request["database_id"],
                "request_sha256": request["request_sha256"],
                "response_sha256": response_hash,
                "raw_response_bytes_base64": base64.b64encode(wire).decode("ascii")
                if wire is not None
                else None,
                "provider_metadata": metadata,
                "provider_error": call["provider_error"],
            },
        )
        content = getattr(capture, "raw_assistant_content_full", None)
        submission, parse_status, parse_detail, parsed_value = (
            m39._parse(content, request["case_id"])
            if error is None
            else (None, m39._classify_provider_error(error)[0], str(error), None)
        )
        _append(
            result_root / "parsed_submissions.jsonl",
            {
                "case_id": request["case_id"],
                "case_index": request["case_index"],
                "request_sha256": request["request_sha256"],
                "response_sha256": response_hash,
                "parsed_submission": parsed_value,
                "schema_validation": parse_status,
                "case_id_matches": None
                if submission is None
                else submission.case_id == request["case_id"],
                "parse_detail": parse_detail,
                "provider_metadata": metadata,
            },
        )
        if error is None and submission is not None and parse_status == "PASS":
            first_response = True
        if error is not None and not first_response and m39._classify_provider_error(error)[1]:
            raise RuntimeError(f"M47B_ABORTED:{type(error).__name__}:{str(error)[:240]}")
    if len(calls) != 90 or sum(call["transport_status"] == "SUCCESS" for call in calls) != 90:
        raise RuntimeError("M47B_GENERATION_INCOMPLETE")
    ledger = contract_data["ledger"] | {"provider_calls": 90}
    _dump(result_root / "request_ledger.json", ledger)
    manifest = {
        "experiment": "M47B",
        "provider_calls": 90,
        "genuine_responses": 90,
        "truth_hash": TRUTH_HASH,
        "prompt_hash": EXPECTED_PROMPT_HASH,
        "contract_source_commit": contract_data["contract"]["contract_source_commit"],
        "execution_commit": _git(),
        "raw_response_hash": _sha(result_root / "raw_responses.jsonl"),
        "parsed_submission_hash": _sha(result_root / "parsed_submissions.jsonl"),
        "normalizer_source_hash": M47A_NORMALIZER_HASH,
    }
    _dump(result_root / "m47b_generation_manifest.json", manifest)
    _dump(AUDIT_ROOT / "m47b_fresh_generation_integrity.json", manifest)
    return {"calls": calls, "manifest": manifest}


def _load_parsed() -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for line in (RESULT_ROOT / "parsed_submissions.jsonl").read_text().splitlines()
    ]
    records.sort(key=lambda item: int(item["case_index"]))
    if len(records) != 90 or len({item["case_id"] for item in records}) != 90:
        raise RuntimeError("M47B_PARSED_SUBMISSION_INTEGRITY")
    return records


def _score_submission(
    case: dict[str, Any],
    truth: dict[str, Any],
    parsed: dict[str, Any],
    expected: dict[str, dict[str, Any]],
    sql_override: str | None = None,
) -> dict[str, Any]:
    if not parsed:
        return {
            "gold_behavior": case["task_type"],
            "model_decision": None,
            "official_category": "INVALID_SUBMISSION",
            "official_correct": False,
            "sql_present": False,
        }
    submission = Submission.from_dict_unchecked(parsed)
    if validate_submission_invariants(case["case_id"], submission):
        return {
            "gold_behavior": case["task_type"],
            "model_decision": submission.decision,
            "official_category": "INVALID_SUBMISSION",
            "official_correct": False,
            "sql_present": submission.sql is not None,
        }
    if sql_override is None or submission.sql is None:
        return m39._evaluate(case, truth, submission, expected)
    clone = {**parsed, "sql": sql_override}
    return m39._evaluate(case, truth, Submission.from_dict_unchecked(clone), expected)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def score(group: list[dict[str, Any]]) -> dict[str, Any]:
        correct = sum(bool(item.get("official_correct")) for item in group)
        return {
            "correct": correct,
            "total": len(group),
            "rate": f"{correct / len(group):.1%}" if group else "UNAVAILABLE",
        }

    answerable = [row for row in rows if row["gold_behavior"] == "ANSWERABLE"]
    admitted = [
        row
        for row in answerable
        if row.get("model_decision") == "ANSWER" and row.get("sql_admission") == "PASS"
    ]
    return {
        "governed": score(rows),
        "answerable_tsa": score(answerable),
        "answer_rate": {
            "answered": sum(row.get("model_decision") == "ANSWER" for row in answerable),
            "total": 60,
        },
        "wrong_refusal": {
            "wrong_refusals": sum(row.get("model_decision") != "ANSWER" for row in answerable),
            "total": 60,
        },
        "conditional_sql": score(admitted),
        "authority": score([row for row in rows if row["gold_behavior"] == "AUTHORITY_BLOCKED"]),
        "ambiguity": score([row for row in rows if row["gold_behavior"] == "AMBIGUOUS"]),
        "policy": score([row for row in rows if row["gold_behavior"] == "POLICY_BLOCKED"]),
        "unauthorized_answers": sum(
            row.get("model_decision") == "ANSWER"
            for row in rows
            if row["gold_behavior"] == "AUTHORITY_BLOCKED"
        ),
        "failure_counts": dict(Counter(row.get("official_category") for row in rows)),
    }


def evaluate_offline(contract_data: dict[str, Any]) -> dict[str, Any]:
    from benchmark.m46br_recovery import build_expected_results

    expected, replay, mutation = build_expected_results()
    records = _load_parsed()
    rows_by_id = contract_data["rows"]
    catalogs = contract_data["catalogs"]
    raw_rows: list[dict[str, Any]] = []
    normalized_rows: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    validator_rows: list[dict[str, Any]] = []
    for item in records:
        case, truth = rows_by_id[item["case_id"]]
        parsed = item.get("parsed_submission")
        raw_sql = (parsed or {}).get("sql")
        raw = _score_submission(case, truth, parsed, expected)
        raw.update(
            {
                "case_id": item["case_id"],
                "case_index": item["case_index"],
                "database_id": case["database_id"],
                "decision": (parsed or {}).get("decision"),
            }
        )
        raw_diag = GrainSafetyValidator(catalogs[case["database_id"]]).validate(raw_sql)
        normal_status = "UNCHANGED"
        normal_reason = "NO_SQL"
        normalized_sql = raw_sql
        norm_result: Any = None
        if raw_sql is not None and (parsed or {}).get("decision") == "ANSWER":
            norm_result = GrainSafeNormalizer(catalogs[case["database_id"]]).normalize(raw_sql)
            normal_status = norm_result.status.value
            normal_reason = norm_result.reason_code.value
            normalized_sql = norm_result.output_sql
        normalized = _score_submission(case, truth, parsed, expected, normalized_sql)
        normalized.update(
            {
                "case_id": item["case_id"],
                "case_index": item["case_index"],
                "database_id": case["database_id"],
                "decision": (parsed or {}).get("decision"),
            }
        )
        norm_diag = GrainSafetyValidator(catalogs[case["database_id"]]).validate(normalized_sql)
        validator_rows.append(
            {
                "case_id": item["case_id"],
                "raw": raw_diag.model_dump(mode="json"),
                "normalized": norm_diag.model_dump(mode="json"),
            }
        )
        ledger.append(
            {
                "case_id": item["case_id"],
                "decision": (parsed or {}).get("decision"),
                "raw_sql_hash": sha256_text(raw_sql) if raw_sql is not None else None,
                "raw_diagnostic": raw_diag.code.value,
                "normalization_status": normal_status,
                "normalization_reason": normal_reason,
                "normalized_sql_hash": sha256_text(normalized_sql)
                if normalized_sql is not None
                else None,
                "normalized_diagnostic": norm_diag.code.value,
                "changed": raw_sql != normalized_sql,
                "idempotent": True
                if norm_result is None
                else GrainSafeNormalizer(catalogs[case["database_id"]])
                .normalize(normalized_sql)
                .output_sql
                == normalized_sql,
            }
        )
        raw_rows.append(raw)
        normalized_rows.append(normalized)
    if any(
        raw.get("model_decision") != norm.get("model_decision")
        for raw, norm in zip(raw_rows, normalized_rows, strict=True)
    ):
        raise RuntimeError("M47B_PIPELINE_CONTAMINATION:decision_changed")
    _dump(RESULT_ROOT / "raw_case_results.json", raw_rows)
    _dump(RESULT_ROOT / "normalized_case_results.json", normalized_rows)
    _dump(RESULT_ROOT / "normalization_ledger.json", ledger)
    _dump(
        RESULT_ROOT / "paired_case_analysis.json",
        [
            {
                "case_id": r["case_id"],
                "raw_correct": r["official_correct"],
                "normalized_correct": n["official_correct"],
                "raw_category": r["official_category"],
                "normalized_category": n["official_category"],
                "decision": r.get("model_decision"),
            }
            for r, n in zip(raw_rows, normalized_rows, strict=True)
        ],
    )
    _dump(RESULT_ROOT / "validator_diagnostics.json", validator_rows)
    fresh_fanout = [
        row
        for row in ledger
        if row["raw_diagnostic"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT.value
    ]
    changed_safe = [
        row
        for row in ledger
        if row["raw_diagnostic"] in {"PASS", "NOT_APPLICABLE"} and row["changed"]
    ]
    regressions = [
        {"case_id": r["case_id"], "raw": r["official_correct"], "normalized": n["official_correct"]}
        for r, n in zip(raw_rows, normalized_rows, strict=True)
        if r["official_correct"] and not n["official_correct"]
    ]
    summary = {
        "experiment": "M47B",
        "raw": _summary(raw_rows),
        "normalized": _summary(normalized_rows),
        "provider_calls": 90,
        "model_calls": 90,
        "raw_fanout": len(fresh_fanout),
        "normalized_fanout": sum(
            item["normalized_diagnostic"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT.value
            for item in ledger
        ),
        "normalized_candidates": sum(
            item["normalization_status"] == NormalizationStatus.NORMALIZED.value for item in ledger
        ),
        "abstained_candidates": sum(
            item["raw_diagnostic"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT.value
            and item["normalization_status"] == NormalizationStatus.ABSTAIN.value
            for item in ledger
        ),
        "safe_sql_changed": len(changed_safe),
        "regressions": regressions,
        "reference_precheck": replay,
        "mutation_precheck": {key: value for key, value in mutation.items() if key != "records"},
        "truth_hash": TRUTH_HASH,
        "prompt_hash": EXPECTED_PROMPT_HASH,
    }
    _dump(RESULT_ROOT / "m47b_summary.json", summary)
    return {
        "raw": raw_rows,
        "normalized": normalized_rows,
        "ledger": ledger,
        "summary": summary,
        "validator": validator_rows,
    }


def run_offline_twice(contract_data: dict[str, Any]) -> dict[str, Any]:
    first = evaluate_offline(contract_data)
    snapshot = {
        name: _sha(RESULT_ROOT / name)
        for name in (
            "raw_case_results.json",
            "normalized_case_results.json",
            "normalization_ledger.json",
            "paired_case_analysis.json",
            "validator_diagnostics.json",
            "m47b_summary.json",
        )
    }
    evaluate_offline(contract_data)
    second_snapshot = {name: _sha(RESULT_ROOT / name) for name in snapshot}
    result = {
        "first": snapshot,
        "second": second_snapshot,
        "identical": snapshot == second_snapshot,
    }
    _dump(AUDIT_ROOT / "m47b_determinism.json", result)
    if not result["identical"]:
        raise RuntimeError("M47B_NONDETERMINISTIC_REPLAY")
    return first


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "generate", "evaluate"))
    args = parser.parse_args()
    if args.command == "preflight":
        data = preflight()
        print(json.dumps(data["contract"], indent=2, sort_keys=True))
    elif args.command == "generate":
        contract = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        ledger = json.loads(
            (ROOT / "manifests" / "m47b_request_ledger.json").read_text(encoding="utf-8")
        )
        case_ids, rows = _rows()
        answerable = [truth for _case, truth in _answerable_pairs()]
        catalogs, _inventory = _build_catalogs(answerable)
        data = {
            "contract": contract,
            "ledger": ledger,
            "requests": _requests(case_ids, rows),
            "rows": rows,
            "catalogs": catalogs,
        }
        print(json.dumps(_call_generation(data)["manifest"], indent=2, sort_keys=True))
    else:
        contract = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        ledger = json.loads(
            (ROOT / "manifests" / "m47b_request_ledger.json").read_text(encoding="utf-8")
        )
        case_ids, rows = _rows()
        answerable = [truth for _case, truth in _answerable_pairs()]
        catalogs, _inventory = _build_catalogs(answerable)
        print(
            json.dumps(
                run_offline_twice(
                    {
                        "contract": contract,
                        "ledger": ledger,
                        "requests": _requests(case_ids, rows),
                        "rows": rows,
                        "catalogs": catalogs,
                    }
                )["summary"],
                indent=2,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
