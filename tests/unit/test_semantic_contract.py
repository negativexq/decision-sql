from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.contract import (
    CatalogVersionMismatch,
    ContractChangeType,
    LifecycleStatus,
    SemanticContractError,
    SemanticGovernanceMetadata,
    SemanticObjectKind,
    SemanticReplacementError,
    SemanticVersionMismatch,
    build_semantic_contract,
    canonical_snapshot_json,
    diff_snapshots,
    metric_provenance,
    snapshot_with,
    stable_id,
    validate_transition,
)
from app.semantics.requests import MetricRequest


@pytest.fixture
def contract():
    return build_semantic_contract(build_m3_catalog())


def _record(contract, stable: str):
    return contract.object(stable)


def test_contract_has_complete_stable_identity_and_versions(contract) -> None:
    assert contract.catalog_id == "decisionsql-demo-semantic-catalog"
    assert contract.catalog_version == 1
    assert len(contract.objects) == 48
    assert len({item.stable_id for item in contract.objects}) == 48
    assert all(item.semantic_version == 1 for item in contract.objects)
    assert all(len(item.definition_hash) == 64 for item in contract.objects)
    assert all(item.lifecycle_status is LifecycleStatus.ACTIVE for item in contract.objects)


def test_contract_hash_and_snapshot_serialization_are_deterministic(contract) -> None:
    reversed_snapshot = snapshot_with(contract, objects=tuple(reversed(contract.objects)))

    assert reversed_snapshot.semantic_contract_hash == contract.semantic_contract_hash
    assert canonical_snapshot_json(reversed_snapshot) == canonical_snapshot_json(contract)
    assert canonical_snapshot_json(contract) == canonical_snapshot_json(contract)


def test_display_only_change_is_allowed_and_diffed(contract) -> None:
    metric = _record(contract, "metric:completed_revenue")
    changed = metric.model_copy(update={"description": "A clearer display description."})
    next_snapshot = snapshot_with(
        contract,
        objects=tuple(
            changed if item.stable_id == metric.stable_id else item for item in contract.objects
        ),
    )

    changes = validate_transition(contract, next_snapshot)
    assert [change.change_type for change in changes] == [ContractChangeType.DISPLAY_ONLY_CHANGED]
    assert next_snapshot.catalog_version == contract.catalog_version


def test_semantic_change_requires_object_and_catalog_version_bumps(contract) -> None:
    metric = _record(contract, "metric:completed_revenue")
    changed = metric.model_copy(update={"definition_hash": "a" * 64})
    changed_snapshot = snapshot_with(
        contract,
        objects=tuple(
            changed if item.stable_id == metric.stable_id else item for item in contract.objects
        ),
    )

    with pytest.raises(SemanticVersionMismatch):
        validate_transition(contract, changed_snapshot)

    versioned = changed.model_copy(update={"semantic_version": 2})
    versioned_snapshot = snapshot_with(
        contract,
        objects=tuple(
            versioned if item.stable_id == metric.stable_id else item for item in contract.objects
        ),
        catalog_version=2,
    )
    changes = validate_transition(contract, versioned_snapshot)
    assert [change.change_type for change in changes] == [ContractChangeType.SEMANTIC_CHANGED]

    downgraded = versioned.model_copy(update={"semantic_version": 1})
    downgraded_snapshot = snapshot_with(
        versioned_snapshot,
        objects=tuple(
            downgraded if item.stable_id == metric.stable_id else item
            for item in versioned_snapshot.objects
        ),
        catalog_version=3,
    )
    with pytest.raises(SemanticVersionMismatch, match="decreased"):
        validate_transition(versioned_snapshot, downgraded_snapshot)


def test_catalog_object_addition_requires_catalog_version_bump(contract) -> None:
    extra = _record(contract, "metric:completed_revenue").model_copy(
        update={
            "stable_id": "metric:temporary_test_metric",
            "display_name": "temporary_test_metric",
            "definition_hash": sha256(b"temporary-test").hexdigest(),
        }
    )
    same_version = snapshot_with(contract, objects=(*contract.objects, extra))

    with pytest.raises(CatalogVersionMismatch):
        validate_transition(contract, same_version)

    versioned = snapshot_with(contract, objects=(*contract.objects, extra), catalog_version=2)
    assert any(
        change.change_type is ContractChangeType.ADDED
        for change in diff_snapshots(contract, versioned)
    )


def test_duplicate_semantic_names_and_stable_ids_are_rejected(contract) -> None:
    catalog = build_m3_catalog()
    with pytest.raises(SemanticContractError, match="duplicate semantic name"):
        build_semantic_contract(
            catalog.model_copy(update={"metrics": (*catalog.metrics, catalog.metrics[0])})
        )

    duplicate_id = contract.objects[0].model_copy(
        update={"stable_id": contract.objects[1].stable_id}
    )
    with pytest.raises(SemanticContractError, match="duplicate stable ID"):
        snapshot_with(contract, objects=(duplicate_id, *contract.objects[1:]))


def test_lifecycle_change_requires_catalog_version_and_active_glossary_exposure(contract) -> None:
    metric = _record(contract, "metric:completed_revenue")
    deprecated = metric.model_copy(update={"lifecycle_status": LifecycleStatus.DEPRECATED})
    next_snapshot = snapshot_with(
        contract,
        objects=tuple(
            deprecated if item.stable_id == metric.stable_id else item for item in contract.objects
        ),
        catalog_version=2,
    )

    changes = validate_transition(contract, next_snapshot)
    assert any(change.change_type is ContractChangeType.LIFECYCLE_CHANGED for change in changes)
    assert "completed_revenue" not in {
        item.display_name
        for item in next_snapshot.objects
        if item.kind is SemanticObjectKind.METRIC
        and item.lifecycle_status is LifecycleStatus.ACTIVE
    }
    assert "completed_revenue" in public_metric_glossary(build_m3_catalog())


@pytest.mark.parametrize(
    "update, message",
    [
        ({"replacement_stable_id": "metric:does_not_exist"}, "does not exist"),
        ({"replacement_stable_id": "metric:completed_revenue"}, "self"),
    ],
)
def test_invalid_replacement_reference_is_rejected(contract, update, message) -> None:
    metric = _record(contract, "metric:completed_revenue")
    deprecated = metric.model_copy(
        update={"lifecycle_status": LifecycleStatus.DEPRECATED, **update}
    )
    with pytest.raises(SemanticReplacementError, match=message):
        snapshot_with(
            contract,
            objects=tuple(
                deprecated if item.stable_id == metric.stable_id else item
                for item in contract.objects
            ),
            catalog_version=2,
        )


def test_replacement_cycle_is_rejected(contract) -> None:
    first = _record(contract, "metric:completed_revenue")
    second = _record(contract, "metric:average_payment_amount")
    first = first.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.DEPRECATED,
            "replacement_stable_id": second.stable_id,
        }
    )
    second = second.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.DEPRECATED,
            "replacement_stable_id": first.stable_id,
        }
    )
    objects = tuple(
        first
        if item.stable_id == first.stable_id
        else second
        if item.stable_id == second.stable_id
        else item
        for item in contract.objects
    )
    with pytest.raises(SemanticReplacementError, match="cycle"):
        snapshot_with(contract, objects=objects, catalog_version=2)


def test_deprecated_provenance_is_allowed_but_retired_metric_is_not_compilable(contract) -> None:
    metric = _record(contract, "metric:completed_revenue")
    deprecated = metric.model_copy(update={"lifecycle_status": LifecycleStatus.DEPRECATED})
    deprecated_contract = snapshot_with(
        contract,
        objects=tuple(
            deprecated if item.stable_id == metric.stable_id else item for item in contract.objects
        ),
        catalog_version=2,
    )
    provenance = metric_provenance(deprecated_contract, "completed_revenue", ())
    assert provenance.metric_lifecycle_status is LifecycleStatus.DEPRECATED
    deprecated_glossary = public_metric_glossary(build_m3_catalog(), deprecated_contract)
    assert "- completed_revenue:" not in deprecated_glossary

    retired = metric.model_copy(update={"lifecycle_status": LifecycleStatus.RETIRED})
    retired_contract = snapshot_with(
        contract,
        objects=tuple(
            retired if item.stable_id == metric.stable_id else item for item in contract.objects
        ),
        catalog_version=2,
    )
    assert "completed_revenue" not in {
        item.display_name
        for item in retired_contract.objects
        if item.kind is SemanticObjectKind.METRIC
        and item.lifecycle_status is LifecycleStatus.ACTIVE
    }
    with pytest.raises(SemanticContractError, match="retired metric"):
        metric_provenance(retired_contract, "completed_revenue", ())
    result = MetricCompiler(build_m3_catalog(), semantic_contract=retired_contract).compile_metric(
        MetricRequest(metric_name="completed_revenue")
    )
    assert isinstance(result, MetricCompilationFailure)
    assert result.code == "RETIRED_METRIC"


def test_retired_dimensions_are_hidden_from_public_glossary(contract) -> None:
    dimension = _record(contract, "dimension:customer")
    retired = dimension.model_copy(update={"lifecycle_status": LifecycleStatus.RETIRED})
    retired_contract = snapshot_with(
        contract,
        objects=tuple(
            retired if item.stable_id == dimension.stable_id else item for item in contract.objects
        ),
        catalog_version=2,
    )
    glossary = public_metric_glossary(build_m3_catalog(), retired_contract)
    assert "- customer:" not in glossary
    assert "- region:" in glossary
    result = MetricCompiler(build_m3_catalog(), semantic_contract=retired_contract).compile_metric(
        MetricRequest(metric_name="completed_revenue", dimensions=("customer",))
    )
    assert isinstance(result, MetricCompilationFailure)
    assert result.code == "RETIRED_DIMENSION"


def test_invalid_versions_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SemanticGovernanceMetadata(semantic_version=0)
    with pytest.raises(SemanticContractError, match="catalog_version"):
        build_semantic_contract(build_m3_catalog(), catalog_version=0)


def test_stable_id_format_is_server_owned() -> None:
    assert stable_id(SemanticObjectKind.METRIC, "completed_revenue") == (
        "metric:completed_revenue"
    )
