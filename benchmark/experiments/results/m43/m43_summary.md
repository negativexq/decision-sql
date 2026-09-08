# Decision-SQL Bench v0.2.1-dev

## M43 — Luna / reasoning-none / single-call baseline

Model: `gpt-5.6-luna`; provider: `openai-compatible`; reasoning: `none`; temperature: `0.0`; calls/case: `1`

| Metric | Correct / Total | Rate |
|---|---:|---:|
| Governed Task Success | 77 / 90 | 85.6% |
| Answerable Test-Suite Accuracy | 49 / 60 | 81.7% |
| Authority-Blocked Accuracy | 15 / 15 | 100.0% |
| Ambiguity Accuracy | 7 / 9 | 77.8% |
| Policy-Blocked Accuracy | 6 / 6 | 100.0% |

## DEV vs CONFIRMATION

| Metric | DEV | CONFIRMATION | OVERALL |
|---|---:|---:|---:|
| Governed Success | 45/50 (90.0%) | 32/40 (80.0%) | 77/90 (85.6%) |
| Answerable TSA | 29/34 (85.3%) | 20/26 (76.9%) | 49/60 (81.7%) |
| Authority | 8/8 (100.0%) | 7/7 (100.0%) | 15/15 (100.0%) |
| Ambiguity | 5/5 (100.0%) | 2/4 (50.0%) | 7/9 (77.8%) |
| Policy | 3/3 (100.0%) | 3/3 (100.0%) | 6/6 (100.0%) |

## Counterfactual contribution

Base-only answerable accuracy: 49/60
Full-suite answerable accuracy: 49/60
Base-only false positives: 0 (none)

## Failures

| case_id | database | split | gold | decision | category | first fixture |
|---|---|---|---|---|---|---|
| fleet_04 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| subscription_04 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_06 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_10 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_14 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_03 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | — |
| warehouse_04 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| warehouse_08 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| warehouse_12 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | — |
| risk_03 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| risk_06 | risk_operations | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_19 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |
| warehouse_20 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |

## Reproducibility

Provider calls attempted: 90; genuine responses: 90; semantic retries: 0; repairs: 0; judges/selectors/reflection: 0
Latency: `{"max_ms": 3610.012791818008, "median_ms": 1797.5397915579379, "min_ms": 1292.3665831331164, "p90_ms": 2448.2223328668624, "successful_responses": 90}`
Token usage: `{"completion_tokens": {"available": 90, "median": 58.0, "total": 6188}, "prompt_tokens": {"available": 90, "median": 8276.5, "total": 673706}, "reasoning_tokens": {"available": 90, "median": 0.0, "total": 0}, "total_tokens": {"available": 90, "median": 8363.5, "total": 679894}}`
Raw responses are immutable evidence; no chain-of-thought was requested or stored.

## M43 typed JSON ablation analysis

M41 control: 46/60 answerable; M43: 49/60.

Target JSON fixes: 2/2 (fleet_06, warehouse_09).
JSON regressions: 0. Non-JSON regressions: 5.
Official scoring remains execution/result-contract based; static coercion labels are diagnostic.

## M43 typed JSON ablation analysis

M41 control: 46/60 answerable; M43: 49/60.

Target JSON fixes: 2/2 (fleet_06, warehouse_09).
JSON regressions: 0. Non-JSON regressions: 5.
Official scoring remains execution/result-contract based; static coercion labels are diagnostic.
