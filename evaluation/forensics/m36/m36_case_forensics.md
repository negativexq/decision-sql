# M36 — M35R1 SQL Failure Forensics

Provider calls: `0`. M35R1 was not rerun. No M35R1 artifact was modified.

## Frozen baseline verification

- Benchmark content hash: `f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8`
- Recomputed baseline: `22/30` governed, `12/20` answerable, `10/10` non-answerable.
- Frozen summary agreement: YES.
- Governance decision errors: `0`.
- Answerable SQL admission failures: `0`.
- All eight failures were reproduced from the frozen case-result rows as `RESULT_MISMATCH`.

## Eight-case forensic table

| Case | Primary failure | Stage | Base | First failing fixture | Existing mutant |
|---|---|---|---|---|---|
| `commerce_05` | `OUTPUT_SHAPE_ERROR` | `OUTPUT_CONTRACT` | PASS | `cf01` | NOVEL |
| `fleet_03` | `OUTPUT_SHAPE_ERROR` | `OUTPUT_CONTRACT` | FAIL | base | NOVEL |
| `fleet_05` | `OUTPUT_SHAPE_ERROR` | `OUTPUT_CONTRACT` | FAIL | base | NOVEL |
| `fleet_06` | `JSON_TYPE_COERCION_ERROR` | `GROUNDING` | FAIL | base | NOVEL |
| `support_01` | `OUTPUT_SHAPE_ERROR` | `OUTPUT_CONTRACT` | FAIL | base | NOVEL |
| `support_02` | `OUTPUT_SHAPE_ERROR` | `OUTPUT_CONTRACT` | FAIL | base | NOVEL |
| `support_03` | `OUTPUT_SHAPE_ERROR` | `OUTPUT_CONTRACT` | FAIL | base | NOVEL |
| `support_05` | `OUTPUT_SHAPE_ERROR` | `OUTPUT_CONTRACT` | FAIL | base | NOVEL |

The exact generated SQL, request/response hashes, AST decomposition, references, fixtures, and evidence fields are in [m36_case_forensics.json](./m36_case_forensics.json).

## Per-case findings

### `commerce_05`

Question: “For each customer who bought a home product in a completed order, count distinct completed orders containing a home product.”

Visible evidence required the authorized path `orders -> customers`, `order_items -> orders`, `order_items -> products`, completed-order status, product category `home`, and `COUNT(DISTINCT order_id)` at customer grain. All of that evidence was present and operationalized correctly.

Generated SQL:

```sql
SELECT c.customer_id, c.customer_name, COUNT(DISTINCT o.order_id) AS completed_orders_with_home_product
FROM customers AS c
JOIN orders AS o ON o.customer_id = c.customer_id
JOIN order_items AS oi ON oi.order_id = o.order_id
JOIN products AS p ON p.product_id = oi.product_id
WHERE o.status = 'completed'
  AND p.category = 'home'
GROUP BY c.customer_id, c.customer_name
```

The first divergence is projection: `customer_name` is added even though the required result is `customer_id` plus the distinct-order count. The join path, population, predicates, and aggregate are correct. The base database has zero matching rows, so the extra column is hidden by an empty-result coincidence. `cf01` creates one matching row and exposes the three-column output against the two-column contract. `cf02` passes because its pending order is correctly excluded.

Semantic diff:

```text
- SELECT c.customer_id, COUNT(DISTINCT o.order_id)
+ SELECT c.customer_id, c.customer_name, COUNT(DISTINCT o.order_id)
```

This is a novel model failure relative to the active mutants. The exact projection boundary is a benchmark-review candidate because the natural-language question does not explicitly prohibit display attributes; it is not evidence that the metric target itself is wrong. Confidence: HIGH.

### `fleet_03`

Question: “For each route, calculate rounded average kilometres per litre across its trips, excluding zero-fuel ratios.”

The visible metric declares `AVG(distance_km / NULLIF(fuel_liters, 0))`, trip grain, display rounding to two decimals, and the authorized `trip_route` relationship. The model implements the correct ratio, NULL handling, grouping semantics, and rounding.

Generated SQL:

```sql
SELECT r.route_id, r.route_code, r.route_name,
       ROUND(AVG(t.distance_km / NULLIF(t.fuel_liters, 0)), 2) AS average_kilometres_per_litre
FROM routes AS r
JOIN trips AS t ON t.route_id = r.route_id
GROUP BY r.route_id, r.route_code, r.route_name
```

The first divergence is projection: `route_code` and `route_name` are added to the required `route_id` and metric. Base and both fixtures fail immediately on column count, so the fixture purposes never become the limiting semantic distinction for this candidate.

Semantic diff:

```text
- SELECT r.route_id, ROUND(AVG(...), 2)
+ SELECT r.route_id, r.route_code, r.route_name, ROUND(AVG(...), 2)
```

No active mutant matches. This is a projection-contract review candidate because “for each route” does not explicitly say route ID only. Confidence: HIGH.

### `fleet_05`

Question: “For each driver, count trips made in cargo vehicles, returning only drivers with at least one such trip.”

Visible evidence required `trips -> drivers`, `trips -> vehicles`, `vehicle_class = 'cargo'`, and matching-only driver population. The model uses both authorized joins, the correct cargo predicate, and a correct count. Its `HAVING COUNT(...) >= 1` is redundant after the cargo inner joins but not wrong.

Generated SQL:

```sql
SELECT d.driver_id, d.driver_name, COUNT(t.trip_id) AS cargo_trip_count
FROM drivers AS d
JOIN trips AS t ON t.driver_id = d.driver_id
JOIN vehicles AS v ON v.vehicle_id = t.vehicle_id
WHERE v.vehicle_class = 'cargo'
GROUP BY d.driver_id, d.driver_name
HAVING COUNT(t.trip_id) >= 1
```

The first divergence is the extra `driver_name` projection. Base and fixtures fail on the three-column result versus the required two-column result. No relationship, filter, population, or aggregation error was observed.

Semantic diff:

```text
- SELECT d.driver_id, COUNT(t.trip_id)
+ SELECT d.driver_id, d.driver_name, COUNT(t.trip_id)
```

No active mutant matches. Projection boundary is a benchmark-review candidate. Confidence: HIGH.

### `fleet_06`

Question: “For every vehicle with telemetry, return its latest telemetry timestamp and documented engine temperature; if timestamps tie, use the highest event ID, and list vehicles by vehicle ID.”

The visible context documents `telemetry_events.payload.engine.temperature_c` as `NUMERIC`, the exact JSON path, the `telemetry_vehicle` relationship, and the timestamp/event-ID ordering. The model’s `DISTINCT ON` subquery is semantically equivalent to the references for latest-row selection and tie-breaking.

Generated SQL:

```sql
SELECT v.vehicle_id, t.event_at AS latest_telemetry_timestamp,
       t.payload #>> '{engine,temperature_c}' AS engine_temperature_c
FROM vehicles AS v
JOIN (
    SELECT DISTINCT ON (vehicle_id) vehicle_id, event_at, payload
    FROM telemetry_events
    ORDER BY vehicle_id, event_at DESC, event_id DESC
) AS t ON t.vehicle_id = v.vehicle_id
ORDER BY v.vehicle_id;
```

The first divergence is type propagation: PostgreSQL `#>>` returns text, while the declared attribute and both references require `(... #>> ... )::numeric`. The JSON path itself is correct. Base and both counterfactuals fail on typed result comparison, not on latest-row selection.

Semantic diff:

```text
- payload #>> '{engine,temperature_c}'
+ (payload #>> '{engine,temperature_c}')::numeric
```

This is a novel failure; none of `m16_oldest`, `m16_returns_all_events`, or `m16_global` describes it. It is detectable from visible attribute type plus generated expression typing. Confidence: HIGH.

### `support_01`

Question: “For every account, count urgent support tickets, including accounts with zero urgent tickets.”

The visible population requirement is `PRESERVE_ANCHOR`. The model correctly uses `LEFT JOIN` and places the urgent predicate in an aggregate `FILTER`, preserving zero-urgent accounts. The ticket-account relationship is authorized.

Generated SQL:

```sql
SELECT a.account_id, a.account_name,
       COUNT(st.ticket_id) FILTER (WHERE st.priority = 'urgent') AS urgent_ticket_count
FROM accounts AS a
LEFT JOIN support_tickets AS st ON st.account_id = a.account_id
GROUP BY a.account_id, a.account_name
ORDER BY a.account_id
```

The first divergence is the added `account_name`. It causes the three-column result to fail against the two-column contract; the population and filter scope are correct. No active mutant matches. Projection boundary is a benchmark-review candidate. Confidence: HIGH.

Semantic diff:

```text
- SELECT a.account_id, COUNT(...) FILTER (...)
+ SELECT a.account_id, a.account_name, COUNT(...) FILTER (...)
```

### `support_02`

Question: “List tickets whose first agent response exceeded the first-response SLA of the account's most recently started subscription.”

The model uses the required authorized path, selects the latest subscription by `starts_on DESC, subscription_id DESC`, takes `MIN(agent_response)` after `opened_at`, and applies a strict `>` SLA comparison. The extra `accounts` join is authorized and unnecessary, but not semantically wrong.

Generated SQL selects:

```text
t.ticket_id,
t.account_id,
t.opened_at,
r.first_response_at,
sp.first_response_sla_hours,
r.first_response_at - t.opened_at
```

The first divergence is output shape: the question’s required output is `ticket_id`, while the model exposes five additional intermediate/diagnostic values. All base and fixture attempts fail on column count before the correct SLA logic can receive credit. No active mutant matches. Projection boundary is a benchmark-review candidate. Confidence: HIGH.

### `support_03`

Question: “For each account with tickets, report the urgent-ticket share rounded to four decimals.”

The model uses the correct matching-only inner join, numerator `FILTER`, denominator `COUNT(*)`, `NULLIF`, and four-decimal rounding. It adds `account_name` to the requested account ID and share.

Generated SQL:

```sql
SELECT a.account_id, a.account_name,
       ROUND(COUNT(*) FILTER (WHERE t.priority = 'urgent')::numeric
             / NULLIF(COUNT(*), 0), 4) AS urgent_ticket_share
FROM accounts AS a
JOIN support_tickets AS t ON t.account_id = a.account_id
GROUP BY a.account_id, a.account_name
```

The first divergence is output shape, not ratio semantics. No active mutant matches. Projection boundary is a benchmark-review candidate. Confidence: HIGH.

### `support_05`

Question: “Find accounts with at least one ticket whose ticket count is above the average count among accounts with at least one ticket.”

The model correctly builds per-account ticket counts, computes the average over that matching-only count population, and applies `>` to the per-account count. It does not include zero-ticket accounts in the denominator. No relationship is required.

Generated SQL selects:

```text
a.account_id,
a.account_name,
tc.ticket_count
```

The first divergence is the extra `account_name`. The nested population and comparison are correct; base and fixtures fail on column count. No active mutant matches. Projection boundary is a benchmark-review candidate. Confidence: HIGH.

## Root-cause distribution

| Root cause family | Count | Cases |
|---|---:|---|
| `OUTPUT_SHAPE_ERROR` | 7 | `commerce_05`, `fleet_03`, `fleet_05`, `support_01`, `support_02`, `support_03`, `support_05` |
| `JSON_TYPE_COERCION_ERROR` | 1 | `fleet_06` |

The dominant failure family is output-contract/projection discipline. Seven SQL statements preserve the core semantics but add display or intermediate columns. One statement fails to carry a visible NUMERIC type through a JSON extraction.

## Failure-stage distribution

| Stage | Count |
|---|---:|
| GROUNDING | 1 |
| RELATIONSHIP_RESOLUTION | 0 |
| SEMANTIC_COMPOSITION | 0 |
| AGGREGATION/GRAIN | 0 |
| TEMPORAL | 0 |
| OUTPUT_CONTRACT | 7 |
| BENCHMARK | 0 confirmed defects |

Relationship result: wrong or missing path `0/8`; correct path or no relationship required with a downstream failure `8/8`.

Population/filter result: `0/8` contributed. Aggregation/grain result: `0/8` contributed. Temporal/latest-row result: `0/8` contributed.

## Counterfactual contribution

Among 20 answerable cases:

- Base execution passed: `13/20 = 65.0%`.
- Full fixture suite passed: `12/20 = 60.0%`.
- Base-only false positives: `1` — `commerce_05`.

`commerce_05` passed the empty base because no row existed on which the extra projection could be compared. `cf01` added a matching completed order with two home lines, producing a row and exposing the three-column output. A classic single-instance evaluator would have incorrectly accepted that SQL as a base pass.

## Mutant overlap

Exact or near existing mutant match: `0/8`.

Novel failures: `8/8`.

The active mutants cover the semantic distinctions the model’s SQL mostly handled correctly: distinct order counting, population/filter placement, aggregate grain, latest-row ordering, SLA selection, ratio construction, and nested denominator population. They do not cover the observed projection-overselection family or JSON text-to-numeric coercion.

## Evidence/context sufficiency

- Required semantic evidence visibly present: `8/8`.
- Missing model-visible context: `0/8`.
- Authority sufficient: `8/8`.
- Question sufficient for the semantic core: `8/8`.
- Exact projection boundary review candidates: `7/8`.

This is not a retrieval or hidden-authority failure. The seven projection cases expose a contract-clarity risk: the questions identify an entity-level task and measure but do not always enumerate the only allowed output columns. That is a review candidate, not a confirmed defective semantic target.

## Correct-vs-failed complexity comparison

The deterministic SQL-AST vector is in [m36_complexity_comparison.json](./m36_complexity_comparison.json). Medians/ranges for the 12 correct versus 8 incorrect answerable cases:

| Vector | Correct median (range) | Incorrect median (range) |
|---|---:|---:|
| entities | 2 (1–3) | 2 (2–5) |
| relationship hops | 1 (0–2) | 1 (1–5) |
| predicates | 2 (1–10) | 2 (1–8) |
| aggregates | 1 (0–2) | 1 (0–3) |
| grouping keys | 1 (0–2) | 2 (0–3) |
| nested depth | 0 (0–1) | 0 (0–2) |
| temporal mechanism flag | 0 (0–1) | 0 (0–1) |
| arithmetic nodes | 0 (0–4) | 0 (0–3) |
| semantic mechanism tags | 5 (3–9) | 5 (3–6) |

There is overlap across every median. The small sample does not support a causal claim that composition depth caused the failures. The observed failure signature is much more specific: projection discipline.

## Shared failure signatures

1. **Core grounding and relational semantics were usually correct.** All eight used authorized relationships correctly or required no relationship.
2. **Seven cases over-projected.** The model added names or intermediate SLA values that were visible and plausible but not part of the canonical result.
3. **One case lost a declared type.** `fleet_06` extracted the right JSON value but left it as text.
4. **No governance failure occurred.** M35R1 selected the correct governed decision for all 30 cases; these are downstream SQL-output failures.

## Runtime detectability

- Extra projection: **PARTIALLY_DETECTABLE** without an explicit requested-column contract; deterministic once the allowed output projection is explicit.
- JSON type coercion: **DETECTABLE** from visible attribute type, JSON path, and generated expression typing.
- Unauthorized joins: not observed; the existing admission layer remains useful but did not explain these failures.

Any future detector must use only question, visible authority, and generated SQL. It must not use semantic targets, reference SQL, fixture results, or gold outputs at runtime.

## What M35R1 actually proves

- The provider-safe contract worked and produced 30 genuine Luna responses.
- Governed decision selection was perfect on this pilot: `30/30`.
- The remaining accuracy loss is downstream of decision selection.
- Counterfactual fixtures found one false positive that base-only execution would have accepted.
- The real model failures were not the pre-authored mutant families; the mutation suite did not cover projection discipline or JSON type coercion.

## What M35R1 does not prove

- It does not establish population-wide rates from eight failures in a 30-case development pilot.
- It does not prove Luna cannot perform relationship, population, aggregation, or temporal semantics; those operations were correct in the failed SQLs where they were exercised.
- It does not prove that a larger model, more reasoning, retries, selectors, agents, or routers are needed.
- It does not prove the seven projection targets are defective; it identifies an output-contract ambiguity worth human review.

## Recommended single M37 experiment

**M37 — Explicit Output-Projection Contract Ablation**

Hypothesis: a server-owned, model-visible per-case projection contract that names the allowed output fields will remove the seven over-projection failures without changing relationship, population, aggregation, temporal, provider, or model settings.

Success criterion: with the same frozen M35R1 model/configuration and one call per case, answerable test-suite accuracy improves over `12/20`, at least six of the seven projection cases no longer fail on output shape, and governance remains `10/10`.

Failure criterion: projection errors persist at the same rate, or the added projection contract causes semantic regressions so full-suite accuracy does not exceed `12/20`.

This recommendation isolates one variable. It does not implement M37.
