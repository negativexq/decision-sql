# M22 — Filter Semantics Deep Forensics

## Objective

M22 decomposes the 61 cases that M20.2 labelled `FILTER_SEMANTICS`. It is an
offline forensic analysis only: provider calls, SQL regeneration, prompt
changes, production changes, and M1 changes are all zero. The purpose is to
choose the next isolated intervention without treating “filters” as one
semantic problem.

## Frozen population and audit

The M20.2 artifact remains unchanged and validates its 774-case ledger:
321 correct, 189 executed mismatches, 264 legitimate M1 rejects, and zero
planning or execution failures. Its 61 filter cases are unique and were
reconstructed exactly once. The requested starting checkpoint was
`50ca58513dcf683e1eaa3056244450ada69a307f`; the repository was already at the
later M21 descendant `15ea2296f7f8013f9fe846f01b407e2594dcb2ce`. No history was
rewritten and M21 evidence was preserved.

## Methodology and filter subtype distribution

Each case was reviewed using its frozen question/evidence, generated and
reference predicate AST facts, and frozen typed-result mismatch. A case was
given one primary subtype. M20.2's primary category was not changed; cases
whose filter difference was incidental to a larger projection/order/aggregate
difference remain explicitly `UNDETERMINED` here.

| Primary subtype | Count | % of 61 | BIRD | Classic | Advanced |
| --- | ---: | ---: | ---: | ---: | ---: |
| MISSING_PREDICATE | 2 | 3.279% | 1 | 1 | 0 |
| EXTRA_PREDICATE | 2 | 3.279% | 2 | 0 | 0 |
| WRONG_FILTER_COLUMN | 2 | 3.279% | 2 | 0 | 0 |
| WRONG_FILTER_VALUE | 9 | 14.754% | 8 | 1 | 0 |
| WRONG_OPERATOR | 0 | 0.000% | 0 | 0 | 0 |
| BOOLEAN_COMPOSITION | 2 | 3.279% | 2 | 0 | 0 |
| NULL_OR_EXISTENCE | 1 | 1.639% | 1 | 0 | 0 |
| TEMPORAL_FILTER | 1 | 1.639% | 1 | 0 | 0 |
| STRING_MATCHING | 1 | 1.639% | 1 | 0 | 0 |
| HAVING_VS_WHERE | 1 | 1.639% | 1 | 0 | 0 |
| MULTI_CAUSAL | 1 | 1.639% | 1 | 0 | 0 |
| UNDETERMINED | 39 | 63.934% | 31 | 8 | 0 |
| **Total** | **61** | **100%** | **51** | **10** | **0** |

The large `UNDETERMINED` group is intentional. In these frozen cases a WHERE
AST difference is present, but the result mismatch also contains a different
projection, ordering, aggregation, subquery, or other semantic change. The
available artifact does not prove that the filter difference was the earliest
cause.

## Value grounding

Nine cases are high-confidence wrong-value cases. Four are explicit enum/code
or state mappings (`-`/`+-`, `California`/`CA`); five are stored-label or case
variants such as `America`/`American`, `Human`, `Stealth`, `Durability`, and
Portuguese locale spelling. A bounded live DB value lookup was not run because
the frozen question/evidence and generated/reference literals already support
the diagnosis and no runtime component is being changed.

| Value-grounding result | Count |
| --- | ---: |
| Wrong-value cases | 9 |
| DB-value lookup strongly plausible | 9 |
| Partially plausible | 0 |
| Not sufficient | 0 |
| Enum/code dictionary | 4 |
| Case-insensitive match | 3 |
| Fuzzy/text variant | 2 |

This is feasibility evidence, not an implemented lookup system. A future
experiment would need bounded, server-owned, read-only value grounding and
must not use gold SQL/results to choose values.

## Constraint contract, operator, temporal, column, boolean, NULL, and HAVING analysis

Eleven cases have an applicable predicate/constraint shape. Ten contain a
high-precision lexical/evidence signal and one remains ambiguous. Eight are
classified as plausibly expressible by a typed high-precision constraint; three
require additional grounding. No isolated wrong-operator case was supported
by the frozen evidence, so no operator mapping intervention is selected.

The two missing-predicate cases are explicit constraints: alignment `Bad` and
declared major. The two extra-predicate cases add null guards, but their result
differences are not isolated. The two column cases are comments-vs-post score
and `type` vs `types`. Boolean cases involve OR precedence/grouping. One case
is a temporal duration-unit normalization issue, one is identifier suffix
matching, and one combines null guards with ratio arithmetic. HAVING-versus-
WHERE is present in one aggregate case.

## Counterfactual evidence

No diagnostic counterfactual SQL was executed (`0` cases tested, `0` fixes,
`0` non-fixes). The reviewed wrong-value and high-precision predicate cases
were sufficiently supported by frozen evidence. This avoids introducing live
database access or any risk of mutating benchmark artifacts during an
offline classification milestone.

Representative cases are retained in the machine-readable artifact, including
`115`, `122`, `181`, `228`, `344`, `349`, `365`, `420`, and `classic:49`.

## Correct control comparison

The accepted M20 SQL ledger with preserved SQL contains 475 cases, of which
296 were correct in the original M20 artifact. The 25 M20.1R2 recovered cases
do not preserve generated SQL in that artifact and were not guessed into shape
analysis. Among the 475 preserved accepted candidates:

| Observable shape | Executed | Correct | Wrong | Error rate |
| --- | ---: | ---: | ---: | ---: |
| 0 joins | 144 | 101 | 43 | 29.861% |
| 1 join | 236 | 148 | 88 | 37.288% |
| 2+ joins | 95 | 47 | 48 | 50.526% |
| Aggregation | 277 | 167 | 110 | 39.711% |
| Filter present | 388 | 234 | 154 | 39.691% |
| OR boolean | 13 | 5 | 8 | 61.538% |
| NULL predicate | 58 | 34 | 24 | 41.379% |
| Inequality predicate | 72 | 40 | 32 | 44.444% |
| IN predicate | 25 | 11 | 14 | 56.000% |

These are descriptive controls, not runtime signals and not proof that any
shape causes an error.

## Existing capability relevance

Whole-request contracts remain authoritative. For these 61 filter residuals,
QueryPlan V1 marks 60 as requiring contract expansion and one as
shape-compatible with catalog coverage unproven. Window IR marks 57 as not
relevant and four as requiring a whole-request check. Governed Metric has zero
confirmed whole-request matches, Verified Memory has zero runtime coverage,
and all 61 came from the direct path. No capability was expanded or integrated.

## Solution-family mapping and recommendation

| Solution family | Affected cases | Causal confidence | Runtime cost | Regression surface |
| --- | ---: | --- | --- | --- |
| BOUNDED_DB_VALUE_GROUNDING | 9 | high | not measured; future provider experiment | stored labels, enum/code values, case variants |
| HIGH_PRECISION_CONSTRAINT_CONTRACT | 5 | mixed | not measured | predicate presence and null/aggregate scope |
| QUERY_AWARE_COLUMN_GROUNDING | 2 | high | not measured | column selection |
| TEMPORAL_NORMALIZATION | 1 | high | not measured | units and boundaries |
| BOOLEAN_CONSTRAINT_TREE | 2 | medium/high | not measured | AND/OR grouping |
| STRING_VALUE_NORMALIZATION | 1 | high | not measured | identifier matching |
| AGGREGATE_FILTER_CONTRACT | 1 | medium | not measured | pre/post aggregation |
| MODEL_REASONING_RESIDUAL | 1 | medium | not measured | broader SQL semantics |
| UNKNOWN | 39 | low | unknown | unknown |

Exactly one next intervention is recommended: `BOUNDED_DB_VALUE_GROUNDING`.
It has the strongest isolated evidence: nine cases across BIRD and Defog
Classic, all with explicit generated/reference value divergence and a plausible
server-owned database-value remedy. It is smaller than the unresolved 39-case
group but has much higher causal confidence than treating those cases as a
single mechanism. A future experiment must be fresh, bounded, and compare a
value-grounded context against the frozen direct path without gold-aware
runtime behavior. The likely surface is a new evaluation-scoped value-grounding
context component and its tests; no implementation is part of M22.

## Non-recommendations

M22 does not recommend a prompt sweep, generic schema verbosity, M1 changes,
operator/threshold relaxation, QueryPlan or Window expansion, temporal or
boolean runtime contracts bundled with value grounding, memory/retrieval,
LLM repair, judge, router, agent, benchmark-specific rules, or another full
provider-backed benchmark run.

## Limitations and next milestone

M22 is a provider-free research artifact. It does not claim intervention
efficacy, and the 39 undetermined cases should not be silently converted into a
new filter taxonomy. The 264 legitimate M1 rejects remain outside the primary
population. The next proposed milestone is a bounded fresh validation of
server-owned database value grounding; it must begin with a frozen contract and
must not implement more than that one intervention.
