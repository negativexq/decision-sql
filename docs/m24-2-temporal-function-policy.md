# M24.2 — Temporal Function Policy Correction

## Why M24.2 exists

M24.1 left nine official analytical queries behind a broad nondeterminism boundary. M24.2 distinguishes execution-context-dependent temporal expressions from volatile nondeterminism and stateful or dangerous functions. Dependence on time is not, by itself, a read-only safety violation.

## Temporal semantic classes

The reviewed stable-temporal family includes PostgreSQL `CURRENT_DATE`, `CURRENT_TIME`, `CURRENT_TIMESTAMP`, `LOCALTIME`, `LOCALTIMESTAMP`, `NOW`, `TRANSACTION_TIMESTAMP`, and `STATEMENT_TIMESTAMP`. `RANDOM` and `CLOCK_TIMESTAMP` remain volatile denials. `PG_SLEEP`, `SET_CONFIG`, advisory locks, and unknown functions remain fail-closed.

The SQLGlot PostgreSQL renderer required a small normalization correction for keyword-style `CURRENT_TIME`, `LOCALTIME`, and `LOCALTIMESTAMP`: SQLGlot otherwise emitted invalid empty-call syntax. This is a dialect-preserving serialization correction, not a policy relaxation.

## M1 responsibility

M1 governs read-only single-statement SQL, queryable catalogs/tables/columns, reviewed function semantics, and operational cost. It does not promise bit-for-bit result reproducibility across wall-clock contexts.

## Reproducibility

Successful execution now records a timezone-aware UTC execution timestamp and the PostgreSQL session timezone. These fields are post-generation metadata and never enter provider prompts or safety decisions. Temporal output is not rewritten.

## What remains denied

`RANDOM()` and `CLOCK_TIMESTAMP()` remain volatile nondeterminism boundaries. `PG_SLEEP()`, `SET_CONFIG()`, system/stateful functions, and unknown anonymous functions remain denied. The cost gate and multi-statement boundary are unchanged.

## LiveSQLBench replay

The frozen 180-case provider-free replay moved from 166 to 175 M1-accepted gold queries after the stable-temporal review. One cost rejection and four multi-statement/parser boundary cases remain. All 175 accepted gold queries executed successfully. Five returned empty results, which the unchanged evaluator does not classify as a successful self-comparison; therefore evaluator compatibility is 170 PASS and the milestone remains blocked pending a separate evaluator/infrastructure decision. No evaluator change was made here.

## Product boundary

The official denominator remains 180. The 175 accepted solutions are not model accuracy. This milestone changes only general M1 policy and execution provenance.

## Claim boundary

Stable temporal policy is supported by PostgreSQL semantics and unit/integration evidence, but the full milestone is not validated because the unchanged evaluator reports five empty reference results as failures. No provider or model calls were made.
