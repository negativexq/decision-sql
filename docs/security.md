# Security posture

## Implemented in M0

- PostgreSQL is the only configured database engine.
- The API uses `decision_reader`, a separate login role from the schema owner.
- `decision_reader` is `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, and `NOINHERIT`.
- The migration grants the reader `SELECT` on the demo tables and no write privileges.
- Schema bootstrap, migrations, and seed use the admin connection and are separate Compose jobs.
- No LLM is called, no generated SQL is accepted, and no query execution endpoint exists.
- Health performs `SELECT 1` through the configured reader connection.
- Tracing has a narrow accessor and does not record result rows by default.

## Required before query execution

The following are intentionally not claimed as implemented:

- `sqlglot` AST validation for one statement and the allowlist of read-only constructs.
- Explicit rejection of writes, DDL, procedural SQL, catalog access, and unsafe functions.
- Server-owned tenant predicates and authorization policy evaluation.
- PostgreSQL `transaction_read_only`, RLS where appropriate, statement timeout, and hard result limits.
- `EXPLAIN` cost gating before execution.
- Bounded, privacy-aware query/result telemetry and Jaeger export configuration.

These controls must be implemented as deterministic code and tested with adversarial inputs before any Text-to-SQL route is enabled. Prompt instructions are not a security control.

## Local credentials

Compose credentials are development-only example values. Production secrets must come from the deployment secret/configuration system and must never be committed.
