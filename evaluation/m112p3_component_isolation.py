"""Bounded, diagnostic-only component isolation over the frozen D2 harness."""

from __future__ import annotations

import copy
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlglot import exp, parse_one

from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlExecutionError
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticExecutionHarness,
    DiagnosticFixture,
    DiagnosticState,
    _snapshot,
    compare_snapshots,
    stable_hash,
)


class ComponentKind(StrEnum):
    TOP_LEVEL_PROJECTION = "TOP_LEVEL_PROJECTION"
    TOP_LEVEL_ORDER_BY = "TOP_LEVEL_ORDER_BY"
    TOP_LEVEL_WHERE = "TOP_LEVEL_WHERE"
    TOP_LEVEL_GROUP_BY = "TOP_LEVEL_GROUP_BY"
    TOP_LEVEL_HAVING = "TOP_LEVEL_HAVING"
    TOP_LEVEL_LIMIT = "TOP_LEVEL_LIMIT"
    TOP_LEVEL_OFFSET = "TOP_LEVEL_OFFSET"
    TOP_LEVEL_DISTINCT = "TOP_LEVEL_DISTINCT"
    RELATIONAL_FROM_JOIN_BLOCK = "RELATIONAL_FROM_JOIN_BLOCK"
    WINDOW_EXPRESSION_SLOT = "WINDOW_EXPRESSION_SLOT"
    PROJECTION_EXPRESSION_SLOT = "PROJECTION_EXPRESSION_SLOT"
    SET_OPERATION = "SET_OPERATION"


SUPPORTED_COMPONENTS = (
    ComponentKind.TOP_LEVEL_PROJECTION,
    ComponentKind.TOP_LEVEL_ORDER_BY,
    ComponentKind.TOP_LEVEL_WHERE,
    ComponentKind.TOP_LEVEL_GROUP_BY,
    ComponentKind.TOP_LEVEL_HAVING,
    ComponentKind.TOP_LEVEL_LIMIT,
    ComponentKind.TOP_LEVEL_OFFSET,
    ComponentKind.TOP_LEVEL_DISTINCT,
    ComponentKind.RELATIONAL_FROM_JOIN_BLOCK,
    ComponentKind.WINDOW_EXPRESSION_SLOT,
)


class SubstitutionDirection(StrEnum):
    REPAIR = "REPAIR"
    TRANSFER = "TRANSFER"


class SubstitutionValidation(StrEnum):
    VALID = "VALID"
    UNSUPPORTED_COMPONENT = "UNSUPPORTED_COMPONENT"
    SUBSTITUTION_NOT_SAFE = "SUBSTITUTION_NOT_SAFE"
    SUBSTITUTION_NOT_ISOLATED = "SUBSTITUTION_NOT_ISOLATED"
    P0_ENTITLEMENT_BLOCKED = "P0_ENTITLEMENT_BLOCKED"


class ArmIsolationState(StrEnum):
    BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED = "BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED"
    REPAIR_SIDE_WITNESS_REMOVED_ONLY = "REPAIR_SIDE_WITNESS_REMOVED_ONLY"
    TRANSFER_SIDE_WITNESS_REPRODUCED_ONLY = "TRANSFER_SIDE_WITNESS_REPRODUCED_ONLY"
    MULTIPLE_COMPONENTS_BIDIRECTIONALLY_EXPLAIN_WITNESS = (
        "MULTIPLE_COMPONENTS_BIDIRECTIONALLY_EXPLAIN_WITNESS"
    )
    COMPONENT_INTERACTION_NOT_ISOLATED = "COMPONENT_INTERACTION_NOT_ISOLATED"
    NO_SUPPORTED_COMPONENT_ISOLATED = "NO_SUPPORTED_COMPONENT_ISOLATED"
    SUBSTITUTION_NOT_SAFE = "SUBSTITUTION_NOT_SAFE"
    P0_ENTITLEMENT_BLOCKED = "P0_ENTITLEMENT_BLOCKED"
    EXECUTION_INCONCLUSIVE = "EXECUTION_INCONCLUSIVE"


class PairIsolationState(StrEnum):
    PAIR_STABLE_COMPONENT_ISOLATED = "PAIR_STABLE_COMPONENT_ISOLATED"
    PAIR_ONE_ARM_COMPONENT_ISOLATED = "PAIR_ONE_ARM_COMPONENT_ISOLATED"
    PAIR_COMPONENT_DISAGREEMENT = "PAIR_COMPONENT_DISAGREEMENT"
    PAIR_MULTIPLE_COMPONENTS_NON_UNIQUE = "PAIR_MULTIPLE_COMPONENTS_NON_UNIQUE"
    PAIR_INTERACTION_NOT_ISOLATED = "PAIR_INTERACTION_NOT_ISOLATED"
    PAIR_NO_SUPPORTED_COMPONENT_ISOLATED = "PAIR_NO_SUPPORTED_COMPONENT_ISOLATED"
    PAIR_P0_ENTITLEMENT_BLOCKED = "PAIR_P0_ENTITLEMENT_BLOCKED"
    PAIR_EXECUTION_INCONCLUSIVE = "PAIR_EXECUTION_INCONCLUSIVE"


class IsolationPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    pair_id: str
    candidate_sql: str
    reference_sql: str
    comparison_mode: ComparisonMode = ComparisonMode.VALUE_BAG
    order_entitled: bool = False
    scenario_tags: tuple[str, ...] = ()

    @property
    def candidate_sql_hash(self) -> str:
        return stable_hash(self.candidate_sql)

    @property
    def reference_sql_hash(self) -> str:
        return stable_hash(self.reference_sql)


class HybridProgram(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: SubstitutionDirection
    component: ComponentKind
    sql: str
    sql_hash: str
    validation: SubstitutionValidation
    reason: str | None = None


class ComponentTrial(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: ComponentKind
    validation: SubstitutionValidation
    repair_sql_hash: str | None = None
    transfer_sql_hash: str | None = None
    repair_equal_reference: bool | None = None
    transfer_equal_candidate: bool | None = None
    repair_witness_removed: bool = False
    transfer_witness_reproduced: bool = False
    invariance_checked: bool = False
    execution_inconclusive: bool = False
    reason: str | None = None


class ArmInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    arm: str
    p0_tier: str = "FULL"
    p0_projection_entitled: bool = True
    pair: IsolationPair
    profile: tuple[str, ...] = ()


class ArmIsolationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    arm: str
    p0_tier: str
    baseline_candidate_hash: str
    baseline_reference_hash: str
    baseline_witness_fixture_ids: tuple[str, ...]
    changed_components: tuple[ComponentKind, ...]
    supported_components: tuple[ComponentKind, ...]
    trials: tuple[ComponentTrial, ...]
    state: ArmIsolationState
    isolated_component: ComponentKind | None = None
    reason: str | None = None


class PairIsolationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    p0_tier: str
    profile: tuple[str, ...]
    off: ArmIsolationResult
    on: ArmIsolationResult
    state: PairIsolationState
    isolated_component: ComponentKind | None = None


class _ComparisonObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: DiagnosticState
    equal: bool | None = None


class ComponentIsolationError(ValueError):
    def __init__(self, validation: SubstitutionValidation, reason: str) -> None:
        super().__init__(reason)
        self.validation = validation
        self.reason = reason


def parse_postgres(sql: str) -> exp.Expression:
    return parse_one(sql, read="postgres")


def _sql(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [_sql(item) for item in value]
    if isinstance(value, exp.Expression):
        return value.sql(dialect="postgres")
    return value


def _select(root: exp.Expression) -> exp.Select:
    if not isinstance(root, exp.Select):
        raise ComponentIsolationError(
            SubstitutionValidation.UNSUPPORTED_COMPONENT,
            "component catalog currently requires a top-level SELECT",
        )
    return root


def _window_slots(root: exp.Expression) -> list[tuple[int, str]]:
    select = _select(root)
    return [
        (index, expression.sql(dialect="postgres"))
        for index, expression in enumerate(select.expressions)
        if expression.find(exp.Window) is not None
    ]


def extract_component(root: exp.Expression, component: ComponentKind) -> Any:
    if component is ComponentKind.SET_OPERATION:
        return root if isinstance(root, (exp.Union, exp.Intersect, exp.Except)) else None
    select = _select(root)
    if component is ComponentKind.TOP_LEVEL_PROJECTION:
        return select.expressions
    if component is ComponentKind.TOP_LEVEL_ORDER_BY:
        return select.args.get("order")
    if component is ComponentKind.TOP_LEVEL_WHERE:
        return select.args.get("where")
    if component is ComponentKind.TOP_LEVEL_GROUP_BY:
        return select.args.get("group")
    if component is ComponentKind.TOP_LEVEL_HAVING:
        return select.args.get("having")
    if component is ComponentKind.TOP_LEVEL_LIMIT:
        return select.args.get("limit")
    if component is ComponentKind.TOP_LEVEL_OFFSET:
        return select.args.get("offset")
    if component is ComponentKind.TOP_LEVEL_DISTINCT:
        return select.args.get("distinct")
    if component is ComponentKind.RELATIONAL_FROM_JOIN_BLOCK:
        return (select.args.get("from_"), select.args.get("joins") or [])
    if component is ComponentKind.WINDOW_EXPRESSION_SLOT:
        return _window_slots(root)
    if component is ComponentKind.PROJECTION_EXPRESSION_SLOT:
        return [
            (index, expression.sql(dialect="postgres"))
            for index, expression in enumerate(select.expressions)
        ]
    raise AssertionError(f"unhandled component: {component}")


def component_fingerprint(root: exp.Expression, component: ComponentKind) -> str:
    value = _sql(extract_component(root, component))
    return stable_hash({"component": component.value, "value": value})


def changed_components(
    candidate: exp.Expression, reference: exp.Expression
) -> tuple[ComponentKind, ...]:
    changed: list[ComponentKind] = []
    for component in SUPPORTED_COMPONENTS:
        try:
            candidate_fp = component_fingerprint(candidate, component)
            reference_fp = component_fingerprint(reference, component)
        except ComponentIsolationError:
            continue
        if candidate_fp != reference_fp:
            changed.append(component)
    return tuple(changed)


def _replace_select_component(
    base: exp.Select, source: exp.Select, component: ComponentKind
) -> exp.Select:
    result = copy.deepcopy(base)
    if component is ComponentKind.TOP_LEVEL_PROJECTION:
        result.set("expressions", copy.deepcopy(source.expressions))
    elif component is ComponentKind.TOP_LEVEL_ORDER_BY:
        result.set("order", copy.deepcopy(source.args.get("order")))
    elif component is ComponentKind.TOP_LEVEL_WHERE:
        result.set("where", copy.deepcopy(source.args.get("where")))
    elif component is ComponentKind.TOP_LEVEL_GROUP_BY:
        result.set("group", copy.deepcopy(source.args.get("group")))
    elif component is ComponentKind.TOP_LEVEL_HAVING:
        result.set("having", copy.deepcopy(source.args.get("having")))
    elif component is ComponentKind.TOP_LEVEL_LIMIT:
        result.set("limit", copy.deepcopy(source.args.get("limit")))
    elif component is ComponentKind.TOP_LEVEL_OFFSET:
        result.set("offset", copy.deepcopy(source.args.get("offset")))
    elif component is ComponentKind.TOP_LEVEL_DISTINCT:
        result.set("distinct", copy.deepcopy(source.args.get("distinct")))
    elif component is ComponentKind.RELATIONAL_FROM_JOIN_BLOCK:
        result.set("from_", copy.deepcopy(source.args.get("from_")))
        result.set("joins", copy.deepcopy(source.args.get("joins") or []))
    elif component is ComponentKind.WINDOW_EXPRESSION_SLOT:
        base_slots = _window_slots(base)
        source_slots = _window_slots(source)
        if len(base_slots) != len(source_slots) or [i for i, _ in base_slots] != [
            i for i, _ in source_slots
        ]:
            raise ComponentIsolationError(
                SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
                "window expression slots cannot be matched deterministically",
            )
        for (index, _), (source_index, _) in zip(base_slots, source_slots, strict=True):
            result.expressions[index] = copy.deepcopy(source.expressions[source_index])
    elif component is ComponentKind.PROJECTION_EXPRESSION_SLOT:
        if len(base.expressions) != len(source.expressions):
            raise ComponentIsolationError(
                SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
                "projection slots have different cardinality",
            )
        differences = [
            index
            for index, (left, right) in enumerate(
                zip(base.expressions, source.expressions, strict=True)
            )
            if left.sql(dialect="postgres") != right.sql(dialect="postgres")
        ]
        if len(differences) != 1:
            raise ComponentIsolationError(
                SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
                "projection slot mapping is not unique",
            )
        index = differences[0]
        result.expressions[index] = copy.deepcopy(source.expressions[index])
    else:
        raise ComponentIsolationError(
            SubstitutionValidation.UNSUPPORTED_COMPONENT,
            f"component is not replaceable under SELECT: {component.value}",
        )
    return result


def replace_component(
    base: exp.Expression, source: exp.Expression, component: ComponentKind
) -> exp.Expression:
    if component is ComponentKind.SET_OPERATION:
        raise ComponentIsolationError(
            SubstitutionValidation.UNSUPPORTED_COMPONENT,
            "set-operation replacement is not a local SELECT mutation",
        )
    return _replace_select_component(_select(base), _select(source), component)


def _projection_aliases(root: exp.Expression) -> set[str]:
    return {
        expression.alias
        for expression in _select(root).expressions
        if isinstance(expression, exp.Alias) and expression.alias
    }


def _dependent_clause_uses_removed_alias(
    original: exp.Expression, replacement: exp.Expression
) -> bool:
    removed_aliases = _projection_aliases(original) - _projection_aliases(replacement)
    if not removed_aliases:
        return False
    clauses = [original.args.get(name) for name in ("order", "group", "having")]
    for clause in clauses:
        if clause is not None:
            if any(column.name in removed_aliases for column in clause.find_all(exp.Column)):
                return True
    return False


def build_hybrid(
    base_sql: str,
    source_sql: str,
    component: ComponentKind,
    direction: SubstitutionDirection,
    *,
    p0_projection_entitled: bool = True,
) -> HybridProgram:
    if component is ComponentKind.TOP_LEVEL_PROJECTION and not p0_projection_entitled:
        raise ComponentIsolationError(
            SubstitutionValidation.P0_ENTITLEMENT_BLOCKED,
            "projection is not entitled by the governing P0 contract",
        )
    base = parse_postgres(base_sql)
    source = parse_postgres(source_sql)
    hybrid = replace_component(base, source, component)
    if component is ComponentKind.TOP_LEVEL_PROJECTION and _dependent_clause_uses_removed_alias(
        base, hybrid
    ):
        raise ComponentIsolationError(
            SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
            "projection replacement leaves a dependent clause using a removed alias",
        )
    sql = hybrid.sql(dialect="postgres")
    round_trip = parse_postgres(sql)
    expected_target = component_fingerprint(source, component)
    if component_fingerprint(round_trip, component) != expected_target:
        raise ComponentIsolationError(
            SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
            "round-trip changed the target component fingerprint",
        )
    for non_target in SUPPORTED_COMPONENTS:
        if non_target is component:
            continue
        try:
            if component_fingerprint(round_trip, non_target) != component_fingerprint(
                base, non_target
            ):
                raise ComponentIsolationError(
                    SubstitutionValidation.SUBSTITUTION_NOT_ISOLATED,
                    f"non-target component changed: {non_target.value}",
                )
        except ComponentIsolationError:
            raise
    return HybridProgram(
        direction=direction,
        component=component,
        sql=sql,
        sql_hash=stable_hash(sql),
        validation=SubstitutionValidation.VALID,
    )


def validate_non_target_invariance(
    base_sql: str, hybrid_sql: str, component: ComponentKind
) -> bool:
    base = parse_postgres(base_sql)
    hybrid = parse_postgres(hybrid_sql)
    for candidate in SUPPORTED_COMPONENTS:
        if candidate is component:
            continue
        if component_fingerprint(base, candidate) != component_fingerprint(hybrid, candidate):
            return False
    return True


class ComponentIsolationHarness:
    """D3 AST substitutions over the frozen M11.2P2 execution harness."""

    def __init__(self, d2_harness: DiagnosticExecutionHarness) -> None:
        self.d2 = d2_harness
        self.bank = d2_harness.fixture_bank

    def _compare_sqls(
        self,
        candidate_sql: str,
        reference_sql: str,
        pair: IsolationPair,
        fixture: DiagnosticFixture,
    ) -> _ComparisonObservation:
        candidate = self.d2._execute(candidate_sql)
        reference = self.d2._execute(reference_sql)
        if not isinstance(candidate, QueryExecution) or not isinstance(reference, QueryExecution):
            if isinstance(candidate, SqlExecutionError) or isinstance(reference, SqlExecutionError):
                return _ComparisonObservation(state=DiagnosticState.EXECUTION_INCONCLUSIVE)
            return _ComparisonObservation(state=DiagnosticState.EXECUTION_INCONCLUSIVE)
        if candidate.truncated or reference.truncated:
            return _ComparisonObservation(state=DiagnosticState.EXECUTION_INCONCLUSIVE)
        try:
            order_sensitive = (
                pair.comparison_mode is ComparisonMode.VALUE_ORDERED and pair.order_entitled
            )
            candidate_snapshot = _snapshot(
                candidate, stable_hash(candidate_sql), fixture, order_sensitive=order_sensitive
            )
            reference_snapshot = _snapshot(
                reference, stable_hash(reference_sql), fixture, order_sensitive=order_sensitive
            )
            equal = compare_snapshots(
                candidate_snapshot,
                reference_snapshot,
                pair.comparison_mode,
                order_entitled=pair.order_entitled,
            )
        except (TypeError, ValueError):
            return _ComparisonObservation(state=DiagnosticState.EXECUTION_INCONCLUSIVE)
        return _ComparisonObservation(state=DiagnosticState.NO_COUNTEREXAMPLE_FOUND, equal=equal)

    def _admitted(self, sql: str) -> bool:
        return isinstance(
            self.d2.service.plan(SqlCandidate(sql=sql, correlation_id="m112p3-diagnostic")),
            QueryPlan,
        )

    def witness_fixture_ids(self, pair: IsolationPair) -> tuple[str, ...]:
        witnesses: list[str] = []
        for fixture in self.bank.fixtures:
            observation = self._compare_sqls(pair.candidate_sql, pair.reference_sql, pair, fixture)
            if observation.state is DiagnosticState.EXECUTION_INCONCLUSIVE:
                continue
            if observation.equal is False:
                witnesses.append(fixture.fixture_id)
        return tuple(witnesses)

    def run_arm(self, arm: ArmInput) -> ArmIsolationResult:
        candidate = parse_postgres(arm.pair.candidate_sql)
        reference = parse_postgres(arm.pair.reference_sql)
        witnesses = self.witness_fixture_ids(arm.pair)
        changed = changed_components(candidate, reference)
        if not witnesses:
            return ArmIsolationResult(
                case_id=arm.case_id,
                arm=arm.arm,
                p0_tier=arm.p0_tier,
                baseline_candidate_hash=arm.pair.candidate_sql_hash,
                baseline_reference_hash=arm.pair.reference_sql_hash,
                baseline_witness_fixture_ids=(),
                changed_components=changed,
                supported_components=(),
                trials=(),
                state=ArmIsolationState.EXECUTION_INCONCLUSIVE,
                reason="frozen D2 baseline witness did not reproduce",
            )
        trials: list[ComponentTrial] = []
        supported: list[ComponentKind] = []
        isolated: list[ComponentKind] = []
        partial = False
        inconclusive = False
        p0_blocked = False
        unsafe_only = True
        for component in changed:
            try:
                repair = build_hybrid(
                    arm.pair.candidate_sql,
                    arm.pair.reference_sql,
                    component,
                    SubstitutionDirection.REPAIR,
                    p0_projection_entitled=arm.p0_projection_entitled,
                )
                transfer = build_hybrid(
                    arm.pair.reference_sql,
                    arm.pair.candidate_sql,
                    component,
                    SubstitutionDirection.TRANSFER,
                    p0_projection_entitled=arm.p0_projection_entitled,
                )
            except ComponentIsolationError as error:
                trials.append(
                    ComponentTrial(
                        component=component,
                        validation=error.validation,
                        reason=error.reason,
                    )
                )
                p0_blocked |= error.validation is SubstitutionValidation.P0_ENTITLEMENT_BLOCKED
                continue
            if not self._admitted(repair.sql) or not self._admitted(transfer.sql):
                trials.append(
                    ComponentTrial(
                        component=component,
                        validation=SubstitutionValidation.SUBSTITUTION_NOT_SAFE,
                        repair_sql_hash=repair.sql_hash,
                        transfer_sql_hash=transfer.sql_hash,
                        reason="hybrid failed the frozen diagnostic SQL admission boundary",
                    )
                )
                continue
            supported.append(component)
            unsafe_only = False
            repair_equal = True
            transfer_equal = True
            trial_inconclusive = False
            for fixture_id in witnesses:
                fixture = self.bank.fixture(fixture_id)
                repair_observation = self._compare_sqls(
                    repair.sql, arm.pair.reference_sql, arm.pair, fixture
                )
                transfer_observation = self._compare_sqls(
                    transfer.sql, arm.pair.candidate_sql, arm.pair, fixture
                )
                if (
                    repair_observation.state is DiagnosticState.EXECUTION_INCONCLUSIVE
                    or transfer_observation.state is DiagnosticState.EXECUTION_INCONCLUSIVE
                ):
                    trial_inconclusive = True
                    continue
                repair_equal &= repair_observation.equal is True
                transfer_equal &= transfer_observation.equal is True
            if trial_inconclusive:
                inconclusive = True
            repair_removed = repair_equal and not trial_inconclusive
            transfer_reproduced = transfer_equal and not trial_inconclusive
            partial |= (repair_removed or transfer_reproduced) and not (
                repair_removed and transfer_reproduced
            )
            if repair_removed and transfer_reproduced:
                isolated.append(component)
            trials.append(
                ComponentTrial(
                    component=component,
                    validation=SubstitutionValidation.VALID,
                    repair_sql_hash=repair.sql_hash,
                    transfer_sql_hash=transfer.sql_hash,
                    repair_equal_reference=repair_removed,
                    transfer_equal_candidate=transfer_reproduced,
                    repair_witness_removed=repair_removed,
                    transfer_witness_reproduced=transfer_reproduced,
                    invariance_checked=True,
                    execution_inconclusive=trial_inconclusive,
                )
            )
        isolated_component: ComponentKind | None = None
        if len(isolated) > 1:
            state = ArmIsolationState.MULTIPLE_COMPONENTS_BIDIRECTIONALLY_EXPLAIN_WITNESS
        elif len(isolated) == 1:
            state = ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
            isolated_component = isolated[0]
        elif partial:
            state = ArmIsolationState.COMPONENT_INTERACTION_NOT_ISOLATED
        elif p0_blocked and not supported:
            state = ArmIsolationState.P0_ENTITLEMENT_BLOCKED
        elif inconclusive:
            state = ArmIsolationState.EXECUTION_INCONCLUSIVE
        elif unsafe_only and trials:
            state = ArmIsolationState.SUBSTITUTION_NOT_SAFE
        else:
            state = ArmIsolationState.NO_SUPPORTED_COMPONENT_ISOLATED
        return ArmIsolationResult(
            case_id=arm.case_id,
            arm=arm.arm,
            p0_tier=arm.p0_tier,
            baseline_candidate_hash=arm.pair.candidate_sql_hash,
            baseline_reference_hash=arm.pair.reference_sql_hash,
            baseline_witness_fixture_ids=witnesses,
            changed_components=changed,
            supported_components=tuple(supported),
            trials=tuple(trials),
            state=state,
            isolated_component=isolated_component,
        )


def aggregate_pair(
    off: ArmIsolationResult, on: ArmIsolationResult, profile: tuple[str, ...]
) -> PairIsolationResult:
    off_component = off.isolated_component
    on_component = on.isolated_component
    if (
        off.state is ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
        and on.state is ArmIsolationState.BIDIRECTIONAL_COMPONENT_WITNESS_ISOLATED
    ):
        if off_component == on_component:
            state = PairIsolationState.PAIR_STABLE_COMPONENT_ISOLATED
            component = off_component
        else:
            state = PairIsolationState.PAIR_COMPONENT_DISAGREEMENT
            component = None
    elif (
        off.state is ArmIsolationState.MULTIPLE_COMPONENTS_BIDIRECTIONALLY_EXPLAIN_WITNESS
        or on.state is ArmIsolationState.MULTIPLE_COMPONENTS_BIDIRECTIONALLY_EXPLAIN_WITNESS
    ):
        state = PairIsolationState.PAIR_MULTIPLE_COMPONENTS_NON_UNIQUE
        component = None
    elif off_component or on_component:
        state = PairIsolationState.PAIR_ONE_ARM_COMPONENT_ISOLATED
        component = off_component or on_component
    elif (
        off.state is ArmIsolationState.P0_ENTITLEMENT_BLOCKED
        or on.state is ArmIsolationState.P0_ENTITLEMENT_BLOCKED
    ):
        state = PairIsolationState.PAIR_P0_ENTITLEMENT_BLOCKED
        component = None
    elif (
        off.state is ArmIsolationState.COMPONENT_INTERACTION_NOT_ISOLATED
        or on.state is ArmIsolationState.COMPONENT_INTERACTION_NOT_ISOLATED
    ):
        state = PairIsolationState.PAIR_INTERACTION_NOT_ISOLATED
        component = None
    elif (
        off.state is ArmIsolationState.EXECUTION_INCONCLUSIVE
        or on.state is ArmIsolationState.EXECUTION_INCONCLUSIVE
    ):
        state = PairIsolationState.PAIR_EXECUTION_INCONCLUSIVE
        component = None
    else:
        state = PairIsolationState.PAIR_NO_SUPPORTED_COMPONENT_ISOLATED
        component = None
    return PairIsolationResult(
        case_id=off.case_id,
        p0_tier=off.p0_tier,
        profile=profile,
        off=off,
        on=on,
        state=state,
        isolated_component=component,
    )
