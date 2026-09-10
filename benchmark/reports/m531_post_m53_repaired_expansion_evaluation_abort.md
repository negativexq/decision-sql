# M53.1 — Post-M53 Repaired Expansion Evaluation

## Historical preservation

Historical M48B.2 remains 78/90 governed and 51/60 Answerable TSA. Pre-M53 M51B-R remains 47/90 governed and 22/60 Answerable TSA. No historical score was rewritten.

## M53 parent benchmark state

M53 remains `BENCHMARK_SEMANTIC_AUDIT_AND_REPAIR_COMPLETE` with post-M53 scoring previously pending fresh evaluation.

## M53.1 scope

The 66 reusable responses were rescored zero-call before acquisition. The exact 24 invalidated cases were scheduled and each received exactly one fresh retained-mainline call.

## Starting repository state

Expected and verified starting HEAD: `a071be2b8fbc106d56c7a8f9f60445f62e239b53`; origin/main matched; working tree was clean.

## Post-M53 truth integrity

Expansion and full truth hashes matched the required post-M53 values. The original response corpus matched `9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a`.

## Response-reuse partition

The frozen M53 partition declared 66 reusable and 24 invalidated cases. The invalidated set matched the required 24 IDs. No reusable case was freshly called and no invalidated historical response was scored.

## Zero-call reusable input validation

The 66-case zero-call rescore completed. During complete 90-case response/input admission, independent recomputation found 11 declared reusable cases whose current post-M53 model-visible hashes differ from the persisted M53 ledger hashes: `procurement_02`, `procurement_05`, `procurement_08`, `procurement_11`, `insurance_04`, `telecom_02`, `telecom_05`, `telecom_08`, `healthcare_02`, `healthcare_05`, and `healthcare_06`.

## Zero-call reusable rescore

The frozen reusable rescore artifact records 66/66 processed cases. It is not promoted to a complete post-M53 score because the exact-input provenance gate for 11 reusable responses failed.

## Evaluator-only repair score impact

The preliminary reusable artifact records 10 old false negatives fixed, 46 unchanged correct, and 10 unchanged incorrect. These are partial diagnostics only; no final 90-case result is claimed.

## Fresh-call schedule

Exactly 24 invalidated cases were scheduled in deterministic manifest order and the schedule was frozen before calls.

## Fresh model configuration

`gpt-5.6-luna`, reasoning `none`, temperature `0`, timeout `90` seconds, retained prompt hash `119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb`.

## Fresh-call accounting

24 scheduled, 24 attempted, 24 successful, 0 retries. No judges, repairs, selectors, routers, or additional model calls were used.

## Fresh response corpus

The frozen 24-response corpus hash is `1bb22e9c70455d7710fc5990f8b9af0dc54090bc6f83ab182f9113cb22647ab4`.

## Complete 90-case response assignment

A provisional 90-case assignment was written, but it was not admitted for scoring after the input-consistency gate failed. No final response map or score is valid.

## Response/input consistency

The M53.1 exact-input reuse contract failed for 11 reusable cases. Per protocol, the milestone stops rather than treating those responses as current-input evidence.

## Post-M53 Expansion Governed Task Success

Not computed.

## Post-M53 Expansion Answerable Runtime TSA

Not computed.

## BASE correctness

Not computed for the complete post-M53 corpus.

## Full counterfactual correctness

Not computed for the complete post-M53 corpus.

## Counterfactual-only failures

Not computed.

## Conditional ANSWER correctness

Not computed.

## False abstentions

Not computed.

## Authority result

Not computed for the complete post-M53 corpus.

## Ambiguity result

Not computed for the complete post-M53 corpus.

## Policy result

Not computed for the complete post-M53 corpus.

## Domain results

Not computed.

## Runtime first failures

Not computed.

## Evaluator first divergences

Not computed.

## Failure decomposition

Not computed.

## Result-failure preliminary forensics

Not applicable before valid complete response admission.

## Historical pre-M53 vs post-M53 comparison

No post-M53 score exists to compare. The historical values remain preserved.

## Benchmark-repair evaluation delta

Not computed.

## Historical legacy + post-M53 expansion combined metrics

Not computed.

## Response acquisition provenance

Acquisition was intended to be temporally mixed: 66 historical reusable responses plus 24 fresh responses. The 24 fresh responses are frozen; the 90-case combination is blocked by reusable-input provenance inconsistency.

## Token accounting

Fresh usage remains persisted in the fresh response records. No combined score-level usage statistic is reported.

## Latency

Fresh latency remains persisted in the fresh response records. No combined score-level latency statistic is reported.

## Suspected post-M53 benchmark defects

No benchmark edits occurred after exposure. The blocking issue is a post-exposure response/input provenance contract defect, not a repaired benchmark change.

## Determinism

Complete post-freeze replay was not run because the response admission gate failed. Determinism is therefore `NOT ESTABLISHED`, not PASS.

## Tests

Pre-live focused tests passed (5). The fresh-call guards and 24-case acquisition completed as specified. Complete post-freeze analysis tests were not run because scoring was blocked.

## Repository state

The original benchmark, truth, references, fixtures, prompt, runtime, and frozen response bytes were not modified after the first fresh response.

## Final M53.1 verdict

`M531_ABORTED_POST_EXPOSURE_BENCHMARK_DEFECT`

The frozen fresh corpus is preserved. The complete post-M53 score is not reported because 11 declared reusable responses fail the exact current-input provenance gate.

## M54 readiness

`NO`. First complete a separate zero-call recovery audit reconciling M53’s reusable input-hash lineage; do not begin residual semantic forensics from an unadmitted 90-case corpus.
