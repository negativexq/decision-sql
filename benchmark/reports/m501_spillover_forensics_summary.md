# M50.1 — Paired Intervention Spillover Forensics

Audit verdict: `M501_SPILLOVER_FORENSICS_SUPPORTED`

Engineering conclusion: `PARTIAL_DISCRIMINANT_EVIDENCE`

| Correctness transition | Count |
| --- | ---: |
| `CONTROL_CORRECT_TREATMENT_CORRECT` | 71 |
| `CONTROL_CORRECT_TREATMENT_WRONG` | 6 |
| `CONTROL_WRONG_TREATMENT_CORRECT` | 4 |
| `CONTROL_WRONG_TREATMENT_WRONG` | 9 |

## Decision transition matrix

| Transition | Count |
| --- | ---: |
| `ANSWER -> ANSWER` | 49 |
| `ANSWER -> BLOCKED_AUTHORITY` | 1 |
| `ANSWER -> NEEDS_CLARIFICATION` | 3 |
| `BLOCKED_AUTHORITY -> BLOCKED_AUTHORITY` | 17 |
| `BLOCKED_POLICY -> BLOCKED_POLICY` | 6 |
| `NEEDS_CLARIFICATION -> ANSWER` | 3 |
| `NEEDS_CLARIFICATION -> NEEDS_CLARIFICATION` | 11 |

## Correctness-transition cases

| Case | Population | Control | Treatment | Primary spillover |
| --- | --- | --- | --- | --- |
| `risk_06` | NON_TARGET_ANSWERABLE | correct | wrong | `DECISION_BOUNDARY_CHANGE` |
| `risk_10` | NON_TARGET_ANSWERABLE | correct | wrong | `SQL_SEMANTIC_PERTURBATION` |
| `risk_11` | NON_TARGET_ANSWERABLE | correct | wrong | `DECISION_BOUNDARY_CHANGE` |
| `risk_12` | NON_TARGET_ANSWERABLE | correct | wrong | `DECISION_BOUNDARY_CHANGE` |
| `subscription_03` | NON_TARGET_ANSWERABLE | correct | wrong | `DECISION_BOUNDARY_CHANGE` |
| `subscription_04` | NON_TARGET_ANSWERABLE | wrong | correct | `DECISION_BOUNDARY_CHANGE` |
| `subscription_18` | GOVERNANCE | correct | wrong | `GOVERNANCE_BOUNDARY_CHANGE` |
| `warehouse_04` | NON_TARGET_ANSWERABLE | wrong | correct | `SQL_SEMANTIC_PERTURBATION` |
| `warehouse_08` | TARGET | wrong | correct | `SQL_SEMANTIC_PERTURBATION` |
| `warehouse_13` | TARGET | wrong | correct | `DECISION_BOUNDARY_CHANGE` |

## SQL perturbation

Both-arm ANSWER pairs: **49**. Changed SQL hashes: **21/49 (42.9%)**.
Material perturbations under the frozen taxonomy: **3/49**.

The frozen evidence shows broad decision/output movement, but no single model-visible discriminant cleanly separates the positive transitions from all six regressions.

M50.2 is not run. M51 remains blocked.
