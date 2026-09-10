# M61R.2 — dbt-Comparable Raw-Schema Arm

## Verdict

`M61R2_DBT_COMPARABLE_RAW_SCHEMA_COMPLETE`

This is a separate external ablation, not a production contract change. The
production Candidate C contract remains `3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587` and the
production runtime remains unchanged.

## Result

- Selected contract: `EXTERNAL_B` (`dbbd918219870fb19e9efd4ea93555546967598663a0f9ebe7b9616b69b76e6d`)
- Fresh full-run dbt-comparable result: **93/220**
- Fully stable questions: **2/11**
- Provider calls: **242** (22 selection + 220 full run); retries: **0**.

The historical production-faithful M61R result was **82/220**. These numbers
measure different epistemic contracts: the raw arm permits ordinary inference
from the visible schema, while production Candidate C requires governed
authority before production execution.

No gold SQL, expected result, or correctness label was provider-visible. The
raw proposals were evaluated only in the isolated read-only ACME database with
the retained dbt comparator. This is a dbt-comparable raw-schema arm, not a
production upgrade or an official dbt leaderboard score.
