# M20 — Full Benchmark Rebaseline (Incomplete)

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

The implementation is ready for a clean full run with GPT-5.6 Luna
(`gpt-5.6-luna`) through the OpenAI-compatible provider, provider-default
temperature, reasoning effort `none`, and a 30-second timeout. Memory,
retrieval, governed semantics, QueryPlan, Window IR, repair, judge, and
best-of-N are disabled. No authoritative M20 manifest/result is currently
retained because the provider run was stopped before a complete final attempt.

## Historical evidence

Historical results remain separate: BIRD `225/500`, Defog Classic `142/210`,
and Defog Advanced `48/64`. The M15 current Luna checkpoint was BIRD
`231/500`, Classic `141/210`, and Advanced `47/64`. Neither historical set is
rewritten by M20.

## Fresh M20 results

No authoritative full-rebaseline result. Two earlier full attempts were
invalidated by manifest/accounting defects, and a third attempt was stopped
after partial provider exposure to avoid further cost.

## Failure-stage distribution

The implementation records M1 rejection, M1 planning errors, result
evaluation mismatches, provider/protocol failures, and execution failures
separately. A future complete run must retain the aggregate and per-case
artifacts before any cleanup.

## Limitations

This is a development/public benchmark baseline, not a claim of general
production quality. M20 performs no semantic failure intervention; causal
analysis of incorrect cases is deferred. Repeated future tuning against these
public sets would turn them into development scoreboards rather than unseen
generalization measurements.

## Next milestone

`M20.1 — Full Failure Forensics` must wait until M20 has a complete authoritative
per-case artifact.
