"""Frozen M9.2 regression corpus and side-by-side contract evaluation.

The historical rows in this suite are compact oracle-bound fixtures derived
from M9/M9.1 metadata.  They are not a benchmark rescore and contain no raw
database results.  The comparator is intentionally independent of case IDs,
questions, SQL text, and expected values.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from evaluation.result_equivalence_contract import (
    CANDIDATE_VERSION,
    CONTRACT_VERSION,
    BoundResult,
    DuplicatePolicy,
    ExtraOutputPolicy,
    Outcome,
    ReasonCode,
    ResultEquivalenceContract,
    ResultSlot,
    RowOrderPolicy,
    ScalarOrTabular,
    SlotKind,
    compare_contract,
    strict_compare,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evaluation" / "fixtures" / "m92_equivalence_contracts.json"
M91_SHAPE = ROOT / "evaluation" / "results" / "m91" / "audit-20260904-v2" / "shape_audit.jsonl"
M9_MANIFEST = (
    ROOT / "evaluation" / "results" / "m9" / "audit-20260904" / "direct_path_manifest.json"
)

CORPUS_ID = "decisionsql-m92-result-equivalence-regression"
CORPUS_VERSION = "m92-equivalence-contract-v1"
M92_REASONS = tuple(reason.value for reason in ReasonCode)

M7_HASHES = {
    "full": "f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043",
    "dev": "59bd32b94939b4f1c2d77ab79cc9feaf4622358009073f9c16f7108eee3a014f",
    "holdout": "740ab83e8700877e3bbe7bee6bcd61611a03d92755231dafebb7270d762be0e5",
}
M8_HASH = "e42d02cd04e31725eb38cf9da235787a410c28b3bd3b2397b35323754a1b319c"
M9_HASHES = {
    "full": "6bd0e22b105607a0af8d17ff8774bba381ba61b4e43cdd88be2f10acc6e36d12",
    "dev": "cc61d0649b0e24c4042b24ae6e70eeb2bd4c4a970f252fd02af03e760d6f16d3",
    "holdout": "313c3d02e525572ac0fff70a9cc55968e9d2483de0e7821ad7d82d292508c696",
    "incorrect": "eadcc297635152999ca6478a74b1b24a67b23b8424630e6e081a3ba7e7c67180",
}
M91_HASH = "7f838d8af24c62d89d37367c41efe1b404fbcb1c2f75ffa01419bd33162e74ac"
M3_HASH = "0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c"
M4_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    source: str
    expected_label: str
    v1_accept: bool
    contract: ResultEquivalenceContract
    generated: BoundResult
    references: tuple[BoundResult, ...]
    split: str = "synthetic"
    family: str = "SYNTHETIC"
    optional_group: str | None = None


def _load_fixture() -> dict[str, Any]:
    value = json.loads(FIXTURE.read_text())
    if not isinstance(value, dict):
        raise ValueError("M9.2 fixture root must be an object")
    return value


def fixture_hash() -> str:
    canonical = json.dumps(_load_fixture(), sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def stable_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode()).hexdigest()


def _slot(slot_id: str, kind: SlotKind = SlotKind.DIMENSION, required: bool = True) -> ResultSlot:
    return ResultSlot(slot_id, kind, required, "output", slot_id, False)


def _contract(
    required: tuple[str, ...] = ("region", "revenue"),
    optional: tuple[str, ...] = (),
    *,
    scalar: ScalarOrTabular = ScalarOrTabular.TABULAR,
    order: RowOrderPolicy = RowOrderPolicy.ORDER_INSENSITIVE,
    row_identity: tuple[str, ...] = ("region",),
) -> ResultEquivalenceContract:
    slots = tuple(
        _slot(name, SlotKind.MEASURE if name.endswith("revenue") else SlotKind.DIMENSION)
        for name in required
    )
    slots += tuple(_slot(name, SlotKind.AUXILIARY, False) for name in optional)
    return ResultEquivalenceContract(
        CONTRACT_VERSION,
        slots,
        row_identity,
        tuple(name for name in required if name.endswith("revenue")),
        scalar,
        order,
        DuplicatePolicy.PRESERVE,
        ExtraOutputPolicy.ALLOW_DECLARED_OPTIONAL_ONLY
        if optional
        else ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
    )


def _result(
    columns: tuple[str, ...],
    bindings: tuple[str | None, ...],
    rows: tuple[tuple[Any, ...], ...],
    scalar: ScalarOrTabular = ScalarOrTabular.TABULAR,
) -> BoundResult:
    return BoundResult(columns, bindings, rows, scalar)


def _synthetic(case_id: str) -> RegressionCase:
    base = _contract()
    ref = _result(("region", "revenue"), ("region", "revenue"), (("North", 100.0), ("South", 80.0)))
    same = _result(
        ("region", "revenue"), ("region", "revenue"), (("North", 100.0), ("South", 80.0))
    )
    specs: dict[
        str, tuple[BoundResult, ResultEquivalenceContract, tuple[BoundResult, ...], bool]
    ] = {
        "A01_DECLARED_OPTIONAL_ID": (
            _result(
                ("region", "revenue", "region_id"),
                ("region", "revenue", "region_id"),
                (("North", 100.0, 1), ("South", 80.0, 2)),
            ),
            _contract(optional=("region_id",)),
            (ref,),
            False,
        ),
        "A02_UNDECLARED_BUSINESS": (
            _result(
                ("region", "revenue", "manager"),
                ("region", "revenue", "manager"),
                (("North", 100.0, "A"), ("South", 80.0, "B")),
            ),
            base,
            (ref,),
            False,
        ),
        "A03_MISSING_DIMENSION": (
            _result(("revenue",), ("revenue",), ((100.0,), (80.0,))),
            base,
            (ref,),
            False,
        ),
        "A04_MISSING_MEASURE": (
            _result(("region",), ("region",), (("North",), ("South",))),
            base,
            (ref,),
            False,
        ),
        "A05_WRONG_MEASURE_VALUE": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("North", 101.0), ("South", 80.0))
            ),
            base,
            (ref,),
            False,
        ),
        "A06_WRONG_DIMENSION_VALUE": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("West", 100.0), ("South", 80.0))
            ),
            base,
            (ref,),
            False,
        ),
        "A07_EXTRA_GROUPING_KEY": (
            _result(
                ("region", "revenue", "rep"),
                ("region", "revenue", "rep"),
                (("North", 60.0, "A"), ("North", 40.0, "B"), ("South", 80.0, "C")),
            ),
            base,
            (ref,),
            False,
        ),
        "A08_EXTRA_KEY_DUPLICATES": (
            _result(
                ("region", "revenue", "rep"),
                ("region", "revenue", "rep"),
                (("North", 100.0, "A"), ("North", 100.0, "B"), ("South", 80.0, "C")),
            ),
            base,
            (ref,),
            False,
        ),
        "A09_DUPLICATE_ROW": (
            _result(
                ("region", "revenue"),
                ("region", "revenue"),
                (("North", 100.0), ("North", 100.0), ("South", 80.0)),
            ),
            base,
            (ref,),
            False,
        ),
        "A10_MISSING_DUPLICATE": (
            _result(("region", "revenue"), ("region", "revenue"), (("North", 100.0),)),
            base,
            (ref,),
            False,
        ),
        "A11_SCALAR_GROUPED": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("North", 100.0), ("South", 80.0))
            ),
            _contract(required=("revenue",), scalar=ScalarOrTabular.SCALAR, row_identity=()),
            (_result(("revenue",), ("revenue",), ((180.0,),), ScalarOrTabular.SCALAR),),
            False,
        ),
        "A12_GROUPED_SCALAR": (
            _result(("revenue",), ("revenue",), ((180.0,),), ScalarOrTabular.SCALAR),
            base,
            (ref,),
            False,
        ),
        "A13_REQUIRED_ORDER_WRONG": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("South", 80.0), ("North", 100.0))
            ),
            _contract(order=RowOrderPolicy.ORDER_REQUIRED),
            (ref,),
            False,
        ),
        "A14_INSENSITIVE_ROW_ORDER": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("South", 80.0), ("North", 100.0))
            ),
            base,
            (ref,),
            True,
        ),
        "A15_ALIAS_ONLY": (
            _result(("area", "total"), ("region", "revenue"), (("North", 100.0), ("South", 80.0))),
            base,
            (ref,),
            True,
        ),
        "A16_COLUMN_ORDER_ONLY": (
            _result(
                ("revenue", "region"), ("revenue", "region"), ((100.0, "North"), (80.0, "South"))
            ),
            base,
            (ref,),
            True,
        ),
        "A17_NULL_EQUIVALENCE": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("North", None), ("South", 80.0))
            ),
            base,
            (
                _result(
                    ("region", "revenue"), ("region", "revenue"), (("North", None), ("South", 80.0))
                ),
            ),
            True,
        ),
        "A18_NUMERIC_TOLERANCE": (
            _result(
                ("region", "revenue"),
                ("region", "revenue"),
                (("North", 100.0000005), ("South", 80.0)),
            ),
            base,
            (ref,),
            True,
        ),
        "A19_NUMERIC_OUTSIDE_TOLERANCE": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("North", 100.01), ("South", 80.0))
            ),
            base,
            (ref,),
            False,
        ),
        "A20_OPTIONAL_APPENDED": (
            _result(
                ("region", "revenue", "region_id"),
                ("region", "revenue", "region_id"),
                (("North", 100.0, 1), ("South", 80.0, 2)),
            ),
            _contract(optional=("region_id",)),
            (ref,),
            True,
        ),
        "A21_OPTIONAL_INSERTED": (
            _result(
                ("region", "region_id", "revenue"),
                ("region", "region_id", "revenue"),
                (("North", 1, 100.0), ("South", 2, 80.0)),
            ),
            _contract(optional=("region_id",)),
            (ref,),
            True,
        ),
        "A22_ONE_OF_TWO_EXTRAS": (
            _result(
                ("region", "revenue", "region_id", "manager"),
                ("region", "revenue", "region_id", "manager"),
                (("North", 100.0, 1, "A"), ("South", 80.0, 2, "B")),
            ),
            _contract(optional=("region_id",)),
            (ref,),
            False,
        ),
        "A23_OPTIONAL_VALUE_DIFFERS": (
            _result(
                ("region", "revenue", "region_id"),
                ("region", "revenue", "region_id"),
                (("North", 100.0, 99), ("South", 80.0, 98)),
            ),
            _contract(optional=("region_id",)),
            (ref,),
            True,
        ),
        "A24_OPTIONAL_REQUIRES_DEDUP": (
            _result(
                ("region", "revenue", "region_id"),
                ("region", "revenue", "region_id"),
                (("North", 100.0, 1), ("North", 100.0, 2), ("South", 80.0, 3)),
            ),
            _contract(optional=("region_id",)),
            (ref,),
            False,
        ),
        "A25_OPTIONAL_REQUIRES_REAGGREGATION": (
            _result(
                ("region", "revenue", "region_id"),
                ("region", "revenue", "region_id"),
                (("North", 60.0, 1), ("North", 40.0, 2), ("South", 80.0, 3)),
            ),
            _contract(optional=("region_id",)),
            (ref,),
            False,
        ),
        "A26_WRONG_SEMANTIC_SLOT": (
            _result(
                ("region", "revenue"),
                ("region", "wrong_revenue"),
                (("North", 100.0), ("South", 80.0)),
            ),
            base,
            (ref,),
            False,
        ),
        "A27_WRONG_LITERAL_SLOT": (
            _result(
                ("region", "revenue"),
                ("wrong_region", "revenue"),
                (("North", 100.0), ("South", 80.0)),
            ),
            base,
            (ref,),
            False,
        ),
        "A28_REFERENCE_VARIANTS": (
            _result(
                ("region", "revenue"), ("region", "revenue"), (("North", 100.0), ("South", 80.0))
            ),
            base,
            (
                _result(
                    ("region", "revenue"), ("region", "revenue"), (("North", 99.0), ("South", 80.0))
                ),
                ref,
            ),
            True,
        ),
        "A29_NO_REFERENCE_MATCH": (
            same,
            base,
            (_result(("region", "revenue"), ("region", "revenue"), (("East", 1.0),)),),
            False,
        ),
        "A30_AMBIGUOUS_BINDING": (
            _result(
                ("region", "revenue"), ("region", "region"), (("North", 100.0), ("South", 80.0))
            ),
            base,
            (ref,),
            False,
        ),
    }
    generated, contract, references, v1 = specs[case_id]
    accepted_ids = {
        "A01_DECLARED_OPTIONAL_ID",
        "A14_INSENSITIVE_ROW_ORDER",
        "A15_ALIAS_ONLY",
        "A16_COLUMN_ORDER_ONLY",
        "A17_NULL_EQUIVALENCE",
        "A18_NUMERIC_TOLERANCE",
        "A20_OPTIONAL_APPENDED",
        "A21_OPTIONAL_INSERTED",
        "A23_OPTIONAL_VALUE_DIFFERS",
        "A28_REFERENCE_VARIANTS",
    }
    return RegressionCase(
        case_id,
        "SYNTHETIC",
        "SHOULD_ACCEPT" if case_id in accepted_ids else "SHOULD_REJECT",
        v1,
        contract,
        generated,
        references,
    )


def _historical() -> tuple[RegressionCase, ...]:
    fixture = _load_fixture()["historical_groups"]
    m91 = {row["case_id"]: row for row in map(json.loads, M91_SHAPE.read_text().splitlines())}
    m9 = {row["case_id"]: row for row in json.loads(M9_MANIFEST.read_text())["cases"]}
    groups = (
        ("POSITIVE_EQUIVALENT_ARTIFACTS", "HISTORICAL_ARTIFACT", True),
        ("NEGATIVE_PROJECTION_VIOLATIONS", "HISTORICAL_PROJECTION", False),
        ("NEGATIVE_SEMANTIC_ERRORS", "HISTORICAL_SEMANTIC", False),
    )
    cases: list[RegressionCase] = []
    for group, source, positive in groups:
        for case_id in fixture[group]:
            row = m91[case_id]
            required = tuple(dict.fromkeys((*row["required_fields"], *row["required_measures"])))
            optional = tuple(row["extra_optional_fields"]) if positive else ()
            slots = tuple(
                _slot(
                    name,
                    SlotKind.MEASURE if name in row["required_measures"] else SlotKind.DIMENSION,
                )
                for name in required
            )
            slots += tuple(_slot(name, SlotKind.AUXILIARY, False) for name in optional)
            contract = ResultEquivalenceContract(
                CONTRACT_VERSION,
                slots,
                (required[0],) if required else (),
                tuple(row["required_measures"]),
                ScalarOrTabular.TABULAR,
                RowOrderPolicy.ORDER_INSENSITIVE,
                DuplicatePolicy.PRESERVE,
                ExtraOutputPolicy.ALLOW_DECLARED_OPTIONAL_ONLY
                if optional
                else ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
            )
            ref_rows = (tuple(float(index + 1) for index in range(len(required))),)
            reference = _result(
                tuple(f"r{index}" for index in range(len(required))), required, ref_rows
            )
            generated_bindings = list(required)
            generated_columns = [f"g{index}" for index in range(len(required))]
            if positive:
                generated_bindings += list(optional)
                generated_columns += [f"extra{index}" for index in range(len(optional))]
            elif row["detailed_mechanism"] == "P3_EXTRA_OPTIONAL_COLUMN":
                extras = tuple(row["extra_optional_fields"])
                generated_bindings += [f"undeclared:{name}" for name in extras]
                generated_columns += [f"extra{index}" for index in range(len(extras))]
            elif row["detailed_mechanism"] == "P6_MISSING_REQUIRED_MEASURE":
                missing = set(row["missing_required_fields"])
                generated_bindings = [name for name in required if name not in missing]
                generated_columns = [f"g{index}" for index in range(len(generated_bindings))]
            elif row["detailed_mechanism"] in {
                "P8_WRONG_MEASURE_PROJECTION",
                "P12_WRONG_ENTITY_ROW_IDENTITY",
            }:
                generated_bindings[-1:] = ["wrong_semantic_slot"]
            generated_rows = [tuple(float(index + 1) for index in range(len(generated_bindings)))]
            if row["detailed_mechanism"] in {
                "P8_WRONG_MEASURE_PROJECTION",
                "P12_WRONG_ENTITY_ROW_IDENTITY",
            }:
                generated_rows = [tuple([1.0] * len(generated_bindings))]
            generated = _result(
                tuple(generated_columns), tuple(generated_bindings), tuple(generated_rows)
            )
            if row["detailed_mechanism"] == "P12_WRONG_ENTITY_ROW_IDENTITY":
                generated = _result(
                    generated.columns, generated.bindings, generated.rows + generated.rows
                )
            cases.append(
                RegressionCase(
                    case_id,
                    source,
                    "SHOULD_ACCEPT" if positive else "SHOULD_REJECT",
                    False,
                    contract,
                    generated,
                    (reference,),
                    row["split"],
                    row["family"],
                    "IDENTITY_COMPANION"
                    if positive and any("id" in x for x in optional)
                    else "AUXILIARY_OUTPUT"
                    if positive
                    else None,
                )
            )
    controls = fixture["CORRECT_CONTROLS"]
    control_stats = {
        row["case_id"]: row
        for row in json.loads(
            (
                ROOT / "evaluation/results/m91/audit-20260904-v2/correct_control_statistics.json"
            ).read_text()
        )["rows"]
    }
    for case_id in controls:
        row = m9[case_id]
        contract = _contract(required=("result",), row_identity=("result",))
        table = _result(("result",), ("result",), ((1.0,),))
        cases.append(
            RegressionCase(
                case_id,
                "CORRECT_CONTROL",
                "SHOULD_ACCEPT",
                True,
                contract,
                table,
                (table,),
                row["split"],
                row["family"],
                "CONTROL_ALIAS"
                if control_stats.get(case_id, {}).get("alias_or_label_difference")
                else None,
            )
        )
    for case_id in fixture["NON_S11_NEGATIVE_CONTROLS"]:
        row = m9[case_id]
        contract = _contract(required=("result",), row_identity=("result",))
        reference = _result(("result",), ("result",), ((1.0,),))
        generated = _result(("result",), ("result",), ((999.0,),))
        cases.append(
            RegressionCase(
                case_id,
                "NON_S11_NEGATIVE",
                "SHOULD_REJECT",
                False,
                contract,
                generated,
                (reference,),
                row["split"],
                row["family"],
            )
        )
    return tuple(cases)


def build_cases() -> tuple[RegressionCase, ...]:
    return _historical() + tuple(
        _synthetic(item["case_id"]) for item in _load_fixture()["synthetic_cases"]
    )


def validate_corpus(cases: tuple[RegressionCase, ...] | None = None) -> dict[str, Any]:
    cases = cases or build_cases()
    fixture = _load_fixture()
    groups = fixture["historical_groups"]
    expected = {case_id for values in groups.values() for case_id in values}
    actual = {case.case_id for case in cases if case.source != "SYNTHETIC"}
    return {
        "passed": len(cases) == 143
        and len(actual) == 113
        and len(actual) == len([case for case in cases if case.source != "SYNTHETIC"]),
        "total_cases": len(cases),
        "historical_cases": len(actual),
        "synthetic_cases": sum(case.source == "SYNTHETIC" for case in cases),
        "expected_historical_case_ids": len(expected),
        "unique_case_ids": len({case.case_id for case in cases}) == len(cases),
        "group_counts": {key: len(value) for key, value in groups.items()},
        "fixture_hash": fixture_hash(),
    }


def evaluate(cases: tuple[RegressionCase, ...] | None = None) -> dict[str, Any]:
    cases = cases or build_cases()
    rows: list[dict[str, Any]] = []
    for case in cases:
        computed_v1 = strict_compare(
            case.generated, case.references[0], case.contract.row_order_policy
        )
        candidate = compare_contract(case.generated, case.references, case.contract)
        rows.append(
            {
                "case_id": case.case_id,
                "source": case.source,
                "split": case.split,
                "family": case.family,
                "expected": case.expected_label,
                "v1_accept": case.v1_accept,
                "computed_v1_accept": computed_v1,
                "candidate_outcome": candidate.outcome.value,
                "candidate_reason": candidate.reason.value,
                "candidate_accept": candidate.outcome
                in (Outcome.STRICT_EQUIVALENT, Outcome.CONTRACT_EQUIVALENT),
                "optional_group": case.optional_group,
            }
        )
    return {"rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    historical = [row for row in rows if row["source"] != "SYNTHETIC"]
    artifacts = [row for row in historical if row["source"] == "HISTORICAL_ARTIFACT"]
    projections = [row for row in historical if row["source"] == "HISTORICAL_PROJECTION"]
    semantic = [row for row in historical if row["source"] == "HISTORICAL_SEMANTIC"]
    controls = [row for row in historical if row["source"] == "CORRECT_CONTROL"]
    nons11 = [row for row in historical if row["source"] == "NON_S11_NEGATIVE"]
    synthetic = [row for row in rows if row["source"] == "SYNTHETIC"]

    def accepted(values: list[dict[str, Any]]) -> int:
        return sum(row["candidate_accept"] for row in values)

    matrix = Counter(
        ("V1_ACCEPT" if row["v1_accept"] else "V1_REJECT")
        + " / "
        + ("CANDIDATE_ACCEPT" if row["candidate_accept"] else "CANDIDATE_REJECT")
        for row in rows
    )
    return {
        "total": len(rows),
        "historical": len(historical),
        "artifacts": {"N": len(artifacts), "recovered": accepted(artifacts)},
        "projection_negatives": {"N": len(projections), "false_accepts": accepted(projections)},
        "semantic_negatives": {"N": len(semantic), "false_accepts": accepted(semantic)},
        "correct_controls": {"N": len(controls), "accepted": accepted(controls)},
        "non_s11_negatives": {"N": len(nons11), "false_accepts": accepted(nons11)},
        "synthetic": {
            "N": len(synthetic),
            "accepted_as_expected": sum(
                row["candidate_accept"] == (row["expected"] == "SHOULD_ACCEPT") for row in synthetic
            ),
            "failures": sum(
                row["candidate_accept"] != (row["expected"] == "SHOULD_ACCEPT") for row in synthetic
            ),
        },
        "matrix": dict(matrix),
        "outcomes": dict(Counter(row["candidate_outcome"] for row in rows)),
        "reasons": dict(Counter(row["candidate_reason"] for row in rows)),
        "deterministic": True,
    }


def final_decision(summary: dict[str, Any]) -> dict[str, Any]:
    gates = {
        "artifact_10_of_10": summary["artifacts"]["recovered"] == 10,
        "projection_zero_false_accept": summary["projection_negatives"]["false_accepts"] == 0,
        "semantic_zero_false_accept": summary["semantic_negatives"]["false_accepts"] == 0,
        "controls_75_of_75": summary["correct_controls"]["accepted"] == 75,
        "non_s11_zero_false_accept": summary["non_s11_negatives"]["false_accepts"] == 0,
        "synthetic_100_percent": summary["synthetic"]["failures"] == 0,
        "duplicate_grain_missing_value_safety": summary["synthetic"]["failures"] == 0,
        "deterministic_repeated_run": summary["deterministic"],
    }
    safety = all(value for key, value in gates.items() if key != "artifact_10_of_10")
    classification = (
        "RESULT_EQUIVALENCE_CONTRACT_ACCEPTED"
        if all(gates.values())
        else "RESULT_EQUIVALENCE_CONTRACT_INSUFFICIENT"
        if safety
        else "RESULT_EQUIVALENCE_HARDENING_TOO_RISKY"
    )
    return {
        "classification": classification,
        "gates": gates,
        "safety_gate_passed": safety,
        "contract_version": CONTRACT_VERSION,
        "candidate_version": CANDIDATE_VERSION,
        "next_milestone": "M9.3 — Versioned Result Equivalence V2 Independent Validation"
        if classification == "RESULT_EQUIVALENCE_CONTRACT_ACCEPTED"
        else "No evaluator replacement milestone until semantic safety is resolved",
    }


def run() -> dict[str, Any]:
    cases = build_cases()
    validation = validate_corpus(cases)
    if not validation["passed"] or any(validate_contract(case.contract) for case in cases):
        raise ValueError("M9.2 frozen corpus or contract validation failed")
    first = evaluate(cases)
    second = evaluate(cases)
    if first != second:
        raise ValueError("M9.2 candidate is not deterministic")
    decision = final_decision(first["summary"])
    return {
        "metadata": {
            "milestone": "M9.2",
            "corpus_id": CORPUS_ID,
            "version": CORPUS_VERSION,
            "contract_version": CONTRACT_VERSION,
            "candidate_version": CANDIDATE_VERSION,
            "fixture_hash": fixture_hash(),
            "provider_calls": 0,
            "sql_generation_calls": 0,
            "repair_calls": 0,
            "llm_judge_calls": 0,
            "embedding_calls": 0,
            "reranker_calls": 0,
            "db_mutations": 0,
            "read_only_db_executions": 0,
            "m7_hashes": M7_HASHES,
            "m8_hash": M8_HASH,
            "m9_hashes": M9_HASHES,
            "m91_hash": M91_HASH,
            "m3_contract_hash": M3_HASH,
            "m4_corpus_hash": M4_HASH,
            "historical_result_rows": "compact oracle-bound fixtures; no raw persisted M9.1 rows",
        },
        "validation": validation,
        "evaluation": first,
        "decision": decision,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
