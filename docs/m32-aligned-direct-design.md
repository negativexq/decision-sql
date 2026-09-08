# M32 — Task-Aligned Direct SQL

M31 closed the custom model-facing logical-plan branch at `0/18` for both
frozen versions. M32 returns SQL to model authority while separating the
semantic/schema selection problem from SQL synthesis.

## Evidence-driven design

| Failure mechanism | NL reasoning | Schema grounding | Server-derivable after alignment | Alignment field | Reason |
|---|---:|---:|---:|---|---|
| Schema/entity selection | Yes | Yes | No | `anchor_entity_id`, `relevant_entity_ids` | The model must identify the concepts asked for. |
| Attribute/measure selection | Yes | Yes | No | `relevant_attribute_ids` | The server can map a selected semantic ID, but cannot choose the intended measure. |
| Filter meaning | Yes | Yes | No | `filters` | Operators and typed values are explicit user meaning. |
| Population/membership | Yes | Partly | No | `population_mode`, entities, relationships | The model must distinguish matching populations from preserved anchors. |
| Relationship authority | No | Yes | Yes | `relationship_ids` when explicit | The server owns physical paths and keys; missing/ambiguous paths fail closed. |
| Aggregation | Yes | No | No | `aggregations` | Aggregate function and measure are semantic intent. |
| Grouping/grain | Yes | No | No | `grouping_attribute_ids` | Grouping dimensions are user meaning; physical grain mechanics are not exposed. |
| Calculation | Yes | No | No | `calculation` | Mathematical operation and operands are semantic intent. |
| Temporal interpretation | Yes | Yes | No | `temporal` | Date/time meaning needs both question interpretation and selected attributes. |
| Ordering/limit | Yes | No | No | `ordering`, `limit` | Requested result ordering and top-K are semantic intent. |
| Nested/correlated shape | Yes | No | No | `query_shape` | The model identifies whether SQL requires a bounded non-flat shape. |
| Physical schema mapping | No | Yes | Yes | Grounded context | Deterministic semantic-to-physical mapping stays server-owned. |
| Join keys and aliases | No | Yes | Yes | Grounded context | They are execution mechanics, never alignment output. |

The prior DIRECT forensic families were schema grounding, population and
relationship interpretation, filters/values, aggregation and calculation,
temporal logic, ordering/limit, projection, and nested structure. The
alignment contract addresses those semantic choices without becoming a query
plan.

## Contract boundary

`QueryAlignmentV1` contains semantic IDs, bounded intent enums, typed filter
values, and a short plain-language `logic_summary`. It contains no SQL,
physical aliases, CTE names, execution steps, join predicates, or compiler
scope fields.

The server validates IDs and deterministically produces a `GroundedSQLContext`
containing only the selected physical schema and authorized relationships. The
second Luna call generates PostgreSQL SQL from the original question plus that
grounded context.

## Frozen v1 pipeline

```text
question + M29R.1-equivalent semantic evidence
    ↓ Luna / strict QueryAlignmentV1
validated alignment
    ↓ deterministic AlignmentGrounder
grounded physical schema + authorized relationships
    ↓ Luna / strict {sql: string}
M1 → EXPLAIN → read-only execution → evaluator
```

Oracle-derived alignment fixtures are evaluation-only. Raw model responses are
stored under ignored protected results; normal artifacts contain hashes and
bounded diagnostics only.

## Development refinement decision

The frozen v1 run completed all 18 alignment and SQL calls, but produced `0/18`
officially correct results. Its preserved alignment captures showed that
schema selection and logical interpretation were coupled: entity, attribute,
population, calculation, and query-shape decisions frequently diverged. SQL
also remained the earliest downstream failure whenever a plan was executable.

The evidence-supported v2A pipeline split the semantic work into:

```text
question + catalog → SchemaAlignmentV1
validated schema → LogicalSynthesisV1
validated logic + grounded schema → SQL
```

It also produced `0/18`. Eleven cases reached valid, executable SQL and still
failed official comparison; five stopped on server relationship ambiguity and
one on an unknown attribute. This was not a reason to add a fourth semantic
language.

The final bounded v3 tested the unused SQL-synthesis axis:

```text
QueryAlignmentV1 → initial SQL → one M1/EXPLAIN-only review → SQL
```

The review saw no evaluator result, reference rows, or gold SQL. It also
produced `0/18`; no official success was recovered. M32 therefore stops after
its three allowed frozen development versions. No confirmation or scale-out
set was consumed.

The oracle alignment comparator was corrected after the first v1 analysis to
ignore canonical output slots, aliases, CTE implementation shape, and hidden
relationship keys. Raw provider captures and the original funnel artifacts
remain preserved; recomputed component metrics are analysis of the same calls,
not a new provider run.

## Contract and run sizes

The v1 `QueryAlignmentV1` provider schema is 5,145 bytes with 12 definitions
and no top-level union. The v2A schema-selection schema is 686 bytes; its
logical-synthesis schema is 4,633 bytes with 12 definitions. The SQL envelope
is 118 bytes. The previous model-facing contracts were 39,454 bytes/63
branches for `SemanticQueryPlan` and 23,522 bytes/29 branches for
`LogicalQueryPlanV1`.

The three frozen development arms all used Luna, temperature 0, reasoning
none, strict native JSON Schema, one request per stage, and no semantic retry:

| arm | calls/case | official correct |
|---|---:|---:|
| v1 alignment → SQL | 2 | 0/18 |
| v2A schema → logic → SQL | 3 where groundable | 0/18 |
| v3 alignment → SQL → review | 3 where groundable | 0/18 |

Because the best result did not exceed the causally comparable fresh DIRECT
score of 8/18 (and also did not exceed the historical 6/18 result), M32 stops
at `M32_NO_GO`; untouched confirmation and scale-out are not justified.

## DIRECT comparability correction

The persisted historical DIRECT result of 6/18 was not used as the sole causal
comparator. M32's alignment request uses the M29R.1 corrected semantic context
plus a server-owned semantic mapping-ID block; the historical DIRECT artifact
used the corrected context without that block. The context size and
serialization therefore changed materially.

The fresh paired control in
`evaluation/fixtures/m32_direct_fresh_result.json` uses the exact M32 base
contexts, the same Luna/provider/settings, and one raw-SQL call per case. It
scored 8/18, while M32 v1, v2A, and v3 each scored 0/18. This control was run
only for comparability and was not used to tune M32.

The result does not show a transport or grounding failure. It shows that the
alignment intervention did not improve SQL correctness under the same base
evidence. The dominant M32 earliest-divergence category remained
`SQL_SYNTHESIS_ERROR`; alignment itself was also weak on population,
calculation, ordering, and query-shape decisions.
