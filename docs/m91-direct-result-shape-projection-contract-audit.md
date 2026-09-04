# M9.1 Direct Result-Shape and Projection Contract Audit

## Executive conclusion

M9.1 is an offline, provider-free audit of the 31 M9 cases previously labeled
`S11_RESULT_SHAPE_PROJECTION_STRUCTURE`. It does not change M7, M8, or M9
scores, and it does not add a runtime result contract.

The primary conclusion is:

```text
EVALUATOR_EQUIVALENCE_HARDENING_JUSTIFIED
```

Ten of the 31 cases are strong evaluator-artifact candidates: the generated
query preserved the user-requested computation and row identity, but the
existing equal-width evaluator rejected an additional or differently selected
column. Nineteen remain projection-contract candidates, and two are deeper
computation/semantic errors. Twenty-eight of 31 retain the requested
computation core under the manually adjudicated contract.

The 19/31 projection-or-grain target is below the precommitted 20/31 threshold
for a new result-shape contract experiment. The evaluator-artifact count is
10/31, and the existing evaluator has deterministic semantics that can be
tested without hiding grain, duplicate, missing-field, or value errors.

The only next milestone recommended is:

```text
M9.2 — Result Equivalence Contract Regression Suite
```

This is an evaluator-hardening investigation. It must not silently rescore
historical benchmarks or change runtime behavior.

## Scope and frozen provenance

The audit slice is derived from the frozen M9 reconstruction; it contains no
new questions and no newly generated SQL.

| Item | Value |
|---|---|
| M9.1 corpus | `decisionsql-m91-result-shape-audit` |
| Version | `m91-result-shape-audit-v1` |
| Cases | 31 (DEV 24, HOLDOUT 7) |
| Full hash | `7f838d8af24c62d89d37367c41efe1b404fbcb1c2f75ffa01419bd33162e74ac` |
| DEV hash | `5de461ba423804654d647305d9ce8509edff113ed4cfab68da6ae8af2e37db27` |
| HOLDOUT hash | `76ccbd1289ddca2e7d8402c09d02ce191a0b20b962adc03558520f4c960321ec` |
| M9 incorrect-source hash | `eadcc297635152999ca6478a74b1b24a67b23b8424630e6e081a3ba7e7c67180` |
| Correct direct controls | 75 |
| Manual control sample | 20 |
| Manual control hash | `4f4a35ca2bc3ba907ff42282ac7b76a984ce70b23fc856761df373b14d970688` |

The 31 cases are 31/31 historically incorrect M4-direct outputs with accepted
M1 plans and persisted generated/reference SQL provenance. The split remains
the original M7 split; no tuning or holdout change occurred.

M7 remains 99/150 (66%), M8's observed counterfactual remains 105/150, and M9
remains 75/120 for the reconstructed direct path. M9.1 does not publish an
adjusted score.

## Existing evaluator semantics

The existing `evaluation.metrics.assess_query_results` implementation remains
the authority for historical correctness:

- aliases and column names are ignored;
- columns are compared by ordinal position, so column order and equal width
  matter;
- row order is ignored by default through multiset sorting;
- duplicates are preserved;
- NULL and date values are canonicalized;
- numeric values use `math.isclose` with absolute and relative tolerance
  `1e-6` by default;
- extra and missing columns are rejected because column counts must match;
- structural SQL diagnostics are non-scoring.

This explains why an SQL query can be execution-equivalent in the product
sense under a user-facing contract while failing the frozen evaluator: the
frozen comparator requires equal-width ordinal tuples. It does not establish
that every width mismatch is harmless. Grain, required fields, duplicate
multiplicity, values, and row membership remain semantic.

## M2.8 reconciliation

The M2.8 artifact and implementation were found. M2.8 used a provider-generated
untrusted `ResultShapeProposal` containing output labels/kinds, optional
physical source hints, shape kind, and explicit limit/order fields. It made an
additional provider call, then validated generated SQL after M1 planning.

On its 48-case development screen, M2.8 was neutral on result equivalence:
16/48 baseline versus 16/48 with the shape proposal. Required-output recall
was 27.08%, five extra execution/contract failures appeared, and end-to-end
generation latency roughly doubled. No holdout was consumed.

The M9.1 audit is materially narrower and different. It does not ask a model
to infer a shape, does not add a DTO, does not validate or reject runtime SQL,
and does not change a prompt. It manually records the minimum user-requested
dimensions, measures, grain, and scalar/tabular intent offline, then tests the
existing evaluator's equivalence boundary. The reconciliation classification
is:

```text
NARROWER_NEW_HYPOTHESIS_SUPPORTED_BY_NEW_EVIDENCE
```

That distinction supports an evaluator regression suite, not an automatic
ResultShape v2.

## Contract audit method

The user question, not the reference SELECT list alone, defines the audit
contract. Each record includes required dimensions, required measures,
required business fields, expected row grain, cardinality class, and optional
display fields. Reference SQL is used as corroborating evidence, not as a
unique product interface specification.

The audit keeps these concepts separate:

- `PRESENTATION_ONLY`: only a demonstrated display difference such as an alias
  or non-semantic ordering difference. None of the 31 cases met this narrow
  category.
- `PROJECTION_CONTRACT_VIOLATION`: requested computation and grain are intact,
  but the output fields do not meet the manually stated user contract.
- `GRAIN_CONTRACT_VIOLATION`: row identity/cardinality is wrong. This is not
  fixed by dropping a column.
- `TRUE_COMPUTATION_OR_SEMANTIC_ERROR`: the S11 label obscured an earlier
  population, measure, or computation failure.
- `EVALUATOR_ARTIFACT`: the user-requested result is satisfied and the frozen
  evaluator rejects it solely because it requires an equal-width ordinal
  projection.

The detailed audit found:

| Primary category | Count |
|---|---:|
| `PRESENTATION_ONLY` | 0 |
| `PROJECTION_CONTRACT_VIOLATION` | 19 |
| `GRAIN_CONTRACT_VIOLATION` | 0 |
| `TRUE_COMPUTATION_OR_SEMANTIC_ERROR` | 2 |
| `EVALUATOR_ARTIFACT` | 10 |
| `OTHER` | 0 |
| `UNDETERMINED` | 0 |

The two deeper errors were a population/computation difference in the
customer order-share query and a left-join population difference in total
quantity purchased. They are not projection-only fixes.

The detailed mechanisms were 28 `P3_EXTRA_OPTIONAL_COLUMN`, one
`P6_MISSING_REQUIRED_MEASURE`, one `P8_WRONG_MEASURE_PROJECTION`, and one
`P12_WRONG_ENTITY_ROW_IDENTITY`. The extra-column count is diagnostic; it is
not permission to discard arbitrary columns. The audit separately checked
that extra grouping keys and duplicate-changing projection were not normalized.

## E0, E1, and E2 diagnostics

The historical score remains E0, the existing strict evaluator. M9.1 adds
diagnostic-only levels:

- `E1_PRESENTATION_NORMALIZED` would permit only proven presentation changes;
  it recovered 0/31.
- `E2_REQUIRED_PROJECTION_NORMALIZED` projects both results onto adjudicated
  required fields only when grain is already exact, required fields exist, and
  duplicates are preserved. It recovered 28/31 diagnostically.

The 28/31 E2 result is not a rescore. It is evidence that the strict comparator
and the product's user-facing output contract are not identical for these
cases. E2 refuses the synthetic `region | rep | revenue` versus
`region | revenue` case when `rep` expands a region into multiple rows. It also
refuses missing required fields and never deduplicates.

The aggregate required-field recall was 98.39%; one case, the ranking query,
was missing the explicitly requested rank measure. Grain relation was exact in
30 cases and different in one. No value, SQL literal, or grain normalization
was performed.

## Correct-control analysis

All 75 correct expected-DIRECT M4 cases were used for deterministic structural
control statistics. A deterministic 20-case sample was manually selected
across family/aggregate/grouping/window/projection buckets.

| Signal | Correct-control support |
|---|---:|
| Projection-width difference | 0/75 |
| Alias/label difference | 44/75 |
| SELECT expression-order difference | 17/75 |
| GROUP BY implementation difference | 3/75 |

The controls demonstrate that aliases and SQL implementation shape can differ
without changing result equivalence. They also show that the particular
projection-width mismatch signal is not common among the 75 successful direct
outputs. The audit nevertheless does not promote width mismatch into a
runtime verifier: an extra field can be a required business field, a grain
key, or evidence of a deeper error.

## Split and family evidence

The 31 cases occur in both splits: 24 DEV and 7 HOLDOUT. The evaluator-artifact
category appears 8 times in DEV and twice in HOLDOUT; projection candidates
appear 15 and 4 times respectively; the deeper semantic category appears once
in each split. This is replication of the diagnostic pattern, not a new
holdout evaluation.

By original M7 family, the slice contains:

| Family | N |
|---|---:|
| `CLEAR_DIRECT_COMPLEX` | 8 |
| `CLEAR_DIRECT_SIMPLE` | 3 |
| `EXACT_LITERAL_VALUE_FILTER` | 6 |
| `FILTER_AGGREGATION` | 2 |
| `ORDER_TOP_N` | 2 |
| `RATIO_DERIVED` | 4 |
| `RELATIONAL_COMPOSITION` | 1 |
| `WINDOW_COMPOSITION` | 5 |

This is broad enough to rule out a single family-only artifact, but the
dominant mechanism is still output selection rather than a single bounded
generation motif.

## Why this does not justify a new result-shape layer

The frozen result-contract threshold required at least 20/31
projection-or-grain cases. The audit found 19/31. Although 28 cases are E2
projection-equivalent, ten of those are more appropriately treated as
evaluator-artifact candidates because the question did not require the
reference's exact projection. A new server-owned contract experiment would
therefore mix a genuine output-contract question with an unresolved evaluator
policy question.

The evaluator-hardening threshold is met: 10/31 cases are clearly
evaluator-artifact candidates, aliases are already ignored, row order and
duplicates have explicit semantics, and the E2 guard preserves grain and
required-field behavior. A future regression suite can establish what
equivalence is intended without changing historical scores first.

No production result contract, ResultShape DTO, normalization layer, or
runtime evaluator change was added.

## Roadmap decision

```text
M9.1 classification:
EVALUATOR_EQUIVALENCE_HARDENING_JUSTIFIED

Next milestone:
M9.2 — Result Equivalence Contract Regression Suite
```

M9.2 outranks a result-shape oracle experiment because the immediate ambiguity
is whether the current evaluator is expressing the intended output contract.
It outranks grain research because only one case had a different grain and two
cases were deeper semantic/population errors. It outranks a generic planner
because no new generation evidence exists and the largest cleanly supported
finding is deterministic equivalence policy, not missing SQL planning.

M9.2 must be independent: it should freeze explicit result-equivalence rules,
preserve duplicates, require requested fields, reject wrong grain, and test
new regression cases before any historical score or evaluator implementation
is changed.

## Safety and non-changes

The audit made zero provider calls and zero SQL-generation calls. Two exact
historical generated/reference queries were checked read-only through M1 to
confirm the evaluator-shape interpretation; no result rows were persisted and
there were zero mutations. It did not rerun M3, M4, M7, M8, Defog, BIRD, or
PRACTIQ. M1, M3, M4, routing, prompts, runtime defaults, and the evaluator
implementation were unchanged. M7/M8/M9 historical scores were not changed.
