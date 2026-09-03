import pytest

from app.generation.window_dsl import parse_window_dsl


def test_window_dsl_parses_and_preserves_typed_fields() -> None:
    dto = parse_window_dsl(
        """WINDOW
pattern=LAG
source=orders
outputs=orders.id, orders.customer_id
target=orders.total_amount
partition=orders.customer_id
order=orders.ordered_at:ASC
offset=1
alias=previous_amount
"""
    )
    assert dto.pattern.value == "LAG"
    assert dto.physical_outputs == ("orders.id", "orders.customer_id")
    assert dto.order_by[0].column == "orders.ordered_at"


@pytest.mark.parametrize(
    "text",
    (
        "WINDOW\npattern=LAG\nsource=orders\noutputs=orders.id\noutputs=orders.id",
        "WINDOW\npattern=LAG\nsource=orders\noutputs=orders.id\nunknown=value",
        "WINDOW\npattern=LAG\nsource=orders\noutputs=orders.id\noffset=-1",
        "WINDOW\npattern=LAG\nsource=orders\noutputs=orders.id\n"
        "target=orders.total_amount); DROP TABLE orders; --",
    ),
)
def test_window_dsl_rejects_malformed_or_sql_looking_content(text: str) -> None:
    with pytest.raises(ValueError):
        parse_window_dsl(text)


def test_window_dsl_requires_header_and_required_keys() -> None:
    with pytest.raises(ValueError, match="WINDOW"):
        parse_window_dsl("pattern=LAG")
    with pytest.raises(ValueError, match="missing DSL keys"):
        parse_window_dsl("WINDOW\npattern=LAG\nsource=orders")
