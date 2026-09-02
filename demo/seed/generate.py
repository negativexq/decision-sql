from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models import (
    Customer,
    Order,
    OrderItem,
    Payment,
    Product,
    Refund,
    Region,
    SalesRepresentative,
)

SEED_VERSION = "commerce-v1"
REGIONS = ("North", "South", "East", "West")
CATEGORIES = ("Electronics", "Home", "Office", "Outdoor")
STATUSES = ("completed", "completed", "completed", "cancelled", "pending")


def seed_database(engine: Engine) -> dict[str, int]:
    """Replace the demo fixture with deterministic data and return inserted counts."""
    with Session(engine) as session, session.begin():
        models = (Refund, Payment, OrderItem, Order, Customer, Product, SalesRepresentative, Region)
        for model in models:
            session.execute(delete(model))

        regions = [Region(id=index, name=name) for index, name in enumerate(REGIONS, start=1)]
        reps = [
            SalesRepresentative(
                id=index,
                name=f"Representative {index:02d}",
                region_id=((index - 1) % 4) + 1,
            )
            for index in range(1, 9)
        ]
        customers = [
            Customer(
                id=index,
                external_key=f"CUST-{index:04d}",
                name=f"Customer {index:04d}",
                region_id=((index - 1) % 4) + 1,
                sales_rep_id=((index - 1) % 8) + 1,
                created_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index),
            )
            for index in range(1, 41)
        ]
        products = [
            Product(
                id=index,
                sku=f"SKU-{index:04d}",
                name=f"{CATEGORIES[(index - 1) % 4]} Item {index:02d}",
                category=CATEGORIES[(index - 1) % 4],
                unit_price=Decimal(10 + (index * 7) % 190),
            )
            for index in range(1, 25)
        ]
        orders: list[Order] = []
        items: list[OrderItem] = []
        payments: list[Payment] = []
        refunds: list[Refund] = []

        for order_id in range(1, 121):
            customer_id = ((order_id - 1) % 40) + 1
            rep_id = ((customer_id - 1) % 8) + 1
            status = STATUSES[(order_id - 1) % len(STATUSES)]
            ordered_at = datetime(2025, 1, 1, 12, tzinfo=UTC) + timedelta(days=(order_id - 1) % 365)
            first_product = ((order_id - 1) % 24) + 1
            second_product = (order_id % 24) + 1
            first_price = products[first_product - 1].unit_price
            second_price = products[second_product - 1].unit_price
            first_quantity = (order_id % 3) + 1
            second_quantity = ((order_id + 1) % 2) + 1
            gross = first_price * first_quantity + second_price * second_quantity
            discount = (
                gross * Decimal((order_id % 5) * 2) / Decimal(100)
            ).quantize(Decimal("0.01"))
            total = gross - discount
            orders.append(
                Order(
                    id=order_id,
                    order_number=f"ORD-{order_id:06d}",
                    customer_id=customer_id,
                    sales_rep_id=rep_id,
                    ordered_at=ordered_at,
                    status=status,
                    currency="USD",
                    subtotal=gross,
                    discount_amount=discount,
                    total_amount=total,
                )
            )
            items.extend(
                (
                    OrderItem(
                        id=(order_id * 2) - 1,
                        order_id=order_id,
                        product_id=first_product,
                        quantity=first_quantity,
                        unit_price=first_price,
                        discount_amount=(discount / 2).quantize(Decimal("0.01")),
                    ),
                    OrderItem(
                        id=order_id * 2,
                        order_id=order_id,
                        product_id=second_product,
                        quantity=second_quantity,
                        unit_price=second_price,
                        discount_amount=(discount / 2).quantize(Decimal("0.01")),
                    ),
                )
            )
            if status == "completed":
                payments.append(
                    Payment(
                        id=len(payments) + 1,
                        order_id=order_id,
                        paid_at=ordered_at + timedelta(hours=2),
                        amount=total,
                        status="settled",
                    )
                )
            if order_id % 11 == 0:
                refunds.append(
                    Refund(
                        id=len(refunds) + 1,
                        order_id=order_id,
                        refunded_at=ordered_at + timedelta(days=7),
                        amount=(total * Decimal("0.5")).quantize(Decimal("0.01")),
                        reason="Customer return",
                    )
                )

        session.add_all(regions)
        session.flush()
        session.add_all(reps)
        session.flush()
        session.add_all(customers)
        session.add_all(products)
        session.flush()
        session.add_all(orders)
        session.flush()
        session.add_all(items)
        session.add_all(payments)
        session.add_all(refunds)

    return {
        "regions": len(regions),
        "sales_representatives": len(reps),
        "customers": len(customers),
        "products": len(products),
        "orders": len(orders),
        "order_items": len(items),
        "payments": len(payments),
        "refunds": len(refunds),
    }
