# M3 — Governed Semantic Metrics

M3 tests a narrow product hypothesis: when a question refers to a known
business metric, the model should select a governed metric and dimensions while
server-owned software determines the physical computation.

The implementation keeps the DecisionSQL trust boundary intact:

```text
question → public metric glossary → metric/dimension grounding
         → typed server-owned catalog → deterministic compiler → M1 → PostgreSQL
```

The direct comparison arm received the same public glossary and the full demo
schema, then generated SQL directly. The governed arm received only the public
glossary and returned the minimal `{metric_name, dimensions}` JSON object. Each
arm made one provider call per question, with no retry, repair, SQL fallback, or
second SQL-generation call. Production default routing remains unchanged;
feature-flagged integration is the next milestone.

## Catalog

The catalog is validated against the actual demo SQLAlchemy metadata before it
can compile a request. The current version contains:

- 8 entities and 9 many-to-one relationships;
- 8 public semantic dimensions;
- 13 typed measures;
- 10 metrics: 4 direct measures and 6 ratios.

The governed metrics are `completed_revenue`,
`average_completed_order_value`, `average_payment_amount`,
`completed_item_quantity`, `payment_success_rate`, `refunded_order_rate`,
`refund_to_revenue_rate`, `average_items_per_completed_order`,
`completed_order_rate`, and `customer_order_coverage_rate`. Five metrics are
COUNT_DISTINCT-sensitive, eight carry default eligibility filters, and three
exercise different component relationship paths.

Measures own aggregation, physical columns, and typed eligibility predicates.
Ratios own only references to measures, valid dimensions, scale, and
zero-denominator policy. The provider never sees physical tables, columns,
joins, filters, aggregation choices, or formulas.

The compiler resolves only deterministic, safe relationship paths. Each ratio
component is aggregated independently before combination, preventing a raw
numerator/denominator join from multiplying one-to-many rows. Reverse
many-to-one traversal is rejected for aggregate subplans rather than silently
repaired. Zero-safe division uses compiler-generated `CASE` and `DECIMAL`
casts; M1 was not changed.

## Provider-free ceiling

The frozen benchmark contains 32 unique semantic targets, 48 development
questions, and 32 holdout questions. The independent reference corpus and all
compiled targets passed PostgreSQL parsing, execution, M1 planning, and
execution-equivalence comparison:

| Check | Result |
| --- | ---: |
| Reference parse | 32/32 |
| Reference execution | 32/32 |
| Compiler output accepted by M1 | 32/32 |
| Compiler result equivalent to independent reference | 32/32 |

Fixtures are in `evaluation/fixtures/m3_targets.json` and
`evaluation/fixtures/m3_cases.json`. The frozen combined benchmark hash is
`bae37fa9c4850954dbae1a58b6c26c5b9cb1855b7ab6b6acd562845ee4bbb0b8`.

## Results

The valid development run is
`evaluation/results/m3/dev/20260903T155347Z/` and the one-time holdout run is
`evaluation/results/m3/holdout/20260903T155913Z/`. Both used `gpt-5.6-luna`,
reasoning `none`, omitted temperature, and one call per arm/question.

| Split / arm | Correct | Accuracy |
| --- | ---: | ---: |
| DEV direct SQL | 26/48 | 54.17% |
| DEV governed compiler | 48/48 | 100.00% |
| HOLDOUT direct SQL | 13/32 | 40.63% |
| HOLDOUT governed compiler | 32/32 | 100.00% |

DEV pairwise outcomes were BOTH_CORRECT 26, GOVERNED_ONLY 22,
DIRECT_ONLY 0, BOTH_INCORRECT 0. Holdout outcomes were BOTH_CORRECT 13,
GOVERNED_ONLY 19, DIRECT_ONLY 0, BOTH_INCORRECT 0. Grounding transport was
48/48 in DEV and 32/32 in holdout; metric and dimension selections were exact
in every transported case, and every correctly grounded request produced the
correct compiled result.

The precommitted DEV gate therefore passed: compiler ceiling 100%, transport
100%, governed improvement +45.83 percentage points, GOVERNED_ONLY greater
than DIRECT_ONLY, and correct grounding to correct result 100%. The holdout
confirmed the direction with a +59.38 percentage-point delta.

DEV used 48 direct-SQL calls and 48 governed-grounding calls. Direct generation
used 62,125 input and 8,638 output tokens; governed grounding used 21,853 input
and 1,103 output tokens. Mean provider latency was 3,364 ms for direct SQL and
1,403 ms for governed grounding. These are run-specific measurements, not
universal performance guarantees. Catalog and public-glossary hashes are
`1ef47fe5d85f9e847740fb5c53932cbb1e17253e34f2b73c4e707125ff38720a` and
`af114d4c52dfed39826e9a49a9159cb2221178999a0b5738ebfb6eaba620cf07`.

## Scope and limitations

This is evidence for a governed metric path, not a complete semantic layer.
M3 V1 does not address arbitrary Text-to-SQL, temporal interpretation,
literal/value grounding, schema retrieval, arbitrary user filters, arbitrary
runtime metric definitions, generic semantic expressions, verified query
retrieval, or repair. Production default routing remains unchanged and the
governed path is not enabled by default.

M1, the canonical evaluator, FULL_COMPACT, WindowQueryIR, and the Window
compiler remain unchanged. Defog, BIRD, and previous internal holdouts were
not rerun or consumed. Broad benchmark acquisition is frozen. M3.4
subsequently validated feature-flagged routing and observability; its
production default remains off. No later milestone is implemented here.
