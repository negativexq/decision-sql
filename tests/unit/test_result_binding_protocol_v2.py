from __future__ import annotations

import inspect

from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.m95r1_binding_v2_validation import _declared_contract, _execution
from evaluation.result_binding_protocol import binder_source_hash
from evaluation.result_binding_protocol_v2 import (
    BindingStatus,
    bind_generated_result_v2,
    projection_proofs,
)
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    verify_frozen_comparator_source,
)


def test_frozen_v1_and_v2_comparator_hashes_are_unchanged() -> None:
    assert (
        binder_source_hash() == "aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb"
    )
    assert verify_frozen_comparator_source() == FROZEN_V2_COMPARATOR_SOURCE_HASH


def test_v2_api_has_no_reference_or_evaluator_inputs() -> None:
    names = set(inspect.signature(bind_generated_result_v2).parameters)
    assert not names & {
        "reference_sql",
        "reference_result",
        "reference_rows",
        "reference_values",
        "oracle",
        "evaluator",
    }


def test_self_join_candidates_remain_distinct_before_static_identity_deduplication() -> None:
    proofs = projection_proofs(
        "SELECT name AS n FROM customers buyer JOIN customers seller ON buyer.id=seller.id",
        build_schema_semantic_map(),
    )
    assert proofs[0][2] is not None
    assert proofs[0][1][0].identity_key != proofs[0][1][1].identity_key
    assert len(proofs[0][1]) == 2


def test_qualified_self_join_binds_and_unqualified_self_join_fails_closed() -> None:
    schema = build_schema_semantic_map()
    contract, spec = _declared_contract("SELECT buyer.name AS n FROM customers buyer", schema)
    qualified = bind_generated_result_v2(
        "SELECT buyer.name AS arbitrary FROM customers buyer JOIN customers seller ON buyer.id=seller.id",  # noqa: E501 - scope fixture
        _execution(
            "SELECT buyer.name AS arbitrary FROM customers buyer JOIN customers seller ON buyer.id=seller.id"  # noqa: E501 - scope fixture
        ),
        spec,
        contract,
        schema,
    )
    assert qualified.report.bindings[0].binding_status is BindingStatus.BOUND
    ambiguous = bind_generated_result_v2(
        "SELECT name AS arbitrary FROM customers buyer JOIN customers seller ON buyer.id=seller.id",
        _execution(
            "SELECT name AS arbitrary FROM customers buyer JOIN customers seller ON buyer.id=seller.id"  # noqa: E501 - scope fixture
        ),
        spec,
        contract,
        schema,
    )
    assert ambiguous.report.bindings[0].binding_status is BindingStatus.AMBIGUOUS


def test_alias_spoof_and_value_matching_are_not_binding_signals() -> None:
    schema = build_schema_semantic_map()
    contract, spec = _declared_contract("SELECT r.name AS region FROM regions r", schema)
    result = bind_generated_result_v2(
        "SELECT COUNT(*) AS region FROM orders o",
        _execution("SELECT COUNT(*) AS region FROM orders o", value="same"),
        spec,
        contract,
        schema,
    )
    assert result.report.bindings[0].binding_status is BindingStatus.UNBOUND
    assert result.report.bindings[0].contract_slot_id is None


def test_duplicate_projection_slot_fails_closed_without_reordering_rows() -> None:
    schema = build_schema_semantic_map()
    contract, spec = _declared_contract("SELECT r.name AS n FROM regions r", schema)
    sql = "SELECT r.name AS first_name, r.name AS second_name FROM regions r"
    result = bind_generated_result_v2(sql, _execution(sql, value=7), spec, contract, schema)
    assert [item.binding_status for item in result.report.bindings] == [BindingStatus.AMBIGUOUS] * 2
    assert result.result.rows == ((7, 7),)
