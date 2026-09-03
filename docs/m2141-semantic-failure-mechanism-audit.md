# M2.14.1 — Cross-Benchmark Semantic Failure Mechanism Audit

Status: complete, offline only. Audit run: `20260903T194000Z`.

This audit uses the persisted development outputs from:

- M2.13 Defog: `evaluation/results/m213/dev/20260903T120700Z/`
- M2.14 BIRD: `evaluation/results/m214/dev/20260903T130000Z/`

It made zero provider calls, did not rerun either benchmark, and did not read
the frozen internal holdouts. The immutable artifacts are under
`evaluation/results/m2141/20260903T194000Z/` (ignored local evaluation data).

## Result

The dominant BIRD ratio failure is metric-formula/aggregation selection, not
SQL parsing:

| BIRD ratio population | Count |
| --- | ---: |
| Total | 101 |
| Correct | 17 |
| Incorrect | 84 |
| Wrong aggregation | 30 |
| Missing filter | 13 |
| Arithmetic structure | 13 |
| Join path | 11 |
| Join fanout | 6 |
| Wrong numerator | 5 |
| Extra filter | 2 |
| Other | 4 |

The primary formula group (`WRONG_AGGREGATION`, `ARITHMETIC_STRUCTURE_ERROR`,
`WRONG_NUMERATOR`, and `WRONG_SCALE`) accounts for 49/84 incorrect BIRD ratio
cases. Filter errors account for 15/84. Grain/cardinality labels account for
8/84, and join-path errors for 11/84. The counts are primary labels; secondary
tags intentionally retain overlapping evidence.

Strict AST component agreement is a conservative lower bound, not a semantic
equivalence proof: numerator agreement is 15/101, denominator agreement is
27/101, and both components agree in 14/101. Equivalent SQL idioms such as
`SUM(CASE ...)` and filtered `COUNT` are not treated as identical by this
diagnostic signature.

The Defog ratio cross-check has 12/35 correct and 23 incorrect. Its largest
repeated mechanism is also wrong aggregation (7/23), with output shape (4/23),
scale (3/23), join path (3/23), and extra filter (3/23) also recurring. The
repeated cross-benchmark signal is formula/aggregation plus predicate and
relational composition—not a provider transport failure.

## Temporal audit

All 19 BIRD temporal cases were accounted for: 4 correct and 15 incorrect.
After normalizing equivalent current-date/current-timestamp spellings and
manually adjudicating cases whose mismatch was clearly non-temporal, the
bounded distribution is:

| Primary mechanism | Count |
| --- | ---: |
| Wrong time column | 3 |
| Wrong time grain | 3 |
| Undetermined / non-temporal cause | 9 |

The sample is too small and too mixed to support a separate TemporalSpec
implementation from this audit alone. It supports keeping temporal semantics as
a follow-up concern, but not claiming that date-range boundaries are the
dominant mechanism.

## General compositional sample

A deterministic 60-case sample was frozen from incorrect BIRD cases outside the
ratio and temporal slices. Selection used stable minimum-count balancing over
difficulty, database, and join presence. The selected indices and source hash
are in `audit_manifest.json` and `compositional_sample.json`.

| Primary mechanism | Count |
| --- | ---: |
| Filter | 21 |
| Table grounding | 20 |
| Join path | 11 |
| Aggregation | 5 |
| Output shape | 2 |
| Order/limit | 1 |

This indicates both schema/table grounding and relational composition matter.
The full schema was already visible, so the evidence does not isolate schema
retrieval as the next intervention. Literal/value failures were not observed
as the primary label in this sample.

## Design implications

The minimum justified next product contract is M3 — Governed Semantic Metrics,
with server-owned definitions for:

- named metric identity;
- numerator and denominator definitions;
- valid entity grain and cardinality behavior;
- allowed dimensions;
- eligibility/default filters;
- scale and zero/NULL policy.

The audit does not implement these components. It also does not recommend
letting the LLM emit unrestricted metric formulas, changing M1, or introducing
generic retrieval as a substitute for governed definitions. Temporal resolver
work should remain separate until a larger temporal failure population is
available.

## Reproducibility and scope

The AST diagnostic normalizes aliases, qualification, and formatting only for
comparison. It does not rewrite generated SQL or repair any result. Ratio and
temporal cases include gold/generated result-shape diagnostics; generated SQL
was executed only through the existing evaluation-only read-only benchmark
executors. Primary result verdicts remain the persisted M2.13/M2.14 verdicts.

M2.13, M2.14, M2.12, and all frozen internal holdouts remain unchanged.
