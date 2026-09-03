"""Deterministic governance metadata for the server-owned semantic catalog.

The typed catalog remains the source of truth for semantic implementation.  This
module adds stable identity, lifecycle, version, contract hashing, and bounded
execution provenance without exposing physical implementation details to the
grounding model.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.semantics.models import (
    DimensionDefinition,
    EntityDefinition,
    MeasureDefinition,
    MeasureMetricDefinition,
    MetricCatalog,
    PredicateDefinition,
    RatioMetricDefinition,
    RelationshipDefinition,
)

SEMANTIC_CATALOG_ID = "decisionsql-demo-semantic-catalog"
SEMANTIC_CATALOG_VERSION = 1
SEMANTIC_COMPILER_CONTRACT_VERSION = 1
DEFAULT_SNAPSHOT_PATH = Path("app/semantics/semantic_contract.json")
SEMANTIC_GOVERNANCE_OVERRIDES: Mapping[str, SemanticGovernanceMetadata] = {}


class SemanticContractError(ValueError):
    """The semantic governance contract is invalid."""


class SemanticVersionMismatch(SemanticContractError):
    """Meaning-bearing content changed without a semantic version bump."""


class CatalogVersionMismatch(SemanticContractError):
    """Catalog-visible content changed without a catalog version bump."""


class SemanticReplacementError(SemanticContractError):
    """A lifecycle replacement reference is invalid."""


class LifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


class SemanticObjectKind(StrEnum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    DIMENSION = "dimension"
    MEASURE = "measure"
    METRIC = "metric"


class ContractChangeType(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    SEMANTIC_CHANGED = "SEMANTIC_CHANGED"
    LIFECYCLE_CHANGED = "LIFECYCLE_CHANGED"
    DISPLAY_ONLY_CHANGED = "DISPLAY_ONLY_CHANGED"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SemanticGovernanceMetadata(_FrozenModel):
    semantic_version: PositiveInt = 1
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE
    replacement_stable_id: str | None = None


class SemanticObjectIdentity(_FrozenModel):
    kind: SemanticObjectKind
    stable_id: str = Field(min_length=3)
    semantic_version: PositiveInt
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lifecycle_status: LifecycleStatus
    replacement_stable_id: str | None = None
    display_name: str = Field(min_length=1)
    description: str = ""


class SemanticCatalogIdentity(_FrozenModel):
    catalog_id: str = Field(min_length=1)
    catalog_version: PositiveInt
    semantic_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_contract_version: PositiveInt = SEMANTIC_COMPILER_CONTRACT_VERSION


class SemanticContractSnapshot(_FrozenModel):
    identity: SemanticCatalogIdentity
    objects: tuple[SemanticObjectIdentity, ...]

    @property
    def catalog_id(self) -> str:
        return self.identity.catalog_id

    @property
    def catalog_version(self) -> int:
        return self.identity.catalog_version

    @property
    def semantic_contract_hash(self) -> str:
        return self.identity.semantic_contract_hash

    @property
    def compiler_contract_version(self) -> int:
        return self.identity.compiler_contract_version

    def object(self, stable_id: str) -> SemanticObjectIdentity:
        for item in self.objects:
            if item.stable_id == stable_id:
                return item
        raise SemanticContractError(f"unknown semantic object: {stable_id}")


class SemanticContractChange(_FrozenModel):
    change_type: ContractChangeType
    kind: SemanticObjectKind
    stable_id: str
    old_semantic_version: int | None = None
    new_semantic_version: int | None = None
    old_definition_hash: str | None = None
    new_definition_hash: str | None = None


class SemanticExecutionProvenance(_FrozenModel):
    catalog_id: str
    catalog_version: PositiveInt
    semantic_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_contract_version: PositiveInt
    metric_stable_id: str
    metric_semantic_version: PositiveInt
    metric_definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric_lifecycle_status: LifecycleStatus
    requested_dimensions: tuple[str, ...] = ()


def stable_id(kind: SemanticObjectKind, name: str) -> str:
    return f"{kind.value}:{name}"


def build_semantic_contract(
    catalog: MetricCatalog,
    *,
    catalog_id: str = SEMANTIC_CATALOG_ID,
    catalog_version: int = SEMANTIC_CATALOG_VERSION,
    compiler_contract_version: int = SEMANTIC_COMPILER_CONTRACT_VERSION,
    metadata: Mapping[str, SemanticGovernanceMetadata] | None = None,
) -> SemanticContractSnapshot:
    """Build and validate a deterministic governance snapshot for ``catalog``."""
    if catalog_version <= 0:
        raise SemanticContractError("catalog_version must be positive")
    if compiler_contract_version <= 0:
        raise SemanticContractError("compiler_contract_version must be positive")
    metadata = SEMANTIC_GOVERNANCE_OVERRIDES if metadata is None else metadata
    records = tuple(
        _object_record(kind, item, metadata.get(stable_id(kind, item.name)))
        for kind, items in _catalog_objects(catalog)
        for item in items
    )
    _validate_catalog_names(records)
    expected_metadata_ids = {record.stable_id for record in records}
    unknown_metadata = sorted(set(metadata) - expected_metadata_ids)
    if unknown_metadata:
        raise SemanticContractError(
            f"governance metadata references unknown objects: {unknown_metadata}"
        )
    contract_hash = _contract_hash(catalog_id, records)
    snapshot = SemanticContractSnapshot(
        identity=SemanticCatalogIdentity(
            catalog_id=catalog_id,
            catalog_version=catalog_version,
            semantic_contract_hash=contract_hash,
            compiler_contract_version=compiler_contract_version,
        ),
        objects=records,
    )
    validate_snapshot(snapshot)
    return snapshot


def snapshot_with(
    snapshot: SemanticContractSnapshot,
    *,
    objects: tuple[SemanticObjectIdentity, ...] | None = None,
    catalog_version: int | None = None,
) -> SemanticContractSnapshot:
    """Rebuild a snapshot after an intentional test or migration change."""
    next_objects = snapshot.objects if objects is None else objects
    next_version = snapshot.catalog_version if catalog_version is None else catalog_version
    contract_hash = _contract_hash(snapshot.catalog_id, next_objects)
    result = SemanticContractSnapshot(
        identity=SemanticCatalogIdentity(
            catalog_id=snapshot.catalog_id,
            catalog_version=next_version,
            semantic_contract_hash=contract_hash,
            compiler_contract_version=snapshot.compiler_contract_version,
        ),
        objects=next_objects,
    )
    validate_snapshot(result)
    return result


def validate_snapshot(snapshot: SemanticContractSnapshot) -> None:
    """Validate identity, hashes, lifecycle references, and replacement cycles."""
    if snapshot.catalog_version <= 0:
        raise SemanticContractError("catalog_version must be positive")
    if snapshot.compiler_contract_version <= 0:
        raise SemanticContractError("compiler_contract_version must be positive")
    by_id: dict[str, SemanticObjectIdentity] = {}
    names_by_kind: dict[SemanticObjectKind, set[str]] = {}
    for item in snapshot.objects:
        if item.semantic_version <= 0:
            raise SemanticContractError(f"semantic_version must be positive: {item.stable_id}")
        if item.stable_id in by_id:
            raise SemanticContractError(f"duplicate stable ID: {item.stable_id}")
        if not item.stable_id.startswith(f"{item.kind.value}:"):
            raise SemanticContractError(f"stable ID kind mismatch: {item.stable_id}")
        names = names_by_kind.setdefault(item.kind, set())
        if item.display_name in names:
            raise SemanticContractError(
                f"duplicate semantic name for {item.kind.value}: {item.display_name}"
            )
        names.add(item.display_name)
        by_id[item.stable_id] = item
    if snapshot.semantic_contract_hash != _contract_hash(snapshot.catalog_id, snapshot.objects):
        raise SemanticContractError("semantic contract hash mismatch")
    for item in snapshot.objects:
        replacement = item.replacement_stable_id
        if replacement is None:
            continue
        if item.lifecycle_status is not LifecycleStatus.DEPRECATED:
            raise SemanticReplacementError(
                f"only deprecated objects may specify replacements: {item.stable_id}"
            )
        if replacement == item.stable_id:
            raise SemanticReplacementError(f"replacement cannot be self: {item.stable_id}")
        target = by_id.get(replacement)
        if target is None:
            raise SemanticReplacementError(
                f"replacement does not exist: {item.stable_id} -> {replacement}"
            )
        visited = {item.stable_id}
        current = target
        while current.replacement_stable_id is not None:
            if current.stable_id in visited:
                raise SemanticReplacementError(
                    f"replacement cycle includes: {item.stable_id}"
                )
            visited.add(current.stable_id)
            current = by_id.get(current.replacement_stable_id)  # type: ignore[assignment]
            if current is None:
                raise SemanticReplacementError("replacement chain references an unknown object")


def diff_snapshots(
    old: SemanticContractSnapshot, new: SemanticContractSnapshot
) -> tuple[SemanticContractChange, ...]:
    validate_snapshot(old)
    validate_snapshot(new)
    old_by_id = {item.stable_id: item for item in old.objects}
    new_by_id = {item.stable_id: item for item in new.objects}
    changes: list[SemanticContractChange] = []
    for stable in sorted(set(old_by_id) | set(new_by_id)):
        before = old_by_id.get(stable)
        after = new_by_id.get(stable)
        if before is None:
            assert after is not None
            changes.append(
                SemanticContractChange(
                    change_type=ContractChangeType.ADDED,
                    kind=after.kind,
                    stable_id=stable,
                    new_semantic_version=after.semantic_version,
                    new_definition_hash=after.definition_hash,
                )
            )
            continue
        if after is None:
            changes.append(
                SemanticContractChange(
                    change_type=ContractChangeType.REMOVED,
                    kind=before.kind,
                    stable_id=stable,
                    old_semantic_version=before.semantic_version,
                    old_definition_hash=before.definition_hash,
                )
            )
            continue
        if before.definition_hash != after.definition_hash:
            changes.append(
                SemanticContractChange(
                    change_type=ContractChangeType.SEMANTIC_CHANGED,
                    kind=after.kind,
                    stable_id=stable,
                    old_semantic_version=before.semantic_version,
                    new_semantic_version=after.semantic_version,
                    old_definition_hash=before.definition_hash,
                    new_definition_hash=after.definition_hash,
                )
            )
        if (
            before.lifecycle_status != after.lifecycle_status
            or before.replacement_stable_id != after.replacement_stable_id
        ):
            changes.append(
                SemanticContractChange(
                    change_type=ContractChangeType.LIFECYCLE_CHANGED,
                    kind=after.kind,
                    stable_id=stable,
                    old_semantic_version=before.semantic_version,
                    new_semantic_version=after.semantic_version,
                    old_definition_hash=before.definition_hash,
                    new_definition_hash=after.definition_hash,
                )
            )
        if (
            before.display_name != after.display_name or before.description != after.description
        ):
            changes.append(
                SemanticContractChange(
                    change_type=ContractChangeType.DISPLAY_ONLY_CHANGED,
                    kind=after.kind,
                    stable_id=stable,
                    old_semantic_version=before.semantic_version,
                    new_semantic_version=after.semantic_version,
                    old_definition_hash=before.definition_hash,
                    new_definition_hash=after.definition_hash,
                )
            )
    return tuple(changes)


def validate_transition(
    old: SemanticContractSnapshot, new: SemanticContractSnapshot
) -> tuple[SemanticContractChange, ...]:
    """Enforce semantic-version and catalog-version bump rules."""
    if old.catalog_id != new.catalog_id:
        raise CatalogVersionMismatch("catalog IDs differ")
    if new.catalog_version < old.catalog_version:
        raise CatalogVersionMismatch("catalog version cannot decrease")
    changes = diff_snapshots(old, new)
    old_by_id = {item.stable_id: item for item in old.objects}
    new_by_id = {item.stable_id: item for item in new.objects}
    for stable_id in sorted(set(old_by_id) & set(new_by_id)):
        if new_by_id[stable_id].semantic_version < old_by_id[stable_id].semantic_version:
            raise SemanticVersionMismatch(f"semantic version decreased: {stable_id}")
    catalog_changes = {
        ContractChangeType.ADDED,
        ContractChangeType.REMOVED,
        ContractChangeType.SEMANTIC_CHANGED,
        ContractChangeType.LIFECYCLE_CHANGED,
    }
    for change in changes:
        if change.change_type is ContractChangeType.SEMANTIC_CHANGED:
            if (change.new_semantic_version or 0) <= (change.old_semantic_version or 0):
                raise SemanticVersionMismatch(
                    f"semantic version did not increase: {change.stable_id}"
                )
        if change.change_type in catalog_changes and new.catalog_version <= old.catalog_version:
            raise CatalogVersionMismatch(
                f"catalog version did not increase for {change.change_type}: {change.stable_id}"
            )
    return changes


def active_metric_names(snapshot: SemanticContractSnapshot) -> frozenset[str]:
    return frozenset(
        item.display_name
        for item in snapshot.objects
        if item.kind is SemanticObjectKind.METRIC
        and item.lifecycle_status is LifecycleStatus.ACTIVE
    )


def metric_provenance(
    snapshot: SemanticContractSnapshot, metric_name: str, dimensions: tuple[str, ...]
) -> SemanticExecutionProvenance:
    metric = snapshot.object(stable_id(SemanticObjectKind.METRIC, metric_name.strip().lower()))
    if metric.lifecycle_status is LifecycleStatus.RETIRED:
        raise SemanticContractError(f"retired metric is not executable: {metric.stable_id}")
    dimension_ids = tuple(
        stable_id(SemanticObjectKind.DIMENSION, dimension.strip().lower())
        for dimension in dimensions
    )
    for dimension_id in dimension_ids:
        dimension = snapshot.object(dimension_id)
        if dimension.lifecycle_status is LifecycleStatus.RETIRED:
            raise SemanticContractError(f"retired dimension is not executable: {dimension_id}")
    return SemanticExecutionProvenance(
        catalog_id=snapshot.catalog_id,
        catalog_version=snapshot.catalog_version,
        semantic_contract_hash=snapshot.semantic_contract_hash,
        compiler_contract_version=snapshot.compiler_contract_version,
        metric_stable_id=metric.stable_id,
        metric_semantic_version=metric.semantic_version,
        metric_definition_hash=metric.definition_hash,
        metric_lifecycle_status=metric.lifecycle_status,
        requested_dimensions=dimension_ids,
    )


def canonical_snapshot_json(snapshot: SemanticContractSnapshot) -> str:
    validate_snapshot(snapshot)
    data = snapshot.model_dump(mode="json")
    data["objects"] = sorted(
        data["objects"], key=lambda item: (item["kind"], item["stable_id"])
    )
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def write_snapshot(path: Path, snapshot: SemanticContractSnapshot) -> None:
    path.write_text(canonical_snapshot_json(snapshot) + "\n")


def load_snapshot(path: Path) -> SemanticContractSnapshot:
    return SemanticContractSnapshot.model_validate_json(path.read_text())


def _catalog_objects(
    catalog: MetricCatalog,
) -> tuple[
    tuple[SemanticObjectKind, tuple[EntityDefinition, ...]],
    tuple[SemanticObjectKind, tuple[RelationshipDefinition, ...]],
    tuple[SemanticObjectKind, tuple[DimensionDefinition, ...]],
    tuple[SemanticObjectKind, tuple[MeasureDefinition, ...]],
    tuple[SemanticObjectKind, tuple[MeasureMetricDefinition | RatioMetricDefinition, ...]],
]:
    return (
        (SemanticObjectKind.ENTITY, catalog.entities),
        (SemanticObjectKind.RELATIONSHIP, catalog.relationships),
        (SemanticObjectKind.DIMENSION, catalog.dimensions),
        (SemanticObjectKind.MEASURE, catalog.measures),
        (SemanticObjectKind.METRIC, catalog.metrics),
    )


def _validate_catalog_names(records: tuple[SemanticObjectIdentity, ...]) -> None:
    names_by_kind: dict[SemanticObjectKind, set[str]] = {}
    for record in records:
        expected_id = stable_id(record.kind, record.display_name)
        if record.stable_id != expected_id:
            raise SemanticContractError(
                f"stable ID does not match semantic name: {record.stable_id}"
            )
        names = names_by_kind.setdefault(record.kind, set())
        if record.display_name in names:
            raise SemanticContractError(
                f"duplicate semantic name for {record.kind.value}: {record.display_name}"
            )
        names.add(record.display_name)


def _object_record(
    kind: SemanticObjectKind,
    item: object,
    metadata: SemanticGovernanceMetadata | None,
) -> SemanticObjectIdentity:
    if not hasattr(item, "name"):
        raise SemanticContractError(f"semantic object has no name: {kind}")
    name = str(item.name)
    governance = metadata or SemanticGovernanceMetadata()
    return SemanticObjectIdentity(
        kind=kind,
        stable_id=stable_id(kind, name),
        semantic_version=governance.semantic_version,
        definition_hash=_definition_hash(kind, item),
        lifecycle_status=governance.lifecycle_status,
        replacement_stable_id=governance.replacement_stable_id,
        display_name=name,
        description=str(getattr(item, "description", "")),
    )


def _definition_hash(kind: SemanticObjectKind, item: object) -> str:
    payload = _meaning_payload(kind, item)
    return sha256(_canonical_json(payload).encode()).hexdigest()


def _meaning_payload(kind: SemanticObjectKind, item: object) -> dict[str, object]:
    if kind is SemanticObjectKind.ENTITY and isinstance(item, EntityDefinition):
        return {
            "kind": kind.value,
            "name": item.name,
            "physical_table": item.physical_table,
            "key_columns": item.key_columns,
        }
    if kind is SemanticObjectKind.RELATIONSHIP and isinstance(item, RelationshipDefinition):
        return {
            "kind": kind.value,
            "name": item.name,
            "from_entity": item.from_entity,
            "to_entity": item.to_entity,
            "column_pairs": [pair.model_dump(mode="json") for pair in item.column_pairs],
            "cardinality": item.cardinality.value,
        }
    if kind is SemanticObjectKind.DIMENSION and isinstance(item, DimensionDefinition):
        return {
            "kind": kind.value,
            "name": item.name,
            "entity": item.entity,
            "physical_column": item.physical_column,
        }
    if kind is SemanticObjectKind.MEASURE and isinstance(item, MeasureDefinition):
        return {
            "kind": kind.value,
            "name": item.name,
            "entity": item.entity,
            "aggregation": item.aggregation.value,
            "physical_column": item.physical_column,
            "filters": _predicate_payload(item.filters),
        }
    if kind is SemanticObjectKind.METRIC and isinstance(
        item, (MeasureMetricDefinition, RatioMetricDefinition)
    ):
        payload: dict[str, object] = {
            "kind": kind.value,
            "metric_kind": item.kind.value,
            "name": item.name,
            "valid_dimensions": sorted(item.valid_dimensions),
            "eligibility_filters": _predicate_payload(item.eligibility_filters),
        }
        if isinstance(item, MeasureMetricDefinition):
            payload["measure"] = item.measure
        else:
            payload.update(
                {
                    "numerator_measure": item.numerator_measure,
                    "denominator_measure": item.denominator_measure,
                    "scale": str(item.scale),
                    "zero_denominator_policy": item.zero_denominator_policy.value,
                }
            )
        return payload
    raise SemanticContractError(f"unsupported semantic object kind: {kind}")


def _predicate_payload(predicates: tuple[PredicateDefinition, ...]) -> list[dict[str, object]]:
    values = [predicate.model_dump(mode="json") for predicate in predicates]
    return sorted(values, key=_canonical_json)


def _contract_hash(catalog_id: str, records: tuple[SemanticObjectIdentity, ...]) -> str:
    payload = {
        "catalog_id": catalog_id,
        "objects": [
            {
                "kind": item.kind.value,
                "stable_id": item.stable_id,
                "semantic_version": item.semantic_version,
                "definition_hash": item.definition_hash,
                "lifecycle_status": item.lifecycle_status.value,
                "replacement_stable_id": item.replacement_stable_id,
            }
            for item in sorted(records, key=lambda record: (record.kind.value, record.stable_id))
        ],
    }
    return sha256(_canonical_json(payload).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _current_snapshot() -> SemanticContractSnapshot:
    from app.semantics.catalog import build_m3_catalog

    return build_semantic_contract(build_m3_catalog())


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the semantic contract snapshot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args()

    if args.command == "snapshot":
        snapshot = _current_snapshot()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_snapshot(args.output, snapshot)
        print(
            json.dumps(
                {
                    "catalog_id": snapshot.catalog_id,
                    "catalog_version": snapshot.catalog_version,
                    "contract_hash": snapshot.semantic_contract_hash,
                    "object_count": len(snapshot.objects),
                    "path": str(args.output),
                },
                sort_keys=True,
            )
        )
        return

    accepted = load_snapshot(args.snapshot)
    current = _current_snapshot()
    try:
        changes = validate_transition(accepted, current)
    except SemanticContractError as error:
        raise SystemExit(f"semantic contract check failed: {error}") from error
    print(
        json.dumps(
            {
                "status": "PASS",
                "changes": [change.model_dump(mode="json") for change in changes],
                "contract_hash": current.semantic_contract_hash,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
