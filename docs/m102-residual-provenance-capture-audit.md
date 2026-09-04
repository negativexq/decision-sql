# M10.2 — Residual Provenance Capture Audit

Status: **M102_PROVENANCE_CAPTURE_CONTRACT_READY**

This is an offline architecture and observability audit. It made no provider,
database, retrieval, grounding, generation, embedding, reranker, or judge
calls, and it did not implement instrumentation. M10R remains consumed
historical evidence: **28/200 = 14%** under Evaluator V1 authoritative. M10.1
remains `M101_AUDIT_BLOCKED`.

## Finding

The current code has explicit orchestration seams and already computes the
missing values. The dominant gap is persistence, not a missing decision stage.
M4 produces ordered, scored `RetrievedExample` objects in
`app/memory/retrieval.py:VerifiedQueryRetriever.retrieve`; the runtime then
passes only their rendered examples to the direct generator in
`app/memory/runtime.py:VerifiedMemoryRuntime._run_memory`. M10R retained only
the resulting `memory_used` flag in its case ledger. The runtime model itself
can hold IDs and scores, but those values were not frozen in the consumed
ledger. Historical M10R retrieval values are therefore not reconstructed here.

M3 similarly has an explicit structured boundary. The provider returns
`GovernedMetricGroundingDTO` in
`app/semantics/routing.py:GovernedMetricRouteService._governed_attempt`, then
`grounding_to_request` creates a `MetricRequest` before
`MetricCompiler.compile_metric`. The current DTO exposes applicability, metric
name, and dimensions; filters, grouping, and ordering are catalog-owned rather
than grounding-selected slots. Future capture must preserve the DTO and the
actual compiler input so metric-correct/slot-wrong and grounding-correct/
compiler-different cases can be separated.

The selected future architecture is **O3_TYPED_PROVENANCE_COLLECTOR_INTERFACE**:
a typed, behavior-neutral collector with a `NoOp` implementation for ordinary
execution and an evaluation sink that writes the canonical append-only JSONL
ledger. OTel span links/low-cardinality status may be added later, but OTel is
not the completeness source. The next milestone is exactly
**M10.3 — Non-Semantic Residual Provenance Instrumentation**; it is not started
by this audit.

## Frozen evidence boundary

M10R v2 (`m10-clean-rebaseline-v2`) was consumed 200/200, including HOLDOUT
80/80. Its score is not recalculated. M10.1 audited 172 failures and 28 correct
controls: E1 18, E2 107, E3 0, E4 0, E5 47; 125/172 had E1/E2 coverage. No
mechanism met the 43-case dominance threshold. M10.1 could not distinguish
memory selectivity, memory overtransfer, and downstream generation because
retrieval IDs, ranks, scores, selected examples, and memory context identity
were absent from the frozen run artifact.

The key immutable M10R hashes are: corpus
`4718051d31ffe00695f26413cc0595f8ab9b7e51a546b551b5d907d17caed0c5`, reference
results `8730aacf8d80d719d492f1fa7d66a34915cc1795721ad0b4e9697514bfc122f9`,
provider protocol `443d3ab59635bc8c3823bfced6c13a6963df60638e9f24586675604349f3c2e2`,
master protocol `1212542536762b0688e465dac3700d07e82e26e252064818996449f5ecb89b18`,
ledger `0eb4d4d640f3759f24da40ad0e242950eedc1a39fb6e039968e9e9c94e346f49`, V1
`e327a6bd3b9272549d5e5cea7fa4edcc836a6841f7627c87b27d96a78c67640d`, V2
`536a1d77883d754e4161d4a0a0bb648013333b7f4f37a85d33f6eafb1dfb9497`, residuals
`38d914526c0debf68da291c9a1a7070c5b4ad3d906ea8bf16f25831b30900e04`, and
summary `3ba4d1c4ab9ee1859576c3672338ddd653b40dd034d8fd4db95d868ea27513a1`.
M10.1 hashes remain taxonomy `e6e47e41d94638c90b77f10216d7450a43db440bb2fa0bb91f91430cd8ca19d0`,
evidence `7c25341a3cc878989b1ee66d4d4dbf3d5589cfd8ac08584289fbdea83da227b5`,
protocol `b833439f0c0c515aa77797fa93e6a402740d8d7f85e5f069414f22f09d459c18`,
automated audit `548a543d3a1f66e19f5403074634736b87dafa4e639fca979cd1b074bb718789`,
and decision `007d1c0442330694fd393cb24f61fa7ca94ed41bf0275652ce3180e537505632`.

## Runtime call graph and source seams

The frozen combined path is:

```text
TextToSqlRequest.question
  -> GovernedMetricRouteService.run
     -> governed grounding -> grounding_to_request -> MetricCompiler
     -> M1 plan/execute
  -> fallback direct path
     -> VerifiedMemoryRuntime._run_memory (when enabled and selected)
        -> SchemaContextResolver.resolve (full compact for M10R)
        -> VerifiedQueryRetriever.retrieve
        -> render_verified_examples
        -> TextToSqlService.run_with_context_addition
           -> SchemaContextResolver.resolve
           -> serialize_schema_context + memory addition
           -> OpenAICompatibleProvider.propose_sql
           -> SqlCandidate
           -> SqlSafetyService.plan -> QueryCostGate.explain
           -> SqlSafetyService.execute -> ReadOnlyExecutor
  -> V1 authoritative evaluation
  -> reference-blind V2 shadow when bindable
```

The exact future capture timing is frozen in
`evaluation/fixtures/m102_capture_seam_matrix.json`. The important seams are:

- M4 ranked results: immediately after `retrieve()` returns and before
  `render_verified_examples` (`app/memory/runtime.py:152-162`). Capture the
  actual returned order, IDs, scores, score type, retriever/config/corpus
  identity, and requested K. Do not retrieve or sort again.
- M4 use/context: after rendering and before
  `run_with_context_addition` (`app/memory/runtime.py:185-202`). Capture the
  selected IDs, use mode, and hash of the exact string passed downstream.
- M3 grounding: after `proposal.grounding` is assigned and before
  `grounding_to_request` (`app/semantics/routing.py:208-263`). Capture the
  bounded DTO and selected metric/dimension IDs.
- M3 compiler: after `grounding_to_request` and before/after
  `compile_metric` (`app/semantics/routing.py:262-319`). Capture compiler input
  identity, compiler version, and compiled SQL identity.
- Direct generation context: after schema serialization and memory addition,
  before provider calls (`app/text_to_sql/service.py:117-146`). Capture the
  actual final context/component manifest; if a future truncation step exists,
  capture the post-truncation value.
- Provider request/response and extraction:
  `app/generation/provider.py:433-455`, around `_post` and
  `_proposal_from_response`. Capture request identity, bounded assistant output,
  response/request ID, and extraction outcome.
- M1: observe typed return boundaries of
  `app/sql/service.py:SqlSafetyService.plan` and `execute`; do not add logging to
  parser/policy/executor internals or issue another SQL operation.

## M4 integration audit

M4 is **CONTEXTUAL_EXAMPLE_INJECTION**. It is not deterministic SQL reuse,
template reuse, candidate ranking, or a short-circuit. The retriever is a
deterministic in-memory lexical/schema retriever (`VerifiedQueryRetriever`) with
`m4-retriever-v1`, `R1_QUESTION_LEXICAL_SCHEMA`, `k=3`, weights 0.75/0.25/0.0.
It computes lexical, schema, structural, and final scores, sorts by descending
final score then `example_id`, and returns the first K. In the M10R configuration
there is no confidence threshold: a nonempty active corpus returns top-K, even
for low scores. IDs are stable `vq:...:vN` values and the corpus is immutable
and hashed. Scores are meaningful weighted similarity scores, not probabilities.

`render_verified_examples` injects the verified question and SQL text into the
bounded generation context. The exact retrieval provenance disappears from the
M10R persisted ledger at the evaluation-run boundary in
`evaluation/run_m10r.py:224-245`, where only `result.verified_memory_used` is
serialized. The runtime `VerifiedMemoryProvenance` contains IDs and final
scores, but was not serialized there. Raw memory SQL does not need runtime
duplication: stable entry ID plus corpus hash recovers it from immutable M4
content. Scores, order, selected IDs, and context identity do need runtime
capture because they are dynamic run facts.

## M3 and generation audit

The current M3 mechanism is one provider call for a structured
`GovernedMetricGroundingDTO` containing `applicable`, `metric_name`, and
`dimensions`. `MetricRequest` contains metric name and dimensions only. The
catalog supplies physical filters, joins, formulas, grouping, and ordering to
the deterministic compiler. The existing result retains some semantic
provenance (`SemanticExecutionProvenance`) and the compiled candidate, but the
M10R ledger did not retain a standalone structured grounding event or the
compiler-input boundary. The future contract therefore requires the structured
output and compiler input hash, while filters/grouping/ordering are explicitly
not mandatory current-M3 fields unless the semantic DTO itself changes in a
separate milestone.

The direct service dynamically composes a schema string from
`SchemaContextResolver` and `serialize_schema_context`, then appends memory
context when present. M10R used `FULL_COMPACT`; the resolver can also select a
bounded retrieved schema. No token truncation or memory Top-K clipping occurs
in the inspected code. Provider messages are built by `_generation_messages`
and the evaluation-only `ModelIOCapture` already retains messages, request
configuration, raw assistant content, and parsed SQL when enabled. That is
useful historical model-I/O evidence, but future diagnostic provenance still
needs stable hashes tying the actual final context/request to M4 IDs and the
candidate extraction event. A bounded raw assistant response remains required
for extraction/malformed-output diagnosis; secrets and provider headers are
never captured.

## Minimum contract

The contract is `decision-sql-residual-provenance-v1`. Its complete field
catalog, hypothesis matrix, seam matrix, strategy matrix, and behavior-
equivalence plan are the JSON fixtures under `evaluation/fixtures/m102_*`.
The minimum runtime facts are:

| Diagnostic profile | Mandatory runtime evidence |
| --- | --- |
| `MEMORY_DIAGNOSTIC` | retrieval attempt/config/corpus/query identity; actual ordered IDs, ranks, scores and score type; selected IDs/use mode; rendered memory-context hash |
| `GOVERNED_DIAGNOSTIC` | grounding request identity; bounded structured grounding output; metric/dimension IDs; compiler-input identity; compiler/version and SQL identities; provider response/extraction facts where a provider call occurred |
| `DIRECT_DIAGNOSTIC` | selected schema/context identities; memory context identity; provider request identity; response ID and bounded raw assistant output; candidate extraction status/SQL identity |
| `FULL_DIAGNOSTIC` | the three profiles plus route, M1, execution, and existing V1/V2/binder result-stage identities in separate post-run result artifacts |

The event envelope is append-only and causally ordered:
`run_id`, `case_id`, `stage`, `event_type`, `sequence`, `schema_version`,
`payload`, and `payload_hash`. Runtime capture never receives reference SQL,
reference results, contracts, expected routes, correctness, or residual
labels. The canonical storage recommendation is one typed JSONL ledger per
future diagnostic run; post-run AST motifs and residual labels remain separate
artifacts. Hashes use canonical UTF-8 JSON, sorted object keys, preserved
meaningful list order, explicit nulls, and no timestamps in semantic hashes.

Fields recoverable from stable IDs plus immutable source artifacts are not
duplicated: memory question/SQL and structural signatures, and candidate/
reference AST motifs, are derived offline. Dynamic ranking/scores/order,
selection/use decision, exact rendered context identity, structured grounding,
actual provider response, and extraction status are not reliably recoverable
and are mandatory where their hypothesis requires them.

## Hypothesis coverage and current gaps

| Hypothesis | M10R diagnosable | Future contract | Required evidence |
| --- | --- | --- | --- |
| Memory selectivity | No | Yes | ranked IDs/order/scores, config/threshold/use decision, immutable entry semantics |
| Memory wrong-motif transfer | No | Yes | selected/context identity plus retrieved/candidate/reference SQL identities and offline motifs |
| Downstream generation | Partially | Yes | exact final context/request identity, bounded response, extraction status, candidate identity |
| Governed semantic slot | No | Yes | structured grounding output and compiler-input identity; current slots are metric/dimensions |
| Governed compiler | No | Yes | grounded input, compiler version, compiled SQL identity, offline SQL motifs |
| Routing overreach | Yes | Yes | router version/input identity/output; benchmark expectation joins only offline |
| M1 structure | Yes | Yes | candidate identity, typed M1 status/rejection, execution status, offline AST |

The proposed contract would have closed the M10.1 blocker if it had existed
during M10R: **YES**. It would not recreate missing historical values now;
it would have made the key hypotheses testable without rerunning the system.

## Behavior-neutrality and implementation risks

Future instrumentation must use immutable/copy-safe bounded DTO snapshots and
must never materialize a retriever iterator twice, sort returned results, alter
floating-point scores, or re-render a prompt for execution. The collector is
called after values already exist; its output is not an input to any decision.
Provider request hashes are made from a redacted canonical body, never headers.
Raw assistant content is bounded. A buffered sink must preserve the monotonic
sequence and must not block or reorder the async decision path. Sink exceptions
fail open in product mode; controlled diagnostic mode marks mandatory
provenance loss and rejects causal conclusions for the affected evidence.

The precommitted behavior-equivalence plan tests sink OFF versus ON for router
output, retrieval IDs/order/scores and call count, grounding output, compiler
input/SQL, final context/provider payload identity, extraction, and M1 plan
input. It also tests iterator safety, async ordering, secret rejection, and
fail-open behavior. A future diagnostic run requires 100% mandatory provenance
completeness before causal conclusions are accepted.

## Decision and boundaries

- `M102_PROVENANCE_CAPTURE_CONTRACT_READY`.
- No semantic change is required; implementation can remain outside the core
  decision logic and preserve a NoOp behavior-equivalent path.
- M10R artifacts were not modified, M10R was not rerun, and no historical
  provenance was reconstructed.
- M10R remains **28/200 = 14%**; M7 remains **99/150 = 66%** on a separate
  historical corpus. These are separate corpora and not a regression delta.
- No new benchmark accuracy was created, no subsystem intervention was
  selected, and no public benchmark was rerun. Defog/BIRD remain deferred.
- M10.2 is complete only as a contract design. The exact next milestone is
  **M10.3 — Non-Semantic Residual Provenance Instrumentation**. Any later causal
  validation requires a new independent dataset; M10R v2 is consumed
  historical evidence and cannot become fresh again.

Hashes for the new contract artifacts are emitted by
`evaluation/m102_provenance_audit.py` and are intentionally separate from the
immutable M10R/M10.1 hashes above.
