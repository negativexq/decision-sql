# M11.0R — Deterministic Compatible-Memory Control Contract Audit

Status: `M110R_COMPATIBILITY_CONTRACT_VALID_CONTROLS_INSUFFICIENT`

This is an offline, evaluator-side compatibility evidence audit. It does not
modify the DecisionSQL runtime, rerun retrieval, call a provider or database,
select a threshold, or evaluate P1/P2/P3.

## Frozen starting evidence

M10.4S remains valid fresh internal diagnostic evidence: V1 was 27/160 with
133 failures. It selected `MEMORY_RETRIEVAL_SELECTIVITY_FAILURE` as the sole
mechanism satisfying the frozen M11 gate: 61 E2-attributed failures, split
31/30 across DIAG_A/DIAG_B. M11 remains the selected target, named
**Verified Query Memory Selectivity Intervention**. M11.0 remains historically
blocked because its compatible-control denominator was zero.

M11.0R does not relabel M10.4S causal mechanisms and does not treat any of the
61 cases as recoverable SQL cases.

## Frozen offline contract

The contract is `decision-sql-memory-compatibility-evidence-v1`, version 1,
and is offline evaluation/control labeling only. It has exactly three states:

- `PROVEN_COMPATIBLE`
- `PROVEN_INCOMPATIBLE`
- `UNKNOWN`

It compares bounded SQL semantic dimensions using frozen target/reference
evidence and immutable selected M4 entries. The dimensions are source
relation, join relationship, result grain, aggregation, formula, filter,
temporal semantics, window semantics, projection role, ordering, Top-N, and
distinct/set semantics.

Positive compatibility requires zero material conflicts, no unresolved
supported dimension, and at least one decision-bearing positive alignment.
Absence of a conflict is not enough. A material supported conflict produces
`PROVEN_INCOMPATIBLE`; insufficient, ambiguous, unsupported, or unparseable
evidence produces `UNKNOWN`. For a selected context, incompatibility
dominates; all compatible entries are required for a compatible context;
otherwise the context is unknown.

Retrieval score, rank, score gap, V1 correctness, causal mechanism, diagnostic
family, partition, provider output, and database results are forbidden inputs
to the contract. The contract creates no runtime signal.

## Independent validation

The independent manifest was frozen before implementation-result inspection:
120 synthetic cases, balanced 40 expected compatible, 40 expected
incompatible, and 40 expected unknown. It includes adversarial scope cases,
single-dimension material mutations, evidence removal, and harmless
representation controls.

The independent contract result was deterministic and safe under the frozen
acceptance rules:

- false `PROVEN_COMPATIBLE` labels: 0
- expected-compatible false-incompatible labels: 0
- expected-unknown promoted to compatible: 0
- expected compatible: 40/40
- expected incompatible: 40/40
- expected unknown: 40/40

This is contract validation evidence, not benchmark or downstream accuracy
evidence.

## M10.4S application

After independent validation, the contract was applied to all 105 actual
memory-use contexts using the frozen target SQL, actual selected M4 IDs and
immutable M4 entries. No retrieval was replayed. The result was:

- context evaluations: 105
- `PROVEN_COMPATIBLE`: 0
- `PROVEN_INCOMPATIBLE`: 105
- `UNKNOWN`: 0
- DIAG_A compatible controls: 0
- DIAG_B compatible controls: 0

The 61 known selectivity cases were labeled 0 compatible, 61 incompatible,
and 0 unknown. The 21 overtransfer cases were labeled 0 compatible, 21
incompatible, and 0 unknown. The 23 former M11.0 unknown cases transitioned
to 0 `PROVEN_COMPATIBLE`, 23 `PROVEN_INCOMPATIBLE`, and 0 `UNKNOWN`.

V1 correctness was inspected only after compatibility labels were frozen. No
case was promoted to compatibility because it was correct. No score signal was
used to construct the labels.

## Decision and boundary

The independent safety gates pass, and no known incompatible case became
compatible. However, the precommitted control sufficiency gate requires at
least 12 compatible contexts overall, with at least 5 in each partition. The
observed count is 0 overall, 0 in DIAG_A, and 0 in DIAG_B.

Therefore the exact classification is:

`M110R_COMPATIBILITY_CONTRACT_VALID_CONTROLS_INSUFFICIENT`

This does not prove that existing score signals are sufficient or
insufficient. It does not select a policy family or threshold. P1/P2/P3 were
not resumed, and AUROC/AUPRC were not computed against an invented control
class. The result is narrower: positive compatibility can be proven safely in
the bounded contract, but the consumed M10.4S evidence contains no controls
for the frozen score-policy cross-fit gate.

M11.0R is not M11 implementation or validation. It did not run memory-OFF,
provider counterfactuals, database workflows, retrieval, a new corpus, or a
public benchmark. The M10.4S corpus remains consumed development evidence.

## Next milestone

The exact next step is:

**CHECKPOINT — Freeze M11.0R Compatible-Memory Contract with Insufficient
Control Coverage**

After that checkpoint, the next scientific milestone is:

**M11.0T — Compatible-Memory Control Evidence Expansion Audit**

The compatibility contract must not be loosened, and M11 runtime
implementation must not begin from this audit.
