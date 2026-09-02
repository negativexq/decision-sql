from sqlalchemy import create_engine, func, select

from app.db.models import (
    Base,
    Customer,
    Order,
    OrderItem,
    Payment,
    Product,
    Refund,
    Region,
    SalesRepresentative,
)
from demo.seed.generate import seed_database


def test_seed_is_deterministic_and_idempotent() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    first_counts = seed_database(engine)
    second_counts = seed_database(engine)

    assert first_counts == second_counts == {
        "regions": 4,
        "sales_representatives": 8,
        "customers": 40,
        "products": 24,
        "orders": 120,
        "order_items": 240,
        "payments": 72,
        "refunds": 10,
    }
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(Order)).scalar_one() == 120
        assert (
            connection.execute(select(Order.order_number).where(Order.id == 1)).scalar_one()
            == "ORD-000001"
        )


def test_all_expected_commerce_tables_are_declared() -> None:
    assert set(Base.metadata.tables) == {
        "regions",
        "sales_representatives",
        "customers",
        "products",
        "orders",
        "order_items",
        "payments",
        "refunds",
    }
    assert {
        Customer.__tablename__,
        Product.__tablename__,
        OrderItem.__tablename__,
    } <= set(Base.metadata.tables)
    assert {
        Payment.__tablename__,
        Refund.__tablename__,
        Region.__tablename__,
        SalesRepresentative.__tablename__,
    } <= set(Base.metadata.tables)
