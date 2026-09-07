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
withheld/empty in the public release to limit answer leakage. Consequently,
this milestone does not claim an accuracy score or reference-answer audit.

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
matching the official `ex_base` behavior. Official custom test-case Python and
management semantics are not claimed from the public artifact because all
public test-case/reference fields are empty.

Known-good and wrong synthetic result controls passed the adapter tests. No
benchmark score was produced.

## External knowledge

All 270 public case-level knowledge fields are empty. Database HKB files are
hashed and preserved as upstream metadata; they are not automatically injected
into runtime context. A future complete, legitimately obtained task artifact
may supply its own `external_knowledge` exactly as provided, but it must remain
untrusted context rather than SQL authority.

## Schema context

The adapter uses a deterministic complete per-database PostgreSQL metadata
renderer for preflight statistics only. It normalizes schema, table, column,
PK, and FK endpoint ordering and emits explicit relationship endpoints. It does
not use gold SQL to select tables, retrieve values, or prune schema. Future
DIRECT baseline behavior must explicitly document whether this complete
context remains within the existing generic bounded context policy.

Observed aggregate catalog size is 175 tables and 2,286 columns across the 18
databases. Context statistics and per-database hashes are in the manifest.

## M1 compatibility

`REFERENCE_M1_COMPATIBILITY = NOT_TESTABLE_FROM_PUBLIC_ARTIFACTS`: the public
JSONL has no reference SQL. No hidden answers were scraped and no reference SQL
was sent to a provider. A complete official GT/test-case artifact is required
for the reference-M1 and reference-execution audits.

## Execution/evaluator sanity

The official PostgreSQL catalog was read successfully for all 18 databases.
The integration includes a PostgreSQL integration test (opt-in) and bounded
known-good/wrong evaluator controls. No generated SQL was executed in this
milestone, and no provider object was constructed.

## Context-size statistics

The preflight result records min, median, P95, maximum, and mean character
counts for question, knowledge, and schema context. Token counts are clearly
labelled as a deterministic four-characters-per-token estimate, not provider
telemetry.

## Frozen manifest

The tracked files are:

- `evaluation/fixtures/livesqlbench_base_lite_preflight_manifest.json`
- `evaluation/fixtures/livesqlbench_base_lite_preflight_result.json`

The manifest freezes the source commits/hashes, image digest, exact SELECT
population, database identities, renderer/evaluator identities, disabled
capabilities, and an outcome-blind 18-case pilot (one lexicographically first
SELECT case per database).

## Future DIRECT baseline

Only after a reviewed preflight should the next milestone run one fresh direct
SQL provider call per eligible case: 180 calls for the current public
population, beginning with the frozen 18-case pilot. The future baseline must
use the current model/provider/settings, legitimate case knowledge, current
schema context, unchanged M1, the existing read-only executor, and the
official evaluator when complete test cases are legitimately available. It
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
