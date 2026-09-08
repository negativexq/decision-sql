# Pilot Human Review Queue V2

M34.1 is an adversarial audit of the original pilot. The queue is ordered REJECT, then REVISE, then ACCEPT. No case status or `human_accepted` field was changed.

## REJECT

### support_08 — AUTHORITY_BLOCKED

Question: “Join ticket requester email to incident code and list incident severities.”

Why: The requested linkage is not a plausible analyst operation: an email is being matched to an incident code with no believable shared identity. This is a toy authority trap, not a realistic governed integration decision. Repair would require replacing the core trap.

Intended mechanism: fail closed on an unauthorized physical-looking join.

Replacement direction: use two plausible external case/incident identifiers that physically align but have an explicitly missing authorized relationship.

## REVISE

### commerce_01 — ANSWERABLE

Question: “List active customers in the North region with their customer identifiers and names.”

Required changes: set result comparison to unordered or state `ORDER BY customer_id`; retain the useful filter smoke test.

### commerce_02 — ANSWERABLE

Question: “Return order IDs for completed orders created during June 2026, including June 1 and excluding July 1.”

Required changes: remove hidden `order_id` ordering from the result contract unless the question states it.

### commerce_03 — ANSWERABLE

Question: “For every customer region, count completed orders and keep regions with no completed orders.”

Required changes: remove hidden region ordering from the result contract or add it to the question.

### commerce_04 — ANSWERABLE

Question: “For each product category, calculate the rounded net value of completed order lines after the order discount.”

Required changes: remove hidden category ordering; add a fixture for captured line price versus current product price and/or rounding stage; make the wording less SQL-shaped.

### commerce_05 — ANSWERABLE

Question: “For each customer who bought a home product in a completed order, count distinct completed orders containing a home product.”

Required changes: remove hidden customer ordering; simplify the repeated product/order wording without losing distinct-order semantics.

### commerce_06 — ANSWERABLE

Question: “For every region, report the average number of completed orders per customer, including customers and regions with zero completed orders.”

Required changes: define two-decimal rounding in the question or visible authority, remove the unrelated net-value dependency, and remove hidden region ordering.

### commerce_07 — ANSWERABLE

Question: “Show the three customers with the highest rounded completed-order net value, returning customer ID and net value.”

Required changes: specify deterministic tie-breaking and ranked output order, or make ties/order non-semantic; align the result contract and references.

### fleet_01 — ANSWERABLE

Question: “List telemetry event IDs and vehicle IDs where documented engine temperature is at least 100 Celsius.”

Required changes: remove hidden event ordering or state it; optionally replace “documented” with operator-facing wording.

### fleet_02 — ANSWERABLE

Question: “For each depot, return the rounded average fuel purchase cost, calculated as liters times price per liter.”

Required changes: expose fuel attributes; add an authorized purchase-depot relationship or use the vehicle-home-depot path consistently; disambiguate depot meaning; add a distinguishing fixture; remove hidden ordering.

### fleet_03 — ANSWERABLE

Question: “For each route, calculate rounded average kilometres per litre across its trips, excluding zero-fuel ratios.”

Required changes: remove hidden route ordering; make the analyst wording less template-like.

### fleet_04 — ANSWERABLE

Question: “For every vehicle, count maintenance events in the 30 days before the benchmark time, including vehicles with none.”

Required changes: expose `maintenance_events` attributes used by the question and references; remove hidden vehicle ordering.

### fleet_05 — ANSWERABLE

Question: “For each driver, count trips made in cargo vehicles, returning only drivers with at least one such trip.”

Required changes: remove hidden driver ordering or state it.

### fleet_06 — ANSWERABLE

Question: “For every vehicle, return its latest telemetry timestamp and documented engine temperature.”

Required changes: either say “vehicles with telemetry” or preserve all vehicle anchors with explicit NULL behavior; state event_id tie-breaking; replace the no-tiebreak mutant or make the tie rule visible; separate latest-row selection from display ordering.

### fleet_08 — AUTHORITY_BLOCKED

Question: “Join weather snapshots to routes through their matching free-text route code and show route names.”

Required changes: expose `weather_snapshots.route_code` and correct the denied relationship endpoint from `routes.route_name` to the matching route-code attribute.

### fleet_10 — AMBIGUOUS

Question: “Which vehicles were recently maintained?”

Required changes: expose maintenance event time/relationship metadata; either keep clarification with both interpretations visible or define one interpretation and convert to ANSWERABLE.

### support_01 — ANSWERABLE

Question: “For every account, count urgent support tickets, including accounts with zero urgent tickets.”

Required changes: remove hidden account ordering or state it.

### support_02 — ANSWERABLE

Question: “List tickets whose first agent response exceeded the subscribed plan's first-response SLA.”

Required changes: define current versus ticket-time subscription selection and expose `starts_on`/validity semantics; make both references use the same policy; add a multi-plan fixture; remove hidden ticket ordering.

### support_03 — ANSWERABLE

Question: “For each account with tickets, report the urgent-ticket share rounded to four decimals.”

Required changes: remove hidden account ordering; remove the false relationship tag unless a relationship is actually tested; make wording less template-like.

### support_04 — ANSWERABLE

Question: “List ticket IDs opened through the documented chat channel and their status.”

Required changes: remove hidden ticket ordering or state it.

### support_05 — ANSWERABLE

Question: “Find accounts whose ticket count is above the average ticket count per account.”

Required changes: explicitly define whether zero-ticket accounts enter the baseline; replace `m25_global_avg` with a realistic competing population mutant; remove unused relationship/correlated tags; remove hidden ordering.

### support_06 — ANSWERABLE

Question: “Return distinct account IDs that have an open ticket or a high-severity incident.”

Required changes: add the intended incident date window or remove it; remove or justify the subscription condition; expose incident severity and started_at; use direct incident-account authority unless subscription membership is intentional; replace the no-date mutant after correction; remove hidden ordering.

## ACCEPT

### commerce_08 — AUTHORITY_BLOCKED

Clear, realistic external-code trap; both fields explicitly state that the linkage is not authorized.

### commerce_09 — AMBIGUOUS

Recent signup and recent completed activity are both reasonable, and the fixture separates them.

### commerce_10 — POLICY_BLOCKED

Plausible retention cleanup request directly conflicts with read-only policy.

### fleet_07 — ANSWERABLE

Top-three ranking, rounding, descending order, and route-ID tie-break are all supported by the wording and metric authority.

### fleet_09 — AUTHORITY_BLOCKED

Realistic legacy device-code trap with explicit non-authority metadata.

### support_07 — AUTHORITY_BLOCKED

Realistic requester/contact identity-resolution trap with explicit non-authority metadata.

### support_09 — AMBIGUOUS

Status-active and not-expired interpretations are both common and fixture-distinguishable; add an open-ended expiry rule later.

### support_10 — POLICY_BLOCKED

Plausible ticket-retention deletion request directly conflicts with read-only policy.
