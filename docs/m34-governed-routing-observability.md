# M3.4 — Feature-Flagged Governed Metric Routing & Observability

M3.4 integrates the accepted governed metric path without changing its
semantic catalog or compiler. The route is explicit, feature-gated, and
default-off.

## Modes

`DECISION_SQL_GOVERNED_METRICS_MODE` accepts `off`, `shadow`, or `on` and
defaults to `off`. `DECISION_SQL_GOVERNED_METRICS_SHADOW_EXECUTE` defaults to
`false`.

- `OFF` runs the existing direct Text-to-SQL service and makes no governed
  grounding call.
- `SHADOW` preserves the direct result as authoritative, performs one bounded
  governed grounding attempt, and compiles/plans the governed candidate. It
  executes the governed candidate only when `shadow_execute=true`; any
  comparison is diagnostic.
- `ON` uses the governed compiler when explicit applicability, metric identity,
  dimensions, compilation, M1 planning, and execution all succeed. A
  not-applicable or invalid governed attempt falls back to direct SQL with a
  typed status and reason. No fuzzy metric substitution or semantic repair is
  performed.

Compiler, M1, and execution failures retain their governed error status and
are also recorded when the safe direct fallback policy preserves the existing
user path. M1 is never bypassed: compiled SQL is always submitted as a
`SEMANTIC_METRIC_COMPILER` candidate to the unchanged M1 service.

## Contract and observability

The provider-facing contract is explicitly applicable:

```json
{"applicable": true, "metric_name": "completed_revenue", "dimensions": ["customer"]}
```

or:

```json
{"applicable": false, "metric_name": null, "dimensions": []}
```

The adapter performs only strict catalog validation and identifier
normalization. It does not accept physical tables/columns, formulas, filters,
join paths, or arbitrary confidence values.

The route adds bounded spans:

- `decision_sql.route`
- `decision_sql.semantic.grounding`
- `decision_sql.semantic.compile`

Attributes include mode, bounded route/status/path, applicability, catalog
metric name, dimension count, fallback reason, token counts when available,
and grounding/compiler/route latency. Full questions, prompts, generated SQL,
result rows, secrets, authorization headers, and hidden reasoning are not span
attributes. Existing M1 plan/execution spans remain the authority and are not
duplicated.

## Routing validation

The frozen corpus is `evaluation/fixtures/m34_routing.json` with 40 cases: 20
governed-applicable and 20 direct-path controls. Its SHA-256 is
`c35ae975f7baf549f3a5014115adf039634e187fc5964eca64bca861467f5b93`.

The valid ON validation run is
`evaluation/results/m34/20260903T164500Z/` (local ignored evaluation data).
It used `gpt-5.6-luna`, `reasoning=none`, omitted temperature/provider default,
one grounding call per case, and no repair or retry. Results:

| Check | Result |
| --- | ---: |
| Route accuracy | 40/40 (100.00%) |
| Governed precision | 20/20 (100.00%) |
| Governed recall | 20/20 (100.00%) |
| Metric accuracy | 20/20 (100.00%) |
| Dimension exact accuracy | 20/20 (100.00%) |
| False governed routes | 0 |
| False direct routes | 0 |

The run made 40 grounding calls and 20 direct SQL fallback calls. Governed
successes made no direct SQL call. Unit tests additionally verify OFF, SHADOW
with execution disabled/enabled, ON governed success, not-applicable fallback,
invalid metric handling, M1 rejection, and bounded span attributes. The
governed path remains default-off; this milestone does not activate production
routing.

## Scope

M3.4 does not add temporal reasoning, value grounding, user-defined filters,
retrieval, verified examples, repair, new metrics, catalog hot reload, or
external benchmark reruns. It is an integration and observability checkpoint,
not a new accuracy benchmark. Activation is a separate operational decision.
