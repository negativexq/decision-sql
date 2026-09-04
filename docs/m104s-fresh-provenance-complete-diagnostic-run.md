# M10.4S — Fresh Provenance-Complete Diagnostic Run

Status: `M104S_HIGH_LEVERAGE_MECHANISM_IDENTIFIED`

M10.4S was executed once from checkpoint
`0326682e96e4a8ac22d73ed6ada7e58af3ccff56` using a new 160-case corpus. The
corpus passed both pre-exposure freshness gates against the frozen
`decision-sql-reference-freshness-v1` inventory, including all 160 invalidated
M10.4 cases. Reference validation succeeded 160/160 with zero database
mutations. The final freshness gate and master lock passed before provider call
#1; provider calls before the final gate and lock were zero.

## Scientific result

Evaluator V1 was authoritative:

| Slice | Result |
| --- | ---: |
| Overall | 27 / 160 |
| DIAG_A | 12 / 80 |
| DIAG_B | 15 / 80 |
| Expected governed | 4 / 40 |
| Expected direct | 23 / 120 |
| Failures | 133 |

This is a valid fresh internal diagnostic result at execution time. After
exposure, the corpus is consumed development/diagnostic evidence and is not
independent validation for a later intervention.

Direct-family results were: `SCHEMA_SOURCE_GROUNDING` 4/10,
`JOIN_GRAIN` 1/10, `AGGREGATION_GROUPING` 1/10, `FORMULA_RATIO` 0/10,
`FILTER_COLUMN` 1/10, `FILTER_OPERATOR_BOOLEAN` 2/10, `FILTER_LITERAL` 0/10,
`TEMPORAL` 2/10, `WINDOW` 0/10, `PROJECTION_RESULT_SHAPE` 4/10,
`ORDER_TOPN` 1/10, and `DISTINCT_SET` 7/10. Each governed metric had four
cases; only `completed_item_quantity` was correct on all four.

The runtime routed 47 cases governed and 113 direct. Expected governed recall
was 100%, precision was 85.1%, route accuracy was 95.6%, with seven false
governed cases and zero false direct cases.

## Provenance and evaluation

All 160 cases were provenance-complete. The runtime ledger had zero missing
stages, ordering violations, payload-hash violations, parse violations,
gold-leakage violations, secret violations, and raw-result violations.

The run used 160 grounding calls and 113 direct-generation calls, for 273
logical and physical provider attempts. The provider was openai-compatible,
requested and observed model `gpt-5.6-luna`, with the frozen request settings:
temperature null, top_p null, reasoning effort null, and the existing 30-second
timeout/retry behavior. Repair, judge, sampling, and post-hoc model calls were
zero. V2 was shadow-only: 40 bindable/evaluated, 120 unavailable, 40
agreements, zero disagreements, zero score overwrite, and zero denominator
removal.

## Deterministic causal attribution

The frozen taxonomy and evidence rules were used without post-run changes.
Primary failures sum to 133:

| Mechanism | Count | E1+E2 | DIAG_A | DIAG_B |
| --- | ---: | ---: | ---: | ---: |
| EVALUATOR_ARTIFACT | 0 | 0 | 0 | 0 |
| EXECUTION_FAILURE | 0 | 0 | 0 | 0 |
| TRANSPORT_FAILURE | 0 | 0 | 0 | 0 |
| M1_REJECTION | 8 | 8 | 3 | 5 |
| ROUTING_OVERREACH | 7 | 7 | 4 | 3 |
| ROUTING_FALSE_DIRECT | 0 | 0 | 0 | 0 |
| GOVERNED_GROUNDING_FAILURE | 0 | 0 | 0 | 0 |
| GOVERNED_COMPILER_FAILURE | 21 | 21 | 9 | 12 |
| MEMORY_RETRIEVAL_SELECTIVITY_FAILURE | 61 | 61 | 31 | 30 |
| MEMORY_OVERTRANSFER_EVIDENCE | 21 | 0 | 0 | 0 |
| DOWNSTREAM_GENERATION_FAILURE | 0 | 0 | 0 | 0 |
| UNRESOLVED | 15 | 0 | 0 | 0 |

Evidence grades were E1 15, E2 82, E3 21, E4 0, and E5 15. E1+E2 coverage
was 97/133 (72.9%); unresolved E5 rate was 15/133 (11.3%). E1+E2 coverage
was 47/80 in DIAG_A and 50/80 in DIAG_B.

The only mechanism passing every frozen M11 gate was
`MEMORY_RETRIEVAL_SELECTIVITY_FAILURE`: 61 E2 failures, 45.9% of failures,
DIAG_A 31, DIAG_B 30, at least 12, at least 20%, and at least five in each
partition. `GOVERNED_COMPILER_FAILURE` reached 21 E2 failures across both
partitions but was 15.8% of failures and therefore failed the 20% gate.

Memory retrieval was attempted on 113 cases and used on 105; 55 had no memory
use. The selected context was compatible in zero incorrect/correct cases under
the bounded structural comparison used by the audit, and incompatible in 82
incorrect memory-use cases. Transfer strengths were MT2 61 and MT3 21. These
are observational classifications: no memory-off rerun was performed, and no
causal memory-harm rate is claimed. The 21 MT3 cases are E3 trace association
and do not establish a memory-off counterfactual.

## Scope boundary

No M4, M3, routing, prompt, provider/model, compiler, evaluator, or binder
intervention occurred. V1 remained authoritative; V2 remained shadow-only.
There was no best-of score, denominator removal, public benchmark rerun, Defog
rerun, BIRD rerun, or M10.4 rerun. M10.4 remains permanently
`M104_RUN_INVALID` with raw 33/160 and its provisional attribution invalidated;
those values were not used to construct or tune M10.4S.

M7 remains 99/150 (66%) on separate historical evidence and M10R remains
28/200 (14%) on separate consumed evidence. M10.4S is not a regression
comparison against either.

## Next step

The measured recommendation is the memory-selectivity boundary, mapping to
`M11 — Verified Query Memory Selectivity Intervention`, but it must not be
implemented from this working tree. The exact next milestone is:

**CHECKPOINT — Freeze Valid M10.4S Diagnostic Evidence**

Any M11 validation must use another new independent dataset. M10.4S itself
cannot validate the intervention.
