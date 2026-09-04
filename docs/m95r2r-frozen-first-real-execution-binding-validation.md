# M9.5R.2R — Frozen-First Real Execution Binding Integration Validation

## Status

This restart is **M95R2R_CLEAN_SELECTION_BLOCKED**. No database connection,
M1 plan, PostgreSQL execution, provider call, or SQL-generation call occurred
during the restart.

The preceding M9.5R.2 attempt is recorded as
**M95R2_INVALIDATED_RECONNAISSANCE**. It performed case-level reconnaissance
against 48 M9.5R.1 executable cases before the required freeze gate. Those
case IDs are preserved in
`evaluation/fixtures/m95r2r_reconnaissance_exclusion.json` and are excluded
from this restart's eligible population.

## Clean-selection result

The frozen M9.5R.1 executable population has 120 cases. After excluding the
48 contaminated IDs, the required M9.5R.2R composition cannot be satisfied:

| Required family | Required | Eligible |
| --- | ---: | ---: |
| governed positives | 10 | 10 |
| unique/direct qualified positives | 7 | 0 |
| CTE/derived passthrough positives | 4 | 0 |
| aggregate/distinct positives | 3 | 4 |
| arithmetic/ratio positives | 2 | 6 |
| case/safe-cast positives | 1 | 4 |
| window positives | 2 | 4 |
| scalar/bounded nested positives | 1 | 2 |

The required 20-case direct-positive subset therefore has no valid
freeze-first selection. No case replacement or post-reconnaissance selection
was attempted, and no M9.5R.2R manifest or freeze lock was created.

## Frozen evidence

The M9.5R.1 binder-v2 evidence remains unchanged and is not revalidated here.
The binder-v2 source hash remains
`ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27ecda3b78fb8e`; the V2
comparator hash remains
`677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97`.

This restart produced no integration evidence. M10 remains fully unconsumed:
no corpus was created, no case was consumed, and
`M10_BINDING_PROTOCOL_READY` remains false.

The next milestone is **M9.5R.2S — Fresh Real-Execution Integration Fixture
Construction**.
