# Security posture

The model proposes; deterministic code decides. Prompt instructions are not a
security control, so every control below is enforced in code and covered by
tests.

## What the model can and cannot do

- The model receives the question and a governed context built from queryable
  catalog metadata. It never receives credentials, result rows, non-queryable
  columns (such as `customers.external_key`), audit records, or secrets.
- Stored values of a saved connection reach the model only after an
  administrator turns on value sharing for that connection, and never for the
  columns they exclude. The values come from an exact, bounded count on a small
  table or from `pg_stats` on a larger one, are limited to short categorical text, and skip
  keys and columns whose names suggest personal data. The model provider sees
  them with every question on that connection. Without sharing, the model sees
  only the shape of code and date columns (`9999-99-99`), which carries no
  stored value. The shares of NULL and blank rows of a column (when at least 1%),
  and the separator a text column's values join items with, come from the same
  statistics and are sent without sharing: they are counts and a punctuation
  mark, not values. `DECISION_SQL_VALUE_EXAMPLES=false` turns all of this off
  server-wide.
- It returns one typed decision. Its SQL is untrusted input
  (`SqlCandidate`) with no authorization state.
- Before deciding it may run up to three read-only probes per round, for a
  bounded number of rounds (two by default). A probe is untrusted SQL like an
  answer and passes every gate. What comes back is counts only (row count,
  distinct and NULL counts per column) unless the connection shares stored
  values and every column the probe reads is public and shared; then it also
  gets up to 20 rows with values cut to 80 characters, which the model provider
  sees. Rejections and failures come back as the runtime's own code, never
  database text. A probe that matched nothing is respelled like an empty answer;
  the model learns that a spelling was applied, not which one. The trace keeps
  each probe's SQL and row count, not its rows.
- It has no other tool calls. Besides exploration, extra requests follow only
  to add tables the model asked for from `other_tables`; to send back once a
  clarification or refusal returned before any probe; to ask for one SQL per
  reading of a clarification, whose results are compared on the server and
  never shown to the model; and for one repair round after a policy rejection,
  an empty result, or a percentage outside 0 to 100. Their SQL goes through every gate
  again, and repair feedback never quotes database error text. After an empty
  result the model learns only that the query matched no rows (or only zeros)
  and which of its literals the column does not store, never rows or stored
  values. After an out-of-range percentage it learns the column name only.
- A follow-up from a report card also sends the earlier question and the SQL
  the model proposed for it, recorded as `follow_up_of` in the audit log. It
  never sends the SQL that ran, which can hold stored values the runtime wrote
  in, nor the reader's filter picks, which are read from the data; a card whose
  answer was not proposed by the model has nothing to follow up on.

## SQL admission

- Exactly one statement, parsed with `sqlglot` in the PostgreSQL dialect.
- Only `SELECT` and `SELECT`-based CTEs; no `SELECT INTO` or locking clauses.
- Only `public` relations listed as queryable in the catalog; `pg_catalog`,
  `information_schema`, and other schemas are rejected.
- Functions must be on an analytical allowlist.
- Wildcards that would expose a non-queryable column are rejected.
- Every referenced relation must be in the request's governed context.
- Equality between columns of two tables must follow a foreign key or a
  declared relationship. Once two tables are joined that way, a further equality
  between them ANDed into the same SELECT (such as `trans.date = loan.date`) only
  narrows the authorized pairs and is allowed; under `OR`, or with the link only
  in an enclosing SELECT, it is still rejected.
- Aggregates that would fan out across a parent-child join are rejected,
  including `SUM(DISTINCT ...)` over such a join.
- `EXPLAIN` runs before execution; plan rows and plan cost must stay under
  `DECISION_SQL_MAX_PLAN_ROWS` and `DECISION_SQL_MAX_PLAN_COST` (10,000,000
  each by default). The estimate is a coarse guess, so these limits refuse only
  plans that are clearly out of scale; `DECISION_SQL_STATEMENT_TIMEOUT_MS` is
  what bounds how long an accepted plan runs, and a plan that reaches it is
  cancelled and returned to the model as `STATEMENT_TIMEOUT`.
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
- Report snapshots are the one place result rows are stored: up to 500 rows
  per card and 30 snapshots per report, in `decision_meta`, deleted with the
  report. A snapshot card whose tables were redescribed since (a column marked
  sensitive, say) is shown without its rows.
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
  self-declared. There is no authentication or SSO yet, so report sharing
  (owner, editors, viewers) organizes work but is not an access boundary.
- A report refresh runs as the connection's reader role, not as the viewer:
  every reader of a report sees what that connection can read.
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
