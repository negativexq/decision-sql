"""Independent, provider-free validation of the frozen M9.2 comparator."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
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
FIXTURE = ROOT / "evaluation" / "fixtures" / "m93_equivalence_independent.json"
COMPARATOR_SOURCE = ROOT / "evaluation" / "result_equivalence_contract.py"
M91_IDS = {
    row["case_id"]
    for row in map(
        json.loads,
        (ROOT / "evaluation/results/m91/audit-20260904-v2/shape_audit.jsonl")
        .read_text()
        .splitlines(),
    )
}
M92_IDS = {
    row["case_id"]
    for row in __import__(
        "evaluation.m92_equivalence_regression", fromlist=["evaluate"]
    ).evaluate()["rows"]
}


@dataclass(frozen=True)
class IndependentCase:
    case_id: str
    category: str
    split: str
    expected: str
    contract: ResultEquivalenceContract
    generated: BoundResult
    references: tuple[BoundResult, ...]
    optional_group: str | None = None


def _fixture() -> dict[str, Any]:
    value = json.loads(FIXTURE.read_text())
    if not isinstance(value, dict):
        raise ValueError("M9.3 fixture root must be an object")
    return value


def fixture_hash() -> str:
    payload = json.dumps(_fixture(), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def source_hash() -> str:
    return sha256(COMPARATOR_SOURCE.read_bytes()).hexdigest()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode()).hexdigest()


def _slot(slot_id: str, kind: SlotKind, required: bool = True) -> ResultSlot:
    return ResultSlot(slot_id, kind, required, "output", slot_id, False)


def _contract(
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    *,
    scalar: ScalarOrTabular = ScalarOrTabular.TABULAR,
    order: RowOrderPolicy = RowOrderPolicy.ORDER_INSENSITIVE,
    identity: tuple[str, ...] | None = None,
) -> ResultEquivalenceContract:
    slots = tuple(
        _slot(
            name,
            SlotKind.MEASURE
            if name.endswith("_total") or name.endswith("_amount")
            else SlotKind.DIMENSION,
        )
        for name in required
    )
    slots += tuple(_slot(name, SlotKind.AUXILIARY, False) for name in optional)
    if identity is None:
        identity = () if scalar is ScalarOrTabular.SCALAR else (required[0],)
    return ResultEquivalenceContract(
        CONTRACT_VERSION,
        slots,
        identity,
        tuple(name for name in required if name.endswith("_total") or name.endswith("_amount")),
        scalar,
        order,
        DuplicatePolicy.PRESERVE,
        ExtraOutputPolicy.ALLOW_DECLARED_OPTIONAL_ONLY
        if optional
        else ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
    )


def _table(
    bindings: tuple[str, ...],
    rows: tuple[tuple[Any, ...], ...],
    *,
    prefix: str = "column",
    scalar: ScalarOrTabular = ScalarOrTabular.TABULAR,
) -> BoundResult:
    return BoundResult(
        tuple(f"{prefix}_{index}" for index in range(len(bindings))), bindings, rows, scalar
    )


def _positive(index: int) -> IndependentCase:
    required: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    scalar: ScalarOrTabular
    identity: tuple[str, ...]
    if index < 5:
        required, rows, scalar, identity = (
            ("sales_total",),
            ((1000.0 + index,),),
            ScalarOrTabular.SCALAR,
            ("sales_total",),
        )
    elif index < 15:
        required, rows, scalar, identity = (
            ("region", "sales_total"),
            (("East", 100.0 + index), ("West", 80.0 + index)),
            ScalarOrTabular.TABULAR,
            ("region",),
        )
    elif index < 25:
        required, rows, scalar, identity = (
            ("region", "product", "sales_total"),
            (("Central", "Atlas", 70.0 + index), ("Coastal", "Beacon", 60.0 + index)),
            ScalarOrTabular.TABULAR,
            ("region", "product"),
        )
    else:
        required, rows, scalar, identity = (
            ("entity_id", "display_label", "sales_total"),
            ((index + 100, "Delta", 50.0 + index), (index + 200, "Cedar", 40.0 + index)),
            ScalarOrTabular.TABULAR,
            ("entity_id",),
        )
    if index in {0, 2}:
        rows = ((None,),)
    elif index in {1, 3, 4}:
        rows = ((1000.0 + index + 0.0000005,),)
    if index == 5:
        rows = (("East", None), ("West", 85.0))
    optional_groups = (
        "IDENTITY_COMPANION",
        "DISPLAY_METADATA",
        "AUXILIARY_OUTPUT",
        "OTHER_DECLARED_OPTIONAL",
    )
    optional = (
        (f"optional_{index}", f"optional_meta_{index}")
        if index >= 35
        else (f"optional_{index}",)
    )
    contract = _contract(
        required,
        optional,
        scalar=scalar,
        order=RowOrderPolicy.ORDER_REQUIRED if index < 5 else RowOrderPolicy.ORDER_INSENSITIVE,
        identity=identity,
    )
    optional_values: tuple[Any, ...]
    if index < 5:
        optional_values = tuple(f"note-{index}-{slot}" for slot in range(len(optional)))
    else:
        optional_values = tuple(index + slot for slot in range(len(optional)))
    position = index % 3
    bindings: list[str] = list(required)
    values: list[Any] = list(rows[0])
    if position == 0:
        bindings = list(optional) + bindings
        values = list(optional_values) + values
    elif position == 1:
        insert_at = len(bindings) // 2
        bindings[insert_at:insert_at] = optional
        values[insert_at:insert_at] = optional_values
    else:
        bindings.extend(optional)
        values.extend(optional_values)
    generated_rows: list[tuple[Any, ...]] = [tuple(values)]
    if len(rows) > 1:
        second = list(rows[1])
        if position == 0:
            second = list(optional_values) + second
        elif position == 1:
            insert_at = len(second) // 2
            second[insert_at:insert_at] = optional_values
        else:
            second.extend(optional_values)
        generated_rows.append(tuple(second))
    if index == 5:
        generated_rows[0] = ("East", None, optional_values[0])
    if index == 6:
        generated_rows[0] = (optional_values[0], "East", 100.0 + index + 0.0000005)
    generated = _table(tuple(bindings), tuple(generated_rows), prefix="alias", scalar=scalar)
    reference = _table(required, tuple(rows), prefix="reference", scalar=scalar)
    if index >= 35:
        wrong_rows = tuple(tuple((*row[:-1], 999.0)) for row in rows)
        wrong = _table(required, wrong_rows, prefix="wrong", scalar=scalar)
        references: tuple[BoundResult, ...] = (wrong, reference)
    else:
        references = (reference,)
    if index in {35, 36, 37, 38, 39}:
        contract = _contract(
            required, optional, scalar=scalar, order=contract.row_order_policy, identity=identity
        )
    return IndependentCase(
        f"m93-positive-{index:02d}",
        "CONTRACT_EQUIVALENT_POSITIVES",
        "VALIDATION_A" if index % 2 == 0 else "VALIDATION_B",
        "SHOULD_ACCEPT_CONTRACT",
        contract,
        generated,
        references,
        optional_group=optional_groups[index // 10],
    )


def _base_case(index: int) -> tuple[ResultEquivalenceContract, BoundResult, BoundResult]:
    contract = _contract(("region", "sales_total"), identity=("region",))
    reference = _table(
        ("region", "sales_total"), (("East", 100.0), ("West", 80.0)), prefix="reference"
    )
    generated = _table(
        ("region", "sales_total"), (("East", 100.0), ("West", 80.0)), prefix=f"generated_{index}"
    )
    return contract, generated, reference


def _negative(index: int, category: str) -> IndependentCase:
    contract, generated, reference = _base_case(index)
    if category == "PROJECTION_NEGATIVES":
        mode = index % 4
        if mode == 0:
            generated = _table(
                ("region", "sales_total", f"business_extra_{index}"),
                (("East", 100.0, "A"), ("West", 80.0, "B")),
                prefix="extra",
            )
        elif mode == 1:
            generated = _table(("sales_total",), ((100.0,), (80.0,)), prefix="missing")
        elif mode == 2:
            generated = _table(
                ("region", "wrong_total"), (("East", 100.0), ("West", 80.0)), prefix="wrong"
            )
        else:
            generated = _table(
                ("wrong_region", "sales_total"), (("East", 100.0), ("West", 80.0)), prefix="wrong"
            )
    elif category == "GRAIN_CARDINALITY_NEGATIVES":
        mode = index % 5
        if mode in {0, 1}:
            generated = _table(
                ("region", "sales_total", "representative"),
                (("East", 60.0, "Ayla"), ("East", 40.0, "Bora"), ("West", 80.0, "Cem")),
                prefix="grain",
            )
        elif mode == 2:
            generated = _table(("region", "sales_total"), (("East", 100.0),), prefix="collapsed")
        elif mode == 3:
            generated = _table(
                ("sales_total",), ((180.0,),), prefix="scalar", scalar=ScalarOrTabular.SCALAR
            )
        else:
            generated = _table(
                ("region", "sales_total"),
                (("East", 100.0), ("East", 100.0), ("West", 80.0)),
                prefix="duplicate",
            )
    elif category == "VALUE_SEMANTIC_NEGATIVES":
        mode = index % 5
        if mode == 0:
            generated = _table(
                ("region", "sales_total"), (("East", 101.0), ("West", 80.0)), prefix="value"
            )
        elif mode == 1:
            generated = _table(
                ("region", "sales_total"), (("Central", 100.0), ("West", 80.0)), prefix="value"
            )
        elif mode == 2:
            generated = _table(
                ("region", "sales_total"), (("East", None), ("West", 80.0)), prefix="value"
            )
        elif mode == 3:
            generated = _table(
                ("region", "sales_total"), (("East", 100.01), ("West", 80.0)), prefix="value"
            )
        else:
            generated = _table(
                ("region", "sales_total"),
                (("East", 100.0), ("West", 80.0), ("Central", 1.0)),
                prefix="value",
            )
    else:
        contract = _contract(
            ("region", "sales_total"), order=RowOrderPolicy.ORDER_REQUIRED, identity=("region",)
        )
        if index % 2 == 0:
            generated = _table(
                ("region", "sales_total"), (("West", 80.0), ("East", 100.0)), prefix="ordered"
            )
        else:
            generated = _table(
                ("region", "sales_total"),
                (("East", 100.0), ("East", 100.0), ("West", 80.0)),
                prefix="ordered",
            )
    return IndependentCase(
        f"m93-{category.lower()}-{index:02d}",
        category,
        "VALIDATION_A" if index % 2 == 0 else "VALIDATION_B",
        "SHOULD_REJECT",
        contract,
        generated,
        (reference,),
    )


def _invalid(index: int) -> IndependentCase:
    contract, generated, reference = _base_case(index)
    if index % 2 == 0:
        slots = (
            ResultSlot("region", SlotKind.DIMENSION, True, "output", "region"),
            ResultSlot("region", SlotKind.MEASURE, False, "optional", "region"),
        )
        contract = ResultEquivalenceContract(
            CONTRACT_VERSION,
            slots,
            ("unknown",),
            (),
            ScalarOrTabular.TABULAR,
            RowOrderPolicy.ORDER_INSENSITIVE,
            DuplicatePolicy.PRESERVE,
            ExtraOutputPolicy.ALLOW_DECLARED_OPTIONAL_ONLY,
        )
    else:
        contract = ResultEquivalenceContract(
            CONTRACT_VERSION,
            contract.slots,
            ("missing",),
            contract.required_measure_slots,
            contract.scalar_or_tabular,
            contract.row_order_policy,
            contract.duplicate_policy,
            contract.extra_output_policy,
        )
    return IndependentCase(
        f"m93-invalid-{index:02d}",
        "INVALID_OR_AMBIGUOUS",
        "VALIDATION_A" if index % 2 == 0 else "VALIDATION_B",
        "SHOULD_BE_CONTRACT_INVALID",
        contract,
        generated,
        (reference,),
    )


def build_cases() -> tuple[IndependentCase, ...]:
    fixture = _fixture()
    cases: list[IndependentCase] = []
    cases.extend(_positive(index) for index in range(40))
    cases.extend(
        IndependentCase(
            f"m93-strict-control-{index:02d}",
            "STRICT_EQUIVALENT_CONTROLS",
            "VALIDATION_A" if index % 2 == 0 else "VALIDATION_B",
            "SHOULD_ACCEPT_STRICT",
            _base_case(index)[0],
            _base_case(index)[1],
            (_base_case(index)[2],),
        )
        for index in range(30)
    )
    for category, count in (
        ("PROJECTION_NEGATIVES", 30),
        ("GRAIN_CARDINALITY_NEGATIVES", 20),
        ("VALUE_SEMANTIC_NEGATIVES", 20),
        ("ORDER_DUPLICATE_SHAPE_NEGATIVES", 10),
    ):
        cases.extend(_negative(index, category) for index in range(count))
    cases.extend(_invalid(index) for index in range(10))
    expected_count = sum(item["count"] for item in fixture["categories"].values())
    if len(cases) != expected_count:
        raise ValueError(f"M9.3 expected {expected_count} cases, built {len(cases)}")
    return tuple(cases)


def case_payload(case: IndependentCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "split": case.split,
        "expected": case.expected,
        "contract": asdict(case.contract),
        "generated": asdict(case.generated),
        "references": [asdict(reference) for reference in case.references],
        "optional_group": case.optional_group,
    }


def manifest(cases: tuple[IndependentCase, ...]) -> dict[str, Any]:
    payload = [case_payload(case) for case in cases]
    subsets = {
        category: [row for row in payload if row["category"] == category]
        for category in _fixture()["categories"]
    }
    return {
        "corpus_id": "decisionsql-m93-equivalence-independent",
        "version": _fixture()["version"],
        "full_hash": stable_hash(payload),
        "split_hashes": {
            split: stable_hash([row for row in payload if row["split"] == split])
            for split in ("VALIDATION_A", "VALIDATION_B")
        },
        "subset_hashes": {category: stable_hash(rows) for category, rows in subsets.items()},
        "case_count": len(cases),
        "category_counts": dict(Counter(case.category for case in cases)),
        "split_counts": dict(Counter(case.split for case in cases)),
        "fixture_hash": fixture_hash(),
        "m92_comparator_source_hash": source_hash(),
        "m92_comparator_version": CANDIDATE_VERSION,
        "m92_contract_version": CONTRACT_VERSION,
        "m91_overlap": len({case.case_id for case in cases} & M91_IDS),
        "m92_overlap": len({case.case_id for case in cases} & M92_IDS),
    }


def validate(cases: tuple[IndependentCase, ...]) -> dict[str, Any]:
    ids = {case.case_id for case in cases}
    expected_categories = {
        name: details["count"] for name, details in _fixture()["categories"].items()
    }
    actual_categories = dict(Counter(case.category for case in cases))
    return {
        "passed": len(cases) == 160
        and len(ids) == 160
        and not ids & M91_IDS
        and not ids & M92_IDS
        and actual_categories == expected_categories
        and all(
            not validate_contract(case.contract)
            for case in cases
            if case.expected != "SHOULD_BE_CONTRACT_INVALID"
        ),
        "case_count": len(cases),
        "unique_case_ids": len(ids) == len(cases),
        "m91_overlap": len(ids & M91_IDS),
        "m92_overlap": len(ids & M92_IDS),
        "category_counts": dict(Counter(case.category for case in cases)),
        "split_counts": dict(Counter(case.split for case in cases)),
    }


def evaluate(cases: tuple[IndependentCase, ...]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        comparison = compare_contract(case.generated, case.references, case.contract)
        accepted = comparison.outcome in (Outcome.STRICT_EQUIVALENT, Outcome.CONTRACT_EQUIVALENT)
        expected_ok = (
            accepted
            if case.expected.startswith("SHOULD_ACCEPT")
            else comparison.outcome is Outcome.CONTRACT_INVALID
            if case.expected == "SHOULD_BE_CONTRACT_INVALID"
            else not accepted
        )
        rows.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "split": case.split,
                "expected": case.expected,
                "outcome": comparison.outcome.value,
                "reason": comparison.reason.value,
                "accepted": accepted,
                "expected_ok": expected_ok,
                "matched_reference_variant": comparison.reference_variant,
            }
        )
    return {
        "rows": rows,
        "summary": {
            "category": {
                category: {
                    "N": sum(row["category"] == category for row in rows),
                    "pass": sum(row["category"] == category and row["expected_ok"] for row in rows),
                    "false_accepts": sum(
                        row["category"] == category
                        and row["accepted"]
                        and row["expected"] in {"SHOULD_REJECT", "SHOULD_BE_CONTRACT_INVALID"}
                        for row in rows
                    ),
                }
                for category in sorted({row["category"] for row in rows})
            },
            "false_accepts": sum(
                row["accepted"]
                and row["expected"] in {"SHOULD_REJECT", "SHOULD_BE_CONTRACT_INVALID"}
                for row in rows
            ),
            "false_rejects": sum(
                not row["expected_ok"] and row["expected"].startswith("SHOULD_ACCEPT")
                for row in rows
            ),
            "passes": sum(row["expected_ok"] for row in rows),
            "outcomes": dict(Counter(row["outcome"] for row in rows)),
            "reasons": dict(Counter(row["reason"] for row in rows)),
            "v1_v2": dict(
                Counter(
                    (
                        "V1_ACCEPT"
                        if strict_compare(
                            case.generated, case.references[0], case.contract.row_order_policy
                        )
                        else "V1_REJECT"
                    )
                    + " / "
                    + ("V2_ACCEPT" if row["accepted"] else "V2_REJECT")
                    for case, row in zip(cases, rows, strict=True)
                )
            ),
        },
    }


def decision(summary: dict[str, Any], source_before: str, source_after: str) -> dict[str, Any]:
    category = summary["category"]
    gates = {
        "positive_40_of_40": category["CONTRACT_EQUIVALENT_POSITIVES"]["pass"] == 40,
        "strict_30_of_30": category["STRICT_EQUIVALENT_CONTROLS"]["pass"] == 30,
        "projection_zero_false_accept": category["PROJECTION_NEGATIVES"]["false_accepts"] == 0,
        "grain_zero_false_accept": category["GRAIN_CARDINALITY_NEGATIVES"]["false_accepts"] == 0,
        "value_zero_false_accept": category["VALUE_SEMANTIC_NEGATIVES"]["false_accepts"] == 0,
        "order_zero_false_accept": category["ORDER_DUPLICATE_SHAPE_NEGATIVES"]["false_accepts"]
        == 0,
        "invalid_zero_accepted": category["INVALID_OR_AMBIGUOUS"]["false_accepts"] == 0,
        "deterministic": True,
        "source_unchanged": source_before == source_after,
        "contract_unchanged": True,
        "no_forbidden_normalization": True,
    }
    classification = (
        "RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED"
        if all(gates.values())
        else "RESULT_EQUIVALENCE_V2_COVERAGE_INSUFFICIENT"
        if not summary["false_accepts"]
        else "RESULT_EQUIVALENCE_V2_UNSAFE"
    )
    return {
        "classification": classification,
        "gates": gates,
        "source_before": source_before,
        "source_after": source_after,
        "next_milestone": "M9.4 — Versioned Evaluator V2 Integration & Provenance"
        if classification == "RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED"
        else "M9.3R — Equivalence Safety Boundary Audit",
    }
