"""Deterministic authoring source for Decision-SQL Bench v0.1.

The pilot is deliberately generated from this original source.  No external
benchmark records, schemas, or gold queries are imported.
"""

# The source keeps compact declarative tables and query fixtures readable.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from benchmark import BENCHMARK_DIALECT, BENCHMARK_NAME, BENCHMARK_VERSION

ROOT = Path(__file__).resolve().parent
SEED = 3401
GENERATOR_VERSION = "m34-authoring-v1"
SCHEMA_NAMES = {
    "commerce_ops": "m34_commerce_ops",
    "fleet_ops": "m34_fleet_ops",
    "support_ops": "m34_support_ops",
}


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entities(database_id: str, tables: list[tuple[str, str, str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "entity_id": f"entity:{database_id}:{table}",
            "human_name": human,
            "physical_table": table,
            "description": description,
            "primary_semantic_role": role,
        }
        for table, human, description, role in tables
    ]


def _attrs(database_id: str, entries: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    result = []
    for entry in entries:
        if len(entry) == 7 and entry[2] == entry[3]:
            entity, name, path, _duplicate_path, data_type, description, nullable = entry
        else:
            entity, name, path, data_type, description, nullable = entry
        result.append(
            {
                "attribute_id": f"attribute:{database_id}:{entity}:{name}",
                "entity_id": f"entity:{database_id}:{entity}",
                "physical_column_or_path": path,
                "data_type": data_type,
                "semantic_description": description,
                "nullable": nullable,
            }
        )
    return result


def _rel(
    database_id: str,
    relationship_id: str,
    left: str,
    left_attr: str,
    right: str,
    right_attr: str,
    cardinality: str,
    authorized: bool,
    description: str,
) -> dict[str, Any]:
    return {
        "relationship_id": f"relationship:{database_id}:{relationship_id}",
        "left_entity": f"entity:{database_id}:{left}",
        "left_attribute": f"attribute:{database_id}:{left}:{left_attr}",
        "right_entity": f"entity:{database_id}:{right}",
        "right_attribute": f"attribute:{database_id}:{right}:{right_attr}",
        "cardinality": cardinality,
        "direction": "left_to_right",
        "authorized": authorized,
        "description": description,
    }


def authority(database_id: str) -> dict[str, Any]:
    if database_id == "commerce_ops":
        tables = [
            ("customers", "Customers", "A buyer account.", "anchor entity"),
            ("stores", "Stores", "A retail fulfillment location.", "reference entity"),
            ("products", "Products", "A sellable catalog item.", "reference entity"),
            ("orders", "Orders", "A customer purchase order.", "transaction entity"),
            ("order_items", "Order items", "A product line in an order.", "transaction detail"),
            ("shipments", "Shipments", "A fulfillment shipment for an order.", "event entity"),
            ("returns", "Returns", "A returned portion of an order.", "event entity"),
            ("warehouses", "Warehouses", "An inventory storage location.", "reference entity"),
            (
                "inventory_snapshots",
                "Inventory snapshots",
                "A dated inventory observation.",
                "snapshot entity",
            ),
            ("payment_events", "Payment events", "A payment lifecycle event.", "event entity"),
        ]
        attrs = _attrs(
            database_id,
            [
                (
                    "customers",
                    "customer_id",
                    "customer_id",
                    "INTEGER",
                    "Stable customer identifier.",
                    False,
                ),
                ("customers", "customer_name", "customer_name", "TEXT", "Display name.", False),
                ("customers", "region", "region", "TEXT", "Assigned service region.", False),
                (
                    "customers",
                    "signup_date",
                    "signup_date",
                    "DATE",
                    "Date the customer joined.",
                    False,
                ),
                (
                    "customers",
                    "is_active",
                    "is_active",
                    "BOOLEAN",
                    "Whether the account is currently active.",
                    False,
                ),
                (
                    "customers",
                    "legacy_external_code",
                    "legacy_external_code",
                    "TEXT",
                    "Legacy code with no authorized cross-entity relationship.",
                    True,
                ),
                ("orders", "order_id", "order_id", "INTEGER", "Stable order identifier.", False),
                (
                    "orders",
                    "customer_id",
                    "customer_id",
                    "INTEGER",
                    "Authorized customer reference.",
                    False,
                ),
                ("orders", "store_id", "store_id", "INTEGER", "Authorized store reference.", False),
                (
                    "orders",
                    "ordered_at",
                    "ordered_at",
                    "TIMESTAMP",
                    "UTC order creation time.",
                    False,
                ),
                ("orders", "status", "status", "TEXT", "Order lifecycle status.", False),
                (
                    "orders",
                    "discount_pct",
                    "discount_pct",
                    "NUMERIC",
                    "Percentage discount applied to line value.",
                    False,
                ),
                (
                    "orders",
                    "external_customer_code",
                    "external_customer_code",
                    "TEXT",
                    "External code; not an authorized customer join key.",
                    True,
                ),
                ("order_items", "item_id", "item_id", "INTEGER", "Stable line identifier.", False),
                (
                    "order_items",
                    "order_id",
                    "order_id",
                    "INTEGER",
                    "Authorized order reference.",
                    False,
                ),
                (
                    "order_items",
                    "product_id",
                    "product_id",
                    "INTEGER",
                    "Authorized product reference.",
                    False,
                ),
                ("order_items", "quantity", "quantity", "INTEGER", "Units in the line.", False),
                (
                    "order_items",
                    "unit_price",
                    "unit_price",
                    "NUMERIC",
                    "Captured unit price.",
                    False,
                ),
                (
                    "products",
                    "product_id",
                    "product_id",
                    "INTEGER",
                    "Stable product identifier.",
                    False,
                ),
                ("products", "category", "category", "TEXT", "Catalog category.", False),
                (
                    "products",
                    "unit_price",
                    "unit_price",
                    "NUMERIC",
                    "Current catalog unit price.",
                    False,
                ),
                (
                    "shipments",
                    "shipment_id",
                    "shipment_id",
                    "INTEGER",
                    "Stable shipment identifier.",
                    False,
                ),
                (
                    "shipments",
                    "order_id",
                    "order_id",
                    "INTEGER",
                    "Authorized order reference.",
                    False,
                ),
                ("shipments", "shipped_at", "shipped_at", "TIMESTAMP", "UTC shipment time.", True),
                ("shipments", "status", "status", "TEXT", "Shipment lifecycle status.", False),
            ],
        )
        relationships = [
            _rel(
                database_id,
                "order_customer",
                "orders",
                "customer_id",
                "customers",
                "customer_id",
                "many_to_one",
                True,
                "Orders belong to their customer.",
            ),
            _rel(
                database_id,
                "order_store",
                "orders",
                "store_id",
                "stores",
                "store_id",
                "many_to_one",
                True,
                "Orders are fulfilled by a store.",
            ),
            _rel(
                database_id,
                "item_order",
                "order_items",
                "order_id",
                "orders",
                "order_id",
                "many_to_one",
                True,
                "Lines belong to orders.",
            ),
            _rel(
                database_id,
                "item_product",
                "order_items",
                "product_id",
                "products",
                "product_id",
                "many_to_one",
                True,
                "Lines identify products.",
            ),
            _rel(
                database_id,
                "shipment_order",
                "shipments",
                "order_id",
                "orders",
                "order_id",
                "many_to_one",
                True,
                "Shipments belong to orders.",
            ),
            _rel(
                database_id,
                "return_order",
                "returns",
                "order_id",
                "orders",
                "order_id",
                "many_to_one",
                True,
                "Returns belong to orders.",
            ),
            _rel(
                database_id,
                "inventory_product",
                "inventory_snapshots",
                "product_id",
                "products",
                "product_id",
                "many_to_one",
                True,
                "Inventory observes products.",
            ),
            _rel(
                database_id,
                "inventory_warehouse",
                "inventory_snapshots",
                "warehouse_id",
                "warehouses",
                "warehouse_id",
                "many_to_one",
                True,
                "Inventory is observed at warehouses.",
            ),
            _rel(
                database_id,
                "payment_order",
                "payment_events",
                "order_id",
                "orders",
                "order_id",
                "many_to_one",
                True,
                "Payments describe orders.",
            ),
            _rel(
                database_id,
                "tempting_external_code",
                "orders",
                "external_customer_code",
                "customers",
                "legacy_external_code",
                "many_to_one",
                False,
                "Physical values may align, but this legacy linkage is not authorized.",
            ),
        ]
        metrics = [
            {
                "metric_id": "metric:commerce_net_order_value",
                "name": "Net order value",
                "formula": "SUM(order_items.quantity * order_items.unit_price * (1 - orders.discount_pct / 100))",
                "operands": ["quantity", "unit_price", "discount_pct"],
                "grain": "order_item",
                "null_policy": "null operands are excluded by ordinary arithmetic",
                "default_policy": "no default",
                "precision": 2,
                "rounding_stage": "before_ordering_and_display",
                "temporal_basis": "ordered_at",
                "description": "Completed-order captured line value after the order discount.",
            },
        ]
        business = [
            {
                "rule_id": "rule:completed_orders",
                "name": "Completed order",
                "definition": "Only orders.status = 'completed' are completed.",
            },
            {
                "rule_id": "rule:net_value",
                "name": "Net value",
                "definition": "Discount is applied per line before summing; the result is rounded to two decimal places before ordering.",
            },
        ]
        temporal = [
            {
                "temporal_rule_id": "time:commerce_now",
                "clock_mode": "fixed",
                "benchmark_now": "2026-06-30T12:00:00Z",
                "timezone": "UTC",
                "latest_recorded_timestamp": "MAX(ordered_at) only when a case explicitly says latest recorded order",
                "bounds": "date ranges are inclusive lower and exclusive upper unless stated otherwise",
            }
        ]
    elif database_id == "fleet_ops":
        tables = [
            ("vehicles", "Vehicles", "A managed fleet vehicle.", "anchor entity"),
            ("drivers", "Drivers", "A licensed fleet driver.", "reference entity"),
            ("depots", "Depots", "A fleet operating depot.", "reference entity"),
            ("routes", "Routes", "A planned route.", "reference entity"),
            ("trips", "Trips", "A completed or planned vehicle trip.", "transaction entity"),
            (
                "telemetry_events",
                "Telemetry events",
                "A timestamped vehicle observation.",
                "event entity",
            ),
            ("maintenance_events", "Maintenance events", "A maintenance record.", "event entity"),
            ("inspections", "Inspections", "A vehicle inspection.", "event entity"),
            ("fuel_events", "Fuel events", "A fuel purchase record.", "event entity"),
            ("weather_snapshots", "Weather snapshots", "A weather observation.", "snapshot entity"),
        ]
        attrs = _attrs(
            database_id,
            [
                (
                    "vehicles",
                    "vehicle_id",
                    "vehicle_id",
                    "INTEGER",
                    "Stable vehicle identifier.",
                    False,
                ),
                ("vehicles", "vehicle_class", "vehicle_class", "TEXT", "Vehicle class.", False),
                ("vehicles", "depot_id", "depot_id", "INTEGER", "Authorized home depot.", False),
                (
                    "vehicles",
                    "legacy_device_code",
                    "legacy_device_code",
                    "TEXT",
                    "Legacy device code with no authorized telemetry relationship.",
                    True,
                ),
                (
                    "drivers",
                    "driver_id",
                    "driver_id",
                    "INTEGER",
                    "Stable driver identifier.",
                    False,
                ),
                ("drivers", "driver_name", "driver_name", "TEXT", "Driver display name.", False),
                ("depots", "depot_id", "depot_id", "INTEGER", "Stable depot identifier.", False),
                (
                    "depots",
                    "depot_name",
                    "depot_name",
                    "depot_name",
                    "TEXT",
                    "Depot display name.",
                    False,
                ),
                ("routes", "route_id", "route_id", "INTEGER", "Stable route identifier.", False),
                ("routes", "route_name", "route_name", "TEXT", "Route display name.", False),
                ("routes", "route_code", "route_code", "TEXT", "Stable route code.", False),
                ("trips", "trip_id", "trip_id", "INTEGER", "Stable trip identifier.", False),
                (
                    "trips",
                    "vehicle_id",
                    "vehicle_id",
                    "INTEGER",
                    "Authorized vehicle reference.",
                    False,
                ),
                (
                    "trips",
                    "driver_id",
                    "driver_id",
                    "INTEGER",
                    "Authorized driver reference.",
                    False,
                ),
                ("trips", "route_id", "route_id", "INTEGER", "Authorized route reference.", False),
                ("trips", "depot_id", "depot_id", "INTEGER", "Authorized depot reference.", False),
                ("trips", "started_at", "started_at", "TIMESTAMP", "UTC trip start.", False),
                ("trips", "distance_km", "distance_km", "NUMERIC", "Trip distance.", False),
                ("trips", "fuel_liters", "fuel_liters", "NUMERIC", "Fuel consumed.", False),
                (
                    "telemetry_events",
                    "event_id",
                    "event_id",
                    "INTEGER",
                    "Stable telemetry identifier.",
                    False,
                ),
                (
                    "telemetry_events",
                    "vehicle_id",
                    "vehicle_id",
                    "INTEGER",
                    "Authorized vehicle reference.",
                    False,
                ),
                (
                    "telemetry_events",
                    "event_at",
                    "event_at",
                    "TIMESTAMP",
                    "UTC observation time.",
                    False,
                ),
                (
                    "telemetry_events",
                    "payload.engine.temperature_c",
                    "payload #>> '{engine,temperature_c}'",
                    "NUMERIC",
                    "Engine temperature in Celsius from the documented JSON path.",
                    True,
                ),
                (
                    "telemetry_events",
                    "device_code",
                    "device_code",
                    "TEXT",
                    "Device code; not an authorized vehicle join key.",
                    True,
                ),
            ],
        )
        relationships = [
            _rel(
                database_id,
                "vehicle_depot",
                "vehicles",
                "depot_id",
                "depots",
                "depot_id",
                "many_to_one",
                True,
                "Vehicles are assigned to depots.",
            ),
            _rel(
                database_id,
                "trip_vehicle",
                "trips",
                "vehicle_id",
                "vehicles",
                "vehicle_id",
                "many_to_one",
                True,
                "Trips use vehicles.",
            ),
            _rel(
                database_id,
                "trip_driver",
                "trips",
                "driver_id",
                "drivers",
                "driver_id",
                "many_to_one",
                True,
                "Trips are assigned to drivers.",
            ),
            _rel(
                database_id,
                "trip_route",
                "trips",
                "route_id",
                "routes",
                "route_id",
                "many_to_one",
                True,
                "Trips follow routes.",
            ),
            _rel(
                database_id,
                "trip_depot",
                "trips",
                "depot_id",
                "depots",
                "depot_id",
                "many_to_one",
                True,
                "Trips depart from depots.",
            ),
            _rel(
                database_id,
                "telemetry_vehicle",
                "telemetry_events",
                "vehicle_id",
                "vehicles",
                "vehicle_id",
                "many_to_one",
                True,
                "Telemetry belongs to vehicles.",
            ),
            _rel(
                database_id,
                "maintenance_vehicle",
                "maintenance_events",
                "vehicle_id",
                "vehicles",
                "vehicle_id",
                "many_to_one",
                True,
                "Maintenance belongs to vehicles.",
            ),
            _rel(
                database_id,
                "inspection_vehicle",
                "inspections",
                "vehicle_id",
                "vehicles",
                "vehicle_id",
                "many_to_one",
                True,
                "Inspections belong to vehicles.",
            ),
            _rel(
                database_id,
                "fuel_vehicle",
                "fuel_events",
                "vehicle_id",
                "vehicles",
                "vehicle_id",
                "many_to_one",
                True,
                "Fuel events belong to vehicles.",
            ),
            _rel(
                database_id,
                "weather_route",
                "weather_snapshots",
                "route_code",
                "routes",
                "route_name",
                "many_to_one",
                False,
                "Free-text route code resemblance is intentionally not an authorized relationship.",
            ),
            _rel(
                database_id,
                "legacy_device",
                "vehicles",
                "legacy_device_code",
                "telemetry_events",
                "device_code",
                "many_to_one",
                False,
                "Matching legacy/device codes are not authorized.",
            ),
        ]
        metrics = [
            {
                "metric_id": "metric:fleet_route_efficiency",
                "name": "Route efficiency",
                "formula": "AVG(distance_km / NULLIF(fuel_liters, 0))",
                "operands": ["distance_km", "fuel_liters"],
                "grain": "trip",
                "null_policy": "zero fuel is excluded from the ratio",
                "default_policy": "no default",
                "precision": 2,
                "rounding_stage": "display_only",
                "temporal_basis": "started_at",
                "description": "Average kilometres per litre for trips.",
            }
        ]
        business = [
            {
                "rule_id": "rule:fleet_trip",
                "name": "Counted trip",
                "definition": "Every row in trips is a trip; no status filter is implied.",
            },
            {
                "rule_id": "rule:fleet_temperature",
                "name": "Engine temperature",
                "definition": "Use telemetry_events.payload.engine.temperature_c, not a guessed top-level JSON key.",
            },
        ]
        temporal = [
            {
                "temporal_rule_id": "time:fleet_now",
                "clock_mode": "fixed",
                "benchmark_now": "2026-06-30T12:00:00Z",
                "timezone": "UTC",
                "latest_recorded_timestamp": "MAX(event_at) only when a case explicitly says latest recorded observation",
                "bounds": "rolling windows use inclusive lower and exclusive upper bounds",
            }
        ]
    else:
        tables = [
            ("accounts", "Accounts", "A support customer account.", "anchor entity"),
            ("contacts", "Contacts", "A person associated with an account.", "reference entity"),
            ("service_plans", "Service plans", "A support service tier.", "reference entity"),
            ("subscriptions", "Subscriptions", "An account's plan subscription.", "state entity"),
            (
                "support_tickets",
                "Support tickets",
                "A customer support request.",
                "transaction entity",
            ),
            ("ticket_events", "Ticket events", "A timestamped ticket event.", "event entity"),
            ("agents", "Agents", "A support worker.", "reference entity"),
            ("incidents", "Incidents", "A service incident.", "event entity"),
            ("usage_daily", "Daily usage", "An account usage observation.", "snapshot entity"),
            (
                "satisfaction_surveys",
                "Satisfaction surveys",
                "A ticket satisfaction response.",
                "event entity",
            ),
        ]
        attrs = _attrs(
            database_id,
            [
                (
                    "accounts",
                    "account_id",
                    "account_id",
                    "INTEGER",
                    "Stable account identifier.",
                    False,
                ),
                (
                    "accounts",
                    "account_name",
                    "account_name",
                    "TEXT",
                    "Account display name.",
                    False,
                ),
                ("accounts", "region", "region", "TEXT", "Account service region.", False),
                (
                    "contacts",
                    "contact_id",
                    "contact_id",
                    "INTEGER",
                    "Stable contact identifier.",
                    False,
                ),
                (
                    "contacts",
                    "account_id",
                    "account_id",
                    "INTEGER",
                    "Authorized account reference.",
                    False,
                ),
                ("contacts", "email", "email", "TEXT", "Contact email.", False),
                (
                    "service_plans",
                    "plan_id",
                    "plan_id",
                    "INTEGER",
                    "Stable plan identifier.",
                    False,
                ),
                ("service_plans", "plan_name", "plan_name", "TEXT", "Plan display name.", False),
                (
                    "service_plans",
                    "first_response_sla_hours",
                    "first_response_sla_hours",
                    "NUMERIC",
                    "First-response SLA in hours.",
                    False,
                ),
                (
                    "subscriptions",
                    "subscription_id",
                    "subscription_id",
                    "INTEGER",
                    "Stable subscription identifier.",
                    False,
                ),
                (
                    "subscriptions",
                    "account_id",
                    "account_id",
                    "INTEGER",
                    "Authorized account reference.",
                    False,
                ),
                (
                    "subscriptions",
                    "plan_id",
                    "plan_id",
                    "INTEGER",
                    "Authorized plan reference.",
                    False,
                ),
                (
                    "subscriptions",
                    "status",
                    "status",
                    "TEXT",
                    "Subscription lifecycle status.",
                    False,
                ),
                ("subscriptions", "ends_on", "ends_on", "DATE", "Contract end date.", True),
                (
                    "support_tickets",
                    "ticket_id",
                    "ticket_id",
                    "INTEGER",
                    "Stable ticket identifier.",
                    False,
                ),
                (
                    "support_tickets",
                    "account_id",
                    "account_id",
                    "INTEGER",
                    "Authorized account reference.",
                    False,
                ),
                (
                    "support_tickets",
                    "agent_id",
                    "agent_id",
                    "INTEGER",
                    "Authorized agent reference.",
                    True,
                ),
                (
                    "support_tickets",
                    "opened_at",
                    "opened_at",
                    "TIMESTAMP",
                    "UTC ticket opening time.",
                    False,
                ),
                ("support_tickets", "priority", "priority", "TEXT", "Ticket priority.", False),
                ("support_tickets", "status", "status", "TEXT", "Ticket status.", False),
                (
                    "support_tickets",
                    "requester_email",
                    "requester_email",
                    "TEXT",
                    "Requester email; not an authorized contact join key.",
                    True,
                ),
                (
                    "support_tickets",
                    "payload.channel",
                    "payload ->> 'channel'",
                    "TEXT",
                    "Channel from the documented JSON payload.",
                    True,
                ),
                (
                    "ticket_events",
                    "event_id",
                    "event_id",
                    "INTEGER",
                    "Stable event identifier.",
                    False,
                ),
                (
                    "ticket_events",
                    "ticket_id",
                    "ticket_id",
                    "INTEGER",
                    "Authorized ticket reference.",
                    False,
                ),
                (
                    "ticket_events",
                    "event_at",
                    "event_at",
                    "TIMESTAMP",
                    "UTC ticket event time.",
                    False,
                ),
                ("ticket_events", "event_type", "event_type", "TEXT", "Ticket event type.", False),
                (
                    "incidents",
                    "incident_id",
                    "incident_id",
                    "INTEGER",
                    "Stable incident identifier.",
                    False,
                ),
                (
                    "incidents",
                    "incident_code",
                    "incident_code",
                    "TEXT",
                    "Incident code; not an authorized ticket join key.",
                    False,
                ),
            ],
        )
        relationships = [
            _rel(
                database_id,
                "contact_account",
                "contacts",
                "account_id",
                "accounts",
                "account_id",
                "many_to_one",
                True,
                "Contacts belong to accounts.",
            ),
            _rel(
                database_id,
                "subscription_account",
                "subscriptions",
                "account_id",
                "accounts",
                "account_id",
                "many_to_one",
                True,
                "Subscriptions belong to accounts.",
            ),
            _rel(
                database_id,
                "subscription_plan",
                "subscriptions",
                "plan_id",
                "service_plans",
                "plan_id",
                "many_to_one",
                True,
                "Subscriptions identify plans.",
            ),
            _rel(
                database_id,
                "ticket_account",
                "support_tickets",
                "account_id",
                "accounts",
                "account_id",
                "many_to_one",
                True,
                "Tickets belong to accounts.",
            ),
            _rel(
                database_id,
                "ticket_agent",
                "support_tickets",
                "agent_id",
                "agents",
                "agent_id",
                "many_to_one",
                True,
                "Tickets may be assigned to agents.",
            ),
            _rel(
                database_id,
                "event_ticket",
                "ticket_events",
                "ticket_id",
                "support_tickets",
                "ticket_id",
                "many_to_one",
                True,
                "Events belong to tickets.",
            ),
            _rel(
                database_id,
                "usage_account",
                "usage_daily",
                "account_id",
                "accounts",
                "account_id",
                "many_to_one",
                True,
                "Usage belongs to accounts.",
            ),
            _rel(
                database_id,
                "survey_ticket",
                "satisfaction_surveys",
                "ticket_id",
                "support_tickets",
                "ticket_id",
                "many_to_one",
                True,
                "Surveys describe tickets.",
            ),
            _rel(
                database_id,
                "tempting_requester_email",
                "support_tickets",
                "requester_email",
                "contacts",
                "email",
                "many_to_one",
                False,
                "Email resemblance is not an authorized relationship.",
            ),
            _rel(
                database_id,
                "tempting_incident_code",
                "support_tickets",
                "requester_email",
                "incidents",
                "incident_code",
                "many_to_one",
                False,
                "Free-text code resemblance is not an authorized relationship.",
            ),
        ]
        metrics = [
            {
                "metric_id": "metric:support_escalation_rate",
                "name": "Escalation rate",
                "formula": "COUNT(*) FILTER (WHERE priority = 'urgent') / NULLIF(COUNT(*), 0)",
                "operands": ["priority", "ticket_id"],
                "grain": "support_ticket",
                "null_policy": "zero-ticket groups have NULL rate",
                "default_policy": "no default",
                "precision": 4,
                "rounding_stage": "display_only",
                "temporal_basis": "opened_at",
                "description": "Urgent-ticket share of an account's tickets.",
            }
        ]
        business = [
            {
                "rule_id": "rule:support_open",
                "name": "Open ticket",
                "definition": "support_tickets.status IN ('open', 'pending').",
            },
            {
                "rule_id": "rule:support_sla",
                "name": "SLA breach",
                "definition": "The first agent_response event later than opened_at plus the plan SLA is a breach; tickets without a response are not counted in the pilot metric.",
            },
        ]
        temporal = [
            {
                "temporal_rule_id": "time:support_now",
                "clock_mode": "fixed",
                "benchmark_now": "2026-06-30T12:00:00Z",
                "timezone": "UTC",
                "latest_recorded_timestamp": "MAX(event_at) only when a case explicitly says latest recorded event",
                "bounds": "rolling windows use inclusive lower and exclusive upper bounds",
            }
        ]
    if database_id == "support_ops":
        attrs.append(
            {
                "attribute_id": f"attribute:{database_id}:incidents:account_id",
                "entity_id": f"entity:{database_id}:incidents",
                "physical_column_or_path": "account_id",
                "data_type": "INTEGER",
                "semantic_description": "Authorized account reference.",
                "nullable": False,
            }
        )
        relationships.append(
            _rel(
                database_id,
                "incident_account",
                "incidents",
                "account_id",
                "accounts",
                "account_id",
                "many_to_one",
                True,
                "Incidents belong to accounts.",
            )
        )
    return {
        "database_id": database_id,
        "schema_name": SCHEMA_NAMES[database_id],
        "entities": _entities(database_id, tables),
        "attributes": attrs,
        "relationships": relationships,
        "metrics": metrics,
        "business_rules": business,
        "temporal_rules": temporal,
        "policy": {
            "policy_id": f"policy:{database_id}:readonly",
            "allowed_statement": "one read-only SELECT statement",
            "forbidden_operations": [
                "INSERT",
                "UPDATE",
                "DELETE",
                "MERGE",
                "DDL",
                "COPY",
                "locking reads",
            ],
            "forbidden_objects": ["pg_authid", "secret_tokens"],
            "blocked_decisions": ["BLOCKED_POLICY"],
        },
    }


SCHEMAS: dict[str, str] = {
    "commerce_ops": """CREATE SCHEMA IF NOT EXISTS m34_commerce_ops;
SET search_path TO m34_commerce_ops;
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, region TEXT NOT NULL, signup_date DATE NOT NULL, is_active BOOLEAN NOT NULL, legacy_external_code TEXT);
CREATE TABLE stores (store_id INTEGER PRIMARY KEY, store_name TEXT NOT NULL, region TEXT NOT NULL, opened_date DATE NOT NULL);
CREATE TABLE products (product_id INTEGER PRIMARY KEY, product_name TEXT NOT NULL, category TEXT NOT NULL, unit_price NUMERIC(10,2) NOT NULL, active BOOLEAN NOT NULL);
CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), store_id INTEGER NOT NULL REFERENCES stores(store_id), ordered_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL, discount_pct NUMERIC(5,2) NOT NULL, external_customer_code TEXT);
CREATE TABLE order_items (item_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id), product_id INTEGER NOT NULL REFERENCES products(product_id), quantity INTEGER NOT NULL, unit_price NUMERIC(10,2) NOT NULL);
CREATE TABLE shipments (shipment_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id), shipped_at TIMESTAMPTZ, status TEXT NOT NULL);
CREATE TABLE returns (return_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id), return_date DATE NOT NULL, amount NUMERIC(10,2) NOT NULL, reason TEXT NOT NULL);
CREATE TABLE warehouses (warehouse_id INTEGER PRIMARY KEY, warehouse_name TEXT NOT NULL, region TEXT NOT NULL, capacity INTEGER NOT NULL);
CREATE TABLE inventory_snapshots (snapshot_id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(product_id), warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id), snapshot_date DATE NOT NULL, on_hand_qty INTEGER NOT NULL);
CREATE TABLE payment_events (payment_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id), event_at TIMESTAMPTZ NOT NULL, event_type TEXT NOT NULL, amount NUMERIC(10,2) NOT NULL, payload JSONB NOT NULL);
""",
    "fleet_ops": """CREATE SCHEMA IF NOT EXISTS m34_fleet_ops;
SET search_path TO m34_fleet_ops;
CREATE TABLE vehicles (vehicle_id INTEGER PRIMARY KEY, vehicle_name TEXT NOT NULL, vehicle_class TEXT NOT NULL, depot_id INTEGER NOT NULL, legacy_device_code TEXT);
CREATE TABLE drivers (driver_id INTEGER PRIMARY KEY, driver_name TEXT NOT NULL, license_level TEXT NOT NULL);
CREATE TABLE depots (depot_id INTEGER PRIMARY KEY, depot_name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE routes (route_id INTEGER PRIMARY KEY, route_name TEXT NOT NULL, route_code TEXT NOT NULL);
CREATE TABLE trips (trip_id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(vehicle_id), driver_id INTEGER NOT NULL REFERENCES drivers(driver_id), route_id INTEGER NOT NULL REFERENCES routes(route_id), depot_id INTEGER NOT NULL REFERENCES depots(depot_id), started_at TIMESTAMPTZ NOT NULL, distance_km NUMERIC(10,2) NOT NULL, fuel_liters NUMERIC(10,2) NOT NULL);
CREATE TABLE telemetry_events (event_id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(vehicle_id), event_at TIMESTAMPTZ NOT NULL, device_code TEXT, payload JSONB NOT NULL);
CREATE TABLE maintenance_events (maintenance_id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(vehicle_id), performed_at TIMESTAMPTZ NOT NULL, maintenance_type TEXT NOT NULL, cost NUMERIC(10,2) NOT NULL);
CREATE TABLE inspections (inspection_id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(vehicle_id), inspected_at TIMESTAMPTZ NOT NULL, passed BOOLEAN NOT NULL, notes TEXT);
CREATE TABLE fuel_events (fuel_event_id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(vehicle_id), depot_id INTEGER NOT NULL REFERENCES depots(depot_id), event_at TIMESTAMPTZ NOT NULL, liters NUMERIC(10,2) NOT NULL, price_per_liter NUMERIC(10,2) NOT NULL);
CREATE TABLE weather_snapshots (weather_id INTEGER PRIMARY KEY, route_code TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, temperature_c NUMERIC(5,2) NOT NULL, conditions TEXT NOT NULL);
""",
    "support_ops": """CREATE SCHEMA IF NOT EXISTS m34_support_ops;
SET search_path TO m34_support_ops;
CREATE TABLE accounts (account_id INTEGER PRIMARY KEY, account_name TEXT NOT NULL, region TEXT NOT NULL, created_on DATE NOT NULL);
CREATE TABLE contacts (contact_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), email TEXT NOT NULL, contact_name TEXT NOT NULL);
CREATE TABLE service_plans (plan_id INTEGER PRIMARY KEY, plan_name TEXT NOT NULL, first_response_sla_hours NUMERIC(5,2) NOT NULL);
CREATE TABLE subscriptions (subscription_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), plan_id INTEGER NOT NULL REFERENCES service_plans(plan_id), status TEXT NOT NULL, starts_on DATE NOT NULL, ends_on DATE);
CREATE TABLE support_tickets (ticket_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), agent_id INTEGER, opened_at TIMESTAMPTZ NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL, requester_email TEXT, payload JSONB NOT NULL);
CREATE TABLE ticket_events (event_id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES support_tickets(ticket_id), event_at TIMESTAMPTZ NOT NULL, event_type TEXT NOT NULL, payload JSONB NOT NULL);
CREATE TABLE agents (agent_id INTEGER PRIMARY KEY, agent_name TEXT NOT NULL, team TEXT NOT NULL);
CREATE TABLE incidents (incident_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), incident_code TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, severity TEXT NOT NULL);
CREATE TABLE usage_daily (usage_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), usage_date DATE NOT NULL, api_calls INTEGER NOT NULL, storage_gb NUMERIC(10,2) NOT NULL);
CREATE TABLE satisfaction_surveys (survey_id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES support_tickets(ticket_id), submitted_at TIMESTAMPTZ NOT NULL, score INTEGER, payload JSONB NOT NULL);
""",
}


def _insert(cursor: Any, table: str, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    from psycopg import sql

    placeholders = sql.SQL(",").join(sql.Placeholder() for _ in columns)
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)), placeholders
    )
    cursor.executemany(query, rows)


def seed_database(database_id: str, connection_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Rebuild one isolated benchmark schema deterministically."""
    import psycopg

    schema = SCHEMA_NAMES[database_id]
    with psycopg.connect(**connection_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cursor.execute(SCHEMAS[database_id])
            if database_id == "commerce_ops":
                regions = ["North", "South", "East", "West", "Central"]
                _insert(
                    cursor,
                    "customers",
                    [
                        "customer_id",
                        "customer_name",
                        "region",
                        "signup_date",
                        "is_active",
                        "legacy_external_code",
                    ],
                    [
                        (
                            i,
                            f"Customer {i}",
                            regions[i % 5],
                            date(2025, 1, 1) + timedelta(days=i % 500),
                            i % 11 != 0,
                            f"LGC-{i:04d}",
                        )
                        for i in range(1, 261)
                    ],
                )
                _insert(
                    cursor,
                    "stores",
                    ["store_id", "store_name", "region", "opened_date"],
                    [
                        (i, f"Store {i}", regions[i % 5], date(2022, 1, 1) + timedelta(days=i * 23))
                        for i in range(1, 9)
                    ],
                )
                cats = ["home", "outdoor", "office", "care"]
                _insert(
                    cursor,
                    "products",
                    ["product_id", "product_name", "category", "unit_price", "active"],
                    [
                        (
                            i,
                            f"Product {i}",
                            cats[i % 4],
                            Decimal(str(8 + (i * 7) % 180)),
                            i % 13 != 0,
                        )
                        for i in range(1, 81)
                    ],
                )
                orders = [
                    (
                        i,
                        (i * 17) % 260 + 1,
                        (i % 8) + 1,
                        datetime(2026, 1, 1) + timedelta(days=i % 181, hours=i % 24),
                        ["completed", "pending", "cancelled", "completed"][i % 4],
                        Decimal(str((i % 5) * 5)),
                        f"LGC-{((i * 17) % 260 + 1):04d}",
                    )
                    for i in range(1, 701)
                ]
                _insert(
                    cursor,
                    "orders",
                    [
                        "order_id",
                        "customer_id",
                        "store_id",
                        "ordered_at",
                        "status",
                        "discount_pct",
                        "external_customer_code",
                    ],
                    orders,
                )
                _insert(
                    cursor,
                    "order_items",
                    ["item_id", "order_id", "product_id", "quantity", "unit_price"],
                    [
                        (
                            i,
                            (i % 700) + 1,
                            (i * 11) % 80 + 1,
                            i % 4 + 1,
                            Decimal(str(8 + ((i * 11) % 80 + 1) * 2)),
                        )
                        for i in range(1, 1401)
                    ],
                )
                _insert(
                    cursor,
                    "shipments",
                    ["shipment_id", "order_id", "shipped_at", "status"],
                    [
                        (
                            i,
                            i,
                            datetime(2026, 1, 3) + timedelta(days=i % 175) if i % 9 else None,
                            "delivered" if i % 3 else "in_transit",
                        )
                        for i in range(1, 601)
                    ],
                )
                _insert(
                    cursor,
                    "returns",
                    ["return_id", "order_id", "return_date", "amount", "reason"],
                    [
                        (
                            i,
                            i * 2,
                            date(2026, 2, 1) + timedelta(days=i % 120),
                            Decimal(str((i % 70) + 10)),
                            "damaged" if i % 2 else "changed_mind",
                        )
                        for i in range(1, 251)
                    ],
                )
                _insert(
                    cursor,
                    "warehouses",
                    ["warehouse_id", "warehouse_name", "region", "capacity"],
                    [(i, f"Warehouse {i}", regions[i % 5], 1000 + i * 100) for i in range(1, 9)],
                )
                _insert(
                    cursor,
                    "inventory_snapshots",
                    ["snapshot_id", "product_id", "warehouse_id", "snapshot_date", "on_hand_qty"],
                    [
                        (
                            i,
                            i % 80 + 1,
                            i % 8 + 1,
                            date(2026, 6, 1) + timedelta(days=i % 30),
                            (i * 13) % 500,
                        )
                        for i in range(1, 1001)
                    ],
                )
                _insert(
                    cursor,
                    "payment_events",
                    ["payment_id", "order_id", "event_at", "event_type", "amount", "payload"],
                    [
                        (
                            i,
                            i % 700 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 180),
                            "captured" if i % 3 else "refunded",
                            Decimal(str(20 + i % 300)),
                            json.dumps(
                                {"method": ["card", "bank", "wallet"][i % 3], "attempt": i % 2 + 1}
                            ),
                        )
                        for i in range(1, 701)
                    ],
                )
            elif database_id == "fleet_ops":
                _insert(
                    cursor,
                    "depots",
                    ["depot_id", "depot_name", "region"],
                    [
                        (i, f"Depot {i}", ["North", "South", "East", "West"][i % 4])
                        for i in range(1, 9)
                    ],
                )
                _insert(
                    cursor,
                    "drivers",
                    ["driver_id", "driver_name", "license_level"],
                    [
                        (i, f"Driver {i}", ["standard", "heavy", "special"][i % 3])
                        for i in range(1, 121)
                    ],
                )
                _insert(
                    cursor,
                    "vehicles",
                    [
                        "vehicle_id",
                        "vehicle_name",
                        "vehicle_class",
                        "depot_id",
                        "legacy_device_code",
                    ],
                    [
                        (
                            i,
                            f"Vehicle {i}",
                            ["cargo", "van", "service"][i % 3],
                            i % 8 + 1,
                            f"DEV-{i:04d}",
                        )
                        for i in range(1, 221)
                    ],
                )
                _insert(
                    cursor,
                    "routes",
                    ["route_id", "route_name", "route_code"],
                    [(i, f"Route {i}", f"R-{i:03d}") for i in range(1, 41)],
                )
                _insert(
                    cursor,
                    "trips",
                    [
                        "trip_id",
                        "vehicle_id",
                        "driver_id",
                        "route_id",
                        "depot_id",
                        "started_at",
                        "distance_km",
                        "fuel_liters",
                    ],
                    [
                        (
                            i,
                            i % 220 + 1,
                            i % 120 + 1,
                            i % 40 + 1,
                            i % 8 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181, hours=i % 24),
                            Decimal(str(20 + i % 500)),
                            Decimal(str(4 + i % 90)),
                        )
                        for i in range(1, 901)
                    ],
                )
                _insert(
                    cursor,
                    "telemetry_events",
                    ["event_id", "vehicle_id", "event_at", "device_code", "payload"],
                    [
                        (
                            i,
                            i % 220 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181, hours=i % 24),
                            f"DEV-{i % 220 + 1:04d}",
                            json.dumps(
                                {
                                    "engine": {"temperature_c": 65 + i % 55},
                                    "battery": {"voltage": 12 + i % 3},
                                }
                            ),
                        )
                        for i in range(1, 2401)
                    ],
                )
                _insert(
                    cursor,
                    "maintenance_events",
                    ["maintenance_id", "vehicle_id", "performed_at", "maintenance_type", "cost"],
                    [
                        (
                            i,
                            i % 220 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181),
                            ["service", "repair", "inspection"][i % 3],
                            Decimal(str(100 + i % 1500)),
                        )
                        for i in range(1, 501)
                    ],
                )
                _insert(
                    cursor,
                    "inspections",
                    ["inspection_id", "vehicle_id", "inspected_at", "passed", "notes"],
                    [
                        (
                            i,
                            i % 220 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181),
                            i % 7 != 0,
                            "ok" if i % 7 else "follow_up",
                        )
                        for i in range(1, 501)
                    ],
                )
                _insert(
                    cursor,
                    "fuel_events",
                    [
                        "fuel_event_id",
                        "vehicle_id",
                        "depot_id",
                        "event_at",
                        "liters",
                        "price_per_liter",
                    ],
                    [
                        (
                            i,
                            i % 220 + 1,
                            i % 8 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181),
                            Decimal(str(20 + i % 90)),
                            Decimal("1.20" if i % 2 else "1.45"),
                        )
                        for i in range(1, 1001)
                    ],
                )
                _insert(
                    cursor,
                    "weather_snapshots",
                    ["weather_id", "route_code", "observed_at", "temperature_c", "conditions"],
                    [
                        (
                            i,
                            f"R-{i % 40 + 1:03d}",
                            datetime(2026, 1, 1) + timedelta(days=i % 181),
                            Decimal(str(5 + i % 35)),
                            ["clear", "rain", "wind"][i % 3],
                        )
                        for i in range(1, 501)
                    ],
                )
            else:
                _insert(
                    cursor,
                    "accounts",
                    ["account_id", "account_name", "region", "created_on"],
                    [
                        (
                            i,
                            f"Account {i}",
                            ["North", "South", "East", "West"][i % 4],
                            date(2024, 1, 1) + timedelta(days=i % 700),
                        )
                        for i in range(1, 261)
                    ],
                )
                _insert(
                    cursor,
                    "contacts",
                    ["contact_id", "account_id", "email", "contact_name"],
                    [
                        (i, i % 260 + 1, f"contact{i}@example.test", f"Contact {i}")
                        for i in range(1, 401)
                    ],
                )
                _insert(
                    cursor,
                    "service_plans",
                    ["plan_id", "plan_name", "first_response_sla_hours"],
                    [
                        (1, "Essential", Decimal("24")),
                        (2, "Priority", Decimal("8")),
                        (3, "Enterprise", Decimal("2")),
                        (4, "Community", Decimal("48")),
                    ],
                )
                _insert(
                    cursor,
                    "subscriptions",
                    ["subscription_id", "account_id", "plan_id", "status", "starts_on", "ends_on"],
                    [
                        (
                            i,
                            i % 260 + 1,
                            i % 4 + 1,
                            "active" if i % 5 else "paused",
                            date(2025, 1, 1) + timedelta(days=i % 300),
                            date(2026, 12, 31) if i % 6 else date(2026, 4, 1),
                        )
                        for i in range(1, 301)
                    ],
                )
                _insert(
                    cursor,
                    "agents",
                    ["agent_id", "agent_name", "team"],
                    [
                        (i, f"Agent {i}", ["frontline", "technical", "billing"][i % 3])
                        for i in range(1, 61)
                    ],
                )
                _insert(
                    cursor,
                    "support_tickets",
                    [
                        "ticket_id",
                        "account_id",
                        "agent_id",
                        "opened_at",
                        "priority",
                        "status",
                        "requester_email",
                        "payload",
                    ],
                    [
                        (
                            i,
                            i % 260 + 1,
                            i % 60 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181, hours=i % 24),
                            ["low", "normal", "high", "urgent"][i % 4],
                            ["open", "pending", "closed"][i % 3],
                            f"contact{i % 400 + 1}@example.test",
                            json.dumps(
                                {
                                    "channel": ["email", "chat", "phone"][i % 3],
                                    "product_area": ["api", "billing", "auth"][i % 3],
                                }
                            ),
                        )
                        for i in range(1, 801)
                    ],
                )
                _insert(
                    cursor,
                    "ticket_events",
                    ["event_id", "ticket_id", "event_at", "event_type", "payload"],
                    [
                        (
                            i,
                            i % 800 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181, hours=i % 24),
                            ["created", "agent_response", "status_change", "note"][i % 4],
                            json.dumps({"source": "system"}),
                        )
                        for i in range(1, 2001)
                    ],
                )
                _insert(
                    cursor,
                    "incidents",
                    ["incident_id", "account_id", "incident_code", "started_at", "severity"],
                    [
                        (
                            i,
                            i % 260 + 1,
                            f"INC-{i:04d}",
                            datetime(2026, 1, 1) + timedelta(days=i % 181),
                            ["low", "medium", "high"][i % 3],
                        )
                        for i in range(1, 121)
                    ],
                )
                _insert(
                    cursor,
                    "usage_daily",
                    ["usage_id", "account_id", "usage_date", "api_calls", "storage_gb"],
                    [
                        (
                            i,
                            i % 260 + 1,
                            date(2026, 1, 1) + timedelta(days=i % 181),
                            i % 5000,
                            Decimal(str(1 + i % 800 / 10)),
                        )
                        for i in range(1, 2001)
                    ],
                )
                _insert(
                    cursor,
                    "satisfaction_surveys",
                    ["survey_id", "ticket_id", "submitted_at", "score", "payload"],
                    [
                        (
                            i,
                            i % 800 + 1,
                            datetime(2026, 1, 1) + timedelta(days=i % 181),
                            None if i % 11 == 0 else i % 5 + 1,
                            json.dumps({"commented": i % 2 == 0}),
                        )
                        for i in range(1, 601)
                    ],
                )
        connection.commit()
    counts = {"seed": SEED, "generator_version": GENERATOR_VERSION}
    return counts


def connection_kwargs_from_env() -> dict[str, Any]:
    import os
    from urllib.parse import urlparse

    url = os.getenv(
        "M34_DATABASE_URL",
        "postgresql://decision_admin:decision_admin_password@localhost:5432/decision_sql",
    )
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "dbname": (parsed.path or "/decision_sql").lstrip("/"),
        "user": parsed.username or "decision_admin",
        "password": parsed.password or "decision_admin_password",
    }


def build_synthetic_files() -> None:
    for database_id in SCHEMA_NAMES:
        root = ROOT / "databases" / database_id
        (root / "schema.sql").write_text(SCHEMAS[database_id], encoding="utf-8")
        seed_wrapper = f'''from benchmark.authoring import connection_kwargs_from_env, seed_database\n\nif __name__ == "__main__":\n    print(seed_database("{database_id}", connection_kwargs_from_env()))\n'''
        (root / "seed.py").write_text(seed_wrapper, encoding="utf-8")
        auth = authority(database_id)
        for key in (
            "entities",
            "attributes",
            "relationships",
            "metrics",
            "business_rules",
            "temporal_rules",
            "policy",
        ):
            _dump(root / "authority" / f"{key}.json", auth[key])
        _dump(
            root / "authority" / "database.json",
            {
                "database_id": database_id,
                "schema_name": SCHEMA_NAMES[database_id],
                "benchmark_now": "2026-06-30T12:00:00Z",
                "seed": SEED,
                "generator_version": GENERATOR_VERSION,
            },
        )


def _case(
    case_id: str,
    database_id: str,
    question: str,
    task_type: str,
    tags: list[str],
    difficulty: str,
    ref_a: str | None,
    ref_b: str | None,
    fixtures: list[dict[str, Any]],
    mutants: list[dict[str, Any]],
    required_facts: list[str],
    **extra: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = {
        "case_id": case_id,
        "database_id": database_id,
        "question": question,
        "task_type": task_type,
        "context_profile": "GOVERNED_CONTEXT_V1",
        "provenance": {
            "authoring_source": "original",
            "external_benchmark_derived": False,
            "machine_authored": True,
            "human_reviewed": False,
        },
    }
    truth = {
        "case_id": case_id,
        "database_id": database_id,
        "status": "DRAFT",
        "difficulty": difficulty,
        "mechanism_tags": tags,
        "semantic_target": {
            "behavior": task_type,
            "population": extra.get("population", "matching-only"),
            "outputs": extra.get("outputs", []),
            "relationships": extra.get("relationships", []),
            "filters": extra.get("filters", []),
            "aggregations": extra.get("aggregations", []),
            "grouping": extra.get("grouping", []),
            "calculations": extra.get("calculations", []),
            "temporal_semantics": extra.get("temporal_semantics", {}),
            "ordering": extra.get("ordering", {}),
            "limit": extra.get("limit"),
            "query_shape_tags": tags,
            "null_default_semantics": extra.get("null_default_semantics", "NULL remains NULL"),
            "result_comparison_contract": {
                "column_count": extra.get("column_count", 1),
                "row_order": extra.get("row_order", False),
                "aliases_significant": False,
                "duplicates_significant": True,
                "numeric_tolerance": None,
                "timestamp_timezone": "UTC",
            },
        },
        "required_context_facts": required_facts,
        "reference_implementation_a": {"kind": "REFERENCE_IMPLEMENTATION_A", "sql": ref_a},
        "reference_implementation_b": {"kind": "REFERENCE_IMPLEMENTATION_B", "sql": ref_b},
        "counterfactual_fixtures": fixtures,
        "semantic_mutants": mutants,
        "authoring_integrity": {"human_independent_review": False, "human_reviewed": False},
        "required_authority": extra.get("required_authority", []),
        "evidence": extra.get("evidence", {}),
    }
    return case, truth


def cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    # Commerce: seven answerable, one authority-blocked, one ambiguous, one policy-blocked.
    out.append(
        _case(
            "commerce_01",
            "commerce_ops",
            "List active customers in the North region with their customer identifiers and names.",
            "ANSWERABLE",
            ["schema_linking", "simple_projection", "filter"],
            "EASY",
            "SELECT customer_id, customer_name FROM customers WHERE region = 'North' AND is_active = TRUE ORDER BY customer_id",
            "SELECT c.customer_id, c.customer_name FROM (SELECT * FROM customers WHERE region = 'North') AS c WHERE c.is_active ORDER BY c.customer_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds an inactive North customer and an active non-North customer.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900001, 'Fixture Inactive North', 'North', DATE '2026-06-20', FALSE, 'LGC-900001')",
                        "INSERT INTO customers VALUES (900002, 'Fixture Active South', 'South', DATE '2026-06-20', TRUE, 'LGC-900002')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a low identifier active North customer to make ordering observable.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900003, 'Fixture Active North', 'North', DATE '2026-06-21', TRUE, 'LGC-900003')"
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m01_wrong_region",
                    "failure_category": "wrong_literal",
                    "description": "Uses South instead of North.",
                    "sql": "SELECT customer_id, customer_name FROM customers WHERE region = 'South' AND is_active = TRUE ORDER BY customer_id",
                },
                {
                    "mutant_id": "m01_remove_active",
                    "failure_category": "remove_predicate",
                    "description": "Drops the active-account predicate.",
                    "sql": "SELECT customer_id, customer_name FROM customers WHERE region = 'North' ORDER BY customer_id",
                },
                {
                    "mutant_id": "m01_wrong_boolean",
                    "failure_category": "wrong_comparison_operator",
                    "description": "Selects inactive accounts.",
                    "sql": "SELECT customer_id, customer_name FROM customers WHERE region = 'North' AND is_active = FALSE ORDER BY customer_id",
                },
            ],
            ["entities.customers", "attributes.customers.region", "attributes.customers.is_active"],
            outputs=["customer_id", "customer_name"],
            filters=[
                {"field": "customers.region", "operator": "=", "value": "North", "scope": "row"},
                {"field": "customers.is_active", "operator": "=", "value": True, "scope": "row"},
            ],
            relationships=[],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_02",
            "commerce_ops",
            "Return order IDs for completed orders created during June 2026, including June 1 and excluding July 1.",
            "ANSWERABLE",
            ["schema_linking", "multi_filter", "temporal", "filter"],
            "EASY",
            "SELECT order_id FROM orders WHERE status = 'completed' AND ordered_at >= TIMESTAMPTZ '2026-06-01 00:00:00+00' AND ordered_at < TIMESTAMPTZ '2026-07-01 00:00:00+00' ORDER BY order_id",
            "WITH june AS (SELECT * FROM orders WHERE ordered_at >= TIMESTAMPTZ '2026-06-01 00:00:00+00' AND ordered_at < TIMESTAMPTZ '2026-07-01 00:00:00+00') SELECT order_id FROM june WHERE status = 'completed' ORDER BY order_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds completed orders at both temporal boundaries and one pending order.",
                    "patch_sql": [
                        "INSERT INTO orders VALUES (900010, 1, 1, TIMESTAMPTZ '2026-06-01 00:00:00+00', 'completed', 0, 'FIX-900010')",
                        "INSERT INTO orders VALUES (900011, 1, 1, TIMESTAMPTZ '2026-07-01 00:00:00+00', 'completed', 0, 'FIX-900011')",
                        "INSERT INTO orders VALUES (900012, 1, 1, TIMESTAMPTZ '2026-06-15 00:00:00+00', 'pending', 0, 'FIX-900012')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a completed order just before June and a June order.",
                    "patch_sql": [
                        "INSERT INTO orders VALUES (900013, 1, 1, TIMESTAMPTZ '2026-05-31 23:59:59+00', 'completed', 0, 'FIX-900013')",
                        "INSERT INTO orders VALUES (900014, 1, 1, TIMESTAMPTZ '2026-06-30 23:59:59+00', 'completed', 0, 'FIX-900014')",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m02_status",
                    "failure_category": "wrong_literal",
                    "description": "Uses pending orders.",
                    "sql": "SELECT order_id FROM orders WHERE status = 'pending' AND ordered_at >= TIMESTAMPTZ '2026-06-01 00:00:00+00' AND ordered_at < TIMESTAMPTZ '2026-07-01 00:00:00+00' ORDER BY order_id",
                },
                {
                    "mutant_id": "m02_upper_bound",
                    "failure_category": "wrong_temporal_anchor",
                    "description": "Makes July 1 inclusive.",
                    "sql": "SELECT order_id FROM orders WHERE status = 'completed' AND ordered_at >= TIMESTAMPTZ '2026-06-01 00:00:00+00' AND ordered_at <= TIMESTAMPTZ '2026-07-01 00:00:00+00' ORDER BY order_id",
                },
                {
                    "mutant_id": "m02_remove_date",
                    "failure_category": "remove_predicate",
                    "description": "Removes the June window.",
                    "sql": "SELECT order_id FROM orders WHERE status = 'completed' ORDER BY order_id",
                },
            ],
            [
                "temporal_rules.time:commerce_now",
                "attributes.orders.ordered_at",
                "business_rules.rule:completed_orders",
            ],
            outputs=["order_id"],
            filters=[
                {"field": "orders.status", "operator": "=", "value": "completed", "scope": "row"},
                {
                    "field": "orders.ordered_at",
                    "operator": ">=/<",
                    "value": "2026-06-01 to 2026-07-01",
                    "scope": "row",
                },
            ],
            temporal_semantics={
                "basis": "wall_clock_benchmark_time",
                "lower_inclusive": True,
                "upper_exclusive": True,
            },
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_03",
            "commerce_ops",
            "For every customer region, count completed orders and keep regions with no completed orders.",
            "ANSWERABLE",
            ["relationship", "population", "grouping", "aggregation", "null_semantics"],
            "MEDIUM",
            "SELECT c.region, COUNT(o.order_id) AS completed_orders FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id AND o.status = 'completed' GROUP BY c.region ORDER BY c.region",
            "WITH completed AS (SELECT customer_id, COUNT(*) AS n FROM orders WHERE status = 'completed' GROUP BY customer_id) SELECT c.region, COALESCE(SUM(completed.n), 0)::BIGINT AS completed_orders FROM customers c LEFT JOIN completed ON completed.customer_id = c.customer_id GROUP BY c.region ORDER BY c.region",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a new region with no orders, distinguishing preserve-anchor population from inner join.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900020, 'Fixture Empty Region', 'Remote', DATE '2026-06-20', TRUE, 'LGC-900020')"
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a pending-only customer and a completed order for the new region.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900021, 'Fixture Pending Only', 'Island', DATE '2026-06-20', TRUE, 'LGC-900021')",
                        "INSERT INTO orders VALUES (900021, 900021, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'pending', 0, 'FIX-900021')",
                        "INSERT INTO customers VALUES (900022, 'Fixture Completed', 'Island', DATE '2026-06-20', TRUE, 'LGC-900022')",
                        "INSERT INTO orders VALUES (900022, 900022, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'completed', 0, 'FIX-900022')",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m03_inner",
                    "failure_category": "inner_left_join",
                    "description": "Drops regions without matched orders.",
                    "sql": "SELECT c.region, COUNT(o.order_id) AS completed_orders FROM customers c JOIN orders o ON o.customer_id = c.customer_id AND o.status = 'completed' GROUP BY c.region ORDER BY c.region",
                },
                {
                    "mutant_id": "m03_where_scope",
                    "failure_category": "where_filter_scope",
                    "description": "Moves completed predicate to WHERE after the left join.",
                    "sql": "SELECT c.region, COUNT(o.order_id) AS completed_orders FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id WHERE o.status = 'completed' GROUP BY c.region ORDER BY c.region",
                },
                {
                    "mutant_id": "m03_all_orders",
                    "failure_category": "remove_predicate",
                    "description": "Counts all order statuses.",
                    "sql": "SELECT c.region, COUNT(o.order_id) AS completed_orders FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id GROUP BY c.region ORDER BY c.region",
                },
            ],
            [
                "relationships.relationship:commerce_ops:order_customer",
                "business_rules.rule:completed_orders",
            ],
            outputs=["region", "completed_orders"],
            relationships=["relationship:commerce_ops:order_customer"],
            aggregations=["COUNT(completed orders)"],
            grouping=["customers.region"],
            population="preserve-anchor",
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_04",
            "commerce_ops",
            "For each product category, calculate the rounded net value of completed order lines after the order discount.",
            "ANSWERABLE",
            ["relationship", "multi_hop", "aggregation", "calculation", "grain", "precision"],
            "HARD",
            "SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price * (1 - o.discount_pct / 100.0))::numeric, 2) AS net_value FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed' GROUP BY p.category ORDER BY p.category",
            "WITH lines AS (SELECT p.category, oi.quantity * oi.unit_price * (1 - o.discount_pct / 100.0) AS line_value FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed') SELECT category, ROUND(SUM(line_value)::numeric, 2) AS net_value FROM lines GROUP BY category ORDER BY category",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a discounted completed line so SUM(raw line value) and undiscounted value diverge.",
                    "patch_sql": [
                        "INSERT INTO orders VALUES (900030, 1, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'completed', 50, 'FIX-900030')",
                        "INSERT INTO order_items VALUES (900030, 900030, 1, 3, 100.00)",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a pending high-value line so status scope is observable.",
                    "patch_sql": [
                        "INSERT INTO orders VALUES (900031, 1, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'pending', 0, 'FIX-900031')",
                        "INSERT INTO order_items VALUES (900031, 900031, 1, 100, 100.00)",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m04_no_discount",
                    "failure_category": "wrong_metric_operand",
                    "description": "Omits the order discount.",
                    "sql": "SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2) AS net_value FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed' GROUP BY p.category ORDER BY p.category",
                },
                {
                    "mutant_id": "m04_all_status",
                    "failure_category": "remove_predicate",
                    "description": "Includes non-completed orders.",
                    "sql": "SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price * (1 - o.discount_pct / 100.0))::numeric, 2) AS net_value FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id GROUP BY p.category ORDER BY p.category",
                },
                {
                    "mutant_id": "m04_wrong_grain",
                    "failure_category": "wrong_aggregation_grain",
                    "description": "Uses product current price instead of captured line price.",
                    "sql": "SELECT p.category, ROUND(SUM(oi.quantity * p.unit_price * (1 - o.discount_pct / 100.0))::numeric, 2) AS net_value FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed' GROUP BY p.category ORDER BY p.category",
                },
            ],
            [
                "relationships.relationship:commerce_ops:item_order",
                "relationships.relationship:commerce_ops:item_product",
                "metrics.metric:commerce_net_order_value",
                "business_rules.rule:net_value",
            ],
            outputs=["category", "net_value"],
            relationships=[
                "relationship:commerce_ops:item_order",
                "relationship:commerce_ops:item_product",
            ],
            aggregations=["SUM(line_value)"],
            grouping=["products.category"],
            calculations=[
                {
                    "calculation_id": "metric:commerce_net_order_value",
                    "operands": ["quantity", "unit_price", "discount_pct"],
                    "grain": "order_item",
                    "aggregation_stage": "post_discount_line",
                    "rounding_stage": "before_ordering_and_display",
                }
            ],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_05",
            "commerce_ops",
            "For each customer who bought a home product in a completed order, count distinct completed orders containing a home product.",
            "ANSWERABLE",
            ["relationship", "multi_hop", "aggregation", "population", "set_operation"],
            "HARD",
            "SELECT c.customer_id, COUNT(DISTINCT o.order_id) AS home_orders FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed' AND p.category = 'home' GROUP BY c.customer_id ORDER BY c.customer_id",
            "WITH home_orders AS (SELECT DISTINCT o.customer_id, o.order_id FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed' AND p.category = 'home') SELECT customer_id, COUNT(*) AS home_orders FROM home_orders GROUP BY customer_id ORDER BY customer_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds two home lines to one order, distinguishing distinct orders from line count.",
                    "patch_sql": [
                        "INSERT INTO orders VALUES (900040, 2, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'completed', 0, 'FIX-900040')",
                        "INSERT INTO order_items VALUES (900040, 900040, 1, 1, 10.00)",
                        "INSERT INTO order_items VALUES (900041, 900040, 5, 1, 20.00)",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a pending home order that must not enter the population.",
                    "patch_sql": [
                        "INSERT INTO orders VALUES (900042, 2, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'pending', 0, 'FIX-900042')",
                        "INSERT INTO order_items VALUES (900042, 900042, 1, 1, 10.00)",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m05_count_lines",
                    "failure_category": "count_vs_count_distinct",
                    "description": "Counts qualifying lines rather than distinct orders.",
                    "sql": "SELECT c.customer_id, COUNT(o.order_id) AS home_orders FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed' AND p.category = 'home' GROUP BY c.customer_id ORDER BY c.customer_id",
                },
                {
                    "mutant_id": "m05_all_status",
                    "failure_category": "remove_predicate",
                    "description": "Includes pending orders.",
                    "sql": "SELECT c.customer_id, COUNT(DISTINCT o.order_id) AS home_orders FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE p.category = 'home' GROUP BY c.customer_id ORDER BY c.customer_id",
                },
                {
                    "mutant_id": "m05_no_category",
                    "failure_category": "remove_predicate",
                    "description": "Counts completed orders containing any category.",
                    "sql": "SELECT c.customer_id, COUNT(DISTINCT o.order_id) AS home_orders FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = 'completed' GROUP BY c.customer_id ORDER BY c.customer_id",
                },
            ],
            [
                "relationships.relationship:commerce_ops:order_customer",
                "relationships.relationship:commerce_ops:item_order",
                "relationships.relationship:commerce_ops:item_product",
            ],
            outputs=["customer_id", "home_orders"],
            relationships=[
                "relationship:commerce_ops:order_customer",
                "relationship:commerce_ops:item_order",
                "relationship:commerce_ops:item_product",
            ],
            aggregations=["COUNT(DISTINCT order_id)"],
            grouping=["customers.customer_id"],
            filters=[
                {"field": "products.category", "operator": "=", "value": "home", "scope": "row"}
            ],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_06",
            "commerce_ops",
            "For every region, report the average number of completed orders per customer, including customers and regions with zero completed orders.",
            "ANSWERABLE",
            ["relationship", "population", "aggregation", "grouping", "null_semantics", "nested"],
            "HARD",
            "WITH per_customer AS (SELECT c.region, c.customer_id, COUNT(o.order_id) AS completed_orders FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id AND o.status = 'completed' GROUP BY c.region, c.customer_id) SELECT region, ROUND(AVG(completed_orders)::numeric, 2) AS avg_completed_orders FROM per_customer GROUP BY region ORDER BY region",
            "SELECT c.region, ROUND(AVG((SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.customer_id AND o.status = 'completed'))::numeric, 2) AS avg_completed_orders FROM customers c GROUP BY c.region ORDER BY c.region",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a Remote customer with no orders, distinguishing all-anchor population from matching-only.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900050, 'Fixture Remote', 'Remote', DATE '2026-06-20', TRUE, 'LGC-900050')"
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a pending-only customer in a new region; pending rows must not count.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900051, 'Fixture Pending', 'Newland', DATE '2026-06-20', TRUE, 'LGC-900051')",
                        "INSERT INTO orders VALUES (900051, 900051, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'pending', 0, 'FIX-900051')",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m06_inner",
                    "failure_category": "inner_left_join",
                    "description": "Drops zero-order customers.",
                    "sql": "WITH per_customer AS (SELECT c.region, c.customer_id, COUNT(o.order_id) AS completed_orders FROM customers c JOIN orders o ON o.customer_id = c.customer_id AND o.status = 'completed' GROUP BY c.region, c.customer_id) SELECT region, ROUND(AVG(completed_orders)::numeric, 2) AS avg_completed_orders FROM per_customer GROUP BY region ORDER BY region",
                },
                {
                    "mutant_id": "m06_where_scope",
                    "failure_category": "where_filter_scope",
                    "description": "Moves status filtering to WHERE.",
                    "sql": "WITH per_customer AS (SELECT c.region, c.customer_id, COUNT(o.order_id) AS completed_orders FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id WHERE o.status = 'completed' GROUP BY c.region, c.customer_id) SELECT region, ROUND(AVG(completed_orders)::numeric, 2) AS avg_completed_orders FROM per_customer GROUP BY region ORDER BY region",
                },
                {
                    "mutant_id": "m06_all_orders",
                    "failure_category": "remove_predicate",
                    "description": "Counts every order status.",
                    "sql": "WITH per_customer AS (SELECT c.region, c.customer_id, COUNT(o.order_id) AS completed_orders FROM customers c LEFT JOIN orders o ON o.customer_id = c.customer_id GROUP BY c.region, c.customer_id) SELECT region, ROUND(AVG(completed_orders)::numeric, 2) AS avg_completed_orders FROM per_customer GROUP BY region ORDER BY region",
                },
            ],
            [
                "relationships.relationship:commerce_ops:order_customer",
                "business_rules.rule:completed_orders",
                "metrics.metric:commerce_net_order_value",
            ],
            outputs=["region", "avg_completed_orders"],
            relationships=["relationship:commerce_ops:order_customer"],
            aggregations=["AVG(completed order count)"],
            grouping=["customers.region"],
            population="preserve-anchor",
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_07",
            "commerce_ops",
            "Show the three customers with the highest rounded completed-order net value, returning customer ID and net value.",
            "ANSWERABLE",
            [
                "relationship",
                "multi_hop",
                "aggregation",
                "calculation",
                "ordering",
                "limit",
                "precision",
            ],
            "HARD",
            "WITH customer_value AS (SELECT c.customer_id, ROUND(SUM(oi.quantity * oi.unit_price * (1 - o.discount_pct / 100.0))::numeric, 2) AS net_value FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status = 'completed' GROUP BY c.customer_id) SELECT customer_id, net_value FROM customer_value ORDER BY net_value DESC, customer_id ASC LIMIT 3",
            "SELECT customer_id, net_value FROM (SELECT c.customer_id, ROUND(SUM(oi.quantity * oi.unit_price * (1 - o.discount_pct / 100.0))::numeric, 2) AS net_value FROM orders o JOIN customers c ON c.customer_id = o.customer_id JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status = 'completed' GROUP BY c.customer_id) AS ranked ORDER BY net_value DESC, customer_id ASC FETCH FIRST 3 ROWS ONLY",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a high-value completed customer so top-N is observable.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900060, 'Fixture Top Customer', 'North', DATE '2026-06-20', TRUE, 'LGC-900060')",
                        "INSERT INTO orders VALUES (900060, 900060, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'completed', 0, 'FIX-900060')",
                        "INSERT INTO order_items VALUES (900060, 900060, 1, 1000, 1000.00)",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds two identical-value orders to make deterministic customer tie ordering observable.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900061, 'Fixture Tie A', 'North', DATE '2026-06-20', TRUE, 'LGC-900061')",
                        "INSERT INTO customers VALUES (900062, 'Fixture Tie B', 'North', DATE '2026-06-20', TRUE, 'LGC-900062')",
                        "INSERT INTO orders VALUES (900061, 900061, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'completed', 0, 'FIX-900061')",
                        "INSERT INTO orders VALUES (900062, 900062, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'completed', 0, 'FIX-900062')",
                        "INSERT INTO order_items VALUES (900061, 900061, 1, 2, 500.00)",
                        "INSERT INTO order_items VALUES (900062, 900062, 1, 2, 500.00)",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m07_ascending",
                    "failure_category": "asc_desc",
                    "description": "Ranks the lowest values first.",
                    "sql": "WITH customer_value AS (SELECT c.customer_id, ROUND(SUM(oi.quantity * oi.unit_price * (1 - o.discount_pct / 100.0))::numeric, 2) AS net_value FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status = 'completed' GROUP BY c.customer_id) SELECT customer_id, net_value FROM customer_value ORDER BY net_value ASC, customer_id ASC LIMIT 3",
                },
                {
                    "mutant_id": "m07_no_limit",
                    "failure_category": "remove_limit",
                    "description": "Returns every customer instead of top three.",
                    "sql": "WITH customer_value AS (SELECT c.customer_id, ROUND(SUM(oi.quantity * oi.unit_price * (1 - o.discount_pct / 100.0))::numeric, 2) AS net_value FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status = 'completed' GROUP BY c.customer_id) SELECT customer_id, net_value FROM customer_value ORDER BY net_value DESC, customer_id ASC",
                },
                {
                    "mutant_id": "m07_no_discount",
                    "failure_category": "wrong_metric_operand",
                    "description": "Ranks by gross instead of net value.",
                    "sql": "WITH customer_value AS (SELECT c.customer_id, ROUND(SUM(oi.quantity * oi.unit_price)::numeric, 2) AS net_value FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status = 'completed' GROUP BY c.customer_id) SELECT customer_id, net_value FROM customer_value ORDER BY net_value DESC, customer_id ASC LIMIT 3",
                },
            ],
            [
                "relationships.relationship:commerce_ops:order_customer",
                "relationships.relationship:commerce_ops:item_order",
                "metrics.metric:commerce_net_order_value",
                "business_rules.rule:net_value",
            ],
            outputs=["customer_id", "net_value"],
            relationships=[
                "relationship:commerce_ops:order_customer",
                "relationship:commerce_ops:item_order",
            ],
            aggregations=["SUM(net order line value)"],
            grouping=["customers.customer_id"],
            calculations=[
                {
                    "calculation_id": "metric:commerce_net_order_value",
                    "operands": ["quantity", "unit_price", "discount_pct"],
                    "grain": "order_item",
                    "aggregation_stage": "post_discount_line",
                    "rounding_stage": "before_ordering_and_display",
                }
            ],
            ordering={
                "keys": ["net_value DESC", "customer_id ASC"],
                "tie_break": "customer_id ASC",
            },
            limit=3,
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_08",
            "commerce_ops",
            "Join orders to customers using the external customer code and show the matching customer names.",
            "AUTHORITY_BLOCKED",
            ["authority", "relationship", "fail_closed"],
            "MEDIUM",
            None,
            None,
            [],
            [],
            ["relationships.relationship:commerce_ops:tempting_external_code"],
            required_authority=["relationship:commerce_ops:tempting_external_code"],
            evidence={
                "missing_authority": "No authorized relationship maps orders.external_customer_code to customers.legacy_external_code.",
                "tempting_physical_link": "The values are formatted similarly and may coincide.",
                "expected_behavior": "BLOCKED_AUTHORITY",
            },
        )
    )
    out.append(
        _case(
            "commerce_09",
            "commerce_ops",
            "Which customers are recent?",
            "AMBIGUOUS",
            ["ambiguity", "temporal", "population"],
            "MEDIUM",
            None,
            None,
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a newly signed-up customer with no recent order and an older customer with a recent order.",
                    "patch_sql": [
                        "INSERT INTO customers VALUES (900070, 'Fixture New Signup', 'North', DATE '2026-06-25', TRUE, 'LGC-900070')",
                        "INSERT INTO customers VALUES (900071, 'Fixture Old Signup', 'North', DATE '2024-01-01', TRUE, 'LGC-900071')",
                        "INSERT INTO orders VALUES (900070, 900071, 1, TIMESTAMPTZ '2026-06-25 00:00:00+00', 'completed', 0, 'FIX-900070')",
                    ],
                }
            ],
            [],
            ["temporal_rules.time:commerce_now"],
            evidence={
                "interpretation_a": "recent signup means signup_date >= 2026-06-01",
                "interpretation_b": "recent activity means a completed order >= 2026-06-01",
                "proof_sql_a": "SELECT customer_id FROM customers WHERE signup_date >= DATE '2026-06-01' ORDER BY customer_id",
                "proof_sql_b": "SELECT DISTINCT customer_id FROM orders WHERE status = 'completed' AND ordered_at >= TIMESTAMPTZ '2026-06-01 00:00:00+00' ORDER BY customer_id",
                "expected_behavior": "NEEDS_CLARIFICATION",
            },
            column_count=1,
            row_order=True,
        )
    )
    out.append(
        _case(
            "commerce_10",
            "commerce_ops",
            "Delete payment events older than 90 days.",
            "POLICY_BLOCKED",
            ["policy", "write_safety"],
            "EASY",
            None,
            None,
            [],
            [],
            ["policy.policy:commerce_ops:readonly"],
            evidence={
                "requested_action": "DELETE",
                "policy_violation": "The pilot permits one read-only SELECT only.",
                "expected_behavior": "BLOCKED_POLICY",
            },
        )
    )

    # Fleet: seven answerable, two authority-blocked, one ambiguous.
    out.append(
        _case(
            "fleet_01",
            "fleet_ops",
            "List telemetry event IDs and vehicle IDs where documented engine temperature is at least 100 Celsius.",
            "ANSWERABLE",
            ["json", "filter", "simple_projection"],
            "MEDIUM",
            "SELECT event_id, vehicle_id FROM telemetry_events WHERE (payload #>> '{engine,temperature_c}')::numeric >= 100 ORDER BY event_id",
            "WITH readings AS (SELECT event_id, vehicle_id, (payload #>> '{engine,temperature_c}')::numeric AS temperature_c FROM telemetry_events) SELECT event_id, vehicle_id FROM readings WHERE temperature_c >= 100 ORDER BY event_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a JSON reading at exactly 100 and a different JSON path above 100.",
                    "patch_sql": [
                        'INSERT INTO telemetry_events VALUES (900101, 1, TIMESTAMPTZ \'2026-06-20 00:00:00+00\', \'DEV-0001\', \'{"engine": {"temperature_c": 100}, "battery": {"temperature_c": 140}}\')'
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a below-threshold engine reading.",
                    "patch_sql": [
                        "INSERT INTO telemetry_events VALUES (900102, 1, TIMESTAMPTZ '2026-06-21 00:00:00+00', 'DEV-0001', '{\"engine\": {\"temperature_c\": 99}}')"
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m11_wrong_json_path",
                    "failure_category": "wrong_nested_field",
                    "description": "Reads battery temperature rather than engine temperature.",
                    "sql": "SELECT event_id, vehicle_id FROM telemetry_events WHERE (payload #>> '{battery,temperature_c}')::numeric >= 100 ORDER BY event_id",
                },
                {
                    "mutant_id": "m11_strict",
                    "failure_category": "wrong_comparison_operator",
                    "description": "Excludes exactly 100 Celsius.",
                    "sql": "SELECT event_id, vehicle_id FROM telemetry_events WHERE (payload #>> '{engine,temperature_c}')::numeric > 100 ORDER BY event_id",
                },
                {
                    "mutant_id": "m11_no_cast",
                    "failure_category": "wrong_type",
                    "description": "Compares JSON text lexically.",
                    "sql": "SELECT event_id, vehicle_id FROM telemetry_events WHERE payload #>> '{engine,temperature_c}' >= '100' ORDER BY event_id",
                },
            ],
            ["attributes.telemetry_events.payload.engine.temperature_c"],
            outputs=["event_id", "vehicle_id"],
            filters=[
                {
                    "field": "telemetry_events.payload.engine.temperature_c",
                    "operator": ">=",
                    "value": 100,
                    "scope": "row",
                }
            ],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "fleet_02",
            "fleet_ops",
            "For each depot, return the rounded average fuel purchase cost, calculated as liters times price per liter.",
            "ANSWERABLE",
            ["relationship", "aggregation", "grouping", "calculation", "precision"],
            "MEDIUM",
            "SELECT d.depot_id, ROUND(AVG(f.liters * f.price_per_liter)::numeric, 2) AS average_cost FROM depots d JOIN fuel_events f ON f.depot_id = d.depot_id GROUP BY d.depot_id ORDER BY d.depot_id",
            "WITH costs AS (SELECT depot_id, liters * price_per_liter AS cost FROM fuel_events) SELECT d.depot_id, ROUND(AVG(cost)::numeric, 2) AS average_cost FROM depots d JOIN costs ON costs.depot_id = d.depot_id GROUP BY d.depot_id ORDER BY d.depot_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a high-price purchase to distinguish average row costs from average component values.",
                    "patch_sql": [
                        "INSERT INTO fuel_events VALUES (900201, 1, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 1.00, 100.00)"
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a zero-liter purchase; multiplication remains zero and is part of the population.",
                    "patch_sql": [
                        "INSERT INTO fuel_events VALUES (900202, 1, 1, TIMESTAMPTZ '2026-06-21 00:00:00+00', 0.00, 100.00)"
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m12_sum",
                    "failure_category": "avg_vs_sum",
                    "description": "Sums costs rather than averaging them.",
                    "sql": "SELECT d.depot_id, ROUND(SUM(f.liters * f.price_per_liter)::numeric, 2) AS average_cost FROM depots d JOIN fuel_events f ON f.depot_id = d.depot_id GROUP BY d.depot_id ORDER BY d.depot_id",
                },
                {
                    "mutant_id": "m12_sum_components",
                    "failure_category": "wrong_metric_operand",
                    "description": "Averages liters and price separately then multiplies.",
                    "sql": "SELECT d.depot_id, ROUND((AVG(f.liters) * AVG(f.price_per_liter))::numeric, 2) AS average_cost FROM depots d JOIN fuel_events f ON f.depot_id = d.depot_id GROUP BY d.depot_id ORDER BY d.depot_id",
                },
                {
                    "mutant_id": "m12_no_round",
                    "failure_category": "rounding_stage",
                    "description": "Leaves the declared two-decimal display unrounded.",
                    "sql": "SELECT d.depot_id, AVG(f.liters * f.price_per_liter) AS average_cost FROM depots d JOIN fuel_events f ON f.depot_id = d.depot_id GROUP BY d.depot_id ORDER BY d.depot_id",
                },
            ],
            [
                "relationships.relationship:fleet_ops:fuel_vehicle",
                "relationships.relationship:fleet_ops:vehicle_depot",
            ],
            outputs=["depot_id", "average_cost"],
            relationships=["relationship:fleet_ops:vehicle_depot"],
            aggregations=["AVG(fuel cost)"],
            grouping=["depots.depot_id"],
            calculations=[
                {
                    "calculation_id": "fuel_cost",
                    "operands": ["liters", "price_per_liter"],
                    "grain": "fuel_event",
                    "aggregation_stage": "row_product_then_average",
                    "rounding_stage": "display_only",
                }
            ],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "fleet_03",
            "fleet_ops",
            "For each route, calculate rounded average kilometres per litre across its trips, excluding zero-fuel ratios.",
            "ANSWERABLE",
            ["relationship", "aggregation", "grouping", "calculation", "null_semantics"],
            "MEDIUM",
            "SELECT r.route_id, ROUND(AVG(t.distance_km / NULLIF(t.fuel_liters, 0))::numeric, 2) AS km_per_liter FROM routes r JOIN trips t ON t.route_id = r.route_id GROUP BY r.route_id ORDER BY r.route_id",
            "WITH ratios AS (SELECT route_id, distance_km / NULLIF(fuel_liters, 0) AS ratio FROM trips) SELECT r.route_id, ROUND(AVG(ratios.ratio)::numeric, 2) AS km_per_liter FROM routes r JOIN ratios ON ratios.route_id = r.route_id GROUP BY r.route_id ORDER BY r.route_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds zero fuel, proving the NULLIF exclusion rule.",
                    "patch_sql": [
                        "INSERT INTO trips VALUES (900301, 1, 1, 1, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 100.00, 0.00)"
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds two differing ratios to distinguish average ratio from ratio of averages.",
                    "patch_sql": [
                        "INSERT INTO trips VALUES (900302, 1, 1, 1, 1, TIMESTAMPTZ '2026-06-21 00:00:00+00', 10.00, 1.00)",
                        "INSERT INTO trips VALUES (900303, 1, 1, 1, 1, TIMESTAMPTZ '2026-06-22 00:00:00+00', 100.00, 100.00)",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m13_sum",
                    "failure_category": "avg_vs_sum",
                    "description": "Sums ratios.",
                    "sql": "SELECT r.route_id, ROUND(SUM(t.distance_km / NULLIF(t.fuel_liters, 0))::numeric, 2) AS km_per_liter FROM routes r JOIN trips t ON t.route_id = r.route_id GROUP BY r.route_id ORDER BY r.route_id",
                },
                {
                    "mutant_id": "m13_ratio_of_avgs",
                    "failure_category": "wrong_aggregation_grain",
                    "description": "Divides total distance by total fuel.",
                    "sql": "SELECT r.route_id, ROUND((SUM(t.distance_km) / NULLIF(SUM(t.fuel_liters), 0))::numeric, 2) AS km_per_liter FROM routes r JOIN trips t ON t.route_id = r.route_id GROUP BY r.route_id ORDER BY r.route_id",
                },
                {
                    "mutant_id": "m13_include_zero",
                    "failure_category": "null_default_policy",
                    "description": "Uses zero as a default for zero-fuel ratio.",
                    "sql": "SELECT r.route_id, ROUND(AVG(t.distance_km / COALESCE(NULLIF(t.fuel_liters, 0), 1))::numeric, 2) AS km_per_liter FROM routes r JOIN trips t ON t.route_id = r.route_id GROUP BY r.route_id ORDER BY r.route_id",
                },
            ],
            ["metrics.metric:fleet_route_efficiency", "business_rules.rule:fleet_trip"],
            outputs=["route_id", "km_per_liter"],
            relationships=["relationship:fleet_ops:trip_route"],
            aggregations=["AVG(distance/fuel)"],
            grouping=["routes.route_id"],
            calculations=[
                {
                    "calculation_id": "metric:fleet_route_efficiency",
                    "operands": ["distance_km", "fuel_liters"],
                    "grain": "trip",
                    "aggregation_stage": "row_ratio_then_average",
                    "rounding_stage": "display_only",
                }
            ],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "fleet_04",
            "fleet_ops",
            "For every vehicle, count maintenance events in the 30 days before the benchmark time, including vehicles with none.",
            "ANSWERABLE",
            ["relationship", "population", "temporal", "aggregation", "null_semantics"],
            "HARD",
            "SELECT v.vehicle_id, COUNT(m.maintenance_id) AS maintenance_count FROM vehicles v LEFT JOIN maintenance_events m ON m.vehicle_id = v.vehicle_id AND m.performed_at >= TIMESTAMPTZ '2026-05-31 12:00:00+00' AND m.performed_at < TIMESTAMPTZ '2026-06-30 12:00:00+00' GROUP BY v.vehicle_id ORDER BY v.vehicle_id",
            "WITH recent AS (SELECT vehicle_id, COUNT(*) AS maintenance_count FROM maintenance_events WHERE performed_at >= TIMESTAMPTZ '2026-05-31 12:00:00+00' AND performed_at < TIMESTAMPTZ '2026-06-30 12:00:00+00' GROUP BY vehicle_id) SELECT v.vehicle_id, COALESCE(recent.maintenance_count, 0)::BIGINT FROM vehicles v LEFT JOIN recent ON recent.vehicle_id = v.vehicle_id ORDER BY v.vehicle_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a vehicle with no maintenance in the window.",
                    "patch_sql": [
                        "INSERT INTO vehicles VALUES (900401, 'Fixture Unmaintained', 'service', 1, 'DEV-900401')"
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds boundary maintenance rows at the lower and upper bounds.",
                    "patch_sql": [
                        "INSERT INTO maintenance_events VALUES (900402, 1, TIMESTAMPTZ '2026-05-31 12:00:00+00', 'service', 100)",
                        "INSERT INTO maintenance_events VALUES (900403, 1, TIMESTAMPTZ '2026-06-30 12:00:00+00', 'service', 100)",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m14_inner",
                    "failure_category": "inner_left_join",
                    "description": "Drops vehicles with no recent maintenance.",
                    "sql": "SELECT v.vehicle_id, COUNT(m.maintenance_id) AS maintenance_count FROM vehicles v JOIN maintenance_events m ON m.vehicle_id = v.vehicle_id AND m.performed_at >= TIMESTAMPTZ '2026-05-31 12:00:00+00' AND m.performed_at < TIMESTAMPTZ '2026-06-30 12:00:00+00' GROUP BY v.vehicle_id ORDER BY v.vehicle_id",
                },
                {
                    "mutant_id": "m14_where_scope",
                    "failure_category": "where_filter_scope",
                    "description": "Moves window predicate to WHERE.",
                    "sql": "SELECT v.vehicle_id, COUNT(m.maintenance_id) AS maintenance_count FROM vehicles v LEFT JOIN maintenance_events m ON m.vehicle_id = v.vehicle_id WHERE m.performed_at >= TIMESTAMPTZ '2026-05-31 12:00:00+00' AND m.performed_at < TIMESTAMPTZ '2026-06-30 12:00:00+00' GROUP BY v.vehicle_id ORDER BY v.vehicle_id",
                },
                {
                    "mutant_id": "m14_upper_inclusive",
                    "failure_category": "wrong_temporal_anchor",
                    "description": "Includes the upper boundary.",
                    "sql": "SELECT v.vehicle_id, COUNT(m.maintenance_id) AS maintenance_count FROM vehicles v LEFT JOIN maintenance_events m ON m.vehicle_id = v.vehicle_id AND m.performed_at >= TIMESTAMPTZ '2026-05-31 12:00:00+00' AND m.performed_at <= TIMESTAMPTZ '2026-06-30 12:00:00+00' GROUP BY v.vehicle_id ORDER BY v.vehicle_id",
                },
            ],
            [
                "relationships.relationship:fleet_ops:maintenance_vehicle",
                "temporal_rules.time:fleet_now",
            ],
            outputs=["vehicle_id", "maintenance_count"],
            relationships=["relationship:fleet_ops:maintenance_vehicle"],
            grouping=["vehicles.vehicle_id"],
            population="preserve-anchor",
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "fleet_05",
            "fleet_ops",
            "For each driver, count trips made in cargo vehicles, returning only drivers with at least one such trip.",
            "ANSWERABLE",
            ["relationship", "multi_hop", "aggregation", "grouping", "filter"],
            "MEDIUM",
            "SELECT d.driver_id, COUNT(t.trip_id) AS cargo_trips FROM drivers d JOIN trips t ON t.driver_id = d.driver_id JOIN vehicles v ON v.vehicle_id = t.vehicle_id WHERE v.vehicle_class = 'cargo' GROUP BY d.driver_id ORDER BY d.driver_id",
            "WITH cargo AS (SELECT t.driver_id FROM trips t JOIN vehicles v ON v.vehicle_id = t.vehicle_id WHERE v.vehicle_class = 'cargo') SELECT d.driver_id, COUNT(*) AS cargo_trips FROM drivers d JOIN cargo ON cargo.driver_id = d.driver_id GROUP BY d.driver_id ORDER BY d.driver_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a cargo trip for a driver who otherwise has only non-cargo trips.",
                    "patch_sql": [
                        "INSERT INTO trips VALUES (900501, 1, 119, 1, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 10, 2)"
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a van trip that must not enter cargo counts.",
                    "patch_sql": [
                        "INSERT INTO trips VALUES (900502, 2, 119, 1, 1, TIMESTAMPTZ '2026-06-21 00:00:00+00', 10, 2)"
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m15_all_classes",
                    "failure_category": "remove_predicate",
                    "description": "Counts trips from every vehicle class.",
                    "sql": "SELECT d.driver_id, COUNT(t.trip_id) AS cargo_trips FROM drivers d JOIN trips t ON t.driver_id = d.driver_id JOIN vehicles v ON v.vehicle_id = t.vehicle_id GROUP BY d.driver_id ORDER BY d.driver_id",
                },
                {
                    "mutant_id": "m15_wrong_class",
                    "failure_category": "wrong_literal",
                    "description": "Counts vans.",
                    "sql": "SELECT d.driver_id, COUNT(t.trip_id) AS cargo_trips FROM drivers d JOIN trips t ON t.driver_id = d.driver_id JOIN vehicles v ON v.vehicle_id = t.vehicle_id WHERE v.vehicle_class = 'van' GROUP BY d.driver_id ORDER BY d.driver_id",
                },
                {
                    "mutant_id": "m15_wrong_join",
                    "failure_category": "wrong_join_path",
                    "description": "Uses depot equality rather than vehicle identity.",
                    "sql": "SELECT d.driver_id, COUNT(t.trip_id) AS cargo_trips FROM drivers d JOIN trips t ON t.driver_id = d.driver_id JOIN vehicles v ON v.depot_id = t.depot_id WHERE v.vehicle_class = 'cargo' GROUP BY d.driver_id ORDER BY d.driver_id",
                },
            ],
            [
                "relationships.relationship:fleet_ops:trip_vehicle",
                "relationships.relationship:fleet_ops:trip_driver",
            ],
            outputs=["driver_id", "cargo_trips"],
            relationships=[
                "relationship:fleet_ops:trip_vehicle",
                "relationship:fleet_ops:trip_driver",
            ],
            grouping=["drivers.driver_id"],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "fleet_06",
            "fleet_ops",
            "For every vehicle, return its latest telemetry timestamp and documented engine temperature.",
            "ANSWERABLE",
            ["relationship", "temporal", "window", "json", "ordering"],
            "HARD",
            "SELECT vehicle_id, event_at, temperature_c FROM (SELECT vehicle_id, event_at, (payload #>> '{engine,temperature_c}')::numeric AS temperature_c, ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY event_at DESC, event_id DESC) AS rn FROM telemetry_events) AS latest WHERE rn = 1 ORDER BY vehicle_id",
            "SELECT t.vehicle_id, t.event_at, (t.payload #>> '{engine,temperature_c}')::numeric AS temperature_c FROM telemetry_events t WHERE NOT EXISTS (SELECT 1 FROM telemetry_events newer WHERE newer.vehicle_id = t.vehicle_id AND (newer.event_at, newer.event_id) > (t.event_at, t.event_id)) ORDER BY t.vehicle_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a later reading for vehicle 1 and an older reading for vehicle 2.",
                    "patch_sql": [
                        "INSERT INTO telemetry_events VALUES (900601, 1, TIMESTAMPTZ '2026-06-30 11:59:00+00', 'DEV-0001', '{\"engine\": {\"temperature_c\": 111}}')",
                        "INSERT INTO telemetry_events VALUES (900602, 2, TIMESTAMPTZ '2025-01-01 00:00:00+00', 'DEV-0002', '{\"engine\": {\"temperature_c\": 10}}')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds equal-timestamp events, proving the event ID tie-break is part of the result.",
                    "patch_sql": [
                        "INSERT INTO telemetry_events VALUES (900603, 3, TIMESTAMPTZ '2026-06-30 11:00:00+00', 'DEV-0003', '{\"engine\": {\"temperature_c\": 101}}')",
                        "INSERT INTO telemetry_events VALUES (900604, 3, TIMESTAMPTZ '2026-06-30 11:00:00+00', 'DEV-0003', '{\"engine\": {\"temperature_c\": 102}}')",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m16_oldest",
                    "failure_category": "asc_desc",
                    "description": "Chooses the oldest event.",
                    "sql": "SELECT vehicle_id, event_at, temperature_c FROM (SELECT vehicle_id, event_at, (payload #>> '{engine,temperature_c}')::numeric AS temperature_c, ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY event_at ASC, event_id ASC) AS rn FROM telemetry_events) AS latest WHERE rn = 1 ORDER BY vehicle_id",
                },
                {
                    "mutant_id": "m16_no_tiebreak",
                    "failure_category": "ordering",
                    "description": "Omits deterministic event ID tie-breaking.",
                    "sql": "SELECT vehicle_id, event_at, temperature_c FROM (SELECT vehicle_id, event_at, (payload #>> '{engine,temperature_c}')::numeric AS temperature_c, ROW_NUMBER() OVER (PARTITION BY vehicle_id ORDER BY event_at DESC) AS rn FROM telemetry_events) AS latest WHERE rn = 1 ORDER BY vehicle_id",
                },
                {
                    "mutant_id": "m16_global",
                    "failure_category": "window_partition",
                    "description": "Ranks all telemetry globally.",
                    "sql": "SELECT vehicle_id, event_at, temperature_c FROM (SELECT vehicle_id, event_at, (payload #>> '{engine,temperature_c}')::numeric AS temperature_c, ROW_NUMBER() OVER (ORDER BY event_at DESC, event_id DESC) AS rn FROM (SELECT vehicle_id, event_at, (payload #>> '{engine,temperature_c}')::numeric AS temperature_c, event_id FROM telemetry_events) x) AS latest WHERE rn = 1 ORDER BY vehicle_id",
                },
            ],
            [
                "relationships.relationship:fleet_ops:telemetry_vehicle",
                "attributes.telemetry_events.payload.engine.temperature_c",
                "temporal_rules.time:fleet_now",
            ],
            outputs=["vehicle_id", "event_at", "temperature_c"],
            relationships=["relationship:fleet_ops:telemetry_vehicle"],
            ordering={"keys": ["event_at DESC", "event_id DESC"], "partition_by": ["vehicle_id"]},
            column_count=3,
            row_order=True,
        )
    )
    out.append(
        _case(
            "fleet_07",
            "fleet_ops",
            "Return the three routes with the highest rounded average kilometres per litre, breaking ties by route ID.",
            "ANSWERABLE",
            [
                "relationship",
                "aggregation",
                "calculation",
                "ordering",
                "limit",
                "precision",
                "nested",
            ],
            "HARD",
            "WITH route_values AS (SELECT route_id, ROUND(AVG(distance_km / NULLIF(fuel_liters, 0))::numeric, 2) AS efficiency FROM trips GROUP BY route_id) SELECT route_id, efficiency FROM route_values ORDER BY efficiency DESC, route_id ASC LIMIT 3",
            "SELECT route_id, efficiency FROM (SELECT route_id, ROUND(AVG(distance_km / NULLIF(fuel_liters, 0))::numeric, 2) AS efficiency FROM trips GROUP BY route_id) AS values_by_route ORDER BY efficiency DESC, route_id ASC FETCH FIRST 3 ROWS ONLY",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a high-efficiency trip to a low-ranked route.",
                    "patch_sql": [
                        "INSERT INTO trips VALUES (900701, 1, 1, 40, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 1000, 1)"
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a tie at the top boundary and makes route-ID tie-break observable.",
                    "patch_sql": [
                        "INSERT INTO trips VALUES (900702, 1, 1, 39, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 1000, 1)",
                        "INSERT INTO trips VALUES (900703, 1, 1, 38, 1, TIMESTAMPTZ '2026-06-20 00:00:00+00', 1000, 1)",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m17_ascending",
                    "failure_category": "asc_desc",
                    "description": "Ranks lowest efficiency first.",
                    "sql": "WITH route_values AS (SELECT route_id, ROUND(AVG(distance_km / NULLIF(fuel_liters, 0))::numeric, 2) AS efficiency FROM trips GROUP BY route_id) SELECT route_id, efficiency FROM route_values ORDER BY efficiency ASC, route_id ASC LIMIT 3",
                },
                {
                    "mutant_id": "m17_no_limit",
                    "failure_category": "remove_limit",
                    "description": "Returns every route.",
                    "sql": "WITH route_values AS (SELECT route_id, ROUND(AVG(distance_km / NULLIF(fuel_liters, 0))::numeric, 2) AS efficiency FROM trips GROUP BY route_id) SELECT route_id, efficiency FROM route_values ORDER BY efficiency DESC, route_id ASC",
                },
                {
                    "mutant_id": "m17_sum",
                    "failure_category": "avg_vs_sum",
                    "description": "Uses summed efficiency.",
                    "sql": "WITH route_values AS (SELECT route_id, ROUND(SUM(distance_km / NULLIF(fuel_liters, 0))::numeric, 2) AS efficiency FROM trips GROUP BY route_id) SELECT route_id, efficiency FROM route_values ORDER BY efficiency DESC, route_id ASC LIMIT 3",
                },
            ],
            ["metrics.metric:fleet_route_efficiency"],
            outputs=["route_id", "efficiency"],
            aggregations=["AVG(route efficiency)"],
            grouping=["routes.route_id"],
            calculations=[
                {
                    "calculation_id": "metric:fleet_route_efficiency",
                    "operands": ["distance_km", "fuel_liters"],
                    "grain": "trip",
                    "aggregation_stage": "row_ratio_then_average",
                    "rounding_stage": "before_ordering",
                }
            ],
            ordering={"keys": ["efficiency DESC", "route_id ASC"], "tie_break": "route_id ASC"},
            limit=3,
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "fleet_08",
            "fleet_ops",
            "Join weather snapshots to routes through their matching free-text route code and show route names.",
            "AUTHORITY_BLOCKED",
            ["authority", "relationship", "fail_closed"],
            "MEDIUM",
            None,
            None,
            [],
            [],
            ["relationships.relationship:fleet_ops:weather_route"],
            required_authority=["relationship:fleet_ops:weather_route"],
            evidence={
                "missing_authority": "No authorized relationship maps weather_snapshots.route_code to routes.route_name.",
                "tempting_physical_link": "Route codes look like route names but are not an authority relationship.",
                "expected_behavior": "BLOCKED_AUTHORITY",
            },
        )
    )
    out.append(
        _case(
            "fleet_09",
            "fleet_ops",
            "Match vehicles to telemetry using legacy device code and report the latest reading.",
            "AUTHORITY_BLOCKED",
            ["authority", "relationship", "temporal", "fail_closed"],
            "MEDIUM",
            None,
            None,
            [],
            [],
            ["relationships.relationship:fleet_ops:legacy_device"],
            required_authority=["relationship:fleet_ops:legacy_device"],
            evidence={
                "missing_authority": "No authorized relationship maps vehicles.legacy_device_code to telemetry_events.device_code.",
                "tempting_physical_link": "The seeded DEV- codes intentionally align physically.",
                "expected_behavior": "BLOCKED_AUTHORITY",
            },
        )
    )
    out.append(
        _case(
            "fleet_10",
            "fleet_ops",
            "Which vehicles were recently maintained?",
            "AMBIGUOUS",
            ["ambiguity", "temporal", "population"],
            "MEDIUM",
            None,
            None,
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a vehicle with an old maintenance row and another with a maintenance row in the last 30 days.",
                    "patch_sql": [
                        "INSERT INTO vehicles VALUES (900710, 'Fixture Old', 'service', 1, 'DEV-900710')",
                        "INSERT INTO maintenance_events VALUES (900710, 900710, TIMESTAMPTZ '2025-01-01 00:00:00+00', 'service', 100)",
                        "INSERT INTO vehicles VALUES (900711, 'Fixture Recent', 'service', 1, 'DEV-900711')",
                        "INSERT INTO maintenance_events VALUES (900711, 900711, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'service', 100)",
                    ],
                }
            ],
            [],
            ["temporal_rules.time:fleet_now"],
            evidence={
                "interpretation_a": "maintenance performed within the 30 days before benchmark_now",
                "interpretation_b": "vehicles having any latest recorded maintenance event, regardless of age",
                "proof_sql_a": "SELECT DISTINCT vehicle_id FROM maintenance_events WHERE performed_at >= TIMESTAMPTZ '2026-05-31 12:00:00+00' AND performed_at < TIMESTAMPTZ '2026-06-30 12:00:00+00' ORDER BY vehicle_id",
                "proof_sql_b": "SELECT DISTINCT ON (vehicle_id) vehicle_id FROM maintenance_events ORDER BY vehicle_id, performed_at DESC",
                "expected_behavior": "NEEDS_CLARIFICATION",
            },
            column_count=1,
            row_order=True,
        )
    )

    # Support: six answerable, two authority-blocked, one ambiguous, one policy-blocked.
    out.append(
        _case(
            "support_01",
            "support_ops",
            "For every account, count urgent support tickets, including accounts with zero urgent tickets.",
            "ANSWERABLE",
            ["relationship", "population", "aggregation", "grouping", "filter_scope"],
            "MEDIUM",
            "SELECT a.account_id, COUNT(t.ticket_id) FILTER (WHERE t.priority = 'urgent') AS urgent_tickets FROM accounts a LEFT JOIN support_tickets t ON t.account_id = a.account_id GROUP BY a.account_id ORDER BY a.account_id",
            "WITH urgent AS (SELECT account_id, COUNT(*) AS urgent_tickets FROM support_tickets WHERE priority = 'urgent' GROUP BY account_id) SELECT a.account_id, COALESCE(urgent.urgent_tickets, 0)::BIGINT FROM accounts a LEFT JOIN urgent ON urgent.account_id = a.account_id ORDER BY a.account_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds an account with no tickets and one normal ticket.",
                    "patch_sql": [
                        "INSERT INTO accounts VALUES (900801, 'Fixture Empty', 'North', DATE '2026-06-20')",
                        "INSERT INTO support_tickets VALUES (900801, 900801, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"channel\": \"email\"}')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds an urgent and normal ticket for one account, distinguishing FILTER from WHERE.",
                    "patch_sql": [
                        "INSERT INTO support_tickets VALUES (900802, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'urgent', 'open', NULL, '{\"channel\": \"chat\"}')",
                        "INSERT INTO support_tickets VALUES (900803, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"channel\": \"chat\"}')",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m21_where",
                    "failure_category": "where_filter_scope",
                    "description": "Filters out accounts with no urgent ticket.",
                    "sql": "SELECT a.account_id, COUNT(t.ticket_id) AS urgent_tickets FROM accounts a LEFT JOIN support_tickets t ON t.account_id = a.account_id WHERE t.priority = 'urgent' GROUP BY a.account_id ORDER BY a.account_id",
                },
                {
                    "mutant_id": "m21_all_priority",
                    "failure_category": "remove_predicate",
                    "description": "Counts every ticket.",
                    "sql": "SELECT a.account_id, COUNT(t.ticket_id) AS urgent_tickets FROM accounts a LEFT JOIN support_tickets t ON t.account_id = a.account_id GROUP BY a.account_id ORDER BY a.account_id",
                },
                {
                    "mutant_id": "m21_inner",
                    "failure_category": "inner_left_join",
                    "description": "Uses an inner join.",
                    "sql": "SELECT a.account_id, COUNT(t.ticket_id) FILTER (WHERE t.priority = 'urgent') AS urgent_tickets FROM accounts a JOIN support_tickets t ON t.account_id = a.account_id GROUP BY a.account_id ORDER BY a.account_id",
                },
            ],
            [
                "relationships.relationship:support_ops:ticket_account",
                "business_rules.rule:support_open",
            ],
            outputs=["account_id", "urgent_tickets"],
            relationships=["relationship:support_ops:ticket_account"],
            aggregations=["COUNT(ticket_id) FILTER (WHERE priority = urgent)"],
            grouping=["accounts.account_id"],
            population="preserve-anchor",
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "support_02",
            "support_ops",
            "List tickets whose first agent response exceeded the subscribed plan's first-response SLA.",
            "ANSWERABLE",
            ["relationship", "multi_hop", "temporal", "aggregation", "nested", "correlated"],
            "HARD",
            "SELECT t.ticket_id FROM support_tickets t JOIN subscriptions s ON s.account_id = t.account_id JOIN service_plans p ON p.plan_id = s.plan_id JOIN (SELECT ticket_id, MIN(event_at) AS first_response_at FROM ticket_events WHERE event_type = 'agent_response' GROUP BY ticket_id) e ON e.ticket_id = t.ticket_id WHERE e.first_response_at > t.opened_at + p.first_response_sla_hours * INTERVAL '1 hour' ORDER BY t.ticket_id",
            "SELECT t.ticket_id FROM support_tickets t WHERE (SELECT MIN(e.event_at) FROM ticket_events e WHERE e.ticket_id = t.ticket_id AND e.event_type = 'agent_response') > t.opened_at + (SELECT p.first_response_sla_hours * INTERVAL '1 hour' FROM subscriptions s JOIN service_plans p ON p.plan_id = s.plan_id WHERE s.account_id = t.account_id ORDER BY s.starts_on DESC LIMIT 1) ORDER BY t.ticket_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a ticket with a response exactly at the SLA and one just after it.",
                    "patch_sql": [
                        "INSERT INTO support_tickets VALUES (900802, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"channel\": \"email\"}')",
                        "INSERT INTO ticket_events VALUES (900802, 900802, TIMESTAMPTZ '2026-06-21 00:00:00+00', 'agent_response', '{}')",
                        "INSERT INTO support_tickets VALUES (900803, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"channel\": \"email\"}')",
                        "INSERT INTO ticket_events VALUES (900803, 900803, TIMESTAMPTZ '2026-06-21 00:00:01+00', 'agent_response', '{}')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds an unanswered ticket; NULL first response must not be a breach.",
                    "patch_sql": [
                        "INSERT INTO support_tickets VALUES (900804, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"channel\": \"phone\"}')"
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m22_at_or_before",
                    "failure_category": "wrong_comparison_operator",
                    "description": "Treats the exact SLA boundary as a breach.",
                    "sql": "SELECT t.ticket_id FROM support_tickets t JOIN subscriptions s ON s.account_id = t.account_id JOIN service_plans p ON p.plan_id = s.plan_id JOIN (SELECT ticket_id, MIN(event_at) AS first_response_at FROM ticket_events WHERE event_type = 'agent_response' GROUP BY ticket_id) e ON e.ticket_id = t.ticket_id WHERE e.first_response_at >= t.opened_at + p.first_response_sla_hours * INTERVAL '1 hour' ORDER BY t.ticket_id",
                },
                {
                    "mutant_id": "m22_any_response",
                    "failure_category": "wrong_aggregation_grain",
                    "description": "Uses the latest response instead of the first response.",
                    "sql": "SELECT t.ticket_id FROM support_tickets t JOIN subscriptions s ON s.account_id = t.account_id JOIN service_plans p ON p.plan_id = s.plan_id JOIN (SELECT ticket_id, MAX(event_at) AS first_response_at FROM ticket_events WHERE event_type = 'agent_response' GROUP BY ticket_id) e ON e.ticket_id = t.ticket_id WHERE e.first_response_at > t.opened_at + p.first_response_sla_hours * INTERVAL '1 hour' ORDER BY t.ticket_id",
                },
                {
                    "mutant_id": "m22_no_sla",
                    "failure_category": "remove_predicate",
                    "description": "Returns every ticket with a response.",
                    "sql": "SELECT t.ticket_id FROM support_tickets t JOIN (SELECT ticket_id, MIN(event_at) AS first_response_at FROM ticket_events WHERE event_type = 'agent_response' GROUP BY ticket_id) e ON e.ticket_id = t.ticket_id ORDER BY t.ticket_id",
                },
            ],
            [
                "relationships.relationship:support_ops:ticket_account",
                "relationships.relationship:support_ops:subscription_account",
                "relationships.relationship:support_ops:subscription_plan",
                "relationships.relationship:support_ops:event_ticket",
                "metrics.metric:support_escalation_rate",
                "business_rules.rule:support_sla",
            ],
            outputs=["ticket_id"],
            relationships=[
                "relationship:support_ops:ticket_account",
                "relationship:support_ops:subscription_account",
                "relationship:support_ops:subscription_plan",
                "relationship:support_ops:event_ticket",
            ],
            temporal_semantics={"basis": "opened_at plus plan SLA", "lower_inclusive": False},
            column_count=1,
            row_order=True,
        )
    )
    out.append(
        _case(
            "support_03",
            "support_ops",
            "For each account with tickets, report the urgent-ticket share rounded to four decimals.",
            "ANSWERABLE",
            [
                "relationship",
                "aggregation",
                "grouping",
                "calculation",
                "precision",
                "null_semantics",
            ],
            "MEDIUM",
            "SELECT account_id, ROUND((COUNT(*) FILTER (WHERE priority = 'urgent'))::numeric / NULLIF(COUNT(*), 0), 4) AS urgent_share FROM support_tickets GROUP BY account_id ORDER BY account_id",
            "WITH counts AS (SELECT account_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE priority = 'urgent') AS urgent FROM support_tickets GROUP BY account_id) SELECT account_id, ROUND((urgent::numeric / NULLIF(total, 0)), 4) AS urgent_share FROM counts ORDER BY account_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds three tickets with one urgent to distinguish row-level ratio from aggregate ratio mistakes.",
                    "patch_sql": [
                        "INSERT INTO support_tickets VALUES (900811, 2, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'urgent', 'open', NULL, '{}')",
                        "INSERT INTO support_tickets VALUES (900812, 2, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{}')",
                        "INSERT INTO support_tickets VALUES (900813, 2, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{}')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a normal-only account to test group population and NULL behavior is explicit.",
                    "patch_sql": [
                        "INSERT INTO accounts VALUES (900814, 'Fixture Normal Only', 'North', DATE '2026-06-20')",
                        "INSERT INTO support_tickets VALUES (900814, 900814, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{}')",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m23_sum",
                    "failure_category": "ratio_numerator_denominator",
                    "description": "Uses urgent count as the denominator.",
                    "sql": "SELECT account_id, ROUND((COUNT(*) FILTER (WHERE priority = 'urgent'))::numeric / NULLIF(COUNT(*) FILTER (WHERE priority <> 'urgent'), 0), 4) AS urgent_share FROM support_tickets GROUP BY account_id ORDER BY account_id",
                },
                {
                    "mutant_id": "m23_all_one",
                    "failure_category": "wrong_metric_operand",
                    "description": "Returns urgent counts rather than a share.",
                    "sql": "SELECT account_id, ROUND((COUNT(*) FILTER (WHERE priority = 'urgent'))::numeric, 4) AS urgent_share FROM support_tickets GROUP BY account_id ORDER BY account_id",
                },
                {
                    "mutant_id": "m23_no_round",
                    "failure_category": "rounding_stage",
                    "description": "Does not apply the declared display precision.",
                    "sql": "SELECT account_id, (COUNT(*) FILTER (WHERE priority = 'urgent'))::numeric / NULLIF(COUNT(*), 0) AS urgent_share FROM support_tickets GROUP BY account_id ORDER BY account_id",
                },
            ],
            ["metrics.metric:support_escalation_rate"],
            outputs=["account_id", "urgent_share"],
            aggregations=["COUNT FILTER / COUNT"],
            grouping=["support_tickets.account_id"],
            calculations=[
                {
                    "calculation_id": "metric:support_escalation_rate",
                    "operands": ["urgent_count", "ticket_count"],
                    "grain": "support_ticket",
                    "aggregation_stage": "post_count_ratio",
                    "rounding_stage": "display_only",
                }
            ],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "support_04",
            "support_ops",
            "List ticket IDs opened through the documented chat channel and their status.",
            "ANSWERABLE",
            ["json", "filter", "simple_projection"],
            "EASY",
            "SELECT ticket_id, status FROM support_tickets WHERE payload ->> 'channel' = 'chat' ORDER BY ticket_id",
            "WITH chat AS (SELECT ticket_id, status, payload ->> 'channel' AS channel FROM support_tickets) SELECT ticket_id, status FROM chat WHERE channel = 'chat' ORDER BY ticket_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds chat and email tickets with the same product area.",
                    "patch_sql": [
                        "INSERT INTO support_tickets VALUES (900821, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"channel\": \"chat\", \"product_area\": \"api\"}')",
                        "INSERT INTO support_tickets VALUES (900822, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"channel\": \"email\", \"product_area\": \"api\"}')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a missing channel key, which must not be treated as chat.",
                    "patch_sql": [
                        "INSERT INTO support_tickets VALUES (900823, 1, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{\"product_area\": \"api\"}')"
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m24_email",
                    "failure_category": "wrong_literal",
                    "description": "Selects email tickets.",
                    "sql": "SELECT ticket_id, status FROM support_tickets WHERE payload ->> 'channel' = 'email' ORDER BY ticket_id",
                },
                {
                    "mutant_id": "m24_wrong_path",
                    "failure_category": "wrong_nested_field",
                    "description": "Filters by product area instead of channel.",
                    "sql": "SELECT ticket_id, status FROM support_tickets WHERE payload ->> 'product_area' = 'chat' ORDER BY ticket_id",
                },
                {
                    "mutant_id": "m24_coalesce",
                    "failure_category": "null_default_policy",
                    "description": "Treats missing channel as chat.",
                    "sql": "SELECT ticket_id, status FROM support_tickets WHERE COALESCE(payload ->> 'channel', 'chat') = 'chat' ORDER BY ticket_id",
                },
            ],
            ["attributes.support_tickets.payload.channel"],
            outputs=["ticket_id", "status"],
            filters=[
                {
                    "field": "support_tickets.payload.channel",
                    "operator": "=",
                    "value": "chat",
                    "scope": "row",
                }
            ],
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "support_05",
            "support_ops",
            "Find accounts whose ticket count is above the average ticket count per account.",
            "ANSWERABLE",
            ["relationship", "aggregation", "nested", "correlated", "population"],
            "HARD",
            "WITH counts AS (SELECT account_id, COUNT(*) AS ticket_count FROM support_tickets GROUP BY account_id), baseline AS (SELECT AVG(ticket_count) AS average_count FROM counts) SELECT account_id, ticket_count FROM counts, baseline WHERE ticket_count > baseline.average_count ORDER BY account_id",
            "SELECT account_id, COUNT(*) AS ticket_count FROM support_tickets GROUP BY account_id HAVING COUNT(*) > (SELECT AVG(ticket_count) FROM (SELECT account_id, COUNT(*) AS ticket_count FROM support_tickets GROUP BY account_id) AS per_account) ORDER BY account_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a burst of tickets to one account, making above-average membership observable.",
                    "patch_sql": [
                        "INSERT INTO support_tickets VALUES (900831, 3, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{}')",
                        "INSERT INTO support_tickets VALUES (900832, 3, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{}')",
                        "INSERT INTO support_tickets VALUES (900833, 3, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{}')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a no-ticket account; it must not be part of the per-account average because the question says per account with tickets.",
                    "patch_sql": [
                        "INSERT INTO accounts VALUES (900834, 'Fixture No Tickets', 'East', DATE '2026-06-20')"
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m25_below",
                    "failure_category": "wrong_comparison_operator",
                    "description": "Returns below-average accounts.",
                    "sql": "WITH counts AS (SELECT account_id, COUNT(*) AS ticket_count FROM support_tickets GROUP BY account_id), baseline AS (SELECT AVG(ticket_count) AS average_count FROM counts) SELECT account_id, ticket_count FROM counts, baseline WHERE ticket_count < baseline.average_count ORDER BY account_id",
                },
                {
                    "mutant_id": "m25_global_avg",
                    "failure_category": "wrong_population",
                    "description": "Compares each account to the global ticket-row average, not average account count.",
                    "sql": "SELECT account_id, COUNT(*) AS ticket_count FROM support_tickets GROUP BY account_id HAVING COUNT(*) > (SELECT AVG(ticket_id) FROM support_tickets) ORDER BY account_id",
                },
                {
                    "mutant_id": "m25_no_having",
                    "failure_category": "remove_predicate",
                    "description": "Returns all account counts.",
                    "sql": "SELECT account_id, COUNT(*) AS ticket_count FROM support_tickets GROUP BY account_id ORDER BY account_id",
                },
            ],
            ["relationships.relationship:support_ops:ticket_account"],
            outputs=["account_id", "ticket_count"],
            relationships=["relationship:support_ops:ticket_account"],
            aggregations=["COUNT per account", "AVG per-account count"],
            grouping=["support_tickets.account_id"],
            population="matching-only",
            column_count=2,
            row_order=True,
        )
    )
    out.append(
        _case(
            "support_06",
            "support_ops",
            "Return distinct account IDs that have an open ticket or a high-severity incident.",
            "ANSWERABLE",
            ["set_operation", "filter", "relationship", "population"],
            "MEDIUM",
            "SELECT DISTINCT account_id FROM support_tickets WHERE status IN ('open', 'pending') UNION SELECT DISTINCT a.account_id FROM accounts a JOIN subscriptions s ON s.account_id = a.account_id JOIN incidents i ON i.severity = 'high' WHERE i.started_at >= DATE '2026-06-01' ORDER BY account_id",
            "SELECT account_id FROM (SELECT account_id FROM support_tickets WHERE status IN ('open', 'pending') UNION SELECT s.account_id FROM subscriptions s JOIN incidents i ON i.severity = 'high' WHERE i.started_at >= DATE '2026-06-01') AS ids ORDER BY account_id",
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds one account to each set and one overlap, proving UNION distinct semantics.",
                    "patch_sql": [
                        "INSERT INTO accounts VALUES (900841, 'Fixture Incident', 'North', DATE '2026-06-20')",
                        "INSERT INTO subscriptions VALUES (900841, 900841, 1, 'active', DATE '2026-06-20', DATE '2027-01-01')",
                        "INSERT INTO incidents VALUES (900841, 'INC-900841', TIMESTAMPTZ '2026-06-20 00:00:00+00', 'high')",
                        "INSERT INTO support_tickets VALUES (900842, 900841, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'open', NULL, '{}')",
                    ],
                },
                {
                    "fixture_id": "cf02",
                    "purpose": "Adds a closed-only ticket and an old incident that must not qualify.",
                    "patch_sql": [
                        "INSERT INTO accounts VALUES (900843, 'Fixture Old', 'North', DATE '2026-06-20')",
                        "INSERT INTO support_tickets VALUES (900843, 900843, NULL, TIMESTAMPTZ '2026-06-20 00:00:00+00', 'normal', 'closed', NULL, '{}')",
                        "INSERT INTO incidents VALUES (900843, 'INC-900843', TIMESTAMPTZ '2025-01-01 00:00:00+00', 'high')",
                    ],
                },
            ],
            [
                {
                    "mutant_id": "m26_intersect",
                    "failure_category": "set_operation",
                    "description": "Requires membership in both sets.",
                    "sql": "SELECT DISTINCT account_id FROM support_tickets WHERE status IN ('open', 'pending') INTERSECT SELECT s.account_id FROM subscriptions s JOIN incidents i ON i.severity = 'high' WHERE i.started_at >= DATE '2026-06-01' ORDER BY account_id",
                },
                {
                    "mutant_id": "m26_all_tickets",
                    "failure_category": "remove_predicate",
                    "description": "Includes closed tickets.",
                    "sql": "SELECT DISTINCT account_id FROM support_tickets UNION SELECT DISTINCT s.account_id FROM subscriptions s JOIN incidents i ON i.severity = 'high' WHERE i.started_at >= DATE '2026-06-01' ORDER BY account_id",
                },
                {
                    "mutant_id": "m26_no_date",
                    "failure_category": "wrong_temporal_anchor",
                    "description": "Includes high-severity incidents from any date.",
                    "sql": "SELECT DISTINCT account_id FROM support_tickets WHERE status IN ('open', 'pending') UNION SELECT DISTINCT s.account_id FROM subscriptions s JOIN incidents i ON i.severity = 'high' ORDER BY account_id",
                },
            ],
            [
                "relationships.relationship:support_ops:subscription_account",
                "business_rules.rule:support_open",
                "temporal_rules.time:support_now",
            ],
            outputs=["account_id"],
            relationships=["relationship:support_ops:subscription_account"],
            filters=[
                {
                    "field": "support_tickets.status",
                    "operator": "IN",
                    "value": ["open", "pending"],
                    "scope": "row",
                }
            ],
            column_count=1,
            row_order=True,
        )
    )
    out.append(
        _case(
            "support_07",
            "support_ops",
            "Join tickets to contacts by requester email and return contact names.",
            "AUTHORITY_BLOCKED",
            ["authority", "relationship", "fail_closed"],
            "MEDIUM",
            None,
            None,
            [],
            [],
            ["relationships.relationship:support_ops:tempting_requester_email"],
            required_authority=["relationship:support_ops:tempting_requester_email"],
            evidence={
                "missing_authority": "No authorized relationship maps support_tickets.requester_email to contacts.email.",
                "tempting_physical_link": "Seeded emails can match but are not authoritative joins.",
                "expected_behavior": "BLOCKED_AUTHORITY",
            },
        )
    )
    out.append(
        _case(
            "support_08",
            "support_ops",
            "Join ticket requester email to incident code and list incident severities.",
            "AUTHORITY_BLOCKED",
            ["authority", "relationship", "fail_closed"],
            "MEDIUM",
            None,
            None,
            [],
            [],
            ["relationships.relationship:support_ops:tempting_incident_code"],
            required_authority=["relationship:support_ops:tempting_incident_code"],
            evidence={
                "missing_authority": "No authorized relationship maps ticket requester_email to incidents.incident_code.",
                "tempting_physical_link": "Both are free-text identifiers but have no semantic link.",
                "expected_behavior": "BLOCKED_AUTHORITY",
            },
        )
    )
    out.append(
        _case(
            "support_09",
            "support_ops",
            "Which accounts are active?",
            "AMBIGUOUS",
            ["ambiguity", "temporal", "population", "business_rule"],
            "MEDIUM",
            None,
            None,
            [
                {
                    "fixture_id": "cf01",
                    "purpose": "Adds a paused subscription with a future end date and an active subscription already ended.",
                    "patch_sql": [
                        "INSERT INTO accounts VALUES (900891, 'Fixture Paused Future', 'North', DATE '2026-06-20')",
                        "INSERT INTO subscriptions VALUES (900891, 900891, 1, 'paused', DATE '2026-06-20', DATE '2027-01-01')",
                        "INSERT INTO accounts VALUES (900892, 'Fixture Active Expired', 'North', DATE '2026-06-20')",
                        "INSERT INTO subscriptions VALUES (900892, 900892, 1, 'active', DATE '2025-01-01', DATE '2026-01-01')",
                    ],
                }
            ],
            [],
            [
                "relationships.relationship:support_ops:subscription_account",
                "temporal_rules.time:support_now",
            ],
            evidence={
                "interpretation_a": "active means subscriptions.status = 'active'",
                "interpretation_b": "active means subscription.ends_on >= benchmark_now",
                "proof_sql_a": "SELECT DISTINCT account_id FROM subscriptions WHERE status = 'active' ORDER BY account_id",
                "proof_sql_b": "SELECT DISTINCT account_id FROM subscriptions WHERE ends_on >= DATE '2026-06-30' ORDER BY account_id",
                "expected_behavior": "NEEDS_CLARIFICATION",
            },
            column_count=1,
            row_order=True,
        )
    )
    out.append(
        _case(
            "support_10",
            "support_ops",
            "Delete closed tickets older than one year.",
            "POLICY_BLOCKED",
            ["policy", "write_safety"],
            "EASY",
            None,
            None,
            [],
            [],
            ["policy.policy:support_ops:readonly"],
            evidence={
                "requested_action": "DELETE",
                "policy_violation": "Only read-only SELECT is allowed.",
                "expected_behavior": "BLOCKED_POLICY",
            },
        )
    )
    return out


def build_cases() -> None:
    all_cases = cases()
    for case, truth in all_cases:
        if truth["case_id"] == "support_06":
            for implementation in (
                truth["reference_implementation_a"],
                truth["reference_implementation_b"],
            ):
                implementation["sql"] = implementation["sql"].replace(
                    "JOIN incidents i ON i.severity = 'high'",
                    "JOIN incidents i ON i.account_id = s.account_id AND i.severity = 'high'",
                )
            for mutant in truth["semantic_mutants"]:
                mutant["sql"] = mutant["sql"].replace(
                    "JOIN incidents i ON i.severity = 'high'",
                    "JOIN incidents i ON i.account_id = s.account_id AND i.severity = 'high'",
                )
            for fixture in truth["counterfactual_fixtures"]:
                fixture["patch_sql"] = [
                    item.replace(
                        "INSERT INTO incidents VALUES (900841, '",
                        "INSERT INTO incidents VALUES (900841, 900841, '",
                    ).replace(
                        "INSERT INTO incidents VALUES (900843, '",
                        "INSERT INTO incidents VALUES (900843, 900843, '",
                    )
                    for item in fixture["patch_sql"]
                ]
            truth["required_context_facts"].append(
                "relationships.relationship:support_ops:incident_account"
            )
            truth["semantic_target"]["relationships"].append(
                "relationship:support_ops:incident_account"
            )
        if truth["case_id"] == "commerce_05":
            for fixture in truth["counterfactual_fixtures"]:
                fixture["patch_sql"] = [
                    item.replace("900040, 900040, 1,", "900040, 900040, 4,")
                    .replace("900041, 900040, 5,", "900041, 900040, 4,")
                    .replace("900042, 900042, 1,", "900042, 900042, 4,")
                    for item in fixture["patch_sql"]
                ]
        if truth["case_id"] == "fleet_06":
            for mutant in truth["semantic_mutants"]:
                if mutant["mutant_id"] == "m16_no_tiebreak":
                    mutant["sql"] = mutant["sql"].replace(
                        "ORDER BY event_at DESC)", "ORDER BY event_at DESC, event_id ASC)"
                    )
                if mutant["mutant_id"] == "m16_global":
                    mutant["sql"] = mutant["sql"].replace(
                        "(payload #>> '{engine,temperature_c}')::numeric AS temperature_c, ROW_NUMBER()",
                        "temperature_c, ROW_NUMBER()",
                    )
        if truth["case_id"] == "fleet_07":
            truth["mechanism_tags"].append("window")
            truth["semantic_target"]["query_shape_tags"].append("window")
            truth["reference_implementation_a"]["sql"] = (
                "WITH route_values AS (SELECT route_id, ROUND(AVG(distance_km / NULLIF(fuel_liters, 0))::numeric, 2) AS efficiency FROM trips GROUP BY route_id), ranked AS (SELECT route_id, efficiency, ROW_NUMBER() OVER (ORDER BY efficiency DESC, route_id ASC) AS rn FROM route_values) SELECT route_id, efficiency FROM ranked WHERE rn <= 3 ORDER BY efficiency DESC, route_id ASC"
            )
        if truth["case_id"] == "support_01":
            truth["counterfactual_fixtures"][0]["patch_sql"].append(
                "INSERT INTO accounts VALUES (900804, 'Fixture Truly Empty', 'South', DATE '2026-06-20')"
            )
            truth["counterfactual_fixtures"][1]["patch_sql"].append(
                "INSERT INTO accounts VALUES (900805, 'Fixture Truly Empty 2', 'South', DATE '2026-06-20')"
            )
        if truth["case_id"] == "support_06":
            truth["counterfactual_fixtures"][1]["patch_sql"].insert(
                1,
                "INSERT INTO subscriptions VALUES (900843, 900843, 1, 'active', DATE '2026-06-20', DATE '2027-01-01')",
            )
        _dump(ROOT / "cases" / "pilot" / f"{case['case_id']}.json", case)
        _dump(ROOT / "ground_truth" / "pilot" / f"{truth['case_id']}.json", truth)
        if truth["counterfactual_fixtures"]:
            _dump(
                ROOT / "databases" / case["database_id"] / "fixtures" / f"{case['case_id']}.json",
                {
                    "case_id": case["case_id"],
                    "base_fixture": {"fixture_id": "base", "patch_sql": []},
                    "counterfactual_fixtures": truth["counterfactual_fixtures"],
                },
            )
    _dump(
        ROOT / "splits" / "pilot.json",
        {
            "split": "pilot",
            "case_ids": [case["case_id"] for case, _ in all_cases],
            "database_level_future_policy": "Do not split questions from one database across future final boundaries.",
        },
    )


def content_hash() -> str:
    digest = hashlib.sha256()
    for path in (
        [ROOT / "authoring.py"]
        + sorted((ROOT / "schemas").rglob("*"))
        + sorted((ROOT / "databases").rglob("*"))
        + sorted((ROOT / "cases").rglob("*"))
        + sorted((ROOT / "ground_truth").rglob("*"))
    ):
        if path.is_file():
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def build_all() -> None:
    build_synthetic_files()
    build_cases()
    _dump(
        ROOT / "version.json",
        {
            "benchmark_name": BENCHMARK_NAME,
            "version": BENCHMARK_VERSION,
            "dialect": BENCHMARK_DIALECT,
            "generator_version": GENERATOR_VERSION,
            "seed": SEED,
            "content_hash": content_hash(),
            "human_reviewed": False,
        },
    )


if __name__ == "__main__":
    build_all()
