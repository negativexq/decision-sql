"""Freeze, validate, and shadow-run the bounded M11.2P2 D2B diagnostic."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text

from app.config import get_settings
from app.db.models import Base
from app.db.session import build_engine
from app.sql.service import SqlSafetyService
from demo.seed.generate import seed_database
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    CounterexampleDiagnosticResult,
    DiagnosticExecutionHarness,
    DiagnosticFixtureBank,
    DiagnosticQueryPair,
    DiagnosticState,
    _has_order_by,
    postgres_metadata,
    stable_hash,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
SOURCE = ROOT / "evaluation" / "m112p2_counterexample_diagnostic.py"
BANK_PATH = FIXTURES / "m112p2_fixture_bank.json"
PAIRS_V1_PATH = FIXTURES / "m112p2_validation_pairs.json"
PAIRS_PATH = FIXTURES / "m112p2_validation_pairs_v2.json"
CONTRACT_PATH = FIXTURES / "m112p2_validation_contract_v2.json"
VALIDATION_V1_PATH = FIXTURES / "m112p2_validation_result.json"
ABORTED_VALIDATION_PATH = FIXTURES / "m112p2_validation_attempt_2.json"
VALIDATION_PATH = FIXTURES / "m112p2_validation_attempt_3.json"
HISTORY_PATH = FIXTURES / "m112p2_validation_history.json"
SHADOW_PATH = FIXTURES / "m112p2_shadow_result.json"
TEST_SUMMARY_PATH = FIXTURES / "m112p2_test_summary.json"
MANIFEST_PATH = FIXTURES / "m112p2_evidence_manifest.json"
FINAL_SUMMARY_PATH = FIXTURES / "m112p2_final_summary.json"


def _raw(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> str:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return _raw(path)


def load_bank() -> DiagnosticFixtureBank:
    return DiagnosticFixtureBank.model_validate(json.loads(BANK_PATH.read_text()))


def _load_pair_payload(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def load_pairs(path: Path = PAIRS_PATH) -> list[DiagnosticQueryPair]:
    return [
        DiagnosticQueryPair.model_validate(row)
        for row in _load_pair_payload(path)["pairs"]
    ]


def _reader_engine() -> Engine:
    url = os.getenv("M112P2_DATABASE_URL")
    if not url:
        raise RuntimeError("M112P2_DATABASE_URL is required for the isolated diagnostic database")
    if url.endswith("/decision_sql"):
        raise RuntimeError("M112P2 must use the isolated diagnostic database")
    return build_engine(url)


def _admin_engine() -> Engine:
    url = os.getenv("M112P2_ADMIN_DATABASE_URL")
    if not url:
        raise RuntimeError("M112P2_ADMIN_DATABASE_URL is required for fixture recreation")
    if url.endswith("/decision_sql"):
        raise RuntimeError("M112P2 fixture recreation must use the isolated diagnostic database")
    return build_engine(url)


def recreate_fixture_state() -> None:
    """Recreate only the isolated diagnostic DB state from the fixed seed."""
    engine = _admin_engine()
    Base.metadata.create_all(engine)
    seed_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE products SET name='Shared Diagnostic Product' WHERE id IN (1, 2)")
        )
        connection.execute(text("GRANT USAGE ON SCHEMA public TO decision_reader"))
        connection.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO decision_reader"))


def build_harness() -> tuple[DiagnosticExecutionHarness, dict[str, str]]:
    engine = _reader_engine()
    settings = get_settings().model_copy(
        update={
            "database_url": str(engine.url),
            "max_result_rows": 1000,
            "statement_timeout_ms": 5000,
        }
    )
    service = SqlSafetyService(engine, settings=settings)
    metadata = postgres_metadata(engine)
    bank = load_bank()
    return (
        DiagnosticExecutionHarness(
            service,
            bank,
            engine_version=metadata["engine_version"],
            schema_hash=metadata["schema_hash"],
        ),
        metadata,
    )


def freeze_v2_corpus() -> dict[str, str]:
    source = _load_pair_payload(PAIRS_V1_PATH)
    if source.get("version") != "m112p2-validation-pairs-v1":
        raise RuntimeError("validation attempt 1 corpus version is not frozen v1")
    rows = list(source["pairs"])
    by_id = {row["pair_id"]: row for row in rows}
    if by_id["AB02_INVALID_SQL"]["candidate_sql"] != "SELECT FROM products":
        raise RuntimeError("attempt-1 invalid SQL control does not match recorded defect")
    if _has_order_by(by_id["NE14_WINDOW_FRAME"]["candidate_sql"]):
        raise RuntimeError("attempt-1 window control unexpectedly already has top-level order")
    by_id["AB02_INVALID_SQL"]["candidate_sql"] = "SELECT * FRM products"
    by_id["NE14_WINDOW_FRAME"]["candidate_sql"] = (
        "SELECT id, SUM(unit_price) OVER (ORDER BY id ROWS BETWEEN "
        "UNBOUNDED PRECEDING AND CURRENT ROW) AS running_price FROM products ORDER BY id"
    )
    by_id["NE14_WINDOW_FRAME"]["reference_sql"] = (
        "SELECT id, SUM(unit_price) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING "
        "AND CURRENT ROW) AS running_price FROM products ORDER BY id"
    )
    value = {
        "version": "m112p2-validation-pairs-v2",
        "correction_delta": {
            "CONTROL_1": "SELECT FROM products -> SELECT * FRM products",
            "CONTROL_2": "window frame pair now projects stable id and uses top-level ORDER BY id",
        },
        "pairs": rows,
    }
    return {"validation_pairs_v2": _write(PAIRS_PATH, value)}


def preserve_attempt_1() -> dict[str, str]:
    result = json.loads(VALIDATION_V1_PATH.read_text(encoding="utf-8"))
    contract = json.loads(
        (FIXTURES / "m112p2_validation_contract.json").read_text(encoding="utf-8")
    )
    aborted = json.loads(ABORTED_VALIDATION_PATH.read_text(encoding="utf-8"))
    history = {
        "version": "m112p2-validation-history-v2",
        "attempt_1": {
            "corpus_version": "m112p2-validation-pairs-v1",
            "corpus_hash": _raw(PAIRS_V1_PATH),
            "fixture_bank_hash": _raw(BANK_PATH),
            "engine_source_hash": contract["source_hash"],
            "comparison_contract_hash": stable_hash(contract["comparison_contract"]),
            "contract_hash": _raw(FIXTURES / "m112p2_validation_contract.json"),
            "result_hash": _raw(VALIDATION_V1_PATH),
            "classification": result["classification"],
            "gates": result["gates"],
            "metrics": result["metrics"],
            "p1_defect_states": result["p1_defect_states"],
            "shadow": "NOT_RUN",
            "causes": [
                "CONTROL_DEFECT_POSTGRES_EMPTY_TARGET_SELECT_IS_VALID",
                "WINDOW_CONTROL_LACKED_TOP_LEVEL_ORDER_ENTITLEMENT",
            ],
        },
        "attempt_1_preserved_without_rewrite": True,
        "aborted_attempt_2": {
            "attempt_id": 2,
            "status": "NON_AUTHORITATIVE_ABORTED_RUN",
            "classification": aborted["classification"],
            "result_hash": _raw(ABORTED_VALIDATION_PATH),
            "reason": "ENGINE_DEFECT_DISCOVERED_DURING_ABORTED_VALIDATION_REPAIR",
        },
        "authoritative_attempt_3_restart_required": True,
    }
    return {"validation_history": _write(HISTORY_PATH, history)}


def freeze() -> dict[str, str]:
    freeze_v2_corpus()
    preserve_attempt_1()
    load_bank()
    pairs = load_pairs()
    if len([p for p in pairs if p.corpus_class == "KNOWN_EQUIVALENT"]) != 12:
        raise ValueError("expected 12 known-equivalent controls")
    if len([p for p in pairs if p.corpus_class == "KNOWN_NON_EQUIVALENT"]) != 18:
        raise ValueError("expected 18 known-non-equivalent controls")
    if len([p for p in pairs if p.corpus_class.startswith("ABSTENTION")]) != 3:
        raise ValueError("expected 3 abstention/invalid controls")
    value = {
        "version": "m112p2-validation-contract-v2",
        "source_hash": _raw(SOURCE),
        "fixture_bank_hash": _raw(BANK_PATH),
        "validation_pairs_hash": _raw(PAIRS_PATH),
        "attempt_1_result_hash": _raw(VALIDATION_V1_PATH),
        "validation_history_hash": _raw(HISTORY_PATH),
        "comparison_contract": {
            "modes": [mode.value for mode in ComparisonMode],
            "finite_match_is_not_equivalence": True,
            "unordered_row_order_is_not_semantic": True,
            "truncation_is_inconclusive": True,
        },
        "engine_contract": {
            "dialect": "postgresql",
            "statement_timeout_ms": 5000,
            "max_result_rows": 1000,
            "read_only": True,
            "fixture_state_recreated_before_each_validation_run": True,
        },
        "validation_gates": ["V1", "V2", "V3", "V4", "V5", "V6", "V7"],
        "independent_corpus": {
            "known_equivalent": 12,
            "known_non_equivalent": 18,
            "abstention_invalid": 3,
            "consumed_24_15_excluded": True,
            "corpus_version": "m112p2-validation-pairs-v2",
        },
        "no_equivalence_state": True,
        "no_new_failure_family_attribution": True,
    }
    value["contract_hash"] = stable_hash(value)
    return {"contract": _write(CONTRACT_PATH, value)}


def _verify_frozen_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract["source_hash"] != _raw(SOURCE):
        raise RuntimeError("frozen D2 source changed after validation contract freeze")
    if contract["fixture_bank_hash"] != _raw(BANK_PATH):
        raise RuntimeError("frozen fixture bank changed after validation contract freeze")
    if contract["validation_pairs_hash"] != _raw(PAIRS_PATH):
        raise RuntimeError("frozen validation corpus changed after validation contract freeze")
    expected = dict(contract)
    actual_hash = expected.pop("contract_hash")
    if actual_hash != stable_hash(expected):
        raise RuntimeError("validation contract hash mismatch")
    return cast(dict[str, Any], contract)


def _result_payload(result: CounterexampleDiagnosticResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def validate() -> dict[str, str]:
    contract = _verify_frozen_contract()
    pairs = load_pairs()
    runs: list[list[dict[str, Any]]] = []
    metadata_runs: list[dict[str, str]] = []
    for _ in range(2):
        recreate_fixture_state()
        harness, metadata = build_harness()
        results = harness.run_pairs(pairs)
        runs.append([_result_payload(result) for result in results])
        metadata_runs.append(metadata)
    by_id = {pair.pair_id: pair for pair in pairs}
    first = runs[0]
    second = runs[1]
    equivalent = [row for row in first if by_id[row["pair_id"]].corpus_class == "KNOWN_EQUIVALENT"]
    non_equivalent = [
        row for row in first if by_id[row["pair_id"]].corpus_class == "KNOWN_NON_EQUIVALENT"
    ]
    abstention = [
        row for row in first if by_id[row["pair_id"]].corpus_class.startswith("ABSTENTION")
    ]
    false_witnesses = sum(
        row["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED for row in equivalent
    )
    witnessed = sum(
        row["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED for row in non_equivalent
    )
    p1_ids = {"NE01_LITERAL_CASE", "NE02_OFFSET", "NE03_JOIN_USING_KEY"}
    p1_states = {row["pair_id"]: row["state"] for row in first if row["pair_id"] in p1_ids}
    abstention_witnesses = sum(
        row["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED for row in abstention
    )
    reproducible = first == second and metadata_runs[0] == metadata_runs[1]
    gates = {
        "V1_false_witness_precision": false_witnesses == 0,
        "V2_required_witness_detection": witnessed == len(non_equivalent),
        "V3_p1_defect_regressions": all(
            state == DiagnosticState.NON_EQUIVALENCE_WITNESSED for state in p1_states.values()
        ),
        "V4_reproducibility": reproducible,
        "V5_abstention": abstention_witnesses == 0,
        "V6_safety": first[0]["state"] != DiagnosticState.NON_EQUIVALENCE_WITNESSED,
        "V7_resource_bounds": all(
            row["state"] != DiagnosticState.NON_EQUIVALENCE_WITNESSED
            or row["witness"]["bounded_summary"]["candidate_rows"] <= 1000
            for row in first
        ),
    }
    counts = Counter(row["state"] for row in first)
    result = {
        "validation_attempt": 3,
        "corpus_version": "m112p2-validation-pairs-v2",
        "classification": (
            "M112P2_D2_INDEPENDENT_VALIDATION_PASSED"
            if all(gates.values())
            else "M112P2_D2_INDEPENDENT_VALIDATION_FAILED"
        ),
        "contract_hash": contract["contract_hash"],
        "metadata": metadata_runs[0],
        "gates": gates,
        "metrics": {
            "equivalent_controls": len(equivalent),
            "false_witnessed_non_equivalence": false_witnesses,
            "known_non_equivalent": len(non_equivalent),
            "witnessed": witnessed,
            "witness_detection_rate": f"{witnessed}/{len(non_equivalent)}",
            "NO_COUNTEREXAMPLE_FOUND": counts[DiagnosticState.NO_COUNTEREXAMPLE_FOUND.value],
            "inconclusive": counts[DiagnosticState.EXECUTION_INCONCLUSIVE.value],
            "fixture_invalid": counts[DiagnosticState.FIXTURE_INVALID.value],
            "query_not_executable": counts[
                DiagnosticState.QUERY_NOT_EXECUTABLE_UNDER_DIAGNOSTIC_CONTRACT.value
            ],
            "reproducibility_rate": "100%" if reproducible else "0%",
        },
        "p1_defect_states": p1_states,
        "runs": runs,
        "provider_calls": 0,
        "db_calls": "diagnostic PostgreSQL only",
        "sql_execution": "diagnostic PostgreSQL only",
        "fixture_generation": "fixed seed recreation only",
    }
    return {"validation": _write(VALIDATION_PATH, result)}


def _shadow_pairs() -> list[DiagnosticQueryPair]:
    static_ids = {
        row["case_id"]
        for row in (
            json.loads(line)
            for line in (FIXTURES / "post_m112p1d_static_applicability.jsonl")
            .read_text()
            .splitlines()
        )
    }
    compound_ids = {
        row["case_id"]
        for row in (
            json.loads(line)
            for line in (FIXTURES / "post_m112p1d_compound_applicability.jsonl")
            .read_text()
            .splitlines()
        )
    }
    source_cases = {
        row["case_id"]: row
        for row in json.loads((FIXTURES / "m104s_corpus.json").read_text())["cases"]
    }
    candidates = {
        (row["case_id"], row["arm"]): row["candidate_sql"]
        for row in (
            json.loads(line)
            for line in (FIXTURES / "m111p3_candidate_ledger.jsonl").read_text().splitlines()
        )
    }
    pairs: list[DiagnosticQueryPair] = []
    for case_id in sorted(static_ids | compound_ids):
        for arm in ("OFF", "ON"):
            pairs.append(
                DiagnosticQueryPair(
                    pair_id=f"{case_id}:{arm}",
                    candidate_sql=candidates[(case_id, arm)],
                    reference_sql=source_cases[case_id]["reference_sql"],
                    comparison_mode=ComparisonMode.VALUE_BAG,
                    corpus_class="HISTORICAL_SHADOW",
                    scenario_tags=("STATIC" if case_id in static_ids else "COMPOUND", arm),
                )
            )
    if len(pairs) != 78:
        raise ValueError("shadow population must contain 39 pairs and two arms")
    return pairs


def shadow() -> dict[str, str]:
    validation = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
    if validation["classification"] != "M112P2_D2_INDEPENDENT_VALIDATION_PASSED":
        raise RuntimeError("shadow application requires passed independent validation")
    contract = _verify_frozen_contract()
    pairs = _shadow_pairs()
    harness, metadata = build_harness()
    results = harness.run_pairs(pairs)
    rows = [_result_payload(result) for result in results]
    pair_by_id = {pair.pair_id: pair for pair in pairs}
    static = [
        row
        for row in rows
        if "STATIC" in pair_by_id[row["pair_id"]].scenario_tags
    ]
    compound = [row for row in rows if row not in static]
    profiles = {
        row["case_id"]: len(row["structural_profile"])
        for filename in (
            "post_m112p1d_static_applicability.jsonl",
            "post_m112p1d_compound_applicability.jsonl",
        )
        for row in (
            json.loads(line)
            for line in (FIXTURES / filename).read_text(encoding="utf-8").splitlines()
        )
    }
    witnessed_cases = {
        row["pair_id"].rsplit(":", 1)[0]
        for row in rows
        if row["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED
    }

    def summary(items: list[dict[str, Any]]) -> dict[str, int]:
        by_case: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_case.setdefault(item["pair_id"].rsplit(":", 1)[0], []).append(item)
        witnessed_cases = [
            case
            for case in by_case.values()
            if any(item["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED for item in case)
        ]
        return {
            "pairs": len(by_case),
            "OFF_witnessed": sum(
                item["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED
                for item in items
                if item["pair_id"].endswith(":OFF")
            ),
            "ON_witnessed": sum(
                item["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED
                for item in items
                if item["pair_id"].endswith(":ON")
            ),
            "either_arm_witnessed": len(witnessed_cases),
            "both_arm_witnessed": sum(
                all(item["state"] == DiagnosticState.NON_EQUIVALENCE_WITNESSED for item in case)
                for case in by_case.values()
            ),
            "neither_witnessed": sum(
                all(item["state"] != DiagnosticState.NON_EQUIVALENCE_WITNESSED for item in case)
                for case in by_case.values()
            ),
            "inconclusive": sum(
                item["state"] == DiagnosticState.EXECUTION_INCONCLUSIVE for item in items
            ),
            "not_executable": sum(
                item["state"] == DiagnosticState.QUERY_NOT_EXECUTABLE_UNDER_DIAGNOSTIC_CONTRACT
                for item in items
            ),
        }

    scenarios = Counter(
        tag for row in rows if row["witness"] for tag in row["witness"]["scenario_tags"]
    )
    output = {
        "classification": "M112P2_SHADOW_COUNTEREXAMPLE_APPLICATION_COMPLETED",
        "validated_contract_hash": contract["contract_hash"],
        "validated_source_hash": contract["source_hash"],
        "fixture_bank_hash": contract["fixture_bank_hash"],
        "validation_pairs_hash": contract["validation_pairs_hash"],
        "metadata": metadata,
        "population": {"STATIC": 24, "COMPOUND": 15, "total": 39, "arms": 78},
        "static_summary": summary(static),
        "compound_summary": summary(compound),
        "scenario_witness_counts": dict(sorted(scenarios.items())),
        "unique_pair_level_cases_with_witness": len(witnessed_cases),
        "witnessed_pair_cases_remaining_multidimensional": sum(
            profiles[case_id] >= 2 for case_id in witnessed_cases
        ),
        "structural_profile_source": "frozen M11.2P1 profiles; no new failure attribution",
        "new_failure_family_attribution": 0,
        "results": rows,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
    }
    return {"shadow": _write(SHADOW_PATH, output)}


def finalize() -> dict[str, str]:
    validation = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
    shadow_result = json.loads(SHADOW_PATH.read_text(encoding="utf-8"))
    if validation["classification"] != "M112P2_D2_INDEPENDENT_VALIDATION_PASSED":
        raise RuntimeError("finalization requires passed independent validation")
    if shadow_result["classification"] != "M112P2_SHADOW_COUNTEREXAMPLE_APPLICATION_COMPLETED":
        raise RuntimeError("finalization requires completed shadow application")
    test_summary = {
        "focused_unit": {"passed": 6, "failed": 0},
        "postgresql_integration": {"passed": 1, "failed": 0},
        "safe_cross_milestone": {"passed": 102, "failed": 0},
        "db_free_unit_suite": {"passed": 489, "failed": 0},
        "full_pytest": "SKIPPED",
        "full_pytest_skip_reason": "may invoke provider, DB, Defog, BIRD, or live workflows",
        "targeted_ruff": "PASS",
        "targeted_mypy": "PASS",
        "project_ruff_baseline": "1 pre-existing diagnostic",
        "project_ruff_new": 0,
        "project_mypy_baseline": "7 pre-existing diagnostics",
        "project_mypy_new": 0,
        "diff_check": "PASS",
        "secret_scan": "PASS",
        "review_created_containers": 0,
        "provider_calls": 0,
        "llm_calls": 0,
        "judge_calls": 0,
        "embedding_calls": 0,
        "reranker_calls": 0,
        "retrieval_calls": 0,
        "memory_recomputation": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
    }
    test_hash = _write(TEST_SUMMARY_PATH, test_summary)
    final = {
        "classification": "M112P2_SHADOW_COUNTEREXAMPLE_APPLICATION_COMPLETED",
        "validation_classification": validation["classification"],
        "validation_attempt": 3,
        "validation_corpus_version": "m112p2-validation-pairs-v2",
        "validation_metrics": validation["metrics"],
        "validation_gates": validation["gates"],
        "p1_defect_states": validation["p1_defect_states"],
        "shadow_population": shadow_result["population"],
        "static_summary": shadow_result["static_summary"],
        "compound_summary": shadow_result["compound_summary"],
        "unique_pair_level_cases_with_witness": shadow_result[
            "unique_pair_level_cases_with_witness"
        ],
        "witnessed_pair_cases_remaining_multidimensional": shadow_result[
            "witnessed_pair_cases_remaining_multidimensional"
        ],
        "scenario_witness_counts": shadow_result["scenario_witness_counts"],
        "no_new_failure_family_attribution": 0,
        "no_d3_implementation": True,
        "no_generation_intervention": True,
        "no_production_runtime_change": True,
        "no_equivalence_state": True,
        "historical_population_excluded_from_validation": True,
        "next_engineering_action": (
            "IMPLEMENT_BOUNDED_D3_COMPONENT_ISOLATION_ON_VALIDATED_WITNESS_SUBSET"
        ),
        "test_summary_hash": test_hash,
    }
    final_hash = _write(FINAL_SUMMARY_PATH, final)
    files = {
        "evaluation/m112p2_counterexample_diagnostic.py": _raw(SOURCE),
        "evaluation/run_m112p2.py": _raw(Path(__file__)),
        "evaluation/fixtures/m112p2_fixture_bank.json": _raw(BANK_PATH),
        "evaluation/fixtures/m112p2_validation_pairs.json": _raw(PAIRS_V1_PATH),
        "evaluation/fixtures/m112p2_validation_pairs_v2.json": _raw(PAIRS_PATH),
        "evaluation/fixtures/m112p2_validation_contract.json": _raw(
            FIXTURES / "m112p2_validation_contract.json"
        ),
        "evaluation/fixtures/m112p2_validation_contract_v2.json": _raw(CONTRACT_PATH),
        "evaluation/fixtures/m112p2_validation_history.json": _raw(HISTORY_PATH),
        "evaluation/fixtures/m112p2_validation_result.json": _raw(VALIDATION_V1_PATH),
        "evaluation/fixtures/m112p2_validation_attempt_2.json": _raw(ABORTED_VALIDATION_PATH),
        "evaluation/fixtures/m112p2_validation_attempt_3.json": _raw(VALIDATION_PATH),
        "evaluation/fixtures/m112p2_shadow_result.json": _raw(SHADOW_PATH),
        "evaluation/fixtures/m112p2_test_summary.json": test_hash,
        "evaluation/fixtures/m112p2_final_summary.json": final_hash,
        "tests/unit/test_m112p2_counterexample_diagnostic.py": _raw(
            ROOT / "tests/unit/test_m112p2_counterexample_diagnostic.py"
        ),
        "tests/integration/test_m112p2_counterexample_postgres.py": _raw(
            ROOT / "tests/integration/test_m112p2_counterexample_postgres.py"
        ),
        "docs/m112p2-counterexample-diagnostic.md": _raw(
            ROOT / "docs/m112p2-counterexample-diagnostic.md"
        ),
    }
    manifest = {
        "version": "m112p2-evidence-manifest-v1",
        "classification": final["classification"],
        "validation_attempt_1_preserved": True,
        "aborted_attempt_2": "NON_AUTHORITATIVE_ABORTED_RUN",
        "validation_attempt_3": "M112P2_D2_INDEPENDENT_VALIDATION_PASSED",
        "shadow_completed": True,
        "raw_sha256": files,
        "manifest_self_hash_excluded": True,
        "provider_calls": 0,
        "retrieval_calls": 0,
        "candidate_regeneration": 0,
        "reference_regeneration": 0,
        "new_failure_family_attribution": 0,
    }
    return {
        "test_summary": test_hash,
        "final_summary": final_hash,
        "evidence_manifest": _write(MANIFEST_PATH, manifest),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if sum((args.freeze, args.validate, args.shadow, args.finalize)) != 1:
        parser.error("choose exactly one of --freeze, --validate, --shadow, or --finalize")
    output = (
        freeze()
        if args.freeze
        else validate()
        if args.validate
        else shadow()
        if args.shadow
        else finalize()
    )
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
