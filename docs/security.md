# Security posture

## Implemented in M1

- PostgreSQL is the only configured database engine.
- The API uses `decision_reader`, a separate login role from the schema owner.
- `decision_reader` is `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, and `NOINHERIT`.
- The migration grants the reader `SELECT` on the demo tables and no write privileges.
- Schema bootstrap, migrations, and seed use the admin connection and are separate Compose jobs.
- No LLM is called, no LLM-generated SQL is accepted, and no public query execution endpoint exists.
- Health performs `SELECT 1` through the configured reader connection.
- Candidate SQL is parsed with `sqlglot` using the PostgreSQL dialect and exactly one statement is required.
- Only `SELECT` ASTs, including SELECT-based CTEs, are accepted.
- Tables must exist in the explicit queryable catalog; non-public schemas, unknown tables, and PostgreSQL system catalogs are rejected.
- `customers.external_key` is marked internal and non-queryable; wildcard projections that would expose it are rejected.
- PostgreSQL functions use an analytical allowlist.
- EXPLAIN runs only after AST/object policy acceptance and before candidate execution.
- EXPLAIN and execution use the reader connection in a read-only transaction.
- Plan rows, plan cost, returned rows, and statement timeout are bounded by settings.
- Tracing has bounded stage spans and does not record result rows or full candidate SQL.

## Required before query execution

The following are intentionally not claimed as implemented:

- Server-owned tenant predicates and authorization policy evaluation beyond the application catalog.
- PostgreSQL RLS where appropriate.
- Result semantic validation and answer grounding.
- Full Jaeger deployment/exporter verification.

These controls are deterministic code and are covered by unit and Docker-backed PostgreSQL adversarial tests. Prompt instructions are not a security control. M1 accepts candidate SQL only; no LLM-generated SQL execution path exists.

## Local credentials

Compose credentials are development-only example values. Production secrets must come from the deployment secret/configuration system and must never be committed.
