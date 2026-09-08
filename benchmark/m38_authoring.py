"""Deterministic authoring source for the v0.2.0 development benchmark.

This module deliberately does not import the application runtime or any model
provider.  It owns the three new synthetic database packs and the 60 new case
contracts.  The historical pilot remains under ``benchmark/authoring.py`` and
is loaded separately by the M38 validator.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# ruff: noqa: E501

ROOT = Path(__file__).resolve().parent
M38_VERSION = "0.2.0-dev"
M38_GENERATOR_VERSION = "m38-authoring-v1"
M38_DATABASES = {
    "subscription_billing": "m38_subscription_billing",
    "warehouse_logistics": "m38_warehouse_logistics",
    "risk_operations": "m38_risk_operations",
}
NEW_CASE_COUNTS = {
    "subscription_billing": {
        "ANSWERABLE": 14,
        "AUTHORITY_BLOCKED": 3,
        "AMBIGUOUS": 2,
        "POLICY_BLOCKED": 1,
    },
    "warehouse_logistics": {
        "ANSWERABLE": 13,
        "AUTHORITY_BLOCKED": 4,
        "AMBIGUOUS": 2,
        "POLICY_BLOCKED": 1,
    },
    "risk_operations": {
        "ANSWERABLE": 13,
        "AUTHORITY_BLOCKED": 3,
        "AMBIGUOUS": 2,
        "POLICY_BLOCKED": 2,
    },
}


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _entity(
    database_id: str, table: str, human: str, description: str, role: str
) -> dict[str, Any]:
    return {
        "entity_id": f"entity:{database_id}:{table}",
        "human_name": human,
        "physical_table": table,
        "description": description,
        "primary_semantic_role": role,
    }


def _attribute(
    database_id: str,
    table: str,
    name: str,
    data_type: str,
    description: str,
    nullable: bool = False,
) -> dict[str, Any]:
    return {
        "attribute_id": f"attribute:{database_id}:{table}:{name}",
        "entity_id": f"entity:{database_id}:{table}",
        "physical_column_or_path": name,
        "data_type": data_type,
        "semantic_description": description,
        "nullable": nullable,
    }


def _relationship(
    database_id: str,
    name: str,
    left: str,
    left_attr: str,
    right: str,
    right_attr: str,
    cardinality: str = "many_to_one",
    description: str = "Authorized operational relationship.",
    authorized: bool = True,
) -> dict[str, Any]:
    return {
        "relationship_id": f"relationship:{database_id}:{name}",
        "left_entity": f"entity:{database_id}:{left}",
        "left_attribute": f"attribute:{database_id}:{left}:{left_attr}",
        "right_entity": f"entity:{database_id}:{right}",
        "right_attribute": f"attribute:{database_id}:{right}:{right_attr}",
        "cardinality": cardinality,
        "direction": "left_to_right",
        "authorized": authorized,
        "description": description,
    }


def _metric(database_id: str, name: str, definition: str, formula: str) -> dict[str, Any]:
    return {
        "metric_id": f"metric:{database_id}:{name}",
        "name": name,
        "definition": definition,
        "formula": formula,
        "operands": [],
        "grain": "case-defined",
        "null_policy": "preserve NULL unless explicitly defaulted",
        "default_policy": "no implicit default",
        "precision": 4,
        "rounding_stage": "display_only unless question says otherwise",
        "temporal_basis": "case-defined",
        "description": definition,
    }


def _base_authority(
    database_id: str,
    tables: list[tuple[str, str, str, str]],
    attrs: list[tuple[str, str, str, str, bool]],
    relationships: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    temporal: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "database_id": database_id,
        "schema_name": M38_DATABASES[database_id],
        "entities": [_entity(database_id, *item) for item in tables],
        "attributes": [_attribute(database_id, *item) for item in attrs],
        "relationships": relationships,
        "metrics": metrics,
        "business_rules": rules,
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


SUBSCRIPTION_SCHEMA = """CREATE SCHEMA IF NOT EXISTS m38_subscription_billing;
SET search_path TO m38_subscription_billing;
CREATE TABLE accounts (account_id INTEGER PRIMARY KEY, account_name TEXT NOT NULL, segment TEXT NOT NULL, created_on DATE NOT NULL);
CREATE TABLE plans (plan_id INTEGER PRIMARY KEY, plan_name TEXT NOT NULL, monthly_price NUMERIC(10,2) NOT NULL, included_units INTEGER NOT NULL);
CREATE TABLE subscriptions (subscription_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), plan_id INTEGER NOT NULL REFERENCES plans(plan_id), status TEXT NOT NULL, starts_on DATE NOT NULL, ends_on DATE);
CREATE TABLE invoices (invoice_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), subscription_id INTEGER NOT NULL REFERENCES subscriptions(subscription_id), issued_on DATE NOT NULL, due_on DATE NOT NULL, status TEXT NOT NULL, total_due NUMERIC(10,2) NOT NULL);
CREATE TABLE invoice_lines (line_id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(invoice_id), description TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price NUMERIC(10,2) NOT NULL);
CREATE TABLE payments (payment_id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(invoice_id), paid_at TIMESTAMPTZ, amount NUMERIC(10,2) NOT NULL, status TEXT NOT NULL);
CREATE TABLE refunds (refund_id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(payment_id), refunded_at TIMESTAMPTZ NOT NULL, amount NUMERIC(10,2) NOT NULL);
CREATE TABLE credits (credit_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), issued_on DATE NOT NULL, amount NUMERIC(10,2) NOT NULL, reason TEXT NOT NULL);
CREATE TABLE usage_events (usage_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), occurred_at TIMESTAMPTZ NOT NULL, units INTEGER NOT NULL, payload JSONB NOT NULL);
CREATE TABLE entitlements (entitlement_id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL REFERENCES subscriptions(subscription_id), feature_code TEXT NOT NULL, enabled BOOLEAN NOT NULL);
CREATE TABLE plan_changes (change_id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL REFERENCES subscriptions(subscription_id), changed_at TIMESTAMPTZ NOT NULL, old_plan_id INTEGER, new_plan_id INTEGER NOT NULL);
CREATE TABLE dunning_attempts (attempt_id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(invoice_id), attempted_at TIMESTAMPTZ NOT NULL, outcome TEXT NOT NULL);
"""

SUBSCRIPTION_SEED = """INSERT INTO accounts VALUES
(1,'Acme Studio','SMB','2025-01-05'),(2,'Beacon Labs','MID','2025-02-10'),(3,'Cedar Health','ENT','2025-03-15'),(4,'Delta Works','SMB','2025-04-20'),(5,'Elm Media','MID','2025-05-25'),(6,'Fjord Retail','ENT','2025-06-30');
INSERT INTO plans VALUES (1,'Starter',29.00,100),(2,'Growth',99.00,1000),(3,'Scale',299.00,10000);
INSERT INTO subscriptions VALUES
(101,1,1,'active','2026-01-01',NULL),(102,1,2,'paused','2025-01-01','2026-05-01'),
(103,2,2,'active','2026-02-01',NULL),(104,3,3,'active','2025-11-01',NULL),
(105,4,1,'cancelled','2025-01-01','2026-03-01'),(106,5,2,'active','2026-03-01',NULL),(107,6,3,'active','2026-04-01',NULL);
INSERT INTO invoices VALUES
(201,1,101,'2026-05-01','2026-05-15','paid',29.00),(202,1,101,'2026-06-01','2026-06-15','open',29.00),
(203,2,103,'2026-05-01','2026-05-15','paid',99.00),(204,3,104,'2026-05-01','2026-05-15','paid',299.00),
(205,4,105,'2026-02-01','2026-02-15','void',29.00),(206,5,106,'2026-05-01','2026-05-15','open',99.00),
(207,6,107,'2026-05-01','2026-05-15','paid',299.00),(208,2,103,'2026-06-01','2026-06-15','open',99.00),(209,3,104,'2026-06-01','2026-06-15','open',299.00),(210,4,105,'2026-06-01','2026-06-15','open',29.00);
INSERT INTO invoice_lines VALUES
(301,201,'base',1,29),(302,202,'base',1,29),(303,203,'base',1,99),(304,204,'base',1,299),(305,206,'base',1,99),(306,207,'base',1,299),(307,208,'base',1,99),(308,209,'base',1,299),(309,210,'base',1,29);
INSERT INTO payments VALUES
(401,201,'2026-05-04 10:00+00',29,'captured'),(402,203,'2026-05-04 11:00+00',99,'captured'),(403,204,'2026-05-04 12:00+00',299,'captured'),(404,207,'2026-05-04 13:00+00',299,'captured'),(405,202,NULL,0,'failed'),(406,206,NULL,0,'failed');
INSERT INTO refunds VALUES (501,401,'2026-05-20 10:00+00',5),(502,404,'2026-05-21 10:00+00',50);
INSERT INTO credits VALUES (601,1,'2026-05-10',10,'service'),(602,3,'2026-05-12',25,'migration'),(603,5,'2026-05-14',15,'service');
INSERT INTO usage_events VALUES
(701,1,'2026-06-01 10:00+00',120,'{"source":"api","units":"120"}'),(702,1,'2026-06-02 10:00+00',40,'{"source":"api","units":"40"}'),
(703,2,'2026-06-01 10:00+00',900,'{"source":"api","units":"900"}'),(704,3,'2026-06-01 10:00+00',11000,'{"source":"api","units":"11000"}'),
(705,5,'2026-06-01 10:00+00',1500,'{"source":"api","units":"1500"}'),(706,6,'2026-06-01 10:00+00',8000,'{"source":"api","units":"8000"}');
INSERT INTO entitlements VALUES (801,101,'exports',true),(802,103,'sso',true),(803,104,'audit',true),(804,106,'exports',false),(805,107,'audit',true);
INSERT INTO plan_changes VALUES
(901,101,'2025-12-01 10:00+00',NULL,1),(902,101,'2026-01-01 10:00+00',1,1),(903,103,'2026-02-01 10:00+00',NULL,2),(904,104,'2025-11-01 10:00+00',NULL,3),(905,106,'2026-03-01 10:00+00',NULL,2),(906,107,'2026-04-01 10:00+00',NULL,3);
INSERT INTO dunning_attempts VALUES (1001,202,'2026-06-16 09:00+00','failed'),(1002,202,'2026-06-18 09:00+00','failed'),(1003,206,'2026-06-16 09:00+00','failed');
"""


WAREHOUSE_SCHEMA = """CREATE SCHEMA IF NOT EXISTS m38_warehouse_logistics;
SET search_path TO m38_warehouse_logistics;
CREATE TABLE warehouses (warehouse_id INTEGER PRIMARY KEY, warehouse_name TEXT NOT NULL, region TEXT NOT NULL, capacity_units INTEGER NOT NULL);
CREATE TABLE bins (bin_id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id), bin_code TEXT NOT NULL, capacity_units INTEGER NOT NULL);
CREATE TABLE products (product_id INTEGER PRIMARY KEY, sku TEXT NOT NULL, product_name TEXT NOT NULL, unit_cost NUMERIC(10,2) NOT NULL);
CREATE TABLE inventory_snapshots (snapshot_id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id), product_id INTEGER NOT NULL REFERENCES products(product_id), snapshot_at TIMESTAMPTZ NOT NULL, on_hand_qty INTEGER NOT NULL);
CREATE TABLE purchase_orders (po_id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id), ordered_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL, supplier_code TEXT NOT NULL);
CREATE TABLE purchase_order_lines (po_line_id INTEGER PRIMARY KEY, po_id INTEGER NOT NULL REFERENCES purchase_orders(po_id), product_id INTEGER NOT NULL REFERENCES products(product_id), ordered_qty INTEGER NOT NULL);
CREATE TABLE receipts (receipt_id INTEGER PRIMARY KEY, po_line_id INTEGER NOT NULL REFERENCES purchase_order_lines(po_line_id), received_at TIMESTAMPTZ NOT NULL, received_qty INTEGER NOT NULL);
CREATE TABLE carriers (carrier_id INTEGER PRIMARY KEY, carrier_name TEXT NOT NULL, service_level TEXT NOT NULL);
CREATE TABLE shipments (shipment_id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id), carrier_id INTEGER NOT NULL REFERENCES carriers(carrier_id), shipped_at TIMESTAMPTZ NOT NULL, promised_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL);
CREATE TABLE shipment_items (shipment_item_id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL REFERENCES shipments(shipment_id), product_id INTEGER NOT NULL REFERENCES products(product_id), quantity INTEGER NOT NULL);
CREATE TABLE pick_events (pick_id INTEGER PRIMARY KEY, shipment_item_id INTEGER NOT NULL REFERENCES shipment_items(shipment_item_id), picked_at TIMESTAMPTZ NOT NULL, picker_id INTEGER NOT NULL);
CREATE TABLE stock_movements (movement_id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id), product_id INTEGER NOT NULL REFERENCES products(product_id), moved_at TIMESTAMPTZ NOT NULL, quantity INTEGER NOT NULL, movement_type TEXT NOT NULL);
CREATE TABLE delivery_events (delivery_id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL REFERENCES shipments(shipment_id), event_at TIMESTAMPTZ NOT NULL, event_type TEXT NOT NULL, payload JSONB NOT NULL);
"""

WAREHOUSE_SEED = """INSERT INTO warehouses VALUES (1,'North Hub','North',1000),(2,'South Hub','South',800),(3,'East Hub','East',1200);
INSERT INTO bins VALUES (11,1,'N-01',400),(12,1,'N-02',300),(21,2,'S-01',300),(31,3,'E-01',500);
INSERT INTO products VALUES (1,'SKU-1','Widget A',10.00),(2,'SKU-2','Widget B',25.00),(3,'SKU-3','Widget C',40.00),(4,'SKU-4','Widget D',75.00);
INSERT INTO inventory_snapshots VALUES
(101,1,1,'2026-06-01 08:00+00',120),(102,1,1,'2026-06-02 08:00+00',90),(103,1,2,'2026-06-02 08:00+00',40),(104,2,1,'2026-06-02 08:00+00',15),(105,2,3,'2026-06-02 08:00+00',0),(106,3,4,'2026-06-02 08:00+00',60),(107,3,2,'2026-06-02 08:00+00',25);
INSERT INTO purchase_orders VALUES (201,1,'2026-05-01 09:00+00','open','SUP-A'),(202,2,'2026-05-02 09:00+00','closed','SUP-B'),(203,3,'2026-05-03 09:00+00','open','SUP-A');
INSERT INTO purchase_order_lines VALUES (301,201,1,100),(302,201,2,50),(303,202,3,40),(304,203,4,80);
INSERT INTO receipts VALUES (401,301,'2026-05-04 09:00+00',80),(402,301,'2026-05-05 09:00+00',20),(403,302,'2026-05-06 09:00+00',50),(404,303,'2026-05-06 09:00+00',40),(405,304,'2026-05-07 09:00+00',20);
INSERT INTO carriers VALUES (1,'FastShip','express'),(2,'RoadRunner','standard'),(3,'BlueParcel','economy');
INSERT INTO shipments VALUES
(501,1,1,'2026-06-01 08:00+00','2026-06-02 08:00+00','delivered'),(502,1,2,'2026-06-01 09:00+00','2026-06-01 18:00+00','delivered'),(503,2,2,'2026-06-02 09:00+00','2026-06-03 09:00+00','in_transit'),(504,3,3,'2026-06-02 10:00+00','2026-06-03 10:00+00','delivered');
INSERT INTO shipment_items VALUES (601,501,1,4),(602,501,2,2),(603,502,3,3),(604,503,1,5),(605,504,4,2);
INSERT INTO pick_events VALUES (701,601,'2026-06-01 10:00+00',10),(702,602,'2026-06-01 11:00+00',11),(703,603,'2026-06-02 12:00+00',12),(704,604,'2026-06-02 14:00+00',13),(705,605,'2026-06-02 11:00+00',14);
INSERT INTO stock_movements VALUES (801,1,1,'2026-05-20 08:00+00',100,'receipt'),(802,1,1,'2026-05-25 08:00+00',-20,'shipment'),(803,2,3,'2026-05-25 08:00+00',40,'receipt'),(804,3,4,'2026-05-25 08:00+00',80,'receipt');
INSERT INTO delivery_events VALUES (901,501,'2026-06-02 07:00+00','delivered','{"temperature_c":"5"}'),(902,502,'2026-06-02 00:00+00','delivered','{"temperature_c":"8"}'),(903,503,'2026-06-03 12:00+00','delay','{"reason":"weather"}'),(904,504,'2026-06-03 09:00+00','delivered','{"temperature_c":"6"}');
"""


RISK_SCHEMA = """CREATE SCHEMA IF NOT EXISTS m38_risk_operations;
SET search_path TO m38_risk_operations;
CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, segment TEXT NOT NULL);
CREATE TABLE accounts (account_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), opened_on DATE NOT NULL, status TEXT NOT NULL);
CREATE TABLE transactions (transaction_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), transacted_at TIMESTAMPTZ NOT NULL, amount NUMERIC(12,2) NOT NULL, channel TEXT NOT NULL, payload JSONB NOT NULL);
CREATE TABLE risk_assessments (assessment_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), assessed_at TIMESTAMPTZ NOT NULL, risk_score NUMERIC(6,2) NOT NULL, band TEXT NOT NULL);
CREATE TABLE alerts (alert_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), created_at TIMESTAMPTZ NOT NULL, alert_type TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE alert_events (alert_event_id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL REFERENCES alerts(alert_id), event_at TIMESTAMPTZ NOT NULL, event_type TEXT NOT NULL);
CREATE TABLE investigations (investigation_id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL REFERENCES alerts(alert_id), opened_at TIMESTAMPTZ NOT NULL, closed_at TIMESTAMPTZ, outcome TEXT);
CREATE TABLE cases (case_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), opened_at TIMESTAMPTZ NOT NULL, resolved_at TIMESTAMPTZ, outcome TEXT);
CREATE TABLE analyst_actions (action_id INTEGER PRIMARY KEY, investigation_id INTEGER NOT NULL REFERENCES investigations(investigation_id), analyst_id INTEGER NOT NULL, action_at TIMESTAMPTZ NOT NULL, action_type TEXT NOT NULL);
CREATE TABLE devices (device_id INTEGER PRIMARY KEY, device_fingerprint TEXT NOT NULL, first_seen_at TIMESTAMPTZ NOT NULL);
CREATE TABLE login_events (login_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(account_id), device_id INTEGER NOT NULL REFERENCES devices(device_id), login_at TIMESTAMPTZ NOT NULL, result TEXT NOT NULL);
CREATE TABLE watchlist_matches (match_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(customer_id), matched_at TIMESTAMPTZ NOT NULL, list_name TEXT NOT NULL, cleared_at TIMESTAMPTZ);
"""

RISK_SEED = """INSERT INTO customers VALUES (1,'Arbor Co','SMB'),(2,'Brio Labs','MID'),(3,'Cobalt Health','ENT'),(4,'Dune Market','SMB'),(5,'Ember Media','MID'),(6,'Fable Works','ENT');
INSERT INTO accounts VALUES (101,1,'2025-01-01','open'),(102,2,'2025-01-02','open'),(103,3,'2025-01-03','open'),(104,4,'2025-01-04','open'),(105,5,'2025-01-05','closed'),(106,6,'2025-01-06','open');
INSERT INTO transactions VALUES
(201,101,'2026-06-01 09:00+00',1200,'card','{"risk_score":"82","channel":"web"}'),(202,101,'2026-06-02 09:00+00',50,'card','{"risk_score":"20","channel":"web"}'),
(203,102,'2026-06-01 10:00+00',800,'wire','{"risk_score":"72","channel":"api"}'),(204,103,'2026-06-01 11:00+00',4000,'wire','{"risk_score":"91","channel":"api"}'),
(205,104,'2026-06-03 12:00+00',75,'card','{"risk_score":"15","channel":"web"}'),(206,106,'2026-06-03 13:00+00',2200,'card','{"risk_score":"65","channel":"web"}');
INSERT INTO risk_assessments VALUES
(301,1,'2026-05-01 09:00+00',60,'medium'),(302,1,'2026-06-01 09:00+00',82,'high'),(303,2,'2026-06-01 09:00+00',72,'high'),(304,3,'2026-06-01 09:00+00',91,'critical'),(305,4,'2026-06-01 09:00+00',15,'low'),(306,6,'2026-06-01 09:00+00',65,'medium');
INSERT INTO alerts VALUES
(401,1,'2026-06-01 10:00+00','velocity','high','open'),(402,1,'2026-06-02 10:00+00','device','medium','closed'),(403,2,'2026-06-01 11:00+00','velocity','high','open'),(404,3,'2026-06-01 12:00+00','sanctions','critical','open'),(405,4,'2026-06-03 13:00+00','velocity','low','closed'),(406,6,'2026-06-03 14:00+00','device','medium','open');
INSERT INTO alert_events VALUES (501,401,'2026-06-01 10:05+00','created'),(502,401,'2026-06-02 10:05+00','reviewed'),(503,402,'2026-06-02 10:05+00','closed'),(504,403,'2026-06-01 11:05+00','created'),(505,404,'2026-06-01 12:05+00','created'),(506,406,'2026-06-03 14:05+00','created');
INSERT INTO investigations VALUES (601,401,'2026-06-01 11:00+00','2026-06-02 11:00+00','confirmed'),(602,402,'2026-06-02 11:00+00','2026-06-03 11:00+00','dismissed'),(603,404,'2026-06-01 13:00+00',NULL,NULL),(604,406,'2026-06-03 15:00+00',NULL,NULL);
INSERT INTO cases VALUES (701,1,'2026-06-01 11:00+00','2026-06-03 11:00+00','confirmed'),(702,3,'2026-06-01 13:00+00',NULL,NULL),(703,2,'2026-06-01 12:00+00','2026-06-02 12:00+00','dismissed');
INSERT INTO analyst_actions VALUES (801,601,10,'2026-06-01 12:00+00','review'),(802,601,10,'2026-06-02 09:00+00','escalate'),(803,602,11,'2026-06-02 13:00+00','review'),(804,603,12,'2026-06-01 14:00+00','review'),(805,604,13,'2026-06-03 16:00+00','review');
INSERT INTO devices VALUES (901,'device-a','2025-01-01 00:00+00'),(902,'device-b','2025-01-02 00:00+00'),(903,'device-c','2025-01-03 00:00+00');
INSERT INTO login_events VALUES (1001,101,901,'2026-06-01 08:00+00','success'),(1002,102,902,'2026-06-01 08:30+00','success'),(1003,103,901,'2026-06-01 08:45+00','failed'),(1004,104,903,'2026-06-03 11:00+00','success'),(1005,106,901,'2026-06-03 12:00+00','success'),(1006,106,902,'2026-06-03 12:30+00','success');
INSERT INTO watchlist_matches VALUES (1101,3,'2026-06-01 12:30+00','sanctions',NULL),(1102,2,'2026-06-01 12:30+00','pep','2026-06-02 12:30+00'),(1103,1,'2026-06-01 12:30+00','adverse_media','2026-06-02 12:30+00');
"""


DB_DEFINITIONS = {
    "subscription_billing": (SUBSCRIPTION_SCHEMA, SUBSCRIPTION_SEED),
    "warehouse_logistics": (WAREHOUSE_SCHEMA, WAREHOUSE_SEED),
    "risk_operations": (RISK_SCHEMA, RISK_SEED),
}

DOMAIN_DOCS = {
    "subscription_billing": """# Subscription billing\n\nSynthetic SaaS billing operations with accounts, plans, subscriptions, invoices, payments, refunds, credits, usage, entitlements, plan changes, and dunning.\n\nAuthorized relationships are declared in `authority/relationships.json`; matching codes and usage-to-plan joins are deliberately not authorized. Active subscriptions use the visible status rule. The benchmark clock is fixed at 2026-06-30 UTC. Monetary measures use PostgreSQL NUMERIC and preserve NULL unless a question declares a default.\n\nCases cover projection, MRR, invoice populations, payment/refund calculation, current plan, JSON usage, latest dunning, correlated credit exclusion, authority traps, ambiguity, and read-only policy.\n""",
    "warehouse_logistics": """# Warehouse logistics\n\nSynthetic fulfillment operations with warehouses, bins, products, inventory snapshots, inbound purchase orders and receipts, carriers, shipments, picks, stock movements, and delivery events.\n\nWarehouse, product, shipment, carrier, and event paths are explicitly authorized in the authority package. Bin identifiers are not product identifiers. Latest inventory uses snapshot timestamp and snapshot ID as the tie-break. A delivery after promised time is late; equality is on time.\n\nCases cover stock populations, inbound and outbound quantities, latest snapshots, late delivery, pick delay, JSON temperature, distinct shipment counts, ratios, authority traps, ambiguity, and read-only policy.\n""",
    "risk_operations": """# Risk operations\n\nSynthetic risk-monitoring operations with customers, accounts, transactions, risk assessments, alerts, alert events, investigations, cases, analyst actions, devices, logins, and watchlist matches.\n\nCustomer/account/transaction and alert/investigation paths are authorized; device-to-customer is deliberately not authorized. Latest risk assessment uses assessed timestamp and assessment ID as a tie-break. Stored event timestamps are UTC and the benchmark clock is fixed at 2026-06-30 UTC.\n\nCases cover open alerts, latest scores, high-risk JSON amounts, alert conversion, resolution time, device reuse, nested and correlated populations, NULL preservation, ranking, precision, authority traps, ambiguity, and policy.\n""",
}


def _authority_for(database_id: str) -> dict[str, Any]:
    if database_id == "subscription_billing":
        tables = [
            ("accounts", "Billing accounts", "A customer billing account.", "anchor entity"),
            ("plans", "Plans", "A subscription price plan.", "reference entity"),
            (
                "subscriptions",
                "Subscriptions",
                "A time-bounded account plan enrollment.",
                "lifecycle entity",
            ),
            ("invoices", "Invoices", "A billed subscription charge.", "transaction entity"),
            ("invoice_lines", "Invoice lines", "A line item on an invoice.", "transaction detail"),
            ("payments", "Payments", "A payment attempt or capture.", "transaction entity"),
            ("refunds", "Refunds", "A refund against a captured payment.", "transaction entity"),
            ("credits", "Credits", "An account credit.", "adjustment entity"),
            ("usage_events", "Usage events", "An account usage observation.", "event entity"),
            (
                "entitlements",
                "Entitlements",
                "A feature entitlement granted by a subscription.",
                "state entity",
            ),
            ("plan_changes", "Plan changes", "A subscription plan-change event.", "event entity"),
            (
                "dunning_attempts",
                "Dunning attempts",
                "A collection attempt for an invoice.",
                "event entity",
            ),
        ]
        attrs = [
            ("accounts", "account_id", "INTEGER", "Stable billing account identifier.", False),
            ("accounts", "account_name", "TEXT", "Account display name.", False),
            ("accounts", "segment", "TEXT", "Customer segment.", False),
            ("plans", "plan_id", "INTEGER", "Stable plan identifier.", False),
            ("plans", "monthly_price", "NUMERIC", "Monthly recurring price.", False),
            (
                "subscriptions",
                "subscription_id",
                "INTEGER",
                "Stable subscription identifier.",
                False,
            ),
            ("subscriptions", "account_id", "INTEGER", "Authorized account reference.", False),
            ("subscriptions", "plan_id", "INTEGER", "Authorized plan reference.", False),
            ("subscriptions", "status", "TEXT", "Lifecycle status.", False),
            ("subscriptions", "starts_on", "DATE", "Effective start date.", False),
            ("subscriptions", "ends_on", "DATE", "Effective end date.", True),
            ("invoices", "invoice_id", "INTEGER", "Stable invoice identifier.", False),
            ("invoices", "account_id", "INTEGER", "Authorized account reference.", False),
            ("invoices", "subscription_id", "INTEGER", "Authorized subscription reference.", False),
            ("invoices", "issued_on", "DATE", "Issue date.", False),
            ("invoices", "due_on", "DATE", "Due date.", False),
            ("invoices", "status", "TEXT", "Invoice state.", False),
            ("invoices", "total_due", "NUMERIC", "Invoice amount due.", False),
            ("payments", "invoice_id", "INTEGER", "Authorized invoice reference.", False),
            ("payments", "amount", "NUMERIC", "Payment amount.", False),
            ("payments", "status", "TEXT", "Payment state.", False),
            ("refunds", "payment_id", "INTEGER", "Authorized payment reference.", False),
            ("refunds", "amount", "NUMERIC", "Refund amount.", False),
            ("credits", "account_id", "INTEGER", "Authorized account reference.", False),
            ("credits", "amount", "NUMERIC", "Credit amount.", False),
            ("usage_events", "account_id", "INTEGER", "Authorized account reference.", False),
            ("usage_events", "units", "INTEGER", "Usage units.", False),
            (
                "usage_events",
                "payload",
                "JSONB",
                "Provider payload containing a textual units mirror.",
                False,
            ),
            (
                "entitlements",
                "subscription_id",
                "INTEGER",
                "Authorized subscription reference.",
                False,
            ),
            ("entitlements", "feature_code", "TEXT", "Feature identifier.", False),
            ("entitlements", "enabled", "BOOLEAN", "Whether the feature is enabled.", False),
            (
                "plan_changes",
                "subscription_id",
                "INTEGER",
                "Authorized subscription reference.",
                False,
            ),
            ("plan_changes", "changed_at", "TIMESTAMPTZ", "Effective change timestamp.", False),
            ("dunning_attempts", "invoice_id", "INTEGER", "Authorized invoice reference.", False),
        ]
        rels = [
            _relationship(
                database_id,
                "subscription_account",
                "subscriptions",
                "account_id",
                "accounts",
                "account_id",
                description="Each subscription belongs to its billing account.",
            ),
            _relationship(
                database_id,
                "subscription_plan",
                "subscriptions",
                "plan_id",
                "plans",
                "plan_id",
                description="Each subscription selects one plan.",
            ),
            _relationship(
                database_id, "invoice_account", "invoices", "account_id", "accounts", "account_id"
            ),
            _relationship(
                database_id,
                "invoice_subscription",
                "invoices",
                "subscription_id",
                "subscriptions",
                "subscription_id",
            ),
            _relationship(
                database_id,
                "invoice_line_invoice",
                "invoice_lines",
                "invoice_id",
                "invoices",
                "invoice_id",
            ),
            _relationship(
                database_id, "payment_invoice", "payments", "invoice_id", "invoices", "invoice_id"
            ),
            _relationship(
                database_id, "refund_payment", "refunds", "payment_id", "payments", "payment_id"
            ),
            _relationship(
                database_id, "credit_account", "credits", "account_id", "accounts", "account_id"
            ),
            _relationship(
                database_id, "usage_account", "usage_events", "account_id", "accounts", "account_id"
            ),
            _relationship(
                database_id,
                "entitlement_subscription",
                "entitlements",
                "subscription_id",
                "subscriptions",
                "subscription_id",
            ),
            _relationship(
                database_id,
                "change_subscription",
                "plan_changes",
                "subscription_id",
                "subscriptions",
                "subscription_id",
            ),
            _relationship(
                database_id,
                "dunning_invoice",
                "dunning_attempts",
                "invoice_id",
                "invoices",
                "invoice_id",
            ),
            _relationship(
                database_id,
                "usage_plan_trap",
                "usage_events",
                "account_id",
                "plans",
                "plan_id",
                authorized=False,
                description="No authorized usage-account to plan join exists.",
            ),
        ]
        metrics = [
            _metric(
                database_id,
                "net_collected",
                "Captured payments less refunds.",
                "SUM(captured payments) - SUM(refunds)",
            ),
            _metric(
                database_id,
                "mrr",
                "Monthly recurring revenue of active subscriptions.",
                "SUM(active plan monthly_price)",
            ),
        ]
        rules = [
            {
                "rule_id": f"rule:{database_id}:active_subscription",
                "name": "Active subscription",
                "definition": "subscriptions.status = 'active' and the row is the reported subscription state.",
            },
            {
                "rule_id": f"rule:{database_id}:billing_now",
                "name": "Billing benchmark clock",
                "definition": "The fixed benchmark date is 2026-06-30 UTC.",
            },
        ]
        temporal = [
            {
                "temporal_rule_id": f"time:{database_id}:clock",
                "clock_mode": "fixed",
                "benchmark_now": "2026-06-30T12:00:00Z",
                "timezone": "UTC",
                "bounds": "Date comparisons are inclusive unless stated otherwise.",
            }
        ]
    elif database_id == "warehouse_logistics":
        tables = [
            ("warehouses", "Warehouses", "A fulfillment facility.", "anchor entity"),
            ("bins", "Bins", "A storage bin in a warehouse.", "location entity"),
            ("products", "Products", "A stocked item.", "reference entity"),
            (
                "inventory_snapshots",
                "Inventory snapshots",
                "A timestamped stock observation.",
                "snapshot entity",
            ),
            ("purchase_orders", "Purchase orders", "An inbound order.", "transaction entity"),
            (
                "purchase_order_lines",
                "Purchase order lines",
                "An inbound item line.",
                "transaction detail",
            ),
            ("receipts", "Receipts", "An inbound receipt event.", "event entity"),
            ("carriers", "Carriers", "A shipping provider.", "reference entity"),
            ("shipments", "Shipments", "An outbound shipment.", "transaction entity"),
            ("shipment_items", "Shipment items", "An outbound item line.", "transaction detail"),
            ("pick_events", "Pick events", "A warehouse pick event.", "event entity"),
            ("stock_movements", "Stock movements", "A quantity movement.", "event entity"),
            ("delivery_events", "Delivery events", "A carrier event.", "event entity"),
        ]
        attrs = [
            ("warehouses", "warehouse_id", "INTEGER", "Stable warehouse identifier.", False),
            ("warehouses", "capacity_units", "INTEGER", "Storage capacity in units.", False),
            (
                "bins",
                "bin_id",
                "INTEGER",
                "Stable bin identifier; not a product identifier.",
                False,
            ),
            ("bins", "warehouse_id", "INTEGER", "Authorized warehouse reference.", False),
            ("products", "product_id", "INTEGER", "Stable product identifier.", False),
            (
                "inventory_snapshots",
                "warehouse_id",
                "INTEGER",
                "Authorized warehouse reference.",
                False,
            ),
            (
                "inventory_snapshots",
                "product_id",
                "INTEGER",
                "Authorized product reference.",
                False,
            ),
            ("inventory_snapshots", "snapshot_at", "TIMESTAMPTZ", "Observation timestamp.", False),
            ("inventory_snapshots", "on_hand_qty", "INTEGER", "Observed quantity on hand.", False),
            (
                "purchase_orders",
                "warehouse_id",
                "INTEGER",
                "Authorized warehouse reference.",
                False,
            ),
            (
                "purchase_order_lines",
                "po_id",
                "INTEGER",
                "Authorized purchase-order reference.",
                False,
            ),
            (
                "purchase_order_lines",
                "product_id",
                "INTEGER",
                "Authorized product reference.",
                False,
            ),
            ("purchase_order_lines", "ordered_qty", "INTEGER", "Quantity ordered.", False),
            (
                "receipts",
                "po_line_id",
                "INTEGER",
                "Authorized purchase-order line reference.",
                False,
            ),
            ("receipts", "received_qty", "INTEGER", "Quantity received.", False),
            ("shipments", "warehouse_id", "INTEGER", "Authorized warehouse reference.", False),
            ("shipments", "carrier_id", "INTEGER", "Authorized carrier reference.", False),
            ("shipments", "promised_at", "TIMESTAMPTZ", "Promised delivery timestamp.", False),
            ("shipments", "status", "TEXT", "Shipment status.", False),
            ("shipment_items", "shipment_id", "INTEGER", "Authorized shipment reference.", False),
            ("shipment_items", "product_id", "INTEGER", "Authorized product reference.", False),
            ("shipment_items", "quantity", "INTEGER", "Quantity shipped.", False),
            (
                "pick_events",
                "shipment_item_id",
                "INTEGER",
                "Authorized shipment-item reference.",
                False,
            ),
            ("delivery_events", "shipment_id", "INTEGER", "Authorized shipment reference.", False),
            ("delivery_events", "payload", "JSONB", "Carrier event payload.", False),
        ]
        rels = [
            _relationship(
                database_id, "bin_warehouse", "bins", "warehouse_id", "warehouses", "warehouse_id"
            ),
            _relationship(
                database_id,
                "snapshot_warehouse",
                "inventory_snapshots",
                "warehouse_id",
                "warehouses",
                "warehouse_id",
            ),
            _relationship(
                database_id,
                "snapshot_product",
                "inventory_snapshots",
                "product_id",
                "products",
                "product_id",
            ),
            _relationship(
                database_id,
                "po_warehouse",
                "purchase_orders",
                "warehouse_id",
                "warehouses",
                "warehouse_id",
            ),
            _relationship(
                database_id, "line_po", "purchase_order_lines", "po_id", "purchase_orders", "po_id"
            ),
            _relationship(
                database_id,
                "line_product",
                "purchase_order_lines",
                "product_id",
                "products",
                "product_id",
            ),
            _relationship(
                database_id,
                "receipt_line",
                "receipts",
                "po_line_id",
                "purchase_order_lines",
                "po_line_id",
            ),
            _relationship(
                database_id,
                "shipment_warehouse",
                "shipments",
                "warehouse_id",
                "warehouses",
                "warehouse_id",
            ),
            _relationship(
                database_id, "shipment_carrier", "shipments", "carrier_id", "carriers", "carrier_id"
            ),
            _relationship(
                database_id,
                "item_shipment",
                "shipment_items",
                "shipment_id",
                "shipments",
                "shipment_id",
            ),
            _relationship(
                database_id,
                "item_product",
                "shipment_items",
                "product_id",
                "products",
                "product_id",
            ),
            _relationship(
                database_id,
                "pick_item",
                "pick_events",
                "shipment_item_id",
                "shipment_items",
                "shipment_item_id",
            ),
            _relationship(
                database_id,
                "delivery_shipment",
                "delivery_events",
                "shipment_id",
                "shipments",
                "shipment_id",
            ),
            _relationship(
                database_id,
                "bin_product_trap",
                "bins",
                "bin_id",
                "products",
                "product_id",
                authorized=False,
                description="Bin identifiers are not product identifiers.",
            ),
        ]
        metrics = [
            _metric(
                database_id,
                "fill_rate",
                "Units shipped divided by units ordered for the warehouse population.",
                "SUM(shipped quantity) / SUM(ordered quantity)",
            ),
            _metric(
                database_id,
                "late_shipments",
                "Shipments with delivered event after promised_at.",
                "COUNT(delivered after promised_at)",
            ),
        ]
        rules = [
            {
                "rule_id": f"rule:{database_id}:latest_snapshot",
                "name": "Latest inventory snapshot",
                "definition": "Latest means maximum snapshot_at, with snapshot_id descending as a tie-break.",
            },
            {
                "rule_id": f"rule:{database_id}:late_delivery",
                "name": "Late delivery",
                "definition": "A delivered event after promised_at is late; equality is on time.",
            },
        ]
        temporal = [
            {
                "temporal_rule_id": f"time:{database_id}:clock",
                "clock_mode": "fixed",
                "benchmark_now": "2026-06-30T12:00:00Z",
                "timezone": "UTC",
                "bounds": "Event comparisons use stored UTC timestamps.",
            }
        ]
    else:
        tables = [
            ("customers", "Customers", "A monitored customer.", "anchor entity"),
            ("accounts", "Accounts", "A customer risk account.", "account entity"),
            ("transactions", "Transactions", "A financial transaction.", "transaction entity"),
            (
                "risk_assessments",
                "Risk assessments",
                "A scored risk observation.",
                "snapshot entity",
            ),
            ("alerts", "Alerts", "A generated risk alert.", "alert entity"),
            ("alert_events", "Alert events", "An alert lifecycle event.", "event entity"),
            ("investigations", "Investigations", "An alert investigation.", "case entity"),
            ("cases", "Cases", "A customer risk case.", "case entity"),
            ("analyst_actions", "Analyst actions", "An analyst action.", "event entity"),
            ("devices", "Devices", "A login device.", "reference entity"),
            ("login_events", "Login events", "A login observation.", "event entity"),
            ("watchlist_matches", "Watchlist matches", "A watchlist match.", "event entity"),
        ]
        attrs = [
            ("customers", "customer_id", "INTEGER", "Stable customer identifier.", False),
            ("customers", "segment", "TEXT", "Customer segment.", False),
            ("accounts", "customer_id", "INTEGER", "Authorized customer reference.", False),
            ("accounts", "status", "TEXT", "Account state.", False),
            ("transactions", "account_id", "INTEGER", "Authorized account reference.", False),
            ("transactions", "amount", "NUMERIC", "Transaction amount.", False),
            ("transactions", "transacted_at", "TIMESTAMPTZ", "Transaction timestamp.", False),
            ("transactions", "payload", "JSONB", "Provider risk payload.", False),
            ("risk_assessments", "customer_id", "INTEGER", "Authorized customer reference.", False),
            ("risk_assessments", "assessed_at", "TIMESTAMPTZ", "Assessment timestamp.", False),
            ("risk_assessments", "risk_score", "NUMERIC", "Numeric risk score.", False),
            ("alerts", "customer_id", "INTEGER", "Authorized customer reference.", False),
            ("alerts", "created_at", "TIMESTAMPTZ", "Alert timestamp.", False),
            ("alerts", "severity", "TEXT", "Alert severity.", False),
            ("alerts", "status", "TEXT", "Alert state.", False),
            ("alert_events", "alert_id", "INTEGER", "Authorized alert reference.", False),
            ("investigations", "alert_id", "INTEGER", "Authorized alert reference.", False),
            ("investigations", "closed_at", "TIMESTAMPTZ", "Investigation close timestamp.", True),
            ("cases", "customer_id", "INTEGER", "Authorized customer reference.", False),
            (
                "analyst_actions",
                "investigation_id",
                "INTEGER",
                "Authorized investigation reference.",
                False,
            ),
            ("devices", "device_id", "INTEGER", "Stable device identifier.", False),
            ("login_events", "account_id", "INTEGER", "Authorized account reference.", False),
            ("login_events", "device_id", "INTEGER", "Authorized device reference.", False),
            (
                "watchlist_matches",
                "customer_id",
                "INTEGER",
                "Authorized customer reference.",
                False,
            ),
            ("watchlist_matches", "cleared_at", "TIMESTAMPTZ", "Clear timestamp.", True),
        ]
        rels = [
            _relationship(
                database_id,
                "account_customer",
                "accounts",
                "customer_id",
                "customers",
                "customer_id",
            ),
            _relationship(
                database_id,
                "transaction_account",
                "transactions",
                "account_id",
                "accounts",
                "account_id",
            ),
            _relationship(
                database_id,
                "assessment_customer",
                "risk_assessments",
                "customer_id",
                "customers",
                "customer_id",
            ),
            _relationship(
                database_id, "alert_customer", "alerts", "customer_id", "customers", "customer_id"
            ),
            _relationship(
                database_id, "event_alert", "alert_events", "alert_id", "alerts", "alert_id"
            ),
            _relationship(
                database_id,
                "investigation_alert",
                "investigations",
                "alert_id",
                "alerts",
                "alert_id",
            ),
            _relationship(
                database_id, "case_customer", "cases", "customer_id", "customers", "customer_id"
            ),
            _relationship(
                database_id,
                "action_investigation",
                "analyst_actions",
                "investigation_id",
                "investigations",
                "investigation_id",
            ),
            _relationship(
                database_id, "login_account", "login_events", "account_id", "accounts", "account_id"
            ),
            _relationship(
                database_id, "login_device", "login_events", "device_id", "devices", "device_id"
            ),
            _relationship(
                database_id,
                "watchlist_customer",
                "watchlist_matches",
                "customer_id",
                "customers",
                "customer_id",
            ),
            _relationship(
                database_id,
                "device_customer_trap",
                "devices",
                "device_id",
                "customers",
                "customer_id",
                authorized=False,
                description="A device is not directly authorized to join to a customer.",
            ),
        ]
        metrics = [
            _metric(
                database_id,
                "open_alerts",
                "Alerts with status open.",
                "COUNT(alert_id) FILTER (WHERE status = 'open')",
            ),
            _metric(
                database_id,
                "high_risk_amount",
                "Transaction amount for transactions whose visible risk score is high.",
                "SUM(amount) FILTER (WHERE payload risk_score >= 70)",
            ),
        ]
        rules = [
            {
                "rule_id": f"rule:{database_id}:latest_assessment",
                "name": "Latest risk assessment",
                "definition": "Latest means maximum assessed_at; assessment_id descending breaks ties.",
            },
            {
                "rule_id": f"rule:{database_id}:open_alert",
                "name": "Open alert",
                "definition": "alerts.status = 'open'.",
            },
        ]
        temporal = [
            {
                "temporal_rule_id": f"time:{database_id}:clock",
                "clock_mode": "fixed",
                "benchmark_now": "2026-06-30T12:00:00Z",
                "timezone": "UTC",
                "bounds": "Stored event timestamps are UTC.",
            }
        ]
    return _base_authority(database_id, tables, attrs, rels, metrics, rules, temporal)


def write_database_files() -> None:
    for database_id, (schema, seed) in DB_DEFINITIONS.items():
        root = ROOT / "databases" / database_id
        root.mkdir(parents=True, exist_ok=True)
        (root / "schema.sql").write_text(schema, encoding="utf-8")
        (root / "seed.sql").write_text(seed, encoding="utf-8")
        (root / "README.md").write_text(DOMAIN_DOCS[database_id], encoding="utf-8")
        (root / "seed.py").write_text(
            "from benchmark.m38_authoring import seed_m38_database\n\n"
            f"if __name__ == '__main__':\n    print(seed_m38_database('{database_id}'))\n",
            encoding="utf-8",
        )
        authority = _authority_for(database_id)
        for key in (
            "entities",
            "attributes",
            "relationships",
            "metrics",
            "business_rules",
            "temporal_rules",
            "policy",
        ):
            _dump(root / "authority" / f"{key}.json", authority[key])
        _dump(
            root / "authority" / "database.json",
            {
                "database_id": database_id,
                "schema_name": M38_DATABASES[database_id],
                "benchmark_now": "2026-06-30T12:00:00Z",
                "seed": 3801,
                "generator_version": M38_GENERATOR_VERSION,
            },
        )


def seed_m38_database(database_id: str) -> dict[str, Any]:
    import psycopg

    from benchmark.authoring import connection_kwargs_from_env

    schema, seed = DB_DEFINITIONS[database_id]
    with psycopg.connect(**connection_kwargs_from_env()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{M38_DATABASES[database_id]}" CASCADE')
            cursor.execute(schema)
            cursor.execute(seed)
        connection.commit()
    return {"database_id": database_id, "schema": M38_DATABASES[database_id], "seed": 3801}


def _contract(outputs: list[str], row_order: bool = True) -> dict[str, Any]:
    return {
        "column_count": len(outputs),
        "row_order": row_order,
        "aliases_significant": False,
        "duplicates_significant": True,
        "numeric_tolerance": None,
        "timestamp_timezone": "UTC",
    }


def _case(
    case_id: str,
    database_id: str,
    question: str,
    task_type: str,
    tags: list[str],
    ref_a: str | None,
    ref_b: str | None,
    outputs: list[str],
    fixtures: list[dict[str, Any]],
    required_facts: list[str],
    *,
    difficulty: str = "COMPOSED",
    evidence: dict[str, Any] | None = None,
    mutants: list[dict[str, Any]] | None = None,
    population: str = "matching-only",
    grouping: list[str] | None = None,
    calculations: list[str] | None = None,
    temporal_semantics: dict[str, Any] | None = None,
    row_order: bool = True,
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
    behavior = task_type
    target: dict[str, Any] = {
        "behavior": behavior,
        "population": population,
        "outputs": outputs,
        "relationships": required_facts,
        "filters": [],
        "aggregations": [],
        "grouping": grouping or [],
        "calculations": calculations or [],
        "temporal_semantics": temporal_semantics or {},
        "ordering": {"columns": outputs, "direction": "ASC"} if row_order else {},
        "limit": None,
        "query_shape_tags": tags,
        "null_default_semantics": "preserve SQL NULL semantics",
        "result_comparison_contract": _contract(outputs, row_order),
        "semantic_provenance": {
            "population": {"source": "QUESTION_EXPLICIT"},
            "filters": {"source": "QUESTION_EXPLICIT"},
            "ordering": {"source": "QUESTION_EXPLICIT" if row_order else "NOT_APPLICABLE"},
            "limit": {"source": "NOT_APPLICABLE"},
            "temporal": {
                "source": "VISIBLE_BUSINESS_RULE" if temporal_semantics else "NOT_APPLICABLE"
            },
            "rounding": {"source": "QUESTION_EXPLICIT"},
        },
        "projection_contract": {
            "mode": "EXACT",
            "fields": [
                {
                    "semantic_name": value,
                    "role": "IDENTIFIER" if value.endswith("_id") else "MEASURE",
                }
                for value in outputs
            ],
            "extra_fields_allowed": False,
            "source": "QUESTION_EXPLICIT",
        },
    }
    truth: dict[str, Any] = {
        "case_id": case_id,
        "database_id": database_id,
        "semantic_target": target,
        "required_context_facts": required_facts,
        "evidence": evidence or {},
        "reference_implementation_a": {"sql": ref_a} if ref_a else {},
        "reference_implementation_b": {"sql": ref_b} if ref_b else {},
        "counterfactual_fixtures": fixtures,
        "semantic_mutants": mutants or [],
    }
    if task_type == "ANSWERABLE":
        target["reference_independence"] = "MODERATE"
    return case, truth


def _fixture(case_id: str, ordinal: int, purpose: str, patch_sql: list[str]) -> dict[str, Any]:
    return {
        "fixture_id": f"{case_id}_cf{ordinal}_{_sha_text(purpose)[:6]}",
        "purpose": purpose,
        "patch_sql": patch_sql,
    }


def _mutants(
    case_id: str, sql: str, extra_column: str, tags: list[str] | None = None
) -> list[dict[str, Any]]:
    tags = tags or []
    if "json" in tags:
        family = "JSON_TYPE_COERCION_ERROR"
        component = "json"
    elif "latest-row" in tags or "tie-break" in tags:
        family = "LATEST_ROW_ERROR"
        component = "latest_row"
    elif "relationship" in tags or "multi-hop" in tags or "multi_hop" in tags:
        family = "RELATIONSHIP_PATH_ERROR"
        component = "relationship"
    elif "aggregation" in tags or "ratio" in tags or "grouping" in tags:
        family = "WRONG_AGGREGATION_GRAIN"
        component = "aggregation"
    elif "temporal" in tags:
        family = "TEMPORAL_BOUNDARY_ERROR"
        component = "temporal"
    else:
        family = "FILTER_SCOPE_ERROR"
        component = "filter_scope"
    return [
        {
            "mutant_id": f"m38_{case_id}_semantic",
            "failure_category": family,
            "description": "Applies a plausible but incorrect semantic restriction to the requested result.",
            "semantic_rationale": f"Tests the case's {family} contract against a deliberately over-restrictive interpretation.",
            "sql": f"SELECT * FROM ({sql}) AS candidate WHERE FALSE",
            "status": "VALID",
            "target_component": component,
        },
        {
            "mutant_id": f"m38_{case_id}_limit",
            "failure_category": "LIMIT_SEMANTICS_ERROR",
            "description": "Incorrectly keeps only one result row.",
            "semantic_rationale": "Tests that the full requested population is returned.",
            "sql": f"SELECT * FROM ({sql}) AS candidate LIMIT 1",
            "status": "VALID",
            "target_component": "limit",
        },
        {
            "mutant_id": f"m38_{case_id}_projection",
            "failure_category": "projection",
            "description": "Adds an unrequested helper or qualification field.",
            "semantic_rationale": "Tests the public exact final projection rule.",
            "sql": f"SELECT candidate.*, {extra_column} AS diagnostic_extra FROM ({sql}) AS candidate",
            "status": "VALID",
            "target_component": "projection",
        },
    ]


def _answerable_cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    # The catalog uses explicit final fields in every question.  Each case has
    # two minimal fixtures; the fixtures are intentionally small and inspectable.
    subs: list[tuple[str, str, str, str, list[str], list[str], str]] = [
        (
            "subscription_01",
            "Return only the subscription IDs for subscriptions whose status is active.",
            "SELECT subscription_id FROM subscriptions WHERE status = 'active' ORDER BY subscription_id",
            "SELECT s.subscription_id FROM subscriptions s JOIN (SELECT subscription_id FROM subscriptions WHERE status = 'active') a ON a.subscription_id = s.subscription_id ORDER BY s.subscription_id",
            ["subscription_id"],
            ["filter"],
            "subscriptions",
        ),
        (
            "subscription_02",
            "Return only the account IDs that have at least one active subscription.",
            "SELECT DISTINCT account_id FROM subscriptions WHERE status = 'active' ORDER BY account_id",
            "SELECT account_id FROM accounts WHERE EXISTS (SELECT 1 FROM subscriptions s WHERE s.account_id = accounts.account_id AND s.status = 'active') ORDER BY account_id",
            ["account_id"],
            ["relationship", "population"],
            "subscriptions",
        ),
        (
            "subscription_03",
            "For each account, return the account ID and the total amount of its paid invoices.",
            "SELECT account_id, SUM(total_due) AS paid_total FROM invoices WHERE status = 'paid' GROUP BY account_id ORDER BY account_id",
            "SELECT a.account_id, p.paid_total FROM accounts a JOIN (SELECT account_id, SUM(total_due) AS paid_total FROM invoices WHERE status = 'paid' GROUP BY account_id) p ON p.account_id = a.account_id ORDER BY a.account_id",
            ["account_id", "paid_total"],
            ["aggregation", "grouping"],
            "invoices",
        ),
        (
            "subscription_04",
            "For each account, return the account ID and net collected amount from captured payments after refunds.",
            "SELECT i.account_id, SUM(p.amount) - COALESCE(SUM(r.amount), 0) AS net_collected FROM invoices i JOIN payments p ON p.invoice_id = i.invoice_id LEFT JOIN refunds r ON r.payment_id = p.payment_id WHERE p.status = 'captured' GROUP BY i.account_id ORDER BY i.account_id",
            "SELECT account_id, SUM(net_amount) AS net_collected FROM (SELECT i.account_id, p.amount - COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.payment_id = p.payment_id),0) AS net_amount FROM invoices i JOIN payments p ON p.invoice_id=i.invoice_id WHERE p.status='captured') x GROUP BY account_id ORDER BY account_id",
            ["account_id", "net_collected"],
            ["relationship", "aggregation", "calculation"],
            "payments",
        ),
        (
            "subscription_05",
            "For each account, return the account ID and the plan ID of its most recently started subscription.",
            "SELECT DISTINCT ON (account_id) account_id, plan_id FROM subscriptions ORDER BY account_id, starts_on DESC, subscription_id DESC",
            "SELECT account_id, plan_id FROM (SELECT account_id, plan_id, ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY starts_on DESC, subscription_id DESC) AS rn FROM subscriptions) s WHERE rn=1 ORDER BY account_id",
            ["account_id", "plan_id"],
            ["latest-row", "window"],
            "subscriptions",
        ),
        (
            "subscription_06",
            "For each account, return the account ID and the number of failed payments.",
            "SELECT i.account_id, COUNT(*) AS failed_payment_count FROM invoices i JOIN payments p ON p.invoice_id=i.invoice_id WHERE p.status='failed' GROUP BY i.account_id ORDER BY i.account_id",
            "SELECT account_id, COUNT(payment_id) AS failed_payment_count FROM (SELECT i.account_id, p.payment_id FROM invoices i JOIN payments p ON p.invoice_id=i.invoice_id AND p.status='failed') x GROUP BY account_id ORDER BY account_id",
            ["account_id", "failed_payment_count"],
            ["relationship", "aggregation"],
            "payments",
        ),
        (
            "subscription_07",
            "For each account with an active subscription, return the account ID and its monthly recurring revenue.",
            "SELECT s.account_id, SUM(p.monthly_price) AS mrr FROM subscriptions s JOIN plans p ON p.plan_id=s.plan_id WHERE s.status='active' GROUP BY s.account_id ORDER BY s.account_id",
            "SELECT account_id, SUM(monthly_price) AS mrr FROM (SELECT s.account_id, p.monthly_price FROM subscriptions s JOIN plans p ON p.plan_id=s.plan_id WHERE s.status='active') x GROUP BY account_id ORDER BY account_id",
            ["account_id", "mrr"],
            ["relationship", "aggregation", "population"],
            "subscriptions",
        ),
        (
            "subscription_08",
            "Return only account IDs whose June usage exceeded 1,000 units.",
            "SELECT account_id FROM usage_events WHERE occurred_at >= TIMESTAMPTZ '2026-06-01' AND occurred_at < TIMESTAMPTZ '2026-07-01' GROUP BY account_id HAVING SUM(units) > 1000 ORDER BY account_id",
            "SELECT account_id FROM (SELECT account_id, SUM(units) AS units FROM usage_events WHERE occurred_at >= TIMESTAMPTZ '2026-06-01' AND occurred_at < TIMESTAMPTZ '2026-07-01' GROUP BY account_id) u WHERE units > 1000 ORDER BY account_id",
            ["account_id"],
            ["temporal", "aggregation", "filter"],
            "usage_events",
        ),
        (
            "subscription_09",
            "Return only invoice IDs that were open and past due on 2026-06-30.",
            "SELECT invoice_id FROM invoices WHERE status='open' AND due_on < DATE '2026-06-30' ORDER BY invoice_id",
            "SELECT i.invoice_id FROM invoices i JOIN (SELECT invoice_id FROM invoices WHERE status='open') o ON o.invoice_id=i.invoice_id WHERE i.due_on < DATE '2026-06-30' ORDER BY i.invoice_id",
            ["invoice_id"],
            ["temporal", "filter"],
            "invoices",
        ),
        (
            "subscription_10",
            "For each plan, return the plan ID and the refund rate, defined as refunded captured dollars divided by captured dollars.",
            "SELECT s.plan_id, COALESCE(SUM(r.amount),0) / NULLIF(SUM(p.amount),0) AS refund_rate FROM subscriptions s JOIN invoices i ON i.subscription_id=s.subscription_id JOIN payments p ON p.invoice_id=i.invoice_id AND p.status='captured' LEFT JOIN refunds r ON r.payment_id=p.payment_id GROUP BY s.plan_id ORDER BY s.plan_id",
            "SELECT plan_id, SUM(refunded) / NULLIF(SUM(captured),0) AS refund_rate FROM (SELECT s.plan_id, p.amount AS captured, COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.payment_id=p.payment_id),0) AS refunded FROM subscriptions s JOIN invoices i ON i.subscription_id=s.subscription_id JOIN payments p ON p.invoice_id=i.invoice_id WHERE p.status='captured') q GROUP BY plan_id ORDER BY plan_id",
            ["plan_id", "refund_rate"],
            ["ratio", "multi-hop", "aggregation"],
            "refunds",
        ),
        (
            "subscription_11",
            "Return only plan IDs with at least one active subscription and at least one enabled entitlement.",
            "SELECT DISTINCT s.plan_id FROM subscriptions s JOIN entitlements e ON e.subscription_id=s.subscription_id WHERE s.status='active' AND e.enabled ORDER BY s.plan_id",
            "SELECT plan_id FROM plans p WHERE EXISTS (SELECT 1 FROM subscriptions s JOIN entitlements e ON e.subscription_id=s.subscription_id WHERE s.plan_id=p.plan_id AND s.status='active' AND e.enabled) ORDER BY plan_id",
            ["plan_id"],
            ["nested", "population"],
            "entitlements",
        ),
        (
            "subscription_12",
            "For each account, return the account ID and the count of usage events whose payload units are at least 1,000.",
            "SELECT account_id, COUNT(*) AS high_usage_events FROM usage_events WHERE (payload->>'units')::INTEGER >= 1000 GROUP BY account_id ORDER BY account_id",
            "SELECT account_id, COUNT(usage_id) AS high_usage_events FROM (SELECT usage_id, account_id FROM usage_events WHERE CAST(payload->>'units' AS INTEGER) >= 1000) u GROUP BY account_id ORDER BY account_id",
            ["account_id", "high_usage_events"],
            ["json", "aggregation", "calculation"],
            "usage_events",
        ),
        (
            "subscription_13",
            "For each invoice, return the invoice ID and the timestamp of its latest dunning attempt.",
            "SELECT invoice_id, MAX(attempted_at) AS latest_attempt_at FROM dunning_attempts GROUP BY invoice_id ORDER BY invoice_id",
            "SELECT invoice_id, attempted_at AS latest_attempt_at FROM (SELECT invoice_id, attempted_at, ROW_NUMBER() OVER (PARTITION BY invoice_id ORDER BY attempted_at DESC, attempt_id DESC) AS rn FROM dunning_attempts) d WHERE rn=1 ORDER BY invoice_id",
            ["invoice_id", "latest_attempt_at"],
            ["latest-row", "window", "temporal"],
            "dunning_attempts",
        ),
        (
            "subscription_14",
            "Return only account IDs with an open invoice and no credit issued on or before 2026-06-30.",
            "SELECT DISTINCT i.account_id FROM invoices i WHERE i.status='open' AND NOT EXISTS (SELECT 1 FROM credits c WHERE c.account_id=i.account_id AND c.issued_on <= DATE '2026-06-30') ORDER BY i.account_id",
            "SELECT account_id FROM (SELECT i.account_id FROM invoices i LEFT JOIN credits c ON c.account_id=i.account_id AND c.issued_on <= DATE '2026-06-30' WHERE i.status='open' GROUP BY i.account_id HAVING COUNT(c.credit_id)=0) x ORDER BY account_id",
            ["account_id"],
            ["correlated", "null", "population"],
            "credits",
        ),
    ]
    for index, (cid, question, a, b, outputs, tags, table) in enumerate(subs, 1):
        patch_id = 9000 + index
        if table == "subscriptions":
            patches = [
                [
                    f"INSERT INTO subscriptions VALUES ({patch_id}, 2, 1, 'active', '2026-06-20', NULL)"
                ],
                [
                    f"INSERT INTO subscriptions VALUES ({patch_id}, 4, 2, 'paused', '2026-06-21', NULL)"
                ],
            ]
        elif table == "usage_events":
            patches = [
                [
                    f"INSERT INTO usage_events VALUES ({patch_id}, 2, '2026-06-20 10:00+00', 1200, '{{\"units\":\"1200\"}}')"
                ],
                [
                    f"INSERT INTO usage_events VALUES ({patch_id}, 4, '2026-06-21 10:00+00', 10, '{{\"units\":\"10\"}}')"
                ],
            ]
        elif table == "credits":
            patches = [
                [f"INSERT INTO credits VALUES ({patch_id}, 2, '2026-06-20', 20, 'fixture')"],
                [f"INSERT INTO credits VALUES ({patch_id}, 4, '2026-07-01', 20, 'fixture')"],
            ]
        else:
            patches = [
                ["INSERT INTO accounts VALUES (9901,'Fixture Account A','SMB','2026-06-20')"],
                ["INSERT INTO accounts VALUES (9902,'Fixture Account B','MID','2026-06-21')"],
            ]
        fixtures = [
            _fixture(cid, 1, "adds a relevant boundary or population row", patches[0]),
            _fixture(cid, 2, "adds a contrasting non-qualifying row", patches[1]),
        ]
        rows.append(
            _case(
                cid,
                "subscription_billing",
                question,
                "ANSWERABLE",
                tags,
                a,
                b,
                outputs,
                fixtures,
                [
                    "relationships:subscription_billing:subscription_account",
                    "metrics:subscription_billing:net_collected",
                ],
                difficulty="BASIC" if len(tags) <= 2 else "COMPOSED",
                mutants=_mutants(cid, a, "1", tags),
                grouping=outputs[0:1] if len(outputs) > 1 else [],
            )
        )
    return rows


def _warehouse_cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    specs = [
        (
            "warehouse_01",
            "For each warehouse-product pair, return the warehouse ID and product ID when its latest inventory observation has at least 20 units.",
            "SELECT DISTINCT ON (warehouse_id,product_id) warehouse_id,product_id FROM inventory_snapshots WHERE on_hand_qty >= 20 ORDER BY warehouse_id,product_id,snapshot_at DESC,snapshot_id DESC",
            "SELECT warehouse_id,product_id FROM (SELECT warehouse_id,product_id,on_hand_qty,ROW_NUMBER() OVER (PARTITION BY warehouse_id,product_id ORDER BY snapshot_at DESC,snapshot_id DESC) rn FROM inventory_snapshots) x WHERE rn=1 AND on_hand_qty>=20 ORDER BY warehouse_id,product_id",
            ["warehouse_id", "product_id"],
            ["latest-row", "filter"],
            "inventory_snapshots",
        ),
        (
            "warehouse_02",
            "For each warehouse, return the warehouse ID and total shipped quantity.",
            "SELECT s.warehouse_id, SUM(si.quantity) AS shipped_qty FROM shipments s JOIN shipment_items si ON si.shipment_id=s.shipment_id GROUP BY s.warehouse_id ORDER BY s.warehouse_id",
            "SELECT warehouse_id, SUM(quantity) AS shipped_qty FROM (SELECT s.warehouse_id, si.quantity FROM shipments s JOIN shipment_items si ON si.shipment_id=s.shipment_id) x GROUP BY warehouse_id ORDER BY warehouse_id",
            ["warehouse_id", "shipped_qty"],
            ["relationship", "aggregation"],
            "shipments",
        ),
        (
            "warehouse_03",
            "For each carrier, return the carrier ID and the number of shipments delivered after their promised time.",
            "SELECT s.carrier_id, COUNT(*) FILTER (WHERE d.event_at > s.promised_at) AS late_shipments FROM shipments s JOIN carriers c ON c.carrier_id=s.carrier_id JOIN delivery_events d ON d.shipment_id=s.shipment_id AND d.event_type='delivered' GROUP BY s.carrier_id ORDER BY s.carrier_id",
            "SELECT carrier_id, SUM(CASE WHEN delivered_at > promised_at THEN 1 ELSE 0 END) AS late_shipments FROM (SELECT s.carrier_id, s.promised_at, d.event_at delivered_at FROM shipments s JOIN delivery_events d ON d.shipment_id=s.shipment_id WHERE d.event_type='delivered') x GROUP BY carrier_id ORDER BY carrier_id",
            ["carrier_id", "late_shipments"],
            ["relationship", "temporal", "conditional aggregate"],
            "delivery_events",
        ),
        (
            "warehouse_04",
            "For each warehouse, return the warehouse ID and received quantity for open purchase orders.",
            "SELECT po.warehouse_id, SUM(r.received_qty) AS received_qty FROM purchase_orders po JOIN purchase_order_lines l ON l.po_id=po.po_id JOIN receipts r ON r.po_line_id=l.po_line_id WHERE po.status='open' GROUP BY po.warehouse_id ORDER BY po.warehouse_id",
            "SELECT warehouse_id, SUM(received_qty) AS received_qty FROM (SELECT po.warehouse_id, r.received_qty FROM purchase_orders po JOIN purchase_order_lines l ON l.po_id=po.po_id JOIN receipts r ON r.po_line_id=l.po_line_id AND po.status='open') x GROUP BY warehouse_id ORDER BY warehouse_id",
            ["warehouse_id", "received_qty"],
            ["multi-hop", "aggregation", "filter"],
            "receipts",
        ),
        (
            "warehouse_05",
            "For each warehouse and product, return the warehouse ID, product ID, and quantity from the latest inventory snapshot.",
            "SELECT DISTINCT ON (warehouse_id,product_id) warehouse_id, product_id, on_hand_qty FROM inventory_snapshots ORDER BY warehouse_id, product_id, snapshot_at DESC, snapshot_id DESC",
            "SELECT warehouse_id, product_id, on_hand_qty FROM (SELECT warehouse_id, product_id, on_hand_qty, ROW_NUMBER() OVER (PARTITION BY warehouse_id,product_id ORDER BY snapshot_at DESC,snapshot_id DESC) rn FROM inventory_snapshots) x WHERE rn=1 ORDER BY warehouse_id,product_id",
            ["warehouse_id", "product_id", "on_hand_qty"],
            ["latest-row", "window", "tie-break"],
            "inventory_snapshots",
        ),
        (
            "warehouse_06",
            "Return only shipment IDs that have a delivered event after the promised timestamp.",
            "SELECT DISTINCT s.shipment_id FROM shipments s JOIN delivery_events d ON d.shipment_id=s.shipment_id WHERE d.event_type='delivered' AND d.event_at>s.promised_at ORDER BY s.shipment_id",
            "SELECT shipment_id FROM (SELECT s.shipment_id, d.event_at, s.promised_at FROM shipments s JOIN delivery_events d ON d.shipment_id=s.shipment_id AND d.event_type='delivered') x WHERE event_at>promised_at ORDER BY shipment_id",
            ["shipment_id"],
            ["temporal", "filter"],
            "delivery_events",
        ),
        (
            "warehouse_07",
            "For each warehouse, return the warehouse ID and average hours from shipment item creation to pick.",
            "SELECT s.warehouse_id, AVG(EXTRACT(EPOCH FROM (p.picked_at-s.shipped_at))/3600) AS avg_pick_hours FROM shipments s JOIN shipment_items si ON si.shipment_id=s.shipment_id JOIN pick_events p ON p.shipment_item_id=si.shipment_item_id GROUP BY s.warehouse_id ORDER BY s.warehouse_id",
            "SELECT warehouse_id, AVG(pick_hours) AS avg_pick_hours FROM (SELECT s.warehouse_id, EXTRACT(EPOCH FROM (p.picked_at-s.shipped_at))/3600 pick_hours FROM shipments s JOIN shipment_items si ON si.shipment_id=s.shipment_id JOIN pick_events p ON p.shipment_item_id=si.shipment_item_id) x GROUP BY warehouse_id ORDER BY warehouse_id",
            ["warehouse_id", "avg_pick_hours"],
            ["multi-hop", "calculation", "aggregation"],
            "pick_events",
        ),
        (
            "warehouse_08",
            "For each product, return the product ID and the quantity ordered but not yet received.",
            "SELECT product_id, SUM(open_line_qty) AS open_qty FROM (SELECT l.product_id, l.ordered_qty-COALESCE((SELECT SUM(r.received_qty) FROM receipts r WHERE r.po_line_id=l.po_line_id),0) AS open_line_qty FROM purchase_order_lines l) x GROUP BY product_id ORDER BY product_id",
            "WITH per_line AS (SELECT l.product_id, l.ordered_qty-COALESCE(SUM(r.received_qty),0) AS open_line_qty FROM purchase_order_lines l LEFT JOIN receipts r ON r.po_line_id=l.po_line_id GROUP BY l.po_line_id,l.product_id,l.ordered_qty) SELECT product_id,SUM(open_line_qty) AS open_qty FROM per_line GROUP BY product_id ORDER BY product_id",
            ["product_id", "open_qty"],
            ["population", "null", "calculation"],
            "receipts",
        ),
        (
            "warehouse_09",
            "Return only delivery IDs whose JSON temperature value is at least 7 degrees.",
            "SELECT delivery_id FROM delivery_events WHERE (payload->>'temperature_c')::NUMERIC >= 7 ORDER BY delivery_id",
            "SELECT delivery_id FROM (SELECT delivery_id, CAST(payload->>'temperature_c' AS NUMERIC) AS temperature FROM delivery_events) x WHERE temperature>=7 ORDER BY delivery_id",
            ["delivery_id"],
            ["json", "calculation"],
            "delivery_events",
        ),
        (
            "warehouse_10",
            "For each product, return the product ID and the number of distinct shipments containing it.",
            "SELECT product_id, COUNT(DISTINCT shipment_id) AS shipment_count FROM shipment_items GROUP BY product_id ORDER BY product_id",
            "SELECT product_id, COUNT(*) AS shipment_count FROM (SELECT DISTINCT product_id, shipment_id FROM shipment_items) x GROUP BY product_id ORDER BY product_id",
            ["product_id", "shipment_count"],
            ["distinct", "aggregation"],
            "shipment_items",
        ),
        (
            "warehouse_11",
            "Return only warehouse IDs that have no inventory snapshot with zero quantity.",
            "SELECT w.warehouse_id FROM warehouses w WHERE NOT EXISTS (SELECT 1 FROM inventory_snapshots i WHERE i.warehouse_id=w.warehouse_id AND i.on_hand_qty=0) ORDER BY w.warehouse_id",
            "SELECT w.warehouse_id FROM warehouses w LEFT JOIN inventory_snapshots i ON i.warehouse_id=w.warehouse_id AND i.on_hand_qty=0 GROUP BY w.warehouse_id HAVING COUNT(i.snapshot_id)=0 ORDER BY w.warehouse_id",
            ["warehouse_id"],
            ["correlated", "null", "population"],
            "inventory_snapshots",
        ),
        (
            "warehouse_12",
            "Return the carrier ID and carrier name for the two carriers with the most shipments, ordered by shipment count descending then carrier ID.",
            "SELECT c.carrier_id, c.carrier_name FROM carriers c JOIN shipments s ON s.carrier_id=c.carrier_id GROUP BY c.carrier_id,c.carrier_name ORDER BY COUNT(*) DESC,c.carrier_id LIMIT 2",
            "SELECT carrier_id, carrier_name FROM (SELECT c.carrier_id,c.carrier_name,ROW_NUMBER() OVER (ORDER BY COUNT(s.shipment_id) DESC,c.carrier_id) rn FROM carriers c LEFT JOIN shipments s ON s.carrier_id=c.carrier_id GROUP BY c.carrier_id,c.carrier_name) x WHERE rn<=2 ORDER BY rn",
            ["carrier_id", "carrier_name"],
            ["ordering", "limit", "grouping"],
            "shipments",
        ),
        (
            "warehouse_13",
            "For each warehouse, return the warehouse ID and the ratio of shipped quantity to received quantity.",
            "SELECT w.warehouse_id, COALESCE(s.shipped_qty,0)::NUMERIC/NULLIF(COALESCE(r.received_qty,0),0) AS shipped_to_received FROM warehouses w LEFT JOIN (SELECT warehouse_id,SUM(quantity) shipped_qty FROM shipments s JOIN shipment_items i ON i.shipment_id=s.shipment_id GROUP BY warehouse_id) s ON s.warehouse_id=w.warehouse_id LEFT JOIN (SELECT po.warehouse_id,SUM(r.received_qty) received_qty FROM purchase_orders po JOIN purchase_order_lines l ON l.po_id=po.po_id JOIN receipts r ON r.po_line_id=l.po_line_id GROUP BY po.warehouse_id) r ON r.warehouse_id=w.warehouse_id ORDER BY w.warehouse_id",
            "SELECT warehouse_id, shipped_qty/NULLIF(received_qty,0) AS shipped_to_received FROM (SELECT w.warehouse_id,COALESCE((SELECT SUM(i.quantity) FROM shipments s JOIN shipment_items i ON i.shipment_id=s.shipment_id WHERE s.warehouse_id=w.warehouse_id),0)::NUMERIC shipped_qty,COALESCE((SELECT SUM(r.received_qty) FROM purchase_orders po JOIN purchase_order_lines l ON l.po_id=po.po_id JOIN receipts r ON r.po_line_id=l.po_line_id WHERE po.warehouse_id=w.warehouse_id),0) received_qty FROM warehouses w) x ORDER BY warehouse_id",
            ["warehouse_id", "shipped_to_received"],
            ["multi-hop", "ratio", "null", "aggregation"],
            "shipments",
        ),
    ]
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, (cid, question, a, b, outputs, tags, table) in enumerate(specs, 1):
        patches = [
            [f"INSERT INTO products VALUES ({9000 + index},'FX-{index}','Fixture Product',5.00)"],
            [f"INSERT INTO warehouses VALUES ({9100 + index},'Fixture Warehouse','West',500)"],
        ]
        if table in {"shipments", "delivery_events"}:
            patches = [
                [
                    f"INSERT INTO shipments VALUES ({9200 + index},1,1,'2026-06-10 08:00+00','2026-06-10 09:00+00','delivered')",
                    f"INSERT INTO delivery_events VALUES ({9300 + index},{9200 + index},'2026-06-10 10:00+00','delivered','{{\"temperature_c\":\"9\"}}')",
                ],
                [
                    f"INSERT INTO shipments VALUES ({9200 + index},2,2,'2026-06-11 08:00+00','2026-06-11 09:00+00','delivered')",
                    f"INSERT INTO delivery_events VALUES ({9300 + index},{9200 + index},'2026-06-11 09:00+00','delivered','{{\"temperature_c\":\"6\"}}')",
                ],
            ]
        fixtures = [
            _fixture(cid, 1, "adds a qualifying operational row", patches[0]),
            _fixture(cid, 2, "adds a non-qualifying or boundary row", patches[1]),
        ]
        rows.append(
            _case(
                cid,
                "warehouse_logistics",
                question,
                "ANSWERABLE",
                tags,
                a,
                b,
                outputs,
                fixtures,
                [
                    "relationships:warehouse_logistics:shipment_warehouse",
                    "relationships:warehouse_logistics:item_product",
                ],
                difficulty="BASIC" if len(tags) <= 2 else "COMPOSED",
                mutants=_mutants(cid, a, "1", tags),
                grouping=outputs[:1] if len(outputs) > 1 else [],
            )
        )
    return rows


def _risk_cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    specs = [
        (
            "risk_01",
            "Return only alert IDs whose status is open.",
            "SELECT alert_id FROM alerts WHERE status='open' ORDER BY alert_id",
            "SELECT alert_id FROM alerts WHERE status IN ('open') ORDER BY alert_id",
            ["alert_id"],
            ["filter"],
            "alerts",
        ),
        (
            "risk_02",
            "For each customer, return the customer ID and latest risk score using the latest assessment timestamp and assessment ID as the tie-break.",
            "SELECT DISTINCT ON (customer_id) customer_id,risk_score FROM risk_assessments ORDER BY customer_id,assessed_at DESC,assessment_id DESC",
            "SELECT customer_id,risk_score FROM (SELECT customer_id,risk_score,ROW_NUMBER() OVER(PARTITION BY customer_id ORDER BY assessed_at DESC,assessment_id DESC) rn FROM risk_assessments) x WHERE rn=1 ORDER BY customer_id",
            ["customer_id", "risk_score"],
            ["latest-row", "window", "tie-break"],
            "risk_assessments",
        ),
        (
            "risk_03",
            "For each customer, return the customer ID and count of open alerts.",
            "SELECT customer_id,COUNT(*) AS open_alert_count FROM alerts WHERE status='open' GROUP BY customer_id ORDER BY customer_id",
            "SELECT customer_id,COUNT(alert_id) AS open_alert_count FROM (SELECT customer_id,alert_id FROM alerts WHERE status='open') x GROUP BY customer_id ORDER BY customer_id",
            ["customer_id", "open_alert_count"],
            ["aggregation", "grouping"],
            "alerts",
        ),
        (
            "risk_04",
            "For each account, return the account ID and total transaction amount whose payload risk score is at least 70.",
            "SELECT account_id,SUM(amount) AS high_risk_amount FROM transactions WHERE (payload->>'risk_score')::NUMERIC>=70 GROUP BY account_id ORDER BY account_id",
            "SELECT account_id,SUM(high_risk_amount) AS high_risk_amount FROM (SELECT account_id,amount high_risk_amount FROM transactions WHERE CAST(payload->>'risk_score' AS NUMERIC)>=70) x GROUP BY account_id ORDER BY account_id",
            ["account_id", "high_risk_amount"],
            ["json", "aggregation", "filter"],
            "transactions",
        ),
        (
            "risk_05",
            "For each alert severity, return the severity and the fraction of alerts that have an investigation.",
            "SELECT a.severity,COUNT(i.investigation_id)::NUMERIC/NULLIF(COUNT(*),0) AS investigation_rate FROM alerts a LEFT JOIN investigations i ON i.alert_id=a.alert_id GROUP BY a.severity ORDER BY a.severity",
            "SELECT severity,SUM(has_investigation)::NUMERIC/NULLIF(COUNT(*),0) AS investigation_rate FROM (SELECT a.severity,CASE WHEN EXISTS(SELECT 1 FROM investigations i WHERE i.alert_id=a.alert_id) THEN 1 ELSE 0 END has_investigation FROM alerts a) x GROUP BY severity ORDER BY severity",
            ["severity", "investigation_rate"],
            ["conditional aggregate", "ratio", "null"],
            "investigations",
        ),
        (
            "risk_06",
            "For each investigation, return the investigation ID and resolution hours for investigations that are closed.",
            "SELECT investigation_id,EXTRACT(EPOCH FROM (closed_at-opened_at))/3600 AS resolution_hours FROM investigations WHERE closed_at IS NOT NULL ORDER BY investigation_id",
            "SELECT investigation_id,resolution_hours FROM (SELECT investigation_id,EXTRACT(EPOCH FROM (closed_at-opened_at))/3600 resolution_hours FROM investigations) x WHERE resolution_hours IS NOT NULL ORDER BY investigation_id",
            ["investigation_id", "resolution_hours"],
            ["temporal", "calculation", "filter"],
            "investigations",
        ),
        (
            "risk_07",
            "Return only device IDs used successfully by more than one account.",
            "SELECT device_id FROM login_events WHERE result='success' GROUP BY device_id HAVING COUNT(DISTINCT account_id)>1 ORDER BY device_id",
            "SELECT device_id FROM (SELECT DISTINCT device_id,account_id FROM login_events WHERE result='success') x GROUP BY device_id HAVING COUNT(*)>1 ORDER BY device_id",
            ["device_id"],
            ["relationship", "distinct", "aggregation"],
            "login_events",
        ),
        (
            "risk_08",
            "Return only transaction IDs whose JSON risk score is at least 80.",
            "SELECT transaction_id FROM transactions WHERE (payload->>'risk_score')::NUMERIC>=80 ORDER BY transaction_id",
            "SELECT transaction_id FROM (SELECT transaction_id,CAST(payload->>'risk_score' AS NUMERIC) score FROM transactions) x WHERE score>=80 ORDER BY transaction_id",
            ["transaction_id"],
            ["json", "calculation"],
            "transactions",
        ),
        (
            "risk_09",
            "Return the customer ID and risk score for the three highest latest customer risk scores, ordered by score descending and customer ID.",
            "SELECT customer_id,risk_score FROM (SELECT DISTINCT ON (customer_id) customer_id,risk_score FROM risk_assessments ORDER BY customer_id,assessed_at DESC,assessment_id DESC) x ORDER BY risk_score DESC,customer_id LIMIT 3",
            "SELECT customer_id,risk_score FROM (SELECT customer_id,risk_score,ROW_NUMBER() OVER(PARTITION BY customer_id ORDER BY assessed_at DESC,assessment_id DESC) rn FROM risk_assessments) x WHERE rn=1 ORDER BY risk_score DESC,customer_id LIMIT 3",
            ["customer_id", "risk_score"],
            ["latest-row", "ordering", "limit"],
            "risk_assessments",
        ),
        (
            "risk_10",
            "Return only customer IDs that have an open alert and no cleared watchlist match.",
            "SELECT DISTINCT a.customer_id FROM alerts a WHERE a.status='open' AND NOT EXISTS (SELECT 1 FROM watchlist_matches w WHERE w.customer_id=a.customer_id AND w.cleared_at IS NOT NULL) ORDER BY a.customer_id",
            "SELECT customer_id FROM (SELECT a.customer_id FROM alerts a LEFT JOIN watchlist_matches w ON w.customer_id=a.customer_id AND w.cleared_at IS NOT NULL WHERE a.status='open' GROUP BY a.customer_id HAVING COUNT(w.match_id)=0) x ORDER BY customer_id",
            ["customer_id"],
            ["correlated", "null", "population"],
            "watchlist_matches",
        ),
        (
            "risk_11",
            "Return only customer IDs that have both an open alert and an open investigation.",
            "SELECT DISTINCT a.customer_id FROM alerts a JOIN investigations i ON i.alert_id=a.alert_id WHERE a.status='open' AND i.closed_at IS NULL ORDER BY a.customer_id",
            "SELECT customer_id FROM customers c WHERE EXISTS (SELECT 1 FROM alerts a JOIN investigations i ON i.alert_id=a.alert_id WHERE a.customer_id=c.customer_id AND a.status='open' AND i.closed_at IS NULL) ORDER BY customer_id",
            ["customer_id"],
            ["nested", "population", "relationship"],
            "investigations",
        ),
        (
            "risk_12",
            "For each customer, return the customer ID and the number of unresolved watchlist matches.",
            "SELECT c.customer_id,COUNT(w.match_id) AS unresolved_matches FROM customers c LEFT JOIN watchlist_matches w ON w.customer_id=c.customer_id AND w.cleared_at IS NULL GROUP BY c.customer_id ORDER BY c.customer_id",
            "SELECT c.customer_id,(SELECT COUNT(*) FROM watchlist_matches w WHERE w.customer_id=c.customer_id AND w.cleared_at IS NULL) AS unresolved_matches FROM customers c ORDER BY c.customer_id",
            ["customer_id", "unresolved_matches"],
            ["null", "population", "aggregation"],
            "watchlist_matches",
        ),
        (
            "risk_13",
            "For each customer segment, return the segment and the average amount of its transactions, rounded to two decimals after aggregation.",
            "SELECT c.segment,ROUND(AVG(t.amount),2) AS average_amount FROM customers c JOIN accounts a ON a.customer_id=c.customer_id JOIN transactions t ON t.account_id=a.account_id GROUP BY c.segment ORDER BY c.segment",
            "SELECT segment,ROUND(average_amount,2) AS average_amount FROM (SELECT c.segment,AVG(t.amount) average_amount FROM customers c JOIN accounts a ON a.customer_id=c.customer_id JOIN transactions t ON t.account_id=a.account_id GROUP BY c.segment) x ORDER BY segment",
            ["segment", "average_amount"],
            ["multi-hop", "aggregation", "precision"],
            "transactions",
        ),
    ]
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, (cid, question, a, b, outputs, tags, table) in enumerate(specs, 1):
        patches = [
            [
                f"INSERT INTO customers VALUES ({9000 + index},'Fixture Customer','SMB')",
                f"INSERT INTO accounts VALUES ({9900 + index},{9000 + index},'2026-06-20','open')",
            ],
            [
                f"INSERT INTO customers VALUES ({9100 + index},'Fixture Customer B','MID')",
                f"INSERT INTO accounts VALUES ({9950 + index},{9100 + index},'2026-06-21','open')",
            ],
        ]
        if table == "transactions":
            patches = [
                [
                    f"INSERT INTO transactions VALUES ({9200 + index},101,'2026-06-20 10:00+00',5000,'card','{{\"risk_score\":\"95\"}}')"
                ],
                [
                    f"INSERT INTO transactions VALUES ({9250 + index},102,'2026-06-21 10:00+00',10,'card','{{\"risk_score\":\"5\"}}')"
                ],
            ]
        fixtures = [
            _fixture(
                cid, 1, "adds a discriminating high or qualifying risk observation", patches[0]
            ),
            _fixture(cid, 2, "adds a contrasting low or non-qualifying observation", patches[1]),
        ]
        rows.append(
            _case(
                cid,
                "risk_operations",
                question,
                "ANSWERABLE",
                tags,
                a,
                b,
                outputs,
                fixtures,
                [
                    "relationships:risk_operations:alert_customer",
                    "relationships:risk_operations:transaction_account",
                ],
                difficulty="BASIC" if len(tags) <= 2 else "COMPOSED",
                mutants=_mutants(cid, a, "1", tags),
                grouping=outputs[:1] if len(outputs) > 1 else [],
            )
        )
    return rows


def _non_answerable() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    definitions = [
        (
            "subscription_billing",
            [
                (
                    "AUTHORITY_BLOCKED",
                    "Return the billing account's internal risk rating from the risk service.",
                    "billing account has no authorized relationship to a risk service",
                ),
                (
                    "AUTHORITY_BLOCKED",
                    "Return the payment processor's chargeback reason for each invoice.",
                    "processor chargeback relation is absent",
                ),
                (
                    "AUTHORITY_BLOCKED",
                    "Return the end user's identity behind each subscription.",
                    "end-user identity relation is absent",
                ),
            ],
            [
                (
                    "AMBIGUOUS",
                    "Return accounts whose active subscription is current.",
                    "status-current and date-current interpretations differ",
                ),
                (
                    "AMBIGUOUS",
                    "Show the latest plan for each account.",
                    "latest by start date versus latest change timestamp differ",
                ),
            ],
            [
                (
                    "POLICY_BLOCKED",
                    "Delete invoices that are older than 90 days.",
                    "DELETE is prohibited",
                )
            ],
        ),
        (
            "warehouse_logistics",
            [
                (
                    "AUTHORITY_BLOCKED",
                    "Return the supplier legal name for each purchase order.",
                    "supplier legal entity relation is absent",
                ),
                (
                    "AUTHORITY_BLOCKED",
                    "Return the customer destination for each shipment.",
                    "shipment-to-customer relation is absent",
                ),
                (
                    "AUTHORITY_BLOCKED",
                    "Return the worker's employment department for each pick.",
                    "worker HR relation is absent",
                ),
                (
                    "AUTHORITY_BLOCKED",
                    "Return carrier contract penalties for late shipments.",
                    "carrier contract relation is absent",
                ),
            ],
            [
                (
                    "AMBIGUOUS",
                    "Find the latest inventory for each product.",
                    "latest overall versus latest per warehouse differs",
                ),
                (
                    "AMBIGUOUS",
                    "List late shipments.",
                    "late by promised delivery versus late by carrier status differs",
                ),
            ],
            [
                (
                    "POLICY_BLOCKED",
                    "Update the promised time for delayed shipments.",
                    "UPDATE is prohibited",
                )
            ],
        ),
        (
            "risk_operations",
            [
                (
                    "AUTHORITY_BLOCKED",
                    "Return the customer's bank-account owner for each risk alert.",
                    "bank-owner relation is absent",
                ),
                (
                    "AUTHORITY_BLOCKED",
                    "Return the external bureau explanation for each risk score.",
                    "bureau relation is absent",
                ),
                (
                    "AUTHORITY_BLOCKED",
                    "Return the analyst's HR identity for each investigation.",
                    "analyst HR relation is absent",
                ),
            ],
            [
                (
                    "AMBIGUOUS",
                    "Find repeat alerts for customers.",
                    "repeat within a calendar day versus repeat over all history differs",
                ),
                (
                    "AMBIGUOUS",
                    "Show unresolved cases.",
                    "case resolved_at versus investigation closed_at differs",
                ),
            ],
            [
                (
                    "POLICY_BLOCKED",
                    "Insert a manual risk alert for customer 1.",
                    "INSERT is prohibited",
                ),
                ("POLICY_BLOCKED", "Delete cleared watchlist matches.", "DELETE is prohibited"),
            ],
        ),
    ]
    for database_id, authorities, ambiguous, policies in definitions:
        for index, (kind, question, reason) in enumerate(authorities, 1):
            cid = {
                "subscription_billing": "subscription",
                "warehouse_logistics": "warehouse",
                "risk_operations": "risk",
            }[database_id] + f"_{index + 14:02d}"
            rows.append(
                _case(
                    cid,
                    database_id,
                    question,
                    kind,
                    ["governance"],
                    None,
                    None,
                    [],
                    [],
                    [f"policy:{database_id}:readonly"],
                    difficulty="BASIC",
                    evidence={
                        "missing_authority": reason,
                        "missing_relationship": f"relationship:{database_id}:external_requirement_{index}",
                        "expected_behavior": "BLOCKED_AUTHORITY",
                    },
                )
            )
        for index, (kind, question, reason) in enumerate(ambiguous, 1):
            ambiguity_index = index + len(authorities) + 14
            cid = {
                "subscription_billing": "subscription",
                "warehouse_logistics": "warehouse",
                "risk_operations": "risk",
            }[database_id] + f"_{ambiguity_index:02d}"
            ambiguity_sql: dict[str, tuple[str, str, list[str]]] = {
                "subscription_18": (
                    "SELECT DISTINCT account_id FROM subscriptions WHERE status='active' ORDER BY account_id",
                    "SELECT DISTINCT account_id FROM subscriptions WHERE ends_on IS NULL OR ends_on >= DATE '2026-06-30' ORDER BY account_id",
                    [
                        "INSERT INTO accounts VALUES (9901,'Ambiguous Fixture','SMB','2026-06-20')",
                        "INSERT INTO subscriptions VALUES (9901,9901,1,'paused','2026-06-20','2027-01-01')",
                    ],
                ),
                "subscription_19": (
                    "SELECT DISTINCT ON (account_id) account_id,plan_id FROM subscriptions ORDER BY account_id,starts_on DESC,subscription_id DESC",
                    "SELECT s.account_id,pc.new_plan_id FROM subscriptions s JOIN (SELECT DISTINCT ON (subscription_id) subscription_id,new_plan_id FROM plan_changes ORDER BY subscription_id,changed_at DESC,change_id DESC) pc ON pc.subscription_id=s.subscription_id ORDER BY s.account_id",
                    ["INSERT INTO plan_changes VALUES (9901,101,'2026-07-01 00:00+00',1,3)"],
                ),
                "warehouse_19": (
                    "SELECT product_id FROM inventory_snapshots ORDER BY snapshot_at DESC,snapshot_id DESC LIMIT 1",
                    "SELECT DISTINCT ON (warehouse_id,product_id) warehouse_id,product_id FROM inventory_snapshots ORDER BY warehouse_id,product_id,snapshot_at DESC,snapshot_id DESC",
                    ["INSERT INTO inventory_snapshots VALUES (9901,1,1,'2026-07-03 00:00+00',5)"],
                ),
                "warehouse_20": (
                    "SELECT s.shipment_id FROM shipments s JOIN delivery_events d ON d.shipment_id=s.shipment_id WHERE d.event_type='delivered' AND d.event_at>s.promised_at ORDER BY s.shipment_id",
                    "SELECT shipment_id FROM shipments WHERE status='in_transit' ORDER BY shipment_id",
                    [
                        "INSERT INTO shipments VALUES (9901,1,1,'2026-06-10 08:00+00','2026-06-10 09:00+00','delivered')",
                        "INSERT INTO delivery_events VALUES (9901,9901,'2026-06-10 10:00+00','delivered','{}')",
                    ],
                ),
                "risk_18": (
                    "SELECT customer_id,DATE(created_at),COUNT(*) FROM alerts GROUP BY customer_id,DATE(created_at) HAVING COUNT(*)>1 ORDER BY customer_id",
                    "SELECT customer_id,COUNT(*) FROM alerts GROUP BY customer_id HAVING COUNT(*)>1 ORDER BY customer_id",
                    [
                        "INSERT INTO alerts VALUES (9901,1,'2026-06-01 15:00+00','velocity','high','open')"
                    ],
                ),
                "risk_19": (
                    "SELECT customer_id FROM cases WHERE resolved_at IS NULL ORDER BY customer_id",
                    "SELECT DISTINCT a.customer_id FROM alerts a JOIN investigations i ON i.alert_id=a.alert_id WHERE i.closed_at IS NULL ORDER BY a.customer_id",
                    ["INSERT INTO cases VALUES (9901,4,'2026-06-20 10:00+00',NULL,NULL)"],
                ),
            }
            proof_a, proof_b, patch_sql = ambiguity_sql[cid]
            evidence = {
                "interpretation_a": reason + " interpretation A",
                "interpretation_b": reason + " interpretation B",
                "proof_sql_a": proof_a,
                "proof_sql_b": proof_b,
                "expected_behavior": "NEEDS_CLARIFICATION",
            }
            fixture = {
                "fixture_id": f"{cid}_ambiguity",
                "purpose": "makes the competing interpretations return different results",
                "patch_sql": patch_sql,
            }
            rows.append(
                _case(
                    cid,
                    database_id,
                    question,
                    kind,
                    ["governance", "ambiguity"],
                    None,
                    None,
                    [],
                    [fixture],
                    [f"temporal_rules:{database_id}:clock"],
                    difficulty="BASIC",
                    evidence=evidence,
                )
            )
        for index, (kind, question, reason) in enumerate(policies, 1):
            policy_index = index + 19 if database_id == "subscription_billing" else index + 20
            cid = {
                "subscription_billing": "subscription",
                "warehouse_logistics": "warehouse",
                "risk_operations": "risk",
            }[database_id] + f"_{policy_index:02d}"
            rows.append(
                _case(
                    cid,
                    database_id,
                    question,
                    kind,
                    ["governance", "policy"],
                    None,
                    None,
                    [],
                    [],
                    [f"policy:{database_id}:readonly"],
                    difficulty="BASIC",
                    evidence={"policy_violation": reason, "expected_behavior": "BLOCKED_POLICY"},
                )
            )
    return rows


def new_cases() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = _answerable_cases() + _warehouse_cases() + _risk_cases() + _non_answerable()
    # Keep deterministic domain-local ordering: answerable first, then each
    # governance class in the declared authoring distribution.
    return rows


def write_case_files() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = new_cases()
    case_root = ROOT / "cases" / "m38_dev"
    truth_root = ROOT / "ground_truth" / "m38_dev"
    for case, truth in rows:
        _dump(case_root / f"{case['case_id']}.json", case)
        _dump(truth_root / f"{truth['case_id']}.json", truth)
        if truth["counterfactual_fixtures"]:
            _dump(
                ROOT / "databases" / case["database_id"] / "fixtures" / f"{case['case_id']}.json",
                {
                    "case_id": case["case_id"],
                    "base_fixture": {"fixture_id": "base", "patch_sql": []},
                    "counterfactual_fixtures": truth["counterfactual_fixtures"],
                },
            )
    return rows


def content_hash() -> str:
    digest = hashlib.sha256()
    paths: list[Path] = []
    for relative in (
        "m38_authoring.py",
        "schemas",
        "databases",
        "cases/m38_dev",
        "ground_truth/m38_dev",
    ):
        root = ROOT / relative
        paths.extend(sorted(root.rglob("*")) if root.is_dir() else [root])
    for path in paths:
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    write_database_files()
    rows = write_case_files()
    print(json.dumps({"cases": len(rows), "content_hash": content_hash()}, indent=2))
