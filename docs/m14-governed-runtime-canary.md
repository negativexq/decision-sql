# M14 — Governed Metric Runtime Operational Canary

M12R proved the bounded semantic-plan capability and M13 proved its production
integration. M14 measures operation through the real `TextToSqlService` route:
explicit `GOVERNED_METRIC` scope, the canary-only feature flag, the validated
provider, strict plan validation, `MetricCompiler`, unchanged M1, and the
read-only demo PostgreSQL executor.

The repository default remains `DIRECT` with
`DECISION_SQL_GOVERNED_METRIC_RUNTIME_ENABLED=false`. Automatic natural-language
routing is absent. The LLM does not grant itself access to a governed execution
path; the server-owned request contract decides whether that path may be
attempted.

The bounded canary uses 60 fresh M3-applicable questions in three repeated
rounds: sequential warm-up, concurrency eight, and concurrency eight. A
separate 200-request provider-free fault soak checks pre-M1 fallback, post-M1
fail-closed behavior, direct-mode isolation, and route-loop absence. Live
results distinguish end-to-end correctness from native governed success.

Operational rules:

- M12R proved the capability. M13 proved the runtime integration. M14 measures operation.
- Do not repeatedly re-prove an already validated semantic capability.
- Operational failures produce operational fixes, not architecture reselection.
- After a successful bounded canary, move to bounded rollout or to a different unsolved capability line.
- Pre-M1 semantic-plan failures may fall back to the existing direct path.
- Post-M1 governed failures do not silently change semantic strategy.

The canary is not a global rollout, an applicability-classifier experiment, or
a general Text-to-SQL benchmark. Defog and BIRD are not run here. The M3
contract is unchanged.
