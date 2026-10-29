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
  -> stored value check      app/sql/values.py
  -> EXPLAIN + cost gate     app/execution/cost.py
  -> read-only execution     app/execution/reader.py
  -> audit record            app/metadata/store.py
```

Every stage is recorded in a run trace (`app/observability/run_trace.py`). The
UI shows eleven of them as the check strip above each answer.

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
  merged in like foreign keys. When an administrator turns on value sharing for
  the connection, categorical text columns carry their stored values
  (`known_values`), read from `pg_stats`; excluded columns, keys, long values,
  and columns whose names suggest personal data never do. Without sharing, code
  and date columns carry only their shape (`value_patterns`, digits as `9`).
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
  Equality between columns of two tables, in a `JOIN`, a `WHERE`, or a
  correlated subquery, must match a foreign key or declared relationship:
  `orders.id = customers.id` is rejected even though both tables are
  authorized, and the rejection names the relationships that do link them.
- **Authority**: every referenced relation must be in the request's context.
- **Grain**: aggregates must not fan out across a parent-child join. Grain
  facts come from the source catalog itself: tables with a primary key are
  entities, foreign keys are many-to-one edges, and numeric non-key columns are
  measures at their table's grain. Summing a parent's column across its child
  rows is stopped at planning time, and so is `SUM(DISTINCT ...)` used to
  hide the repeated rows. Declared relationships count as foreign keys.
  Tables without a primary key, and joins that are neither, are not checked.
- **Stored values**: for columns whose complete value set was read from
  statistics, a text literal compared with the column that differs from one
  stored value only in case, spacing, or punctuation takes the stored
  spelling. A literal that matches nothing is reported; when the model never
  saw the column's values it gets one repair round that names its guess, not
  the stored values, and the answer notes that the filter matches no rows.
  This works whether or not values are shared with the model.
- **Plan and cost**: `EXPLAIN` in a read-only transaction; plan rows and cost
  must stay under the configured limits.

Execution runs only an accepted plan, on the `decision_reader` role, in a
`READ ONLY` transaction with a statement timeout and a result row cap. The
audit log keeps both the model's SQL and the text the database received
(`executed_sql`), with one sentence for each logic change a gate made
(`sql_changes`).

### Known gaps

- Columns of CTEs and derived tables are not traced to their source tables.
  A fan-out hidden inside a CTE, or a CTE column joined on a non-key, passes
  the grain and join checks.
- A table joined with itself is not checked against relationships.
- The value check covers `column = 'text'`, `<>`, and `IN` lists on bare
  columns; `LIKE`, functions such as `LOWER(column)`, and columns whose
  values are not all known are not checked.
- Repair is one round. A second rejection is final.

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

Value sharing (`decision_meta.value_sharing`) is a per-connection choice, off
by default, to send stored spellings of categorical columns to the model, with
columns an administrator excludes. The connection page shows what each column
would share. The server-wide `DECISION_SQL_VALUE_EXAMPLES=false` turns all
value hints off.

### Reports

A report (`decision_meta.reports`) is a page of cards on a twelve-column grid
for one connection. A chart card starts as a question answered the usual way,
model and all; the card then pins the SQL that run executed, read from the
audit log by run id, never from the client. Opening or refreshing a report runs
each pinned query again as a `SAVED_QUERY` run: no model call, every gate on
the current schema, and an audit record. A query that no longer passes shows
why on its card. Text cards hold headings and notes; chart cards carry a
description, a chart type (number, bars, columns, line, area, pie, table), and
optional category and value columns.

Passing the gates says a saved query may still run, not that it still means
what it meant. A column redescribed, retyped, renamed, hidden, or marked
sensitive can leave the SQL valid and the chart misleading. So a card also keeps
a basis: a fingerprint of each table its SQL reads, taken from the catalog when
the card is pinned (descriptions, types, sensitivity, queryability, keys and
relationships; stored values are left out because they change with the data).
A refresh names the tables whose fingerprint differs, the card says so above
its chart, and a reader who checked it takes the current tables as the new
basis.

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
