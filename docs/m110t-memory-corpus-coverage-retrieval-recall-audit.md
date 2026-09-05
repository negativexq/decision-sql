# M11.0T — Memory Corpus Coverage & Retrieval Recall Audit

Status: `M110T_CORPUS_COVERAGE_DOMINANT` (offline diagnostic evidence)

## Purpose

This audit applies the frozen offline contract
`decision-sql-memory-compatibility-evidence-v1` to every frozen M4 entry for
each of the 105 M10.4S memory-use targets. It uses actual historical selected
IDs from frozen provenance; it does not replay retrieval.

The audit separates:

1. whether a compatible entry exists in the corpus;
2. whether the frozen historical Top-K selected one; and
3. whether the selected context was pure compatible memory.

Compatibility is an evaluator-side oracle using frozen gold/reference semantics.
It is not a runtime feature.

## Provenance and boundaries

- Starting checkpoint: `1869c43b0a0821e85135faee54ef8d4c30402741`.
- M4 corpus: 50 immutable entries; corpus hash
  `f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae`.
- Compatibility contract hash:
  `0876ea1196163de30abd79c6aec671f3399701ae49a89a625f5a9365dd43b4f7`.
- Primary population: 105 memory-use targets, 5,250 target-entry pairs.
- Secondary population: 55 no-memory targets, 2,750 coverage-only pairs.
- Provider, database, generation, embedding, judge, and reranker calls: 0.
- Historical retrieval reruns: 0.
- M4 changes, new entries, and new benchmark cases: 0.

The frozen M11.0R contract was not changed. It uses exactly
`PROVEN_COMPATIBLE`, `PROVEN_INCOMPATIBLE`, and `UNKNOWN`. The scan does
not use retrieval scores, ranks, score gaps, V1 correctness, causal labels,
partition, or diagnostic family to assign compatibility or mechanism states.

## Results

Among 105 primary targets:

| State | Count | DIAG_A | DIAG_B |
|---|---:|---:|---:|
| CORPUS_COVERAGE_FAILURE | 102 | 52 | 50 |
| RETRIEVAL_RECALL_FAILURE | 0 | 0 | 0 |
| SELECTIVITY_ADMISSION_FAILURE | 3 | 1 | 2 |
| COMPATIBLE_CONTEXT_RETRIEVED | 0 | 0 | 0 |
| UNRESOLVED | 0 | 0 | 0 |
| Total | 105 | 53 | 52 |

The three covered targets each had one compatible corpus entry, and each
historical selected context contained that compatible entry plus two proven
incompatible entries. Thus all three are admission/selectivity cases under
the precommitted decision priority. No target was a retrieval-recall miss.

Corpus coverage was 3/105 = 2.857%, classified
`M4_CORPUS_COVERAGE_WEAK`. Among covered targets, compatible-entry count was
always 1 (min/p25/median/p75/max/mean all 1).

The frozen dominance gate is count >=20, share >=25%, both partitions, and at
least 5 cases per partition. `CORPUS_COVERAGE_FAILURE` alone passes:
102/105 (97.14%), with 52 DIAG_A and 50 DIAG_B. Retrieval-recall and
admission/selectivity do not pass.

The three compatible corpus entries are:

- `vq:distinct_entity_count_3:v1`
- `vq:distinct_entity_count_5:v1`
- `vq:non_governed_arithmetic_3:v1`

They each cover one target. Forty-seven M4 entries were never compatible with a
primary target under the frozen contract. This is selected-context evidence
only: it does **not** prove that the entire M4 corpus contains no compatible
examples outside the evaluated target-entry relationships, nor does it justify
adding entries.

## Historical refinements

For the frozen 61 M10.4S selectivity cases:

- CORPUS_COVERAGE_FAILURE: 60
- RETRIEVAL_RECALL_FAILURE: 0
- SELECTIVITY_ADMISSION_FAILURE: 1
- COMPATIBLE_CONTEXT_RETRIEVED: 0
- UNRESOLVED: 0

For the separate 21 M10.4S overtransfer cases:

- CORPUS_COVERAGE_FAILURE: 21
- RETRIEVAL_RECALL_FAILURE: 0
- SELECTIVITY_ADMISSION_FAILURE: 0
- COMPATIBLE_CONTEXT_RETRIEVED: 0
- UNRESOLVED: 0

For the former 23 M11.0 compatibility-unknown cases:

- CORPUS_COVERAGE_FAILURE: 21
- RETRIEVAL_RECALL_FAILURE: 0
- SELECTIVITY_ADMISSION_FAILURE: 2
- COMPATIBLE_CONTEXT_RETRIEVED: 0
- UNRESOLVED: 0

V1 correctness was inspected only after mechanism labels were frozen and did
not define them. The 102 corpus-coverage cases include 21 V1-correct and 81
V1-incorrect cases; this is a diagnostic cross-tab, not a correctness claim.

The 55 no-memory targets had no compatible corpus entry (0/55); this secondary
coverage result does not receive retrieval-recall or admission labels.

## Interpretation

The result is the single dominant refinement
`M110T_CORPUS_COVERAGE_DOMINANT`. It says that, under the bounded frozen
contract, compatible examples were absent for 102 of the 105 historically
memory-use targets. It does not select a runtime intervention, prove that
retrieval scores are sufficient or insufficient, or prove that M4 is globally
incapable of compatibility.

No threshold, P1/P2/P3 analysis, AUROC/AUPRC, memory-OFF experiment, provider
counterfactual, retrieval replay, runtime compatibility feature, M4
modification, or public benchmark rerun occurred. M11 remains the high-level
memory retrieval/selectivity target; the lower failure layer is refined here
toward corpus coverage evidence.

## Artifacts

The pair matrix is stored as 35 bounded deterministic shards and indexed by
`m110t_pairwise_compatibility.json`. Target coverage, actual historical
retrieval composition, mechanism labels, refinements, reverse entry coverage,
concentration, secondary coverage, cross-tabs, manifests, and summaries are in
the adjacent `m110t_*.json` fixtures. All are new M11.0T evidence; historical
M10.4/M10.4R/M10.4S/M11.0/M11.0R artifacts remain unchanged.

Next exact milestone if this evidence is accepted:

**CHECKPOINT — Freeze M11.0T Corpus-Coverage Evidence**

Then:

**M11.1 — Verified Query Corpus Coverage Intervention Design Audit**

Do not expand the corpus or implement an intervention during this audit.
