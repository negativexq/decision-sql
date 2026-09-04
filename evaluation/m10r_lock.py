# ruff: noqa: E501

"""M10R Phase G: freeze DB state and the master pre-provider protocol."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.session import build_reader_engine
from evaluation.m10_corpus import stable_hash
from evaluation.run_m10r import (
    COMPARATOR_HASH,
    RESULT_ROOT,
    STARTING_HEAD,
    V1_HASH,
    V2_HASH,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(name: str, value: Any) -> str:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    path = RESULT_ROOT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return _hash_file(path)


def _component_manifest() -> dict[str, Any]:
    paths = (
        "app/sql/parser.py",
        "app/sql/policy.py",
        "app/sql/service.py",
        "app/execution/cost.py",
        "app/execution/reader.py",
        "app/semantics/catalog.py",
        "app/semantics/compiler.py",
        "app/semantics/routing.py",
        "app/memory/runtime.py",
        "app/memory/retrieval.py",
        "app/text_to_sql/service.py",
        "app/generation/provider.py",
        "app/retrieval/context.py",
        "evaluation/metrics.py",
        "evaluation/result_equivalence_contract.py",
        "evaluation/versioned_result_evaluator.py",
        "evaluation/result_binding_protocol_v2.py",
        "evaluation/m95_binding_protocol.py",
        "evaluation/m4_benchmark.py",
        "alembic/versions/0001_commerce_schema.py",
    )
    return {path: _hash_file(ROOT / path) for path in paths}


def _db_state() -> dict[str, Any]:
    settings = get_settings()
    engine = build_reader_engine(settings)
    tables = (
        "regions",
        "sales_representatives",
        "customers",
        "products",
        "orders",
        "order_items",
        "payments",
        "refunds",
    )
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            counts = {
                table: int(connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one())
                for table in tables
            }
            version = connection.exec_driver_sql("SELECT version(), current_database(), current_user, current_schema(), current_setting('transaction_read_only')").one()
    state = {
        "postgres_version": version[0],
        "database_identity": version[1],
        "reader_identity": version[2],
        "schema": version[3],
        "transaction_read_only": version[4],
        "migration_head": "0001_commerce_schema",
        "table_row_counts": counts,
        "m1_timeout_ms": settings.statement_timeout_ms,
        "m1_max_rows": settings.max_result_rows,
        "m1_max_plan_rows": settings.max_plan_rows,
        "m1_max_plan_cost": settings.max_plan_cost,
        "setup_db_mutations": 0,
        "benchmark_db_mutations": 0,
    }
    state["db_state_fingerprint"] = stable_hash(state)
    return state


def run() -> dict[str, Any]:
    reference_validation = json.loads((RESULT_ROOT / "reference_validation.json").read_text())
    if reference_validation["parse"] != "200/200" or reference_validation["m1_acceptance"] != "200/200" or reference_validation["execution"] != "200/200":
        raise RuntimeError("reference gate is not complete")
    component_manifest = _component_manifest()
    component_hash = stable_hash(component_manifest)
    db_state = _db_state()
    db_state_hash = _write("db_state.json", db_state)
    protocol = {
        "protocol_id": "m10r-clean-rebaseline-v2",
        "configured_provider": "openai-compatible",
        "configured_model": "gpt-5.6-luna",
        "temperature": None,
        "top_p": None,
        "reasoning_effort": None,
        "max_output_tokens": "provider_default_current_architecture",
        "request_timeout_seconds": get_settings().llm_timeout_seconds,
        "transport_retry_policy": {"initial_attempts": 1, "transport_retries": 2, "same_payload_only": True, "retryable": ["timeout", "connection_reset", "429", "5xx"]},
        "provider_call_budget": {"architecture_attempts": 200, "repair_calls": 0, "judge_calls": 0, "sampling_calls": 0, "post_hoc_generation_calls": 0},
        "architecture_arm": "CURRENT_COMBINED_DECISIONSQL",
        "primary_evaluator": "decision-result-evaluator-v1",
        "shadow_evaluator": "decision-result-evaluator-v2",
        "best_of": False,
        "reference_leakage": False,
        "expected_route_leakage": False,
        "case_removal": False,
        "pass_at_k": False,
        "prompt_tuning": False,
    }
    provider_protocol_hash = _write("provider_protocol.json", protocol)
    component_payload = {"starting_head": STARTING_HEAD, "manifest": component_manifest, "manifest_hash": component_hash, "binder_v1": V1_HASH, "binder_v2": V2_HASH, "comparator": COMPARATOR_HASH}
    component_file_hash = _write("component_manifest.json", component_payload)
    cases = json.loads((FIXTURES / "m10r_cases_v2.json").read_text())["cases"]
    corpus_hashes = {
        "transport_contract_hash": _hash_file(FIXTURES / "m10r_transport_contract.json"),
        "reconstruction_recipe_hash": _hash_file(FIXTURES / "m10r_construction_recipe_v2.json"),
        "mapping_hash": _hash_file(FIXTURES / "m10r_v1_v2_case_mapping.json"),
        "corpus_content_hash": stable_hash(json.loads((FIXTURES / "m10r_cases_v2.json").read_text())),
        "case_id_hash": stable_hash([case["case_id"] for case in cases]),
        "question_hash": stable_hash([case["question"] for case in cases]),
        "reference_hash": stable_hash([case["reference_sql_variants"] for case in cases]),
        "contract_hash": stable_hash([{ "case_id": case["case_id"], "contract": case["contract"] } for case in cases]),
    }
    execution_manifest = _load_fixture("m10r_execution_manifest_v2.json")
    residual_taxonomy = {"version": "m10r-residual-taxonomy-v1", "statuses": ["CORRECT", "RESULT_MISMATCH", "GENERATION_FAILURE", "M1_REJECTED", "EXECUTION_FAILURE", "EVALUATOR_PROTOCOL_FAILURE", "PROVIDER_FAILURE", "REFERENCE_PROTOCOL_FAILURE"], "mechanisms": ["SCHEMA_OR_COLUMN_GROUNDING", "FILTER_OR_VALUE_CONDITION", "JOIN_OR_RELATIONAL_COMPOSITION", "AGGREGATION_OR_GROUPING", "FORMULA_OR_METRIC_DEFINITION", "TEMPORAL_LOGIC", "WINDOW_LOGIC", "ORDER_OR_LIMIT", "PROJECTION_OR_RESULT_SHAPE", "DISTINCT_OR_SET_SEMANTICS", "M1_INCOMPATIBLE_GENERATED_STRUCTURE", "M1_TRANSPORT_INCOMPATIBLE_GENERATED_SQL", "MEMORY_OVERTRANSFER", "ROUTING", "OTHER_SEMANTIC", "UNCLASSIFIED"]}
    master = {
        "protocol_id": "m10r-clean-rebaseline-v2",
        "starting_checkpoint": STARTING_HEAD,
        "component_manifest_hash": component_file_hash,
        "binder_v1_hash": V1_HASH,
        "binder_v2_hash": V2_HASH,
        "comparator_hash": COMPARATOR_HASH,
        "transport_audit_hash": _hash_file(FIXTURES / "m10r_transport_root_cause.json"),
        "transport_contract_hash": _hash_file(FIXTURES / "m10r_transport_contract.json"),
        "reconstruction_recipe_hash": _hash_file(FIXTURES / "m10r_construction_recipe_v2.json"),
        "mapping_hash": _hash_file(FIXTURES / "m10r_v1_v2_case_mapping.json"),
        "corpus_hashes": corpus_hashes,
        "freshness_hash": _hash_file(FIXTURES / "m10r_freshness_manifest_v2.json"),
        "pre_db_freeze_lock_hash": _hash_file(FIXTURES / "m10r_pre_db_freeze_lock_v2.json"),
        "db_state_hash": db_state_hash,
        "reference_results_hash": reference_validation["reference_results_hash"],
        "provider_protocol_hash": provider_protocol_hash,
        "execution_order_hash": execution_manifest["order_hash"],
        "primary_evaluator": "decision-result-evaluator-v1",
        "shadow_evaluator": "decision-result-evaluator-v2",
        "residual_taxonomy": residual_taxonomy,
        "success_criteria": {"cases": 200, "primary_denominator": 200, "v1_protocol_failures": 0, "db_mutations_after_state_freeze": 0, "best_of": False, "case_removal": False},
        "provider_calls_before_lock": 0,
        "cases_consumed_before_lock": 0,
        "frozen_before_provider_call_one": True,
    }
    master_hash = stable_hash(master)
    master["master_protocol_hash"] = master_hash
    _write("master_protocol.json", master)
    return {"component_manifest_hash": component_hash, "component_manifest_file_hash": component_file_hash, "db_state_hash": db_state_hash, "provider_protocol_hash": provider_protocol_hash, "execution_order_hash": execution_manifest["order_hash"], "master_protocol_hash": master_hash, "cases_consumed": 0, "provider_calls": 0}


def _load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
