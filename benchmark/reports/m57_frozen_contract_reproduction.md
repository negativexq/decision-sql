# M57 — Frozen Contract Reproduction & Stability Audit

## Verdict

`M57_DIRECTIONALLY_REPRODUCED`

Candidate C was frozen unchanged and rebuilt through the canonical request
boundary. Prelive provenance passed for 90/90 cases with zero calls before the
gate. M57 made 90 fresh one-shot calls, with zero retries and 0 provider failures.

## Results

| Metric | M54 | M56R | M57 |
| --- | ---: | ---: | ---: |
| Governed | 82/90 | 83/90 | 84/90 |
| Answerable TSA | 57/62 | 59/62 | 60/62 |
| Authority | 13/15 | 13/15 | 13/15 |
| Ambiguity | 6/7 | 5/7 | 5/7 |
| Policy | 6/6 | 6/6 | 6/6 |

## Stability

- Decision stability: 90/90.
- Governed verdict transitions: `{"FAIL_TO_FAIL_SAME_MODE": 5, "FAIL_TO_PASS": 2, "PASS_TO_FAIL": 1, "PASS_TO_PASS_DIFFERENT_OUTPUT": 16, "PASS_TO_PASS_SAME_OUTPUT": 66}`.
- SQL stability is recorded in `m57_sql_stability.json` using deterministic hash and parser normalization only; no LLM judge was used.

The original M55 fixes and M56R regressions are recorded case-by-case in
`m57_residual_stability.json`. The M56R result is therefore independently
reproduced only to the degree shown by these aggregate and case-level
transitions.

## telecom_15 safety

The historical model decision remains `ANSWER`. The M52.S replay remains
`AUTHORITY_REJECTION / UNAUTHORIZED_RELATION`, with EXPLAIN calls 0, database
connections 0, and execution calls 0. This runtime proof is independent of
whether M57's model output changes.

## Provenance

The 210 invalid pre-M56R calls were excluded. M57 did not reuse M56R response
bytes. Benchmark semantics, runtime semantics, prompt wording, Candidate C,
and the single-call architecture were unchanged. Public arithmetic is
descriptive historical composition, not a same-time 180-case run.

## Recommendation

Best observed valid result is M57 `84/90` governed and `60/62` TSA.
The conservative two-run lower envelope is `83/90` governed and `59/62` TSA.
M57 result is `M57_DIRECTIONALLY_REPRODUCED`. M57 does not promote or edit the public README;
M58/M57 follow-up can decide the conservative stable-public policy.
