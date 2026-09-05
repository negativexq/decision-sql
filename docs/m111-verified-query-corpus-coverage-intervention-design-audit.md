# M11.1 — Verified Query Corpus Coverage Intervention Design Audit

M11.1 is an offline architecture-design audit. It consumes the frozen M11.0T
population and the unchanged `decision-sql-memory-compatibility-evidence-v1`
contract. It does not add memory entries, replay retrieval, call a provider or
database, or change runtime behavior.

## Frozen input and oracle boundary

The primary population is the 105 historically memory-used M10.4S targets. A
minimal evaluator-side adapter creates one hypothetical exact verified-query
candidate per target from its frozen reference SQL. These candidates are
design oracles only; they are not additions to M4 and are not runtime memory.

The adapter passes its hard validity gate: 105/105 self-pairs are
`PROVEN_COMPATIBLE`, with zero incompatible or unknown self-pairs. The
target-by-target matrix contains 11,025 pairs, of which 10,920 are off-diagonal.
Self-pairs are excluded from cross-target reuse metrics.

## Exact-query reuse and scaling

Only 2/105 target consumers have an off-diagonal compatible candidate; 103 are
self-only. The cross-target reuse rate is 1.90%, classified
`EXACT_QUERY_REPRESENTATION_REUSE_WEAK`. Candidate coverage is one target for
103 candidates and two targets for two candidates. Deterministic greedy set
cover reaches 50% with 52 representatives, 80% with 83, 90% with 94, 95% with
99, and 100% with 104 representatives.

Greedy set cover is a deterministic design heuristic, not a proof of the
globally minimal corpus size. The self-only count is a lower-bound pressure
signal under this design oracle, not a production corpus-size theorem.

## Semantic factorization

The audit uses only the 12 dimensions already supported by the frozen M11.0R
contract. All 105 target signatures are complete. There are 104 distinct full
signatures: 103 singleton full signatures and one repeated signature. The
bounded atom inventory contains 230 distinct atoms, of which 176 are singleton
atoms and 54 are reused. Only 10/105 targets have all atoms reused; 95/105 have
at least one novel atom; 8/105 are compositionally novel under the strict
definition of a unique full signature made entirely from reused atoms.

The resulting classifications are:

- `REUSABLE_SEMANTIC_REPRESENTATION_PRESSURE_WEAK`
- `NEW_SEMANTIC_CONTENT_PRESSURE_STRONG`

This means the frozen target population shows substantial new semantic content,
not strong evidence that its unique combinations are mostly assembled from
already-reused atoms. Compositional novelty does not prove that a semantic-memory
runtime will improve SQL accuracy.

## Design decision

The precommitted decision is:

`M111_NEW_SEMANTIC_COVERAGE_REQUIRED_REPRESENTATION_UNRESOLVED`

No intervention family is selected. In particular, the audit does not authorize
coverage-balanced exact-query expansion, reusable semantic memory, or hybrid
memory. The result is a design limitation: weak exact-query reuse and strong
novel-semantic-content pressure do not support choosing one intervention family
without another bounded audit of the missing semantic coverage.

The frozen M11.0T result remains authoritative: corpus coverage is the dominant
lower-level failure under that audit. M11.1 explains why the current full-query
unit cannot be treated as broadly reusable in this target population, but it does
not prove that the entire M4 corpus is globally insufficient, that more examples
would solve accuracy, or that any future representation will help.

## Boundaries

V1 correctness, retrieval scores, partitions, causal labels, provider outputs,
and policy results do not define the design metrics or decision. No threshold,
P1/P2/P3 analysis, AUROC/AUPRC, memory-OFF experiment, provider counterfactual,
retrieval replay, M4 modification, new validation corpus, or runtime M11
implementation occurred. The target×target matrix and semantic signatures are
evaluator-side consumed development evidence and cannot serve as independent
validation for a later intervention.

The exact next step is:

`CHECKPOINT — Freeze M11.1 New-Semantic-Coverage Evidence`

followed by `M11.1R — Missing Semantic Coverage Boundary Audit`. This audit does
not start that work.
