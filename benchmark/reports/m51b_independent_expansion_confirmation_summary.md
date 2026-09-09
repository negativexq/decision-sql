# M51B — Independent 90-Case Expansion Confirmation

## Historical preservation

The actual starting HEAD was `645e77d3b9158e6b2e2d07185da04f001c92176a`, matching the expected state. The official M48B.2 result remains 78/90 governed and 51/60 Answerable Runtime TSA. M50C.3, M50C.4, M50C.4S, and M50C.5 identities were not rewritten. README, truth, references, fixtures, prompt, runtime, and historical scores were not modified.

## M51B experimental boundary

This was intended as the first independent evaluation of the untouched M51A expansion set. The expansion was exposed to the model, so it is no longer untouched. The run is not a valid completed confirmation because the post-response analysis harness aborted.

## Starting repository state

The initial worktree was clean and `HEAD == origin/main == 645e77d3b9158e6b2e2d07185da04f001c92176a`.

## Frozen benchmark integrity

The frozen M51A expansion was used without edits: 90 cases, 6 domains, and the required 60/15/9/6 distribution. Expansion truth hash: `7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9`. Full truth hash: `b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173`.

## Expansion manifest integrity

The expansion manifest hash was `d48be18622e34c11057a7ad31272cc96b1fbb7d8017b9c0f74953de5a92a2417`; count and distribution preflight passed.

## Truth hashes

Both required M51A truth hashes matched before live calls. Truth was not changed after exposure.

## System under test

The retained M43/M47/M48A.2/M48B.2 mainline was used. No M50C blocker contract, M50C context, router, population contract, retry, repair, judge, or selector was used.

## Frozen prompt

The retained prompt hash was `119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb`.

## Model configuration

`gpt-5.6-luna`, reasoning `none`, temperature `0`, timeout `90` seconds.

## Planner/runtime configuration

The retained planner contract and runtime were frozen before calls. No runtime semantic change was made after exposure.

## Reader preflight

Expansion reference and restricted-reader preflight passed before call #1.

## Reference canary

The frozen M51A reference validation passed: 120/120 witnesses across 360 reference state runs.

## Leakage preflight

Model-visible/evaluator-only leakage was 0 in preflight.

## Call schedule

90 expansion cases were scheduled in deterministic manifest order. Schedule hash: `3acf4bbee74621115261d2f7ed03cfe2d9e8965386c5ad023daeb73effc6e6ce`.

## Live call accounting

90/90 provider attempts were made and 90/90 returned successful provider responses. Retries, repairs, judges, selectors, and routers: 0.

## Response freeze integrity

The 90-response corpus was frozen before analysis. Corpus hash: `9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a`. There were no duplicate slots or additional calls. Post-freeze provider/model calls: 0.

## Response acquisition

The response corpus is complete and persisted. A valid completed M51B analysis is not available because post-freeze serialization aborted.

## Expansion decision distribution

Partial diagnostic distribution: ANSWER 53, NEEDS_CLARIFICATION 19, BLOCKED_AUTHORITY 12, BLOCKED_POLICY 6. These values were written before the abort and are not a completed M51B result.

## Expansion truth × decision matrix

NOT ADJUDICATED: analysis aborted while serializing this matrix.

## Expansion Governed Task Success

Partial diagnostic only: 47/90. Do not treat this as a valid final M51B metric.

## Expansion Answerable Runtime TSA

Partial diagnostic only: 22/60. Do not treat this as a valid final M51B metric.

## BASE correctness

Partial diagnostic only: 29/60.

## Counterfactual correctness

NOT ADJUDICATED as a complete report; the partial runtime analysis recorded 26 counterfactual first failures.

## BASE-only accidental correctness

NOT ADJUDICATED.

## Conditional ANSWER correctness

NOT ADJUDICATED as a completed metric.

## Wrong refusals

Partial diagnostic count: 10.

## Authority safety

Partial diagnostic: 12/15 correct and 1 unauthorized ANSWER. Because analysis aborted, no final safety adjudication is issued.

## Ambiguity correctness

Partial diagnostic: 7/9.

## Policy correctness

Partial diagnostic: 6/6.

## Domain results

Partial governed diagnostics: healthcare 9/15, insurance 8/15, marketplace 9/15, procurement 6/15, telecom 6/15, workforce 9/15. No domain-level confirmation claim is made.

## Mechanism results

NOT ADJUDICATED.

## Grain behavior

Partial trace diagnostics recorded 2 GRAIN first failures. No runtime semantic changes were introduced.

## JSON typing results

NOT ADJUDICATED.

## Population/group-survival results

NOT ADJUDICATED.

## NULL/anti-join results

NOT ADJUDICATED.

## Temporal results

NOT ADJUDICATED.

## Window/CTE/subquery results

NOT ADJUDICATED.

## Runtime first failures

Partial trace distribution: NONE 62, GRAIN 2, RESULT_COUNTERFACTUAL 26. This is diagnostic output only.

## Evaluator first divergences

Partial evaluator output was persisted, but aggregate serialization did not complete. The final distribution is NOT ADJUDICATED.

## Failure decomposition

NOT ADJUDICATED.

## Token accounting

Persisted partial usage: prompt median 4,740, p90 5,006, max 5,013, total 425,162; completion median 53, p90 105, max 143, total 5,914.

## Latency

Persisted partial latency: median 1,489.62 ms, p90 1,842.81 ms, max 5,727.61 ms.

## Legacy vs expansion comparison

The official legacy result remains 78/90 governed and 51/60 Answerable Runtime TSA. A valid expansion comparison was not completed.

## Governed generalization gap

NOT ADJUDICATED; partial 47/90 must not be used as a completed comparison.

## Answerable generalization gap

NOT ADJUDICATED; partial 22/60 must not be used as a completed comparison.

## Combined 180-case Governed Task Success

NOT ADJUDICATED.

## Combined 120-case Answerable Runtime TSA

NOT ADJUDICATED.

## Combined authority result

NOT ADJUDICATED.

## Combined ambiguity result

NOT ADJUDICATED.

## Combined policy result

NOT ADJUDICATED.

## Combined unauthorized ANSWER

The partial expansion diagnostic recorded 1 unauthorized ANSWER; no combined headline is issued.

## Suspected benchmark defects

None were adjudicated. No cases were edited.

## Determinism

The post-response deterministic replay suite was not run because the first analysis pass aborted. The abort is reproducible from the code path: sorting a mapping containing `None` and string keys.

## Tests

Pre-live focused tests passed: 58. Preflight, reference validation, reader checks, prompt/hash checks, and M48B.2 regression checks passed. Post-freeze full analysis tests were not completed due the abort.

## Repository state

Pre-live freeze commit: `76dfef7cd302ae5a06e6c45011a089480022e432`. Response-freeze commit: `807c7255c55a69095e7c3c8c08225e7e078df164`. Final audit changes are pending the closing commit; no benchmark, prompt, runtime, or README edits were made.

## Final M51B verdict

`M51B_ABORTED_POST_RESPONSE_CONTRACT_DEFECT`.

The defect was a zero-call post-response analysis-harness serialization failure: `TypeError: '<' not supported between instances of 'NoneType' and 'str'`. M51B is not converted into a completed evaluation.

## Recommended next milestone

Create a new zero-call recovery-analysis identity that fixes the generic serializer, proves evidence non-mutation, and reanalyzes the frozen corpus. Do not rerun provider calls under M51B or patch-and-continue inside M51B.
