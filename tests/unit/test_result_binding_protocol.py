from __future__ import annotations

import inspect
from uuid import uuid4

from app.sql.models import QueryExecution
from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.result_binding_protocol import (
    BINDING_PROTOCOL_VERSION,
    BindingKind,
    BindingStatus,
    ResultBindingSpec,
    SlotBindingSpec,
    bind_generated_result,
    binding_spec_hash,
    canonical_hash,
    projection_fingerprints,
)
from evaluation.result_equivalence_contract import (
    CONTRACT_VERSION,
    DuplicatePolicy,
    ExtraOutputPolicy,
    ResultEquivalenceContract,
    ResultSlot,
    RowOrderPolicy,
    ScalarOrTabular,
    SlotKind,
)
from evaluation.versioned_result_evaluator import contract_instance_hash


def _spec(sql: str, schema_map, *, duplicate_slot: bool = False):
    fingerprint = projection_fingerprints(sql, schema_map)[0]
    assert fingerprint is not None
    fingerprint_hash = canonical_hash(fingerprint)
    slots = [ResultSlot("slot_0", SlotKind.DIMENSION, True, "output", "region")]
    specs = [
        SlotBindingSpec(
            "slot_0",
            "region",
            BindingKind.DIRECT_COLUMN,
            fingerprint_hash,
            "DIRECT_COLUMN",
        )
    ]
    if duplicate_slot:
        slots.append(ResultSlot("slot_1", SlotKind.DIMENSION, True, "output", "other"))
        specs.append(
            SlotBindingSpec(
                "slot_1",
                "other",
                BindingKind.DIRECT_COLUMN,
                fingerprint_hash,
                "DIRECT_COLUMN",
            )
        )
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
    return contract, ResultBindingSpec(
        BINDING_PROTOCOL_VERSION,
        contract_instance_hash(contract),
        "m3-semantic-catalog-v1",
        schema_map.version,
        tuple(specs),
    )


def _execution(*columns: str) -> QueryExecution:
    return QueryExecution(plan_id=uuid4(), columns=list(columns), rows=[], latency_ms=0.0)


def test_binder_api_has_no_reference_or_evaluator_inputs() -> None:
    names = set(inspect.signature(bind_generated_result).parameters)
    assert not names & {"reference_sql", "reference_result", "reference_values", "evaluator"}


def test_alias_is_not_a_semantic_binding_signal() -> None:
    schema_map = build_schema_semantic_map()
    contract, spec = _spec("SELECT r.name AS region FROM regions r", schema_map)
    result = bind_generated_result(
        "SELECT c.name AS region FROM customers c",
        _execution("region"),
        spec,
        contract,
        schema_map,
    )
    assert result.report.bindings[0].contract_slot_id is None


def test_direct_lineage_survives_alias_variation_and_cte() -> None:
    schema_map = build_schema_semantic_map()
    contract, spec = _spec("SELECT r.name AS region FROM regions r", schema_map)
    result = bind_generated_result(
        "WITH x AS (SELECT r.name AS arbitrary FROM regions r) SELECT x.arbitrary FROM x",
        _execution("arbitrary"),
        spec,
        contract,
        schema_map,
    )
    assert result.report.bindings[0].status is BindingStatus.BOUND
    assert result.report.bindings[0].contract_slot_id == "slot_0"


def test_duplicate_slot_matches_fail_closed() -> None:
    schema_map = build_schema_semantic_map()
    contract, spec = _spec(
        "SELECT r.name AS region FROM regions r", schema_map, duplicate_slot=True
    )
    result = bind_generated_result(
        "SELECT r.name AS arbitrary FROM regions r",
        _execution("arbitrary"),
        spec,
        contract,
        schema_map,
    )
    assert result.report.bindings[0].status is BindingStatus.AMBIGUOUS


def test_same_contract_hash_is_stable() -> None:
    schema_map = build_schema_semantic_map()
    contract, spec = _spec("SELECT r.name AS region FROM regions r", schema_map)
    assert binding_spec_hash(spec) == binding_spec_hash(spec)
    assert contract_instance_hash(contract) == contract_instance_hash(contract)
