# M20 — Full Benchmark Rebaseline

## Objective

M20 establishes a reproducible current baseline for the complete public
workloads before another semantic intervention. It measures the system; it
does not tune prompts or infer a new architecture.

## Frozen architecture

Every case followed the current direct path:

```text
question + benchmark-authorized evidence + schema
→ direct SQL generation
→ SqlSafetyService.plan() (M1)
→ SqlSafetyService.execute()
→ existing Defog/BIRD evaluator
```

QueryPlan, Window IR, governed semantic routing, repair, judge, best-of-N,
memory, and retrieval were disabled. M1 remained the execution authority.

## Integrity fixes

The M20 harness now fails closed: all 774 cases are loaded and all required
gold/database/evaluator/M1 prerequisites are validated before provider
construction. A preflight failure produces zero provider calls. Evaluation
records preserve typed stage provenance for provider/protocol, M1, execution,
and result evaluation. Governed execution failures now use the distinct
`EXECUTION_FAILURE` fallback reason instead of `COMPILER_FAILURE`.

## Manifest

The authoritative run used commit
`8439b1e10bb53dbf58b7d78a81418da33f18ca58` with GPT-5.6 Luna
(`gpt-5.6-luna`) through the OpenAI-compatible provider, provider-default
temperature, reasoning effort `none`, and a 30-second timeout. Memory,
retrieval, governed semantics, QueryPlan, Window IR, repair, judge, and
best-of-N were disabled. The pre-provider manifest hash is
`36e53850a704049cdf48453f8f3910eefeb877c9c57c342c0913674c8f13025a`.
The raw git cleanliness field is `false` because the non-source smoke
manifest was still present when the full manifest was written; the recorded
code-state hash is empty, so no source diff was present.

## Historical evidence

Historical results remain separate: BIRD `225/500`, Defog Classic `142/210`,
and Defog Advanced `48/64`. The M15 current Luna checkpoint was BIRD
`231/500`, Classic `141/210`, and Advanced `47/64`. Neither historical set is
rewritten by M20.

## Fresh M20 results

The authoritative run submitted all 500 BIRD, 210 Classic, and 64 Advanced
cases and made 774 provider calls:

| Benchmark | Correct | Total | Accuracy |
| --- | ---: | ---: | ---: |
| BIRD Mini-Dev | 193 | 500 | 38.60% |
| Defog Classic | 90 | 210 | 42.86% |
| Defog Advanced | 13 | 64 | 20.31% |

The earlier invalid attempts and the stopped partial attempt are preserved as
separate invalid-run records and are not included in these scores.

## Failure-stage distribution

The aggregate artifact records M1 rejection, M1 planning errors, result
evaluation mismatches, provider/protocol failures, and execution failures
separately. In the authoritative run, M1 accepted 475 candidates, rejected
264, and returned 35 planning errors; all 475 accepted candidates executed
successfully. Result mismatches were 179. Provider/protocol and execution
failures were zero. Per-case provenance is retained in
`evaluation/fixtures/m20_full_benchmark_cases.jsonl`.

## Limitations

This is a development/public benchmark baseline, not a claim of general
production quality. M20 performs no semantic failure intervention; causal
analysis of incorrect cases is deferred. Repeated future tuning against these
public sets would turn them into development scoreboards rather than unseen
generalization measurements.

## Next milestone

`M20.1 — Full Failure Forensics` can use the frozen per-case artifact to ask
which causal mechanisms produced M1 rejections, M1 planning errors, and result
mismatches, without changing this baseline.
