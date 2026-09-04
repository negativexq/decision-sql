# M9 — Direct-Path Query Structure Failure Audit

## Executive conclusion

M9 classifies the result as `STRUCTURE_FAILURES_REAL_BUT_FRAGMENTED`.

The reconstructed expected-DIRECT population contains 120 observed frozen
M4-direct outputs: 100 from M7 and 20 from the M8 counterfactual. It contains
75 correct and 45 incorrect results. Of the 45 failures, 35 were attributed to
structural composition, but 31 of those were result-shape/projection mismatches
and only four matched a bounded structural motif. Bounded representability is
therefore 4/35 (11.4%), below the precommitted 60% threshold. A generic planner,
new semantic IR, or structure layer is not justified.

The strongest specific residual is `S11_RESULT_SHAPE_PROJECTION_STRUCTURE`,
but this is an output-contract problem rather than evidence for a general SQL
planner. The next milestone is:

**M9.1 — Direct Result-Shape and Projection Contract Audit**

This is a proposed evaluation milestone only. No production behavior changed.

## Reconstructed population

M9 combines existing artifacts rather than running a new benchmark:

| Source | Cases | Correct | Incorrect |
|---|---:|---:|---:|
| M7 actual DIRECT/M4 | 100 | 62 | 38 |
| M8 counterfactual DIRECT/M4 | 20 | 13 | 7 |
| Reconstructed expected-DIRECT path | 120 | 75 | 45 |

The reconstructed result is 75/120 (62.5%). This is not a new M9 accuracy
score or a production direct-path score; it is a frozen population for failure
analysis. DEV contains 80 cases (55 correct, 25 incorrect) and HOLDOUT contains
40 (20 correct, 20 incorrect).

M9 corpus identity:

- ID: `decisionsql-m9-direct-path-audit`
- Version: `m9-direct-path-audit-v1`
- full hash: recorded in `direct_path_manifest.json`
- DEV, HOLDOUT, incorrect-only, and correct-control hashes: recorded in the
  same manifest

The source anchors remained unchanged: M7 full/DEV/HOLDOUT hashes are
`f169e364…921043`, `59bd32b…3a014f`, and `740ab83…be0e5`; M8 full/DEV/HOLDOUT
hashes are `e42d02c…a1b319`, `030111f…599d400`, and `6429cfd…75a5a5`; the M3
contract is `0463a10e…70eb3c`; and the M4 corpus is
`f871bd31…4087ae`.

## Correctness and paired baseline

Correctness uses the existing M7 result-equivalence evaluator. SQL text and AST
similarity do not redefine correctness. The P0-versus-M4 paired outcomes over
the same 120 cases were:

| Outcome | Count |
|---|---:|
| BOTH_CORRECT | 42 |
| M4_ONLY | 33 |
| P0_ONLY | 3 |
| BOTH_INCORRECT | 42 |

M4 improves the paired result over P0 on this reconstructed population, but the
three P0-only cases were inspected as possible memory regressions. None had
confirmed causal evidence that retrieved examples caused the error. The M4
memory-context regression count is therefore zero. Retrieval-miss and
structure-example-present-but-generation-failed counts are also zero confirmed;
the remaining memory causality is undetermined rather than silently attributed
to M4.

## Structure signature

The evaluation-only `SqlStructureSignature` uses bounded sqlglot features:
tables and join types/count, aggregate functions and stage depth, grouping,
HAVING, WHERE, DISTINCT, ordering and LIMIT, CTE/subquery depth and
correlation, set operations, windows and partition/order/frame presence,
arithmetic/CASE/NULLIF, projection count, and aggregate/non-aggregate mix.

Reference variants are parsed locally and compared as a set. A signature delta
is diagnostic only: AST difference is not semantic failure, and valid SQL may
use a CTE where another valid query uses a subquery or no nesting.

Control analysis used all 75 correct cases. Projection-count mismatch appeared
in 37/45 failures and 0/75 correct controls. Other signals were much smaller:
window-count mismatch 6/45 vs 0/75, subquery-depth mismatch 6/45 vs 0/75,
join-count mismatch 5/45 vs 0/75, aggregate-stage-depth mismatch 5/45 vs 0/75,
group-by mismatch 4/45 vs 2/75, WHERE mismatch 3/45 vs 4/75, DISTINCT mismatch
2/45 vs 2/75, and HAVING mismatch 1/45 vs 0/75. LIMIT presence mismatch was
0/45 vs 0/75. These rates identify useful review signals but do not form a
runtime verifier.

A deterministic stratified sample of 20 correct controls was also persisted and
reviewed to guard against treating common alternative SQL forms as failures.

## Failure attribution

Each of the 45 incorrect cases received exactly one primary scope and a
predeclared status. The scopes were:

| Primary scope | Count | Share of residuals |
|---|---:|---:|
| STRUCTURAL_COMPOSITION | 35 | 77.8% |
| SEMANTIC_GROUNDING | 3 | 6.7% |
| POLICY_OR_EXECUTION | 7 | 15.6% |
| MEMORY_CONTEXT_REGRESSION | 0 | 0% |
| OTHER / UNDETERMINED | 0 | 0% |

Structural failures by mechanism:

| Mechanism | Count |
|---|---:|
| S1 aggregation scope | 1 |
| S2 multi-stage aggregation | 0 |
| S3 group-by/HAVING | 0 |
| S4 top-N/order scope | 1 |
| S5 window partition/order/frame | 1 |
| S6 subquery/CTE/correlation | 0 |
| S7 derived formula structure | 0 |
| S8 join grain/cardinality | 1 |
| S9 filter-stage placement | 0 |
| S10 distinct/cardinality structure | 0 |
| S11 result-shape/projection | 31 |
| S12 set operation | 0 |
| S13 other structure | 0 |
| S14 undetermined structure | 0 |

S11 is 31/35 (88.6%) of structural failures. The other four failures are
bounded motifs: one grouped aggregate, one global top-N, one running window,
and one multi-join aggregate. Top-three structural mechanisms account for
33/35 (94.3%), but the precommitted bounded-motif condition fails because only
4/35 are representable by the motif catalog.

Semantic grounding accounted for one field/measure error, one value-grounding
error, and one metric/formula-concept error. This does not reopen M6: the value
grounding count is one isolated case in this audit and is not evidence for the
M6 experiment threshold.

The seven M1 policy rejections were retained as policy/execution failures. The
generated SQL was rejected under the existing safety boundary; no evidence met
the standard for an M1 false-positive claim. M1 was not changed.

## Gold complexity

Reference-side classes and M4-direct outcomes were:

| Gold class | N | Correct | Incorrect |
|---|---:|---:|---:|
| Grouped aggregate | 59 | 41 | 18 |
| HAVING | 9 | 4 | 5 |
| Multi-join | 23 | 18 | 5 |
| Ratio arithmetic | 13 | 2 | 11 |
| Single-stage/list | 35 | 24 | 11 |
| Subquery | 10 | 1 | 9 |
| Top-N | 10 | 7 | 3 |
| Window | 13 | 6 | 7 |

These cells overlap by design. Subqueries, ratio arithmetic, and windows show
weakness, but the sample does not justify a generic planner. The bounded motif
counts were: T1 grouped aggregate 1, T4 global top-N 1, T9 running/moving
window 1, and T14 multi-join aggregate 1; T2, T3, T5, T6, T7, T8, T10, T11,
T12, T13, and T15 had zero adjudicated structural failures.

Structural failures appeared in both splits: 25 DEV and 10 HOLDOUT. The
dominant S11 result-shape mechanism appeared in both splits; no bounded
mechanism had enough replicated support to justify a new generation layer.

## M7/M8 reconciliation and memory

M9 agreed with the prior M7/M8 attribution on 33 cases and refined it on 12.
The refinements mostly split broad historical `FILTER_ERROR` and
`QUERY_STRUCTURE_ERROR` labels into result-shape, grounding, or policy causes.
Historical labels were not modified.

The 3 P0-only cases were inspected. All M4 cases had persisted memory context;
the audit found no confirmed literal, filter, entity, join, or aggregation
overtransfer. Consequently:

- confirmed memory-context regressions: 0
- retrieval-miss cases: 0 confirmed
- structure example present but generation failed: 0 confirmed
- no relevant example retrieved: 0 confirmed
- no relevant example exists: 0 confirmed
- memory research reopen threshold: not passed

## Frozen decision rules

M9 required at least 15/45 structural failures, at least 33% structural share,
one mechanism with at least five cases, top-three concentration of at least
60%, at least 60% bounded representability, and a correct-control guard. The
first four and the control guard passed; bounded representability failed
(4/35 = 11.4%). Therefore:

`BOUNDED_STRUCTURE_RESEARCH_JUSTIFIED`: no

`STRUCTURE_FAILURES_REAL_BUT_FRAGMENTED`: yes

`DIRECT_STRUCTURE_NOT_PRIMARY_BOTTLENECK`: no

Query intelligence is not considered saturated. The evidence supports a
focused output-shape/projection audit, not a generic planner or production
structure layer. M9.1 should use new questions because the M9 cases cannot both
discover and validate a future intervention.

## Scope integrity

- Provider/OpenAI calls: 0.
- SQL-generation calls: 0.
- Repair, judge, embedding, and reranker calls: 0.
- New database executions/mutations: 0; all analysis used persisted results and
  local parsing.
- M1, M3, M4, routing, M5, M6, runtime defaults, and provider prompts were
  unchanged.
- No external benchmark was rerun.

Evaluation-only files added:

- `evaluation/m9_direct_structure_audit.py`
- `evaluation/run_m9.py`
- `tests/unit/test_m9_direct_structure_audit.py`
- `docs/m9-direct-path-query-structure-audit.md`

M8 files were pre-existing and unchanged. M9 artifacts are under
`evaluation/results/m9/audit-20260904/` and remain evaluation artifacts.
