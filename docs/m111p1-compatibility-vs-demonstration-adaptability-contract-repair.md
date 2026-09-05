# M11.1P1 — Compatibility vs Demonstration-Adaptability Contract Repair Audit

Status: `M111P1_DUAL_CONTRACT_VALIDATED`

This offline audit validates two separate evaluator-side questions. It does not
apply either contract to historical M10/M11 evidence and it does not change
runtime behavior.

## Contracts

`decision-sql-query-semantic-relation-v1` answers whether the bounded supported
SQL profiles are equivalent, materially conflicting, or unknown. Its profile
keeps sources, join kind and ON relation, projection, aggregation/formula,
WHERE, HAVING, GROUP BY, ordered ORDER BY expressions and directions, LIMIT,
DISTINCT/set operations, windows, and temporal expressions distinct.

`decision-sql-demonstration-adaptability-v1` answers whether the memory query
can serve as a verified demonstration after changing only explicitly allowed
parameter slots. Version 1 allows typed scalar literal slots in WHERE and
HAVING comparisons and integer LIMIT slots. It never parameterizes identifiers,
operators, joins, grouping, ordering, aggregation, DISTINCT, set operations, or
window semantics. Positive adaptability requires a matching stable skeleton;
absence of a known conflict is not sufficient. Unsupported or ambiguous input
fails closed to `UNKNOWN`.

The contracts are reference-blind in principle and use only the two SQL
artifacts in this audit. They do not use correctness labels, retrieval scores,
partitions, causal labels, provider output, database results, embeddings, or
rerankers. They are structural evidence, not accuracy predictions.

## Adversarial validation

The five mandatory controls all pass:

| control | semantic relation | adaptability |
| --- | --- | --- |
| numeric threshold `>100` vs `>200` | material conflict | adaptable |
| `A AND B` vs `B AND A` | equivalent | adaptable |
| `LEFT JOIN` vs `INNER JOIN` | material conflict | non-adaptable |
| HAVING threshold `>5` vs `>50` | material conflict | adaptable |
| `ORDER BY a,b` vs `b,a` | material conflict | non-adaptable |

The independent validation corpus contains 40 pairs. Results are 9
equivalent, 27 material conflict, and 4 unknown semantically; and 13
adaptable, 23 non-adaptable, and 4 unknown for demonstration adaptability.
There are zero false-equivalent labels, zero false material-conflict labels,
zero false-adaptable labels, zero false non-adaptable labels on required
adaptable cases, and zero required-supported UNKNOWN results.

Safe normalization covers alias changes, projection aliases, whitespace and
keyword case, harmless parentheses, AND term order, and GROUP BY term order.
ORDER BY remains an ordered sequence. JOIN kind and ON relation are retained.
HAVING remains separate from WHERE.

## Legacy comparison and historical boundary

The frozen M11.0R single compatibility contract remains unchanged. On the five
mandatory concepts its observed states were: threshold variation
`PROVEN_INCOMPATIBLE`, AND reorder `PROVEN_INCOMPATIBLE`, JOIN kind
`PROVEN_COMPATIBLE`, HAVING threshold `PROVEN_COMPATIBLE`, and ORDER priority
`PROVEN_COMPATIBLE`. This is diagnostic evidence only; it does not rewrite
M11.0R.

The new contracts do not reclassify M10/M11 cases. Historical M11 intervention
selection remains quarantined pending P2 historical evaluator and attribution
revalidation. No historical score was recomputed.

## Boundaries

No runtime memory intervention occurred. M4, retrieval, Top-K, admission,
prompts, compatibility semantics, causal attribution, and the P0 typed result
snapshot were not modified. No provider, database, retrieval, memory-OFF, or
benchmark operation occurred. No public benchmark was rerun.

A semantic conflict can still be demonstration-adaptable when its only
difference is an explicitly allowed parameter slot. Conversely, JOIN kind,
HAVING role/structure, and ORDER BY priority are material evidence. Structural
result equality on one database snapshot is not used as semantic equivalence.

The next milestone is **M11.1P2 — Historical Evaluator & Attribution Integrity
Revalidation**. P1 does not authorize implementation or historical
application.
