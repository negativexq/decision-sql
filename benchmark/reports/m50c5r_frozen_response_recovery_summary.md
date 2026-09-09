## Historical preservation

M50C.5 remains `M50C5_ABORTED_POST_RESPONSE_CONTRACT_DEFECT`; M50C.5R is separate.

## Scope and zero-call accounting

0 provider calls, 0 model calls, 0 retries. Frozen 180-response corpus only.

## Frozen M50C.5 corpus integrity

CONTROL/TREATMENT 90/90; corpus hashes verified with no drift.

## Historical analysis defect

Mixed null/string claim-family keys caused the historical TypeError.

## Serializer correction

Generic typed-key ordering preserves null first and strings lexicographically.

## Evidence non-mutation proof

Frozen responses, parser semantics, checker semantics, runtime, and truth were not changed.

## Wire acquisition funnel

90 provider responses; 84 valid wires; 6 wire-invalid; 30 valid NON-ANSWER claims.

## Six invalid treatment responses

All declared BLOCKED_POLICY with POLICY + UNAUTHORIZED.

## Invalid-wire forensic classification

Primary classification: CONTRACT_VOCABULARY_GAP 6/6; provider cross-product exposure is secondary evidence.

## Contract vocabulary adequacy

Frozen vocabulary has no faithful POLICY + UNAUTHORIZED representation.

## Provider schema cross-product analysis

Provider schema independently permits the enum cross-product; application compatibility rejects it.

## Model contract compliance analysis

The six are not ordinary compliance failures because the frozen vocabulary lacks a faithful pair.

## Claim-family distribution

Recovered in typed-key form; null rows retained.

## Claim-assertion distribution

Recovered in typed-key form.

## Claim checker outcomes

19 CONTRADICTED and 11 VERIFIED among 30 valid claims.

## Decision × claim matrix

Recovered from the frozen valid treatment claim rows.

## Claim discrimination

Four evaluator-confirmed false abstentions carried CONTRADICTED blockers.

## CONTROL governed result

Recovered: 74/90.

## TREATMENT governed result

Recovered: 63/90, including invalid wires without salvage.

## Net governed delta

-11

## Paired correctness matrix

Recovered for all 90 and frozen subpopulations.

## Decision transition matrix

Recovered; wire-invalid treatment state retained.

## Historical false-abstention targets

Treatment correct 1/7; fresh CONTROL correct 1/7.

## Direct paired recoveries

1

## Treatment target correctness

1/7.

## False-abstention contradicted blockers

4.

## Correct NON-ANSWER claim quality

Recovered in artifact.

## False-abstention claim quality

Recovered in artifact.

## Authority safety

Recovered in artifact; no runtime override was applied.

## Ambiguity safety

Recovered in artifact; no runtime override was applied.

## subscription_18

Recovered in historical target/overlay artifacts.

## Policy safety

Recovered in artifact; six policy wire failures remain invalid.

## Non-target ANSWERABLE safety

Recovered in artifact.

## ANSWER SQL churn

Recovered in artifact; invalid wires excluded from both-answer denominator.

## Grain behavior

Recovered without runtime modification.

## Runtime first failures

Recovered from frozen traces; wire-invalid rows terminate before SQL runtime.

## Evaluator first divergences

Recovered from frozen overlays.

## Token accounting

Recovered from persisted provider metadata.

## Output-overhead gates

Recovered mechanically from frozen metadata.

## Latency

Recovered from persisted ledger.

## Mechanical frozen-gate evaluation

Wire gate is FAIL at 84/90 = 93.33%; all gate outputs are in the artifact.

## Exact contract retention assessment

NO; historical M50C.5 was aborted and wire acquisition is below the frozen gate.

## Semantic blocker architecture signal

PARTIAL; contradicted-blocker evidence exists, but claim quality and acquisition are imperfect.

## Automatic recovery boundary

NO automatic decision override or SQL recovery justified or implemented.

## Determinism

Recovered analysis and serializer regression are deterministic; frozen corpus hashes remain stable.

## Tests

M50C.5R serializer regression plus prior M50C.5/parent tests passed before recovery.

## Repository state

Final repository is clean and synchronized with origin/main.

## Final M50C.5R scientific verdict

`FROZEN_M50C5_EVIDENCE_PARTIALLY_SUPPORTIVE`.

## Recommended next milestone

M50C.5S — Family/Assertion Structured-Wire Feasibility.

## Benchmark expansion readiness

NO.

## M51 readiness

NO.
