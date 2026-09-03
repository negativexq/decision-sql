# Security posture

## Implemented in M1 and M2

- PostgreSQL is the only configured database engine.
- The API uses `decision_reader`, a separate login role from the schema owner.
- `decision_reader` is `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, and `NOINHERIT`.
- The migration grants the reader `SELECT` on the demo tables and no write privileges.
- Schema bootstrap, migrations, and seed use the admin connection and are separate Compose jobs.
- M1 itself calls no LLM and exposes no public query execution endpoint. M2's optional provider path is internal and remains proposal-only.
- `SqlCandidate` is untrusted input; it contains no authorization state.
- `QueryPlan` is created only after deterministic policy and EXPLAIN/cost acceptance.
- `QueryExecution` is the bounded outcome of an accepted plan; proposal, authorization, and execution are never treated as the same state.
- M2 provider output is converted to `SqlCandidate(source=LLM)` before any M1 call; providers cannot access the reader engine, admin connection, or credentials.
- Health performs `SELECT 1` through the configured reader connection.
- Candidate SQL is parsed with `sqlglot` using the PostgreSQL dialect and exactly one statement is required.
- Only `SELECT` ASTs, including SELECT-based CTEs, are accepted.
- Tables must exist in the explicit queryable catalog; non-public schemas, unknown tables, and PostgreSQL system catalogs are rejected.
- `customers.external_key` is marked internal and non-queryable; wildcard projections that would expose it are rejected.
- PostgreSQL functions use an analytical allowlist.
- EXPLAIN runs only after AST/object policy acceptance and before candidate execution.
- The supported service API is `plan(SqlCandidate)` followed by `execute(QueryPlan)`. Raw SQL, rejected plans, and copied/foreign plans are rejected at the execution boundary.
- Context sent to M2 providers is derived from queryable catalog metadata only. Non-queryable columns such as `customers.external_key`, forbidden catalogs, hidden objects, sample values, and secrets are excluded.
- EXPLAIN and execution use the reader connection in a read-only transaction.
- Plan rows, plan cost, returned rows, and statement timeout are bounded by settings.
- Tracing has bounded stage spans and does not record result rows or full candidate SQL.

## M2.5 grounding boundary

`QueryIntent` is not an authorization artifact. It is an untrusted model
interpretation of the bounded schema context. A valid-looking intent cannot
authorize a write, a hidden table, or a sensitive column. The SQL proposal is
still converted to `SqlCandidate(source=LLM)` and must pass the unchanged M1
parse, AST policy, reader validation, EXPLAIN, cost gate, and execution path.

M2.5 intentionally has no SQL repair loop: one intent call and one SQL call are
made, and any resulting candidate is handled by M1 or returned as a typed
failure.

## Required before query execution

The following are intentionally not claimed as implemented:

- Server-owned tenant predicates and authorization policy evaluation beyond the application catalog.
- PostgreSQL RLS where appropriate.
- Result semantic validation and answer grounding.
- Full Jaeger deployment/exporter verification.

These controls are deterministic code and are covered by unit and Docker-backed PostgreSQL adversarial tests. Prompt instructions are not a security control. M1 accepts candidate SQL directly; M2-generated candidates still pass through the same M1 control plane.

## Local credentials

Compose credentials are development-only example values. Production secrets must come from the deployment secret/configuration system and must never be committed.
