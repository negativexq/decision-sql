# Evaluation research history

This document records the completed evaluation line without turning the README
into an experiment diary. Historical artifacts and frozen holdouts remain
unchanged.

| Milestone | Question | Result | Decision |
| --- | --- | --- | --- |
| M0–M1 | Can SQL execution be governed deterministically? | M1 safety, planning, cost, reader, timeout, and bounded-result controls implemented and tested. | Keep M1 as the execution authority. |
| M2 | What is the internal one-shot baseline? | Reproducible generation/evaluation path on the synthetic commerce schema. | Continue controlled generation research. |
| M2.5–M2.9 | Do grounding, reasoning, model capacity, result-shape, or role-aware context improve the baseline? | No durable architecture-changing gain; role-aware schema was costly and marginal. | Keep the baseline FULL_COMPACT path. |
| M2.10 | Does candidate scaling solve the hard slice? | Pass@1 and Pass@8 were both 0/10. | Do not add sampling or selection. |
| M2.11 | Does an LLM-generated narrow plan improve SQL? | Baseline and decomposed arms were both 0/10. | Do not add a second SQL-generating LLM stage. |
| M2.12 | Can typed Window IR be compiled deterministically? | Gold IR reached 80/80 through compiler, M1, execution, and result comparison. | Keep the compiler as a bounded research component. |
| M2.12R series | Can the provider reliably express Window IR semantics? | Transport was eventually reliable, but semantic slot grounding remained weak and did not beat direct SQL. | Close provider-format experiments; retain Window as an internal stress suite. |
| M2.13 | How does one-shot SQL perform on public Defog PostgreSQL? | Classic exact 142/210; Advanced exact 48/64; Advanced Window 6/8. | External calibration is viable; ratio/date semantics remain weak. |
| M2.14 | Does a second public PostgreSQL benchmark confirm the picture? | BIRD EX 225/500; simple 87/148; challenging 30/102. | Broad calibration is sufficient; focus product work on semantic/compositional failure modes. |

## Current evidence

Defog SQL-Eval PostgreSQL and BIRD Mini-Dev PostgreSQL are independent external
calibrations with different datasets and evaluators. Their percentages are not
combined. The BIRD run’s parse rate was 499/500 and execution rate 496/500,
while result correctness was 225/500, so semantic correctness is the dominant
observed external bottleneck.

Derived metrics are a repeated weakness: Defog ratio exact was 12/35 (34.29%)
and BIRD ratio/division EX was 17/101 (16.83%). BIRD temporal EX was 4/19
(21.05%). These results motivate governed semantic metric definitions rather
than another SQL-generation stage.

## Window status

M2.12 is the **Internal Window Compositional Stress Suite**. It is not a
general product-accuracy benchmark or a general Window capability score. The
gold deterministic compiler ceiling is 48/48 development plus 32/32 holdout.
The LLM-generated Window IR path did not outperform direct SQL, and the frozen
Window holdout was not consumed by later reruns.

## Benchmarking status

Broad benchmark acquisition is frozen after Defog and BIRD PostgreSQL. The
next product milestone is **M3 — Governed Semantic Metrics**. No M3
implementation is included in this consolidation.
