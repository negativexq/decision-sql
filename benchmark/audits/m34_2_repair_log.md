# M34.2 Repair Log

M34.2 repairs the governed benchmark pilot from the M34.1 human-audit findings. Historical M34/M34.1 evidence is preserved; `benchmark/audits/m34_1_human_audit.md` was missing at baseline and was not fabricated or overwritten.

## Provenance and ordering

All 20 answerable targets now carry component provenance for population, filters, ordering, limit, temporal semantics, and rounding. The central authoring rule emits `row_order=true` only when target ordering is present and explicitly sourced from the question. The three ordered cases are `commerce_07`, `fleet_06`, and `fleet_07`; the remaining 17 use unordered duplicate-preserving comparison.

## Temporal, filter, and authority repairs

- `commerce_04`: explicit captured-price/two-decimal semantics and a catalog-price divergence fixture.
- `commerce_06`: explicit two-decimal output and removal of an unrelated metric dependency.
- `fleet_02`: authorized vehicle-home-depot path, visible fuel attributes, and clarified population.
- `fleet_04`: visible maintenance attributes.
- `fleet_06`: explicit telemetry population, event-ID tie-break, and display order.
- `fleet_08`: internally consistent denied route-code authority trap.
- `support_02`: visible `starts_on`, a domain subscription-selection rule, aligned references, and a differentiating later-plan fixture.
- `support_05`: explicit matching-only baseline, realistic all-account mutant, and no-ticket cohort fixture.
- `support_06`: direct visible incident-account path; hidden date/subscription constraints removed.
- `support_08`: replacement with a realistic numeric-ID identity trap while preserving the blocked slot and distribution.

## Mutant repair

The invalid `fleet_06/m16_no_tiebreak` and `support_06/m26_no_date` mutants were replaced. The weak `support_05/m25_global_avg` mutant was replaced with a plausible all-account baseline mutant. The active set has 61 valid mutants; the real execution gate killed 61/61 with zero survivors and zero invalid executions.

## Hashes and status

- Baseline commit: `caa97316d6d312f9ccc9bcef9e3d44b0f9c8d4cd`
- Old content hash: `4e28b456e67c21675ea7baf4db900db3c50cfd3d5e30d17968cb0f7534d69332`
- Post-repair content hash at artifact generation: `f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8`
- Human acceptance: pending; no case is marked `HUMAN_ACCEPTED`.
