# M61 — dbt ACME External Architecture Benchmark

## Verdict

`M61_BLOCKED_DBT_EXECUTION_CREDENTIALS`

M61 resolved the exact first-party 11-question ACME subset and froze its
provenance, but stopped before evaluator preflight. The official dbt harness
requires a Semantic Layer Flight SQL backend configured with `SL_URL`,
`ENVIRONMENT_ID`, and `DBT_SL_SERVICE_TOKEN`. None of those credentials were
available in the execution environment. No provider request was built and no
model call was made.

## Scope resolved before the block

The official `dbt-labs/dbt-llm-sl-bench` checkout was pinned at
`a29f2429b1bb38cee9d591892299e96f1e197ff9`. Its published benchmark pages and
`BaseConfig.selected_challenges` identify the same ordered 11 insurance
questions. The TTL contains 86 SQL query records and 43 inquiry records, so
M61 did not treat the whole TTL as the benchmark. Each selected question was
resolved to its single `QandA:Inquiry` identifier and its `dwt:SqlQuery` gold
identifier; the sibling SPARQL query was excluded from the SQL evaluation.

The raw schema variant is `ACME_small.ddl`, matching the official base config.
The exact ordered set is in
[`m61_question_set.json`](m61_question_set.json).

Pinned source hashes are in
[`m61_external_source_manifest.json`](m61_external_source_manifest.json).
The historical Bodo/dbt comparison is classified
`BENCHMARK_FAMILY_COMPARABLE`, not exact artifact comparable, because its
historical database state and run configuration were not independently frozen
in this audit.

## Evaluator gate

The retained official comparator is
`ComparisonService.compare_query_results` at SHA-256
`73a6e83202caa56b6e1964f5d53cb3f9f9929f29906b799c81a2698ed2d7cbea`.

The mandatory 11/11 gold execution, comparator reflexivity, repeated-gold
determinism, and candidate-execution-path canary were not run because the
required backend credentials were absent. M61 therefore does not claim that
the evaluator is operational and does not proceed with a 10/11 or partial
external run.

## Information boundary and planned adapter

The planned ACME adapter is schema-only, deterministic, question-independent,
and gold-blind. It would map tables, columns, declared keys, types, nullability,
and declared foreign keys into the existing governed context representation.
It would not infer business synonyms, metrics, or join paths from gold SQL.

Because the credential gate blocked the run before adapter activation, adapter
output and provider-visible requests were not created. The boundary contract
is recorded in [`m61_information_boundary.json`](m61_information_boundary.json)
and [`m61_schema_adapter.json`](m61_schema_adapter.json).

## Call accounting

| Category | Calls |
| --- | ---: |
| Calls before prelive gates | 0 |
| SELECT model calls | 0 |
| Management model calls | 0 |
| Retries | 0 |

No response or result corpus was created. No external SELECT score exists.
Management tasks remain outside Decision-SQL's intentionally read-only product
scope and were not sent to a model.

## Internal evidence boundary

The retained internal stable evidence remains `83/90 Governed` and `59/62
Answerable Runtime TSA`. These metrics are not replaced or numerically merged
with M61 because M61 produced no external observations and, even when run,
the dbt-compatible external result metric measures a different construct.

M60 remains immutable historical evidence with verdict
`M60_ABORTED_EVALUATOR_INTEGRITY_FAILURE`.

## Integrity

- Candidate C hash remains
  `3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587`.
- The retained canonical builder remains
  `benchmark.m51b_runner._provider_request` with source hash
  `ff695c5a4f9b26ffe9d88f30ee9c4a917c90e670e72b70a7b3739a9ed41b8a23`.
- Candidate C, internal benchmark semantics, runtime safety, and M60 artifacts
  were not changed.
- No credentials, secrets, provider payloads, or failed-case reruns were
  recorded.

## Reproduction requirement

A future recovery milestone must first supply the official dbt execution
credentials or document a fully reproducible first-party local equivalent,
then pass all evaluator gates for all 11 questions before any provider call.
