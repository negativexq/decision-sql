# M59 — Targeted Contract Iteration

## Scope and provenance

M59 used the canonical `benchmark.m51b_runner._provider_request` path. CONTROL_C is byte-identical to promoted Candidate C (`3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587`). Candidate D adds only a general decision/governance hierarchy; Candidate E adds that hierarchy plus a silent semantic consistency checklist. No benchmark or runtime semantics changed.

The 210 invalid M56 responses were excluded from all M59 selection, scoring, reuse, and comparison.

## Prelive gates

CONTROL_C → production equivalence: **90/90 PASS**. Candidate structural diff: **PASS**; only contract message content differed. Provider calls before gate: **0**.

## Selection

The preregistered primary metric was governed correctness. Hard constraints were non-decreasing authority and policy relative to CONTROL_C, preserved stable fixes, unchanged runtime safety, and zero retries. Tie-breaks were higher governed/authority/policy, fewer regressions, smaller contract delta, then lexical arm order.

| Arm | Governed | TSA | Authority | Ambiguity | Policy | PASS→FAIL | FAIL→PASS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CONTROL_C` | 26/30 | 13/13 | 4/6 | 3/5 | 6/6 | 0 | 0 |
| `CANDIDATE_D` | 23/30 | 10/13 | 4/6 | 3/5 | 6/6 | 3 | 0 |
| `CANDIDATE_E` | 25/30 | 12/13 | 4/6 | 3/5 | 6/6 | 1 | 0 |

Selected arm: **CONTROL_C**. Full run status: **NOT_RUN_NO_NEW_CONTRACT_ACCEPTED**.

Exact transitions versus CONTROL_C:

- `CONTROL_C` PASS→FAIL: none; FAIL→PASS: none.
- `CANDIDATE_D` PASS→FAIL: ['healthcare_10', 'procurement_05', 'workforce_02']; FAIL→PASS: none.
- `CANDIDATE_E` PASS→FAIL: ['healthcare_10']; FAIL→PASS: none.

## Focused cases

- `telecom_10`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}, 'CANDIDATE_D': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}, 'CANDIDATE_E': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}}
- `procurement_03`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_D': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_E': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}}
- `procurement_13`: {'CONTROL_C': {'decision': 'NEEDS_CLARIFICATION', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_D': {'decision': 'NEEDS_CLARIFICATION', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_E': {'decision': 'NEEDS_CLARIFICATION', 'governed': False, 'first_failure': 'DECISION'}}
- `telecom_15`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_D': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_E': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}}
- `workforce_03`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_D': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_E': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'DECISION'}}
- `marketplace_07`: {'CONTROL_C': {'status': 'NOT_IN_SELECTION_SET'}, 'CANDIDATE_D': {'status': 'NOT_IN_SELECTION_SET'}, 'CANDIDATE_E': {'status': 'NOT_IN_SELECTION_SET'}}
- `marketplace_10`: {'CONTROL_C': {'status': 'NOT_IN_SELECTION_SET'}, 'CANDIDATE_D': {'status': 'NOT_IN_SELECTION_SET'}, 'CANDIDATE_E': {'status': 'NOT_IN_SELECTION_SET'}}
- `healthcare_10`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}, 'CANDIDATE_D': {'decision': 'NEEDS_CLARIFICATION', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_E': {'decision': 'BLOCKED_AUTHORITY', 'governed': False, 'first_failure': 'DECISION'}}
- `workforce_10`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}, 'CANDIDATE_D': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}, 'CANDIDATE_E': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}}
- `procurement_05`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}, 'CANDIDATE_D': {'decision': 'ANSWER', 'governed': False, 'first_failure': 'GRAIN'}, 'CANDIDATE_E': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}}
- `workforce_02`: {'CONTROL_C': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}, 'CANDIDATE_D': {'decision': 'NEEDS_CLARIFICATION', 'governed': False, 'first_failure': 'DECISION'}, 'CANDIDATE_E': {'decision': 'ANSWER', 'governed': True, 'first_failure': None}}

## telecom_15 safety

Historical model decision remains `ANSWER`. Runtime remains `AUTHORITY_REJECTION / UNAUTHORIZED_RELATION` with EXPLAIN calls `0`, database connections `0`, and execution calls `0`. This is runtime protection, not model governance correctness.

## Full run and promotion

M58 stable Candidate C remains **83/90 Governed** and **59/62 Answerable TSA**. No challenger was accepted; no additional 90-case run was required and the stable contract remains unchanged.

## Integrity

Provider/model calls: **90**; retries: **0**. Benchmark semantics, runtime safety semantics, Candidate C wording, and single-call architecture were unchanged. Historical artifacts were not rewritten.

## Verdict

`M59_NO_NEW_CONTRACT_ACCEPTED`
