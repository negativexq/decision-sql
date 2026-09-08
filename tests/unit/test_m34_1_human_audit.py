import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "benchmark" / "audits" / "m34_1_human_audit.json"

CASE_IDS = [
    f"{domain}_{index:02d}" for domain in ("commerce", "fleet", "support") for index in range(1, 11)
]

REQUIRED_FIELDS = {
    "case_id",
    "database",
    "task_type",
    "question_naturalness",
    "question_clarity",
    "context_sufficiency",
    "authority_correctness",
    "semantic_target_correctness",
    "metric_business_semantics",
    "population_semantics",
    "temporal_semantics",
    "reference_a_correctness",
    "reference_b_correctness",
    "reference_diversity",
    "result_contract",
    "counterfactual_quality",
    "mutant_quality",
    "authority_trap_quality",
    "ambiguity_quality",
    "domain_realism",
    "authoring_circularity",
    "benchmark_value",
    "final_recommendation",
    "defect_codes",
    "evidence",
}


def _tree_hash(relative_root: str) -> str:
    root = ROOT / relative_root
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def test_m34_1_audits_each_pilot_case_once() -> None:
    payload = json.loads(AUDIT.read_text(encoding="utf-8"))
    scorecards = payload["scorecards"]
    assert len(scorecards) == 30
    assert [row["case_id"] for row in scorecards] == CASE_IDS
    assert len({row["case_id"] for row in scorecards}) == 30
    assert all(REQUIRED_FIELDS <= row.keys() for row in scorecards)
    assert {row["final_recommendation"] for row in scorecards} <= {"ACCEPT", "REVISE", "REJECT"}
    source_types = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))["task_type"]
        for path in sorted((ROOT / "benchmark" / "cases" / "pilot").glob("*.json"))
    }
    assert {row["case_id"]: row["task_type"] for row in scorecards} == source_types
    assert payload["recommendation_counts"] == {
        status: sum(row["final_recommendation"] == status for row in scorecards)
        for status in ("ACCEPT", "REVISE", "REJECT")
    }
    assert payload["provider_calls"] == 0


def test_m34_1_pilot_sources_match_pre_audit_hashes() -> None:
    payload = json.loads(AUDIT.read_text(encoding="utf-8"))
    for relative_root, expected in payload["immutability_before"].items():
        assert _tree_hash(relative_root) == expected
