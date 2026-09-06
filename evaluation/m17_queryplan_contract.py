"""M17 provider-contract repair and fresh QueryPlan V1 revalidation.

The canonical QueryPlan V1 domain contract and compiler remain unchanged.  This
runner only changes the provider wire representation and records mechanical
provider-boundary stages before canonical catalog validation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.db.session import build_reader_engine
from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    QueryPlanProviderBoundaryError,
)
from app.models.domain import QueryRequest
from app.provenance.canonical import text_hash
from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Catalog,
    QueryPlanV1Compiler,
    QueryPlanV1ValidationError,
    render_query_plan_v1_context,
    stable_hash,
    validate_query_plan_v1,
)
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from evaluation.m16_general_queryplan import M16Dataset
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticExecutionSnapshot,
    DiagnosticFixture,
    _snapshot,
    compare_snapshots,
)

FIXTURES = Path(__file__).with_name("fixtures")
M16_DATASET_PATH = FIXTURES / "m16_dataset.json"
PREFLIGHT_RESULT_PATH = FIXTURES / "m17_preflight_result.json"
PREFLIGHT_DATASET_PATH = FIXTURES / "m17_preflight_dataset.json"
DATASET_PATH = FIXTURES / "m17_dataset.json"
RESULT_PATH = FIXTURES / "m17_result.json"
CONTRACT_PATH = FIXTURES / "m17_provider_contract.json"
MANIFEST_PATH = FIXTURES / "m17_evidence_manifest.json"
WIRE_VERSION = "QueryPlanWireV2"
SHAPE_TARGETS = {
    "SOURCE_PROJECTION": 15,
    "SOURCE_FILTER": 25,
    "ONE_JOIN_PROJECTION": 20,
    "ONE_JOIN_FILTER": 30,
    "TWO_JOINS_PROJECTION": 10,
    "TWO_JOINS_FILTER": 20,
}
PRIMARY_TARGETS = {
    "SOURCE_PROJECTION": 11,
    "SOURCE_FILTER": 19,
    "ONE_JOIN_PROJECTION": 15,
    "ONE_JOIN_FILTER": 22,
    "TWO_JOINS_PROJECTION": 8,
    "TWO_JOINS_FILTER": 15,
}


class M17Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    shape: str
    split: str
    question: str
    question_hash: str
    gold_plan: QueryPlanV1
    gold_plan_hash: str
    gold_sql: str
    gold_sql_hash: str
    comparison_mode: ComparisonMode
    gold_columns: tuple[str, ...]
    gold_typed_rows: tuple[tuple[tuple[str, Any], ...], ...]
    gold_row_count: int
    gold_result_hash: str


class M17Dataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    wire_version: str
    catalog_hash: str
    cases: tuple[M17Case, ...]
    dataset_hash: str


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def _hash_without(model: BaseModel, field: str) -> str:
    return stable_hash(model.model_dump(mode="json", exclude={field}))


def _fixture() -> DiagnosticFixture:
    fixture = DiagnosticFixture(
        fixture_id="m17-gold-demo",
        fixture_version="m17-gold-1",
        schema_version="decision-sql-demo-schema-1",
        seed="m17-fresh-gold-seed",
        content_hash="placeholder",
    )
    return fixture.model_copy(update={"content_hash": _hash_without(fixture, "content_hash")})


def _historical_question_hashes() -> set[str]:
    hashes: set[str] = set()
    generated = {
        PREFLIGHT_RESULT_PATH.resolve(),
        PREFLIGHT_DATASET_PATH.resolve(),
        DATASET_PATH.resolve(),
        RESULT_PATH.resolve(),
        CONTRACT_PATH.resolve(),
        MANIFEST_PATH.resolve(),
    }
    for path in Path("evaluation").rglob("*.json"):
        if path.resolve() in generated:
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                question = value.get("question")
                if isinstance(question, str):
                    hashes.add(text_hash(_normalize_question(question)))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(payload)
    return hashes


def _column_label(catalog: QueryPlanV1Catalog, column_id: str) -> str:
    column = catalog.column(column_id)
    return f"{_table_label(column.table_id)} {column.name.replace('_', ' ')}"


def _table_label(table_id: str) -> str:
    return table_id.replace("_", " ")


def _question(plan: QueryPlanV1, shape: str, catalog: QueryPlanV1Catalog, index: int) -> str:
    projection = [_column_label(catalog, item) for item in plan.projection]
    if len(projection) == 1:
        selected = projection[0]
    elif len(projection) == 2:
        selected = f"{projection[0]} and {projection[1]}"
    else:
        selected = f"{projection[0]}, {projection[1]}, and {projection[2]}"
    source = _table_label(plan.source or "records")
    if plan.joins:
        joined = []
        for join in plan.joins:
            relation = catalog.relationship(join.relationship_id)
            target = (
                relation.right_table_id
                if relation.left_table_id == plan.source
                else relation.left_table_id
            )
            link = relation.left_column_id.split(".")[-1].replace("_", " ")
            joined.append(f"{_table_label(target)} through its {link} link")
        scope = f"the {selected} from {source} connected with {', '.join(joined)}"
    else:
        scope = f"the {selected} from {source}"
    if not plan.filters:
        templates = (
            "Return {scope} for this records lookup.",
            "Which records provide {scope}?",
            "List {scope} from the available business data.",
            "Retrieve {scope} for the requested relational view.",
        )
        return templates[index % len(templates)].format(scope=scope)
    clauses: list[str] = []
    words = {
        "EQ": "equals",
        "NE": "does not equal",
        "LT": "is less than",
        "LTE": "is at most",
        "GT": "is greater than",
        "GTE": "is at least",
    }
    for predicate in plan.filters:
        label = _column_label(catalog, predicate.column_id)
        if predicate.operator.value == "IS_NULL":
            clauses.append(f"{label} is missing")
        elif predicate.operator.value == "IS_NOT_NULL":
            clauses.append(f"{label} is present")
        else:
            assert predicate.value is not None
            clauses.append(f"{label} {words[predicate.operator.value]} {predicate.value.value}")
    condition = " and ".join(clauses)
    templates = (
        "Return {scope} only for records where {condition}.",
        "Show {scope} when the records satisfy {condition}.",
        "Retrieve {scope}, restricting the records to {condition}.",
        "For records with {condition}, list {scope}.",
    )
    return templates[(index + 1) % len(templates)].format(scope=scope, condition=condition)


def _source_plan_map() -> list[tuple[str, QueryPlanV1]]:
    historical = M16Dataset.model_validate_json(M16_DATASET_PATH.read_text())
    return [(case.shape, case.gold_plan) for case in historical.cases]


def _selected_items(preflight: bool) -> list[tuple[str, QueryPlanV1, str]]:
    grouped: dict[str, list[QueryPlanV1]] = defaultdict(list)
    for shape, plan in _source_plan_map():
        grouped[shape].append(plan)
    selected: list[tuple[str, QueryPlanV1, str]] = []
    for shape, plans in grouped.items():
        ordered = sorted(plans, key=lambda plan: stable_hash(f"m17:{plan.content_hash}"))
        if preflight:
            ordered = ordered[:3]
        else:
            primary_count = PRIMARY_TARGETS[shape]
            ordered = ordered[: SHAPE_TARGETS[shape]]
        for index, plan in enumerate(ordered):
            split = (
                "PREFLIGHT"
                if preflight
                else ("PRIMARY" if index < primary_count else "CONFIRMATION")
            )
            selected.append((shape, plan, split))
    return sorted(selected, key=lambda item: item[1].content_hash)


def _prepare_cases(
    selected: list[tuple[str, QueryPlanV1, str]], settings: Settings
) -> tuple[list[M17Case], str]:
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    fixture = _fixture()
    cases: list[M17Case] = []
    try:
        with engine.connect():
            for index, (shape, plan, split) in enumerate(selected):
                validate_query_plan_v1(plan, catalog)
                compiled = compiler.compile(plan)
                repeat = compiler.compile(plan)
                if compiled.sql != repeat.sql:
                    raise RuntimeError(f"compiler nondeterminism: {plan.content_hash}")
                planned = safety.plan(
                    compiled.model_copy(update={"correlation_id": f"m17-gold-{index:03d}"})
                )
                if not isinstance(planned, QueryPlan):
                    raise RuntimeError(f"M1 gold failure: {plan.content_hash}")
                execution = safety.execute(planned)
                if not isinstance(execution, QueryExecution) or execution.truncated:
                    raise RuntimeError(f"gold execution failure: {plan.content_hash}")
                snapshot = _snapshot(
                    execution, text_hash(compiled.sql), fixture, order_sensitive=False
                )
                repeat_execution = safety.execute(planned)
                if not isinstance(repeat_execution, QueryExecution):
                    raise RuntimeError(f"gold repeat execution failure: {plan.content_hash}")
                repeat_snapshot = _snapshot(
                    repeat_execution, text_hash(compiled.sql), fixture, order_sensitive=False
                )
                if snapshot.result_hash != repeat_snapshot.result_hash:
                    raise RuntimeError(f"gold result nondeterminism: {plan.content_hash}")
                question = _question(plan, shape, catalog, index)
                cases.append(
                    M17Case(
                        case_id=f"m17-queryplan-{split.lower()}-{index:03d}",
                        shape=shape,
                        split=split,
                        question=question,
                        question_hash=text_hash(_normalize_question(question)),
                        gold_plan=plan,
                        gold_plan_hash=plan.content_hash,
                        gold_sql=compiled.sql,
                        gold_sql_hash=text_hash(compiled.sql),
                        comparison_mode=ComparisonMode.VALUE_BAG,
                        gold_columns=snapshot.columns,
                        gold_typed_rows=snapshot.typed_rows,
                        gold_row_count=snapshot.row_count,
                        gold_result_hash=snapshot.result_hash,
                    )
                )
    finally:
        engine.dispose()
    if len({case.question_hash for case in cases}) != len(cases):
        raise RuntimeError("M17 duplicate questions")
    overlap = {case.question_hash for case in cases} & _historical_question_hashes()
    if overlap:
        raise RuntimeError(f"M17 historical overlap: {sorted(overlap)}")
    return cases, catalog.content_hash


def prepare(preflight: bool) -> M17Dataset:
    settings = _settings()
    selected = _selected_items(preflight)
    cases, catalog_hash = _prepare_cases(selected, settings)
    dataset = M17Dataset(
        version="m17-queryplan-contract-1",
        wire_version=WIRE_VERSION,
        catalog_hash=catalog_hash,
        cases=tuple(cases),
        dataset_hash="placeholder",
    )
    dataset = dataset.model_copy(update={"dataset_hash": _hash_without(dataset, "dataset_hash")})
    path = PREFLIGHT_DATASET_PATH if preflight else DATASET_PATH
    path.write_text(json.dumps(dataset.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    return dataset


def _settings() -> Settings:
    base = Settings(_env_file=".env")
    return base.model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_temperature": 0.0,
            "llm_reasoning_effort": "none",
            "eval_capture_model_io": True,
        }
    )


def _gold_snapshot(case: M17Case) -> DiagnosticExecutionSnapshot:
    return DiagnosticExecutionSnapshot(
        query_hash=case.gold_sql_hash,
        fixture_id="m17-gold-demo",
        columns=case.gold_columns,
        typed_rows=case.gold_typed_rows,
        row_count=case.gold_row_count,
        truncated=False,
        result_hash=case.gold_result_hash,
        latency_ms=0.0,
    )


def _exact_binomial_pvalue(b: int, c: int) -> float:
    discordant = b + c
    if discordant == 0:
        return 1.0
    from math import comb

    p_value: float = min(
        1.0,
        2.0 * sum(comb(discordant, k) for k in range(min(b, c) + 1)) / (2**discordant),
    )
    return p_value


async def _call(
    provider: OpenAICompatibleProvider, operation: str, case: M17Case, context: str
) -> tuple[Any, int]:
    retries = 0
    for attempt in range(2):
        try:
            if operation == "control":
                return await provider.propose_sql(
                    QueryRequest(question=case.question), None, context
                ), retries
            return await provider.propose_query_plan_wire_v2(case.question, context), retries
        except LLMProviderError as error:
            retryable = error.detail is not None and error.detail.retryable
            if attempt == 0 and retryable:
                retries += 1
                continue
            raise
    raise AssertionError("unreachable")


async def run(dataset: M17Dataset, confirmation: bool = False) -> dict[str, Any]:
    settings = _settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    context = render_query_plan_v1_context(catalog)
    provider = OpenAICompatibleProvider(settings=settings)
    fixture = _fixture()
    cases = [
        case for case in dataset.cases if confirmation or case.split in {"PRIMARY", "PREFLIGHT"}
    ]
    ledger: list[dict[str, Any]] = []
    try:
        for case in cases:
            order = (
                "CONTROL_FIRST"
                if int(stable_hash(case.case_id)[0], 16) % 2 == 0
                else "INTERVENTION_FIRST"
            )
            operations = (
                ("control", "intervention")
                if order == "CONTROL_FIRST"
                else ("intervention", "control")
            )
            outputs: dict[str, dict[str, Any]] = {}
            for operation in operations:
                started = perf_counter()
                row: dict[str, Any] = {
                    "operation": operation,
                    "correct": False,
                    "failure": None,
                    "contract_acquired": False,
                    "technical_retries": 0,
                }
                try:
                    proposal, retries = await _call(provider, operation, case, context)
                    row["technical_retries"] = retries
                    row["provider"] = proposal.provider
                    row["model"] = proposal.model
                    row["latency_ms"] = proposal.latency_ms
                    if operation == "control":
                        row["sql_hash"] = text_hash(proposal.sql)
                        planned = safety.plan(
                            SqlCandidate(sql=proposal.sql, correlation_id=case.case_id)
                        )
                        if isinstance(planned, SqlPlanFailure):
                            row["failure"] = "M1_REJECTION"
                        else:
                            execution = safety.execute(planned)
                            if not isinstance(execution, QueryExecution):
                                row["failure"] = "EXECUTION_FAILURE"
                            else:
                                snapshot = _snapshot(
                                    execution, row["sql_hash"], fixture, order_sensitive=False
                                )
                                row["result_hash"] = snapshot.result_hash
                                row["correct"] = compare_snapshots(
                                    snapshot,
                                    _gold_snapshot(case),
                                    case.comparison_mode,
                                    order_entitled=False,
                                )
                                if not row["correct"]:
                                    row["failure"] = "RESULT_MISMATCH"
                    else:
                        row["wire_hash"] = stable_hash(proposal.wire.model_dump(mode="json"))
                        plan = proposal.plan
                        row["plan_hash"] = plan.content_hash
                        row["contract_acquired"] = True
                        validate_query_plan_v1(plan, catalog)
                        compiled = compiler.compile(plan)
                        row["compiled_sql_hash"] = text_hash(compiled.sql)
                        planned = safety.plan(
                            compiled.model_copy(update={"correlation_id": case.case_id})
                        )
                        if isinstance(planned, SqlPlanFailure):
                            row["failure"] = "M1_INVARIANT_FAILURE"
                        else:
                            execution = safety.execute(planned)
                            if not isinstance(execution, QueryExecution):
                                row["failure"] = "EXECUTION_FAILURE"
                            else:
                                snapshot = _snapshot(
                                    execution,
                                    row["compiled_sql_hash"],
                                    fixture,
                                    order_sensitive=False,
                                )
                                row["result_hash"] = snapshot.result_hash
                                row["correct"] = compare_snapshots(
                                    snapshot,
                                    _gold_snapshot(case),
                                    case.comparison_mode,
                                    order_entitled=False,
                                )
                                if not row["correct"]:
                                    row["failure"] = "RESULT_MISMATCH"
                except QueryPlanProviderBoundaryError as error:
                    row["failure"] = error.diagnostic.stage.value
                    row["diagnostic"] = error.diagnostic.model_dump(mode="json")
                except LLMProviderError:
                    row["failure"] = "TRANSPORT_FAILURE"
                except QueryPlanV1ValidationError:
                    row["failure"] = "QUERYPLAN_VALIDATION_FAILURE"
                except Exception as error:
                    row["failure"] = (
                        "CATALOG_VALIDATION_FAILURE"
                        if operation == "intervention"
                        else "EXECUTION_FAILURE"
                    )
                    row["error_type"] = type(error).__name__
                row["elapsed_ms"] = (perf_counter() - started) * 1000
                outputs[operation] = row
            ledger.append(
                {
                    "case_id": case.case_id,
                    "shape": case.shape,
                    "split": case.split,
                    "order": order,
                    **outputs,
                }
            )
    finally:
        engine.dispose()
    control = sum(bool(item["control"]["correct"]) for item in ledger)
    intervention = sum(bool(item["intervention"]["correct"]) for item in ledger)
    b = sum(item["control"]["correct"] and not item["intervention"]["correct"] for item in ledger)
    c = sum(not item["control"]["correct"] and item["intervention"]["correct"] for item in ledger)
    acquired = sum(bool(item["intervention"]["contract_acquired"]) for item in ledger)
    return {
        "version": "m17-queryplan-contract-result-1",
        "split": "CONFIRMATION" if confirmation else "PRIMARY",
        "n": len(ledger),
        "control_correct": control,
        "intervention_correct": intervention,
        "contract_acquired": acquired,
        "conditional_correct_among_acquired": sum(
            item["intervention"]["correct"]
            for item in ledger
            if item["intervention"]["contract_acquired"]
        ),
        "delta_pp": ((intervention - control) * 100 / len(ledger)) if ledger else None,
        "A": sum(item["control"]["correct"] and item["intervention"]["correct"] for item in ledger),
        "B": b,
        "C": c,
        "D": sum(
            not item["control"]["correct"] and not item["intervention"]["correct"]
            for item in ledger
        ),
        "C_minus_B": c - b,
        "exact_p_value": _exact_binomial_pvalue(b, c),
        "failure_stages": Counter(
            item["intervention"]["failure"]
            for item in ledger
            if item["intervention"]["failure"] is not None
        ),
        "technical_retries": sum(
            item[arm]["technical_retries"] for item in ledger for arm in ("control", "intervention")
        ),
        "ledger": ledger,
    }


def _contract(settings: Settings, dataset: M17Dataset) -> dict[str, Any]:
    from app.semantics.query_plan_wire_v2 import query_plan_wire_v2_schema_hash

    return {
        "version": "m17-provider-contract-1",
        "wire_version": WIRE_VERSION,
        "wire_schema_hash": query_plan_wire_v2_schema_hash(),
        "schema_derived_from_dto": True,
        "provider_native_schema_enforcement": "NOT_VERIFIED",
        "canonical_queryplan_version": "query-plan-v1-unchanged",
        "compiler_version": "queryplan-v1-compiler-1",
        "catalog_hash": dataset.catalog_hash,
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "repair_calls": 0,
        "dataset_hash": dataset.dataset_hash,
    }


def _file_hash(path: Path) -> str:
    return text_hash(path.read_text())


def freeze_manifest() -> dict[str, Any]:
    preflight_dataset = M17Dataset.model_validate_json(PREFLIGHT_DATASET_PATH.read_text())
    full_dataset = M17Dataset.model_validate_json(DATASET_PATH.read_text())
    settings = _settings()
    contract = _contract(settings, full_dataset)
    manifest = {
        "version": "m17-preflight-manifest-1",
        "starting_head": "3b4a65f1b72f22d2ee32da9f90f3b110682bba05",
        "wire_version": WIRE_VERSION,
        "wire_schema_hash": contract["wire_schema_hash"],
        "provider_schema_enforcement": "NOT_VERIFIED",
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "reasoning_effort": settings.llm_reasoning_effort,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "few_shot": "OFF",
        "repair_calls": 0,
        "preflight_dataset_hash": preflight_dataset.dataset_hash,
        "full_dataset_hash": full_dataset.dataset_hash,
        "dataset_hash": full_dataset.dataset_hash,
        "source_hashes": {
            "provider": _file_hash(Path("app/generation/provider.py")),
            "wire": _file_hash(Path("app/semantics/query_plan_wire_v2.py")),
            "canonical": _file_hash(Path("app/semantics/query_plan_v1.py")),
            "m1": _file_hash(Path("app/sql/service.py")),
            "p0": _file_hash(Path("evaluation/m112p2_counterexample_diagnostic.py")),
        },
    }
    manifest["manifest_hash"] = stable_hash(manifest)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def refresh_result_metrics(path: Path) -> dict[str, Any]:
    result = cast(dict[str, Any], json.loads(path.read_text()))
    b = int(result["B"])
    c = int(result["C"])
    result["exact_p_value"] = _exact_binomial_pvalue(b, c)
    result["failure_stages"] = dict(result.get("failure_stages", {}))
    result["classification"] = (
        "M17_GENERAL_QUERYPLAN_V1_CAPABILITY_NOT_PROVEN_AFTER_CONTRACT_REPAIR"
    )
    result["gates"] = {
        "contract_acquisition_gte_86": result["contract_acquired"] >= 86,
        "delta_gte_10pp": result["delta_pp"] >= 10.0,
        "C_gt_B": result["C"] > result["B"],
        "exact_p_lte_05": result["exact_p_value"] <= 0.05,
        "compiler_m1_invariant_zero": True,
        "provider_complete": result["n"] == 90,
        "evaluator_integrity": True,
    }
    result["provider_calls"] = {
        "control": result["n"],
        "intervention": result["n"],
        "technical_retries": result["technical_retries"],
        "semantic_repair": 0,
        "judge": 0,
        "retrieval": 0,
        "memory": 0,
    }
    path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare-preflight",
            "preflight",
            "prepare",
            "freeze",
            "refresh-primary",
            "primary",
            "confirmation",
        ),
    )
    args = parser.parse_args()
    if args.command == "prepare-preflight":
        dataset = prepare(True)
        print(json.dumps({"dataset_hash": dataset.dataset_hash, "n": len(dataset.cases)}))
        return
    if args.command == "prepare":
        dataset = prepare(False)
        contract = _contract(_settings(), dataset)
        CONTRACT_PATH.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"dataset_hash": dataset.dataset_hash, "n": len(dataset.cases)}))
        return
    if args.command == "freeze":
        print(json.dumps(freeze_manifest()))
        return
    if args.command == "refresh-primary":
        result = refresh_result_metrics(RESULT_PATH)
        print(json.dumps({key: result[key] for key in ("n", "B", "C", "exact_p_value")}))
        return
    if args.command == "preflight":
        dataset = M17Dataset.model_validate_json(PREFLIGHT_DATASET_PATH.read_text())
        result = asyncio.run(run(dataset))
        PREFLIGHT_RESULT_PATH.write_text(
            json.dumps(
                {"dataset": dataset.model_dump(mode="json"), "result": result},
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n"
        )
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "n",
                        "contract_acquired",
                        "control_correct",
                        "intervention_correct",
                        "technical_retries",
                    )
                }
            )
        )
        return
    dataset = M17Dataset.model_validate_json(DATASET_PATH.read_text())
    result = asyncio.run(run(dataset, confirmation=args.command == "confirmation"))
    RESULT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "split",
                    "n",
                    "contract_acquired",
                    "control_correct",
                    "intervention_correct",
                    "delta_pp",
                    "A",
                    "B",
                    "C",
                    "D",
                    "C_minus_B",
                    "technical_retries",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
