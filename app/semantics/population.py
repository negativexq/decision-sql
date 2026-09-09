"""Reference-blind structural population semantics diagnostics.

The analyzer describes what a SQL statement does to relational population.  It
does not infer what a question intended, inspect data, or decide whether a
query is correct.  A future declared intent may be compared with this
diagnostic, but that comparison is deliberately separate from answerability.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

POPULATION_CONTRACT_VERSION = "population-behavior-1"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PopulationBehavior(StrEnum):
    REQUIRES_QUALIFYING_ROWS = "REQUIRES_QUALIFYING_ROWS"
    PRESERVES_BASE_GROUPS = "PRESERVES_BASE_GROUPS"
    CONDITIONALLY_PRESERVES_BASE_GROUPS = "CONDITIONALLY_PRESERVES_BASE_GROUPS"
    MIXED = "MIXED"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PopulationIntent(StrEnum):
    MATCHING_ONLY = "MATCHING_ONLY"
    PRESERVE_BASE_ENTITIES = "PRESERVE_BASE_ENTITIES"
    UNSPECIFIED = "UNSPECIFIED"


class PopulationCheckStatus(StrEnum):
    CONSISTENT = "CONSISTENT"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PredicateScope(StrEnum):
    ROW_POPULATION = "ROW_POPULATION"
    JOIN_LOCAL = "JOIN_LOCAL"
    AGGREGATE_LOCAL = "AGGREGATE_LOCAL"
    GROUP_SURVIVAL = "GROUP_SURVIVAL"
    UNRESOLVED = "UNRESOLVED"


class GroupSurvival(StrEnum):
    PRESERVED = "PRESERVED"
    REQUIRES_QUALIFYING_ROWS = "REQUIRES_QUALIFYING_ROWS"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PopulationJoinEdge(_Frozen):
    left_relation: str
    right_relation: str
    join_kind: str
    on_sql: str | None = None


class PopulationPredicate(_Frozen):
    scope: PredicateScope
    sql: str
    referenced_relations: tuple[str, ...] = ()
    null_rejecting_relations: tuple[str, ...] = ()
    deterministically_classified: bool = True


class PopulationDiagnostic(_Frozen):
    contract_version: str = POPULATION_CONTRACT_VERSION
    sql_hash: str
    behavior: PopulationBehavior
    group_survival: GroupSurvival
    root_relations: tuple[str, ...] = ()
    grouping_relations: tuple[str, ...] = ()
    inner_join_relations: tuple[str, ...] = ()
    outer_join_edges: tuple[PopulationJoinEdge, ...] = ()
    aggregate_functions: tuple[str, ...] = ()
    aggregate_local_predicates: tuple[PopulationPredicate, ...] = ()
    row_population_predicates: tuple[PopulationPredicate, ...] = ()
    join_local_predicates: tuple[PopulationPredicate, ...] = ()
    group_survival_predicates: tuple[PopulationPredicate, ...] = ()
    null_rejecting_relations: tuple[str, ...] = ()
    qualifying_source_relations: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    unsupported_constructs: tuple[str, ...] = ()

    @property
    def diagnostic_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"sql_hash"})
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class PopulationIntentClaim(_Frozen):
    mode: PopulationIntent
    entity_id: str | None = Field(default=None, min_length=1)


class PopulationConsistencyCheck(_Frozen):
    status: PopulationCheckStatus
    reason_code: str
    deterministic: bool


def _relation_name(node: exp.Expression) -> str:
    alias = node.alias_or_name
    if alias:
        return str(alias)
    return str(node.name) if hasattr(node, "name") else node.sql(dialect="postgres")


def _columns(node: exp.Expression | None) -> tuple[exp.Column, ...]:
    return tuple(node.find_all(exp.Column)) if node is not None else ()


def _referenced_relations(node: exp.Expression | None) -> tuple[str, ...]:
    return tuple(sorted({str(column.table) for column in _columns(node) if column.table}))


def _is_null_rejecting(node: exp.Expression | None, nullable: set[str]) -> tuple[set[str], bool]:
    """Return nullable aliases null-rejected by a conservative SQL subset."""
    if node is None:
        return set(), True
    if isinstance(node, exp.And):
        left, left_known = _is_null_rejecting(node.left, nullable)
        right, right_known = _is_null_rejecting(node.right, nullable)
        return left | right, left_known and right_known
    if isinstance(node, exp.Or):
        # A disjunction can admit a NULL branch; only a few tautological forms
        # can be safely reduced and they are intentionally left unresolved.
        return set(), False
    columns = _columns(node)
    aliases = {str(column.table) for column in columns if column.table} & nullable
    if not aliases:
        return set(), True
    safe = (
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.In,
        exp.Like,
        exp.Not,
    )
    if isinstance(node, safe):
        return aliases, True
    if isinstance(node, exp.Not) and isinstance(node.this, exp.Is):
        # `x IS NOT NULL` is represented as NOT (x IS NULL) in current SQLGlot.
        return aliases, True
    if isinstance(node, exp.Is):
        # `x IS NULL` explicitly admits NULL and is not null-rejecting.
        return set(), True
    return set(), False


def _predicate(
    node: exp.Expression, scope: PredicateScope, nullable: set[str]
) -> PopulationPredicate:
    rejected, known = _is_null_rejecting(node, nullable)
    return PopulationPredicate(
        scope=scope,
        sql=node.sql(dialect="postgres"),
        referenced_relations=_referenced_relations(node),
        null_rejecting_relations=tuple(sorted(rejected)),
        deterministically_classified=known,
    )


def _atomic_predicates(node: exp.Expression | None) -> tuple[exp.Expression, ...]:
    if node is None:
        return ()
    if isinstance(node, exp.And):
        return _atomic_predicates(node.left) + _atomic_predicates(node.right)
    return (node,)


def _select_root(expression: exp.Expression) -> exp.Select | None:
    if isinstance(expression, exp.Select):
        return expression
    return None


class PopulationBehaviorAnalyzer:
    """Describe SQL population structure without question or data inputs."""

    def inspect(self, sql: str) -> PopulationDiagnostic:
        sql_hash = sha256(sql.encode()).hexdigest()
        try:
            parsed = sqlglot.parse(sql, read="postgres")
        except Exception as error:  # SQLGlot exposes several parser exceptions.
            return PopulationDiagnostic(
                sql_hash=sql_hash,
                behavior=PopulationBehavior.UNRESOLVED,
                group_survival=GroupSurvival.UNKNOWN,
                reason_codes=("SQL_PARSE_UNRESOLVED",),
                unsupported_constructs=(type(error).__name__,),
            )
        first_expression = parsed[0] if parsed else None
        if (
            len(parsed) != 1
            or first_expression is None
            or (select := _select_root(first_expression)) is None
        ):
            return PopulationDiagnostic(
                sql_hash=sql_hash,
                behavior=PopulationBehavior.UNRESOLVED,
                group_survival=GroupSurvival.UNKNOWN,
                reason_codes=("QUERY_SHAPE_UNSUPPORTED",),
                unsupported_constructs=(
                    type(first_expression).__name__ if first_expression else "EMPTY",
                ),
            )

        from_clause = select.args.get("from_")
        root_nodes: list[exp.Expression] = []
        if from_clause is not None and from_clause.this is not None:
            root_nodes.append(from_clause.this)
        root_relations = tuple(_relation_name(node) for node in root_nodes)
        joins = tuple(select.args.get("joins") or ())
        inner: list[str] = []
        outer: list[PopulationJoinEdge] = []
        nullable: set[str] = set()
        for join in joins:
            right = join.this
            right_name = _relation_name(right)
            side = str(join.args.get("side") or "").upper()
            kind = (
                "FULL"
                if side == "FULL"
                else "RIGHT"
                if side == "RIGHT"
                else "LEFT"
                if side == "LEFT"
                else "INNER"
            )
            if kind == "INNER":
                inner.append(right_name)
            else:
                if kind == "LEFT":
                    nullable.add(right_name)
                elif kind == "RIGHT":
                    nullable.update(root_relations)
                else:
                    nullable.update(root_relations)
                    nullable.add(right_name)
                outer.append(
                    PopulationJoinEdge(
                        left_relation=root_relations[-1] if root_relations else "",
                        right_relation=right_name,
                        join_kind=kind,
                        on_sql=join.args.get("on").sql(dialect="postgres")
                        if join.args.get("on") is not None
                        else None,
                    )
                )

        group = select.args.get("group")
        group_expressions = tuple(group.expressions) if group is not None else ()
        grouping_relations = tuple(
            sorted(
                {r for expression in group_expressions for r in _referenced_relations(expression)}
            )
        )
        aggregate_nodes = tuple(select.find_all(exp.AggFunc))
        aggregate_functions = tuple(
            sorted({type(node).__name__.upper() for node in aggregate_nodes})
        )
        aggregate_local: list[PopulationPredicate] = []
        for filt in select.find_all(exp.Filter):
            aggregate_local.append(
                _predicate(filt.expression, PredicateScope.AGGREGATE_LOCAL, nullable)
            )
        for case in select.find_all(exp.Case):
            if not isinstance(case.parent, exp.AggFunc):
                continue
            for condition in case.args.get("ifs") or ():
                if isinstance(condition, exp.If) and condition.this is not None:
                    aggregate_local.append(
                        _predicate(condition.this, PredicateScope.AGGREGATE_LOCAL, nullable)
                    )

        where = select.args.get("where")
        row_population = [
            _predicate(node, PredicateScope.ROW_POPULATION, nullable)
            for node in _atomic_predicates(where.this if where is not None else None)
        ]
        join_local = [
            _predicate(join.args["on"], PredicateScope.JOIN_LOCAL, nullable)
            for join in joins
            if join.args.get("on") is not None
        ]
        having = select.args.get("having")
        group_survival: list[PopulationPredicate] = []
        if having is not None and any(
            isinstance(node, exp.Count) for node in having.find_all(exp.Count)
        ):
            group_survival.extend(
                _predicate(node, PredicateScope.GROUP_SURVIVAL, nullable)
                for node in _atomic_predicates(having.this)
            )
        null_rejected = tuple(
            sorted({r for item in row_population for r in item.null_rejecting_relations})
        )
        qualifying = tuple(
            sorted(
                {r for item in row_population + group_survival for r in item.referenced_relations}
            )
        )
        unsupported: set[str] = set()
        if select.args.get("with_") is not None:
            unsupported.add("CTE_BOUNDARY")
        if any(
            isinstance(node, (exp.Subquery, exp.Exists, exp.Not))
            for node in select.args.get("where", exp.Literal.number(0)).walk()
        ):
            unsupported.add("NESTED_RELATIONAL_PREDICATE")
        if any(
            isinstance(node, (exp.Subquery, exp.Paren)) for node in select.find_all(exp.Subquery)
        ):
            unsupported.add("SUBQUERY_BOUNDARY")
        has_group = bool(group_expressions or aggregate_nodes)
        if not has_group:
            behavior = PopulationBehavior.NOT_APPLICABLE
            survival = GroupSurvival.NOT_APPLICABLE
            reasons: tuple[str, ...] = ("NO_GROUPED_POPULATION",)
        elif unsupported and not (outer or inner):
            behavior = PopulationBehavior.UNRESOLVED
            survival = GroupSurvival.UNKNOWN
            reasons = ("POPULATION_BEHAVIOR_UNRESOLVED",)
        else:
            base_driven = bool(set(grouping_relations) & set(root_relations))
            rejected_preservation = bool(set(null_rejected) & nullable)
            positive_having = bool(group_survival)
            measure_only_qualification = bool(aggregate_local) and not row_population
            if outer and base_driven and not rejected_preservation and not positive_having:
                behavior = PopulationBehavior.PRESERVES_BASE_GROUPS
                survival = GroupSurvival.PRESERVED
                reasons = ("BASE_DRIVEN_GROUPING", "OUTER_JOIN_PRESERVES_BASE")
            elif measure_only_qualification and not positive_having:
                behavior = PopulationBehavior.CONDITIONALLY_PRESERVES_BASE_GROUPS
                survival = GroupSurvival.CONDITIONAL
                reasons = ("AGGREGATE_LOCAL_PREDICATE", "GROUP_SURVIVES_NONQUALIFYING_ROWS")
            elif outer and base_driven and not rejected_preservation and positive_having:
                behavior = PopulationBehavior.CONDITIONALLY_PRESERVES_BASE_GROUPS
                survival = GroupSurvival.CONDITIONAL
                reasons = ("BASE_DRIVEN_GROUPING", "GROUP_SURVIVAL_PREDICATE")
            else:
                behavior = PopulationBehavior.REQUIRES_QUALIFYING_ROWS
                survival = GroupSurvival.REQUIRES_QUALIFYING_ROWS
                reasons = ("GROUP_REQUIRES_QUALIFYING_ROWS",)
            if rejected_preservation:
                reasons = reasons + ("OUTER_JOIN_NULL_REJECTED",)
            if aggregate_local:
                reasons = reasons + ("AGGREGATE_LOCAL_PREDICATE",)
            if row_population:
                reasons = reasons + ("ROW_POPULATION_PREDICATE",)
            if unsupported:
                reasons = reasons + ("NESTED_SCOPE_REQUIRES_REVIEW",)
        return PopulationDiagnostic(
            sql_hash=sql_hash,
            behavior=behavior,
            group_survival=survival,
            root_relations=root_relations,
            grouping_relations=grouping_relations,
            inner_join_relations=tuple(sorted(inner)),
            outer_join_edges=tuple(outer),
            aggregate_functions=aggregate_functions,
            aggregate_local_predicates=tuple(aggregate_local),
            row_population_predicates=tuple(row_population),
            join_local_predicates=tuple(join_local),
            group_survival_predicates=tuple(group_survival),
            null_rejecting_relations=null_rejected,
            qualifying_source_relations=qualifying,
            reason_codes=tuple(dict.fromkeys(reasons)),
            unsupported_constructs=tuple(sorted(unsupported)),
        )


def check_population_intent(
    intent: PopulationIntentClaim, diagnostic: PopulationDiagnostic
) -> PopulationConsistencyCheck:
    """Compare an explicit future intent with observed SQL behavior only."""
    if intent.mode is PopulationIntent.UNSPECIFIED:
        return PopulationConsistencyCheck(
            status=PopulationCheckStatus.NOT_APPLICABLE,
            reason_code="POPULATION_INTENT_UNSPECIFIED",
            deterministic=True,
        )
    if diagnostic.behavior is PopulationBehavior.UNRESOLVED:
        return PopulationConsistencyCheck(
            status=PopulationCheckStatus.UNRESOLVED,
            reason_code="POPULATION_BEHAVIOR_UNRESOLVED",
            deterministic=False,
        )
    if intent.mode is PopulationIntent.MATCHING_ONLY:
        if diagnostic.behavior is PopulationBehavior.PRESERVES_BASE_GROUPS:
            return PopulationConsistencyCheck(
                status=PopulationCheckStatus.CONTRADICTED,
                reason_code="MATCHING_ONLY_BUT_BASE_GROUPS_PRESERVED",
                deterministic=True,
            )
        return PopulationConsistencyCheck(
            status=PopulationCheckStatus.CONSISTENT,
            reason_code="MATCHING_ONLY_BEHAVIOR",
            deterministic=diagnostic.behavior is not PopulationBehavior.NOT_APPLICABLE,
        )
    if intent.mode is PopulationIntent.PRESERVE_BASE_ENTITIES:
        if diagnostic.behavior is PopulationBehavior.REQUIRES_QUALIFYING_ROWS:
            return PopulationConsistencyCheck(
                status=PopulationCheckStatus.CONTRADICTED,
                reason_code="PRESERVE_BASE_BUT_QUALIFYING_ROWS_REQUIRED",
                deterministic=True,
            )
        if diagnostic.behavior in {
            PopulationBehavior.PRESERVES_BASE_GROUPS,
            PopulationBehavior.CONDITIONALLY_PRESERVES_BASE_GROUPS,
        }:
            return PopulationConsistencyCheck(
                status=PopulationCheckStatus.CONSISTENT,
                reason_code="BASE_PRESERVATION_BEHAVIOR",
                deterministic=True,
            )
    return PopulationConsistencyCheck(
        status=PopulationCheckStatus.UNRESOLVED,
        reason_code="POPULATION_INTENT_COMPARISON_UNRESOLVED",
        deterministic=False,
    )


def canonical_population_contract() -> dict[str, Any]:
    return {
        "contract_version": POPULATION_CONTRACT_VERSION,
        "behavior_values": [item.value for item in PopulationBehavior],
        "intent_values": [item.value for item in PopulationIntent],
        "check_status_values": [item.value for item in PopulationCheckStatus],
        "predicate_scopes": [item.value for item in PredicateScope],
        "group_survival_values": [item.value for item in GroupSurvival],
        "inputs": ["SQL text"],
        "forbidden_inputs": [
            "question",
            "truth",
            "reference SQL",
            "database contents",
            "ResultContract",
            "case ID",
            "domain name",
        ],
        "negative_capabilities": [
            "intended population",
            "answerability",
            "semantic uniqueness",
            "correctness",
        ],
    }
