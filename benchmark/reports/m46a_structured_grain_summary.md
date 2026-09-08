# M46A structured grain summary

## Historical preservation

- M39–M45 artifacts hashed: **74**
- Historical artifacts changed: **NO**
- Provider calls: **0**; model calls: **0**

## M46A scope

The frozen benchmark remains **0.2.1-dev** with content hash
`aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281`. Model-facing prompt/context and benchmark truth
were not changed. The new layer is server-owned and diagnostic-only.

## Architecture

- Integration point: `app/semantics/grain.py`
- Typed contract: `MeasureCatalog`, `MeasureSemantics`, `GrainEntity`, `GrainRelationship`
- Alignment: `GrainAlignmentAnalyzer`
- SQL diagnostics: `GrainSafetyValidator`
- SQL repair, runtime blocking, and score changes: **none**

## Measure inventory

- Numeric attributes audited: **153**
- Identifiers / dimensions / measures: **126 / 11 / 16**
- Additive / semi-additive / non-additive / derived:
  **15 / 0 / 1 / 0**
- Required unknown semantics: **0**

## Coverage

- Quantitative answerable cases covered: **14/14**
- Required native grains: **3/3**
- Required rollup paths: **3/3**

## Replay

- Reference SQL analyzed / parseable: **120/120 / 120/120**
- Reference `PARENT_MEASURE_FANOUT` diagnostics requiring review: **2**
- Historically correct model SQL false positives: **0**
- M43/M44/M45 answer SQL replayed: **165**
- `warehouse_08` M43 / M44 / M45: **PARENT_MEASURE_FANOUT / NO_SQL / PARENT_MEASURE_FANOUT**
- `risk_05` M45: **NOT_APPLICABLE**

## Reference review candidates

`subscription_04` reference A and `subscription_10` reference A aggregate a
payment-level additive measure after joining refunds, modeled as many-to-one
toward payments. The validator is not weakened to hide this. The two references
remain benchmark review candidates; their reference B implementations reduce
refunds at payment grain.

## Determinism and M46B readiness

Catalog hashes, graph hashes, reference diagnostics, and historical diagnostics
are identical across two complete runs: **YES**. Structured facts can be exposed
without gold leakage, but that would change model-visible context and therefore
requires a new benchmark version/hash. Recommended isolation is structured
context exposure first, then a separate validator-admission experiment.

## Verdict

`STRUCTURED_GRAIN_ARCHITECTURE_PARTIAL`. The architecture detects the known unsafe shape and
protects safe existence and legal metric-specific rollups, but the two reference
review candidates prevent a full supported verdict.
