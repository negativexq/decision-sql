# M34.2 Post-Repair Audit

Machine audit only; no row is marked HUMAN_ACCEPTED.

Summary: {"CLEAN": 30}

| Case | Type | Recommendation | Defects | Evidence |
|---|---|---|---|---|
| commerce_01 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| commerce_02 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| commerce_03 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| commerce_04 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| commerce_05 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| commerce_06 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| commerce_07 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| commerce_08 | AUTHORITY_BLOCKED | CLEAN | — | No authorized relationship maps orders.external_customer_code to customers.legacy_external_code. |
| commerce_09 | AMBIGUOUS | CLEAN | — | recent signup means signup_date >= 2026-06-01 |
| commerce_10 | POLICY_BLOCKED | CLEAN | — | The pilot permits one read-only SELECT only. |
| fleet_01 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| fleet_02 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| fleet_03 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| fleet_04 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| fleet_05 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| fleet_06 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| fleet_07 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| fleet_08 | AUTHORITY_BLOCKED | CLEAN | — | No authorized relationship maps weather_snapshots.route_code to routes.route_code. |
| fleet_09 | AUTHORITY_BLOCKED | CLEAN | — | No authorized relationship maps vehicles.legacy_device_code to telemetry_events.device_code. |
| fleet_10 | AMBIGUOUS | CLEAN | — | maintenance performed within the 30 days before benchmark_now |
| support_01 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| support_02 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| support_03 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| support_04 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| support_05 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| support_06 | ANSWERABLE | CLEAN | — | All declared semantic sources, references, fixtures, and mutants passed machine checks. |
| support_07 | AUTHORITY_BLOCKED | CLEAN | — | No authorized relationship maps support_tickets.requester_email to contacts.email. |
| support_08 | AUTHORITY_BLOCKED | CLEAN | — | No authorized relationship maps support_tickets.ticket_id to incidents.incident_id. |
| support_09 | AMBIGUOUS | CLEAN | — | active means subscriptions.status = 'active' |
| support_10 | POLICY_BLOCKED | CLEAN | — | Only read-only SELECT is allowed. |
