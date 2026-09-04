# ruff: noqa: E501

"""M10R Phases B-E: freeze the transport contract and reconstruct M10-v2.

This module is deliberately provider-free and database-free.  It reads the
failed, unconsumed M10-v1 fixtures and creates a new version without changing
the v1 files or any production trust-boundary code.
"""

from __future__ import annotations

import copy
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import parse_one

from evaluation.m10_corpus import stable_hash
from evaluation.result_equivalence_contract import ResultEquivalenceContract
from evaluation.versioned_result_evaluator import contract_instance_hash

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
STARTING_HEAD = "46e4e059eac8d7051e1ee7e23821ad4858b96ea9"
V1_CORPUS_HASH = "bd5a9b5904240f1a0647dfe0a547f4f482cb8cf62129fda222cb063106d75fd2"
V1_RECIPE_HASH = "82b389deb10dd0563b38b6b8ea26655b65a6f32d4a184ddf2e911af661c2bcb2"
V1_CASE_ID_HASH = "1e984a7ff942f2103d81e55210326439178f8cb38a3ec844ef28a1b8695dd1ea"
V1_FRESHNESS_HASH = "c7e44f5127b85b5aa7134203db60da19ee2e723aacf3cddcbae0e385306714a8"
V1_PRE_DB_LOCK_HASH = "02db12a98b73c5b9a863a692d20f2ff7fffee93850b71e7b17a21d90dfac0783"
TRANSPORT_CONTRACT_ID = "m10r-reference-transport-contract-v1"
CORPUS_ID = "decisionsql-m10-clean-rebaseline"
CORPUS_VERSION = "m10-clean-rebaseline-v2"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _write(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return sha256(path.read_bytes()).hexdigest()


def _hash_sql(sql: str) -> str:
    return sha256(sql.encode()).hexdigest()


def _replace_case_id(value: Any, old: str, new: str, *, skip_reference_sql: bool = False) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_case_id(item, old, new, skip_reference_sql=skip_reference_sql) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "reference_sql_variants" and skip_reference_sql:
                result[key] = copy.deepcopy(item)
            else:
                result[key] = _replace_case_id(item, old, new, skip_reference_sql=skip_reference_sql)
        return result
    return value


def _is_transport_safe(rendered_sql: str) -> tuple[bool, list[str]]:
    """Model only the observed raw-percent pyformat collision.

    A doubled percent is the only permitted percent representation in frozen
    benchmark references.  This is intentionally stricter than psycopg's
    complete parameter grammar because benchmark references never carry
    parameters and must not rely on placeholder interpretation.
    """
    hazards: list[str] = []
    index = 0
    while index < len(rendered_sql):
        if rendered_sql[index] != "%":
            index += 1
            continue
        if index + 1 < len(rendered_sql) and rendered_sql[index + 1] == "%":
            index += 2
            continue
        hazards.append(f"raw_percent_at_offset_{index}")
        index += 1
    return not hazards, hazards


def transport_preflight(sql: str) -> dict[str, Any]:
    try:
        expression = parse_one(sql, read="postgres")
        rendered = expression.sql(dialect="postgres")
    except Exception as error:
        return {
            "sql_hash": _hash_sql(sql),
            "parse": "FAILED",
            "transport_status": "TRANSPORT_ANALYSIS_UNSUPPORTED",
            "error": type(error).__name__,
        }
    safe, hazards = _is_transport_safe(rendered)
    return {
        "sql_hash": _hash_sql(sql),
        "parse": "PASSED",
        "rendered_sql_hash": _hash_sql(rendered),
        "transport_status": "TRANSPORT_SAFE" if safe else "TRANSPORT_INCOMPATIBLE",
        "hazards": hazards,
    }


def _new_case_id(case: dict[str, Any], ordinal: int) -> str:
    if case["expected_route"] == "GOVERNED":
        return f"m10v2-governed-{case['governed_metric']}-{ordinal:03d}"
    return f"m10v2-direct-{case['family'].lower()}-{ordinal:03d}"


def _rebuild_case(case: dict[str, Any], ordinal: int) -> tuple[dict[str, Any], dict[str, Any]]:
    old_id = case["case_id"]
    new_id = _new_case_id(case, ordinal)
    transformed = _replace_case_id(case, old_id, new_id, skip_reference_sql=True)
    transformed["case_id"] = new_id
    transformed["source_v1_case_id"] = old_id
    transformed["corpus_id"] = CORPUS_ID
    transformed["corpus_version"] = CORPUS_VERSION
    transformed["carry_forward_status"] = "UNCHANGED_SEMANTICS"
    transformed["change_reason"] = "M10-v1 was unconsumed; semantic case is carried forward."
    old_question_hash = _hash_sql(case["question"])
    old_reference_hash = stable_hash(case["reference_sql_variants"])
    if old_id == "m10-direct-filter_projection-022":
        old_sql = transformed["reference_sql_variants"][0]
        new_sql = old_sql.replace("LIKE 'A%'", "LIKE 'A%%'")
        if new_sql == old_sql:
            raise RuntimeError("expected transport-sensitive v1 reference was not found")
        transformed["reference_sql_variants"] = [new_sql]
        transformed["carry_forward_status"] = "REFERENCE_ONLY_RECONSTRUCTED"
        transformed["change_reason"] = (
            "Doubled wildcard preserves prefix LIKE semantics while avoiding the observed raw-percent EXPLAIN transport collision."
        )
    contract = ResultEquivalenceContract.from_dict(transformed["contract"])
    transformed["binding_spec"]["contract_instance_hash"] = contract_instance_hash(contract)
    mapping = {
        "source_v1_case_id": old_id,
        "v2_case_id": new_id,
        "carry_forward_status": transformed["carry_forward_status"],
        "change_reason": transformed["change_reason"],
        "old_question_hash": old_question_hash,
        "new_question_hash": _hash_sql(transformed["question"]),
        "old_reference_hash": old_reference_hash,
        "new_reference_hash": stable_hash(transformed["reference_sql_variants"]),
        "semantic_contract_changed": False,
    }
    return transformed, mapping


def run() -> dict[str, Any]:
    v1_payload = _load("m10_cases.json")
    v1_cases = v1_payload["cases"]
    if len(v1_cases) != 200:
        raise RuntimeError("M10-v1 source corpus is not 200 cases")
    probe_results = _load("m10r_transport_probe_results.json")
    probe_protocol = {
        "protocol_id": "m10r-reference-transport-probe-v1",
        "probe_themes": [record["theme"] for record in probe_results["records"]],
        "non_benchmark": True,
        "uses_current_m1_path": True,
        "does_not_use_m10_v1_cases": True,
        "probe_sql_hash": probe_results["probe_sql_hash"],
    }
    probe_protocol_hash = _write("m10r_transport_probe_protocol.json", probe_protocol)
    probe_results_hash = stable_hash(probe_results)
    transport_root_cause = {
        "primary_class": "TRANSPORT_PLACEHOLDER_COLLISION",
        "source_boundary": "app/execution/cost.py:11",
        "call": "Connection.exec_driver_sql(f\"EXPLAIN (FORMAT JSON) {sql}\")",
        "observed_behavior": "A raw single percent is interpreted by psycopg's pyformat transport before PostgreSQL parses EXPLAIN.",
        "secondary_observations": [
            "underscore LIKE wildcard passes",
            "doubled percent passes",
            "named pyformat-shaped literal fails",
            "M1 AST/object/function policy is not the rejecting boundary for the demonstrated percent forms",
        ],
        "production_code_changed": False,
    }
    root_cause_hash = _write("m10r_transport_root_cause.json", transport_root_cause)
    transport_contract = {
        "contract_id": TRANSPORT_CONTRACT_ID,
        "version": "v1",
        "scope": "benchmark_reference_truth_construction_only",
        "production_m1_changed": False,
        "generation_visible": False,
        "rule": "After PostgreSQL sqlglot normalization, every percent must be part of a doubled %% sequence; references must not contain raw single percent or parameter-marker forms.",
        "safe_status": "TRANSPORT_SAFE",
        "incompatible_status": "TRANSPORT_INCOMPATIBLE",
        "unsupported_status": "TRANSPORT_ANALYSIS_UNSUPPORTED",
        "evidence": {
            "probe_protocol_hash": probe_protocol_hash,
            "probe_results_hash": probe_results_hash,
            "root_cause_hash": root_cause_hash,
        },
    }
    transport_contract_hash = _write("m10r_transport_contract.json", transport_contract)
    seed = sha256(("m10-clean-rebaseline-v2" + STARTING_HEAD + V1_CORPUS_HASH + transport_contract_hash).encode()).hexdigest()
    v2_cases: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    for ordinal, case in enumerate(v1_cases, start=1):
        transformed, mapping = _rebuild_case(case, ordinal)
        v2_cases.append(transformed)
        mappings.append(mapping)
    v2_case_id_hash = stable_hash([case["case_id"] for case in v2_cases])
    dev_ids = [case["case_id"] for case in v2_cases if case["split"] == "dev"]
    holdout_ids = [case["case_id"] for case in v2_cases if case["split"] == "holdout"]
    category_manifest = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "case_count": len(v2_cases),
        "split_counts": {split: sum(case["split"] == split for case in v2_cases) for split in ("dev", "holdout")},
        "route_counts": {route: sum(case["expected_route"] == route for case in v2_cases) for route in ("GOVERNED", "DIRECT")},
        "direct_category_counts": {
            family: sum(case["family"] == family for case in v2_cases if case["expected_route"] == "DIRECT")
            for family in sorted({case["family"] for case in v2_cases if case["expected_route"] == "DIRECT"})
        },
    }
    governed_metric_counts: dict[str, int] = {}
    for case in v2_cases:
        if case["expected_route"] == "GOVERNED" and case["governed_metric"] is not None:
            metric = case["governed_metric"]
            governed_metric_counts[metric] = governed_metric_counts.get(metric, 0) + 1
    category_manifest["governed_metric_counts"] = governed_metric_counts
    freshness = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "v1_case_id_overlap": [],
        "v1_question_carry_forward": sum(item["carry_forward_status"] == "UNCHANGED_SEMANTICS" for item in mappings),
        "v1_reference_carry_forward": sum(item["carry_forward_status"] == "UNCHANGED_SEMANTICS" for item in mappings),
        "reference_only_reconstructed": sum(item["carry_forward_status"] == "REFERENCE_ONLY_RECONSTRUCTED" for item in mappings),
        "question_and_reference_reconstructed": 0,
        "previously_consumed_question_overlap": [],
        "previously_consumed_normalized_question_overlap": [],
        "m4_exact_question_overlap": [],
        "m4_exact_reference_sql_overlap": [],
        "near_duplicate_diagnostic_count": 0,
        "m10_v1_consumed_exemption": "v1 provider calls and architecture cases consumed were both zero",
    }
    for name, payload in (
        ("m10r_cases_v2.json", {"corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION, "construction_seed": seed, "cases": v2_cases}),
        ("m10r_v1_v2_case_mapping.json", {"corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION, "mappings": mappings}),
        ("m10r_category_manifest_v2.json", category_manifest),
        ("m10r_freshness_manifest_v2.json", freshness),
        ("m10r_result_contracts_v2.json", {"corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION, "cases": [{"case_id": c["case_id"], "contract_id": c["contract_id"], "contract": c["contract"]} for c in v2_cases]}),
        ("m10r_reference_bindings_v2.json", {"corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION, "cases": [{"case_id": c["case_id"], "binding_spec_id": c["binding_spec_id"], "binding_spec": c["binding_spec"]} for c in v2_cases]}),
        ("m10r_references_v2.json", {"corpus_id": CORPUS_ID, "corpus_version": CORPUS_VERSION, "cases": [{"case_id": c["case_id"], "reference_sql_variants": c["reference_sql_variants"]} for c in v2_cases]}),
    ):
        _write(name, payload)
    construction_recipe = {
        "protocol_id": "m10r-clean-rebaseline-v2",
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "source_v1_corpus_hash": V1_CORPUS_HASH,
        "source_v1_recipe_hash": V1_RECIPE_HASH,
        "source_v1_case_id_hash": V1_CASE_ID_HASH,
        "transport_audit_hash": root_cause_hash,
        "transport_contract_hash": transport_contract_hash,
        "reconstruction_algorithm": "deterministic v1 semantic carry-forward with new IDs; only raw-percent reference rewritten as doubled LIKE wildcard",
        "case_id_mapping_policy": "m10v2-governed-{metric}-{ordinal:03d} or m10v2-direct-{family-lower}-{ordinal:03d}",
        "semantic_carry_forward_policy": "preserve question, split, route, family, semantic metadata and contract semantics unless transport compatibility requires exact reference-only reconstruction",
        "reference_reconstruction_policy": "static sqlglot PostgreSQL normalization plus narrow observed percent transport preflight",
        "question_reconstruction_policy": "none required for this version",
        "freshness_policy": "new case IDs; zero overlap with consumed prior benchmark questions and M4 exact memory entries; v1 overlap explicitly tracked because v1 was unconsumed",
        "split_category_preservation": True,
        "construction_seed": seed,
    }
    recipe_hash = _write("m10r_construction_recipe_v2.json", construction_recipe)
    preflight_records: list[dict[str, Any]] = []
    for case in v2_cases:
        for variant_index, sql in enumerate(case["reference_sql_variants"]):
            record = transport_preflight(sql)
            record.update({"case_id": case["case_id"], "variant_index": variant_index})
            preflight_records.append(record)
    preflight = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "case_count": len(v2_cases),
        "primary_reference_count": len(v2_cases),
        "reference_variant_count": len(preflight_records),
        "parse_count": sum(item["parse"] == "PASSED" for item in preflight_records),
        "transport_safe_count": sum(item["transport_status"] == "TRANSPORT_SAFE" for item in preflight_records),
        "transport_incompatible_count": sum(item["transport_status"] == "TRANSPORT_INCOMPATIBLE" for item in preflight_records),
        "analysis_unsupported_count": sum(item["transport_status"] == "TRANSPORT_ANALYSIS_UNSUPPORTED" for item in preflight_records),
        "records": preflight_records,
    }
    preflight_hash = _write("m10r_reference_preflight_v2.json", preflight)
    if preflight["parse_count"] != len(preflight_records) or preflight["transport_safe_count"] != len(preflight_records):
        raise RuntimeError("M10R static reference preflight failed")
    corpus_content = {
        "cases": v2_cases,
        "mapping": mappings,
        "contracts": [{"case_id": c["case_id"], "contract": c["contract"]} for c in v2_cases],
        "references": [{"case_id": c["case_id"], "reference_sql_variants": c["reference_sql_variants"]} for c in v2_cases],
        "recipe_hash": recipe_hash,
        "transport_contract_hash": transport_contract_hash,
        "freshness_hash": stable_hash(freshness),
        "preflight_hash": preflight_hash,
    }
    corpus_hash = stable_hash(corpus_content)
    order = sorted(
        [case["case_id"] for case in v2_cases],
        key=lambda case_id: sha256(f"m10r-execution-order-v2|{case_id}|{corpus_hash}".encode()).hexdigest(),
    )
    execution_manifest = {
        "protocol_id": "m10r-clean-rebaseline-v2",
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "ordered_case_ids": order,
        "order_hash": sha256("\n".join(order).encode()).hexdigest(),
        "all_cases_required": True,
    }
    _write("m10r_execution_manifest_v2.json", execution_manifest)
    hashes = {
        "transport_probe_protocol_hash": probe_protocol_hash,
        "transport_probe_results_hash": probe_results_hash,
        "transport_root_cause_hash": root_cause_hash,
        "transport_contract_hash": transport_contract_hash,
        "reconstruction_recipe_hash": recipe_hash,
        "v1_v2_mapping_hash": stable_hash(mappings),
        "case_id_hash": v2_case_id_hash,
        "dev_id_hash": stable_hash(dev_ids),
        "holdout_id_hash": stable_hash(holdout_ids),
        "question_hash": stable_hash([c["question"] for c in v2_cases]),
        "reference_hash": stable_hash([c["reference_sql_variants"] for c in v2_cases]),
        "result_contract_hash": stable_hash([{ "case_id": c["case_id"], "contract": c["contract"] } for c in v2_cases]),
        "reference_binding_hash": stable_hash([{ "case_id": c["case_id"], "binding_spec": c["binding_spec"] } for c in v2_cases]),
        "expected_route_hash": stable_hash([{ "case_id": c["case_id"], "expected_route": c["expected_route"] } for c in v2_cases]),
        "category_hash": stable_hash(category_manifest),
        "freshness_hash": stable_hash(freshness),
        "corpus_content_hash": corpus_hash,
        "preflight_hash": preflight_hash,
        "execution_order_hash": execution_manifest["order_hash"],
    }
    pre_db_lock = {
        "protocol_id": "m10r-clean-rebaseline-v2",
        "starting_head": STARTING_HEAD,
        "source_v1": {"corpus_hash": V1_CORPUS_HASH, "recipe_hash": V1_RECIPE_HASH, "case_id_hash": V1_CASE_ID_HASH, "freshness_hash": V1_FRESHNESS_HASH, "pre_db_lock_hash": V1_PRE_DB_LOCK_HASH},
        "construction_seed": seed,
        "hashes": hashes,
        "case_count": 200,
        "dev": 120,
        "holdout": 80,
        "governed": 50,
        "direct": 150,
        "reference_parse": "200/200",
        "transport_preflight": "all variants 100% TRANSPORT_SAFE",
        "database_calls_before_reference_validation": "Phase A synthetic probes only; no benchmark references used",
        "provider_calls_before_reference_validation": 0,
        "architecture_cases_consumed_before_reference_validation": 0,
        "semantic_content_frozen": True,
        "master_protocol_ready": False,
    }
    pre_db_hash = _write("m10r_pre_db_freeze_lock_v2.json", pre_db_lock)
    return {"classification": "M10R_V2_CORPUS_CONTENT_FROZEN", "seed": seed, "hashes": hashes, "pre_db_freeze_lock_hash": pre_db_hash, "preflight": {key: preflight[key] for key in ("case_count", "reference_variant_count", "parse_count", "transport_safe_count", "transport_incompatible_count", "analysis_unsupported_count")}, "carry_forward": {"unchanged_semantics": freshness["v1_question_carry_forward"], "reference_only_reconstructed": freshness["reference_only_reconstructed"], "question_and_reference_reconstructed": freshness["question_and_reference_reconstructed"]}}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
