# M10.4R — Canonical Freshness Protocol Repair Audit

Status: `M104R_CANONICAL_FRESHNESS_PROTOCOL_REPAIRED`

This is an offline, provider-free, DB-free protocol repair. It did not rerun
M10.4, create replacement cases, modify the invalidated M10.4 artifacts, or
change DecisionSQL runtime semantics.

## Root cause

The invalid M10.4 pre-exposure check was
`evaluation/m104_corpus.py:freshness_report`, which compared
`reference_sql.strip().lower()` strings. The post-exposure check was
`evaluation/m104_invalidity_audit.py:main`, which compared
`sqlglot.parse_one(...).sql(dialect="postgres").lower()` strings. The former
preserved line breaks, comments, and spacing; the latter used deterministic
PostgreSQL parser rendering. The seven references therefore escaped the first
gate and matched the second.

M10.4R does not repair the old artifact in place. It introduces one evaluation
implementation at `evaluation/reference_freshness.py`:
`canonical_reference_fingerprint`. Both `pre_run_canonical_overlap` and
`post_run_canonical_overlap` call the same `canonical_overlap` function, which
calls that fingerprint implementation.

## Frozen contract

Contract: `decision-sql-reference-freshness-v1`, version `1`.

The pipeline is:

```text
reference SQL → PostgreSQL parse → deterministic parser rendering → SHA256
```

The parser is SQLGlot `29.0.1` with the PostgreSQL dialect. Exactly one parsed
statement is required. Parse failure means no fingerprint and a freshness
failure; raw text is never used as a fallback.

The bounded v1 normalizations are whitespace, comments, parser formatting, and
keyword case as represented by SQLGlot's rendering. Literal values, table and
column identity, aliases, join paths, aggregation, grouping, formulas,
temporal expressions, windows, ordering, limits, DISTINCT, and set operators
remain represented in the canonical SQL. Alias renaming and broad semantic
rewrites are not performed.

This contract does not claim arbitrary SQL semantic equivalence, same-result
equivalence, join algebra, predicate implication, aggregate equivalence,
subquery/CTE equivalence, theorem proving, LLM similarity, or embedding
similarity.

## Regression and safety evidence

All seven known invalid M10.4 overlaps are detected as
`PRIOR_CANONICAL_REFERENCE_OVERLAP`. Every candidate and prior raw string had
different raw and trimmed identities, while canonical fingerprints matched:

| Candidate | Prior source | Prior identity |
| --- | --- | --- |
| `m104-governed-completed_revenue-00` | M3 | `evaluation/fixtures/m3_targets.json` |
| `m104-governed-average_completed_order_value-00` | M3 | `evaluation/fixtures/m3_targets.json` |
| `m104-direct-formula_ratio-01` | M4 | `vq:non_governed_arithmetic_3:v1` |
| `m104-direct-order_topn-00` | M4 | `vq:order_limit_1:v1` |
| `m104-direct-order_topn-05` | M4 | `vq:grouped_top_n_1:v1` |
| `m104-direct-distinct_set-06` | M4 | `vq:distinct_entity_count_3:v1` |
| `m104-direct-distinct_set-07` | M4 | `vq:distinct_entity_count_5:v1` |

Safe formatting controls passed `4/4`. Material and scope-sensitive negative
controls remained distinct `19/19`, with zero false canonical duplicates and
zero unsafe self-join or nested-scope collapses. The historical index had 1,192
entries, 587 unique raw hashes, 508 unique canonical fingerprints, and 211
fingerprints with multiple source entries. All 1,192 references fingerprinted;
none were silently excluded.

The seven known regressions reproduce the old defect: the legacy trimmed-string
gate detected `0/7`, while the repaired canonical gate detects `7/7`.

## Future gate

Future freshness retains exact and normalized question checks, exact raw SQL
checks, question/reference-pair checks, M4 question/SQL checks, and the new
canonical reference check. Any canonical overlap is a hard pre-exposure
failure. The canonical freshness manifest hash must be included in the final
master lock, and the canonical check must run both before reference validation
where practical and immediately before provider exposure.

Reference or question edits invalidate the prior freshness result and require
recomputation. The consumed historical inventory includes M3, M4, M7, M10,
M10R, and the 160-case invalidated M10.4 corpus with role `INVALID_EXPOSED`.

M10.4 remains permanently `M104_RUN_INVALID`; its raw `33/160` output and
provisional mechanism counts are not accepted evidence. M10.4R created no new
benchmark accuracy, replacement corpus, provider call, DB call, intervention,
or M11 selection.

## Integrity note

The prior M10.4 report supplied one malformed 65-character value for the final
summary hash. The unchanged on-disk artifact has the valid 64-character SHA256
`4b37dc70f768247608dbf98d504820124a900615a73fef70ce9362df479d03f7`; this is
documented rather than rewriting the invalid-run artifact.

## Next checkpoint

The repaired contract must be checkpointed before constructing any replacement
corpus:

`CHECKPOINT — Freeze M10.4 Invalid Run + M10.4R Canonical Freshness Repair`

Only after that checkpoint may a new fresh diagnostic corpus be designed.

## Frozen repair hashes

The generated repair artifacts are under
`evaluation/results/m104r/canonical-freshness-v1/`:

- canonicalizer source: `3917b5f4a4a55ec897e8e799d1228ca4223ceae57aa836e54de3e17c22118e62`
- contract/protocol: `f4ae704d4380e41bdc1bbfefb487216d0e03b6ea53a7834a796b8e08a012617e`
- known-overlap regression: `315d7d9a0a5a2719a33e08f4a216541b97e7c9346c042ccaf70aa31c6398b038`
- positive controls: `d1d82b70fae3a21a55744a39c95d2a1f470951e9b73b2d97230d88a39fb8bbf9`
- negative/adversarial controls: `276fa7f9a9886b87c946869b7c3a6ff7a3a8344cc556f259ce8e972ed2bbad20`
- consumed-reference manifest: `17b5fc23ffdbcf1c2e45191c708032fae9b7690632197f4c0aa8b9d9d5de7ebc`
- canonical index: `eba19f6064d85eb51c6dbfa1870b349085c04840004c79547df704ab35b45020`
- evidence manifest: `3662edd8a92541850c565222f09c6890078ac858ddae8613591a5ba90ee462e3`
- test summary: `770d674c9c920abf93d3722cbb34af5350805eb93b15804b06c1961b43ad3bda`
- final decision: `6ea8bc8924f715bf71e259833b24fcb0582fa42f0e3381a5c944904d9784d0a3`
- final summary: `c344135063947e71425cbecc6aa76da7c0e570b4b0e5fd02cfb0841c98c58ed3`
