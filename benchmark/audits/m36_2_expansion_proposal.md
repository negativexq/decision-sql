# M38 preview — Decision-SQL Bench v0.2 development set

This is a proposal only. M36.2 creates no new cases or databases.

## Target

Expand the repaired `0.1.2-pilot` authoring template from 3 databases / 30 cases
to 6 databases / 90 cases:

- existing pilot: 3 databases / 30 cases;
- new material: 3 databases / 60 cases;
- combined development set: 6 databases / 90 cases.

The 90-case set remains a development benchmark, not a hidden final test. The
smaller expansion is intentional: it is large enough to test authoring generality
and small enough for complete manual-style adversarial audit and mutation review.

## Proposed new domains

1. `healthcare_operations`: encounters, care teams, orders, results, appointments,
   authorizations, and clinical time windows. This adds privacy-safe entity
   boundaries, episode population, and result/latest-record semantics.
2. `warehouse_logistics`: facilities, bins, inventory movements, shipments,
   carriers, scans, and fulfillment exceptions. This adds stock population,
   multi-hop movement paths, and event/latest-scan semantics.
3. `manufacturing_quality`: work orders, production runs, batches, inspections,
   defects, machines, and corrective actions. This adds batch grain, conditional
   measures, defect rates, and temporal process joins.

These domains are structurally different from `commerce_ops`, `fleet_ops`, and
`support_ops`; they are not renamed copies of pilot schemas.

## Proposed governed distribution for 60 new cases

| Behavior | New cases |
|---|---:|
| ANSWERABLE | 40 |
| AUTHORITY_BLOCKED | 10 |
| AMBIGUOUS | 6 |
| POLICY_BLOCKED | 4 |
| Total | 60 |

Combined with the repaired pilot this yields 60 ANSWERABLE, 15
AUTHORITY_BLOCKED, 9 AMBIGUOUS, and 6 POLICY_BLOCKED across 90 cases.

## New-case mechanism targets

The 60 new cases should have explicit target counts for simple projection, filter,
aggregation, grouping, relationship, multi-hop, population, conditional measure,
calculation, ratio, temporal, latest-row, JSON, window, nested, correlated, set
operation, ordering, limit, NULL semantics, precision/rounding, and projection
discipline. The authoring plan should reserve additional composition slots for:

- relationship + population + aggregation;
- temporal + latest-row + JSON;
- multi-hop + ratio;
- nested + conditional aggregate;
- projection discipline combined with a qualifying measure.

Counts must be frozen before references and fixtures are authored, and each case
must receive independent references, discriminating fixtures, visible authority,
and valid killed mutants.

## Database-level isolation

The six-database plan should define DEV and CONFIRMATION database groups. Do not
randomly split questions from one database across future evaluation partitions.
A future FINAL partition must use entirely unseen databases. The 90-case set is
not yet that final partition.

## Non-copying rule

New questions may exercise the same mechanism family but must be independently
authored, with different schema vocabulary, data-generating assumptions, and
counterfactual distinctions. Cosmetic paraphrases of pilot questions are not
acceptable expansion material.
