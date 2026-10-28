from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.semantics.grain_runtime import GrainRuntimeStatus, RuntimeGrainSafetyCoordinator
from app.semantics.measures import measure_catalog_from_schema


def _coordinator() -> RuntimeGrainSafetyCoordinator:
    return RuntimeGrainSafetyCoordinator(
        measure_catalog_from_schema(build_default_catalog(Base.metadata))
    )


def test_catalog_keys_and_foreign_keys_become_grain_facts() -> None:
    catalog = measure_catalog_from_schema(build_default_catalog(Base.metadata))

    assert "orders" in {entity.physical_table for entity in catalog.entities}
    measures = {(m.physical_table, m.physical_column_or_path) for m in catalog.measures}
    assert ("orders", "total_amount") in measures
    assert ("orders", "customer_id") not in measures  # foreign keys are not measures
    assert all(r.cardinality == "MANY_TO_ONE" for r in catalog.relationships)


def test_parent_measure_summed_across_child_rows_is_stopped() -> None:
    decision = _coordinator().inspect(
        "SELECT p.category, SUM(o.total_amount) FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id "
        "JOIN products p ON p.id = oi.product_id GROUP BY p.category"
    )
    assert decision.status is not GrainRuntimeStatus.UNCHANGED


def test_aggregates_at_their_own_grain_pass_unchanged() -> None:
    coordinator = _coordinator()
    for sql in (
        "SELECT status, SUM(total_amount) FROM orders GROUP BY status",
        "SELECT r.name, SUM(o.total_amount) FROM orders o "
        "JOIN customers c ON c.id = o.customer_id JOIN regions r ON r.id = c.region_id "
        "GROUP BY r.name",
        "SELECT p.category, SUM(oi.quantity) FROM order_items oi "
        "JOIN products p ON p.id = oi.product_id GROUP BY p.category",
    ):
        assert coordinator.inspect(sql).status is GrainRuntimeStatus.UNCHANGED, sql
