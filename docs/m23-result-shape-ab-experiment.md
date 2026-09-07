# M23 — Existing ResultShape Paired A/B Experiment

## Objective

M23 tests whether the repository's existing, unchanged `ResultShapeProposal`
capability improves end-to-end SQL correctness on an outcome-blind,
projection-sensitive holdout. The primary outcome is evaluator correctness;
projection-shape compliance is diagnostic evidence only.

## Prior evidence and boundary

M22.2 identified projection-causal residuals and M22.3 found that the existing
ResultShape contract can represent and detect the eight missing/extra-output
cases selected for capability audit. Historical M2.8 is a warning: output-shape
compliance improved without improving accuracy, at the cost of a second model
call. M23 therefore does not claim success from shape metrics alone.

## Experimental design

The primary population is 30 outcome-blind public development cases: 15 BIRD
and 15 Defog Classic. Cases are selected from question/runtime evidence using
deterministic lexical projection-sensitive strata and a SHA-256 tie-break. The
eight discovery cases from M22.2 are excluded. No M20/M22 correctness labels,
gold SQL, or reference results are used for selection or provider input. The
known eight mechanism cases are not run as a primary or secondary arm in this
bounded experiment.

The control uses the existing `propose_sql` direct generation call. The
intervention calls the existing `propose_result_shape`, then the existing
`propose_sql(..., result_shape=proposal)` path. Both arms use the same schema
context, model, decoding, question/evidence, M1, executor, and evaluator.
M1 remains the sole SQL safety authority. ResultShape validation is recorded as
telemetry and does not authorize or suppress execution in this evaluation-only
runner.

The 90-call maximum is frozen before exposure: 30 control SQL calls, 30
ResultShape proposal calls, and 30 ResultShape-guided SQL calls. The run begins
with a 10-case technical pilot, then continues with the remaining 20 cases
without changing the manifest or configuration.

## Preflight and claim boundary

The manifest freezes the commit, datasets, schema-context identities, prompt
and contract source identities, provider configuration, M1 settings, case
membership, arm order, and call budget before provider construction. Runtime
inputs contain only the question, legitimate benchmark evidence, and schema
context. Gold/reference material is used offline for evaluator integrity and
proposal-quality analysis after generation.

M20's BIRD runner used a benchmark-specific raw SQL transport helper. For
paired ResultShape compatibility, M23 uses the existing general `propose_sql`
JSON SQL-generation path for both arms; this is documented in the manifest and
is not a byte-for-byte M20 baseline rerun.

## Results

The authoritative numeric results are written to
`evaluation/fixtures/m23_result_shape_ab_experiment.json`. All 90 planned
calls completed: 30 control SQL calls, 30 ResultShape proposal calls, and 30
ResultShape SQL calls. The primary result was:

| State | Count |
|---|---:|
| A — control correct / ResultShape correct | 15 |
| B — control correct / ResultShape wrong | 0 |
| C — control wrong / ResultShape correct | 1 |
| D — control wrong / ResultShape wrong | 14 |

Control correctness was 15/30 (50.00%) and ResultShape correctness was 16/30
(53.33%). The single recovery was not enough to support the hypothesis on this
bounded holdout; no control-to-ResultShape regression occurred. ResultShape
proposal quality was 8 correct, 1 partially correct, and 21 wrong under the
offline physical-arity/source assessment. The ResultShape arm had 13 M1
rejections versus 14 for control, and no execution failures in either arm.

The primary classification is therefore
`M23_RESULTSHAPE_HYPOTHESIS_NOT_SUPPORTED`: the experiment is valid, but the
observed end-to-end gain is too small and is not supported by proposal quality
or a broad recovery pattern. The primary paired matrix is A/B/C/D: both
correct, control-only correct, ResultShape-only correct, and both wrong.
ResultShape proposal quality, projection compliance, M1 outcomes, execution
outcomes, non-projection AST drift, latency, and token usage are reported
separately in the artifact.

## Failure stages, drift, and cost

The ResultShape arm had 17 M1 accepts, 13 M1 rejects, and no planning errors or
execution failures. Its 14 incorrect cases decomposed into 13 M1 rejects and
one semantically wrong result after a compliant ResultShape validation. The
control arm had 16 M1 accepts, 14 M1 rejects, and no execution failures. There
were no control-correct to ResultShape-wrong regressions.

Offline proposal assessment found 8 correct, 1 partially correct, and 21
wrong proposals. The generated ResultShape SQL had three extra-output cases
and no missing-output cases under the bounded projection comparison; control
had four extra-output cases and no missing-output cases. The ResultShape arm
changed non-projection AST shape on 3 table sets, 2 join sets, 1 aggregation
shape, 6 order/limit shapes, 1 subquery/window shape, and 1 filter shape. These
are diagnostic observations, not routing signals.

Control used 30 provider calls, 14,653 input tokens and 1,847 output tokens.
ResultShape used 30 proposal calls plus 30 SQL calls, with 16,571 proposal
input / 1,478 output tokens and 17,033 SQL input / 1,819 output tokens. Median
provider latency was 1,563 ms for control, 1,451 ms for the proposal call,
and 1,571 ms for ResultShape SQL; corresponding p95 values were 3,298 ms,
1,853 ms, and 1,929 ms. No reasoning-token telemetry was recorded.

The 10-case pilot is a continuation of the same frozen experiment, not a
tuning checkpoint. No prompt, model, population, or validation rule may change
after the first provider call.

## Limitations and next step

M23 is a bounded development/public experiment, not a full 774-case benchmark
rerun and not production promotion. It does not test QueryIntent integration,
aggregate/derived/DISTINCT validation beyond the frozen ResultShape contract,
routers, repair, judges, retries, or memory. A positive result would justify a
separate M24 study of equivalent output-shape guidance without the second
provider call; it would not enable ResultShape globally.
