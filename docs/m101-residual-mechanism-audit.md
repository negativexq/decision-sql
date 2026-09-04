# M10.1 — Residual Mechanism Audit

Status: `M101_AUDIT_BLOCKED`

This is an offline, provider-free, DB-free audit of the consumed M10R
artifacts. It does not change the M10R corpus, references, contracts, route
results, provider responses, evaluator outcomes, or score.

## Frozen input

M10R version `m10-clean-rebaseline-v2` remains historical evidence:

- primary Evaluator V1 result: `28/200 = 14%`
- primary failures: `172`
- executed-result mismatches: `164`
- M1 rejections: `8`
- V1 protocol failures: `0`
- V2 shadow recovery: `1`

The M10R score remains unchanged. M7 remains `99/150 = 66%` on its historical
frozen workload.

## Audit protocol

The taxonomy and audit protocol were frozen before the full per-case audit.
Primary mechanism assignments are mutually exclusive. Stage failures and
governed route provenance have priority over memory and direct AST diagnoses.
Only E1 direct provenance and E2 structural corroboration can satisfy the
dominance gate: at least 43 of the 172 failures, with coherent intervention
semantics. Family membership alone, manual interpretation, or memory use plus
an incorrect result cannot establish dominance.

The canonical reference is the first benchmark-designated reference variant.
AST signatures are bounded to tables, joins, projections, predicates,
grouping, aggregates, formulas, temporal expressions, windows, ordering,
limits, offsets, and set operations. SQL text inequality is not treated as a
mechanism.

## Provenance limitation

The frozen M10R ledger records `memory_used`, but does not retain retrieved
memory IDs, ranks, retrieval scores, verified example SQL, or memory
provenance. The provider captures retain metric-grounding and SQL-generation
records but do not add those retriever fields. The frozen M4 runtime confirms
that memory is injected as verified-example context before direct generation;
it does not restore the missing M10R per-case retrieval record.

Consequently, memory selectivity and memory overtransfer are not proven or
disproven here. All 132 memory-use cases are recorded as
`M0_NO_TRANSFER_EVIDENCE`; the 23 correct cases remain controls against the
109 incorrect cases. No retrieval, embedding, reranker, provider, database,
or generation call was made to recreate the missing evidence.

## Deterministic findings

The audit preserves the 8 M1 stage failures and audits all 60 actual-governed
cases, all 132 memory-use cases, all 172 failures, and all 28 correct controls.
Expected-governed metric grounding is compared only when the frozen grounding
capture contains the selected metric. Direct executed candidates are compared
with the stable primary reference using bounded AST signatures. Unresolved or
multi-axis cases remain unclassified/compound rather than being assigned a
speculative mechanism.

Because the missing memory provenance prevents distinguishing a key M10.1
hypothesis—retrieval selectivity/overtransfer—from downstream direct semantic
generation error, the audit is blocked for a complete causal decomposition.
This is a provenance limitation, not evidence that memory caused the errors,
and not a new benchmark score.

The automated output contains 82 `COMPOUND_SEMANTIC_FAILURE` cases, 45
`GOVERNED_UNCLASSIFIED` cases, 25 `DIRECT_PROJECTION_OR_RESULT_SHAPE` cases,
10 `ROUTING_OVERREACH` cases, 5 `M1_FORBIDDEN_FUNCTION` cases, one each of
`M1_UNKNOWN_COLUMN`, `M1_UNKNOWN_TABLE`, and
`M1_TRANSPORT_PLACEHOLDER_COLLISION`, plus two `UNCLASSIFIED` cases. One of
those two is the single frozen V2 shadow recovery and is intentionally kept out
of generation-mechanism claims.
The 83-case compound group is not treated as a dominant mechanism because it
is explicitly non-coherent for intervention selection. The governed failures
have 100% expected-metric selection in the retained grounding captures, so
metric-ID grounding is not supported as their explanation; finer slot/compiler
provenance is not retained.

Frozen audit hashes:

- taxonomy: `e6e47e41d94638c90b77f10216d7450a43db440bb2fa0bb91f91430cd8ca19d0`
- evidence manifest: `7c25341a3cc878989b1ee66d4d4dbf3d5589cfd8ac08584289fbdea83da227b5`
- audit protocol: `b833439f0c0c515aa77797fa93e6a402740d8d7f85e5f069414f22f09d459c18`
- automated audit: `548a543d3a1f66e19f5403074634736b87dafa4e639fca979cd1b074bb718789`
- final decision: `007d1c0442330694fd393cb24f61fa7ca94ed41bf0275652ce3180e537505632`

## Outputs

Ignored run artifacts are written under
`evaluation/results/m101/residual-audit-20260904/`. Tracked audit inputs are:

- `evaluation/fixtures/m101_residual_taxonomy.json`
- `evaluation/fixtures/m101_evidence_manifest.json`
- `evaluation/fixtures/m101_audit_protocol.json`
- `evaluation/m101_residual_audit.py`
- `tests/unit/test_m101_residual_audit.py`

M10R artifacts are read-only. No README, application, runtime, M1, M3, M4,
routing, prompt, provider, evaluator, binder, or contract source was changed.

## Next milestone

`M10.2 — Residual Provenance Capture Audit` is the single recommended next
milestone. It should first establish a new, immutable benchmark-run ledger
that records memory retrieval IDs/ranks/scores and verified SQL context before
any causal claim about memory is made. Any subsequent intervention still
requires a new independent validation corpus; consumed M10R v2 cannot serve as
fresh proof.
