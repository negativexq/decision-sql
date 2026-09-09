## Historical preservation

M48B.2 and prior milestone artifacts remain unchanged. The official M48B.2 result remains 78/90 governed and 51/60 Answerable Runtime TSA.

## Development claim boundary

M50C.4 was a paired development experiment, not independent confirmation or production evidence. It is aborted because the treatment response schema was rejected by the provider before model response.

## Scope and call accounting

Provider attempts: 180/180. CONTROL: 90. TREATMENT: 90. Retries, repairs, judges, and selectors: 0. Post-freeze calls: 0.

## Frozen parent contracts

Wire: `semantic-submission-shadow-1` / `e799113d0f6a20e96ae5ac3abaca3b20a2cf12deebea8ae01ee033c35d3384ca`. Checker: `semantic-blocker-checker-1` / `cb396d42675235632bf8e713270c9024cf9ddf098fadd19cb4fcf2a04fa61b98`.

## Model configuration

`gpt-5.6-luna`, reasoning `none`, temperature `0`, timeout `90s`, one scheduled attempt per arm/case.

## CONTROL contract

Frozen baseline prompt hash: `119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb`. All 90 CONTROL requests produced successful model responses.

## TREATMENT contract

Treatment prompt hash: `92f010b5cf817bd9f8dbf39000845e9a27dc3248af3b6013623ba4dbf35954c3`. The provider rejected the response schema because `blocking_claim` was present in `properties` but absent from the required-key array.

## Input factual parity

Phase A parity: 90/90; new factual facts: 0; evaluator leakage: 0; factual-context growth: 0.

## Input/schema token delta

Phase A froze factual-context delta at 0. Treatment prompt/schema overhead was frozen, but treatment usage tokens are unavailable because requests were rejected.

## Paired schedule

90 complete scheduled pairs and schedule hash `49bc54c2423fcceca9a18aa3edc199ff3cef5aa60d302c45f490f8c824d769e2`.

## Reader preflight

PASS before live calls.

## Reference runtime canary

PASS before live calls.

## Response freeze integrity

180 slots are frozen. CONTROL corpus hash: `493d46729dae59a03b08620796201f09a73f7af444e89fa53d39d0f65e41b6ef`. TREATMENT corpus hash: `b22026f827cb25ab4d5080a615e303e18295ff615702c0e7b0a59fd158ca41b5`.

## Wire acquisition

CONTROL parse: 90/90. TREATMENT model responses: 0/90. All treatment provider outcomes were schema-validation failures.

## NON-ANSWER claim presence

Not adjudicable; no treatment model submissions were returned.

## Stable-ID validity

Not adjudicable; no treatment model submissions were returned.

## Claim-family distribution

Not adjudicable.

## Claim-assertion distribution

Not adjudicable.

## Claim checker outcomes

Not adjudicable. No treatment claim reached the checker.

## Decision × claim matrix

Not adjudicable for TREATMENT.

## CONTROL runtime result

CONTROL replay artifacts were produced, but paired treatment comparison is invalid after the provider contract defect.

## TREATMENT runtime result

No treatment runtime replay is valid because no treatment submission was produced.

## Paired correctness matrix

Not adjudicable.

## Decision transition matrix

Not adjudicable.

## Historical false-abstention target analysis

Not adjudicable. No treatment outputs exist.

## Direct target recoveries

0 observed; this is not an efficacy result.

## False-abstention blocker contradictions

Not adjudicable.

## Correct NON-ANSWER claim quality

Not adjudicable.

## Authority safety

Not adjudicable for TREATMENT; no treatment decisions exist.

## Ambiguity safety

Not adjudicable for TREATMENT.

## Policy safety

Not adjudicable for TREATMENT.

## Non-target ANSWERABLE safety

Not adjudicable for TREATMENT.

## SQL churn

Not adjudicable for paired treatment behavior.

## Grain behavior

Not adjudicable for TREATMENT.

## Runtime first failures

The observed treatment failure is provider response-schema validation, before runtime replay.

## Evaluator first divergences

Not adjudicable for TREATMENT.

## Token accounting

CONTROL metadata is available; TREATMENT prompt/completion usage is unavailable because all requests were rejected at provider validation.

## Output-token gates

Not evaluated. No treatment model output tokens exist.

## Latency

Descriptive provider request latency exists in the frozen ledger; it is not a model-arm comparison.

## Frozen gate evaluation

Retention and safety gates were not evaluated. The mechanical status is `ABORTED`.

## Scientific interpretation

This run provides no treatment qualification evidence. It identifies a frozen response-schema defect: `blocking_claim` was not included in the provider-required-key list.

## Determinism

The defect is identical across all 90 treatment attempts; no rerun was performed.

## Tests

Phase A ruff, format check, mypy, diff check, reader preflight, and reference canary passed. Post-response analysis is not a valid qualification run.

## Repository state

This final audit records the abort; no historical artifacts or README were changed.

## Final M50C.4 verdict

`M50C4_ABORTED_POST_RESPONSE_CONTRACT_DEFECT`.

## Architecture retention

No model-facing contract is retained. The M50C.3 shadow architecture remains historical evidence only.

## Recommended next milestone

Do not rerun under M50C.4. Freeze and validate a corrected provider-compatible schema in a new experiment identity before any further model calls.

## Benchmark expansion readiness

NO.

## M51 readiness

NO.
