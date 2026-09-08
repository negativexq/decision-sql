# Decision-SQL Bench v0.2.0-dev

## M39 — Luna / reasoning-none / single-call baseline

- Model: `gpt-5.6-luna`; provider: `openai-compatible`; reasoning: `none`
- Temperature: `0.0`; timeout: `90s`; calls/case: `1`
- Retries: transport `0`; semantic `0`; repair `false`; selector `false`; judge `false`; reflection `false`
- Benchmark hash: `32fb1f941773f4ec7d7ccc1fa38525e2730509b00c5df1f5f7dbb72cc5c77226`
- Prompt hash: `1407f33106506753ba4dd552c51ac6524133bc2789020237be5a64cf80aee544`

M35R1 used benchmark 0.1.1-pilot / 30 cases. M39 uses benchmark 0.2.0-dev / 90 cases; percentages are not a direct model comparison.

## Overall benchmark result

| Metric | Correct / Total | Rate |
|---|---:|---:|
| Governed Task Success | 60 / 90 | 66.7% |
| Answerable Test-Suite Accuracy | 32 / 60 | 53.3% |
| Authority-Blocked Accuracy | 15 / 15 | 100.0% |
| Ambiguity Accuracy | 7 / 9 | 77.8% |
| Policy-Blocked Accuracy | 6 / 6 | 100.0% |

## DEV vs CONFIRMATION

| Metric | DEV | CONFIRMATION | OVERALL |
|---|---:|---:|---:|
| Governed Success | 40/50 (80.0%) | 20/40 (50.0%) | 60/90 (66.7%) |
| Answerable TSA | 25/34 (73.5%) | 7/26 (26.9%) | 32/60 (53.3%) |
| Authority | 8/8 (100.0%) | 7/7 (100.0%) | 15/15 (100.0%) |
| Ambiguity | 4/5 (80.0%) | 3/4 (75.0%) | 7/9 (77.8%) |
| Policy | 3/3 (100.0%) | 3/3 (100.0%) | 6/6 (100.0%) |

## Domain results

| Domain | Governed | Answerable | Governance-negative |
|---|---:|---:|---:|
| commerce_ops | 10/10 | 7/7 | 3/3 |
| fleet_ops | 8/10 | 5/7 | 3/3 |
| risk_operations | 12/20 | 5/13 | 7/7 |
| subscription_billing | 12/20 | 7/14 | 5/6 |
| support_ops | 10/10 | 6/6 | 4/4 |
| warehouse_logistics | 8/20 | 2/13 | 6/7 |

## Failure-category distribution

| Category | Count |
|---|---:|
| TRANSPORT_FAILURE | 0 |
| PROVIDER_SCHEMA_FAILURE | 0 |
| INVALID_SUBMISSION | 0 |
| WRONG_GOVERNED_DECISION | 19 |
| SQL_ADMISSION_FAILURE | 0 |
| EXECUTION_FAILURE | 0 |
| RESULT_MISMATCH | 11 |
| CORRECT | 60 |

## Counterfactual contribution

- Base-only answerable accuracy: 33/60
- Full-suite answerable accuracy: 32/60
- Base-only false positives: 1
- Cases: warehouse_11

## Projection discipline

- Observable extra-column mismatches: 0
- Observable column-order mismatches: 0

## Per-case results

| # | case_id | database | split | gold | model decision | category | base | fixtures | latency ms |
|---:|---|---|---|---|---|---|---|---|---:|
| 1 | commerce_01 | commerce_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 3047.5 |
| 2 | commerce_02 | commerce_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2368.7 |
| 3 | commerce_03 | commerce_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1989.4 |
| 4 | commerce_04 | commerce_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1916.7 |
| 5 | commerce_05 | commerce_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1778.4 |
| 6 | commerce_06 | commerce_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2644.9 |
| 7 | commerce_07 | commerce_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2945.8 |
| 8 | commerce_08 | commerce_ops | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1816.1 |
| 9 | commerce_09 | commerce_ops | DEV | AMBIGUOUS | NEEDS_CLARIFICATION | CORRECT | None | None | 1427.4 |
| 10 | commerce_10 | commerce_ops | DEV | POLICY_BLOCKED | BLOCKED_POLICY | CORRECT | None | None | 1535.3 |
| 11 | fleet_01 | fleet_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1835.8 |
| 12 | fleet_02 | fleet_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2151.5 |
| 13 | fleet_03 | fleet_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1833.3 |
| 14 | fleet_04 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 2146.2 |
| 15 | fleet_05 | fleet_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1827.9 |
| 16 | fleet_06 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 2157.8 |
| 17 | fleet_07 | fleet_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2036.0 |
| 18 | fleet_08 | fleet_ops | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1431.6 |
| 19 | fleet_09 | fleet_ops | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1459.5 |
| 20 | fleet_10 | fleet_ops | DEV | AMBIGUOUS | NEEDS_CLARIFICATION | CORRECT | None | None | 1509.5 |
| 21 | support_01 | support_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1836.4 |
| 22 | support_02 | support_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2938.1 |
| 23 | support_03 | support_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1704.5 |
| 24 | support_04 | support_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1687.3 |
| 25 | support_05 | support_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2316.0 |
| 26 | support_06 | support_ops | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1753.1 |
| 27 | support_07 | support_ops | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1678.6 |
| 28 | support_08 | support_ops | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1501.0 |
| 29 | support_09 | support_ops | DEV | AMBIGUOUS | NEEDS_CLARIFICATION | CORRECT | None | None | 1853.4 |
| 30 | support_10 | support_ops | DEV | POLICY_BLOCKED | BLOCKED_POLICY | CORRECT | None | None | 1734.0 |
| 31 | subscription_01 | subscription_billing | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1506.7 |
| 32 | subscription_02 | subscription_billing | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1606.6 |
| 33 | subscription_03 | subscription_billing | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1971.9 |
| 34 | subscription_04 | subscription_billing | DEV | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1741.9 |
| 35 | subscription_05 | subscription_billing | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1933.7 |
| 36 | subscription_06 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1638.7 |
| 37 | subscription_07 | subscription_billing | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2475.8 |
| 38 | subscription_08 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1635.8 |
| 39 | subscription_09 | subscription_billing | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1816.7 |
| 40 | subscription_10 | subscription_billing | DEV | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1704.6 |
| 41 | subscription_11 | subscription_billing | DEV | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1783.3 |
| 42 | subscription_12 | subscription_billing | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 1880.4 |
| 43 | subscription_13 | subscription_billing | DEV | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1751.7 |
| 44 | subscription_14 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1657.8 |
| 45 | warehouse_01 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1635.6 |
| 46 | warehouse_02 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 1801.3 |
| 47 | warehouse_03 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 1994.5 |
| 48 | warehouse_04 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1542.8 |
| 49 | warehouse_05 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1786.5 |
| 50 | warehouse_06 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1644.4 |
| 51 | warehouse_07 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1503.0 |
| 52 | warehouse_08 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 2086.1 |
| 53 | warehouse_09 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1509.2 |
| 54 | warehouse_10 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2149.1 |
| 55 | warehouse_11 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | PASS | FAIL | 1677.5 |
| 56 | warehouse_12 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1212.4 |
| 57 | warehouse_13 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2420.1 |
| 58 | risk_01 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1694.4 |
| 59 | risk_02 | risk_operations | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1400.9 |
| 60 | risk_03 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 1579.6 |
| 61 | risk_04 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 1837.9 |
| 62 | risk_05 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 1744.0 |
| 63 | risk_06 | risk_operations | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1332.7 |
| 64 | risk_07 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1846.4 |
| 65 | risk_08 | risk_operations | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | None | None | 1619.8 |
| 66 | risk_09 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2351.2 |
| 67 | risk_10 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 2063.6 |
| 68 | risk_11 | risk_operations | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1723.4 |
| 69 | risk_12 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | FAIL | FAIL | 1906.9 |
| 70 | risk_13 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | CORRECT | PASS | PASS | 1802.5 |
| 71 | subscription_15 | subscription_billing | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1133.7 |
| 72 | subscription_16 | subscription_billing | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1693.8 |
| 73 | subscription_17 | subscription_billing | DEV | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1229.2 |
| 74 | subscription_18 | subscription_billing | DEV | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | None | None | 1992.7 |
| 75 | subscription_19 | subscription_billing | DEV | AMBIGUOUS | NEEDS_CLARIFICATION | CORRECT | None | None | 1315.1 |
| 76 | subscription_20 | subscription_billing | DEV | POLICY_BLOCKED | BLOCKED_POLICY | CORRECT | None | None | 1397.6 |
| 77 | warehouse_15 | warehouse_logistics | CONFIRMATION | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1448.0 |
| 78 | warehouse_16 | warehouse_logistics | CONFIRMATION | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1632.7 |
| 79 | warehouse_17 | warehouse_logistics | CONFIRMATION | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 3845.0 |
| 80 | warehouse_18 | warehouse_logistics | CONFIRMATION | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1676.7 |
| 81 | warehouse_19 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | None | None | 1531.1 |
| 82 | warehouse_20 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | NEEDS_CLARIFICATION | CORRECT | None | None | 1737.6 |
| 83 | warehouse_21 | warehouse_logistics | CONFIRMATION | POLICY_BLOCKED | BLOCKED_POLICY | CORRECT | None | None | 1532.2 |
| 84 | risk_15 | risk_operations | CONFIRMATION | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1429.1 |
| 85 | risk_16 | risk_operations | CONFIRMATION | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1535.8 |
| 86 | risk_17 | risk_operations | CONFIRMATION | AUTHORITY_BLOCKED | BLOCKED_AUTHORITY | CORRECT | None | None | 1358.7 |
| 87 | risk_18 | risk_operations | CONFIRMATION | AMBIGUOUS | NEEDS_CLARIFICATION | CORRECT | None | None | 2931.9 |
| 88 | risk_19 | risk_operations | CONFIRMATION | AMBIGUOUS | NEEDS_CLARIFICATION | CORRECT | None | None | 1537.0 |
| 89 | risk_21 | risk_operations | CONFIRMATION | POLICY_BLOCKED | BLOCKED_POLICY | CORRECT | None | None | 1324.4 |
| 90 | risk_22 | risk_operations | CONFIRMATION | POLICY_BLOCKED | BLOCKED_POLICY | CORRECT | None | None | 1532.9 |

## Per-case failures

- `fleet_04` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `fleet_06` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `subscription_04` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `subscription_06` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `subscription_08` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `subscription_10` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `subscription_12` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `subscription_13` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `subscription_14` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_01` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_02` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `warehouse_03` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `warehouse_04` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_05` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_06` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_07` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_08` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `warehouse_09` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_11` — `RESULT_MISMATCH`, base `PASS`, first fixture `warehouse_11_cf2_b6ed47`; observable reason `—`
- `warehouse_12` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `risk_02` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `risk_03` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `risk_04` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `risk_05` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `risk_06` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `risk_08` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `risk_11` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `risk_12` — `RESULT_MISMATCH`, base `FAIL`, first fixture `base`; observable reason `—`
- `subscription_18` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`
- `warehouse_19` — `WRONG_GOVERNED_DECISION`, base `None`, first fixture `—`; observable reason `—`

## Latency and usage

- Latency: `{"max_ms": 3844.964292133227, "median_ms": 1735.7833330752328, "min_ms": 1133.7474170140922, "p90_ms": 2352.942104358226, "successful_responses": 90}`
- Token usage: `{"completion_tokens": {"available_cases": 90, "median_per_case": 42.0, "total": 5821}, "prompt_tokens": {"available_cases": 90, "median_per_case": 5469.5, "total": 489483}, "reasoning_tokens": {"available_cases": 90, "median_per_case": 0.0, "total": 0}, "total_tokens": {"available_cases": 90, "median_per_case": 5546.0, "total": 495304}}`

## Reproducibility

- Provider calls: `90`; responses: `90`
- Request/contract hashes: `{"benchmark_content_hash": "32fb1f941773f4ec7d7ccc1fa38525e2730509b00c5df1f5f7dbb72cc5c77226", "case_order_hash": "3299ecb9046619cd7b2e2aed66ed2e4146e8286b0497202b3b3ebc151947c2c2", "context_hashes": {"commerce_ops": "f94bc724e404723969a74a5cfee0b02dd447112559ba9f00d39a54a90615417e", "fleet_ops": "1fde312b7bc723ba877b26720519b9c123e347ec4bd1069f8ebb142d4cfc4ea6", "risk_operations": "b9468c4078f732cdd1fdb41aa154d7156b585b40df19832240928245b2d01064", "subscription_billing": "a8a4bc24e4d5b75c6c1fa4cde0d99fead7f5a2cfef40a94d0d23b37ac814ea0b", "support_ops": "e78b61f854cd1b87f1a9fdc049c795974bc06888e62258bb4785842aadfa2c17", "warehouse_logistics": "0aabc8e303fbace15ae2f9f983e6f537642bcd3e63c77f564bde7c1be67f149c"}, "evaluator_hash": "ccc3175fde205630fe87717480e3e521be2cee8e6b4fb26c359448977d95e019", "experiment_config_hash": "bfeecd4e290b1c981dae8244302b8e1242532186171968b811d6ad974f100cbd", "governance_prompt_hash": "1407f33106506753ba4dd552c51ac6524133bc2789020237be5a64cf80aee544", "postgresql_version": "16.15", "provider_adapter_hash": "d8cabbbd146b54a7fdbd1d6b3c20ffeb712d7e01613cff997cb25fe3fb4efed7", "provider_config": {"base_url": "https://api.openai.com/v1", "credential_configured": true, "endpoint_family": "chat_completions", "model": "gpt-5.6-luna", "provider": "openai-compatible", "reasoning": "none", "temperature": 0.0, "timeout_seconds": 90}, "provider_config_hash": "7d97392c16f194d96d2a63af34e7403f2b38229ecefbc447dffeab5c3aba9ccb", "serializer_hash": "19b398badcb193c2b3c20f11697155b184113a956b4b79f4e22ff0b20f6b47f2", "submission_schema_hash": "a20bcacd0f9221b53ccf0b8ab860981956fa7eef36bcfb507ffd79453c52b240", "validator_hash": "6949c380dddd2d9123a8dfcad2319496fd951bd43297b72851af099daba1b274"}`
- Raw responses are immutable evidence; no chain-of-thought was requested or stored.
