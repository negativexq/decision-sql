from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR


def _compiler() -> WindowSqlCompiler:
    return WindowSqlCompiler(build_default_catalog(Base.metadata))


def _ir(
    pattern: str, computation: dict[str, object], outputs: tuple[str, ...] = ("orders.id",)
) -> WindowQueryIR:
    return WindowQueryIR(
        source_relation="orders",
        pattern=pattern,
        physical_outputs=outputs,
        computations=(computation,),
    )


def test_compiler_owns_latest_and_top_n_sql_shape() -> None:
    latest = _ir(
        "LATEST_PER_GROUP",
        {
            "pattern": "LATEST_PER_GROUP",
            "partition_by": ("orders.customer_id",),
            "order_by": ({"column": "orders.ordered_at", "direction": "DESC"},),
            "tie_breakers": ({"column": "orders.id", "direction": "DESC"},),
        },
        ("orders.customer_id", "orders.id"),
    )
    sql = _compiler().compile(latest)
    assert "ROW_NUMBER() OVER" in sql
    assert '"__decision_rank_0" = 1' in sql
    assert sql.startswith('SELECT "customer_id", "id" FROM (SELECT')

    top_n = _ir(
        "TOP_N_PER_GROUP",
        {
            "pattern": "TOP_N_PER_GROUP",
            "partition_by": ("orders.customer_id",),
            "order_by": ({"column": "orders.total_amount", "direction": "DESC"},),
            "n": 3,
            "tie_policy": "INCLUDE_TIES",
        },
    )
    assert '"__decision_rank_0" <= 3' in _compiler().compile(top_n)


def test_compiler_handles_lag_lead_aggregate_ranking_and_share() -> None:
    compiler = _compiler()
    cases = (
        (
            "LAG",
            {
                "pattern": "LAG",
                "target": "orders.total_amount",
                "order_by": ({"column": "orders.id"},),
                "alias": "previous",
            },
            "LAG",
        ),
        (
            "LEAD",
            {
                "pattern": "LEAD",
                "target": "orders.total_amount",
                "order_by": ({"column": "orders.id"},),
                "alias": "next_value",
            },
            "LEAD",
        ),
        (
            "RUNNING_AGGREGATE",
            {
                "pattern": "RUNNING_AGGREGATE",
                "aggregate": "SUM",
                "target": "orders.total_amount",
                "order_by": ({"column": "orders.id"},),
                "alias": "running",
            },
            "ROWS BETWEEN UNBOUNDED PRECEDING",
        ),
        (
            "RANKING",
            {
                "pattern": "RANKING",
                "function": "DENSE_RANK",
                "order_by": ({"column": "orders.total_amount", "direction": "DESC"},),
                "alias": "rank_value",
            },
            "DENSE_RANK",
        ),
        (
            "SHARE_OF_TOTAL",
            {
                "pattern": "SHARE_OF_TOTAL",
                "target": "orders.total_amount",
                "partition_by": ("orders.customer_id",),
                "scale": 100,
                "alias": "share",
            },
            "SUM",
        ),
    )
    for pattern, computation, marker in cases:
        sql = compiler.compile(_ir(pattern, computation))
        assert marker in sql


def test_compiler_is_deterministic_and_does_not_accept_sql_fragments() -> None:
    ir = _ir(
        "MOVING_AGGREGATE",
        {
            "pattern": "MOVING_AGGREGATE",
            "aggregate": "AVG",
            "target": "orders.total_amount",
            "order_by": ({"column": "orders.id"},),
            "frame": {
                "mode": "ROWS",
                "start": {"kind": "N_PRECEDING", "value": 2},
                "end": {"kind": "CURRENT_ROW"},
            },
            "alias": "moving",
        },
    )
    first = _compiler().compile(ir)
    second = _compiler().compile(ir)
    assert first == second
    assert "expression_sql" not in ir.model_dump_json()
