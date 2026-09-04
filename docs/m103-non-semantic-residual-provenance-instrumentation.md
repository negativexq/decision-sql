# M10.3 — Non-Semantic Residual Provenance Instrumentation

Status: `M103_NON_SEMANTIC_PROVENANCE_INSTRUMENTATION_ACCEPTED`

M10.3 implements the frozen `decision-sql-residual-provenance-v1` contract.
It adds observation at existing orchestration boundaries only. It does not
change DecisionSQL accuracy, routing, retrieval, grounding, generation,
compiler behavior, M1 policy, execution, or evaluator behavior. No benchmark,
provider, retrieval, grounding, generation, or database run was performed.

## Implementation

The typed collector is in `app/provenance/models.py`. It defines immutable
`ProvenanceEvent` records, the `ProvenanceSink` interface, and a request-local
recorder. `NoOpProvenanceSink` is the default in every instrumented service.
`DiagnosticJsonlProvenanceSink` writes bounded, one-event-per-line records to an
explicit append-only path. Sink errors are retained as bounded sink state and
fail open with respect to the product decision.

Canonical identities are implemented in `app/provenance/canonical.py`:
canonical UTF-8 JSON, sorted object keys, preserved meaningful list order,
explicit nulls, finite JSON numbers, and SHA-256 hashes without timestamps.
Secret fields, gold/reference fields, and unrestricted result rows are
rejected by the diagnostic sink.

`evaluation/m103_provenance_validation.py` validates event hashes, profile
field completeness, required stages, per-case sequence, and causal ordering.
It has no benchmark-gold input. `evaluation/m103_behavior_equivalence.py`
contains the deterministic semantic snapshot comparator used by the tests.

## Capture seams

The same sink can be injected into the current orchestration objects:

- M4: `VerifiedMemoryRuntime._run_memory` records the existing retriever
  version/config/corpus identity, returned entry IDs, returned order/ranks,
  final weighted scores, selected IDs, use mode, and rendered context hash.
  It never re-retrieves, resorts, decomposes scores, or changes Top-K.
- M3: `GovernedMetricRouteService._governed_attempt` records the existing
  grounding DTO (`applicable`, metric, dimensions), grounding request identity,
  compiler input identity, compiler identity, and compiled SQL identity. No
  filter, time, grouping, ordering, or other semantic slot was added.
- Direct context: `TextToSqlService._run` records identities of the existing
  schema and final context composition, including the existing memory addition
  when present. It does not rewrite or re-render the context.
- Provider: `OpenAICompatibleProvider.propose_sql` records the hash of the
  already-composed request body, bounded assistant content, response identity,
  and extraction status/SQL identity. Authorization headers are never part of
  the captured request identity.
- M1/execution: `SqlSafetyService.plan` and `execute` observe their existing
  typed return boundaries, recording outcome, rejection code, candidate hash,
  and execution status without logging result rows or issuing another query.
- Router: `GovernedMetricRouteService.run` records the existing route output,
  route-input identity, and router implementation identity.

These are optional constructor dependencies. Existing callers that do not
provide a sink use `NoOpProvenanceSink`, so ordinary product execution remains
unchanged and persistence is not silently enabled.

## Runtime boundary

Runtime events contain only values already present at the named seam. They do
not receive reference SQL, reference results, expected route/metric, benchmark
split/family, V1/V2 correctness, or residual labels. AST motifs, semantic
comparisons, correctness, and residual mechanisms remain post-run analysis.

The implementation does not create new retrieval scores, score components,
thresholds, margins, abstention, schema pruning, truncation, M3 semantic slots,
prompt content, provider parameters, repairs, or evaluator logic. Existing M4
final scores are captured as `weighted_lexical_schema`; component scores are not
introduced as new telemetry signals.

Raw verified memory SQL is not duplicated: immutable entry ID plus corpus hash
is the recovery identity. Bounded raw assistant output is retained only where
needed to diagnose extraction or malformed-output behavior. Unrestricted DB
rows and credential-bearing metadata are prohibited.

Product mode is fail-open: sink construction, serialization, and persistence
failures cannot alter inference or execution. A controlled diagnostic run may
invalidate its evidence when mandatory provenance is missing; it must not make
the product request fail for that reason.

## Equivalence evidence

The tests use deterministic providers and in-memory/fake execution boundaries.
They compare sink OFF versus sink ON for direct generation, memory retrieval,
and governed compilation, including:

- retrieval IDs, order, ties, and final scores;
- selected memory IDs and rendered memory context;
- structured grounding and compiler output boundaries;
- final provider request identity, extraction, and candidate SQL;
- product fail-open behavior when the sink fails;
- secret/gold/result-row boundaries;
- append-only JSONL replay, event hashes, completeness, and impossible order.

The complete synthetic full-profile fixture reaches 100% mandatory-field
completeness. Incomplete profiles identify missing fields/stages rather than
silently passing. The future diagnostic gate remains 100% mandatory
provenance completeness per applicable case/profile.

No live provider is required for equivalence: request payload identity and a
frozen/mock provider response are the deterministic comparison boundary.
Retriever capture is tested against the current immutable result shape for
single materialization, deterministic tie ordering, score preservation, and
no sink-induced selection changes.

## Scope and roadmap

M10R remains consumed historical evidence at 28/200 under Evaluator V1; M10.3
creates no new accuracy result and does not reconstruct historical M10R
provenance. M7 remains 99/150 on its separate historical corpus. The exact
next milestone is **CHECKPOINT — Freeze M10.3 Instrumentation**. After that
review checkpoint, **M10.4 — Fresh Provenance-Complete Diagnostic Run**
requires a new dataset and must not reuse M10R as independent validation.
Only after fresh evidence identifies a mechanism can an M11 intervention be
selected. A provenance run alone cannot prove the no-memory counterfactual
“the model would have succeeded without memory.”
