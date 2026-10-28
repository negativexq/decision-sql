# Architecture

Decision-SQL answers a natural-language question with one typed model decision
and lets deterministic code decide whether any SQL runs. The model never holds
credentials, authority, or an execution tool.

## Request path

```text
question
  -> governed context        app/retrieval, app/governance, app/catalog
  -> one model decision      app/generation (decision contract v2)
  -> context expansion       app/decision (once, only for a table the model names)
  -> decision admission      app/decision
  -> SQL parse               app/sql/parser.py
  -> global policy           app/sql/policy.py
  -> execution authority     app/sql/authority.py
  -> grain safety            app/semantics
  -> EXPLAIN + cost gate     app/execution/cost.py
  -> read-only execution     app/execution/reader.py
  -> audit record            app/metadata/store.py
```

Every stage is recorded in a run trace (`app/observability/run_trace.py`). The
UI shows the same ten stages as the check strip above each answer.

### 1. Governed context

The catalog (`app/catalog/default.py`) lists queryable tables, columns,
relationships, and the business meaning of key columns, for example which
column is order revenue and which timestamp drives monthly trends.

`SchemaContextResolver` decides what the model sees:

- Schemas with at most `DECISION_SQL_FULL_CONTEXT_MAX_TABLES` queryable tables
  (default 12) are sent whole. Keyword retrieval only matches English tokens
  and can drop the measure or dimension a question needs, so small schemas skip
  it.
- Larger schemas use bounded retrieval: top-k tables by keyword relevance,
  relationship paths between them, up to `DECISION_SQL_CONTEXT_NEIGHBOR_TABLES`
  (default 6) tables one relationship away, and a per-table column cap. The
  remaining tables are listed by name in `other_tables`. A name grants nothing;
  the model can only ask for the table (see below).
- For saved PostgreSQL connections, relationships an administrator declared are
  merged in like foreign keys, and categorical text columns carry their stored
  values (`known_values`), read from `pg_stats`. Keys, long values, and columns
  whose names suggest personal data never get values.
- The policy section lists the SQL functions the runtime accepts, from the same
  allowlist the policy gate uses.

Non-queryable tables and columns never enter the context. The context is also
the request's authority envelope: only relations in it may be executed.

### 2. One model decision

`OpenAICompatibleProvider` makes exactly one chat-completions call with a strict
JSON schema. The model returns one of:

| Decision | Meaning | SQL runtime |
| --- | --- | --- |
| `ANSWER` | Read-only SQL is proposed | Entered |
| `NEEDS_CLARIFICATION` | The question can be read more than one way | Bypassed |
| `BLOCKED_AUTHORITY` | The data is outside the governed context | Bypassed |
| `BLOCKED_POLICY` | The request would write or breach policy | Bypassed |

A non-answer carries an explanation; a clarification also carries the question
to ask and two to four readings, and a refusal names the missing joins.

At most one more call follows, recorded as its own trace stage:

- **Context expansion**: the model returned `BLOCKED_AUTHORITY` naming a table
  from `other_tables`. The runtime rebuilds the context from the catalog with
  that table and asks again.
- **Repair round**: the checks rejected the SQL for a reason the model can fix
  (parse error, unknown table or column, function outside the allowlist, cost
  limit, fan-out). The model gets the runtime's own rejection code and message,
  never database error text or rows. Authority, write, forbidden-relation, and
  catalog rejections are final.

Replays of recorded decisions never get either round. The system prompt is
pinned by `production_decision_contract_hash()`, so changing it is a visible
contract change.

### 3. Deterministic gates

An `ANSWER` is still only a proposal. `SqlSafetyService` turns it into a
`QueryPlan` only after:

- **Parse**: exactly one PostgreSQL statement, parsed with `sqlglot`.
- **Policy**: `SELECT` only, public schema only, no system catalogs, an
  analytical function allowlist, no wildcard over non-queryable columns.
- **Authority**: every referenced relation must be in the request's context.
- **Grain**: aggregates must not fan out across a parent-child join. Grain
  facts come from the source catalog itself: tables with a primary key are
  entities, foreign keys are many-to-one edges, and numeric non-key columns are
  measures at their table's grain. Summing a parent's column across its child
  rows is stopped at planning time. Declared relationships count as foreign
  keys. Tables without a primary key, and joins that are neither, are not
  checked.
- **Plan and cost**: `EXPLAIN` in a read-only transaction; plan rows and cost
  must stay under the configured limits.

Execution runs only an accepted plan, on the `decision_reader` role, in a
`READ ONLY` transaction with a statement timeout and a result row cap.

## Workspace services

### Audit log

Every run is stored in `decision_meta.audit_runs` with the actor, question,
model decision, SQL, outcome, timing, model context, and trace. Result rows are
not stored. The actor comes from the `X-Decision-Actor` header and is
self-declared until SSO is added.

### Connections

`app/metadata` manages saved database connections:

- Connector specs for PostgreSQL, MySQL, SQL Server, Trino, Snowflake, and
  BigQuery declare their fields and driver package.
- A preflight connects with the given details, checks the schema, counts
  tables, and warns when the user can write. Errors are classified to the field
  to fix (host, port, username, password, database, schema, SSL).
- A connection is saved only after a passing preflight. Passwords are encrypted
  with Fernet (`DECISION_SQL_SECRET_KEY`) and never returned.
- Drivers are optional extras. With `DECISION_SQL_ALLOW_DRIVER_INSTALL=true`
  an operator can install one connector's allowlisted package from the UI.

Questions run on the governed source and on saved PostgreSQL connections.
Saved connections to other engines can be tested and browsed; running questions
on them needs dialect-aware policy, read-only enforcement, and cost gates for
each engine.

Declared relationships (`decision_meta.declared_relationships`) are checked
before they are saved: both columns exist, their types are compatible, the
target column holds unique values, and the link is not a second path to
another key of a row the source table already reaches. Suggestions come from
column names, types, and unique keys, with the share of sampled values found in
the target.

### Metadata isolation

Audit records and connections live in the `decision_meta` schema, written with
the admin role. `PUBLIC` has no grant on it, the reader role cannot read it, and
SQL policy only admits `public` relations, so model SQL cannot reach audit data
or stored secrets.

## Code map

| Path | Owns |
| --- | --- |
| `app/api` | HTTP routes |
| `app/operator` | The workspace facade the UI calls |
| `app/decision` | Admission of the model decision into the SQL runtime |
| `app/generation` | The decision contract and the model provider |
| `app/catalog`, `app/retrieval`, `app/governance` | Catalog, context selection, and the model-visible context |
| `app/sql` | Parsing, policy, authority, and the plan/execute service |
| `app/semantics` | Grain safety |
| `app/execution` | `EXPLAIN` cost gate and the read-only reader |
| `app/metadata` | Audit log, connections, connectors, and secrets |
| `app/observability` | Run traces and OpenTelemetry |
| `app/db`, `alembic`, `demo` | Demo schema, migrations, and deterministic seed |
| `ui` | React workspace |
