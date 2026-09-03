# M4.1 — Feature-Flagged Verified Query Memory Integration & Observability

M4.1 operationalizes the accepted Verified Query Memory capability for the
residual direct Text-to-SQL path. It does not retune retrieval or change the
M3 governed metric path. M1 remains the only SQL planning and execution
authority.

## Accepted M4 boundary

M4 established a frozen, server-owned corpus of 50 independently verified
examples:

- corpus ID: `decisionsql-demo-verified-query-memory`
- corpus version: `1`
- corpus SHA-256: `f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae`
- retriever: `m4-retriever-v1`, R1 lexical plus schema overlap
- K: `3`

M4's accepted result remains 15/48 to 29/48 on DEV and 10/32 to 17/32 on
HOLDOUT. M4.1 treats those retrieval and quality choices as immutable.

## Runtime boundary

The existing order is preserved:

```text
question
  -> governed metric routing
  -> governed M3 path, or residual direct path
  -> optional verified-memory runtime
  -> SQL generation
  -> M1 planning and policy
  -> read-only execution
```

Memory is never placed in front of governed metrics. Retrieved SQL is prompt
context only; it is never executed or parameter-substituted directly.

`VerifiedMemoryRuntime` is the small orchestration boundary. It receives the
existing direct service, the server-owned corpus, and typed settings. The
retriever and accepted prompt renderer are reused without duplication.

## Feature modes

Configuration is explicit and default-off:

```dotenv
DECISION_SQL_VERIFIED_QUERY_MEMORY_MODE=off
DECISION_SQL_VERIFIED_QUERY_MEMORY_SHADOW_SAMPLE_RATE=0.0
DECISION_SQL_VERIFIED_QUERY_MEMORY_SHADOW_EXECUTE=false
```

- `off`: the existing direct path is used; no corpus is loaded, retrieval is
  not called, and no memory prompt or provider call is added.
- `shadow`: the direct result remains authoritative. A sampled memory-assisted
  generation may run diagnostically. Shadow generation failures and result
  differences cannot affect the user result. Sampling is deterministic from
  the request correlation ID, or the question only when no ID exists.
- `on`: residual direct requests use one memory-assisted generation. Governed
  metric requests do not invoke memory. A no-hit or recoverable retrieval
  failure falls back to one ordinary direct generation.

Shadow execution is separately disabled by default. When enabled, its SQL is
still planned and executed through M1 and comparisons are only reported as
`RESULTS_EQUAL` or `RESULTS_DIFFER`; no hidden selector is used.

Unknown or missing memory hits are not repaired, reranked, or retried. A
memory provider failure in `on` mode follows the ordinary direct generation
error behavior without a hidden baseline retry.

## Provenance and observability

Successful or diagnostic memory decisions carry immutable bounded
`VerifiedMemoryProvenance` containing the corpus ID/version/full hash,
retriever version, frozen K, retrieved example IDs and scores, retrieval
latency, mode, sampling state, and bounded outcome/fallback values.

The route span adds bounded attributes for mode, enabled state, hit count,
corpus version/hash prefix, retriever version, K, outcome, sampling, fallback,
and shadow comparison. The memory runtime adds:

- `decision_sql.memory.retrieve`
- `decision_sql.memory.prompt`

Existing provider and M1 spans remain reused. Raw questions, memory questions,
SQL, result rows, full prompts, secrets, and authorization headers are not
trace attributes or memory provenance.

## Provider-free integration validation

M4.1 uses deterministic fakes for orchestration tests and makes no provider
calls. The new routing regression fixture is
[`evaluation/fixtures/m41_integration.json`](../evaluation/fixtures/m41_integration.json):
40 cases, 30 residual direct cases and 10 governed controls. Its frozen
canonical SHA-256 is
`31d1d3612ba8377ace6097ccb60dac79cf136a4c2d3af58596ef710d33b97461`.

The fixture validates route boundaries and is not a new quality benchmark. It
does not contain gold SQL. Unit tests cover OFF, sampled/unsampled SHADOW, ON
hits, no-hit fallback, retrieval failure, governed exclusion, provenance, and
M1 rejection of malicious generated SQL.

The provider-free integration gates passed. M4.1 classification:
**VERIFIED_MEMORY_INTEGRATION_ACCEPTED**. This validates the runtime boundary
and safety controls; it does not activate the feature in production.

## Limitations

M4.1 does not guarantee correctness, change the verified corpus, add
embeddings or rerankers, learn from production traffic, repair SQL, create
metrics, or solve temporal/value grounding. It does not activate memory in
the production default. Any rollout decision is a later explicit operational
step.
