CREATE SCHEMA IF NOT EXISTS m51a_procurement_ops;
SET search_path TO m51a_procurement_ops;
CREATE TABLE suppliers (supplier_id INTEGER PRIMARY KEY, supplier_name TEXT NOT NULL, region TEXT NOT NULL, active BOOLEAN NOT NULL, profile JSONB NOT NULL);
CREATE TABLE requisitions (req_id INTEGER PRIMARY KEY, department TEXT NOT NULL, requested_on DATE NOT NULL, status TEXT NOT NULL, estimated_amount NUMERIC(10,2) NOT NULL, metadata JSONB NOT NULL);
CREATE TABLE purchase_orders (po_id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id), ordered_on DATE NOT NULL, status TEXT NOT NULL);
CREATE TABLE po_lines (line_id INTEGER PRIMARY KEY, po_id INTEGER NOT NULL REFERENCES purchase_orders(po_id), sku TEXT NOT NULL, ordered_qty INTEGER NOT NULL, unit_cost NUMERIC(10,2) NOT NULL);
CREATE TABLE receipts (receipt_id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES po_lines(line_id), received_on TIMESTAMPTZ NOT NULL, received_qty INTEGER NOT NULL);
CREATE TABLE approvals (approval_id INTEGER PRIMARY KEY, req_id INTEGER NOT NULL REFERENCES requisitions(req_id), approved_at TIMESTAMPTZ NOT NULL, decision TEXT NOT NULL);
CREATE TABLE external_directory (directory_id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL, owner_email TEXT NOT NULL);
