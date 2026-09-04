# M5.0 — Semantic Verification Signal Audit

M5.0 is an offline verifier-feasibility audit. It asks whether deterministic
signals can identify a useful, high-precision subset of semantically incorrect
direct-path SQL after generation. It does not block execution, rewrite SQL,
ask for clarification, call a model judge, or make provider calls.

## Evidence and boundaries

The audit uses the persisted M4 direct-path outputs only:

- M4 DEV: `34ff68f3b0fe632645d5e1013c2498fd4ae0c97a1e8287c308fe3c82b134c2b0`
- M4 HOLDOUT: `910bb52de4d58e2963c695748e15064ef8d73b1c83c8dd6b74147c35c5e176fd`
- verified-memory corpus: `f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae`
- M3 semantic contract: `0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c`

The verifier receives only runtime-legitimate inputs: the question, generated
SQL, parsed SQL facts, server-owned schema/metric metadata, and bounded memory
provenance. Gold SQL and result correctness are held exclusively by the
offline evaluator after verification, for scoring. Successful execution is
not treated as semantic correctness.

The audit population is 96 DEV candidates (48 DIRECT and 48 MEMORY) and 64
HOLDOUT candidates (32 DIRECT and 32 MEMORY). Correctness remains the frozen
M4 canonical result-equivalence label: DEV is 15/48 DIRECT and 29/48 MEMORY;
HOLDOUT is 10/32 DIRECT and 17/32 MEMORY.

## Verifier v1

`semantic-verifier-v1` uses deterministic `sqlglot` AST facts and conservative
question cues. The evaluated rule inventory covers requested grouping,
top-N shape, aggregate thresholds and mismatches, entity-count and aggregate
fanout, requested projection, governed-path escape, lifecycle regression,
memory over-transfer, explicit entity/filter grounding, output shape,
Cartesian joins, unrequested limits, and reserved future diagnostic codes.

The canonical ruleset hash after the DEV-only rule review is:

`4b3c91168832f1d3ca4370972d33abfebc7158712ea7ac04c8ea687d2253b494`

Rules are deterministic and finite. There is no search, retry, repair, or
recursive candidate generation. The combined policy is `ANY_HARD` →
`SUSPICIOUS`; otherwise the result is `CONTINUE`. Clarification output is not
generated.

## DEV freeze and HOLDOUT result

HARD rules are eligible only with at least three fired cases and at least 90%
precision on DEV. No rule met both requirements with sufficient support:

- `REQUESTED_GROUPING_MISSING`: 2/2 true positives, but insufficient support
- `GOVERNED_METRIC_SHOULD_USE_SEMANTIC_PATH`: 1/1, but insufficient support
- `UNEXPECTED_CARTESIAN_JOIN`: no DEV support
- fanout and entity rules were noisy or unsupported on DEV
- top-N, threshold, explicit-filter, projection, and lifecycle rules had no
  qualifying DEV support

Accordingly, the frozen accepted HARD-rule set is empty. HOLDOUT was then run
once with that unchanged set. `ANY_HARD` fired 0 times on both DEV and
HOLDOUT, so no production-useful verifier gate was established by this
population. The high-confidence structural cues remain implemented and unit
tested, but they are not promoted to a blocking or fallback policy.

The largest observed residual error rates among `CONTINUE` were 54.17% on DEV
and 57.81% on HOLDOUT. This is evidence that execution/result correctness
errors in this population are mostly not proven by the conservative runtime
signals considered here. Soft signals are diagnostic only; their observed
precision was insufficient for blocking use.

Verifier latency was local and provider-independent: approximately 1.21 ms
average, 0.86 ms p50, and 2.58 ms p95 on the 96-candidate DEV audit;
HOLDOUT was approximately 1.23 ms average, 1.13 ms p50, and 2.55 ms p95.

## Clarification boundary

The persisted M4 corpus does not contain an independently annotated sample of
genuine user ambiguity. The clarification audit therefore has zero supported
cases and is classified `INSUFFICIENT_CLARIFICATION_SAMPLE`. A missing filter,
join, grouping, or aggregate is a verifier diagnostic, not a clarification
candidate. No clarification UX is implemented.

## Reproducibility

Run the provider-free audit with:

```bash
docker compose run --rm -T --no-deps \
  -v "$PWD:/app" api python -m evaluation.m50_audit \
  --phase dev \
  --artifact-dir evaluation/results/m50/dev/<run-id>
```

After reviewing DEV, run HOLDOUT exactly once with the DEV
`accepted_rules.json`:

```bash
docker compose run --rm -T --no-deps \
  -v "$PWD:/app" api python -m evaluation.m50_audit \
  --phase holdout \
  --accepted-rules evaluation/results/m50/dev/<run-id>/accepted_rules.json \
  --artifact-dir evaluation/results/m50/holdout/<run-id>
```

The generated summaries persist the source hashes, ruleset identity, signal
results, per-rule statistics, arm/family breakdowns, and latency. Raw provider
I/O is neither required nor produced.

## Classification and limitation

M5.0 result: `SEMANTIC_VERIFIER_TOO_NOISY`. No verifier rule reached the
required supported DEV precision gate, and the frozen combined HOLDOUT policy
had no fired HARD signals. This does not claim that deterministic verification
is impossible; it says that this narrow M4 evidence does not justify an
execution gate yet.

M1 remains the sole SQL safety, planning, and execution authority. M3 governed
semantics, M3.4 routing, M3.5 provenance, M4 retrieval, and M4.1 runtime are
unchanged. The next evidence-supported milestone is **M5.0.1 — Adjudicated
Semantic Verification Corpus**: create a small, independently reviewed set of
runtime-legitimate contradictions and equivalent SQL idioms before deciding
whether M5.1 is justified. M5.1 is not implemented here.
