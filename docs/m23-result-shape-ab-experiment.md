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
`evaluation/fixtures/m23_result_shape_ab_experiment.json` after the frozen run.
The primary paired matrix is A/B/C/D: both correct, control-only correct,
ResultShape-only correct, and both wrong. ResultShape proposal quality,
projection compliance, M1 outcomes, execution outcomes, non-projection AST
drift, latency, and token usage are reported separately.

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
