"""Frozen, provider-free M9.5R.1 binder-v2 corpus and one-shot harness."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlglot import exp, parse_one

from app.sql.models import QueryExecution
from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.result_binding_protocol_v2 import (
    BINDING_PROTOCOL_ID,
    BindingStatus,
    GovernedProjectionProvenance,
    ResultBindingSpecV2,
    SlotBindingSpecV2,
    bind_generated_result_v2,
    binder_v2_source_hash,
    canonical_hash,
    projection_proofs,
)
from evaluation.result_equivalence_contract import (
    CONTRACT_VERSION,
    BoundResult,
    DuplicatePolicy,
    ExtraOutputPolicy,
    ResultEquivalenceContract,
    ResultSlot,
    RowOrderPolicy,
    ScalarOrTabular,
    SlotKind,
)
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    EvaluationComparisonRequest,
    EvaluatorMode,
    contract_instance_hash,
    evaluate,
    verify_frozen_comparator_source,
)

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "evaluation" / "fixtures" / "m95r1_validation_recipe.json"
ADVERSARIAL_RECIPE = ROOT / "evaluation" / "fixtures" / "m95r1_adversarial_recipe.json"
M95R1_ID = "decisionsql-m95r1-binding-v2-independent"
ADVERSARIAL_ID = "decisionsql-m95r1-binding-v2-adversarial"


@dataclass(frozen=True)
class ValidationCase:
    case_id: str
    category: str
    family: str
    generated_sql: str
    reference_sql: str
    contract: ResultEquivalenceContract
    binding_spec: ResultBindingSpecV2
    execution: QueryExecution
    governed_provenance: tuple[GovernedProjectionProvenance, ...]
    expected: str
    semantically_correct: bool
    executable: bool = True


def stable_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recipe_hash() -> str:
    return sha256(RECIPE.read_bytes()).hexdigest()


def adversarial_recipe_hash() -> str:
    return sha256(ADVERSARIAL_RECIPE.read_bytes()).hexdigest()


def _select_columns(sql: str) -> list[str]:
    statement = parse_one(sql, read="postgres")
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        raise ValueError("fixture SQL has no SELECT")
    return [item.alias_or_name for item in select.expressions]


def _execution(sql: str, value: object = 1) -> QueryExecution:
    columns = _select_columns(sql)
    return QueryExecution(
        plan_id=uuid4(),
        columns=columns,
        rows=[{column: value for column in columns}],
        row_count=1,
        latency_ms=0.0,
    )


def _declared_contract(
    target_sql: str,
    schema_map: Any,
    *,
    semantic_identity: str | None = None,
    slot_count: int = 1,
) -> tuple[ResultEquivalenceContract, ResultBindingSpecV2]:
    fingerprints = projection_proofs(target_sql, schema_map)
    if len(fingerprints) < slot_count or any(item[0] is None for item in fingerprints[:slot_count]):
        raise ValueError(f"fixture target cannot be represented: {target_sql}")
    slots: list[ResultSlot] = []
    specs: list[SlotBindingSpecV2] = []
    for index in range(slot_count):
        fingerprint = fingerprints[index][0]
        assert fingerprint is not None
        identity = (
            semantic_identity
            if index == 0 and semantic_identity
            else f"expression:{canonical_hash(fingerprint)}"
        )
        slot_id = f"slot_{index}"
        slots.append(ResultSlot(slot_id, SlotKind.MEASURE, True, "output", identity))
        specs.append(SlotBindingSpecV2(slot_id, identity, fingerprint, "BOUNDED_EXPRESSION"))
    contract = ResultEquivalenceContract(
        CONTRACT_VERSION,
        tuple(slots),
        (),
        (),
        ScalarOrTabular.TABULAR,
        RowOrderPolicy.ORDER_INSENSITIVE,
        DuplicatePolicy.PRESERVE,
        ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
    )
    spec = ResultBindingSpecV2(
        BINDING_PROTOCOL_ID,
        contract_instance_hash(contract),
        "m3-semantic-catalog-v1",
        schema_map.version,
        tuple(specs),
    )
    return contract, spec


def _case(
    case_id: str,
    category: str,
    family: str,
    generated_sql: str,
    target_sql: str,
    schema_map: Any,
    *,
    expected: str = "BOUND",
    correct: bool = True,
    value: object = 1,
    governed: str | None = None,
    slot_count: int = 1,
    executable: bool = True,
) -> ValidationCase:
    identity = governed or None
    contract, spec = _declared_contract(
        target_sql, schema_map, semantic_identity=identity, slot_count=slot_count
    )
    provenance = () if governed is None else (GovernedProjectionProvenance(0, governed, "slot_0"),)
    return ValidationCase(
        case_id,
        category,
        family,
        generated_sql,
        target_sql,
        contract,
        spec,
        _execution(generated_sql, value),
        provenance,
        expected,
        correct,
        executable,
    )


def build_independent_cases() -> tuple[ValidationCase, ...]:
    """Materialize the recipe without consulting binder output or an oracle."""
    schema = build_schema_semantic_map()
    cases: list[ValidationCase] = []
    governed_metrics = (
        "metric:completed_revenue",
        "metric:average_completed_order_value",
        "metric:average_payment_amount",
        "metric:completed_item_quantity",
        "metric:payment_success_rate",
        "metric:refunded_order_rate",
        "metric:refund_to_revenue_rate",
        "metric:average_items_per_completed_order",
        "metric:completed_order_rate",
        "metric:customer_order_coverage_rate",
    )
    for index in range(20):
        sql = f"SELECT o.total_amount AS governed_{index} FROM orders o"
        cases.append(
            _case(
                f"M95R1-G-{index:02d}",
                "SEMANTICALLY_CORRECT_POSITIVE",
                "GOVERNED_PROVENANCE",
                sql,
                sql,
                schema,
            governed=governed_metrics[index % len(governed_metrics)],
            )
        )
    direct_templates = [
        (
            "UNIQUE_DIRECT_COLUMN_QUALIFIED_MULTI_TABLE",
            "SELECT r.name AS region_label FROM regions r",
            "SELECT r.name AS region_label FROM regions r",
        ),
        (
            "UNIQUE_DIRECT_COLUMN_QUALIFIED_MULTI_TABLE",
            "SELECT c.name AS customer_label FROM customers c JOIN regions r ON TRUE",
            "SELECT c.name AS customer_label FROM customers c JOIN regions r ON TRUE",
        ),
        (
            "CTE_OR_DERIVED_PASSTHROUGH",
            "WITH region_source AS (SELECT r.name AS label FROM regions r) SELECT x.label AS region_label FROM region_source x",  # noqa: E501 - frozen fixture SQL
            "SELECT r.name AS region_label FROM regions r",
        ),
        (
            "CTE_OR_DERIVED_PASSTHROUGH",
            "SELECT x.label AS region_label FROM (SELECT r.name AS label FROM regions r) x",
            "SELECT r.name AS region_label FROM regions r",
        ),
        (
            "AGGREGATE_OR_DISTINCT",
            "SELECT SUM(oi.quantity) AS quantity_total FROM order_items oi",
            "SELECT SUM(oi.quantity) AS quantity_total FROM order_items oi",
        ),
        (
            "AGGREGATE_OR_DISTINCT",
            "SELECT COUNT(*) AS order_count FROM orders o",
            "SELECT COUNT(*) AS order_count FROM orders o",
        ),
        (
            "AGGREGATE_OR_DISTINCT",
            "SELECT COUNT(DISTINCT o.customer_id) AS customer_count FROM orders o",
            "SELECT COUNT(DISTINCT o.customer_id) AS customer_count FROM orders o",
        ),
        (
            "ARITHMETIC_OR_RATIO",
            "SELECT o.total_amount / o.subtotal AS ratio_value FROM orders o",
            "SELECT o.total_amount / o.subtotal AS ratio_value FROM orders o",
        ),
        (
            "ARITHMETIC_OR_RATIO",
            "SELECT o.subtotal - o.discount_amount AS net_value FROM orders o",
            "SELECT o.subtotal - o.discount_amount AS net_value FROM orders o",
        ),
        (
            "CASE_OR_SAFE_CAST",
            "SELECT CAST(o.total_amount AS NUMERIC) AS total_value FROM orders o",
            "SELECT CAST(o.total_amount AS NUMERIC) AS total_value FROM orders o",
        ),
        (
            "CASE_OR_SAFE_CAST",
            "SELECT CASE WHEN o.status = 'paid' THEN o.total_amount ELSE 0 END AS paid_value FROM orders o",  # noqa: E501 - frozen fixture SQL
            "SELECT CASE WHEN o.status = 'paid' THEN o.total_amount ELSE 0 END AS paid_value FROM orders o",  # noqa: E501 - frozen fixture SQL
        ),
        (
            "WINDOW",
            "SELECT ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at) AS row_number_value FROM orders o",  # noqa: E501 - frozen fixture SQL
            "SELECT ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at) AS row_number_value FROM orders o",  # noqa: E501 - frozen fixture SQL
        ),
        (
            "WINDOW",
            "SELECT RANK() OVER (PARTITION BY o.sales_rep_id ORDER BY o.total_amount DESC) AS rank_value FROM orders o",  # noqa: E501 - frozen fixture SQL
            "SELECT RANK() OVER (PARTITION BY o.sales_rep_id ORDER BY o.total_amount DESC) AS rank_value FROM orders o",  # noqa: E501 - frozen fixture SQL
        ),
    ]
    direct_counts = {
        "UNIQUE_DIRECT_COLUMN_QUALIFIED_MULTI_TABLE": 8,
        "CTE_OR_DERIVED_PASSTHROUGH": 8,
        "AGGREGATE_OR_DISTINCT": 8,
        "ARITHMETIC_OR_RATIO": 6,
        "CASE_OR_SAFE_CAST": 4,
        "WINDOW": 4,
        "SCALAR_OR_BOUNDED_NESTED_EXPRESSION": 2,
    }
    direct_by_family: dict[str, list[tuple[str, str]]] = {}
    for family, generated, target in direct_templates:
        direct_by_family.setdefault(family, []).append((generated, target))
    index = 0
    for family, count in direct_counts.items():
        templates = direct_by_family.get(
            family,
            [
                (
                    "SELECT c.name AS customer_label FROM customers c",
                    "SELECT c.name AS customer_label FROM customers c",
                )
            ],
        )
        for offset in range(count):
            generated, target = templates[offset % len(templates)]
            cases.append(
                _case(
                    f"M95R1-D-{index:02d}",
                    "SEMANTICALLY_CORRECT_POSITIVE",
                    family,
                    generated,
                    target,
                    schema,
                )
            )
            index += 1
    wrong_templates = [
        (
            "WRONG_SOURCE_OR_LEAF",
            "SELECT c.name AS region_label FROM customers c",
            "SELECT r.name AS region_label FROM regions r",
        ),
        (
            "WRONG_AGGREGATE_OR_DISTINCT",
            "SELECT COUNT(o.customer_id) AS customer_count FROM orders o",
            "SELECT COUNT(DISTINCT o.customer_id) AS customer_count FROM orders o",
        ),
        (
            "WRONG_ARITHMETIC_OR_RATIO",
            "SELECT o.subtotal / o.total_amount AS ratio_value FROM orders o",
            "SELECT o.total_amount / o.subtotal AS ratio_value FROM orders o",
        ),
        (
            "WRONG_WINDOW_SEMANTICS",
            "SELECT ROW_NUMBER() OVER (PARTITION BY o.sales_rep_id ORDER BY o.ordered_at) AS row_number_value FROM orders o",  # noqa: E501 - frozen fixture SQL
            "SELECT ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at) AS row_number_value FROM orders o",  # noqa: E501 - frozen fixture SQL
        ),
        (
            "WRONG_CTE_DERIVED_LINEAGE",
            "WITH x AS (SELECT c.name AS label FROM customers c) SELECT x.label AS region_label FROM x",  # noqa: E501 - frozen fixture SQL
            "SELECT r.name AS region_label FROM regions r",
        ),
        (
            "WRONG_RELATION_SOURCE_ROLE",
            "SELECT b.name AS buyer_label FROM customers b JOIN regions s ON TRUE",
            "SELECT s.name AS buyer_label FROM regions s",
        ),
        (
            "WRONG_OUTPUT_SEMANTIC_IDENTITY",
            "SELECT o.status AS region_label FROM orders o",
            "SELECT r.name AS region_label FROM regions r",
        ),
    ]
    wrong_counts = {
        "WRONG_SOURCE_OR_LEAF": 8,
        "WRONG_AGGREGATE_OR_DISTINCT": 6,
        "WRONG_ARITHMETIC_OR_RATIO": 6,
        "WRONG_WINDOW_SEMANTICS": 6,
        "WRONG_CTE_DERIVED_LINEAGE": 4,
        "WRONG_RELATION_SOURCE_ROLE": 4,
        "WRONG_OUTPUT_SEMANTIC_IDENTITY": 6,
    }
    wrong_by_family: dict[str, list[tuple[str, str]]] = {}
    for family, generated, target in wrong_templates:
        wrong_by_family.setdefault(family, []).append((generated, target))
    index = 0
    for family, count in wrong_counts.items():
        templates = wrong_by_family[family]
        for offset in range(count):
            generated, target = templates[offset % len(templates)]
            cases.append(
                _case(
                    f"M95R1-W-{index:02d}",
                    "SEMANTICALLY_WRONG_EXECUTABLE",
                    family,
                    generated,
                    target,
                    schema,
                    expected="REJECT",
                    correct=False,
                    value=2,
                )
            )
            index += 1
    fail: list[tuple[str, str, str]] = []
    for _index in range(6):
        fail.append(
            (
                "SELF_JOIN_OR_SCOPE_AMBIGUITY",
                "SELECT name AS ambiguous_name FROM customers buyer JOIN customers seller ON buyer.id = seller.id",  # noqa: E501 - frozen fixture SQL
                "SELECT buyer.name AS ambiguous_name FROM customers buyer JOIN customers seller ON buyer.id = seller.id",  # noqa: E501 - frozen fixture SQL
            )
        )
    for _index in range(4):
        fail.append(
            (
                "MULTI_SOURCE_SAME_COLUMN_AMBIGUITY",
                "SELECT id AS ambiguous_id FROM customers c JOIN orders o ON c.id = o.customer_id",
                "SELECT c.id AS ambiguous_id FROM customers c",
            )
        )
    for _index in range(4):
        fail.append(
            (
                "MULTIPLE_SLOT_CANDIDATES",
                "SELECT r.name AS region_label FROM regions r",
                "SELECT r.name AS region_label FROM regions r",
            )
        )
    for _index in range(3):
        fail.append(
            (
                "UNSUPPORTED_EXPRESSION",
                "SELECT CURRENT_DATE AS unsupported_value FROM orders o",
                "SELECT r.name AS unsupported_value FROM regions r",
            )
        )
    for _index in range(3):
        fail.append(
            (
                "COMPETING_PROJECTIONS_OR_DUPLICATE_SLOT",
                "SELECT r.name AS first_name, r.name AS second_name FROM regions r",
                "SELECT r.name AS first_name FROM regions r",
            )
        )
    for index, (family, generated, target) in enumerate(fail):
        if family == "MULTIPLE_SLOT_CANDIDATES":
            contract, one_spec = _declared_contract(target, schema, slot_count=1)
            first = one_spec.slot_binding_specs[0]
            slots = (
                ResultSlot("slot_0", SlotKind.DIMENSION, True, "output", first.semantic_identity),
                ResultSlot("slot_1", SlotKind.DIMENSION, True, "output", "same"),
            )
            contract = ResultEquivalenceContract(
                CONTRACT_VERSION,
                slots,
                (),
                (),
                ScalarOrTabular.TABULAR,
                RowOrderPolicy.ORDER_INSENSITIVE,
                DuplicatePolicy.PRESERVE,
                ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
            )
            spec = ResultBindingSpecV2(
                one_spec.binding_protocol_version,
                contract_instance_hash(contract),
                one_spec.semantic_catalog_version,
                one_spec.schema_semantic_map_version,
                (
                    first,
                    SlotBindingSpecV2(
                        "slot_1",
                        "same",
                        first.expression_fingerprint,
                        first.allowed_expression_class,
                    ),
                ),
            )
            case = ValidationCase(
                f"M95R1-F-{index:02d}",
                "FAIL_CLOSED_AMBIGUOUS_OR_UNSUPPORTED",
                family,
                generated,
                target,
                contract,
                spec,
                _execution(generated),
                (),
                "AMBIGUOUS",
                False,
                True,
            )
        elif family == "COMPETING_PROJECTIONS_OR_DUPLICATE_SLOT":
            case = _case(
                f"M95R1-F-{index:02d}",
                "FAIL_CLOSED_AMBIGUOUS_OR_UNSUPPORTED",
                family,
                generated,
                target,
                schema,
                expected="AMBIGUOUS",
                correct=False,
            )
        else:
            expected = (
                "AMBIGUOUS" if "SELF_JOIN" in family or "MULTI_SOURCE" in family else "UNSUPPORTED"
            )
            case = _case(
                f"M95R1-F-{index:02d}",
                "FAIL_CLOSED_AMBIGUOUS_OR_UNSUPPORTED",
                family,
                generated,
                target,
                schema,
                expected=expected,
                correct=False,
            )
        cases.append(case)
    if len(cases) != 120:
        raise AssertionError(f"independent recipe materialized {len(cases)} cases")
    return tuple(cases)


def _adversarial_case(
    case_id: str, family: str, generated: str, target: str, schema: Any, expected: str = "BOUND"
) -> ValidationCase:
    return _case(
        case_id,
        "ADVERSARIAL",
        family,
        generated,
        target,
        schema,
        expected=expected,
        correct=expected == "BOUND",
        executable=False,
    )


def build_adversarial_cases() -> tuple[ValidationCase, ...]:
    schema = build_schema_semantic_map()
    cases: list[ValidationCase] = []
    for i in range(6):
        cases.append(
            _adversarial_case(
                f"M95R1-A-SCOPE-{i:02d}",
                "SCOPE_AND_RELATION_INSTANCE",
                "SELECT buyer.name AS n FROM customers buyer JOIN customers seller ON buyer.id=seller.id",  # noqa: E501 - frozen adversarial SQL
                "SELECT buyer.name AS n FROM customers buyer JOIN customers seller ON buyer.id=seller.id",  # noqa: E501 - frozen adversarial SQL
                schema,
            )
        )
    for i in range(6):
        cases.append(
            _adversarial_case(
                f"M95R1-A-SCOPE-{i + 6:02d}",
                "SCOPE_AND_RELATION_INSTANCE",
                "SELECT name AS n FROM customers buyer JOIN customers seller ON buyer.id=seller.id",  # noqa: E501 - frozen adversarial SQL
                "SELECT buyer.name AS n FROM customers buyer JOIN customers seller ON buyer.id=seller.id",  # noqa: E501 - frozen adversarial SQL
                schema,
                "AMBIGUOUS",
            )
        )
    alias_pairs = [
        (
            "SELECT COUNT(*) AS completed_revenue FROM orders o",
            "SELECT SUM(o.total_amount) AS completed_revenue FROM orders o",
        ),
        ("SELECT c.name AS region FROM customers c", "SELECT r.name AS region FROM regions r"),
        (
            "SELECT 1 AS total_amount FROM orders o",
            "SELECT o.total_amount AS total_amount FROM orders o",
        ),
    ]
    for i in range(10):
        generated, target = alias_pairs[i % len(alias_pairs)]
        cases.append(
            _adversarial_case(
                f"M95R1-A-ALIAS-{i:02d}",
                "ALIAS_SPOOF_AND_COLLISION",
                generated,
                target,
                schema,
                "UNBOUND",
            )
        )
    norm = [
        ("SELECT (r.name) AS n FROM regions r", "SELECT r.name AS n FROM regions r", "BOUND"),
        (
            "SELECT CAST(r.name AS TEXT) AS n FROM regions r",
            "SELECT CAST(r.name AS TEXT) AS n FROM regions r",
            "BOUND",
        ),
        (
            "SELECT CURRENT_DATE AS n FROM regions r",
            "SELECT r.name AS n FROM regions r",
            "UNSUPPORTED",
        ),
    ]
    for i in range(10):
        generated, target, expected = norm[i % len(norm)]
        cases.append(
            _adversarial_case(
                f"M95R1-A-NORM-{i:02d}",
                "NORMALIZATION_AND_FINGERPRINT_COLLISION",
                generated,
                target,
                schema,
                expected,
            )
        )
    for i in range(8):
        generated, target = (
            (
                "SELECT COUNT(DISTINCT o.customer_id) AS n FROM orders o",
                "SELECT COUNT(DISTINCT o.customer_id) AS n FROM orders o",
            )
            if i < 4
            else (
                "SELECT COUNT(o.customer_id) AS n FROM orders o",
                "SELECT COUNT(DISTINCT o.customer_id) AS n FROM orders o",
            )
        )
        cases.append(
            _adversarial_case(
                f"M95R1-A-AGG-{i:02d}",
                "AGGREGATE_AND_DISTINCT",
                generated,
                target,
                schema,
                "BOUND" if i < 4 else "UNBOUND",
            )
        )
    for i in range(8):
        generated, target = (
            (
                "SELECT o.total_amount / o.subtotal AS n FROM orders o",
                "SELECT o.total_amount / o.subtotal AS n FROM orders o",
            )
            if i < 4
            else (
                "SELECT o.subtotal / o.total_amount AS n FROM orders o",
                "SELECT o.total_amount / o.subtotal AS n FROM orders o",
            )
        )
        cases.append(
            _adversarial_case(
                f"M95R1-A-RATIO-{i:02d}",
                "ARITHMETIC_AND_RATIO",
                generated,
                target,
                schema,
                "BOUND" if i < 4 else "UNBOUND",
            )
        )
    for i in range(8):
        generated, target = (
            (
                "SELECT ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at) AS n FROM orders o",  # noqa: E501 - frozen adversarial SQL
                "SELECT ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at) AS n FROM orders o",  # noqa: E501 - frozen adversarial SQL
            )
            if i < 4
            else (
                "SELECT ROW_NUMBER() OVER (PARTITION BY o.sales_rep_id ORDER BY o.ordered_at) AS n FROM orders o",  # noqa: E501 - frozen adversarial SQL
                "SELECT ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.ordered_at) AS n FROM orders o",  # noqa: E501 - frozen adversarial SQL
            )
        )
        cases.append(
            _adversarial_case(
                f"M95R1-A-WINDOW-{i:02d}",
                "WINDOW",
                generated,
                target,
                schema,
                "BOUND" if i < 4 else "UNBOUND",
            )
        )
    for i in range(4):
        generated = (
            "SELECT r.name AS n, r.name AS n2 FROM regions r"
            if i < 2
            else "SELECT CURRENT_DATE AS n FROM regions r"
        )
        target = "SELECT r.name AS n FROM regions r"
        cases.append(
            _adversarial_case(
                f"M95R1-A-UNSUPPORTED-{i:02d}",
                "MULTI_SLOT_OR_UNSUPPORTED",
                generated,
                target,
                schema,
                "AMBIGUOUS" if i < 2 else "UNSUPPORTED",
            )
        )
    if len(cases) != 60:
        raise AssertionError(f"adversarial recipe materialized {len(cases)} cases")
    return tuple(cases)


def _public_api_is_nonleaky() -> bool:
    names = set(inspect.signature(bind_generated_result_v2).parameters)
    return not names & {
        "reference_sql",
        "reference_result",
        "reference_rows",
        "reference_values",
        "oracle",
        "evaluator",
    }


def _run_cases(cases: tuple[ValidationCase, ...]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    v2_calls = 0
    for case in cases:
        bound = bind_generated_result_v2(
            case.generated_sql,
            case.execution,
            case.binding_spec,
            case.contract,
            build_schema_semantic_map(),
            case.governed_provenance,
        )
        statuses = [item.binding_status.value for item in bound.report.bindings]
        if case.expected == "BOUND":
            binding_ok = all(status == BindingStatus.BOUND.value for status in statuses)
        elif case.expected == "REJECT":
            binding_ok = all(status != BindingStatus.BOUND.value for status in statuses)
        else:
            binding_ok = (
                statuses == [case.expected]
                if len(statuses) == 1
                else all(status != BindingStatus.BOUND.value for status in statuses)
            )
        v2_equivalent: bool | None = None
        if case.executable and case.expected == "BOUND":
            reference = BoundResult(
                tuple(case.execution.columns),
                tuple(
                    item.slot_id
                    for item in case.binding_spec.slot_binding_specs[: len(case.execution.columns)]
                ),
                tuple(
                    tuple(row.get(column) for column in case.execution.columns)
                    for row in case.execution.rows
                ),
                case.contract.scalar_or_tabular,
            )
            v2 = evaluate(
                EvaluationComparisonRequest(
                    bound.result, (reference,), EvaluatorMode.V2, case.contract
                )
            )
            v2_calls += 1
            v2_equivalent = v2.equivalent
        results.append(
            {
                "case_id": case.case_id,
                "expected": case.expected,
                "statuses": statuses,
                "binding_ok": binding_ok,
                "v2_equivalent": v2_equivalent,
                "methods": [item.proof_method.value for item in bound.report.bindings],
            }
        )
    return {
        "case_count": len(cases),
        "results": results,
        "v2_calls": v2_calls,
        "all_expected": all(item["binding_ok"] for item in results),
        "nonleaky_api": _public_api_is_nonleaky(),
    }


def run() -> dict[str, Any]:
    verify_frozen_comparator_source()
    pre_hash = binder_v2_source_hash()
    independent = build_independent_cases()
    adversarial = build_adversarial_cases()
    independent_result = _run_cases(independent)
    adversarial_result = _run_cases(adversarial)
    post_hash = binder_v2_source_hash()
    positive = [
        item
        for item in independent_result["results"]
        if item["case_id"].startswith("M95R1-G-") or item["case_id"].startswith("M95R1-D-")
    ]
    failclosed = [
        item for item in independent_result["results"] if item["case_id"].startswith("M95R1-F-")
    ]
    return {
        "classification": (
            "FAIL_CLOSED_BINDER_V2_INDEPENDENTLY_VALIDATED"
            if independent_result["all_expected"]
            and adversarial_result["all_expected"]
            and sum(item["v2_equivalent"] is True for item in independent_result["results"]) == 60
            and pre_hash == post_hash
            and _public_api_is_nonleaky()
            else "BINDER_V2_UNSAFE"
        ),
        "binder_protocol_id": BINDING_PROTOCOL_ID,
        "binder_v2_pre_hash": pre_hash,
        "binder_v2_post_hash": post_hash,
        "binder_v2_unchanged": pre_hash == post_hash,
        "comparator_hash": FROZEN_V2_COMPARATOR_SOURCE_HASH,
        "recipe_hash": recipe_hash(),
        "adversarial_recipe_hash": adversarial_recipe_hash(),
        "independent": independent_result,
        "adversarial": adversarial_result,
        "positive_count": len(positive),
        "positive_evaluable": sum(
            item["binding_ok"] and item["v2_equivalent"] is True for item in positive
        ),
        "failclosed_count": len(failclosed),
        "m10_binding_protocol_ready": False,
        "m10_corpus_created": False,
        "m10_cases_consumed": 0,
        "provider_calls": 0,
        "sql_generation_calls": 0,
        "db_calls": 0,
        "db_mutations": 0,
    }
