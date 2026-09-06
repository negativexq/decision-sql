# M21 — Server-Owned Schema and Relationship Grounding

## Objective

M21 tested one bounded hypothesis: a clearer deterministic representation of
authoritative schema and relationship facts can reduce direct-SQL grounding
errors without changing the model, SQL instructions, M1, evaluator, or any
semantic runtime contract.

M20.2 identified `SCHEMA_GROUNDING` in 56/189 residual mismatches (46 BIRD,
10 Defog Classic). That was observational evidence, not proof that a renderer
change would repair the cases.

## Current renderer audit

The historical direct benchmark renderers had two concrete representation
limitations:

- Defog emitted SQL-like `CREATE TABLE` definitions and foreign-key facts as
  separate comments, rather than a typed relationship record.
- BIRD emitted columns and types only. Its metadata explicitly recorded
  `foreign_keys_exposed: false` and did not expose PK/FK facts, although the
  benchmark package supplied declared PK/FK metadata in `dev_tables.json`.

M21 therefore used the existing authoritative database metadata plus the
benchmark-provided BIRD PK/FK metadata. It did not use gold SQL, results,
case IDs, or failure labels in provider context.

## Intervention

`serialize_schema_context_v2` is an opt-in renderer. It emits stable, explicit
fields for table IDs, column IDs, types, primary-key status, foreign-key
targets, and relationship endpoints (`FROM_TABLE`, `FROM_COLUMN`, `TO_TABLE`,
`TO_COLUMN`). Tables, columns, and relationships are sorted deterministically.

The facts are context only. The model still proposes SQL, and every candidate
passes the unchanged M1 plan and read-only execution path. The production
default remains the historical V1 serializer; no runtime default changed.

## Experimental population and manifest

P0 was the frozen M20 direct output. P1 was one fresh generation using the
same model, instructions, decoding, M1, database, evaluator, and question,
with only Schema Context V2 substituted.

| Set | Count | Composition |
| --- | ---: | --- |
| DEV | 20 | 18 BIRD, 2 Classic |
| TARGET_HOLDOUT | 36 | 28 BIRD, 8 Classic |
| CONTROL | 30 | 18 BIRD, 11 Classic, 1 Advanced |

The split was deterministic from the M20.2 schema-grounding case IDs and a
fixed SHA-256 assignment; controls were selected deterministically from frozen
M20 correct cases across zero/one/two-plus joins, relationship-heavy queries,
and aggregation-with-joins. The pre-provider manifest hash was
`cc68934a9cc395587db844caf7cc83c067c68faedd651202ebecff2a47c950cd`.

Planned and actual P1 provider calls were both 86. There was no P0 provider
rerun, repair, judge, retrieval, memory, or spillover experiment.

## Preflight and cost

The gold-leakage check passed, the dataset and database prerequisites passed,
and provider calls before preflight completion were zero. Model configuration
was GPT-5.6 Luna (`gpt-5.6-luna`), provider-default temperature, reasoning
effort `none`, and the frozen M1 configuration.

V2 retained the same full benchmark table population but was more verbose:

| Metric across 22 database contexts | V1 | V2 |
| --- | ---: | ---: |
| Characters, median | 1,428 | 7,399 |
| Characters, p95 | 3,559 | 16,228 |
| Characters, maximum | 5,505 | 27,000 |
| Characters, mean | 1,901.6 | 8,963.2 |
| V2 token estimate | — | median 668, p95 1,395 |

The extra context cost is material and is part of the result, not hidden by
the correctness comparison.

## Results

### Target holdout

All 36 P0 cases were frozen wrong by construction.

| State | Count |
| --- | ---: |
| P0 wrong → P1 correct | 2 |
| P0 wrong → P1 wrong | 34 |
| New M1 rejection | 2 |
| New execution failure | 0 |

The two recovered cases were BIRD case `44` and Defog Classic case `classic:138`
in the holdout population. Three holdout outputs aligned their table/relation facts with the
reference; two of those still had a result mismatch. The observed mechanism
change was therefore not a reliable recovery pattern.

### Control

| State | Count |
| --- | ---: |
| P0 correct → P1 correct | 27 |
| P0 correct → P1 wrong | 3 |
| New M1 rejection | 2 |
| New execution failure | 0 |

The targeted result was classified
`M21_SCHEMA_GROUNDING_HYPOTHESIS_NOT_SUPPORTED`: only 2/36 holdout cases were
recovered, while 3/30 previously correct controls regressed.

### Mechanism evidence

Across DEV and TARGET_HOLDOUT, P1 resolved the old relation/table divergence
in five cases, preserved it in 31, and produced a different semantic failure
in two holdout cases. The only recovered holdout was not a broad pattern; the
two holdout relation-aligned outputs still included two result mismatches.

## Limitations and claim boundary

M21 did not add authorization, join selection, routing, QueryPlan, Window IR,
repair, or a second reasoning system. It did not run the full 774-case
benchmark. The result does not show that explicit schema context is generally
useless; it shows that this single frozen representation did not support a
general schema-grounding intervention claim on this targeted holdout.

## Conclusion

The unchanged direct path remains the production default. M21 does not justify
promotion of Schema Context V2. The next work should return to the M20.2
evidence and select a separately scoped mechanism; it should not tune this
holdout or silently expand the renderer.
