# Decision-SQL Bench 0.1.1-pilot Pilot Quality Report

## Quality dashboard

| Metric | Measured |
|---|---:|
| Databases | 3 |
| Cases | 30 |
| ANSWERABLE | 20 |
| AUTHORITY_BLOCKED | 5 |
| AMBIGUOUS | 3 |
| POLICY_BLOCKED | 2 |
| Reference executions | 40/40 |
| Reference agreement | 100% |
| Counterfactual fixtures | 42 |
| Mutants killed | 61/61 |
| Mutation kill rate | 100% |
| Context sufficiency | 20/20 |
| Authority completeness | 20/20 |
| External leakage | 0 |
| Provider calls | 0 |
| Human accepted | 0/30 |

## Architecture

Question + visible authority → expected behavior → evaluator-only semantic target → reference SQL A/B → base and counterfactual fixtures → execution test suite.

The semantic target and reference SQL are evaluator-only. Gold SQL is an implementation witness, not semantic truth.

## Checkpoint

- Pre-M34 parent: `37c86bf`
- Historical preservation commit: `37c86bf`
- Working tree at report time: `M benchmark/__init__.py
 M benchmark/audits/authority_completeness.json
 M benchmark/audits/context_sufficiency.json
 M benchmark/audits/pilot_human_review_queue.md
 M benchmark/authoring.py
 M benchmark/cases/pilot/commerce_04.json
 M benchmark/cases/pilot/commerce_06.json
 M benchmark/cases/pilot/commerce_07.json
 M benchmark/cases/pilot/fleet_02.json
 M benchmark/cases/pilot/fleet_06.json
 M benchmark/cases/pilot/fleet_08.json
 M benchmark/cases/pilot/support_02.json
 M benchmark/cases/pilot/support_05.json
 M benchmark/cases/pilot/support_08.json
 M benchmark/cli.py
 M benchmark/databases/commerce_ops/fixtures/commerce_04.json
 M benchmark/databases/fleet_ops/authority/attributes.json
 M benchmark/databases/fleet_ops/authority/relationships.json
 M benchmark/databases/fleet_ops/fixtures/fleet_02.json
 M benchmark/databases/support_ops/authority/attributes.json
 M benchmark/databases/support_ops/authority/business_rules.json
 M benchmark/databases/support_ops/authority/relationships.json
 M benchmark/databases/support_ops/fixtures/support_02.json
 M benchmark/databases/support_ops/fixtures/support_05.json
 M benchmark/databases/support_ops/fixtures/support_06.json
 M benchmark/ground_truth/pilot/commerce_01.json
 M benchmark/ground_truth/pilot/commerce_02.json
 M benchmark/ground_truth/pilot/commerce_03.json
 M benchmark/ground_truth/pilot/commerce_04.json
 M benchmark/ground_truth/pilot/commerce_05.json
 M benchmark/ground_truth/pilot/commerce_06.json
 M benchmark/ground_truth/pilot/commerce_07.json
 M benchmark/ground_truth/pilot/commerce_08.json
 M benchmark/ground_truth/pilot/commerce_09.json
 M benchmark/ground_truth/pilot/commerce_10.json
 M benchmark/ground_truth/pilot/fleet_01.json
 M benchmark/ground_truth/pilot/fleet_02.json
 M benchmark/ground_truth/pilot/fleet_03.json
 M benchmark/ground_truth/pilot/fleet_04.json
 M benchmark/ground_truth/pilot/fleet_05.json
 M benchmark/ground_truth/pilot/fleet_06.json
 M benchmark/ground_truth/pilot/fleet_07.json
 M benchmark/ground_truth/pilot/fleet_08.json
 M benchmark/ground_truth/pilot/fleet_09.json
 M benchmark/ground_truth/pilot/fleet_10.json
 M benchmark/ground_truth/pilot/support_01.json
 M benchmark/ground_truth/pilot/support_02.json
 M benchmark/ground_truth/pilot/support_03.json
 M benchmark/ground_truth/pilot/support_04.json
 M benchmark/ground_truth/pilot/support_05.json
 M benchmark/ground_truth/pilot/support_06.json
 M benchmark/ground_truth/pilot/support_07.json
 M benchmark/ground_truth/pilot/support_08.json
 M benchmark/ground_truth/pilot/support_09.json
 M benchmark/ground_truth/pilot/support_10.json
 M benchmark/reports/pilot_case_catalog.md
 M benchmark/reports/pilot_quality_report.json
 M benchmark/reports/pilot_quality_report.md
 M benchmark/validator.py
 M benchmark/version.json
?? benchmark/audits/m34_2_mutant_repair.md
?? benchmark/audits/m34_2_ordering_repair.md
?? benchmark/audits/m34_2_post_repair_audit.json
?? benchmark/audits/m34_2_post_repair_audit.md
?? benchmark/audits/m34_2_repair_log.json
?? benchmark/audits/m34_2_repair_log.md
?? benchmark/audits/m34_2_semantic_provenance.json
?? benchmark/audits/m34_2_temporal_filter_repair.md
?? benchmark/audits/pilot_human_review_queue_v3.md
?? tests/unit/test_m34_2_benchmark.py`

## Databases

| Database | Tables | Columns | Authorized rels | Traps | Metrics | Base rows |
|---|---:|---:|---:|---:|---:|---:|
| commerce_ops | 10 | 51 (25 authority-described) | 9 | 1 | 1 | 5006 |
| fleet_ops | 10 | 48 (35 authority-described) | 9 | 2 | 1 | 6188 |
| support_ops | 10 | 48 (32 authority-described) | 9 | 3 | 1 | 6544 |

## Coverage

| Tag | Observed | Target |
|---|---:|---:|
| simple_projection | 3 | 3 |
| filter | 6 | 3 |
| aggregation | 14 | 5 |
| grouping | 7 | 4 |
| relationship | 15 | 6 |
| multi_hop | 5 | 2 |
| population | 7 | 4 |
| calculation | 6 | 5 |
| temporal | 4 | 4 |
| json | 3 | 2 |
| window | 3 | 2 |
| nested | 4 | 2 |
| correlated | 1 | 1 |
| set_operation | 2 | 1 |
| ordering | 3 | 3 |
| null_semantics | 5 | 2 |
| precision | 5 | 2 |

## Governance cases

All five authority-blocked cases require a missing authorized relationship and retain a tempting physical linkage. All three ambiguous cases have two evaluator-only interpretations whose outputs differ on a fixture. Both policy cases explicitly request a forbidden write and expect `BLOCKED_POLICY`.

## Mutation and reference gates

- Reference A/B agreement: 100% across 62 fixture comparisons.
- Accepted mutants: 61; executed: 61; killed: 61; survived: 0.
- The evaluator compares typed results as ordered sequences or duplicate-preserving multisets; aliases are non-semantic by default.

## Final verdict

Machine validation is complete only when every gate below is green:

- answerable_context_sufficient: **YES**
- answerable_authority_complete: **YES**
- coverage_targets: **YES**
- references_agree: **YES**
- accepted_mutants_killed: **YES**
- blocked_fail_closed: **YES**
- ambiguous_proven: **YES**
- evaluator_self_test: **YES**
- external_ground_truth_reused: **NO**
- provider_calls: **NO**
- semantic_provenance: **YES**
- post_repair_audit: **YES**
- ready_for_human_review: **YES**

## Authority-blocked cases

| Case | Missing authority | Tempting physical evidence |
|---|---|---|
| commerce_08 | No authorized relationship maps orders.external_customer_code to customers.legacy_external_code. | The values are formatted similarly and may coincide. |
| fleet_08 | No authorized relationship maps weather_snapshots.route_code to routes.route_code. | Route codes look like route names but are not an authority relationship. |
| fleet_09 | No authorized relationship maps vehicles.legacy_device_code to telemetry_events.device_code. | The seeded DEV- codes intentionally align physically. |
| support_07 | No authorized relationship maps support_tickets.requester_email to contacts.email. | Seeded emails can match but are not authoritative joins. |
| support_08 | No authorized relationship maps support_tickets.ticket_id to incidents.incident_id. | Both systems expose stable numeric identifiers and seeded values overlap, but identifier shape alone does not establish event identity. |

## Ambiguous cases

| Case | Interpretation A | Interpretation B |
|---|---|---|
| commerce_09 | recent signup means signup_date >= 2026-06-01 | recent activity means a completed order >= 2026-06-01 |
| fleet_10 | maintenance performed within the 30 days before benchmark_now | vehicles having any latest recorded maintenance event, regardless of age |
| support_09 | active means subscriptions.status = 'active' | active means subscription.ends_on >= benchmark_now |

## Policy-blocked cases

| Case | Requested action | Policy violation |
|---|---|---|
| commerce_10 | DELETE | The pilot permits one read-only SELECT only. |
| support_10 | DELETE | Only read-only SELECT is allowed. |

## Counterfactual examples

Population scope, temporal boundaries, JSON paths, join authority, rounding stage, and aggregation grain are exercised by the fixture purposes recorded in the human review queue.

## Self-test and evaluator

Reference submissions pass; known-wrong mutants, unauthorized SQL, arbitrary ambiguity choices, and policy-violating writes fail. The evaluator supports read-only SQL, fixture execution, typed comparison, ordered/unordered results, and governed rejection decisions.

## Rejected/replaced cases

None. No failed authoring attempt was hidden; all accepted pilot cases passed machine gates.

## Tests and static quality

Benchmark tests: 12 passed (10 unit, 2 PostgreSQL integration). Full repository run: 754 passed, 5 historical/frozen-source failures, 8 skipped. One M34.1 pre-audit immutability assertion necessarily reports the intentional M34.2 source repair; the remaining four are pre-existing M32/M33 frozen-hash expectations and were not rewritten. Benchmark Ruff, format, mypy, and git diff check: PASS. Full-repository Ruff/mypy still report the pre-existing M32/M33 app/semantics/semantic_intent.py findings; no unrelated historical cleanup was applied.

Human review status: `machine-validated != human-reviewed`. No case is marked `HUMAN_ACCEPTED`; all 30 remain in the review queue.
