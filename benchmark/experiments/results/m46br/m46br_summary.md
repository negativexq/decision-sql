# M46BR — Offline recovery of frozen M46B responses

- M46B status: `M46B_ABORTED_CONTRACT_DEFECT`
- M46BR provider/model calls: `0 / 0`

## control

- Governed: **76/90 (84.4%)**
- Answerable TSA: **49/60 (81.7%)**
- ANSWER rate: 54/60
- Wrong refusal: 6/60
- Conditional SQL: 49/54 (90.7%)
- Authority: 14/15
- Ambiguity: 7/9
- Policy: 6/6
- Unauthorized answers: 0

## treatment

- Governed: **78/90 (86.7%)**
- Answerable TSA: **53/60 (88.3%)**
- ANSWER rate: 57/60
- Wrong refusal: 3/60
- Conditional SQL: 53/57 (93.0%)
- Authority: 14/15
- Ambiguity: 5/9
- Policy: 6/6
- Unauthorized answers: 0

## Grain-sensitive family

- Cases: subscription_04, subscription_10, warehouse_08
- CONTROL correct: 0/3
- TREATMENT correct: 0/3
- PARENT_MEASURE_FANOUT diagnostics: CONTROL 2; TREATMENT 2

## Recovery validity

- Reference precheck: 120/120 references; 184/184 fixture comparisons.
- Mutation precheck: 190/190 killed; 0 invalid; 0 surviving.
- Evaluation contract errors: 0.
- Deterministic second replay: PASS.

## Paired interpretation

- Control → treatment governed delta: +2/90 (+2.3 percentage points).
- Control → treatment answerable TSA delta: +4/60 (+6.7 percentage points).
- Grain-sensitive family: 0/3 correct in both arms; PARENT_MEASURE_FANOUT: 2 in both arms.
- Hardened fanout targets: subscription_04, subscription_10, and warehouse_08 remained incorrect in both arms.
- Recovery verdict: `M46BR_RECOVERY_VALID`.
- Structured-context verdict: `STRUCTURED_GRAIN_CONTEXT_NO_EFFECT` on the targeted family; headline changes are paired variation, not a demonstrated grain intervention effect.
