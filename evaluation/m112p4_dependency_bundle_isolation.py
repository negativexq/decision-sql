"""Bounded dependency-closed size-two bundles over the frozen D3 harness."""

from __future__ import annotations

import copy
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp

from evaluation.m112p2_counterexample_diagnostic import (
    DiagnosticState,
    stable_hash,
)
from evaluation.m112p3_component_isolation import (
    ArmInput,
    ComponentIsolationError,
    ComponentIsolationHarness,
    ComponentKind,
    IsolationPair,
    SubstitutionValidation,
    changed_components,
    component_fingerprint,
    parse_postgres,
    replace_component,
)


class DependencyReason(StrEnum):
    ALIAS_REFERENCE_DEPENDENCY = "ALIAS_REFERENCE_DEPENDENCY"
    ORDINAL_REFERENCE_DEPENDENCY = "ORDINAL_REFERENCE_DEPENDENCY"
    IDENTIFIER_BINDING_DEPENDENCY = "IDENTIFIER_BINDING_DEPENDENCY"
    PROJECTION_SLOT_OWNERSHIP = "PROJECTION_SLOT_OWNERSHIP"
    WINDOW_SLOT_OWNERSHIP = "WINDOW_SLOT_OWNERSHIP"
    RELATIONAL_BINDING_DEPENDENCY = "RELATIONAL_BINDING_DEPENDENCY"
    AST_CONTAINER_DEPENDENCY = "AST_CONTAINER_DEPENDENCY"
    OTHER_EXPLICIT_BOUNDED_DEPENDENCY = "OTHER_EXPLICIT_BOUNDED_DEPENDENCY"


class BundleArmState(StrEnum):
    BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED = "BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED"
    BUNDLE_REPAIR_SIDE_ONLY = "BUNDLE_REPAIR_SIDE_ONLY"
    BUNDLE_TRANSFER_SIDE_ONLY = "BUNDLE_TRANSFER_SIDE_ONLY"
    MULTIPLE_DEPENDENCY_BUNDLES_ISOLATE_WITNESS = "MULTIPLE_DEPENDENCY_BUNDLES_ISOLATE_WITNESS"
    NO_DEPENDENCY_BUNDLE_CANDIDATE = "NO_DEPENDENCY_BUNDLE_CANDIDATE"
    NO_DEPENDENCY_BUNDLE_ISOLATED = "NO_DEPENDENCY_BUNDLE_ISOLATED"
    DEPENDENCY_CLOSURE_TOO_LARGE = "DEPENDENCY_CLOSURE_TOO_LARGE"
    BUNDLE_SUBSTITUTION_NOT_SAFE = "BUNDLE_SUBSTITUTION_NOT_SAFE"
    BUNDLE_SUBSTITUTION_NOT_ISOLATED = "BUNDLE_SUBSTITUTION_NOT_ISOLATED"
    P0_ENTITLEMENT_BLOCKED = "P0_ENTITLEMENT_BLOCKED"
    EXECUTION_INCONCLUSIVE = "EXECUTION_INCONCLUSIVE"


class PairBundleState(StrEnum):
    PAIR_STABLE_DEPENDENCY_BUNDLE_ISOLATED = "PAIR_STABLE_DEPENDENCY_BUNDLE_ISOLATED"
    PAIR_ONE_ARM_DEPENDENCY_BUNDLE_ISOLATED = "PAIR_ONE_ARM_DEPENDENCY_BUNDLE_ISOLATED"
    PAIR_BUNDLE_DISAGREEMENT = "PAIR_BUNDLE_DISAGREEMENT"
    PAIR_MULTIPLE_DEPENDENCY_BUNDLES = "PAIR_MULTIPLE_DEPENDENCY_BUNDLES"
    PAIR_NO_DEPENDENCY_BUNDLE_CANDIDATE = "PAIR_NO_DEPENDENCY_BUNDLE_CANDIDATE"
    PAIR_NO_DEPENDENCY_BUNDLE_ISOLATED = "PAIR_NO_DEPENDENCY_BUNDLE_ISOLATED"
    PAIR_DEPENDENCY_CLOSURE_TOO_LARGE = "PAIR_DEPENDENCY_CLOSURE_TOO_LARGE"
    PAIR_SUBSTITUTION_UNSAFE = "PAIR_SUBSTITUTION_UNSAFE"
    PAIR_EXECUTION_INCONCLUSIVE = "PAIR_EXECUTION_INCONCLUSIVE"


class DependencyEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: ComponentKind
    target: ComponentKind
    reason: DependencyReason


class ClosureResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: ComponentKind
    components: tuple[ComponentKind, ...]
    edges: tuple[DependencyEdge, ...] = ()
    too_large: bool = False


class BundleProgram(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: str
    bundle_id: str
    sql: str
    sql_hash: str
    validation: str
    reason: str | None = None


class BundleTrial(BaseModel):
    model_config = ConfigDict(frozen=True)

    bundle_id: str
    components: tuple[ComponentKind, ...]
    dependency_reasons: tuple[DependencyReason, ...]
    repair_sql_hash: str | None = None
    transfer_sql_hash: str | None = None
    invariance_checked: bool = False
    dependency_closed: bool = False
    admitted: bool = False
    repair_equal_reference: bool | None = None
    transfer_equal_candidate: bool | None = None
    repair_witness_removed: bool = False
    transfer_witness_reproduced: bool = False
    execution_inconclusive: bool = False
    validation: str = "VALID"
    reason: str | None = None


class BundleArmResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    arm: str
    p0_tier: str
    baseline_candidate_hash: str
    baseline_reference_hash: str
    baseline_witness_fixture_ids: tuple[str, ...]
    changed_components: tuple[ComponentKind, ...]
    dependency_edges: tuple[DependencyEdge, ...]
    closures: tuple[ClosureResult, ...]
    trials: tuple[BundleTrial, ...]
    state: BundleArmState
    isolated_bundle: str | None = None
    reason: str | None = None


class BundlePairResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    p0_tier: str
    profile: tuple[str, ...]
    off: BundleArmResult
    on: BundleArmResult
    state: PairBundleState
    isolated_bundle: str | None = None


def canonical_bundle_id(components: tuple[ComponentKind, ...] | list[ComponentKind]) -> str:
    ordered = tuple(sorted({component.value for component in components}))
    if len(ordered) != 2:
        raise ValueError("authoritative dependency bundles must contain exactly two components")
    return "+".join(ordered)


def _select(root: Any) -> Any:
    if not hasattr(root, "args") or root.key != "select":
        raise ComponentIsolationError(
            SubstitutionValidation.UNSUPPORTED_COMPONENT,
            "dependency extraction requires a top-level SELECT",
        )
    return root


def _aliases(root: Any) -> set[str]:
    return {
        expression.alias
        for expression in _select(root).expressions
        if getattr(expression, "alias", None)
    }


def _alias_refs(root: Any, clause_name: str) -> set[str]:
    clause = _select(root).args.get(clause_name)
    if clause is None:
        return set()
    return {column.name for column in clause.find_all(exp.Column)}


def _alias_dependency(candidate: Any, reference: Any, dependent: str) -> bool:
    candidate_aliases = _aliases(candidate)
    reference_aliases = _aliases(reference)
    if candidate_aliases == reference_aliases:
        return False
    candidate_refs = _alias_refs(candidate, dependent) & candidate_aliases
    reference_refs = _alias_refs(reference, dependent) & reference_aliases
    return bool(candidate_refs or reference_refs)


def _qualified_tables(root: Any, component: ComponentKind) -> set[str]:
    from evaluation.m112p3_component_isolation import extract_component

    try:
        extracted = extract_component(root, component)
    except ComponentIsolationError:
        return set()
    nodes = extracted if isinstance(extracted, (list, tuple)) else [extracted]
    tables: set[str] = set()
    for node in nodes:
        if hasattr(node, "find_all"):
            tables.update(column.table for column in node.find_all(exp.Column) if column.table)
    return tables


def extract_component_dependencies(
    candidate: Any,
    reference: Any,
    changed: tuple[ComponentKind, ...] | None = None,
) -> tuple[DependencyEdge, ...]:
    """Return only dependencies proven by projection-alias binding.

    The graph is intentionally conservative.  Co-occurrence of changed clauses
    creates no edge; an edge exists only when a changed clause actually binds a
    changed projection alias on at least one side.
    """
    changed_set = set(changed or changed_components(candidate, reference))
    edges: set[tuple[str, str, str]] = set()
    dependent_map = {
        ComponentKind.TOP_LEVEL_ORDER_BY: "order",
        ComponentKind.TOP_LEVEL_GROUP_BY: "group",
        ComponentKind.TOP_LEVEL_HAVING: "having",
    }
    projection = ComponentKind.TOP_LEVEL_PROJECTION
    if projection in changed_set:
        for dependent, clause_name in dependent_map.items():
            if dependent in changed_set and _alias_dependency(candidate, reference, clause_name):
                edges.add(
                    (
                        projection.value,
                        dependent.value,
                        DependencyReason.ALIAS_REFERENCE_DEPENDENCY.value,
                    )
                )
                edges.add(
                    (
                        dependent.value,
                        projection.value,
                        DependencyReason.ALIAS_REFERENCE_DEPENDENCY.value,
                    )
                )
    relational = ComponentKind.RELATIONAL_FROM_JOIN_BLOCK
    if relational in changed_set:
        for component in changed_set - {relational}:
            candidate_tables = _qualified_tables(candidate, component)
            reference_tables = _qualified_tables(reference, component)
            if candidate_tables != reference_tables and (candidate_tables or reference_tables):
                reason = DependencyReason.IDENTIFIER_BINDING_DEPENDENCY.value
                edges.add((component.value, relational.value, reason))
                edges.add((relational.value, component.value, reason))
    return tuple(
        DependencyEdge(
            source=ComponentKind(source),
            target=ComponentKind(target),
            reason=DependencyReason(reason),
        )
        for source, target, reason in sorted(edges)
    )


def minimal_dependency_closure(
    target: ComponentKind,
    changed: tuple[ComponentKind, ...],
    edges: tuple[DependencyEdge, ...],
) -> ClosureResult:
    changed_set = set(changed)
    closure = {target}
    frontier = [target]
    while frontier:
        source = frontier.pop()
        for edge in edges:
            if edge.source is source and edge.target in changed_set and edge.target not in closure:
                closure.add(edge.target)
                frontier.append(edge.target)
    components = tuple(sorted(closure, key=lambda component: component.value))
    relevant = tuple(edge for edge in edges if edge.source in closure and edge.target in closure)
    return ClosureResult(
        target=target,
        components=components,
        edges=relevant,
        too_large=len(components) > 2,
    )


def _replace_bundle(base: Any, source: Any, components: tuple[ComponentKind, ...]) -> Any:
    result = copy.deepcopy(base)
    source_copy = copy.deepcopy(source)
    for component in components:
        result = replace_component(result, source_copy, component)
    return result


def _bundle_fingerprints(root: Any) -> dict[ComponentKind, str]:
    from evaluation.m112p3_component_isolation import SUPPORTED_COMPONENTS

    return {component: component_fingerprint(root, component) for component in SUPPORTED_COMPONENTS}


def build_bundle_hybrid(
    base_sql: str,
    source_sql: str,
    components: tuple[ComponentKind, ...],
    direction: str,
    *,
    p0_projection_entitled: bool = True,
) -> BundleProgram:
    if len(set(components)) != 2:
        raise ComponentIsolationError(
            SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
            "bundle must contain exactly two distinct components",
        )
    if ComponentKind.TOP_LEVEL_PROJECTION in components and not p0_projection_entitled:
        raise ComponentIsolationError(
            SubstitutionValidation.P0_ENTITLEMENT_BLOCKED,
            "projection component is not entitled by the governing P0 contract",
        )
    base = parse_postgres(base_sql)
    source = parse_postgres(source_sql)
    hybrid = _replace_bundle(base, source, components)
    before = _bundle_fingerprints(base)
    source_fingerprints = _bundle_fingerprints(source)
    after = _bundle_fingerprints(hybrid)
    for component in components:
        if after[component] != source_fingerprints[component]:
            raise ComponentIsolationError(
                SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
                f"bundle target changed during substitution: {component.value}",
            )
    for component in before:
        if component not in components and after[component] != before[component]:
            raise ComponentIsolationError(
                SubstitutionValidation.SUBSTITUTION_NOT_ISOLATED,
                f"non-target component changed: {component.value}",
            )
    sql = hybrid.sql(dialect="postgres")
    round_trip = parse_postgres(sql)
    round_trip_fingerprints = _bundle_fingerprints(round_trip)
    for component in components:
        if round_trip_fingerprints[component] != source_fingerprints[component]:
            raise ComponentIsolationError(
                SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
                f"round-trip changed bundle target: {component.value}",
            )
    for component in before:
        if component not in components and round_trip_fingerprints[component] != before[component]:
            raise ComponentIsolationError(
                SubstitutionValidation.SUBSTITUTION_NOT_ISOLATED,
                f"round-trip changed non-target: {component.value}",
            )
    bundle_id = canonical_bundle_id(components)
    return BundleProgram(
        direction=direction,
        bundle_id=bundle_id,
        sql=sql,
        sql_hash=stable_hash(sql),
        validation=SubstitutionValidation.VALID.value,
    )


def validate_bundle_non_target_invariance(
    base_sql: str, hybrid_sql: str, components: tuple[ComponentKind, ...]
) -> bool:
    base = _bundle_fingerprints(parse_postgres(base_sql))
    hybrid = _bundle_fingerprints(parse_postgres(hybrid_sql))
    return all(
        base[component] == hybrid[component] for component in base if component not in components
    )


class DependencyBundleHarness:
    """P4 bundle substitutions over the frozen P2/P3 diagnostic services."""

    def __init__(self, d3_harness: ComponentIsolationHarness) -> None:
        self.d3 = d3_harness
        self.bank = d3_harness.bank

    def _observe(
        self, left: str, right: str, pair: IsolationPair, fixture: Any
    ) -> tuple[bool | None, bool]:
        observation = self.d3._compare_sqls(left, right, pair, fixture)
        return observation.equal, observation.state is DiagnosticState.EXECUTION_INCONCLUSIVE

    def run_arm(self, arm: ArmInput) -> BundleArmResult:
        candidate = parse_postgres(arm.pair.candidate_sql)
        reference = parse_postgres(arm.pair.reference_sql)
        witnesses = self.d3.witness_fixture_ids(arm.pair)
        changed = changed_components(candidate, reference)
        base = {
            "case_id": arm.case_id,
            "arm": arm.arm,
            "p0_tier": arm.p0_tier,
            "baseline_candidate_hash": arm.pair.candidate_sql_hash,
            "baseline_reference_hash": arm.pair.reference_sql_hash,
            "baseline_witness_fixture_ids": witnesses,
            "changed_components": changed,
        }
        if not witnesses:
            return BundleArmResult(
                **base,
                dependency_edges=(),
                closures=(),
                trials=(),
                state=BundleArmState.EXECUTION_INCONCLUSIVE,
                reason="frozen D2 baseline witness did not reproduce",
            )
        edges = extract_component_dependencies(candidate, reference, changed)
        closures = tuple(
            minimal_dependency_closure(component, changed, edges) for component in changed
        )
        candidates: dict[str, tuple[ComponentKind, ...]] = {}
        too_large = False
        has_dependency = False
        for closure in closures:
            if len(closure.components) > 1:
                has_dependency = True
            if closure.too_large:
                too_large = True
            elif len(closure.components) == 2 and len(closure.edges) > 0:
                candidates[canonical_bundle_id(closure.components)] = closure.components
        trials: list[BundleTrial] = []
        isolated: list[str] = []
        partial_repair = False
        partial_transfer = False
        inconclusive = False
        unsafe = False
        p0_blocked = False
        for bundle_id, components in sorted(candidates.items()):
            reasons = tuple(
                sorted(
                    {
                        edge.reason
                        for edge in edges
                        if edge.source in components and edge.target in components
                    },
                    key=lambda x: x.value,
                )
            )
            try:
                repair = build_bundle_hybrid(
                    arm.pair.candidate_sql,
                    arm.pair.reference_sql,
                    components,
                    "REPAIR",
                    p0_projection_entitled=arm.p0_projection_entitled,
                )
                transfer = build_bundle_hybrid(
                    arm.pair.reference_sql,
                    arm.pair.candidate_sql,
                    components,
                    "TRANSFER",
                    p0_projection_entitled=arm.p0_projection_entitled,
                )
            except ComponentIsolationError as error:
                unsafe = True
                p0_blocked |= error.validation is SubstitutionValidation.P0_ENTITLEMENT_BLOCKED
                trials.append(
                    BundleTrial(
                        bundle_id=bundle_id,
                        components=components,
                        dependency_reasons=reasons,
                        validation=error.validation.value,
                        reason=error.reason,
                    )
                )
                continue
            repair_equal = True
            transfer_equal = True
            trial_inconclusive = False
            for fixture_id in witnesses:
                fixture = self.bank.fixture(fixture_id)
                repair_observation, repair_inconclusive = self._observe(
                    repair.sql, arm.pair.reference_sql, arm.pair, fixture
                )
                transfer_observation, transfer_inconclusive = self._observe(
                    transfer.sql, arm.pair.candidate_sql, arm.pair, fixture
                )
                if repair_inconclusive or transfer_inconclusive:
                    trial_inconclusive = True
                    continue
                repair_equal &= repair_observation is True
                transfer_equal &= transfer_observation is True
            if trial_inconclusive:
                inconclusive = True
            repair_removed = repair_equal and not trial_inconclusive
            transfer_reproduced = transfer_equal and not trial_inconclusive
            partial_repair |= repair_removed and not transfer_reproduced
            partial_transfer |= transfer_reproduced and not repair_removed
            if repair_removed and transfer_reproduced:
                isolated.append(bundle_id)
            trials.append(
                BundleTrial(
                    bundle_id=bundle_id,
                    components=components,
                    dependency_reasons=reasons,
                    repair_sql_hash=repair.sql_hash,
                    transfer_sql_hash=transfer.sql_hash,
                    invariance_checked=True,
                    dependency_closed=True,
                    admitted=True,
                    repair_equal_reference=repair_removed,
                    transfer_equal_candidate=transfer_reproduced,
                    repair_witness_removed=repair_removed,
                    transfer_witness_reproduced=transfer_reproduced,
                    execution_inconclusive=trial_inconclusive,
                )
            )
        state: BundleArmState
        isolated_bundle: str | None = None
        if len(isolated) > 1:
            state = BundleArmState.MULTIPLE_DEPENDENCY_BUNDLES_ISOLATE_WITNESS
        elif len(isolated) == 1:
            state = BundleArmState.BIDIRECTIONAL_DEPENDENCY_BUNDLE_ISOLATED
            isolated_bundle = isolated[0]
        elif partial_repair:
            state = BundleArmState.BUNDLE_REPAIR_SIDE_ONLY
        elif partial_transfer:
            state = BundleArmState.BUNDLE_TRANSFER_SIDE_ONLY
        elif p0_blocked:
            state = BundleArmState.P0_ENTITLEMENT_BLOCKED
        elif unsafe:
            state = BundleArmState.BUNDLE_SUBSTITUTION_NOT_SAFE
        elif inconclusive:
            state = BundleArmState.EXECUTION_INCONCLUSIVE
        elif too_large:
            state = BundleArmState.DEPENDENCY_CLOSURE_TOO_LARGE
        elif not has_dependency:
            state = BundleArmState.NO_DEPENDENCY_BUNDLE_CANDIDATE
        else:
            state = BundleArmState.NO_DEPENDENCY_BUNDLE_ISOLATED
        return BundleArmResult(
            **base,
            dependency_edges=edges,
            closures=closures,
            trials=tuple(trials),
            state=state,
            isolated_bundle=isolated_bundle,
        )


def aggregate_bundle_pair(
    off: BundleArmResult, on: BundleArmResult, profile: tuple[str, ...]
) -> BundlePairResult:
    off_bundle = off.isolated_bundle
    on_bundle = on.isolated_bundle
    if off_bundle and on_bundle:
        if off_bundle == on_bundle:
            state = PairBundleState.PAIR_STABLE_DEPENDENCY_BUNDLE_ISOLATED
            bundle = off_bundle
        else:
            state = PairBundleState.PAIR_BUNDLE_DISAGREEMENT
            bundle = None
    elif (
        off.state is BundleArmState.MULTIPLE_DEPENDENCY_BUNDLES_ISOLATE_WITNESS
        or on.state is BundleArmState.MULTIPLE_DEPENDENCY_BUNDLES_ISOLATE_WITNESS
    ):
        state, bundle = PairBundleState.PAIR_MULTIPLE_DEPENDENCY_BUNDLES, None
    elif off_bundle or on_bundle:
        state, bundle = (
            PairBundleState.PAIR_ONE_ARM_DEPENDENCY_BUNDLE_ISOLATED,
            off_bundle or on_bundle,
        )
    elif (
        off.state is BundleArmState.P0_ENTITLEMENT_BLOCKED
        or on.state is BundleArmState.P0_ENTITLEMENT_BLOCKED
    ):
        state, bundle = PairBundleState.PAIR_SUBSTITUTION_UNSAFE, None
    elif (
        off.state is BundleArmState.DEPENDENCY_CLOSURE_TOO_LARGE
        or on.state is BundleArmState.DEPENDENCY_CLOSURE_TOO_LARGE
    ):
        state, bundle = PairBundleState.PAIR_DEPENDENCY_CLOSURE_TOO_LARGE, None
    elif (
        off.state is BundleArmState.BUNDLE_SUBSTITUTION_NOT_SAFE
        or on.state is BundleArmState.BUNDLE_SUBSTITUTION_NOT_SAFE
    ):
        state, bundle = PairBundleState.PAIR_SUBSTITUTION_UNSAFE, None
    elif (
        off.state is BundleArmState.EXECUTION_INCONCLUSIVE
        or on.state is BundleArmState.EXECUTION_INCONCLUSIVE
    ):
        state, bundle = PairBundleState.PAIR_EXECUTION_INCONCLUSIVE, None
    elif (
        off.state is BundleArmState.NO_DEPENDENCY_BUNDLE_CANDIDATE
        and on.state is BundleArmState.NO_DEPENDENCY_BUNDLE_CANDIDATE
    ):
        state, bundle = PairBundleState.PAIR_NO_DEPENDENCY_BUNDLE_CANDIDATE, None
    else:
        state, bundle = PairBundleState.PAIR_NO_DEPENDENCY_BUNDLE_ISOLATED, None
    return BundlePairResult(
        case_id=off.case_id,
        p0_tier=off.p0_tier,
        profile=profile,
        off=off,
        on=on,
        state=state,
        isolated_bundle=bundle,
    )
