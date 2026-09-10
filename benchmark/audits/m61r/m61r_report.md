# M61R — dbt ACME local first-party reproduction

## Verdict

`M61R_DBT_ACME_LOCAL_REPRODUCTION_COMPLETE`

Decision-SQL was evaluated locally on the exact first-party dbt ACME question set: **82/220 (37.27%)** end-to-end execution-equivalent observations. The run used the frozen Candidate C contract, one fresh provider call per observation, and zero retries.

This is a **local first-party reproduction**, not an official dbt Cloud or dbt Semantic Layer benchmark run. The official remote backend remains credential-gated in historical M61.

## Frozen scope and provenance

- 11 exact questions × 20 independent observations = 220 calls.
- Candidate C: `3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587`.
- Canonical builder: `ff695c5a4f9b26ffe9d88f30ee9c4a917c90e670e72b70a7b3739a9ed41b8a23`.
- Local backend: PostgreSQL 16, database fingerprint recorded in `m61r_database_fingerprint.json`.
- First-party dbt questions, `ACME_small.ddl`, source CSV hashes, and comparator hashes are recorded in `m61r_source_manifest.json` and `m61r_evaluator_integrity.json`.
- No management/write tasks were sent; Decision-SQL remains intentionally read-only.

## Prelive gates

The local database was rebuilt twice with identical fingerprints. All 11 gold SQL statements executed, dbt comparator reflexivity passed 11/11, repeated gold results were deterministic 11/11, and the Decision-SQL candidate-path canary passed 11/11. Provider-visible request provenance passed 11/11 before acquisition; provider calls before the gate were 0.

Gold SQL, gold results, expected outputs, and prior responses were evaluator-only and were not present in provider requests. The only compatibility shim was the same deterministic `DATEDIFF("day", ...)` to date-subtraction normalization for gold and candidate SQL; no per-case SQL rewrite exists.

## Results

| Question | End-to-end | Raw proposal comparator | Unique SQLs |
| --- | ---: | ---: | ---: |
| `IQ_2b95bb37fe905f8c74b35d72e65f3d08` | 10/20 | 10/20 | 7 |
| `IQ_317ef7a6c42204b1933a510805c57e45` | 15/20 | 15/20 | 1 |
| `IQ_419b164c362608054523707eaf5a57f3` | 3/20 | 3/20 | 2 |
| `IQ_43461e701debb68f0f8ac1dceb945650` | 14/20 | 14/20 | 1 |
| `IQ_5b813c2c3d7949916d33976f2da518a0` | 0/20 | 0/20 | 0 |
| `IQ_6da3f7fcefcdd7453548c0956632a211` | 0/20 | 0/20 | 0 |
| `IQ_8f2344e8d1bba2cc39fcae99afd42e4a` | 20/20 | 20/20 | 1 |
| `IQ_985f30d50b59256c4b01d42901b0f9fb` | 0/20 | 0/20 | 0 |
| `IQ_a8f3cd24b58a0e3dc8a30c45d315b195` | 0/20 | 0/20 | 0 |
| `IQ_b2c56b1858b24690742a6b86af872858` | 0/20 | 0/20 | 0 |
| `IQ_f1b8ef62994d657eda300db1a4b71046` | 20/20 | 20/20 | 1 |

**Fully stable questions:** 2/11 (20/20).

Typed decisions were: `ANSWER` 89, `BLOCKED_AUTHORITY` 118, and `NEEDS_CLARIFICATION` 13. All 89 `ANSWER` observations executed through the deterministic runtime; the remaining observations were model-level blocks/abstentions. No runtime authority, policy, grain, cost, or execution rejection occurred in this corpus.

The deterministic failure decomposition is `MODEL_DECISION`/`MODEL_ABSTENTION` for model-level non-answers and `METRIC_SEMANTICS` for the seven answerable observations whose result did not match the retained dbt comparator. Detailed join and output-stability evidence is in the JSON artifacts.

## Architecture comparison boundary

The internal stable result (`83/90` Governed, `59/62` Answerable Runtime TSA) is a Decision-SQL-specific governed/counterfactual evaluation. The M61R result (`82/220`) is a local first-party dbt-compatible execution-result comparison. These are separate measurements; neither is a universal Text-to-SQL accuracy claim, and this local result is not an official dbt leaderboard score. PyDough and dbt Semantic Layer comparisons remain methodological context only because model, prompt, representation, execution backend, and runtime controls differ.

## Safety regression

The zero-model-call historical `telecom_15` replay remains `ANSWER` at the model boundary but `AUTHORITY_REJECTION / UNAUTHORIZED_RELATION` at runtime, with EXPLAIN calls 0, database connection calls 0, and execution calls 0. M61R did not alter this relation-level safety boundary.

## Integrity

Candidate C, internal benchmark semantics, runtime semantics, the dbt comparator, and M61 artifacts were unchanged. There were no retries, repairs, judges, routers, selectors, pass@K, case-specific SQL rewrites, or failed-observation reruns. Provider cost was not returned by the provider; token usage and latency are recorded where available.
