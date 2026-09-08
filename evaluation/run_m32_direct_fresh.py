"""Fresh one-call DIRECT control for M32 comparability.

This runner exists only because M32's base semantic context contains the
server-owned mapping block that was not present in the persisted historical
DIRECT arm.  It uses the exact M32 base contexts, the same provider settings,
and one raw-SQL call per frozen case.  It does not use alignment or grounded
SQL context.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.generation.provider import OpenAICompatibleProvider, SqlProposal
from app.models.domain import QueryRequest
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from evaluation.external.livesqlbench.evaluator import soft_ex_match
from evaluation.run_m29_semantic_plan import DB_IMAGE_DIGEST
from evaluation.run_m32 import (
    MODEL,
    TIMEOUT_SECONDS,
    _hash_json,
    _load_cases,
    _metadata_blocked_ids,
    _result,
    _settings,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evaluation/fixtures/m32_direct_fresh_manifest.json"
RESULT = ROOT / "evaluation/fixtures/m32_direct_fresh_result.json"
RAW_ROOT = ROOT / "evaluation/external/livesqlbench/protected/results/m32/direct_fresh"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_capture(case_id: str, capture: Any | None) -> None:
    if capture is None:
        return
    path = RAW_ROOT / case_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "direct.json").write_text(
        json.dumps(capture.model_dump(mode="json"), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _manifest(cases: list[Any], contexts: dict[str, str]) -> dict[str, Any]:
    ids = [case.instance_id for case in cases]
    from app.generation.provider import _generation_messages

    return {
        "milestone": "M32-DIRECT-FRESH",
        "reason": (
            "Historical DIRECT used materially different base context; fresh paired "
            "control uses exact M32 base contexts."
        ),
        "provider": "openai-compatible",
        "model": MODEL,
        "reasoning": "none",
        "temperature": 0.0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "calls_per_case": 1,
        "semantic_retries": 0,
        "repair_calls": 0,
        "alignment_calls": 0,
        "case_order_hash": _hash_json(ids),
        "generation_prompt_hash": _sha256(inspect.getsource(_generation_messages)),
        "context_hashes": {case_id: _sha256(contexts[case_id]) for case_id in ids},
        "context_profile": "M32 base corrected semantic context plus server mapping IDs",
        "db_image_digest": DB_IMAGE_DIGEST,
        "raw_artifact_root": str(RAW_ROOT),
    }


async def main() -> int:
    cases, contexts, _mappings, states, _expected_alignments = _load_cases()
    manifest = _manifest(cases, contexts)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provider = OpenAICompatibleProvider(_settings())
    rows: list[dict[str, Any]] = []

    for ordinal, case in enumerate(cases, start=1):
        started = time.perf_counter()
        row: dict[str, Any] = {
            "ordinal": ordinal,
            "case_id": case.instance_id,
            "database": case.database,
            "request_made": True,
            "response_received": False,
            "provider_status": "NOT_REACHED",
            "response_hash": None,
            "generated_sql_hash": None,
            "m1": "NOT_REACHED",
            "explain": "NOT_REACHED",
            "executed": "NOT_REACHED",
            "official": "NOT_REACHED",
            "primary_failure": None,
        }
        try:
            proposal: SqlProposal = await provider.propose_sql(
                QueryRequest(question=case.runtime.question),
                None,
                contexts[case.instance_id],
            )
            capture = provider.consume_model_io()
            _write_capture(case.instance_id, capture)
            row["response_received"] = capture is not None
            row["provider_status"] = "PROVIDER_SUCCESS"
            row["response_hash"] = (
                capture.raw_assistant_content_sha256 if capture is not None else None
            )
            row["latency_ms"] = proposal.latency_ms
            row["usage"] = capture.usage if capture is not None else {}
            row["generated_sql_hash"] = _sha256(proposal.sql)
            safety = states[case.database][2]
            planned = safety.plan(SqlCandidate(sql=proposal.sql))
            if not isinstance(planned, QueryPlan):
                row["m1"] = "REJECTED"
                row["primary_failure"] = "M1_REJECT"
                if isinstance(planned, SqlPlanFailure):
                    row["m1_failure"] = planned.status.value
            else:
                row["m1"] = "ACCEPTED"
                row["explain"] = "ACCEPTED"
                row["explain_estimate"] = planned.estimate.model_dump(mode="json")
                execution = safety.execute(planned)
                if not isinstance(execution, QueryExecution):
                    row["executed"] = "FAILED"
                    row["primary_failure"] = "EXECUTION_ERROR"
                else:
                    row["executed"] = "SUCCESS"
                    reference = safety.plan(SqlCandidate(sql=case.sol_sql[0]))
                    if not isinstance(reference, QueryPlan):
                        row["official"] = "LIMITATION"
                        row["primary_failure"] = "EVALUATOR_LIMITATION"
                    else:
                        reference_execution = safety.execute(reference)
                        if not isinstance(reference_execution, QueryExecution):
                            row["official"] = "LIMITATION"
                            row["primary_failure"] = "EVALUATOR_LIMITATION"
                        elif soft_ex_match(
                            _result(execution),
                            _result(reference_execution),
                            ordered=bool(case.public.conditions.get("order", False)),
                        ):
                            row["official"] = "CORRECT"
                        else:
                            row["official"] = "INCORRECT"
                            row["primary_failure"] = "SQL_SYNTHESIS_ERROR"
        except Exception as error:
            capture = provider.consume_model_io()
            _write_capture(case.instance_id, capture)
            row["provider_status"] = "PROVIDER_ERROR"
            row["primary_failure"] = type(error).__name__
            row["error_message"] = str(error)[:240]
        finally:
            row["total_latency_ms"] = (time.perf_counter() - started) * 1000
            rows.append(row)
            print(
                json.dumps(
                    {
                        "completed": ordinal,
                        "case_id": case.instance_id,
                        "m1": row["m1"],
                        "executed": row["executed"],
                        "official": row["official"],
                        "failure": row["primary_failure"],
                    }
                ),
                flush=True,
            )

    official = Counter(row["official"] for row in rows)
    failures = Counter(row["primary_failure"] for row in rows if row["primary_failure"])
    metadata_blocked = _metadata_blocked_ids()
    normalized_rows = [row for row in rows if row["case_id"] not in metadata_blocked]
    latencies = sorted(float(row["total_latency_ms"]) for row in rows)
    result = {
        "milestone": "M32-DIRECT-FRESH",
        "status": "COMPLETED",
        "reason": manifest["reason"],
        "total_cases": len(rows),
        "requests": sum(row["request_made"] for row in rows),
        "responses": sum(row["response_received"] for row in rows),
        "provider_success": sum(row["provider_status"] == "PROVIDER_SUCCESS" for row in rows),
        "m1_accepted": sum(row["m1"] == "ACCEPTED" for row in rows),
        "executed": sum(row["executed"] == "SUCCESS" for row in rows),
        "official": dict(official),
        "official_correct": official["CORRECT"],
        "authority_normalized": {
            "cases": len(normalized_rows),
            "official_correct": sum(row["official"] == "CORRECT" for row in normalized_rows),
            "executed": sum(row["executed"] == "SUCCESS" for row in normalized_rows),
        },
        "metadata_blocked_cases": sorted(metadata_blocked),
        "primary_failures": dict(failures),
        "latency_ms": {
            "min": min(latencies),
            "median": latencies[len(latencies) // 2]
            if len(latencies) % 2
            else (latencies[len(latencies) // 2 - 1] + latencies[len(latencies) // 2]) / 2,
            "max": max(latencies),
        },
        "rows": rows,
        "manifest": str(MANIFEST),
    }
    RESULT.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps({"official_correct": result["official_correct"], "total": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
