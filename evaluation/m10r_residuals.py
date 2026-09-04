# ruff: noqa: E501

"""Deterministic post-run residual classification for M10R."""

from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.sql.parser import SQLParser
from app.sql.policy import SQLPolicy

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "evaluation" / "results" / "m10r" / "clean-rebaseline-20260904"


def _write(name: str, value: Any) -> str:
    path = RUN_DIR / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def _load_rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (RUN_DIR / "case_ledger.jsonl").read_text().splitlines() if line.strip()]


def run() -> dict[str, Any]:
    rows = _load_rows()
    case_ledger_hash = sha256((RUN_DIR / "case_ledger.jsonl").read_bytes()).hexdigest()
    residuals: list[dict[str, Any]] = []
    mechanism_counts: Counter[str] = Counter()
    m1_reason_counts: Counter[str] = Counter()
    parser = SQLParser()
    policy = SQLPolicy(build_default_catalog(Base.metadata))
    for row in rows:
        status = row.get("primary_status")
        mechanism = None
        basis = ""
        if status == "M1_REJECTED":
            sql = row.get("candidate_sql") or ""
            if "%" in sql:
                mechanism = "M1_TRANSPORT_INCOMPATIBLE_GENERATED_SQL"
                basis = "candidate contains raw percent syntax and was rejected at the frozen M1 EXPLAIN transport boundary"
                m1_reason = "TRANSPORT_PLACEHOLDER_COLLISION"
            else:
                mechanism = "M1_INCOMPATIBLE_GENERATED_STRUCTURE"
                basis = "M1 rejection was recorded, but the runner did not retain a more specific policy code"
                try:
                    rejection = policy.validate(parser.parse(sql))
                    m1_reason = str(rejection.code) if rejection else "EXPLAIN_OR_COST_BOUNDARY"
                except Exception as error:
                    m1_reason = type(error).__name__
                basis = f"offline policy preflight reason: {m1_reason}"
            m1_reason_counts[m1_reason] += 1
        elif status == "RESULT_MISMATCH":
            mechanism = "UNCLASSIFIED"
            basis = "candidate/reference result mismatch is observed; family membership alone is insufficient for mechanism attribution"
        elif status not in ("CORRECT", None):
            mechanism = status
            basis = "stage status"
        if mechanism is not None:
            mechanism_counts[mechanism] += 1
            residuals.append({"case_id": row["case_id"], "family": row["family"], "split": row["split"], "expected_route": row["expected_route"], "actual_path": row.get("actual_path"), "primary_status": status, "mechanism": mechanism, "basis": basis})
    failures = sum(not bool(row.get("v1_correct")) for row in rows)
    coherent_candidates = {key: value for key, value in mechanism_counts.items() if key not in {"UNCLASSIFIED", "M1_INCOMPATIBLE_GENERATED_STRUCTURE", "M1_TRANSPORT_INCOMPATIBLE_GENERATED_SQL"}}
    top_coherent = max(coherent_candidates.items(), key=lambda item: item[1], default=(None, 0))
    dominant = top_coherent[1] >= 10 and top_coherent[1] >= 0.25 * failures
    route_mismatches = [row for row in rows if (row.get("actual_path") == "GOVERNED_METRIC") != (row.get("expected_route") == "GOVERNED")]
    summary = {
        "classification": "M10R_CLEAN_REBASELINE_COMPLETED_DOMINANT_RESIDUAL_IDENTIFIED" if dominant else "M10R_CLEAN_REBASELINE_COMPLETED_FRAGMENTED_RESIDUALS",
        "primary_failures": failures,
        "status_counts": dict(Counter(row.get("primary_status") for row in rows)),
        "result_mismatches": sum(row.get("primary_status") == "RESULT_MISMATCH" for row in rows),
        "generation_failures": sum(row.get("primary_status") == "GENERATION_FAILURE" for row in rows),
        "m1_rejections": sum(row.get("primary_status") == "M1_REJECTED" for row in rows),
        "m1_rejection_reason_distribution": dict(m1_reason_counts),
        "candidate_execution_failures": sum(row.get("primary_status") == "EXECUTION_FAILURE" for row in rows),
        "provider_failures": sum(row.get("primary_status") == "PROVIDER_FAILURE" for row in rows),
        "route_mismatches": len(route_mismatches),
        "harmful_route_mismatches": sum(not row.get("v1_correct") for row in route_mismatches),
        "harmless_route_mismatches": sum(bool(row.get("v1_correct")) for row in route_mismatches),
        "generated_transport_incompatible_sql_rejections": sum(item["mechanism"] == "M1_TRANSPORT_INCOMPATIBLE_GENERATED_SQL" for item in residuals),
        "semantic_family_failure_distribution": {family: {"total": sum(row["family"] == family for row in rows), "failures": sum(row["family"] == family and not row.get("v1_correct") for row in rows)} for family in sorted({row["family"] for row in rows})},
        "secondary_mechanism_distribution": dict(mechanism_counts),
        "top_residual_mechanism": top_coherent[0] if dominant else mechanism_counts.most_common(1)[0][0] if mechanism_counts else None,
        "top_residual_count": top_coherent[1] if dominant else mechanism_counts.most_common(1)[0][1] if mechanism_counts else 0,
        "dominant_residual_gate_met": dominant,
        "dominance_rule": "count >= 10 and share >= 25% of primary failures, excluding unclassified/stage-only attribution",
        "deterministic_evidence_policy": "do not infer mechanism from family membership alone",
        "residuals": residuals,
    }
    shadow = [{"case_id": row["case_id"], "primary_v1_correct": row.get("v1_correct"), "shadow": row.get("v2_shadow")} for row in rows]
    primary = [{"case_id": row["case_id"], "primary_status": row.get("primary_status"), "v1_correct": row.get("v1_correct")} for row in rows]
    shadow_hash = _write("v2_shadow_results.json", shadow)
    primary_hash = _write("primary_results.json", primary)
    residual_hash = _write("residual_results.json", summary)
    summary["v2_shadow_results_hash"] = shadow_hash
    summary["primary_results_hash"] = primary_hash
    summary["case_ledger_hash"] = case_ledger_hash
    summary["residual_results_hash"] = residual_hash
    final_summary_hash = _write("final_summary.json", summary)
    return {"classification": summary["classification"], "primary_failures": failures, "status_counts": summary["status_counts"], "mechanism_counts": dict(mechanism_counts), "route_mismatches": len(route_mismatches), "harmful_route_mismatches": summary["harmful_route_mismatches"], "case_ledger_hash": case_ledger_hash, "primary_hash": primary_hash, "shadow_hash": shadow_hash, "residual_hash": residual_hash, "final_summary_hash": final_summary_hash}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
