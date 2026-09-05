# M11.2P0 — Question–Reference–Output Contract Integrity Audit

This is an offline, evaluator-side audit of the frozen P3 D cell. It asks
whether the natural-language question, frozen reference SQL, expected output
contract, and V1 correctness judgment are aligned enough for later generation
attribution.

The 56 cases are described as **V1-classified BOTH-WRONG residual cases**, not
as 56 model failures. P3 remains valid as a MEMORY_OFF versus CURRENT_MEMORY_ON
effect measurement under the frozen V1 correctness contract. This audit may
change how much of the D cell is eligible for later generation attribution,
without rewriting P3.

## Frozen boundaries

The audit freezes its contract taxonomy and conservative projection comparator
before exposing the D population. The question is primary semantic evidence;
the reference SQL is intended-answer evidence, not the definition of the
question. Reference semantics and question-owned output entitlement are
separate axes. Extra projected columns are neither automatically correct nor
automatically wrong.

The projection comparator preserves string-literal case, numeric values, JOIN
USING columns, OFFSET, ordering, HAVING, window structure, and other supported
non-projection AST structure. It is only allowed to prove a projection-only
difference when the remaining supported structure is equal. It is not a general
semantic equivalence engine.

`m104s-direct-window-06` is a pre-exposed regression control for the question /
reference formula boundary: a question asking for each line's share of quantity
requires numerator/group-total semantics, while the frozen reference contains
only the partition total. That historical reference is not edited.

## P1 quarantine

The frozen P1 semantic-relation implementation is not used for P0
adjudication. Its historical false-equivalence behavior is reproduced for
three controls: string-literal case (`COMPLETED` versus `completed`), OFFSET,
and JOIN USING key identity. P1 remains immutable, but its semantic and
adaptability labels are quarantined for future architecture decisions pending
P1R2 — Semantic Relation Contract Adversarial Repair.

## Scope and next step

No provider, database, retrieval, candidate generation, runtime change,
reference rewrite, evaluator repair, or M11.2P1 generation-failure taxonomy is
performed. No individual D-cell mechanism is classified here. The resulting
P0-eligible residuals are inputs to a future M11.2P1 audit, which must freeze
its own taxonomy before case-level mechanism inspection and must use fresh
independent validation for any intervention.

The README is recorded as stale relative to the P2/P3 research state and is not
edited in this milestone.

## Frozen P0 result

The exact P3 D population was reconstructed as 56 unique cases. The audit
classified 53 references as semantically aligned, 2 as
question/reference-semantic ambiguity, and 1 as a reference semantic defect.
Output-contract statuses were 26 partially specified and 30 underspecified.
The diagnostic eligibility counts are:

- `FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE`: 23
- `CORE_SEMANTIC_ATTRIBUTION_ONLY`: 30
- quarantined: 3 (2 question/reference ambiguities and 1 reference defect)

The independent top-level projection-expression count reproduction was OFF
52/56, ON 33/56, both 32/56, and either 53/56, versus the external review's
49/56, 31/56, 28/56, and 52/56. This is recorded as a methodology/result
discrepancy, not forced to match. Under the conservative AST comparator, no
arm was proven to differ from its reference by projection alone; 5 OFF and 3
ON rows were M1 rejections, and all remaining compared rows had a supported
non-projection difference. No historical V1 score was rewritten.

`m104s-direct-window-06` is quarantined as
`QUESTION_ROW_SHARE_REFERENCE_GROUP_TOTAL_ONLY`. No generation-mechanism
labels were assigned, and P0 does not claim that the remaining eligible cases
share a single failure family.
