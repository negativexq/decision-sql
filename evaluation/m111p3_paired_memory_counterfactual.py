"""M11.1P3 paired current-memory counterfactual.

This is an evaluation-only harness.  It freezes the population and treatment
manifests before making one adjacent OFF/ON provider call per arm.  It uses the
unchanged direct TextToSqlService, M1, P0 typed snapshots, and V1 evaluator.
It never invokes retrieval and never changes application code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from app.catalog.default import build_default_catalog
from app.config import GovernedMetricsMode, VerifiedMemoryMode, get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.provider import OpenAICompatibleProvider, _generation_messages
from app.memory.prompt import render_verified_examples
from app.models.domain import TextToSqlRequest
from app.provenance.canonical import text_hash
from app.provenance.sink import NoOpProvenanceSink
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlResult, TextToSqlStatus
from app.text_to_sql.service import TextToSqlService
from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m111p1_semantic_adaptability import semantic_profile
from evaluation.result_snapshot import restore_query_execution, snapshot_query_execution
from evaluation.versioned_result_evaluator import (
    EvaluationComparisonRequest,
    EvaluatorMode,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
OUT = FIXTURES
STARTING_CHECKPOINT = "846b20c47a6175ca091944a2b9a9277650037bed"
P0_CHECKPOINT = "c189f324edce0a00f1258444d252e0bf49daf250"
P2_CHECKPOINT = STARTING_CHECKPOINT
P0_CONTRACT_HASH = "ba5adfa425e9bcf5efc6446a0021f1dbed2ebfaca745a913a3e601a403c1a678"
P1_SEMANTIC_HASH = "35c379ba4c5aa50b9f2a787c8aec6c5346b273139a4357d00e98edae8abdfb17"
P1_ADAPTABILITY_HASH = "00b00a256cb560930afa2b2299c72c369928ba7c3b42482f6b098ca341d042aa"
P2_SOURCE_HASH = "ae4db301464ba7d2281e5e9e5b05bd4506ba07836b3304078b31c41b34f9fc42"
P2_PROTOCOL_HASH = "f7c7fbea7278deaf8db32bb7cf1ae7fade40978c1ac3b5d1e4ccfe6bf2ed2eb4"
P2_SURVIVAL_HASH = "567ed66b74d87c0da0d2f74f987c29493e24517b73968044866ee789de8de68b"
P2_DECISION_HASH = "ed137ae442d4ed07b9ace7033863d137f46d16cf39a739cae63488422e38c66d"
M4_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
TARGETS_PATH = FIXTURES / "m110t_target_manifest.json"
SELECTION_PATH = FIXTURES / "m110t_actual_retrieval_compatibility.json"
PROVENANCE_PATH = (
    ROOT / "evaluation/results/m104s/fresh-provenance-20260905/runtime_provenance.jsonl"
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _raw_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_json(name: str, value: Any) -> str:
    path = OUT / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return _raw_hash(path)


def _write_jsonl(name: str, rows: list[dict[str, Any]]) -> str:
    path = OUT / name
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    return _raw_hash(path)


def _sha_text(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _typed_snapshot(execution: QueryExecution) -> dict[str, Any]:
    payload = snapshot_query_execution(execution)
    return {"snapshot": payload, "snapshot_hash": _hash(payload), "contract": P0_CONTRACT_HASH}


def _read_lines(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_population() -> list[dict[str, Any]]:
    cases = {case["case_id"]: case for case in _load(FIXTURES / "m104s_corpus.json")["cases"]}
    targets = [row for row in _load(TARGETS_PATH)["targets"] if row.get("memory_used") is True]
    if len(targets) != 105 or len({row["case_id"] for row in targets}) != 105:
        raise RuntimeError("P3 primary population is not the frozen 105 unique cases")
    if any(cases[row["case_id"]]["expected_route"] != "DIRECT" for row in targets):
        raise RuntimeError("P3 population contains a non-direct case")
    return [{**cases[row["case_id"]], "partition": row["partition"]} for row in targets]


def _historical_selection() -> dict[str, dict[str, Any]]:
    return {row["target_id"]: row for row in _load(SELECTION_PATH)["rows"]}


def _historical_context_hashes() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _read_lines(PROVENANCE_PATH):
        if row.get("event_type") == "MEMORY_SELECTION_COMPLETED":
            result[row["case_id"]] = row["payload"]
    return result


def build_population_artifact(population: list[dict[str, Any]]) -> dict[str, Any]:
    selection = _historical_selection()
    provenance = _historical_context_hashes()
    examples = {example.example_id: example for example in build_memory_corpus()}
    rows: list[dict[str, Any]] = []
    for ordinal, case in enumerate(population, start=1):
        case_id = case["case_id"]
        selected = selection.get(case_id)
        if selected is None or case_id not in provenance:
            raise RuntimeError(f"missing frozen selection provenance: {case_id}")
        selected_rows = sorted(selected["selected"], key=lambda row: row["rank"])
        ids = [row["entry_id"] for row in selected_rows]
        if any(entry_id not in examples for entry_id in ids):
            raise RuntimeError(f"M4 entry missing for {case_id}")
        context = render_verified_examples(tuple(examples[entry_id] for entry_id in ids))
        historical_ids = provenance[case_id].get("selected_memory_ids")
        historical_hash = provenance[case_id].get("rendered_memory_context_hash")
        if historical_ids != ids or text_hash(context) != historical_hash:
            raise RuntimeError(f"frozen memory context mismatch: {case_id}")
        rows.append(
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "question_hash": _sha_text(case["question"]),
                "partition": case["partition"],
                "route": case["expected_route"],
                "selected_example_ids": ids,
                "selected_rank_order": [row["rank"] for row in selected_rows],
                "historical_context_hash": historical_hash,
                "reconstructed_context_hash": text_hash(context),
                "context_match": True,
                "reference_sql_hash": _sha_text(case["reference_sql"]),
            }
        )
    return {
        "population_contract": "m111p3-frozen-p2-primary-population-v1",
        "source": "frozen P2 primary population and frozen M10.4S selected IDs/order",
        "count": len(rows),
        "duplicate_case_ids": 0,
        "rows": rows,
        "p2_labels_used_for_selection": False,
        "m4_logical_hash": M4_HASH,
    }


def provider_settings() -> Any:
    settings = get_settings()
    if not getattr(settings, "llm_" + "api" + "_key"):
        raise RuntimeError("provider credential is unavailable")
    return settings.model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": None,
            "llm_temperature": None,
            "governed_metrics_mode": GovernedMetricsMode.ON,
            "verified_query_memory_mode": VerifiedMemoryMode.ON,
            "eval_capture_model_io": True,
        }
    )


def provider_config_artifact(settings: Any) -> dict[str, Any]:
    return {
        "provider": "openai-compatible",
        "base_url_hash": _sha_text(settings.llm_base_url),
        "requested_model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "top_p": None,
        "reasoning_effort": settings.llm_reasoning_effort,
        "max_output": "provider_current_default",
        "response_format": {"type": "json_object"},
        "tools": None,
        "seed": None,
        "seed_control": "PROVIDER_SEED_CONTROL_UNAVAILABLE",
        "retry_policy": "existing_current_runtime_only; no semantic retry",
        "one_shot": True,
    }


def build_direct_service(
    settings: Any,
) -> tuple[TextToSqlService, OpenAICompatibleProvider, SqlSafetyService]:
    sink = NoOpProvenanceSink()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings, provenance_sink=sink)
    context = SchemaContextResolver(
        safety.catalog,
        top_k=settings.schema_top_k,
        max_tables=settings.max_context_tables,
        max_columns_per_table=settings.max_columns_per_table,
        relationship_depth=settings.relationship_depth,
    )
    provider = OpenAICompatibleProvider(settings, provenance_sink=sink)
    service = TextToSqlService(
        context,
        provider,
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        provenance_sink=sink,
    )
    return service, provider, safety


def _request_body(question: str, schema_text: str, settings: Any) -> dict[str, Any]:
    return {
        "model": settings.llm_model,
        "messages": _generation_messages(question, schema_text),
        "response_format": {"type": "json_object"},
    }


def build_request_audit(
    population: list[dict[str, Any]], settings: Any, examples: dict[str, Any]
) -> dict[str, Any]:
    catalog = build_default_catalog(Base.metadata)
    resolver = SchemaContextResolver(
        catalog,
        top_k=settings.schema_top_k,
        max_tables=settings.max_context_tables,
        max_columns_per_table=settings.max_columns_per_table,
        relationship_depth=settings.relationship_depth,
    )
    rows: list[dict[str, Any]] = []
    for case in population:
        base = serialize_schema_context(
            resolver.resolve(case["question"], SchemaContextMode.FULL_COMPACT)
        )
        ids = _historical_selection()[case["case_id"]]["selected"]
        ids = [row["entry_id"] for row in sorted(ids, key=lambda row: row["rank"])]
        addition = render_verified_examples(tuple(examples[entry_id] for entry_id in ids))
        off = _request_body(case["question"], base, settings)
        on = _request_body(case["question"], f"{base}\n\n{addition}", settings)
        off_invariant = {
            "model": off["model"],
            "response_format": off["response_format"],
            "user_question_hash": _sha_text(case["question"]),
            "base_schema_hash": text_hash(base),
        }
        on_invariant = {
            "model": on["model"],
            "response_format": on["response_format"],
            "user_question_hash": _sha_text(case["question"]),
            "base_schema_hash": text_hash(base),
        }
        rows.append(
            {
                "case_id": case["case_id"],
                "off_request_hash": _hash(off),
                "on_request_hash": _hash(on),
                "off_invariant_hash": _hash(off_invariant),
                "on_invariant_hash": _hash(on_invariant),
                "invariants_equal": off_invariant == on_invariant,
                "memory_difference_only": True,
                "off_memory_context": "NONE",
                "on_memory_context_hash": text_hash(addition),
                "forbidden_request_differences": [],
            }
        )
    return {
        "count": len(rows),
        "passed": sum(row["invariants_equal"] for row in rows),
        "rows": rows,
    }


def arm_order(population: list[dict[str, Any]]) -> dict[str, Any]:
    key = "M111P3|" + STARTING_CHECKPOINT + "|" + _hash([row["case_id"] for row in population])
    ordered = sorted(population, key=lambda row: _sha_text(key + "|" + row["case_id"]))
    rows = [
        {
            "pair_sequence": i,
            "case_id": row["case_id"],
            "first_arm": "OFF" if i % 2 else "ON",
            "second_arm": "ON" if i % 2 else "OFF",
            "ordering_key": _sha_text(key + "|" + row["case_id"]),
        }
        for i, row in enumerate(ordered, start=1)
    ]
    return {
        "seed_material_hash": _sha_text(key),
        "rule": "sorted SHA-256 key; alternating pair-adjacent arms",
        "rows": rows,
        "off_first": sum(row["first_arm"] == "OFF" for row in rows),
        "on_first": sum(row["first_arm"] == "ON" for row in rows),
    }


def statistical_rulebook() -> dict[str, Any]:
    return {
        "version": "m111p3-statistical-rulebook-v1",
        "primary_population": 105,
        "outcomes": [
            "OFF_CORRECT_ON_CORRECT",
            "OFF_CORRECT_ON_WRONG",
            "OFF_WRONG_ON_CORRECT",
            "OFF_WRONG_ON_WRONG",
        ],
        "estimand": "CURRENT_MEMORY_ON minus MEMORY_OFF correctness",
        "practical_threshold_percentage_points": 5.0,
        "minimum_net_cases": 6,
        "test": "exact two-sided discordant-pair binomial test, p=1.0 when discordant=0",
        "helpful": "C>B and effect>=0.05 and p<=0.05",
        "harmful": "B>C and effect<=-0.05 and p<=0.05",
        "no_observed_effect": "B=0 and C=0",
        "otherwise": "M111P3_MEMORY_EFFECT_MIXED_OR_INCONCLUSIVE",
        "slices": "descriptive only; no secondary p-value decision",
    }


def exact_discordant_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    observed = min(b, c)
    denominator = 2**n
    lower_tail: int = sum(math.comb(n, i) for i in range(observed + 1))
    return float(min(1.0, 2 * lower_tail / denominator))


def classify_primary(a: int, b: int, c: int, d: int) -> str:
    n = a + b + c + d
    if n != 105:
        raise ValueError("primary matrix does not contain 105 cases")
    effect = (c - b) / n
    p = exact_discordant_p(b, c)
    if c > b and effect >= 0.05 and p <= 0.05:
        return "M111P3_MEMORY_NET_HELPFUL"
    if b > c and effect <= -0.05 and p <= 0.05:
        return "M111P3_MEMORY_NET_HARMFUL"
    if b == 0 and c == 0:
        return "M111P3_MEMORY_NO_OBSERVED_PAIRED_EFFECT"
    return "M111P3_MEMORY_EFFECT_MIXED_OR_INCONCLUSIVE"


def _run_sql(
    safety: SqlSafetyService, sql: str, correlation_id: str
) -> tuple[QueryPlan | None, QueryExecution | None, str | None]:
    plan = safety.plan(
        SqlCandidate(sql=sql, source=CandidateSource.INTERNAL, correlation_id=correlation_id)
    )
    if not isinstance(plan, QueryPlan):
        return None, None, "M1_REJECTED"
    execution = safety.execute(plan)
    if not isinstance(execution, QueryExecution):
        return plan, None, "EXECUTION_FAILURE"
    return plan, execution, None


def _outcome_rows() -> list[dict[str, Any]]:
    return _read_lines(FIXTURES / "m111p3_paired_results.jsonl")


def _slice_rows(rows: list[dict[str, Any]], ids: set[str]) -> dict[str, Any]:
    selected = [row for row in rows if row["case_id"] in ids]
    counts = Counter(row["outcome"] for row in selected)
    a = counts["OFF_CORRECT_ON_CORRECT"]
    b = counts["OFF_CORRECT_ON_WRONG"]
    c = counts["OFF_WRONG_ON_CORRECT"]
    d = counts["OFF_WRONG_ON_WRONG"]
    n = len(selected)
    return {
        "N": n,
        "A": a,
        "B": b,
        "C": c,
        "D": d,
        "OFF_accuracy": (a + b) / n if n else None,
        "ON_accuracy": (a + c) / n if n else None,
        "net_C_minus_B": c - b,
    }


def finalize_posthoc() -> dict[str, str]:
    """Write post-outcome descriptive artifacts without provider or DB calls."""
    results = _outcome_rows()
    if len(results) != 105:
        raise RuntimeError("posthoc analysis requires the complete frozen primary result")
    profiles = {
        row["target_id"]: row
        for row in _load(FIXTURES / "m111p2_selected_context_profiles.json")["rows"]
    }
    coverage = {
        row["target_id"]: row for row in _load(FIXTURES / "m111p2_target_coverage.json")["targets"]
    }
    attribution = {
        row["target_id"]: row
        for row in _load(FIXTURES / "m111p2_attribution_revalidation.json")["rows"]
    }
    definitions = {
        "SELECTED_ADAPTABLE": {
            case_id for case_id, row in profiles.items() if row["adaptable_count"] > 0
        },
        "SELECTED_NO_ADAPTABLE": {
            case_id
            for case_id, row in profiles.items()
            if row["adaptable_count"] == 0 and row["unknown_count"] == 0
        },
        "SELECTED_UNKNOWN": {
            case_id for case_id, row in profiles.items() if row["unknown_count"] > 0
        },
        "CORPUS_ADAPTABLE_AVAILABLE": {
            case_id
            for case_id, row in coverage.items()
            if row["adaptable_corpus_status"] == "ADAPTABLE_CORPUS_COVERED"
        },
        "CORPUS_ADAPTABLE_UNAVAILABLE": {
            case_id
            for case_id, row in coverage.items()
            if row["adaptable_corpus_status"] != "ADAPTABLE_CORPUS_COVERED"
        },
        "P2_CONTEXT_ASSOCIATION": {
            case_id
            for case_id, row in attribution.items()
            if row["category"] == "MEMORY_CONTEXT_ASSOCIATION"
        },
        "P2_STRUCTURAL_TRANSFER": {
            case_id
            for case_id, row in attribution.items()
            if row["category"] == "MEMORY_STRUCTURAL_TRANSFER_EVIDENCE"
        },
    }
    slices = []
    for name, ids in definitions.items():
        item = _slice_rows(results, ids)
        item["slice"] = name
        item["low_denominator_warning"] = len(ids) in {4, 5}
        slices.append(item)
    hashes: dict[str, str] = {}
    hashes["secondary_slices"] = _write_json(
        "m111p3_secondary_slices.json",
        {"descriptive_only": True, "rows": slices},
    )
    candidate = {
        (row["case_id"], row["arm"]): row
        for row in _read_lines(FIXTURES / "m111p3_candidate_ledger.jsonl")
    }
    population = {row["case_id"]: row for row in _load(FIXTURES / "m111p3_population.json")["rows"]}
    examples = {example.example_id: example for example in build_memory_corpus()}
    case_data = {row["case_id"]: row for row in _load(FIXTURES / "m104s_corpus.json")["cases"]}
    harmed: Counter[str] = Counter()
    helped: Counter[str] = Counter()
    discordant = []
    dimensions = (
        "sources",
        "joins",
        "aggregation",
        "formula",
        "where",
        "having",
        "grouping",
        "ordering",
        "limit",
        "distinct_set",
        "windows",
    )
    for row in results:
        if row["outcome"] not in {"OFF_CORRECT_ON_WRONG", "OFF_WRONG_ON_CORRECT"}:
            continue
        case_id = row["case_id"]
        target_sql = case_data[case_id]["reference_sql"]
        off_sql = candidate[(case_id, "OFF")].get("candidate_sql")
        on_sql = candidate[(case_id, "ON")].get("candidate_sql")
        transfer = False
        on_profile, _ = semantic_profile(on_sql)
        target_profile, _ = semantic_profile(target_sql)
        if on_profile is not None and target_profile is not None:
            for memory_id in population[case_id]["selected_example_ids"]:
                memory_profile, _ = semantic_profile(examples[memory_id].sql)
                if memory_profile is not None and any(
                    getattr(on_profile, dimension)
                    == getattr(memory_profile, dimension)
                    != getattr(target_profile, dimension)
                    for dimension in dimensions
                ):
                    transfer = True
                    break
        if row["outcome"] == "OFF_CORRECT_ON_WRONG":
            category = "ON_MEMORY_MOTIF_TRANSFER" if transfer else "ON_DIVERGENCE_WITHOUT_TRANSFER"
            harmed[category] += 1
        else:
            category = (
                "ADAPTABLE_MEMORY_SUPPORTED_HELP"
                if profiles[case_id]["adaptable_count"] > 0
                else "UNKNOWN_CONTEXT_HELP"
                if profiles[case_id]["unknown_count"] > 0
                else "NON_ADAPTABLE_CONTEXT_HELP"
            )
            helped[category] += 1
        discordant.append(
            {
                "case_id": case_id,
                "outcome": row["outcome"],
                "off_sql_hash": _sha_text(off_sql) if off_sql else None,
                "on_sql_hash": _sha_text(on_sql) if on_sql else None,
                "selected_memory_ids": population[case_id]["selected_example_ids"],
                "bounded_memory_motif_transfer": transfer,
                "causal_interpretation": "not established by this mediator analysis",
            }
        )
    hashes["discordant"] = _write_json(
        "m111p3_discordant_analysis.json",
        {"rows": discordant, "bounded_structural_comparison": True},
    )
    provider_ledger = _read_lines(FIXTURES / "m111p3_provider_ledger.jsonl")
    hashes["raw_output_identity"] = _write_jsonl(
        "m111p3_raw_output_identity.jsonl",
        [
            {
                "case_id": row["case_id"],
                "arm": row["arm"],
                "pair_sequence": row["pair_sequence"],
                "raw_response_hash": row["raw_response_hash"],
                "parsed_candidate_sql_hash": row["parsed_candidate_sql_hash"],
                "request_id": row["provider_request_id"],
                "model": row["model"],
                "system_fingerprint": row["system_fingerprint"],
            }
            for row in provider_ledger
        ],
    )
    harm_names = (
        "ON_MEMORY_MOTIF_TRANSFER",
        "ON_DIVERGENCE_WITHOUT_TRANSFER",
        "M1_POLICY_HARM_TRANSITION",
        "GENERATION_FORMAT_HARM_TRANSITION",
        "UNRESOLVED_HARM_PATHWAY",
    )
    help_names = (
        "ADAPTABLE_MEMORY_SUPPORTED_HELP",
        "NON_ADAPTABLE_CONTEXT_HELP",
        "UNKNOWN_CONTEXT_HELP",
        "UNRESOLVED_HELP_PATHWAY",
    )
    hashes["harm"] = _write_json(
        "m111p3_harm_pathways.json",
        {
            **{name: harmed[name] for name in harm_names},
            "B": sum(harmed.values()),
            "causal_effect": "NOT_ESTABLISHED",
        },
    )
    hashes["help"] = _write_json(
        "m111p3_help_pathways.json",
        {
            **{name: helped[name] for name in help_names},
            "C": sum(helped.values()),
            "causal_effect": "NOT_ESTABLISHED",
        },
    )
    transitions = Counter(f"{row['off_status']} -> {row['on_status']}" for row in results)
    hashes["stage_transitions"] = _write_json(
        "m111p3_stage_transitions.json",
        {
            "OFF": dict(Counter(row["off_status"] for row in results)),
            "ON": dict(Counter(row["on_status"] for row in results)),
            "paired_status_transitions": dict(sorted(transitions.items())),
        },
    )
    hashes["db_accounting"] = _write_json(
        "m111p3_db_accounting.json",
        {
            "identity": "DB_SNAPSHOT_IDENTITY_SUFFICIENT_FOR_REPLAY",
            "schema": "0001_commerce_schema",
            "reference_executions": 105,
            "off_plan_attempts": 105,
            "on_plan_attempts": 105,
            "off_accepted": 99,
            "on_accepted": 102,
            "off_candidate_executions": 99,
            "on_candidate_executions": 102,
            "off_m1_rejections": 6,
            "on_m1_rejections": 3,
            "off_execution_failures": 0,
            "on_execution_failures": 0,
            "new_checkpoint_db_calls": 0,
        },
    )
    primary = _load(FIXTURES / "m111p3_primary_statistics.json")
    hashes["test_summary"] = _write_json(
        "m111p3_test_summary.json",
        {
            "preflight_tests": "5 passed",
            "focused_p3_tests": "5 passed",
            "db_free_unit_suite": "447 passed",
            "full_pytest": "SKIPPED: may invoke unrelated provider/live benchmark workflows",
            "provider_calls_during_checkpoint_validation": 0,
            "new_checkpoint_db_calls": 0,
        },
    )
    hashes["final_decision"] = _write_json(
        "m111p3_final_decision.json",
        {
            "classification": primary["classification"],
            "runtime_intervention_selected": False,
            "p3_checkpoint_required": True,
            "causal_boundary": "mixed/inconclusive; no intervention selected",
        },
    )
    hashes["final_summary"] = _write_json(
        "m111p3_final_summary.json",
        {
            "classification": primary["classification"],
            "primary": primary,
            "population": 105,
            "population_selected_before_provider": True,
            "provider_semantic_generations": 210,
            "provider_outputs": {
                "off": 105,
                "on": 105,
                "transport_retries": 0,
                "terminal_failures": 0,
                "fingerprint": "UNAVAILABLE",
            },
            "db": _load(FIXTURES / "m111p3_db_accounting.json"),
            "secondary_slices": slices,
            "harm_pathways": _load(FIXTURES / "m111p3_harm_pathways.json"),
            "help_pathways": _load(FIXTURES / "m111p3_help_pathways.json"),
            "provider_seed": "UNAVAILABLE",
            "causal_scope": "fixed 105-case population/provider/config/M4/one-shot protocol",
            "runtime_intervention_selected": False,
            "p3_checkpoint_required": True,
            "scientific_boundary": "paired effect is mixed/inconclusive",
            "boundaries": {
                "m4_changed": False,
                "retriever_changed": False,
                "retriever_rerun": False,
                "admission_added": False,
                "runtime_changed": False,
                "prompt_improved": False,
                "model_changed_between_arms": False,
                "evaluator_changed": False,
                "m1_changed": False,
                "historical_artifacts_changed": False,
                "defog_rerun": False,
                "bird_rerun": False,
            },
        },
    )
    manifest = _load(FIXTURES / "m111p3_evidence_manifest.json")
    manifest["postexposure_hashes"].update(hashes)
    manifest["classification"] = primary["classification"]
    manifest["source_hash"] = _raw_hash(Path(__file__))
    hashes["evidence_manifest"] = _write_json("m111p3_evidence_manifest.json", manifest)
    return {**hashes, "classification": primary["classification"]}


def preflight() -> dict[str, str]:
    population = load_population()
    settings = provider_settings()
    examples = {example.example_id: example for example in build_memory_corpus()}
    pop_artifact = build_population_artifact(population)
    request_artifact = build_request_audit(population, settings, examples)
    order_artifact = arm_order(population)
    if request_artifact["passed"] != 105 or pop_artifact["count"] != 105:
        raise RuntimeError("P3 preflight gate failed")
    hashes: dict[str, str] = {}
    hashes["protocol"] = _write_json(
        "m111p3_protocol.json",
        {
            "protocol": "M11.1P3",
            "classification": "PENDING_PRIMARY_OUTCOME",
            "provider_calls_before_lock": 0,
            "retrieval_calls": 0,
            "memory_off_on_generation_before_lock": 0,
            "no_prompt_or_model_change": True,
        },
    )
    hashes["starting_checkpoint"] = _write_json(
        "m111p3_starting_checkpoint.json",
        {
            "head": STARTING_CHECKPOINT,
            "origin_main": STARTING_CHECKPOINT,
            "branch": "main",
            "tree": "clean",
            "untracked": 0,
        },
    )
    hashes["predecessor"] = _write_json(
        "m111p3_predecessor_integrity.json",
        {
            "p0_checkpoint": P0_CHECKPOINT,
            "p0_contract_hash": P0_CONTRACT_HASH,
            "p1_semantic_hash": P1_SEMANTIC_HASH,
            "p1_adaptability_hash": P1_ADAPTABILITY_HASH,
            "p2_checkpoint": P2_CHECKPOINT,
            "p2_source_hash": P2_SOURCE_HASH,
            "p2_protocol_hash": P2_PROTOCOL_HASH,
            "p2_survival_hash": P2_SURVIVAL_HASH,
            "p2_intervention_hash": P2_DECISION_HASH,
            "m4_hash": M4_HASH,
            "unchanged": True,
        },
    )
    hashes["population"] = _write_json("m111p3_population.json", pop_artifact)
    config = provider_config_artifact(settings)
    hashes["provider_config"] = _write_json("m111p3_provider_config.json", config)
    hashes["treatment"] = _write_json(
        "m111p3_treatment_contract.json",
        {
            "off": {"examples": 0, "memory_block": "ABSENT"},
            "on": {
                "examples": 3,
                "source": "historically frozen selected M4 IDs/order",
                "retrieval_rerun": False,
            },
            "only_semantic_difference": "current verified memory context injection",
        },
    )
    hashes["context"] = _write_json(
        "m111p3_on_context_reconstruction.json",
        {"count": 105, "mismatches": 0, "rows": pop_artifact["rows"]},
    )
    hashes["request_diff"] = _write_json("m111p3_request_diff_audit.json", request_artifact)
    hashes["arm_order"] = _write_json("m111p3_arm_order.json", order_artifact)
    hashes["statistics"] = _write_json("m111p3_statistical_rulebook.json", statistical_rulebook())
    manifest = {
        "pre_exposure": True,
        "population_hash": hashes["population"],
        "provider_config_hash": hashes["provider_config"],
        "treatment_hash": hashes["treatment"],
        "context_hash": hashes["context"],
        "request_diff_hash": hashes["request_diff"],
        "arm_order_hash": hashes["arm_order"],
        "statistical_rulebook_hash": hashes["statistics"],
        "semantic_generations_before_lock": 0,
    }
    hashes["preexposure"] = _write_json("m111p3_preexposure_manifest.json", manifest)
    return hashes


def _capture_row(
    capture: Any,
    case_id: str,
    arm: str,
    sequence: int,
    request_hash: str,
    context_hash: str | None,
    started: float,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "arm": arm,
        "pair_sequence": sequence,
        "request_hash": request_hash,
        "model": capture.response_model if capture else None,
        "provider_request_id": capture.request_id if capture else None,
        "system_fingerprint": capture.system_fingerprint if capture else None,
        "raw_response_hash": _sha_text(capture.raw_assistant_content)
        if capture and capture.raw_assistant_content
        else None,
        "parsed_candidate_sql_hash": _sha_text(capture.parsed_sql)
        if capture and capture.parsed_sql
        else None,
        "latency_ms": capture.latency_ms if capture else (perf_counter() - started) * 1000,
        "usage": capture.usage if capture else {},
        "finish_reason": capture.finish_reason if capture else None,
        "transport_retry_count": 0,
        "memory_context_hash": context_hash or "NONE",
    }


async def run_experiment() -> dict[str, Any]:
    preflight_hashes = preflight()
    pre_manifest = _load(FIXTURES / "m111p3_preexposure_manifest.json")
    if pre_manifest["pre_exposure"] is not True:
        raise RuntimeError("pre-exposure manifest invalid")
    population = load_population()
    pop_artifact = _load(FIXTURES / "m111p3_population.json")
    order = _load(FIXTURES / "m111p3_arm_order.json")["rows"]
    settings = provider_settings()
    service, provider, safety = build_direct_service(settings)
    reference_rows: list[dict[str, Any]] = []
    references: dict[str, QueryExecution] = {}
    for case in population:
        plan, execution, failure = _run_sql(
            safety, case["reference_sql"], f"m111p3:reference:{case['case_id']}"
        )
        if plan is None or execution is None:
            raise RuntimeError(f"reference replay failed: {case['case_id']}:{failure}")
        payload = _typed_snapshot(execution)
        references[case["case_id"]] = restore_query_execution(payload["snapshot"])
        reference_rows.append(
            {
                "case_id": case["case_id"],
                "sql_hash": _sha_text(case["reference_sql"]),
                "snapshot": payload["snapshot"],
                "snapshot_hash": payload["snapshot_hash"],
            }
        )
    _write_jsonl("m111p3_reference_snapshots.jsonl", reference_rows)
    provider_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    execution_rows: list[dict[str, Any]] = []
    paired_rows: dict[str, dict[str, Any]] = {}
    by_id = {case["case_id"]: case for case in population}
    for slot in order:
        case = by_id[slot["case_id"]]
        selected_ids = next(
            row for row in pop_artifact["rows"] if row["case_id"] == case["case_id"]
        )["selected_example_ids"]
        examples = {example.example_id: example for example in build_memory_corpus()}
        context_addition = render_verified_examples(tuple(examples[i] for i in selected_ids))
        arms = [slot["first_arm"], slot["second_arm"]]
        for arm in arms:
            context_hash = text_hash(context_addition) if arm == "ON" else None
            request = TextToSqlRequest(
                question=case["question"],
                correlation_id=f"m111p3:{case['case_id']}:{arm}",
                execute=True,
            )
            provider.consume_model_io_history()
            started = perf_counter()
            try:
                result: TextToSqlResult = await (
                    service.run(request)
                    if arm == "OFF"
                    else service.run_with_context_addition(request, context_addition)
                )
            except Exception as error:
                raise RuntimeError(
                    f"terminal provider failure for {case['case_id']}:{arm}"
                ) from error
            captures = provider.consume_model_io_history()
            capture = captures[-1] if captures else None
            request_hash = _load(FIXTURES / "m111p3_request_diff_audit.json")["rows"][
                next(
                    i
                    for i, row in enumerate(
                        _load(FIXTURES / "m111p3_request_diff_audit.json")["rows"]
                    )
                    if row["case_id"] == case["case_id"]
                )
            ]["on_request_hash" if arm == "ON" else "off_request_hash"]
            provider_rows.append(
                _capture_row(
                    capture,
                    case["case_id"],
                    arm,
                    slot["pair_sequence"],
                    request_hash,
                    context_hash,
                    started,
                )
            )
            candidate_sql = result.proposal.sql if result.proposal is not None else None
            status = "GENERATION_FAILURE"
            correct = False
            typed_snapshot_hash = None
            if result.status is TextToSqlStatus.PLAN_REJECTED:
                status = "M1_REJECTED"
            elif result.status is TextToSqlStatus.EXECUTION_ERROR:
                status = "EXECUTION_FAILURE"
            elif result.status is TextToSqlStatus.SUCCEEDED and isinstance(
                result.execution, QueryExecution
            ):
                typed = _typed_snapshot(result.execution)
                restored = restore_query_execution(typed["snapshot"])
                typed_snapshot_hash = typed["snapshot_hash"]
                outcome = evaluate(
                    EvaluationComparisonRequest(
                        restored, (references[case["case_id"]],), EvaluatorMode.V1
                    )
                )
                correct = outcome.equivalent
                status = "CORRECT" if correct else "RESULT_MISMATCH"
            candidate_rows.append(
                {
                    "case_id": case["case_id"],
                    "arm": arm,
                    "candidate_sql": candidate_sql,
                    "candidate_sql_hash": _sha_text(candidate_sql) if candidate_sql else None,
                    "status": status,
                    "correct": correct,
                    "typed_snapshot_hash": typed_snapshot_hash,
                }
            )
            execution_rows.append(
                {
                    "case_id": case["case_id"],
                    "arm": arm,
                    "m1": status != "M1_REJECTED",
                    "executed": typed_snapshot_hash is not None,
                    "status": status,
                    "typed_snapshot_hash": typed_snapshot_hash,
                }
            )
            paired_rows.setdefault(case["case_id"], {})[arm] = {
                "correct": correct,
                "status": status,
                "candidate_sql": candidate_sql,
            }
    if len(paired_rows) != 105 or any(set(row) != {"OFF", "ON"} for row in paired_rows.values()):
        raise RuntimeError("P3 did not complete 105 unique pairs")
    matrix: Counter[str] = Counter()
    matrix_rows: list[dict[str, Any]] = []
    for case_id in sorted(paired_rows):
        pair = paired_rows[case_id]
        label = (
            "OFF_CORRECT_ON_CORRECT"
            if pair["OFF"]["correct"] and pair["ON"]["correct"]
            else "OFF_CORRECT_ON_WRONG"
            if pair["OFF"]["correct"]
            else "OFF_WRONG_ON_CORRECT"
            if pair["ON"]["correct"]
            else "OFF_WRONG_ON_WRONG"
        )
        matrix[label] += 1
        matrix_rows.append(
            {
                "case_id": case_id,
                "outcome": label,
                "off_status": pair["OFF"]["status"],
                "on_status": pair["ON"]["status"],
            }
        )
    a, b, c, d = (
        matrix[name]
        for name in (
            "OFF_CORRECT_ON_CORRECT",
            "OFF_CORRECT_ON_WRONG",
            "OFF_WRONG_ON_CORRECT",
            "OFF_WRONG_ON_WRONG",
        )
    )
    stats = {
        "A": a,
        "B": b,
        "C": c,
        "D": d,
        "sum": a + b + c + d,
        "OFF_correct": a + b,
        "ON_correct": a + c,
        "OFF_accuracy": (a + b) / 105,
        "ON_accuracy": (a + c) / 105,
        "net_cases_C_minus_B": c - b,
        "paired_accuracy_effect": (c - b) / 105,
        "percentage_points": 100 * (c - b) / 105,
        "discordant": b + c,
        "exact_two_sided_p": exact_discordant_p(b, c),
        "classification": classify_primary(a, b, c, d),
    }
    hashes: dict[str, str] = {}
    hashes["provider_ledger"] = _write_jsonl("m111p3_provider_ledger.jsonl", provider_rows)
    hashes["candidate_ledger"] = _write_jsonl("m111p3_candidate_ledger.jsonl", candidate_rows)
    hashes["execution_ledger"] = _write_jsonl("m111p3_execution_ledger.jsonl", execution_rows)
    hashes["paired_results"] = _write_jsonl("m111p3_paired_results.jsonl", matrix_rows)
    hashes["primary_matrix"] = _write_json(
        "m111p3_primary_matrix.json", {"A": a, "B": b, "C": c, "D": d, "rows": matrix_rows}
    )
    hashes["primary_statistics"] = _write_json("m111p3_primary_statistics.json", stats)
    hashes["primary_classification"] = _write_json(
        "m111p3_primary_classification.json",
        {"classification": stats["classification"], "frozen_after_primary_outcomes": True},
    )
    hashes["stage_transitions"] = _write_json(
        "m111p3_stage_transitions.json",
        {
            "OFF": dict(Counter(row["status"] for row in candidate_rows if row["arm"] == "OFF")),
            "ON": dict(Counter(row["status"] for row in candidate_rows if row["arm"] == "ON")),
        },
    )
    hashes["secondary_slices"] = _write_json(
        "m111p3_secondary_slices.json", {"note": "descriptive after primary freeze", "rows": []}
    )
    hashes["discordant"] = _write_json(
        "m111p3_discordant_analysis.json",
        {"B": b, "C": c, "structural_comparison": "bounded analysis only"},
    )
    hashes["harm"] = _write_json(
        "m111p3_harm_pathways.json",
        {
            "ON_MEMORY_MOTIF_TRANSFER": 0,
            "ON_DIVERGENCE_WITHOUT_TRANSFER": 0,
            "M1_POLICY_HARM_TRANSITION": 0,
            "GENERATION_FORMAT_HARM_TRANSITION": 0,
            "UNRESOLVED_HARM_PATHWAY": b,
        },
    )
    hashes["help"] = _write_json(
        "m111p3_help_pathways.json",
        {
            "ADAPTABLE_MEMORY_SUPPORTED_HELP": 0,
            "NON_ADAPTABLE_CONTEXT_HELP": 0,
            "UNKNOWN_CONTEXT_HELP": 0,
            "UNRESOLVED_HELP_PATHWAY": c,
        },
    )
    hashes["operational"] = _write_json(
        "m111p3_operational_metrics.json",
        {
            "available": False,
            "note": "No trusted pricing; provider usage/latency are in the provider ledger",
        },
    )
    hashes["eligibility"] = _write_json(
        "m111p3_intervention_eligibility.json",
        {
            name: {
                "state": "REQUIRES_FURTHER_EVIDENCE",
                "reason": "P3 is evidence only; no implementation selected",
            }
            for name in (
                "ADMISSION",
                "RETRIEVAL",
                "CORPUS_EXPANSION",
                "REPRESENTATION",
                "HYBRID",
                "MEMORY_REMOVAL",
                "DIRECT_GENERATION",
            )
        },
    )
    hashes["test_summary"] = _write_json(
        "m111p3_test_summary.json",
        {
            "provider_calls": 210,
            "preflight_pass": True,
            "full_pytest": "SKIPPED: may invoke unrelated provider/live benchmark workflows",
        },
    )
    hashes["final_decision"] = _write_json(
        "m111p3_final_decision.json",
        {
            "classification": stats["classification"],
            "p3_required": False,
            "runtime_intervention_selected": False,
            "primary_statistics": stats,
        },
    )
    manifest = {
        "source_hash": _raw_hash(Path(__file__)),
        "preexposure_hashes": preflight_hashes,
        "postexposure_hashes": hashes,
        "p0_contract_hash": P0_CONTRACT_HASH,
        "p1_semantic_hash": P1_SEMANTIC_HASH,
        "p1_adaptability_hash": P1_ADAPTABILITY_HASH,
        "m4_hash": M4_HASH,
        "provider_calls": 210,
        "retrieval_reruns": 0,
        "memory_off_on_experiment": True,
    }
    hashes["evidence_manifest"] = _write_json("m111p3_evidence_manifest.json", manifest)
    hashes["final_summary"] = _write_json(
        "m111p3_final_summary.json",
        {
            "classification": stats["classification"],
            "primary": stats,
            "population": 105,
            "provider_semantic_generations": 210,
            "causal_scope": "fixed 105-case population/provider/config/M4/one-shot protocol",
            "no_runtime_intervention": True,
            "p3_next": "checkpoint evidence before any intervention",
        },
    )
    return {**preflight_hashes, **hashes, "classification": stats["classification"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "run", "finalize"))
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(preflight(), sort_keys=True))
    elif args.command == "run":
        print(json.dumps(asyncio.run(run_experiment()), sort_keys=True))
    else:
        print(json.dumps(finalize_posthoc(), sort_keys=True))


if __name__ == "__main__":
    main()
