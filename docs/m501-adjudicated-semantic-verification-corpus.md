# M5.0.1 — Adjudicated Semantic Verification Corpus

M5.0 used the historical M4 generation population. It had too little support
for most proposed contradiction rules, so its `SEMANTIC_VERIFIER_TOO_NOISY`
result could not distinguish weak rules from under-measured rules. M5.0.1
creates a separate, provider-free benchmark for that distinction.

## Corpus contract

The frozen corpus is `decisionsql-adjudicated-verification`, version
`m501-adjudicated-verification-v1`. It contains 240 paired cases:

- 120 `CORRECT` controls and 120 `INCORRECT` minimal contrasts
- 192 DEV cases and 48 HOLDOUT cases
- 12 families, 20 cases per family, with 16 DEV and 4 HOLDOUT cases each
- 120 reciprocal correct/incorrect pairs
- zero ambiguous primary cases

The checked-in manifest is
[`evaluation/fixtures/m501_manifest.json`](../evaluation/fixtures/m501_manifest.json).
Corpus construction is explicit and finite; labels are authored from the
question contract and independently written reference SQL. The verifier is
not called during construction or adjudication.

Primary families are GROUPING, TOP_N, AGGREGATE_THRESHOLD,
AGGREGATION_INTENT, ENTITY_COUNT_FANOUT, AGGREGATE_FANOUT,
DIMENSION_PROJECTION, MEASURE_PROJECTION, EXPLICIT_FILTER,
UNREQUESTED_LIMIT, CARTESIAN, and OUTPUT_SHAPE. Each pair changes one
semantic feature where practical, and correct controls include alternate SQL
forms where applicable.

## Independent validation

Against the existing demo PostgreSQL fixture:

- candidate SQL parse: 240/240
- reference SQL parse: 240/240
- candidate M1 acceptance: 240/240
- reference M1 acceptance: 240/240
- candidate execution: 240/240
- reference execution: 240/240
- correct candidate/reference equivalence: 120/120
- incorrect candidates that execute: 120/120 (100%)
- incorrect candidates differentiated from their references: 120/120

The validation uses DecisionSQL's existing canonical result evaluator. Gold
SQL and expected labels are evaluation-only and are never passed to the
production verifier. No counterexample fixture or multi-fixture execution
was required; all incorrect mutations differentiated on the deterministic
demo data.

The corpus hash is
`7cee1b1ca6ccd856a46a1fd17c0c9a1f7765fdec060bfbeef7b149a51e1e60c7`.
The DEV hash is
`85f7424a363261e670a56ea0e441181eca6e409e3e9ce14e0c5c2959ddcc011a`.
The HOLDOUT hash is
`56e4cdd96a924431dd076e381e1f0ad332fe88249929660dd0bc205282edba3b`.
Canonical serialization is sorted-key JSON with stable case ordering and no
runtime timestamps.

## Frozen verifier-v1 audit

The evaluated implementation remained `semantic-verifier-v1` with ruleset
hash
`4b3c91168832f1d3ca4370972d33abfebc7158712ea7ac04c8ea687d2253b494`.
No rule, threshold, or severity was changed after corpus construction.

On DEV (192 cases), the existing aggregate HARD result was 55 fired cases,
with 67.27% precision, 38.54% incorrect-query recall, and 18.75% false
positive rate. On HOLDOUT (48 cases), it was 16 fired cases, with 68.75%
precision, 45.83% recall, and 20.83% false positive rate. Equivalent correct
controls had the same false-positive rates. Therefore the complete existing
HARD set is not suitable for a production gate.

Three DEV rule candidates met the DEV support/precision/FPR screen:

| Candidate | DEV support | DEV precision | HOLDOUT TP / FP |
| --- | ---: | ---: | ---: |
| `POSSIBLE_ENTITY_COUNT_FANOUT` | 9 | 100% | 3 / 0 |
| `UNEXPECTED_CARTESIAN_JOIN` | 8 | 100% | 2 / 0 |
| `UNREQUESTED_LIMIT` | 8 | 100% | 2 / 0 |

Together these candidates produced 7 true positives and 0 false positives
on HOLDOUT (100% precision, 29.17% incorrect recall), but no individual
candidate reached the requested HOLDOUT support of four incorrect cases.
They are recommendation-only output in
`evaluation/results/m501/dev/frozen/future_candidate_rules.json`; they were
not promoted in production code.

The noisy families were especially informative:
`POSSIBLE_AGGREGATE_FANOUT` was 56.25% precise on DEV and 50% on HOLDOUT,
while `POSSIBLE_AGGREGATION_MISMATCH` was 75% precise on DEV with insufficient
HOLDOUT support. These remain diagnostic-only. TOP_N and CARTESIAN mutations
were often detected cleanly, but HOLDOUT support was intentionally small.

## Ambiguity and boundaries

No separate set of 24 genuinely non-contrived ambiguity cases was available,
so ambiguity classification is `INSUFFICIENT_AMBIGUITY_CORPUS`. No
clarification behavior was implemented.

The runtime verifier consumes only the question, candidate SQL, and
runtime-owned schema/contract/memory metadata. It does not accept gold SQL,
gold results, expected status, pair labels, or adjudication notes. It produces
signals only; M5.0.1 does not block execution, repair SQL, invoke a second
model, or generate clarification questions.

M4 DEV/HOLDOUT questions and SQL were not reused, and no provider calls were
made. Defog, BIRD, M3, M4, and M4.1 generation/evaluation runs were not
rerun. M1, the M3 contract, M4 corpus/retriever, M4.1 runtime, and historical
M5.0 artifacts remain unchanged.

## Result and limitation

The dataset milestone is accepted as
`ADJUDICATED_VERIFICATION_CORPUS_ACCEPTED`.
The verifier result is
`SEMANTIC_VERIFIER_V1_PARTIALLY_VALIDATED`: several narrow signals look
promising under independent contrasts, but the full v1 set is too noisy and
the promising individual rules need more independent HOLDOUT support before
an execution gate is justified.

This corpus does not establish generic semantic verification. It does not
solve temporal or value grounding, ambiguity resolution, SQL repair, or
production blocking. Any future M5.1 gate must use only individually
validated rule families and must preserve M1 as the sole execution authority.
