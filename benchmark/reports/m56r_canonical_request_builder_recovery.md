# M56R — Canonical Request Builder Recovery

## Verdict

`POST_M54_CONTRACT_EVALUATION_COMPLETE`

## Request-builder recovery

The aborted M56 runner read `benchmark/prompts/governed_context_v1.md` directly. The retained production path derives the prompt through `benchmark.m46b_contract.m43_prompt()` and is now shared by `benchmark.m51b_runner._requests` and `_provider_request`. The first provider-visible divergence was the system message: the prompt file contained an additional parent/child section absent from the retained M43 ledger prompt.

CONTROL equivalence passed for 90/90 cases before any M56R provider call. The complete provider payload fingerprint covers model, ordered messages, response schema, and decoding parameters.

## Valid acquisition

| Phase | Calls | Retries | Corpus hash |
| --- | ---: | ---: | --- |
| Selection | 120 | 0 | `9f2a953a7653be3d2ce99b5d12abd1b9216a1e2abbd04e15957c33cbe815d99d` |
| Full run | 90 | 0 | `1fc0d59cb29d7e4d1b8e23428da9c445f08c265337b3bd17c3cee6d877f634a0` |

The 210 pre-M56R calls remain preserved as `PRELIVE_REQUEST_BUILDER_DRIFT` evidence and are excluded from scoring, reuse, and selection.

## Selection

`CANDIDATE_C` was selected by the preregistered governed-correctness criterion with authority and policy hard constraints. Invalid M56 outcomes were not used.

| Arm | Governed | Answerable TSA | Authority | Policy | Control regressions |
| --- | ---: | ---: | ---: | ---: | ---: |
| `CONTROL` | 24/30 | 9/13 | 4/6 | 6/6 | 0 |
| `CANDIDATE_A` | 24/30 | 11/13 | 4/6 | 6/6 | 3 |
| `CANDIDATE_B` | 24/30 | 10/13 | 5/6 | 6/6 | 2 |
| `CANDIDATE_C` | 26/30 | 13/13 | 4/6 | 6/6 | 2 |

## Full M56R expansion result

| Metric | M54 baseline | M56R | Delta |
| --- | ---: | ---: | ---: |
| Governed Task Success | 82/90 | 83/90 | +1 |
| Answerable Runtime TSA | 57/62 | 59/62 | +2 |
| Authority | 13/15 | 13/15 | +0 |
| Ambiguity | 6/7 | 5/7 | -1 |
| Policy | 6/6 | 6/6 | +0 |

New passes: `procurement_05, workforce_02, workforce_10`. Regressions: `marketplace_10, workforce_03`. Transitions: PASS→PASS 80, FAIL→FAIL 5, FAIL→PASS 3, PASS→FAIL 2.

## M55 residual outcomes

| Case | Outcome | New decision | New failure |
| --- | --- | --- | --- |
| `telecom_10` | UNCHANGED | `NEEDS_CLARIFICATION` | `DECISION` |
| `workforce_10` | FIXED | `ANSWER` | `NONE` |
| `procurement_03` | UNCHANGED | `ANSWER` | `DECISION` |
| `procurement_13` | UNCHANGED | `NEEDS_CLARIFICATION` | `DECISION` |
| `telecom_15` | UNCHANGED | `ANSWER` | `DECISION` |
| `procurement_05` | FIXED | `ANSWER` | `NONE` |
| `workforce_02` | FIXED | `ANSWER` | `NONE` |
| `healthcare_10` | UNCHANGED | `ANSWER` | `RESULT_COUNTERFACTUAL` |

## Runtime safety and provenance

The frozen `telecom_15` model decision remains `ANSWER`. M52.S rejects its unauthorized relation as `AUTHORITY_REJECTION / UNAUTHORIZED_RELATION` with EXPLAIN calls 0, database connections 0, and execution calls 0. This is runtime protection, not model governance correctness.

M56R used one call per request, with no retries, repair, judge, selector, router, or pass@K. Benchmark and runtime semantics were unchanged. The valid result is a contract/model comparison against M54, not a benchmark-repair delta or universal Text-to-SQL claim.

## Public descriptive arithmetic

Historical legacy plus M56R expansion: governed `161/180`, Answerable Runtime TSA `110/122`, authority `28/30`, ambiguity `11/16`, policy `12/12`.
These values combine separately acquired controlled evidence; they are not a single same-time 180-request run.

## Determinism

Two post-freeze replays were canonical-identical. Analysis hash: `2e687c2a78842c56046332fd9fbb20ccfe6fc7ea35f4868440a6afe8b5a41c39`. Provider calls during replay: `0`.

## Interpretation

M56R provides a valid single-call contract experiment after recovering the canonical request builder. The selected contract improved the M54 expansion result by one governed case and two Answerable TSA cases, while producing two explicit regressions. M57 is required for independent reproduction and is not started here.
