from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.models.domain import FailureStage
from app.semantics.grain import (
    AggregationBehavior,
    GrainDiagnosticCode,
    GrainEntity,
    GrainKey,
    GrainRelationship,
    MeasureCatalog,
    MeasureSemantics,
)
from app.semantics.grain_runtime import (
    GrainRuntimeStatus,
    RuntimeGrainSafetyCoordinator,
)
from app.sql.authority import ExecutionAuthority
from app.sql.models import (
    ExplainEstimate,
    PolicyCode,
    PolicyRejection,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.sql.parser import SQLParseFailure, SQLParser
from app.sql.policy import SQLPolicy
from app.sql.service import SqlSafetyService

UNSAFE_SQL = """
SELECT p.group_id,
       SUM(p.parent_amount) - COALESCE(SUM(c.child_amount), 0) AS net
FROM parent AS p
LEFT JOIN child AS c
    ON c.parent_id = p.parent_id
GROUP BY p.group_id
"""


def _attribute(entity: str, column: str) -> str:
    return f"attribute:test:{entity}:{column}"


def _catalog(*, second_child: bool = False) -> MeasureCatalog:
    parent = GrainEntity(
        entity_id="entity:test:parent",
        physical_table="parent",
        key_attribute_ids=(_attribute("parent", "parent_id"),),
    )
    child = GrainEntity(
        entity_id="entity:test:child",
        physical_table="child",
        key_attribute_ids=(_attribute("child", "child_id"),),
    )
    entities = [parent, child]
    relationships = [
        GrainRelationship(
            relationship_id="relationship:test:child_parent",
            from_entity_id=child.entity_id,
            from_attribute_ids=(_attribute("child", "parent_id"),),
            to_entity_id=parent.entity_id,
            to_attribute_ids=(_attribute("parent", "parent_id"),),
            cardinality="many_to_one",
            provenance=("PUBLIC_RELATIONSHIP_CARDINALITY",),
        )
    ]
    measures = [
        MeasureSemantics(
            measure_id="measure:test:parent:parent_amount",
            source_attribute_id=_attribute("parent", "parent_amount"),
            entity_id=parent.entity_id,
            physical_table="parent",
            physical_column_or_path="parent_amount",
            native_grain=GrainKey(
                entity_id=parent.entity_id, key_attribute_ids=parent.key_attribute_ids
            ),
            aggregation_behavior=AggregationBehavior.ADDITIVE,
            provenance=("PUBLIC_SCHEMA",),
        ),
        MeasureSemantics(
            measure_id="measure:test:child:child_amount",
            source_attribute_id=_attribute("child", "child_amount"),
            entity_id=child.entity_id,
            physical_table="child",
            physical_column_or_path="child_amount",
            native_grain=GrainKey(
                entity_id=child.entity_id, key_attribute_ids=child.key_attribute_ids
            ),
            aggregation_behavior=AggregationBehavior.ADDITIVE,
            provenance=("PUBLIC_SCHEMA",),
        ),
    ]
    if second_child:
        child_two = GrainEntity(
            entity_id="entity:test:child_two",
            physical_table="child_two",
            key_attribute_ids=(_attribute("child_two", "child_two_id"),),
        )
        entities.append(child_two)
        relationships.append(
            GrainRelationship(
                relationship_id="relationship:test:child_two_parent",
                from_entity_id=child_two.entity_id,
                from_attribute_ids=(_attribute("child_two", "parent_id"),),
                to_entity_id=parent.entity_id,
                to_attribute_ids=(_attribute("parent", "parent_id"),),
                cardinality="many_to_one",
                provenance=("PUBLIC_RELATIONSHIP_CARDINALITY",),
            )
        )
    return MeasureCatalog(
        entities=tuple(entities),
        relationships=tuple(relationships),
        measures=tuple(measures),
    )


SAFE_SQL = (
    "SELECT p.group_id, SUM(c.child_amount) FROM parent p "
    "LEFT JOIN child c ON c.parent_id = p.parent_id GROUP BY p.group_id"
)


class _Connection:
    @contextmanager
    def begin(self) -> Iterator[_Connection]:
        yield self


class _Engine:
    def __init__(self) -> None:
        self.connect_calls = 0

    @contextmanager
    def connect(self) -> Iterator[_Connection]:
        self.connect_calls += 1
        yield _Connection()


class _CostGate:
    def __init__(self, *, too_expensive: bool = False) -> None:
        self.explained: list[str] = []
        self.too_expensive = too_expensive

    def explain(self, _connection: object, sql: str) -> ExplainEstimate:
        self.explained.append(sql)
        return ExplainEstimate(
            total_cost=200_000 if self.too_expensive else 1,
            plan_rows=1,
            top_level_node_type="Aggregate",
        )

    def exceeds(self, estimate: ExplainEstimate, max_rows: int, max_cost: float) -> bool:
        del estimate, max_rows, max_cost
        return self.too_expensive


class _Executor:
    def __init__(self) -> None:
        self.configured = 0
        self.executed: list[str] = []

    def configure_transaction(self, _connection: object) -> None:
        self.configured += 1

    def _execute_on_connection(self, _connection: object, plan: QueryPlan) -> QueryExecution:
        self.executed.append(plan.normalized_sql)
        return QueryExecution(
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            columns=["ok"],
            rows=[{"ok": 1}],
            row_count=1,
            latency_ms=0,
        )


class _AllowPolicy:
    def validate(self, _parsed: object) -> PolicyRejection | None:
        return None

    @staticmethod
    def function_name(function: object) -> str:
        del function
        return "SUM"


class _ParserFailsForNormalized(SQLParser):
    def parse(self, sql: str):  # type: ignore[no-untyped-def]
        if "__grain" in sql:
            raise SQLParseFailure("injected post-normalization parse failure")
        return super().parse(sql)


class _RaisingNormalizer:
    def normalize(self, _sql: str) -> object:
        raise RuntimeError("injected normalizer failure")


def _service(
    *, enabled: bool, catalog: MeasureCatalog | None = None
) -> tuple[SqlSafetyService, _Engine, _CostGate, _Executor]:
    engine = _Engine()
    service = SqlSafetyService(
        create_engine("sqlite://"),
        measure_catalog=catalog if enabled else None,
        grain_normalization_enabled=enabled,
    )
    service.reader_engine = engine  # type: ignore[assignment]
    service.policy = _AllowPolicy()  # type: ignore[assignment]
    service.default_execution_authority = (
        ExecutionAuthority.from_table_names(entity.physical_table for entity in catalog.entities)
        if catalog is not None
        else ExecutionAuthority.from_table_names(("parent", "child"))
    )
    cost = _CostGate()
    executor = _Executor()
    service.cost_gate = cost  # type: ignore[assignment]
    service.executor = executor  # type: ignore[assignment]
    return service, engine, cost, executor


def test_enabled_supported_fanout_reparses_and_explains_only_selected_sql() -> None:
    service, engine, cost, executor = _service(enabled=True, catalog=_catalog())

    planned = service.plan(SqlCandidate(sql=UNSAFE_SQL))

    assert isinstance(planned, QueryPlan)
    assert len(cost.explained) == 1
    assert "c__grain" in cost.explained[0]
    assert UNSAFE_SQL not in cost.explained
    assert engine.connect_calls == 1
    assert executor.configured == 1

    executed = service.execute(planned)
    assert isinstance(executed, QueryExecution)
    assert executor.executed == [planned.normalized_sql]
    assert UNSAFE_SQL not in executor.executed


def test_unsupported_fanout_abstains_and_fails_closed_before_explain() -> None:
    service, engine, cost, executor = _service(enabled=True, catalog=_catalog(second_child=True))
    sql = UNSAFE_SQL.replace(
        "GROUP BY p.group_id",
        "LEFT JOIN child_two c2 ON c2.parent_id = p.parent_id GROUP BY p.group_id",
    )

    result = service.plan(SqlCandidate(sql=sql))

    assert isinstance(result, SqlPlanFailure)
    assert result.status is SqlSafetyStatus.SEMANTIC_REJECTION
    assert result.failure_stage is FailureStage.SEMANTIC_SAFETY_REJECTION
    assert result.semantic_reason == "ABSTAIN_UNSUPPORTED"
    assert cost.explained == []
    assert engine.connect_calls == 0
    assert executor.configured == 0


def test_normalizer_exception_never_falls_back_to_raw_sql() -> None:
    service, engine, cost, executor = _service(enabled=True, catalog=_catalog())
    assert service.grain_coordinator is not None
    service.grain_coordinator = RuntimeGrainSafetyCoordinator(
        _catalog(),
        normalizer=_RaisingNormalizer(),  # type: ignore[arg-type]
    )

    result = service.plan(SqlCandidate(sql=UNSAFE_SQL))

    assert isinstance(result, SqlPlanFailure)
    assert result.semantic_reason == "NORMALIZER_ERROR"
    assert cost.explained == []
    assert engine.connect_calls == 0
    assert executor.executed == []


def test_post_normalization_parse_failure_is_rejected_before_explain() -> None:
    service, engine, cost, executor = _service(enabled=True, catalog=_catalog())
    service.parser = _ParserFailsForNormalized()

    result = service.plan(SqlCandidate(sql=UNSAFE_SQL))

    assert isinstance(result, SqlPlanFailure)
    assert result.semantic_reason == "POST_NORMALIZATION_PARSE_REJECTED"
    assert cost.explained == []
    assert engine.connect_calls == 0
    assert executor.executed == []


def test_post_normalization_policy_rejection_is_rechecked() -> None:
    service, engine, cost, executor = _service(enabled=True, catalog=_catalog())

    class RejectNormalizedPolicy(_AllowPolicy):
        def validate(self, parsed: object) -> PolicyRejection | None:
            if "__grain" in parsed.sql:  # type: ignore[attr-defined]
                return PolicyRejection(
                    code=PolicyCode.FORBIDDEN_TABLE,
                    message="injected policy rejection",
                )
            return None

    service.policy = RejectNormalizedPolicy()  # type: ignore[assignment]
    result = service.plan(SqlCandidate(sql=UNSAFE_SQL))

    assert isinstance(result, SqlPlanFailure)
    assert result.semantic_reason == "POST_NORMALIZATION_POLICY_REJECTED"
    assert result.rejection is not None
    assert cost.explained == []
    assert engine.connect_calls == 0
    assert executor.executed == []


def test_cost_policy_runs_on_normalized_sql_and_can_reject_it() -> None:
    service, engine, cost, executor = _service(enabled=True, catalog=_catalog())
    cost.too_expensive = True

    result = service.plan(SqlCandidate(sql=UNSAFE_SQL))

    assert isinstance(result, SqlPlanFailure)
    assert result.status is SqlSafetyStatus.QUERY_COST_REJECTION
    assert len(cost.explained) == 1
    assert "c__grain" in cost.explained[0]
    assert engine.connect_calls == 1
    assert executor.executed == []


def test_feature_disabled_preserves_legacy_sql_path_and_does_not_require_catalog() -> None:
    service, _engine, cost, _executor = _service(enabled=False)

    result = service.plan(SqlCandidate(sql=SAFE_SQL))

    assert isinstance(result, QueryPlan)
    assert len(cost.explained) == 1
    assert "__grain" not in result.normalized_sql


def test_enabled_runtime_requires_explicit_measure_catalog() -> None:
    with pytest.raises(ValueError, match="MeasureCatalog"):
        SqlSafetyService(
            create_engine("sqlite://"),
            grain_normalization_enabled=True,
        )


def test_coordinator_leaves_safe_sql_byte_identical() -> None:
    coordinator = RuntimeGrainSafetyCoordinator(_catalog())
    decision = coordinator.inspect(SAFE_SQL)

    assert decision.status is GrainRuntimeStatus.UNCHANGED
    assert decision.selected_sql == SAFE_SQL
    assert decision.input_diagnostic.code in {
        GrainDiagnosticCode.PASS,
        GrainDiagnosticCode.NOT_APPLICABLE,
    }


def test_existing_policy_boundary_accepts_read_only_set_operations() -> None:
    parser = SQLParser()
    policy = SQLPolicy(build_default_catalog(Base.metadata))
    parsed = parser.parse("SELECT id FROM products UNION SELECT id FROM products ORDER BY id")

    assert policy.validate(parsed) is None
