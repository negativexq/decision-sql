# Decision-SQL Bench v0.1 Pilot Quality Report

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
| Counterfactual fixtures | 40 |
| Mutants killed | 60/60 |
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
- Working tree at report time: `?? benchmark/
?? tests/integration/test_m34_benchmark_postgres.py
?? tests/unit/test_m34_benchmark.py`

## Databases

| Database | Tables | Columns | Authorized rels | Traps | Metrics | Base rows |
|---|---:|---:|---:|---:|---:|---:|
| commerce_ops | 10 | 51 (25 authority-described) | 9 | 1 | 1 | 5006 |
| fleet_ops | 10 | 48 (24 authority-described) | 9 | 2 | 1 | 6188 |
| support_ops | 10 | 48 (29 authority-described) | 9 | 2 | 1 | 6544 |

## Coverage

| Tag | Observed | Target |
|---|---:|---:|
| simple_projection | 3 | 3 |
| filter | 6 | 3 |
| aggregation | 14 | 5 |
| grouping | 7 | 4 |
| relationship | 16 | 6 |
| multi_hop | 5 | 2 |
| population | 7 | 4 |
| calculation | 6 | 5 |
| temporal | 4 | 4 |
| json | 3 | 2 |
| window | 3 | 2 |
| nested | 4 | 2 |
| correlated | 2 | 1 |
| set_operation | 2 | 1 |
| ordering | 3 | 3 |
| null_semantics | 5 | 2 |
| precision | 5 | 2 |

## Governance cases

All five authority-blocked cases require a missing authorized relationship and retain a tempting physical linkage. All three ambiguous cases have two evaluator-only interpretations whose outputs differ on a fixture. Both policy cases explicitly request a forbidden write and expect `BLOCKED_POLICY`.

## Mutation and reference gates

- Reference A/B agreement: 100% across 60 fixture comparisons.
- Accepted mutants: 60; executed: 60; killed: 60; survived: 0.
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
- ready_for_human_review: **YES**

## Authority-blocked cases

| Case | Missing authority | Tempting physical evidence |
|---|---|---|
| commerce_08 | No authorized relationship maps orders.external_customer_code to customers.legacy_external_code. | The values are formatted similarly and may coincide. |
| fleet_08 | No authorized relationship maps weather_snapshots.route_code to routes.route_name. | Route codes look like route names but are not an authority relationship. |
| fleet_09 | No authorized relationship maps vehicles.legacy_device_code to telemetry_events.device_code. | The seeded DEV- codes intentionally align physically. |
| support_07 | No authorized relationship maps support_tickets.requester_email to contacts.email. | Seeded emails can match but are not authoritative joins. |
| support_08 | No authorized relationship maps ticket requester_email to incidents.incident_code. | Both are free-text identifiers but have no semantic link. |

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

Benchmark tests: 6 passed (4 unit, 2 PostgreSQL integration). Full repository run: 747 passed, 4 historical frozen-hash failures, 8 skipped. The four failures are pre-existing M32/M33 forensic-state hash expectations and were not rewritten by M34. Benchmark Ruff, format, mypy, and git diff check: PASS. Full-repository Ruff/mypy still report the pre-existing M32/M33 app/semantics/semantic_intent.py findings; no unrelated historical cleanup was applied.

Human review status: `machine-validated != human-reviewed`. No case is marked `HUMAN_ACCEPTED`; all 30 remain in the review queue.
