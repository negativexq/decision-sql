# M11.1P0 — Typed Result Persistence Integrity Repair

## Decision

`M111P0_TYPED_RESULT_PERSISTENCE_REPAIR_ACCEPTED`

The legacy evaluation path was reproduced as a real type-loss defect:

`Decimal("123.45")` → `model_dump(mode="json")` → JSON → `QueryExecution.model_validate`

restored the cell as `str("123.45")`. Comparing the original execution with
that persisted/reloaded result returned false. This can create false result
mismatches for typed numeric database values.

The evaluation-only contract `decision-sql-query-result-snapshot-v1` now uses
explicit type-tagged cells for `NULL`, `BOOLEAN`, `INTEGER`, `FLOAT`,
`DECIMAL`, `STRING`, `DATE`, and `DATETIME`. Decimal values are persisted as
exact text, while numeric-looking strings remain strings. Boolean and integer,
and date and datetime, remain distinct.

## Scope and migration

`evaluation/result_snapshot.py` provides the bounded
`snapshot_query_execution` and `restore_query_execution` API. Future
reference-result persistence was migrated in the evaluation seams for M10,
M10R, M10.4, and M10.4S. The application/runtime model and comparator were not
changed. Unsupported cell objects are rejected; no pickle, `eval`, dynamic
codec, or content-based string conversion is used.

Payloads without the exact version marker are classified as
`LEGACY_UNTYPED_RESULT_SNAPSHOT`. They are not silently treated as typed-v1,
and lost Decimal types are not guessed back from strings.

## Evidence and quarantine

The typed round-trip regression covers all eight supported types, Decimal scale
and precision examples, numeric-looking strings, mixed rows, duplicate rows,
column/row order, `row_count`, `truncated`, and comparison with the original
execution. The new Decimal self-comparison passes.

Historical M10R and M10.4S scores and all prior M10/M11 artifacts remain
unchanged. This repair does not determine how many historical cases were
mis-scored and does not rescore them. Because downstream M11 intervention
selection may depend on persisted reference results, M10R/M10.4S historical
results are pending revalidation if affected, and M11.0R, M11.0T, and M11.1
intervention-selection authority is
`PENDING_EVALUATOR_INTEGRITY_REVALIDATION`.

This quarantine does not conclude that M11.0T or M11.1 is false, nor does it
identify memory, corpus coverage, or semantic representation as the true
runtime bottleneck.

No provider, database, retrieval, benchmark, memory-OFF, counterfactual,
compatibility-contract, causal-attribution, M4, or runtime intervention work
was performed.

The next milestone is **M11.1P1 — Compatibility vs Demonstration-Adaptability
Contract Repair Audit**. It must separately re-audit result/query
compatibility and demonstration adaptability before compatibility evidence is
used for intervention selection again.
