# M44 fanout inventory

Offline result: **SUPPORTED_OFFLINE** for `warehouse_08`; provider calls: 0.

Answerable cases: 60.
Fanout-sensitive cases: risk_05, subscription_04, subscription_10, warehouse_08.
Multi-grain cases: warehouse_13.

`warehouse_08` has a line-grain `ordered_qty` measure and a one-to-many receipt relation; both references reduce receipts at line grain before product rollup.
