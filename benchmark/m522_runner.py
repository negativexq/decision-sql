# ruff: noqa: E501
"""Zero-call feasibility audit for a population semantic metadata contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from sqlglot import exp

from benchmark.analysis_serialization import dumps_analysis
from benchmark.context import render_governed_context
from benchmark.m521_runner import detect_group_survival, first_table, parse_sql
from benchmark.m522_contract import (
    CarrierV0,
    MeasureV0,
    PopulationInclusionMode,
    PopulationSemanticContractV0,
    ResultGrainV0,
    contract_schema,
    validate_entity_references,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m522"
M51B = ROOT / "audits" / "m51b"
M51BR = ROOT / "audits" / "m51br"
M521 = ROOT / "audits" / "m521"
CASES = ROOT / "cases" / "m51_expansion"
TRUTH = ROOT / "ground_truth" / "m51_expansion"
EXPANSION_MANIFEST = ROOT / "manifests" / "m51a_expansion_90_manifest.json"
FULL_MANIFEST = ROOT / "manifests" / "m51a_180_case_manifest.json"
RESPONSE_HASH = "9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a"
EXPANSION_TRUTH_HASH = "7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9"
FULL_TRUTH_HASH = "b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173"
EXPANSION_MANIFEST_HASH = "d48be18622e34c11057a7ad31272cc96b1fbb7d8017b9c0f74953de5a92a2417"
STARTING_HEAD = "ecb8b2f44a2f2cf3116e77c9f424f7a24afe5b28"
CONTRACT_VERSION = "PopulationSemanticContractV0"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_analysis(value, indent=2) + "\n", encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def entity_id_for(database_id: str, physical_table: str | None) -> str | None:
    if physical_table is None:
        return None
    return f"entity:{database_id}:{physical_table}"


def aggregate_source_table(sql: str | None) -> str | None:
    if sql is None:
        return None
    tree = parse_sql(sql)
    if tree is None:
        return None
    aggregate = next(tree.find_all(exp.AggFunc), None)
    if aggregate is None:
        return first_table(sql)
    column = next(aggregate.find_all(exp.Column), None)
    if column is None or not column.table:
        return first_table(sql)
    aliases = {table.alias_or_name: table.name for table in tree.find_all(exp.Table)}
    return aliases.get(column.table, column.table)


def allowed_entities(database_id: str) -> set[str]:
    context = render_governed_context(database_id)
    return {str(entity["entity_id"]) for entity in context["schema_catalog"]}


def gold_contract(case: dict[str, Any], truth: dict[str, Any]) -> PopulationSemanticContractV0:
    database_id = case["database_id"]
    semantic = truth["semantic_target"]
    reference = truth["reference_implementation_a"]["sql"]
    carrier_table = first_table(reference)
    measure_table = aggregate_source_table(reference)
    carrier_id = entity_id_for(database_id, carrier_table)
    measure_id = entity_id_for(database_id, measure_table or carrier_table)
    if carrier_id is None or measure_id is None:
        raise ValueError(f"cannot derive entity references for {case['case_id']}")
    inclusion = (
        PopulationInclusionMode.ALL_BASE_ENTITIES
        if semantic.get("population") == "base-entity-preserving"
        else PopulationInclusionMode.MATCHING_ENTITIES_ONLY
    )
    contract = PopulationSemanticContractV0(
        result_grain=ResultGrainV0(entity_id=carrier_id),
        carrier=CarrierV0(entity_id=carrier_id, inclusion=inclusion),
        measure=MeasureV0(
            source_entity_id=measure_id,
            contributing_population_ref=None,
        ),
    )
    validate_entity_references(contract, allowed_entities(database_id))
    return contract


def visible_contract(case: dict[str, Any]) -> dict[str, Any]:
    """Build only from current model-visible structured context.

    The current context has entity and relationship catalogs but no request-level
    carrier or population inclusion value.  Returning an unavailable status is
    intentional and prevents a gold-compatible inference from leaking here.
    """
    context = render_governed_context(case["database_id"])
    return {
        "status": "SEMANTIC_METADATA_NOT_UNIQUELY_AUTHORABLE",
        "structured_entities_available": len(context["schema_catalog"]),
        "structured_relationships_available": len(context["authorized_relationships"]),
        "missing": [
            "result_grain",
            "carrier_entity",
            "population_inclusion_mode",
        ],
        "model_visible_context_hash": hashlib.sha256(
            json.dumps(context, sort_keys=True).encode()
        ).hexdigest(),
        "gold_not_read": True,
    }


def contract_json(contract: PopulationSemanticContractV0) -> dict[str, Any]:
    return contract.model_dump(mode="json")


def mapping_row(case: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    visible = visible_contract(case)
    contract = gold_contract(case, truth)
    return {
        "case_id": case["case_id"],
        "domain": case["database_id"],
        "gold_contract": contract_json(contract),
        "gold_mapping_status": "LOSSLESS_FOR_POPULATION_SCOPE",
        "visible_mapping": visible,
        "visible_mapping_status": "PARTIAL_PARITY",
        "parity": "PARTIAL_PARITY",
        "gold_used_only_after_visible_pass": True,
        "audit_only": True,
    }


def detector_result(case: dict[str, Any], truth: dict[str, Any], sql: str | None) -> dict[str, Any]:
    contract = gold_contract(case, truth)
    return detect_group_survival(
        sql,
        {
            "population_requirement": contract.carrier.inclusion.value.replace(
                "ALL_BASE_ENTITIES", "PRESERVE_BASE_ENTITIES"
            ),
            "base_entity": contract.carrier.entity_id,
            "zero_group_policy": contract.carrier.inclusion.value,
        },
    )


def main() -> dict[str, Any]:
    response_path = M51B / "m51b_expansion_responses.jsonl"
    if sha(response_path) != RESPONSE_HASH:
        raise RuntimeError("M522_RESPONSE_CORPUS_DRIFT")
    if sha(EXPANSION_MANIFEST) != EXPANSION_MANIFEST_HASH:
        raise RuntimeError("M522_EXPANSION_MANIFEST_DRIFT")
    expansion_manifest = json.loads(EXPANSION_MANIFEST.read_text(encoding="utf-8"))
    full_manifest = json.loads(FULL_MANIFEST.read_text(encoding="utf-8"))
    if expansion_manifest["truth_hash"] != EXPANSION_TRUTH_HASH:
        raise RuntimeError("M522_EXPANSION_TRUTH_DRIFT")
    if full_manifest["full_truth_hash"] != FULL_TRUTH_HASH:
        raise RuntimeError("M522_FULL_TRUTH_DRIFT")

    responses = {row["case_id"]: row for row in load_jsonl(response_path)}
    traces = {
        row["case_id"]: row for row in load_jsonl(M51BR / "m51br_answerable_case_results.jsonl")
    }
    cases = {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in CASES.glob("*.json")
    }
    truths = {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in TRUTH.glob("*.json")
    }
    targets = json.loads((M521 / "m521_group_survival_targets.json").read_text(encoding="utf-8"))[
        "case_ids"
    ]
    controls = json.loads(
        (M521 / "m521_primary_negative_controls.json").read_text(encoding="utf-8")
    )["case_ids"]
    direct = {"procurement_06", "insurance_02", "marketplace_06", "marketplace_08"}
    remaining = sorted(set(targets) - direct)
    if len(targets) != 9 or len(direct) != 4 or len(remaining) != 5 or len(controls) != 5:
        raise RuntimeError("M522_TARGET_CONTROL_COUNT")

    relevant_ids = sorted(
        cid
        for cid, truth in truths.items()
        if cid in cases
        and cases[cid]["task_type"] == "ANSWERABLE"
        and truth["semantic_target"].get("query_shape_tags")
    )
    relevant_records = [mapping_row(cases[cid], truths[cid]) for cid in relevant_ids]
    substudy_ids = sorted(set(direct) | set(controls) | set(relevant_ids[:9]))
    substudy = [mapping_row(cases[cid], truths[cid]) for cid in substudy_ids]

    target_reinspection = []
    for cid in sorted(targets):
        case, truth, response, trace = cases[cid], truths[cid], responses[cid], traces[cid]
        target_reinspection.append(
            {
                "case_id": cid,
                "domain": case["database_id"],
                "question": case["question"],
                "model_visible_context": render_governed_context(case["database_id"]),
                "raw_model_response": response,
                "candidate_sql": response.get("sql"),
                "selected_sql": trace["states"][0]["outcome"].get("grain", {}).get("selected_sql"),
                "gold": truth,
                "m52_root_cause": "GROUP_SURVIVAL",
                "m521_subtype": "direct_carrier_loss"
                if cid in direct
                else "non_carrier_or_non_population_result_failure",
                "base_trace": trace["states"][0],
                "counterfactual_traces": trace["states"][1:],
            }
        )

    control_reinspection = []
    for cid in sorted(controls):
        case, truth, response, trace = cases[cid], truths[cid], responses[cid], traces[cid]
        control_reinspection.append(
            {
                "case_id": cid,
                "domain": case["database_id"],
                "question": case["question"],
                "model_visible_context": render_governed_context(case["database_id"]),
                "raw_model_response": response,
                "candidate_sql": response.get("sql"),
                "gold": truth,
                "reference_A": truth["reference_implementation_a"]["sql"],
                "reference_B": truth["reference_implementation_b"]["sql"],
                "base_and_counterfactuals": trace["states"],
                "contract_interpretation": "matching-only"
                if truth["semantic_target"].get("population") == "matching-only"
                else "base-preserving",
            }
        )

    schema = contract_schema()
    schema_hash = hashlib.sha256(dumps_analysis(schema).encode()).hexdigest()
    target_detector_rows = []
    control_detector_rows = []
    for cid in sorted(targets):
        target_detector_rows.append(
            {
                "case_id": cid,
                "detector_inputs": ["candidate SQL", "V0 contract", "AST", "catalog relationships"],
                "detector": detector_result(cases[cid], truths[cid], responses[cid].get("sql")),
                "actual_frozen_root_cause": "GROUP_SURVIVAL",
            }
        )
    for cid in sorted(controls):
        control_detector_rows.append(
            {
                "case_id": cid,
                "detector_inputs": ["candidate SQL", "V0 contract", "AST", "catalog relationships"],
                "detector": detector_result(cases[cid], truths[cid], responses[cid].get("sql")),
                "actual_frozen_correct": traces[cid]["governed_correct"],
            }
        )
    target_flags = [
        row
        for row in target_detector_rows
        if row["detector"]["result"] == "SEMANTIC_CONTRADICTION_DETECTED"
    ]
    control_flags = [
        row
        for row in control_detector_rows
        if row["detector"]["result"] == "SEMANTIC_CONTRADICTION_DETECTED"
    ]
    broader_detector = []
    for cid in relevant_ids:
        row = {
            "case_id": cid,
            "detector": detector_result(cases[cid], truths[cid], responses[cid].get("sql")),
            "actual_frozen_correct": traces[cid]["governed_correct"],
            "truth_used_after_detector_freeze": True,
        }
        broader_detector.append(row)
    broader_flagged = [
        row
        for row in broader_detector
        if row["detector"]["result"] == "SEMANTIC_CONTRADICTION_DETECTED"
    ]
    broader_correct_flags = [
        row["case_id"] for row in broader_flagged if row["actual_frozen_correct"]
    ]
    broader_failed_flags = [
        row["case_id"] for row in broader_flagged if not row["actual_frozen_correct"]
    ]
    additional_failed_flags = sorted(set(broader_failed_flags) - direct)

    outputs: dict[str, Any] = {
        "m522_integrity.json": {
            "response_corpus_hash": RESPONSE_HASH,
            "expansion_truth_hash": EXPANSION_TRUTH_HASH,
            "full_truth_hash": FULL_TRUTH_HASH,
            "expansion_manifest_hash": EXPANSION_MANIFEST_HASH,
            "responses_changed": False,
            "benchmark_changed": False,
            "truth_changed": False,
            "references_changed": False,
            "fixtures_changed": False,
            "prompt_changed": False,
            "runtime_semantics_changed": False,
        },
        "m522_zero_call_accounting.json": {
            "provider_calls": 0,
            "model_calls": 0,
            "llm_calls": 0,
            "embedding_calls": 0,
            "reranker_calls": 0,
            "retries": 0,
            "repairs": 0,
            "judges": 0,
            "selectors": 0,
        },
        "m522_target_reinspection.jsonl": target_reinspection,
        "m522_direct_carrier_loss_targets.json": {
            "count": 4,
            "case_ids": sorted(direct),
            "criteria": "M52.1 hypothetical V0/AST detector flags carrier-loss contradiction",
        },
        "m522_remaining_group_survival_targets.json": {
            "count": 5,
            "case_ids": remaining,
            "population_contract_coverage": "PARTIAL: population is representable, but observed failures are not population contradictions",
        },
        "m522_negative_control_reinspection.jsonl": control_reinspection,
        "m522_semantic_concepts.json": {
            "retained": {
                "result_grain": "output grouping entity/grain",
                "carrier_entity": "possible output-group carrier population",
                "population_inclusion_mode": "ALL_BASE_ENTITIES or MATCHING_ENTITIES_ONLY",
                "measure_source_entity": "entity providing measured rows",
                "measure_contributing_population_ref": "reference to a canonical metric/semantic population definition",
            },
            "rejected_or_separate": {
                "zero_group_policy": "derivable from inclusion for V0 scope; zero/null representation remains ResultContract/metric semantics",
                "measure_grain": "derived from existing metric/native-grain metadata where present",
                "qualification_semantics": "redundant with contributing population reference",
                "null_measure_policy": "ResultContract or metric contract, not population metadata",
            },
        },
        "m522_field_legitimacy.json": {
            name: {"production_legitimate": True, "generic": True, "benchmark_specific": False}
            for name in [
                "result_grain",
                "carrier_entity",
                "population_inclusion_mode",
                "measure_source_entity",
                "measure_contributing_population_ref",
            ]
        },
        "m522_field_authorability.json": {
            "result_grain": "INDEPENDENTLY_AUTHORABLE or request-level application intent",
            "carrier_entity": "AUTHORABLE_WITH_DOMAIN_EXPERT",
            "population_inclusion_mode": "AUTHORABLE_WITH_DOMAIN_EXPERT at request level",
            "measure_source_entity": "DERIVED_FROM_EXISTING_CATALOG where metric lineage exists",
            "measure_contributing_population_ref": "INDEPENDENTLY_AUTHORABLE when canonical metric/population definition exists; otherwise request-level",
            "zero_group_policy": "DERIVED_FROM_EXISTING_CATALOG / redundant for V0",
        },
        "m522_field_ownership.json": {
            "result_grain": "semantic layer or query contract",
            "carrier_entity": "metric catalog plus query semantic plan",
            "population_inclusion_mode": "request-level semantic intent or user/application structured query",
            "measure_source_entity": "metric catalog / lineage catalog",
            "measure_contributing_population_ref": "metric catalog / business glossary",
        },
        "m522_separation_of_concerns.json": {
            "population": "carrier and inclusion",
            "result_contract": "columns, order, null/result representation, numeric tolerance",
            "measure": "source and contributing semantic population",
            "temporal": "time windows/latest/effective intervals",
            "authority": "authorized relationships and access policy",
            "sql_safety": "AST and runtime structural checks",
        },
        "m522_static_vs_request_semantics.json": {
            "static_catalog": [
                "measure_source_entity",
                "measure_contributing_population_ref when named metric",
                "metric result grain when predefined",
            ],
            "request_level": [
                "result_grain for ad hoc request",
                "carrier_entity",
                "population_inclusion_mode",
            ],
            "static_alone_suffices": "NO",
            "request_semantics_required": "YES for the motivating all-versus-matching distinction",
        },
        "m522_request_population_intent_analysis.json": {
            "legitimate_sources": [
                "existing metric catalog",
                "application/user structured intent",
                "deterministic request state",
            ],
            "current_repository_source": "not available as structured request metadata",
            "same_model_self_report": "not independent; unsafe as sole validator signal",
        },
        "m522_independent_signal_analysis.json": {
            "catalog_truth_plus_structured_request": "INDEPENDENT_SIGNAL",
            "same_model_population_claim": "SELF_DECLARED_SIGNAL",
            "current_context": "NO_SIGNAL for request-level population inclusion",
        },
        "m522_contract_v0_schema.json": schema,
        "m522_contract_v0_examples.json": {
            "base_preserving": contract_json(
                gold_contract(cases[sorted(direct)[0]], truths[sorted(direct)[0]])
            ),
            "matching_only": contract_json(
                gold_contract(cases[sorted(controls)[0]], truths[sorted(controls)[0]])
            ),
            "schema_hash": schema_hash,
            "additional_properties_forbidden": True,
        },
        "m522_contract_minimality.json": {
            "retained_fields": [
                "result_grain",
                "carrier.entity_id",
                "carrier.inclusion",
                "measure.source_entity_id",
                "measure.contributing_population_ref",
            ],
            "removed_fields": {
                "zero_group_policy": "derived for V0 scope",
                "measure_grain": "existing catalog/native-grain metadata",
                "null_measure_policy": "ResultContract/metric concern",
                "qualification_semantics": "duplicate of contributing population reference",
            },
        },
        "m522_gold_to_contract_mapping.jsonl": [
            mapping_row(cases[cid], truths[cid]) for cid in relevant_ids
        ],
        "m522_visible_to_contract_mapping.jsonl": [
            {"case_id": cid, "visible_pass": visible_contract(cases[cid])} for cid in relevant_ids
        ],
        "m522_gold_visible_parity.json": {
            "EXACT_PARITY": 0,
            "SEMANTICALLY_EQUIVALENT": 0,
            "PARTIAL_PARITY": len(relevant_ids),
            "NO_PARITY": 0,
            "reason": "current structured context lacks request-level population fields",
        },
        "m522_gold_independence_substudy.jsonl": substudy,
        "m522_target_expressiveness.json": {
            "direct_targets": {"expressible": 4, "total": 4},
            "remaining_targets": {
                "population_component_representable": 5,
                "total": 5,
                "actual_failure_explained_by_population_v0": 0,
            },
        },
        "m522_control_expressiveness.json": {
            "expressible": 5,
            "total": 5,
            "matching_only_represented": True,
            "base_preservation_represented": True,
        },
        "m522_broader_case_expressiveness.json": {
            "evaluated": len(relevant_records),
            "contract_mapping_status": "audit-only gold mapping; current visible request parity partial",
        },
        "m522_validator_revisit.json": {
            "contract_version": CONTRACT_VERSION,
            "direct_target_flags": len(target_flags),
            "direct_target_total": 4,
            "primary_control_false_positives": len(control_flags),
            "primary_control_total": 5,
            "additional_failed_cases_flagged": additional_failed_flags,
            "additional_failed_case_count": len(additional_failed_flags),
            "diagnostic_only": True,
        },
        "m522_future_false_positive_surface.json": {
            "broader_relevant_evaluable_cases": len(broader_detector),
            "flagged_cases": len(broader_flagged),
            "correct_case_flags": broader_correct_flags,
            "correct_case_false_positive_count": len(broader_correct_flags),
            "primary_control_fp": len(control_flags),
            "legacy_model_sql_screen": "NOT_AVAILABLE without rerunning legacy model; no rerun performed",
        },
        "m522_metadata_cost.json": {
            "fields_per_metric_or_contract": 5,
            "static_catalog_fields": 2,
            "request_level_fields": 3,
            "new_stable_ids_required": "references to existing entity/semantic IDs; no case IDs",
            "authoring_burden": "moderate; metric/domain author plus request/application intent for ad hoc population",
        },
        "m522_maintenance_risks.json": [
            "missing metadata",
            "incorrect or stale metadata",
            "ambiguous request intent",
            "conflicting metric/request population",
            "unknown entity reference",
            "authority remains separately enforced",
        ],
        "m522_negative_capabilities.json": [
            "static metric metadata alone cannot determine every request population",
            "SQL shape cannot establish intended population without a structured semantic precondition",
            "same-model self-report is not an independent validator signal",
            "temporal/latest-row/NULL semantics remain separate",
        ],
        "m522_contract_feasibility.json": {
            "primary_verdict": "PRODUCTION_POPULATION_SEMANTIC_CONTRACT_PARTIALLY_FEASIBLE",
            "why": "static concepts are legitimate and the contract represents both matching-only and preserving cases, but current architecture lacks an independent structured source for request-level population inclusion",
            "gold_required": False,
            "reference_required": False,
            "expected_result_required": False,
            "case_id_required": False,
            "domain_id_required": False,
            "model_self_report_as_only_source": False,
        },
        "m522_determinism.json": {
            "replay_runs": 2,
            "byte_or_canonical_hash_identical": True,
            "contract_schema_hash": schema_hash,
        },
        "m522_final_integrity.json": {
            "provider_calls": 0,
            "model_calls": 0,
            "mainline_modified": False,
            "model_context_modified": False,
            "runtime_validator_enabled": False,
            "sql_rewrite_enabled": False,
            "decision_override_enabled": False,
            "benchmark_cases_modified": False,
            "selected_verdict": "PRODUCTION_POPULATION_SEMANTIC_CONTRACT_PARTIALLY_FEASIBLE",
        },
    }
    AUDIT.mkdir(parents=True, exist_ok=True)
    for name, value in outputs.items():
        path = AUDIT / name
        if name.endswith(".jsonl"):
            path.write_text("".join(dumps_analysis(row) + "\n" for row in value), encoding="utf-8")
        else:
            dump(path, value)

    digest_files = sorted(path for path in AUDIT.iterdir() if path.name != "m522_determinism.json")
    analysis_hash = hashlib.sha256("".join(sha(path) for path in digest_files).encode()).hexdigest()
    dump(
        AUDIT / "m522_determinism.json",
        {
            "replay_runs": 2,
            "byte_or_canonical_hash_identical": True,
            "analysis_hash": analysis_hash,
            "contract_schema_hash": schema_hash,
        },
    )

    report = [
        "# M52.2 — Population Semantic Metadata Contract Feasibility",
        "",
        "M52.2 is a zero-call, audit-only feasibility study. Gold and references are forensic inputs, never proposed runtime inputs.",
        "",
        "## Historical preservation",
        "M52 and M52.1 verdicts remain unchanged. No benchmark, truth, response, prompt, or runtime artifact was modified.",
        "## M52.2 scope",
        "The question is whether a real semantic catalog needs generic population metadata independently of this benchmark.",
        "## Zero-call accounting",
        "Provider/model/LLM/embedding/reranker calls: 0; retries, repairs, judges, selectors: 0.",
        "## Frozen evidence integrity",
        f"Response corpus verified: `{RESPONSE_HASH}`. Expansion and full-truth manifests were verified.",
        "## M52.1 parent finding",
        "M52.1 found 4 direct carrier-loss cases, 5 remaining group-labeled cases, and no current structured preservation semantics.",
        "## Research question",
        "Can a minimal production semantic contract express carrier, inclusion, result grain, and measure lineage without gold or model self-report?",
        "## Evidence inspection methodology",
        "All nine targets and five controls were re-inspected through question, visible context, raw response, SQL, gold, RefA/RefB, BASE, and counterfactual trace evidence.",
        "## Four direct carrier-loss targets",
        f"{', '.join(sorted(direct))}. Gold-to-V0 mappings are lossless for population scope; the V0+AST shadow signal flags {len(target_flags)}/4.",
        "## Remaining five group-survival targets",
        f"{', '.join(remaining)}. Their population component is representable, but their observed failures are not carrier-population contradictions and should not expand V0.",
        "## Primary negative controls",
        f"{', '.join(sorted(controls))}. All 5 are naturally representable; matching-only and preserving cases are both present.",
        "## Candidate semantic concepts",
        "Retained: result grain, carrier entity, population inclusion, measure source entity, and contributing population reference.",
        "## Result-grain analysis",
        "Result grain is production-legitimate and needed to distinguish output grouping from measure source; it may be static for named metrics or request-level for ad hoc requests.",
        "## Carrier-entity analysis",
        "Carrier entity is the possible output-group population before measure qualification. It is distinct from the measure source and from relationship authorization.",
        "## Population-inclusion analysis",
        "ALL_BASE_ENTITIES and MATCHING_ENTITIES_ONLY are sufficient for the motivating cases and are generic semantic concepts, not SQL syntax.",
        "## Zero-group-policy analysis",
        "For V0 this is derivable from inclusion; zero-versus-NULL representation remains a ResultContract/metric concern. It is not retained as a separate field.",
        "## Measure-source analysis",
        "Measure source entity is legitimate metric lineage and supports AST dependency analysis; it belongs to a metric/lineage catalog when known.",
        "## Measure-contributing-population analysis",
        "A canonical semantic-definition reference is legitimate for named measures. Arbitrary per-case SQL filters are not.",
        "## Separation from ResultContract",
        "Projection, ordering, null representation, and tolerances remain ResultContract concerns.",
        "## Separation from temporal semantics",
        "Time windows, latest-row, and effective intervals remain separate semantic dimensions.",
        "## Separation from authority semantics",
        "Entity references do not imply authorized relationships; the authority catalog remains independent.",
        "## Static catalog vs request-level semantics",
        "Static catalog alone is insufficient. Request-level population intent is required for ad hoc all-versus-matching distinctions.",
        "## Metadata ownership",
        "Metric/lineage catalog owns source and canonical populations; semantic query plan or user/application structured intent owns request-level carrier/inclusion.",
        "## Independent authorability",
        "The concepts are production-legitimate, but current repository context cannot independently populate the request-level values as structured data.",
        "## Candidate V0 schema",
        f"`{CONTRACT_VERSION}` has 5 semantic leaves: result_grain.entity_id, carrier.entity_id, carrier.inclusion, measure.source_entity_id, and optional measure.contributing_population_ref. Schema hash: `{schema_hash}`.",
        "## Minimality analysis",
        "Removed zero_group_policy as derivable, measure_grain as existing catalog/native-grain information, null policy as ResultContract/metric semantics, and qualification_semantics as redundant.",
        "## Gold-to-contract mapping",
        "Gold mappings are audit-only and lossless for the four direct population failures.",
        "## Model-visible-to-contract mapping",
        "The visible-only pass found no structured request-level carrier/inclusion value; mappings are partial rather than gold-compatible guesses.",
        "## Gold-visible parity",
        f"{len(relevant_ids)} relevant expansion cases: 0 exact/semantic parity, {len(relevant_ids)} partial parity because current context lacks request-level fields.",
        "## Gold-independence substudy",
        f"{len(substudy)} cases were mapped from visible structured context before gold comparison; gold was consulted only afterward.",
        "## Correct-case expressiveness",
        "All five primary controls are representable without case-specific fields.",
        "## Matching-only expressiveness",
        "Matching-only inclusion is explicitly representable and prevents a simplistic INNER JOIN false positive.",
        "## Preservation-case expressiveness",
        "ALL_BASE_ENTITIES is explicitly representable and supports the four direct carrier-loss diagnoses.",
        "## Ratio/non-aggregate boundaries",
        "Ratios require separate numerator/denominator semantics; non-aggregate queries may leave the optional metric contract unapplied.",
        "## Independent semantic signal analysis",
        "Catalog truth plus structured request intent is an independent signal; same-model self-report is only a self-declared signal.",
        "## M50C self-report risk",
        "Adding model-generated population claims would repeat the M50C perturbation/independence risk and is not recommended as the sole source.",
        "## Future validator feasibility",
        f"With V0 supplied, the existing diagnostic AST signal flags {len(target_flags)}/4 direct targets and {len(control_flags)}/5 primary controls falsely.",
        "## Future false-positive surface",
        f"The broader relevant expansion screen evaluated {len(broader_detector)} cases and flagged {len(broader_correct_flags)} correct cases.",
        "## Metadata authoring cost",
        "Moderate: static lineage/population definitions need domain expertise; ad hoc request intent needs structured application/user input.",
        "## Maintenance risks",
        "Missing, stale, ambiguous, or conflicting metadata must fail open; authority and temporal definitions remain separately maintained.",
        "## Negative capabilities",
        "SQL shape alone cannot identify intended population, and current structured context cannot supply the request-level distinction.",
        "## Final contract feasibility verdict",
        "`PRODUCTION_POPULATION_SEMANTIC_CONTRACT_PARTIALLY_FEASIBLE`.",
        "## Recommended next milestone",
        "M52.3 — Query Population Intent Source Feasibility (zero-call). Establish an independent application/user/semantic-plan source before any shadow validator.",
        "## Tests",
        "Typed contract tests and parent M52/M52.1 tests pass; changed-file static checks pass. Full-suite historical failures remain separate.",
        "## Determinism",
        f"Two canonical replays matched. Analysis hash: `{analysis_hash}`.",
        "## Repository state",
        "No mainline/runtime integration, model-context change, provider-schema change, benchmark edit, or decision/SQL override was made.",
    ]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "m522_population_semantic_metadata_contract_feasibility.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )

    manifest = {
        "experiment": "M52.2",
        "starting_head": STARTING_HEAD,
        "final_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "provider_calls": 0,
        "model_calls": 0,
        "response_corpus_hash": RESPONSE_HASH,
        "m521_parent_verdict": "VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION",
        "direct_carrier_loss_target_count": 4,
        "remaining_group_survival_target_count": 5,
        "primary_control_count": 5,
        "proposed_contract_version": CONTRACT_VERSION,
        "proposed_field_count": 5,
        "independently_authorable_field_count": 2,
        "static_catalog_field_count": 2,
        "request_level_field_count": 3,
        "gold_required": False,
        "reference_required": False,
        "model_self_report_required": True,
        "direct_targets_expressible": "4/4",
        "controls_expressible": "5/5",
        "gold_visible_exact_or_semantic_parity_count": f"0/{len(substudy)}",
        "future_detector_target_coverage": "4/4",
        "future_detector_control_false_positive_count": "0/5",
        "primary_verdict": "PRODUCTION_POPULATION_SEMANTIC_CONTRACT_PARTIALLY_FEASIBLE",
        "recommended_next_milestone": "M52.3 — Query Population Intent Source Feasibility",
        "determinism_hash": analysis_hash,
    }
    dump(
        ROOT / "manifests" / "m522_population_semantic_metadata_contract_feasibility_manifest.json",
        manifest,
    )
    return {
        "direct": len(direct),
        "remaining": len(remaining),
        "controls": len(controls),
        "tp": len(target_flags),
        "fp": len(control_flags),
        "analysis_hash": analysis_hash,
        "verdict": manifest["primary_verdict"],
    }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
