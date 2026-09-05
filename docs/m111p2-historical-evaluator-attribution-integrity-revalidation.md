# M11.1P2 — Historical Evaluator & Attribution Integrity Revalidation

## Classification

`M111P2_HISTORICAL_CONCLUSIONS_MATERIALLY_REVISED`

This is an additive, evaluator-side revalidation of preserved M10/M11
evidence. Historical files and scores remain immutable.

## Scope and provenance

The run starts from P1 checkpoint
`8608063eec2a0dc883fa58b4578cd083f6bd5c7a`, with P0 checkpoint
`c189f324edce0a00f1258444d252e0bf49daf250`. The authoritative P0 typed-result
contract is `decision-sql-query-result-snapshot-v1`, version 1, with raw file
hash `ba5adfa425e9bcf5efc6446a0021f1dbed2ebfaca745a913a3e601a403c1a678`.
The later P1 prose contained a mismatched value under the P0-contract label;
this is recorded as `P1_CHECKPOINT_REPORT_METADATA_MISMATCH`. No historical
commit was modified.

The M4 corpus and the DecisionSQL demo PostgreSQL snapshot were reconstructed
and identity-checked. Replay used only already-frozen reference and candidate
SQL through the existing M1 safety path. No candidate was regenerated.

## Correctness revalidation

The legacy reference snapshots are `LEGACY_UNTYPED_RESULT_SNAPSHOT`. Lost
database types were not inferred from strings. The repaired typed snapshot was
used for all new replay results.

The additive results were:

- M10R: historical `28/200`; P2 revalidated `113/200`.
- M10.4S: historical `27/160`; P2 revalidated `85/160`.

These are not rewritten historical scores. They are bounded results for the
matched demo state. No unexpected correct-to-incorrect transition occurred.
The observed historical-incorrect to revalidated-correct transitions with a
legacy reference self-comparison failure are evaluator-persistence false
negative repairs: 85 M10R cases and 58 M10.4S cases.

This establishes that a real persistence type-loss defect could create false
result mismatches for typed values such as `Decimal`; it does not establish
how many other historical cases were mis-scored outside the replay boundary.

## P1 application to historical memory evidence

The frozen P1 contracts were applied additively to 105 primary targets × 50
reconstructed M4 entries = 5,250 exhaustive pairs. The results were kept
separate:

- semantic-equivalent corpus coverage: 3/105 targets;
- proven-adaptable corpus coverage: 5/105 targets;
- proven-adaptable entries historically retrieved: 4/5; missed: 1/5.

The denominator is small, so retrieval recall is descriptive only. Selected
context profiles were also computed from frozen selected IDs; this was not a
retriever rerun. An adaptable example is not a semantic-equivalent example,
and adaptability does not establish model success.

The old M10.4S counts of 61 selectivity cases and 21 overtransfer cases remain
historical values. P2 maps them to observational association/structural
transfer evidence without calling selection causal. A selected non-adaptable
example is association; a matching wrong structural motif is stronger
observational transfer evidence. Neither proves a memory effect without the
paired P3 MEMORY-OFF versus MEMORY-ON counterfactual.

## Claim survival

- M11.0R's old `0/105` single-compatibility result is revised as an
  intervention-selection interpretation; the legacy contract remains immutable.
- M11.0T's old `3/105` compatibility coverage is revised under the P1
  adaptability question to `5/105`; the old statistic is preserved.
- M11.1 exact-query reuse `2/105` remains a historical full-query identity
  statistic, independent of P0/P1 correctness changes.
- M11.1's 176 singleton atoms and 95/105 novel-atom targets remain
  historical-only statistics under legacy atomization. They do not prove
  genuinely new business-semantic primitives; M11.1R was not started.

The governed reference boundary remains
`REFERENCE_ORACLE_INDEPENDENCE_UNRESOLVED`: equivalence to a
MetricCompiler-generated reference is not independent business-semantic
validation. Freshness remains an identity/non-overlap control, not semantic
independence proof.

Because correctness and intervention-driving interpretations materially
changed, no retrieval, admission, corpus, representation, or hybrid memory
intervention is selected. Causal memory claims remain blocked pending P3.

## Explicit boundaries

No provider calls, candidate regeneration, retrieval reruns, memory-OFF/ON
experiment, M4 modification, runtime/app modification, P1 contract tuning,
semantic-atom repair, public benchmark rerun, or historical artifact rewrite
occurred. Corrected historical accuracy was not presented as an original
score, and no conclusion is made that memory, retrieval, corpus coverage, or
semantic representation is the true dominant runtime bottleneck.

The exact next milestone, if this evidence is checkpointed, is:

`CHECKPOINT — Freeze M11.1P2 Historical Evaluator & Attribution Revalidation
Evidence`, followed by `M11.1P3 — Paired Memory-OFF vs Memory-ON
Counterfactual`. P3 is required for causal memory-effect claims.
