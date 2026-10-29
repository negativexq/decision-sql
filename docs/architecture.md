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
  (`known_values`), counted exactly on small tables and read from `pg_stats`
  on larger ones (whose estimates can miss a rare value, so the list is then
  not treated as complete); excluded columns, keys, long values,
  and columns whose names suggest personal data never do. Without sharing, code
  and date columns carry only their shape (`value_patterns`, digits as `9`).
- Every queryable column whose rows are at least 1% NULL, or empty or blank
  strings, carries those shares (`null_share`, `blank_share`), from the same
  statistics. A share is a count, not a stored value, so it does not wait for
  value sharing; a filter such as `<> 'x'` drops NULL rows but keeps blank ones,
  and the model cannot see that from the column's type.
- A short-valued text column whose values join several items with `,`, `;`, or
  `|` in at least 10% of rows carries that separator and share (`separator`,
  `separated_share`; `separated_by` in the compact form), such as a list of card
  types or a place and its country: an equality then matches only a whole value.
  The share is exact on a counted column and estimated from the most common
  values and the histogram otherwise; free text, whose median value is longer
  than 60 characters, is left out.
- Column descriptions come from the source's comments. An administrator who
  cannot write comments to the source can describe a column on the connection
  page instead (`column_notes`); the note replaces that column's description in
  the context. It explains a column and grants nothing.
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
| `EXPLORE` | One to three read-only probes before deciding | Each probe entered |
| `NEEDS_CLARIFICATION` | The question can be read more than one way | Bypassed |
| `BLOCKED_AUTHORITY` | The data is outside the governed context | Bypassed |
| `BLOCKED_POLICY` | The request would write or breach policy | Bypassed |

A non-answer carries an explanation; a clarification also carries the question
to ask and two to four readings, and a refusal names the missing joins.

**Context format**: the governed context reaches the model as compact text: one
line per column, names instead of ids, under the section names the contract uses
(`render_governed_context`). It carries the same facts as the canonical JSON in
about a quarter of the text; the JSON stays the audited and hashed form, and
`DECISION_SQL_CONTEXT_FORMAT=json` sends it to the model instead.

**Exploration**: before it answers or asks, the model may return `EXPLORE` with
one to three probes (`app/decision/exploration.py`), for example to see how a
value is spelled or whether a tie it would ask about occurs. Each probe goes
through every gate below under the run's authority and runs as the reader role;
its gates are not recorded as the answer's stages. The model then hears, per
probe, the row count and each column's distinct and NULL counts, and the rows
(at most 20, values cut to 80 characters) only when the connection shares stored
values and every column the probe reads is public and shared. A rejected probe
reports the runtime's reason, a failed one its error class. A probe that matched
no rows has its literals looked up like an empty answer (below): a literal
stored in exactly one other spelling is respelled and the probe runs again, and
the model hears `VALUE_RESPELLED` with the new counts (the rows only under the
same sharing rule), never the spelling itself; a literal the column does not
store is named as `VALUE_NOT_STORED`. Exploration has
`DECISION_SQL_EXPLORATION_ROUNDS` rounds (default 2); the request says whether
it is still open, and a model that still explores after the last round fails the
run with `EXPLORATION_FAILURE`. A `NEEDS_CLARIFICATION` or `BLOCKED_AUTHORITY`
returned before any probe ran goes back to the model once, asking it to check
whether the data or the context settles it; the model may return the same
decision, and if that call fails the original one stands. Repair rounds and
replays never explore. The
`exploration` trace stage records each probe's SQL, outcome, and row count.
`DECISION_SQL_EXPLORATION_ENABLED=false` turns it off.

**Reading check**: a final `NEEDS_CLARIFICATION` with two or three
interpretations gets one more call, asking for one probe per reading: the SQL
that answers the question under it. Each runs through every gate as a probe, and
the full results are compared (column count and rows, order ignored; a
truncated result counts as unchecked). When every reading returns the same rows,
the question does not change the answer on this data: the first reading's SQL
runs as the answer, and the `reading_check` trace stage records
`READINGS_AGREE` with the readings and their SQL. When the readings differ,
`DECISION_SQL_AMBIGUITY_MODE` decides: `assume` (the default) answers with the
model's first reading, returns it as `assumption`, and returns the other
readings with their SQL and rows as `alternatives`, which the result view offers
as a switch (`READINGS_DIFFER_ASSUMED`); `ask` keeps the clarification
(`READINGS_DIFFER`). A reading that could not be checked (`NO_SQL_PER_READING`,
`READING_NOT_RUN`) keeps the clarification in either mode. The model never sees
the rows; the audit log keeps the readings and their SQL in the trace, never the
alternatives' rows. Replays skip it, and `DECISION_SQL_READING_CHECK_ENABLED=false`
turns it off.

After that, these calls may follow, each recorded as its own trace stage. The
expansion has its own call; the repair round, execution error, empty result,
and percentage out of range share one:

- **Context expansion**: the model returned `BLOCKED_AUTHORITY` naming a table
  from `other_tables`. The runtime rebuilds the context from the catalog with
  that table and asks again.
- **Repair round**: the checks rejected the SQL for a reason the model can fix
  (parse error, unknown table or column, function outside the allowlist, cost
  limit, fan-out). The model gets the runtime's own rejection code and message,
  never database error text or rows. Authority, write, forbidden-relation, and
  catalog rejections are final.
- **Execution error**: the query failed while running with a database error
  class the author can fix (failed cast, division by zero, a scalar subquery
  with several rows, a type or function mismatch, a timeout). The model gets a
  fixed runtime sentence for that class, read from the SQLSTATE; the database's
  message never goes back. For a failed cast the sentence says that a `WHERE`
  filter does not protect a cast and shows a `CASE` guard built from allowed
  functions. Other failures are final.
- **Empty result**: the query ran and returned no rows, or one row of only
  zeros and NULLs, and the run has not used its repair round. The model hears
  that the result was empty, which filters compare with a value the column
  does not store, and that it may keep the query. It never sees rows of any
  query or the values a column stores.
- **Percentage out of range**: a column the model named as a percentage holds
  a value below 0 or above 100, and the run has not used its repair round. The
  model hears the column and that it left the range, never the value, and may
  keep the query when the question asks for a change that can exceed 100.

### Model-call budget

Every model call of a run is taken from an `AttemptBudget`
(`app/decision/budget.py`) before the provider is asked. Each step has its own
allowance, and a step with none left is refused rather than called:

| Step | Calls |
| --- | ---: |
| First decision | 1 |
| Context expansion | 1 |
| Look first (a question back or refusal before any probe) | 1 |
| Probe rounds | `DECISION_SQL_EXPLORATION_ROUNDS` (default 2) |
| Reading check | 1 |
| Repair round | 1 |

A step turned off in settings has no allowance, and a replay has none at all.
With the defaults a question makes at most 7 calls; most make one. A failed
call still counts, so a repair call that fails uses the repair round. The
response stage records `model_calls` and `model_call_limit` for every run.

`DecisionSqlApplication.run` (`app/decision/service.py`) opens the run with
the context and the first decision, then hands a `DecisionState`
(`app/decision/state.py`) through the stages in order: admission, context
expansion, look first, exploration, reading check, then the query (plan,
repair, execute, result check, response). Each stage either moves the state on
or returns the finished result. The runtime's feedback to the model and the
result checks live in `app/decision/feedback.py`.

The contract also fixes the shape of an answer: percentages on a 0 to 100
scale with `percent` in the alias, yes/no as `YES` or `NO`, named fields in
their own columns, and every row tied for a highest or lowest value rather
than one picked by `LIMIT 1`.

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
  stored value only in case, spacing, punctuation, or the zero padding of a
  code of digits (`1611430131177` for `01611430131177`) takes the stored
  spelling. A literal that matches nothing is reported; when the model never
  saw the column's values it gets one repair round that names its guess, not
  the stored values, and the answer notes that the filter matches no rows.
  This works whether or not values are shared with the model.
- **NULL ordering**: PostgreSQL sorts NULL above every value, so a
  descending order that decides which rows come back (with `LIMIT`, `OFFSET`,
  `FETCH`, `DISTINCT ON`, or in a ranking window such as `RANK()`) would put
  rows with no value first: the "greatest" player would be one with no rating.
  Such an order gets `NULLS LAST`. A query that writes `NULLS FIRST` anywhere
  is left as written, and so is an order by `COUNT`, which is never NULL.
- **Single precision**: PostgreSQL's `SUM` over a `real` column adds in
  single precision and keeps about six significant digits, so a total of
  14009.34 comes back as 14009.3; a cast of a `real` column to `numeric`
  rounds each value the same way (`12685.04::real::numeric` is `12685`).
  Inside a `SUM`, a `real` column the author did not cast is read as
  `double precision`, and a `real` column cast to `numeric` goes through
  `double precision` first (`app/sql/precision.py`).
- **Plan and cost**: `EXPLAIN` in a read-only transaction; plan rows and cost
  must stay under the configured limits. A planner estimate does not predict run
  time well: on the BIRD databases, gold queries estimated at cost 100,000 to
  510,000 ran in 10 to 580 ms, and one estimated at 116,000 rows returned 3.
  The limits therefore refuse only plans that are clearly out of scale
  (10,000,000 by default), and the statement timeout bounds the real run time.
  A missing index on a join key is a common reason for a high estimate; the
  fix for that belongs to the database administrator, not to the query.

After execution, an empty result (no rows, or one row of only zeros and
NULLs) is checked once. For text columns whose values are not all known, the
runtime looks each literal up in the database as the reader role, with a short
timeout (`DECISION_SQL_VALUE_PROBE_TIMEOUT_MS`). A literal stored in exactly one
other spelling (case, spacing, punctuation, zero padding) takes that spelling, and the query
runs again through every gate without a model call. Otherwise the empty
result goes to the repair round above. Confidential columns are not looked up.
A result with rows is checked for a percentage outside 0 to 100, which gets the
repair round and stays flagged in the UI if the model keeps it, and for rows that
tie: when the query keeps every row equal to an overall `MAX` or `MIN`, or ranked
first by an unpartitioned `RANK()`, and more than one comes back, the trace
records `TIED_ROWS` and the UI says the rows share that value.
`DECISION_SQL_RESULT_CHECK_ENABLED=false` turns these checks off.

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
  columns; `LIKE` and functions such as `LOWER(column)` are not checked.
  Columns whose values are not all known are looked up only after a query
  comes back empty.
- Repair is one round, used whether or not the call succeeds. A second
  rejection is final.
- A report filter does not follow relationships: a filter on `regions.name`
  leaves a card that reads only `orders` unfiltered (the card says so). It
  narrows every SELECT that reads its table, including a subquery that
  computes a total to compare against.
- A query with a RIGHT or FULL JOIN is not filtered.

## Workspace services

### Conversations

A question on Ask can follow up on the one before it: the request names the
earlier run (`follow_up_of`), which must be on the same connection, and the
model gets the new question with the same `Earlier answer` a report follow-up
sends. The audit log links each run to the one it built on, and the sidebar
lists the user's recent conversations from it; opening one loads the chain of
runs (`GET /api/runs/{id}/thread`). Runs read back from the log have no rows.

`POST /api/playground/query/stream` answers like `/api/playground/query` but
sends server-sent events as the run goes: `working` when a model call starts,
`stage` when the trace records a stage (name, status, reason code; no SQL or
rows), then the run record. The UI draws the gate path from them.

Each executed answer carries an explanation read from the proposed SQL by
`app/sql/explain.py`, not asked of the model: the tables it reads, its
aggregates, its conditions (a column compared with literals), grouping,
ordering, and limit. A condition can be changed or, when it is ANDed into a
WHERE or JOIN ON, removed (`POST /api/runs/{id}/edit`). The server takes the SQL
from the stored run, never from the client; the client names a condition by
number and sends values, which become literals of the kind the condition had
(a number is checked, a date is parsed, text is quoted by the parser). The
edited SQL runs as `EDITED_QUERY` without a model call, through every gate, and
the audit log records which run it came from (`edited_from`). A follow-up on an
edited run tells the model that its conditions were changed by hand.

An executed answer can be added to a report the user may edit on the same
connection, or to a new one (`POST /api/reports/{id}/cards`). The client sends
only the run id, chart type, and title; the card is placed under the report's
other cards and pins the run's executed SQL from the audit log, as any card
does. `PUT /api/reports/{id}/title` renames a report for everyone who can open
it.

The sidebar's names and pins are the user's own (`decision_meta.marks`, at most
500 per person): `PUT /api/marks/{kind}/{id}` renames a conversation or pins a
conversation or report to the top for that person only. A mark grants nothing
and no gate reads it.

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
why on its card. Heading cards start a section; text cards hold notes in a
small Markdown subset rendered as text, never HTML; chart cards carry a
description, a chart type (number, bars, columns, line, area, pie, stacked
columns, lines by series, scatter, table), optional category, value, and
series columns, and display settings: number format
(number, currency, percent, decimals), row order, how many rows to show, and
for a number card a comparison with its second row or a target. Display
settings change how rows look, never the query or which rows it returns.

Passing the gates says a saved query may still run, not that it still means
what it meant. A column redescribed, retyped, renamed, hidden, or marked
sensitive can leave the SQL valid and the chart misleading. So a card also keeps
a basis: a fingerprint of each table its SQL reads, taken from the catalog when
the card is pinned (descriptions, types, sensitivity, queryability, keys and
relationships; stored values are left out because they change with the data).
A refresh names the tables whose fingerprint differs, the card says so above
its chart, and an editor who checked it takes the current tables as the new
basis.

A report has an owner, editors, and viewers, and is either private to them or
open to the whole workspace. Viewers open and refresh; editors also change
cards; only the owner changes who may open it or deletes it. A report someone
cannot open reads as missing. Every refresh runs as the connection's reader
role and records the person who asked, so all readers see the same data:
sharing a report shares what that connection can read.

Filters narrow a whole report. An editor makes a filter of a catalog column,
either a list of values or a date range; a reader's picks stay in their
browser and travel with each refresh. The server adds the predicate to every
SELECT of a card's pinned SQL that reads the column's table directly (in the
join condition when the table is LEFT JOINed, so unmatched rows stay), and the
narrowed SQL goes through every gate: the value check gives each picked value
its stored spelling, and the audit log records the SQL that ran. Values come
from the reader as quoted literals built by the parser, never as SQL text. The
list of values to pick from is read by a gated, audited `SELECT DISTINCT`. A
card whose SQL does not read the table is left as it is and says so.

Any reader can follow up on a card ("Which region drove this?"). The model gets
the new question with an `Earlier answer`: the card's question and the SQL the
model proposed for it, and the context details the tables that SQL read. The
earlier SQL grants nothing; the answer runs through every gate and an editor
can add it as a new card.

A snapshot (`decision_meta.report_snapshots`) keeps what a report showed at one
moment: every chart card refreshed without filters, with up to 500 rows each,
and the 30 most recent per report. Any reader can take one; an editor can also
set the report to take one every hour, day, or week. A scheduler loop in the
API process (`DECISION_SQL_REPORT_SCHEDULER`, on by default; turn it off on all
but one worker if you run several) claims a due report with a conditional
update before refreshing it, so two workers never take the same one, and after
downtime it moves on from now rather than catching up. Scheduled runs are
audited as `scheduler`. Snapshot rows passed the checks of the day they were
read, so each card also keeps its basis then: opening a snapshot whose tables
are described differently now shows the card's outcome but holds back its rows.

A reader can download a card's rows as CSV, exactly as the query returned them
(the rows already on screen, so no new query and nothing beyond what the card
shows); text that would start a spreadsheet formula is prefixed with `'`.
Printing, or saving as PDF from the print dialog, lays the grid out in bands
that keep the screen layout, so a page breaks between rows of cards, not
through one, and stamps the time it was printed.

Editors can undo and redo their changes during a session, and duplicate a
card. A copy shares its original's run, so the server gives it the original's
pinned SQL and basis rather than reading the tables afresh: copying a card
does not quietly accept tables that changed since.

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
| `app/decision` | The stages of a run, its model-call budget, and the runtime's feedback to the model |
| `app/generation` | The decision contract and the model provider |
| `app/catalog`, `app/retrieval`, `app/governance` | Catalog, context selection, and the model-visible context |
| `app/sql` | Parsing, policy, authority, the plan/execute service, and the plain-language explanation of a query |
| `app/semantics` | Grain safety |
| `app/execution` | `EXPLAIN` cost gate and the read-only reader |
| `app/metadata` | Audit log, connections, connectors, and secrets |
| `app/observability` | Run traces and OpenTelemetry |
| `app/db`, `alembic`, `demo` | Demo schema, migrations, and deterministic seed |
| `ui` | React workspace |
