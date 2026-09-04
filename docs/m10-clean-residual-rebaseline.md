# M10 — Clean Residual Rebaseline

## Pre-provider protocol decision

M10 is `M10_PROTOCOL_BLOCKED`. No M10 benchmark corpus was consumed, no
provider request was made, and no generated output was evaluated.

The required primary evaluator is `decision-result-evaluator-v2`. The frozen
M9.4 integration accepts ordinary `QueryExecution` values for V1, but V2
requires both generated and reference values to be `BoundResult` objects with
explicit semantic slot bindings. `TextToSqlResult.execution` is an ordinary
`QueryExecution`; the unchanged generation service does not produce a
`BoundResult` or any semantic binding metadata for generated columns.

The existing `BoundResult` construction appears only in evaluation fixtures
for M9.2/M9.3. Those bindings are frozen evaluation-oracle annotations. They
are not produced by the runtime SQL generator, the M1 executor, the M3
compiler, the M4 retriever, or the M9.4 versioned evaluation boundary.

## Binding gate

`M10_BINDING_PROTOCOL_READY = false`.

There is no predeclared deterministic mechanism that maps a newly generated
result's columns to benchmark-owned semantic slots before provider execution.
The current result contains physical/display column names and values only.
Aliases are not a sufficient semantic contract, and the existing V2 comparator
does not infer bindings from SQL, values, reference results, or question text.

The following alternatives were not used because they would invalidate the
baseline or change frozen semantics:

- manually binding generated columns after seeing provider output;
- matching columns by result values or reference tuples;
- searching projection subsets;
- adding SQL AST lineage or physical-column inference as a new unvalidated
  binding mechanism;
- adding an LLM judge or result-contract generation stage.

Consequently, running the proposed 200-case corpus would not be a clean M10
V2 rebaseline. It would either fail closed for every ordinary generated result
or require post-hoc adjudication of the generated bindings.

## Scope preserved

The repository remains at the M9.4 provenance anchor
`9fb6413811661d3320db83507f737feecff46657`. M1, M3, M4, routing, prompts,
runtime defaults, the V2 contract schema, and the frozen comparator were not
changed. M7, M8, M9, M9.1, M9.2, and M9.3 historical evidence was not
rescored.

Provider calls, SQL-generation calls, repair calls, judge calls, embedding
calls, reranker calls, and database mutations: **0**.

## Next milestone

The appropriate next step is exactly:

**M9.5 — Benchmark Result-Contract Binding Protocol**

That milestone should define and independently validate a deterministic,
non-leaky way to bind newly generated result columns to benchmark-owned
semantic slots before a fresh M10 corpus is consumed. It must not modify the
frozen V2 comparator or infer bindings from correctness outcomes.
