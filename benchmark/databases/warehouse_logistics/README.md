# Warehouse logistics

Synthetic fulfillment operations with warehouses, bins, products, inventory snapshots, inbound purchase orders and receipts, carriers, shipments, picks, stock movements, and delivery events.

Warehouse, product, shipment, carrier, and event paths are explicitly authorized in the authority package. Bin identifiers are not product identifiers. Latest inventory uses snapshot timestamp and snapshot ID as the tie-break. A delivery after promised time is late; equality is on time.

Cases cover stock populations, inbound and outbound quantities, latest snapshots, late delivery, pick delay, JSON temperature, distinct shipment counts, ratios, authority traps, ambiguity, and read-only policy.
