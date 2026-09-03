from app.generation.window_ir import WindowQueryIR
from evaluation.run_m212r import _semantic_parts


def test_m212r_ir_match_keeps_physical_output_order() -> None:
    gold = WindowQueryIR.model_validate(
        {
            "source_relation": "orders",
            "pattern": "RANKING",
            "physical_outputs": ["orders.customer_id", "orders.id"],
            "computations": [
                {
                    "pattern": "RANKING",
                    "function": "RANK",
                    "partition_by": ["orders.customer_id"],
                    "order_by": [{"column": "orders.total_amount", "direction": "DESC"}],
                    "alias": "amount_rank",
                }
            ],
        }
    )
    reordered = gold.model_copy(
        update={"physical_outputs": ("orders.id", "orders.customer_id")}
    )

    parts = _semantic_parts(reordered, gold)

    assert parts["physical_outputs"] is False
