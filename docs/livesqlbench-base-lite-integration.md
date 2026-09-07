# LiveSQLBench Base-Lite PostgreSQL integration

## Why LiveSQLBench

LiveSQLBench Base-Lite is an independent PostgreSQL benchmark surface with 18
end-user databases and 270 tasks. Its official README describes 180 SELECT
tasks and 90 management/CRUD tasks, plus execution-based Soft EX evaluation.
It is complementary evidence; BIRD and Defog historical artifacts remain
unchanged.

Source: [official LiveSQLBench repository](https://github.com/bird-bench/livesqlbench)
and [official Base-Lite dataset card](https://huggingface.co/datasets/birdsql/livesqlbench-base-lite).

## Upstream version

This integration freezes the public dataset repository at
`507d7d98f477b9d6a990628ea021bd2501cfd6e2` and the evaluator repository at
`e15cd221267e06fabfaf6a3d4a69308280ce9a7c`. The source dataset is the public
`livesqlbench_data.jsonl`; its SHA-256 and all companion metadata-file hashes
are recorded in the preflight manifest.

The public artifact is licensed `CC BY-SA 4.0`. The official dataset card
states that `sol_sql`, `test_cases`, and case-level `external_knowledge` are
withheld/empty in the public release to limit answer leakage. The protected
artifact is therefore kept local-only and is never included in committed
fixtures.

## Dataset population

The adapter loads the upstream field names exactly, including the observed
`clean_up_sqls` spelling. It classifies only `category == "Query"` as the
Decision-SQL SELECT population. `Management` is excluded from execution and
any other category is an integrity failure.

Observed public population:

| Population | Count |
|---|---:|
| Upstream tasks | 270 |
| SELECT/Query eligible | 180 |
| Management/CRUD excluded | 90 |
| Unclassified | 0 |

All 18 referenced databases have the three official metadata assets and are
available as `<database>_template` databases in the pinned PostgreSQL image.

## SELECT-only boundary

Future generated SQL will use the existing Decision-SQL boundary:

```text
SqlCandidate -> unchanged M1 -> existing read-only executor -> evaluator
```

The adapter does not authorize SQL, alter M1, or add a benchmark-specific
execution path. Management rows are retained in the audit population but are
not silently converted into SELECT cases.

## PostgreSQL environment

The official image is
`docker.io/shawnxxh/bird-interact-postgresql:latest`, pinned in the manifest
by digest `sha256:1ae45d7aa5d64dd8eb82e4058f56b4b9625d5035b9b9dc0d2afa0295d9d3053c`.
The provider-free local audit used the official image on host port `55434`
with the upstream `root` credentials. The adapter introspects each
`<database>_template` database read-only and records server/catalog hashes.

## Evaluator semantics

The official Query default performs execution-based Soft EX comparison. It
normalizes dates, rounds numeric values to two decimal places, compares ordered
rows when `conditions.order` is true, and otherwise compares row sets. The
adapter implements that post-execution comparison and rejects empty results,
matching the official `ex_base` behavior. Query rows in the protected artifact
use the official default `conditions` path; their custom `test_cases` arrays
are empty. Management test-case execution is outside this SELECT-only
integration.

Known-good and wrong synthetic result controls passed the adapter tests. No
benchmark score was produced.

## External knowledge

The public case-level knowledge fields are empty, while the protected merge
contains non-empty official knowledge IDs for all 180 SELECT cases and all 90
Management cases. The IDs are preserved exactly in memory for future runtime
context; protected knowledge text/fields are never written to committed
artifacts. Database HKB files are hashed and preserved as upstream metadata.
Knowledge remains untrusted context rather than SQL authority.

## Schema context

The adapter uses a deterministic complete per-database PostgreSQL metadata
renderer for preflight statistics only. It normalizes schema, table, column,
PK, and FK endpoint ordering and emits explicit relationship endpoints. It does
not use gold SQL to select tables, retrieve values, or prune schema. Future
DIRECT baseline behavior must explicitly document whether this complete
context remains within the existing generic bounded context policy.

Observed aggregate catalog size is 175 tables and 2,286 columns across the 18
databases. Context statistics and per-database hashes are in the manifest.

## Protected GT artifact

The local-only file is:

```text
evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl
```

It is protected by `.gitignore`, is not tracked, and is merged with the public
270 rows strictly by `instance_id`. The protected artifact has 270 rows and
SHA-256 `eb3ddfff4371a5f7aeacefefbfc5ff039fbf5fec57ff69ae64c1b0378a3a94b2`.
Only this hash, field names, counts, and safe per-case statuses are committed.

## Gold SQL isolation

The protected merge uses separate in-memory runtime and evaluation objects.
Future provider input can contain only question, official external knowledge,
and schema context. `sol_sql`, `test_cases`, and reference results remain
offline-only. The protected preflight tests use sentinel values to verify this
boundary without committing protected content.

## M1 compatibility

The protected artifact makes the reference audit testable. All 180 gold SQL
statements parse as PostgreSQL, but the unchanged M1 accepted only 21, rejected
155 by existing policy, and returned 4 planning errors for multi-statement
gold strings. The 155 policy outcomes were 142 forbidden-function and 13
unknown-column outcomes; offline review identifies the 13 unknown-column
outcomes as CTE/derived-scope alias-resolution implementation defects. The four
multi-statement parse outcomes are an intentional M1 boundary. No allowlist,
threshold, parser, or M1 behavior was changed.

This is a final-preflight blocker: the official SELECT surface is not yet
compatible enough with the frozen Decision-SQL M1 boundary for an interpretable
provider baseline.

## Execution/evaluator sanity

The official PostgreSQL catalog was read successfully for all 18 databases.
The 21 M1-accepted reference solutions executed successfully and passed the
faithful Query evaluator. The remaining 159 references were not executed
because M1 correctly stopped them at its frozen boundary. No generated SQL was
executed in this milestone, and no provider object was constructed.

## Context-size statistics

The preflight result records min, median, P95, maximum, and mean character
counts for question, knowledge, and schema context. Token counts are clearly
labelled as a deterministic four-characters-per-token estimate, not provider
telemetry.

## Frozen manifest

The tracked files are:

- `evaluation/fixtures/livesqlbench_base_lite_preflight_manifest.json`
- `evaluation/fixtures/livesqlbench_base_lite_preflight_result.json`
- `evaluation/fixtures/livesqlbench_base_lite_final_manifest.json`
- `evaluation/fixtures/livesqlbench_base_lite_protected_preflight_result.json`

The final manifest freezes the public/protected source hashes, exact merged
SELECT population, database identities, renderer/evaluator identities, M1
configuration, disabled capabilities, and the unchanged outcome-blind
18-case pilot (one lexicographically first SELECT case per database).

## Future DIRECT baseline

The final preflight is currently blocked by M1 compatibility. No provider run
should begin until that blocker is resolved in a separate, explicitly reviewed
infrastructure/policy milestone. If cleared, the next run is one fresh direct
SQL provider call per eligible case, beginning with the unchanged 18-case
pilot. It must use current legitimate knowledge, current schema context,
unchanged M1, the existing read-only executor, and the official evaluator. It
must not enable ResultShape, QueryPlan, Window IR, governed routing, repair,
judge, retry-for-correctness, or routing.

## Human failure ledger and claim boundaries

The adapter reserves a future evaluation-only ledger containing case ID,
database, question, legitimate knowledge, generated SQL, offline reference
SQL/expectation, result summaries, M1 status, execution status, and correctness.
It makes no automatic root-cause claim or intervention recommendation.

This milestone is a data/environment/evaluator preflight, not a model
measurement. No provider/model/LLM calls, fresh SQL generation, prompt change,
production runtime change, or historical benchmark mutation occurred.
