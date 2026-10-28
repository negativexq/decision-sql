# Security posture

The model proposes; deterministic code decides. Prompt instructions are not a
security control, so every control below is enforced in code and covered by
tests.

## What the model can and cannot do

- The model receives the question and a governed context built from queryable
  catalog metadata. It never receives credentials, sample values, non-queryable
  columns (such as `customers.external_key`), audit records, or secrets.
- It returns one typed decision. Its SQL is untrusted input
  (`SqlCandidate`) with no authorization state.
- It has no tool calls, no retries, and no second request.

## SQL admission

- Exactly one statement, parsed with `sqlglot` in the PostgreSQL dialect.
- Only `SELECT` and `SELECT`-based CTEs; no `SELECT INTO` or locking clauses.
- Only `public` relations listed as queryable in the catalog; `pg_catalog`,
  `information_schema`, and other schemas are rejected.
- Functions must be on an analytical allowlist.
- Wildcards that would expose a non-queryable column are rejected.
- Every referenced relation must be in the request's governed context.
- Aggregates that would fan out across a parent-child join are rejected.
- `EXPLAIN` runs before execution; plan rows and plan cost must stay under
  `DECISION_SQL_MAX_PLAN_ROWS` and `DECISION_SQL_MAX_PLAN_COST`.
- Only an accepted `QueryPlan` can execute. Raw SQL, rejected plans, and copied
  plans are refused at the execution boundary.

## Database roles

- The API reads with `decision_reader`: `NOSUPERUSER`, `NOCREATEDB`,
  `NOCREATEROLE`, `NOINHERIT`, `SELECT` on demo tables only.
- `EXPLAIN` and execution run in a `READ ONLY` transaction with
  `search_path = public` and `DECISION_SQL_STATEMENT_TIMEOUT_MS`.
- Results are capped at `DECISION_SQL_MAX_RESULT_ROWS` and marked truncated.
- Migrations, seed, audit writes, and connection storage use the admin role.

## Audit log and connections

- Audit records and connections live in the `decision_meta` schema.
  `PUBLIC` has no grant on it and SQL policy only admits `public`, so model SQL
  cannot read it.
- Audit records keep the question, decision, SQL, outcome, timing, context, and
  trace, but not result rows.
- Connection passwords are encrypted with Fernet using
  `DECISION_SQL_SECRET_KEY` and are never returned by the API. Without a key,
  saving a connection with a password fails instead of storing plaintext.
- A connection is saved only after a passing preflight. Preflight sessions are
  read-only where the engine supports it, and the preflight warns when the user
  can write.
- Driver installation is off by default. When
  `DECISION_SQL_ALLOW_DRIVER_INSTALL=true`, only the allowlisted package for one
  connector can be installed, under a lock and a timeout.

## Known gaps

- The actor in the audit log comes from the `X-Decision-Actor` header and is
  self-declared. There is no authentication or SSO yet.
- There are no tenant predicates or PostgreSQL row-level security.
- Authority is relation-level; column- and join-path-level authorization is not
  complete.
- Questions run only on PostgreSQL: the governed source and saved PostgreSQL
  connections. Saved connections to other engines are tested and browsed, not
  queried.
- Tracing records bounded stage metadata, not rows or full SQL; exporter
  deployment is not verified.

## Local credentials

Docker Compose credentials and the demo secret key are development-only
values. Production secrets must come from a secret manager and never be
committed.
