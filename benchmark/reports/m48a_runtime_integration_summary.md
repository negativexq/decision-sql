# M48A runtime integration summary

Provider/model calls: `0/0`.

The frozen GrainSafeNormalizer was integrated before EXPLAIN, re-parsed,
re-policy-checked, re-grain-validated, cost-checked, and executed only through
an accepted QueryPlan. The frozen M47B `subscription_04` submission was
normalized and passed its hardened multi-refund fixture. `subscription_10`
remained a no-SQL clarification and `warehouse_08` was not spuriously changed.

The result is `RUNTIME_GRAIN_INTEGRATION_PARTIAL`: three correct warehouse
reference-B fixture executions exceeded the existing cost threshold during the
120-reference runtime replay. The threshold was not loosened or bypassed.
