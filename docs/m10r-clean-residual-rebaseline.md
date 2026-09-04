# M10R — Fresh Corpus Reconstruction & Clean Residual Rebaseline

Status: `M10R_CLEAN_REBASELINE_COMPLETED_FRAGMENTED_RESIDUALS`

M10R reconstructed and froze `decisionsql-m10-clean-rebaseline` version
`m10-clean-rebaseline-v2`, then validated all 200 benchmark-owned references
through the unchanged M1/PostgreSQL path before making any provider call. The
current combined architecture was then run once, pass@1, with Evaluator V1
authoritative and Evaluator V2 reference-blind shadow-only.

## Transport boundary

The synthetic Phase A probe suite used 22 non-benchmark SQL probes. It made 22
M1 plan attempts, 22 EXPLAIN calls, 17 probe executions, and 156 observed
database driver calls. It made no provider calls and no database mutations.

The primary transport finding is `TRANSPORT_PLACEHOLDER_COLLISION`. The exact
boundary is `app/execution/cost.py:11`, where
`QueryCostGate.explain()` calls:

```python
connection.exec_driver_sql(f"EXPLAIN (FORMAT JSON) {sql}")
```

The current psycopg transport interprets an unpaired percent character as
pyformat syntax before PostgreSQL parses the statement. The benchmark-only
contract therefore permits doubled `%%` sequences after PostgreSQL sqlglot
normalization and rejects raw single-percent/parameter-marker forms. This
contract is not passed to routing, M3, M4, generation, or production M1, and
production transport code was not changed.

## Corpus reconstruction

M10-v1 remained preserved as the failed, unconsumed corpus. Its business
questions, composition, split assignments, and semantic metadata were carried
forward with new v2 case IDs. There were 199 unchanged semantic carry-forwards
and one reference-only reconstruction: the prefix pattern `LIKE 'A%'` became
`LIKE 'A%%'`, which preserves the same prefix-match language while satisfying
the observed transport boundary. No question was changed.

The v2 corpus contains exactly 200 cases: 120 DEV, 80 HOLDOUT, 50 expected
GOVERNED, and 150 expected DIRECT, with the frozen direct-category quotas.
All 200 references parsed offline and passed the static transport preflight.
Real reference validation then passed 200/200 parse, 200/200 M1 acceptance,
200/200 read-only execution, and 200/200 result availability. Provider calls
before that gate were zero.

## Clean rebaseline

The frozen architecture consumed all 200 v2 cases and made 340 provider calls:
140 direct-path cases used two calls each and 60 governed-path cases used one
call each. There were no repair, judge, sampling, or post-hoc generation
calls.

Authoritative result:

`DecisionSQL M10R Clean Rebaseline — Evaluator V1 Authoritative: 28/200 (14%)`

This is a fresh internal rebaseline, not production accuracy, general
Text-to-SQL accuracy, or a public benchmark score. DEV was 17/120 and HOLDOUT
was 11/80. There were 164 result mismatches and 8 M1 rejections.

V2 shadow binding was available for 110 cases and unavailable for 90. The
shadow matrix was 22 V1-correct/V2-correct, 1 V1-incorrect/V2-correct, and 87
V1-incorrect/V2-incorrect. V2 did not overwrite the authoritative V1 result;
no best-of or union score was created, and no V2-unavailable case was removed.

Routing produced 60 governed and 140 direct paths against 50 expected governed
and 150 expected direct cases: 10 mismatches, all harmful because the primary
result was incorrect. M4 memory was used for 132 cases, of which 23 were V1
correct and 109 were incorrect. These are diagnostics, not causal claims.

Deterministic residual evidence identified 7
`M1_INCOMPATIBLE_GENERATED_STRUCTURE` cases and 1
`M1_TRANSPORT_INCOMPATIBLE_GENERATED_SQL` case. The remaining 164 result
mismatches are `UNCLASSIFIED`; broad family membership was not treated as
mechanism proof. No coherent recoverable mechanism met the dominance rule of
at least 10 failures and at least 25% of all primary failures.

## Frozen provenance

- Starting checkpoint: `46e4e059eac8d7051e1ee7e23821ad4858b96ea9`
- M10-v1 classification: `M10_CORPUS_VALIDATION_FAILED`
- M10-v1 provider calls / cases consumed: `0 / 0`
- M10-v1 corpus hash: `bd5a9b5904240f1a0647dfe0a547f4f482cb8cf62129fda222cb063106d75fd2`
- M10R v2 corpus hash: `4718051d31ffe00695f26413cc0595f8ab9b7e51a546b551b5d907d17caed0c5`
- M10R master protocol hash: `1212542536762b0688e465dac3700d07e82e26e252064818996449f5ecb89b18`
- Reference-results hash: `8730aacf8d80d719d492f1fa7d66a34915cc1795721ad0b4e9697514bfc122f9`
- Final case-ledger hash: computed from the immutable append-only ledger in the run artifact directory.

M7 remains `99/150 = 66%` on its historical frozen workload. No public
benchmark was rerun, no adjusted accuracy was created, and no production
component was changed. The completed v2 corpus is now consumed historical
evidence; future intervention work requires a new independent validation set.
