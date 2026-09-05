# M11.1P3 — Paired Memory-OFF vs Memory-ON Counterfactual

P3 measures the current frozen Verified Query Memory context-injection effect
on the exact 105-case P2 primary population.  The population and treatment
manifest are frozen before provider exposure.  MEMORY OFF uses the unchanged
direct generation path with no verified examples; MEMORY ON uses the frozen
historical M4 selected IDs and order.  Retrieval is not rerun.

The two arms differ semantically only by the current memory context injection.
The question, schema/business context, provider/model/configuration, M1,
database snapshot, typed P0 evaluator, reference snapshots, and one-shot policy
remain shared.  P1 semantic-relation and demonstration-adaptability labels are
secondary stratification evidence, not the correctness evaluator.

The primary estimand is ON correctness minus OFF correctness.  The four paired
outcomes are frozen before secondary slices and mediator analysis.  Discordant
pairs use an exact two-sided binomial test with a five percentage-point practical
threshold.

Semantic equivalence and demonstration adaptability remain separate evidence
questions.  Selected non-adaptable context alone is association, not causation;
structural motif transfer is a pathway diagnostic, not a MEMORY-OFF
counterfactual.  OFF-correct/ON-wrong is observed paired harm under this
protocol, while OFF-wrong/ON-correct is observed paired help.  Provider
stochasticity remains a limitation when no shared seed is available.

P3 does not improve memory, change M4, alter retrieval, add admission, change
prompts or models, modify M1/P0/P1, or implement a runtime intervention.
Historical artifacts remain immutable.  A valid P3 result must be checkpointed
before any intervention milestone is started.

## Frozen primary result

The 105 paired outcomes were A=36, B=4, C=9, D=56.  OFF correctness was
40/105 (38.0952%) and ON correctness was 45/105 (42.8571%), for a net +5
cases or +4.7619 percentage points.  There were 13 discordant pairs and the
exact two-sided paired binomial p-value was 0.266845703125.  The frozen result
is `M111P3_MEMORY_EFFECT_MIXED_OR_INCONCLUSIVE`: neither the precommitted
5-point nor p≤0.05 gate passed.

There were 210 semantic generations, 105 per arm, with no terminal failures or
transport retries.  The provider did not expose a stable fingerprint and no
shared seed was available.  The accepted demo snapshot was PostgreSQL
`0001_commerce_schema`; all new candidate results were persisted through the
P0 typed snapshot contract.  The observed paired result is scoped to this
population, provider/configuration, frozen M4, snapshot, prompt, and one-shot
protocol.  It is not a general model or memory-quality claim.
