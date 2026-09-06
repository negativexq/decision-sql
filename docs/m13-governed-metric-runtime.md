# M13 — Bounded Governed Metric Runtime Integration

M12R established a fresh generation capability for the frozen M3 contract:
`applicable=true`, one server-owned `metric_name`, and zero through two
server-owned dimensions. M13 integrates that capability behind an explicit
server-owned execution scope.

The available scopes are `DIRECT` and `GOVERNED_METRIC`. The default is
`DIRECT`; there is no automatic natural-language classifier. The governed
runtime is additionally protected by the default-off
`DECISION_SQL_GOVERNED_METRIC_RUNTIME_ENABLED` flag.

The LLM does not grant itself access to a governed execution path. The
server-owned request contract decides whether that path may be attempted. In
the governed path, the provider proposes only the frozen M3 plan DTO; strict
validation, `MetricCompiler`, M1, and read-only execution remain deterministic
server-owned steps. Pre-M1 plan failures may fall back to the existing direct
path. Once governed SQL passes M1, execution failure and policy invariant
failure are returned without silently changing semantic strategy.

M12R already established the bounded generation capability. M13 is production
integration, not capability reselection.

The LLM proposes. Deterministic software decides what may execute.

Fresh implementation experiments replace residual-mining reviews. Once a
bounded capability is testable, run the control/intervention experiment instead
of reopening architecture selection. A failed fresh experiment closes that
exact attempt; it does not authorize tuning on the consumed holdout. A
successful fresh experiment proceeds directly to bounded runtime integration.

Implementation and measurement remain the default after a bounded mechanism
has passed selection. The governed path is a bounded M3 capability, not a
general Text-to-SQL applicability claim.
