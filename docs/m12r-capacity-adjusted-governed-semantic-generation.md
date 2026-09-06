# M12R — Capacity-Adjusted Governed Semantic Generation

M12R evaluates the complete frozen M3 state space without expanding M3. The
current provider-facing governed plan contains only `applicable`,
`metric_name`, and ordered `dimensions`; the compiler accepts at most two valid
dimensions. That yields 109 distinct accepted ordered plans: 10 scalar, 31
one-dimensional, and 68 two-dimensional.

Each plan receives one deterministic fresh natural-language question. Gold SQL
is compiled by the existing server-owned `MetricCompiler`, admitted through M1,
and executed in the demo PostgreSQL database before provider exposure. Grouped
results use the repaired typed `VALUE_BAG` comparison contract; scalar results
use `SCALAR`.

The primary split is 81 cases (7 scalar, 23 one-dimensional, 51 two-dimensional)
and the sealed confirmation split is 28 cases (3, 8, and 17). Direct SQL
generation and strict governed-plan proposal use the same provider, model,
context, and disabled memory/retrieval settings. There is no fallback, repair,
judge, or prompt tuning.

The LLM proposes. Deterministic software decides what may execute.

Experiment population must match the frozen capability being tested; do not
expand the capability merely to satisfy an arbitrary sample-size target.

After a frozen bounded capability exists, evaluate it directly. Provider
outcomes produce a measured capability result, not another mechanism selection
review. A positive result proceeds to runtime implementation. A negative result
closes the consumed intervention attempt.
